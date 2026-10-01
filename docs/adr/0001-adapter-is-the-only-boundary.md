---
status: accepted
date: 2026-09-22
---

# 0001 — The Adapter is the only thing that touches a Target (narrowed by ADR-0011), and every Span says how it was obtained

## Context

agentdiag must profile Targets it did not write, from an in-process Python callable to an
HTTP/SSE endpoint whose stream reported zero tool calls on every turn (as in a production
agent-maintenance repository studied during design, where tool truth had to come from a saved
trace). The reference repository inlined its transport in the runner and reconstructed tool
calls from proxy logs, attributing them to turns by position, which turned four correct
behaviours into "defects". deepseek-harness states the cleanest boundary rule found: drive
the system only through the entry points it ships, and substitute only the non-deterministic
boundary.

## Decision

1. The Adapter is the sole component that converses with a Target. It opens a session,
   delivers user Turns, applies Fixtures, returns responses and emits Spans. Nothing else
   calls a Target's conversational entry points, and nothing but the Adapter and the Connector
   imports Target code. (Narrowed 2026-09-25 by ADR-0011: the Connector reads a
   Target's deployed set and Evidence stores and writes the deployed set through an explicit
   push; it never converses.)
2. An Adapter drives a Target only through interfaces the Target already ships to its users.
   It may substitute the Target's non-deterministic boundary (model client, network, clock)
   for capture or replay. It never adds a hidden entry point.
3. Every Span carries a Fidelity: `instrumented` (captured inside the Target's process),
   `reconstructed` (inferred from transport or proxy evidence), or `observed` (only what the
   response surface showed). Reconstructed Spans are attributed to Turns by content, never
   by position. Evals declare the minimum Fidelity they need and return `unverifiable` below
   it. Every Score records the Fidelity that grounded it.
4. A Target is the definition of the system, not a deployment of it. Its deployment environment (local, staging, prod, model
   tier) is a named Adapter configuration recorded in each Run. Comparing environments is
   comparing Runs whose Adapter configurations differ.
5. agentdiag modifies a Target only through a Connector push (ADR-0011 §6–§7). Every Adapter
   declares a side-effect class: `none`, `sandboxed`, or `live`. A Run against a `live` Adapter
   requires an explicit flag, and the Run records it. (First sentence replaced 2026-09-25 by
   ADR-0011; the original read "agentdiag never modifies a Target's code or
   configuration".)
6. Fixtures are declared on the Scenario; the mechanism that injects them (the reference repository's six
   `inject_identity` modes) is Adapter configuration, not Scenario schema.

## Considered options

- Require instrumentation (an OTel SDK) in every Target. Rejected: the reference repository's Targets
  are black-box HTTP/SSE endpoints.
- No Fidelity marking. Rejected: the reference repository's SSE-graded tool verdicts "are not
  tool truth", and a Score must be able to say so.
- Each environment its own Target. Rejected: doubles Manifests and makes "staging vs prod" a
  different mechanism from "prompt A vs prompt B".
- An OTLP-receiver Adapter that ingests telemetry from Targets agentdiag did not launch.
  Deferred, not rejected: the Span model stays OTLP-isomorphic so it can be added (ADR-0004).

## Consequences

- The Phase 4 Adapter wraps an in-process Python callable at Fidelity `instrumented`. The
  HTTP Adapter is `reconstructed` or `observed`, and its Scores say so.
- The Adapter interface has two obligations, not one: drive the shipped entry point, and own
  the swap of the non-deterministic boundary.
- Adapters must return "unknown" for tool arguments they could not see, never "no arguments"
  (the reference repository's `_tool_args` lesson).
