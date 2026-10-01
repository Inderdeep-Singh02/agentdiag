"""The Target's model calls through Claude Code: `/v1/messages` served over one CLI session.

The Claude Code **Backend** of the Target's calls. The in-process Adapter hands the Target an
`anthropic` client whose transport it owns (D6).
With an API key that transport is `httpx2.HTTPTransport`; in replay it is `ReplayTransport`;
when the credentials preflight resolved are the developer's Claude Code login (decision 29)
it is this one, sitting under `CapturingTransport` exactly where the other two sit, so the
`request` Event is still the body the SDK put on the wire and the tool Spans nest as they
always have (phase-5 interfaces, ticket 20, decisions 28 to 36; ADR-0010).

**One Claude Code session per Adapter session** (decision 30). The CLI holds the
conversation, so the transport does not replay history into it: the Target's first request
starts the session (lazily, because the system prompt, the tools, the model and
`max_tokens` are only known then), each new user message is sent into it, and each set of
tool results is handed to the in-process MCP tool handlers the CLI is waiting on. The
session lives on a dedicated daemon thread's asyncio loop, because `ClaudeSDKClient` must
live in one async context while the Target is synchronous; `handle_request` posts one
coroutine per request and waits for it with `timeout_s`, holding no lock `close()` needs, so
a close from another thread cancels a waiting request rather than queueing behind it.
`close()` ends the session and the thread within one `CLOSE_TIMEOUT_S` deadline;
`InProcessSession.close()` calls it.

**The handshake between the CLI's tool call and the Target's.** The model's `tool_use`
blocks reach the Target in the reassembled response; the Target runs its own tools (the
Adapter's wrapped callables, so the tool Spans are the Target's) and sends the results back
as its next request. Meanwhile the CLI calls the MCP handler registered for that tool,
which receives the arguments and no id, so the handler finds its block by content — the same
tool name and the same arguments as canonical JSON, first unclaimed in block order, the rule
`TraceEmitter._attribute` follows (ADR-0001 point 3) — and awaits the Target's result for
that id, which may arrive before or after the handler asks. The handler returns that result
verbatim, so the tool result the model reads is the Target's. A handler whose call matches
no block of a finished call fails at once rather than waiting out `timeout_s`, and a CLI that
answers again before taking a result it was handed (a denied or skipped tool call) is a
session error, never a response passed off as one that read the Target's result.

**The response body is reassembled, not wire bytes**, as decision 23 says of the Judge's:
laid out per API call from the stream events the CLI forwards (`message_start`,
`content_block_*`, `message_delta`, `message_stop`) — the API's own layout, not the SDK's
per-block `AssistantMessage`s, whose parser drops block types it does not know. Tool names
lose the `mcp__target__` prefix the model saw, so the Target reads the names it offered. A
`claude_code` block carries the session id and the CLI version, `startup_ms` on a session's
first call (decision 36) and the Turn's `ResultMessage` figures on its last.

**What this Backend cannot carry is refused, never trimmed** (decision 31):
`ClaudeCodeRefused` before anything is sent, `ClaudeCodeSessionError` when the session could
not answer. What the CLI reports as a model failure is an HTTP error response, so the Target
meets what the API would have sent (decision 32); the session is spent after one, because
the conversation the CLI holds no longer matches the one the Target will resend.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import sys
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx2
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    Message,
    ResultMessage,
    SdkMcpTool,
    StreamEvent,
    SystemMessage,
    ToolAnnotations,
    create_sdk_mcp_server,
    tool,
)

from agentdiag.model.claude_code import ClaudeCodeCli, with_stderr
from agentdiag.model.replay import canonical

DEFAULT_TIMEOUT_S = 600.0
"""One request's ceiling: the CLI's start-up, the model's answer and the Turn's result."""

CLOSE_TIMEOUT_S = 10.0
"""The one deadline `close()` shares across the disconnect, the loop's last tasks and the
thread's join; past it the thread is left to die with the process."""

TOOL_RESULT_INLINE_CHARS = 1_000_000
"""`maxResultSizeChars` for every Target tool: below it the CLI keeps a tool result inline,
so the model reads the Target's result and never a preview of a file (decision 34)."""

SERVER_NAME = "target"
TOOL_PREFIX = f"mcp__{SERVER_NAME}__"
"""What the CLI puts before an in-process MCP tool's name; the Target never sees it."""

MESSAGES_HOST = "api.anthropic.com"
"""The host the `anthropic` SDK sends to by default, and the only one this Backend serves:
a Target that points its client elsewhere meant somewhere else, so it is refused, not
answered by Claude Code in that host's place."""

MESSAGES_PATH = "/v1/messages"
"""The Messages API's path: the one path the capture records and this Backend serves."""

