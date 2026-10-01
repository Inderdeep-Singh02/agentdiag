"""Seam 1: `agentdiag rescore` — a stored Run judged again, into a new Run (D34, ADR-0005 §4).

The source is a committed Run fixture copied into a Target root beside a copy of the
example, so the rescore reads the current Suites and Manifest exactly as a user's would.
In replay the Judge's exchanges come from the example's scoped recording
(`toy-orders.jsonl`), or from `runs-judge-swap.jsonl` for a Judge on another model; both
are written by `scripts/record_fixtures.py`.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.model.prices import PRICE_TABLE_VERSION

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
FIXTURES = REPO / "tests" / "fixtures"
RUNS = FIXTURES / "runs"
RECORDING = FIXTURES / "recordings" / "toy-orders.jsonl"
JUDGE_SWAP_RECORDING = FIXTURES / "recordings" / "runs-judge-swap.jsonl"
BASELINE = "20260923T100000Z-base"
INTERRUPTED = "20260923T100600Z-tri3"
COMMITTED_RESCORE = RUNS / "20260923T100500Z-resc"

SHIPPED = "where-is-shipped-order"
JUDGED = "delivered-order-cannot-be-cancelled"
TWO_TURNS = "lookup-then-cancel"
GREETING = "greeting-calls-no-tool"

runner = CliRunner()


def target_with(tmp_path: Path, *sources: str) -> Path:
    """A copy of the example whose `runs/` holds the named Run fixtures."""
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs"))
    for source in sources:
        shutil.copytree(
            RUNS / source, root / ".agentdiag" / "targets" / "toy-order-desk" / "runs" / source
        )
    return root


def rescore(root: Path, source: str, *arguments: str, replay: Path = RECORDING) -> object:
    return runner.invoke(
        app, ["rescore", source, "--root", str(root), *arguments, "--replay", str(replay)]
    )


def the_new_run(root: Path, *sources: str) -> Path:
    (run_dir,) = [
        path
        for path in (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir()
        if path.name not in sources
    ]
    return run_dir


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def digest_tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# --- a new Run, never an edit ---


def test_a_rescore_writes_a_new_run_whose_record_names_its_source(tmp_path: Path) -> None:
    root = target_with(tmp_path, BASELINE)

    result = rescore(root, BASELINE)

    assert result.exit_code == 1, result.stdout  # the Baseline's own fail, judged again
    assert read_json(the_new_run(root, BASELINE) / "run.json")["traces_from"] == BASELINE


def test_a_rescored_trial_holds_its_judgement_and_scores_and_no_trace(tmp_path: Path) -> None:
    root = target_with(tmp_path, BASELINE)

    rescore(root, BASELINE)

    trials = the_new_run(root, BASELINE) / "trials"
    assert not list(trials.rglob("trace.jsonl"))
    assert sorted(p.name for p in (trials / JUDGED / "1").iterdir()) == [
        "judgement.jsonl",
        "scores.json",
    ]
    assert sorted(p.name for p in (trials / SHIPPED / "1").iterdir()) == ["scores.json"]


def test_the_source_runs_bytes_are_unchanged_by_a_rescore(tmp_path: Path) -> None:
    root = target_with(tmp_path, BASELINE)
    source = root / ".agentdiag" / "targets" / "toy-order-desk" / "runs" / BASELINE
    before = digest_tree(source)

    rescore(root, BASELINE)

    assert digest_tree(source) == before


def test_the_record_carries_the_sources_trace_configuration_and_the_current_judge(
    tmp_path: Path,
) -> None:
    root = target_with(tmp_path, BASELINE)

    rescore(root, BASELINE, "--judge-model", "claude-opus-5-5", replay=JUDGE_SWAP_RECORDING)

    source = read_json(RUNS / BASELINE / "run.json")
    record = read_json(the_new_run(root, BASELINE) / "run.json")
    for copied in ("adapter", "fingerprint", "sync", "trials", "simulated_user"):
        assert record[copied] == source[copied], copied
    assert record["git"]["target"] == source["git"]["target"]
    assert record["judge"]["model"] == "claude-opus-5-5"
    assert record["price_table"] == {"version": PRICE_TABLE_VERSION}


def test_a_rescore_judges_with_the_current_judge_configuration(tmp_path: Path) -> None:
    """The other Judge fails the goal the Baseline's Judge passed; the Trace is the same."""
    root = target_with(tmp_path, BASELINE)

    rescore(root, BASELINE, "--judge-model", "claude-opus-5-5", replay=JUDGE_SWAP_RECORDING)

    (score,) = read_json(the_new_run(root, BASELINE) / "trials" / JUDGED / "1" / "scores.json")[
        "scores"
    ]
    assert (score["eval"], score["verdict"]) == ("goal", "fail")
    assert score["source"]["requested_model"] == "claude-opus-5-5"


