"""Calibration notes: what a Target's authors tell the Judge about judging it (ADR-0003 §8).

The reference repository kept per-agent judge calibration in `AUDIT_NOTES.md` — known
false-fail patterns, budgeted at 600 words and pasted verbatim into every judge prompt — and
the lesson was that it is genuinely necessary and genuinely a thumb on the scale. agentdiag
keeps both halves of that lesson:

- **The notes reach every Judge prompt verbatim**, in their own section of the prompt head
  (`agentdiag.eval.render`), so what the Judge was told is on record in `judgement.jsonl`.
- **They are part of the Judge Fingerprint on every judged Score** (`ScoreSource.
  judge_fingerprint`), so an edit to the notes changes every judged Score's Fingerprint and
  shows in `compare` rather than moving Verdicts silently.
- **The word budget keeps them from becoming a second prompt.** Over budget is a preflight
  problem, never a silent cut: a note the Judge never saw would be worse than one it did.

HTML comments (`<!-- ... -->`) are the author's, not the Judge's: the starter file `init`
writes carries the authoring guidance as one, and a Judge told "write your known false-fail
patterns here" would be reading instructions meant for a person. So the text the Judge sees
— and the text the budget counts and the Fingerprint hashes — is the file with its comments
removed and its ends trimmed; everything else is exactly as written. A file that holds only
comments is a Target with no notes yet, and the prompt says so.

This module reads a file and hashes text; it imports nothing that reaches the network.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pydantic import BaseModel

JUDGE_NOTES_MAX_WORDS = 600
"""The reference repository's budget for `AUDIT_NOTES.md`, kept."""

AUTHOR_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
"""An HTML comment: guidance for the author, never shown to the Judge."""


class JudgeNotesProblem(ValueError):
    """Notes the Manifest names that cannot reach the Judge: missing or over budget."""


class JudgeNotes(BaseModel):
    """The calibration notes one Run's Judge was given (ADR-0003 §8)."""

    path: str
    """As the Manifest names it, relative to the Target directory."""

    text: str
    """What the Judge reads: the file without its HTML comments, trimmed."""

    fingerprint: str
    """sha256 of `text`, so a notes edit is a visible change in a comparison."""

    @property
    def empty(self) -> bool:
        """Named but holding nothing for the Judge yet (the `init` starter, for one)."""
        return not self.text


def judge_text(raw: str) -> str:
    """The part of a notes file the Judge reads: comments removed, ends trimmed."""
    return AUTHOR_COMMENT.sub("", raw).strip()


def word_count(text: str) -> int:
    """Words as a reader counts them: runs of non-space characters."""
    return len(text.split())


def read_judge_notes(target_dir: Path, reference: str) -> JudgeNotes:
    """The notes the Manifest names, or `JudgeNotesProblem` saying why they cannot be used.
    `target_dir` is the Target directory the Manifest's paths are relative to."""
    path = Path(target_dir) / reference
    if not path.is_file():
        raise JudgeNotesProblem(
            f"The Manifest names judge_notes {reference!r}, and there is no file at {path}"
        )
    text = judge_text(path.read_text(encoding="utf-8"))
    words = word_count(text)
    if words > JUDGE_NOTES_MAX_WORDS:
        raise JudgeNotesProblem(
            f"The calibration notes at {path} run to {words} words; the budget is "
            f"{JUDGE_NOTES_MAX_WORDS}, so they are not a second prompt (ADR-0003 §8). "
            "Cut them rather than let the notes grow into a second prompt beside the Eval's own"
        )
    return JudgeNotes(
        path=reference,
        text=text,
        fingerprint=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


__all__ = [
    "AUTHOR_COMMENT",
    "JUDGE_NOTES_MAX_WORDS",
    "JudgeNotes",
    "JudgeNotesProblem",
    "judge_text",
    "read_judge_notes",
    "word_count",
]
