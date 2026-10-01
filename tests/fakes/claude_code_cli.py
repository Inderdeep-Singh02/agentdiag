"""A fake Claude Code CLI: the `ClaudeCodeSession` a test hands the Claude Code transport.

It plays the CLI as the live probe of 2026-09-24 saw it (ticket 20's first Comments entry):
per user message, a `SystemMessage` (`init` on the session's first, `status` on each), then
per API call the stream events the CLI forwards — `message_start` with the usage snapshot
and its `cache_creation` split, per block `content_block_start`, its deltas (`text_delta`,
`thinking_delta` and `signature_delta`, `input_json_delta` in parts), the per-block
`AssistantMessage` the CLI also sends and `content_block_stop`, then `message_delta` with
the final usage and `message_stop`. Each `tool_use` block's tool is called where the
probe saw the CLI call it — after that block's `content_block_stop`, before the call's
`message_delta` — through the options' MCP server over mcp's in-memory transport, as the
CLI's bridge reaches it; once the call's stream is done and every tool has answered, a
`RateLimitEvent` and the `UserMessage` echoing the results (`is_error` None unless the tool
failed, as the CLI echoes it). After the last call, the Turn's `ResultMessage`. It records
the messages it was sent, the options it was built with, the server's tool listing and
every tool result.

Nothing here spawns the CLI or reaches a model.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    Message,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from mcp.client.session import ClientSession
from mcp.server import Server
from mcp.shared.memory import create_client_server_memory_streams

SESSION = "8f7d0c1e-5b1a-4d0e-9a53-fake-session"
MODEL = "claude-sonnet-5"
"""The alias the CLI's `message_start` names, not a dated id."""

PREFIX = "mcp__target__"


@dataclass
class Call:
    """One API call as the model made it: its blocks (tool names un-prefixed), its stop
    reason, and the usage `message_start` and `message_delta` carry."""

    id: str
    blocks: list[dict[str, Any]]
    stop_reason: str
    start_usage: dict[str, Any]
    final_usage: dict[str, Any]


@dataclass
class Script:
    """How the fake CLI answers one user message."""

    calls: list[Call] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    """Fields of the Turn's `ResultMessage` over the defaults."""

    before: list[Message] = field(default_factory=list)
    """Messages emitted before the first call (an assistant error, say)."""

    raw: list[Message] | None = None
    """Emitted in place of the calls and the result: a stream the transport must refuse."""

    take_results: bool = True
    """False: move on to the next call without calling the handlers (a denied tool call)."""

    silent: bool = False
    """Answer nothing at all."""

    end_stream: bool = False
    """End the stream instead of answering."""

    end_stream_after_calls: bool = False
    """Play the calls, then end the stream where the Turn's result would be."""

    handler_arguments: dict[str, Any] | None = None
    """Call every tool with these arguments instead of its block's: a CLI whose call matches
    no block."""


END = object()


def usage(cache_write: int, cache_read: int, output: int, **extra: Any) -> dict[str, Any]:
    return {
        "input_tokens": 2,
        "cache_creation_input_tokens": cache_write,
        "cache_read_input_tokens": cache_read,
        "output_tokens": output,
        **extra,
    }


def snapshot(cache_write: int, cache_read: int, output: int) -> dict[str, Any]:
    """`message_start`'s usage, with the one-hour split the CLI writes and the service tier."""
    return usage(
        cache_write,
        cache_read,
        output,
        cache_creation={
            "ephemeral_5m_input_tokens": 0,
            "ephemeral_1h_input_tokens": cache_write,
        },
        service_tier="standard",
        inference_geo="not_available",
    )


def final(cache_write: int, cache_read: int, output: int, thinking: int = 0) -> dict[str, Any]:
    """`message_delta`'s usage: the call's total."""
    return usage(
        cache_write, cache_read, output, output_tokens_details={"thinking_tokens": thinking}
    )


def tool_use(call_id: str, name: str, **arguments: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": call_id, "name": name, "input": arguments}


def text(words: str) -> dict[str, Any]:
    return {"type": "text", "text": words}


def thinking(signature: str, words: str = "") -> dict[str, Any]:
    return {"type": "thinking", "thinking": words, "signature": signature}


def result_message(calls: int, **fields: Any) -> ResultMessage:
    values: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 14390,
        "duration_api_ms": 5460,
        "is_error": False,
        "num_turns": calls,
        "session_id": SESSION,
        "stop_reason": "end_turn",
        "total_cost_usd": 0.01,
        "usage": {"input_tokens": 2, "output_tokens": 10},
        "result": "",
    }
    values.update(fields)
    return ResultMessage(**values)


