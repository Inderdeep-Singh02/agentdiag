"""The replay transport serves a recording and fails loud (D12, phase-4 interfaces decision 5).

Seam 2's own file (`tests/test_model_replay.py`) is slice 4's; these are the transport
behaviours slice 2 depends on, tested here so the Adapter's tests can assume them.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

from agentdiag.model.replay import (
    Recording,
    RecordingNotConsumed,
    ReplayMismatch,
    ReplayTransport,
)

MODEL = "claude-sonnet-5"
REQUEST_ONE = {"model": MODEL, "max_tokens": 8, "messages": [{"role": "user", "content": "one"}]}
REQUEST_TWO = {"model": MODEL, "max_tokens": 8, "messages": [{"role": "user", "content": "two"}]}


def _message(text: str, id: str = "msg_1") -> dict[str, object]:
    return {
        "id": id,
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-20260815",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }


def write_recording(path: Path, pairs: list[tuple[dict[str, object], dict[str, object]]]) -> Path:
    path.write_text(
        "".join(
            json.dumps({"request": request, "response": response}) + "\n"
            for request, response in pairs
        ),
        encoding="utf-8",
    )
    return path


def _post(transport: ReplayTransport, body: dict[str, object]) -> httpx2.Response:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages", json=body)
    return transport.handle_request(request)


def test_a_recorded_request_is_served_from_the_recording(tmp_path: Path) -> None:
    recording = write_recording(tmp_path / "r.jsonl", [(REQUEST_ONE, _message("hello"))])
    transport = ReplayTransport(recording)

    response = _post(transport, REQUEST_ONE)

    assert response.status_code == 200
    assert json.loads(response.read())["content"][0]["text"] == "hello"


def test_a_request_matches_whatever_the_key_order_was(tmp_path: Path) -> None:
    """Matching is on canonical JSON, so the SDK's key order cannot cause a false miss."""
    recording = write_recording(tmp_path / "r.jsonl", [(REQUEST_ONE, _message("hello"))])
    transport = ReplayTransport(recording)

    reordered = {
        "messages": REQUEST_ONE["messages"],
        "max_tokens": REQUEST_ONE["max_tokens"],
        "model": REQUEST_ONE["model"],
    }
    assert _post(transport, reordered).status_code == 200


def test_each_pair_is_consumed_once_and_in_order(tmp_path: Path) -> None:
    """Two identical requests get the first unconsumed pair each, in turn."""
    recording = write_recording(
        tmp_path / "r.jsonl",
        [(REQUEST_ONE, _message("first", "msg_1")), (REQUEST_ONE, _message("second", "msg_2"))],
    )
    transport = ReplayTransport(recording)

    assert json.loads(_post(transport, REQUEST_ONE).read())["id"] == "msg_1"
    assert json.loads(_post(transport, REQUEST_ONE).read())["id"] == "msg_2"
    transport.assert_consumed()


def test_an_unmatched_request_raises_replay_mismatch_showing_the_closest_difference(
    tmp_path: Path,
) -> None:
    recording = write_recording(tmp_path / "r.jsonl", [(REQUEST_ONE, _message("hello"))])
    transport = ReplayTransport(recording)

    with pytest.raises(ReplayMismatch) as raised:
        _post(transport, REQUEST_TWO)

    message = str(raised.value)
    assert "two" in message  # the request that was sent
    assert "one" in message  # the closest unconsumed request it was compared against


def test_an_unconsumed_recording_raises_naming_what_is_left(tmp_path: Path) -> None:
    recording = write_recording(
        tmp_path / "r.jsonl", [(REQUEST_ONE, _message("hello")), (REQUEST_TWO, _message("bye"))]
    )
    transport = ReplayTransport(recording)
    _post(transport, REQUEST_ONE)

    with pytest.raises(RecordingNotConsumed) as raised:
        transport.assert_consumed()

    assert "1 of 2" in str(raised.value)


def test_a_recording_loads_its_exchanges_in_file_order(tmp_path: Path) -> None:
    recording = Recording.load(
        write_recording(
            tmp_path / "r.jsonl", [(REQUEST_ONE, _message("hello")), (REQUEST_TWO, _message("bye"))]
        )
    )

    contents = [
        exchange.request["messages"][0]["content"]  # type: ignore[index]
        for exchange in recording.exchanges
    ]
    assert contents == ["one", "two"]


