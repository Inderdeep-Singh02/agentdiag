"""`agentdiag push`: the local files written to the deployed set, and only under ADR-0011 §6
(phase-7 decisions 12 to 14).

**A push is a preview until it is asked to write.** `preview` takes a Connector read of the
deployed set *now* (never a pull), compares it with the pointed files and the Target's
Fingerprint (`sync.check.check_target`), and lists exactly what would be written: each
section whose local bytes differ from the deployed ones, with the unified diff deployed →
local, the deployed Fingerprint the write will expect (`sync.observe.deployed_fingerprint`),
the effective side-effect class (the higher of the environment's and the write
operation's), and the confirmation it needs. It also lists every reason nothing may be
written; an empty `refusals` is the only preview `push` accepts. The sections a push would
write are the pointed prompt and tool sections whose local side differs from the deployed
one; any of them Sync reads `deployed_ahead` or `diverged` refuses the push (pull, merge,
push again; there is no force flag), and `--section` narrows them. The model, the provider
and the tier are never pushed (ADR-0011 §1).

**The write, in order, and nothing else** (`push`): refuse a preview with refusals, a
protected environment with no Change record or confirmed by `--push`, a Change record that
is neither `proposed` nor `pushed`, a preview older than `PREVIEW_MAX_AGE_S`; save the
**Restore point** (the preview's read); write through the Connector with the expected
Fingerprint, so a deployed set that moved since the preview is refused
(`ExpectedFingerprintMoved`) and nothing after it runs; re-read, rebuild the Fingerprint
from that read and the local files with `pushed_from` naming the one it replaces; write the
**Push record**; append the Change record's push event (`change.lifecycle`). A push writes
only the sections the preview listed, with exactly the values it showed.

**`push --restore <point>`** is the same command over a Restore point: the local side is
the point's deployed set, each prompt that differs from the read is written whole as the
point holds it, and each tool and Flow that differs as the point holds it. `--section`
narrows a restore as it narrows a local push: a heading section (`prompt.<name>#<slug>`)
writes only that section's body from the point, through the same replace. A tool or a Flow
added on the deployed side since the point is not removed (a write names what it sets; v1
has no removal), and the preview says so.

**`push_target`** is the command: the preview, the confirmation rules (`check_confirmation`,
one place), the typed name asked through its caller, the write, and the exit code.

Offline: the Connector reads and writes, and nothing converses with the Target.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from agentdiag.change.lifecycle import ChangeRefused, record_connector_push, require_pushable
from agentdiag.change.record import ChangeRecordInvalid, ChangeRecordNotFound, find_record
from agentdiag.connector.base import (
    WRITE_DEPLOYED_SET,
    Connector,
    ConnectorError,
    DeployedSet,
    ExpectedFingerprintMoved,
    WriteReceipt,
)
from agentdiag.connector.plugins import UnknownKind, build_connector
from agentdiag.exits import USAGE_EXIT
from agentdiag.run.directory import render_json
from agentdiag.run.manifest import Manifest, ManifestError, higher_side_effects
from agentdiag.sync.check import check_target, rebuilt
from agentdiag.sync.compare import SectionState
from agentdiag.sync.fingerprint import (
    FingerprintError,
    load_fingerprint,
    short,
    write_fingerprint,
)
from agentdiag.sync.observe import (
    DeployedSide,
    connector_failure,
    connector_refusal,
    deployed_fingerprint,
    deployed_from,
)
from agentdiag.sync.pointed import (
    OUTSIDE_ROOT,
    PointedFile,
    narrow,
    pointed_file,
    read_text,
    render_schema,
    section_value,
)
from agentdiag.sync.pushes import (
    PushFileError,
    PushRecord,
    RestorePoint,
    load_restore_point,
    who,
    write_push_record,
    write_restore_point,
)
from agentdiag.sync.sections import (
    FLOW_PREFIX,
    PREAMBLE_SLUG,
    PROMPT_PREFIX,
    TOOL_PREFIX,
    Hashed,
    belongs_to,
    heading_line,
    sha256,
)
from agentdiag.timestamps import now_utc
from agentdiag.types import DEPLOYED_MOVED, Confirmation, SideEffectClass
from agentdiag.workspace import TargetPaths

PREVIEW_MAX_AGE_S = 300
"""How old a preview may be when the push that confirms it writes (decision 13): past it,
the bytes a person confirmed may no longer be the bytes that would change."""

MOVED_EXIT = 2
"""`push` when the deployed set moved between the preview and the write: a re-run signal
(preview again), as `sync --check` exits 2 on `broken` (phase-7 decision 27)."""

PULL_FIRST = "pull, merge and push again; there is no force flag"

PROTECTED_HAND_OFF = (
    "this environment is protected: hand the push to a person on a terminal or in the UI"
)
"""What a non-interactive session asked to push to a protected environment is told."""

RESTORE_POINT_PATTERN = "restore-points/<time of the write>-{environment}.json"
"""Where the preview says the Restore point will be saved: the name is taken when it is
written, so the preview names the pattern and the push prints the file."""

PUSHABLE = (PROMPT_PREFIX, TOOL_PREFIX)
"""The sections a pointed file holds and a push writes; a Restore point adds `flow.`."""

NO_CHANGE = "none"
"""`--change none`: the push names no Change record (unprotected environments only)."""


class SectionDiff(BaseModel):
    """One section a push would write, and the bytes that would change."""

    id: str
    """The section id written: a Fingerprint section id, or `prompt.<name>` for a whole
    prompt a restore writes back."""

    file: str
    """The pointed file relative to the Target directory, or the Restore point's path."""

    deployed_sha256: str | None = None
    local_sha256: str
    """The hash of what will be written: the local section (or the Restore point's)."""

    diff: str
    """Unified, deployed → local."""


