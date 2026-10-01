"""The mechanical Evals, over committed Trace fixtures and hand-lowered copies of them (D21).

These drive `perform_evals` — the function a Run hands each finished Trace to — with the
Traces `scripts/record_fixtures.py` generated from the toy Target, so every Score here is
one a Run would write. What varies between a case and the fixture is only the one thing the
case is about: a Fidelity lowered, a Span removed, an argument or a message changed, all
made in code from the committed file rather than written by hand. `test_mechanical_run_cli`
runs the same Scenarios through `agentdiag run`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from agentdiag.eval.perform import perform_evals
from agentdiag.eval.registry import REGISTRY
from agentdiag.eval.score import Score
from agentdiag.run.scorecard import negate
from agentdiag.scenario.models import Scenario
from agentdiag.trace import Event, project_spans, read_trace
from agentdiag.types import Fidelity

TRACES = Path(__file__).resolve().parent / "fixtures" / "traces"

CANCEL = "cancel-processing-order"
WHERE = "where-is-shipped-order"
LOOKUP_THEN_CANCEL = "lookup-then-cancel"
STATUS = "status-question-is-not-a-cancel"
UNSEEN = "lookup-with-unseen-arguments"
GREETING = "greeting-calls-no-tool"

MECHANICAL = sorted(name for name, spec in REGISTRY.items() if spec.kind == "mechanical")

ONE_OF_EACH: dict[str, dict[str, Any]] = {
    "expect_tools": {"expect_tools": ["lookup_order"]},
    "expect_tools_order": {"expect_tools_order": ["lookup_order", "cancel_order"]},
    "expect_tools_any": {"expect_tools_any": ["lookup_order"]},
    "forbid_tools": {"forbid_tools": ["cancel_order"]},
    "tool_count_max": {"tool_count_max": {"lookup_order": 1}},
    "expect_tool_args": {
        "expect_tool_args": {"tool": "lookup_order", "args": {"order_id": {"equals": "NB-1042"}}}
    },
    "must_say_any": {"must_say_any": ["cancelled"]},
    "must_not_say": {"must_not_say": ["refund"]},
    "forbidden_phrases": {"forbidden_phrases": ["as an AI"]},
    "tool_latency": {"tool_latency": {"max_ms": 1000}},
    "response_latency": {"response_latency": {"max_ms": 30000}},
    "first_token_latency": {"first_token_latency": {"max_ms": 1000}},
    "tool_argument_types": {"tool_argument_types": {"lookup_order": {"order_id": "string"}}},
}
"""One declaration of every mechanical Eval, as an author writes it."""


def test_one_declaration_of_every_mechanical_eval_is_listed_here() -> None:
    assert sorted(ONE_OF_EACH) == MECHANICAL


# --- helpers: a fixture, and the same fixture changed in one way ---


def trace(name: str) -> list[Event]:
    return read_trace(TRACES / f"{name}.trace.jsonl")


def scored(
    declaration: Any,
    events: Sequence[Event],
    *,
    fidelity: Fidelity = "instrumented",
    forbidden_phrases: list[str] | None = None,
) -> Score:
    """The one Score a Scenario declaring only `declaration` gets for this Trace."""
    scenario = Scenario.model_validate(
        {"id": "s", "title": "One declaration", "turns": ["Hello"], "evals": [declaration]}
    )
    (only,) = perform_evals(
        scenario,
        trace_events=events,
        fidelity=fidelity,
        forbidden_phrases=forbidden_phrases,
        tool_kinds={"lookup_order": "retrieval", "cancel_order": "action"},
    )
    return only


def lowered(
    events: Sequence[Event],
    fidelity: Fidelity,
    *,
    only: Callable[[dict[str, Any]], bool] | None = None,
) -> list[Event]:
    """The Trace with its Spans' Fidelity lowered, all of them or the ones `only` picks."""
    changed = []
    for event in events:
        fields = event.model_extra or {}
        if event.type == "span/start" and (
            only is None or only(fields | {"span_id": event.span_id})
        ):
            event = event.model_copy(update={"fidelity": fidelity})
        changed.append(event)
    return changed


