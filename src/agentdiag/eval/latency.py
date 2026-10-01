"""The latency Evals: how long did the tool, the response, and its first token take (D21,
ADR-0003 §5, §6)?

Each is a Metric plus a threshold, never a judgement of its own. The Metric is a projection
of the Trace's timestamps (D11), read through `metrics`, and exists on every Span whether
or not an Eval thresholds it; the Eval only reads it back and compares. Its Score stores
the value, the threshold it was held to and the direction of better (`minimize`) beside the
derived Verdict, so a later change to the threshold never re-interprets a stored Score
(ADR-0003 §5). One Score holds one value against one threshold, which is why time to first
token is an Eval of its own rather than a second bound on `response_latency`.

- `tool_latency`, `threshold: {max_ms}`, optional `tool`: the value is the longest
  `duration_ms` over the tool Spans considered.
- `response_latency`, `threshold: {max_ms}`: the value is the longest `turn` Span's
  `duration_ms`, net of Backend start-up.
- `first_token_latency`, `threshold: {max_ms}`: the value is the longest time, over the
  Turns, from a Turn's start to the first token of its first `llm_call` that observed one,
  net of that `llm_call`'s Backend start-up. Only a streamed response observes a first
  token; with none observed the Score is `unverifiable` / `evidence_missing`, never a pass —
  a non-streaming Target cannot show how soon it started to answer. A Turn with no
  `llm_call` that holds an HTTP `response` Span reads the Span's observed first-frame
  latency (`agentdiag.time_to_first_frame_ms`, phase-8 decision 7) instead, and the Score
  cites that Span and is grounded `observed`.

**Net of Backend start-up** (phase-5 decision 45). The first `llm_call` of a Claude Code
session encloses the CLI's start-up as well as the model's answer, and records how long it
took as `agentdiag.backend.startup_ms` (decision 36). That time is the Backend's, not the
Target's, so these two Evals subtract it before holding the Target to `max_ms`: a Turn's
value is its duration minus the start-up of every `llm_call` inside it, and a first token's
is its arrival minus the start-up of the `llm_call` that carried it and of every `llm_call`
of its Turn that opened before it. The rationale says how much was excluded, and
`Score.value` is the net value; the Score cites every `llm_call` whose start-up it
subtracted beside the Turns. The Trace is not changed — the Span's duration stays the
Metric it measured (decision 4, ADR-0001), and `show` prints the start-up beside that
`llm_call` — so only what the Eval compares moves. A start-up that is not a finite,
non-negative number, or larger than the time that encloses it, can only be agentdiag's own
bug: the Score is `invalid` / `agentdiag`, never a negative value. `tool_latency` reads
tool Spans, which no start-up lies inside.

With no Span to measure there is no value, and the Score is `unverifiable` /
`evidence_missing`.

**Imported evidence** (ticket 26, phase-6 decisions 34, 35 and 37). A Span whose times are
not the evidence's own (`timestamps` not observed: a conversation record without `at`; a
`start_time` or `end_time` the Trace had to clamp) has no duration worth measuring, nor has
a tool Span whose result was not observed or whose ends are only the proxy rows around it,
so an Eval reading one is `unverifiable` / `evidence_missing`, never a pass on a made-up
interval. A Turn a staff member answered (`agentdiag.turn.answered_by: operator`) is not the
Target's response and `response_latency` does not read it. A proxy row's `llm_call` ends at
`created_at + latency_ms`, the proxy's measurement rather than the model's exact end
(`end_time_exact` not observed): that is still the row's latency, so `response_latency`
holds it to `max_ms` and cites those `llm_call` Spans beside the Turns, saying which ends
were reconstructed."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated

from pydantic import Field, StrictFloat, StrictInt

from agentdiag.eval.mechanical import Name, Parameters, Reading, perform_mechanical, span_ids
from agentdiag.eval.score import Score
from agentdiag.eval.spec import EvalContext, EvalSpec
from agentdiag.trace.attributes import BACKEND_STARTUP_MS, TIME_TO_FIRST_FRAME_MS, TURN_ANSWERED_BY
from agentdiag.trace.spans import LLM_CALL_KIND, Span, metrics, not_observed
from agentdiag.types import NotObservedFact

OPERATOR = "operator"

Milliseconds = Annotated[StrictInt | StrictFloat, Field(ge=0)]


class Threshold(Parameters):
    """Where pass turns into fail, in milliseconds."""

    max_ms: Milliseconds


class LatencyParameters(Parameters):
    """`response_latency` and `first_token_latency`: a threshold and nothing else."""

    threshold: Threshold


class ToolLatencyParameters(LatencyParameters):
    """`tool_latency`: a threshold, optionally over one tool's Spans."""

    tool: Name | None = None


