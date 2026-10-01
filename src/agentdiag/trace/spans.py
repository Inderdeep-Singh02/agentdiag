"""Spans and Metrics, projected over the Events (ADR-0004 section 2, D11).

Nothing here is ever written to disk. A Span is its `span/start` and `span/end` pair; a
duration is a subtraction over two Events. The projection is a pure function of a read
Trace, so a Metric cannot disagree with the recording that produced it.

Cost is the one Metric not computed here: it depends on a price table that can change, so
it is written on the Span at capture time (OpenInference's `llm.cost.total`, phase-5
decision 4) and only read back. Recomputing it would let a later price edit rewrite what
a recorded Run cost.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentdiag.trace.attributes import NOT_OBSERVED
from agentdiag.trace.events import Event
from agentdiag.trace.openinference import COST_TOTAL
from agentdiag.types import Actor, Fidelity

TOKEN_ATTRIBUTES: dict[str, str] = {
    "gen_ai.usage.input_tokens": "input",
    "gen_ai.usage.output_tokens": "output",
    "gen_ai.usage.cache_read.input_tokens": "cache_read",
    "gen_ai.usage.cache_write.input_tokens": "cache_write",
    "gen_ai.usage.reasoning.output_tokens": "reasoning",
}
"""OTel GenAI usage attributes, mapped to the open token map's keys (ADR-0006 section 4)."""

LLM_CALL_KIND = "llm_call"
"""The Target's model call (ADR-0006 §2)."""

RESPONSE_KIND = "response"
"""One HTTP Turn's request and its streamed reply, as the Target's response surface showed
it, at Fidelity `observed` (phase-8 decision 7): drawn like an `llm_call` by `show` and the
Report, though it shows no model call and no tokens (`agentdiag.not_observed` says so)."""

MODEL_SPAN_KINDS = frozenset({LLM_CALL_KIND, "judge"})
"""The Span kinds whose `request` and `response` Events bound a model's own latency."""


def is_own_span(span: Span) -> bool:
    """Whether a Span is agentdiag's own model call inside a Trial rather than the Target's:
    the Simulated User's (any Span of actor `simulated_user`) or the stop check's (an
    `llm_call` of actor `agentdiag`). The Judge is never shown these, and the mechanical
    Evals never read them (phase-5 decision 59)."""
    return span.actor == "simulated_user" or (
        span.kind == LLM_CALL_KIND and span.actor == "agentdiag"
    )


class Span(BaseModel):
    """One timed unit inside a Trace, derived from its opening and closing Events."""

    span_id: str
    parent_span_id: str | None
    kind: str
    name: str
    actor: Actor
    fidelity: Fidelity
    turn: int | None
    dotted_order: str
    start_ms: int
    end_ms: int | None
    """None while the Span is open: its `span/start` has no matching `span/end`."""

    status: Literal["ok", "error", "open"]
    attributes: dict[str, Any]
    """The start attributes, updated by the end attributes."""

    events: list[int]
    """`seq` of the Events directly inside this Span, its own start and end included."""


class Metrics(BaseModel):
    """What a Span measured. Facts, never judgements (CONTEXT.md, Metric)."""

    duration_ms: int | None
    model_latency_ms: int | None
    time_to_first_token_ms: int | None
    tokens: dict[str, int]
    """An open map with a reserved `total` key (ADR-0006 section 4)."""

    cost_usd: float | None = None
    """What the Span's model call cost, as recorded on it; None when unpriced or not a
    model Span."""


class ActorTotals(BaseModel):
    """What one actor accounted for across a Trace."""

    duration_ms: int = 0
    """Wall time of this actor's outermost Spans; nested time is not counted twice."""

    tokens: dict[str, int] = Field(default_factory=dict)
    spans: int = 0
    cost_usd: float = 0.0
    """The sum of this actor's priced Spans; an unpriced Span adds nothing to it."""

    unpriced: int = 0
    """This actor's model Spans with no recorded cost: their model is not in the price
    table, so `show` says `unpriced` rather than implying the sum covers them."""


class TraceTotals(BaseModel):
    """A Trace summed up per actor. A projection, never written to disk."""

    actors: dict[str, ActorTotals] = Field(default_factory=dict)
    events: int
    spans: int


def project_spans(events: Sequence[Event]) -> list[Span]:
    """Every Span the Events describe, sorted by `dotted_order`.

    One lexicographic sort of `dotted_order` yields execution order, with children after
    their parent and siblings by start (ADR-0006 section 3). An unmatched `span/start` is
    an open Span: `end_ms` None, `status` "open".
    """
    starts: dict[str, Event] = {}
    ends: dict[str, Event] = {}
    for event in events:
        if event.span_id is None:
            continue
        if event.type == "span/start":
            starts[event.span_id] = event
        elif event.type == "span/end":
            ends[event.span_id] = event

    contained: dict[str, list[int]] = {span_id: [] for span_id in starts}
    for event in events:
        if event.span_id is not None and event.span_id in contained:
            contained[event.span_id].append(event.seq)

    spans: list[Span] = []
    for span_id, start in starts.items():
        start_fields = start.model_extra or {}
        end = ends.get(span_id)
        end_fields = end.model_extra or {} if end else {}
        attributes = dict(start_fields.get("attributes") or {})
        attributes.update(end_fields.get("attributes") or {})
        spans.append(
            Span(
                span_id=span_id,
                parent_span_id=start.parent_span_id,
                kind=start_fields.get("kind", ""),
                name=start_fields.get("name", ""),
                actor=start.actor,
                fidelity=start_fields.get("fidelity", "observed"),
                turn=start.turn,
                dotted_order=start_fields.get("dotted_order", ""),
                start_ms=start.ts,
                end_ms=end.ts if end else None,
                status=end_fields.get("status", "ok") if end else "open",
                attributes=attributes,
                events=sorted(contained[span_id]),
            )
        )
    return sorted(spans, key=lambda span: span.dotted_order)