def without_spans(events: Sequence[Event], kind: str) -> list[Event]:
    """The Trace with every Span of `kind` gone, and everything written inside it."""
    gone = {
        event.span_id
        for event in events
        if event.type == "span/start" and (event.model_extra or {}).get("kind") == kind
    }
    return [event for event in events if event.span_id not in gone]


def with_arguments(events: Sequence[Event], span_id: str, **extra: Any) -> list[Event]:
    """The Trace with one `tool/call`'s arguments extended."""
    changed = []
    for event in events:
        if event.type == "tool/call" and event.span_id == span_id:
            arguments = dict((event.model_extra or {})["arguments"])
            event = event.model_copy(update={"arguments": {**arguments, **extra}})
        changed.append(event)
    return changed


def with_target_message(events: Sequence[Event], content: str) -> list[Event]:
    """The Trace with the Target's message replaced by `content`."""
    return [
        event.model_copy(update={"content": content})
        if event.type == "message" and event.actor == "target"
        else event
        for event in events
    ]


def streamed(events: Sequence[Event], first_token_ms: int = 400) -> list[Event]:
    """The Trace as if `llm_call-1` had been streamed: its response observed a first token."""
    return [
        event.model_copy(update={"time_to_first_token_ms": first_token_ms})
        if event.type == "response" and event.span_id == "llm_call-1"
        else event
        for event in events
    ]


def span_ids(events: Sequence[Event]) -> set[str]:
    return {span.span_id for span in project_spans(events)}


# --- every Score cites Spans of the Trace it judged, and its code ---


@pytest.mark.parametrize("name", MECHANICAL)
@pytest.mark.parametrize("fixture", [CANCEL, WHERE, LOOKUP_THEN_CANCEL, STATUS, UNSEEN, GREETING])
def test_every_mechanical_score_cites_only_spans_that_exist_in_its_trace(
    name: str, fixture: str
) -> None:
    events = trace(fixture)

    score = scored(ONE_OF_EACH[name], events)

    assert set(score.evidence) <= span_ids(events)
    assert score.source.kind == "mechanical"
    assert score.source.code == f"{REGISTRY[name].perform.__module__}:{name}"  # type: ignore[union-attr]


@pytest.mark.parametrize("name", MECHANICAL)
def test_every_mechanical_score_that_decided_cites_at_least_one_span(name: str) -> None:
    score = scored(ONE_OF_EACH[name], streamed(trace(CANCEL)))

    assert score.verdict in {"pass", "fail"}
    assert score.evidence


# --- the min-Fidelity gate runs first (D21, ADR-0003 §3) ---


TOOL_EVALS = [name for name in MECHANICAL if REGISTRY[name].min_fidelity == "reconstructed"]
OBSERVED_EVALS = [name for name in MECHANICAL if REGISTRY[name].min_fidelity == "observed"]


@pytest.mark.parametrize("name", TOOL_EVALS)
def test_an_eval_that_needs_reconstructed_evidence_is_unverifiable_on_an_observed_trace(
    name: str,
) -> None:
    score = scored(ONE_OF_EACH[name], lowered(trace(CANCEL), "observed"), fidelity="observed")

    assert (score.verdict, score.reason) == ("unverifiable", "fidelity_too_low")
    assert score.fidelity == "observed"


@pytest.mark.parametrize("name", OBSERVED_EVALS)
def test_an_eval_that_needs_only_observed_evidence_still_decides_on_an_observed_trace(
    name: str,
) -> None:
    events = lowered(streamed(trace(CANCEL)), "observed")

    score = scored(ONE_OF_EACH[name], events, fidelity="observed")

    assert score.verdict in {"pass", "fail"}
    assert score.fidelity == "observed"


def test_one_span_below_the_minimum_is_enough_to_close_the_gate_and_it_is_cited() -> None:
    events = lowered(trace(CANCEL), "observed", only=lambda span: span["span_id"] == "retrieval-1")

    score = scored({"expect_tools": ["lookup_order"]}, events)

    assert (score.verdict, score.reason) == ("unverifiable", "fidelity_too_low")
    assert score.evidence == ["retrieval-1"]