def _durations(reading: Reading, spans: Sequence[Span]) -> list[tuple[Span, int]]:
    """Each finished Span with its `duration_ms` Metric; an open Span has none (D11)."""
    measured: list[tuple[Span, int]] = []
    for span in spans:
        duration = metrics(span, reading.context.events).duration_ms
        if duration is not None:
            measured.append((span, duration))
    return measured


def _held(
    reading: Reading, value: float, maximum: float, rationale: str, spans: list[Span]
) -> Score:
    """The Metric's Score: its value against `max_ms`, citing what was measured."""
    return reading.score(
        "pass" if value <= maximum else "fail",
        f"{rationale}, against a maximum of {maximum:g} ms",
        evidence=spans,
        read=spans,
        value=float(value),
    )


def _nothing_measured(reading: Reading, what: str, spans: list[Span]) -> Score:
    return reading.score(
        "unverifiable",
        f"{reading.scope()} holds no {what} to measure",
        evidence=spans,
        read=spans,
        reason="evidence_missing",
    )


TIMELESS_TURN_FACTS: frozenset[NotObservedFact] = frozenset(
    {"timestamps", "start_time", "end_time"}
)
"""A Turn marked with any of these has no duration of the evidence's own: its times follow a
record's order, or the Trace could not hold an instant the evidence gave."""

TIMELESS_TOOL_FACTS: frozenset[NotObservedFact] = TIMELESS_TURN_FACTS | {
    "result",
    "end_time_exact",
}
"""A tool Span missing its result, or whose ends are only the rows around it, has no
measured duration either."""

END_NOT_EXACT: NotObservedFact = "end_time_exact"


def _untimed(
    reading: Reading, spans: list[Span], facts: frozenset[NotObservedFact]
) -> Score | None:
    """`unverifiable` / `evidence_missing` when a Span read has no times of its own."""
    unmeasured = [span for span in spans if set(not_observed(span)) & facts]
    if not unmeasured:
        return None
    missing = sorted({fact for span in unmeasured for fact in not_observed(span)} & facts)
    return reading.score(
        "unverifiable",
        f"{span_ids(unmeasured)} record {', '.join(missing)} as not observed, so how long "
        f"{'it' if len(unmeasured) == 1 else 'they'} took is not known",
        evidence=unmeasured,
        read=spans,
        reason="evidence_missing",
    )


def _reconstructed_ends(reading: Reading, turns: list[Span]) -> list[Span]:
    """The `llm_call` Spans of these Turns whose end was reconstructed, not observed."""
    return [
        span
        for turn in turns
        for span in _llm_calls_of(reading, turn)
        if END_NOT_EXACT in not_observed(span)
    ]


_UNKNOWN_LATENCY = (
    "the start-up agentdiag recorded is its own bug, so the Target's latency is not known"
)


class _StartupBug(Exception):
    """A recorded start-up these Evals cannot subtract: agentdiag's own bug (decision 45)."""

    def __init__(self, rationale: str, spans: list[Span]) -> None:
        super().__init__(rationale)
        self.rationale = rationale
        self.spans = spans


