"""Seam 1: the Dashboard (ticket 29, phase-7 decision 24).

The Workspace is three Targets built from committed files, so every timestamp is fixed and
the table can be pinned byte for byte: the toy with every Run fixture (six Runs agentdiag
drove, two rescores of `BASE`), one imported Run made from a copy of `BASE`, every Change
record fixture and a Push record, but no Fingerprint; the help desk with its Fingerprint and
no Run; the order desk with neither. The Index is rebuilt from those files, as `index
rebuild` would.

The Dashboard reads the Registry and the Index only: the trend leaves the imported Run and
the rescores out while the imported Run is still the last Run; the open Change records are
counted from the Index's `changes` table, which every `change` write keeps current; a
Target with no Run or no Fingerprint says so in words; a missing, corrupt or stale Index is
a problem line naming `index rebuild`, a busy one says to try again, and nothing writes.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentdiag.change.lifecycle import record_connector_push
from agentdiag.cli import app
from agentdiag.dashboard import DashboardView, dashboard, percent, render_dashboard
from agentdiag.run.index import rebuild
from agentdiag.run.locate import RUN_RECORD_FILE, SCORECARD_FILE, index_path
from agentdiag.workspace import TargetPaths, Workspace
from tests.change_fixtures import BASE, RUNS, SLUG, toy_with_records

REPO = Path(__file__).resolve().parents[1]
EXAMPLE_TARGETS = REPO / "examples" / "workspace" / ".agentdiag" / "targets"
IMPORTED = "20260923T100800Z-impt"
TRI3 = "20260923T100600Z-tri3"
JDGE = "20260923T100400Z-jdge"
DELIVERED = "delivered-order-cannot-be-cancelled"
OPEN_RECORDS = ["20260923-a-proposed-record", "20260923-a-pushed-record", "20260923-an-open-record"]

runner = CliRunner()


def imported_copy(target: TargetPaths) -> None:
    """`BASE` copied as a Run of `source: imported`, made after every fixture Run: the
    Index reads only `run.json` and `scorecard.json`, so the copy stands for one `import`
    wrote without a live timestamp. Only `run.json` is rewritten: the copied
    `scorecard.json` still names `BASE` as its Run, which nothing here reads."""
    made = target.runs / IMPORTED
    shutil.copytree(RUNS / BASE, made)
    record = json.loads((made / RUN_RECORD_FILE).read_text(encoding="utf-8"))
    record.update(run_id=IMPORTED, created_at="2026-09-23T10:08:00Z", source="imported")
    (made / RUN_RECORD_FILE).write_text(json.dumps(record, indent=2), encoding="utf-8")


def three_targets(tmp_path: Path) -> Workspace:
    target = toy_with_records(tmp_path)
    for run in sorted(RUNS.iterdir()):
        if run.is_dir() and not (target.runs / run.name).exists():
            shutil.copytree(run, target.runs / run.name)
    imported_copy(target)
    for slug in ("help-desk", "order-desk"):
        shutil.copytree(
            EXAMPLE_TARGETS / slug,
            target.directory.parent / slug,
            ignore=shutil.ignore_patterns("runs", "platform", "restore-points"),
        )
    rebuild(target.root)
    return Workspace.find(target.root)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return three_targets(tmp_path)


PINNED = (REPO / "tests" / "fixtures" / "dashboard" / "three-targets.txt").read_text(
    encoding="utf-8"
)
"""The table over `three_targets`, with its trailing newline: what `agentdiag dashboard`
prints."""


def slugs(view: DashboardView) -> list[str]:
    return [row.entry.slug for row in view.rows]


def index_bytes(workspace: Workspace) -> bytes:
    return index_path(workspace.root).read_bytes()


def test_one_row_per_target_with_its_last_run_trend_and_open_records(
    workspace: Workspace,
) -> None:
    view = dashboard(workspace)

    assert slugs(view) == ["help-desk", "order-desk", SLUG]
    assert view.index_read and view.problems == []
    assert view.trend == 10 and view.generated_from == str(workspace.root)
    toy = view.rows[2]
    assert toy.last_run is not None and toy.last_run.run_id == IMPORTED
    assert toy.last_run.source == "imported"
    trended = [point.run_id for point in toy.trend]
    assert trended == [
        BASE,
        "20260923T100100Z-swap",
        "20260923T100200Z-prmt",
        "20260923T100300Z-part",
        JDGE,
        TRI3,
    ]
    assert IMPORTED not in trended
    assert "20260923T100500Z-resc" not in trended and "20260923T100700Z-thrs" not in trended
    assert toy.trend[-1].counts.incomplete == 6 and toy.trend[0].counts.fail == 2
    assert toy.open_changes == 3 and toy.open_change_ids == OPEN_RECORDS
    assert toy.entry.sync is not None and toy.entry.sync.status == "not_checked"
    assert toy.entry.sync.last_push == "2026-09-23T10:01:30Z"


def test_a_target_with_no_run_or_no_fingerprint_says_so_in_words(workspace: Workspace) -> None:
    view = dashboard(workspace)
    help_desk, order_desk, _ = view.rows

    assert help_desk.last_run is None and help_desk.trend == []
    assert help_desk.open_changes == 0
    assert help_desk.entry.sync is not None and help_desk.entry.sync.status == "held"
    assert help_desk.entry.sync.environment == "local"
    assert order_desk.entry.sync is not None and order_desk.entry.sync.status == "not_checked"
    assert order_desk.entry.sync.environment is None
    rendered = render_dashboard(view).splitlines()
    assert "no Run yet" in rendered[1] and "held (local)" in rendered[1]
    assert "not synced yet" in rendered[2] and "no Run yet" in rendered[2]
    for line in rendered[1:3]:
        assert " 0 " not in line and "0%" not in line


def test_the_table_is_pinned_byte_for_byte(workspace: Workspace) -> None:
    assert render_dashboard(dashboard(workspace)) + "\n" == PINNED


def test_reading_the_dashboard_writes_nothing(workspace: Workspace) -> None:
    before = index_bytes(workspace)

    dashboard(workspace)

    assert index_bytes(workspace) == before


def test_a_narrower_trend_keeps_the_newest_driven_runs(workspace: Workspace) -> None:
    toy = dashboard(workspace, trend=2).rows[2]

    assert [point.run_id for point in toy.trend] == [JDGE, TRI3]
    with pytest.raises(ValueError, match="at least 1"):
        dashboard(workspace, trend=0)


def test_a_driven_run_with_no_pass_rate_is_a_gap_in_place_with_its_counts(
    tmp_path: Path,
) -> None:
    target = toy_with_records(tmp_path)
    scorecard = target.runs / BASE / SCORECARD_FILE
    card = json.loads(scorecard.read_text(encoding="utf-8"))
    card["pass_rate"] = None
    scorecard.write_text(json.dumps(card), encoding="utf-8")
    rebuild(target.root)

    view = dashboard(Workspace.find(target.root))

    (row,) = view.rows
    assert (row.trend[0].run_id, row.trend[0].pass_rate) == (BASE, None)
    assert row.trend[0].counts.fail == 2
    assert "- (0/0/0) 82% (0/0/0)" in render_dashboard(view)


def test_a_last_run_with_no_score_says_not_judged_never_five_zeros(tmp_path: Path) -> None:
    """An imported Run before a `rescore` judges it: its Scorecard counts nothing."""
    target = toy_with_records(tmp_path)
    imported_copy(target)
    scorecard = target.runs / IMPORTED / SCORECARD_FILE
    card = json.loads(scorecard.read_text(encoding="utf-8"))
    card.update(counts={}, pass_rate=None)
    scorecard.write_text(json.dumps(card), encoding="utf-8")
    rebuild(target.root)

    view = dashboard(Workspace.find(target.root))

    (row,) = view.rows
    assert row.last_run is not None and row.last_run.run_id == IMPORTED
    line = render_dashboard(view).splitlines()[1]
    assert "not judged" in line
    assert "pass 0" not in line and "invalid 0" not in line


def test_a_target_with_only_imported_and_rescored_runs_has_no_trend(tmp_path: Path) -> None:
    target = toy_with_records(tmp_path)
    for run in (BASE, "20260923T100200Z-prmt"):
        shutil.rmtree(target.runs / run)
    imported_copy(target)
    rebuild(target.root)

    view = dashboard(Workspace.find(target.root))

    (row,) = view.rows
    assert row.last_run is not None and row.last_run.run_id == IMPORTED
    assert row.trend == []
    assert "no Run agentdiag drove" in render_dashboard(view)


def test_a_missing_index_is_a_problem_line_naming_the_rebuild(workspace: Workspace) -> None:
    index_path(workspace.root).unlink()

    view = dashboard(workspace)

    assert not view.index_read
    (problem,) = view.problems
    assert "no Index" in problem and "agentdiag index rebuild" in problem
    assert all(row.last_run is None and row.open_changes == 0 for row in view.rows)
    rendered = render_dashboard(view)
    assert "Index not read" in rendered and "no Run yet" not in rendered
    assert "none open" not in rendered
    assert rendered.splitlines()[-1] == f"problem: {problem}"
    assert not index_path(workspace.root).exists()


def test_a_corrupt_index_is_a_problem_line_too(workspace: Workspace) -> None:
    index_path(workspace.root).write_bytes(b"not a database")

    view = dashboard(workspace)

    assert not view.index_read
    (problem,) = view.problems
    assert problem.startswith("the Index at ") and "agentdiag index rebuild" in problem


def test_an_index_another_process_holds_says_busy_not_rebuild(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("agentdiag.run.index.BUSY_TIMEOUT_S", 0.1)
    holder = sqlite3.connect(index_path(workspace.root))
    holder.execute("BEGIN EXCLUSIVE")
    try:
        view = dashboard(workspace)
    finally:
        holder.rollback()
        holder.close()

    assert not view.index_read
    (problem,) = view.problems
    assert "busy" in problem and "try again" in problem
    assert "rebuild" not in problem


def test_an_index_behind_the_run_directories_says_so_and_shows_what_it_holds(
    workspace: Workspace,
) -> None:
    toy = workspace.resolve(SLUG)
    shutil.rmtree(toy.runs / TRI3)
    late = "20260923T100900Z-late"
    shutil.copytree(RUNS / TRI3, toy.runs / late)
    record_file = toy.runs / late / RUN_RECORD_FILE
    record = json.loads(record_file.read_text(encoding="utf-8"))
    record.update(run_id=late)
    record_file.write_text(json.dumps(record), encoding="utf-8")

    view = dashboard(workspace)

    assert view.index_read
    (problem,) = view.problems
    assert "1 Run it does not hold" in problem
    assert "1 Run whose directory is gone" in problem
    assert "agentdiag index rebuild" in problem
    assert view.rows[2].last_run is not None and view.rows[2].last_run.run_id == IMPORTED


def test_a_run_a_rebuild_would_skip_is_its_own_problem_never_behind(
    workspace: Workspace,
) -> None:
    """After a rebuild, an unreadable directory and a Run id under two Targets are still
    not in the Index: each is its own problem line, and the Index is not behind."""
    toy = workspace.resolve(SLUG)
    broken = toy.runs / "20260923T100900Z-bad0"
    shutil.copytree(RUNS / TRI3, broken)
    (broken / RUN_RECORD_FILE).write_text("{not json", encoding="utf-8")
    twice = workspace.resolve("help-desk").runs / JDGE
    shutil.copytree(RUNS / JDGE, twice)
    rebuild(workspace.root)

    view = dashboard(workspace)

    assert view.index_read
    assert not any("behind" in problem for problem in view.problems)
    assert any(
        problem.startswith(f"skipped {broken}: no readable {RUN_RECORD_FILE}")
        for problem in view.problems
    )
    assert any(
        problem.startswith(f"skipped Run {JDGE}: it is under more than one Target")
        for problem in view.problems
    )


def test_a_workspace_with_nothing_to_index_needs_no_index(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    shutil.copytree(
        REPO / "examples" / "workspace",
        root,
        ignore=shutil.ignore_patterns("runs", "platform", "restore-points", "index.sqlite"),
    )

    view = dashboard(Workspace.find(root))

    assert view.index_read and view.problems == []
    assert [row.open_changes for row in view.rows] == [0, 0]
    assert not index_path(root).exists()


def test_percent_rounds_halves_up_as_the_page_does() -> None:
    assert [percent(rate) for rate in (0.0, 0.125, 0.8823529, 0.995, 1.0)] == [
        "0%",
        "13%",
        "88%",
        "100%",
        "100%",
    ]


# --- every `change` write keeps the Index current ---


def change(workspace: Workspace, *arguments: str) -> Any:
    return runner.invoke(
        app, ["change", *arguments, "--root", str(workspace.root), "--target", SLUG]
    )


def test_a_record_opened_then_closed_is_counted_with_no_rebuild(workspace: Workspace) -> None:
    opened = change(
        workspace,
        "open",
        "--from",
        f"{BASE}/{DELIVERED}/1",
        "--layer",
        "rules",
        "--title",
        "one more",
    )
    assert opened.exit_code == 0, opened.output
    record_id = opened.stdout.split()[1]

    after_open = dashboard(workspace).rows[2]
    closed = change(workspace, "close", record_id, "--wontfix", "--why", "by design")
    after_close = dashboard(workspace).rows[2]

    assert after_open.open_changes == 4 and record_id in after_open.open_change_ids
    assert closed.exit_code == 0, closed.output
    assert after_close.open_changes == 3 and record_id not in after_close.open_change_ids
    assert "warning" not in opened.stderr + closed.stderr


def test_a_push_that_moves_a_record_is_in_the_index(workspace: Workspace) -> None:
    toy = workspace.resolve(SLUG)

    record_connector_push(
        toy,
        "20260923-a-proposed-record",
        environment="local",
        at="2026-09-23T10:09:00Z",
        push_record="pushes/20260923T100900Z-local.json",
        fingerprint_before=None,
        fingerprint_after=None,
    )

    with sqlite3.connect(index_path(workspace.root)) as connection:
        statuses = dict(
            connection.execute("SELECT id, status FROM changes WHERE target = ?", [SLUG])
        )
    assert statuses["20260923-a-proposed-record"] == "pushed"
    assert dashboard(workspace).rows[2].open_changes == 3


def test_a_change_write_with_no_index_creates_none_and_warns_nothing(tmp_path: Path) -> None:
    target = toy_with_records(tmp_path)
    workspace = Workspace.find(target.root)
    assert not index_path(workspace.root).exists()

    result = change(
        workspace,
        "open",
        "--from",
        f"{BASE}/{DELIVERED}/1",
        "--layer",
        "rules",
        "--title",
        "no index here",
    )

    assert result.exit_code == 0, result.output
    assert "warning" not in result.stderr
    assert not index_path(workspace.root).exists()
    view = dashboard(workspace)
    assert not view.index_read and "agentdiag index rebuild" in view.problems[0]


def test_an_index_that_fails_to_record_a_change_warns_and_the_command_succeeds(
    workspace: Workspace,
) -> None:
    index_path(workspace.root).write_bytes(b"not a database")

    result = change(
        workspace,
        "open",
        "--from",
        f"{BASE}/{DELIVERED}/1",
        "--layer",
        "rules",
        "--title",
        "a corrupt index",
    )

    assert result.exit_code == 0, result.output
    assert "was written but not indexed" in result.stderr
    assert "agentdiag index rebuild" in result.stderr


# --- the command ---


def test_the_command_prints_the_table_and_json(workspace: Workspace) -> None:
    root = str(workspace.root)

    table = runner.invoke(app, ["dashboard", "--root", root])
    dumped = runner.invoke(app, ["dashboard", "--root", root, "--json"])
    narrowed = runner.invoke(app, ["dashboard", "--root", root, "--json", "--trend", "2"])

    assert table.exit_code == 0, table.output
    assert table.stdout == PINNED
    assert dumped.exit_code == 0, dumped.output
    body = json.loads(dumped.stdout)
    assert body == dashboard(workspace).model_dump(mode="json", by_alias=True)
    assert body["rows"][2]["last_run"]["counts"]["pass"] == 15
    assert narrowed.exit_code == 0
    trend = json.loads(narrowed.stdout)["rows"][2]["trend"]
    assert [point["run_id"] for point in trend] == [JDGE, TRI3]


def test_a_trend_below_one_is_a_usage_error(workspace: Workspace) -> None:
    result = runner.invoke(app, ["dashboard", "--root", str(workspace.root), "--trend", "0"])

    assert result.exit_code == 3
    assert "--trend must be at least 1" in result.output


def test_dashboard_imports_no_sdk() -> None:
    """The offline-import gate: the Dashboard reads files and SQLite, never a model."""
    probe = (
        "import sys, agentdiag.dashboard; "
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] in "
        "('anthropic', 'claude_agent_sdk') "
        "or m.startswith(('agentdiag.model', 'agentdiag.adapter', 'agentdiag.run.execute'))); "
        "print(','.join(leaked))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=REPO
    )

    assert completed.stdout.strip() == ""
