"""The Importer protocol: Evidence rows in, a Trace out, and what the evidence lacked
(phase-6 decision 32, ADR-0013 §5-§6).

An Importer turns what a Target's live side recorded — proxy rows, a conversation record,
a voice conversation — into a Trace in the ADR-0004 format, at the Fidelity that evidence
supports, so an imported Trial is judged by the same Evals as a driven one. Two rules make
that honest:

- **What is lost is said** (ADR-0013 §6). Every Span fact the evidence cannot give carries
  ADR-0006 §4's not-observed state (`agentdiag.not_observed`), a cut body its `truncated`
  marker, and the Trace's second Event, `import/source`, lists all of them as `Lacked`, so
  an Eval answers `unverifiable` with the right reason instead of guessing.
- **The Events are the writer's.** An Importer is pure — rows in, Events out, no file, no
  Connector, no SDK at import — yet it writes through `TraceWriter.in_memory`, so ids,
  `dotted_order`, blobs and the closed sets are exactly a live Trace's. The `import`
  command stamps the Run the Trace lands in (`stamped`) and writes it once.

Every Span an Importer writes carries `agentdiag.span.origin: imported` (phase-5 decision
58) and the Importer's Fidelity.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from agentdiag.connector.base import EvidenceRows
from agentdiag.evidence import Lacked
from agentdiag.trace.attributes import NOT_OBSERVED, SPAN_ORIGIN
from agentdiag.trace.events import Event
from agentdiag.trace.writer import SpanHandle, TraceWriter
from agentdiag.types import EvidenceKind, Fidelity, NotObservedFact, SpanOrigin, SpanStatus

IMPORTED: SpanOrigin = "imported"

SCENARIO_PREFIX = "imported-"
"""An imported Trial's Scenario id is this plus the slug of the conversation id."""

IMPORT_SOURCE = "import/source"
"""The Event naming the evidence a Trace was imported from and what it lacked."""


class Imported(BaseModel):
    """What one Importer made of one conversation's rows."""

    scenario_id: str
    """`imported-<slug of the conversation id>`."""

    events: list[Event]
    """The Trace, ADR-0004 shape: `trace/start` first, `import/source` second, `trace/end`
    last. Its `trace/start` names no Run until the command stamps one."""

    lacked: list[Lacked]
    conversation_id: str | None


class ImportRowsError(ValueError):
    """Rows the Importer cannot read: not in the generic shape, or none at all."""


@runtime_checkable
class Importer(Protocol):
    """Turns one Evidence store's rows into a Trace (decision 32)."""

    kind: EvidenceKind
    fidelity: Fidelity
    """What every Span it writes carries."""

    def import_rows(self, rows: EvidenceRows, *, clock_origin: str | None = None) -> Imported:
        """The Trace the rows describe. `clock_origin`, when given, is the Trace's start
        time (ISO-8601) instead of the evidence's first instant: the time a Trace that has
        no timestamps of its own steps from."""
        ...


def scenario_id_for(conversation_id: str | None) -> str:
    """`imported-<slug>`: lowercase, every run of other characters one hyphen."""
    kept = "".join(c.lower() if c.isascii() and c.isalnum() else "-" for c in conversation_id or "")
    slug = "-".join(part for part in kept.split("-") if part)
    return SCENARIO_PREFIX + (slug or "conversation")


def epoch_ms(value: str) -> int:
    """An ISO-8601 instant as epoch milliseconds; a naive one is read as UTC."""
    moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return round(moment.timestamp() * 1000)


def parsed[M: BaseModel](model: type[M], row: Mapping[str, Any], index: int, kind: str) -> M:
    """One row read as its generic shape, or `ImportRowsError` naming the row."""
    try:
        return model.model_validate(dict(row))
    except (ValidationError, ValueError) as exc:
        raise ImportRowsError(
            f"{kind} row {index} is not a {model.__name__} (phase-6 decision 33): {exc}"
        ) from exc


def instant(value: str, where: str) -> int:
    """`epoch_ms`, or `ImportRowsError` naming the field that is not an instant."""
    try:
        return epoch_ms(value)
    except (TypeError, ValueError) as exc:
        raise ImportRowsError(f"{where} is not an ISO-8601 instant: {value!r}") from exc