CARRIED_KEYS = frozenset({"model", "max_tokens", "system", "tools", "messages"})
"""The request keys the CLI can carry as sent; any other is refused by name (decision 31)."""

TOOL_KEYS = frozenset({"name", "description", "input_schema"})
"""A tool's keys the MCP server can carry; `cache_control` or a server tool is refused."""

TOOL_RESULT_KEYS = frozenset({"type", "tool_use_id", "content", "is_error"})
"""A `tool_result` block's keys a handler can hand the CLI; `cache_control` is refused."""

SESSION_KEYS = ("model", "max_tokens", "system", "tools")
"""What a session fixes at its start: the CLI cannot change them mid-session."""

TEXT_DELTAS = {
    ("text", "text_delta"): "text",
    ("thinking", "thinking_delta"): "thinking",
    ("thinking", "signature_delta"): "signature",
}
"""(block type, delta type) → the field the delta appends to, spelled alike in both."""

ERROR_STATUS = 502
"""The status of a CLI-reported failure that names none (decision 32)."""

THREAD_NAME = "agentdiag-claude-code"
"""The session thread's name, so a test can see that `close` ended it."""

STDERR_KEPT = 200
"""How many of the CLI's stderr lines a session keeps; an error quotes the last few."""

ROUTE_AROUND = "unset it or export ANTHROPIC_API_KEY"
"""What every refusal ends with: the two ways a developer gets the request through — change
it so this Backend can carry it, or put the Target's calls on the Messages API instead."""

TAINTED = (
    "the previous call on this session failed; the conversation the CLI holds no longer "
    "matches the Target's"
)
"""Decision 31: after an error response the CLI holds a user message the Target will send
again, so the session answers nothing more rather than let it be sent twice."""

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
"""An in-process MCP tool's handler, keyed by the Target's un-prefixed tool name."""


class ClaudeCodeSession(Protocol):
    """What the transport drives: `ClaudeSDKClient` by default, a fake in a test."""

    async def connect(self) -> None: ...

    async def send(self, message: dict[str, Any]) -> None: ...

    def receive(self) -> AsyncIterator[Message]: ...

    async def disconnect(self) -> None: ...


SessionFactory = Callable[[ClaudeAgentOptions], ClaudeCodeSession]
"""Builds the session from its options; the Target's tools are in their MCP server."""


class ClaudeCodeTransportError(RuntimeError):
    """The Claude Code Backend could not carry a Target's request: agentdiag's to fix, or the
    developer's to route around, never the Target's fault (decision 31)."""


class ClaudeCodeRefused(ClaudeCodeTransportError):
    """A request this Backend cannot carry as sent, refused before anything reached the CLI."""


class ClaudeCodeSessionError(ClaudeCodeTransportError):
    """A session that could not answer: no answer in time, a stream that ended, an answer
    before a pending tool result was taken, a tool call that matches no block, a stream the
    reassembly cannot follow, or a session spent by an earlier failure."""


class SdkSession:
    """`ClaudeSDKClient` as a `ClaudeCodeSession`: the default the factory builds."""

    def __init__(self, options: ClaudeAgentOptions) -> None:
        self.client = ClaudeSDKClient(options=options)

    async def connect(self) -> None:
        await self.client.connect()

    async def send(self, message: dict[str, Any]) -> None:
        async def one() -> AsyncIterator[dict[str, Any]]:
            yield message

        await self.client.query(one())

    def receive(self) -> AsyncIterator[Message]:
        return self.client.receive_messages()

    async def disconnect(self) -> None:
        await self.client.disconnect()


def sdk_session(options: ClaudeAgentOptions) -> SdkSession:
    """The default factory."""
    return SdkSession(options)


@dataclass
class _NewMessage:
    content: Any
    """The user message's content, as the Target sent it."""


@dataclass
class _ToolResults:
    results: dict[str, dict[str, Any]]
    """By `tool_use_id`, what the handler returns: text content blocks and `is_error`."""


_Ask = _NewMessage | _ToolResults
"""What one request asks of the session."""


@dataclass
class _Answer:
    status: int
    body: dict[str, Any]


