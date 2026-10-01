"""The two files a push writes under the Target: the Restore point before the write and the
Push record after it (ADR-0011 §6b, §8; phase-7 decisions 13 and 17).

- A **Restore point**, `restore-points/<compact-ts>-<env>.json`: the deployed set exactly as
  the Connector read it for the push's preview, with that read's deployed Fingerprint.
  Written before the write, kept (every one, until retention is decided), gitignored, and
  never a Fingerprint: `push --restore <point>` pushes it back under the same rules.
- A **Push record**, `pushes/<compact-ts>-<env>.json`, committed: the environment, when,
  who, the effective side-effect class and how the push was confirmed, the sections
  written, the Target's Fingerprints before and after, the Restore point, the hash of the
  diff the preview showed, the Change record, and the Connector's receipt. A Change
  record's push event points at it.

Both are written as `render_json` writes a Run's files: indented, keys sorted. A second
file in the same second and environment takes `_002`, `_003`, as a Sync break does, so the
files sort oldest first under `sorted()`. Nothing edits or deletes either.

Offline: it reads and writes JSON.
"""

from __future__ import annotations

import getpass
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentdiag.change.record import AGENT_VARIABLE, UNKNOWN
from agentdiag.connector.base import DeployedSet, WriteReceipt
from agentdiag.run.directory import render_json
from agentdiag.sync.breaks import compact
from agentdiag.types import Confirmation, SideEffectClass
from agentdiag.workspace import TargetPaths

SCHEMA_VERSION = 1
SUFFIX = ".json"


class PushFileError(ValueError):
    """A Push record or a Restore point that cannot be read as one."""


class RestorePoint(BaseModel):
    """The deployed set as the Connector read it immediately before a push (CONTEXT.md
    **Restore point**)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    target: str
    environment: str
    saved_at: str
    fingerprint: str
    """The deployed Fingerprint of `deployed` (phase-7 decision 10): what the push that
    saved this point expected to find."""

    deployed: DeployedSet


class PushRecord(BaseModel):
    """One push, as committed under `pushes/` (CONTEXT.md **Push record**)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    pushed_at: str
    """When the write was made, ISO-8601 in UTC."""

    target: str
    """The Target's slug."""

    environment: str
    by: str
    """`$AGENTDIAG_AGENT` when a coding agent set it, else the operating-system user: the
    Change record's `opened_by` rule."""

    os_user: str
    """The operating-system user the push ran as, whoever `by` names (ADR-0011 §8)."""

    agent: str | None = None
    """The agent session that asked for the push, when `$AGENTDIAG_AGENT` named one."""

    effective_side_effects: SideEffectClass
    """The higher of the environment's class and the write operation's (ADR-0011 §6d)."""

    confirmed_by: Confirmation
    sections: list[str]
    """The section ids written, as the preview listed them."""

    fingerprint_before: str
    """The Target's Fingerprint in force before the push; `fingerprint_after`'s
    `pushed_from`."""

    fingerprint_after: str
    """The Fingerprint rebuilt from the Connector's read after the write; the receipt's
    deployed Fingerprint when that re-read failed (`problems` says so)."""

    restore_point: str
    """The Restore point saved before the write, relative to the Target directory."""

    diff_sha256: str
    """Of the preview's diffs, concatenated in section order: which bytes were confirmed."""

    change_record: str | None = None
    """The Change record the push names; None (`--change none`) on an unprotected
    environment."""

    receipt: WriteReceipt
    """What the Connector said it wrote, with the deployed Fingerprints on either side."""

    problems: list[str] = Field(default_factory=list)
    """What failed after the write (the re-read, the Fingerprint, the Change record), so the
    record of a write that happened is kept whatever came after it; empty when nothing did."""


def who() -> tuple[str, str, str | None]:
    """`(by, os_user, agent)`: who a push is recorded as."""
    agent = os.environ.get(AGENT_VARIABLE, "").strip() or None
    try:
        user = getpass.getuser()
    except (OSError, KeyError):
        user = UNKNOWN
    return agent or user, user, agent


def _next_path(directory: Path, at: str, environment: str) -> Path:
    base = f"{compact(at)}-{environment}"
    candidate, number = directory / f"{base}{SUFFIX}", 1
    while candidate.exists():
        number += 1
        candidate = directory / f"{base}_{number:03d}{SUFFIX}"
    return candidate


def _write_new(directory: Path, at: str, environment: str, text: str) -> Path:
    """Write `text` to the next free name, never over a file another push wrote."""
    directory.mkdir(parents=True, exist_ok=True)
    while True:
        path = _next_path(directory, at, environment)
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(text)
        except FileExistsError:
            continue
        return path


def restore_point_path(target: TargetPaths, at: str, environment: str) -> Path:
    """Where a Restore point saved at `at` for `environment` would be written now."""
    return _next_path(target.restore_points, at, environment)


def write_restore_point(target: TargetPaths, point: RestorePoint) -> Path:
    """`restore-points/<compact-ts>-<env>.json`, `render_json`; the path."""
    return _write_new(target.restore_points, point.saved_at, point.environment, render_json(point))


def load_restore_point(target: TargetPaths, reference: str) -> tuple[Path, RestorePoint]:
    """The Restore point `reference` names: a file name under the Target's
    `restore-points/`, with or without `.json`, or a path to one there (ADR-0011 §6b: a
    Restore point is the Workspace's, and nothing outside it is pushed back).
    `PushFileError` naming the directory and the points it holds otherwise."""
    directory = target.restore_points
    given = Path(reference)
    name = given.name if given.suffix == SUFFIX else f"{given.name}{SUFFIX}"
    path = directory / name
    inside = given.parent == Path(".") or _same(given.parent, directory)
    if not inside or not path.is_file():
        names = sorted(p.stem for p in directory.glob(f"*{SUFFIX}")) if directory.is_dir() else []
        where = target.relative_of(directory)
        why = "is not under" if not inside else "names nothing in"
        raise PushFileError(
            f"the Restore point {reference!r} {why} {where}, where Target {target.slug}'s "
            f"Restore points are (it holds: {', '.join(names) or 'none'})"
        )
    try:
        return path, RestorePoint.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise PushFileError(f"{path} is not a Restore point: {exc}") from exc


def _same(one: Path, other: Path) -> bool:
    try:
        return one.resolve() == other.resolve()
    except OSError:
        return False


def write_push_record(target: TargetPaths, record: PushRecord) -> Path:
    """`pushes/<compact-ts>-<env>.json`, `render_json`; the path."""
    return _write_new(target.pushes, record.pushed_at, record.environment, render_json(record))


def load_push_records(target: TargetPaths) -> list[tuple[Path, PushRecord]]:
    """Every Push record of the Target, oldest first; `PushFileError` naming a file that
    is not one."""
    directory = target.pushes
    if not directory.is_dir():
        return []
    return [(path, load_push_record(path)) for path in sorted(directory.glob(f"*{SUFFIX}"))]


def load_push_record(path: Path) -> PushRecord:
    """The Push record one file holds; `PushFileError` naming a file that is not one."""
    try:
        return PushRecord.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise PushFileError(f"{path} is not a Push record: {exc}") from exc


__all__ = [
    "PushFileError",
    "PushRecord",
    "RestorePoint",
    "load_push_record",
    "load_push_records",
    "load_restore_point",
    "restore_point_path",
    "who",
    "write_push_record",
    "write_restore_point",
]
