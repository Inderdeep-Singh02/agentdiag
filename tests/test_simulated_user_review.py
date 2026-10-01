"""Seam 2: the Simulated User reviewer rewrites a fail the Simulated User caused (D28;
phase-5 decision 57, ADR-0003 §3 and its consequence).

Over the worked recordings `scripts/record_fixtures.py --scripted` writes for the test-only
Suite (`tests/simulated.py`): a Simulated User that states the order status it was told it
does not know (`leak`, the fault helped the Target), one that answers the stop token before
its goal is reached (`premature-stop`, it hindered it), and three reviews of a fail the
Target earned on its own — a finding of `none`, a finding citing no Span, and a reviewer
whose output does not fit its schema. Every answer is worked, so its wording is the fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.eval.simulated_user_review import GUARD, PARTS
from agentdiag.trace import read_trace, resolve_blobs
from tests.simulated import RECORDINGS, only_run, simulated_root

runner = CliRunner()

LEAK = "leaks-the-order-status"
STOPS = "stops-before-the-cancel"
REFUND = "promises-a-refund-date"


def run(root: Path, scenario: str, recording: str, *extra: str) -> Any:
    return runner.invoke(
        app,
        [
            "run",
            "--root",
            str(root),
            "--scenario",
            scenario,
            *extra,
            "--replay",
            str(RECORDINGS / f"simulated_user_review-{recording}.jsonl"),
        ],
    )


def scores(run_dir: Path, scenario: str) -> dict[str, dict[str, Any]]:
    path = run_dir / "trials" / scenario / "1" / "scores.json"
    return {score["eval"]: score for score in json.loads(path.read_text())["scores"]}


def judgement(run_dir: Path, scenario: str) -> list[Any]:
    return resolve_blobs(read_trace(run_dir / "trials" / scenario / "1" / "judgement.jsonl"))


def review_note(run_dir: Path, scenario: str) -> dict[str, Any]:
    (note,) = [
        e.model_extra or {}
        for e in judgement(run_dir, scenario)
        if e.type == "note" and (e.model_extra or {}).get("about") == "simulated_user_review"
    ]
    return note


def test_a_leaked_hidden_fact_rewrites_every_fail_invalid_helped_and_leaves_the_passes(
    tmp_path: Path,
) -> None:
    root = simulated_root(tmp_path)

    result = run(root, LEAK, "leak")

    assert result.exit_code == 2, result.stdout
    by_eval = scores(only_run(root), LEAK)
    ordered = by_eval["expect_tools_order"]
    assert (ordered["verdict"], ordered["fault_source"], ordered["fault_direction"]) == (
        "invalid",
        "simulated_user",
        "helped",
    )
    assert ordered["evidence"] == ["turn-2"]
    assert ordered["rationale"].startswith(
        "The Simulated User reviewer found leaked_hidden_fact (helped): In turn-2 the "
        "Simulated User stated that NB-1042 is still processing"
    )
    assert "handed the Target what it should have fetched" in ordered["rationale"]
    assert " The expect_tools_order had scored fail: " in ordered["rationale"]
    assert ordered["source"]["kind"] == "mechanical", "the Score keeps the source of its Eval"
    assert by_eval["expect_tools"]["verdict"] == "pass", "a pass is never reviewed"


def test_a_reviewed_trial_with_no_judged_eval_gets_a_diagnosis_told_the_review(
    tmp_path: Path,
) -> None:
    """Amended decision 57: the Diagnosis runs when a judged Eval was performed or a review
    applied; the leak's Trial has mechanical Evals only."""
    root = simulated_root(tmp_path)
    run(root, LEAK, "leak")

    events = judgement(only_run(root), LEAK)
    names = {e.span_id: (e.model_extra or {})["name"] for e in events if e.type == "span/start"}
    assert list(names.values()) == ["judge simulated_user_review", "judge diagnosis"]
    (diagnosis,) = [
        (e.model_extra or {})["body"]["messages"][0]["content"]
        for e in events
        if e.type == "request" and names[str(e.span_id)] == "judge diagnosis"
    ]
    assert (
        "- expect_tools_order: invalid (simulated_user), from mechanical. Evidence: turn-2."
        in diagnosis
    )
    assert (
        "\n\n## The Simulated User review\n\nfinding leaked_hidden_fact (helped), citing "
        "turn-2: In turn-2 the Simulated User stated that NB-1042 is still processing"
    ) in diagnosis


def test_the_reviewer_reads_the_spec_as_the_simulated_user_did_and_the_guard(
    tmp_path: Path,
) -> None:
    root = simulated_root(tmp_path)
    run(root, LEAK, "leak")

    events = judgement(only_run(root), LEAK)
    review = next(
        (e.model_extra or {})["body"]
        for e in events
        if e.type == "request"
        and (e.model_extra or {})["body"]["output_config"]["format"]["schema"]
        == PARTS.output_schema
    )
    prompt = review["messages"][0]["content"]
    assert review["model"] == "claude-opus-5"
    assert GUARD in prompt
    assert "## What you do not know" in prompt and "order_status: processing" in prompt
    assert "[turn-1] user (scripted): I want to cancel an order." in prompt
    assert "[turn-2] user (simulated): NB-1042. It's still processing" in prompt
    assert "The conversation ended: stop_when (tool_called cancel_order held after Turn 2)" in (
        prompt
    )
    assert "- expect_tools_order: " in prompt
    assert "- expect_tools: " not in prompt, "only the fails are the reviewer's to read"