class PushPreview(BaseModel):
    """What `push` would write, and every reason it may not (phase-7 decision 12)."""

    environment: str
    """The Connector environment the push writes."""

    read_at: str | None = None
    """When the Connector's read was taken; None when there was no read."""

    expected_fingerprint: str | None = None
    """The deployed Fingerprint of that read (decision 10): what the write will expect."""

    fingerprint_before: str | None = None
    """The Target's Fingerprint in force: the rebuilt one's `pushed_from`."""

    sections: list[SectionDiff] = Field(default_factory=list)
    """What the push would write, sorted by id."""

    refusals: list[str] = Field(default_factory=list)
    """Every reason nothing may be written; empty means the push may proceed."""

    notes: list[str] = Field(default_factory=list)
    """What the preview says beside the diffs: what a restore cannot undo."""

    effective_side_effects: SideEffectClass
    """The higher of the environment's class and the Connector's write operation's
    (ADR-0011 §6d)."""

    protected: bool
    """Whether the Manifest marks the environment protected: then the push needs the typed
    name and a Change record."""

    confirmation: Confirmation
    """`typed_name` on a protected environment, else `--push`."""

    change_record_required: bool
    """True on a protected environment: `--change <id>` is required, `none` refused."""

    restore_point: str | None = None
    """Where the Restore point will be saved, relative to the Target directory: a pattern,
    since its name is taken when it is written."""

    restore: str | None = None
    """With `--restore`: the Restore point being pushed back, relative to the Target
    directory."""

    diff_sha256: str
    """Over the concatenated diffs, in section order."""

    payload: dict[str, str | dict[str, Any]] = Field(default_factory=dict)
    """The exact values the write sends, by section id: what the diffs show."""

    read: DeployedSet | None = None
    """The Connector's read the preview was computed against: the Restore point."""


class PushRefused(ValueError):
    """A push that writes nothing; the message says why."""


class PushMoved(PushRefused):
    """The deployed set moved between the preview and the write (`ExpectedFingerprintMoved`):
    a re-run signal, exit 2 — preview again."""


class PushWriteRefused(PushRefused):
    """The Connector refused the write itself: nothing was written to the deployed set, but
    the Restore point was saved first and is kept."""


