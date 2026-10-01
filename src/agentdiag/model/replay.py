"""Replaying recorded model exchanges, and failing loud when the recording does not fit (D12).

One recording format serves both replay users (phase-4 interfaces, decision 5): a JSONL
file of `{"request": <API request body>, "response": <API response body>}` pairs, the same
bodies the Trace's `request` and `response` Events carry with blobs resolved. Slice 2's
Target-side `ReplayTransport` lives here; slice 4's `ReplayModelClient` reads the same
`Recording`.

Failing loud is the point. A recorded session that no longer matches the code means the
code changed, and a replay that quietly improvised would hide exactly the regression the
recording exists to catch. So: no fuzzy matching, no retry,
and an unconsumed recording is an error at the end of a Trial.

**A line may be scoped to a Scenario** (phase-5 decision 19): `{"scenario": "<id>",
"request": ..., "response": ...}`. Without `scenario` a line belongs to any Trial, so every
Phase 4 recording reads exactly as before. `ReplayCursor.begin(scenario)` starts a Trial:
from then on the cursor walks that Trial's *view* — the lines scoped to its Scenario plus
the unscoped ones, in file order, from the start — and `assert_consumed` asks about that
view only. One file then replays a whole Suite, even when two Scenarios open with the same
request body, and a Scenario's second Trial walks its view again rather than finding it
spent. The cursor object is still one per Run, shared by the Target's transport and the
Judge's client; what is per Trial is the accounting.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx2


class ReplayMismatch(RuntimeError):
    """A request arrived that no unconsumed exchange in the recording matches."""


class RecordingNotConsumed(RuntimeError):
    """The recording still held exchanges when the Trial ended."""


def canonical(body: Any) -> str:
    """The form two request bodies are compared in: key order cannot cause a false miss."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Exchange:
    """One recorded exchange: the request body as sent, the response body as received."""

    request: dict[str, Any]
    response: dict[str, Any]
    scenario: str | None = None
    """The Scenario whose Trials may take this exchange; None means any Trial may."""

    @property
    def key(self) -> str:
        return canonical(self.request)


@dataclass(frozen=True)
class Recording:
    """A recording file, loaded. Immutable: what is consumed is the reader's business."""

    path: Path
    exchanges: tuple[Exchange, ...]

    @classmethod
    def load(cls, path: Path) -> Recording:
        """Read a recording, keeping its exchanges in file order."""
        path = Path(path)
        exchanges: list[Exchange] = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number} is not JSON: {exc}") from exc
            if "request" not in raw or "response" not in raw:
                raise ValueError(f"{path}:{number} is not a {{request, response}} exchange")
            scenario = raw.get("scenario")
            if scenario is not None and not isinstance(scenario, str):
                raise ValueError(f"{path}:{number} has a `scenario` that is not a Scenario id")
            exchanges.append(
                Exchange(request=raw["request"], response=raw["response"], scenario=scenario)
            )
        return cls(path=path, exchanges=tuple(exchanges))


class Cursor(Protocol):
    """What answers a replayed model call: the recording's side of the exchange.

    `ReplayCursor` is the one agentdiag runs on; `scripts/record_fixtures.py --scripted`
    implements the same three operations over a scripted model, so the Adapter and the
    Judge's client drive either without knowing which.
    """

    def begin(self, scenario: str) -> None:
        """Start a Trial of `scenario` (decision 19)."""
        ...

    def take(self, request: Any) -> Exchange:
        """The exchange that answers this request body, or raise `ReplayMismatch`."""
        ...

    def assert_consumed(self, which: Callable[[dict[str, Any]], bool] | None = None) -> None:
        """Raise `RecordingNotConsumed` when the Trial left an exchange unused — of every
        exchange, or only of those whose request `which` picks."""
        ...


