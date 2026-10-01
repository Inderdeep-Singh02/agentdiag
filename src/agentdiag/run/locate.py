"""Finding a Trial's files inside a Run directory, for the readers of a Run.

Separate from `run.directory` because the two change for different reasons: `directory`
writes a Run and enforces that it is written once (ADR-0005 §2), while this module only
looks, and is what `show`, `export` and the Phase 7 UI need. A reader importing the writer
to find a path was the wrong direction.

Nothing here writes, creates or deletes anything: the functions return paths, and
`require_trace` raises `TrialNotFound` naming the path it looked at.

**A rescored Trial's Trace lives in another Run** (D34, ADR-0005 §4). `rescore` copies no
Trace: its `run.json` names the Run that holds them under `traces_from`, and `trace_path`
follows that name, to the sibling directory under the same `runs/`, whenever the Trial's
own `trace.jsonl` is absent. So `show`, `export` and the index read a rescored Trial
without knowing it is one, and a rescore is always written beside its source, under the
same Target's `runs/`.

**Runs live under their Target** (ADR-0013 §1), at `TargetPaths.runs`: `run_directories`
walks every Target of the Workspace, and `locate_run` searches every Target's `runs/` for an
id. Where a Target directory is, is
`agentdiag.workspace`'s to say. `run.json` is read as plain JSON here, not as a `RunRecord`:
this module is imported by `show`, which must not pay for the Judge's imports.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from agentdiag.exits import USAGE_EXIT
from agentdiag.workspace import TargetPaths, Workspace

TRIALS_DIRNAME = "trials"

TRACE_FILE = "trace.jsonl"
JUDGEMENT_FILE = "judgement.jsonl"
SCORES_FILE = "scores.json"
RUN_RECORD_FILE = "run.json"
SCORECARD_FILE = "scorecard.json"
"""Every file name a Run directory holds (ADR-0005 §2), in one place for writer and readers."""

REPORT_FILE = "report.html"
"""The Report a Run writes last (ADR-0005 §11, ticket 15): a rendering of the files above,
never read back by agentdiag; Runs recorded before ticket 15 have none."""

NOT_FOUND_EXIT = USAGE_EXIT
"""The Run or the Trial is not where the caller said (`show`, `export`, CLI)."""


class TrialNotFound(FileNotFoundError):
    """A Run directory or a Trial's Trace is not at the path the caller named."""


def run_directories(root: Path, within: TargetPaths | None = None) -> list[Path]:
    """Every Run directory of every Target in the Workspace at `root`, or of the one Target
    `within` names, oldest first.

    The one function that knows where Runs live, for the index and `list` (phase-5
    decision 63, phase-6 decision 2). Sorted by directory name across Targets, and a Run id
    starts with its creation time, so the order is the order they were made. A Target with
    no `runs/` has no Runs, which is not an error.
    """
    targets = [within] if within is not None else Workspace.at(root).targets()
    found: list[Path] = []
    for target in targets:
        if target.runs.is_dir():
            found.extend(path for path in target.runs.iterdir() if path.is_dir())
    return sorted(found, key=lambda path: (path.name, str(path)))


def index_path(root: Path) -> Path:
    """Where the derived index of the Workspace at `root` lives: one per Workspace, keyed by
    Target (ADR-0005 §5, ADR-0013 §3, decision 62)."""
    return Workspace.at(root).index


def locate_run(
    workspace: Workspace | None, run: str, *, within: Sequence[TargetPaths] | None = None
) -> Path:
    """The Run directory `run` names: a path to one, or an id under a Target's `runs/`.

    A path is tried first, and needs no Workspace, so a caller who pasted one from `run`'s
    output is never told to look under a `--root` they did not mean. An id is searched for
    under every Target of the Workspace, or only under `within` when a `--target` narrowed
    it; the error names every place it looked.

    An id found under two Targets is refused naming both directories rather than one being
    picked. Run ids carry a random suffix (`directory.new_run_id`), so this only happens
    when a Run directory was copied between Targets: a guard, not a path anyone takes.
    """
    candidate = Path(run)
    if candidate.is_dir():
        return candidate
    if workspace is None:
        raise TrialNotFound(
            f"no Run {run!r}: {candidate} is not a Run directory, and no Workspace was found "
            "to look the id up in; name one with --root"
        )
    targets = list(within) if within is not None else workspace.targets()
    found = [target.runs / run for target in targets if (target.runs / run).is_dir()]
    if len(found) > 1:
        raise TrialNotFound(
            f"Run {run!r} is under more than one Target: {', '.join(map(str, found))}; "
            "name the one you mean with --target, or by its path"
        )
    if found:
        return found[0]
    looked = ", ".join(f"Target {target.slug}'s {target.runs}" for target in targets)
    raise TrialNotFound(
        f"no Run {run!r}: looked at {candidate}"
        + (
            f" and under {looked}"
            if targets
            else f", and the Workspace at {workspace.root} holds no Target"
        )
    )