class PushIncomplete(PushRefused):
    """The write was made and its Push record kept, but a step after it failed (the re-read,
    the Fingerprint, the Change record); the message names each and the record."""

    def __init__(self, message: str, outcome: PushOutcome) -> None:
        super().__init__(message)
        self.outcome = outcome


@dataclass(frozen=True)
class PushOutcome:
    """What one push wrote."""

    push_record: Path
    restore_point: Path
    fingerprint_before: str
    fingerprint_after: str
    sections: list[str]
    receipt: WriteReceipt
    change_record: Path | None = None
    problems: list[str] = field(default_factory=list)


# --- the preview ---


def preview(
    target: TargetPaths,
    manifest: Manifest,
    environment: str,
    sections: Sequence[str] | None = None,
    *,
    restore: Path | str | None = None,
) -> PushPreview:
    """What a push of the local files (or of the Restore point `restore`) to `environment`
    would write, computed against a Connector read taken now."""
    protected = manifest.is_protected(environment)
    shown = PushPreview(
        environment=environment,
        effective_side_effects=manifest.side_effects_of(environment),
        protected=protected,
        confirmation="typed_name" if protected else "--push",
        change_record_required=protected,
        diff_sha256=sha256(""),
    )
    refusal = connector_refusal(manifest, environment)
    if refusal is not None:
        return _refused(shown, refusal)
    try:
        connector = _connector(manifest)
        write_class = _write_class(connector)
        read = connector.read_deployed_set(environment)
    except (ConnectorError, UnknownKind) as exc:
        return _refused(shown, connector_failure(exc, manifest, environment))
    shown.effective_side_effects = higher_side_effects(shown.effective_side_effects, write_class)
    shown.read_at, shown.read = read.read_at, read
    shown.expected_fingerprint = deployed_fingerprint(read)
    shown.restore_point = RESTORE_POINT_PATTERN.format(environment=environment)
    try:
        recorded = load_fingerprint(target)
        check = check_target(
            target, manifest, environment, DeployedSide(deployed=deployed_from(read))
        )
    except (FingerprintError, ManifestError) as exc:
        return _refused(shown, str(exc))
    if recorded is None:
        return _refused(
            shown,
            f"Target {target.slug} has no Fingerprint to push against: run `agentdiag sync "
            f"--env {environment}` first",
        )
    shown.fingerprint_before = recorded.id
    states = {state.id: state for state in check.result.sections}
    if restore is not None:
        wholes, per_section = _restore_plan(target, shown, read, restore)
        planned = {**per_section, **wholes} if sections else wholes
    else:
        planned = _local_plan(target, manifest, shown, states, read)
    kept, narrowed = narrow(planned, sections)
    if narrowed is not None:
        shown.refusals.append(narrowed)
    else:
        _direction_refusals(
            shown,
            [s for s in states.values() if any(belongs_to(s.id, w) for w in kept)],
            recorded.environment,
        )
        for identifier in sorted(kept):
            diff, value = kept[identifier]
            shown.sections.append(diff)
            shown.payload[identifier] = value
    if not shown.sections and not shown.refusals:
        shown.refusals.append(
            "nothing to push: no section's local side differs from the deployed set"
            if restore is None
            else "nothing to push: the deployed set already holds the Restore point"
        )
    shown.diff_sha256 = sha256("".join(diff.diff for diff in shown.sections))
    return shown


Planned = dict[str, tuple[SectionDiff, str | dict[str, Any]]]
"""Each section a push could write: its diff and the value it would send."""


def _refused(shown: PushPreview, reason: str) -> PushPreview:
    shown.refusals.append(reason)
    return shown


def _connector(manifest: Manifest) -> Connector:
    connector = build_connector(manifest)
    assert connector is not None  # the Manifest names a Connector
    return connector


def _write_class(connector: Connector) -> SideEffectClass:
    for operation in connector.describe().operations:
        if operation.name == WRITE_DEPLOYED_SET:
            return operation.side_effects
    return "none"


