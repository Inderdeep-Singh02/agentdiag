"""What every mechanical Eval shares: its typed parameters, the Spans it reads, the Fidelity
gate, and the Score it builds (D21, D25, ADR-0003 §3 to §6).

The three Eval modules (`tools`, `text`, `latency`) each decide one question; everything
they would otherwise each re-implement lives here once, so the rules below cannot hold in
one module and drift in another:

- **Parameters are typed.** Each Eval's `EvalSpec.params_model` is what `validate` refuses
  a malformed declaration with, and what the Eval reads here; a declaration that slips past
  `validate` (a Suite built in code, an old Run rescored) is the Scenario's fault, scored
  `invalid` with `fault_source: scenario` — never a crash, never a guess at what was meant.
- **The min-Fidelity gate runs first.** When any Span an Eval would read is below the
  Eval's `min_fidelity` — or, reading none, when the Trace is — the Score is
  `unverifiable` / `fidelity_too_low` before anything is decided (D21, ADR-0003 §3). Order
  is `FIDELITY_RANK`'s.
- **A Score cites what grounded it.** `evidence` is Span ids from this Trace, and the
  Score's `fidelity` is the lowest among the Spans it read, or the Trace's when it read
  none, so a Score never claims better grounding than its evidence had.
- **Absence of tool Spans is read through Fidelity, and through the responses** (phase-5
  decision 3). At `instrumented`, `llm_call` Spans with no tool Span are proof the Target
  called nothing — unless a response asked for a tool that no Span answers, which means a
  requested call left no record (an unwrapped or server tool, a crash after `tool_use`), so
  the Score is `unverifiable` / `evidence_missing` citing that `llm_call`. At
  `reconstructed` the Adapter may have missed a call, so no tool Span at all is
  `unverifiable` / `evidence_missing`. With no `llm_call` Span either, nothing shows the
  Target was even asked, at any Fidelity. `unanswered` finds those requests, and the tool
  Evals also consult it wherever a Verdict would rest on a call being absent.
- **`turn: <n>` restricts every tool and text Eval to one Turn** (decision 7), by the
  `turn` field every Span and Event carries.
- **A response not observed hides tool calls** (ticket 26). When an `llm_call` in scope
  marks its `response_body` (or `response`) not observed — an imported proxy row the proxy
  cut or never logged — the calls it asked for are not fully known, so every tool Eval that
  decided is `unverifiable` / `evidence_missing` citing those Spans (`unseen_responses`).

Text is compared NFC-normalised and case-folded (decision 18), and that
normalisation lives here too because `expect_tool_args`' `contains` and `none_in` use it.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from agentdiag.eval.score import Score, ScoreSource
from agentdiag.eval.spec import EvalContext, EvalSpec
from agentdiag.trace.events import Event
from agentdiag.trace.otel_genai import TOOL_CALL_ID, TOOL_NAME
from agentdiag.trace.spans import LLM_CALL_KIND, RESPONSE_KIND, Span, is_own_span, not_observed
from agentdiag.types import (
    FIDELITY_RANK,
    FaultDirection,
    FaultSource,
    Fidelity,
    UnverifiableReason,
    Verdict,
)

TOOL_SPAN_KINDS = frozenset({"tool_call", "retrieval"})
"""The Span kinds a tool execution is recorded as (ADR-0006 §2)."""

TURN_SPAN_KIND = "turn"

TurnNumber = Annotated[StrictInt, Field(ge=1)]
"""`turn: <n>`, a Turn number from 1 (decision 7)."""

Name = Annotated[str, Field(min_length=1)]


class Parameters(BaseModel):
    """What every mechanical Eval's typed parameters share: unknown keys are refused, so a
    typo (`tols:`) is a `validate` error rather than a silently ignored parameter."""

    model_config = ConfigDict(extra="forbid")


class DeclarationError(ValueError):
    """A declaration this Eval cannot read. The Scenario's fault, not the Target's."""


class Reading:
    """One mechanical Eval reading one Trace: its Spans, its messages, its Score."""

    def __init__(self, spec: EvalSpec, context: EvalContext, *, turn: int | None) -> None:
        self.spec = spec
        self.context = context
        self.turn = turn

    def scope(self) -> str:
        """How the rationale names what was read: the whole Trace, or one Turn."""
        return "the Trace" if self.turn is None else f"Turn {self.turn}"

    # --- the Spans and Events it reads ---

    def _in_scope(self, span: Span) -> bool:
        """In the Turn read, and not agentdiag's own model call inside the Trial: a Span of
        the Simulated User's, or a stop check's `llm_call`, is never read (phase-5 decision
        59), so a mechanical Eval judges the Target and nothing else."""
        return (self.turn is None or span.turn == self.turn) and not is_own_span(span)

    def tool_spans(self, tool: str | None = None) -> list[Span]:
        """Tool Spans in scope, optionally of one tool, in `dotted_order`."""
        return [
            span
            for span in self.context.spans
            if span.kind in TOOL_SPAN_KINDS
            and self._in_scope(span)
            and (tool is None or tool_name(span) == tool)
        ]

    def model_spans(self) -> list[Span]:
        """The Target's `llm_call` Spans in scope."""
        return [
            span
            for span in self.context.spans
            if span.kind == LLM_CALL_KIND and self._in_scope(span)
        ]

    def response_spans(self) -> list[Span]:
        """The HTTP Adapter's `response` Spans in scope (phase-8 decision 7)."""
        return [
            span
            for span in self.context.spans
            if span.kind == RESPONSE_KIND and self._in_scope(span)
        ]

    def turn_spans(self) -> list[Span]:
        """The `turn` Spans in scope."""
        return [
            span
            for span in self.context.spans
            if span.kind == TURN_SPAN_KIND and self._in_scope(span)
        ]

    def target_messages(self) -> list[Event]:
        """The Target's own messages in scope (decision 18): never a tool result, never
        the user's or the Simulated User's words."""
        return [
            event
            for event in self.context.events
            if event.type == "message"
            and event.actor == "target"
            and (event.model_extra or {}).get("role") == "assistant"
            and (self.turn is None or event.turn == self.turn)
        ]

    def unseen_responses(self) -> list[Span]:
        """The Target's `llm_call` Spans in scope whose response was cut or never logged
        (`response_body` or `response` not observed, ADR-0006 §4): what tools it asked for
        is not fully known (ticket 26)."""
        return [
            span
            for span in self.model_spans()
            if {"response_body", "response"} & set(not_observed(span))
        ]

    def spans_of(self, events: Sequence[Event]) -> list[Span]:
        """The Spans these Events were written in, once each, in `dotted_order`."""
        wanted = {event.span_id for event in events if event.span_id is not None}
        return [span for span in self.context.spans if span.span_id in wanted]

    def unanswered(self, tools: Sequence[str] | None = None) -> list[Span]:
        """The `llm_call` Spans in scope whose response asked for a tool — one of `tools`,
        or any — that no tool Span answers (decision 3).

        A `tool_use` block is answered by a tool Span of the same tool carrying its id in
        `gen_ai.tool.call.id`, or, when the Adapter could not attribute a call and so
        recorded no id, by one such Span of the same tool. Each Span answers one block.
        """
        spare = self.tool_spans()
        asking: list[Span] = []
        for model in self.model_spans():
            for block in _tool_use_blocks(self.context.events, model):
                answer = _answer(block, spare)
                if answer is not None:
                    spare.remove(answer)
                elif (tools is None or block.get("name") in tools) and model not in asking:
                    asking.append(model)
        return asking

    # --- deciding ---

    def gate(self, read: Sequence[Span]) -> Score | None:
        """`unverifiable` / `fidelity_too_low` when the evidence is below this Eval's minimum."""
        grounded = lowest_fidelity(read, self.context.fidelity)
        minimum = FIDELITY_RANK[self.spec.min_fidelity]
        if FIDELITY_RANK[grounded] >= minimum:
            return None
        below = [span for span in read if FIDELITY_RANK[span.fidelity] < minimum]
        what = (
            f"the Spans it would read ({span_ids(below)}) are {grounded}"
            if below
            else f"the Trace is {grounded}"
        )
        return self.score(
            "unverifiable",
            f"{self.spec.name} needs evidence at Fidelity {self.spec.min_fidelity} or better, "
            f"and {what}",
            evidence=below,
            read=read,
            reason="fidelity_too_low",
        )

    def absent(self, models: Sequence[Span], *, fact: Verdict, because: str) -> Score:
        """Decision 3: what "no tool Span" means depends on how the Trace was obtained,
        and on whether any response asked for a tool.

        `because` is the rationale when the absence is a fact; it names what the Eval
        concluded from it.
        """
        if not models:
            return self.score(
                "unverifiable",
                f"{self.scope()} holds no tool Span and no llm_call Span, so nothing shows "
                "whether the Target was asked anything, let alone which tools it called",
                evidence=[],
                read=[],
                reason="evidence_missing",
            )
        if asking := self.unanswered():
            return self.requested_without_a_span(asking, read=models)
        grounded = lowest_fidelity(models, self.context.fidelity)
        if grounded == "instrumented":
            return self.score(
                fact,
                f"{because} The Adapter instrumented every model call and every tool, and "
                f"no response in {self.scope()} ({span_ids(models)}) asked for a tool, so "
                "the absence of tool Spans is a fact, not a gap.",
                evidence=models,
                read=models,
            )
        return self.score(
            "unverifiable",
            f"{self.scope()} holds no tool Span, and at Fidelity {grounded} the Adapter may "
            "have missed calls, so their absence proves nothing",
            evidence=models,
            read=models,
            reason="evidence_missing",
        )

    def requested_without_a_span(self, asking: Sequence[Span], *, read: Sequence[Span]) -> Score:
        """A response asked for a tool that left no Span: what was called is not known."""
        return self.score(
            "unverifiable",
            f"The response in {span_ids(asking)} asked for a tool that no tool Span answers "
            "(an unwrapped or server tool, or a Target that stopped after asking), so which "
            "tools were called is not fully recorded",
            evidence=asking,
            read=read,
            reason="evidence_missing",
        )

    def score(
        self,
        verdict: Verdict,
        rationale: str,
        *,
        evidence: Sequence[Span],
        read: Sequence[Span],
        reason: UnverifiableReason | None = None,
        value: float | None = None,
        fault_source: FaultSource | None = None,
        fault_direction: FaultDirection | None = None,
    ) -> Score:
        """The Score, grounded in the lowest Fidelity it read (ADR-0003 §4). An `invalid`
        names its fault's source and direction (ADR-0003 §3)."""
        return _score(
            self.spec,
            self.context,
            verdict=verdict,
            rationale=rationale,
            reason=reason,
            value=value,
            fault_source=fault_source,
            fault_direction=fault_direction,
            evidence=list(dict.fromkeys(span.span_id for span in evidence)),
            fidelity=lowest_fidelity(read, self.context.fidelity),
        )


