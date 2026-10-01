"""The Target's calls through Claude Code: the transport under the in-process Adapter.

Seam 3 for the happy paths: the toy Target runs through `InProcessAdapter` with the Claude
Code login as its credentials and a fake `ClaudeCodeSession` in place of the CLI. What the
Target's calls became is read back from the Trace; what reached the CLI — the messages sent,
the options, the tool results the MCP server returned, the disconnects — is read off the
fake, which is the only witness of that side. Seam 2 for the refusals and the failures: the
transport is driven with crafted requests and asserted by exception name and message
(phase-5 interfaces, ticket 20, decisions 30 to 36).

The fake CLI (`tests/fakes/claude_code_cli.py`) plays the session as the live probe of
2026-09-24 saw it, stream events and all; nothing here spawns the CLI.
"""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import anthropic
import httpx2
import pytest
import yaml
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    StreamEvent,
)

from agentdiag.adapter import InProcessAdapter
from agentdiag.eval.perform import target_error
from agentdiag.examples.toy import SYSTEM_PROMPT, TOOL_SCHEMAS
from agentdiag.model import claude_code_transport, credentials
from agentdiag.model.claude_code import ClaudeCodeCli
from agentdiag.model.claude_code_transport import (
    ROUTE_AROUND,
    THREAD_NAME,
    ClaudeCodeRefused,
    ClaudeCodeSessionError,
    ClaudeCodeTransport,
)
from agentdiag.model.credentials import CredentialSource
from agentdiag.run.execute import RunOptions, Sessions, run
from agentdiag.run.preflight import PlannedScenario
from agentdiag.trace import (
    Event,
    TraceWriter,
    event_fields,
    project_spans,
    read_trace,
    resolve_blobs,
)
from tests.fakes.claude_code_cli import (
    MODEL,
    SESSION,
    Call,
    FakeCli,
    Script,
    call_messages,
    final,
    result_message,
    snapshot,
    stream,
    text,
    thinking,
    tool_use,
)
from tests.fakes.workspace import the_target

CLI = ClaudeCodeCli(path="/opt/claude/bin/claude", version="2.1.280")
LOGIN = CredentialSource(kind="claude_code", detail="Claude Code login (2.1.280)", cli=CLI)
MESSAGE = "Hi, I'd like to cancel order NB-1042."
REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"

LOOKUP_RESULT = (
    '{"order_id": "NB-1042", "status": "processing", "placed_at": "2026-09-20T14:22:00Z", '
    '"item": "Northwind Trailhead gravel bike, 54cm, slate", "total_usd": 2149.0}'
)
CANCEL_RESULT = (
    '{"order_id": "NB-1042", "status": "cancelled", "cancelled_at": "2026-09-22T10:15:01Z", '
    '"refund_usd": 2149.0}'
)
ANSWER = "Order NB-1042 has been cancelled. Let me know if you need anything else!"


# --- the probe's Turn, as the fake CLI plays it ---

LOOKUP = Call(
    "msg_011CfMwg5LWzzZhYJiyqnTX3",
    [tool_use("toolu_011tiH7p9jSeRbUMVqoygvAv", "lookup_order", order_id="NB-1042")],
    "tool_use",
    snapshot(2263, 0, 24),
    final(2263, 0, 61),
)
CANCEL = Call(
    "msg_011CfMwgDqVeJ2Pv3uztuG2V",
    [
        thinking("ErYCCqgBsignature"),
        tool_use("toolu_01UBwTvZXYsWgSD1Mj5qTJZB", "cancel_order", order_id="NB-1042"),
    ],
    "tool_use",
    snapshot(187, 2263, 5),
    final(187, 2263, 80, thinking=17),
)
ANSWERED = Call(
    "msg_011CfMwgMdYnpsQfan6o63mJ",
    [text(ANSWER)],
    "end_turn",
    snapshot(183, 2450, 8),
    final(183, 2450, 44),
)
TURN_USAGE = {
    "input_tokens": 6,
    "cache_creation_input_tokens": 2633,
    "cache_read_input_tokens": 4713,
    "output_tokens": 185,
    "cache_creation": {"ephemeral_1h_input_tokens": 2633, "ephemeral_5m_input_tokens": 0},
}
CANCEL_TURN = Script(
    calls=[LOOKUP, CANCEL, ANSWERED],
    result={"total_cost_usd": 0.0133366, "usage": TURN_USAGE, "result": ANSWER},
)
"""The probe's Turn: lookup, then cancel, then the answer, three API calls."""


# --- driving the toy Target through the Adapter ---


def scripted_clock(start: int = 1_000_000_000_000) -> Any:
    state = {"now": start - 1}

    def clock() -> int:
        state["now"] += 1
        return state["now"]

    return clock


def config(**overrides: Any) -> dict[str, Any]:
    environment: dict[str, Any] = {
        "factory": "agentdiag.examples.toy:make_target",
        "tools": "agentdiag.examples.toy:make_tools",
        "model": MODEL,
    }
    environment.update(overrides)
    return {
        "kind": "inprocess",
        "side_effects": "none",
        "environments": {"default": "local", "local": environment},
    }


def adapter_for(fake: FakeCli, **overrides: Any) -> InProcessAdapter:
    return InProcessAdapter(
        config(**overrides),
        environment="local",
        credentials=LOGIN,
        claude_code_session=fake,
        tool_kinds={"lookup_order": "retrieval", "cancel_order": "action"},
    )


