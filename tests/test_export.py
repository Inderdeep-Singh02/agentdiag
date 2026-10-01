"""Seam 1: the two exports, over the committed Trace fixture (D41, ADR-0004 §7).

Both expected files were generated once from `cancel-processing-order.trace.jsonl`, read
by eye against it — every `ts` and `dur` is the fixture's millisecond times a thousand,
every frame name is the Span's kind, name, actor and Fidelity, every `O`/`C` pair is a
`span/start` and its `span/end` — and committed as known-good. The comparison here is byte
for byte, so a change to either exporter has to be looked at before it is accepted.

The doctored Traces below are built from the same fixture rather than written by hand, so
the only thing that differs between a case and the known-good file is the one thing the
case is about.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from agentdiag.trace import export as export_module
from agentdiag.trace.events import Event
from agentdiag.trace.export import (
    OPEN_SUFFIX,
    export_chrome,
    export_speedscope,
    render,
)
from agentdiag.trace.reader import read_trace
from agentdiag.trace.spans import project_spans

REPO = Path(__file__).resolve().parents[1]
TRACE_FIXTURE = REPO / "tests" / "fixtures" / "traces" / "cancel-processing-order.trace.jsonl"
EXPORTS = REPO / "tests" / "fixtures" / "exports"
CHROME_EXPECTED = EXPORTS / "cancel-processing-order.chrome.json"
SPEEDSCOPE_EXPECTED = EXPORTS / "cancel-processing-order.speedscope.json"

SPEEDSCOPE_NAME = "20260922T101500Z-k7pq · cancel-processing-order · trial 1"
"""The `name` the CLI builds for this Run, Scenario and Trial."""


@pytest.fixture
def trace() -> list[Event]:
    return read_trace(TRACE_FIXTURE)


# --- helpers: the same Trace, doctored in one way ---


def renumbered(events: Sequence[Event]) -> list[Event]:
    """The Events with `seq` made contiguous again, as a Trace's always is."""
    return [
        Event.model_validate({**event.model_dump(), "seq": index})
        for index, event in enumerate(events)
    ]


def with_overlapping_siblings(events: Sequence[Event]) -> list[Event]:
    """`tool_call-1` re-parented under `llm_call-1`, starting while `retrieval-1` runs.

    Two concurrent tool calls under one `llm_call`: the fan-out D11 says renderers must
    lay out in lanes, and the one shape neither format can nest.
    """
    doctored: list[Event] = []
    for event in events:
        fields = event.model_dump()
        if fields["span_id"] == "llm_call-2":
            continue
        if fields["span_id"] == "tool_call-1":
            fields["parent_span_id"] = "llm_call-1"
            fields["dotted_order"] = str(fields.get("dotted_order", "")).replace(
                "175853610095500012-llm_call-2", "175853610000600003-llm_call-1"
            )
            fields["ts"] = fields["ts"] - 800
        doctored.append(Event.model_validate(fields))
    return renumbered(doctored)


def with_last_span_left_open(events: Sequence[Event]) -> list[Event]:
    """The Turn Span's `span/end` removed: a Trial that ended mid-Turn."""
    last_end = max(index for index, event in enumerate(events) if event.type == "span/end")
    return renumbered([event for index, event in enumerate(events) if index != last_end])


def with_a_child_outliving_its_parent(events: Sequence[Event]) -> list[Event]:
    """`turn-1` closed at 1758536101000, while `llm_call-3` runs on until 1758536102392.

    A child whose interval is not contained in its parent's. agentdiag's own Adapter never
    writes this — it closes an open `llm_call` before the Turn ends — but a reconstructed
    Trace (Phase 8) can, and neither format can draw it as a nest: Chrome would hold
    overlapping non-nested events on one track (research §1.4) and speedscope would have to
    close a frame before events already emitted (§2.3).
    """
    return renumbered(
        [
            Event.model_validate({**event.model_dump(), "ts": 1758536101000})
            if event.type == "span/end" and event.span_id == "turn-1"
            else event
            for event in events
        ]
    )


