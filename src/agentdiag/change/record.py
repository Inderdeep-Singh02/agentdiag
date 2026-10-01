"""The Change record as a model and a file (ADR-0012 §1 to §3, phase-7 decision 1).

One file per record, `targets/<slug>/changes/<id>.md`, committed: the `ChangeRecord` dump as
a YAML head between `---` lines, then the record's prose (the complaint's text under
`## Trigger`, notes under `## Notes`, an imported entry under `## Imported`). The id is
`<YYYYMMDD>-<slug(title)>`, the day it was opened (UTC), with `-2`, `-3` on a collision; the
title is redacted before it is slugged, so the id and the file name carry nothing the head
may not.

`write_record` redacts before it writes (decision 6): every string of the head but the
fields `IDENTIFIER_FIELDS` names (ids, the Target's slug, timestamps, Run and Scenario ids,
span and section ids, hashes, closed-set values, `push_record`, `superseded_by`,
`imported_from`), and the whole body. The same list decides what `validate` checks for a
redaction miss (`redactable_fields`), so the two cannot drift; a field added to the model
later is redacted unless it is named here.

Offline: this module reads YAML and writes files; it never reads a Run.
"""

from __future__ import annotations

import getpass
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentdiag.change.redact import redact, redaction_names
from agentdiag.run.manifest import manifest_if_it_loads
from agentdiag.types import (
    ChangeStatus,
    Layer,
    PushKind,
    TriggerKind,
    Verdict,
    VerificationResult,
)
from agentdiag.workspace import CHANGES_DIRNAME, TargetPaths

SCHEMA_VERSION = 1
RECORD_SUFFIX = ".md"
FENCE = "---"
AGENT_VARIABLE = "AGENTDIAG_AGENT"
"""Set by a coding agent to name itself: `opened_by` (and ticket 27's Push record `by`)."""

UNKNOWN = "unknown"
"""What an imported entry records where agentdiag was not there to see (a Run it
never made, an environment the entry does not name); `validate` accepts it only on a record
with `imported_from`."""

CLOSED: frozenset[ChangeStatus] = frozenset({"verified", "refuted", "wontfix", "superseded"})
"""The statuses a record ends in; `open`, `proposed` and `pushed` are the open ones that
`target show` lists and the Dashboard counts."""


class Trigger(BaseModel):
    """What opened the record: a Diagnosis (with the Trial it explains) or a complaint."""

    model_config = ConfigDict(extra="forbid")

    kind: TriggerKind
    summary: str
    """One redacted line: the Diagnosis's first sentence, or the complaint's title."""

    run: str | None = None
    scenario: str | None = None
    trial: int | None = None
    cites: list[str] = Field(default_factory=list)
    """The Span ids the Diagnosis cited."""


class DiagnosisLink(BaseModel):
    """The Diagnosis the record answers, as read at `open`; the Run is never written to."""

    model_config = ConfigDict(extra="forbid")

    run: str
    scenario: str
    trial: int
    text: str


class ChangeSet(BaseModel):
    """What the change touches."""

    model_config = ConfigDict(extra="forbid")

    sections: list[str] = Field(default_factory=list)
    """Fingerprint section ids (phase-6 decision 10)."""

    files: list[str] = Field(default_factory=list)
    """Paths relative to the Target directory."""

    git_sha: str | None = None
    """The Workspace repository's HEAD at `propose`; None outside git."""

    flow_ids: list[str] = Field(default_factory=list)


class PushEvent(BaseModel):
    """One push of the change: a Connector push (its Push record) or a `local` one (the
    Run that re-synced onto the edited files, ADR-0012 §2)."""

    model_config = ConfigDict(extra="forbid")

    kind: PushKind
    environment: str
    at: str
    push_record: str | None = None
    """`pushes/<name>.json`, relative to the Target directory (kind `connector`)."""

    run: str | None = None
    """The re-syncing Run (kind `local`)."""

    fingerprint_before: str | None = None
    fingerprint_after: str | None = None