class ReplayCursor:
    """Walks one Trial's view of a Recording, handing out the first unconsumed match.

    First-unconsumed, not best-match: two identical requests in one Trial must get the two
    recorded responses in the order they were recorded, or a loop that calls the same tool
    twice would replay as the same answer twice.

    Until `begin` names a Scenario the view is every exchange, scoped or not: an Adapter
    or a Judge driven outside a Run has no Trial to scope to, and a Phase 4 recording
    replays as one walk over the file.
    """

    def __init__(self, recording: Recording) -> None:
        self.recording = recording
        self.scenario: str | None = None
        self._consumed: list[bool] = [False] * len(recording.exchanges)

    def begin(self, scenario: str) -> None:
        """Start a Trial of `scenario`: its view, walked from the start (decision 19)."""
        self.scenario = scenario
        self._consumed = [False] * len(self.recording.exchanges)

    def _view(self) -> list[int]:
        """The indices this Trial may take, in file order."""
        return [
            index
            for index, exchange in enumerate(self.recording.exchanges)
            if self.scenario is None or exchange.scenario in (None, self.scenario)
        ]

    def take(self, request: Any) -> Exchange:
        """The exchange for this request body, marked consumed. Raises `ReplayMismatch`."""
        wanted = canonical(request)
        for index in self._view():
            exchange = self.recording.exchanges[index]
            if not self._consumed[index] and exchange.key == wanted:
                self._consumed[index] = True
                return exchange
        raise ReplayMismatch(self._mismatch_message(request, wanted))

    def assert_consumed(self, which: Callable[[dict[str, Any]], bool] | None = None) -> None:
        """Raise `RecordingNotConsumed` when any exchange in this Trial's view went unused.

        `which` narrows the question to the exchanges whose request it picks: a Run asks
        about the Evals' exchanges before the Diagnosis runs and about the Diagnosis's after
        it, so an unused Diagnosis exchange can never overrule the Scores (ticket 05).
        """
        view = [
            index
            for index in self._view()
            if which is None or which(self.recording.exchanges[index].request)
        ]
        left = [index for index in view if not self._consumed[index]]
        if not left:
            return
        lines = "\n".join(
            f"  exchange {i} (line {i + 1}): {_summarise(self.recording.exchanges[i].request)}"
            for i in left
        )
        raise RecordingNotConsumed(
            f"{len(left)} of {len(view)} recorded exchanges{self._scope_text()} in "
            f"{self.recording.path} were never requested:\n{lines}"
        )

    def _scope_text(self) -> str:
        """Which view a message is about, when a Trial named one."""
        return "" if self.scenario is None else f" for Scenario {self.scenario!r}"

    def _mismatch_message(self, request: Any, wanted: str) -> str:
        unconsumed = [
            self.recording.exchanges[index] for index in self._view() if not self._consumed[index]
        ]
        if not unconsumed:
            return (
                f"The recording {self.recording.path} is exhausted{self._scope_text()}, but "
                f"another request arrived:\n  sent: {_summarise(request)}"
            )
        closest = min(unconsumed, key=lambda exchange: _distance(exchange.key, wanted))
        return (
            f"No unconsumed exchange{self._scope_text()} in {self.recording.path} matches "
            "the request.\n"
            f"  sent:    {_summarise(request)}\n"
            f"  closest: {_summarise(closest.request)}\n"
            f"  first difference: {_first_difference(closest.request, request)}"
        )


class ReplayTransport(httpx2.BaseTransport):
    """An httpx2 transport that answers from a recording instead of the network.

    Substituting the transport rather than the SDK's `messages.create` keeps one code path
    for live and replay: whatever the SDK would have put on the wire is what gets matched,
    so a replay that passes proves the real request body (ADR-0001 point 2).
    """

    def __init__(self, recording: Path | Recording | Cursor) -> None:
        # A cursor may be passed in when one recording serves both the Target's transport
        # and the Judge's client: they must then walk the same cursor, or each would think
        # the other's exchanges were never requested.
        if isinstance(recording, Path):
            recording = Recording.load(recording)
        self.cursor: Cursor = (
            ReplayCursor(recording) if isinstance(recording, Recording) else recording
        )

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        exchange = self.cursor.take(json.loads(request.read() or b"{}"))
        return httpx2.Response(
            200,
            json=exchange.response,
            headers={"content-type": "application/json", "request-id": "replay"},
        )

    def assert_consumed(self) -> None:
        """Raise `RecordingNotConsumed` when any recorded exchange went unused."""
        self.cursor.assert_consumed()


def _summarise(body: Any) -> str:
    """A request body short enough for an error message but long enough to recognise."""
    text = canonical(body)
    return text if len(text) <= 400 else text[:400] + "..."


def _distance(one: str, other: str) -> int:
    """How far apart two canonical bodies are: where they diverge, then by length."""
    common = 0
    for a, b in zip(one, other, strict=False):
        if a != b:
            break
        common += 1
    return -common * 1000 + abs(len(one) - len(other))


def _first_difference(recorded: Any, sent: Any, path: str = "") -> str:
    """Name the first place two request bodies diverge, so the diff is readable."""
    if isinstance(recorded, dict) and isinstance(sent, dict):
        for key in sorted(set(recorded) | set(sent)):
            here = f"{path}.{key}" if path else key
            if key not in recorded:
                return f"{here} was not recorded"
            if key not in sent:
                return f"{here} was recorded but not sent"
            if recorded[key] != sent[key]:
                return _first_difference(recorded[key], sent[key], here)
        return "none"
    if isinstance(recorded, list) and isinstance(sent, list):
        if len(recorded) != len(sent):
            return f"{path or 'body'}: recorded {len(recorded)} items, sent {len(sent)}"
        for index, (a, b) in enumerate(zip(recorded, sent, strict=True)):
            if a != b:
                return _first_difference(a, b, f"{path}[{index}]")
        return "none"
    if recorded == sent:
        return "none"
    return f"{path or 'body'}: recorded {_summarise(recorded)}, sent {_summarise(sent)}"


__all__ = [
    "Cursor",
    "Exchange",
    "Recording",
    "RecordingNotConsumed",
    "ReplayCursor",
    "ReplayMismatch",
    "ReplayTransport",
    "canonical",
]
