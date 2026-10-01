---
status: accepted
date: 2026-09-22
---

# 0008 — What happens when Sync is broken at the start of a Run

## Context

The product requirement landed after ADR-0007 was accepted: the goal is to drop agentdiag on
any agentic system, understand everything about that system, and be synced always before
running tests. ADR-0007 §2 decides that a broken
Sync flags and never invalidates, with refusing to run available behind an explicit strict
flag; ADR-0005 §9 allows a Run with no Manifest, recorded as Sync `not_checked`. Neither ADR
says what happens to the Manifest and Fingerprint when a break is found: the Run proceeds
against the stale Manifest, and the stale Manifest stays until someone rebuilds it. The
requirement asks for more than a label. It asks that the tool's understanding of the
Target be current when tests run.

## Options

a. Keep ADR-0007 as is: check, label `broken`, run against the stale Manifest. Satisfies
   "synced" only in the sense "checked".
b. Strict by default: refuse to run on `broken` until the author accepts the change or
   re-syncs. This is what ADR-0007 rejected, because it turns a Phase 6 promise into a
   Phase 4 obstacle.
c. Re-sync by default: on `broken`, rebuild the Fingerprint from the deployed set (and, once
   Phase 6 discovery exists, the Manifest), record `resynced_from: <old fingerprint>` in
   `run.json`, then run against the new one. `compare` still treats the change as an
   undeclared variation (ADR-0005 §6). `--no-resync` gives option a; `--strict` gives
   option b.

## Recommendation

Option c. It matches the requirement's literal words, leaves ADR-0007's "never invalidates prior
Runs" intact because old Runs keep their own Fingerprint, and leaves the zero-friction first
run of ADR-0005 §9 unchanged since `not_checked` only applies when there is no Manifest.
Cost: a re-sync must be cheap and observable. Until Phase 6, it covers only what the Adapter
can observe (ADR-0007 §1).

## Decision

Option c, accepted by the author on 2026-09-22. One
addition: when a Run re-syncs, `show` and the Report state up front which Fingerprint
sections changed, so a re-sync is never silent. ADR-0007 §2 gains a one-line pointer here and
is otherwise not edited.