def perform_mechanical[P: Parameters](
    spec: EvalSpec,
    context: EvalContext,
    model: type[P],
    decide: Callable[[Reading, P], Score],
) -> Score:
    """Read the declaration as `model`, then decide; an unreadable one is the Scenario's."""
    try:
        parameters = spec.parameters(context.declaration)
        if not isinstance(parameters, model):
            raise DeclarationError(f"{spec.name} reads its parameters as {model.__name__}")
        return decide(Reading(spec, context, turn=getattr(parameters, "turn", None)), parameters)
    except (DeclarationError, ValidationError) as exc:
        return _score(
            spec,
            context,
            verdict="invalid",
            rationale=f"The {spec.name} declaration cannot be read: {_problem(exc)}",
            fault_source="scenario",
            fault_direction="none",
            evidence=[],
            fidelity=context.fidelity,
        )


def _score(spec: EvalSpec, context: EvalContext, **fields: Any) -> Score:
    """Every mechanical Score: its Eval, its code, and a Metric's threshold and direction."""
    metric: dict[str, Any] = (
        {"threshold": context.declaration.threshold, "direction": spec.direction}
        if spec.metric
        else {}
    )
    return Score(
        eval=spec.name,
        eval_id=context.declaration.id,
        source=ScoreSource(kind="mechanical", code=spec.code),
        **metric,
        **fields,
    )