class ClaudeCodeTransport(httpx2.BaseTransport):
    """Serves the Messages API over one Claude Code session (decision 30).

    Refusals that need no session are checked on the caller's thread, before anything is
    sent; everything that talks to the CLI runs on the session's own loop. `_state` guards
    the fields both threads touch and is never held while a request waits; `_serving` keeps
    two requests from interleaving and is never taken by `close()`.
    """

    def __init__(
        self,
        cli: ClaudeCodeCli,
        *,
        session: SessionFactory | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.cli = cli
        self.timeout_s = timeout_s
        self._factory: SessionFactory = session if session is not None else sdk_session
        self._state = threading.Lock()
        self._serving = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._waiting: concurrent.futures.Future[_Answer] | None = None
        self._closed = False
        self._broken: str | None = None
        self._stderr: deque[str] = deque(maxlen=STDERR_KEPT)
        # The conversation as the Target has sent it, checked before each request.
        self._first: dict[str, Any] | None = None
        self._history: list[Any] | None = None
        self._pending: list[str] = []
        # The CLI's side, touched only on the session's loop.
        self._live: _Live | None = None

    # --- options (decision 34) ---

    def tools(self, body: Mapping[str, Any]) -> list[SdkMcpTool[Any]]:
        """The Target's tools as in-process MCP tools, each wrapping this transport's handler
        for it, its schema passed through as the JSON Schema it is."""
        return [
            tool(
                str(declared["name"]),
                str(declared.get("description") or ""),
                dict(declared["input_schema"]),
                annotations=ToolAnnotations(maxResultSizeChars=TOOL_RESULT_INLINE_CHARS),
            )(self._handler(str(declared["name"])))
            for declared in body.get("tools") or []
        ]

    def options(
        self, body: Mapping[str, Any], *, stderr: Callable[[str], None] | None = None
    ) -> ClaudeAgentOptions:
        """The session's `ClaudeAgentOptions`: the Target's prompt, model and tools, and
        nothing of Claude Code's own.

        `system_prompt` is a string, so it replaces the preset whole; `tools=[]` offers none
        of the CLI's built-in tools, and the MCP server `target` offers the Target's, each
        allowed by name, so no permission prompt is asked (`allowed_tools` is the approval).
        `setting_sources=[]` and `skills=[]` load no settings file and no skill;
        `verbatim_prompts=True` marks every user message `client_composed`, so an `@path` or
        a slash command inside a Target's conversation is text and nothing else;
        `include_partial_messages=True` forwards the API's own stream events, which the
        response is reassembled from. No `max_turns` (the Target's loop decides), no
        `thinking` (the Target sent none). `max_tokens` has no field: Claude Code reads
        `CLAUDE_CODE_MAX_OUTPUT_TOKENS`. `--no-session-persistence` keeps the conversation
        out of `~/.claude/projects/`.
        """
        names = [str(declared["name"]) for declared in body.get("tools") or []]
        return ClaudeAgentOptions(
            system_prompt=body.get("system") or "",
            tools=[],
            allowed_tools=[f"{TOOL_PREFIX}{name}" for name in names],
            mcp_servers={SERVER_NAME: create_sdk_mcp_server(SERVER_NAME, tools=self.tools(body))},
            strict_mcp_config=True,
            setting_sources=[],
            skills=[],
            model=body.get("model"),
            include_partial_messages=True,
            verbatim_prompts=True,
            env={
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(body.get("max_tokens")),
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            },
            cli_path=self.cli.path,
            extra_args={"no-session-persistence": None},
            stderr=stderr if stderr is not None else _discard,
        )

    # --- one request ---

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.host != MESSAGES_HOST or request.url.path != MESSAGES_PATH:
            raise ClaudeCodeRefused(
                f"the Claude Code Backend serves the Messages API at {MESSAGES_HOST} only; "
                f"this request is for {request.url.host}{request.url.path}; {ROUTE_AROUND}"
            )
        body = json.loads(request.read() or b"{}")
        with self._serving:
            with self._state:
                if self._closed:
                    raise ClaudeCodeSessionError("the Claude Code session is closed")
                if self._broken is not None:
                    raise ClaudeCodeSessionError(
                        f"the Claude Code session cannot answer after an earlier failure: "
                        f"{self._broken}"
                    )
                ask = self._read(body)
                loop = self._ensure_loop()
                waiting = asyncio.run_coroutine_threadsafe(self._answer(body, ask), loop)
                self._waiting = waiting
            answer = self._wait(waiting)
            with self._state:
                if answer.status != 200:
                    self._broken = TAINTED
                    return _response(answer)
                if self._first is None:
                    self._first = {key: body.get(key) for key in SESSION_KEYS}
                self._history = list(body.get("messages") or [])
                self._pending = [
                    str(block.get("id"))
                    for block in answer.body["content"]
                    if block.get("type") == "tool_use"
                ]
            return _response(answer)

    def _wait(self, waiting: concurrent.futures.Future[_Answer]) -> _Answer:
        """The request's answer, or the session error that says why there is none. Whatever
        stops the wait — a timeout, a close, a Ctrl-C — cancels the request on the loop."""
        try:
            return waiting.result(timeout=self.timeout_s)
        except TimeoutError as exc:
            raise self._break(
                f"the Claude Code CLI did not answer within {self.timeout_s} s"
            ) from exc
        except concurrent.futures.CancelledError as exc:
            raise self._break("the Claude Code session was closed while a request waited") from exc
        except ClaudeCodeSessionError as exc:
            raise self._break(str(exc)) from exc
        except ClaudeCodeTransportError:
            raise
        except KeyboardInterrupt:
            self._break("a request was interrupted")
            raise
        except Exception as exc:
            raise self._break(f"{type(exc).__name__}: {exc}") from exc
        finally:
            waiting.cancel()
            with self._state:
                self._waiting = None

    def close(self) -> None:
        """End the session and its thread within one `CLOSE_TIMEOUT_S` deadline, shared by
        the disconnect, the loop's last tasks and the thread's join; a second close does
        nothing (decision 30).

        A request still waiting is cancelled first, so a close from another thread never
        waits behind it. Nothing but a Ctrl-C is raised: a CLI that would not end cleanly is
        said on stderr and left to the SDK's own at-exit kill, because a close runs when a
        Trial is over and must not turn a finished Trial into a failed one.
        """
        deadline = time.monotonic() + CLOSE_TIMEOUT_S

        def left() -> float:
            return max(deadline - time.monotonic(), 0.0)

        with self._state:
            if self._closed:
                return
            self._closed = True
            loop, thread, waiting = self._loop, self._thread, self._waiting
        if waiting is not None:
            waiting.cancel()
        if loop is None or thread is None:
            return
        problems: list[str] = []
        shutdown = asyncio.run_coroutine_threadsafe(self._shutdown(deadline), loop)
        try:
            problem = shutdown.result(timeout=left())
            if problem:
                problems.append(problem)
        except TimeoutError:
            shutdown.cancel()
            problems.append(f"it did not end within {CLOSE_TIMEOUT_S} s")
        except Exception as exc:
            shutdown.cancel()
            problems.append(f"{type(exc).__name__}: {exc}")
        try:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=left())
            if thread.is_alive():
                problems.append("its thread did not stop")
            else:
                loop.close()
        except Exception as exc:
            problems.append(f"{type(exc).__name__}: {exc}")
        if problems:
            print(
                "warning: the Claude Code session did not end cleanly: " + "; ".join(problems),
                file=sys.stderr,
            )

    # --- the caller's side: what the Target sent, checked before anything is sent ---

    def _read(self, body: Mapping[str, Any]) -> _Ask:
        """What this request asks of the session, or `ClaudeCodeRefused` naming the first
        thing this Backend cannot carry as sent (decision 31)."""
        unknown = sorted(set(body) - CARRIED_KEYS)
        if unknown:
            raise _refused(f"the Claude Code Backend cannot carry {', '.join(unknown)} as sent")
        system = body.get("system")
        if system is not None and not isinstance(system, str):
            raise _refused(
                "the Claude Code Backend carries a system prompt that is a string, not a "
                "list of blocks"
            )
        for declared in body.get("tools") or []:
            _check_tool(declared)
        messages = body.get("messages")
        if not isinstance(messages, list):
            raise _refused("the request carries no list of messages")

        if self._first is None or self._history is None:
            if len(messages) != 1 or not _is_role(messages[0], "user"):
                raise _refused(
                    "the first request of a Claude Code session carries one user message; "
                    f"this one carries {len(messages)} messages"
                )
            return _read_user_message(messages[0], pending=[])

        for key in SESSION_KEYS:
            if canonical(body.get(key)) != canonical(self._first[key]):
                raise _refused(
                    f"the request's {key} differs from the session's first; the Claude Code "
                    "CLI cannot change it mid-session"
                )
        previous = self._history
        if len(messages) != len(previous) + 2 or canonical(messages[: len(previous)]) != canonical(
            previous
        ):
            raise _refused(
                "the request's messages do not continue the previous request's exactly "
                "(the Claude Code CLI holds the conversation, so history cannot be rewritten, "
                "trimmed or reordered)"
            )
        assistant = messages[-2]
        if not _is_role(assistant, "assistant"):
            raise _refused(
                "the request's messages do not add one assistant message and one user "
                "message to the previous request's"
            )
        # By its `tool_use` ids, not its whole content: a Target may drop thinking blocks
        # before resending, and the CLI holds the model's own answer either way.
        asked = _tool_use_ids(assistant)
        if asked != self._pending:
            raise _refused(
                f"the assistant message's tool_use ids {asked} are not the ones the session "
                f"answered with {self._pending}"
            )
        return _read_user_message(messages[-1], pending=self._pending)

    def _break(self, detail: str) -> ClaudeCodeSessionError:
        """A session failure, with the CLI's last stderr lines; later requests refuse too."""
        with self._state:
            self._broken = detail
        return ClaudeCodeSessionError(with_stderr(detail, list(self._stderr)))

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=_run_forever, args=(loop,), name=THREAD_NAME, daemon=True
            )
            thread.start()
            self._loop, self._thread = loop, thread
        return self._loop

    # --- the session's side, on its loop ---

    def _handler(self, name: str) -> ToolHandler:
        async def handle(arguments: dict[str, Any]) -> dict[str, Any]:
            if self._live is None:
                raise ClaudeCodeSessionError(f"{name} was called before the session started")
            return await self._live.tool_called(name, arguments)

        return handle

    async def _answer(self, body: Mapping[str, Any], ask: _Ask) -> _Answer:
        live = self._live
        if live is None:
            live = await self._connect(body)
        if isinstance(ask, _NewMessage):
            await live.session.send(
                {
                    "type": "user",
                    "message": {"role": "user", "content": ask.content},
                    "parent_tool_use_id": None,
                    "session_id": "default",
                }
            )
        else:
            live.deliver(ask.results)
        return await live.next_response()

    async def _connect(self, body: Mapping[str, Any]) -> _Live:
        session = self._factory(self.options(body, stderr=self._stderr.append))
        loop = asyncio.get_running_loop()
        live = _Live(session=session, cli_version=self.cli.version, stderr=self._stderr)
        live.began = loop.time()
        self._live = live
        await session.connect()
        live.connected = loop.time()
        live.reader = asyncio.create_task(live.read())
        return live

    async def _shutdown(self, deadline: float) -> str | None:
        """Disconnect, then cancel what is left on the loop, both by `deadline`."""
        problem = await self._live.shutdown(deadline) if self._live is not None else None
        current = asyncio.current_task()
        tasks = [task for task in asyncio.all_tasks() if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=max(deadline - time.monotonic(), 0.0))
        return problem


