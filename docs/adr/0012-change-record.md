---
status: accepted
date: 2026-09-25
---

# 0012 — A Change record carries the fix loop: trigger, Diagnosis, change, push, expected effect, verification

## Context

The kit must show "this is what happened, here was the problem, here is the fix, so now we
can expect so and so to happen", and must fix the agent's prompt files or tools. A production
agent-maintenance repository studied during design records every cycle as a `HISTORY.md`
entry under a grammar set by one of its own ADRs (symptom and evidence, root cause, what
changed, how verified, post-fix observation, follow-ups; status from `open | fixed-unverified
| verified | wontfix | superseded`; a validator refuses `verified` with an empty
observation), 146 entries for one agent alone, all Claude-driven with no code applying a fix.
Its gaps: no status for
"deployed but refuted", the deploy's who and when only in prose, commits not linked, customer
data in tracked entries with an unused `redact()`.

agentdiag has a Diagnosis, "the Judge's narrative for one Trial ... never a Score itself", which
avoids "root cause" and "post-mortem" on purpose, and no term for a complaint, a fix, a deploy or
a record. Without one, the fix and its verification live outside the tool, and the Flow view
has nothing to render.

## Decision

1. **A Change record is one file per change under the Target** (`targets/<slug>/changes/
   <id>.md` with a YAML head), committed, with an id scoped to the Target and a date.
2. **Lifecycle is a closed set**: `open` → `proposed` → `pushed` → `verified` | `refuted` |
   `wontfix` | `superseded`. `refuted` exists so a change that was pushed and did not fix the
   problem is not left at "unverified". On a Target with no Connector write (an in-process or
   repository Target, changed by editing its files) the record moves to `pushed` when the next
   Run re-syncs onto the edited files; its push event is then of kind `local` and points at the
   Run, not at a Push record.
3. **Fields**: the trigger (a redacted complaint, or the Diagnosis with the Run and Trial ids
   that found it); the linked Diagnosis and the layer it names (`persona`, `rules`, `memory`,
   `checklist`, `flow`, `runtime`, `data`); the change set (files or sections, the git SHA, flow
   ids); push events, each pointing at its Push record (ADR-0011 §8: environment, when, who,
   Fingerprints before and after, Restore point) or of kind `local`; the **expected effect,
   stated before the verifying Run** (which Scenarios should move and which must not), with its
   timestamp, so a `close` can refuse an expectation written after the verifying Run started;
   verification (Run ids and the `compare` result); the post-change observation as cited
   Scores.
4. **Gates.** `validate` checks the file. `verified` requires a cited post-change Run whose
   `compare` against the pre-change Run shows the expected movement and no undeclared
   variation; `refuted` requires the same `compare` showing it did not. Redaction runs on write:
   customer identifiers never enter a committed file.
5. **Relations.** Every Connector push cites a Change record or `none` (ADR-0011 §6e). The
   Diagnosis stays what it is: it explains Scores and proposes nothing. A Change record links
   to it; nothing is written into a Run. The Flow view is a rendering of a Change
   record, never a store.
6. **A history kept in another shape is not imported.** agentdiag ships no converter; a
   reference repository's `HISTORY.md` entry becomes a Change record by hand, with
   `fixed-unverified` mapped to `pushed`.

## Considered options

- Extend Diagnosis with a fix. Rejected: a Diagnosis belongs to one Trial in one immutable
  Run; a change spans Runs and is mutable until closed.
- A Change record as a kind of Score, as the human-override Score is. Rejected: it has a
  lifecycle and a push, which no Score has.
- Free-form history as the reference repository keeps it. Rejected: the `verified` gate and the Flow view
  both need typed fields.

## Consequences

- The Index gains a `change_record` column on Runs (already reserved) and a table
  for Change records for the Dashboard, rebuilt from the Change record files as the Run rows
  are from the Run directories.
- Suite generation gains a source: a Change record's trigger and expected effect become
  a Scenario.
- A Change record may reference Trials from an imported Trace (ADR-0013), which is how the
  reference repository's cycles start.
