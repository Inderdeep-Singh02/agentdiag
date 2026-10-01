"""Seam 2: the Claude Code backend — one `query()` per Judge call, and what comes back.

Every test drives `ClaudeCodeClient.complete` against a fake `query` that records the
`ClaudeAgentOptions` it was handed and yields the SDK's own dataclasses, so nothing here
spawns the CLI or reaches a model. The options and the body are asserted as literals: the
options are what keeps a Judge call from turning into a Claude Code session (its tools, its
settings, its system prompt), and the body is what `judgement.jsonl` and a recording keep
(phase-5 interfaces, ticket 19, decisions 22 to 24).
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, get_args

import anyio
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    Message,
    ResultMessage,
    ServerToolUseBlock,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from agentdiag.model import claude_code
from agentdiag.model.claude_code import (
    Backend,
    ClaudeCodeCli,
    ClaudeCodeClient,
    ClaudeCodeError,
)
from agentdiag.model.client import (
    ModelRequest,
    RecordingModelClient,
    ReplayModelClient,
    SamplingNotSupported,
)
from agentdiag.model.replay import Recording, ReplayCursor
from agentdiag.types import STRUCTURED_OUTPUT, BackendKind

CLI = ClaudeCodeCli(path="/opt/claude/bin/claude", version="2.1.280")
SCHEMA: dict[str, Any] = {"type": "object", "properties": {"verdict": {"type": "string"}}}
MODEL = "claude-opus-5"
RESOLVED = "claude-opus-5-20260401"


def a_request(**overrides: Any) -> ModelRequest:
    fields: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": 16000,
        "messages": [{"role": "user", "content": "judge this"}],
    }
    fields.update(overrides)
    return ModelRequest(**fields)


class FakeQuery:
    """`claude_agent_sdk.query` as the client calls it: records each call, yields a script.

    `stderr` lines are fed to the options' callback before anything is yielded, the way the
    transport's stderr reader delivers them while the CLI runs.
    """

    def __init__(self, *messages: Message, stderr: tuple[str, ...] = ()) -> None:
        self.messages = messages
        self.stderr = stderr
        self.calls: list[tuple[str, ClaudeAgentOptions]] = []

    async def __call__(self, *, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        self.calls.append((prompt, options))
        for line in self.stderr:
            assert options.stderr is not None
            options.stderr(line)
        for message in self.messages:
            yield message


def init() -> SystemMessage:
    return SystemMessage(subtype="init", data={"type": "system", "subtype": "init"})


def assistant(*content: Any, **fields: Any) -> AssistantMessage:
    values: dict[str, Any] = {
        "content": list(content),
        "model": RESOLVED,
        "usage": {"input_tokens": 1200, "output_tokens": 80},
        "message_id": "msg_01",
        "stop_reason": "end_turn",
        "session_id": "session-1",
    }
    values.update(fields)
    return AssistantMessage(**values)


def result(**fields: Any) -> ResultMessage:
    values: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 2400,
        "duration_api_ms": 2100,
        "is_error": False,
        "num_turns": 1,
        "session_id": "session-1",
        "stop_reason": "end_turn",
        "total_cost_usd": 0.008,
        "result": '{"verdict": "pass"}',
        "usage": {"input_tokens": 1200, "output_tokens": 80},
        "model_usage": {RESOLVED: {"inputTokens": 1200, "outputTokens": 80, "costUSD": 0.008}},
    }
    values.update(fields)
    return ResultMessage(**values)


def complete(query: FakeQuery, request: ModelRequest | None = None) -> Any:
    return ClaudeCodeClient(CLI, query=query).complete(request or a_request())


# --- the options: one retry, no tools, no settings, the whole system prompt replaced ---

SET_BY_THE_CLIENT = (
    "system_prompt",
    "tools",
    "allowed_tools",
    "mcp_servers",
    "strict_mcp_config",
    "setting_sources",
    "skills",
    "permission_mode",
    "max_turns",
    "model",
    "effort",
    "output_format",
    "env",
    "cli_path",
    "extra_args",
    "verbatim_prompts",
)


def test_the_options_allow_one_retry_with_no_tools_no_settings_and_the_judges_prompt() -> None:
    """`max_turns=2`: the CLI validates a structured answer after the fact and re-prompts
    once (decision 42); a second rejection is still rule 4."""
    options = ClaudeCodeClient(CLI).options(
        a_request(system="You are the Judge.", output_schema=SCHEMA, effort="high")
    )

    assert {name: getattr(options, name) for name in SET_BY_THE_CLIENT} == {
        "system_prompt": "You are the Judge.",
        "tools": [],
        "allowed_tools": [],
        "mcp_servers": {},
        "strict_mcp_config": True,
        "setting_sources": [],
        "skills": [],
        "permission_mode": None,
        "max_turns": 2,
        "model": "claude-opus-5",
        "effort": "high",
        "output_format": {"type": "json_schema", "schema": SCHEMA},
        "env": {
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "16000",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
        "cli_path": "/opt/claude/bin/claude",
        "extra_args": {"no-session-persistence": None},
        "verbatim_prompts": True,
    }
    # Captured, never inherited: the CLI's stderr must not reach agentdiag's terminal.
    assert callable(options.stderr)


def test_a_backend_says_how_its_structured_output_is_held_to_the_schema() -> None:
    """Decision 44: validate-and-retry is written where the Backend is defined, read from
    the one table in `agentdiag.types` (whose phrases `tests/test_closed_types.py` pins)."""
    for kind in get_args(BackendKind):
        assert Backend(kind=kind).structured_output == STRUCTURED_OUTPUT[kind]
    assert "structured_output" not in Backend(kind="replay").model_dump()


def test_a_request_with_no_system_prompt_replaces_the_preset_with_an_empty_one() -> None:
    options = ClaudeCodeClient(CLI).options(a_request())

    assert (options.system_prompt, options.effort, options.output_format) == ("", None, None)


def test_every_option_the_client_does_not_set_keeps_the_sdks_default() -> None:
    options = ClaudeCodeClient(CLI).options(a_request(output_schema=SCHEMA))
    default = ClaudeAgentOptions()

    for field in dataclasses.fields(ClaudeAgentOptions):
        if field.name in (*SET_BY_THE_CLIENT, "stderr"):
            continue
        assert getattr(options, field.name) == getattr(default, field.name), field.name


def test_the_prompt_is_the_one_user_message_and_the_options_are_the_requests() -> None:
    query = FakeQuery(init(), assistant(TextBlock('{"verdict": "pass"}')), result())

    complete(query, a_request(system="You are the Judge.", output_schema=SCHEMA))

    ((prompt, options),) = query.calls
    assert prompt == "judge this"
    assert options.system_prompt == "You are the Judge."
    assert options.output_format == {"type": "json_schema", "schema": SCHEMA}


def test_a_request_this_backend_cannot_send_as_one_prompt_is_refused() -> None:
    query = FakeQuery(result())
    two = a_request(
        messages=[
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ]
    )
    blocks = a_request(messages=[{"role": "user", "content": [{"type": "text", "text": "x"}]}])

    for request in (two, blocks):
        with pytest.raises(ClaudeCodeError, match="one user message"):
            complete(query, request)
    assert query.calls == []


def test_a_request_offering_tools_is_refused_rather_than_sent_without_them() -> None:
    query = FakeQuery(result())
    tools = a_request(tools=[{"name": "lookup", "input_schema": {"type": "object"}}])

    with pytest.raises(ClaudeCodeError, match="offers the model no tools"):
        complete(query, tools)
    assert query.calls == []


def test_a_request_with_sampling_parameters_is_refused_as_sampling_not_supported() -> None:
    """Phase-5 decision 53: the same refusal the Messages API client raises on a 400 naming
    a parameter, each key `not_supported`, so the Simulated User meets one exception; and
    still refused rather than sent without them."""
    query = FakeQuery(result())

    with pytest.raises(SamplingNotSupported, match="sends no sampling parameters") as refused:
        complete(query, a_request(sampling={"temperature": 0.0, "top_p": 0.9}))
    assert refused.value.sampling_accepted == {
        "temperature": "not_supported",
        "top_p": "not_supported",
    }
    assert query.calls == []


# --- the body: the Messages layout, reassembled, plus what only the CLI reports ---


def test_a_text_answer_is_reassembled_into_the_messages_layout() -> None:
    query = FakeQuery(init(), assistant(TextBlock('{"verdict": "pass"}')), result())

    response = complete(query)

    assert response.body == {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-20260401",
        "content": [{"type": "text", "text": '{"verdict": "pass"}'}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1200, "output_tokens": 80},
        "structured_output": None,
        "claude_code": {
            "total_cost_usd": 0.008,
            "model_usage": {
                "claude-opus-5-20260401": {
                    "inputTokens": 1200,
                    "outputTokens": 80,
                    "costUSD": 0.008,
                }
            },
            "session_id": "session-1",
            "num_turns": 1,
            "duration_api_ms": 2100,
            "cli_version": "2.1.280",
        },
    }
    assert (response.requested_model, response.resolved_model) == (MODEL, RESOLVED)
    assert response.text() == '{"verdict": "pass"}'
    assert response.stop_reason == "end_turn"


def test_a_structured_answer_keeps_the_tool_use_and_reads_the_structured_output() -> None:
    """The CLI delivers a schema's answer through its `StructuredOutput` tool: the content
    is recorded as the model produced it, and the answer is `structured_output`."""
    block = ToolUseBlock(id="toolu_01", name="StructuredOutput", input={"verdict": "pass"})
    query = FakeQuery(
        init(),
        assistant(block, stop_reason="tool_use"),
        result(structured_output={"verdict": "pass"}, result=None, stop_reason=None),
    )

    response = complete(query, a_request(output_schema=SCHEMA))

    assert response.body["content"] == [
        {
            "type": "tool_use",
            "id": "toolu_01",
            "name": "StructuredOutput",
            "input": {"verdict": "pass"},
        }
    ]
    assert response.body["structured_output"] == {"verdict": "pass"}
    assert response.body["stop_reason"] == "tool_use"
    assert response.structured_output == {"verdict": "pass"}
    assert response.text() == ""


def test_a_thinking_block_is_kept_with_its_signature() -> None:
    query = FakeQuery(
        assistant(ThinkingBlock(thinking="weighing it", signature="sig-1"), TextBlock("{}")),
        result(),
    )

    assert complete(query).body["content"] == [
        {"type": "thinking", "thinking": "weighing it", "signature": "sig-1"},
        {"type": "text", "text": "{}"},
    ]


def test_a_tool_result_block_is_kept_as_the_messages_api_spells_it() -> None:
    block = ToolResultBlock(tool_use_id="toolu_01", content="done", is_error=False)

    assert complete(FakeQuery(assistant(block, TextBlock("{}")), result())).body["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_01",
        "content": "done",
        "is_error": False,
    }


def test_a_content_block_this_backend_does_not_know_is_refused_not_given_a_type() -> None:
    block = ServerToolUseBlock(id="srvtoolu_01", name="web_search", input={})

    with pytest.raises(ClaudeCodeError, match="does not know: ServerToolUseBlock"):
        complete(FakeQuery(assistant(block), result()))


def test_the_cost_the_cli_reports_is_carried_as_the_reported_cost() -> None:
    assert complete(FakeQuery(assistant(TextBlock("{}")), result())).reported_cost_usd == 0.008


def test_a_cli_that_reports_no_cost_leaves_the_reported_cost_empty() -> None:
    response = complete(FakeQuery(assistant(TextBlock("{}")), result(total_cost_usd=None)))

    assert response.reported_cost_usd is None
    assert response.body["claude_code"]["total_cost_usd"] is None


def test_the_stop_reason_falls_back_to_the_results_and_is_never_made_up() -> None:
    from_result = complete(
        FakeQuery(assistant(TextBlock("{}"), stop_reason=None), result(stop_reason="max_tokens"))
    )
    neither = complete(
        FakeQuery(assistant(TextBlock("{}"), stop_reason=None), result(stop_reason=None))
    )

    assert from_result.body["stop_reason"] == "max_tokens"
    # Recorded as the SDK said it, nothing; the response's own field defaults.
    assert neither.body["stop_reason"] is None
    assert neither.stop_reason == "end_turn"


def test_an_assistant_message_split_over_several_lines_is_merged_into_one_body() -> None:
    query = FakeQuery(
        init(),
        assistant(
            ThinkingBlock(thinking="first", signature="s"),
            stop_reason=None,
            usage={"input_tokens": 1200, "output_tokens": 1},
        ),
        assistant(TextBlock('{"verdict": "fail"}')),
        result(),
    )

    body = complete(query).body

    assert body["content"] == [
        {"type": "thinking", "thinking": "first", "signature": "s"},
        {"type": "text", "text": '{"verdict": "fail"}'},
    ]
    assert body["usage"] == {"input_tokens": 1200, "output_tokens": 80}
    assert body["stop_reason"] == "end_turn"


def test_the_usage_is_the_calls_total_from_the_result_not_the_streams_first_snapshot() -> None:
    """Seen live (2026-09-23): the assistant message's `usage` is the `message_start`
    snapshot — 7 output tokens for a 969-token answer — while the result's `usage` is the
    call's total in the Messages API's spelling, split by cache lifetime. Priced from the
    snapshot, the Span would have said the Judge cost a third of what it did."""
    total = {
        "input_tokens": 2,
        "cache_creation_input_tokens": 3552,
        "cache_read_input_tokens": 0,
        "output_tokens": 969,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 3552},
        "service_tier": "standard",
    }
    query = FakeQuery(
        init(),
        assistant(
            TextBlock('{"verdict": "pass"}'),
            usage={"input_tokens": 2, "cache_creation_input_tokens": 3552, "output_tokens": 7},
        ),
        result(usage=total),
    )

    body = complete(query).body

    assert body["usage"] == total


def test_a_result_without_a_usage_leaves_the_assistant_messages_snapshot_in_place() -> None:
    query = FakeQuery(init(), assistant(TextBlock("{}")), result(usage=None))

    body = complete(query).body

    assert body["usage"] == {"input_tokens": 1200, "output_tokens": 80}


def test_a_recording_made_through_claude_code_replays_byte_for_byte(tmp_path: Path) -> None:
    """The request key is `request.body()` unchanged, so the replay needs no Claude Code."""
    path = tmp_path / "judge.jsonl"
    block = ToolUseBlock(id="toolu_01", name="StructuredOutput", input={"verdict": "pass"})
    query = FakeQuery(
        assistant(block, stop_reason="tool_use"), result(structured_output={"verdict": "pass"})
    )
    request = a_request(output_schema=SCHEMA, effort="high")

    live = RecordingModelClient(ClaudeCodeClient(CLI, query=query), path).complete(request)
    replayed = ReplayModelClient(ReplayCursor(Recording.load(path))).complete(request)

    (line,) = [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines()]
    assert line["request"] == request.body()
    assert replayed == live
    assert replayed.structured_output == {"verdict": "pass"}
    assert replayed.reported_cost_usd == 0.008


# --- every failure is ClaudeCodeError, told in the CLI's words (decision 24) ---


def test_a_result_marked_as_an_error_raises_with_the_clis_words_and_its_stderr() -> None:
    query = FakeQuery(
        assistant(TextBlock("API Error: 529 overloaded")),
        result(is_error=True, result="API Error: 529 overloaded", api_error_status=529),
        stderr=("retrying the request", "giving up after 1 attempt"),
    )

    with pytest.raises(ClaudeCodeError) as raised:
        complete(query)

    assert "ended in an error (subtype success, is_error, api_error_status 529)" in str(
        raised.value
    )
    assert "API Error: 529 overloaded" in str(raised.value)
    assert "giving up after 1 attempt" in str(raised.value)


def test_a_result_that_did_not_succeed_raises_naming_its_subtype_and_errors() -> None:
    query = FakeQuery(
        assistant(TextBlock("")),
        result(subtype="error_max_turns", errors=["Reached maximum number of turns (1)"]),
    )

    with pytest.raises(ClaudeCodeError, match="error_max_turns") as raised:
        complete(query)

    assert "Reached maximum number of turns (1)" in str(raised.value)


def test_an_assistant_message_carrying_an_error_raises_naming_it() -> None:
    query = FakeQuery(
        assistant(TextBlock("Invalid API key"), error="authentication_failed"), result()
    )

    with pytest.raises(ClaudeCodeError, match="authentication_failed"):
        complete(query)


def test_a_call_that_produced_no_assistant_message_raises() -> None:
    with pytest.raises(ClaudeCodeError, match="no assistant message"):
        complete(FakeQuery(init(), result()))


def test_a_call_that_ended_without_a_result_raises() -> None:
    with pytest.raises(ClaudeCodeError, match="no result"):
        complete(FakeQuery(init(), assistant(TextBlock("{}"))))


REJECTION = (
    "Output does not match required schema: root: must have required property 'text', "
    "root: must have required property 'cites'"
)
"""What the CLI sent back to a degenerate first attempt in the capture batch of 2026-09-24."""


def rejected(tool_use_id: str, text: Any = REJECTION) -> UserMessage:
    """The `UserMessage` the CLI sends after a `StructuredOutput` call it did not accept."""
    return UserMessage(
        content=[ToolResultBlock(tool_use_id=tool_use_id, content=text, is_error=True)]
    )


def accepted(tool_use_id: str) -> UserMessage:
    return UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id=tool_use_id,
                content="Structured output provided successfully",
                is_error=None,
            )
        ]
    )


GOOD = {"verdict": "pass"}
TOTAL = {"input_tokens": 4, "cache_read_input_tokens": 3552, "output_tokens": 420}


def retried_session(*, second: Any = None) -> FakeQuery:
    """The live shape decision 42 was found in: a thinking block and a degenerate
    `StructuredOutput` call under one id, the CLI's rejection, the good call under a second
    id, the CLI's acceptance, and a `success` result with `num_turns` 3."""
    return FakeQuery(
        init(),
        assistant(
            ThinkingBlock(thinking="weighing it", signature="sig-A"),
            ToolUseBlock(
                id="toolu_A", name="StructuredOutput", input={"$0": {"text": "placeholder"}}
            ),
            message_id="msg_A",
            stop_reason="tool_use",
            model="claude-opus-5-20260401",
        ),
        rejected("toolu_A"),
        second
        or assistant(
            ToolUseBlock(id="toolu_B", name="StructuredOutput", input=GOOD),
            message_id="msg_B",
            stop_reason="tool_use",
        ),
        accepted("toolu_B"),
        result(num_turns=3, structured_output=GOOD, result=None, usage=TOTAL),
    )


