"""One Run as the Report and the UI render it: the `RunView` (D40, phase-7 decision 19).

D40 asks for one renderer over three surfaces. `show` prints one Trial's `TrialStory` as
text; the Report embeds a whole Run's `RunView` as JSON beside the JavaScript renderer; the
UI serves the same JSON from its routes. So this module is the Run-level half of the view
model and `agentdiag.trace.show` the Trial-level half, and a `RunView` holds the Trials as
the very `TrialStory` objects `show` renders: the three surfaces cannot disagree about what
happened in a Trial because there is only one account of it.

The view model is a projection (D11). Every number about a Trial — a Span's offsets, its
depth, its duration, its tokens — comes from the Events through `project_spans` and
`metrics`, inside `trial_story`. This module adds only what the Events cannot know, read
from the Run's own files: who ran it and under which configuration (`run.json`), what Sync
said before the first Trial, the Scorecard with what did not happen, and the Scenarios that
never ran. `run.json` is read as plain JSON rather than as `RunRecord`, because that model
reaches the Adapter's and the Judge's configuration and this module must import no SDK: a
Report renders offline, in any process.

A rescored Run's Trials are told from the Trace of the Run it rescored (D34): `trial_story`
follows `traces_from` there, and the header carries `traces_from` so the Report says whose
Trace it drew. A Trial whose Trace cannot be found — a rescore whose source directory was
removed — still has its Scores, and the Report says it has no Trace rather than dropping it.

A Run that verified or refuted a Change record carries that record's story (ticket 28):
`run_view` finds the Target whose `runs/` holds the Run directory (`locate.holding_target`,
in the nearest Workspace above it) and reads its records for one whose `verification.run`
is this Run. A Run directory anywhere else — a copied fixture, an exported Run — is in no
Target, and nothing is read. The `report.html` a Run writes when it ends predates any close
and is never rewritten (a Run is not mutated, ADR-0005 §2), so it carries no story; the
Report as served, or one rendered after the close, does.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agentdiag.change.record import load_records
from agentdiag.evidence import ImporterSection
from agentdiag.report.story import ChangeRecordStory, change_record_story
from agentdiag.run.locate import (
    RUN_RECORD_FILE,
    SCORECARD_FILE,
    SCORES_FILE,
    TrialNotFound,
    holding_target,
    trial_dir,
)
from agentdiag.run.scorecard import Scorecard
from agentdiag.scenario.select import NotRun
from agentdiag.sync.compare import SyncSection
from agentdiag.sync.fingerprint import Fingerprint
from agentdiag.trace.show import TrialStory, score_views, trial_story
from agentdiag.types import DEFAULT_RUN_SOURCE
from agentdiag.workspace import TargetPaths, Workspace, WorkspaceError


class RunHeaderView(BaseModel):
    """What produced the Run, as the Report's first lines say it (ADR-0005 §3)."""

    run_id: str
    target: str | None
    source: str
    created_at: str
    environment: str | None
    adapter_kind: str | None
    side_effects: str | None
    target_backend: str | None
    judge_model: str | None
    judge_backend: str | None
    trials: int
    selection: str
    traces_from: str | None
    """A rescore's source (D34): the Run whose Traces this Run's Scores read."""

    importer: ImporterSection | None
    """An imported Run's evidence (ADR-0013 §5), said before the first Trial."""

    fingerprint: str | None
    """The id of the Fingerprint in force for the Run; None when it recorded none."""

    git_dirty: dict[str, bool] = Field(default_factory=dict)
    """Per repository `run.json` could read: whether its working tree was dirty."""

    agentdiag_version: str


class RunView(BaseModel):
    """One Run, whole: the header, Sync, the Scorecard, what did not run, every Trial.

    `sync` comes before the Scorecard because a Run that re-synced, or whose Connector read
    failed, says so before anything else (ADR-0008): the renderer draws it first.
    """

    header: RunHeaderView
    sync: SyncSection
    scorecard: Scorecard
    not_run: list[NotRun] = Field(default_factory=list)
    trials: list[TrialStory] = Field(default_factory=list)
    change_record: ChangeRecordStory | None = None
    """The story of the Change record this Run verified or refuted, drawn after the
    Scorecard; None for every other Run."""


