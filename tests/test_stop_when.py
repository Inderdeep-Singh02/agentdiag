"""Seam 2: the `judged` stop predicate, a bounded Judge call per Turn in the Trace (D27;
phase-5 decision 51, the assessment's note 2).

Over the worked recordings `scripts/record_fixtures.py --scripted` writes for the test-only
Suite: `stop_when-judged` answers `holds: false` after Turn 1 and `true` after Turn 2;
`stop_when-judged-failure` fails its first check (a schema failure) and holds after Turn 2.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.eval import stop_when
from agentdiag.eval.judged import is_judge_request, is_rescored_request
from agentdiag.eval.render import RecordedPrompt
from agentdiag.model.replay import Recording
from agentdiag.trace import project_spans, read_trace
from agentdiag.trace.attributes import JUDGE_FINGERPRINT
from tests.simulated import RECORDINGS, only_run, simulated_root

runner = CliRunner()

JUDGED = "judged-stop"
SURVIVES = "judged-stop-survives-a-failed-check"


def run(root: Path, scenario: str, recording: str) -> Any:
    return runner.invoke(
        app,
        [
            "run",
            "--root",
            str(root),
            "--scenario",
            scenario,
            "--replay",
            str(RECORDINGS / f"{recording}.jsonl"),
        ],
    )


def trace_path(root: Path, scenario: str) -> Path:
    return only_run(root) / "trials" / scenario / "1" / "trace.jsonl"


def test_a_judged_criterion_is_asked_after_each_turn_and_ends_the_trial_when_it_holds(
    tmp_path: Path,
) -> None:
    root = simulated_root(tmp_path)

    result = run(root, JUDGED, "stop_when-judged")

    assert result.exit_code == 0, result.stdout
    events = read_trace(trace_path(root, JUDGED))
    checks = [s for s in project_spans(events) if s.kind == "llm_call" and s.actor == "agentdiag"]
    assert [(s.span_id, s.name, s.parent_span_id, s.turn) for s in checks] == [
        ("llm_call-2", "stop_when judged", None, None),
        ("llm_call-7", "stop_when judged", None, None),
    ]
    for check in checks:
        assert check.attributes["agentdiag.eval"] == "stop_when"
        assert len(check.attributes[JUDGE_FINGERPRINT]) == 64
        assert check.attributes["gen_ai.request.max_tokens"] == stop_when.STOP_WHEN_MAX_TOKENS
        assert check.attributes["llm.cost.total"] > 0
    notes = [
        (e.span_id, (e.model_extra or {})["holds"], (e.model_extra or {})["text"])
        for e in events
        if e.type == "note"
    ]
    assert notes == [
        (
            "llm_call-2",
            False,
            "judged: Nothing is cancelled yet: in llm_call-1 the Target only asked which "
            "order it is.",
        ),
        (
            "llm_call-7",
            True,
            "judged: cancel_order in tool_call-1 cancelled NB-1042 and llm_call-6 told the "
            "customer so.",
        ),
    ]
    end = events[-1].model_extra or {}
    assert (end["termination"], end["detail"]) == ("stop_when", notes[-1][2])


def test_the_checks_are_agentdiags_cost_and_show_prices_them_under_it(tmp_path: Path) -> None:
    root = simulated_root(tmp_path)
    run(root, JUDGED, "stop_when-judged")

    result = runner.invoke(app, ["show", str(only_run(root)), JUDGED])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    cost = next(line for line in lines if line.startswith("Cost "))
    assert "agentdiag $" in cost
    assert any(
        line.startswith("llm_call-2  stop_when judged   ") and line.endswith("holds no")
        for line in lines
    )
    assert any(line.startswith("llm_call-7  stop_when judged   ") for line in lines)


def test_a_check_that_fails_neither_stops_the_conversation_nor_fails_the_trial(
    tmp_path: Path,
) -> None:
    """Decision 51: agentdiag's instrument failing is never read as the criterion met."""
    root = simulated_root(tmp_path)

    result = run(root, SURVIVES, "stop_when-judged-failure")

    assert result.exit_code == 0, result.stdout
    events = read_trace(trace_path(root, SURVIVES))
    failed = next(s for s in project_spans(events) if s.span_id == "llm_call-2")
    (error,) = [e for e in events if e.type == "error"]
    assert (error.span_id, error.actor) == (failed.span_id, "agentdiag")
    first = next(e for e in events if e.type == "note")
    assert (first.model_extra or {})["holds"] is False
    assert (first.model_extra or {})["text"].startswith(
        "judged: the check failed, so it does not hold: The Judge's output did not fit"
    )
    assert [e.turn for e in events if e.type == "message" and e.actor == "simulated_user"] == [2]
    assert (events[-1].model_extra or {})["termination"] == "stop_when"


def test_run_json_records_the_stop_checks_prompt_when_a_judged_criterion_is_selected(
    tmp_path: Path,
) -> None:
    root = simulated_root(tmp_path)
    run(root, JUDGED, "stop_when-judged")

    record = json.loads((only_run(root) / "run.json").read_text(encoding="utf-8"))
    assert (
        record["judge"]["prompts"]["stop_when"] == RecordedPrompt.of(stop_when.PARTS).model_dump()
    )
    assert record["judge"]["prompts"]["stop_when"]["version"] == "stop_when.v1"
    assert record["judge"]["prompts"]["diagnosis"]["version"] == "diagnosis.v5", (
        "recorded whenever a Judge configuration exists (amended 57)"
    )


def test_a_stop_check_is_a_judge_request_that_a_rescore_leaves_alone() -> None:
    """Decision 51: the recording check of a `run` accounts for it; a `rescore` drives
    nothing, so it never asks for it again."""
    (check, *_) = [
        exchange.request
        for exchange in Recording.load(RECORDINGS / "stop_when-judged.jsonl").exchanges
        if stop_when.is_stop_when_request(exchange.request)
    ]

    assert is_judge_request(check)
    assert not is_rescored_request(check)
