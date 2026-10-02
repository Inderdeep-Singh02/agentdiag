"""The Registry: a Workspace's Targets, derived from their Manifests and never authored.

ADR-0013 §3: the list of Targets with their Family and `channel`, environments and Suites
is a projection of the Manifests (phase-6 decision 5). Nothing here writes a file, because
a Registry on disk would be a second copy of what the Manifests say, and the day the two
disagreed nobody could tell which was right. `agentdiag registry` prints it; `agentdiag
target show <slug>` prints one Target in full; the Dashboard (ticket 29) reads the same
function.

**A Manifest that does not load is still an entry.** The Registry is where a broken Target
should be noticed, so its row carries the load error under `problems` instead of the whole
listing failing on one file.

**Sync is read from files, never checked here** (phase-6 decision 26). `sync` is a
`SyncSummary` derived from the Fingerprint `agentdiag sync` wrote and the Sync breaks it
recorded: `not_checked` with no Fingerprint, `broken` while any break is open (found against
the Fingerprint now in force, `sync.breaks`), `held` otherwise. A comparison would need a
Connector read or a probe, and listing a Workspace must stay offline and cheap; a held
`sync --check` writes nothing, so `sync`, which re-records the Fingerprint, is what settles
a break. `target show` lists the open breaks with that hint, then the Change records still
open (`open`, `proposed`, `pushed`), `none` rather than left out when there are none; the
view carries every record, closed ones included (ADR-0012, phase-7 decision 5).

Offline: it reads YAML and Markdown, and imports nothing that reaches a model.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import BaseModel, Field

from agentdiag.change.record import is_closed, load_records, unreadable_records
from agentdiag.eval.notes import JudgeNotesProblem, author_text, read_judge_notes, word_count
from agentdiag.run.manifest import (
    DEFAULT_ENVIRONMENT_KEY,
    MAINTAINER_NOTES_WHAT,
    Manifest,
    ManifestError,
    ManifestNotFound,
    SuiteEntry,
    load_manifest,
    pointer_refusal,
)
from agentdiag.run.templates import MAINTAINER_NOTES_STARTER
from agentdiag.sync.breaks import SyncBreak, SyncBreakError, load_breaks, open_breaks
from agentdiag.sync.fingerprint import (
    NONE_SHOWN,
    Fingerprint,
    FingerprintError,
    load_fingerprint,
    short,
)
from agentdiag.sync.pushes import PushFileError, PushRecord, load_push_records
from agentdiag.table import render_table
from agentdiag.types import ChangeStatus, SyncStatus
from agentdiag.workspace import TargetPaths, Workspace


class SyncSummary(BaseModel):
    """A Target's Sync state as the Registry shows it (phase-6 decision 26): the status of
    the last check as the files record it, how many Sync breaks are open, and the current
    Fingerprint's id and when it was built."""

    status: SyncStatus
    open_breaks: int = 0
    fingerprint: str | None = None
    built_at: str | None = None
    environment: str | None = None
    """The environment the Fingerprint was recorded against: what `status` is the state
    of, since a Target has one Fingerprint (ticket 41). None with no Fingerprint."""

    last_push: str | None = None
    """When the Target's last Push record says it was pushed (phase-7 decision 17); None
    when nothing was ever pushed through the Connector."""


class RegistryEntry(BaseModel):
    """One Target of the Workspace, as its Manifest describes it (decision 5)."""

    slug: str
    name: str | None = None
    """The Manifest's `target.name`; None when the Manifest did not load."""

    family: str | None = None
    channel: str | None = None
    environments: list[str] = Field(default_factory=list)
    """The Adapter's environments, the default first."""

    protected: list[str] = Field(default_factory=list)
    """The protected environments (`Manifest.is_protected`): the ones marked `protected:
    true`, and `prod`, `staging` and `eu_prod` unless their block says otherwise."""

    connector: str | None = None
    """The Manifest's `connector.kind`, when it names a Connector (ticket 23)."""

    suites: list[SuiteEntry] = Field(default_factory=list)
    sync: SyncSummary | None = None
    problems: list[str] = Field(default_factory=list)
    """Why this entry is incomplete: a Manifest that did not load, a default environment
    it does not name."""

    maintainer_notes: str | None = None
    """The Manifest's `maintainer_notes` pointer as written (ADR-0016 §5); None when the key
    is absent. The Orientation page's Targets table lists it; the terminal table does not
    (0.1.2-interfaces decision 5)."""


