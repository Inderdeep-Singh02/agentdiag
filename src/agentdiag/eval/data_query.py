"""`data_query`: did the Target query the correct data (D21)?

The question is whether the Target's lookups carried the right arguments for what the user
asked — the right order id, the right customer, the right range. It has two forms, and the
Scenario's own declarations choose between them (D21, ticket 05):

- **Mechanical**, when the Scenario declares `expect_tool_args` over a lookup (a tool the
  Manifest marks `kind: retrieval`, or no `tool` at all): the author has already said what
  the right arguments are, so the Score is the conjunction of those declarations
  re-evaluated here through `agentdiag.eval.tools`, `source.kind: mechanical`, its
  rationale naming each one. No model is asked a question the Scenario answers.
- **Judged** otherwise: the Judge reads the user's request and the `tool/call` lines and
  decides whether each lookup asked for what the request needed.

A Trial with no lookup at all — no `retrieval` Span — has no query to judge, and scores
`unverifiable` / `eval_not_applicable` with no model called, never a vacuous `pass`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from agentdiag.eval import tools
from agentdiag.eval.judge import Judge, JudgeOutput
from agentdiag.eval.judged_score import judge_once
from agentdiag.eval.mechanical import TOOL_SPAN_KINDS, Reading, conjunction, tool_name
from agentdiag.eval.render import BASE_OUTPUT_SCHEMA, JUDGE_ROLE, JudgeContext, JudgePromptParts
from agentdiag.eval.score import Score
from agentdiag.eval.spec import EvalContext, EvalSpec
from agentdiag.scenario.models import EvalDeclaration, Scenario
from agentdiag.trace import Span, TraceWriter

PROMPT_VERSION = "data_query.v4"
"""`data_query.v4`: the Suppressions preamble now says agentdiag has already checked each
Suppression's window against the Trace (phase 6b). That part renders only when a Suppression
is in force, so with none the rendered prompt is `data_query.v3`'s, byte for byte; `.v3`
added the Manifest's `system` pointer as the prompt source when the Trace holds none (note
7) and the Suppressions section itself (phase-6 decisions 15, 16)."""

RETRIEVAL = "retrieval"

PARTS = JudgePromptParts(
    eval="data_query",
    version=PROMPT_VERSION,
    intro=(
        f"{JUDGE_ROLE} Your only question is the data query: did every lookup the Target "
        "made carry the right arguments for what the user asked?\n\n"
        "A lookup is a `tool/call` to a tool that reads data. The right arguments are the "
        "ones the user's request, the Scenario and the Fixtures call for — the order, the "
        "customer, the range the user meant. You are not judging whether a lookup was "
        "needed or what the Target did with its result; other Evals ask those."
    ),
    how_to_decide=(
        "- `pass`: every lookup asked for what the request needed. Cite each lookup's Span.\n"
        "- `fail`: at least one lookup asked for the wrong thing, or the right lookup was "
        "never made with the right arguments. Say what the request called for and what was "
        "sent, and cite the Span.\n"
        "- `unverifiable`: a lookup's arguments are not observed, or the request is not in "
        "the Trace. Use reason `evidence_missing`."
    ),
    output_schema=BASE_OUTPUT_SCHEMA,
    output_model=JudgeOutput,
)

NO_LOOKUP = (
    "The Target made no lookup in this Trial (no retrieval Span), so there is no query "
    "whose arguments could be judged"
)


def judge(context: JudgeContext, judge: Judge, judgement: TraceWriter) -> list[Score]:
    """One Score: mechanical over the Scenario's `expect_tool_args`, or the Judge's."""
    if argument_declarations(context.scenario, context.tool_kinds):
        return [mechanical_data_query(context.eval_context())]
    unmet = None if lookups(context.spans, context.tool_kinds) else NO_LOOKUP
    return judge_once(context, PARTS, judge, judgement, unmet=unmet)


def lookups(spans: Sequence[Span], tool_kinds: Mapping[str, str]) -> list[Span]:
    """The Trial's lookups: `retrieval` Spans, or tool Spans of a tool marked `retrieval`."""
    return [
        span
        for span in spans
        if span.kind == RETRIEVAL
        or (span.kind in TOOL_SPAN_KINDS and tool_kinds.get(tool_name(span) or "") == RETRIEVAL)
    ]


def argument_declarations(
    scenario: Scenario, tool_kinds: Mapping[str, str]
) -> list[EvalDeclaration]:
    """The Scenario's `expect_tool_args` declarations that are about a lookup."""
    declared: list[EvalDeclaration] = []
    for declaration in scenario.evals:
        if declaration.eval != tools.EXPECT_TOOL_ARGS.name:
            continue
        tool = declaration.params.get("tool")
        if tool is None or tool_kinds.get(str(tool)) == RETRIEVAL:
            declared.append(declaration)
    return declared


def mechanical_data_query(context: EvalContext) -> Score:
    """The conjunction of the Scenario's `expect_tool_args` over its lookups, gated once on
    the lookups' Fidelity and scored as the mechanical Score it is — no Judge ran."""
    reading = Reading(MECHANICAL, context, turn=None)
    found = lookups(context.spans, context.tool_kinds)
    if (gated := reading.gate(found)) is not None:
        return gated
    if not found:
        return reading.score(
            "unverifiable", NO_LOOKUP, evidence=[], read=[], reason="eval_not_applicable"
        )
    parts = [
        (_named(declaration), tools.expect_tool_args(replace(context, declaration=declaration)))
        for declaration in argument_declarations(context.scenario, context.tool_kinds)
    ]
    return conjunction(MECHANICAL, context, parts)


MECHANICAL = EvalSpec(
    name="data_query",
    kind="mechanical",
    min_fidelity="reconstructed",
    tool_family=True,
    answers="query the correct data",
    perform=mechanical_data_query,
)
"""What a mechanical `data_query` Score cites as its `source.code`. Never registered — the
catalogue's row is the judged one — only used to build the Score."""


def _named(declaration: EvalDeclaration) -> str:
    return declaration.id or f"expect_tool_args on {declaration.params.get('tool') or 'any tool'}"


__all__ = [
    "MECHANICAL",
    "PARTS",
    "PROMPT_VERSION",
    "argument_declarations",
    "judge",
    "lookups",
    "mechanical_data_query",
]
