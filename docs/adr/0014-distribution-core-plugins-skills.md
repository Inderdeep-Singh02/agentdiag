---
status: accepted
date: 2026-09-25
---

# 0014 — Distribution in three layers: core, platform plugins, operating skills

## Context

The kit must be a production agent-maintenance repository studied during design, stripped of
every platform-specific endpoint and methodology, and usable with any coding agent. A review
of the reference repository found that its product is its `AGENTS.md` routing plus six
maintenance skills that make a coding agent run one maintenance cycle with budgets and gates,
and that agentdiag had said nothing about how it installs into an existing repository, what a
platform plugin is, or where the operating procedure lives.

## Decision

1. **Core** is the schema, the Trace, the Evals, Runs, `compare`, the Index, the Report, the UI,
   and the protocols: Adapter, Connector, Importer, Simulated User, Dialect. Core imports
   no plugin.
2. **A plugin is one platform's Connector, Dialect, Evidence store readers and identity
   modes**, registered through Python entry points and discovered by name from the Manifest's
   `adapter.kind` and `connector.kind`. The core Importers take rows in generic shapes; a
   plugin's Connector returns its platform's rows in those shapes. The first platform plugin is distributed separately
   as its own package. Platform facts (endpoint shapes,
   full-replace PUT, `x-chat-history-id`, the `0:`/`8:` dialect) never appear in core.
3. **Skills are the operating procedure**, written with `writing-for-agents` and installed
   under the Workspace root (`.claude/skills/` beside `.agentdiag/`) by `init --skills`:
   discovery and generation and the generic forms of the reference repository's six
   (`preflight`, `review-chat`, `fix-cycle`, `regression-run`, `build-tests`, `correction`;
   the reference repository's `feedback` and `audit-run` are renamed because both words are avoided in
   `CONTEXT.md`), each calling agentdiag commands and never a platform's. Budgets (two evidence
   commands, one read bundle, diff first) are skill text.
   _Amended by ADR-0016 §1 and §6 (2026-10-02): the skills are installed as one Tracked copy
   under `.agents/skills/`, reached from `.claude/skills/` by per-skill links, and the
   Orientation page `init` writes at the Workspace root is part of the operating procedure._
4. **Install shapes.** `pip install agentdiag` then `agentdiag init` at a repository root or an
   empty directory creates a Workspace with one Target, and `init --target <slug>` adds one
   (ADR-0013); a plugin is a second package.

## Considered options

- Everything in one package with a `platforms/` directory. Rejected: the platform facts that must not
  be genericised would leak into core one import at a time.
- Skills as documentation only. Rejected: the reference repository shows the skills are what makes the tool
  usable by an agent.
