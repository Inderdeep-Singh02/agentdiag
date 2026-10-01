"""The committed Trace fixture is what the toy Target really produces (slice order, item 2).

Every later slice — projections, `show`, the Judge, the exporters — reads
`cancel-processing-order.trace.jsonl` instead of running a Target. That only holds while
the fixture is the writer's own output, so this test regenerates it: the toy Target in
replay, a scripted clock, and exactly the sequence slice 3's runner will perform. A diff
means the pipeline changed and the fixture must be regenerated, deliberately.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agentdiag.adapter import InProcessAdapter
from agentdiag.run.manifest import load_manifest
from agentdiag.trace import TraceWriter
from tests.fakes.workspace import the_target

FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "toy"
TRACE_FIXTURE = FIXTURES / "traces" / "cancel-processing-order.trace.jsonl"
RECORDING = FIXTURES / "recordings" / "toy-cancel-target.jsonl"

BASE_MS = 1_758_536_100_000
"""The instant the Trial started; every Event's `ts` is this plus its offset."""

OFFSETS = [
    0,
    2,
    4,
    6,
    8,
    9,
    912,
    918,
    920,
    944,
    948,
    951,
    955,
    957,
    1703,
    1709,
    1711,
    1748,
    1752,
    1755,
    1759,
    1761,
    2388,
    2392,
    2395,
    2398,
    2401,
]
"""One reading per Event written, in order. The gaps are the model's latency, which is a
projection of these timestamps and never a stored field (D11)."""

MESSAGE = "Hi, I'd like to cancel order NB-1042."
TRACE_ID = "20260922T101500Z-k7pq/cancel-processing-order/1"
RUN = "20260922T101500Z-k7pq"
SCENARIO = "cancel-processing-order"


def scripted_clock() -> Callable[[], int]:
    """Hands out the recorded offsets in order, so a regeneration is byte-identical."""
    readings = iter(OFFSETS)

    def clock() -> int:
        return BASE_MS + next(readings)

    return clock


def adapter_config() -> dict[str, object]:
    return {
        "kind": "inprocess",
        "side_effects": "none",
        "environments": {
            "default": "local",
            "local": {
                "factory": "agentdiag.examples.toy:make_target",
                "tools": "agentdiag.examples.toy:make_tools",
                "model": "claude-sonnet-5",
            },
        },
    }


def regenerate(path: Path) -> Path:
    """Run the cancel Trial exactly as slice 3's runner will, into `path`.

    The tools' kinds come from the shipped Manifest, as preflight passes them: the lookup
    is a `retrieval` Span, the cancellation a `tool_call` (ticket 04).
    """
    adapter = InProcessAdapter(
        adapter_config(),
        environment="local",
        replay=RECORDING,
        tool_kinds=load_manifest(the_target(EXAMPLE)).tool_kinds,
    )
    writer = TraceWriter(path, clock=scripted_clock())

    writer.start(trace_id=TRACE_ID, scenario=SCENARIO, run=RUN, trial=1)
    session = adapter.open(writer)
    with writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented") as turn:
        turn.event("message", actor="agentdiag", role="user", content=MESSAGE)
        reply = session.deliver(MESSAGE)
        turn.event("message", actor="target", role="assistant", content=reply)
    session.close()
    writer.end("completed")
    writer.close()

    adapter.assert_consumed()
    return path


def test_the_toy_target_in_replay_regenerates_the_committed_trace_byte_for_byte(
    tmp_path: Path,
) -> None:
    regenerated = regenerate(tmp_path / "trace.jsonl")

    assert regenerated.read_text(encoding="utf-8") == TRACE_FIXTURE.read_text(encoding="utf-8")


def test_the_scripted_clock_has_one_reading_per_event(tmp_path: Path) -> None:
    """If the pipeline gains or loses an Event, the offsets must be revised deliberately."""
    regenerated = regenerate(tmp_path / "trace.jsonl")

    lines = regenerated.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(OFFSETS)
