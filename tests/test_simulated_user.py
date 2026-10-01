"""Seam 2: the model implementation of the Simulated User (D27; phase-5 decisions 48, 52, 53).

`ModelSimulatedUser` over a fake `ModelClient`: the prompt it renders, the request it sends,
how it reads the answer — the stop token, rule 4's four cases, the one resend without a
sampling parameter the API refused — and what `run.json` records about it. The Claude Code
client's own attempts are the decision-42 fake session, reused.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ToolUseBlock

from agentdiag.eval.render import RecordedPrompt
from agentdiag.model.claude_code import Backend, ClaudeCodeClient
from agentdiag.model.client import ModelRequest, ModelResponse, SamplingNotSupported
from agentdiag.model.replay import ReplayMismatch
from agentdiag.scenario.models import Scenario, SimulateSpec, StopWhen
from agentdiag.simulate import user
from agentdiag.simulate.configuration import (
    SimulatedUserConfiguration,
    simulated_user_configuration,
)
from agentdiag.simulate.drive import sampling_outcomes
from agentdiag.simulate.user import (
    OUTPUT_SCHEMA,
    SIMULATED_USER_MAX_TOKENS,
    ModelSimulatedUser,
    NextMessage,
    Stop,
    render_simulate_spec,
)
from agentdiag.trace import Event, TraceWriter, project_spans, read_trace
from agentdiag.trace.attributes import SIMULATED_USER_ATTEMPTS, SIMULATED_USER_FINGERPRINT
from tests.test_claude_code_client import (
    CLI,
    FakeQuery,
    accepted,
    assistant,
    init,
    rejected,
    result,
)

EVERY_FIELD = SimulateSpec(
    goal="Get order NB-1042 cancelled.",
    persona="A hurried customer who gives one piece of information at a time.",
    known_facts={"order_id": "NB-1042", "email": "carmen@example.com"},
    unknown_facts={"order_status": "processing"},
    hints=["Give the order number only when asked for it.", "Never say please twice."],
    stop_token="[DONE]",
)
NO_FIELD = SimulateSpec(goal="Get order NB-1042 cancelled.", stop_when=StopWhen(tool_called="x"))


def a_scenario(spec: SimulateSpec, *, opener: bool = True) -> Scenario:
    turns: list[Any] = ["Hi, I need to cancel an order."] if opener else []
    return Scenario(
        id="cancel-adaptively",
        title="Cancel, the customer improvising",
        turns=[*turns, {"simulate": spec.model_dump(exclude_none=True)}],
        max_turns=4,
    )


def configured(**overrides: Any) -> SimulatedUserConfiguration:
    return simulated_user_configuration(
        RecordedPrompt.of(user.PARTS), backend=Backend(kind="replay"), **overrides
    )


class FakeClient:
    """Answers each call with the next scripted body, or raises the next scripted error."""

    def __init__(self, *answers: dict[str, Any] | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return ModelResponse.from_body(request, answer)


def answered(message: str | None = None, **overrides: Any) -> dict[str, Any]:
    text = json.dumps({"message": message}) if message is not None else "no JSON here"
    body: dict[str, Any] = {
        "id": "msg_su",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-20260815",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 900, "output_tokens": 12},
    }
    body.update(overrides)
    return body


def conversation(tmp_path: Path) -> tuple[TraceWriter, list[Event]]:
    """A Trace with the opener delivered: what the Simulated User reads before Turn 2."""
    writer = TraceWriter(tmp_path / "trace.jsonl")
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    with writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented") as turn:
        turn.event("message", actor="agentdiag", role="user", content="Hi, I need to cancel.")
        turn.event("message", actor="target", role="assistant", content="Which order is it?")
    return writer, read_trace(writer.path)


def asked(
    tmp_path: Path, client: FakeClient, spec: SimulateSpec = EVERY_FIELD, **overrides: Any
) -> tuple[NextMessage | Stop, list[Event]]:
    writer, events = conversation(tmp_path)
    simulated = ModelSimulatedUser(client, configured(**overrides), spec, a_scenario(spec))
    answer = simulated.next(events, writer)
    writer.end("completed")
    writer.close()
    return answer, read_trace(writer.path)


# --- the prompt (decision 48) ---


def test_the_system_prompt_tells_the_simulated_user_every_field_in_a_fixed_order() -> None:
    """The persona, the goal, the known facts sorted by key, the unknown facts with the rule
    never to reveal them, the hints numbered in authored order, the stop rule: one literal."""
    request = ModelSimulatedUser(FakeClient(), configured(), EVERY_FIELD, a_scenario(EVERY_FIELD))

    assert request.request([]).system == (
        "You are playing a person who is writing to a service to get something done. You are "
        "that person, not an assistant: you do not help, advise or serve anyone, and you never "
        "say or suggest that you are simulated, playing a part or following instructions.\n\n"
        "## Who you are\n\n"
        "A hurried customer who gives one piece of information at a time.\n\n"
        "## Your goal\n\n"
        "Get order NB-1042 cancelled.\n\n"
        "## What you know\n\n"
        "email: carmen@example.com\n"
        "order_id: NB-1042\n\n"
        "## What you do not know\n\n"
        "These are true, but the person you are playing does not know them. Never state them, "
        "hint at them or ask about them.\n\n"
        "order_status: processing\n\n"
        "## How you behave\n\n"
        "1. Give the order number only when asked for it.\n"
        "2. Never say please twice.\n\n"
        "## How you write\n\n"
        "Write one message at a time, the way this person would type it: plain text, no stage "
        "directions, no quotation marks around it, no notes about what you are doing. When your "
        "goal is achieved, or the conversation cannot make progress, answer with exactly [DONE] "
        "and nothing else.\n\n"
        "Answer only in the structured format requested: your message goes in `message`."
    )


def test_a_spec_with_nothing_but_a_goal_says_so_in_place_of_each_empty_field() -> None:
    assert render_simulate_spec(NO_FIELD) == (
        "## Your goal\n\n"
        "Get order NB-1042 cancelled.\n\n"
        "## What you know\n\n"
        "Nothing beyond your goal.\n\n"
        "## What you do not know\n\n"
        "Nothing is kept from you.\n\n"
        "## How you behave\n\n"
        "No further guidance: behave as this person would."
    )
    system = ModelSimulatedUser(FakeClient(), configured(), NO_FIELD, a_scenario(NO_FIELD))
    assert "exactly" not in str(system.request([]).system), "no stop token, no stop rule"


def test_the_conversation_so_far_is_the_one_user_message(tmp_path: Path) -> None:
    """One message whichever Backend the Run takes: the Claude Code Backend sends one user
    message and refuses a transcript of turns (decision 24)."""
    _, events = conversation(tmp_path)
    request = ModelSimulatedUser(FakeClient(), configured(), EVERY_FIELD, a_scenario(EVERY_FIELD))

    assert request.request(events).messages == [
        {
            "role": "user",
            "content": (
                'The conversation so far, oldest first. "You" is you; "They" is the service you '
                "are writing to.\n\n"
                "You: Hi, I need to cancel.\n\n"
                "They: Which order is it?\n\n"
                "Write your next message."
            ),
        }
    ]


def test_with_no_turn_before_it_the_simulated_user_is_asked_for_its_opening_message() -> None:
    spec = EVERY_FIELD
    request = ModelSimulatedUser(FakeClient(), configured(), spec, a_scenario(spec, opener=False))

    assert request.request([]).messages == [
        {
            "role": "user",
            "content": "(The conversation has not started. Write your opening message.)",
        }
    ]


def test_the_request_asks_for_a_message_as_structured_output_at_the_configured_effort() -> None:
    simulated = ModelSimulatedUser(
        FakeClient(), configured(effort="low"), NO_FIELD, a_scenario(NO_FIELD)
    )

    body = simulated.request([]).body()

    assert body["model"] == "claude-sonnet-5"
    assert body["max_tokens"] == SIMULATED_USER_MAX_TOKENS == 2000
    assert body["output_config"] == {
        "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
        "effort": "low",
    }
    assert OUTPUT_SCHEMA == {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
        "additionalProperties": False,
    }
    assert "temperature" not in body


def test_a_declared_temperature_is_sent_and_the_fingerprint_rides_beside_the_body() -> None:
    configuration = configured(temperature=0.7)
    request = ModelSimulatedUser(
        FakeClient(), configuration, NO_FIELD, a_scenario(NO_FIELD)
    ).request([])

    assert request.body()["temperature"] == 0.7
    assert request.fingerprint == configuration.fingerprint
    assert "fingerprint" not in request.body()


# --- the answer ---


def test_a_message_is_the_next_user_message_and_its_call_is_the_simulated_users_span(
    tmp_path: Path,
) -> None:
    answer, events = asked(tmp_path, FakeClient(answered("It's NB-1042.")))

    assert answer == NextMessage(text="It's NB-1042.")
    (span,) = [s for s in project_spans(events) if s.kind == "llm_call"]
    assert (span.actor, span.name) == ("simulated_user", "chat claude-sonnet-5")
    assert span.attributes[SIMULATED_USER_FINGERPRINT] == configured().fingerprint
    assert span.attributes["gen_ai.response.model"] == "claude-sonnet-5-20260815"
    assert span.attributes["llm.cost.total"] > 0
    assert [e.type for e in events if e.span_id == span.span_id and e.type != "blob"] == [
        "span/start",
        "request",
        "response",
        "span/end",
    ]


def test_the_stop_token_is_returned_as_written_for_the_driver_loop_to_read(
    tmp_path: Path,
) -> None:
    """Amended decision 48: the stop token is detected in `drive`, for every implementation;
    the model implementation returns the message as the model wrote it."""
    answer, _ = asked(tmp_path, FakeClient(answered("Thanks, that's all. [DONE]")))

    assert answer == NextMessage(text="Thanks, that's all. [DONE]")


def test_a_replay_that_does_not_fit_is_agentdiags_and_leaves_the_call_as_it_raised(
    tmp_path: Path,
) -> None:
    """Amended decision 48: a `ReplayMismatch` is agentdiag's recording being wrong, so it
    propagates and the Trial loop ends the Trial `agentdiag_error`, not the Simulated
    User's error."""
    writer, events = conversation(tmp_path)
    simulated = ModelSimulatedUser(
        FakeClient(ReplayMismatch("no exchange matches")),
        configured(),
        EVERY_FIELD,
        a_scenario(EVERY_FIELD),
    )

    with pytest.raises(ReplayMismatch):
        simulated.next(events, writer)
    writer.end("agentdiag_error")
    writer.close()
    (span,) = [s for s in project_spans(read_trace(writer.path)) if s.kind == "llm_call"]
    assert span.status == "error"