def test_a_rejected_first_attempt_is_recorded_and_the_body_is_the_retry() -> None:
    """Decision 42: the last message id is the answer; every earlier one is an attempt,
    kept verbatim with the CLI's rejection, so what the Judge first said is not lost."""
    body = complete(retried_session(), a_request(output_schema=SCHEMA)).body

    assert body["id"] == "msg_B"
    assert body["model"] == RESOLVED
    assert body["content"] == [
        {"type": "tool_use", "id": "toolu_B", "name": "StructuredOutput", "input": GOOD}
    ]
    assert body["structured_output"] == GOOD
    assert body["usage"] == TOTAL, "the result's total covers both turns"
    assert body["claude_code"]["num_turns"] == 3
    assert body["claude_code"]["attempts"] == [
        {
            "id": "msg_A",
            "content": [
                {"type": "thinking", "thinking": "weighing it", "signature": "sig-A"},
                {
                    "type": "tool_use",
                    "id": "toolu_A",
                    "name": "StructuredOutput",
                    "input": {"$0": {"text": "placeholder"}},
                },
            ],
            "rejected": REJECTION,
        }
    ]


def test_a_rejection_given_as_text_blocks_is_kept_as_its_text() -> None:
    """`ToolResultBlock.content` may be a list of text blocks; the attempt keeps the text."""
    query = retried_session()
    query.messages = tuple(
        rejected("toolu_A", [{"type": "text", "text": REJECTION}])
        if isinstance(message, UserMessage) and message.content[0].is_error  # type: ignore[union-attr]
        else message
        for message in query.messages
    )

    body = complete(query, a_request(output_schema=SCHEMA)).body

    assert body["claude_code"]["attempts"][0]["rejected"] == REJECTION


