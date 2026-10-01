"""Writing a Trace: append-only, one line per Event, flushed as it happens (D10).

The writer is the only thing that assigns `seq`, `ts`, `span_id`, `parent_span_id`,
`turn` and `dotted_order`. It never rewrites a line: a crash mid-Turn loses nothing that
came before it. Everything a reader wants to know about timing comes back out of the
file by projection (`agentdiag.trace.spans`), never from a second store.

Since ticket 06 a Turn's `deliver` runs on a worker thread, so the Target writes into the
Trace from there while the driver loop waits (phase-5 decision 56). Every write is taken
under one lock, and `end()` seals the Trace: a Target abandoned by a Turn timeout that wakes
later cannot append after `trace/end`, which is the last line by construction.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Literal, Protocol, Self

from agentdiag.trace.events import Event, to_line, validate_closed_fields
from agentdiag.types import Actor, Fidelity, SpanStatus, TerminationReason

BLOB_THRESHOLD = 1024
"""Default size in characters above which a string inside a body becomes a `blob` (D10)."""

BLOB_BEARING_TYPES = frozenset({"request", "response", "tool/result"})
"""The only Event types whose payload is scanned for blobs (ADR-0004 section 5)."""

BLOB_BEARING_FIELDS: dict[str, str] = {
    "request": "body",
    "response": "body",
    "tool/result": "result",
}


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def dotted_segment(ts: int, seq: int, span_id: str) -> str:
    """One Span's segment of a `dotted_order` (ADR-0006 section 3).

    The `seq` digits break millisecond ties, so one lexicographic sort of `dotted_order`
    yields execution order even when two Spans open in the same millisecond.
    """
    return f"{ts:013d}{seq:05d}-{span_id}"


class SpanEndedTwice(RuntimeError):
    """A Span was ended after it had already been ended."""


class TraceSealed(RuntimeError):
    """A write reached a Trace after its `trace/end` (phase-5 decision 56)."""


class TraceFileExists(FileExistsError):
    """A Trace file was opened a second time. One file records one Trial, once.

    Append-only holds within a Trial, not across two passes over it. A reopened file
    would let a second pass append Events after `trace/end` — and because
    `judgement.jsonl` is a Trace file too, it would let a re-scoring overwrite the
    Judge's request and response, which ADR-0003 §7 keeps beside the Score precisely so a
    disputed Verdict can be re-examined without re-running the Judge.
    """


MEMORY_PATH = Path("<memory>")
"""What an in-memory writer names as its path in a message."""


def refuse_existing(path: Path) -> None:
    """`TraceFileExists` when `path` exists: one file records one Trial, once."""
    if path.exists():
        raise TraceFileExists(
            f"{path} already exists; a Trace file records one Trial and is written once "
            "(ADR-0005 §2)"
        )


class _Sink(Protocol):
    """Where a writer's Events go, each as it is written."""

    def append(self, event: Event) -> None: ...

    def close(self) -> None: ...


