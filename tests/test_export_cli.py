"""Seam 1: `agentdiag export` over a Run directory, as a developer runs it.

Every assertion is on the file the command wrote, the JSON it printed, or the code it
exited with. The Run it reads is a directory holding the committed Trace fixture, so the
content it produces is the known-good export the other test file compares byte for byte.

One rule has a test of its own because it is the reason `--out` defaults where it does: an
export is a Report — a rendering, never the source of truth — and a Run is written once and
never edited (ADR-0005 §2). So no `export` ever writes inside the Run directory.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.trace.export import write_export

REPO = Path(__file__).resolve().parents[1]
TRACE_FIXTURE = REPO / "tests" / "fixtures" / "traces" / "cancel-processing-order.trace.jsonl"
EXPORTS = REPO / "tests" / "fixtures" / "exports"
CHROME_EXPECTED = EXPORTS / "cancel-processing-order.chrome.json"
SPEEDSCOPE_EXPECTED = EXPORTS / "cancel-processing-order.speedscope.json"
"""The known-good exports: what the command writes is compared with these, not with
a second call to the code under test."""

SCENARIO = "cancel-processing-order"
FIXTURE_RUN_ID = "20260922T101500Z-k7pq"
"""The Run id inside the committed Trace fixture's `trace/start` Event."""

runner = CliRunner()


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """A Run directory holding one Trial, whose Trace is the committed fixture."""
    directory = tmp_path / "runs" / FIXTURE_RUN_ID
    trial = directory / "trials" / SCENARIO / "1"
    trial.mkdir(parents=True)
    shutil.copyfile(TRACE_FIXTURE, trial / "trace.jsonl")
    return directory


def export(*arguments: str) -> object:
    return runner.invoke(app, ["export", *arguments])


def trial_files(run_dir: Path) -> dict[str, str]:
    """Every file in the Trial directory, by name, so a write into it is visible."""
    trial = run_dir / "trials" / SCENARIO / "1"
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(trial.rglob("*"))
        if path.is_file()
    }


# --- where it writes ---


def test_export_chrome_writes_the_default_path_in_the_current_directory(
    run_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here = tmp_path / "cwd"
    here.mkdir()
    monkeypatch.chdir(here)

    result = export(str(run_dir), SCENARIO, "--format", "chrome")

    assert result.exit_code == 0, result.output
    written = here / f"{FIXTURE_RUN_ID}.{SCENARIO}.1.chrome.json"
    assert written.exists()
    assert written.read_text(encoding="utf-8") == CHROME_EXPECTED.read_text(encoding="utf-8")


def test_export_speedscope_writes_the_default_path_in_the_current_directory(
    run_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here = tmp_path / "cwd"
    here.mkdir()
    monkeypatch.chdir(here)

    result = export(str(run_dir), SCENARIO, "--format", "speedscope")

    assert result.exit_code == 0, result.output
    written = here / f"{FIXTURE_RUN_ID}.{SCENARIO}.1.speedscope.json"
    assert written.read_text(encoding="utf-8") == SPEEDSCOPE_EXPECTED.read_text(encoding="utf-8")


def test_out_writes_where_the_caller_asked(run_dir: Path, tmp_path: Path) -> None:
    asked = tmp_path / "somewhere" / "trial.chrome.json"

    result = export(str(run_dir), SCENARIO, "--format", "chrome", "--out", str(asked))

    assert result.exit_code == 0, result.output
    assert asked.exists()
    assert str(asked) in result.stdout


def test_out_dash_prints_the_export_instead_of_writing_a_file(
    run_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here = tmp_path / "cwd"
    here.mkdir()
    monkeypatch.chdir(here)

    result = export(str(run_dir), SCENARIO, "--format", "chrome", "--out", "-")

    assert result.exit_code == 0, result.output
    printed = json.loads(result.stdout)
    assert printed["otherData"]["run"] == FIXTURE_RUN_ID
    assert list(here.iterdir()) == []


def test_an_export_never_writes_inside_the_run_directory(
    run_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Run is written once (ADR-0005 §2) and an export is a rendering of it, not part of it."""
    here = tmp_path / "cwd"
    here.mkdir()
    monkeypatch.chdir(here)
    before = trial_files(run_dir)
    listed_before = sorted(path.name for path in run_dir.rglob("*"))

    for format in ("chrome", "speedscope"):
        assert export(str(run_dir), SCENARIO, "--format", format).exit_code == 0

    assert trial_files(run_dir) == before
    assert sorted(path.name for path in run_dir.rglob("*")) == listed_before


def test_write_export_writes_the_path_it_was_given_and_returns_it(
    run_dir: Path, tmp_path: Path
) -> None:
    """The one function here that touches a filesystem, called without the CLI."""
    asked = tmp_path / "elsewhere" / "one.chrome.json"

    written = write_export(run_dir, SCENARIO, 1, "chrome", asked)

    assert written == asked
    assert asked.read_text(encoding="utf-8") == CHROME_EXPECTED.read_text(encoding="utf-8")


def test_what_the_command_prints_and_what_it_writes_are_the_same_rendering(
    run_dir: Path, tmp_path: Path
) -> None:
    """One renderer, one writer: `--out -` and `--out PATH` cannot drift apart."""
    asked = tmp_path / "one.chrome.json"

    printed = export(str(run_dir), SCENARIO, "--format", "chrome", "--out", "-")
    assert export(str(run_dir), SCENARIO, "--format", "chrome", "--out", str(asked)).exit_code == 0

    assert printed.stdout == asked.read_text(encoding="utf-8")


# --- which Trial it reads ---


def test_a_run_id_is_resolved_under_the_root(
    run_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "target"
    (root / ".agentdiag").mkdir(parents=True)
    shutil.copytree(run_dir.parent, root / ".agentdiag" / "targets" / "toy-order-desk" / "runs")
    here = tmp_path / "cwd"
    here.mkdir()
    monkeypatch.chdir(here)

    result = export(FIXTURE_RUN_ID, SCENARIO, "--format", "chrome", "--root", str(root))

    assert result.exit_code == 0, result.output
    assert (here / f"{FIXTURE_RUN_ID}.{SCENARIO}.1.chrome.json").exists()


# --- when it cannot ---


def test_a_missing_run_exits_three_and_names_the_path_it_looked_at(tmp_path: Path) -> None:
    (tmp_path / ".agentdiag").mkdir()
    result = export("no-such-run", SCENARIO, "--format", "chrome", "--root", str(tmp_path))

    assert result.exit_code == 3
    assert "no-such-run" in result.output
    assert str(tmp_path) in result.output


def test_a_missing_trial_exits_three_and_names_the_trace_it_looked_for(run_dir: Path) -> None:
    result = export(str(run_dir), SCENARIO, "--format", "chrome", "--trial", "7")

    assert result.exit_code == 3
    assert "trace.jsonl" in result.output


def test_an_unknown_format_exits_three_and_names_the_two_it_writes(run_dir: Path) -> None:
    result = export(str(run_dir), SCENARIO, "--format", "flamegraph")

    assert result.exit_code == 3
    assert "flamegraph" in result.output
    assert "chrome" in result.output
    assert "speedscope" in result.output


# --- the command a reader can find ---


def test_help_lists_export() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "export" in result.stdout
