# Changelog

## 0.1.0 — 2026-10-01

First public release: a profiler and evaluator for agentic systems, and a kit for
maintaining them.

**Profile and evaluate**
- A Target is driven through Scenarios by an Adapter; every Trial is recorded as an
  append-only Trace of Events, with Spans that borrow OpenTelemetry GenAI names and say how
  they were obtained (`instrumented`, `reconstructed`, `observed`).
- Two Adapters: `inprocess` for a Python Target built from a factory, and `http` for a Target
  reachable only through the chat endpoint it ships, with the `json` and `sse-json` Dialects,
  identity modes as Fixtures, per-environment timeouts and tool truth read back from proxy
  rows through the Connector.
- One Scenario schema (`docs/scenario-schema.md`): scripted Turns, Simulated User Turns with
  a stop criterion, Fixtures, typed Evals with short forms, Suite-level guardrail rules and
  provenance on every Scenario. `validate` checks Suites and the Manifest offline.
- Mechanical Evals (text, tool calls and arguments, order, latency, cost per model Span) and
  judged Evals with calibration notes, per-Eval Judge overrides and a cited Diagnosis. Five
  Verdicts with closed reason codes; every Score cites the Spans it judged.
- The Judge runs through the Anthropic API or a Claude Code login; the in-process Adapter
  can carry the Target's own model calls through the same login. A Run records the Backend.
- Trials with pass^k, `compare` leading with the configuration diff, `rescore` over a stored
  Run's Traces, `list` over a derived SQLite Index, `show` as a story, `export` to Perfetto
  and speedscope, an HTML Report with a flame graph.

**Maintain**
- A Workspace of Targets with a Registry derived from their Manifests; Manifest v1 with
  Fingerprints; Sync between a Target's local files, its deployed set and its last
  Fingerprint, section by section, flagging and never invalidating.
- A Connector per platform manages the deployed set and Evidence stores; `pull` and `push`
  with Restore points and Push records; `sync --check` records a Sync break whenever the
  comparison is broken.
- Trace Importers for proxy rows, conversation records and voice conversations; `import`
  writes a Run from evidence agentdiag did not drive.
- `discover` drafts a Manifest for an unknown Target; `generate` drafts a Suite from its
  prompt rules and tools.
- Change records carry a fix from trigger to verification; the `verified` and `refuted`
  gates go through `compare`; a Flow view tells each record as a story; redaction on write.
- A local web UI (`serve`) and a Workspace Dashboard.

**Distribute**
- Core imports no plugin: Adapter, Connector and Dialect kinds are found by Python entry
  points (`docs/plugins.md`); conformance suites for Adapters and Connectors ship with core.
- Four operating skills for a coding agent (`agentdiag-discover`, `agentdiag-generate`,
  `agentdiag-fix-cycle`, `agentdiag-correction`), installed with `agentdiag init --skills`.
- Credentials live in one file outside every repository (`~/.agentdiag/env`), read once per
  command and never written by agentdiag.
