"""The `agentdiag change` commands as functions (phase-7 decision 9): what the CLI and the
UI's routes (ticket 16) both call. Each takes a resolved `TargetPaths`, returns a
`ChangeExit` (the code, what to print, the record written), and never exits the process;
the CLI adds only the argument parsing.

- `open_record`: from a Trial's Diagnosis (`--from <run>/<scenario>/<trial>`, read from its
  `judgement.jsonl`; the Run is never written) or from a complaint file, redacted;
- `propose_change`: the change set (sections, files, flows) and the Workspace's git HEAD;
- `expect_change`: the expected effect, timestamped, its Scenario ids checked against the
  Target's runnable Suites;
- `close_change`: `--verified`/`--refuted` through `compare` (decision 3), `--wontfix`
  with its reason, `--superseded-by` another record;
- `show_change`, `list_changes`: the record as its story in five lanes (ticket 28), the
  records as a padded table;

Exit 0, or 3 (`USAGE_EXIT`) for every refusal (decision 27).
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast, get_args

from agentdiag.change.lifecycle import (
    ChangeRefused,
    close_by_compare,
    close_without_compare,
    propose,
    pushed_environment,
    state_expectation,
)
from agentdiag.change.record import (
    ChangeRecord,
    ChangeRecordInvalid,
    ChangeRecordNotFound,
    ChangeSet,
    DiagnosisLink,
    Trigger,
    find_record,
    load_records,
    new_record_id,
    opened_by,
    record_path,
    records_dir,
    write_record,
)
from agentdiag.exits import USAGE_EXIT
from agentdiag.overwrite import GIT_TIMEOUT_SECONDS
from agentdiag.run.directory import is_run_id
from agentdiag.run.manifest import Manifest, manifest_if_it_loads
from agentdiag.table import render_table
from agentdiag.timestamps import now_utc
from agentdiag.types import ChangeStatus, Layer
from agentdiag.workspace import TargetPaths

if TYPE_CHECKING:
    from agentdiag.report.story import ChangeRecordStory

NONE_SHOWN = "none"
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass
class ChangeExit:
    """What a `change` command prints and exits with."""

    code: int
    message: str
    path: Path | None = None
    record: ChangeRecord | None = None
    paths: list[Path] = field(default_factory=list)


Clock = Callable[[], str]
"""What a command reads the time from: `timestamps.now_utc` unless a test fixes it."""


def _refused(problem: Exception) -> ChangeExit:
    return ChangeExit(code=USAGE_EXIT, message=f"error: {problem}")


REFUSALS = (ChangeRefused, ChangeRecordInvalid, ChangeRecordNotFound)
"""Every refusal a command reports as `error: …`, exit 3: the lifecycle's (a transition, a
close, an expectation), a record file, a missing record."""


# --- open ---


def open_record(
    target: TargetPaths,
    *,
    layer: str,
    title: str,
    from_trial: str | None = None,
    complaint: Path | None = None,
    by: str | None = None,
    now: Clock = now_utc,
) -> ChangeExit:
    """A new `open` record, from a Diagnosis or a complaint (exactly one), in `layer` (one
    of `Layer`, refused by name otherwise)."""
    try:
        if layer not in get_args(Layer):
            raise ChangeRefused(f"--layer {layer!r} is not one of {', '.join(get_args(Layer))}")
        if (from_trial is None) == (complaint is None):
            raise ChangeRefused(
                "name exactly one trigger: --from <run>/<scenario>/<trial> or --complaint <file>"
            )
        if not title.strip():
            raise ChangeRefused("--title is empty; give the record a title")
        opened_at = now()
        body = ""
        diagnosis: DiagnosisLink | None = None
        if from_trial is not None:
            trigger, diagnosis = _diagnosis_trigger(target, from_trial)
        else:
            assert complaint is not None
            trigger, text = _complaint_trigger(complaint, title)
            body = f"## Trigger\n\n{text.strip()}\n"
        record = ChangeRecord(
            id=new_record_id(target, title, opened_at),
            target=target.slug,
            status="open",
            opened_at=opened_at,
            opened_by=by or opened_by(),
            title=title.strip(),
            trigger=trigger,
            diagnosis=diagnosis,
            layer=cast(Layer, layer),
        )
        path = write_record(target, record, body)
    except REFUSALS as problem:
        return _refused(problem)
    return ChangeExit(code=0, message=f"opened {record.id} at {path}", path=path, record=record)


def _diagnosis_trigger(target: TargetPaths, reference: str) -> tuple[Trigger, DiagnosisLink]:
    from agentdiag.run.locate import JUDGEMENT_FILE, trial_dir
    from agentdiag.trace.reader import read_trace, resolve_blobs
    from agentdiag.trace.show import diagnosis_view

    parts = reference.rsplit("/", 2)
    if len(parts) != 3 or not parts[2].isdigit():
        raise ChangeRefused(
            f"--from {reference!r} is not <run>/<scenario>/<trial>, such as "
            "20260923T100000Z-base/delivered-order-cannot-be-cancelled/1"
        )
    run, scenario, trial = parts[0], parts[1], int(parts[2])
    if not is_run_id(run):
        raise ChangeRefused(f"--from names {run!r}, which is not a Run id")
    run_dir = target.runs / run
    if not run_dir.is_dir():
        raise ChangeRefused(f"no Run {run!r} under Target {target.slug}'s {target.runs}")
    judgement = trial_dir(run_dir, scenario, trial) / JUDGEMENT_FILE
    view = diagnosis_view(resolve_blobs(read_trace(judgement))) if judgement.is_file() else None
    if view is None or not view.text.strip():
        raise ChangeRefused(
            f"Trial {trial} of Scenario {scenario!r} in Run {run} has no Diagnosis "
            f"(looked in {judgement}); open the record from a complaint, or from a judged Trial"
        )
    summary = SENTENCE_END.split(view.text.strip(), 1)[0]
    trigger = Trigger(
        kind="diagnosis", summary=summary, run=run, scenario=scenario, trial=trial, cites=view.cites
    )
    return trigger, DiagnosisLink(run=run, scenario=scenario, trial=trial, text=view.text)


def _complaint_trigger(path: Path, title: str) -> tuple[Trigger, str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ChangeRefused(f"the complaint {path} cannot be read: {exc}") from exc
    first = next(
        (line.strip().lstrip("#").strip() for line in text.splitlines() if line.strip()), ""
    )
    return Trigger(kind="complaint", summary=first or title.strip()), text


# --- propose, expect ---


def propose_change(
    target: TargetPaths,
    record_id: str,
    *,
    sections: Sequence[str],
    files: Sequence[str] = (),
    flows: Sequence[str] = (),
) -> ChangeExit:
    """`open → proposed` with the change set and the Workspace repository's HEAD."""
    try:
        if not sections and not files and not flows:
            raise ChangeRefused("name what the change touches: --section, --file or --flow")
        path, record, body = find_record(target, record_id)
        missing = [name for name in files if not target.relative(name).exists()]
        if missing:
            raise ChangeRefused(
                f"no such file under the Target directory {target.directory}: " + ", ".join(missing)
            )
        change = ChangeSet(
            sections=list(dict.fromkeys(sections)),
            files=list(dict.fromkeys(files)),
            git_sha=git_head(target.root),
            flow_ids=list(dict.fromkeys(flows)),
        )
        proposed = propose(record, change)
        path = write_record(target, proposed, body)
    except REFUSALS as problem:
        return _refused(problem)
    return ChangeExit(
        code=0, message=f"{record_id} proposed: {_change_text(change)}", path=path, record=proposed
    )