class _FileSink:
    """A Trace file: one line per Event, flushed as it happens (D10)."""

    def __init__(self, path: Path) -> None:
        self._file: IO[str] = path.open("a", encoding="utf-8")

    def append(self, event: Event) -> None:
        self._file.write(to_line(event) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


class MemorySink:
    """The Events a writer wrote, kept in order instead of a file (ticket 26)."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def append(self, event: Event) -> None:
        self.events.append(event)

    def close(self) -> None:
        return None


class TraceWriter:
    """Appends Events to one Trace file.

    `clock` is injectable so tests and replay produce byte-identical Traces; whatever it
    returns is clamped so `ts` never decreases within a Trace.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        sink: MemorySink | None = None,
        blob_threshold: int = BLOB_THRESHOLD,
        clock: Callable[[], int] | None = None,
    ) -> None:
        """A writer to the Trace file at `path`, created now and refused when it exists;
        or, with `sink` and no `path`, into that `MemorySink` (an Importer's, ticket 26)."""
        if (path is None) == (sink is None):
            raise ValueError("a TraceWriter writes to a path or to a sink, exactly one")
        self.path = Path(path) if path is not None else MEMORY_PATH
        if sink is None:
            refuse_existing(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._sink: _Sink | None = sink if sink is not None else _FileSink(self.path)
        self.blob_threshold = blob_threshold
        self._clock = clock or _now_ms
        self._seq = 0
        self._last_ts = 0
        self._counters: dict[str, int] = {}
        self._stack: list[SpanHandle] = []
        self._open: set[str] = set()
        self._blobs: set[str] = set()
        self._lock = threading.RLock()
        self._sealed = False

    @classmethod
    def in_memory(
        cls, *, clock: Callable[[], int], blob_threshold: int = BLOB_THRESHOLD
    ) -> tuple[TraceWriter, MemorySink]:
        """A writer into a fresh `MemorySink`, and the sink (ticket 26).

        An Importer is pure — rows in, Events out (phase-6 decision 32) — yet its Events
        are the writer's: ids, `dotted_order`, blobs and the closed sets come from here, as
        for any Trace. `write_events` later puts them in the Trial's file, once.
        """
        sink = MemorySink()
        return cls(sink=sink, clock=clock, blob_threshold=blob_threshold), sink

    # --- lifecycle ---

    def start(
        self,
        *,
        trace_id: str,
        scenario: str,
        run: str,
        trial: int,
        continues: str | None = None,
    ) -> Event:
        """The first Event of a Trace: which Trial this file records.

        `continues` names the Scenario whose Adapter session this Trial resumed (phase-5
        decision 13). It is written only when set, so a Trial that opened its own session
        reads exactly as it always has.
        """
        fields: dict[str, Any] = {"continues": continues} if continues is not None else {}
        return self._write(
            "trace/start",
            actor="agentdiag",
            trace_id=trace_id,
            scenario=scenario,
            run=run,
            trial=trial,
            **fields,
        )

    def end(
        self,
        termination: TerminationReason,
        *,
        error: str | None = None,
        detail: str | None = None,
        **extra: Any,
    ) -> Event:
        """The last Event of a Trace: how the Trial ended. It seals the Trace.

        `detail` says what ended it in words (`tool_called cancel_order held after Turn 3`,
        phase-5 decision 47); it is written only when given, so a Trace that has none reads
        exactly as it always has. `extra` fields ride along as given, such as an imported
        voice conversation's `duration_s` (phase-6 decision 35).
        """
        fields: dict[str, Any] = {"detail": detail} if detail is not None else {}
        fields.update(extra)
        with self._lock:
            event = self._write(
                "trace/end", actor="agentdiag", termination=termination, error=error, **fields
            )
            self._sealed = True
        return event

    def close(self) -> None:
        """Close the file. Closing twice is harmless."""
        if self._sink is not None:
            self._sink.close()
            self._sink = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- Events and Spans ---

    def event(
        self,
        type: str,
        *,
        actor: Actor,
        span: SpanHandle | None = None,
        **fields: Any,
    ) -> Event:
        """Append one Event.

        Without `span` the Event lands in the innermost open Span. With `span` it lands
        in that Span and inherits its `turn`, whatever is on the stack.
        """
        return self._write(type, actor=actor, span=span, **fields)

    def span(
        self,
        kind: str,
        *,
        actor: Actor,
        name: str,
        fidelity: Fidelity,
        attributes: Mapping[str, Any] | None = None,
        parent: SpanHandle | None = None,
    ) -> SpanHandle:
        """Open a Span: write its `span/start` and return a handle that can end it.

        The parent is the innermost open Span unless `parent` names another one.
        """
        # Validate before anything advances: a refused Span consumes no id, no clock
        # reading and no `seq`, so the next Span opened is the one that would have been.
        validate_closed_fields(
            "span/start", {"fidelity": fidelity, "attributes": dict(attributes or {})}
        )
        with self._lock:
            self._refuse_if_sealed()
            parent_span = parent if parent is not None else self._current()
            span_id = self._next_span_id(kind)
            turn = self._turn_for(kind, parent_span)
            seq, ts = self._seq, self._next_ts()
            segment = dotted_segment(ts, seq, span_id)
            dotted = f"{parent_span.dotted_order}.{segment}" if parent_span else segment
            handle = SpanHandle(
                writer=self,
                span_id=span_id,
                dotted_order=dotted,
                turn=turn,
                kind=kind,
                parent=parent_span,
                actor=actor,
            )
            self._write(
                "span/start",
                actor=actor,
                _span_id=span_id,
                _parent_span_id=parent_span.span_id if parent_span else None,
                _turn=turn,
                _ts=ts,
                kind=kind,
                name=name,
                fidelity=fidelity,
                dotted_order=dotted,
                attributes=dict(attributes or {}),
            )
            self._open.add(span_id)
            self._stack.append(handle)
            return handle

    # --- internals ---

    def _refuse_if_sealed(self) -> None:
        if self._sealed:
            raise TraceSealed(
                f"Trace {self.path} has ended; nothing is written after its trace/end"
            )

    def _current(self) -> SpanHandle | None:
        return self._stack[-1] if self._stack else None

    def _turn_for(self, kind: str, parent: SpanHandle | None) -> int | None:
        """A `turn` Span is its own Turn; anything else inherits its parent's."""
        if kind == "turn":
            return self._counters["turn"]
        return parent.turn if parent else None

    def _next_span_id(self, kind: str) -> str:
        self._counters[kind] = self._counters.get(kind, 0) + 1
        return f"{kind}-{self._counters[kind]}"

    def _next_ts(self) -> int:
        """The clock's reading, clamped so `ts` never decreases within a Trace."""
        ts = max(self._clock(), self._last_ts)
        self._last_ts = ts
        return ts

    def _end_span(
        self,
        handle: SpanHandle,
        *,
        status: SpanStatus,
        attributes: Mapping[str, Any] | None,
        error: tuple[str, str] | None,
    ) -> Event:
        with self._lock:
            if handle.span_id not in self._open:
                raise SpanEndedTwice(f"Span {handle.span_id} has already ended")
            # `_write` validates before it writes, so a refused `span/end` raises here with
            # the Span still open: the Trace never ends up with an unmatched `span/start`.
            event = self._write(
                "span/end",
                actor=handle.actor,
                _span_id=handle.span_id,
                _parent_span_id=handle.parent.span_id if handle.parent else None,
                _turn=handle.turn,
                status=status,
                attributes=dict(attributes or {}),
                error={"type": error[0], "message": error[1]} if error else None,
            )
            self._open.discard(handle.span_id)
            if handle in self._stack:
                self._stack.remove(handle)
            return event

    def _write(
        self,
        type: str,
        *,
        actor: Actor,
        span: SpanHandle | None = None,
        _span_id: str | None = None,
        _parent_span_id: str | None = None,
        _turn: int | None = None,
        _ts: int | None = None,
        **fields: Any,
    ) -> Event:
        """Validate the closed sets, assign the coordinates, substitute blobs, append, flush.

        Validation comes first and is dispatched on the Event type, so it holds for every
        write path — the convenience methods and a raw `event()` alike — and a refused
        Event leaves no line, no blob and no changed state behind. Taken under the lock, and
        refused once the Trace is sealed (decision 56).
        """
        with self._lock:
            self._refuse_if_sealed()
            if self._sink is None:
                raise RuntimeError(f"Trace {self.path} is closed")

            validate_closed_fields(type, fields)

            if _span_id is None:
                # `trace/end` is the Trace's own last line, never inside a Span a Target
                # abandoned by a Turn timeout left open (decision 56).
                here = (
                    span if span is not None else None if type == "trace/end" else self._current()
                )
                span_id = here.span_id if here else None
                parent_span_id = here.parent.span_id if here and here.parent else None
                turn = here.turn if here else None
            else:
                span_id, parent_span_id, turn = _span_id, _parent_span_id, _turn

            if type in BLOB_BEARING_TYPES:
                field = BLOB_BEARING_FIELDS[type]
                if field in fields:
                    fields[field] = self._substitute_blobs(fields[field], actor=actor)

            ts = _ts if _ts is not None else self._next_ts()
            event = Event(
                seq=self._seq,
                ts=ts,
                type=type,
                actor=actor,
                turn=turn,
                span_id=span_id,
                parent_span_id=parent_span_id,
                **fields,
            )
            self._seq += 1
            self._sink.append(event)
            return event

    def _substitute_blobs(self, value: Any, *, actor: Actor) -> Any:
        """Replace every long string inside `value` by a `{"blob": "sha256:..."}` ref.

        Each distinct sha256 is written as one `blob` Event the first time it is seen in
        this file; every later sighting is the reference alone.
        """
        if isinstance(value, str):
            if len(value) <= self.blob_threshold:
                return value
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
            if digest not in self._blobs:
                self._blobs.add(digest)
                self._write(
                    "blob",
                    actor=actor,
                    sha256=digest,
                    size=len(value),
                    content=value,
                )
            return {"blob": f"sha256:{digest}"}
        if isinstance(value, Mapping):
            return {k: self._substitute_blobs(v, actor=actor) for k, v in value.items()}
        if isinstance(value, list):
            return [self._substitute_blobs(v, actor=actor) for v in value]
        return value


def write_events(path: Path, events: Sequence[Event]) -> Path:
    """Put Events a `TraceWriter.in_memory` wrote into a Trial's Trace file, once.

    The file is refused when it exists, as `TraceWriter` refuses it: one file records one
    Trial, once (ADR-0005 §2).
    """
    path = Path(path)
    refuse_existing(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(to_line(event) + "\n" for event in events), encoding="utf-8")
    return path


class SpanHandle:
    """A Span that is open: what Events written inside it inherit, and how it ends."""

    def __init__(
        self,
        *,
        writer: TraceWriter,
        span_id: str,
        dotted_order: str,
        turn: int | None,
        kind: str,
        parent: SpanHandle | None,
        actor: Actor,
    ) -> None:
        self._writer = writer
        self.span_id = span_id
        self.dotted_order = dotted_order
        self.turn = turn
        self.kind = kind
        self.parent = parent
        self.actor: Actor = actor
        """The actor that opened this Span; its `span/end` carries the same one."""

    def event(self, type: str, *, actor: Actor, **fields: Any) -> Event:
        """Append one Event inside this Span."""
        return self._writer.event(type, actor=actor, span=self, **fields)

    def end(
        self,
        *,
        status: SpanStatus = "ok",
        attributes: Mapping[str, Any] | None = None,
        error: tuple[str, str] | None = None,
    ) -> Event:
        """Close this Span. Ending a Span twice raises `SpanEndedTwice`."""
        return self._writer._end_span(self, status=status, attributes=attributes, error=error)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        """Never swallows: an exception that left a Span still reaches the caller."""
        if exc is not None:
            self.end(status="error", error=(type(exc).__name__, str(exc)))
            return False
        self.end()
        return False


__all__ = [
    "BLOB_THRESHOLD",
    "MemorySink",
    "SpanEndedTwice",
    "SpanHandle",
    "TraceFileExists",
    "TraceSealed",
    "TraceWriter",
    "dotted_segment",
    "write_events",
]