def not_observed(span: Span) -> list[str]:
    """What the Span's evidence could not give, by ADR-0006 §4's not-observed state
    (`agentdiag.not_observed`); empty for a Span that observed everything it records."""
    value = span.attributes.get(NOT_OBSERVED)
    return [str(item) for item in value] if isinstance(value, list) else []


def children(spans: Sequence[Span], span_id: str) -> list[Span]:
    """The Spans directly under `span_id`, in execution order."""
    return [span for span in spans if span.parent_span_id == span_id]


def metrics(span: Span, events: Sequence[Event]) -> Metrics:
    """The Metrics of one Span, computed from the Events inside it."""
    inside = {event.seq for event in events if event.span_id == span.span_id}
    duration = span.end_ms - span.start_ms if span.end_ms is not None else None
    return Metrics(
        duration_ms=duration,
        model_latency_ms=_model_latency_ms(span, events, inside),
        time_to_first_token_ms=_time_to_first_token_ms(events, inside),
        tokens=_tokens(span),
        cost_usd=span_cost(span),
    )


def span_cost(span: Span) -> float | None:
    """The cost the Span recorded, or None when it recorded none (decision 4)."""
    value = span.attributes.get(COST_TOTAL)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def trace_totals(spans: Sequence[Span], events: Sequence[Event]) -> TraceTotals:
    """Per actor: the wall time of that actor's outermost Spans and the tokens they used.

    Nested Spans are not added up — a tool Span's time already lies inside its
    `llm_call`'s — so `duration_ms` sums only the Spans whose parent belongs to another
    actor (or to nothing), while tokens are summed over every Span that reported any.
    """
    by_id = {span.span_id: span for span in spans}
    actors: dict[str, ActorTotals] = {}
    for span in spans:
        totals = actors.setdefault(span.actor, ActorTotals())
        totals.spans += 1
        parent = by_id.get(span.parent_span_id) if span.parent_span_id else None
        if (parent is None or parent.actor != span.actor) and span.end_ms is not None:
            totals.duration_ms += span.end_ms - span.start_ms
        for key, value in _tokens(span).items():
            totals.tokens[key] = totals.tokens.get(key, 0) + value
        cost = span_cost(span)
        if cost is not None:
            totals.cost_usd = round(totals.cost_usd + cost, 6)
        elif span.kind in MODEL_SPAN_KINDS:
            totals.unpriced += 1
    return TraceTotals(actors=actors, events=len(events), spans=len(spans))


def _model_latency_ms(span: Span, events: Sequence[Event], inside: set[int]) -> int | None:
    """The model's own latency: the `response` Event's ts minus the `request` Event's.

    Only for the Span kinds that wrap a model call; a `tool_call` Span has no latency of
    its own beyond its duration.
    """
    if span.kind not in MODEL_SPAN_KINDS:
        return None
    request = _first(events, inside, "request")
    response = _first(events, inside, "response")
    if request is None or response is None:
        return None
    return response.ts - request.ts


def _time_to_first_token_ms(events: Sequence[Event], inside: set[int]) -> int | None:
    """None unless an Event supplied it: nothing non-streaming can observe it."""
    for event in events:
        if event.seq not in inside:
            continue
        reported = (event.model_extra or {}).get("time_to_first_token_ms")
        if isinstance(reported, int):
            return reported
    return None


def _tokens(span: Span) -> dict[str, int]:
    """The token counts a Span's attributes report, with `total` when both halves are there."""
    tokens: dict[str, int] = {}
    for attribute, key in TOKEN_ATTRIBUTES.items():
        value = span.attributes.get(attribute)
        if isinstance(value, int):
            tokens[key] = value
    if "input" in tokens and "output" in tokens:
        tokens["total"] = tokens["input"] + tokens["output"]
    return tokens


def _first(events: Sequence[Event], inside: set[int], type: str) -> Event | None:
    for event in events:
        if event.seq in inside and event.type == type:
            return event
    return None


__all__ = [
    "LLM_CALL_KIND",
    "MODEL_SPAN_KINDS",
    "RESPONSE_KIND",
    "TOKEN_ATTRIBUTES",
    "ActorTotals",
    "Metrics",
    "Span",
    "TraceTotals",
    "children",
    "is_own_span",
    "metrics",
    "not_observed",
    "project_spans",
    "span_cost",
    "trace_totals",
]