def deliver(
    tmp_path: Path, fake: FakeCli, *messages: str, name: str = "trace", **overrides: Any
) -> list[Event]:
    """Each message as one Turn of one session, then close; the Trace, blobs resolved."""
    path = tmp_path / f"{name}.jsonl"
    writer = TraceWriter(path, clock=scripted_clock())
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    session = adapter_for(fake, **overrides).open(writer)
    try:
        for number, message in enumerate(messages, start=1):
            with writer.span(
                "turn", actor="agentdiag", name=f"turn {number}", fidelity="instrumented"
            ):
                session.deliver(message)
    finally:
        session.close()
        writer.end("completed")
        writer.close()
    return resolve_blobs(read_trace(path))


def spans(events: Sequence[Event], *kinds: str) -> list[Any]:
    return [span for span in project_spans(events) if span.kind in kinds]


def bodies(events: Sequence[Event], type: str) -> list[dict[str, Any]]:
    return [event_fields(event)["body"] for event in events if event.type == type]


def transport_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == THREAD_NAME]


@pytest.fixture
def cancel_trace(tmp_path: Path) -> tuple[FakeCli, list[Event]]:
    fake = FakeCli(CANCEL_TURN)
    return fake, deliver(tmp_path, fake, MESSAGE)


# --- seam 3: the toy Target's Turn, read back from the Trace ---


def test_each_api_call_is_an_llm_call_span_with_the_usage_of_its_message_delta(
    cancel_trace: tuple[FakeCli, list[Event]],
) -> None:
    _, events = cancel_trace
    llm_calls = spans(events, "llm_call")

    assert [span.span_id for span in llm_calls] == ["llm_call-1", "llm_call-2", "llm_call-3"]
    usage_of = [
        {
            name: span.attributes.get(f"gen_ai.usage.{name}")
            for name in (
                "input_tokens",
                "output_tokens",
                "cache_read.input_tokens",
                "cache_write.input_tokens",
            )
        }
        for span in llm_calls
    ]
    assert usage_of == [
        {
            "input_tokens": 2,
            "output_tokens": 61,
            "cache_read.input_tokens": 0,
            "cache_write.input_tokens": 2263,
        },
        {
            "input_tokens": 2,
            "output_tokens": 80,
            "cache_read.input_tokens": 2263,
            "cache_write.input_tokens": 187,
        },
        {
            "input_tokens": 2,
            "output_tokens": 44,
            "cache_read.input_tokens": 2450,
            "cache_write.input_tokens": 183,
        },
    ]


def test_each_llm_call_names_the_message_start_id_the_model_and_the_stop_reason(
    cancel_trace: tuple[FakeCli, list[Event]],
) -> None:
    _, events = cancel_trace

    assert [
        (
            span.attributes["gen_ai.response.id"],
            span.attributes["gen_ai.response.model"],
            span.attributes["gen_ai.response.finish_reasons"],
        )
        for span in spans(events, "llm_call")
    ] == [
        ("msg_011CfMwg5LWzzZhYJiyqnTX3", MODEL, ["tool_use"]),
        ("msg_011CfMwgDqVeJ2Pv3uztuG2V", MODEL, ["tool_use"]),
        ("msg_011CfMwgMdYnpsQfan6o63mJ", MODEL, ["end_turn"]),
    ]


def test_each_call_is_priced_with_its_cache_writes_at_the_one_hour_rate(
    cancel_trace: tuple[FakeCli, list[Event]],
) -> None:
    """claude-sonnet-5: $2 in, $10 out, $0.20 a cache read, $4 a one-hour cache write per
    million, each rounded to the micro-dollar; unrounded they sum to $0.0133366, the Turn's
    `total_cost_usd` in the probe."""
    _, events = cancel_trace

    costs = [span.attributes["llm.cost.total"] for span in spans(events, "llm_call")]
    assert costs == [0.009666, 0.002005, 0.001666]


def test_the_tool_spans_nest_under_the_call_that_asked_for_them_with_its_tool_use_ids(
    cancel_trace: tuple[FakeCli, list[Event]],
) -> None:
    _, events = cancel_trace

    assert [
        (span.span_id, span.parent_span_id, span.attributes["gen_ai.tool.call.id"])
        for span in spans(events, "retrieval", "tool_call")
    ] == [
        ("retrieval-1", "llm_call-1", "toolu_011tiH7p9jSeRbUMVqoygvAv"),
        ("tool_call-1", "llm_call-2", "toolu_01UBwTvZXYsWgSD1Mj5qTJZB"),
    ]


def test_the_request_events_are_the_bodies_the_sdk_put_on_the_wire(
    cancel_trace: tuple[FakeCli, list[Event]],
) -> None:
    _, events = cancel_trace
    requests = bodies(events, "request")

    assert requests[0] == {
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": MESSAGE}],
        "model": MODEL,
        "system": SYSTEM_PROMPT,
        "tools": TOOL_SCHEMAS,
    }
    assert [len(body["messages"]) for body in requests] == [1, 3, 5]
    assert requests[2]["messages"][-1] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_01UBwTvZXYsWgSD1Mj5qTJZB",
                "content": [{"type": "text", "text": CANCEL_RESULT}],
            }
        ],
    }


