"""Seam 1: the Index's `changes` table and `runs.change_record` (ticket 25, phase-7
decision 8).

The table is derived from the Change record files as `runs` is from the Run directories:
`list` reconciles it, `index rebuild` builds it again, and a Run that verified or refuted a
record carries the record's id in `runs.change_record`, which `list` prints. `run.json` is
never written for it.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from agentdiag.change.record import ChangeRecord, Trigger, Verification, write_record
from agentdiag.cli import app
from agentdiag.run.locate import index_path
from agentdiag.workspace import Workspace

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
RUNS = REPO / "tests" / "fixtures" / "runs"
SLUG = "toy-order-desk"
BASE = "20260923T100000Z-base"
PRMT = "20260923T100200Z-prmt"

runner = CliRunner()


def toy(tmp_path: Path) -> Path:
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    for run in (BASE, PRMT):
        shutil.copytree(RUNS / run, root / ".agentdiag" / "targets" / SLUG / "runs" / run)
    return root


def rows(root: Path, sql: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(index_path(root))
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def verified_record(root: Path) -> ChangeRecord:
    record = ChangeRecord(
        id="20260923-refund-on-shipped",
        target=SLUG,
        status="verified",
        opened_at="2026-09-23T08:00:00Z",
        opened_by="tester",
        closed_at="2026-09-23T11:00:00Z",
        title="Refund promised on a shipped order",
        trigger=Trigger(
            kind="diagnosis", summary="It said refund.", run=PRMT, scenario="s", trial=1
        ),
        layer="rules",
        pushes=[{"kind": "local", "environment": "local", "at": "2026-09-23T09:00:00Z"}],
        verification=Verification(
            baseline=PRMT,
            run=BASE,
            compared_at="2026-09-23T11:00:00Z",
            expect=["manifest.prompts"],
            result="verified",
            summary="improvement 1",
        ),
    )
    write_record(Workspace.find(root).resolve(None), record)
    return record


def test_list_fills_the_changes_table_and_the_verifying_runs_change_record(
    tmp_path: Path,
) -> None:
    root = toy(tmp_path)
    record = verified_record(root)

    result = runner.invoke(app, ["list", "--root", str(root)])

    assert result.exit_code == 0, result.output
    assert rows(root, "SELECT * FROM changes") == [
        (
            record.id,
            SLUG,
            "verified",
            "2026-09-23T08:00:00Z",
            "2026-09-23T11:00:00Z",
            "rules",
            "diagnosis",
            PRMT,
            BASE,
            1,
            "Refund promised on a shipped order",
            f".agentdiag/targets/{SLUG}/changes/{record.id}.md",
        )
    ]
    assert rows(root, "SELECT run_id, change_record FROM runs ORDER BY run_id") == [
        (BASE, record.id),
        (PRMT, None),
    ]
    header, *lines = result.stdout.splitlines()
    assert "change" in header.split()
    (base_line,) = [line for line in lines if line.startswith(BASE)]
    assert record.id in base_line
    assert (RUNS / BASE / "run.json").read_bytes() == (
        root / ".agentdiag" / "targets" / SLUG / "runs" / BASE / "run.json"
    ).read_bytes()


def test_rebuild_builds_the_changes_table_again_from_the_files(tmp_path: Path) -> None:
    root = toy(tmp_path)
    runner.invoke(app, ["list", "--root", str(root)])
    record = verified_record(root)

    result = runner.invoke(app, ["index", "rebuild", "--root", str(root)])

    assert result.exit_code == 0, result.output
    assert rows(root, "SELECT id, status FROM changes") == [(record.id, "verified")]
    assert rows(root, f"SELECT change_record FROM runs WHERE run_id = '{BASE}'") == [(record.id,)]


def test_a_record_file_removed_is_a_row_forgotten(tmp_path: Path) -> None:
    root = toy(tmp_path)
    record = verified_record(root)
    runner.invoke(app, ["list", "--root", str(root)])
    (root / ".agentdiag" / "targets" / SLUG / "changes" / f"{record.id}.md").unlink()

    runner.invoke(app, ["list", "--root", str(root)])

    assert rows(root, "SELECT id FROM changes") == []
    assert rows(root, f"SELECT change_record FROM runs WHERE run_id = '{BASE}'") == [(None,)]