class CalibrationNotesSummary(BaseModel):
    """The Calibration Notes as `target show` reports them: what the Judge would read."""

    path: str
    words: int
    fingerprint: str


class MaintainerNotesSummary(BaseModel):
    """The Maintainer notes as `target show` reports them (ADR-0016 §5): where they are and
    how long, counted outside the HTML comment as the Calibration Notes are."""

    path: str
    """As the Manifest names it, relative to the Target directory."""

    words: int

    written: bool = True
    """False while the text outside the HTML comment is still the starter's (its headings and
    placeholders): `target show` says `not written yet` rather than count them."""


class OpenSyncBreak(BaseModel):
    """One open Sync break as `target show` lists it: its file and what it recorded."""

    path: str
    """The break file, relative to the Workspace root."""

    sync_break: SyncBreak


class ChangeRecordSummary(BaseModel):
    """One Change record as `target show` lists it (phase-7 decision 5)."""

    id: str
    status: ChangeStatus
    title: str
    opened_at: str

    @property
    def is_open(self) -> bool:
        return not is_closed(self.status)


SHOWN_PUSHES = 3
"""How many Push records `target show` lists."""


class PushSummary(BaseModel):
    """One Push record as `target show` lists it."""

    path: str
    """The record's file, relative to the Workspace root."""

    push: PushRecord


class TargetView(BaseModel):
    """One Target in full, as `agentdiag target show` prints it (decision 5)."""

    entry: RegistryEntry
    directory: str
    """The Target directory, relative to the Workspace root."""

    manifest: dict[str, Any] | None = None
    """The Manifest as loaded; None when it did not load (the entry says why)."""

    calibration_notes: CalibrationNotesSummary | None = None
    maintainer_notes: MaintainerNotesSummary | None = None
    """The Maintainer notes the Manifest names; None when it names none or the file is
    missing (the entry's problems say which)."""

    fingerprint: Fingerprint | None = None
    """The Target's `fingerprint.json` (phase-6 decision 9); None until `agentdiag sync`
    or a re-syncing Run writes one."""

    sync_breaks: list[OpenSyncBreak] = Field(default_factory=list)
    """The open Sync breaks, oldest first (decision 26)."""

    change_records: list[ChangeRecordSummary] = Field(default_factory=list)
    """Every Change record of the Target, by id (ADR-0012); `target show` prints the open
    ones, and a file that does not load is a problem of the entry."""

    pushes: list[PushSummary] = Field(default_factory=list)
    """The last `SHOWN_PUSHES` Push records, newest first (phase-7 decision 17)."""


def registry(workspace: Workspace, target: TargetPaths | None = None) -> list[RegistryEntry]:
    """Every Target of the Workspace, by slug, each as its Manifest describes it; only
    `target`'s entry when one is named (`registry --target`)."""
    targets = [target] if target is not None else workspace.targets()
    return [_with_sync(each, _entry(each)[0])[0] for each in targets]


def _with_sync(
    target: TargetPaths, entry: RegistryEntry
) -> tuple[
    RegistryEntry, Fingerprint | None, list[tuple[Path, SyncBreak]], list[tuple[Path, PushRecord]]
]:
    """The entry with its `SyncSummary`, and the Fingerprint, open breaks and Push records
    it came from; a file among them that does not read is one of the entry's problems."""
    fingerprint: Fingerprint | None = None
    try:
        fingerprint = load_fingerprint(target)
    except FingerprintError as exc:
        entry.problems.append(str(exc))
    try:
        still_open = open_breaks(load_breaks(target), fingerprint)
    except SyncBreakError as exc:
        entry.problems.append(str(exc))
        still_open = []
    status: SyncStatus = (
        "not_checked" if fingerprint is None else "broken" if still_open else "held"
    )
    pushes = _pushes(target, entry)
    entry.sync = SyncSummary(
        status=status,
        open_breaks=len(still_open),
        fingerprint=fingerprint.id if fingerprint is not None else None,
        built_at=fingerprint.built_at if fingerprint is not None else None,
        environment=fingerprint.environment if fingerprint is not None else None,
        last_push=pushes[-1][1].pushed_at if pushes else None,
    )
    return entry, fingerprint, still_open, pushes


def _pushes(target: TargetPaths, entry: RegistryEntry) -> list[tuple[Path, PushRecord]]:
    try:
        return load_push_records(target)
    except PushFileError as exc:
        entry.problems.append(str(exc))
        return []


