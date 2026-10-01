"""Seam 1: the derived index and `agentdiag list` over the committed Run fixtures (ticket 09).

Every Run listed here is a copy of a directory under `tests/fixtures/runs/` in a temporary
Target's `<root>/.agentdiag/targets/<slug>/runs/`, so an index is only ever written into a
temporary root and never beside the fixtures. The index is derived (ADR-0005 §5): `list`
builds it when it is missing, reconciles it with the directories before every listing, and
refuses to guess when the file is corrupt; `index rebuild` deletes it and builds it again
from the files, which are the truth (D35, phase-5 decisions 62 to 64).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.run.index import INDEX_SCHEMA_VERSION, RunListing
from agentdiag.run.locate import index_path, trace_path
from agentdiag.trace.reader import read_trace
from agentdiag.trace.spans import project_spans

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
FIXTURES = REPO / "tests" / "fixtures"
RUNS = FIXTURES / "runs"
LISTING_SCHEMA = REPO / "schemas" / "listing.schema.json"
RECORDING = FIXTURES / "recordings" / "toy-orders.jsonl"

BASELINE = "20260923T100000Z-base"
RESCORE = "20260923T100500Z-resc"
INTERRUPTED = "20260923T100600Z-tri3"
THRESHOLD = "20260923T100700Z-thrs"
EVERY_RUN = sorted(path.name for path in RUNS.iterdir())
NEWEST_FIRST = sorted(EVERY_RUN, reverse=True)

GREETING = "greeting-calls-no-tool"
TWO_TURNS = "lookup-then-cancel"
SHIPPED = "where-is-shipped-order"
STATUS = "status-question-is-not-a-cancel"

REBUILD = "agentdiag index rebuild"

runner = CliRunner()


def example_runs(root: Path) -> Path:
    """Where the example's one Target, `toy-order-desk`, keeps its Runs."""
    return root / ".agentdiag" / "targets" / "toy-order-desk" / "runs"


def root_with(tmp_path: Path, *runs: str) -> Path:
    """A root whose one Target's `runs/` holds copies of the named Run fixtures."""
    root = tmp_path / "workspace"
    example_runs(root).mkdir(parents=True)
    for run in runs:
        shutil.copytree(RUNS / run, example_runs(root) / run)
    return root


def target_with(tmp_path: Path, *runs: str) -> Path:
    """A copy of the example, so `run` and `rescore` have a Manifest and Suites to read."""
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    for run in runs:
        shutil.copytree(RUNS / run, example_runs(root) / run)
    return root


def invoke(*arguments: str | Path) -> object:
    return runner.invoke(app, [str(argument) for argument in arguments])


def listed(root: Path, *arguments: str) -> str:
    result = invoke("list", "--root", root, *arguments)
    assert result.exit_code == 0, result.stdout + result.stderr
    return result.stdout


def listing_rows(root: Path, *arguments: str) -> list[RunListing]:
    return [
        RunListing.model_validate(row) for row in json.loads(listed(root, "--json", *arguments))
    ]


def rows(root: Path, sql: str, *parameters: object) -> list[tuple]:
    connection = sqlite3.connect(index_path(root))
    try:
        return connection.execute(sql, parameters).fetchall()
    finally:
        connection.close()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def disk_bytes(run_dir: Path) -> int:
    return sum(path.stat().st_size for path in run_dir.rglob("*") if path.is_file())


# --- `list` over the eight committed Runs ---


def test_list_builds_a_missing_index_and_says_so_on_stderr(tmp_path: Path) -> None:
    root = root_with(tmp_path, *EVERY_RUN)

    result = invoke("list", "--root", root)

    assert result.exit_code == 0, result.stdout + result.stderr
    assert f"notice: built the index at {index_path(root)}" in result.stderr.splitlines()
    assert index_path(root).is_file()
    assert "notice:" not in result.stdout


def test_list_prints_one_line_per_run_newest_first_under_a_header(tmp_path: Path) -> None:
    root = root_with(tmp_path, *EVERY_RUN)

    header, *lines = listed(root).splitlines()

    assert re.split(r" {2,}", header) == [
        "run",
        "target",
        "source",
        "created",
        "fingerprint",
        "selection",
        "sync",
        "verdicts",
        "not run",
        "traces",
        "change",
        "size",
    ]
    assert [line.split()[0] for line in lines] == NEWEST_FIRST