def test_the_response_events_are_the_reassembled_bodies_with_the_targets_tool_names(
    cancel_trace: tuple[FakeCli, list[Event]],
) -> None:
    _, events = cancel_trace
    first, second, third = bodies(events, "response")
    startup = first["claude_code"].pop("startup_ms")

    assert isinstance(startup, int) and startup >= 0
    assert first == {
        "id": "msg_011CfMwg5LWzzZhYJiyqnTX3",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_011tiH7p9jSeRbUMVqoygvAv",
                "name": "lookup_order",
                "input": {"order_id": "NB-1042"},
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "stop_details": None,
        "usage": {
            "input_tokens": 2,
            "cache_creation_input_tokens": 2263,
            "cache_read_input_tokens": 0,
            "output_tokens": 61,
            "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 2263},
            "service_tier": "standard",
            "inference_geo": "not_available",
            "output_tokens_details": {"thinking_tokens": 0},
        },
        "claude_code": {"session_id": SESSION, "cli_version": "2.1.280"},
    }
    assert second["content"] == [
        {"type": "thinking", "thinking": "", "signature": "ErYCCqgBsignature"},
        {
            "type": "tool_use",
            "id": "toolu_01UBwTvZXYsWgSD1Mj5qTJZB",
            "name": "cancel_order",
            "input": {"order_id": "NB-1042"},
        },
    ]
    assert second["claude_code"] == {"session_id": SESSION, "cli_version": "2.1.280"}
    assert third["content"] == [{"type": "text", "text": ANSWER}]
    assert third["claude_code"] == {
        "session_id": SESSION,
        "cli_version": "2.1.280",
        "turn": {
            "total_cost_usd": 0.0133366,
            "num_turns": 3,
            "duration_api_ms": 5460,
            "usage": TURN_USAGE,
        },
    }


def test_the_first_llm_call_of_a_session_records_the_cli_start_up_and_no_other_does(
    cancel_trace: tuple[FakeCli, list[Event]],
) -> None:
    """Decision 36: that Span's duration encloses the CLI's start-up as well as the answer."""
    _, events = cancel_trace
    startups = [
        span.attributes.get("agentdiag.backend.startup_ms") for span in spans(events, "llm_call")
    ]

    assert isinstance(startups[0], int)
    assert startups[1:] == [None, None]


def test_the_handlers_hand_the_cli_the_targets_tool_results_verbatim(
    cancel_trace: tuple[FakeCli, list[Event]],
) -> None:
    fake, _ = cancel_trace

    assert fake.session.handler_results == [
        {"content": [{"type": "text", "text": LOOKUP_RESULT}], "is_error": False},
        {"content": [{"type": "text", "text": CANCEL_RESULT}], "is_error": False},
    ]


def test_a_redacted_thinking_block_is_carried_as_it_arrived(tmp_path: Path) -> None:
    redacted = {"type": "redacted_thinking", "data": "EmwKAhgBEgy3va3pzix/LafPsn4a"}
    answered = Call(
        "msg_redacted", [redacted, text("Hello.")], "end_turn", snapshot(0, 0, 5), final(0, 0, 9)
    )
    fake = FakeCli(Script(calls=[answered]))

    events = deliver(tmp_path, fake, "Hi there!")

    (response,) = bodies(events, "response")
    assert response["content"] == [redacted, {"type": "text", "text": "Hello."}]


def test_a_tool_the_target_ran_and_that_raised_reaches_the_cli_as_an_error_result(
    tmp_path: Path,
) -> None:
    missing = Call(
        "msg_missing",
        [tool_use("toolu_01Missing", "lookup_order", order_id="NB-9999")],
        "tool_use",
        snapshot(2263, 0, 20),
        final(2263, 0, 40),
    )
    sorry = Call(
        "msg_sorry",
        [text("I could not find it.")],
        "end_turn",
        snapshot(0, 2263, 5),
        final(0, 2263, 9),
    )
    fake = FakeCli(Script(calls=[missing, sorry]))

    deliver(tmp_path, fake, "Where is NB-9999?")

    assert fake.session.handler_results == [
        {"content": [{"type": "text", "text": "No order NB-9999"}], "is_error": True}
    ]


def test_the_user_message_is_sent_into_the_session_with_its_content_as_the_target_sent_it(
    cancel_trace: tuple[FakeCli, list[Event]],
) -> None:
    fake, _ = cancel_trace

    assert fake.session.sent == [
        {
            "type": "user",
            "message": {"role": "user", "content": MESSAGE},
            "parent_tool_use_id": None,
            "session_id": "default",
        }
    ]


def test_the_session_is_built_with_the_targets_prompt_model_and_tools_and_nothing_of_claude_codes(
    cancel_trace: tuple[FakeCli, list[Event]],
) -> None:
    """Decision 34, as one literal."""
    fake, _ = cancel_trace
    options = fake.session.options
    set_by_the_transport = {
        name: getattr(options, name)
        for name in (
            "system_prompt",
            "tools",
            "allowed_tools",
            "strict_mcp_config",
            "setting_sources",
            "skills",
            "model",
            "include_partial_messages",
            "verbatim_prompts",
            "env",
            "cli_path",
            "extra_args",
            "max_turns",
            "permission_mode",
            "thinking",
        )
    }

    assert set_by_the_transport == {
        "system_prompt": SYSTEM_PROMPT,
        "tools": [],
        "allowed_tools": ["mcp__target__lookup_order", "mcp__target__cancel_order"],
        "strict_mcp_config": True,
        "setting_sources": [],
        "skills": [],
        "model": MODEL,
        "include_partial_messages": True,
        "verbatim_prompts": True,
        "env": {
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "1024",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
        "cli_path": "/opt/claude/bin/claude",
        "extra_args": {"no-session-persistence": None},
        "max_turns": None,
        "permission_mode": None,
        "thinking": None,
    }
    servers = options.mcp_servers
    assert isinstance(servers, dict) and list(servers) == ["target"]
    server = dict(servers["target"])
    assert (server["type"], server["name"]) == ("sdk", "target")
    assert callable(options.stderr)


def test_every_option_the_transport_does_not_set_keeps_the_sdks_default(
    cancel_trace: tuple[FakeCli, list[Event]],
) -> None:
    fake, _ = cancel_trace
    options = fake.session.options
    default = ClaudeAgentOptions()
    set_here = {
        "system_prompt", "tools", "allowed_tools", "mcp_servers", "strict_mcp_config",
        "setting_sources", "skills", "model", "include_partial_messages", "verbatim_prompts",
        "env", "cli_path", "extra_args", "stderr",
    }  # fmt: skip

    for option in dataclasses.fields(ClaudeAgentOptions):
        if option.name not in set_here:
            assert getattr(options, option.name) == getattr(default, option.name), option.name


LOOKUP_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string", "description": "The order id, such as NB-1042."}},
    "required": ["order_id"],
}
CANCEL_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string", "description": "The order id to cancel."}},
    "required": ["order_id"],
}