def judgement_events() -> list[Event]:
    """A `judgement.jsonl`'s Events: one `judge` Span, written after the Trial (ADR-0004 §3)."""
    return [
        Event.model_validate(fields)
        for fields in (
            {
                "seq": 0,
                "ts": 1758536110000,
                "type": "trace/start",
                "actor": "agentdiag",
                "turn": None,
                "span_id": None,
                "parent_span_id": None,
                "trace_id": "20260922T101500Z-k7pq/cancel-processing-order/1",
                "scenario": "cancel-processing-order",
                "run": "20260922T101500Z-k7pq",
                "trial": 1,
            },
            {
                "seq": 1,
                "ts": 1758536110001,
                "type": "span/start",
                "actor": "judge",
                "turn": None,
                "span_id": "judge-1",
                "parent_span_id": None,
                "kind": "judge",
                "name": "judge prompt_adherence",
                "fidelity": "instrumented",
                "dotted_order": "175853611000100001-judge-1",
                "attributes": {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.request.model": "claude-opus-5",
                    "agentdiag.eval": "prompt_adherence",
                },
            },
            {
                "seq": 2,
                "ts": 1758536114200,
                "type": "span/end",
                "actor": "judge",
                "turn": None,
                "span_id": "judge-1",
                "parent_span_id": None,
                "status": "ok",
                "attributes": {"gen_ai.usage.input_tokens": 3184},
                "error": None,
            },
            {
                "seq": 3,
                "ts": 1758536114201,
                "type": "trace/end",
                "actor": "agentdiag",
                "turn": None,
                "span_id": None,
                "parent_span_id": None,
                "termination": "completed",
                "error": None,
            },
        )
    ]


def slices(export: dict[str, Any]) -> list[dict[str, Any]]:
    """The Chrome events that are Spans: complete ones and open ones."""
    return [event for event in export["traceEvents"] if event["ph"] in {"X", "B"}]


def named(export: dict[str, Any], span_id: str) -> dict[str, Any]:
    """The one Chrome slice for this Span."""
    matches = [event for event in slices(export) if event["args"]["span_id"] == span_id]
    assert len(matches) == 1, f"expected one slice for {span_id}, got {len(matches)}"
    return matches[0]


# --- the known-good files ---


def test_the_chrome_export_of_the_committed_trace_is_byte_for_byte_the_committed_file(
    trace: list[Event],
) -> None:
    assert render(export_chrome(trace)) == CHROME_EXPECTED.read_text(encoding="utf-8")


def test_the_speedscope_export_of_the_committed_trace_is_byte_for_byte_the_committed_file(
    trace: list[Event],
) -> None:
    rendered = render(export_speedscope(trace, name=SPEEDSCOPE_NAME))

    assert rendered == SPEEDSCOPE_EXPECTED.read_text(encoding="utf-8")


# --- Chrome: one slice per Span, carrying what the geometry cannot say ---


def test_every_closed_span_becomes_exactly_one_complete_event(trace: list[Event]) -> None:
    export = export_chrome(trace)
    closed = [span for span in project_spans(trace) if span.end_ms is not None]

    complete = [event for event in export["traceEvents"] if event["ph"] == "X"]
    assert len(complete) == len(closed)
    assert {event["args"]["span_id"] for event in complete} == {span.span_id for span in closed}


def test_every_slice_carries_the_actor_the_fidelity_and_the_span_id(
    trace: list[Event],
) -> None:
    """The three things a flame graph's geometry cannot say (ADR-0006 §4, research §9)."""
    for event in slices(export_chrome(trace)):
        assert event["args"]["actor"] in {"target", "agentdiag", "simulated_user", "judge"}
        assert event["args"]["fidelity"] == "instrumented"
        assert event["args"]["span_id"]


def test_a_slice_carries_every_span_attribute_and_the_projected_metrics(
    trace: list[Event],
) -> None:
    llm_call = named(export_chrome(trace), "llm_call-1")

    assert llm_call["args"]["gen_ai.request.model"] == "claude-sonnet-5"
    assert llm_call["args"]["gen_ai.response.model"] == "claude-sonnet-5-20260815"
    assert llm_call["args"]["duration_ms"] == 945
    assert llm_call["args"]["model_latency_ms"] == 903
    assert llm_call["args"]["tokens.total"] == 803


def test_a_slice_states_microseconds_where_the_trace_states_milliseconds(
    trace: list[Event],
) -> None:
    """Chrome's `ts` is microseconds (research §1.2); a Trace's `ts` is milliseconds."""
    turn = named(export_chrome(trace), "turn-1")

    assert turn["ts"] == 1758536100002 * 1000
    assert turn["dur"] == (1758536102398 - 1758536100002) * 1000


def test_a_tool_call_lies_inside_the_llm_call_that_requested_it(trace: list[Event]) -> None:
    """Ticket 01 Decision 1, read off the export: the nesting Perfetto draws as a flame."""
    export = export_chrome(trace)
    llm_call = named(export, "llm_call-1")
    tool_call = named(export, "retrieval-1")

    assert llm_call["ts"] <= tool_call["ts"]
    assert tool_call["ts"] + tool_call["dur"] <= llm_call["ts"] + llm_call["dur"]
    assert tool_call["tid"] == llm_call["tid"], "a nested chain stays on one track"


