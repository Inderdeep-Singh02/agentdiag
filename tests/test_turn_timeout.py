"""Seam 1: a Turn that overruns its `turn_timeout` ends the Trial `timeout` (decision 56).

`turn_timeout` is per-environment Adapter configuration, default 600 s; the driver loop runs
each `deliver` on a worker thread and waits that long. On expiry the Turn gets an `error`
Event and no assistant message — the error text is never the Target's reply — every Eval
scores `incomplete` / `timeout`, and the Trace is sealed at `trace/end`, so the abandoned
Target thread cannot append to it when it wakes (the assessment's note 3).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from tests.fakes import sleeping_target

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
runner = CliRunner()

SUITE = {
    "schema_version": 1,
    "target": "toy-order-desk",
    "scenarios": [
        {
            "id": "slow-answer",
            "title": "The Target takes longer than a Turn may",
            "turns": ["Where is order NB-1042?"],
            "evals": [{"must_say_any": ["processing"]}, {"expect_tools": ["lookup_order"]}],
        }
    ],
}


def sleeping_root(tmp_path: Path, **environment: Any) -> Path:
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs"))
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["adapter"]["environments"]["local"] = {
        "factory": "tests.fakes.sleeping_target:make_target",
        "tools": "tests.fakes.sleeping_target:TOOLS",
        **environment,
    }
    manifest["suites"] = ["suites/orders.yaml"]
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "orders.yaml").write_text(
        yaml.safe_dump(SUITE), encoding="utf-8"
    )
    return root


def invoke(root: Path) -> Any:
    return runner.invoke(app, ["run", "--root", str(root)])


def run_dir(root: Path) -> Path:
    (only,) = (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir()
    return only


def trace_lines(root: Path) -> list[dict[str, Any]]:
    path = run_dir(root) / "trials" / "slow-answer" / "1" / "trace.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_turn_past_its_timeout_ends_the_trial_timeout_with_no_reply_from_the_target(
    tmp_path: Path,
) -> None:
    sleeping_target.WOKE.clear()
    sleeping_target.LATE.clear()
    root = sleeping_root(tmp_path, turn_timeout=0.2, options={"sleep_s": 1.0})

    result = invoke(root)

    assert result.exit_code == 2, result.stdout
    events = trace_lines(root)
    assert events[-1]["type"] == "trace/end"
    assert events[-1]["termination"] == "timeout"
    assert events[-1]["error"] == "the Target did not answer Turn 1 within 0.2 s"
    (error,) = [e for e in events if e["type"] == "error"]
    assert (error["actor"], error["error_type"], error["span_id"]) == (
        "agentdiag",
        "TurnTimeout",
        "turn-1",
    )
    assert not [e for e in events if e["type"] == "message" and e["role"] == "assistant"]
    turn_end = next(e for e in events if e["type"] == "span/end" and e["span_id"] == "turn-1")
    assert turn_end["status"] == "error"
    scores = json.loads(
        (run_dir(root) / "trials" / "slow-answer" / "1" / "scores.json").read_text()
    )["scores"]
    assert {(s["verdict"], s["reason"]) for s in scores} == {("incomplete", "timeout")}


def test_the_trace_ends_at_trace_end_even_though_the_abandoned_target_writes_later(
    tmp_path: Path,
) -> None:
    sleeping_target.WOKE.clear()
    sleeping_target.LATE.clear()
    root = sleeping_root(tmp_path, turn_timeout=0.2, options={"sleep_s": 1.0})
    invoke(root)
    before = trace_lines(root)

    assert sleeping_target.WOKE.wait(10), "the fake Target never woke"

    after = trace_lines(root)
    assert after == before
    assert after[-1]["type"] == "trace/end"
    assert sleeping_target.LATE == ["TraceSealed"]


def test_a_manifest_that_writes_no_turn_timeout_runs_under_ten_minutes_and_says_so(
    tmp_path: Path,
) -> None:
    root = sleeping_root(tmp_path, options={"sleep_s": 0.0})

    result = invoke(root)

    record = json.loads((run_dir(root) / "run.json").read_text(encoding="utf-8"))
    assert record["adapter"]["turn_timeout_s"] == 600.0
    assert "turn_timeout" not in record["adapter"]["config"]
    assert result.exit_code == 1, result.stdout  # the fake says nothing about processing


def test_the_turn_timeout_in_force_is_recorded_from_the_environment(tmp_path: Path) -> None:
    root = sleeping_root(tmp_path, turn_timeout=45, options={"sleep_s": 0.0})

    invoke(root)

    record = json.loads((run_dir(root) / "run.json").read_text(encoding="utf-8"))
    assert record["adapter"]["turn_timeout_s"] == 45.0


def test_a_turn_timeout_that_is_not_a_positive_number_is_refused_at_preflight(
    tmp_path: Path,
) -> None:
    for bad in (0, -5, "ten minutes", True):
        root = sleeping_root(tmp_path / str(bad), turn_timeout=bad)

        result = invoke(root)

        assert result.exit_code == 3, result.stdout
        assert "turn_timeout" in result.stdout
        assert "positive number of seconds" in result.stdout
        assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()
