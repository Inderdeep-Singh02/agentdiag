---
name: agentdiag-correction
description: Correct a Score the Judge or an Eval got wrong, through Calibration Notes, Eval parameters or a Suppression, and prove it with a rescore and compare.
disable-model-invocation: true
---

# Correct a wrong Score

A **correction** changes how a Target is judged, never the Target: the Score was wrong, the Trace was right. Its levers are three, each part of the judging configuration a `compare` diffs: the **Calibration Notes** (`judge_notes.md`, guidance every Judge prompt carries verbatim), the Manifest's **Eval parameters** (`eval_parameters`: latency thresholds, tool argument types; read by mechanical Evals, never shown to the Judge), and a **Suppression** (the Manifest's `suppressions`: a known false fail with an id and a date window). A Target that behaved wrongly is a fix, not a correction: use the `agentdiag-fix-cycle` skill. Every step is marked **code** (run it), **judgement** (decide it, and say why in a comment beside the edit) or **human** (hand it to the person and wait for their answer).

`agentdiag <command> --help` is the reference for every flag. Every command takes `--root <workspace> --target <slug>`; they are left out below.

## Budgets

- **Maintainer notes first.** Before step 1, read the Target's Maintainer notes, `maintainer_notes.md` beside the Manifest, in full: what it does, who it serves, its environments, its traps, where its evidence lives; with none yet, write them as you learn these.
- **Two evidence commands.** `agentdiag show <run> <scenario> --trial <n>` for the Score and its Trace, and one more at most (`agentdiag target show` for the notes and the Manifest as loaded).
- **One read bundle.** Read `judge_notes.md` and the Manifest's `eval_parameters` and `suppressions` once, in full, before you edit.
- **Diff first.** Before the rescore, `git diff` the one file you edited and read it.

## Steps

1. **judgement** — The wrong Score: `agentdiag show <run> <scenario> --trial <n>`. Quote the Score's Eval, Verdict and the sentence of its rationale that is wrong, and the Span (by id) whose content contradicts it. Done when both quotes are written down; with no contradicting Span the Score may be right, and the case is a fix (`agentdiag-fix-cycle`).
2. **judgement** — The lever, by the Eval and the cause:
   - a **judged** Eval misread the Target's situation every time it arises (a policy the Judge does not know, a same-model bias): a Calibration Notes paragraph saying what the Judge must know, in general terms, never naming this Trace;
   - a **mechanical** Eval's threshold or type is wrong for this Target: the `eval_parameters` entry it reads;
   - a false fail bound to dates (a known incident, a policy change in force from a day): a Suppression, `{id: sup-<slug>, eval: <eval name or *>, from: <date>, until: <date>, pattern: <what the Judge should disregard>, why: <one sentence>}`; the Judge names the id in its rationale when it applied one.
   Done when one lever is chosen and the reason is one sentence.
3. **human** — A Calibration Notes paragraph or a Suppression changes every later judgement of this Target: show the person the paragraph or entry and the Score it corrects, and ask whether it states the Target's owners' intent. Done when the person agrees, or rewrites it.
4. **judgement** — Make the edit in the one file, then `git diff -- <file>` (diff first). Done when the diff holds the one paragraph or entry and nothing else.
5. **code** — `agentdiag validate` then `agentdiag sync`: `validate` checks the Manifest (a Suppression whose `until` precedes its `from` is an error, one naming a mechanical Eval a warning) and prints `0 errors`; `sync` records the Manifest you edited. Done when both exit 0.
6. **code** — `agentdiag rescore <run>` (with `--scenario <id>` for the Trial of step 1, and `--eval <name>` when the Eval is one the Scenario did not declare). The Target is not run again: the same Traces, judged under the new configuration. Done when it prints the rescored Run's id.
7. **code** — `agentdiag compare <run> <rescored run> --expect judge` for Calibration Notes or a Suppression; add `--expect manifest.eval_parameters` and the `scenarios.<id>.evals.<eval>` path `compare` names for an Eval parameters edit. Done when the comparison's last line counts no `undeclared difference`.
8. **judgement** — Read the deltas: the step-1 Score moved to the Verdict the Trace supports, and no other Score moved. A Score that moved and should not have is the edit reaching too far: narrow it (step 4) and repeat from step 6. Done when the step-1 Score is corrected and every other delta is `unchanged`.
9. **code** — Commit the edited file and `fingerprint.json` together (diff first: `git diff --cached`).

## Done when

1. the rescored Run's Score for the step-1 Trial carries the Verdict the Trace supports, and every other Score is unchanged against the source Run;
2. `compare`'s last line counts no `undeclared difference` with the `--expect` paths of step 7;
3. the one edited file is committed, with the reason beside the edit.
