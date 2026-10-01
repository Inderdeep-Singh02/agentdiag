"""Seam 1: `agentdiag run --trials N`, and the Scorecard's pass^k (ticket 08, ADR-0005 §7).

Two kinds of evidence. A replayed Run of the example with `--trials 3`, where every Trial
re-walks its Scenario's view of one recording (phase-5 decision 19), so each Trial is
identical and pass^k is 1 at every k. And the committed Run fixture
`tests/fixtures/runs/20260923T100600Z-tri3`, which `scripts/record_fixtures.py --runs`
wrote with a clock that made one lookup slow and then interrupted the Run mid-Trial:
there the Trials differ, one is excluded, and one never started. Every expected pass^k
below is worked by hand from C(c, k) / C(n, k), never read off the code.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from agentdiag.cli import app

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
RECORDING = REPO / "tests" / "fixtures" / "recordings" / "toy-orders.jsonl"
RUNS = REPO / "tests" / "fixtures" / "runs"
INTERRUPTED = RUNS / "20260923T100600Z-tri3"

SHIPPED = "where-is-shipped-order"
TWO_TURNS = "lookup-then-cancel"
GREETING = "greeting-calls-no-tool"

runner = CliRunner()


def target_root(tmp_path: Path) -> Path:
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs"))
    return root


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(root: Path, *arguments: str) -> Result:
    """`agentdiag run` of the example's shipped-order Scenario, replayed."""
    return runner.invoke(
        app,
        ["run", "--root", str(root), "--scenario", SHIPPED, *arguments, "--replay", str(RECORDING)],
    )


