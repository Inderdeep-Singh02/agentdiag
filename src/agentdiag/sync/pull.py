"""`agentdiag pull`: the deployed set written into the files the Manifest points at
(ADR-0011 §5, phase-7 decision 11).

The candidates are the sections Sync reads as `deployed_ahead` or `diverged` against a
Connector read taken now — the ones whose deployed side moved — less a `diverged` section
whose two live sides already agree (nothing to pull; the next `sync` settles the record).
`--section` narrows them, and naming one that is not a candidate refuses the pull before
anything is written. Each candidate goes into the file its pointer names
(`sync.pointed`): a heading section's body into that heading (headings kept, a heading the
file lacks added at the end), a prompt with no headings as the whole file, a tool schema as
`discover --from-connector` spells it.

A file is written only when git vouches for what it holds — tracked, and `git status
--porcelain` empty for it — unless `--overwrite-local`: a file with uncommitted changes, an
untracked one, or any file outside a git repository is skipped as `uncommitted`, because
nothing would record what the pull replaced. A pointer outside the Workspace root is
skipped and named (ADR-0013 §2); a section no local file holds (the model, a Flow, an
`observed` prompt) is skipped saying so. `pull` then prints the git diff of what it wrote,
a file it created shown against nothing (`git diff --no-index`), staging nothing.
It never commits and never touches `fingerprint.json`: the next `sync` or Run settles it.

Offline: the Connector reads, git is asked with a timeout, and nothing converses.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from agentdiag.connector.base import ConnectorError, DeployedSet
from agentdiag.connector.plugins import UnknownKind, build_connector
from agentdiag.exits import USAGE_EXIT
from agentdiag.overwrite import GIT_TIMEOUT_SECONDS, committed_unchanged
from agentdiag.run.manifest import Manifest, ManifestError
from agentdiag.sync.check import check_target
from agentdiag.sync.compare import SectionState
from agentdiag.sync.fingerprint import FingerprintError
from agentdiag.sync.observe import (
    DeployedSide,
    connector_failure,
    connector_refusal,
    deployed_from,
)
from agentdiag.sync.pointed import (
    PointedFile,
    narrow,
    pointed_file,
    read_text,
    render_schema,
    section_value,
)
from agentdiag.sync.sections import replace_section
from agentdiag.types import DEPLOYED_MOVED
from agentdiag.workspace import TargetPaths

UNCOMMITTED = "uncommitted"
"""Why a pointed file git does not vouch for is skipped without `--overwrite-local`."""

IDENTICAL = "identical"
"""Why a section the file already holds as deployed is skipped."""


class PullRefused(ValueError):
    """A pull that writes nothing: no Connector, a read that failed, a named section that
    is not a candidate. The message says why."""


class Pulled(BaseModel):
    """One section a pull wrote."""

    section: str
    file: str
    """The pointed file, relative to the Target directory."""


class Skipped(BaseModel):
    """One candidate section a pull did not write, and why."""

    section: str
    reason: str
    """Outside the root, uncommitted, identical, or no local file holds it."""


class PullResult(BaseModel):
    """What one `pull` did (phase-7 decision 11)."""

    environment: str
    """The Connector environment whose deployed set was read."""

    written: list[Pulled] = Field(default_factory=list)
    """By section id."""

    skipped: list[Skipped] = Field(default_factory=list)
    """By section id."""

    diff: str = ""
    """`git diff -- <files>` after the write; empty outside git or when nothing moved."""


def pull(
    target: TargetPaths,
    manifest: Manifest,
    environment: str,
    sections: Sequence[str] | None = None,
    *,
    overwrite_local: bool = False,
) -> PullResult:
    """Write the deployed sections of `environment` into the pointed files; `PullRefused`
    when nothing may be written, before anything is."""
    read = _read(manifest, environment)
    try:
        check = check_target(
            target, manifest, environment, DeployedSide(deployed=deployed_from(read))
        )
    except (FingerprintError, ManifestError) as exc:
        raise PullRefused(str(exc)) from exc
    candidates = {
        state.id: state
        for state in check.result.sections
        if state.direction in DEPLOYED_MOVED and state.local != state.deployed
    }
    kept, refusal = narrow(candidates, sections)
    if refusal is not None:
        raise PullRefused(f"{refusal}; nothing was pulled")
    result = PullResult(environment=environment)
    plan = _plan(target, manifest, list(kept.values()), read, result)
    written: list[Path] = []
    created: list[Path] = []
    for path, (text, ids, relative) in sorted(plan.items()):
        if path.is_file() and not overwrite_local and not committed_unchanged(path):
            result.skipped += [
                Skipped(
                    section=section,
                    reason=f"{UNCOMMITTED}: {relative} holds changes no commit does; "
                    "commit them, or pass --overwrite-local",
                )
                for section in ids
            ]
            continue
        if read_text(path) == text:
            result.skipped += [Skipped(section=section, reason=IDENTICAL) for section in ids]
            continue
        (written if path.is_file() else created).append(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        result.written += [Pulled(section=section, file=relative) for section in ids]
    result.written.sort(key=lambda pulled: pulled.section)
    result.skipped.sort(key=lambda skipped: skipped.section)
    result.diff = git_diff(target.root, written, created)
    return result


def _read(manifest: Manifest, environment: str) -> DeployedSet:
    """The Connector's read of `environment`, or `PullRefused` saying why there is none."""
    refusal = connector_refusal(manifest, environment)
    if refusal is not None:
        raise PullRefused(refusal)
    try:
        connector = build_connector(manifest)
        assert connector is not None  # the Manifest names a Connector
        return connector.read_deployed_set(environment)
    except (ConnectorError, UnknownKind) as exc:
        raise PullRefused(connector_failure(exc, manifest, environment)) from exc