def holding_target(root: Path, run_dir: Path) -> TargetPaths | None:
    """The Target whose `runs/` holds `run_dir`, or None for a Run directory elsewhere: the
    Target a Run belongs to when its `run.json` does not say (the directory is the truth),
    and the one a rescore of it is written beside."""
    parent = Path(run_dir).resolve().parent
    for target in Workspace.at(root).targets():
        if target.runs.resolve() == parent:
            return target
    return None


def trial_dir(run_dir: Path, scenario_id: str, trial: int) -> Path:
    """Where one Trial's files live (ADR-0005 §2)."""
    return Path(run_dir) / TRIALS_DIRNAME / scenario_id / str(trial)


def trace_path(run_dir: Path, scenario_id: str, trial: int) -> Path:
    """The Trace of one Trial, whose absence is what makes a Trial "not found".

    The Trial's own `trace.jsonl` when it has one; otherwise, in a rescore, the same Trial's
    Trace in the Run `traces_from` names. When neither exists, the Trial's own path, so a
    "not found" names where this Run would have kept it.
    """
    local = trial_dir(run_dir, scenario_id, trial) / TRACE_FILE
    if local.exists():
        return local
    source = traces_from(run_dir)
    if source is not None:
        borrowed = trial_dir(Path(run_dir).parent / source, scenario_id, trial) / TRACE_FILE
        if borrowed.exists():
            return borrowed
    return local


def traces_holder(run_dir: Path) -> Path:
    """The Run directory that holds this Run's Traces: its own, or its rescore source's."""
    source = traces_from(run_dir)
    return Path(run_dir) if source is None else Path(run_dir).parent / source


def recorded_trials(run_dir: Path) -> dict[str, list[int]]:
    """Every Trial with a Trace, by Scenario id, following `traces_from` (D34)."""
    root = traces_holder(run_dir) / TRIALS_DIRNAME
    trials: dict[str, list[int]] = {}
    if not root.is_dir():
        return trials
    for trace in sorted(root.glob(f"*/*/{TRACE_FILE}")):
        if trace.parent.name.isdigit():
            trials.setdefault(trace.parent.parent.name, []).append(int(trace.parent.name))
    return {scenario: sorted(numbers) for scenario, numbers in trials.items()}


def traces_from(run_dir: Path) -> str | None:
    """The id of the Run whose Traces this one's Scores read, when it is a rescore."""
    path = Path(run_dir) / RUN_RECORD_FILE
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    source = record.get("traces_from") if isinstance(record, dict) else None
    return source if isinstance(source, str) and source else None


def require_trace(run_dir: Path, scenario_id: str, trial: int) -> Path:
    """The Trace of one Trial, or `TrialNotFound` naming the path that was looked at."""
    path = trace_path(run_dir, scenario_id, trial)
    if not path.exists():
        raise TrialNotFound(
            f"no Trial {trial} of Scenario {scenario_id!r} in Run {Path(run_dir).name}: "
            f"looked for {path}"
        )
    return path


__all__ = [
    "JUDGEMENT_FILE",
    "NOT_FOUND_EXIT",
    "REPORT_FILE",
    "RUN_RECORD_FILE",
    "SCORECARD_FILE",
    "SCORES_FILE",
    "TRACE_FILE",
    "TRIALS_DIRNAME",
    "TrialNotFound",
    "holding_target",
    "index_path",
    "locate_run",
    "recorded_trials",
    "require_trace",
    "run_directories",
    "trace_path",
    "traces_from",
    "traces_holder",
    "trial_dir",
]