def test_an_answer_at_the_first_attempt_records_no_attempts() -> None:
    body = complete(FakeQuery(assistant(TextBlock("{}")), result())).body

    assert "attempts" not in body["claude_code"]


def test_messages_sharing_one_id_are_still_one_attempt_when_the_call_was_retried() -> None:
    query = FakeQuery(
        assistant(ThinkingBlock(thinking="first", signature="s"), message_id="msg_A"),
        assistant(
            ToolUseBlock(id="toolu_A", name="StructuredOutput", input={}), message_id="msg_A"
        ),
        rejected("toolu_A"),
        assistant(ThinkingBlock(thinking="again", signature="t"), message_id="msg_B"),
        assistant(
            ToolUseBlock(id="toolu_B", name="StructuredOutput", input=GOOD), message_id="msg_B"
        ),
        result(num_turns=3, structured_output=GOOD, result=None),
    )

    body = complete(query, a_request(output_schema=SCHEMA)).body

    assert [block["type"] for block in body["content"]] == ["thinking", "tool_use"]
    ((attempt),) = body["claude_code"]["attempts"]
    assert attempt["id"] == "msg_A"
    assert [block["type"] for block in attempt["content"]] == ["thinking", "tool_use"]


def test_two_rejections_end_the_call_as_a_claude_code_error_in_the_clis_words() -> None:
    """One retry, not more: a second rejection is rule 4, as decision 24 has it."""
    bad = {"$0": {"text": "placeholder"}}
    query = FakeQuery(
        assistant(
            ToolUseBlock(id="toolu_A", name="StructuredOutput", input=bad), message_id="msg_A"
        ),
        rejected("toolu_A"),
        assistant(
            ToolUseBlock(id="toolu_B", name="StructuredOutput", input=bad), message_id="msg_B"
        ),
        rejected("toolu_B"),
        result(
            subtype="error_max_turns",
            is_error=True,
            result=None,
            errors=["Reached maximum number of turns (2)"],
        ),
    )

    with pytest.raises(ClaudeCodeError, match="error_max_turns") as raised:
        complete(query, a_request(output_schema=SCHEMA))

    assert "Reached maximum number of turns (2)" in str(raised.value)