def test_a_score_records_the_lowest_fidelity_among_the_spans_it_read() -> None:
    events = lowered(
        trace(CANCEL), "reconstructed", only=lambda span: span["span_id"] == "retrieval-1"
    )

    score = scored({"expect_tools": ["lookup_order"]}, events)

    assert score.verdict == "pass"
    assert score.fidelity == "reconstructed"


def test_a_score_that_read_no_span_records_the_traces_fidelity() -> None:
    score = scored("forbidden_phrases", trace(CANCEL), forbidden_phrases=[])

    assert score.evidence == []
    assert score.fidelity == "instrumented"


# --- no tool Spans at all is read through Fidelity (phase-5 decision 3) ---


@pytest.mark.parametrize(
    ("declaration", "verdict"),
    [
        ({"expect_tools": ["lookup_order"]}, "fail"),
        ({"expect_tools_order": ["lookup_order", "cancel_order"]}, "fail"),
        ({"expect_tools_any": ["lookup_order", "cancel_order"]}, "fail"),
        ({"expect_tool_args": {"args": {"order_id": {"present": True}}}}, "fail"),
        ({"forbid_tools": ["cancel_order"]}, "pass"),
        ({"tool_count_max": {"cancel_order": 0}}, "pass"),
    ],
)
def test_no_tool_span_in_an_instrumented_trace_is_a_fact_citing_the_llm_calls(
    declaration: dict[str, Any], verdict: str
) -> None:
    """The in-process Adapter saw every model call and every tool: none were called."""
    score = scored(declaration, trace(GREETING))

    assert score.verdict == verdict
    assert score.evidence == ["llm_call-1"]
    assert "a fact" in score.rationale


@pytest.mark.parametrize(
    "declaration",
    [
        {"forbid_tools": ["lookup_order"]},
        {"tool_count_max": {"lookup_order": 0}},
        {"expect_tools": ["lookup_order"]},
        {"expect_tools_any": ["lookup_order"]},
        {"expect_tool_args": {"args": {"order_id": {"present": True}}}},
    ],
)
def test_a_response_that_asked_for_a_tool_no_span_answers_is_unverifiable_never_a_fact(
    declaration: dict[str, Any],
) -> None:
    """Decision 3 reads the responses: `llm_call-1` asked for `lookup_order` and the Trace
    holds no Span of it, so a call may have happened unrecorded (ticket 04 Spec review)."""
    events = without_spans(trace(WHERE), "retrieval")

    score = scored(declaration, events)

    assert (score.verdict, score.reason) == ("unverifiable", "evidence_missing")
    assert score.evidence == ["llm_call-1"]


@pytest.mark.parametrize(
    "declaration",
    [
        {"forbid_tools": ["cancel_order"]},
        {"tool_count_max": {"cancel_order": 0}},
        {"expect_tools": ["cancel_order"]},
        {"expect_tools_order": ["lookup_order", "cancel_order"]},
        {"expect_tool_args": {"tool": "cancel_order", "args": {"order_id": {"present": True}}}},
    ],
)
def test_a_verdict_resting_on_an_absent_call_is_unverifiable_when_that_call_was_requested(
    declaration: dict[str, Any],
) -> None:
    """`llm_call-2` asked for `cancel_order`; with its Span gone, the lookup's still stands."""
    events = without_spans(trace(CANCEL), "tool_call")

    score = scored(declaration, events)

    assert (score.verdict, score.reason) == ("unverifiable", "evidence_missing")
    assert score.evidence == ["llm_call-2"]


def test_a_request_that_no_span_answers_does_not_hide_a_call_the_trace_proves() -> None:
    events = without_spans(trace(CANCEL), "tool_call")

    assert scored({"expect_tools": ["lookup_order"]}, events).verdict == "pass"
    assert scored({"forbid_tools": ["lookup_order"]}, events).verdict == "fail"