def target_view(workspace: Workspace, slug: str | None) -> TargetView:
    """One Target in full: the one `slug` names, or the Workspace's one Target when it names
    none, as `Workspace.resolve` has it. `TargetNotFound` or `TargetAmbiguous` naming the
    slugs there are."""
    target = workspace.resolve(slug)
    entry, manifest = _entry(target)
    calibration_notes = _calibration_notes(target, manifest, entry)
    maintainer_notes = _maintainer_notes(target, manifest, entry)
    entry, fingerprint, still_open, pushes = _with_sync(target, entry)
    change_records = _change_records(target, entry)
    return TargetView(
        entry=entry,
        directory=target.relative_of(target.directory),
        manifest=manifest.model_dump(mode="json") if manifest is not None else None,
        calibration_notes=calibration_notes,
        maintainer_notes=maintainer_notes,
        fingerprint=fingerprint,
        sync_breaks=[
            OpenSyncBreak(path=target.relative_of(path), sync_break=each)
            for path, each in still_open
        ],
        change_records=change_records,
        pushes=[
            PushSummary(path=target.relative_of(path), push=each) for path, each in reversed(pushes)
        ][:SHOWN_PUSHES],
    )


def _calibration_notes(
    target: TargetPaths, manifest: Manifest | None, entry: RegistryEntry
) -> CalibrationNotesSummary | None:
    """The Calibration Notes the Manifest names, as the Judge would read them; notes that
    cannot reach the Judge (missing, over budget) are a problem of the entry."""
    if manifest is None or manifest.judge_notes is None:
        return None
    try:
        read = read_judge_notes(target.directory, manifest.judge_notes)
    except (JudgeNotesProblem, OSError) as exc:
        entry.problems.append(str(exc))
        return None
    return CalibrationNotesSummary(
        path=read.path, words=word_count(read.text), fingerprint=read.fingerprint
    )


def _maintainer_notes(
    target: TargetPaths, manifest: Manifest | None, entry: RegistryEntry
) -> MaintainerNotesSummary | None:
    """The Maintainer notes the Manifest names, counted (ADR-0016 §5). The pointer rule of
    ADR-0015 §2 holds here as in `validate`: a pointer that is absolute or leaves the root is
    a problem of the entry and its file is never read; a named file that is not there is a
    problem too, in `validate`'s words. Read here and never by `agentdiag.eval`."""
    if manifest is None or manifest.maintainer_notes is None:
        return None
    pointer = manifest.maintainer_notes
    refusal = pointer_refusal(target, MAINTAINER_NOTES_WHAT, pointer)
    if refusal is not None:
        entry.problems.append(f"maintainer_notes: {refusal}")
        return None
    path = target.relative(pointer)
    try:
        text = author_text(path.read_text(encoding="utf-8"))
    except OSError:
        entry.problems.append(
            f"maintainer_notes: {MAINTAINER_NOTES_WHAT} {pointer} does not exist under "
            f"{target.directory}"
        )
        return None
    return MaintainerNotesSummary(
        path=pointer,
        words=word_count(text),
        written=text != author_text(MAINTAINER_NOTES_STARTER),
    )


def _change_records(target: TargetPaths, entry: RegistryEntry) -> list[ChangeRecordSummary]:
    entry.problems += [str(invalid) for invalid in unreadable_records(target)]
    return [
        ChangeRecordSummary(
            id=record.id, status=record.status, title=record.title, opened_at=record.opened_at
        )
        for _, record in load_records(target)
    ]


def _entry(target: TargetPaths) -> tuple[RegistryEntry, Manifest | None]:
    try:
        manifest = load_manifest(target)
    except (ManifestNotFound, ManifestError) as exc:
        return RegistryEntry(slug=target.slug, problems=[str(exc)]), None
    problems: list[str] = []
    names = [name for name in manifest.adapter.environments if name != DEFAULT_ENVIRONMENT_KEY]
    try:
        default = manifest.adapter.default_environment
    except ManifestError as exc:
        problems.append(str(exc))
    else:
        names = [default, *(name for name in names if name != default)]
    return (
        RegistryEntry(
            slug=target.slug,
            name=manifest.target.name,
            family=manifest.family,
            channel=manifest.channel,
            environments=names,
            protected=[name for name in names if manifest.is_protected(name)],
            connector=manifest.connector_kind,
            suites=list(manifest.suites),
            problems=problems,
            maintainer_notes=manifest.maintainer_notes,
        ),
        manifest,
    )


