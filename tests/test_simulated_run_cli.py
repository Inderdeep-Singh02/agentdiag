"""Seam 1 and 2: `agentdiag run` over a simulated Scenario, and `show` of what it recorded.

Ticket 06 (phase-5 decisions 47 to 53, 56, 60 and 61). The example's adaptive Scenario replays from
`toy-orders.jsonl`, the one recording the whole example Suite replays from; the worked
Trials replay over the test-only Suite (`tests/simulated.py`). The toy Scenario's Simulated
User message, its `goal` answer and its Diagnosis are natural answers (decision 41): these
tests assert the story — the Verdicts, the termination, which Spans exist — never their
wording, which the main session's `--capture` replaces with the model's own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.eval import simulated_user_review
from agentdiag.eval.render import RecordedPrompt
from agentdiag.model.client import ModelClient, ModelRequest, ModelResponse, SamplingNotSupported
from agentdiag.run import execute
from agentdiag.simulate import user
from agentdiag.trace import read_trace, resolve_blobs
from tests.simulated import RECORDINGS, example_root, only_run, simulated_root

runner = CliRunner()

ADAPTIVE = "cancel-without-the-order-number"
SUITE_RECORDING = RECORDINGS / "toy-orders.jsonl"


def invoke(root: Path, *arguments: str, replay: Path | None) -> Any:
    command = ["run", "--root", str(root), *arguments]
    if replay is not None:
        command += ["--replay", str(replay)]
    return runner.invoke(app, command)


def trial_file(root: Path, scenario: str, name: str) -> Path:
    return only_run(root) / "trials" / scenario / "1" / name


def trace(root: Path, scenario: str) -> list[dict[str, Any]]:
    path = trial_file(root, scenario, "trace.jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def scores(root: Path, scenario: str) -> list[dict[str, Any]]:
    return json.loads(trial_file(root, scenario, "scores.json").read_text())["scores"]


def show(root: Path, scenario: str) -> str:
    result = runner.invoke(app, ["show", str(only_run(root)), scenario])
    assert result.exit_code == 0, result.output
    return result.stdout


# --- the example's adaptive Scenario ---


def test_the_adaptive_example_replays_to_two_passes_and_exits_0(tmp_path: Path) -> None:
    root = example_root(tmp_path)

    result = invoke(root, "--scenario", ADAPTIVE, replay=SUITE_RECORDING)

    assert result.exit_code == 0, result.stdout
    assert {(s["eval"], s["verdict"]) for s in scores(root, ADAPTIVE)} == {
        ("expect_tools_order", "pass"),
        ("goal", "pass"),
    }
    events = trace(root, ADAPTIVE)
    assert events[-1]["termination"] == "stop_when"
    assert events[-1]["detail"] == "tool_called cancel_order held after Turn 2"
    users = [
        (e["actor"], e["turn"]) for e in events if e["type"] == "message" and e["role"] == "user"
    ]
    assert users == [("agentdiag", 1), ("simulated_user", 2)]
    assert "sampling none declared" in result.stdout


def test_run_json_records_the_simulated_user_and_the_reviewer(tmp_path: Path) -> None:
    root = example_root(tmp_path)

    invoke(root, "--scenario", ADAPTIVE, replay=SUITE_RECORDING)

    record = json.loads((only_run(root) / "run.json").read_text(encoding="utf-8"))
    simulated = record["simulated_user"]
    assert {key: simulated[key] for key in ("implementation", "model", "effort", "sampling")} == {
        "implementation": "model",
        "model": "claude-sonnet-5",
        "effort": None,
        "sampling": {},
    }
    assert simulated["backend"] == {"kind": "replay", "cli_version": None}
    assert simulated["prompt"] == RecordedPrompt.of(user.PARTS).model_dump()
    assert len(simulated["fingerprint"]) == 64
    assert record["judge"]["reviewer"] == {
        "model": "claude-opus-5",
        "effort": None,
        "prompt": RecordedPrompt.of(simulated_user_review.PARTS).model_dump(),
    }
    assert "stop_when" not in record["judge"]["prompts"], "no judged stop criterion selected"
    assert record["adapter"]["turn_timeout_s"] == 600.0


def test_show_prints_the_simulated_turn_the_simulated_users_span_and_what_ended_it(
    tmp_path: Path,
) -> None:
    root = example_root(tmp_path)
    invoke(root, "--scenario", ADAPTIVE, replay=SUITE_RECORDING)

    printed = show(root, ADAPTIVE)
    lines = printed.splitlines()

    assert "Termination stop_when (tool_called cancel_order held after Turn 2)" in printed
    assert "stop_when tool_called cancel_order: not held" in lines
    assert "stop_when tool_called cancel_order: held" in lines
    authoring = next(i for i, line in enumerate(lines) if line.startswith("simulate-1  "))
    assert lines[authoring].startswith("simulate-1  simulated user   ")
    assert lines[authoring + 1].startswith("  llm_call-2  chat claude-sonnet-5   ")
    turn_two = next(i for i, line in enumerate(lines) if line.startswith("Turn 2"))
    assert authoring < turn_two
    assert lines[turn_two + 1].startswith("  simulated ")
    cost = next(line for line in lines if line.startswith("Cost "))
    assert "target $" in cost and "simulated_user $" in cost


def test_the_mechanical_evals_read_the_targets_model_calls_and_never_the_simulated_users(
    tmp_path: Path,
) -> None:
    """Decision 59: `expect_tools_order` cites the Target's Spans only."""
    root = example_root(tmp_path)
    invoke(root, "--scenario", ADAPTIVE, replay=SUITE_RECORDING)

    (ordered,) = [s for s in scores(root, ADAPTIVE) if s["eval"] == "expect_tools_order"]
    assert "llm_call-2" not in ordered["evidence"]
    assert "simulate-1" not in ordered["evidence"]


