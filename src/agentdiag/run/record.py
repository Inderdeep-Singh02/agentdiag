"""`run.json`: everything that produced a Run, frozen mechanically (ADR-0005 §3, D29).

A Run is meant to be interpretable with no access to the files that produced it, and a
comparison between two Runs is only honest if the configuration each ran under is on
record. So this is a snapshot, not a pointer: the Manifest as read, the Adapter's own
description, the resolved package versions, and the git state of both repositories with a
dirty flag — because "the same commit" and "the same working tree" are different claims.

Git state is best-effort by design. A Target that is not in a repository is a normal case
(ADR-0005 §9), so every failure to read git is recorded as `null` rather than raising.

What changes with the moment a Run is recorded — the time, the git state, the installed
packages — is one `RunStamp`, read once. A caller that must reproduce a Run byte for byte
(the Run fixtures `scripts/record_fixtures.py --runs` writes) passes a fixed one; nothing
else does.
"""

from __future__ import annotations

import importlib.metadata
import subprocess
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

import agentdiag
from agentdiag.adapter import AdapterDescription
from agentdiag.eval.judged import JudgeConfiguration
from agentdiag.evidence import ImporterSection
from agentdiag.model.prices import PRICE_TABLE_VERSION
from agentdiag.run.manifest import Manifest
from agentdiag.scenario.models import EvalDeclaration, Scenario
from agentdiag.scenario.select import NotRun, Selection
from agentdiag.simulate.configuration import SimulatedUserConfiguration
from agentdiag.sync.compare import SyncSection
from agentdiag.sync.fingerprint import Fingerprint
from agentdiag.timestamps import iso_utc
from agentdiag.types import (
    DEFAULT_RUN_SOURCE,
    RunSource,
)
from agentdiag.workspace import TargetPaths, find_repository

RECORDED_PACKAGES = ("anthropic", "claude-agent-sdk", "pydantic", "typer", "pyyaml")
"""The dependencies whose version can change a Run's behaviour, recorded by name."""

GIT_TIMEOUT_SECONDS = 5.0
"""Short: a Run must not hang on a git command over a slow or broken repository."""

FROM_MANIFEST = "from_manifest"
"""The key an effective threshold carries in `run.json` when the Manifest supplied it."""

Defaulted = Collection[tuple[str, str, str | None]]
"""The declarations whose threshold the Manifest supplied, as (Scenario id, Eval, id)."""


class GitState(BaseModel):
    """One repository at the moment of the Run."""

    repository: str
    """The absolute path of the repository root that was found.

    Named rather than assumed: a Target inside agentdiag's own checkout — the shipped
    `examples/toy` is one — walks up to the same `.git` as agentdiag does, and a reader
    comparing two commits deserves to see that they came from one repository rather than
    two that happen to agree."""

    commit: str
    dirty: bool
    """Whether the working tree differed from the commit. A Run from a dirty tree is not
    reproducible from that commit alone, and the Scorecard's reader deserves to know."""


class GitSection(BaseModel):
    """Both repositories: agentdiag's own, and the Target's."""

    agentdiag: GitState | None = None
    target: GitState | None = None


class AgentdiagSection(BaseModel):
    """Which agentdiag, and which of the packages it drives models and files with."""

    version: str
    packages: dict[str, str] = Field(default_factory=dict)


class SelectionSection(BaseModel):
    """What the caller asked to run, normalised, and the expression it amounts to (D31)."""

    scenario: list[str] = Field(default_factory=list)
    tag: list[str] = Field(default_factory=list)
    suite: list[str] = Field(default_factory=list)
    expression: str

    @classmethod
    def of(cls, selection: Selection) -> SelectionSection:
        """The section for a Selection: its normalised values and its expression."""
        return cls(
            scenario=list(selection.scenario),
            tag=list(selection.tag),
            suite=list(selection.suite),
            expression=selection.expression(),
        )


class EvalSummary(BaseModel):
    """One effective declaration of a Scenario, and whether its Suite put it there."""

    eval: str
    id: str | None = None
    inherited: bool = False
    """True for a Suite-level declaration the Scenario inherited (phase-5 decision 17), so
    a Report can say where a Score came from without the Suite file."""

    params: dict[str, Any] = Field(default_factory=dict)
    threshold: dict[str, Any] | None = None
    """As the declaration was effective in this Run: what `compare` diffs, so an edited
    threshold or parameter is a visible configuration difference (ticket 08). A threshold
    the Manifest's `eval_parameters.latency` supplied carries `from_manifest: true`
    (phase-6 decision 17), so moving a default and writing one differ in a comparison."""

    @classmethod
    def of(cls, declaration: EvalDeclaration, *, from_manifest: bool = False) -> EvalSummary:
        threshold = declaration.threshold
        if from_manifest and threshold is not None:
            threshold = {**threshold, FROM_MANIFEST: True}
        return cls(
            eval=declaration.eval,
            id=declaration.id,
            inherited=declaration.inherited,
            params=dict(declaration.params),
            threshold=threshold,
        )