def test_a_listed_rescore_names_its_source_and_every_verdict_zeros_included(
    tmp_path: Path,
) -> None:
    """The one line a reader needs to see whole: a rescore says whose Traces it read, a Run
    with no Fingerprint says `-`, and a pass count never stands without the other four."""
    root = root_with(tmp_path, *EVERY_RUN)

    (line,) = [line for line in listed(root).splitlines() if line.startswith(RESCORE)]

    # Padded to the widest cell of its column among the eight: the four-Scenario selection
    # expression (122 characters) and the interrupted Run's verdicts (56).
    selection = "all".ljust(122)
    verdicts = "pass 15  fail 2  incomplete 0  unverifiable 0  invalid 0".ljust(56)
    assert line == (
        f"{RESCORE}  toy-order-desk  run     2026-09-23T10:05:00Z  -            {selection}  "
        f"not_checked (no_fingerprint)  {verdicts}  9        from {BASELINE}  -       79 KB"
    )


def test_a_run_that_is_not_a_rescore_lists_its_traces_as_its_own(tmp_path: Path) -> None:
    root = root_with(tmp_path, *EVERY_RUN)

    (line,) = [line for line in listed(root).splitlines() if line.startswith(INTERRUPTED)]

    assert "pass 47  fail 1  incomplete 6  unverifiable 0  invalid 0" in line
    assert "  11       -" in line
    assert line.endswith(f"{round(disk_bytes(RUNS / INTERRUPTED) / 1024)} KB")


def test_list_json_emits_the_rows_the_schema_publishes(tmp_path: Path) -> None:
    root = root_with(tmp_path, *EVERY_RUN)

    payload = json.loads(listed(root, "--json"))

    schema = json.loads(LISTING_SCHEMA.read_text(encoding="utf-8"))
    for row in payload:
        jsonschema.validate(row, schema)
    found = [RunListing.model_validate(row) for row in payload]
    assert [row.model_dump(mode="json", by_alias=True) for row in found] == payload
    assert [row.run_id for row in found] == NEWEST_FIRST
    rescore = next(row for row in found if row.run_id == RESCORE)
    assert rescore.traces_from == BASELINE
    assert rescore.source == "run"
    assert rescore.change_record is None
    assert rescore.fingerprint is None
    assert rescore.target == "toy-order-desk"
    assert rescore.sync.status == "not_checked"
    assert rescore.sync.reason == "no_fingerprint"
    assert rescore.selection == "all"
    assert rescore.counts.model_dump(by_alias=True) == {
        "pass": 15,
        "fail": 2,
        "incomplete": 0,
        "unverifiable": 0,
        "invalid": 0,
    }
    assert rescore.not_run == 9
    assert rescore.disk_bytes == disk_bytes(RUNS / RESCORE)
    interrupted = next(row for row in found if row.run_id == INTERRUPTED)
    assert interrupted.trials == 3
    assert interrupted.pass_rate == 47 / 48


def test_the_published_listing_schema_requires_every_verdict() -> None:
    """ADR-0005 §8 on a listing: a row whose counts leave a Verdict out does not validate."""
    schema = json.loads(LISTING_SCHEMA.read_text(encoding="utf-8"))
    counts = schema["$defs"]["VerdictCounts"]

    assert sorted(counts["required"]) == sorted(
        ["pass", "fail", "incomplete", "unverifiable", "invalid"]
    )


def test_list_limit_shows_only_the_newest_runs(tmp_path: Path) -> None:
    root = root_with(tmp_path, *EVERY_RUN)

    _, *lines = listed(root, "--limit", "2").splitlines()

    assert [line.split()[0] for line in lines] == NEWEST_FIRST[:2]
    assert [row.run_id for row in listing_rows(root, "--limit", "2")] == NEWEST_FIRST[:2]