def test_the_judge_is_shown_the_users_messages_and_never_the_simulated_users_own_call(
    tmp_path: Path,
) -> None:
    root = example_root(tmp_path)
    invoke(root, "--scenario", ADAPTIVE, replay=SUITE_RECORDING)

    judgement = resolve_blobs(read_trace(trial_file(root, ADAPTIVE, "judgement.jsonl")))
    (goal_span,) = [
        e.span_id
        for e in judgement
        if e.type == "span/start" and (e.model_extra or {})["name"] == "judge goal"
    ]
    (request,) = [e for e in judgement if e.type == "request" and e.span_id == goal_span]
    prompt = (request.model_extra or {})["body"]["messages"][0]["content"]
    simulated = next(
        e["content"]
        for e in trace(root, ADAPTIVE)
        if e.get("actor") == "simulated_user" and e["type"] == "message"
    )
    assert f"[turn-2] user: {simulated}" in prompt
    assert "\n[llm_call-2] " not in prompt, "the Simulated User's own call is never a Trace line"
    assert "[simulate-1]" not in prompt
    assert "tool_called cancel_order" not in prompt, "nor agentdiag's own stop checks"
    assert "trace ended: stop_when" in prompt


# --- the worked Trials over the test-only Suite ---


def test_a_simulated_user_that_refuses_scores_every_eval_invalid_its_own_fault(
    tmp_path: Path,
) -> None:
    root = simulated_root(tmp_path)

    result = invoke(
        root, "--scenario", "refuses-to-play", replay=RECORDINGS / "simulate-refusal.jsonl"
    )

    assert result.exit_code == 2, result.stdout
    (score,) = scores(root, "refuses-to-play")
    assert (score["verdict"], score["fault_source"], score["fault_direction"]) == (
        "invalid",
        "simulated_user",
        "none",
    )
    end = trace(root, "refuses-to-play")[-1]
    assert end["termination"] == "simulated_user_error"
    assert end["error"].startswith("The Simulated User returned no message: the Simulated User")