def test_mechanical_evals_are_re_run_and_score_an_unchanged_trace_identically(
    tmp_path: Path,
) -> None:
    root = target_with(tmp_path, BASELINE)

    rescore(root, BASELINE)

    new = the_new_run(root, BASELINE)
    for scenario in (SHIPPED, GREETING, "status-question-is-not-a-cancel"):
        assert (new / "trials" / scenario / "1" / "scores.json").read_bytes() == (
            RUNS / BASELINE / "trials" / scenario / "1" / "scores.json"
        ).read_bytes()


# --- the current Suites decide what is judged ---


def test_an_edited_threshold_in_the_current_suite_applies_to_the_rescore(tmp_path: Path) -> None:
    root = target_with(tmp_path, BASELINE)
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "orders.yaml"
    suite = yaml.safe_load(path.read_text(encoding="utf-8"))
    shipped = next(s for s in suite["scenarios"] if s["id"] == SHIPPED)
    shipped["evals"] = [
        {"tool_latency": {"threshold": {"max_ms": 1}, "tool": "lookup_order"}}
        if isinstance(e, dict) and "tool_latency" in e
        else e
        for e in shipped["evals"]
    ]
    path.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")

    rescore(root, BASELINE)

    scores = read_json(the_new_run(root, BASELINE) / "trials" / SHIPPED / "1" / "scores.json")
    latency = next(s for s in scores["scores"] if s["eval"] == "tool_latency")
    assert latency["verdict"] == "fail"


def test_the_selection_flags_narrow_the_rescore_to_what_they_name(tmp_path: Path) -> None:
    root = target_with(tmp_path, BASELINE)

    result = rescore(root, BASELINE, "--scenario", SHIPPED)

    assert result.exit_code == 0, result.stdout
    new = the_new_run(root, BASELINE)
    assert sorted(p.name for p in (new / "trials").iterdir()) == [SHIPPED]


def test_a_scenario_the_source_never_ran_is_not_rescored_and_says_why(tmp_path: Path) -> None:
    root = target_with(tmp_path, BASELINE)

    rescore(root, BASELINE)

    not_run = read_json(the_new_run(root, BASELINE) / "scorecard.json")["not_run"]
    entry = next(e for e in not_run if e["scenario"] == TWO_TURNS)
    assert (entry["reason"], entry["detail"]) == (
        "not_selected",
        f"no Trial in the source Run {BASELINE}",
    )