def _ms(value: float) -> str:
    """A number of milliseconds for a rationale: no decimal point when integral, else at
    most three decimals, so a float never prints as `0.30000000000000004` (decision 45 v)."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _startup_ms(span: Span, turn: Span) -> float:
    """The Backend start-up `span` recorded, ms (decision 36); 0 when it recorded none.

    A recorded 0 is nothing subtracted too: the rationale is unchanged and the Span is not
    cited. A value that is not a finite, non-negative number raises `_StartupBug`, citing
    `turn` and `span`.
    """
    if BACKEND_STARTUP_MS not in span.attributes:
        return 0
    value = span.attributes[BACKEND_STARTUP_MS]
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        problem = "not a number of milliseconds"
    elif value < 0:
        problem = "negative"
    else:
        return value
    raise _StartupBug(
        f"{span.span_id} records {value!r} as its Backend start-up ({BACKEND_STARTUP_MS}), "
        f"which is {problem}; {_UNKNOWN_LATENCY}",
        [turn, span],
    )


def _exceeds(
    startup_spans: list[Span], startup: float, measured: float, turn: Span, what: str
) -> _StartupBug:
    """A start-up larger than the time `what` names, which encloses it: agentdiag's bug."""
    records = "records" if len(startup_spans) == 1 else "record between them"
    return _StartupBug(
        f"{span_ids(startup_spans)} {records} {_ms(startup)} ms of Backend start-up, more "
        f"than the {_ms(measured)} ms {what}; {_UNKNOWN_LATENCY}",
        [turn, *startup_spans],
    )


def _invalid(reading: Reading, bug: _StartupBug, read: list[Span]) -> Score:
    """The Score of a start-up that cannot be subtracted: `invalid` / `agentdiag` / `none`,
    citing the Turn and the Spans that recorded it."""
    return reading.score(
        "invalid",
        bug.rationale,
        evidence=bug.spans,
        read=read,
        fault_source="agentdiag",
        fault_direction="none",
    )


@dataclass(frozen=True)
class _NetLatency:
    """One Turn's latency as the Target's: what was measured, less the Backend's start-up."""

    turn: Span
    value: float
    excluded: float
    startup_spans: list[Span]
    """The `llm_call` Spans whose start-up was excluded."""

    measured_on: Span | None = None
    """The `response` Span whose first frame was read, for a Turn with no `llm_call`."""


def _llm_calls_of(reading: Reading, turn: Span) -> list[Span]:
    """The `llm_call` Spans of `turn`, by their Turn number (decision 45 vi), in
    `dotted_order`."""
    return [
        span
        for span in reading.context.spans
        if span.kind == LLM_CALL_KIND and span.turn == turn.turn
    ]


def _net_of(spans: list[Span], turn: Span) -> tuple[float, list[Span]]:
    """The start-up these Spans recorded between them, and the Spans that recorded any."""
    excluded = 0.0
    startup_spans: list[Span] = []
    for span in spans:
        startup = _startup_ms(span, turn)
        if startup:
            startup_spans.append(span)
            excluded += startup
    return excluded, startup_spans


def _excluding(excluded: float) -> str:
    """The rationale's account of the start-up subtracted; nothing when none was."""
    return f", excluding {_ms(excluded)} ms of Backend start-up" if excluded else ""


def _cited(turns: list[Span], nets: Sequence[_NetLatency]) -> list[Span]:
    """The Turns, then every `llm_call` whose start-up an Eval subtracted, then every
    `response` Span a first frame was read from."""
    return [
        *turns,
        *(span for net in nets for span in net.startup_spans),
        *(net.measured_on for net in nets if net.measured_on is not None),
    ]


def tool_latency(context: EvalContext) -> Score:
    """The longest tool Span against `max_ms`."""
    return perform_mechanical(TOOL_LATENCY, context, ToolLatencyParameters, _tool_latency)


