"""Seam 2's live smoke tests: every judged Eval against the real model, when credentials are.

Skipped, never failed, when nothing resolves (spec, Testing Decisions). Their job is not to
assert a Verdict — a real model may reasonably judge a fixture Trial either way — but to
prove the request each replay test sends is one the API accepts: the structured-output
schema of every judged Eval and of the Diagnosis, the model id, the absence of sampling
parameters. Everything a recording cannot tell you.

The client is the one a Run would build, `live_client(backend_for(source, False), source)`
over `source = resolve()`: with an API key these reach the Messages API and with only a
Claude Code login they reach the Claude Code CLI (ticket 19). One more test drives
`ClaudeCodeClient` directly, whatever else resolves, because that backend's body is
reassembled and only a real CLI can show it fits. `tests/conftest.py` leaves the Claude
Code probe on for these tests alone.

Marked `live` and deselected by default (`pyproject.toml`). Run them with:

    uv run pytest -m live
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentdiag.eval import diagnosis
from agentdiag.eval.judge import Judge, JudgeAnswer, JudgeFailure
from agentdiag.eval.judged import judge_function, parts_of
from agentdiag.eval.judged_score import judge_fingerprint
from agentdiag.eval.render import JudgeContext, render_judge_prompt
from agentdiag.model import claude_code
from agentdiag.model.claude_code import Backend, ClaudeCodeClient, backend_for, live_client
from agentdiag.model.credentials import CredentialSource, resolve
from agentdiag.run.preflight import preflight
from agentdiag.scenario.models import EvalDeclaration
from agentdiag.scenario.select import Selection
from agentdiag.trace import TraceWriter, read_trace
from tests.fakes.workspace import the_target

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
TRACES = REPO / "tests" / "fixtures" / "traces"
RECORDING = REPO / "tests" / "fixtures" / "recordings" / "toy-orders.jsonl"
JUDGE_MODEL = "claude-opus-5"

SCENARIO_OF = {
    "prompt_adherence": "cancel-processing-order",
    "goal": "delivered-order-cannot-be-cancelled",
    "guardrails": "cancel-with-a-promised-refund-date",
    "data_grounding": "order-details-come-from-the-lookup",
    "data_query": "status-lookup-asks-for-the-named-order",
    "tool_choice": "cancel-looks-up-then-cancels",
}
"""The example Scenario that exercises each judged Eval."""


@pytest.fixture
def resolved() -> tuple[Backend, CredentialSource]:
    """The Backend a Run would take here, or a skip when nothing resolves.

    Resolved inside the test, never at import: collecting this file must not run the
    Claude Code probes for a suite that deselects `live`.
    """
    source = resolve()
    chosen = backend_for(source, replay=False)
    if source is None or chosen is None:
        pytest.skip("no credentials resolve")
    return chosen, source


def live_judge(resolved: tuple[Backend, CredentialSource]) -> Judge:
    backend, source = resolved
    return Judge(live_client(backend, source), JUDGE_MODEL, None, backend=backend)


def context(eval_name: str) -> JudgeContext:
    scenario_id = SCENARIO_OF.get(eval_name, "cancel-processing-order")
    plan = preflight(
        the_target(EXAMPLE), Selection(scenario=[scenario_id]), RECORDING, dry_run=True
    )
    scenario = plan.selected[0].scenario
    declaration = next(
        (d for d in scenario.evals if d.eval == eval_name), EvalDeclaration(eval=eval_name)
    )
    return JudgeContext.of(
        scenario,
        declaration,
        read_trace(TRACES / f"{scenario_id}.trace.jsonl"),
        fidelity="instrumented",
        notes=plan.judge.notes if plan.judge else None,
        tool_kinds=plan.tool_kinds,
    )


def judgement(tmp_path: Path) -> TraceWriter:
    writer = TraceWriter(tmp_path / "judgement.jsonl")
    writer.start(trace_id="live/judged/1", scenario="live", run="live", trial=1)
    return writer


@pytest.mark.live
@pytest.mark.parametrize("eval_name", sorted(SCENARIO_OF))
def test_each_judged_eval_produces_a_score_against_the_real_model(
    eval_name: str, tmp_path: Path, resolved: tuple[Backend, CredentialSource]
) -> None:
    writer = judgement(tmp_path)
    scores = judge_function(eval_name)(context(eval_name), live_judge(resolved), writer)
    writer.end("completed")
    writer.close()

    assert scores
    for score in scores:
        assert score.eval == eval_name
        assert score.source.kind in {"judge", "mechanical"}
        assert score.source.requested_model in {JUDGE_MODEL, None}
        # A schema the API rejected would make every Score invalid: that is what this
        # test exists to catch before a recording hides it.
        assert score.verdict != "invalid", score.rationale
        # The resolved model may carry a date suffix the alias hides, which is the whole
        # reason `resolved_model` is a separate field (D12).
        if score.source.resolved_model is not None:
            assert score.source.resolved_model.startswith(JUDGE_MODEL)


@pytest.mark.live
def test_a_diagnosis_is_produced_against_the_real_model(
    tmp_path: Path, resolved: tuple[Backend, CredentialSource]
) -> None:
    writer = judgement(tmp_path)
    judge = live_judge(resolved)
    scores = judge_function("prompt_adherence")(context("prompt_adherence"), judge, writer)
    diagnosis.judge(context("diagnosis"), judge, writer, scores=scores)
    writer.end("completed")
    writer.close()

    notes = [
        event
        for event in map(json.loads, (tmp_path / "judgement.jsonl").read_text().splitlines())
        if event["type"] == "note" and event.get("about") == "diagnosis"
    ]
    assert len(notes) == 1
    assert not notes[0]["text"].startswith("No Diagnosis was produced"), notes[0]["text"]


@pytest.mark.live
def test_the_claude_code_client_answers_a_judged_eval_through_the_cli(tmp_path: Path) -> None:
    """Whatever else resolves: the reassembled body, the structured output and the CLI
    version, from a real `query()`. Skipped when no logged-in Claude Code CLI is found."""
    cli = claude_code.cli()  # the probe itself, on purpose: this test exists to reach the CLI
    if cli is None:
        pytest.skip("no logged-in Claude Code CLI")
    backend = Backend(kind="claude_code", cli_version=cli.version)
    judge = Judge(ClaudeCodeClient(cli), JUDGE_MODEL, None, backend=backend)
    parts = parts_of("goal")
    prompt = render_judge_prompt(parts, context("goal"))
    assert prompt is not None
    writer = judgement(tmp_path)

    fingerprint = judge_fingerprint(parts, None, JUDGE_MODEL, None, backend)
    answer = judge.ask(parts, prompt, writer, eval="goal", eval_id=None, fingerprint=fingerprint)
    writer.end("completed")
    writer.close()

    assert isinstance(answer, JudgeAnswer | JudgeFailure)
    events = [json.loads(line) for line in (tmp_path / "judgement.jsonl").read_text().splitlines()]
    responses = [event for event in events if event["type"] == "response"]
    # No response Event means the call raised: the failure's detail is the CLI's words.
    assert len(responses) == 1, getattr(answer, "detail", answer)
    (response,) = responses
    assert response["body"]["claude_code"]["cli_version"] == cli.version
    end = next(event for event in events if event["type"] == "span/end")
    assert end["attributes"]["agentdiag.cost.check"] in {"agrees", "differs", "unpriced"}