def _local_plan(
    target: TargetPaths,
    manifest: Manifest,
    shown: PushPreview,
    states: dict[str, SectionState],
    read: DeployedSet,
) -> Planned:
    """The pointed sections whose local side differs from the read."""
    planned: Planned = {}
    for state in states.values():
        if not state.id.startswith(PUSHABLE) or state.local is None:
            continue
        if state.local == state.deployed:
            continue
        found = pointed_file(target, manifest, state.id)
        if isinstance(found, str):
            if found.startswith(OUTSIDE_ROOT):
                shown.refusals.append(f"{state.id} is {found}: it is compared, never written")
            continue
        local_text = read_text(found.path) or ""
        if found.kind == "tool":
            try:
                value: str | dict[str, Any] = _schema(local_text, found)
            except PushRefused as refused:
                shown.refusals.append(str(refused))
                continue
            before = render_schema(read.tools[found.name]) if found.name in read.tools else ""
            after = render_schema(value)
        else:
            deployed_text = read.prompts.get(found.name, "")
            value = section_value(state.id, local_text, deployed_text)
            before = _other_value(state.id, deployed_text, local_text)
            after = value
        planned[state.id] = (
            _section_diff(
                shown, state.id, found.relative, state.deployed, state.local, before, after
            ),
            value,
        )
    return planned


def _schema(text: str, found: PointedFile) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(text) if text else None
    except yaml.YAMLError as exc:
        raise PushRefused(f"{found.relative} is not a tool schema: {exc}") from exc
    if not isinstance(loaded, dict):
        raise PushRefused(f"{found.relative} is not a tool schema (a JSON object)")
    return loaded


def _other_value(identifier: str, text: str, other: str) -> str:
    """Prompt section `identifier` of `text`, as the diff shows it beside `other`'s value:
    empty when `text` does not hold that heading."""
    if "#" not in identifier:
        return text
    heading = identifier.partition("#")[2]
    if heading != PREAMBLE_SLUG and heading_line(text, heading) is None:
        return ""
    return section_value(identifier, text, other)


def _section_diff(
    shown: PushPreview,
    identifier: str,
    file: str,
    deployed_sha256: str | None,
    local_sha256: str,
    before: str,
    after: str,
) -> SectionDiff:
    return SectionDiff(
        id=identifier,
        file=file,
        deployed_sha256=deployed_sha256,
        local_sha256=local_sha256,
        diff="".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"deployed/{shown.environment}/{identifier}",
                tofile=file,
            )
        ),
    )


def _direction_refusals(
    shown: PushPreview, states: Sequence[SectionState], recorded_environment: str
) -> None:
    """The §6f refusal. A Target has one Fingerprint; when it was recorded against another
    environment, a section of this one can read ahead only because the two environments
    differ, and pulling would undo the fix: the refusal says to compare against this one
    first."""
    ahead = [state for state in states if state.direction in DEPLOYED_MOVED]
    if not ahead:
        return
    moved = ", ".join(f"{state.id} {state.direction}" for state in ahead)
    if recorded_environment != shown.environment:
        shown.refusals.append(
            f"the deployed side of {shown.environment} differs from the Fingerprint on a section "
            f"this push would write ({moved}), and that Fingerprint was recorded against "
            f"{recorded_environment!r}: run `agentdiag sync --env {shown.environment}` to "
            "record it against this environment, then preview again"
        )
        return
    shown.refusals.append(
        f"the deployed side moved on a section this push would write ({moved}): {PULL_FIRST}"
    )