def test_selecting_only_scenarios_the_source_never_ran_is_a_usage_error(tmp_path: Path) -> None:
    root = target_with(tmp_path, BASELINE)

    result = rescore(root, BASELINE, "--scenario", TWO_TURNS)

    assert result.exit_code == 3
    assert "no Trial in the source Run" in result.stdout
    assert [
        p.name for p in (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir()
    ] == [BASELINE]


def test_a_source_run_that_does_not_exist_is_a_usage_error(tmp_path: Path) -> None:
    root = target_with(tmp_path)

    result = rescore(root, "20260101T000000Z-none")

    assert result.exit_code == 3
    assert "no Run '20260101T000000Z-none'" in result.stderr


# --- Trials: the source's, and how each ended ---


def test_a_rescore_keeps_the_sources_trials_and_how_each_one_ended(tmp_path: Path) -> None:
    root = target_with(tmp_path, INTERRUPTED)

    rescore(root, INTERRUPTED)

    new = the_new_run(root, INTERRUPTED)
    assert read_json(new / "run.json")["trials"] == 3
    assert sorted(p.name for p in (new / "trials" / GREETING).iterdir()) == ["1", "2"]
    cancelled = read_json(new / "trials" / TWO_TURNS / "3" / "scores.json")["scores"]
    assert {(s["verdict"], s["reason"]) for s in cancelled} == {("incomplete", "cancelled")}
    scorecard = read_json(new / "scorecard.json")
    assert {"scenario": GREETING, "trial": 3, "reason": "cancelled"}.items() <= next(
        e for e in scorecard["not_run"] if e["scenario"] == GREETING
    ).items()
    assert scorecard["excluded_trials"] == 1


def test_a_scenario_a_single_trial_source_never_started_stays_cancelled(tmp_path: Path) -> None:
    """ADR-0005 §8: a Ctrl-C in the source is still what did not happen in the rescore.

    The source Run is made here the way the existing cancellation tests make one: the
    Target's factory raises `KeyboardInterrupt`, so the first Scenario's Trial is cancelled
    and the second never starts. The Manifest is then put back, as a user would, and rescored.
    """
    root = target_with(tmp_path)
    manifest_path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    original = manifest_path.read_text(encoding="utf-8")
    manifest = yaml.safe_load(original)
    manifest["adapter"]["environments"]["local"] = {
        "factory": "tests.fakes.interrupted_target:make_target",
        "tools": "tests.fakes.interrupted_target:make_tools",
    }
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    first = "cancel-processing-order"
    ran = runner.invoke(
        app,
        [
            *("run", "--root", str(root), "--scenario", first, "--scenario", SHIPPED),
            *("--replay", str(RECORDING)),
        ],
    )
    assert ran.exit_code == 2, ran.stdout
    (source,) = [
        p.name for p in (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir()
    ]
    manifest_path.write_text(original, encoding="utf-8")

    rescore(root, source)

    new = the_new_run(root, source)
    not_run = read_json(new / "scorecard.json")["not_run"]
    assert [e for e in not_run if e["scenario"] == SHIPPED] == [
        {
            "scenario": SHIPPED,
            "suite": "orders",
            "reason": "cancelled",
            "detail": f"never started in the source Run {source}",
            "trial": None,
        }
    ]
    (score,) = read_json(new / "trials" / first / "1" / "scores.json")["scores"]
    assert (score["verdict"], score["reason"]) == ("incomplete", "cancelled")


def test_a_rescore_of_a_rescore_names_the_run_that_holds_the_traces(tmp_path: Path) -> None:
    root = target_with(tmp_path, BASELINE, COMMITTED_RESCORE.name)

    result = rescore(root, COMMITTED_RESCORE.name)

    assert result.exit_code == 1, result.stdout
    new = the_new_run(root, BASELINE, COMMITTED_RESCORE.name)
    assert read_json(new / "run.json")["traces_from"] == BASELINE


# --- show and export read a rescored Trial through traces_from ---


def test_show_renders_a_rescored_trial_and_says_which_run_it_was_rescored_from() -> None:
    result = runner.invoke(app, ["show", str(COMMITTED_RESCORE), JUDGED])

    assert result.exit_code == 0, result.stdout
    lines = result.stdout.splitlines()
    assert lines[0] == f"Run {COMMITTED_RESCORE.name} · Scenario {JUDGED} · Trial 1"
    assert f"Rescored from Run {BASELINE} (its Trace, judged again in this Run)" in lines
    assert "Turn 1" in result.stdout


def test_show_json_of_a_rescored_trial_is_the_sources_trace_then_this_runs_judgement() -> None:
    result = runner.invoke(app, ["show", str(COMMITTED_RESCORE), JUDGED, "--json"])

    trace = (RUNS / BASELINE / "trials" / JUDGED / "1" / "trace.jsonl").read_text()
    judgement = (COMMITTED_RESCORE / "trials" / JUDGED / "1" / "judgement.jsonl").read_text()
    assert result.stdout == trace + judgement


def test_export_writes_a_rescored_trial_under_the_rescores_own_run_id(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["export", str(COMMITTED_RESCORE), JUDGED, "--format", "speedscope", "--out", "-"],
    )

    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["name"] == f"{COMMITTED_RESCORE.name} · {JUDGED} · trial 1"