def only_run(root: Path) -> Path:
    (run_dir,) = sorted((root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir())
    return run_dir


# --- a replayed Run with three Trials ---


def test_three_trials_of_a_scenario_are_three_trial_directories_each_with_its_trace(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)

    result = run(root, "--trials", "3")

    assert result.exit_code == 0, result.stdout
    trials = only_run(root) / "trials" / SHIPPED
    assert sorted(path.name for path in trials.iterdir()) == ["1", "2", "3"]
    for number in ("1", "2", "3"):
        assert (trials / number / "trace.jsonl").is_file()
        assert read_json(trials / number / "scores.json")["trial"] == int(number)


def test_each_replayed_trial_rewalks_its_scenarios_view_of_the_recording(tmp_path: Path) -> None:
    """Decision 19: a second Trial finds the view fresh, not spent, so nothing is invalid."""
    root = target_root(tmp_path)

    run(root, "--trials", "3")

    scorecard = read_json(only_run(root) / "scorecard.json")
    assert scorecard["counts"]["invalid"] == 0
    assert scorecard["counts"]["pass"] == 3 * 10


def test_the_run_record_and_the_scorecard_carry_the_trial_count(tmp_path: Path) -> None:
    root = target_root(tmp_path)

    run(root, "--trials", "2")

    assert read_json(only_run(root) / "run.json")["trials"] == 2
    assert read_json(only_run(root) / "scorecard.json")["trials"] == 2


def test_identical_trials_that_all_pass_have_pass_k_of_one_at_every_k(tmp_path: Path) -> None:
    """c = n = 3: C(3, k) / C(3, k) = 1 for k = 1, 2, 3."""
    root = target_root(tmp_path)

    result = run(root, "--trials", "3")

    lines = result.stdout.splitlines()
    assert (
        f"{SHIPPED}  trials 3  passed 3 of 3 decidable  pass^1 1.00  pass^2 1.00  pass^3 1.00"
        in lines
    )
    assert lines[-1] == (
        "pass^1 1.00  pass^2 1.00  pass^3 1.00  trials 3  excluded 0  sampling not configured"
    )


def test_the_default_is_one_trial(tmp_path: Path) -> None:
    root = target_root(tmp_path)

    result = runner.invoke(
        app, ["run", "--root", str(root), "--scenario", SHIPPED, "--replay", str(RECORDING)]
    )

    assert result.stdout.splitlines()[-1].startswith("pass^1 1.00  trials 1  excluded 0")
    assert read_json(only_run(root) / "run.json")["trials"] == 1


@pytest.mark.parametrize("trials", ["0", "-2"])
def test_fewer_than_one_trial_is_a_usage_error_and_nothing_is_written(
    tmp_path: Path, trials: str
) -> None:
    root = target_root(tmp_path)

    result = run(root, "--trials", trials)

    assert result.exit_code == 3
    assert "--trials must be at least 1" in result.stderr
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()


# --- the committed interrupted Run: three Scenarios, three sweeps, a slow lookup ---
#
# Sweep order is W1 L1 G1, W2 L2 G2, W3 L3 G3 (W = where-is-shipped-order, L =
# lookup-then-cancel, G = greeting-calls-no-tool). W2's lookup took 5 seconds, over
# tool_latency's 1000 ms, so W2 fails; the interrupt landed inside L3; G3 never started.


def test_an_interrupted_multi_trial_run_keeps_every_finished_trial() -> None:
    trials = INTERRUPTED / "trials"

    assert sorted(p.name for p in (trials / SHIPPED).iterdir()) == ["1", "2", "3"]
    assert sorted(p.name for p in (trials / TWO_TURNS).iterdir()) == ["1", "2", "3"]
    assert sorted(p.name for p in (trials / GREETING).iterdir()) == ["1", "2"]
    assert (INTERRUPTED / "scorecard.json").is_file()


def test_the_interrupted_trial_is_incomplete_cancelled_and_its_trace_says_so() -> None:
    trial = INTERRUPTED / "trials" / TWO_TURNS / "3"

    scores = read_json(trial / "scores.json")["scores"]
    assert {(score["verdict"], score["reason"]) for score in scores} == {
        ("incomplete", "cancelled")
    }
    last = json.loads((trial / "trace.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last["termination"] == "cancelled"


def test_the_unstarted_trial_is_named_cancelled_in_not_run_with_its_trial_number() -> None:
    scorecard = read_json(INTERRUPTED / "scorecard.json")
    record = read_json(INTERRUPTED / "run.json")

    cancelled = [entry for entry in scorecard["not_run"] if entry["reason"] == "cancelled"]
    assert cancelled == [
        {
            "scenario": GREETING,
            "suite": "orders",
            "reason": "cancelled",
            "detail": None,
            "trial": 3,
        }
    ]
    assert record["trials"] == 3


def test_pass_k_per_scenario_matches_the_formula_worked_by_hand() -> None:
    """W: 2 of 3 decidable pass -> 2/3, C(2,2)/C(3,2) = 1/3, C(2,3)/C(3,3) = 0.
    L: L3 excluded, 2 of 2 pass -> 1, 1, n/a (2 < 3). G: 2 recorded, 2 of 2 -> 1, 1, n/a."""
    scorecard = read_json(INTERRUPTED / "scorecard.json")
    by_id = {entry["id"]: entry for entry in scorecard["scenario_aggregates"]}

    shipped = by_id[SHIPPED]
    assert (shipped["trials"], shipped["decidable"], shipped["passed"]) == (3, 3, 2)
    assert shipped["pass_k"] == pytest.approx({"1": 2 / 3, "2": 1 / 3, "3": 0.0})

    two_turns = by_id[TWO_TURNS]
    assert (two_turns["trials"], two_turns["decidable"], two_turns["passed"]) == (3, 2, 2)
    assert two_turns["pass_k"] == {"1": 1.0, "2": 1.0, "3": None}

    greeting = by_id[GREETING]
    assert (greeting["trials"], greeting["decidable"], greeting["passed"]) == (2, 2, 2)
    assert greeting["pass_k"] == {"1": 1.0, "2": 1.0, "3": None}


def test_the_runs_pass_k_is_the_mean_over_scenarios_with_k_decidable_trials() -> None:
    """pass^1 = (2/3 + 1 + 1) / 3 = 8/9; pass^2 = (1/3 + 1 + 1) / 3 = 7/9; pass^3 = 0,
    from W alone, the only Scenario with three decidable Trials. One Trial excluded."""
    scorecard = read_json(INTERRUPTED / "scorecard.json")

    assert scorecard["pass_k"] == pytest.approx({"1": 8 / 9, "2": 7 / 9, "3": 0.0})
    assert scorecard["excluded_trials"] == 1
    assert scorecard["simulated_user_sampling"] is None


def test_the_slow_trial_fails_its_latency_metric_and_the_aggregate_says_by_how_much() -> None:
    scorecard = read_json(INTERRUPTED / "scorecard.json")
    shipped = next(e for e in scorecard["scenario_aggregates"] if e["id"] == SHIPPED)
    latency = next(e for e in shipped["evals"] if e["eval"] == "tool_latency")

    assert (latency["decidable"], latency["passed"], latency["direction"]) == (3, 2, "minimize")
    assert latency["abnormal"] == {"incomplete": 0, "unverifiable": 0, "invalid": 0}
    # The scripted clock gives the lookup 3 ms (one per Event between its start and end);
    # W2's reads 5000 ms later from its end on: (3 + 5003 + 3) / 3.
    assert latency["value_mean"] == pytest.approx((3 + 5003 + 3) / 3)
