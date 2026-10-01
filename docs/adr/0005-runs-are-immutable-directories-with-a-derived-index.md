---
status: accepted
date: 2026-09-22
---

# 0005 — Runs are immutable directories beside the Target, with a derived index, and `compare` leads with the config diff

## Context

Phase 4 needs a local, append-only Run store; Phase 5 needs `compare <run> <run>`; Phase 7
needs something a local web UI can query. Across the frameworks surveyed: four independent designs make one
file the Run; re-scoring a stored Run without re-running the Target is the majority position;
promptfoo's SQLite schema is the only local tool with a Trace-to-Run join; no tool detects
that the Target's configuration differed between two compared Runs; none enumerates skipped
Scenarios with reasons; none selects by tag. deepseek-harness never overwrites a recording
and splits "re-record against the model" from "re-derive from a recording".
The report generator of a production agent-maintenance repository studied during design
drifted because "each script grew its own HTML". The author's
requirements: complete per-test transparency, and a first run with no setup.

## Decision

1. **Location.** A Target's agentdiag material lives in `.agentdiag/` at the Target's
   repository root (configurable root for Targets without a repo): the Manifest, its
   Fingerprint and the Suites are committed; `runs/` is gitignored by default. (ADR-0013
   makes the root a Workspace of many Targets under `targets/<slug>/`, chosen by the user; the
   one-Target root is its degenerate case.)
2. **One immutable directory per Run**, named by creation time and never overwritten:
   `run.json`, `trials/<scenario_id>/<n>/trace.jsonl`, `judgement.jsonl` and `scores.json`
   per Trial, and `scorecard.json`. A Run is interpretable with no access to the files that
   produced it. (An imported Trace is one Trial under a Scenario id the Importer derives,
   ADR-0013 §5.)
3. **`run.json` freezes the configuration mechanically**: a snapshot of the Manifest, the
   Fingerprint observed and the Sync result (`held`, `broken`, `not_checked`; ADR-0007), the Adapter
   configuration including environment and side-effect class, the Simulated User and Judge
   configurations with full prompt text and their own Fingerprints, the price-table version,
   `trials`, the selection expression, git state of both agentdiag and the Target including
   a dirty flag, the agentdiag version and packages, and every Scenario not run with its
   reason.
4. **Re-scoring creates a new Run.** `rescore` writes a new Run directory whose `run.json`
   carries `traces_from: <run id>` and reuses the source Traces. No Run is ever mutated.
5. **A derived SQLite index** at `.agentdiag/index.sqlite` holds runs, trials, spans and
   scores for listing, selection, `compare` and the UI. It can be deleted and rebuilt from the
   files at any time. The files are the truth.
6. **`compare <baseline> <run>` leads with the configuration diff**, field by field, across
   Target, Adapter, Simulated User and Judge Fingerprints. The caller declares what is expected
   to vary; any undeclared difference is labelled and no regression is claimed across it.
   Scores are then joined per Scenario id over the intersection of the two Runs' Scenario
   sets, aggregated over Trials, and shared Traces are flagged. Scenarios present in only one
   Run are counted and named; no aggregate is shown over mismatched sets.
7. **Trials.** `trials` is a Run parameter, default 1. Raw per-Trial Scores are kept. The
   Scorecard reports the mean and pass^k for every k up to `trials`, with the trial count and
   the Simulated User temperature beside them, since pass^k at temperature 1.0 and at 0.0
   measure different things.
8. **The Scorecard enumerates what did not happen**: skipped Scenarios with reasons, counts
   of `incomplete`, `unverifiable` and `invalid` Verdicts, and the Sync status. A pass rate
   is never shown without them.
9. **No Manifest is required to run.** `agentdiag run` needs an Adapter spec and one Scenario
   file. The Run records what it could fingerprint and marks Sync `not_checked`.
   `agentdiag init` scaffolds `.agentdiag/` with a sample Scenario and a Manifest skeleton
   (since ADR-0013, as a Workspace with one Target).
10. **No human-driven Trials.** agentdiag automates. The Simulated User interface allows a
    human driver as an implementation later; such Runs would be a labelled Run type excluded
    from `compare`. Not built until a phase needs it. (An agentic Simulated User
    is automated and fingerprinted, not a human driver; an imported Run, ADR-0013 §5, is a
    labelled Run type of a conversation agentdiag did not drive.)
11. **One renderer.** `show` renders a Trial in the terminal; every Run writes one
    self-contained HTML Report from the same files; the Phase 7 UI reads the same files and
    index. Nothing hand-builds a second report path.

## Considered options

- SQLite as the source of truth. Rejected: append-only files are diffable, greppable and
  survive a crashed writer; the index is cheap to rebuild.
- Files only, no index. Rejected: `compare` and the UI would rescan every Run.
- A central store under the user's home directory. Rejected: Scenarios belong with the code
  they test and should travel with it.
- Everything in this repository, as the reference repository keeps `agents/<slug>/`. Rejected: works only
  when one team owns every Target.
- Mutable Runs with an edit history, as Inspect AI does. Rejected: makes `compare run1 run2`
  ambiguous about which version is meant.
- Refuse to compare when configuration differs. Rejected: "prompt A vs prompt B" is the main
  use of comparison, and the config is supposed to differ.

## Consequences

- Re-judging multiplies Run directories. Accepted; disk is cheaper than ambiguity.
- Retention (live window, archive bundles) is deferred until volume demands it; the reference
  repository's concepts are the starting point.
- Tag-based selection is ours to build; nothing surveyed has it.
