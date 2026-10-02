---
status: accepted
date: 2026-09-25
---

# 0013 — One workspace holds many Targets, a Registry is derived from their Manifests, and Traces can be imported

## Context

ADR-0005 §1 puts `.agentdiag/` at the Target's repository root and rejected the
`agents/<slug>/` layout of a production agent-maintenance repository studied during design
because it "works only when one team owns every Target". The reference repository is ten
Targets in one repository, and a platform-hosted Target has no repository of its own. The kit
must give a dashboard over seven or more different agents and hold all the information on each
agent. A rule that agentdiag never writes into a Target's own repository collides with putting
`.agentdiag/` in it. Every change record in the reference repository starts from a production
chat nobody drove, and every Trace today comes from a Run through an Adapter.

## Decision

1. **A Workspace is one root holding many Targets**: `<root>/.agentdiag/targets/<slug>/` with
   that Target's Manifest, Fingerprint, Suites, Calibration Notes, Change records, push records
   and `runs/`. A single-Target root stays valid: it is a Workspace with one Target and the
   older single-Target layout is read as `targets/default/` (an expand–contract migration).
   `runs/` and Restore points are gitignored; everything else is committed.
   _Amended by ADR-0016 (2026-10-02): the root also holds `AGENTS.md`, `CLAUDE.md`,
   `GEMINI.md`, `.agents/skills/` and `.claude/skills/`; `.agentdiag/` holds the vocabulary copy
   `CONTEXT.md`; and each Target directory holds `maintainer_notes.md`._
2. **The Workspace root is the user's choice**, given by `--root` or found by walking up from
   the current directory. For a Target whose repository agentdiag must not write into, the root
   is outside that repository, so the rule holds; moving it into the Target's repository later is the owner's
   decision, not this ADR's. agentdiag writes only under its Workspace root and to a deployed
   Target only through a Connector push (ADR-0011 §7). This strains ADR-0005's reason
   for rejecting a central store ("Scenarios belong with the code they test"): for a Target
   whose definition is a platform record there is no code to belong with, and for such a Target
   the local copies of its deployed sections live under the Workspace, written by `discover
   --from-connector` and `pull`, until the owner moves the root.
3. **A Registry is derived from the Manifests**, never authored: the list of Targets with
   their Family and `channel` (a chat build and a voice build of one persona are two Targets of
   one Family), environments and Suites. The Index (ADR-0005 §5) is Workspace-wide, keyed by
   Target, and the Dashboard reads both. ADR-0005 §1 gains a one-line pointer here.
4. **`--target <slug>` on every command**; a Workspace with one Target needs none.
5. **A Trace can be imported.** An Importer turns an Evidence store the Connector reads (proxy
   rows, conversation records, a voice conversation) into a Trace in the ADR-0004 format, at
   the Fidelity the evidence supports (`reconstructed` for proxy rows, `observed` for a
   conversation record), with an `import/source` Event naming the evidence and what it lacks,
   Actor `operator` for staff-takeover Turns and `agentdiag.span.origin: imported` on every
   Span. An imported Trace is one Trial under a Scenario id the Importer derives from the
   evidence (the conversation id), in a Run of `source: imported` whose `run.json` records the
   Importer and the Connector query as its selection, the Manifest and Fingerprint as at
   import, no Simulated User and no Fixtures; it is judged like any other Run. `compare`
   refuses to aggregate a `run` Run with an `imported` one unless the comparison declares it.
   The same proxy-row Importer serves inside an HTTP Trial.
6. **What is lost is said**: an Importer marks every Span field the evidence cannot give (true
   end time, time to first token, a truncated body) with ADR-0006 §4's not-observed state or
   `truncated`, and lists them in the `import/source` Event, so Evals return `unverifiable`
   with the right reason rather than a guess.

## Considered options

- One `.agentdiag/` per Target repository, as ADR-0005 §1. Rejected for the reasons above; kept
  as the one-Target case.
- A central store under the user's home directory. Rejected again (ADR-0005): Suites and
  Change records belong in git with the Target's definition.
- An OTLP receiver instead of Importers. Deferred still (ADR-0001, ADR-0006 §5); the Importers
  read stores that exist today and produce the same Span model.

## Consequences

- `locate.run_directories(root)` and the CLI's root resolution widen; the Index's
  `target`, `source` and `change_record` columns exist already.
- `run.json.source` is `run` or `imported`; `compare` refuses to aggregate across the two
  unless declared.
- The Dashboard is a Workspace view; a Report stays per Run.