def _restore_plan(
    target: TargetPaths, shown: PushPreview, read: DeployedSet, restore: Path | str
) -> tuple[Planned, Planned]:
    """What the Restore point holds that the read does not: `(wholes, sections)`. The
    wholes are each differing prompt as the point holds it (`prompt.<name>`) and each
    differing tool and Flow, what a restore writes by default; the sections are each
    differing heading section of those prompts, which `--section` may name instead, written
    through the same replace a local push uses."""
    try:
        path, point = load_restore_point(target, str(restore))
    except PushFileError as exc:
        shown.refusals.append(str(exc))
        return {}, {}
    relative = in_target(target, path)
    shown.restore = relative
    if point.environment != shown.environment:
        shown.refusals.append(
            f"the Restore point {relative} was saved from {point.environment!r}, not "
            f"{shown.environment!r}: a Restore point goes back where it came from"
        )
        return {}, {}
    kept = point.deployed
    then, now = deployed_from(kept).sections, deployed_from(read).sections
    planned: Planned = {}
    wholes: Planned = {}
    for name in sorted(set(kept.prompts) | set(read.prompts)):
        if name not in kept.prompts:
            shown.notes.append(f"prompt {name} was added since the Restore point; it is kept")
            continue
        text, current = kept.prompts[name], read.prompts.get(name, "")
        if current == text:
            continue
        whole = f"{PROMPT_PREFIX}{name}"
        wholes[whole] = (
            _section_diff(shown, whole, relative, sha256(current), sha256(text), current, text),
            text,
        )
        for identifier in sorted(then):
            if not belongs_to(identifier, whole) or identifier == whole:
                continue
            if _same(then.get(identifier), now.get(identifier)):
                continue
            value = section_value(identifier, text, current)
            planned[identifier] = (
                _section_diff(
                    shown,
                    identifier,
                    relative,
                    _hash(now.get(identifier)),
                    then[identifier].sha256,
                    _other_value(identifier, current, text),
                    value,
                ),
                value,
            )
    for prefix, held, current_set in (
        (TOOL_PREFIX, kept.tools, read.tools),
        (FLOW_PREFIX, kept.flows, read.flows),
    ):
        for name in sorted(set(held) | set(current_set)):
            identifier = f"{prefix}{name}"
            if name not in held:
                shown.notes.append(
                    f"{identifier} was added since the Restore point; a push cannot remove it"
                )
                continue
            if current_set.get(name) == held[name]:
                continue
            before = render_schema(current_set[name]) if name in current_set else ""
            entry = (
                _section_diff(
                    shown,
                    identifier,
                    relative,
                    _hash(now.get(identifier)),
                    then[identifier].sha256,
                    before,
                    render_schema(held[name]),
                ),
                held[name],
            )
            wholes[identifier] = entry
    return wholes, planned


