"""The Run directory: created once, written once, never edited (ADR-0005 §2).

Immutability is enforced here rather than promised in prose. Every writer refuses a path
that already exists, so a second `run` cannot land on top of a first, and a bug that tried
to rewrite `scorecard.json` raises instead of silently changing what a Run said. Re-scoring
makes a new Run (ADR-0005 §4); nothing here can make an old one say something else.

Finding a Trial's files inside a Run is `run.locate`: looking and writing change for
different reasons, and a reader should not have to import the writer to find a path.

The id is the creation time plus four random characters: it sorts chronologically by name,
and two Runs starting in the same second still get their own directory.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from agentdiag.run.locate import (
    RUN_RECORD_FILE,
    SCORECARD_FILE,
    SCORES_FILE,
    TRIALS_DIRNAME,
)
from agentdiag.workspace import TargetPaths

RUN_ID_FORMAT = "%Y%m%dT%H%M%SZ"
"""The creation time in a Run id. UTC, no separators: a directory name that sorts."""

SUFFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
"""Lowercase base32. No uppercase, so a case-insensitive filesystem cannot collide two ids."""

SUFFIX_LENGTH = 4

RUN_ID = re.compile(rf"^\d{{8}}T\d{{6}}Z-[{SUFFIX_ALPHABET}]{{{SUFFIX_LENGTH}}}$")
"""The grammar `new_run_id` writes: what a Run id given on a command line must match before
it is joined onto a `runs/` directory, so `../x` never names a path."""


def is_run_id(text: str) -> bool:
    return RUN_ID.fullmatch(text) is not None


class RunDirectoryExists(FileExistsError):
    """A Run directory or one of its files was already there. A Run is written once."""


def new_run_id(now: datetime) -> str:
    """A fresh Run id for a Run created at `now` (ADR-0005 §2, D29)."""
    suffix = "".join(secrets.choice(SUFFIX_ALPHABET) for _ in range(SUFFIX_LENGTH))
    return f"{now.strftime(RUN_ID_FORMAT)}-{suffix}"


def render_json(model: BaseModel) -> str:
    """The one JSON spelling every Run file uses: indented, sorted, newline-terminated.

    Sorted keys and a trailing newline make a Run directory diffable and greppable, which
    is the reason the files are the truth and the index is derived (ADR-0005 §5).
    """
    return json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


class RunDirectory:
    """One Run's directory, and the only thing that writes into it."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @classmethod
    def create(cls, target: TargetPaths, run_id: str) -> RunDirectory:
        """Make the Target's `runs/<run_id>/`, refusing to reuse one that exists."""
        path = target.runs / run_id
        if path.exists():
            raise RunDirectoryExists(f"{path} already exists; a Run is never overwritten")
        path.mkdir(parents=True)
        return cls(path)

    def trial_dir(self, scenario_id: str, trial: int) -> Path:
        """Where one Trial's Trace, Scores and judgement live (ADR-0005 §2)."""
        return self.path / TRIALS_DIRNAME / scenario_id / str(trial)

    # --- the files (ADR-0005 §2) ---

    def write_run_record(self, record: BaseModel) -> Path:
        return self._write(self.path / RUN_RECORD_FILE, record)

    def write_scores(self, scenario_id: str, trial: int, scores: BaseModel) -> Path:
        return self._write(self.trial_dir(scenario_id, trial) / SCORES_FILE, scores)

    def write_scorecard(self, scorecard: BaseModel) -> Path:
        return self._write(self.path / SCORECARD_FILE, scorecard)

    def _write(self, path: Path, model: BaseModel) -> Path:
        if path.exists():
            raise RunDirectoryExists(f"{path} already exists; a Run is written once")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_json(model), encoding="utf-8")
        return path


__all__ = [
    "RUN_ID",
    "RUN_ID_FORMAT",
    "SUFFIX_ALPHABET",
    "SUFFIX_LENGTH",
    "TRIALS_DIRNAME",
    "RunDirectory",
    "RunDirectoryExists",
    "is_run_id",
    "new_run_id",
    "render_json",
]
