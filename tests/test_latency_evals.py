"""The two response-latency Evals measure the Target net of recorded Backend start-up
(ticket 22, phase-5 decision 45).

The first `llm_call` of a Claude Code session encloses the CLI's start-up, recorded on that
Span as `agentdiag.backend.startup_ms` (decision 36). `response_latency` and
`first_token_latency` hold the Target to `max_ms` without it; the Trace itself is not
changed. Every Trace here is a committed fixture, or a copy of one changed in code in the
one way the test is about — never a hand-written file — and is scored through
`perform_evals`, as a Run scores it. `tests/test_mechanical_evals.py` holds the latency
Evals' other tests; this file is the start-up's.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from agentdiag.eval.perform import perform_evals
from agentdiag.eval.score import Score
from agentdiag.scenario.models import Scenario
from agentdiag.trace import Event, project_spans, read_trace
from agentdiag.trace.attributes import BACKEND_STARTUP_MS

TRACES = Path(__file__).resolve().parent / "fixtures" / "traces"

CANCEL = "cancel-processing-order"
"""`turn-1` took 2396 ms; `llm_call-1` opened 4 ms into it and took 945 ms, then
`llm_call-2` (800 ms) and `llm_call-3` (633 ms)."""

WHERE = "where-is-shipped-order"

COMMITTED = sorted(path.name.removesuffix(".trace.jsonl") for path in TRACES.glob("*.jsonl"))


def trace(name: str) -> list[Event]:
    return read_trace(TRACES / f"{name}.trace.jsonl")


def scored(declaration: Any, events: Sequence[Event]) -> Score:
    """The one Score a Scenario declaring only `declaration` gets for this Trace."""
    scenario = Scenario.model_validate(
        {"id": "s", "title": "One declaration", "turns": ["Hello"], "evals": [declaration]}
    )
    (only,) = perform_evals(
        scenario,
        trace_events=events,
        fidelity="instrumented",
        forbidden_phrases=None,
        tool_kinds={"lookup_order": "retrieval", "cancel_order": "action"},
    )
    return only


def with_startup(events: Sequence[Event], span_id: str, startup_ms: Any) -> list[Event]:
    """The Trace as the Claude Code Backend writes it: `span_id`'s `span/end` attributes
    carry the CLI's start-up (decision 36)."""
    changed = []
    for event in events:
        if event.type == "span/end" and event.span_id == span_id:
            attributes = dict((event.model_extra or {}).get("attributes") or {})
            attributes[BACKEND_STARTUP_MS] = startup_ms
            event = event.model_copy(update={"attributes": attributes})
        changed.append(event)
    return changed


def streamed(events: Sequence[Event], span_id: str, first_token_ms: int) -> list[Event]:
    """The Trace as if `span_id` had been streamed: its response observed a first token."""
    return [
        event.model_copy(update={"time_to_first_token_ms": first_token_ms})
        if event.type == "response" and event.span_id == span_id
        else event
        for event in events
    ]


# --- response_latency ---


def test_a_turn_is_held_to_max_ms_net_of_the_backend_start_up_its_llm_call_recorded() -> None:
    """2396 ms of Turn, 900 of them the CLI starting: the Target took 1496 ms."""
    started = with_startup(trace(CANCEL), "llm_call-1", 900)

    with_it_in = scored({"response_latency": {"max_ms": 2000}}, trace(CANCEL))
    net = scored({"response_latency": {"max_ms": 2000}}, started)

    assert (with_it_in.verdict, with_it_in.value) == ("fail", 2396.0)
    assert (net.verdict, net.value, net.threshold) == ("pass", 1496.0, {"max_ms": 2000})
    assert net.rationale == (
        "The slowest Turn was turn-1 at 1496 ms, excluding 900 ms of Backend start-up, "
        "against a maximum of 2000 ms"
    )
    assert net.evidence == ["turn-1", "llm_call-1"]


def test_the_trace_keeps_the_turns_measured_duration_when_start_up_is_excluded() -> None:
    """Metrics are fact (ADR-0001): only what the Eval measures moves, never the Span."""
    started = with_startup(trace(CANCEL), "llm_call-1", 900)

    (turn,) = [span for span in project_spans(started) if span.span_id == "turn-1"]

    assert turn.end_ms is not None and turn.end_ms - turn.start_ms == 2396