def test_the_mcp_server_lists_each_target_tool_with_its_schema_and_the_inline_result_size(
    cancel_trace: tuple[FakeCli, list[Event]],
) -> None:
    """What the CLI reads from `tools/list` over MCP: the Target's schemas as it sent them,
    and the size below which a result stays inline, under the names the model sees."""
    fake, _ = cancel_trace

    assert [
        (tool.name, tool.description, tool.input_schema, tool.meta)
        for tool in fake.session.listed_tools
    ] == [
        (
            "lookup_order",
            "Look up the current record for one order by its order id.",
            LOOKUP_SCHEMA,
            {"anthropic/maxResultSizeChars": 1_000_000},
        ),
        (
            "cancel_order",
            "Cancel an order that is still in processing.",
            CANCEL_SCHEMA,
            {"anthropic/maxResultSizeChars": 1_000_000},
        ),
    ]
    assert list(fake.session.names) == ["mcp__target__lookup_order", "mcp__target__cancel_order"]


def test_a_request_without_a_system_prompt_replaces_the_preset_with_an_empty_one() -> None:
    options = ClaudeCodeTransport(CLI).options({"model": MODEL, "max_tokens": 64, "messages": []})

    assert (options.system_prompt, options.allowed_tools) == ("", [])


def test_two_turns_are_two_user_messages_into_one_session_and_rebind_keeps_it(
    tmp_path: Path,
) -> None:
    lookup_only = Script(
        calls=[
            LOOKUP,
            Call(
                "msg_status",
                [text("It is processing.")],
                "end_turn",
                snapshot(0, 2263, 5),
                final(0, 2263, 9),
            ),
        ]
    )
    cancel_only = Script(calls=[CANCEL, ANSWERED])
    fake = FakeCli(
        lookup_only,
        cancel_only,
        Script(
            calls=[
                Call("msg_bye", [text("Bye.")], "end_turn", snapshot(0, 2633, 1), final(0, 2633, 3))
            ]
        ),
    )
    first_path, second_path = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    first = TraceWriter(first_path, clock=scripted_clock())
    first.start(trace_id="r/a/1", scenario="a", run="r", trial=1)
    session = adapter_for(fake).open(first)
    with first.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented"):
        session.deliver("Can you check on order NB-1042 for me?")
    with first.span("turn", actor="agentdiag", name="turn 2", fidelity="instrumented"):
        session.deliver("Thanks. Please cancel it.")
    first.end("completed")
    first.close()
    second = TraceWriter(second_path, clock=scripted_clock())
    second.start(trace_id="r/b/1", scenario="b", run="r", trial=1, continues="a")
    session.rebind(second)
    with second.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented"):
        session.deliver("Goodbye.")
    session.close()
    second.end("completed")
    second.close()

    assert [message["message"]["content"] for message in fake.session.sent] == [
        "Can you check on order NB-1042 for me?",
        "Thanks. Please cancel it.",
        "Goodbye.",
    ]
    assert fake.session.connects == 1
    continued = resolve_blobs(read_trace(second_path))
    assert [len(body["messages"]) for body in bodies(continued, "request")] == [9]
    assert "startup_ms" not in bodies(continued, "response")[0]["claude_code"]


def test_closing_the_session_disconnects_the_cli_once_and_ends_its_thread(
    cancel_trace: tuple[FakeCli, list[Event]],
) -> None:
    fake, _ = cancel_trace

    assert fake.session.disconnects == 1
    assert transport_threads() == []


def test_closing_the_transport_twice_disconnects_once() -> None:
    fake = FakeCli(Script(calls=[ANSWERED]))
    transport = ClaudeCodeTransport(CLI, session=fake)
    post(transport, first_body())

    transport.close()
    transport.close()

    assert fake.session.disconnects == 1
    assert transport_threads() == []


def test_a_close_from_another_thread_cancels_a_waiting_request_rather_than_waiting_behind_it() -> (
    None
):
    fake = FakeCli(Script(silent=True))
    transport = ClaudeCodeTransport(CLI, session=fake, timeout_s=60.0)
    failures: list[BaseException] = []

    def request() -> None:
        try:
            post(transport, first_body())
        except BaseException as exc:
            failures.append(exc)

    waiting = threading.Thread(target=request)
    waiting.start()
    started = time.monotonic()
    while not (fake.sessions and fake.session.sent) and time.monotonic() - started < 10:
        time.sleep(0.01)

    transport.close()
    waiting.join(timeout=10)

    assert not waiting.is_alive()
    assert time.monotonic() - started < 10
    (failure,) = failures
    assert isinstance(failure, ClaudeCodeSessionError)
    assert "the Claude Code session was closed while a request waited" in str(failure)
    assert fake.session.disconnects == 1