class _End:
    """The session's stream ended, or failed with `error`."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error


@dataclass
class _Live:
    """One running Claude Code session: its inbox, and the tool handshake's state."""

    session: ClaudeCodeSession
    cli_version: str
    stderr: deque[str]
    began: float = 0.0
    connected: float = 0.0
    startup_ms: int | None = None
    startup_reported: bool = False
    reader: asyncio.Task[None] | None = None
    inbox: asyncio.Queue[Message | _End] = field(default_factory=asyncio.Queue)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    unclaimed: list[tuple[str, str, str]] = field(default_factory=list)
    """(id, un-prefixed name, canonical input) of each `tool_use` no handler has claimed."""
    results: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    delivered: set[str] = field(default_factory=set)
    taken: set[str] = field(default_factory=set)
    ended: _End | None = None
    complete: bool = False
    """Whether the call being read has reached its `message_stop`: a handler that matches no
    block by then never will."""

    async def read(self) -> None:
        """Move every message the session yields into the inbox, for the whole session."""
        loop = asyncio.get_running_loop()
        try:
            async for message in self.session.receive():
                if (
                    isinstance(message, SystemMessage)
                    and message.subtype == "init"
                    and self.startup_ms is None
                ):
                    self.startup_ms = round((loop.time() - self.began) * 1000)
                await self.inbox.put(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.inbox.put(_End(exc))
        else:
            await self.inbox.put(_End())

    async def tool_called(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """The CLI's call of one Target tool: find its `tool_use` by content and hand back
        the Target's result for it, whichever of the two arrived first."""
        key = canonical(dict(arguments))
        async with self.changed:
            while (call_id := self._claim(name, key)) is None:
                if self.complete:
                    await self._fail(
                        ClaudeCodeSessionError(
                            f"the Claude Code CLI called {name} with {key}, which matches no "
                            "tool_use block of the call it answered"
                        )
                    )
                await self.changed.wait()
        result = await self._future(call_id)
        self.taken.add(call_id)
        return result

    def deliver(self, results: Mapping[str, dict[str, Any]]) -> None:
        for call_id, result in results.items():
            future = self._future(call_id)
            if future.done():
                raise ClaudeCodeSessionError(f"the tool result for {call_id} was delivered twice")
            future.set_result(result)
            self.delivered.add(call_id)

    async def next_response(self) -> _Answer:
        """Read the session until one API call is answered, and lay it out (decision 30).

        A call that asked for tools is answered at its `message_stop`; a Turn's last call is
        answered once the Turn's `ResultMessage` has arrived, so the Turn is settled when the
        Target reads its answer. A result that says the Turn failed is an error response
        (decision 32), even before any call began.
        """
        call: _Reassembly | None = None
        answered: dict[str, Any] | None = None
        assistant_errors: list[str] = []
        while True:
            message = await self._next()
            if isinstance(message, StreamEvent):
                if message.parent_tool_use_id is not None:
                    continue
                event = message.event
                kind = event.get("type")
                if kind == "message_start":
                    self._guard_taken()
                    if call is not None:
                        raise _unfollowable("a second message_start before the first call ended")
                    call = _Reassembly(event.get("message") or {}, message.session_id)
                    await self._completed(False)
                    continue
                if kind not in {
                    "content_block_start",
                    "content_block_delta",
                    "content_block_stop",
                    "message_delta",
                    "message_stop",
                }:
                    continue
                if call is None or call.stopped:
                    raise _unfollowable(f"a {kind} outside a call")
                if kind == "content_block_start":
                    call.start_block(event)
                elif kind == "content_block_delta":
                    call.delta(event)
                elif kind == "content_block_stop":
                    finished = call.stop_block(event)
                    if finished.get("type") == "tool_use":
                        await self._offer(finished)
                elif kind == "message_delta":
                    call.message_delta(event)
                else:
                    call.stopped = True
                    answered = self._body(call)
                    await self._completed(True)
                    if answered["stop_reason"] == "tool_use":
                        return _Answer(200, answered)
            elif isinstance(message, AssistantMessage):
                if message.error is not None and message.parent_tool_use_id is None:
                    assistant_errors.append(message.error)
            elif isinstance(message, ResultMessage):
                self._guard_taken()
                failed = message.is_error or message.subtype != "success" or assistant_errors
                if failed:
                    return self._failure(message, assistant_errors)
                if answered is None:
                    raise _unfollowable(
                        "the Turn's result arrived before its call's message_stop"
                        if call is not None
                        else "the Turn ended in a result without any call"
                    )
                answered["claude_code"]["turn"] = {
                    "total_cost_usd": message.total_cost_usd,
                    "num_turns": message.num_turns,
                    "duration_api_ms": message.duration_api_ms,
                    "usage": message.usage,
                }
                return _Answer(200, answered)
            # SystemMessage, RateLimitEvent, the UserMessage echoes: not the request's own.

    async def shutdown(self, deadline: float) -> str | None:
        """Disconnect by `deadline`, then stop reading and fail what waits."""
        problem: str | None = None
        try:
            async with asyncio.timeout(max(deadline - time.monotonic(), 0.0)):
                await self.session.disconnect()
        except TimeoutError:
            problem = f"the disconnect did not finish within {CLOSE_TIMEOUT_S} s"
        except Exception as exc:
            problem = f"{type(exc).__name__}: {exc}"
        if self.reader is not None:
            self.reader.cancel()
        for future in self.results.values():
            if not future.done():
                future.cancel()
        return problem

    async def _completed(self, complete: bool) -> None:
        async with self.changed:
            self.complete = complete
            self.changed.notify_all()

    async def _fail(self, error: ClaudeCodeSessionError) -> None:
        """A handler's failure, raised to the CLI (which tells the model) and queued for the
        Target's next request, which is where agentdiag learns of it."""
        await self.inbox.put(_End(error))
        raise error

    def _claim(self, name: str, key: str) -> str | None:
        for index, (call_id, offered, input_key) in enumerate(self.unclaimed):
            if offered == name and input_key == key:
                del self.unclaimed[index]
                return call_id
        return None

    async def _offer(self, block: Mapping[str, Any]) -> None:
        async with self.changed:
            self.unclaimed.append(
                (str(block.get("id")), str(block.get("name")), canonical(block.get("input")))
            )
            self.changed.notify_all()

    def _future(self, call_id: str) -> asyncio.Future[dict[str, Any]]:
        if call_id not in self.results:
            self.results[call_id] = asyncio.get_running_loop().create_future()
        return self.results[call_id]

    def _guard_taken(self) -> None:
        """The CLI moved on: every result the Target delivered must have been read."""
        untaken = sorted(self.delivered - self.taken)
        if untaken:
            raise ClaudeCodeSessionError(
                "the Claude Code CLI answered before taking the Target's tool result for "
                f"{', '.join(untaken)} (a denied or skipped tool call)"
            )

    async def _next(self) -> Message:
        if self.ended is not None:
            raise _ended(self.ended)
        item = await self.inbox.get()
        if isinstance(item, _End):
            self.ended = item
            raise _ended(item)
        return item

    def _body(self, call: _Reassembly) -> dict[str, Any]:
        body = call.body()
        block: dict[str, Any] = {"session_id": call.session_id, "cli_version": self.cli_version}
        if not self.startup_reported:
            self.startup_reported = True
            startup = self.startup_ms
            if startup is None:
                startup = round((self.connected - self.began) * 1000)
            block["startup_ms"] = startup
        body["claude_code"] = block
        return body

    def _failure(self, result: ResultMessage, assistant_errors: Sequence[str]) -> _Answer:
        """A Turn the CLI reports failed, as the API's own error response (decision 32)."""
        facts = [f"subtype {result.subtype}"]
        if result.is_error:
            facts.append("is_error")
        if result.api_error_status is not None:
            facts.append(f"api_error_status {result.api_error_status}")
        said = [
            *(f"assistant error {error}" for error in assistant_errors),
            *(result.errors or []),
            *([result.result] if result.result else []),
        ]
        detail = f"the Claude Code CLI reported the Turn failed ({', '.join(facts)})"
        if said:
            detail += ": " + "; ".join(said)
        detail = with_stderr(detail, list(self.stderr))
        error_type = result.subtype if result.subtype != "success" else "api_error"
        return _Answer(
            result.api_error_status or ERROR_STATUS,
            {"type": "error", "error": {"type": error_type, "message": detail}},
        )


class _Reassembly:
    """One API call laid out from its stream events, in the Messages API's own layout."""

    def __init__(self, message: Mapping[str, Any], session_id: str) -> None:
        self.session_id = session_id
        self.id = message.get("id")
        self.model = message.get("model")
        self.role = message.get("role", "assistant")
        self.usage: dict[str, Any] = dict(message.get("usage") or {})
        self.blocks: dict[int, dict[str, Any]] = {}
        self.partial_json: dict[int, list[str]] = {}
        self.open: set[int] = set()
        self.stop_reason: Any = None
        self.stop_sequence: Any = None
        self.stop_details: Any = None
        self.stopped = False

    def start_block(self, event: Mapping[str, Any]) -> None:
        index = event.get("index")
        block = event.get("content_block")
        if not isinstance(index, int) or not isinstance(block, Mapping) or index in self.blocks:
            raise _unfollowable(f"a content_block_start at index {index!r}")
        if block.get("type") not in {"text", "thinking", "redacted_thinking", "tool_use"}:
            raise _unfollowable(f"a content block of type {block.get('type')!r}")
        self.blocks[index] = dict(block)
        self.open.add(index)
        if block.get("type") == "tool_use":
            self.partial_json[index] = []

    def delta(self, event: Mapping[str, Any]) -> None:
        index = event.get("index")
        delta = event.get("delta") or {}
        if index not in self.open or not isinstance(index, int):
            raise _unfollowable(f"a content_block_delta for index {index!r}, which is not open")
        block = self.blocks[index]
        kind = str(delta.get("type"))
        pair = (str(block.get("type")), kind)
        if pair in TEXT_DELTAS:
            target = TEXT_DELTAS[pair]
            block[target] = str(block.get(target) or "") + str(delta.get(target) or "")
        elif pair == ("tool_use", "input_json_delta"):
            self.partial_json[index].append(str(delta.get("partial_json") or ""))
        else:
            raise _unfollowable(f"a {kind!r} delta on a {block.get('type')!r} block")

    def stop_block(self, event: Mapping[str, Any]) -> dict[str, Any]:
        index = event.get("index")
        if index not in self.open or not isinstance(index, int):
            raise _unfollowable(f"a content_block_stop for index {index!r}, which is not open")
        self.open.discard(index)
        block = self.blocks[index]
        if block.get("type") == "tool_use":
            name = str(block.get("name"))
            if not name.startswith(TOOL_PREFIX):
                raise ClaudeCodeSessionError(
                    f"the model called {name}, a tool the Target did not offer"
                )
            block["name"] = name[len(TOOL_PREFIX) :]
            text = "".join(self.partial_json.pop(index))
            try:
                arguments = json.loads(text) if text else {}
            except ValueError as exc:
                raise _unfollowable(f"a tool input that is not JSON: {text!r}") from exc
            if not isinstance(arguments, dict):
                raise _unfollowable(f"a tool input that is not an object: {text!r}")
            block["input"] = arguments
        return block

    def message_delta(self, event: Mapping[str, Any]) -> None:
        delta = event.get("delta") or {}
        self.stop_reason = delta.get("stop_reason")
        self.stop_sequence = delta.get("stop_sequence")
        self.stop_details = delta.get("stop_details")
        # The final usage over the snapshot: the `cache_creation` split and `service_tier`
        # live only in `message_start`'s.
        self.usage.update(event.get("usage") or {})

    def body(self) -> dict[str, Any]:
        if self.open:
            raise _unfollowable(f"a message_stop with blocks {sorted(self.open)} still open")
        return {
            "id": self.id,
            "type": "message",
            "role": self.role,
            "model": self.model,
            "content": [self.blocks[index] for index in sorted(self.blocks)],
            "stop_reason": self.stop_reason,
            "stop_sequence": self.stop_sequence,
            "stop_details": self.stop_details,
            "usage": self.usage,
        }


def _check_tool(declared: Any) -> None:
    if not isinstance(declared, Mapping):
        raise _refused(f"a tool that is not an object: {declared!r}")
    extra = sorted(set(declared) - TOOL_KEYS)
    if extra:
        raise _refused(
            f"the Claude Code Backend cannot carry the tool {declared.get('name')!r}'s "
            f"{', '.join(extra)}"
        )
    schema = declared.get("input_schema")
    # The SDK passes a schema through only when it has a string `type` and `properties`;
    # anything else it would rebuild as a map of parameter names to Python types.
    if not (
        isinstance(schema, Mapping)
        and isinstance(schema.get("type"), str)
        and "properties" in schema
    ):
        raise _refused(
            f"the tool {declared.get('name')!r}'s input_schema has no type and properties, "
            "and the Claude Agent SDK would rebuild it rather than pass it through"
        )


def _read_user_message(message: Any, *, pending: Sequence[str]) -> _Ask:
    """The request's last message as a new user message or as the pending tool results."""
    if not _is_role(message, "user"):
        raise _refused("the request's last message is not a user message")
    content = message.get("content")
    results = (
        [
            block
            for block in content
            if isinstance(block, Mapping) and block.get("type") == "tool_result"
        ]
        if isinstance(content, list)
        else []
    )
    if not results:
        if pending:
            raise _refused(
                f"a new user message while the tool results for {', '.join(pending)} are "
                "pending (the Claude Code CLI's handlers are waiting for them)"
            )
        return _NewMessage(content)
    if len(results) != len(content):
        raise _refused(
            "a user message that mixes text with tool results cannot be carried: the "
            "Claude Code CLI takes tool results through its tool handlers"
        )
    ids = [str(block.get("tool_use_id")) for block in results]
    if sorted(ids) != sorted(pending):
        raise _refused(
            f"the tool results cover {ids}, and the pending tool_use ids are {list(pending)}"
        )
    for block in results:
        extra = sorted(set(block) - TOOL_RESULT_KEYS)
        if extra:
            raise _refused(
                f"the Claude Code Backend cannot carry the tool result for "
                f"{block.get('tool_use_id')}'s {', '.join(extra)}"
            )
    return _ToolResults(
        {
            str(block.get("tool_use_id")): {
                "content": _text_content(block),
                "is_error": bool(block.get("is_error") or False),
            }
            for block in results
        }
    )


def _text_content(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A tool result's content as text blocks, or `ClaudeCodeRefused` when it is not text."""
    content = block.get("content")
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    texts: list[dict[str, Any]] = []
    for item in content if isinstance(content, list) else [content]:
        if not (
            isinstance(item, Mapping)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
            and set(item) == {"type", "text"}
        ):
            raise _refused(
                f"the tool result for {block.get('tool_use_id')} carries content that is not "
                "plain text"
            )
        texts.append({"type": "text", "text": item["text"]})
    return texts


def _tool_use_ids(message: Mapping[str, Any]) -> list[str]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [
        str(block.get("id"))
        for block in content
        if isinstance(block, Mapping) and block.get("type") == "tool_use"
    ]


def _is_role(message: Any, role: str) -> bool:
    return isinstance(message, Mapping) and message.get("role") == role


def _response(answer: _Answer) -> httpx2.Response:
    return httpx2.Response(
        answer.status,
        json=answer.body,
        headers={"content-type": "application/json", "request-id": "claude-code"},
    )


def _refused(detail: str) -> ClaudeCodeRefused:
    """A refusal, ending as every refusal does with the way round it (decision 31)."""
    return ClaudeCodeRefused(f"{detail}; {ROUTE_AROUND}")


def _unfollowable(detail: str) -> ClaudeCodeSessionError:
    return ClaudeCodeSessionError(
        f"the Claude Code CLI delivered a stream the reassembly cannot follow: {detail}"
    )


def _ended(end: _End) -> ClaudeCodeSessionError:
    if isinstance(end.error, ClaudeCodeSessionError):
        return end.error
    if end.error is not None:
        return ClaudeCodeSessionError(
            f"the Claude Code session failed: {type(end.error).__name__}: {end.error}"
        )
    return ClaudeCodeSessionError("the Claude Code CLI's stream ended")


def _run_forever(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _discard(line: str) -> None:
    """A stderr callback that keeps nothing, for options built outside a session."""


__all__ = [
    "CARRIED_KEYS",
    "CLOSE_TIMEOUT_S",
    "DEFAULT_TIMEOUT_S",
    "MESSAGES_HOST",
    "MESSAGES_PATH",
    "ROUTE_AROUND",
    "SERVER_NAME",
    "TOOL_KEYS",
    "TOOL_PREFIX",
    "TOOL_RESULT_INLINE_CHARS",
    "TOOL_RESULT_KEYS",
    "ClaudeCodeRefused",
    "ClaudeCodeSession",
    "ClaudeCodeSessionError",
    "ClaudeCodeTransport",
    "ClaudeCodeTransportError",
    "SdkSession",
    "SessionFactory",
    "ToolHandler",
    "sdk_session",
]