def run_view(run_dir: Path) -> RunView:
    """The `RunView` of a finished Run directory: one that holds `run.json` and
    `scorecard.json` (ADR-0005 §2). Trials are in the Scorecard's order."""
    run_dir = Path(run_dir)
    record = _object(json.loads((run_dir / RUN_RECORD_FILE).read_text(encoding="utf-8")))
    scorecard = Scorecard.model_validate_json(
        (run_dir / SCORECARD_FILE).read_text(encoding="utf-8")
    )
    not_run = record.get("not_run")
    return RunView(
        header=_header(record, run_dir),
        sync=SyncSection.model_validate(record.get("sync") or scorecard.sync.model_dump()),
        scorecard=scorecard,
        not_run=(
            [NotRun.model_validate(entry) for entry in not_run]
            if isinstance(not_run, list)
            else list(scorecard.not_run)
        ),
        trials=[_trial(run_dir, line.id, line.trial) for line in scorecard.scenarios],
        change_record=verified_story(run_dir),
    )


def target_of_run(run_dir: Path) -> TargetPaths | None:
    """The Target whose `runs/` holds a Run directory, in the nearest Workspace above it;
    None for a Run directory in no Workspace, or in none of its Targets."""
    workspace = Workspace.holding(run_dir)
    if workspace is None:
        return None
    try:
        return holding_target(workspace.root, run_dir)
    except WorkspaceError:
        return None


def verified_story(run_dir: Path) -> ChangeRecordStory | None:
    """The story of the first record (by id) whose verification names this Run, when the
    Run sits in a Workspace; None otherwise."""
    target = target_of_run(run_dir)
    if target is None:
        return None
    run_id = Path(run_dir).name
    for _, record in load_records(target):
        if record.verification is not None and record.verification.run == run_id:
            return change_record_story(record, target)
    return None


def _header(record: Mapping[str, Any], run_dir: Path) -> RunHeaderView:
    adapter = _object(record.get("adapter"))
    judge = _object(record.get("judge"))
    importer = record.get("importer")
    return RunHeaderView(
        run_id=str(record.get("run_id") or run_dir.name),
        target=_text(record.get("target")),
        source=str(record.get("source") or DEFAULT_RUN_SOURCE),
        created_at=str(record.get("created_at") or ""),
        environment=_text(adapter.get("environment")),
        adapter_kind=_text(adapter.get("kind")),
        side_effects=_text(adapter.get("side_effects")),
        target_backend=_text(_object(adapter.get("backend")).get("kind")),
        judge_model=_text(judge.get("model")),
        judge_backend=_text(_object(judge.get("backend")).get("kind")),
        trials=int(record.get("trials") or 1),
        selection=str(_object(record.get("selection")).get("expression") or ""),
        traces_from=_text(record.get("traces_from")),
        importer=ImporterSection.model_validate(importer) if importer else None,
        fingerprint=_fingerprint_id(record.get("fingerprint")),
        git_dirty={
            repository: bool(state.get("dirty"))
            for repository, state in _object(record.get("git")).items()
            if isinstance(state, Mapping)
        },
        agentdiag_version=str(_object(record.get("agentdiag")).get("version") or ""),
    )


def _trial(run_dir: Path, scenario_id: str, trial: int) -> TrialStory:
    """The Trial's story, or its Scores alone when its Trace is nowhere to be found."""
    try:
        return trial_story(run_dir, scenario_id, trial)
    except TrialNotFound:
        story = TrialStory(run_id=run_dir.name, scenario_id=scenario_id, trial=trial)
        scores = trial_dir(run_dir, scenario_id, trial) / SCORES_FILE
        if scores.exists():
            story.scores = score_views(json.loads(scores.read_text(encoding="utf-8")))
        return story


def _fingerprint_id(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    return Fingerprint.model_validate(value).id


def _object(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = ["RunHeaderView", "RunView", "run_view", "target_of_run", "verified_story"]
