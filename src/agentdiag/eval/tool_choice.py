"""`tool_choice`: did the Target use the correct tools, and none it did not need (D21)?

Two forms, chosen by the Scenario's own declarations (D21, ticket 05):

- **Mechanical**, when the Scenario declares a positive tool list — `expect_tools`,
  `expect_tools_order` or `expect_tools_any` — so the author has said which tools were
  right, and the question is set arithmetic over the tool Spans (`forbid_tools` joins in
  when it is declared too; alone it says only which tools were wrong, and leaves the
  judged form in place). Required are the `expect_tools` and
  `expect_tools_order` names, allowed are those plus the `expect_tools_any` names, and the
  Score is `pass` exactly when every required tool was called, every call was to an
  allowed tool, and no forbidden tool was called — "the right tools, none unnecessary".
  A declaration restricted to one Turn (`turn: <n>`, decision 7) requires or forbids in
  that Turn only, so a Scenario that looks up in Turn 1 and cancels in Turn 2 is not
  failed for cancelling. The rationale names every missing, extra or forbidden call.
- **Judged** otherwise: the Judge reads the user's request and the tool Spans and decides
  whether the tools fit the intent.

The precondition is a tool Span, or a Trace at Fidelity `instrumented`, where no tool Span
is the fact that no tool was called (decision 3); otherwise the Score is `unverifiable` /
`eval_not_applicable` with no model called. A response that asked for a tool no Span
answers leaves which tools ran unknown, so the mechanical form is then `unverifiable` /
`evidence_missing`, as the tool Evals it reuses are.
"""

from __future__ import annotations

from pydantic import ValidationError

from agentdiag.eval import tools
from agentdiag.eval.judge import Judge, JudgeOutput
from agentdiag.eval.judged_score import judge_once
from agentdiag.eval.mechanical import Reading, lowest_fidelity, span_ids, tool_name
from agentdiag.eval.render import BASE_OUTPUT_SCHEMA, JUDGE_ROLE, JudgeContext, JudgePromptParts
from agentdiag.eval.score import Score
from agentdiag.eval.spec import EvalContext, EvalSpec
from agentdiag.trace import Span, TraceWriter

PROMPT_VERSION = "tool_choice.v4"
"""`tool_choice.v4`: the Suppressions preamble now says agentdiag has already checked each
Suppression's window against the Trace (phase 6b). That part renders only when a Suppression
is in force, so with none the rendered prompt is `tool_choice.v3`'s, byte for byte; `.v3`
added the Manifest's `system` pointer as the prompt source when the Trace holds none (note
7) and the Suppressions section itself (phase-6 decisions 15, 16)."""

TOOL_LISTS = {
    spec.name: spec
    for spec in (
        tools.EXPECT_TOOLS,
        tools.EXPECT_TOOLS_ORDER,
        tools.EXPECT_TOOLS_ANY,
        tools.FORBID_TOOLS,
    )
}
"""The declarations whose presence makes `tool_choice` mechanical."""

REQUIRING = frozenset({tools.EXPECT_TOOLS.name, tools.EXPECT_TOOLS_ORDER.name})
POSITIVE = REQUIRING | {tools.EXPECT_TOOLS_ANY.name}
"""The lists that say which tools were right. `forbid_tools` alone says only which were
wrong, so it leaves the question to the Judge (ticket 05 Spec review)."""
FORBID = tools.FORBID_TOOLS.name

PARTS = JudgePromptParts(
    eval="tool_choice",
    version=PROMPT_VERSION,
    intro=(
        f"{JUDGE_ROLE} Your only question is tool choice: did the Target call the tools the "
        "user's request needed, and no tool it did not need?\n\n"
        "The Target's tools are named in its prompt sections. A tool it needed and never "
        "called is as wrong as one it called for no reason; an action taken without the "
        "user asking for it is the worst of these. You are not judging the tools' arguments "
        "or what the Target said about their results; other Evals ask those."
    ),
    how_to_decide=(
        "- `pass`: every tool the request needed was called and no unnecessary tool was. "
        "Cite the tool Spans, or, when no tool was needed and none was called, the model "
        "call Spans that answered without one.\n"
        "- `fail`: a needed tool was never called, or a tool was called that the request did "
        "not need. Name the tool and cite the Span.\n"
        "- `unverifiable`: the Trace does not show which tools were called. Use reason "
        "`evidence_missing`."
    ),
    output_schema=BASE_OUTPUT_SCHEMA,
    output_model=JudgeOutput,
)

