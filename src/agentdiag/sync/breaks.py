"""Sync breaks: the recorded findings that a Target moved against its last Fingerprint.

A **Sync break** (CONTEXT.md) names the sections whose directions broke Sync and against
which Fingerprint; it is a file, `targets/<slug>/sync-breaks/<opened, compact>.json`,
committed beside the Fingerprint it was found against (phase-6 decision 26). `agentdiag sync`
and `sync --check` record one whenever the comparison is `broken`; ticket 32's `watch` will
too. **A Run alone never does**: a Run re-syncs, or runs labelled, and says so in its own
`run.json` (ADR-0008), which is the Run's record, not the Target's.

**Nothing edits or deletes a break file.** A break is closed by what came after it, not by
rewriting it: a break is *open* exactly while the Fingerprint it was found against is the one
in force (its `fingerprint` equals the current `fingerprint.json`'s id). A rebuild that moves
the id — `sync`, a re-syncing Run — supersedes every break found against the old one; a
rebuild to the same id (every broken section `local_ahead`: the Fingerprint records the
deployed side, which never moved, and only a push would) leaves the break open, as it should.
No timestamp decides it. A comparison that repeats an open break (the same sections, the
same directions) records nothing new: the break is already there.

File names are the opening moment, compact, then `_002`, `_003` for a second or third break
in the same second, so `sorted()` of the names is oldest first (`_` sorts after `.`).

Offline: it reads and writes JSON.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from agentdiag.sync.compare import SectionState, SyncResult
from agentdiag.sync.fingerprint import Fingerprint
from agentdiag.types import CoveredBy, SyncBreakOpener
from agentdiag.workspace import TargetPaths


class SyncBreakError(ValueError):
    """A file under `sync-breaks/` that cannot be read as a Sync break."""


class SyncBreak(BaseModel):
    """One recorded Sync break (decision 26)."""

    opened_at: str
    """ISO-8601 in UTC, `run.json`'s spelling."""

    environment: str | None = None
    fingerprint: str | None = None
    """The id of the Fingerprint the comparison was against."""

    sections: list[SectionState] = Field(default_factory=list)
    """The broken sections: each `local_ahead`, `deployed_ahead` or `diverged`."""

    opened_by: SyncBreakOpener
    covered_by: CoveredBy | None = None
    """Which source read the deployed side for this comparison."""


def compact(opened_at: str) -> str:
    """`2026-09-27T10:00:00Z` as a file name stem, `20260927T100000Z`."""
    return opened_at.replace("-", "").replace(":", "")


def render_break(sync_break: SyncBreak) -> str:
    """The one spelling of a break file: indented, keys sorted, newline-terminated."""
    return json.dumps(sync_break.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class Recorded:
    """What `record_break` did: the file written, or the open break it repeated."""

    path: Path
    already_open: bool = False


def record_break(
    target: TargetPaths,
    result: SyncResult,
    *,
    opened_at: str,
    opened_by: SyncBreakOpener = "sync",
) -> Recorded | None:
    """Record a Sync break for `result` when it is `broken`; None when it is not.

    When an open break (found against the same Fingerprint) already names the same sections
    with the same directions, nothing is written and that break is returned
    `already_open`. Otherwise the file is created exclusively and never replaced."""
    if result.status != "broken":
        return None
    sync_break = SyncBreak(
        opened_at=opened_at,
        environment=result.environment,
        fingerprint=result.fingerprint,
        sections=result.broken,
        opened_by=opened_by,
        covered_by=result.covered_by,
    )
    for path, existing in load_breaks(target):
        if existing.fingerprint == sync_break.fingerprint and _found(existing) == _found(
            sync_break
        ):
            return Recorded(path=path, already_open=True)
    directory = target.sync_breaks
    directory.mkdir(parents=True, exist_ok=True)
    stem, number = compact(opened_at), 1
    while True:
        suffix = "" if number == 1 else f"_{number:03d}"
        path = directory / f"{stem}{suffix}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(render_break(sync_break))
            return Recorded(path=path)
        except FileExistsError:
            number += 1


def _found(sync_break: SyncBreak) -> set[tuple[str, str]]:
    return {(state.id, state.direction) for state in sync_break.sections}


def load_breaks(target: TargetPaths) -> list[tuple[Path, SyncBreak]]:
    """Every recorded break of the Target with its file, oldest first by name;
    `SyncBreakError` naming a file that is not one."""
    directory = target.sync_breaks
    if not directory.is_dir():
        return []
    found: list[tuple[Path, SyncBreak]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            found.append((path, SyncBreak.model_validate_json(path.read_text(encoding="utf-8"))))
        except (OSError, ValidationError, ValueError) as exc:
            raise SyncBreakError(f"{path} is not a Sync break: {exc}") from exc
    return found


def is_open(sync_break: SyncBreak, fingerprint: Fingerprint | None) -> bool:
    """Found against the Fingerprint now in force; with none in force, nothing is open."""
    return fingerprint is not None and sync_break.fingerprint == fingerprint.id


def open_breaks(
    breaks: Sequence[tuple[Path, SyncBreak]], fingerprint: Fingerprint | None
) -> list[tuple[Path, SyncBreak]]:
    """The breaks found against the current Fingerprint, oldest first."""
    return [(path, each) for path, each in breaks if is_open(each, fingerprint)]


__all__ = [
    "Recorded",
    "SyncBreak",
    "SyncBreakError",
    "compact",
    "is_open",
    "load_breaks",
    "open_breaks",
    "record_break",
    "render_break",
]