@pytest.mark.parametrize(
    ("body", "error_type", "detail"),
    [
        (
            answered("x", content=[], stop_reason="refusal", stop_details={"category": "cyber"}),
            "SimulatedUserRefused",
            "The Simulated User returned no message: the Simulated User refused to answer "
            "(category cyber)",
        ),
        (
            answered("x", stop_reason="max_tokens"),
            "SimulatedUserRanOutOfTokens",
            "The Simulated User returned no message: the Simulated User ran out of tokens before "
            "answering (max_tokens 2000)",
        ),
        (answered(None), "ValidationError", "The Simulated User's output did not fit the schema"),
        (
            RuntimeError("connection reset"),
            "RuntimeError",
            "The Simulated User call failed: RuntimeError: connection reset",
        ),
    ],
    ids=["refusal", "max_tokens", "schema failure", "client exception"],
)
def test_rule_4_is_the_simulated_users_error_with_an_error_event_inside_its_call(
    tmp_path: Path, body: dict[str, Any] | Exception, error_type: str, detail: str
) -> None:
    """Decision 48: never a retry; `perform.simulated_user_failed` then scores every Eval."""
    answer, events = asked(tmp_path, FakeClient(body))

    assert isinstance(answer, Stop)
    assert answer.termination == "simulated_user_error"
    assert answer.detail.startswith(detail)
    (error,) = [e for e in events if e.type == "error"]
    assert (error.actor, (error.model_extra or {})["error_type"]) == ("simulated_user", error_type)
    (span,) = [s for s in project_spans(events) if s.kind == "llm_call"]
    assert error.span_id == span.span_id