def expect_change(
    target: TargetPaths,
    record_id: str,
    *,
    should_move: Sequence[str],
    must_not_move: Sequence[str] = (),
    now: Clock = now_utc,
) -> ChangeExit:
    """State the expected effect before the verifying Run, timestamped now."""
    try:
        if not should_move:
            raise ChangeRefused("--should-move names no Scenario: say which should improve")
        path, record, body = find_record(target, record_id)
        known = runnable_scenario_ids(target)
        unknown = [s for s in [*should_move, *must_not_move] if s not in known]
        if unknown:
            raise ChangeRefused(
                "not a Scenario of the Target's runnable Suites: " + ", ".join(unknown)
            )
        stated = state_expectation(record, should_move, must_not_move, now())
        path = write_record(target, stated, body)
    except REFUSALS as problem:
        return _refused(problem)
    assert stated.expected is not None
    return ChangeExit(
        code=0,
        message=(
            f"{record_id} expects, stated at {stated.expected.stated_at}: should move "
            f"{', '.join(stated.expected.should_move)}; must not move "
            f"{', '.join(stated.expected.must_not_move) or NONE_SHOWN}"
        ),
        path=path,
        record=stated,
    )


def runnable_scenario_ids(target: TargetPaths) -> set[str]:
    """Every Scenario id in the Target's runnable Suites."""
    from agentdiag.scenario.load import SuiteLoadError, load_suite

    manifest = _manifest(target)
    ids: set[str] = set()
    for entry in manifest.runnable_suites:
        try:
            suite, _ = load_suite(
                target.relative(entry.path), default_thresholds=manifest.latency_thresholds
            )
        except (SuiteLoadError, OSError) as exc:
            raise ChangeRefused(f"the Suite {entry.path} does not load: {exc}") from exc
        ids.update(scenario.id for scenario in suite.scenarios)
    return ids