def test_a_stop_token_answered_before_the_goal_is_invalid_hindered(tmp_path: Path) -> None:
    root = simulated_root(tmp_path)

    result = run(root, STOPS, "premature-stop")

    assert result.exit_code == 2, result.stdout
    score = scores(only_run(root), STOPS)["expect_tools"]
    assert (score["verdict"], score["fault_source"], score["fault_direction"]) == (
        "invalid",
        "simulated_user",
        "hindered",
    )
    assert score["evidence"] == ["turn-1"]
    printed = runner.invoke(app, ["show", str(only_run(root)), STOPS]).stdout
    assert "fault      simulated_user (hindered)" in printed
    judged = next(line for line in printed.splitlines() if "  simulated_user_review  " in line)
    assert judged.startswith("  judge-1  simulated_user_review  claude-opus-5")
    assert "note  finding stopped_early (hindered): After turn-1" in printed


def test_a_finding_with_no_direction_is_still_applied_with_direction_none(
    tmp_path: Path,
) -> None:
    """Amended decision 57: the fault is the Simulated User's whether or not the reviewer can
    say which way it pushed; `FaultDirection` carries `none` for that (ADR-0003 §3)."""
    root = simulated_root(tmp_path)

    result = run(root, LEAK, "direction-none")

    assert result.exit_code == 2, result.stdout
    ordered = scores(only_run(root), LEAK)["expect_tools_order"]
    assert (ordered["verdict"], ordered["fault_source"], ordered["fault_direction"]) == (
        "invalid",
        "simulated_user",
        "none",
    )
    assert review_note(only_run(root), LEAK)["applied"] is True


def test_a_finding_of_none_changes_nothing(tmp_path: Path) -> None:
    root = simulated_root(tmp_path)

    result = run(root, REFUND, "none")

    assert result.exit_code == 1, result.stdout
    assert scores(only_run(root), REFUND)["must_not_say"]["verdict"] == "fail"
    note = review_note(only_run(root), REFUND)
    assert note["applied"] is False
    assert note["text"].startswith("finding none (none): The Simulated User gave the order number")


def test_a_finding_that_cites_no_span_of_the_trace_is_not_applied(tmp_path: Path) -> None:
    root = simulated_root(tmp_path)

    run(root, REFUND, "cites-nothing")

    assert scores(only_run(root), REFUND)["must_not_say"]["verdict"] == "fail"
    note = review_note(only_run(root), REFUND)
    assert note["applied"] is False
    assert (
        "The finding cites no Span of this Trace (it cited turn-9), so it is not applied and "
        "the Scores stand." in note["text"]
    )


def test_a_reviewer_whose_output_fits_no_schema_is_an_error_and_the_scores_stand(
    tmp_path: Path,
) -> None:
    """Rule 4: an instrument failing never changes a Verdict."""
    root = simulated_root(tmp_path)

    run(root, REFUND, "schema-failure")

    assert scores(only_run(root), REFUND)["must_not_say"]["verdict"] == "fail"
    events = judgement(only_run(root), REFUND)
    (error,) = [e for e in events if e.type == "error"]
    assert (error.model_extra or {})["error_type"] == "ValidationError"
    assert review_note(only_run(root), REFUND)["text"].startswith("No review was applied: ")


def test_the_reviewer_runs_only_on_a_trial_with_a_fail(tmp_path: Path) -> None:
    root = simulated_root(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "--root",
            str(root),
            "--scenario",
            "cancel-then-done",
            "--replay",
            str(RECORDINGS / "simulate-stop-token.jsonl"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    trial = only_run(root) / "trials" / "cancel-then-done" / "1"
    assert not (trial / "judgement.jsonl").exists(), "no judged Eval, and nothing to review"


def test_a_rescore_runs_the_reviewer_again_on_the_model_it_names(tmp_path: Path) -> None:
    root = simulated_root(tmp_path)
    run(root, LEAK, "leak")
    source = only_run(root)
    recording = str(RECORDINGS / "simulated_user_review-leak.jsonl")

    again = runner.invoke(app, ["rescore", source.name, "--root", str(root), "--replay", recording])
    other = runner.invoke(
        app,
        [
            "rescore",
            source.name,
            "--root",
            str(root),
            "--reviewer-model",
            "claude-opus-5-5",
            "--replay",
            recording,
        ],
    )

    assert again.exit_code == 2, again.stdout
    # Two rescores in one second share a timestamp, so the ids do not order them: tell
    # them apart by the reviewer model each run.json names.
    records = {
        p: json.loads((p / "run.json").read_text(encoding="utf-8"))
        for p in (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir()
        if p != source
    }
    assert len(records) == 2
    second = next(
        p for p, r in records.items() if r["judge"]["reviewer"]["model"] == "claude-opus-5-5"
    )
    first = next(p for p in records if p != second)
    assert scores(first, LEAK)["expect_tools_order"]["fault_direction"] == "helped"
    reviewer_request = next(
        (e.model_extra or {})["body"]
        for e in judgement(second, LEAK)
        if e.type == "request"
        and (e.model_extra or {})["body"]["output_config"]["format"]["schema"]
        == PARTS.output_schema
    )
    assert reviewer_request["model"] == "claude-opus-5-5"
    # The recording answers the default reviewer's request only: this one is not in it, so
    # the recorded review is left unused, which agentdiag's own recording check reports.
    overruled = scores(second, LEAK)["expect_tools_order"]
    assert (overruled["verdict"], overruled["fault_source"]) == ("invalid", "agentdiag")
    assert other.exit_code == 2, other.stdout