@pytest.mark.parametrize(
    ("scenario", "recording", "termination", "detail"),
    [
        (
            "cancel-then-done",
            "simulate-stop-token",
            "stop_token",
            "the Simulated User answered the stop token after Turn 1",
        ),
        ("keeps-asking", "simulate-max-turns", "max_turns", "max_turns 2 reached"),
        (
            "says-cancelled",
            "simulate-target-says-any",
            "stop_when",
            'target_says_any matched "cancelled" in Turn 1',
        ),
    ],
)
def test_each_way_a_conversation_ends_is_recorded_and_shown(
    tmp_path: Path, scenario: str, recording: str, termination: str, detail: str
) -> None:
    root = simulated_root(tmp_path)

    result = invoke(root, "--scenario", scenario, replay=RECORDINGS / f"{recording}.jsonl")

    assert result.exit_code == 0, result.stdout
    end = trace(root, scenario)[-1]
    assert (end["termination"], end["detail"]) == (termination, detail)
    assert f"Termination {termination} ({detail})" in show(root, scenario)


class RefusesSamplingOnce:
    """A client that refuses a sampling parameter the way the Messages API client does on a
    400 naming it, then answers from the Run's recording (decision 53)."""

    def __init__(self, inner: ModelClient) -> None:
        self.inner = inner

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.sampling:
            raise SamplingNotSupported(
                dict.fromkeys(request.sampling, "not_supported"),
                RuntimeError("temperature: not supported by this model"),
            )
        return self.inner.complete(request)