def test_a_workspace_with_no_runs_says_so_and_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    assert invoke("init", "--root", root).exit_code == 0

    result = invoke("list", "--root", root)

    assert result.exit_code == 0
    runs = root / ".agentdiag" / "targets" / "default" / "runs"
    assert result.stdout.strip() == f"no Runs under {runs}"
    assert not index_path(root).exists()


def test_the_readmes_listing_is_what_list_prints_over_the_eight_runs(tmp_path: Path) -> None:
    """The guide pastes `list`'s output over the committed fixtures; it cannot drift."""
    root = root_with(tmp_path, *EVERY_RUN)
    readme = (REPO / "docs" / "guide.md").read_text(encoding="utf-8")
    section = readme[readme.index("## List the Runs") :]
    section = section[: section.index("\n## ", 1)]
    typed = "uv run agentdiag list --root /tmp/agentdiag-toy\n```\n\n```\n"
    printed = section[section.index(typed) + len(typed) :].split("```")[0]

    assert printed == listed(root)


# --- `index rebuild`: the files are the truth ---


def test_rebuilding_the_index_leaves_the_listing_identical_byte_for_byte(tmp_path: Path) -> None:
    root = root_with(tmp_path, *EVERY_RUN)
    before = listed(root)
    before_json = listed(root, "--json")

    result = invoke("index", "rebuild", "--root", root)

    assert result.exit_code == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"indexed 8 Runs into {index_path(root)}"
    assert listed(root) == before
    assert listed(root, "--json") == before_json


def test_rebuild_replaces_a_corrupt_index(tmp_path: Path) -> None:
    root = root_with(tmp_path, BASELINE)
    index_path(root).write_bytes(b"not a database, not even close" * 40)

    result = invoke("index", "rebuild", "--root", root)

    assert result.exit_code == 0, result.stdout + result.stderr
    assert [row.run_id for row in listing_rows(root)] == [BASELINE]


# --- the four tables hold what the files say ---


def test_every_recorded_trial_of_an_interrupted_run_is_a_row_with_its_termination(
    tmp_path: Path,
) -> None:
    """Trial 3 of the greeting was never started (the Scorecard names it `cancelled` in
    `not_run`), so it has no Trace and no row; the Trial the interrupt stopped does."""
    root = root_with(tmp_path, INTERRUPTED)
    listed(root)

    trials = rows(
        root,
        "SELECT scenario, trial, termination, suite FROM trials WHERE run_id = ? "
        "ORDER BY scenario, trial",
        INTERRUPTED,
    )

    assert trials == [
        (GREETING, 1, "completed", "orders"),
        (GREETING, 2, "completed", "orders"),
        (TWO_TURNS, 1, "completed", "orders"),
        (TWO_TURNS, 2, "completed", "orders"),
        (TWO_TURNS, 3, "cancelled", "orders"),
        (SHIPPED, 1, "completed", "orders"),
        (SHIPPED, 2, "completed", "orders"),
        (SHIPPED, 3, "completed", "orders"),
    ]


def test_a_trials_spans_rows_are_the_spans_its_trace_projects(tmp_path: Path) -> None:
    root = root_with(tmp_path, INTERRUPTED)
    listed(root)
    run_dir = example_runs(root) / INTERRUPTED

    for scenario, trial in [(GREETING, 1), (TWO_TURNS, 2), (TWO_TURNS, 3), (SHIPPED, 3)]:
        events = read_trace(trace_path(run_dir, scenario, trial))
        spans = project_spans(events)
        indexed = rows(
            root,
            "SELECT span_id, kind, actor, start_ms, end_ms, status FROM spans "
            "WHERE run_id = ? AND scenario = ? AND trial = ? ORDER BY span_id",
            INTERRUPTED,
            scenario,
            trial,
        )
        assert indexed == sorted(
            (span.span_id, span.kind, span.actor, span.start_ms, span.end_ms, span.status)
            for span in spans
        )
        ((event_count, span_count),) = rows(
            root,
            "SELECT events, spans FROM trials WHERE run_id = ? AND scenario = ? AND trial = ?",
            INTERRUPTED,
            scenario,
            trial,
        )
        assert (event_count, span_count) == (len(events), len(spans))


