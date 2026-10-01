"""The one driver loop, with the scripted Simulated User and no model (D26, ticket 06).

Phase-5 decisions 46, 47 and 50: `drive` serves the literal Turns, then the `simulate`
Turn's, asking the stop check before the first simulated Turn and after every Turn, and
ending on the criterion, the bound, the stop token or the Simulated User's own stop. The
Target is the toy Target in replay, over the recordings `scripts/record_fixtures.py
--scripted` wrote for the test-only Suite; the Simulated User is `ScriptedSimulatedUser`,
whose messages are the ones those recordings were rendered with.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentdiag.adapter import InProcessAdapter
from agentdiag.model.replay import Recording, ReplayCursor
from agentdiag.run.manifest import load_manifest
from agentdiag.scenario.load import load_suite
from agentdiag.scenario.models import Scenario, SimulateSpec
from agentdiag.simulate.drive import Driven, drive
from agentdiag.simulate.stop import StopCheck
from agentdiag.simulate.user import ScriptedSimulatedUser
from agentdiag.trace import Event, project_spans, read_trace
from agentdiag.trace.writer import TraceWriter
from tests.fakes.workspace import the_target
from tests.simulated import RECORDINGS, simulated_root, ticking

RECORDING_OF = {
    "promises-a-refund-date": "simulated_user_review-none",
    "says-cancelled": "simulate-target-says-any",
    "keeps-asking": "simulate-max-turns",
    "cancel-then-done": "simulate-stop-token",
    "refuses-to-play": "simulate-refusal",
}
"""Which recording holds each Scenario's Target exchanges."""


class Driving:
    """One Trial of a test-only Scenario, driven by a scripted Simulated User."""

    def __init__(self, tmp_path: Path, scenario_id: str, messages: list[str]) -> None:
        root = simulated_root(tmp_path)
        manifest = load_manifest(the_target(root))
        suite, _ = load_suite(
            root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "simulated.yaml"
        )
        self.scenario: Scenario = next(s for s in suite.scenarios if s.id == scenario_id)
        cursor = ReplayCursor(Recording.load(RECORDINGS / f"{RECORDING_OF[scenario_id]}.jsonl"))
        cursor.begin(scenario_id)
        self.adapter = InProcessAdapter(
            manifest.adapter.as_adapter_config(),
            environment="local",
            replay=cursor,
            tool_kinds=manifest.tool_kinds,
        )
        self.path = tmp_path / "trace.jsonl"
        self.messages = messages
        self.asked: list[SimulateSpec] = []

    def run(self) -> tuple[Driven, list[Event]]:
        writer = TraceWriter(self.path, clock=ticking())
        writer.start(trace_id="r/s/1", scenario=self.scenario.id, run="r", trial=1)
        session = self.adapter.open(writer)

        def simulated_user_for(spec: SimulateSpec) -> ScriptedSimulatedUser:
            self.asked.append(spec)
            return ScriptedSimulatedUser(self.messages)

        driven = drive(
            self.scenario,
            session,
            writer,
            simulated_user_for=simulated_user_for,
            stop_check=StopCheck(),
            turn_timeout_s=30.0,
        )
        session.close()
        writer.end(driven.termination, detail=driven.detail)
        writer.close()
        return driven, read_trace(self.path)


def fields(event: Event) -> dict[str, Any]:
    return dict(event.model_extra or {})


def stop_notes(events: list[Event]) -> list[dict[str, Any]]:
    return [fields(e) for e in events if e.type == "note" and fields(e).get("about") == "stop_when"]


def user_messages(events: list[Event]) -> list[tuple[str, str]]:
    return [
        (e.actor, str(fields(e)["content"]))
        for e in events
        if e.type == "message" and fields(e).get("role") == "user"
    ]


def test_a_tool_called_criterion_ends_the_trial_stop_when_after_the_turn_it_held(
    tmp_path: Path,
) -> None:
    driven, events = Driving(tmp_path, "promises-a-refund-date", ["NB-1042."]).run()

    assert driven.termination == "stop_when"
    assert driven.detail == "tool_called cancel_order held after Turn 2"
    assert user_messages(events) == [
        ("agentdiag", "Please cancel my order."),
        ("simulated_user", "NB-1042."),
    ]
    end = fields(events[-1])
    assert events[-1].type == "trace/end"
    assert (end["termination"], end["detail"]) == (
        "stop_when",
        "tool_called cancel_order held after Turn 2",
    )


