"""What a TraceWriter puts in the file, read back as lines a person could read.

No test here reaches into the writer: every assertion is on the Trace file, either
through `read_trace` or through `json.loads` of a raw line.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from agentdiag.trace import (
    Event,
    SpanEndedTwice,
    TraceFileExists,
    TraceSealed,
    TraceWriter,
    read_trace,
)

BASE_MS = 1758536100000


def ticking(start: int = BASE_MS, step: int = 10) -> Callable[[], int]:
    """A clock that advances by a fixed step on every reading."""
    state = {"now": start - step}

    def clock() -> int:
        state["now"] += step
        return state["now"]

    return clock


def lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def written(tmp_path: Path, **kwargs: object) -> tuple[TraceWriter, Path]:
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path, clock=ticking(), **kwargs)  # type: ignore[arg-type]
    return writer, path


def test_every_event_carries_the_seven_core_fields_in_reading_order(tmp_path: Path) -> None:
    writer, path = written(tmp_path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    writer.end("completed")
    writer.close()

    for line in lines(path):
        assert list(line)[:7] == [
            "seq",
            "ts",
            "type",
            "actor",
            "turn",
            "span_id",
            "parent_span_id",
        ]


def test_seq_is_contiguous_from_zero_across_the_whole_trace(tmp_path: Path) -> None:
    writer, path = written(tmp_path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    with writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented") as turn:
        turn.event("message", actor="agentdiag", role="user", content="hello")
        with writer.span("llm_call", actor="target", name="chat m", fidelity="instrumented"):
            pass
    writer.end("completed")
    writer.close()

    events = read_trace(path)
    assert [event.seq for event in events] == list(range(len(events)))


def test_ts_never_decreases_even_when_the_clock_goes_backwards(tmp_path: Path) -> None:
    readings = iter([BASE_MS + 500, BASE_MS + 100, BASE_MS + 700, BASE_MS])
    writer = TraceWriter(tmp_path / "trace.jsonl", clock=lambda: next(readings))
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    writer.event("note", actor="agentdiag", text="backwards")
    writer.event("note", actor="agentdiag", text="forwards")
    writer.end("completed")
    writer.close()

    stamps = [event.ts for event in read_trace(tmp_path / "trace.jsonl")]
    assert stamps == [BASE_MS + 500, BASE_MS + 500, BASE_MS + 700, BASE_MS + 700]


def test_span_ids_are_words_and_a_counter_per_kind(tmp_path: Path) -> None:
    writer, path = written(tmp_path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    with writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented"):
        first = writer.span("llm_call", actor="target", name="chat m", fidelity="instrumented")
        tool = writer.span(
            "tool_call",
            actor="target",
            name="execute_tool t",
            fidelity="instrumented",
            parent=first,
        )
        tool.end()
        first.end()
        second = writer.span("llm_call", actor="target", name="chat m", fidelity="instrumented")
        second.end()
    writer.end("completed")
    writer.close()

    opened = [
        (event.span_id, (event.model_extra or {})["kind"])
        for event in read_trace(path)
        if event.type == "span/start"
    ]
    assert opened == [
        ("turn-1", "turn"),
        ("llm_call-1", "llm_call"),
        ("tool_call-1", "tool_call"),
        ("llm_call-2", "llm_call"),
    ]


def test_every_event_inside_a_turn_span_carries_that_turns_index(tmp_path: Path) -> None:
    writer, path = written(tmp_path)
    start = writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    assert start.turn is None
    with (
        writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented"),
        writer.span("llm_call", actor="target", name="chat m", fidelity="instrumented") as call,
    ):
        call.event("request", actor="target", body={"model": "m"})
    with writer.span("turn", actor="agentdiag", name="turn 2", fidelity="instrumented"):
        pass
    end = writer.end("completed")
    writer.close()
    assert end.turn is None

    turns = {event.type + ":" + (event.span_id or "-"): event.turn for event in read_trace(path)}
    assert turns["span/start:turn-1"] == 1
    assert turns["request:llm_call-1"] == 1
    assert turns["span/start:turn-2"] == 2


def test_dotted_order_is_the_parents_order_a_dot_and_this_spans_segment(tmp_path: Path) -> None:
    writer, path = written(tmp_path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    turn = writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented")
    call = writer.span("llm_call", actor="target", name="chat m", fidelity="instrumented")
    call.end()
    turn.end()
    writer.end("completed")
    writer.close()

    starts = {
        event.span_id: (event.ts, event.seq, (event.model_extra or {})["dotted_order"])
        for event in read_trace(path)
        if event.type == "span/start"
    }
    turn_ts, turn_seq, turn_order = starts["turn-1"]
    call_ts, call_seq, call_order = starts["llm_call-1"]
    assert turn_order == f"{turn_ts:013d}{turn_seq:05d}-turn-1"
    assert call_order == f"{turn_order}.{call_ts:013d}{call_seq:05d}-llm_call-1"


def test_a_long_string_is_written_once_as_a_blob_and_referenced_thereafter(
    tmp_path: Path,
) -> None:
    prompt = "You are the order desk. " * 60
    writer, path = written(tmp_path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    with writer.span("llm_call", actor="target", name="chat m", fidelity="instrumented") as call:
        call.event("request", actor="target", body={"system": prompt})
        call.event("request", actor="target", body={"system": prompt})
    writer.end("completed")
    writer.close()

    events = read_trace(path)
    blobs = [event for event in events if event.type == "blob"]
    requests = [event for event in events if event.type == "request"]
    assert len(blobs) == 1
    digest = (blobs[0].model_extra or {})["sha256"]
    assert (blobs[0].model_extra or {})["content"] == prompt
    assert (blobs[0].model_extra or {})["size"] == len(prompt)
    for request in requests:
        assert (request.model_extra or {})["body"]["system"] == {"blob": f"sha256:{digest}"}


def test_a_short_string_is_never_a_blob(tmp_path: Path) -> None:
    writer, path = written(tmp_path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    writer.event("request", actor="target", body={"system": "be brief"})
    writer.end("completed")
    writer.close()

    events = read_trace(path)
    assert not [event for event in events if event.type == "blob"]


def test_a_tool_result_above_the_threshold_becomes_a_blob(tmp_path: Path) -> None:
    result = {"rows": ["NB-1042 processing"] * 200}
    writer, path = written(tmp_path, blob_threshold=32)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    writer.event(
        "tool/result",
        actor="target",
        tool="lookup_order",
        call_id="toolu_1",
        result={"summary": "x" * 100, "rows": result["rows"][:1]},
        is_error=False,
    )
    writer.end("completed")
    writer.close()

    events = read_trace(path)
    assert len([event for event in events if event.type == "blob"]) == 1
    tool_result = next(event for event in events if event.type == "tool/result")
    body = (tool_result.model_extra or {})["result"]
    assert set(body["summary"]) == {"blob"}


def test_each_event_is_flushed_before_the_next_one_is_written(tmp_path: Path) -> None:
    writer, path = written(tmp_path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    assert len(lines(path)) == 1
    writer.event("note", actor="agentdiag", text="mid-trial")
    assert len(lines(path)) == 2
    assert lines(path)[-1]["text"] == "mid-trial"
    writer.end("completed")
    writer.close()


def test_a_span_left_by_an_exception_ends_with_status_error_and_the_exception_reraises(
    tmp_path: Path,
) -> None:
    writer, path = written(tmp_path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    with (
        pytest.raises(ValueError, match="the tool blew up"),
        writer.span("tool_call", actor="target", name="execute_tool t", fidelity="instrumented"),
    ):
        raise ValueError("the tool blew up")
    writer.end("target_error", error="ValueError: the tool blew up")
    writer.close()

    end = next(event for event in read_trace(path) if event.type == "span/end")
    fields = end.model_extra or {}
    assert fields["status"] == "error"
    assert fields["error"] == {"type": "ValueError", "message": "the tool blew up"}


def test_ending_a_span_twice_raises(tmp_path: Path) -> None:
    writer, _ = written(tmp_path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    span = writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented")
    span.end()
    with pytest.raises(SpanEndedTwice):
        span.end()
    writer.close()


def test_an_explicit_parent_overrides_the_enclosing_span(tmp_path: Path) -> None:
    writer, path = written(tmp_path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    turn = writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented")
    first = writer.span("llm_call", actor="target", name="chat m", fidelity="instrumented")
    inner = writer.span("llm_call", actor="target", name="chat m", fidelity="instrumented")
    # `first` is no longer innermost, but naming it as the parent still nests under it.
    tool = writer.span(
        "tool_call",
        actor="target",
        name="execute_tool t",
        fidelity="instrumented",
        parent=first,
    )
    tool.end()
    inner.end()
    first.end()
    turn.end()
    writer.end("completed")
    writer.close()

    starts = {
        event.span_id: event.parent_span_id
        for event in read_trace(path)
        if event.type == "span/start"
    }
    assert starts["tool_call-1"] == "llm_call-1"


def test_an_event_written_with_an_explicit_span_parents_under_it_and_inherits_its_turn(
    tmp_path: Path,
) -> None:
    writer, path = written(tmp_path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    turn = writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented")
    call = writer.span("llm_call", actor="target", name="chat m", fidelity="instrumented")
    call.end()
    turn.end()
    # Both Spans are closed; the handle still says where the Event belongs.
    late = writer.event("note", actor="agentdiag", span=call, text="after the fact")
    writer.end("completed")
    writer.close()

    assert late.span_id == "llm_call-1"
    assert late.parent_span_id == "turn-1"
    assert late.turn == 1
    assert read_trace(path)[-2].text == "after the fact"  # type: ignore[attr-defined]


def test_a_trace_is_append_only_and_never_rewrites_a_line(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    first = TraceWriter(path, clock=ticking())
    first.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    before = path.read_text(encoding="utf-8")
    first.event("note", actor="agentdiag", text="second line")
    first.close()

    after = path.read_text(encoding="utf-8")
    assert after.startswith(before)
    assert len(read_trace(path)) == 2


def test_an_event_round_trips_through_the_file_unchanged(tmp_path: Path) -> None:
    writer, path = written(tmp_path)
    written_event = writer.event(
        "weather/observed",  # an unknown type carries through (ADR-0004 section 1)
        actor="adapter",
        conditions="clear",
        depth={"nested": [1, 2, 3]},
    )
    writer.close()

    read_back = read_trace(path)[0]
    assert isinstance(read_back, Event)
    assert read_back.model_dump() == written_event.model_dump()


# --- a Trace file is created once per Trial, and never reopened (ADR-0004, ADR-0005 §2) ---


def test_a_trace_file_that_already_exists_is_refused(tmp_path: Path) -> None:
    """Append-only means append-only within one Trial, not across two.

    Reopening a Trace file would let a second pass over the same Trial append Events
    after its `trace/end`, or — worse, because `judgement.jsonl` is a Trace file too —
    silently replace the Judge's request and response that ADR-0003 §7 keeps beside the
    Score so a disputed Verdict can be re-examined.
    """
    path = tmp_path / "trace.jsonl"
    first = TraceWriter(path, clock=ticking())
    first.start(trace_id="t/s/1", scenario="s", run="t", trial=1)
    first.end("completed")
    first.close()

    with pytest.raises(TraceFileExists) as raised:
        TraceWriter(path, clock=ticking())

    assert str(path) in str(raised.value)


def test_the_refused_second_writer_leaves_the_first_ones_events_intact(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path, clock=ticking())
    writer.start(trace_id="t/s/1", scenario="s", run="t", trial=1)
    writer.end("completed")
    writer.close()
    before = path.read_text(encoding="utf-8")

    with pytest.raises(TraceFileExists):
        TraceWriter(path, clock=ticking())

    assert path.read_text(encoding="utf-8") == before


# --- ticket 06: the end's detail, the seal, the lock (phase-5 decisions 47 and 56) ---


def test_trace_end_carries_a_detail_only_when_one_is_given(tmp_path: Path) -> None:
    """Decision 47: every Trace written before ticket 06 is byte-identical."""
    writer, path = written(tmp_path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    writer.end("stop_when", detail="tool_called cancel_order held after Turn 3")
    writer.close()
    plain, plain_path = written(tmp_path / "plain")
    plain.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    plain.end("completed")
    plain.close()

    assert lines(path)[-1]["detail"] == "tool_called cancel_order held after Turn 3"
    assert "detail" not in lines(plain_path)[-1]


def test_nothing_is_written_after_trace_end(tmp_path: Path) -> None:
    """Decision 56: a Target thread abandoned by a Turn timeout cannot append to a Trace
    that has ended, so `trace/end` is the last line by construction."""
    writer, path = written(tmp_path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    writer.end("timeout", error="the Target did not answer Turn 1 within 0.2 s")

    with pytest.raises(TraceSealed):
        writer.event("note", actor="target", text="woke up late")
    with pytest.raises(TraceSealed):
        writer.span("llm_call", actor="target", name="chat late", fidelity="instrumented")
    writer.close()

    assert [line["type"] for line in lines(path)] == ["trace/start", "trace/end"]


def test_the_events_so_far_read_back_from_the_file_while_the_trace_is_open(
    tmp_path: Path,
) -> None:
    """What the Simulated User and the stop check read of a Trial still running (amended
    decision 56): every write is flushed, so the file is the Trace so far."""
    writer, path = written(tmp_path, blob_threshold=8)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    with writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented") as turn:
        turn.event("message", actor="agentdiag", role="user", content="where is it?")
        turn.event("response", actor="target", body={"text": "a reply long enough"})

    assert [event.type for event in read_trace(path)] == [
        "trace/start",
        "span/start",
        "message",
        "blob",
        "response",
        "span/end",
    ]
    writer.close()


def test_writes_from_two_threads_never_interleave_inside_a_line(tmp_path: Path) -> None:
    """Decision 56: `deliver` runs on a worker thread, so the Trace takes every write under
    one lock and `seq` stays contiguous whoever wrote."""
    import threading

    writer, path = written(tmp_path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)

    def notes(actor: str) -> None:
        for number in range(200):
            writer.event("note", actor=actor, text=f"{actor} {number}")  # type: ignore[arg-type]

    threads = [threading.Thread(target=notes, args=(actor,)) for actor in ("target", "agentdiag")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    writer.end("completed")
    writer.close()

    written_lines = lines(path)
    assert [line["seq"] for line in written_lines] == list(range(402))