class ScenarioSummary(BaseModel):
    """One selected Scenario as `run.json` names it, without repeating its Turns."""

    id: str
    title: str
    tags: list[str] = Field(default_factory=list)
    evals: list[EvalSummary] = Field(default_factory=list)
    provenance: str | None = None
    """Where the Scenario came from, so a Report can say so without the Suite (D18)."""

    notes: str | None = None
    ground_truth: Any = None
    """As authored, free: what the right answer was, for the Report and the Judge."""

    continues: str | None = None
    """The Scenario whose Adapter session this one resumed (decision 13)."""

    simulated: bool = False
    """A Simulated User played its `simulate` Turn (amended decision 52), so a reader of the
    record — `compare` among them — knows which Scenarios it drove without the Suite."""

    @classmethod
    def of(cls, scenario: Scenario, defaulted: Defaulted = ()) -> ScenarioSummary:
        """A selected Scenario as `run.json` names it, without repeating its Turns;
        `defaulted` names the declarations whose threshold came from the Manifest."""
        return cls(
            id=scenario.id,
            title=scenario.title,
            tags=list(scenario.tags),
            evals=[
                EvalSummary.of(
                    declaration,
                    from_manifest=(scenario.id, declaration.eval, declaration.id) in defaulted,
                )
                for declaration in scenario.evals
            ],
            provenance=scenario.provenance,
            notes=scenario.notes,
            ground_truth=scenario.ground_truth,
            continues=scenario.continues,
            simulated=scenario.simulated,
        )


class PriceTableSection(BaseModel):
    """Which price table the Spans of this Run were costed under (ADR-0006 §4, decision 4).

    The costs themselves are on the Spans, written at capture; this names the table, so a
    comparison of two Runs can say they were priced differently rather than imply that the
    Target's cost moved."""

    version: str


class RunRecord(BaseModel):
    """The whole of `run.json` (ADR-0005 §3)."""

    run_id: str
    created_at: str
    """ISO-8601 in UTC, so two Runs sort by name and by field the same way."""

    agentdiag: AgentdiagSection
    manifest: dict[str, Any]
    target: str | None = None
    """The slug of the Target the Run belongs to (ADR-0013 §1, phase-6 decision 6); None in
    a Run recorded before a Workspace held several Targets, which the Index then names by
    `manifest.target.name`."""

    source: RunSource = DEFAULT_RUN_SOURCE
    """`run` for a Run agentdiag drove; `imported` for one an Importer made (ADR-0013 §5)."""

    fingerprint: Fingerprint | None = None
    """The Fingerprint in force for the Run: the one found, or the one a re-sync rebuilt
    (phase-6 decision 9). A prior Run keeps its own, never rewritten (ADR-0007 §2)."""

    sync: SyncSection
    adapter: AdapterDescription
    judge: JudgeConfiguration | None = None
    """Model, effort and each judged Eval's full prompt text with its Fingerprint
    (ADR-0005 §3), and the Simulated User reviewer's. Null when the selection declared no
    judged Eval and no `simulate` Turn."""

    simulated_user: SimulatedUserConfiguration | None = None
    """The Simulated User the adaptive Trials were driven with, and its Fingerprint (D27,
    phase-5 decision 52). Null when no selected Scenario has a `simulate` Turn; a rescore
    carries its source's, since its Traces were driven under it."""

    price_table: PriceTableSection | None = None
    """The price table the Spans' costs were taken from; None for a Run recorded before
    agentdiag stamped cost (Phase 4), which still reads."""

    trials: int = 1
    """Trials per Scenario (ADR-0005 §7); a rescore carries its source's."""

    selection: SelectionSection
    git: GitSection
    scenarios: list[ScenarioSummary] = Field(default_factory=list)
    not_run: list[NotRun] = Field(default_factory=list)
    """Every Scenario in the loaded Suites that did not run, with its reason (D31)."""

    traces_from: str | None = None
    """A rescore's source (D34, ADR-0005 §4): the id of the Run whose Traces this Run's
    Scores read. Its Trials hold no `trace.jsonl`; `run.locate` finds the Trace there."""

    importer: ImporterSection | None = Field(default=None, exclude_if=lambda value: value is None)
    """An imported Run's evidence (phase-6 decisions 36, 41); a rescore carries its source's.
    Left out of `run.json` when None, so a Run agentdiag drove is written byte for byte as
    before the field existed."""


class RunStamp(BaseModel):
    """What a Run records about the moment it was made: when, which agentdiag, which git.

    Read once per Run by `now`, or passed in fixed so a Run reproduces byte for byte.
    """

    created_at: datetime
    agentdiag: AgentdiagSection
    git: GitSection

    @classmethod
    def now(cls, start: Path) -> RunStamp:
        """The stamp of a Run starting now; the Target's git state is that of the repository
        holding `start`, its Target directory."""
        return cls(
            created_at=datetime.now(UTC), agentdiag=agentdiag_section(), git=git_section(start)
        )