# --- close ---


def close_change(
    target: TargetPaths,
    record_id: str,
    *,
    verified: bool = False,
    refuted: bool = False,
    wontfix: bool = False,
    superseded_by: str | None = None,
    why: str | None = None,
    run: str | None = None,
    baseline: str | None = None,
    expect: Sequence[str] = (),
    any_env: bool = False,
    now: Clock = now_utc,
) -> ChangeExit:
    """Close the record one way: `--verified`/`--refuted --run`, `--wontfix --why`, or
    `--superseded-by <id>`. `any_env` (`--any-env`) lets a verifying Run on another
    environment than the last push's close it, recorded as `environment_mismatch` (phase-8
    decision 12)."""
    chosen = sum([verified, refuted, wontfix, superseded_by is not None])
    try:
        if chosen != 1:
            raise ChangeRefused(
                "close it one way: --verified, --refuted, --wontfix or --superseded-by <id>"
            )
        if any_env and not (verified or refuted):
            raise ChangeRefused("--any-env applies to --verified and --refuted, which name a Run")
        path, record, body = find_record(target, record_id)
        message: str
        if verified or refuted:
            if run is None:
                raise ChangeRefused("--verified and --refuted need --run <post-change Run>")
            closed, comparison = close_by_compare(
                target,
                record,
                result="verified" if verified else "refuted",
                run=run,
                baseline=baseline,
                expect=list(expect),
                compared_at=now(),
                any_env=any_env,
            )
            verification = closed.verification
            assert verification is not None
            message = (
                f"{record_id} {closed.status}: {verification.baseline} -> "
                f"{verification.run}, {comparison.summary}"
            )
            if verification.environment_mismatch:
                message += (
                    f"\nRun {verification.run} ran on {verification.environment!r}, not "
                    f"{pushed_environment(record)!r} where the record was pushed "
                    "(--any-env: recorded as environment_mismatch)"
                )
        elif wontfix:
            if not (why or "").strip():
                raise ChangeRefused("--wontfix needs --why: say why it is not changed")
            closed = close_without_compare(record, status="wontfix", closed_at=now(), why=why)
            message = f"{record_id} wontfix: {why}"
        else:
            assert superseded_by is not None
            if superseded_by == record_id:
                raise ChangeRefused("a Change record cannot supersede itself")
            if not record_path(target, superseded_by).is_file():
                raise ChangeRecordNotFound(
                    f"no Change record {superseded_by!r} under Target {target.slug} to supersede it"
                )
            closed = close_without_compare(
                record, status="superseded", closed_at=now(), superseded_by=superseded_by
            )
            message = f"{record_id} superseded by {superseded_by}"
        path = write_record(target, closed, body)
    except REFUSALS as problem:
        return _refused(problem)
    return ChangeExit(code=0, message=message, path=path, record=closed)


