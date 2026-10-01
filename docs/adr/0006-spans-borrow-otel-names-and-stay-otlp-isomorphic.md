---
status: accepted
date: 2026-09-22
---

# 0006 — Spans borrow OpenTelemetry GenAI names, own what OTel lacks, and stay OTLP-isomorphic

## Context

The format research found: the OTel GenAI semantic
conventions moved to their own repository in 2026 and every `gen_ai.*` attribute is
Development-stability; OTel has no cost attribute; no surveyed format marks
truncation or missing evidence; OTel GenAI does not require a tool-execution span to nest
under the LLM call that requested it; OTLP as an on-disk format costs attribute
envelopes, integer enums, hex ids and string nanoseconds on every read. A Target
that already emits OTel GenAI spans could one day be profiled without an Adapter; the
author deferred that but wants it to stay possible.

## Decision

1. **Derived Spans use OTel GenAI attribute names where they exist**: `gen_ai.operation.name`,
   `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.*`,
   `gen_ai.response.finish_reasons`, `gen_ai.tool.name`, `gen_ai.tool.call.id`,
   `gen_ai.conversation.id`, `error.type`. Pinned to `open-telemetry/semantic-conventions-genai`
   commit `8ffdf56` and vendored as a machine-checked list, so drift is visible.
2. **Span kinds are a recommended open set**: `turn`, `llm_call`, `tool_call`, `retrieval`,
   `simulate`, `judge` (the last only in `judgement.jsonl`, ADR-0004 §3). Every precedent
   surveyed moved from a closed enum to an open set. Verdicts (ADR-0003) are the
   opposite case and stay closed.
3. **Hierarchy is referential**: explicit `parent_span_id` plus a LangSmith-style
   `dotted_order`, so one lexicographic sort yields execution order. A tool Span nests under
   the `llm_call` that requested it. This is stricter than OTel GenAI; ingesting foreign
   telemetry will need a re-parenting step keyed on `gen_ai.tool.call.id`.
4. **Fields no format provides are ours**: `fidelity` (ADR-0001), `actor` (ADR-0004),
   per-field `truncated` with the original length, and an explicit not-observed state distinct
   from null. Token usage is an open map with a reserved `total` key, following Langfuse. Cost
   is stored per Span in USD under OpenInference's `llm.cost.*` names, and the price-table
   version is stamped on the Run (ADR-0005), so a comparison can say "priced under different
   tables".
5. **OTLP-isomorphic.** Ids, parent pointers, timestamps and attributes keep OTLP semantics, so
   an OTLP importer or exporter is a mechanical transform. An OTLP-receiver Adapter is deferred
   (ADR-0001), not rejected.

## Considered options

- OTLP/JSON as the native store. Rejected: its costs are transport costs paid on every read of
  every fixture, in a tool whose defining need is that a person and a Judge can both read a
  Trace.
- OpenInference. Rejected: flattened indexed message keys collide with attribute limits, and it
  cannot express requested-versus-resolved model.
- Inventing all names. Rejected: OTel names cost nothing and buy a migration path and a
  ready-made vocabulary for requested-versus-resolved model and finish reasons.

## Consequences

- The vendored attribute list must be re-checked when the pinned commit moves.
- Requested and resolved model are always both recorded, so `compare` can detect a model swap
  mechanically (ADR-0005 §6).