class Recorder:
    """An in-memory `TraceWriter` on a clock the Importer sets to the evidence's times.

    `at(ms)` moves the clock. A Trace's `ts` never decreases (ADR-0004 §1), so an instant
    earlier than one already written cannot be recorded as itself: the writer clamps it, and
    `at` says so in `clamped`, for the Importer to mark the Span that instant belonged to
    rather than let an interval shrink silently. Seeded at the earliest evidence instant, a
    Recorder never clamps a Trace whose evidence runs forward.
    """

    def __init__(self, start_ms: int) -> None:
        self.now = start_ms
        self.high = start_ms
        self.clamped = False
        self.writer, self._sink = TraceWriter.in_memory(clock=self._clock)

    def _clock(self) -> int:
        return self.now

    def at(self, ms: int) -> TraceWriter:
        self.clamped = ms < self.high
        self.now = ms
        self.high = max(self.high, ms)
        return self.writer

    def events(self) -> list[Event]:
        return list(self._sink.events)


class Marked:
    """An imported Span and what it has not observed, which may grow before it ends."""

    def __init__(self, handle: SpanHandle, facts: Sequence[NotObservedFact]) -> None:
        self.handle = handle
        self.opened_with: list[NotObservedFact] = list(dict.fromkeys(facts))
        self.facts: list[NotObservedFact] = list(self.opened_with)

    @property
    def span_id(self) -> str:
        return self.handle.span_id

    def add(self, fact: NotObservedFact) -> None:
        if fact not in self.facts:
            self.facts.append(fact)

    def end(
        self,
        *,
        status: SpanStatus = "ok",
        attributes: Mapping[str, Any] | None = None,
        error: tuple[str, str] | None = None,
    ) -> None:
        """End the Span; its end attributes carry the whole not-observed list when it grew
        after the Span opened (a Span's attributes are its start's, updated by its end's)."""
        closing = dict(attributes or {})
        if self.facts != self.opened_with:
            closing[NOT_OBSERVED] = list(self.facts)
        self.handle.end(status=status, attributes=closing, error=error)


def span_attributes(
    attributes: Mapping[str, Any], not_observed: Sequence[NotObservedFact] = ()
) -> dict[str, Any]:
    """An imported Span's start attributes: its own, the origin, and what it lacks."""
    marked: dict[str, Any] = {**attributes, SPAN_ORIGIN: IMPORTED}
    if not_observed:
        marked[NOT_OBSERVED] = list(dict.fromkeys(not_observed))
    return marked


def open_trace(
    recorder: Recorder,
    *,
    kind: EvidenceKind,
    scenario_id: str,
    conversation_id: str | None,
    rows: EvidenceRows,
    lacked: Sequence[Lacked],
) -> None:
    """`trace/start`, then `import/source` naming the store, the query, the row count and
    everything not observed (decision 34)."""
    writer = recorder.writer
    writer.start(
        trace_id=f"{kind}:{conversation_id or ''}",
        scenario=scenario_id,
        run="",
        trial=1,
    )
    writer.event(
        IMPORT_SOURCE,
        actor="agentdiag",
        store=kind,
        query=rows.query.model_dump(mode="json"),
        rows=len(rows.rows),
        read_at=rows.read_at,
        truncated=rows.truncated,
        lacked=[entry.model_dump() for entry in lacked],
    )


def stamped(events: Sequence[Event], run_id: str) -> list[Event]:
    """The Events with `trace/start` naming the Run the Trace lands in, as a live Trace's
    does: `run` and `trace_id` (`<run>/<scenario>/<trial>`)."""
    out: list[Event] = []
    for event in events:
        if event.type == "trace/start":
            fields = dict(event.model_extra or {})
            fields["run"] = run_id
            fields["trace_id"] = f"{run_id}/{fields.get('scenario')}/{fields.get('trial', 1)}"
            event = Event.model_validate({**event.model_dump(exclude=set(fields)), **fields})
        out.append(event)
    return out


__all__ = [
    "IMPORTED",
    "IMPORT_SOURCE",
    "SCENARIO_PREFIX",
    "ImportRowsError",
    "Imported",
    "Importer",
    "Lacked",
    "Marked",
    "Recorder",
    "epoch_ms",
    "instant",
    "open_trace",
    "parsed",
    "scenario_id_for",
    "span_attributes",
    "stamped",
]