def stream(event: dict[str, Any]) -> StreamEvent:
    return StreamEvent(uuid=f"uuid-{event['type']}", session_id=SESSION, event=event)


def halves(words: str) -> list[str]:
    middle = len(words) // 2
    return [part for part in (words[:middle], words[middle:]) if part]


def prefixed(name: str) -> str:
    """A Target tool's name as the CLI shows it to the model, for the server `target`."""
    return PREFIX + name


def call_messages(call: Call, named: Callable[[str], str] = prefixed) -> list[Message]:
    """One API call as the CLI forwards it (the probe log's shapes); `named` gives each
    `tool_use` block the name the model saw."""
    messages: list[Message] = [
        stream(
            {
                "type": "message_start",
                "message": {
                    "id": call.id,
                    "type": "message",
                    "role": "assistant",
                    "model": MODEL,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": call.start_usage,
                },
            }
        )
    ]
    for index, block in enumerate(call.blocks):
        sdk_block: Any
        start: dict[str, Any]
        deltas: list[dict[str, Any]]
        if block["type"] == "text":
            start = {"type": "text", "text": ""}
            deltas = [{"type": "text_delta", "text": part} for part in halves(block["text"])]
            sdk_block = TextBlock(block["text"])
        elif block["type"] == "redacted_thinking":
            # Carried whole in its start, with no delta; the SDK's parser drops the block, so
            # the CLI's per-block `AssistantMessage` for it has no content the SDK names.
            start = dict(block)
            deltas = []
            sdk_block = None
        elif block["type"] == "thinking":
            start = {"type": "thinking", "thinking": "", "signature": ""}
            deltas = [
                *({"type": "thinking_delta", "thinking": p} for p in halves(block["thinking"])),
                {"type": "signature_delta", "signature": block["signature"]},
            ]
            sdk_block = ThinkingBlock(block["thinking"], block["signature"])
        else:
            seen = named(block["name"])
            start = {"type": "tool_use", "id": block["id"], "name": seen, "input": {}}
            deltas = [
                {"type": "input_json_delta", "partial_json": part}
                for part in ["", *halves(json.dumps(block["input"]))]
            ]
            sdk_block = ToolUseBlock(block["id"], seen, block["input"])
        messages.append(
            stream({"type": "content_block_start", "index": index, "content_block": start})
        )
        messages.extend(
            stream({"type": "content_block_delta", "index": index, "delta": delta})
            for delta in deltas
        )
        messages.append(
            AssistantMessage(
                content=[sdk_block] if sdk_block is not None else [],
                model=MODEL,
                usage=call.start_usage,
                message_id=call.id,
                session_id=SESSION,
            )
        )
        messages.append(stream({"type": "content_block_stop", "index": index}))
    messages.append(
        stream(
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": call.stop_reason,
                    "stop_sequence": None,
                    "stop_details": None,
                },
                "usage": call.final_usage,
            }
        )
    )
    messages.append(stream({"type": "message_stop"}))
    return messages


