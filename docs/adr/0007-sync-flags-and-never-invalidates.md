---
status: accepted
date: 2026-09-22
---

# 0007 — A Fingerprint covers what the Adapter can observe of the deployed set, and a Sync break flags but never invalidates

## Context

A production agent-maintenance repository studied during design hashed only the prompt files
that ship to the model, by heading section, in the repository rather than in the deployment;
tool schemas, flows and data sources were not covered, and a live-config drift check existed
separately and never met the file hashes. Its rule for orphaned graph nodes was
"reviewed, never auto-deleted", because a rule that moved looks identical to a rule that was
removed. No eval framework surveyed has a Sync concept; the closest is lm-evaluation-harness
hashing the system prompt and chat template into every Run.

## Decision

1. **Coverage.** A Fingerprint covers everything in the Manifest's deployed set that the
   Adapter can observe: prompt text hashed by section, tool schemas, the identity of retrieval
   and data sources, and the resolved model and provider. Sections are hashed separately so a
   break names what changed. What the Adapter cannot observe is recorded as not covered, never
   silently omitted. (ADR-0011 widens coverage to what the Adapter or the Connector can observe,
   and Sync to a three-state comparison per section.)
2. **A broken Sync never invalidates prior Runs.** It labels them. `compare` treats a
   Fingerprint change as an undeclared variation (ADR-0005 §6) and claims no regression across
   it. Refusing to run on a break is available behind an explicit strict flag, not the default.
   What a Run does at the moment it finds a break is decided in ADR-0008 (re-sync by default).
3. The Simulated User and Judge configurations, including per-Target calibration notes
   (ADR-0003 §8), carry their own Fingerprints, recorded per Run and diffed by `compare`. Sync
   itself remains a claim about the Target.

## Considered options

- Hash repository files only, as the reference repository did. Rejected: a Target whose deployed tool
  arguments changed showed a clean diff.
- Auto-invalidate prior Runs on a break. Rejected: the reference repository's "never guess" rule; a moved
  section and a removed one look the same.
- Refuse to run on a break by default. Rejected: it turns a Phase 6 promise into a Phase 4
  obstacle; the Run already records `held`, `broken` or `not_checked`.