def _tool_latency(reading: Reading, parameters: ToolLatencyParameters) -> Score:
    spans = reading.tool_spans(parameters.tool)
    if (gated := reading.gate(spans)) is not None:
        return gated
    if (untimed := _untimed(reading, spans, TIMELESS_TOOL_FACTS)) is not None:
        return untimed
    measured = _durations(reading, spans)
    if not measured:
        return _nothing_measured(reading, f"finished {parameters.tool or 'tool'} Span", spans)
    slowest, longest = max(measured, key=lambda pair: pair[1])
    return _held(
        reading,
        longest,
        parameters.threshold.max_ms,
        f"The slowest of {len(measured)} tool Spans ({span_ids(spans)}) was "
        f"{slowest.span_id} at {longest} ms",
        spans,
    )


def response_latency(context: EvalContext) -> Score:
    """The longest Turn against `max_ms`."""
    return perform_mechanical(RESPONSE_LATENCY, context, LatencyParameters, _response_latency)


def _response_latency(reading: Reading, parameters: LatencyParameters) -> Score:
    # A Turn a staff member answered measures the staff member, not the Target (decision 35).
    turns = [
        span for span in reading.turn_spans() if span.attributes.get(TURN_ANSWERED_BY) != OPERATOR
    ]
    if (gated := reading.gate(turns)) is not None:
        return gated
    if (untimed := _untimed(reading, turns, TIMELESS_TURN_FACTS)) is not None:
        return untimed
    measured = _durations(reading, turns)
    if not measured:
        return _nothing_measured(reading, "finished Turn", turns)
    try:
        nets = [_net_turn(reading, turn, duration) for turn, duration in measured]
    except _StartupBug as bug:
        return _invalid(reading, bug, [*turns, *bug.spans])
    slowest = max(nets, key=lambda net: net.value)
    reconstructed = _reconstructed_ends(reading, turns)
    cited = _cited(turns, nets)
    cited += [span for span in reconstructed if span.span_id not in {c.span_id for c in cited}]
    ends = (
        f" (the ends of {span_ids(reconstructed)} are the proxy's created_at plus latency, "
        "reconstructed)"
        if reconstructed
        else ""
    )
    return _held(
        reading,
        slowest.value,
        parameters.threshold.max_ms,
        f"The slowest Turn was {slowest.turn.span_id} at {_ms(slowest.value)} ms"
        f"{_excluding(slowest.excluded)}{ends}",
        cited,
    )


def _net_turn(reading: Reading, turn: Span, duration: int) -> _NetLatency:
    """A Turn's duration less the start-up of every `llm_call` inside it (decision 45)."""
    excluded, startup_spans = _net_of(_llm_calls_of(reading, turn), turn)
    if excluded > duration:
        raise _exceeds(
            startup_spans, excluded, duration, turn, f"{turn.span_id} measured around it"
        )
    return _NetLatency(turn, duration - excluded, excluded, startup_spans)


def first_token_latency(context: EvalContext) -> Score:
    """The latest first token of any Turn against `max_ms`."""
    return perform_mechanical(FIRST_TOKEN_LATENCY, context, LatencyParameters, _first_token_latency)


def _first_token_latency(reading: Reading, parameters: LatencyParameters) -> Score:
    turns = reading.turn_spans()
    models = reading.model_spans()
    modelled = {span.turn for span in models}
    responses = [span for span in reading.response_spans() if span.turn not in modelled]
    read = [*turns, *models, *responses]
    if (gated := reading.gate(read)) is not None:
        return gated
    try:
        observed = _first_tokens(reading, turns, responses)
    except _StartupBug as bug:
        return _invalid(reading, bug, read)
    if not observed:
        return reading.score(
            "unverifiable",
            f"No Turn in {reading.scope()} observed a first token (the responses were not "
            "streamed), so how soon the Target started to answer is not known",
            evidence=turns,
            read=read,
            reason="evidence_missing",
        )
    latest = max(observed, key=lambda net: net.value)
    return _held(
        reading,
        latest.value,
        parameters.threshold.max_ms,
        f"The latest first token was {_ms(latest.value)} ms into {latest.turn.span_id}"
        f"{_excluding(latest.excluded)}",
        _cited(turns, observed),
    )