def test_the_exclusion_is_the_sum_over_every_llm_call_span_inside_the_turn() -> None:
    """Only the first `llm_call` of a session carries start-up; the sum reads them all."""
    once = with_startup(trace(CANCEL), "llm_call-1", 900)
    twice = with_startup(once, "llm_call-2", 400)

    first_only = scored({"response_latency": {"max_ms": 2000}}, once)
    both = scored({"response_latency": {"max_ms": 2000}}, twice)

    assert (first_only.value, first_only.evidence) == (1496.0, ["turn-1", "llm_call-1"])
    assert both.value == 1096.0
    assert both.rationale.startswith(
        "The slowest Turn was turn-1 at 1096 ms, excluding 1300 ms of Backend start-up"
    )
    assert both.evidence == ["turn-1", "llm_call-1", "llm_call-2"]


def test_the_slowest_turn_is_chosen_by_its_net_value() -> None:
    """Turn 1 measured 16 ms and Turn 2 15 ms; net of 5 ms of start-up Turn 1 took 11."""
    events = with_startup(trace("lookup-then-cancel"), "llm_call-1", 5)

    score = scored({"response_latency": {"max_ms": 30000}}, events)

    assert score.value == 15.0
    assert score.rationale == "The slowest Turn was turn-2 at 15 ms, against a maximum of 30000 ms"
    assert score.evidence == ["turn-1", "turn-2", "llm_call-1"]


def test_a_trace_without_start_up_scores_its_measured_turn() -> None:
    score = scored({"response_latency": {"max_ms": 30000}}, trace(WHERE))

    assert score.model_dump() == {
        "eval": "response_latency",
        "eval_id": None,
        "verdict": "pass",
        "value": 16.0,
        "threshold": {"max_ms": 30000},
        "direction": "minimize",
        "rationale": "The slowest Turn was turn-1 at 16 ms, against a maximum of 30000 ms",
        "reason": None,
        "fault_source": None,
        "fault_direction": None,
        "evidence": ["turn-1"],
        "source": {
            "kind": "mechanical",
            "requested_model": None,
            "resolved_model": None,
            "prompt_version": None,
            "code": "agentdiag.eval.latency:response_latency",
            "judge_fingerprint": None,
            "suppressions_in_force": [],
        },
        "fidelity": "instrumented",
        "shared_model": None,
        "cites_read": [],
    }


@pytest.mark.parametrize("fixture", COMMITTED)
def test_no_committed_trace_carries_start_up_so_each_scores_its_measured_turn(
    fixture: str,
) -> None:
    events = trace(fixture)
    spans = project_spans(events)
    turns = [s for s in spans if s.kind == "turn" and s.end_ms is not None]
    slowest = max(turns, key=lambda span: (span.end_ms or 0) - span.start_ms)
    measured = (slowest.end_ms or 0) - slowest.start_ms

    score = scored({"response_latency": {"max_ms": 30000}}, events)

    assert not any(BACKEND_STARTUP_MS in span.attributes for span in spans)
    assert score.value == float(measured)
    assert score.rationale == (
        f"The slowest Turn was {slowest.span_id} at {measured} ms, against a maximum of 30000 ms"
    )
    assert score.evidence == [turn.span_id for turn in turns]


def test_a_start_up_larger_than_its_turn_is_agentdiags_own_bug_never_a_negative_value() -> None:
    score = scored(
        {"response_latency": {"max_ms": 2000}}, with_startup(trace(CANCEL), "llm_call-1", 3000)
    )

    assert (score.verdict, score.fault_source, score.fault_direction, score.value) == (
        "invalid",
        "agentdiag",
        "none",
        None,
    )
    assert "llm_call-1" in score.rationale
    assert "3000 ms of Backend start-up" in score.rationale
    assert "2396 ms" in score.rationale
    assert score.evidence == ["turn-1", "llm_call-1"]


@pytest.mark.parametrize("recorded", [-5, "900", True, None])
def test_a_start_up_that_is_not_a_non_negative_number_is_agentdiags_own_bug(
    recorded: Any,
) -> None:
    score = scored(
        {"response_latency": {"max_ms": 2000}},
        with_startup(trace(CANCEL), "llm_call-1", recorded),
    )

    assert (score.verdict, score.fault_source, score.fault_direction) == (
        "invalid",
        "agentdiag",
        "none",
    )
    assert "llm_call-1" in score.rationale
    assert repr(recorded) in score.rationale
    assert score.evidence == ["turn-1", "llm_call-1"]


def test_a_start_up_spelled_as_a_float_reads_as_whole_milliseconds() -> None:
    score = scored(
        {"response_latency": {"max_ms": 2000}}, with_startup(trace(CANCEL), "llm_call-1", 900.0)
    )

    assert score.value == 1496.0
    assert score.rationale == (
        "The slowest Turn was turn-1 at 1496 ms, excluding 900 ms of Backend start-up, "
        "against a maximum of 2000 ms"
    )