def package_versions() -> dict[str, str]:
    """The installed version of each recorded dependency; absent ones are left out."""
    versions: dict[str, str] = {}
    for name in RECORDED_PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:  # pragma: no cover - always installed
            continue
    return versions


def agentdiag_section() -> AgentdiagSection:
    """Which agentdiag produced this Run, and what it was built on."""
    return AgentdiagSection(version=agentdiag.__version__, packages=package_versions())


def git_state(start: Path) -> GitState | None:
    """The commit and dirty flag of the repository `start` sits in, if any.

    Best-effort: no repository, no git binary, a timeout or a non-zero exit all give None,
    because a Target outside version control is a supported case, not a failure.
    """
    repository = find_repository(start)
    if repository is None:
        return None
    commit = _git(repository, "rev-parse", "HEAD")
    if commit is None:
        return None
    porcelain = _git(repository, "status", "--porcelain")
    if porcelain is None:
        return None
    return GitState(
        repository=str(repository),
        commit=commit.strip(),
        dirty=bool(porcelain.strip()),
    )


def _git(repository: Path, *arguments: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def git_section(root: Path) -> GitSection:
    """Both repositories: agentdiag's, found from its own module, and the Target's."""
    return GitSection(
        agentdiag=git_state(Path(agentdiag.__file__).parent),
        target=git_state(root),
    )


def build_run_record(
    *,
    run_id: str,
    stamp: RunStamp,
    target: TargetPaths,
    manifest: Manifest,
    selection: SelectionSection,
    scenarios: list[ScenarioSummary],
    not_run: list[NotRun],
    judge: JudgeConfiguration | None = None,
    adapter: AdapterDescription | None = None,
    simulated_user: SimulatedUserConfiguration | None = None,
    trials: int = 1,
    source: RunRecord | None = None,
    sync: SyncSection | None = None,
    fingerprint: Fingerprint | None = None,
    importer: ImporterSection | None = None,
) -> RunRecord:
    """Freeze the configuration a Run is about to execute under: the one `run.json` builder.

    With `source`, the Run is a rescore of it (D34, ADR-0005 §4): the Manifest, the Judge,
    the price table and agentdiag's own state are current, because re-judging under them
    is the point; the Adapter, the Fingerprint, the Sync result, the Simulated User,
    `trials` and the Target's git state are the source's, because its Traces were made
    under them; and `traces_from` names the Run that holds those Traces, the source's own
    source when it is itself a rescore. `target` is the Target the Run is written under; a
    rescore is written beside its source, so `target` is the Target holding
    both, and it records its source's `source`, since its Traces are the source's.
    `sync` and `fingerprint` are what preflight checked (phase-6 decision 14); a rescore
    ignores both and carries its source's. `importer` makes the Run one of `source:
    imported` (decision 36); a rescore carries its source's.
    """
    if source is None:
        if adapter is None:
            raise ValueError("a Run that is not a rescore records the Adapter it drove")
        return RunRecord(
            run_id=run_id,
            created_at=iso_utc(stamp.created_at),
            agentdiag=stamp.agentdiag,
            manifest=manifest.model_dump(mode="json"),
            target=target.slug,
            source="imported" if importer is not None else DEFAULT_RUN_SOURCE,
            fingerprint=fingerprint,
            sync=sync or SyncSection(status="not_checked", reason="no_fingerprint"),
            adapter=adapter,
            judge=judge,
            simulated_user=simulated_user,
            price_table=PriceTableSection(version=PRICE_TABLE_VERSION),
            trials=trials,
            selection=selection,
            git=stamp.git,
            scenarios=scenarios,
            not_run=not_run,
            importer=importer,
        )
    return RunRecord(
        run_id=run_id,
        created_at=iso_utc(stamp.created_at),
        agentdiag=stamp.agentdiag,
        manifest=manifest.model_dump(mode="json"),
        target=target.slug,
        source=source.source,
        fingerprint=source.fingerprint,
        sync=source.sync,
        adapter=source.adapter,
        judge=judge,
        simulated_user=source.simulated_user,
        price_table=PriceTableSection(version=PRICE_TABLE_VERSION),
        trials=source.trials,
        selection=selection,
        git=GitSection(agentdiag=stamp.git.agentdiag, target=source.git.target),
        scenarios=scenarios,
        not_run=not_run,
        traces_from=source.traces_from or source.run_id,
        importer=source.importer,
    )


__all__ = [
    "FROM_MANIFEST",
    "GIT_TIMEOUT_SECONDS",
    "RECORDED_PACKAGES",
    "AgentdiagSection",
    "Defaulted",
    "EvalSummary",
    "GitSection",
    "GitState",
    "ImporterSection",
    "NotRun",
    "PriceTableSection",
    "RunRecord",
    "RunStamp",
    "ScenarioSummary",
    "SelectionSection",
    "SyncSection",
    "agentdiag_section",
    "build_run_record",
    "find_repository",
    "git_section",
    "git_state",
    "iso_utc",
    "package_versions",
]