@pytest.mark.parametrize(
    "declaration",
    [
        {"expect_tools": ["lookup_order"]},
        {"expect_tools_order": ["lookup_order", "cancel_order"]},
        {"expect_tools_any": ["lookup_order"]},
        {"expect_tool_args": {"args": {"order_id": {"present": True}}}},
        {"forbid_tools": ["cancel_order"]},
        {"tool_count_max": {"cancel_order": 0}},
    ],
)
def test_no_tool_span_in_a_reconstructed_trace_is_unverifiable_never_a_pass(
    declaration: dict[str, Any],
) -> None:
    """Ticket 04's own rule, where it applies: the Adapter may have missed the calls."""
    events = lowered(trace(GREETING), "reconstructed")

    score = scored(declaration, events, fidelity="reconstructed")

    assert (score.verdict, score.reason) == ("unverifiable", "evidence_missing")


@pytest.mark.parametrize("name", TOOL_EVALS)
def test_a_trace_with_no_llm_call_span_either_is_unverifiable_for_every_tool_eval(
    name: str,
) -> None:
    events = without_spans(trace(GREETING), "llm_call")

    score = scored(ONE_OF_EACH[name], events)

    assert (score.verdict, score.reason) == ("unverifiable", "evidence_missing")


# --- the tool lists ---


def test_expect_tools_passes_citing_the_spans_that_satisfied_it() -> None:
    score = scored({"expect_tools": ["lookup_order"]}, trace(WHERE))

    assert (score.verdict, score.evidence) == ("pass", ["retrieval-1"])


def test_expect_tools_fails_naming_the_tool_never_called_and_citing_every_tool_span() -> None:
    score = scored({"expect_tools": ["lookup_order", "cancel_order"]}, trace(WHERE))

    assert (score.verdict, score.evidence) == ("fail", ["retrieval-1"])
    assert "never called cancel_order" in score.rationale


def test_expect_tools_order_passes_on_the_calls_in_order_and_fails_on_them_reversed() -> None:
    events = trace(LOOKUP_THEN_CANCEL)

    in_order = scored({"expect_tools_order": ["lookup_order", "cancel_order"]}, events)
    reversed_ = scored({"expect_tools_order": ["cancel_order", "lookup_order"]}, events)

    assert (in_order.verdict, in_order.evidence) == ("pass", ["retrieval-1", "tool_call-1"])
    assert reversed_.verdict == "fail"


def test_expect_tools_any_fails_when_none_of_the_tools_was_called() -> None:
    score = scored({"expect_tools_any": ["cancel_order", "refund_order"]}, trace(WHERE))

    assert (score.verdict, score.evidence) == ("fail", ["retrieval-1"])


def test_forbid_tools_fails_citing_only_the_span_that_violated_it() -> None:
    score = scored({"forbid_tools": ["cancel_order"]}, trace(STATUS))

    assert (score.verdict, score.evidence) == ("fail", ["tool_call-1"])


def test_tool_count_max_fails_citing_the_calls_over_the_cap() -> None:
    score = scored({"tool_count_max": {"lookup_order": 1}}, trace(UNSEEN))

    assert (score.verdict, score.evidence) == ("fail", ["retrieval-1", "retrieval-2"])
    assert "lookup_order 2 times against a cap of 1" in score.rationale


def test_turn_restricts_a_tool_eval_to_the_spans_of_that_turn() -> None:
    """Decision 7: per-turn expectations are kept losslessly."""
    events = trace(LOOKUP_THEN_CANCEL)

    first = scored({"forbid_tools": {"tools": ["cancel_order"], "turn": 1}}, events)
    second = scored({"forbid_tools": {"tools": ["cancel_order"], "turn": 2}}, events)

    assert (first.verdict, first.evidence) == ("pass", ["retrieval-1"])
    assert (second.verdict, second.evidence) == ("fail", ["tool_call-1"])


# --- the arguments (D21's five operators) ---


def args_score(events: Sequence[Event], **operators: Any) -> Score:
    return scored(
        {"expect_tool_args": {"tool": "lookup_order", "args": {"order_id": operators}}}, events
    )


