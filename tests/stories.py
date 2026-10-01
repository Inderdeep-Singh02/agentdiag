"""What the Judge says over the committed cancel Trial, told once (phase-5 decision 43).

`cancel-processing-order` is the example's first Scenario and the README's first Run. The
Target looks NB-1042 up, cancels it because the customer asked and the order was
processing, and closes in `llm_call-3` by telling the customer "the charge will drop off
within a few days". `cancel_order` returned a refund amount and a timestamp and no timing,
so that window is the Target's own invention, which rule 3 of its prompt forbids. The live
Judge fails `prompt_adherence` on rule 3, citing `llm_call-3`, and it is right: the
captured answer carries the story, and every replay of the cancel Trial (`toy-cancel.jsonl`,
or the cancel Scenario out of `toy-orders.jsonl`) ends in that fail, so `run` exits 1 by
design. The authored pass over the same Trace is a worked fixture (`judge-pass.jsonl`).

The CLI tests import these rather than write the reason beside every exit code.
"""

from __future__ import annotations

CANCEL_EXIT = 1
"""What `run` exits with over the cancel Trial: its `prompt_adherence` Score is a fail."""

CANCEL_VERDICT = "fail"
"""The Verdict the Judge gives the cancel Trial's `prompt_adherence` Eval."""

CANCEL_COUNTS = "pass 0  fail 1"
"""How the summary's counts line opens for a Run of the cancel Trial alone."""

CANCEL_BROKEN_AT = "llm_call-3"
"""The Span the fail rests on, the closing reply that invents the refund window: whatever
else the Judge cites, it cites this."""