class FakeSession:
    """One Claude Code session, played from the scripts, one per user message sent.

    The Target's tools are reached as the CLI reaches them: through the MCP server in
    `options.mcp_servers`, over mcp's own in-memory transport, so the arguments travel as
    JSON-RPC and the names the model sees are the server's tools under its prefix. A tool
    whose MCP wiring were wrong would fail here as it would against the CLI.
    """

    def __init__(self, scripts: Sequence[Script], options: ClaudeAgentOptions) -> None:
        self.scripts = scripts
        self.options = options
        self.sent: list[dict[str, Any]] = []
        self.handler_results: list[dict[str, Any]] = []
        self.listed_tools: list[Any] = []
        """The server's `tools/list` answer, as the CLI would read it at start-up."""
        self.connects = 0
        self.disconnects = 0
        self.queue: asyncio.Queue[Any] | None = None
        self.plays: list[asyncio.Task[None]] = []
        self.mcp: ClientSession | None = None
        self.names: dict[str, str] = {}
        """The CLI's name for each tool the server lists → the server's own name."""
        self._mcp_ready = asyncio.Event()
        self._mcp_done = asyncio.Event()
        self._mcp_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        self.connects += 1
        self.queue = asyncio.Queue()
        self._mcp_task = asyncio.create_task(self._serve_mcp())
        await self._mcp_ready.wait()

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)
        script = self.scripts[len(self.sent) - 1]
        self.plays.append(asyncio.create_task(self.play(script, first=len(self.sent) == 1)))

    async def receive(self) -> AsyncIterator[Message]:
        assert self.queue is not None
        while (item := await self.queue.get()) is not END:
            yield item

    async def disconnect(self) -> None:
        self.disconnects += 1
        # What the CLI was still doing dies with it, before the MCP connection closes.
        for play in self.plays:
            play.cancel()
        if self.plays:
            await asyncio.wait(self.plays, timeout=5)
        self._mcp_done.set()
        if self._mcp_task is not None:
            await asyncio.wait([self._mcp_task], timeout=5)
        if self.queue is not None:
            self.queue.put_nowait(END)

    async def _serve_mcp(self) -> None:
        """Serve the options' one MCP server for the session, as the CLI's bridge does."""
        servers = self.options.mcp_servers
        assert isinstance(servers, dict)
        ((key, config),) = servers.items()
        server = dict(config)["instance"]
        assert isinstance(server, Server)
        async with (
            create_client_server_memory_streams() as (client, served),
            anyio.create_task_group() as group,
        ):

            async def run() -> None:
                await server.run(
                    served[0],
                    served[1],
                    server.create_initialization_options(),
                    raise_exceptions=False,
                )

            group.start_soon(run)
            async with ClientSession(client[0], client[1]) as session:
                await session.initialize()
                self.listed_tools = list((await session.list_tools()).tools)
                self.names = {f"mcp__{key}__{tool.name}": tool.name for tool in self.listed_tools}
                self.mcp = session
                self._mcp_ready.set()
                await self._mcp_done.wait()
            group.cancel_scope.cancel()

    def _seen(self, name: str) -> str:
        """The name the model sees for the Target's tool `name`: the listed tool's, else the
        bare name (a tool no server offers, which the transport must refuse)."""
        return next((seen for seen, own in self.names.items() if own == name), name)

    async def _call_tool(self, seen: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One tool call as the CLI makes it: by the server's own name, over JSON-RPC."""
        assert self.mcp is not None
        result = await self.mcp.call_tool(self.names[seen], arguments)
        answered = {
            "content": [item.model_dump(exclude_none=True) for item in result.content],
            "is_error": result.is_error,
        }
        self.handler_results.append(answered)
        return answered

    async def play(self, script: Script, *, first: bool) -> None:
        assert self.queue is not None
        emit = self.queue.put_nowait
        if script.silent:
            return
        if script.end_stream:
            emit(END)
            return
        if first:
            tools = list(self.names)
            emit(SystemMessage("init", {"subtype": "init", "session_id": SESSION, "tools": tools}))
        emit(SystemMessage("status", {"subtype": "status", "status": "requesting"}))
        for message in script.before:
            emit(message)
        if script.raw is not None:
            for message in script.raw:
                emit(message)
            return
        for call in script.calls:
            uses = {
                index: block
                for index, block in enumerate(call.blocks)
                if block["type"] == "tool_use"
            }
            calls: list[asyncio.Task[dict[str, Any]]] = []
            for message in call_messages(call, self._seen):
                emit(message)
                # As the probe saw: the CLI calls a tool once its block's stream is done,
                # before the call's `message_delta`, while the API's stream goes on.
                if (
                    isinstance(message, StreamEvent)
                    and message.event["type"] == "content_block_stop"
                    and message.event["index"] in uses
                    and script.take_results
                ):
                    block = uses[message.event["index"]]
                    arguments = dict(script.handler_arguments or block["input"])
                    seen = self._seen(block["name"])
                    calls.append(asyncio.create_task(self._call_tool(seen, arguments)))
            if not calls:
                continue
            results = await asyncio.gather(*calls)
            emit(
                RateLimitEvent(
                    RateLimitInfo(status="allowed", rate_limit_type="five_hour"),
                    uuid="uuid-rate",
                    session_id=SESSION,
                )
            )
            emit(
                UserMessage(
                    content=[
                        ToolResultBlock(
                            block["id"], result["content"], True if result["is_error"] else None
                        )
                        for block, result in zip(uses.values(), results, strict=True)
                    ]
                )
            )
        if script.end_stream_after_calls:
            emit(END)
            return
        emit(result_message(len(script.calls), **script.result))


class FakeCli:
    """A `SessionFactory` that records every session it builds."""

    def __init__(self, *scripts: Script) -> None:
        self.scripts = list(scripts)
        self.sessions: list[FakeSession] = []

    def __call__(self, options: ClaudeAgentOptions) -> FakeSession:
        session = FakeSession(self.scripts, options)
        self.sessions.append(session)
        return session

    @property
    def session(self) -> FakeSession:
        (only,) = self.sessions
        return only


__all__ = [
    "END",
    "MODEL",
    "PREFIX",
    "SESSION",
    "Call",
    "FakeCli",
    "FakeSession",
    "Script",
    "call_messages",
    "final",
    "prefixed",
    "result_message",
    "snapshot",
    "stream",
    "text",
    "thinking",
    "tool_use",
    "usage",
]