def test_each_actor_is_one_process_with_a_name(trace: list[Event]) -> None:
    export = export_chrome(trace)
    processes = {
        event["pid"]: event["args"]["name"]
        for event in export["traceEvents"]
        if event["ph"] == "M" and event["name"] == "process_name"
    }

    assert set(processes.values()) == {"agentdiag", "target"}
    for event in slices(export):
        assert processes[event["pid"]] == event["args"]["actor"]


def test_each_lane_is_one_thread_named_after_it(trace: list[Event]) -> None:
    export = export_chrome(trace)
    threads = {
        (event["pid"], event["tid"])
        for event in export["traceEvents"]
        if event["ph"] == "M" and event["name"] == "thread_name"
    }

    for event in slices(export):
        assert (event["pid"], event["tid"]) in threads


def test_the_container_names_the_run_the_scenario_and_the_trial(trace: list[Event]) -> None:
    other = export_chrome(trace)["otherData"]

    assert other["run"] == "20260922T101500Z-k7pq"
    assert other["scenario"] == "cancel-processing-order"
    assert other["trial"] == 1
    assert other["trace_id"] == "20260922T101500Z-k7pq/cancel-processing-order/1"


def test_the_events_that_are_not_spans_become_instant_events(trace: list[Event]) -> None:
    instants = [event for event in export_chrome(trace)["traceEvents"] if event["ph"] == "i"]

    assert [event["name"] for event in instants] == [
        "message user",
        "tool/call lookup_order",
        "tool/result lookup_order",
        "tool/call cancel_order",
        "tool/result cancel_order",
        "message assistant",
    ]
    assert all(event["s"] == "t" for event in instants)
    lookup = next(event for event in instants if event["name"] == "tool/call lookup_order")
    assert lookup["args"]["arguments"] == {"order_id": "NB-1042"}


def test_a_long_field_in_an_instant_event_is_cut_and_says_by_how_much(
    trace: list[Event],
) -> None:
    """Perfetto's `args` are for querying, not payload; a cut is never silent (§1.5)."""
    long_text = "x" * 3000
    doctored = renumbered(
        [
            Event.model_validate({**event.model_dump(), "content": long_text})
            if event.type == "message"
            else event
            for event in trace
        ]
    )

    instants = [event for event in export_chrome(doctored)["traceEvents"] if event["ph"] == "i"]
    content = next(event["args"]["content"] for event in instants if "content" in event["args"])
    assert content.endswith("[truncated 1000 chars]")
    assert content.startswith("x" * 2000)


# --- concurrency: lanes, because neither format has a parent pointer ---


def test_two_overlapping_tool_call_spans_land_on_different_tracks(
    trace: list[Event],
) -> None:
    """The ticket's requirement: concurrent Spans on separate tracks (D11, research §1.4)."""
    export = export_chrome(with_overlapping_siblings(trace))

    first = named(export, "retrieval-1")
    second = named(export, "tool_call-1")
    assert first["pid"] == second["pid"], "the same actor is still one process"
    assert first["tid"] != second["tid"]


def test_two_overlapping_tool_call_spans_become_two_speedscope_profiles(
    trace: list[Event],
) -> None:
    """One evented profile cannot express them: its `O`/`C` must nest (research §2.3)."""
    export = export_speedscope(with_overlapping_siblings(trace), name="doctored")

    assert [profile["name"] for profile in export["profiles"]] == [
        "turn 1 · lane 0",
        "turn 1 · lane 1",
    ]


def test_a_child_that_outlives_its_parent_leaves_its_parents_track(
    trace: list[Event],
) -> None:
    """It is not nested, so it must not share a track: Chrome nests positionally (§1.4)."""
    export = export_chrome(with_a_child_outliving_its_parent(trace))

    turn = named(export, "turn-1")
    outliving = named(export, "llm_call-3")
    assert outliving["ts"] + outliving["dur"] > turn["ts"] + turn["dur"]
    assert outliving["tid"] != turn["tid"]


def test_a_child_that_outlives_its_parent_says_so_in_its_speedscope_frame(
    trace: list[Event],
) -> None:
    """The viewer is shown the truth rather than a nest that was never there."""
    export = export_speedscope(with_a_child_outliving_its_parent(trace), name="doctored")

    frames = [frame["name"] for frame in export["shared"]["frames"]]
    assert "llm_call chat claude-sonnet-5 [target, instrumented] (exceeds parent)" in frames