def in_target(target: TargetPaths, path: Path) -> str:
    """`path` relative to the Target directory, as a Push record and a Change record's
    push event name it."""
    try:
        return path.resolve().relative_to(target.directory.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _same(one: Hashed | None, other: Hashed | None) -> bool:
    return one is not None and other is not None and one.sha256 == other.sha256


def _hash(hashed: Hashed | None) -> str | None:
    return hashed.sha256 if hashed is not None else None


# --- the write ---


def check_confirmation(
    shown: PushPreview, change_record: str | None, confirmed: Confirmation | None
) -> None:
    """The confirmation rules of a protected environment (ADR-0011 §6d, e), in one place:
    it names a Change record, and `--push` never stands in for the typed name. `confirmed`
    None asks the first rule only, before a person is asked to type anything."""
    if not shown.protected:
        return
    if change_record is None:
        raise PushRefused(
            f"{shown.environment} is protected: name the push's Change record with --change <id>"
        )
    if confirmed == "--push":
        raise PushRefused(
            f"{shown.environment} is protected: --push does not stand in for the "
            "environment's name typed by a person"
        )


def pushable_record(target: TargetPaths, change_record: str | None) -> Path | None:
    """The file of Change record `change_record` when a push may name it (`lifecycle.
    require_pushable`); `PushRefused` saying why otherwise."""
    if change_record is None:
        return None
    try:
        path, record, _ = find_record(target, change_record)
        require_pushable(record)
    except (ChangeRecordNotFound, ChangeRecordInvalid, ChangeRefused) as exc:
        raise PushRefused(str(exc)) from exc
    return path


def push(
    target: TargetPaths,
    manifest: Manifest,
    environment: str,
    shown: PushPreview,
    *,
    change_record: str | None,
    confirmed: Confirmation,
    now: str | None = None,
) -> PushOutcome:
    """Write what `shown` previewed (decision 13), in order. `PushRefused` before anything
    is written; `PushMoved` when the Connector found the deployed set moved; after a write,
    `PushIncomplete` when a later step failed, its Push record kept."""
    moment = now or now_utc()
    if shown.environment != environment:
        raise PushRefused(f"the preview is of {shown.environment!r}, not {environment!r}")
    if shown.refusals:
        raise PushRefused("; ".join(shown.refusals))
    check_confirmation(shown, change_record, confirmed)
    pushable_record(target, change_record)
    assert shown.read is not None and shown.read_at is not None
    assert shown.expected_fingerprint is not None
    age = _seconds_between(shown.read_at, moment)
    if age is None or age > PREVIEW_MAX_AGE_S:
        read = f"read {age:.0f} s ago" if age is not None else f"read at {shown.read_at!r}"
        raise PushRefused(
            f"the preview was {read}, not within the last {PREVIEW_MAX_AGE_S} s: preview again"
        )

    point = write_restore_point(
        target,
        RestorePoint(
            target=target.slug,
            environment=environment,
            saved_at=shown.read_at,
            fingerprint=shown.expected_fingerprint,
            deployed=shown.read,
        ),
    )
    kept = f"Restore point {target.relative_of(point)} is kept"
    connector = _connector(manifest)
    try:
        receipt = connector.write_deployed_set(
            environment,
            shown.payload,
            expected_fingerprint=shown.expected_fingerprint,
            change_record=change_record,
        )
    except ExpectedFingerprintMoved as moved:
        raise PushMoved(
            f"{connector_failure(moved, manifest, environment)}; nothing was written; {kept}"
        ) from moved
    except ConnectorError as refused:
        raise PushWriteRefused(
            "the Connector refused the write: "
            f"{connector_failure(refused, manifest, environment)}; nothing was written; {kept}"
        ) from refused

    problems: list[str] = []
    before = shown.fingerprint_before or ""
    after = receipt.fingerprint_after
    try:
        before = _in_force(target, before)
        after_read = connector.read_deployed_set(environment)
        check = check_target(
            target, manifest, environment, DeployedSide(deployed=deployed_from(after_read))
        )
        rebuilt_one = rebuilt(check, built_at=moment, resynced=False)
        fingerprint = rebuilt_one.model_copy(update={"pushed_from": before or None})
        write_fingerprint(target, fingerprint)
        after = fingerprint.id
    except (ConnectorError, UnknownKind, FingerprintError, ManifestError, OSError) as exc:
        problems.append(
            f"the Fingerprint was not rebuilt after the write ({type(exc).__name__}: "
            f"{connector_failure(exc, manifest, environment)}); `agentdiag sync --env "
            f"{environment}` records it"
        )

    by, os_user, agent = who()
    record = PushRecord(
        pushed_at=moment,
        target=target.slug,
        environment=environment,
        by=by,
        os_user=os_user,
        agent=agent,
        effective_side_effects=shown.effective_side_effects,
        confirmed_by=confirmed,
        sections=[diff.id for diff in shown.sections],
        fingerprint_before=before,
        fingerprint_after=after,
        restore_point=in_target(target, point),
        diff_sha256=shown.diff_sha256,
        change_record=change_record,
        receipt=receipt,
        problems=problems,
    )
    path = write_push_record(target, record)
    changed = None
    if change_record is not None:
        try:
            changed = record_connector_push(
                target,
                change_record,
                environment=environment,
                at=moment,
                push_record=in_target(target, path),
                fingerprint_before=before,
                fingerprint_after=after,
            )
        except (ChangeRefused, ChangeRecordNotFound, ChangeRecordInvalid, OSError) as exc:
            problems.append(
                f"Change record {change_record} gained no push event: {exc}; append it with "
                "the Push record's path once the record is proposed or pushed again"
            )
            record = record.model_copy(update={"problems": problems})
            path.write_text(render_json(record), encoding="utf-8")
    outcome = PushOutcome(
        push_record=path,
        restore_point=point,
        fingerprint_before=before,
        fingerprint_after=after,
        sections=record.sections,
        receipt=receipt,
        change_record=changed,
        problems=problems,
    )
    if problems:
        raise PushIncomplete(
            f"the write was made and recorded in {target.relative_of(path)}, but "
            + "; ".join(problems),
            outcome,
        )
    return outcome


def _in_force(target: TargetPaths, fallback: str) -> str:
    recorded = load_fingerprint(target)
    return recorded.id if recorded is not None else fallback


def _seconds_between(earlier: str, later: str) -> float | None:
    """Seconds from `earlier` to `later`, both in `timestamps.iso_utc`'s spelling; None
    when either does not parse (a preview whose moment is unreadable counts as stale)."""
    try:
        start = datetime.strptime(earlier, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        end = datetime.strptime(later, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None
    return (end - start).total_seconds()


# --- the command ---


@dataclass
class PushExit:
    """What `agentdiag push` prints and exits with. `ask` is set when the push needs the
    environment's name typed by a person: the caller asks it (the CLI on a terminal, the UI
    in its confirm step) and calls `push_target` again with `typed` and this `preview`."""

    code: int
    message: str = ""
    """What goes to stdout: the preview, and what the push wrote."""

    error: str | None = None
    """What goes to stderr: why nothing, or not everything, happened."""

    preview: PushPreview | None = None
    ask: str | None = None
    outcome: PushOutcome | None = None
    untouched: bool = False
    """True when nothing was written at all, not even a Restore point: a preview, a
    refusal before the write, a name still to be typed. The preview may be confirmed again."""


def push_target(
    target: TargetPaths,
    manifest: Manifest,
    environment: str,
    sections: Sequence[str] | None = None,
    *,
    write: bool = False,
    change: str | None = None,
    restore: str | None = None,
    json_output: bool = False,
    terminal: bool = False,
    typed: str | None = None,
    shown: PushPreview | None = None,
    typed_as: Literal["typed_name", "ui_confirm"] = "typed_name",
) -> PushExit:
    """`agentdiag push` for one Target (decision 14): the preview, and with `write` the
    confirmation and the write. On a protected environment the first call returns `ask`
    (or, with no `terminal`, the hand-off refusal); the second, with `typed`, writes.

    `typed_as` is the `Confirmation` the Push record keeps for a name typed by a person:
    `typed_name` at a terminal, `ui_confirm` in the UI's confirm step (`agentdiag serve`,
    phase-7 decision 22), the only two places a person types it (ADR-0011 §6d). `--push` is
    never one: it is what `write` alone confirms, and only where no name is needed."""
    asked = typed is not None
    if shown is None:
        shown = preview(target, manifest, environment, sections, restore=restore)
    message = (
        "" if asked else (shown.model_dump_json(indent=2) if json_output else render_preview(shown))
    )
    if shown.refusals:
        return PushExit(code=USAGE_EXIT, message=message, preview=shown, untouched=True)
    if not write:
        return PushExit(code=0, message=message, preview=shown, untouched=True)
    change_record = None if change in (None, NO_CHANGE) else change
    try:
        check_confirmation(shown, change_record, None)
        pushable_record(target, change_record)
    except PushRefused as refused:
        return PushExit(
            code=USAGE_EXIT,
            message=message,
            error=f"error: {refused}",
            preview=shown,
            untouched=True,
        )
    confirmed: Confirmation = "--push"
    if shown.protected:
        if typed is None:
            if not terminal:
                return PushExit(
                    code=USAGE_EXIT,
                    message=message,
                    error=f"error: {PROTECTED_HAND_OFF}",
                    preview=shown,
                    untouched=True,
                )
            return PushExit(
                code=0,
                message=message,
                preview=shown,
                ask=f"Type the environment's name to confirm the push to {environment}",
                untouched=True,
            )
        if typed.strip() != environment:
            return PushExit(
                code=USAGE_EXIT,
                error=f"error: {typed.strip()!r} is not {environment!r}; nothing was written",
                preview=shown,
                untouched=True,
            )
        confirmed = typed_as
    try:
        outcome = push(
            target, manifest, environment, shown, change_record=change_record, confirmed=confirmed
        )
    except PushMoved as moved:
        return PushExit(code=MOVED_EXIT, message=message, error=f"error: {moved}", preview=shown)
    except PushIncomplete as incomplete:
        done = render_outcome(incomplete.outcome, target)
        return PushExit(
            code=USAGE_EXIT,
            message="\n".join(part for part in (message, done) if part),
            error=f"error: {incomplete}",
            preview=shown,
            outcome=incomplete.outcome,
        )
    except PushRefused as refused:
        return PushExit(
            code=USAGE_EXIT,
            message=message,
            error=f"error: {refused}",
            preview=shown,
            untouched=not isinstance(refused, PushWriteRefused),
        )
    done = render_outcome(outcome, target)
    return PushExit(
        code=0,
        message="\n".join(part for part in (message, done) if part),
        preview=shown,
        outcome=outcome,
    )


# --- the terminal ---


def render_preview(shown: PushPreview) -> str:
    """The section table, each diff, the expected Fingerprint, the class, and what the
    write needs; then every refusal."""
    what = f"Restore point {shown.restore}" if shown.restore else "the local files"
    lines = [f"Push preview: {what} to {shown.environment}"]
    if shown.read_at is not None:
        lines.append(
            f"deployed set read at {shown.read_at}, fingerprint "
            f"{short(shown.expected_fingerprint)} (the write expects it)"
        )
    if shown.sections:
        width = max(len(diff.id) for diff in shown.sections)
        lines.append(f"{'section'.ljust(width)}  file")
        lines += [f"{diff.id.ljust(width)}  {diff.file}" for diff in shown.sections]
    lines += [f"note: {note}" for note in shown.notes]
    lines += [
        f"side effects {shown.effective_side_effects}; "
        + (
            f"{shown.environment} is protected: the push needs --push, the environment's name "
            "typed by a person, and --change <id>"
            if shown.protected
            else f"{shown.environment} is not protected: --push writes it"
        ),
    ]
    if shown.restore_point is not None and shown.sections:
        lines.append(f"before the write, the deployed set is saved as {shown.restore_point}")
    for diff in shown.sections:
        lines += ["", diff.diff.rstrip("\n")]
    if shown.refusals:
        lines.append("")
        lines += [f"refused: {reason}" for reason in shown.refusals]
    return "\n".join(lines)


def render_outcome(outcome: PushOutcome, target: TargetPaths) -> str:
    lines = [
        f"pushed {', '.join(outcome.sections)} to {outcome.receipt.environment}",
        f"fingerprint {short(outcome.fingerprint_before)} -> {short(outcome.fingerprint_after)} "
        f"(pushed_from {short(outcome.fingerprint_before)})",
        f"deployed fingerprint {short(outcome.receipt.fingerprint_before)} -> "
        f"{short(outcome.receipt.fingerprint_after)}",
        f"Restore point {target.relative_of(outcome.restore_point)}",
        f"Push record {target.relative_of(outcome.push_record)}",
    ]
    if outcome.change_record is not None:
        lines.append(
            f"Change record {target.relative_of(outcome.change_record)} gained a push event"
        )
    return "\n".join(lines)


__all__ = [
    "MOVED_EXIT",
    "NO_CHANGE",
    "PREVIEW_MAX_AGE_S",
    "PROTECTED_HAND_OFF",
    "PULL_FIRST",
    "RESTORE_POINT_PATTERN",
    "PushExit",
    "PushIncomplete",
    "PushMoved",
    "PushOutcome",
    "PushPreview",
    "PushRefused",
    "PushWriteRefused",
    "SectionDiff",
    "check_confirmation",
    "in_target",
    "preview",
    "push",
    "push_target",
    "pushable_record",
    "render_outcome",
    "render_preview",
]