@dataclass
class _File:
    text: str
    ids: list[str] = field(default_factory=list)


def _plan(
    target: TargetPaths,
    manifest: Manifest,
    candidates: Sequence[SectionState],
    read: DeployedSet,
    result: PullResult,
) -> dict[Path, tuple[str, list[str], str]]:
    """Each pointed file's new text and the sections it takes; what cannot be written is
    added to `result.skipped` with its reason."""
    files: dict[Path, tuple[PointedFile, _File]] = {}
    for state in candidates:
        found = pointed_file(target, manifest, state.id)
        if isinstance(found, str):
            result.skipped.append(Skipped(section=state.id, reason=found))
            continue
        if found.path not in files:
            files[found.path] = (found, _File(text=read_text(found.path) or ""))
        _, planned = files[found.path]
        if found.kind == "tool":
            planned.text = render_schema(read.tools[found.name])
        else:
            deployed = read.prompts.get(found.name, "")
            heading = state.id.partition("#")[2]
            value = section_value(state.id, deployed, planned.text)
            planned.text = replace_section(planned.text, heading, value) if heading else value
        planned.ids.append(state.id)
    return {
        path: (planned.text, planned.ids, pointed.relative)
        for path, (pointed, planned) in files.items()
    }


def git_diff(root: Path, files: Sequence[Path], created: Sequence[Path] = ()) -> str:
    """`git diff` of `files` in the repository holding `root`, then each file in `created`
    (new, so untracked) against nothing, `git diff --no-index`; empty outside git. Nothing
    is staged or committed."""
    diffs = (
        [_git(root, "diff", "--no-color", "--", *(str(path) for path in files))] if files else []
    )
    for path in created:
        try:
            shown = path.relative_to(root).as_posix()
        except ValueError:
            shown = str(path)
        diffs.append(
            _git(root, "diff", "--no-color", "--no-index", "--", "/dev/null", shown, differs=True)
        )
    return "".join(diffs)


def _git(root: Path, *arguments: str, differs: bool = False) -> str:
    """What `git -C root <arguments>` prints; empty when it fails. `differs`: exit 1 means
    "the files differ", as `--no-index` says it, and is not a failure."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    ok = completed.returncode == 0 or (differs and completed.returncode == 1)
    if not ok or (differs and not _inside_git(root)):
        return ""
    return completed.stdout


def _inside_git(root: Path) -> bool:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.stdout.strip() == "true"


def render_pull(result: PullResult) -> str:
    """The table of what was written and skipped, then the diff."""
    lines = [
        f"Pull from {result.environment}: {len(result.written)} "
        f"{'section' if len(result.written) == 1 else 'sections'} written, "
        f"{len(result.skipped)} skipped"
    ]
    if not result.written and not result.skipped:
        lines[0] = (
            f"Pull from {result.environment}: nothing to pull; the deployed side is ahead on "
            "no section"
        )
    rows = [("written", pulled.section, pulled.file) for pulled in result.written]
    rows += [("skipped", skipped.section, skipped.reason) for skipped in result.skipped]
    width = max((len(section) for _, section, _ in rows), default=0)
    lines += [f"{outcome}  {section.ljust(width)}  {what}" for outcome, section, what in rows]
    if result.written:
        lines.append(
            "nothing was committed and fingerprint.json is unchanged; review the diff, commit, "
            "then `agentdiag sync`"
        )
    if result.diff:
        lines += ["", result.diff.rstrip("\n")]
    return "\n".join(lines)


@dataclass
class PullExit:
    """What `agentdiag pull` prints and exits with, and the result it printed."""

    code: int
    message: str
    result: PullResult | None = None


def pull_target(
    target: TargetPaths,
    manifest: Manifest,
    environment: str,
    sections: Sequence[str] | None = None,
    *,
    overwrite_local: bool = False,
) -> PullExit:
    """`agentdiag pull` for one Target: exit 0, or 3 with the refusal."""
    try:
        result = pull(target, manifest, environment, sections, overwrite_local=overwrite_local)
    except PullRefused as refused:
        return PullExit(code=USAGE_EXIT, message=f"error: {refused}")
    return PullExit(code=0, message=render_pull(result), result=result)


__all__ = [
    "IDENTICAL",
    "UNCOMMITTED",
    "PullExit",
    "PullRefused",
    "PullResult",
    "Pulled",
    "Skipped",
    "git_diff",
    "pull",
    "pull_target",
    "render_pull",
]