def test_a_schema_sent_and_neither_a_structured_output_nor_text_back_raises() -> None:
    block = ToolUseBlock(id="toolu_01", name="StructuredOutput", input={})
    query = FakeQuery(assistant(block), result(structured_output=None, result=None))

    with pytest.raises(ClaudeCodeError, match="no answer"):
        complete(query, a_request(output_schema=SCHEMA))


def test_an_sdk_exception_raises_as_claude_code_error_with_the_stderr() -> None:
    class Failing(FakeQuery):
        async def __call__(
            self, *, prompt: str, options: ClaudeAgentOptions
        ) -> AsyncIterator[Message]:
            assert options.stderr is not None
            options.stderr("error: unknown option '--effort'")
            raise RuntimeError("Command failed with exit code 1")
            yield result()  # an async generator, as the SDK's query is

    with pytest.raises(ClaudeCodeError) as raised:
        complete(Failing())

    assert "RuntimeError: Command failed with exit code 1" in str(raised.value)
    assert "unknown option '--effort'" in str(raised.value)


def test_a_call_that_exceeds_its_timeout_raises() -> None:
    class Silent(FakeQuery):
        async def __call__(
            self, *, prompt: str, options: ClaudeAgentOptions
        ) -> AsyncIterator[Message]:
            await anyio.sleep(3600)
            yield result()

    client = ClaudeCodeClient(CLI, query=Silent(), timeout_s=0.05)

    with pytest.raises(ClaudeCodeError, match=r"did not finish within 0\.05 s"):
        client.complete(a_request())