class Expectation(BaseModel):
    """The effect expected, stated before the verifying Run (ADR-0012 §3)."""

    model_config = ConfigDict(extra="forbid")

    stated_at: str
    should_move: list[str]
    """Scenario ids that should improve."""

    must_not_move: list[str]
    """Scenario ids whose every Score must stay unchanged."""


class Verification(BaseModel):
    """The `compare` that closed the record as `verified` or `refuted`."""

    model_config = ConfigDict(extra="forbid")

    baseline: str
    run: str
    compared_at: str
    expect: list[str]
    """The `--expect` paths the comparison declared."""

    result: VerificationResult
    summary: str
    """`Comparison.summary`, or an imported entry's post-fix observation."""

    environment: str = UNKNOWN
    """The Adapter environment the verifying Run opened, as its `run.json.adapter` records
    it (phase-8 decision 12); `unknown` on a record closed before it was recorded, or an
    imported one."""

    environment_mismatch: bool = False
    """`change close --any-env`: the verifying Run opened another environment than the one
    the record's last Connector push wrote, and the close was asked for anyway."""


class Observation(BaseModel):
    """One Score of the post-change Run over a `should_move` Scenario, cited at close."""

    model_config = ConfigDict(extra="forbid")

    run: str
    scenario: str
    trial: int
    eval: str
    eval_id: str | None = None
    verdict: Verdict
    rationale: str


class ChangeRecord(BaseModel):
    """One recorded fix for a Target (CONTEXT.md **Change record**, ADR-0012 §3)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    id: str
    target: str
    status: ChangeStatus
    opened_at: str
    opened_by: str
    closed_at: str | None = None
    title: str
    trigger: Trigger
    diagnosis: DiagnosisLink | None = None
    layer: Layer | None = None
    """None only on an imported entry (`imported_from` set)."""

    change: ChangeSet | None = None
    pushes: list[PushEvent] = Field(default_factory=list)
    expected: Expectation | None = None
    verification: Verification | None = None
    observed: list[Observation] = Field(default_factory=list)
    superseded_by: str | None = None
    why: str | None = None
    """`wontfix`'s reason."""

    imported_from: str | None = None
    """`HISTORY.md FB-146` for an imported entry."""

    @property
    def is_closed(self) -> bool:
        return is_closed(self.status)


def is_closed(status: ChangeStatus) -> bool:
    """Whether a record in `status` has ended: the one rule every reader asks."""
    return status in CLOSED


class ChangeRecordInvalid(ValueError):
    """A file under `changes/` that is not a Change record; the message names the file and
    the field."""

    def __init__(self, path: Path, problems: list[tuple[str, str]]) -> None:
        self.path = path
        self.problems = problems
        super().__init__(
            f"{path}: " + "; ".join(f"{where}: {message}" for where, message in problems)
        )


IDENTIFIER_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "target",
        "status",
        "opened_at",
        "closed_at",
        "layer",
        "trigger.kind",
        "trigger.run",
        "trigger.scenario",
        "trigger.cites[]",
        "diagnosis.run",
        "diagnosis.scenario",
        "change.sections[]",
        "change.git_sha",
        "pushes[].kind",
        "pushes[].at",
        "pushes[].push_record",
        "pushes[].run",
        "pushes[].fingerprint_before",
        "pushes[].fingerprint_after",
        "expected.stated_at",
        "expected.should_move[]",
        "expected.must_not_move[]",
        "verification.baseline",
        "verification.run",
        "verification.compared_at",
        "verification.expect[]",
        "verification.result",
        "observed[].run",
        "observed[].scenario",
        "observed[].eval",
        "observed[].eval_id",
        "observed[].verdict",
        "superseded_by",
        "imported_from",
    }
)
"""The head's string fields that are identifiers, never redacted (`[]` is a list's items).
Every other string — the title, `opened_by`, the trigger's summary, the Diagnosis text, the
change's files and Flow ids, a push's environment, the verification's summary, each
observation's rationale, `why` — is redacted on write and checked by `validate`."""


def redactable_fields(record: ChangeRecord) -> list[tuple[str, str]]:
    """Every string of the head that is not an identifier, as (where, text)."""
    return [
        (where, text)
        for where, key, text in _strings(record.model_dump(mode="json"), "", "")
        if key not in IDENTIFIER_FIELDS
    ]