@pytest.mark.parametrize(
    "operators",
    [
        {"equals": "NB-0917"},
        {"contains": "0917"},
        {"contains": "nb-0917"},
        {"contains": ["1042", "0917"]},
        {"present": True},
        {"none_in": ["0688", "1042"]},
        {"equals": "NB-0917", "contains": "0917", "present": True},
    ],
)
def test_an_argument_that_satisfies_every_operator_passes(operators: dict[str, Any]) -> None:
    score = args_score(trace(WHERE), **operators)

    assert (score.verdict, score.evidence) == ("pass", ["retrieval-1"])


@pytest.mark.parametrize(
    "operators",
    [
        {"equals": "NB-1042"},
        {"contains": "1042"},
        {"contains": ["1042", "0688"]},
        {"none_in": "0917"},
        {"present": False},
    ],
)
def test_an_argument_that_breaks_an_operator_fails_citing_the_call(
    operators: dict[str, Any],
) -> None:
    score = args_score(trace(WHERE), **operators)

    assert (score.verdict, score.evidence) == ("fail", ["retrieval-1"])


def test_min_compares_numbers_and_equals_compares_numbers_numerically() -> None:
    events = with_arguments(trace(WHERE), "retrieval-1", quantity=2)

    def on_quantity(**operators: Any) -> str:
        declaration = {"expect_tool_args": {"args": {"quantity": operators}}}
        return scored(declaration, events).verdict

    assert on_quantity(min=1) == "pass"
    assert on_quantity(min=2) == "pass"
    assert on_quantity(min=3) == "fail"
    assert on_quantity(equals="2") == "pass"
    assert on_quantity(equals=2.0) == "pass"


def test_an_argument_that_was_never_passed_fails_every_operator_but_none_in() -> None:
    events = trace(WHERE)

    def on_reason(**operators: Any) -> str:
        return scored({"expect_tool_args": {"args": {"reason": operators}}}, events).verdict

    assert on_reason(present=True) == "fail"
    assert on_reason(equals="lost") == "fail"
    assert on_reason(min=1) == "fail"
    assert on_reason(none_in=["test"]) == "pass"


def test_arguments_the_adapter_could_not_see_are_unverifiable_never_a_pass() -> None:
    """The lesson: arguments nobody could see are never a pass."""
    score = args_score(trace(UNSEEN), equals="NB-0688")

    assert (score.verdict, score.reason) == ("unverifiable", "evidence_missing")
    assert score.evidence == ["retrieval-1"]


def test_a_seen_call_that_breaks_an_operator_fails_even_beside_an_unseen_one() -> None:
    """A failure the Trace proves is never hidden behind one it cannot see (decision 3)."""
    score = args_score(trace(UNSEEN), equals="NB-1042")

    assert (score.verdict, score.evidence) == ("fail", ["retrieval-2"])


@pytest.mark.parametrize("fidelity", ["instrumented", "reconstructed"])
def test_no_call_to_the_named_tool_fails_once_any_tool_span_exists(fidelity: Fidelity) -> None:
    """Amended decision 3: a tool Span means the record was there, at either Fidelity."""
    declaration = {
        "expect_tool_args": {"tool": "cancel_order", "args": {"order_id": {"present": True}}}
    }

    score = scored(declaration, lowered(trace(WHERE), fidelity), fidelity=fidelity)

    assert (score.verdict, score.evidence) == ("fail", ["retrieval-1"])


def test_a_missing_tool_fails_at_reconstructed_once_any_tool_span_exists() -> None:
    events = lowered(trace(WHERE), "reconstructed")

    score = scored({"expect_tools": ["cancel_order"]}, events, fidelity="reconstructed")

    assert score.verdict == "fail"


def test_without_tool_a_call_that_does_not_supply_the_argument_is_ignored() -> None:
    """A Turn's calls are merged: only `tool_call-1` supplies `quantity`."""
    events = with_arguments(trace(CANCEL), "tool_call-1", quantity=2)

    holds = scored({"expect_tool_args": {"args": {"quantity": {"equals": 2}}}}, events)
    breaks = scored({"expect_tool_args": {"args": {"quantity": {"min": 3}}}}, events)

    assert (holds.verdict, holds.evidence) == ("pass", ["retrieval-1", "tool_call-1"])
    assert (breaks.verdict, breaks.evidence) == ("fail", ["tool_call-1"])


