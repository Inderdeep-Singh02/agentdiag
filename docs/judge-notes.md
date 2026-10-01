# Calibration notes for the Judge

A Target's calibration notes are what its authors have learned about judging it: the
Verdicts a Judge gets wrong for this Target, and the conventions of this Target that a
Judge cannot learn from its prompt or its Trace. They live beside the Manifest, in
`.agentdiag/judge_notes.md` (the path is the Manifest's `judge_notes`), and `agentdiag
init` writes a starter file whose heading comment summarises this page (ADR-0003 §8).

## What reaches the Judge

Everything in the file outside HTML comments (`<!-- ... -->`), exactly as written, in its
own section of every judged Eval's prompt and of the Diagnosis:
`## Calibration notes for this Target`. Comments are for the author: the starter's guidance
is one, and a Judge never reads it. A file that holds only comments is a Target with no
notes yet, and the prompt says so.

The notes are part of the **Judge Fingerprint** on every judged Score
(`source.judge_fingerprint`), together with the prompt's version and text, the requested
model and the effort. An edit to the notes changes the Fingerprint of every judged Score it
touched, so a comparison of two Runs shows the judgement moved instead of implying the
Target did. `run.json` records the notes' text and their own sha256 under `judge.notes`.

## What belongs

- **Known false-fail patterns**, each as the Verdict a Judge gets wrong and why:
  "the cancel tool returns `refund_usd`; a reply that repeats the refund amount is quoting
  data, not inventing it."
- **Conventions of this Target** a Judge cannot infer: an abbreviation the Target is meant
  to use, a status the tools spell differently from the prompt, a field that is always
  empty in the test environment.
- **The self-preference note (D23).** When the Target and the Judge run on the same model,
  say so here. A model judging its own output tends to favour it; agentdiag records
  `shared_model: true` on every Score from such a Judge, and the Scorecard and `show` warn
  of it, but a reader of the notes should not have to find the warning to know.

## What does not belong

- **Rules for the Target.** Those are its prompt, or a Suite's `guardrails`. A rule in the
  notes would be judged against without ever being tested for.
- **Anything that excuses a failure the Trace shows.** The notes explain the Target; they
  never overrule the evidence. "Ignore invented delivery dates" is not calibration, it is a
  hidden thumb on the scale, and the Fingerprint makes it visible for exactly that reason.
- **The Scenario's own facts.** A Scenario's `notes`, `goal` and `ground_truth` reach the
  Judge for that Scenario alone; the calibration notes reach it for every Scenario.

## The budget

At most **600 words** (the budget a production agent-maintenance repository studied during design kept for its audit notes). A Run whose notes run
longer is refused before anything is written, and so is one whose Manifest names a notes
file that does not exist: a note the Judge never saw, or one it saw cut short, would be
worse than none. Six hundred words is room for a dozen patterns; notes longer than that are
usually a second prompt, and the Eval's own prompt is where the question it asks belongs.