def redacted(record: ChangeRecord, names: list[str]) -> ChangeRecord:
    """`record` with every string that is not an identifier redacted (decision 6)."""
    return ChangeRecord.model_validate(_redact_tree(record.model_dump(mode="json"), "", names))


def _strings(value: Any, where: str, key: str) -> Iterator[tuple[str, str, str]]:
    """(where, field key, text) for every string under `value`; `where` indexes lists,
    `key` spells a list's items as `[]`."""
    if isinstance(value, str):
        yield where, key, value
    elif isinstance(value, dict):
        for name, item in value.items():
            yield from _strings(item, _dotted(where, name), _dotted(key, name))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f"{where}[{index}]", f"{key}[]")


def _redact_tree(value: Any, key: str, names: list[str]) -> Any:
    if isinstance(value, str):
        return value if key in IDENTIFIER_FIELDS else redact(value, names)
    if isinstance(value, dict):
        return {name: _redact_tree(item, _dotted(key, name), names) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact_tree(item, f"{key}[]", names) for item in value]
    return value


def _dotted(prefix: str, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


def records_dir(target: TargetPaths) -> Path:
    """Where the Target's Change records live: the Manifest's `records` directory, relative
    to the Target directory (`changes` by default, and when the Manifest does not load)."""
    manifest = manifest_if_it_loads(target)
    return target.relative(manifest.records if manifest is not None else CHANGES_DIRNAME)


def record_path(target: TargetPaths, record_id: str) -> Path:
    return records_dir(target) / f"{record_id}{RECORD_SUFFIX}"


def opened_by() -> str:
    """Who is acting: `$AGENTDIAG_AGENT` when a coding agent set it, else the OS user."""
    agent = os.environ.get(AGENT_VARIABLE, "").strip()
    if agent:
        return agent
    try:
        return getpass.getuser()
    except (OSError, KeyError):
        return UNKNOWN


def new_record_id(target: TargetPaths, title: str, opened_at: str) -> str:
    """`<YYYYMMDD>-<slug(title)>`, the date `opened_at`'s, `-2`, `-3` when taken; the title
    redacted first, so the id carries nothing the head may not."""
    from agentdiag.run.init import slug

    base = f"{opened_at[:10].replace('-', '')}-{slug(redact(title, redaction_names(target)))}"
    candidate, number = base, 1
    while record_path(target, candidate).exists():
        number += 1
        candidate = f"{base}-{number}"
    return candidate


def render_record(record: ChangeRecord, body: str) -> str:
    """The file's text: the head between `---` lines, then the body."""
    head = yaml.safe_dump(
        record.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=88
    )
    text = f"{FENCE}\n{head}{FENCE}\n"
    body = body.strip("\n")
    return text + (f"\n{body}\n" if body else "")


def render_redacted(record: ChangeRecord, body: str, names: list[str]) -> str:
    """The file's text with the head's non-identifier strings and the body redacted: what
    is written."""
    return render_record(redacted(record, names), redact(body, names))


def _parse(text: str, path: Path) -> tuple[ChangeRecord, str]:
    """The record and its body from a file's text; `ChangeRecordInvalid` naming the field."""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != FENCE:
        raise ChangeRecordInvalid(path, [("head", "the file does not open with a --- line")])
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == FENCE)
    except StopIteration:
        raise ChangeRecordInvalid(path, [("head", "no --- line closes the head")]) from None
    try:
        raw = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise ChangeRecordInvalid(path, [("head", f"not valid YAML: {exc}")]) from exc
    if not isinstance(raw, dict):
        raise ChangeRecordInvalid(path, [("head", "the head is not a mapping")])
    try:
        record = ChangeRecord.model_validate(raw)
    except ValidationError as exc:
        raise ChangeRecordInvalid(
            path,
            [
                (".".join(str(part) for part in error["loc"]) or "head", str(error["msg"]))
                for error in exc.errors()
            ],
        ) from exc
    return record, "\n".join(lines[end + 1 :]).strip("\n")


