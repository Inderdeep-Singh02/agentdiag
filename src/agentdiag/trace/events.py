"""One line of a Trace (ADR-0004 sections 1 and 6).

An Event is one JSON object per line: when, what, who, where, then the type-specific
fields flat beside them. The core seven fields are always written, `null` included, so
`cat` reads left to right and `grep` finds a Turn by coordinate.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from agentdiag.trace.attributes import SPAN_ORIGIN
from agentdiag.types import (
    Actor,
    Fidelity,
    NotCheckedReason,
    SideEffectClass,
    SpanOrigin,
    SpanStatus,
    SyncStatus,
    TerminationReason,
)


class Event(BaseModel):
    """One thing that happened at an instant.

    `extra="allow"` carries unknown Event types and type-specific fields through a
    read/write round trip untouched (ADR-0004 section 1: the type set is open).
    """

    model_config = ConfigDict(extra="allow")

    seq: int
    """0-based, contiguous within the file."""

    ts: int
    """Epoch milliseconds; monotonic non-decreasing within a Trace."""

    type: str
    """`trace/start`, `span/start`, `message`, ... The set is open."""

    actor: Actor

    turn: int | None
    """1-based index of the nearest enclosing `turn` Span; null outside one."""

    span_id: str | None
    """The Span this Event belongs to; for `span/start` and `span/end`, that Span."""

    parent_span_id: str | None


class SpanStartFields(BaseModel):
    """The closed-set fields a `span/start` carries, and the one closed attribute."""

    model_config = ConfigDict(extra="ignore")

    fidelity: Fidelity
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def _origin_is_closed(cls, attributes: dict[str, Any]) -> dict[str, Any]:
        """`agentdiag.span.origin`, when present, is one of `SpanOrigin` (decision 58)."""
        if SPAN_ORIGIN in attributes:
            _ORIGIN.validate_python(attributes[SPAN_ORIGIN])
        return attributes


_ORIGIN: TypeAdapter[SpanOrigin] = TypeAdapter(SpanOrigin)


class SpanEndFields(BaseModel):
    """The closed-set fields a `span/end` carries."""

    model_config = ConfigDict(extra="ignore")

    status: SpanStatus


class TraceEndFields(BaseModel):
    """The closed-set fields a `trace/end` carries."""

    model_config = ConfigDict(extra="ignore")

    termination: TerminationReason


class SyncCheckedFields(BaseModel):
    """The closed-set fields a `sync/checked` carries."""

    model_config = ConfigDict(extra="ignore")

    status: SyncStatus
    reason: NotCheckedReason | None = None


CLOSED_FIELDS_BY_TYPE: dict[str, type[BaseModel]] = {
    "span/start": SpanStartFields,
    "span/end": SpanEndFields,
    "trace/end": TraceEndFields,
    "sync/checked": SyncCheckedFields,
}
"""Which Event types carry closed sets, and the model that guards each (D2, ADR-0003 section 1)."""


def validate_closed_fields(type: str, fields: Mapping[str, Any]) -> None:
    """Raise `ValidationError` when an Event's closed-set field is outside its set.

    `Event` is `extra="allow"`, so a Literal outside its set would otherwise slip through
    as an extra field. Every write goes through here, whichever method wrote it, and it
    runs before the writer changes any state — a refused Event leaves nothing behind.
    """
    guard = CLOSED_FIELDS_BY_TYPE.get(type)
    if guard is not None:
        guard.model_validate(dict(fields))


def to_line(event: Event) -> str:
    """Serialise one Event as the single JSON line a Trace file holds."""
    return json.dumps(event.model_dump(mode="json"), ensure_ascii=False)


def from_line(line: str) -> Event:
    """Parse one line of a Trace file back into an Event."""
    return Event.model_validate(json.loads(line))


def event_fields(event: Event) -> dict[str, Any]:
    """The type-specific fields of an Event: everything but the core seven."""
    return dict(event.model_extra or {})


__all__ = [
    "CLOSED_FIELDS_BY_TYPE",
    "Actor",
    "Event",
    "Fidelity",
    "NotCheckedReason",
    "SideEffectClass",
    "SpanEndFields",
    "SpanStartFields",
    "SpanStatus",
    "SyncCheckedFields",
    "SyncStatus",
    "TerminationReason",
    "TraceEndFields",
    "event_fields",
    "from_line",
    "to_line",
    "validate_closed_fields",
]
