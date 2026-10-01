---
status: accepted
date: 2026-09-22
---

# 0003 — Five Verdicts, closed reason codes, and Scores that cite their evidence

## Context

A production agent-maintenance repository studied during design needed PASS, FAIL,
INCOMPLETE, INSUFFICIENT_EVIDENCE, a per-check SKIP, NA and TEST ARTIFACT, and its renderer
drifted to 19 verdict strings. Across eight eval frameworks no two aggregate the not-a-pass
state the same way: Braintrust scores a crashed grader as 0, DeepEval defaults an
unmeasurable sub-score to 1.0. Only Inspect AI is coherent, through a
closed `ScoreReason` that splits Target-side from instrument-side failure. tau2-bench found a
task-critical simulator error in 12 to 13 percent of conversations and grades simulator errors
by direction. Nothing surveyed requires a
Score to cite the Spans that grounded it.

## Decision

1. Verdict is a closed set of five: `pass`, `fail`, `incomplete` (the Target execution did
   not finish; re-run), `unverifiable` (the Eval ran but the evidence to decide is missing,
   truncated, or below the Eval's required Fidelity), `invalid` (agentdiag itself broke the test).
   Enforced at the type level and in the schema. `CONTEXT.md` names the five; this ADR
   defines them.
2. Aggregation is a property of the Verdict, defined once. Only `pass` and `fail` enter a
   pass rate; the other three are always counted and enumerated in the Scorecard. No Eval
   chooses its own aggregation. A negated Eval never flips an abnormal Verdict into a pass.
3. Each abnormal Verdict has one closed carrier for its cause. `incomplete` carries a
   `reason` from (`timeout`, `transport_error`, `no_response`, `target_error`).
   `unverifiable` carries a `reason` from (`evidence_missing`, `evidence_truncated`,
   `fidelity_too_low`, `eval_not_applicable`). `invalid` carries `fault_source` from
   (`simulated_user`, `scenario`, `fixture`, `judge`, `agentdiag`) and `direction` from
   (`helped`, `hindered`, `none`): a Simulated User that leaks a hidden fact produces an
   untrustworthy pass, not an untrustworthy fail; a Judge that errors is `invalid` with
   `fault_source: judge`. A `fail` may carry an optional Target-side `reason` from
   (`refusal`, `malformed_output`) so that refusal rate is a first-class quantity.
4. A Score is: eval, verdict, optional value, threshold applied, direction of better,
   rationale, reason, evidence, source. `evidence` is a required list of Span references on
   every judged Score; a judged `pass` or `fail` with no evidence is recorded as
   `unverifiable` with reason `evidence_missing`. A cite that begins with a Span id —
   bracketed, or the whole line as the Trace is rendered to the Judge — is read as that id
   and the rationale says it was (added 2026-09-24, after the first live Judge call through
   Claude Code cited three rendered lines where the schema asked for ids). The Judge's
   structured-output schema constrains the shape of each cite to one bare Span id — a
   guarantee on the `anthropic_api` Backend, which constrains decoding, and an instruction
   plus a check on `claude_code`, which validates after the fact and retries once (ADR-0009)
   — and a cite that still begins with a Span id is read as it, and counted (added
   2026-09-24). `source` names the actual Judge model and prompt version, the mechanical
   Eval's code, or the human.
5. Partial credit is not a Verdict. A Score stores its value, the threshold applied and the
   derived Verdict together, so a stored Score stays interpretable when the threshold changes.
6. Metrics (duration, tokens, cost) are always recorded on Spans whether or not an Eval
   thresholds them. A latency Eval is a Metric plus a threshold.
7. The Judge's rendered prompt and raw output are kept beside the Score, so a disputed
   Verdict can be re-examined without re-running the Judge.
8. Per-Target Judge calibration (known false-fail patterns, as the reference repository's `AUDIT_NOTES.md`)
   lives in the Target's `.agentdiag/` beside the Manifest, is part of the Judge
   configuration's Fingerprint (ADR-0007), and every judged Score records that Fingerprint,
   so an edit to the notes is a visible variation in `compare`, not a hidden thumb on the
   scale.

## Considered options

- The reference repository's four, folding `invalid` into `incomplete`. Rejected: loses "fix the test"
  versus "re-run".
- Three (`pass`, `fail`, `error`). Rejected: loses "get better evidence" versus "agentdiag
  broke it".
- One `unmeasured` Verdict with reason codes. Rejected: the Scorecard must make the
  distinction impossible to ignore.
- Optional evidence. Rejected: Fidelity (ADR-0001) is meaningless if a Score can claim more
  than its evidence.

## Consequences

- The Judge must emit Span ids through structured output from Phase 4 onward.
- A Simulated User reviewer Eval runs by default on every `fail` from an adaptive Scenario,
  on a stronger model than the Simulated User, and produces the `invalid` Verdicts with
  `fault_source` and `direction`.
- Verdict vocabulary is data validated on write, so it cannot drift as the reference repository's did.
