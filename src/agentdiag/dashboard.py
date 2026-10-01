"""The Dashboard: every Target's Sync state, last Run, Score trend and open Change records.

ADR-0013 §3 and phase-7 decision 24: one row per Registry entry, read from the Registry and
the Index and never a source of truth. `agentdiag dashboard` prints it as a padded table;
`GET /api/dashboard` serves the same `DashboardView` to the UI's first screen.

**It only reads.** The Registry reads the Manifests, each Target's `fingerprint.json`, its
Sync breaks and Push records; the Index gives each Run's Verdict counts and pass rate and
each Change record's status, since `run` records a Run and every `change` write records its
file (`index.record_change`). Nothing is written, the Index included, and no Trace is
opened: the Run directories are listed by name, and only one the Index lacks is read as far
as its `run.json` and `scorecard.json`, to say whether the Index is behind it.

**The Index is a problem line, never a crash.** A missing Index (when there is a Run or a
Change record it would hold), one at an old schema version or corrupt, or one that lacks a
Run `index rebuild` would add or holds one whose directory is gone, is a problem of the
view naming `agentdiag index rebuild`; a Run directory that cannot be indexed, or a Run id
under two Targets, is its own problem line in the words `list` warns with, because a
rebuild would not settle it. An Index another process holds past the busy timeout says to
try again. When the Index could not be read `index_read` is False and the rows say so in
words; when it is behind, the rows are what it holds.

**Words, not zeros** (ADR-0005 §8). A Target with no Run says `no Run yet`, one with no
Fingerprint `not synced yet`; a pass rate is never shown without the counts of
`incomplete`, `unverifiable` and `invalid` beside it, at every point of the trend. The
trend is the last `trend` Runs agentdiag drove, oldest first, a Run with no pass rate kept
in place as a gap: a Run of `source: imported` or a rescore (one with `traces_from`) may be
the last Run, and is listed as such, but never trended, since neither says anything new
about the Target's behaviour under its Suites.

Offline: it imports the Registry and the Index, neither of which reaches a model.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from agentdiag.change.record import RECORD_SUFFIX, records_dir
from agentdiag.eval.score import render_counts
from agentdiag.registry import RegistryEntry, registry, render_sync
from agentdiag.run.index import (
    REBUILD_COMMAND,
    Index,
    IndexCorrupt,
    RunListing,
    VerdictCounts,
    run_census,
)
from agentdiag.run.locate import index_path, run_directories
from agentdiag.sync.fingerprint import NONE_SHOWN
from agentdiag.table import render_table
from agentdiag.workspace import TargetPaths, Workspace

DEFAULT_TREND = 10
"""How many driven Runs the trend covers unless `--trend` says otherwise."""

NO_RUN = "no Run yet"
NOT_READ = "Index not read"
NO_DRIVEN_RUN = "no Run agentdiag drove"
NOT_JUDGED = "not judged"
"""What a Run with no Score at all shows instead of five zeros: an imported Run before a
`rescore` judges it, say."""


class TrendPoint(BaseModel):
    """One Run of the trend: its pass rate, None when it has none (a gap, kept in place),
    and its Verdict counts, shown beside the rate (ADR-0005 §8)."""

    run_id: str
    pass_rate: float | None = None
    counts: VerdictCounts


class DashboardRow(BaseModel):
    """One Target as the Dashboard shows it (phase-7 decision 24)."""

    entry: RegistryEntry
    """The Target's Registry entry: its Family, environments, Sync state and problems."""

    last_run: RunListing | None = None
    """The newest Run in the Index, of any source; None when there is none or the Index
    was not read (`DashboardView.index_read` says which)."""

    trend: list[TrendPoint] = Field(default_factory=list)
    """The last `trend` Runs of `source: run` with no `traces_from`, oldest first."""

    open_changes: int = 0
    """How many Change records are `open`, `proposed` or `pushed`."""

    open_change_ids: list[str] = Field(default_factory=list)
    """Their ids, by id: the UI links each to its Flow view."""


class DashboardView(BaseModel):
    """The Workspace-wide view: one row per Target, by slug."""

    rows: list[DashboardRow] = Field(default_factory=list)
    generated_from: str
    """The Workspace root the view was read from."""

    trend: int
    """How many driven Runs each row's trend covers at most."""

    index_read: bool = True
    """Whether the Index answered; when False, every row's Run and Change record cells are
    unknown and `problems` says why."""

    problems: list[str] = Field(default_factory=list)
    """What is wrong Workspace-wide: the Index missing, unreadable or behind the files."""


def dashboard(workspace: Workspace, *, trend: int = DEFAULT_TREND) -> DashboardView:
    """The Dashboard of `workspace`: every Registry entry with its last Run, the trend over
    its last `trend` driven Runs and its open Change records. `ValueError` for a `trend`
    below 1."""
    if trend < 1:
        raise ValueError(f"the trend must cover at least 1 Run; got {trend}")
    read = _read_index(workspace)
    rows = []
    for entry in registry(workspace):
        runs = [run for run in read.runs if run.target == entry.slug]
        driven = [run for run in runs if run.source == "run" and run.traces_from is None]
        opened = read.open_changes.get(entry.slug, [])
        rows.append(
            DashboardRow(
                entry=entry,
                last_run=runs[0] if runs else None,
                trend=[
                    TrendPoint(run_id=run.run_id, pass_rate=run.pass_rate, counts=run.counts)
                    for run in reversed(driven[:trend])
                ],
                open_changes=len(opened),
                open_change_ids=opened,
            )
        )
    return DashboardView(
        rows=rows,
        generated_from=str(workspace.root),
        trend=trend,
        index_read=read.answered,
        problems=read.problems,
    )