CONJUNCTION_ORDER: tuple[Verdict, ...] = ("fail", "invalid", "unverifiable", "incomplete", "pass")
"""Which Verdict a conjunction of Scores takes: a proven failure is never hidden behind one
that could not be decided, and a `pass` needs every part to pass."""


def conjunction(
    spec: EvalSpec,
    context: EvalContext,
    parts: Sequence[tuple[str, Score]],
) -> Score:
    """One Score that holds when every one of `parts` does, each named in the rationale.

    For an Eval that is mechanical when the Scenario already authored the mechanical
    question (ticket 05: `data_query` over `expect_tool_args`): the parts' evidence is
    cited together, the Verdict is the first of `CONJUNCTION_ORDER` any part took, and its
    cause is the first such part's, so the Score's validator still sees one closed cause.
    """
    verdict = next(v for v in CONJUNCTION_ORDER if any(s.verdict == v for _, s in parts))
    deciding = next(score for _, score in parts if score.verdict == verdict)
    rationale = "; ".join(f"{name}: {score.verdict} ({score.rationale})" for name, score in parts)
    evidence = list(dict.fromkeys(span for _, score in parts for span in score.evidence))
    fidelity = min(
        (score.fidelity for _, score in parts),
        key=lambda value: FIDELITY_RANK[value],
        default=context.fidelity,
    )
    return _score(
        spec,
        context,
        verdict=verdict,
        rationale=f"Decided mechanically from the Scenario's own declarations — {rationale}",
        evidence=evidence,
        fidelity=fidelity,
        reason=deciding.reason,
        fault_source=deciding.fault_source,
        fault_direction=deciding.fault_direction,
    )