# --- a recording scoped to Scenarios (phase-5 decision 19) ---


def write_scoped(
    path: Path, lines: list[tuple[str | None, dict[str, object], dict[str, object]]]
) -> Path:
    """A recording whose lines may carry `scenario`; `None` writes a line without one."""
    rendered = []
    for scenario, request, response in lines:
        line: dict[str, object] = {"request": request, "response": response}
        if scenario is not None:
            line = {"scenario": scenario, **line}
        rendered.append(json.dumps(line) + "\n")
    path.write_text("".join(rendered), encoding="utf-8")
    return path


def test_an_exchange_scoped_to_one_scenario_is_invisible_to_another_scenarios_trial(
    tmp_path: Path,
) -> None:
    recording = write_scoped(tmp_path / "r.jsonl", [("a", REQUEST_ONE, _message("for a"))])
    transport = ReplayTransport(recording)
    transport.cursor.begin("b")

    with pytest.raises(ReplayMismatch):
        _post(transport, REQUEST_ONE)


def test_the_same_request_is_answered_by_the_exchange_scoped_to_the_trials_scenario(
    tmp_path: Path,
) -> None:
    """One file replays a whole Suite even when two Scenarios open with the same request."""
    recording = write_scoped(
        tmp_path / "r.jsonl",
        [
            ("a", REQUEST_ONE, _message("for a", "msg_a")),
            ("b", REQUEST_ONE, _message("for b", "msg_b")),
        ],
    )
    transport = ReplayTransport(recording)

    transport.cursor.begin("b")
    assert json.loads(_post(transport, REQUEST_ONE).read())["id"] == "msg_b"
    transport.assert_consumed()
    transport.cursor.begin("a")
    assert json.loads(_post(transport, REQUEST_ONE).read())["id"] == "msg_a"
    transport.assert_consumed()


def test_an_unscoped_exchange_is_served_to_every_scenarios_trial(tmp_path: Path) -> None:
    recording = write_scoped(tmp_path / "r.jsonl", [(None, REQUEST_ONE, _message("anyone"))])
    transport = ReplayTransport(recording)

    for scenario in ("a", "b"):
        transport.cursor.begin(scenario)
        assert _post(transport, REQUEST_ONE).status_code == 200
        transport.assert_consumed()


def test_a_leftover_scoped_exchange_is_reported_after_its_own_trial_only(tmp_path: Path) -> None:
    recording = write_scoped(
        tmp_path / "r.jsonl",
        [
            ("a", REQUEST_ONE, _message("one")),
            ("a", REQUEST_TWO, _message("two")),
            ("b", REQUEST_ONE, _message("b")),
        ],
    )
    transport = ReplayTransport(recording)

    transport.cursor.begin("b")
    _post(transport, REQUEST_ONE)
    transport.assert_consumed()  # a's leftover is not b's to answer for

    transport.cursor.begin("a")
    _post(transport, REQUEST_ONE)
    with pytest.raises(RecordingNotConsumed) as raised:
        transport.assert_consumed()
    assert "1 of 2" in str(raised.value)
    assert "'a'" in str(raised.value)


def test_the_next_trial_of_the_same_scenario_walks_its_view_again_from_the_start(
    tmp_path: Path,
) -> None:
    """Ticket 08's `trials`: two Trials of one Scenario replay the same exchanges."""
    recording = write_scoped(tmp_path / "r.jsonl", [("a", REQUEST_ONE, _message("again"))])
    transport = ReplayTransport(recording)

    for _ in range(2):
        transport.cursor.begin("a")
        assert _post(transport, REQUEST_ONE).status_code == 200
        transport.assert_consumed()


def test_a_recording_with_no_scope_walks_every_exchange_as_phase_4_did(tmp_path: Path) -> None:
    """Every Phase 4 recording stays valid: without `begin`, the view is the whole file."""
    recording = write_scoped(
        tmp_path / "r.jsonl",
        [("a", REQUEST_ONE, _message("one")), (None, REQUEST_TWO, _message("two"))],
    )
    transport = ReplayTransport(recording)

    _post(transport, REQUEST_ONE)
    _post(transport, REQUEST_TWO)
    transport.assert_consumed()