def test_a_trials_scores_rows_are_its_scores_json_in_order(tmp_path: Path) -> None:
    root = root_with(tmp_path, INTERRUPTED)
    listed(root)
    run_dir = example_runs(root) / INTERRUPTED

    for scores_file in sorted(run_dir.glob("trials/*/*/scores.json")):
        recorded = json.loads(scores_file.read_text(encoding="utf-8"))
        indexed = rows(
            root,
            "SELECT position, eval, eval_id, verdict, reason, source_kind FROM scores "
            "WHERE run_id = ? AND scenario = ? AND trial = ? ORDER BY position",
            INTERRUPTED,
            recorded["scenario"],
            recorded["trial"],
        )
        assert indexed == [
            (
                position,
                score["eval"],
                score["eval_id"],
                score["verdict"],
                score["reason"],
                score["source"]["kind"],
            )
            for position, score in enumerate(recorded["scores"])
        ]


def test_a_rescored_trial_has_its_sources_spans_and_its_own_scores(tmp_path: Path) -> None:
    root = root_with(tmp_path, BASELINE, THRESHOLD)
    listed(root)

    def spans_of(run_id: str) -> list[tuple]:
        return rows(
            root,
            "SELECT span_id, kind, start_ms, end_ms FROM spans "
            "WHERE run_id = ? AND scenario = ? AND trial = 1 ORDER BY span_id",
            run_id,
            SHIPPED,
        )

    def verdicts_of(run_id: str) -> list[tuple]:
        return rows(
            root,
            "SELECT eval, verdict FROM scores WHERE run_id = ? AND scenario = ? AND trial = 1 "
            "ORDER BY position",
            run_id,
            SHIPPED,
        )

    assert spans_of(THRESHOLD) == spans_of(BASELINE) != []
    assert ("response_latency", "pass") in verdicts_of(BASELINE)
    assert ("response_latency", "fail") in verdicts_of(THRESHOLD)


def test_a_run_recorded_before_run_json_named_its_target_belongs_to_the_target_holding_it(
    tmp_path: Path,
) -> None:
    """Phase-6 decision 6, amended: a `run.json` an agentdiag older than ticket 24 wrote
    names no `target`, and the Run is the Target's whose `runs/` holds the directory (the
    directory is the truth, never the Manifest's `target.name`); `source` falls back to
    `run`."""
    root = root_with(tmp_path, BASELINE)
    # Under a slug that is not the Manifest's name, so only the directory can say it.
    moved = root / ".agentdiag" / "targets" / "orders" / "runs"
    moved.parent.mkdir(parents=True)
    example_runs(root).rename(moved)
    path = moved / BASELINE / "run.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    del record["target"], record["source"]
    path.write_text(json.dumps(record), encoding="utf-8")

    (row,) = listing_rows(root, "--target", "orders")
    rebuilt = invoke("index", "rebuild", "--root", root, "--target", "orders")

    assert (row.run_id, row.target, row.source) == (BASELINE, "orders", "run")
    assert rebuilt.stdout.startswith("indexed 1 Runs of Target orders into")


def test_a_run_id_under_two_targets_is_skipped_by_name_and_refused_by_show(
    tmp_path: Path,
) -> None:
    """Run ids are random-suffixed, so only a copied directory does this: a guard."""
    root = tmp_path / "ws"
    for slug in ("a", "b"):
        assert invoke("init", "--root", root, "--target", slug).exit_code == 0
        shutil.copytree(RUNS / BASELINE, root / ".agentdiag" / "targets" / slug / "runs" / BASELINE)

    listed_result = invoke("list", "--root", root)
    shown = invoke("show", BASELINE, GREETING, "--root", root)

    assert f"warning: skipped Run {BASELINE}: it is under more than one Target" in (
        listed_result.stderr
    )
    assert listed_result.stdout.strip().startswith("no Runs under")
    assert shown.exit_code == 3
    assert "under more than one Target" in shown.output
    assert str(root / ".agentdiag" / "targets" / "a" / "runs" / BASELINE) in shown.output


