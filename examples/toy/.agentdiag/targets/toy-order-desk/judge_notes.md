<!--
Calibration notes for the Judge (ADR-0003 section 8). docs/judge-notes.md has the guidance.

What belongs here: known false-fail patterns, each as the Verdict a Judge gets wrong for
this Target and why; and conventions of this Target a Judge cannot learn from its prompt.
What does not: rules for the Target (those are its prompt, or a Suite's guardrails), and
anything that would excuse a failure the Trace shows.

Everything outside this comment reaches the Judge verbatim, in every judged Eval's prompt
and in the Diagnosis, and is part of every judged Score's Judge Fingerprint, so an edit
shows in a comparison. At most 600 words; a Run refuses more.

When the Target and the Judge run on the same model, say so here: a model judging its own
output tends to favour it, and agentdiag warns of that on every Score it touches.
-->