def test_a_child_that_outlives_its_parent_never_sends_a_profile_backwards(
    trace: list[Event],
) -> None:
    """The format's one ordering rule holds even for a Trace that does not nest (§2.3)."""
    export = export_speedscope(with_a_child_outliving_its_parent(trace), name="doctored")

    for profile in export["profiles"]:
        times = [event["at"] for event in profile["events"]]
        assert times == sorted(times), f"{profile['name']}: {times}"


# --- an open Span, in both views ---


def test_an_open_span_is_a_begin_event_with_no_end(trace: list[Event]) -> None:
    export = export_chrome(with_last_span_left_open(trace))

    turn = named(export, "turn-1")
    assert turn["ph"] == "B"
    assert turn["args"]["status"] == "open"
    assert "dur" not in turn
    assert not [event for event in export["traceEvents"] if event["ph"] == "E"]


def test_an_open_span_gets_a_speedscope_frame_that_says_so(trace: list[Event]) -> None:
    export = export_speedscope(with_last_span_left_open(trace), name="doctored")

    open_frames = [
        frame["name"] for frame in export["shared"]["frames"] if frame["name"].endswith(OPEN_SUFFIX)
    ]
    assert open_frames == ["turn 1 [agentdiag, instrumented]" + OPEN_SUFFIX]


def test_an_open_span_is_closed_at_the_last_timestamp_in_the_trace(
    trace: list[Event],
) -> None:
    doctored = with_last_span_left_open(trace)
    export = export_speedscope(doctored, name="doctored")

    last_ts = max(event.ts for event in doctored)
    closes = [event for event in export["profiles"][0]["events"] if event["type"] == "C"]
    assert closes[-1]["at"] == last_ts


# --- speedscope: one profile per Turn, balanced, in order ---


def test_one_evented_profile_per_turn(trace: list[Event]) -> None:
    export = export_speedscope(trace, name=SPEEDSCOPE_NAME)

    assert [profile["name"] for profile in export["profiles"]] == ["turn 1"]
    assert export["profiles"][0]["type"] == "evented"
    assert export["profiles"][0]["unit"] == "milliseconds"
    assert export["activeProfileIndex"] == 0


def test_a_profile_starts_and_ends_at_its_turn_spans_own_interval(trace: list[Event]) -> None:
    profile = export_speedscope(trace, name=SPEEDSCOPE_NAME)["profiles"][0]

    turn = next(span for span in project_spans(trace) if span.kind == "turn")
    assert profile["startValue"] == turn.start_ms
    assert profile["endValue"] == turn.end_ms


def test_open_and_close_events_are_lifo_nested_in_every_profile(trace: list[Event]) -> None:
    """The format's rule, checked as a stack simulation: "the ordering must be balanced"."""
    for events in (
        trace,
        with_overlapping_siblings(trace),
        with_last_span_left_open(trace),
        with_a_child_outliving_its_parent(trace),
    ):
        export = export_speedscope(events, name="under test")
        for profile in export["profiles"]:
            stack: list[int] = []
            for event in profile["events"]:
                if event["type"] == "O":
                    stack.append(event["frame"])
                else:
                    assert stack, f"{profile['name']}: closed a frame nothing opened"
                    assert stack.pop() == event["frame"], (
                        f"{profile['name']}: closed a frame that was not the innermost"
                    )
            assert not stack, f"{profile['name']}: left {stack} open"


def test_the_timestamps_of_a_profile_never_go_backwards(trace: list[Event]) -> None:
    """The format's rule: every event's `at` is in non-decreasing order (research §2.3)."""
    for profile in export_speedscope(trace, name=SPEEDSCOPE_NAME)["profiles"]:
        times = [event["at"] for event in profile["events"]]
        assert times == sorted(times)


def test_a_frame_name_carries_the_kind_the_name_the_actor_and_the_fidelity(
    trace: list[Event],
) -> None:
    """A `Frame` has no payload (research §2.2), so the name is the only place to say it."""
    frames = [frame["name"] for frame in export_speedscope(trace, name="x")["shared"]["frames"]]

    assert "llm_call chat claude-sonnet-5 [target, instrumented]" in frames
    assert "retrieval execute_tool lookup_order [target, instrumented]" in frames
    assert "tool_call execute_tool cancel_order [target, instrumented]" in frames