def test_a_transport_that_never_started_closes_without_a_session() -> None:
    fake = FakeCli()
    transport = ClaudeCodeTransport(CLI, session=fake)

    transport.close()

    assert fake.sessions == []


# --- seam 2: what this Backend cannot carry is refused, never trimmed (decision 31) ---


def first_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": MESSAGE}],
        "model": MODEL,
        "system": SYSTEM_PROMPT,
        "tools": TOOL_SCHEMAS,
    }
    body.update(overrides)
    return body


def refused(detail: str) -> Any:
    """`pytest.raises` for a refusal naming `detail` and ending, as every refusal must, with
    the way round it (decision 31)."""
    return pytest.raises(ClaudeCodeRefused, match=rf"{detail}.*; {re.escape(ROUTE_AROUND)}$")


def post(
    transport: ClaudeCodeTransport,
    body: Mapping[str, Any],
    path: str = "/v1/messages",
    host: str = "api.anthropic.com",
) -> httpx2.Response:
    request = httpx2.Request("POST", f"https://{host}{path}", json=dict(body))
    return transport.handle_request(request)


@pytest.fixture
def transports() -> Iterator[list[ClaudeCodeTransport]]:
    """Every transport a test builds, closed when it ends, so no thread outlives a test."""
    built: list[ClaudeCodeTransport] = []
    yield built
    for transport in built:
        transport.close()


def a_transport(
    transports: list[ClaudeCodeTransport], *scripts: Script, timeout_s: float = 30.0
) -> tuple[ClaudeCodeTransport, FakeCli]:
    fake = FakeCli(*scripts)
    transport = ClaudeCodeTransport(CLI, session=fake, timeout_s=timeout_s)
    transports.append(transport)
    return transport, fake


LOOKUP_BLOCK = {
    "type": "tool_use",
    "id": "toolu_011tiH7p9jSeRbUMVqoygvAv",
    "name": "lookup_order",
    "input": {"order_id": "NB-1042"},
}


def results_body(
    *content: Any, assistant: Sequence[Any] = (LOOKUP_BLOCK,), **overrides: Any
) -> dict[str, Any]:
    """The request after the lookup: the first user message, the answer, and `content`."""
    return first_body(
        messages=[
            {"role": "user", "content": MESSAGE},
            {"role": "assistant", "content": list(assistant)},
            {"role": "user", "content": list(content)},
        ],
        **overrides,
    )


def lookup_result(**overrides: Any) -> dict[str, Any]:
    block = {
        "type": "tool_result",
        "tool_use_id": "toolu_011tiH7p9jSeRbUMVqoygvAv",
        "content": [{"type": "text", "text": LOOKUP_RESULT}],
    }
    block.update(overrides)
    return block


def after_lookup(transports: list[ClaudeCodeTransport], *later: Script) -> ClaudeCodeTransport:
    """A transport whose session answered the first request with the lookup's tool_use."""
    transport, _ = a_transport(transports, CANCEL_TURN, *later)
    response = post(transport, first_body())
    assert response.json()["stop_reason"] == "tool_use"
    return transport


def test_a_path_other_than_messages_is_refused(transports: list[ClaudeCodeTransport]) -> None:
    transport, fake = a_transport(transports)

    with refused(
        r"serves the Messages API at api.anthropic.com only; this request is for "
        r"api.anthropic.com/v1/messages/count_tokens",
    ):
        post(transport, first_body(), path="/v1/messages/count_tokens")
    assert fake.sessions == []


def test_a_request_for_another_host_is_refused_not_answered_in_its_place(
    transports: list[ClaudeCodeTransport],
) -> None:
    transport, fake = a_transport(transports)

    with refused(r"this request is for gateway.example.com/v1/messages"):
        post(transport, first_body(), host="gateway.example.com")
    assert fake.sessions == []


@pytest.mark.parametrize(
    "key, value",
    [
        ("temperature", 0.2),
        ("top_p", 0.9),
        ("stop_sequences", ["END"]),
        ("tool_choice", {"type": "any"}),
        ("thinking", {"type": "enabled", "budget_tokens": 2048}),
        ("stream", True),
        ("metadata", {"user_id": "u-1"}),
        ("output_config", {"effort": "high"}),
    ],
)
def test_a_body_key_the_backend_cannot_carry_is_refused_by_name(
    transports: list[ClaudeCodeTransport], key: str, value: Any
) -> None:
    transport, fake = a_transport(transports)

    with refused(
        rf"cannot carry {key} as sent",
    ):
        post(transport, first_body(**{key: value}))
    assert fake.sessions == []


def test_a_system_prompt_given_as_blocks_is_refused(transports: list[ClaudeCodeTransport]) -> None:
    transport, _ = a_transport(transports)

    with refused("system prompt that is a string"):
        post(transport, first_body(system=[{"type": "text", "text": SYSTEM_PROMPT}]))


def test_a_tool_carrying_cache_control_is_refused(transports: list[ClaudeCodeTransport]) -> None:
    cached = [{**TOOL_SCHEMAS[0], "cache_control": {"type": "ephemeral"}}, TOOL_SCHEMAS[1]]
    transport, _ = a_transport(transports)

    with refused(r"cannot carry the tool 'lookup_order''s cache_control"):
        post(transport, first_body(tools=cached))


def test_a_server_tool_is_refused(transports: list[ClaudeCodeTransport]) -> None:
    search = {"type": "web_search_20250305", "name": "web_search"}
    transport, _ = a_transport(transports)

    with refused(r"the tool 'web_search''s type"):
        post(transport, first_body(tools=[search]))