class _IndexRead(BaseModel):
    answered: bool
    runs: list[RunListing] = Field(default_factory=list)
    """Newest first."""

    open_changes: dict[str, list[str]] = Field(default_factory=dict)
    problems: list[str] = Field(default_factory=list)


def _read_index(workspace: Workspace) -> _IndexRead:
    """The Index's Runs and open Change record ids by Target, and what is wrong with it;
    `answered=False` with the problem when it could not be read."""
    root = workspace.root
    path = index_path(root)
    if not path.exists():
        if run_directories(root) or any(_has_records(target) for target in workspace.targets()):
            return _IndexRead(
                answered=False, problems=[f"there is no Index at {path}; run {REBUILD_COMMAND}"]
            )
        return _IndexRead(answered=True)
    try:
        index = Index.open(root)
    except IndexCorrupt as corrupt:
        return _IndexRead(answered=False, problems=[str(corrupt)])
    try:
        runs = index.listing()
        open_changes = index.open_change_ids_by_target()
        held = index.run_ids()
    except IndexCorrupt as corrupt:
        return _IndexRead(answered=False, problems=[str(corrupt)])
    finally:
        index.close()
    return _IndexRead(
        answered=True, runs=runs, open_changes=open_changes, problems=_behind(root, path, held)
    )


def _behind(root: Path, path: Path, held: set[str]) -> list[str]:
    """One problem line when the Index lacks a Run a rebuild would add or holds one whose
    directory is gone; then one per directory a rebuild would skip, as `list` warns."""
    census = run_census(root, held)
    said = []
    if census.addable:
        said.append(f"{len(census.addable)} {_runs(len(census.addable))} it does not hold")
    if census.gone:
        said.append(f"{len(census.gone)} {_runs(len(census.gone))} whose directory is gone")
    if not said:
        return census.skipped
    return [
        f"the Index at {path} is behind the Run directories ({', '.join(said)}); "
        f"run {REBUILD_COMMAND}",
        *census.skipped,
    ]


def _runs(count: int) -> str:
    return "Run" if count == 1 else "Runs"


def _has_records(target: TargetPaths) -> bool:
    directory = records_dir(target)
    return directory.is_dir() and any(directory.glob(f"*{RECORD_SUFFIX}"))


# --- the terminal ---

COLUMNS = (
    "target",
    "family",
    "sync",
    "last run",
    "created",
    "verdicts",
    "trend (incomplete/unverifiable/invalid)",
    "changes",
)


def render_dashboard(view: DashboardView) -> str:
    """The Dashboard as a table under a header line, one line per Target, then one
    `problem:` line per problem, the Workspace's first. Plain padded text
    (`table.render_table`), so the bytes are the same at any terminal width."""
    if not view.rows:
        return "no Targets in this Workspace; `agentdiag init --target <slug>` adds one"
    lines = render_table([list(COLUMNS), *(_cells(row, view.index_read) for row in view.rows)])
    lines += [f"problem: {problem}" for problem in view.problems]
    lines += [
        f"problem: {row.entry.slug}: {problem}"
        for row in view.rows
        for problem in row.entry.problems
    ]
    return "\n".join(lines)


def _cells(row: DashboardRow, index_read: bool) -> list[str]:
    entry = row.entry
    last = row.last_run
    if not index_read:
        run_cells = [NOT_READ, NONE_SHOWN, NONE_SHOWN, NONE_SHOWN, NONE_SHOWN]
    elif last is None:
        run_cells = [NO_RUN, NONE_SHOWN, NONE_SHOWN, NONE_SHOWN, _changes(row)]
    else:
        run_cells = [
            _run_shown(last),
            last.created_at,
            _verdicts(last.counts),
            _trend(row),
            _changes(row),
        ]
    return [
        entry.slug,
        entry.family or NONE_SHOWN,
        render_sync(entry.sync, in_words=True),
        *run_cells,
    ]


def _run_shown(run: RunListing) -> str:
    """The Run id, and what kind of Run it is when it is not one agentdiag drove."""
    if run.source == "imported":
        return f"{run.run_id} (imported)"
    if run.traces_from is not None:
        return f"{run.run_id} (rescored)"
    return run.run_id


def _verdicts(counts: VerdictCounts) -> str:
    """Every Verdict counted, zeros included (ADR-0005 §8), or `not judged` when there
    is no Score to count."""
    tally = counts.as_mapping()
    return render_counts(tally) if any(tally.values()) else NOT_JUDGED


def _trend(row: DashboardRow) -> str:
    """`88% (0/0/0) - (0/2/0) 98% (6/0/0)`: each Run's rate, `-` for a gap, oldest first,
    with its incomplete, unverifiable and invalid counts beside it."""
    if not row.trend:
        return NO_DRIVEN_RUN
    return " ".join(
        f"{percent(point.pass_rate) if point.pass_rate is not None else NONE_SHOWN} "
        f"({point.counts.incomplete}/{point.counts.unverifiable}/{point.counts.invalid})"
        for point in row.trend
    )


def percent(rate: float) -> str:
    """A rate as whole percent, halves rounded up, as the page's `pct` rounds it."""
    return f"{int(rate * 100 + 0.5)}%"


def _changes(row: DashboardRow) -> str:
    return f"{row.open_changes} open" if row.open_changes else "none open"


__all__ = [
    "DEFAULT_TREND",
    "DashboardRow",
    "DashboardView",
    "TrendPoint",
    "dashboard",
    "percent",
    "render_dashboard",
]