# --- show, list ---


def show_change(target: TargetPaths, record_id: str, *, json_output: bool = False) -> ChangeExit:
    """One record: its story in five lanes (ticket 28), or its head as JSON."""
    from agentdiag.report.story import change_record_story

    try:
        path, record, _ = find_record(target, record_id)
    except REFUSALS as problem:
        return _refused(problem)
    if json_output:
        return ChangeExit(
            code=0, message=record.model_dump_json(indent=2), path=path, record=record
        )
    story = change_record_story(record, target)
    return ChangeExit(code=0, message=render_story(story), path=path, record=record)


def render_story(story: ChangeRecordStory) -> str:
    """The story as text: the head, then each lane numbered, its items indented beneath it
    with what each links to and the Spans it cites, each on its own line. Byte-stable: a
    function of the story alone."""
    closed = f" · closed {story.closed_at}" if story.closed_at else ""
    lines = [
        f"Change record {story.id}: {story.title}",
        f"{story.status} · Target {story.target} · opened {story.opened_at} by "
        f"{story.opened_by}{closed}",
    ]
    if story.file is not None:
        lines.append(f"file {story.file}")
    for number, lane in enumerate(story.lanes, start=1):
        lines.append("")
        lines.append(f"{number} {lane.title}" + (" · pending" if lane.pending else ""))
        for item in lane.items:
            text = item.text.strip().splitlines() or [""]
            lines.append(f"  - {text[0]}".rstrip())
            lines += [f"    {line}".rstrip() for line in text[1:]]
            if item.link is not None:
                lines.append(f"    -> {item.link}")
            if item.cites:
                lines.append(f"    cites {', '.join(item.cites)}")
    return "\n".join(lines)


def list_changes(target: TargetPaths, *, status: str | None = None) -> ChangeExit:
    """The Target's records by id, one line each; only `status`'s when given."""
    allowed = get_args(ChangeStatus)
    if status is not None and status not in allowed:
        return ChangeExit(
            code=USAGE_EXIT,
            message=f"error: --status {status!r} is not one of {', '.join(allowed)}",
        )
    records = [record for _, record in load_records(target) if status in (None, record.status)]
    if not records:
        where = target.relative_of(records_dir(target))
        of = f" with status {status}" if status else ""
        return ChangeExit(code=0, message=f"no Change records{of} under {where}")
    return ChangeExit(code=0, message=render_record_list(records))


def render_record_list(records: Sequence[ChangeRecord]) -> str:
    """`id  status  opened  layer  title`, padded to each column's widest cell."""
    table = [["id", "status", "opened", "layer", "title"]]
    table += [
        [record.id, record.status, record.opened_at, record.layer or "-", record.title]
        for record in records
    ]
    return "\n".join(render_table(table))


# --- helpers ---


def git_head(root: Path) -> str | None:
    """The Workspace repository's HEAD commit, None outside git."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    head = completed.stdout.strip()
    return head if completed.returncode == 0 and head else None


def _change_text(change: ChangeSet) -> str:
    parts = []
    if change.sections:
        parts.append("sections " + ", ".join(change.sections))
    if change.files:
        parts.append("files " + ", ".join(change.files))
    if change.flow_ids:
        parts.append("flows " + ", ".join(change.flow_ids))
    if change.git_sha:
        parts.append(f"at {change.git_sha[:12]}")
    return "; ".join(parts) or NONE_SHOWN


def _manifest(target: TargetPaths) -> Manifest:
    """The Target's Manifest, which these commands need; refused, pointing at `validate`,
    when it does not load."""
    manifest = manifest_if_it_loads(target)
    if manifest is None:
        raise ChangeRefused(
            f"the Manifest of Target {target.slug} does not load; `agentdiag validate` says why"
        )
    return manifest


__all__ = [
    "REFUSALS",
    "ChangeExit",
    "Clock",
    "close_change",
    "expect_change",
    "git_head",
    "list_changes",
    "open_record",
    "propose_change",
    "render_record_list",
    "render_story",
    "runnable_scenario_ids",
    "show_change",
]