def test_a_tool_whose_schema_the_sdk_would_rebuild_is_refused(
    transports: list[ClaudeCodeTransport],
) -> None:
    bare = {"name": "ping", "description": "", "input_schema": {"type": "object"}}
    transport, _ = a_transport(transports)

    with refused("the tool 'ping''s input_schema has no type and properties"):
        post(transport, first_body(tools=[bare]))


def test_a_first_request_with_more_than_one_message_is_refused(
    transports: list[ClaudeCodeTransport],
) -> None:
    transport, _ = a_transport(transports)

    with refused("carries one user message; this one carries 3"):
        post(transport, results_body(lookup_result()))


def test_a_first_request_of_tool_results_is_refused(transports: list[ClaudeCodeTransport]) -> None:
    transport, _ = a_transport(transports)

    with refused(r"the pending tool_use ids are \[\]"):
        post(transport, first_body(messages=[{"role": "user", "content": [lookup_result()]}]))


@pytest.mark.parametrize(
    "key, value",
    [
        ("model", "claude-opus-5"),
        ("max_tokens", 2048),
        ("system", "Be brief."),
        ("tools", TOOL_SCHEMAS[:1]),
    ],
)
def test_a_later_request_that_changes_what_the_session_fixed_is_refused(
    transports: list[ClaudeCodeTransport], key: str, value: Any
) -> None:
    transport = after_lookup(transports)

    with refused(rf"the request's {key} differs from the session's first"):
        post(transport, results_body(lookup_result(), **{key: value}))


def test_a_later_request_that_rewrites_the_history_is_refused(
    transports: list[ClaudeCodeTransport],
) -> None:
    transport = after_lookup(transports)
    rewritten = results_body(lookup_result())
    rewritten["messages"][0]["content"] = "Hi, please cancel NB-1042."

    with refused("do not continue the previous request's exactly"):
        post(transport, rewritten)


def test_a_later_request_whose_assistant_message_names_other_tool_uses_is_refused(
    transports: list[ClaudeCodeTransport],
) -> None:
    transport = after_lookup(transports)
    other = {**LOOKUP_BLOCK, "id": "toolu_01Invented"}

    with refused(r"tool_use ids \['toolu_01Invented'\] are not"):
        post(
            transport,
            results_body(lookup_result(tool_use_id="toolu_01Invented"), assistant=[other]),
        )


def test_a_user_message_mixing_text_with_tool_results_is_refused(
    transports: list[ClaudeCodeTransport],
) -> None:
    transport = after_lookup(transports)

    with refused("mixes text with tool results"):
        post(transport, results_body(lookup_result(), {"type": "text", "text": "and hurry"}))


def test_a_tool_result_that_is_not_text_is_refused(transports: list[ClaudeCodeTransport]) -> None:
    transport = after_lookup(transports)
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "iVBO"},
    }

    with refused("carries content that is not plain text"):
        post(transport, results_body(lookup_result(content=[image])))


def test_a_new_user_message_while_tool_results_are_pending_is_refused(
    transports: list[ClaudeCodeTransport],
) -> None:
    transport = after_lookup(transports)

    with refused("a new user message while the tool results for"):
        post(transport, results_body({"type": "text", "text": "Never mind."}))


def test_tool_results_that_do_not_cover_the_pending_ones_are_refused(
    transports: list[ClaudeCodeTransport],
) -> None:
    transport = after_lookup(transports)

    with refused("the tool results cover"):
        post(transport, results_body(lookup_result(), lookup_result()))


def test_a_tool_result_carrying_cache_control_is_refused_by_name_not_dropped(
    transports: list[ClaudeCodeTransport],
) -> None:
    transport = after_lookup(transports)

    with refused(
        r"cannot carry the tool result for toolu_011tiH7p9jSeRbUMVqoygvAv's cache_control"
    ):
        post(transport, results_body(lookup_result(cache_control={"type": "ephemeral"})))


def test_a_string_tool_result_is_one_text_block(transports: list[ClaudeCodeTransport]) -> None:
    transport, fake = a_transport(transports, CANCEL_TURN)
    post(transport, first_body())

    post(transport, results_body(lookup_result(content=LOOKUP_RESULT)))

    assert fake.session.handler_results[0] == {
        "content": [{"type": "text", "text": LOOKUP_RESULT}],
        "is_error": False,
    }


# --- seam 2: a session that could not answer (decision 31) ---


def test_a_cli_that_never_answers_is_a_session_error_after_the_timeout(
    transports: list[ClaudeCodeTransport],
) -> None:
    transport, _ = a_transport(transports, Script(silent=True), timeout_s=0.3)

    with pytest.raises(ClaudeCodeSessionError, match=r"did not answer within 0.3 s"):
        post(transport, first_body())
    with pytest.raises(ClaudeCodeSessionError, match="cannot answer after an earlier failure"):
        post(transport, first_body())


def test_a_stream_that_ends_is_a_session_error(transports: list[ClaudeCodeTransport]) -> None:
    transport, _ = a_transport(transports, Script(end_stream=True))

    with pytest.raises(ClaudeCodeSessionError, match="the Claude Code CLI's stream ended"):
        post(transport, first_body())


def test_an_answer_before_the_pending_tool_result_was_taken_is_a_session_error(
    transports: list[ClaudeCodeTransport],
) -> None:
    """A denied or skipped tool call must not pass a response off as one that read the
    Target's result."""
    transport, _ = a_transport(transports, Script(calls=[LOOKUP, ANSWERED], take_results=False))
    post(transport, first_body())

    with pytest.raises(
        ClaudeCodeSessionError,
        match="answered before taking the Target's tool result for toolu_011tiH7p9jSeRbUMVqoygvAv",
    ):
        post(transport, results_body(lookup_result()))