def test_the_default_timeout_is_ten_minutes() -> None:
    assert ClaudeCodeClient(CLI).timeout_s == 600


# --- which binary, and what it says (decision 22) ---


def test_the_cli_agentdiag_probes_is_the_one_the_sdk_would_spawn() -> None:
    """The one private call in the suite: it guards decision 22 against SDK drift."""
    transport = SubprocessCLITransport(prompt="", options=ClaudeAgentOptions())

    assert claude_code.cli_path() == transport._find_cli()


def test_the_version_is_read_from_the_clis_own_banner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_code, "_probe", lambda *_: "2.1.280 (Claude Code)\n")
    claude_code.cli_version.cache_clear()

    assert claude_code.cli_version("/opt/claude/bin/claude") == "2.1.280"
    claude_code.cli_version.cache_clear()


def test_the_login_is_what_auth_status_says_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = {
        "/in": '{"loggedIn": true, "authMethod": "claude.ai"}',
        "/out": '{"loggedIn": false}',
        "/broken": "not json",
    }
    monkeypatch.setattr(claude_code, "_probe", lambda path, *_: answers[path])
    claude_code.logged_in.cache_clear()

    assert [claude_code.logged_in(path) for path in answers] == [True, False, False]
    claude_code.logged_in.cache_clear()