def test_the_runs_row_carries_the_workspace_columns_from_the_first_version(
    tmp_path: Path,
) -> None:
    root = root_with(tmp_path, BASELINE, RESCORE)
    listed(root)

    assert rows(
        root, "SELECT run_id, target, source, change_record, traces_from FROM runs ORDER BY run_id"
    ) == [
        (BASELINE, "toy-order-desk", "run", None, None),
        (RESCORE, "toy-order-desk", "run", None, BASELINE),
    ]
    assert rows(root, "PRAGMA user_version") == [(INDEX_SCHEMA_VERSION,)]
    assert rows(root, "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name") == [
        ("changes",),
        ("runs",),
        ("scores",),
        ("spans",),
        ("trials",),
    ]


# --- reconcile: the directories decide what is listed ---


def test_a_run_copied_in_after_the_index_exists_is_listed(tmp_path: Path) -> None:
    root = root_with(tmp_path, *[run for run in EVERY_RUN if run != THRESHOLD])
    listed(root)
    shutil.copytree(RUNS / THRESHOLD, example_runs(root) / THRESHOLD)

    result = invoke("list", "--root", root)

    assert result.exit_code == 0
    assert "notice:" not in result.stderr
    assert [line.split()[0] for line in result.stdout.splitlines()[1:]] == NEWEST_FIRST


def test_a_run_whose_directory_is_gone_is_forgotten(tmp_path: Path) -> None:
    root = root_with(tmp_path, *EVERY_RUN)
    listed(root)
    shutil.rmtree(example_runs(root) / RESCORE)

    assert [row.run_id for row in listing_rows(root)] == [r for r in NEWEST_FIRST if r != RESCORE]
    assert rows(root, "SELECT COUNT(*) FROM trials WHERE run_id = ?", RESCORE) == [(0,)]
    assert rows(root, "SELECT COUNT(*) FROM scores WHERE run_id = ?", RESCORE) == [(0,)]


def test_a_directory_with_an_unreadable_run_record_is_warned_about_and_skipped(
    tmp_path: Path,
) -> None:
    root = root_with(tmp_path, BASELINE)
    broken = example_runs(root) / "20260924T000000Z-brkn"
    broken.mkdir()
    (broken / "run.json").write_text("{ not json", encoding="utf-8")

    result = invoke("list", "--root", root)

    assert result.exit_code == 0, result.stdout + result.stderr
    assert any(
        line.startswith("warning:") and str(broken) in line for line in result.stderr.splitlines()
    )
    assert [line.split()[0] for line in result.stdout.splitlines()[1:]] == [BASELINE]

    rebuilt = invoke("index", "rebuild", "--root", root)
    assert rebuilt.exit_code == 0
    assert str(broken) in rebuilt.stderr
    assert rebuilt.stdout.strip() == f"indexed 1 Runs into {index_path(root)}"


# --- a corrupt index is a fact for a human ---


def assert_reported_corrupt(root: Path) -> None:
    before = digest(index_path(root))

    result = invoke("list", "--root", root)

    assert result.exit_code == 3
    (line,) = result.stderr.splitlines()
    assert line.startswith(f"error: the Index at {index_path(root)} could not be read (")
    assert line.endswith(f"); run {REBUILD}")
    assert result.stdout == ""
    assert digest(index_path(root)) == before, "nothing is rebuilt on its own"


def test_an_index_of_random_bytes_is_reported_with_the_rebuild_command(tmp_path: Path) -> None:
    root = root_with(tmp_path, BASELINE)
    index_path(root).write_bytes(bytes(range(256)) * 16)

    assert_reported_corrupt(root)


def test_an_index_of_another_schema_version_is_reported_with_the_rebuild_command(
    tmp_path: Path,
) -> None:
    root = root_with(tmp_path, BASELINE)
    connection = sqlite3.connect(index_path(root))
    connection.execute("PRAGMA user_version = 99")
    connection.commit()
    connection.close()

    assert_reported_corrupt(root)


# --- written when a Run completes, by `run` and `rescore` alike ---