def test_a_stream_the_reassembly_cannot_follow_is_a_session_error(
    transports: list[ClaudeCodeTransport],
) -> None:
    orphan = stream(
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}}
    )
    transport, _ = a_transport(transports, Script(raw=[orphan]))

    with pytest.raises(
        ClaudeCodeSessionError,
        match="reassembly cannot follow: a content_block_delta outside a call",
    ):
        post(transport, first_body())


def test_a_block_type_the_reassembly_does_not_know_is_a_session_error(
    transports: list[ClaudeCodeTransport],
) -> None:
    start, *_ = call_messages(ANSWERED)
    unknown = stream(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "server_tool_use",
                "id": "srvtoolu_1",
                "name": "web_search",
                "input": {},
            },
        }
    )
    transport, _ = a_transport(transports, Script(raw=[start, unknown]))

    with pytest.raises(ClaudeCodeSessionError, match="a content block of type 'server_tool_use'"):
        post(transport, first_body())


def test_a_tool_use_without_the_target_prefix_names_a_tool_the_target_did_not_offer(
    transports: list[ClaudeCodeTransport],
) -> None:
    messages = call_messages(LOOKUP)
    for message in messages:
        if isinstance(message, StreamEvent) and message.event["type"] == "content_block_start":
            message.event["content_block"]["name"] = "Bash"
    transport, _ = a_transport(transports, Script(raw=messages))

    with pytest.raises(
        ClaudeCodeSessionError, match="the model called Bash, a tool the Target did not offer"
    ):
        post(transport, first_body())


def test_a_tool_call_that_matches_no_block_of_its_call_fails_at_once_naming_it(
    transports: list[ClaudeCodeTransport],
) -> None:
    """Not after `timeout_s`: once the call's stream is done, no block can still arrive."""
    transport, _ = a_transport(
        transports,
        Script(calls=[LOOKUP, ANSWERED], handler_arguments={"order_id": "NB-9999"}),
    )
    post(transport, first_body())

    with pytest.raises(
        ClaudeCodeSessionError,
        match=r'called lookup_order with \{"order_id":"NB-9999"\}, which matches no tool_use',
    ):
        post(transport, results_body(lookup_result()))


def test_after_an_error_response_the_session_answers_nothing_more(
    transports: list[ClaudeCodeTransport],
) -> None:
    """The CLI holds the user message whose Turn failed; the Target would send it again."""
    transport, fake = a_transport(transports, RATE_LIMITED, CANCEL_TURN)
    assert post(transport, first_body()).status_code == 429

    with pytest.raises(
        ClaudeCodeSessionError,
        match="the previous call on this session failed; the conversation the CLI holds no "
        "longer matches the Target's",
    ):
        post(transport, first_body())
    assert len(fake.session.sent) == 1


def test_a_success_result_before_any_call_is_a_session_error(
    transports: list[ClaudeCodeTransport],
) -> None:
    transport, _ = a_transport(transports, Script(raw=[result_message(0)]))

    with pytest.raises(ClaudeCodeSessionError, match="ended in a result without any call"):
        post(transport, first_body())


# --- decision 32: what the CLI reports as a model failure is an HTTP error response ---


RATE_LIMITED = Script(
    result={
        "is_error": True,
        "api_error_status": 429,
        "result": "API Error: Request rejected (429)",
        "total_cost_usd": 0.0,
    }
)


def test_a_result_marked_as_an_error_is_the_apis_error_response_with_the_clis_words(
    transports: list[ClaudeCodeTransport],
) -> None:
    transport, _ = a_transport(transports, RATE_LIMITED)

    response = post(transport, first_body())

    assert response.status_code == 429
    assert response.json() == {
        "type": "error",
        "error": {
            "type": "api_error",
            "message": "the Claude Code CLI reported the Turn failed (subtype success, is_error, "
            "api_error_status 429): API Error: Request rejected (429)",
        },
    }


def test_a_result_that_did_not_succeed_is_a_502_naming_its_subtype(
    transports: list[ClaudeCodeTransport],
) -> None:
    failed = Script(
        before=[
            AssistantMessage(content=[], model=MODEL, error="server_error", session_id=SESSION)
        ],
        result={
            "subtype": "error_during_execution",
            "errors": ["the model is overloaded"],
            "result": None,
        },
    )
    transport, _ = a_transport(transports, failed)

    response = post(transport, first_body())

    assert response.status_code == 502
    assert response.json()["error"] == {
        "type": "error_during_execution",
        "message": "the Claude Code CLI reported the Turn failed (subtype error_during_execution): "
        "assistant error server_error; the model is overloaded",
    }


def test_a_rate_limited_turn_raises_the_sdks_own_error_in_the_target(tmp_path: Path) -> None:
    fake = FakeCli(RATE_LIMITED)

    with pytest.raises(anthropic.RateLimitError):
        deliver(tmp_path, fake, MESSAGE)
    events = resolve_blobs(read_trace(tmp_path / "trace.jsonl"))
    (llm_call,) = spans(events, "llm_call")
    assert llm_call.attributes["error.type"] == "HTTPStatus429"
    assert fake.session.disconnects == 1


# --- the Adapter and the Run (decisions 31 and 33) ---


def test_deliver_raises_the_refusal_not_the_sdks_connection_error(tmp_path: Path) -> None:
    fake = FakeCli()

    with refused("cannot carry stop_sequences as sent"):
        deliver(
            tmp_path,
            fake,
            MESSAGE,
            name="refused",
            factory="tests.fakes.stop_sequence_target:make_target",
        )
    # Nothing was sent: a refusal comes before the session starts.
    assert fake.sessions == []