def test_a_fractional_start_up_is_spelled_to_at_most_three_decimals() -> None:
    score = scored(
        {"response_latency": {"max_ms": 3000}},
        with_startup(trace(CANCEL), "llm_call-1", 0.1 + 0.2),
    )

    assert score.value == pytest.approx(2395.7)
    assert score.rationale == (
        "The slowest Turn was turn-1 at 2395.7 ms, excluding 0.3 ms of Backend start-up, "
        "against a maximum of 3000 ms"
    )


# --- first_token_latency ---


def test_a_first_token_is_held_to_max_ms_net_of_its_llm_calls_start_up() -> None:
    """`llm_call-1` opened 4 ms into the Turn; a first token 900 ms later, 600 of them the
    CLI starting, reached the user 304 ms into the Target's answer."""
    events = streamed(trace(CANCEL), "llm_call-1", 900)
    started = with_startup(events, "llm_call-1", 600)

    with_it_in = scored({"first_token_latency": {"max_ms": 500}}, events)
    net = scored({"first_token_latency": {"max_ms": 500}}, started)

    assert (with_it_in.verdict, with_it_in.value) == ("fail", 904.0)
    assert (net.verdict, net.value) == ("pass", 304.0)
    assert net.rationale == (
        "The latest first token was 304 ms into turn-1, excluding 600 ms of Backend "
        "start-up, against a maximum of 500 ms"
    )
    assert net.evidence == ["turn-1", "llm_call-1"]


def test_a_first_token_without_start_up_scores_its_measured_first_token() -> None:
    score = scored(
        {"first_token_latency": {"max_ms": 1000}}, streamed(trace(WHERE), "llm_call-1", 5)
    )

    assert (score.verdict, score.value, score.evidence) == ("pass", 7.0, ["turn-1"])
    assert score.rationale == (
        "The latest first token was 7 ms into turn-1, against a maximum of 1000 ms"
    )


def test_a_start_up_larger_than_its_first_token_is_agentdiags_own_bug() -> None:
    events = with_startup(streamed(trace(CANCEL), "llm_call-1", 400), "llm_call-1", 600)

    score = scored({"first_token_latency": {"max_ms": 500}}, events)

    assert (score.verdict, score.fault_source, score.fault_direction, score.value) == (
        "invalid",
        "agentdiag",
        "none",
        None,
    )
    assert "llm_call-1" in score.rationale
    assert "600 ms of Backend start-up" in score.rationale
    assert "404 ms" in score.rationale
    assert score.evidence == ["turn-1", "llm_call-1"]


def test_a_first_token_is_net_of_the_start_up_of_an_earlier_llm_call_in_its_turn() -> None:
    """The session's start-up sits in the Turn's first call even when only a later one
    streams: `llm_call-2` opened 953 ms into the Turn, its first token 100 ms later."""
    events = streamed(trace(CANCEL), "llm_call-2", 100)
    started = with_startup(events, "llm_call-1", 900)

    with_it_in = scored({"first_token_latency": {"max_ms": 500}}, events)
    net = scored({"first_token_latency": {"max_ms": 500}}, started)

    assert (with_it_in.verdict, with_it_in.value) == ("fail", 1053.0)
    assert (net.verdict, net.value) == ("pass", 153.0)
    assert net.rationale == (
        "The latest first token was 153 ms into turn-1, excluding 900 ms of Backend "
        "start-up, against a maximum of 500 ms"
    )
    assert net.evidence == ["turn-1", "llm_call-1"]


def test_a_bad_start_up_on_an_earlier_llm_call_makes_the_first_token_agentdiags_own_bug() -> None:
    events = with_startup(streamed(trace(CANCEL), "llm_call-2", 100), "llm_call-1", -5)

    score = scored({"first_token_latency": {"max_ms": 500}}, events)

    assert (score.verdict, score.fault_source, score.fault_direction) == (
        "invalid",
        "agentdiag",
        "none",
    )
    assert "llm_call-1" in score.rationale and "-5" in score.rationale
    assert score.evidence == ["turn-1", "llm_call-1"]


def test_a_start_up_on_an_llm_call_after_the_first_token_is_not_read() -> None:
    """`llm_call-2` opened after `llm_call-1`'s first token: nothing it records is in it."""
    events = with_startup(streamed(trace(CANCEL), "llm_call-1", 400), "llm_call-2", "x")

    score = scored({"first_token_latency": {"max_ms": 500}}, events)

    assert (score.verdict, score.value, score.evidence) == ("pass", 404.0, ["turn-1"])