def test_a_refused_temperature_is_resent_without_it_and_the_scorecard_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = execute.agentdiag_client

    def refusing(cursor: Any, plan: Any) -> ModelClient | None:
        client = original(cursor, plan)
        return RefusesSamplingOnce(client) if client is not None else None

    monkeypatch.setattr(execute, "agentdiag_client", refusing)
    root = simulated_root(tmp_path)

    result = invoke(
        root,
        "--scenario",
        "cancel-then-done",
        "--simulated-user-temperature",
        "0.7",
        replay=RECORDINGS / "simulate-stop-token.jsonl",
    )

    assert result.exit_code == 0, result.stdout
    assert "sampling temperature 0.7 not_supported" in result.stdout
    record = json.loads((only_run(root) / "run.json").read_text(encoding="utf-8"))
    assert record["simulated_user"]["sampling"] == {"temperature": 0.7}
    scorecard = json.loads((only_run(root) / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["simulated_user_sampling"] == {"temperature": "0.7 not_supported"}
    spans = [
        e
        for e in trace(root, "cancel-then-done")
        if e["type"] == "span/end" and e["actor"] == "simulated_user"
    ]
    assert [s["status"] for s in spans] == ["error", "ok", "ok"], "two calls, then its Span"


def test_a_run_of_literal_turns_says_no_simulated_user_ran(tmp_path: Path) -> None:
    root = example_root(tmp_path)

    result = invoke(root, "--scenario", "where-is-shipped-order", replay=SUITE_RECORDING)

    assert "sampling not configured" in result.stdout
    record = json.loads((only_run(root) / "run.json").read_text(encoding="utf-8"))
    assert record["simulated_user"] is None


def test_a_live_simulated_run_with_no_credentials_is_refused_citing_the_simulate_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D16, decision 49: a `simulate` Turn needs a model as a judged Eval does."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("anthropic.default_credentials", lambda **_: None)
    root = simulated_root(tmp_path)

    result = invoke(root, "--scenario", "cancel-then-done", replay=None)

    assert result.exit_code == 3, result.stdout
    assert (
        "A judged Eval or a simulate Turn is selected (simulate in cancel-then-done) and no "
        "credentials resolve" in result.stdout
    )
    assert "claude auth login" in result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()


def test_a_reviewer_on_the_simulated_users_own_model_is_warned_of_and_runs(
    tmp_path: Path,
) -> None:
    root = simulated_root(tmp_path)

    result = invoke(
        root,
        "--scenario",
        "cancel-then-done",
        "--reviewer-model",
        "claude-sonnet-5",
        replay=RECORDINGS / "simulate-stop-token.jsonl",
    )

    assert result.exit_code == 0, result.stdout
    assert "reviewer runs on claude-sonnet-5, the Simulated User's own model" in result.stderr


def test_show_spells_the_stop_checks_note_as_the_stop_check_writes_it() -> None:
    """`show` keeps its own copy so it can read a Run without the SDK; they must agree."""
    from agentdiag.eval import render
    from agentdiag.trace import show as show_module

    assert show_module.STOP_WHEN == render.STOP_WHEN


def test_following_a_simulated_trial_prints_each_stop_check_as_it_lands(tmp_path: Path) -> None:
    """Decision 60: `show --follow` says what the stop check found after each Turn."""
    from agentdiag.trace.show import follow_story

    root = example_root(tmp_path)
    invoke(root, "--scenario", ADAPTIVE, replay=SUITE_RECORDING)

    lines = list(follow_story(only_run(root), ADAPTIVE, wait_seconds=0, poll_ms=1))

    assert lines.count("stop_when tool_called cancel_order: not held") == 1
    assert lines.count("stop_when tool_called cancel_order: held") == 1
    assert any(line.startswith("  simulated ") for line in lines)
    assert "Termination stop_when (tool_called cancel_order held after Turn 2)" in lines


def test_a_simulated_user_effort_outside_the_closed_set_is_refused_at_preflight(
    tmp_path: Path,
) -> None:
    root = example_root(tmp_path)

    result = invoke(
        root, "--scenario", ADAPTIVE, "--simulated-user-effort", "extreme", replay=SUITE_RECORDING
    )

    assert result.exit_code == 3, result.stdout
    assert "The Simulated User's effort is one of" in result.stdout, "effort_problem's words"
    assert "got 'extreme'" in result.stdout


class AcceptsSampling:
    """A client whose API accepts the declared sampling: it answers from the Run's recording
    (made with none) and reports each sent key `accepted`, as the Messages API client does."""

    def __init__(self, inner: ModelClient) -> None:
        self.inner = inner

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = self.inner.complete(request.model_copy(update={"sampling": {}}))
        return response.model_copy(
            update={"sampling_accepted": dict.fromkeys(request.sampling, "accepted")}
        )


def run_with_temperature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wrap: Any, scenario: str, recording: str
) -> Any:
    original = execute.agentdiag_client

    def wrapped(cursor: Any, plan: Any) -> ModelClient | None:
        client = original(cursor, plan)
        return wrap(client) if client is not None else None

    monkeypatch.setattr(execute, "agentdiag_client", wrapped)
    root = simulated_root(tmp_path)
    result = invoke(
        root,
        "--scenario",
        scenario,
        "--simulated-user-temperature",
        "0.7",
        replay=RECORDINGS / f"{recording}.jsonl",
    )
    assert result.exit_code == 0, result.stdout
    return result


def test_a_declared_temperature_the_api_accepted_is_said_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run_with_temperature(
        tmp_path, monkeypatch, AcceptsSampling, "cancel-then-done", "simulate-stop-token"
    )

    assert "sampling temperature 0.7 accepted" in result.stdout


def test_a_declared_temperature_no_call_sent_is_said_not_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Amended decision 53: the opener met the criterion, so the Simulated User was never
    asked; the declared key is still said, as `not sent`, never `none declared`."""
    result = run_with_temperature(
        tmp_path, monkeypatch, AcceptsSampling, "says-cancelled", "simulate-target-says-any"
    )

    assert "sampling temperature 0.7 not sent" in result.stdout


def test_a_replay_the_simulated_users_request_does_not_fit_ends_the_trial_agentdiag_error(
    tmp_path: Path,
) -> None:
    """Amended decision 48: agentdiag's recording was wrong, not the Simulated User."""
    kept = [
        line
        for line in SUITE_RECORDING.read_text(encoding="utf-8").splitlines()
        if '"required": ["message"]' not in line
    ]
    recording = tmp_path / "no-simulated-user.jsonl"
    recording.write_text("\n".join(kept) + "\n", encoding="utf-8")
    root = example_root(tmp_path)

    result = invoke(root, "--scenario", ADAPTIVE, replay=recording)

    assert result.exit_code == 2, result.stdout
    assert trace(root, ADAPTIVE)[-1]["termination"] == "agentdiag_error"
    assert {(s["fault_source"], s["verdict"]) for s in scores(root, ADAPTIVE)} == {
        ("agentdiag", "invalid")
    }