NO_TOOLS_KNOWN = (
    "The Trace holds no tool Span and was not instrumented, so whether the Target called any "
    "tool is not known and its choice of tools cannot be judged"
)


def judge(context: JudgeContext, judge: Judge, judgement: TraceWriter) -> list[Score]:
    """One Score: mechanical over the Scenario's positive tool lists, or the Judge's."""
    evaluated = context.eval_context()
    if any(declaration.eval in POSITIVE for declaration in context.scenario.evals):
        return [mechanical_tool_choice(evaluated)]
    no_tools = not Reading(MECHANICAL, evaluated, turn=None).tool_spans()
    grounded = lowest_fidelity(context.spans, context.fidelity)
    unmet = NO_TOOLS_KNOWN if no_tools and grounded != "instrumented" else None
    return judge_once(context, PARTS, judge, judgement, unmet=unmet)


def mechanical_tool_choice(context: EvalContext) -> Score:
    """Required ⊆ called ⊆ allowed, and nothing forbidden, per Turn where a list says one."""
    reading = Reading(MECHANICAL, context, turn=None)
    called, models = reading.tool_spans(), reading.model_spans()
    read = [*called, *models]
    if (gated := reading.gate(read)) is not None:
        return gated
    try:
        lists = _lists(context)
    except ValidationError as exc:
        return reading.score(
            "unverifiable",
            f"A tool list the Scenario declares cannot be read: {exc.errors()[0]['msg']}",
            evidence=[],
            read=read,
            reason="eval_not_applicable",
        )
    required = [(name, turn) for kind, names, turn in lists if kind in REQUIRING for name in names]
    allowed = {name for kind, names, _ in lists if kind != FORBID for name in names}
    forbidden = [(name, turn) for kind, names, turn in lists if kind == FORBID for name in names]

    if not called:
        wanted = ", ".join(f"{name}{_in(turn)}" for name, turn in required)
        return reading.absent(
            models,
            fact="fail" if required else "pass",
            because=(
                f"The Target called no tool, and {wanted} were required."
                if required
                else "The Target called no tool, and no list required one."
            ),
        )
    if asking := reading.unanswered():
        return reading.requested_without_a_span(asking, read=read)

    missing = [f"{name}{_in(turn)}" for name, turn in required if not _calls(called, name, turn)]
    extra = list(
        dict.fromkeys(str(tool_name(span)) for span in called if tool_name(span) not in allowed)
    )
    broken = [span for name, turn in forbidden for span in _calls(called, name, turn)]
    if missing or extra or broken:
        problems = []
        if missing:
            problems.append(f"required but never called: {', '.join(missing)}")
        if extra:
            problems.append(f"called though no list allows it: {', '.join(extra)}")
        if broken:
            problems.append(f"forbidden and called: {span_ids(broken)}")
        return reading.score(
            "fail",
            f"The tools called were not the right ones: {'; '.join(problems)}. Calls: "
            f"{_names(called)}",
            evidence=called,
            read=read,
        )
    return reading.score(
        "pass",
        "Every required tool was called, every call was to an allowed tool, and no forbidden "
        f"tool was called. Calls: {_names(called)}",
        evidence=called,
        read=read,
    )


MECHANICAL = EvalSpec(
    name="tool_choice",
    kind="mechanical",
    min_fidelity="reconstructed",
    tool_family=True,
    answers="use the correct tool",
    perform=mechanical_tool_choice,
)
"""What a mechanical `tool_choice` Score cites as its `source.code`. Never registered — the
catalogue's row is the judged one — only used to build the Score."""


def _lists(context: EvalContext) -> list[tuple[str, list[str], int | None]]:
    """Every tool list the Scenario declares, as (Eval, names, Turn or None)."""
    lists: list[tuple[str, list[str], int | None]] = []
    for declaration in context.scenario.evals:
        spec = TOOL_LISTS.get(declaration.eval)
        if spec is None:
            continue
        parameters = spec.parameters(declaration)
        if isinstance(parameters, tools.ToolListParameters):
            lists.append((spec.name, list(parameters.tools), parameters.turn))
    return lists


def _calls(called: list[Span], name: str, turn: int | None) -> list[Span]:
    return [
        span for span in called if tool_name(span) == name and (turn is None or span.turn == turn)
    ]


def _in(turn: int | None) -> str:
    return "" if turn is None else f" (Turn {turn})"


def _names(spans: list[Span]) -> str:
    return ", ".join(f"{tool_name(span)} in {span.span_id}" for span in spans) or "none"


__all__ = ["MECHANICAL", "PARTS", "PROMPT_VERSION", "judge", "mechanical_tool_choice"]