def test_the_stop_check_writes_one_note_per_evaluation_outside_every_span(tmp_path: Path) -> None:
    """Decision 47: before the first simulated Turn, and after every Turn thereafter."""
    _, events = Driving(tmp_path, "promises-a-refund-date", ["NB-1042."]).run()

    notes = [e for e in events if e.type == "note"]
    assert [(n.actor, n.span_id, n.turn) for n in notes] == [("agentdiag", None, None)] * 2
    assert stop_notes(events) == [
        {
            "about": "stop_when",
            "predicate": "tool_called cancel_order",
            "holds": False,
            "text": "tool_called cancel_order did not hold after Turn 1",
        },
        {
            "about": "stop_when",
            "predicate": "tool_called cancel_order",
            "holds": True,
            "text": "tool_called cancel_order held after Turn 2",
        },
    ]


def test_the_simulate_span_sits_outside_the_turns_so_a_turns_duration_is_the_targets_alone(
    tmp_path: Path,
) -> None:
    """D27, decision 47: `response_latency` reads a Turn's duration; the Simulated User's
    authoring is a sibling before the Turn it authors, never inside it."""
    _, events = Driving(tmp_path, "promises-a-refund-date", ["NB-1042."]).run()

    spans = {span.span_id: span for span in project_spans(events)}
    authoring = spans["simulate-1"]
    assert (authoring.actor, authoring.name, authoring.parent_span_id, authoring.turn) == (
        "simulated_user",
        "simulated user",
        None,
        None,
    )
    turn = spans["turn-2"]
    assert authoring.end_ms is not None and turn.start_ms > authoring.end_ms
    assert [s.span_id for s in spans.values() if s.kind == "turn"] == ["turn-1", "turn-2"]
    (simulated,) = [e for e in events if e.actor == "simulated_user" and e.type == "message"]
    assert (simulated.span_id, simulated.turn) == ("turn-2", 2)


def test_a_target_says_any_criterion_the_opener_already_met_ends_before_any_simulated_turn(
    tmp_path: Path,
) -> None:
    """Decision 50: the truth of that Trial; the Simulated User writes nothing."""
    driving = Driving(tmp_path, "says-cancelled", [])
    driven, events = driving.run()

    assert driven.termination == "stop_when"
    assert driven.detail == 'target_says_any matched "cancelled" in Turn 1'
    assert not [e for e in events if e.actor == "simulated_user"]
    assert not [s for s in project_spans(events) if s.kind == "simulate"]


def test_a_criterion_that_never_holds_ends_the_trial_at_max_turns(tmp_path: Path) -> None:
    driven, events = Driving(tmp_path, "keeps-asking", ["When will it arrive?"]).run()

    assert (driven.termination, driven.detail) == ("max_turns", "max_turns 2 reached")
    assert [n["holds"] for n in stop_notes(events)] == [False, False]
    assert stop_notes(events)[-1]["text"] == "target_says_any matched nothing by Turn 2"


@pytest.mark.parametrize("said", ["[DONE]", "  [DONE]\n", "Thanks, that's all. [DONE]"])
def test_the_stop_token_or_a_message_containing_it_ends_the_trial_and_is_never_delivered(
    tmp_path: Path, said: str
) -> None:
    """Amended decision 48: detected here, for every implementation of the Simulated User."""
    driven, events = Driving(tmp_path, "cancel-then-done", [said]).run()

    assert driven.termination == "stop_token"
    assert driven.detail == "the Simulated User answered the stop token after Turn 1"
    assert said not in [text for _, text in user_messages(events)]
    assert [s.kind for s in project_spans(events)].count("turn") == 1


def test_a_script_that_runs_out_is_the_simulated_users_error_not_the_targets(
    tmp_path: Path,
) -> None:
    """Decision 46: a script too short for its Scenario is the Simulated User's fault."""
    driven, events = Driving(tmp_path, "refuses-to-play", []).run()

    assert driven.termination == "simulated_user_error"
    assert driven.detail == "the scripted Simulated User ran out of messages after 0"
    authoring = next(s for s in project_spans(events) if s.kind == "simulate")
    assert authoring.status == "error"


def test_the_simulated_user_is_built_once_for_the_simulate_turn_it_plays(tmp_path: Path) -> None:
    driving = Driving(tmp_path, "promises-a-refund-date", ["NB-1042."])
    driving.run()

    assert [spec.goal for spec in driving.asked] == ["Get order NB-1042 cancelled."]


def test_a_literal_turn_after_the_simulate_turn_is_refused_by_the_loop_itself(
    tmp_path: Path,
) -> None:
    """Decision 55: `validate` refuses it, and `drive` asserts it."""
    driving = Driving(tmp_path, "promises-a-refund-date", [])
    bad = driving.scenario.model_copy(update={"turns": [*driving.scenario.turns, "Thanks."]})
    writer = TraceWriter(tmp_path / "bad.jsonl")
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)

    with pytest.raises(ValueError, match="one simulate Turn and it is the last"):
        drive(
            bad,
            driving.adapter.open(writer),
            writer,
            simulated_user_for=lambda spec: ScriptedSimulatedUser([]),
            stop_check=StopCheck(),
            turn_timeout_s=30.0,
        )
    writer.close()