def test_a_run_writes_its_index_row_when_it_completes(tmp_path: Path) -> None:
    root = target_with(tmp_path)

    result = invoke("run", "--root", root, "--scenario", GREETING, "--replay", RECORDING)

    assert result.exit_code == 0, result.stdout + result.stderr
    ((run_id, trials),) = rows(root, "SELECT run_id, trials FROM runs")
    assert (example_runs(root) / run_id / "scorecard.json").is_file()
    assert trials == 1
    assert rows(root, "SELECT scenario, trial FROM trials") == [(GREETING, 1)]


def test_a_rescore_adds_its_own_row_beside_its_sources(tmp_path: Path) -> None:
    root = target_with(tmp_path, BASELINE)
    listed(root)

    result = invoke("rescore", BASELINE, "--root", root, "--replay", RECORDING)

    assert result.exit_code in (0, 1), result.stdout + result.stderr
    found = rows(root, "SELECT run_id, traces_from FROM runs ORDER BY run_id")
    assert found[0] == (BASELINE, None)
    assert len(found) == 2
    assert found[1][1] == BASELINE


def test_a_run_whose_index_cannot_be_written_warns_and_keeps_its_exit_code(
    tmp_path: Path,
) -> None:
    """The status question's Verdict is a fail, so its exit code is 1: an unchanged code is
    then told apart from one forced to 0. The warning names the rebuild command once."""
    root = target_with(tmp_path)
    index_path(root).mkdir()

    result = invoke("run", "--root", root, "--scenario", STATUS, "--replay", RECORDING)

    assert result.exit_code == 1, result.stdout + result.stderr
    (run_dir,) = list(example_runs(root).iterdir())
    assert (run_dir / "scorecard.json").is_file()
    (warning,) = [line for line in result.stderr.splitlines() if line.startswith("warning:")]
    assert warning.startswith(
        f"warning: Run {run_dir.name} is complete but was not indexed: "
        f"the Index at {index_path(root)} could not be read ("
    )
    assert warning.endswith(f"); run {REBUILD}")
    assert warning.count(REBUILD) == 1


def test_an_index_path_that_is_a_directory_is_reported_not_raised(tmp_path: Path) -> None:
    root = root_with(tmp_path, BASELINE)
    index_path(root).mkdir()

    result = invoke("list", "--root", root)

    assert result.exit_code == 3
    (line,) = result.stderr.splitlines()
    assert line.startswith(f"error: the Index at {index_path(root)} could not be read (")
    assert line.endswith(f"); run {REBUILD}")
    assert line.count(REBUILD) == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only directory")
def test_an_index_that_cannot_be_created_is_reported_not_raised(tmp_path: Path) -> None:
    """A read-only `.agentdiag/`: creating the file fails, and that is the same exit 3 with
    the rebuild command as a corrupt one, never a traceback (amended decision 63)."""
    root = root_with(tmp_path, BASELINE)
    agentdiag_dir = index_path(root).parent
    agentdiag_dir.chmod(0o555)
    try:
        result = invoke("list", "--root", root)
    finally:
        agentdiag_dir.chmod(0o755)

    assert result.exit_code == 3, result.stdout + result.stderr
    (line,) = result.stderr.splitlines()
    assert line.startswith(f"error: the Index at {index_path(root)} could not be read (")
    assert line.endswith(f"); run {REBUILD}")
    assert isinstance(result.exception, SystemExit)


# --- offline ---


def test_list_and_the_index_load_no_sdk_and_no_model_client(tmp_path: Path) -> None:
    root = root_with(tmp_path, BASELINE, RESCORE)
    probe = (
        "import sys\n"
        "from agentdiag.cli import app\n"
        "for arguments in (['list', '--root', sys.argv[1]], "
        "['index', 'rebuild', '--root', sys.argv[1]]):\n"
        "    try:\n"
        "        app(arguments)\n"
        "    except SystemExit as exit:\n"
        "        assert exit.code == 0, exit.code\n"
        "leaked = sorted(m for m in sys.modules "
        "if m == 'anthropic' or m.startswith(('anthropic.', 'agentdiag.model', "
        "'claude_agent_sdk')))\n"
        "print('leaked:' + ','.join(leaked))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe, str(root)],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )

    assert "leaked:\n" in completed.stdout, completed.stdout