def claude_code_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeCli, **environment: str
) -> Path:
    """The example with only mechanical Evals on its first Scenario, the Claude Code login
    resolving and no key, and the fake in place of the CLI: no Judge is built, and nothing
    can spawn the CLI (the SDK client itself refuses to be built)."""
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs"))
    suite = root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "orders.yaml"
    document = yaml.safe_load(suite.read_text(encoding="utf-8"))
    document["scenarios"][0]["evals"] = [{"expect_tools": ["lookup_order"]}]
    suite.write_text(yaml.safe_dump(document), encoding="utf-8")
    if environment:
        manifest = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
        loaded = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        loaded["adapter"]["environments"]["local"].update(environment)
        manifest.write_text(yaml.safe_dump(loaded), encoding="utf-8")
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("anthropic.default_credentials", lambda **_: None)
    monkeypatch.setattr(credentials, "claude_code_probe", lambda: CLI)
    monkeypatch.setattr(claude_code_transport, "sdk_session", fake)

    def no_cli(*arguments: Any, **keywords: Any) -> None:
        raise AssertionError("a test tried to build the Claude Code SDK client")

    monkeypatch.setattr(claude_code_transport, "ClaudeSDKClient", no_cli)
    return root


def only_trace(root: Path) -> list[dict[str, Any]]:
    (run_dir,) = (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir()
    path = run_dir / "trials" / "cancel-processing-order" / "1" / "trace.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def run_record(root: Path) -> dict[str, Any]:
    (run_dir,) = (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir()
    record: dict[str, Any] = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    return record


def test_a_run_through_the_claude_code_login_records_the_targets_backend_and_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeCli(CANCEL_TURN)
    root = claude_code_root(tmp_path, monkeypatch, fake)

    outcome = run(RunOptions(target=the_target(root), scenario=["cancel-processing-order"]))

    assert outcome.code == 0, outcome.message
    assert run_record(root)["adapter"]["backend"] == {
        "kind": "claude_code",
        "cli_version": "2.1.280",
    }
    assert only_trace(root)[-1]["termination"] == "completed"
    assert fake.session.disconnects == 1


def test_a_turn_the_cli_reports_rate_limited_ends_the_trial_target_error_and_closes_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decisions 32 and 33: the Target met the API's own 429 and raised; the Trial is the
    Target's failure, and its session is closed rather than dropped."""
    fake = FakeCli(RATE_LIMITED)
    root = claude_code_root(tmp_path, monkeypatch, fake)

    run(RunOptions(target=the_target(root), scenario=["cancel-processing-order"]))

    trace = only_trace(root)
    assert trace[-1]["termination"] == "target_error"
    assert fake.session.disconnects == 1
    assert transport_threads() == []


def test_a_session_whose_stream_ends_mid_turn_ends_the_trial_agentdiag_error_and_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision 33: the session was live when the Trial failed, and it is disconnected."""
    fake = FakeCli(Script(calls=[LOOKUP], end_stream_after_calls=True))
    root = claude_code_root(tmp_path, monkeypatch, fake)

    run(RunOptions(target=the_target(root), scenario=["cancel-processing-order"]))

    trace = only_trace(root)
    assert trace[-1]["termination"] == "agentdiag_error"
    (error,) = [
        event for event in trace if event["type"] == "error" and event["actor"] == "agentdiag"
    ]
    assert (error["error_type"], error["message"]) == (
        "ClaudeCodeSessionError",
        "the Claude Code CLI's stream ended",
    )
    assert fake.session.disconnects == 1
    assert transport_threads() == []


class RebindRefusingSession:
    """A session its continuing Trial cannot rebind; it counts its closes."""

    def __init__(self) -> None:
        self.closes = 0

    def deliver(self, message: str) -> str:
        return ""

    def rebind(self, trace: TraceWriter) -> None:
        raise RuntimeError("cannot rebind")

    def close(self) -> None:
        self.closes += 1


def planned(scenario: str, continues: str | None = None) -> PlannedScenario:
    return cast(
        PlannedScenario,
        SimpleNamespace(
            key=("orders", scenario),
            suite=SimpleNamespace(name="orders"),
            scenario=SimpleNamespace(id=scenario, continues=continues),
        ),
    )


def test_a_session_its_continuing_trial_failed_to_resume_is_handed_back_to_be_closed() -> None:
    """Decision 33: `failed` returns the session the chain still held, so `_trial` closes it
    rather than dropping it with its CLI subprocess and thread behind it."""
    opened, continuing = planned("lookup"), planned("cancel", continues="lookup")
    sessions = Sessions.for_selection([opened, continuing])
    held = RebindRefusingSession()
    sessions.finished(opened, held)

    with pytest.raises(RuntimeError, match="cannot rebind"):
        sessions.resume(continuing, cast(TraceWriter, None))

    assert sessions.failed(continuing, target_error("cannot rebind")) is held
    assert held.closes == 0


def test_a_request_the_claude_code_backend_refuses_ends_the_trial_agentdiag_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeCli()
    root = claude_code_root(
        tmp_path, monkeypatch, fake, factory="tests.fakes.stop_sequence_target:make_target"
    )

    run(RunOptions(target=the_target(root), scenario=["cancel-processing-order"]))

    trace = only_trace(root)
    assert trace[-1]["termination"] == "agentdiag_error"
    (error,) = [
        event for event in trace if event["type"] == "error" and event["actor"] == "agentdiag"
    ]
    assert error["error_type"] == "ClaudeCodeRefused"
    assert "cannot carry stop_sequences as sent" in error["message"]