def _read(path: Path) -> tuple[ChangeRecord, str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ChangeRecordInvalid(Path(path), [("file", str(exc))]) from exc
    return _parse(text, Path(path))


def load_record(path: Path) -> ChangeRecord:
    """The record a file holds; `ChangeRecordInvalid` naming the file and the field."""
    return _read(path)[0]


def record_body(path: Path) -> str:
    """The prose below a record file's head, kept as it is when the head is rewritten."""
    return _read(path)[1]


def write_record(target: TargetPaths, record: ChangeRecord, body: str = "") -> Path:
    """Redact, then write the record's file under the Target's `changes/`, and record it
    into the Workspace's Index when there is one (`index.record_change`, which only warns);
    the path. Every `change` command and every push that moves a record writes here."""
    path = record_path(target, record.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_redacted(record, body, redaction_names(target)), encoding="utf-8")
    index_record_file(target, path)
    return path


def index_record_file(target: TargetPaths, path: Path) -> None:
    """Record a written record file into the Index. Imported here, not at the top: the
    Index reads Change records, so it imports this module."""
    from agentdiag.run.index import record_change

    record_change(target, path)


def _record_files(target: TargetPaths) -> list[Path]:
    directory = records_dir(target)
    return sorted(directory.glob(f"*{RECORD_SUFFIX}")) if directory.is_dir() else []


def load_records(target: TargetPaths) -> list[tuple[Path, ChangeRecord]]:
    """The Target's records that load, by id; `unreadable_records` names the others."""
    found: list[tuple[Path, ChangeRecord]] = []
    for path in _record_files(target):
        try:
            found.append((path, load_record(path)))
        except ChangeRecordInvalid:
            continue
    return sorted(found, key=lambda pair: pair[1].id)


def unreadable_records(target: TargetPaths) -> list[ChangeRecordInvalid]:
    """Why each file under the Target's `changes/` that is not a record is not one."""
    problems: list[ChangeRecordInvalid] = []
    for path in _record_files(target):
        try:
            load_record(path)
        except ChangeRecordInvalid as invalid:
            problems.append(invalid)
    return problems


RECORD_ID = re.compile(r"\d{8}-[a-z0-9]+(-[a-z0-9]+)*")
"""The grammar of a record id (decision 1, `<YYYYMMDD>-<slug>`, and an imported
entry's `<YYYYMMDD>-fb-<nnn>`): what an id given on a command line or in a request must
match before it is joined onto `changes/`, so `../x` never names a path."""


def is_record_id(text: str) -> bool:
    return RECORD_ID.fullmatch(text) is not None


def find_record(target: TargetPaths, record_id: str) -> tuple[Path, ChangeRecord, str]:
    """The record `record_id` names under the Target, with its path and body; an id outside
    the grammar is not looked for (`ChangeRecordNotFound`, naming no path)."""
    if not is_record_id(record_id):
        raise ChangeRecordNotFound(f"{record_id!r} is not a Change record id")
    path = record_path(target, record_id)
    if not path.is_file():
        known = ", ".join(record.id for _, record in load_records(target)) or "none"
        raise ChangeRecordNotFound(
            f"no Change record {record_id!r} under Target {target.slug} (it holds: {known})"
        )
    record, body = _read(path)
    return path, record, body


class ChangeRecordNotFound(LookupError):
    """No record of that id under the Target; the message names the ones there are."""


__all__ = [
    "AGENT_VARIABLE",
    "CLOSED",
    "IDENTIFIER_FIELDS",
    "UNKNOWN",
    "ChangeRecord",
    "ChangeRecordInvalid",
    "ChangeRecordNotFound",
    "ChangeSet",
    "DiagnosisLink",
    "Expectation",
    "Observation",
    "PushEvent",
    "Trigger",
    "Verification",
    "find_record",
    "index_record_file",
    "is_closed",
    "is_record_id",
    "load_record",
    "load_records",
    "new_record_id",
    "opened_by",
    "record_body",
    "record_path",
    "records_dir",
    "redactable_fields",
    "redacted",
    "render_record",
    "render_redacted",
    "unreadable_records",
    "write_record",
]