# --- the terminal ---

COLUMNS = ("target", "name", "family", "channel", "environments", "connector", "suites", "sync")


def render_registry(entries: Sequence[RegistryEntry]) -> str:
    """The Registry as a table under a header line, one line per Target, then one
    `problem:` line per problem. Plain padded text, as `list` renders, so the bytes are
    the same at any terminal width."""
    if not entries:
        return "no Targets in this Workspace; `agentdiag init --target <slug>` adds one"
    lines = render_table([list(COLUMNS), *(_cells(entry) for entry in entries)])
    lines += [
        f"problem: {entry.slug}: {problem}" for entry in entries for problem in entry.problems
    ]
    return "\n".join(lines)


def _cells(entry: RegistryEntry) -> list[str]:
    return [
        entry.slug,
        entry.name or NONE_SHOWN,
        entry.family or NONE_SHOWN,
        entry.channel or NONE_SHOWN,
        _environments(entry),
        entry.connector or NONE_SHOWN,
        suites_shown(entry),
        render_sync(entry.sync),
    ]


def _environments(entry: RegistryEntry) -> str:
    shown = [
        f"{name} (protected)" if name in entry.protected else name for name in entry.environments
    ]
    return ", ".join(shown) or NONE_SHOWN


NOT_SYNCED = "not synced yet"
"""What the Dashboard says for a Target with no Fingerprint, in the terminal and the page."""


def render_sync(sync: SyncSummary | None, *, in_words: bool = False) -> str:
    """A Sync state as one cell: the status, and its open Sync breaks when there are any
    (`broken (1 open Sync break)`). `in_words`, the Dashboard's spelling: no Fingerprint is
    `not synced yet` and the Fingerprint's environment leads the parenthesis (`held
    (local)`); the Registry prints the status as recorded."""
    if sync is None or (in_words and sync.status == "not_checked"):
        return NOT_SYNCED if in_words else NONE_SHOWN
    said = [sync.environment] if in_words and sync.environment else []
    if sync.open_breaks:
        noun = "Sync break" if sync.open_breaks == 1 else "Sync breaks"
        said.append(f"{sync.open_breaks} open {noun}")
    return f"{sync.status} ({', '.join(said)})" if said else sync.status


def suites_shown(entry: RegistryEntry) -> str:
    """An entry's Suites as one cell, each by its file's stem and its status when it does
    not run: what the terminal table and the Orientation page's table both print."""
    shown = (suite_shown(PurePosixPath(suite.path).stem, suite) for suite in entry.suites)
    return ", ".join(shown) or NONE_SHOWN


def suite_shown(name: str, suite: SuiteEntry) -> str:
    """A Suite as the Registry prints it: its name, and its status when it does not run
    (walkthrough friction 13: a retired Suite listed bare looked runnable)."""
    return name if suite.status == "runnable" else f"{name} ({suite.status})"


def render_target(view: TargetView) -> str:
    """One Target: its Registry entry, its Calibration Notes and Maintainer notes (ADR-0016
    §5), its Fingerprint, its open Sync breaks and Change records (each `none` rather than
    left out, decision 5), then the Manifest as loaded."""
    entry = view.entry
    notes = view.calibration_notes
    maintainer = view.maintainer_notes
    opened = [record for record in view.change_records if record.is_open]
    rows = [
        ("directory", view.directory),
        ("family", entry.family or NONE_SHOWN),
        ("channel", entry.channel or NONE_SHOWN),
        ("environments", _environments(entry)),
        ("connector", entry.connector or NONE_SHOWN),
        (
            "suites",
            ", ".join(suite_shown(suite.path, suite) for suite in entry.suites) or NONE_SHOWN,
        ),
        (
            "notes",
            f"{notes.path}, {notes.words} words, fingerprint {short(notes.fingerprint)}"
            if notes is not None
            else "none",
        ),
        (
            "maintainer notes",
            _maintainer_shown(maintainer),
        ),
        ("fingerprint", _fingerprint(view.fingerprint)),
        ("sync", render_sync(entry.sync)),
        (
            "sync breaks",
            _listed(view.sync_breaks) + (f"; {SETTLE_HINT}" if view.sync_breaks else ""),
        ),
        ("change records", _listed(opened)),
        ("pushes", _pushes_shown(view.pushes)),
    ]
    width = max(len(label) for label, _ in rows)
    lines = [f"Target {entry.slug}: {entry.name or '(no Manifest loaded)'}"]
    lines += [f"{label.ljust(width)}  {value}" for label, value in rows]
    lines += [f"{' ' * width}  {_break_line(opened)}" for opened in view.sync_breaks]
    lines += [f"{' ' * width}  {_change_line(record)}" for record in opened]
    lines += [f"{' ' * width}  {_push_line(pushed)}" for pushed in view.pushes]
    lines += _section_lines(view.fingerprint)
    lines += [f"problem: {problem}" for problem in entry.problems]
    if view.manifest is not None:
        dumped = yaml.safe_dump(
            _without_none(view.manifest), sort_keys=False, allow_unicode=True, width=88
        )
        lines += ["", "Manifest as loaded:", *(f"  {line}" for line in dumped.splitlines())]
    return "\n".join(lines)