def test_a_frame_name_does_not_repeat_a_kind_the_span_name_already_opens_with(
    trace: list[Event],
) -> None:
    """A Turn reads `turn 1 [...]`, not `turn turn 1 [...]`: Span names follow OTel."""
    frames = [frame["name"] for frame in export_speedscope(trace, name="x")["shared"]["frames"]]

    assert "turn 1 [agentdiag, instrumented]" in frames
    assert not [name for name in frames if name.startswith("turn turn ")]


def test_spans_that_say_the_same_thing_share_one_frame(trace: list[Event]) -> None:
    """Three `llm_call` Spans, one frame: what makes speedscope's Left Heavy view work."""
    export = export_speedscope(trace, name="x")
    frames = [frame["name"] for frame in export["shared"]["frames"]]

    assert len(frames) == len(set(frames))
    chat = frames.index("llm_call chat claude-sonnet-5 [target, instrumented]")
    opens = [event for event in export["profiles"][0]["events"] if event["type"] == "O"]
    assert len([event for event in opens if event["frame"] == chat]) == 3


# --- the Judge, when there is one ---


def test_the_judge_gets_its_own_process_in_the_chrome_export(trace: list[Event]) -> None:
    export = export_chrome(trace, judgement=judgement_events())

    judge = named(export, "judge-1")
    target = named(export, "llm_call-1")
    assert judge["args"]["actor"] == "judge"
    assert judge["pid"] != target["pid"]
    processes = {
        event["args"]["name"]
        for event in export["traceEvents"]
        if event["ph"] == "M" and event["name"] == "process_name"
    }
    assert processes == {"agentdiag", "target", "judge"}


def test_the_judge_gets_its_own_profile_in_the_speedscope_export(trace: list[Event]) -> None:
    export = export_speedscope(trace, judgement=judgement_events(), name=SPEEDSCOPE_NAME)

    assert [profile["name"] for profile in export["profiles"]] == ["turn 1", "judgement"]
    judgement = export["profiles"][-1]
    frames = export["shared"]["frames"]
    assert frames[judgement["events"][0]["frame"]]["name"] == (
        "judge prompt_adherence [judge, instrumented]"
    )


# --- what an exporter is: a pure function, and a one-way street ---


def test_exporting_twice_gives_the_same_export(trace: list[Event]) -> None:
    """A view of a Trace is a pure function of it (D11): nothing here holds state."""
    judgement = judgement_events()

    assert export_chrome(trace, judgement=judgement) == export_chrome(trace, judgement=judgement)
    assert export_speedscope(trace, name="x") == export_speedscope(trace, name="x")


def test_exporting_does_not_change_the_events_it_was_given(trace: list[Event]) -> None:
    before = [event.model_dump() for event in trace]

    export_chrome(trace)
    export_speedscope(trace, name="x")

    assert [event.model_dump() for event in trace] == before


def test_the_export_module_exposes_nothing_that_reads_a_chrome_or_speedscope_file() -> None:
    """ADR-0004 §7: exporters are views, never stores. There is no way back in."""
    names = [name for name in dir(export_module) if not name.startswith("_")]

    forbidden = ("import_", "load", "parse", "read_chrome", "read_speedscope", "from_chrome")
    offenders = [name for name in names if any(word in name.lower() for word in forbidden)]
    assert offenders == [], f"{offenders} would be a path back from a rendered view"


def test_no_other_module_in_the_package_knows_either_format() -> None:
    """Both formats are spelled out in exactly one file, so nothing else can grow a reader.

    `cli.py` names speedscope in its `--help` prose, which is what a command is for; what
    no other module may hold is the container key or the schema URL, because knowing those
    is knowing the format.
    """
    package = REPO / "src" / "agentdiag"
    mentions = sorted(
        path.relative_to(package).as_posix()
        for path in package.rglob("*.py")
        for text in [path.read_text(encoding="utf-8")]
        if "traceEvents" in text or "speedscope.app" in text
    )

    assert mentions == ["trace/export.py"]


# --- the format the schema describes (deselected by default) ---


@pytest.mark.network
def test_the_speedscope_export_validates_against_the_published_schema(
    trace: list[Event],
) -> None:
    """Checked against speedscope's own schema, not against our reading of it.

    A User-Agent is required: speedscope.app answers urllib's default with a 403.
    """
    import urllib.request

    jsonschema = pytest.importorskip("jsonschema")
    request = urllib.request.Request(
        export_module.SPEEDSCOPE_SCHEMA, headers={"User-Agent": "agentdiag-tests"}
    )
    with urllib.request.urlopen(request) as response:
        schema = json.load(response)

    jsonschema.validate(export_speedscope(trace, name=SPEEDSCOPE_NAME), schema)
