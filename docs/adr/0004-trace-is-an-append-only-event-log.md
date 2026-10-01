---
status: accepted
date: 2026-09-22
---

# 0004 — A Trace is an append-only JSONL Event log, and Spans are derived from it

## Context

Phase 4 needs Trace capture; Phase 7 needs a flame graph. The author's requirement is complete
per-test logs in the style of deepseek-harness, readable by a person without the tool.
deepseek-harness stores one append-only JSONL file per session with flat rows carrying turn
and step coordinates and derives every timing by projection over the durable log.
The format research found that every path into a flame-graph viewer destroys payload and that
content is about 95 percent of a Span's bytes. The Judge acts after a Trial, possibly in a
later rescore Run (ADR-0005), so it cannot write into a Trace that is meant to be immutable.

## Decision

1. **A Trace is one append-only JSONL file of Events**, one JSON object per line, written as
   things happen. Every Event carries `seq`, `ts` (epoch milliseconds), `type`, `actor`
   (`target`, `simulated_user`, `adapter`, `agentdiag`), `span_id`, `parent_span_id` and
   `turn`. `turn` is a denormalised coordinate for reading and grepping the flat file: it must
   equal the index of the Event's nearest enclosing Turn Span, which is authoritative. Core
   Event types: `trace/start`, `trace/end`, `span/start`, `span/end`, `message`, `request`,
   `response`, `tool/call`, `tool/result`, `fixture/applied`, `sync/checked`, `blob`, `note`,
   `error`. The set is open; unknown types are carried through.
2. **Spans and Metrics are projections over the Events.** A Span is its `span/start` and
   `span/end` pair (attribute vocabulary in ADR-0006); an unmatched start is an open Span and
   is shown as one. Duration, time-to-first-token and tool wall time are computed from the log
   and never recorded separately, so they cannot disagree with it.
3. **The Trace is immutable once the Trial ends.** It holds every actor's activity during the
   Trial: the Target, the Simulated User, the Adapter and agentdiag itself (Fixtures applied,
   the Sync check). The Judge acts afterwards and writes its own append-only file per Trial,
   `judgement.jsonl`, in the same Event format with `actor: judge`, owned by whichever Run
   produced the Scores. `show` renders both as one story.
4. **A Trace is replay input.** `request` and `response` Events carry the full model request
   and response bodies, so the Target's model boundary, the Simulated User and the Judge can
   all be replayed from what was recorded. Designed in now, implemented when a
   phase needs it.
5. **Self-contained with deduplication.** Large repeated content (system prompt, tool schemas,
   prior messages) is stored once as a content-addressed `blob` Event (sha256) in the same
   file and referenced by hash. No sidecar files. Readers resolve references; a prompt change
   appears as one new blob.
6. **Human-readable by construction**: no envelopes, no integer enums, no hex-only ids, field
   names are words. `agentdiag show <run> <scenario>` renders a Trial step by step in the
   terminal; `--json` emits raw Events; `--follow` streams during a Trial.
7. **Exporters are views, never stores**: one-way export to Chrome trace JSON (for Perfetto)
   and to a speedscope evented profile with one profile per Turn. The UI reads the JSONL.

## Considered options

- One row per completed Span, content nested inside. Rejected: a crash mid-Turn loses the
  Span, and the file no longer reads as what happened in order.
- Judge Events appended to the Trace. Rejected: a rescore Run would mutate another Run's Trace.
- Prompts tokenised into a sidecar, as deepseek-harness does. Rejected: a Trace must be
  shippable and judgeable alone; in-file blobs give the same deduplication.
- Chrome trace format as the store. Rejected: legacy by its owner's description, positional
  hierarchy, no error, token, cost or conversation concept.

## Consequences

- Trace files are large but deduplicated; retention policy is a later decision.
- The flame graph is always a projection; the Phase 7 UI must read the JSONL for everything
  except geometry.
- Span `actor` lets the Target's latency and cost be reported without the Simulated User's
  share, and the Judge's cost is accounted in `judgement.jsonl`.
