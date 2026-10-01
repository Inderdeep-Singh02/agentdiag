"""Reading a Trace: the whole file, or tailed while a Trial is still running."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from agentdiag.trace import TraceWriter, follow, read_trace, resolve_blobs


def test_read_trace_returns_every_event_in_the_order_it_was_written(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    writer.event("note", actor="agentdiag", text="one")
    writer.event("note", actor="agentdiag", text="two")
    writer.end("completed")
    writer.close()

    assert [event.type for event in read_trace(path)] == [
        "trace/start",
        "note",
        "note",
        "trace/end",
    ]


def test_follow_yields_events_appended_after_it_started_and_stops_at_trace_end(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)

    def keep_writing() -> None:
        for index in range(3):
            time.sleep(0.02)
            writer.event("note", actor="agentdiag", text=f"step {index}")
        time.sleep(0.02)
        writer.end("completed")
        writer.close()

    thread = threading.Thread(target=keep_writing)
    thread.start()
    seen = list(follow(path, poll_ms=10))
    thread.join()

    assert [event.type for event in seen] == [
        "trace/start",
        "note",
        "note",
        "note",
        "trace/end",
    ]


def test_follow_stops_when_the_caller_says_to_even_without_a_trace_end(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    writer.event("note", actor="agentdiag", text="still running")

    seen = []
    for event in follow(path, poll_ms=10, stop=lambda: len(seen) >= 2):
        seen.append(event)

    assert [event.type for event in seen] == ["trace/start", "note"]
    writer.close()


def test_follow_waits_for_a_trace_that_does_not_exist_yet(tmp_path: Path) -> None:
    path = tmp_path / "later" / "trace.jsonl"

    def write_later() -> None:
        time.sleep(0.05)
        writer = TraceWriter(path)
        writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
        writer.end("completed")
        writer.close()

    thread = threading.Thread(target=write_later)
    thread.start()
    seen = list(follow(path, poll_ms=10))
    thread.join()

    assert [event.type for event in seen] == ["trace/start", "trace/end"]


def test_resolve_blobs_restores_the_content_a_reference_names(tmp_path: Path) -> None:
    prompt = "Rule 1. Look up before you answer. " * 40
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    writer.event("request", actor="target", body={"system": prompt, "model": "claude-sonnet-5"})
    writer.end("completed")
    writer.close()

    raw = read_trace(path)
    assert (raw[-2].model_extra or {})["body"]["system"] != prompt

    resolved = resolve_blobs(raw)
    request = next(event for event in resolved if event.type == "request")
    assert (request.model_extra or {})["body"]["system"] == prompt
    assert (request.model_extra or {})["body"]["model"] == "claude-sonnet-5"


def test_resolve_blobs_leaves_a_reference_whose_blob_is_missing_alone() -> None:
    from agentdiag.trace import Event

    dangling = Event(
        seq=0,
        ts=1,
        type="request",
        actor="target",
        turn=None,
        span_id=None,
        parent_span_id=None,
        body={"system": {"blob": "sha256:deadbeef"}},
    )
    resolved = resolve_blobs([dangling])
    assert (resolved[0].model_extra or {})["body"]["system"] == {"blob": "sha256:deadbeef"}