# --- sampling (decision 53) ---


def refused(key: str = "temperature") -> SamplingNotSupported:
    return SamplingNotSupported({key: "not_supported"}, RuntimeError(f"{key} is not supported"))


def test_a_refused_temperature_is_recorded_and_the_message_asked_once_more_without_it(
    tmp_path: Path,
) -> None:
    client = FakeClient(refused(), answered("It's NB-1042."))

    answer, events = asked(tmp_path, client, temperature=0.7)

    assert answer == NextMessage(text="It's NB-1042.")
    assert [request.sampling for request in client.requests] == [{"temperature": 0.7}, {}]
    first, second = [s for s in project_spans(events) if s.kind == "llm_call"]
    assert first.status == "error"
    assert first.attributes["agentdiag.sampling.temperature"] == "not_supported"
    assert second.status == "ok"
    assert "agentdiag.sampling.temperature" not in second.attributes, "not sent the second time"
    assert sampling_outcomes(events) == [{"temperature": "not_supported"}, {}]


def test_an_accepted_temperature_is_recorded_as_accepted_per_call(tmp_path: Path) -> None:
    _, events = asked(tmp_path, FakeClient(answered("It's NB-1042.")), temperature=0.7)

    (span,) = [s for s in project_spans(events) if s.kind == "llm_call"]
    assert span.attributes["agentdiag.sampling.temperature"] == "accepted"
    assert sampling_outcomes(events) == [{"temperature": "accepted"}]