def _maintainer_shown(notes: MaintainerNotesSummary | None) -> str:
    """`maintainer_notes.md, 120 words`, `maintainer_notes.md, not written yet`, or `none`."""
    if notes is None:
        return "none"
    return f"{notes.path}, " + (f"{notes.words} words" if notes.written else "not written yet")


SETTLE_HINT = "run `agentdiag sync` to re-record the Fingerprint"
"""What `target show` says beside open Sync breaks: `sync` is what settles them."""


def _fingerprint(fingerprint: Fingerprint | None) -> str:
    """`abbe0370, built 2026-09-27T10:00:00Z (local), 5 sections covered, 1 not covered`."""
    if fingerprint is None:
        return "none"
    return (
        f"{short(fingerprint.id)}, built {fingerprint.built_at} "
        f"({fingerprint.environment}), {len(fingerprint.sections)} sections covered, "
        f"{len(fingerprint.not_covered)} not covered"
    )


def _section_lines(fingerprint: Fingerprint | None) -> list[str]:
    """The Fingerprint's sections by id with what a human reads them by, then those not
    covered with why: the ids a generated Scenario's `provenance` cites (phase-6 decision
    31), so an agent drafting Scenarios reads them here rather than from the JSON."""
    if fingerprint is None or not (fingerprint.sections or fingerprint.not_covered):
        return []
    rows = [(section.id, section.summary or "") for section in fingerprint.sections.values()]
    rows += [(entry.id, f"not covered: {entry.reason}") for entry in fingerprint.not_covered]
    width = max(len(identifier) for identifier, _ in rows)
    return ["", "Sections:"] + [
        f"  {identifier.ljust(width)}  {summary}".rstrip() for identifier, summary in rows
    ]


def _listed(items: Sequence[object]) -> str:
    return "none" if not items else f"{len(items)} open"


def _change_line(record: ChangeRecordSummary) -> str:
    """`<id> <status>: <title>`: one open Change record."""
    return f"{record.id} {record.status}: {record.title}"


def _pushes_shown(pushes: Sequence[PushSummary]) -> str:
    if not pushes:
        return "none"
    return f"last {len(pushes)}, newest first" if len(pushes) > 1 else "last 1"


def _push_line(pushed: PushSummary) -> str:
    """`<file>: <env> at <when> by <who>, <sections>, change <id>`: one Push record."""
    push = pushed.push
    change = push.change_record or "none"
    return (
        f"{pushed.path}: {push.environment} at {push.pushed_at} by {push.by} "
        f"({push.confirmed_by}), {', '.join(push.sections)}, change {change}"
    )


def _break_line(opened: OpenSyncBreak) -> str:
    """`<file>: prompt.system deployed_ahead, tool.x local_ahead`: one open break."""
    sections = ", ".join(f"{state.id} {state.direction}" for state in opened.sync_break.sections)
    return f"{opened.path}: {sections or 'no section named'}"


def _without_none(value: Any) -> Any:
    """The Manifest without its unset keys, so the terminal shows what the file says."""
    if isinstance(value, dict):
        return {key: _without_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_none(item) for item in value]
    return value


__all__ = [
    "CalibrationNotesSummary",
    "ChangeRecordSummary",
    "OpenSyncBreak",
    "RegistryEntry",
    "SyncSummary",
    "TargetView",
    "registry",
    "render_registry",
    "render_sync",
    "render_target",
    "suite_shown",
    "suites_shown",
    "target_view",
]