def test_without_tool_an_argument_no_call_supplies_fails_all_but_none_in_and_present_false() -> (
    None
):
    events = trace(CANCEL)

    def on_reason(**operators: Any) -> str:
        return scored({"expect_tool_args": {"args": {"reason": operators}}}, events).verdict

    assert on_reason(equals="lost") == "fail"
    assert on_reason(contains="lost") == "fail"
    assert on_reason(min=1) == "fail"
    assert on_reason(present=True) == "fail"
    assert on_reason(none_in=["test"]) == "pass"
    assert on_reason(present=False) == "pass"


def test_an_operator_that_is_not_one_of_the_five_is_the_scenarios_fault() -> None:
    score = args_score(trace(WHERE), startswith="NB")

    assert (score.verdict, score.fault_source, score.fault_direction) == (
        "invalid",
        "scenario",
        "none",
    )
    assert "startswith" in score.rationale


# --- the text Evals screen the Target's own messages (decision 18) ---


def test_must_say_any_passes_citing_the_turn_whose_message_said_it() -> None:
    score = scored({"must_say_any": ["shipped", "delivered"]}, trace(WHERE))

    assert (score.verdict, score.evidence) == ("pass", ["turn-1"])


def test_phrases_match_case_insensitively() -> None:
    assert scored({"must_say_any": ["SHIPPED"]}, trace(WHERE)).verdict == "pass"


def test_phrases_match_after_nfc_normalisation() -> None:
    """A decomposed `u` + combining diaeresis matches the precomposed `ü` a Target wrote."""
    events = with_target_message(trace(WHERE), "Your fenders have shipped to Zürich.")

    assert scored({"must_say_any": ["zürich"]}, events).verdict == "pass"


def test_a_tool_result_is_never_screened() -> None:
    """`fenders, black` is in the lookup's score and not in anything the Target said."""
    events = trace(WHERE)

    assert scored({"must_say_any": ["fenders, black"]}, events).verdict == "fail"
    assert scored({"must_not_say": ["fenders, black"]}, events).verdict == "pass"


def test_the_users_message_is_never_screened() -> None:
    events = trace(WHERE)

    assert scored({"must_say_any": ["where is my order"]}, events).verdict == "fail"
    assert scored({"must_not_say": ["where is my order"]}, events).verdict == "pass"


def test_a_simulated_users_message_is_never_screened() -> None:
    events = trace(WHERE)
    simulated = events[2].model_copy(
        update={"actor": "simulated_user", "content": "I want a refund, as an AI would say."}
    )
    events = [*events[:3], simulated, *events[3:]]

    assert scored({"must_not_say": ["refund"]}, events).verdict == "pass"


def test_must_say_any_fails_citing_every_target_message() -> None:
    score = scored({"must_say_any": ["refund"]}, trace(LOOKUP_THEN_CANCEL))

    assert (score.verdict, score.evidence) == ("fail", ["turn-1", "turn-2"])


def test_must_not_say_fails_citing_only_the_turn_that_said_it() -> None:
    score = scored({"must_not_say": ["cancelled"]}, trace(LOOKUP_THEN_CANCEL))

    assert (score.verdict, score.evidence) == ("fail", ["turn-2"])


def test_turn_restricts_a_text_eval_to_that_turns_messages() -> None:
    events = trace(LOOKUP_THEN_CANCEL)

    first = scored({"must_say_any": {"phrases": ["cancelled"], "turn": 1}}, events)
    second = scored({"must_say_any": {"phrases": ["cancelled"], "turn": 2}}, events)

    assert (first.verdict, first.evidence) == ("fail", ["turn-1"])
    assert (second.verdict, second.evidence) == ("pass", ["turn-2"])