def _first_tokens(
    reading: Reading, turns: list[Span], responses: Sequence[Span] = ()
) -> list[_NetLatency]:
    """Per Turn, how long after it opened its first streamed token arrived, when observed,
    less the start-up of the `llm_call` that carried it and of every `llm_call` of the Turn
    that opened before it (decision 45 i): a session's start-up sits in the Turn's first
    call whichever call streams. A Turn with no `llm_call` reads its `response` Span's first
    frame instead (phase-8 decision 7)."""
    events = reading.context.events
    observed: list[_NetLatency] = []
    for turn in turns:
        calls = _llm_calls_of(reading, turn)
        if not calls:
            frame = _first_frame(turn, responses)
            if frame is not None:
                observed.append(frame)
            continue
        for span in calls:
            first = metrics(span, events).time_to_first_token_ms
            if first is None:
                continue
            arrived = span.start_ms - turn.start_ms + first
            # Only the calls opened by the carrying one's start hold start-up inside the
            # first token; a later call's value is never read, so a bad one is not invalid.
            before = [call for call in calls if call.start_ms <= span.start_ms]
            excluded, startup_spans = _net_of(before, turn)
            if excluded > arrived:
                raise _exceeds(
                    startup_spans,
                    excluded,
                    arrived,
                    turn,
                    f"into {turn.span_id} at which {span.span_id}'s first token arrived",
                )
            observed.append(_NetLatency(turn, arrived - excluded, excluded, startup_spans))
            break
    return observed


def _first_frame(turn: Span, responses: Sequence[Span]) -> _NetLatency | None:
    """How long into `turn` its HTTP reply's first Frame arrived, from the first `response`
    Span of the Turn that observed one; None when none did."""
    for span in responses:
        first = span.attributes.get(TIME_TO_FIRST_FRAME_MS)
        if span.turn != turn.turn or isinstance(first, bool) or not isinstance(first, int | float):
            continue
        arrived = span.start_ms - turn.start_ms + first
        return _NetLatency(turn, arrived, 0.0, [], measured_on=span)
    return None


def _metric(
    name: str, perform_row: object, parameters: type[LatencyParameters], **fields: object
) -> EvalSpec:
    return EvalSpec.model_validate(
        {
            "name": name,
            "kind": "mechanical",
            "direction": "minimize",
            "primary": "threshold",
            "metric": True,
            "perform": perform_row,
            "params_model": parameters,
            **fields,
        }
    )


TOOL_LATENCY = _metric(
    "tool_latency",
    tool_latency,
    ToolLatencyParameters,
    min_fidelity="reconstructed",
    tool_family=True,
    answers="how long did the tool take",
)
RESPONSE_LATENCY = _metric(
    "response_latency",
    response_latency,
    LatencyParameters,
    min_fidelity="observed",
    answers="how long did the response take",
)
FIRST_TOKEN_LATENCY = _metric(
    "first_token_latency",
    first_token_latency,
    LatencyParameters,
    min_fidelity="observed",
    answers="how long did the response take",
)

SPECS: tuple[EvalSpec, ...] = (TOOL_LATENCY, RESPONSE_LATENCY, FIRST_TOKEN_LATENCY)
"""This module's rows, in the catalogue's order (D21)."""

MANIFEST_LATENCY_KEYS: dict[str, str] = {
    RESPONSE_LATENCY.name: "response_max_ms",
    FIRST_TOKEN_LATENCY.name: "first_token_max_ms",
    TOOL_LATENCY.name: "tool_max_ms",
}
"""Which key of the Manifest's `eval_parameters.latency` is each Eval's default `max_ms`
(phase-6 decision 17): a declaration that writes no threshold is held to it, and
`run.json` records that it came from the Manifest."""


__all__ = [
    "MANIFEST_LATENCY_KEYS",
    "SPECS",
    "LatencyParameters",
    "Threshold",
    "ToolLatencyParameters",
    "first_token_latency",
    "response_latency",
    "tool_latency",
]