def test_a_second_refusal_is_the_simulated_users_error(tmp_path: Path) -> None:
    answer, _ = asked(tmp_path, FakeClient(refused(), refused()), temperature=0.7)

    assert isinstance(answer, Stop) and answer.termination == "simulated_user_error"


# --- the Claude Code Backend (decisions 42, 53) ---


def test_an_answer_the_claude_code_cli_re_prompted_for_counts_its_attempts_on_the_span(
    tmp_path: Path,
) -> None:
    """The author's instruction of 2026-09-25: the Simulated User answers through structured
    output, so on `claude_code` the CLI's validate-and-retry applies and is in the record."""
    good = {"message": "It's NB-1042."}
    session = FakeQuery(
        init(),
        assistant(
            ToolUseBlock(id="toolu_A", name="StructuredOutput", input={"msg": "NB-1042"}),
            message_id="msg_A",
            stop_reason="tool_use",
        ),
        rejected("toolu_A", "root: must have required property 'message'"),
        assistant(
            ToolUseBlock(id="toolu_B", name="StructuredOutput", input=good),
            message_id="msg_B",
            stop_reason="tool_use",
        ),
        accepted("toolu_B"),
        result(num_turns=3, structured_output=good, result=None),
    )
    writer, events = conversation(tmp_path)
    simulated = ModelSimulatedUser(
        ClaudeCodeClient(CLI, query=session),
        configured(),
        NO_FIELD,
        a_scenario(NO_FIELD),
    )

    answer = simulated.next(events, writer)
    writer.end("completed")
    writer.close()

    assert answer == NextMessage(text="It's NB-1042.")
    (span,) = [s for s in project_spans(read_trace(writer.path)) if s.kind == "llm_call"]
    assert span.attributes[SIMULATED_USER_ATTEMPTS] == 2


# --- what run.json records (decision 52) ---


def test_run_json_records_the_simulated_user_configuration_with_its_fingerprint() -> None:
    configuration = configured()

    assert configuration.model_dump(mode="json") == {
        "implementation": "model",
        "model": "claude-sonnet-5",
        "effort": None,
        "backend": {"kind": "replay", "cli_version": None},
        "prompt": {
            "version": "simulated_user.v1",
            "text": user.TEMPLATE,
            "fingerprint": RecordedPrompt.of(user.PARTS).fingerprint,
        },
        "sampling": {},
        "fingerprint": configuration.fingerprint,
    }
    assert len(configuration.fingerprint) == 64


@pytest.mark.parametrize(
    "change",
    [
        {"model": "claude-haiku-4-5"},
        {"effort": "high"},
        {"temperature": 0.7},
    ],
    ids=["model", "effort", "sampling"],
)
def test_the_fingerprint_moves_with_the_model_the_effort_and_the_sampling(
    change: dict[str, Any],
) -> None:
    assert configured(**change).fingerprint != configured().fingerprint


def test_the_fingerprint_moves_with_the_prompt_and_the_backend() -> None:
    other = RecordedPrompt(version="simulated_user.v2", text="else", fingerprint="0" * 64)

    assert (
        simulated_user_configuration(other).fingerprint
        != simulated_user_configuration(RecordedPrompt.of(user.PARTS)).fingerprint
    )
    assert (
        configured().fingerprint
        != simulated_user_configuration(
            RecordedPrompt.of(user.PARTS),
            backend=Backend(kind="claude_code", cli_version="2.1.280"),
        ).fingerprint
    )