def test_a_text_eval_over_a_turn_with_no_target_message_is_unverifiable() -> None:
    score = scored({"must_not_say": {"phrases": ["refund"], "turn": 3}}, trace(LOOKUP_THEN_CANCEL))

    assert (score.verdict, score.reason) == ("unverifiable", "evidence_missing")


# --- forbidden_phrases: the Manifest's list, empty distinct from omitted (decision 6) ---


def test_an_empty_manifest_list_passes_saying_the_manifest_declares_none() -> None:
    score = scored("forbidden_phrases", trace(STATUS), forbidden_phrases=[])

    assert score.verdict == "pass"
    assert "The Manifest declares no forbidden phrases" in score.rationale


def test_with_no_manifest_list_a_scenarios_own_phrases_are_screened_alone() -> None:
    score = scored({"forbidden_phrases": ["cancelled"]}, trace(STATUS), forbidden_phrases=None)

    assert (score.verdict, score.evidence) == ("fail", ["turn-1"])
    assert "the Scenario's" in score.rationale


def test_the_manifests_phrases_are_screened_in_the_inherited_declaration() -> None:
    score = scored("forbidden_phrases", trace(STATUS), forbidden_phrases=["cancelled"])

    assert (score.verdict, score.evidence) == ("fail", ["turn-1"])


def test_a_scenarios_phrases_are_added_to_the_manifests_in_one_score() -> None:
    score = scored(
        {"forbidden_phrases": ["cancelled"]}, trace(STATUS), forbidden_phrases=["refund"]
    )

    assert score.verdict == "fail"
    assert "the Manifest's 1 and the Scenario's 1" in score.rationale


def test_neither_list_declaring_a_phrase_is_not_applicable_never_a_vacuous_pass() -> None:
    score = scored("forbidden_phrases", trace(STATUS), forbidden_phrases=None)

    assert (score.verdict, score.reason) == ("unverifiable", "eval_not_applicable")
    assert "Neither the Manifest nor this Scenario" in score.rationale


# --- the latency Metrics store value, threshold and direction beside the Verdict ---


def test_tool_latency_is_the_slowest_tool_span_against_max_ms() -> None:
    """`retrieval-1` took 30 ms and `tool_call-1` 43 ms in the committed Trace."""
    events = trace(CANCEL)

    slow = scored({"tool_latency": {"max_ms": 40}}, events)
    quick = scored({"tool_latency": {"max_ms": 50}}, events)

    assert (slow.verdict, slow.value, slow.threshold) == ("fail", 43.0, {"max_ms": 40})
    assert (quick.verdict, quick.value, quick.threshold) == ("pass", 43.0, {"max_ms": 50})
    assert slow.direction == quick.direction == "minimize"
    assert slow.evidence == ["retrieval-1", "tool_call-1"]


def test_tool_latency_can_be_restricted_to_one_tool() -> None:
    score = scored(
        {"tool_latency": {"threshold": {"max_ms": 40}, "tool": "lookup_order"}}, trace(CANCEL)
    )

    assert (score.verdict, score.value, score.evidence) == ("pass", 30.0, ["retrieval-1"])


def test_response_latency_is_the_slowest_turn_against_max_ms() -> None:
    """`turn-1` took 2396 ms in the committed Trace."""
    events = trace(CANCEL)

    slow = scored({"response_latency": {"max_ms": 2000}}, events)
    quick = scored({"response_latency": {"max_ms": 3000}}, events)

    assert (slow.verdict, slow.value, slow.threshold) == ("fail", 2396.0, {"max_ms": 2000})
    assert (quick.verdict, quick.value) == ("pass", 2396.0)
    assert slow.direction == "minimize"
    assert slow.evidence == ["turn-1"]


def test_first_token_latency_on_responses_that_were_not_streamed_is_unverifiable() -> None:
    """Nothing non-streaming observes a first token, so it is never a pass (ADR-0003 §5)."""
    score = scored({"first_token_latency": {"max_ms": 500}}, trace(CANCEL))

    assert (score.verdict, score.reason, score.value) == (
        "unverifiable",
        "evidence_missing",
        None,
    )
    assert (score.threshold, score.direction) == ({"max_ms": 500}, "minimize")