def _problem(exc: DeclarationError | ValidationError) -> str:
    if isinstance(exc, DeclarationError):
        return str(exc)
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'the declaration'}: {error['msg']}"
        for error in exc.errors()
    )


def _tool_use_blocks(events: Sequence[Event], model: Span) -> list[Mapping[str, Any]]:
    """The `tool_use` blocks of the response recorded inside one `llm_call` Span."""
    blocks: list[Mapping[str, Any]] = []
    for event in events:
        if event.type != "response" or event.span_id != model.span_id:
            continue
        body = (event.model_extra or {}).get("body")
        content = body.get("content") if isinstance(body, Mapping) else None
        for block in content if isinstance(content, list) else []:
            if isinstance(block, Mapping) and block.get("type") == "tool_use":
                blocks.append(block)
    return blocks


def _answer(block: Mapping[str, Any], spare: Sequence[Span]) -> Span | None:
    """The tool Span that answers one `tool_use` block: by call id, else by name alone."""
    named = [span for span in spare if tool_name(span) == block.get("name")]
    for span in named:
        if span.attributes.get(TOOL_CALL_ID) == block.get("id"):
            return span
    return next((span for span in named if TOOL_CALL_ID not in span.attributes), None)


def tool_name(span: Span) -> str | None:
    """The tool a tool Span executed, by its OTel attribute (ADR-0006 §1)."""
    name = span.attributes.get(TOOL_NAME)
    return name if isinstance(name, str) else None


def lowest_fidelity(spans: Sequence[Span], default: Fidelity) -> Fidelity:
    """The weakest grounding among `spans`, or `default` when there are none."""
    if not spans:
        return default
    return min((span.fidelity for span in spans), key=lambda fidelity: FIDELITY_RANK[fidelity])


def normalised(text: str) -> str:
    """The form text is compared in: NFC, case-folded (decision 18)."""
    return unicodedata.normalize("NFC", text).casefold()


def as_text(value: Any) -> str:
    """A value as the text a substring is looked for in: a string is itself, anything else
    its JSON."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def span_ids(spans: Sequence[Span]) -> str:
    """Span ids for a rationale."""
    return ", ".join(span.span_id for span in spans) or "none"


__all__ = [
    "CONJUNCTION_ORDER",
    "TOOL_SPAN_KINDS",
    "TURN_SPAN_KIND",
    "DeclarationError",
    "Name",
    "Parameters",
    "Reading",
    "TurnNumber",
    "as_text",
    "conjunction",
    "lowest_fidelity",
    "normalised",
    "perform_mechanical",
    "span_ids",
    "tool_name",
]
