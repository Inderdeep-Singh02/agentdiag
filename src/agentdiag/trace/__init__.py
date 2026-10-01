"""Traces: writing them, reading them back, and projecting Spans and Metrics over them.

A Trace is one append-only JSONL file of Events (ADR-0004). Everything else here —
Spans, durations, tokens — is a pure projection over what the file already says, so
nothing can disagree with the recording (D11).

`agentdiag.trace.show` and `agentdiag.trace.export` are not re-exported here: both read a
Run directory, and `agentdiag.run` reads Traces, so importing either from this package's
`__init__` would close a cycle. Import them by module.
"""

from __future__ import annotations

from agentdiag.trace.events import Event, event_fields, from_line, to_line
from agentdiag.trace.otel_genai import ATTRIBUTES, PINNED_COMMIT, REGISTRY_URL
from agentdiag.trace.reader import follow, read_trace, resolve_blobs
from agentdiag.trace.spans import (
    ActorTotals,
    Metrics,
    Span,
    TraceTotals,
    children,
    metrics,
    project_spans,
    trace_totals,
)
from agentdiag.trace.writer import (
    BLOB_THRESHOLD,
    SpanEndedTwice,
    SpanHandle,
    TraceFileExists,
    TraceSealed,
    TraceWriter,
    dotted_segment,
)
from agentdiag.types import Actor, Fidelity, TerminationReason

__all__ = [
    "ATTRIBUTES",
    "BLOB_THRESHOLD",
    "PINNED_COMMIT",
    "REGISTRY_URL",
    "Actor",
    "ActorTotals",
    "Event",
    "Fidelity",
    "Metrics",
    "Span",
    "SpanEndedTwice",
    "SpanHandle",
    "TerminationReason",
    "TraceFileExists",
    "TraceSealed",
    "TraceTotals",
    "TraceWriter",
    "children",
    "dotted_segment",
    "event_fields",
    "follow",
    "from_line",
    "metrics",
    "project_spans",
    "read_trace",
    "resolve_blobs",
    "to_line",
    "trace_totals",
]