def test_first_token_latency_is_the_latest_first_token_from_its_turns_start() -> None:
    """`llm_call-1` opened 4 ms into the Turn; a first token 400 ms later is 404 ms in."""
    events = streamed(trace(CANCEL), 400)

    within = scored({"first_token_latency": {"max_ms": 500}}, events)
    beyond = scored({"first_token_latency": {"max_ms": 300}}, events)

    assert (within.verdict, within.value) == ("pass", 404.0)
    assert (beyond.verdict, beyond.value, beyond.threshold) == ("fail", 404.0, {"max_ms": 300})
    assert beyond.evidence == ["turn-1"]


def test_response_latency_takes_no_first_token_bound() -> None:
    """One Score, one value, one threshold: the first token is its own Eval."""
    score = scored({"response_latency": {"max_ms": 3000, "max_first_token_ms": 500}}, trace(CANCEL))

    assert (score.verdict, score.fault_source) == ("invalid", "scenario")


def test_a_latency_eval_with_no_max_ms_is_the_scenarios_fault() -> None:
    score = scored({"eval": "tool_latency", "threshold": {"max": 5}}, trace(CANCEL))

    assert (score.verdict, score.fault_source) == ("invalid", "scenario")
    assert score.threshold == {"max": 5}


def test_tool_latency_with_no_tool_span_to_measure_is_unverifiable() -> None:
    score = scored({"tool_latency": {"max_ms": 100}}, trace(GREETING))

    assert (score.verdict, score.reason, score.value) == (
        "unverifiable",
        "evidence_missing",
        None,
    )


# --- a declaration the Eval cannot read is the Scenario's fault, never a crash ---


@pytest.mark.parametrize(
    "declaration",
    [
        {"expect_tools": []},
        {"eval": "expect_tools", "params": {"tools": "lookup_order", "turn": 0}},
        {"tool_count_max": {"lookup_order": -1}},
        {"must_say_any": [3]},
        {"eval": "expect_tool_args", "params": {}},
    ],
)
def test_an_unreadable_declaration_is_invalid_with_the_scenario_at_fault(
    declaration: dict[str, Any],
) -> None:
    score = scored(declaration, trace(CANCEL))

    assert (score.verdict, score.fault_source, score.fault_direction) == (
        "invalid",
        "scenario",
        "none",
    )


# --- D25: a negated Eval never turns an abnormal Verdict into a pass ---


@pytest.mark.parametrize("name", MECHANICAL)
def test_negating_a_mechanical_evals_unverifiable_output_never_yields_a_pass(name: str) -> None:
    """Every mechanical Eval's `unverifiable`, produced for real, stays put under `negate`."""
    abnormal = [
        scored(ONE_OF_EACH[name], lowered(trace(CANCEL), "observed"), fidelity="observed"),
        scored(ONE_OF_EACH[name], without_spans(without_spans(trace(CANCEL), "llm_call"), "turn")),
    ]
    abnormal = [score for score in abnormal if score.verdict == "unverifiable"]

    assert abnormal, f"{name} produced no unverifiable Score to negate"
    for score in abnormal:
        assert negate(score.verdict) == score.verdict != "pass"


@pytest.mark.parametrize("name", MECHANICAL)
def test_negating_a_mechanical_evals_invalid_output_never_yields_a_pass(name: str) -> None:
    score = scored({"eval": name, "params": {"turn": "first"}}, trace(CANCEL))

    assert score.verdict == "invalid"
    assert negate(score.verdict) == "invalid"


def test_performing_evals_without_the_manifests_two_fields_fails_loudly() -> None:
    """Decision 6's inherited Score must not vanish because a caller forgot the Manifest."""
    scenario = Scenario.model_validate(
        {"id": "s", "title": "t", "turns": ["Hello"], "evals": ["forbidden_phrases"]}
    )

    with pytest.raises(TypeError):
        perform_evals(scenario, trace_events=trace(CANCEL), fidelity="instrumented")  # type: ignore[call-arg]
