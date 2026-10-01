"""The Claude Code backend: Judge calls through the Claude Agent SDK and the developer's login.

A second live `ModelClient` beside `LiveAnthropicClient` (phase-5 interfaces, ticket 19).
Everything above the seam is unchanged: the Judge builds the same `ModelRequest`, the
recording key is still `request.body()`, and the four answer rules still live in
`Judge.ask`. What differs is the path a call takes to the model — the **Backend**
(`CONTEXT.md`, decision 20) — and three things follow from taking this one:

- **A Judge call must not become a Claude Code session.** Every call is one `query()`
  with the whole system prompt replaced, no tools, no MCP servers, no settings file, no
  skills, two turns and `--no-session-persistence` (`ClaudeCodeClient.options`), so no
  transcript of the Judge's prompt and the Target's Trace is saved under
  `~/.claude/projects/` or offered in the user's resume list. What the CLI would add around the
  Judge's prompt is exactly what the Judge Fingerprint cannot see, so as little as the CLI
  allows is added, and the CLI's version is recorded because what it still adds is its own.
- **The response body is reassembled, not wire bytes** (decision 23). The CLI speaks its
  own stream; this module lays what it reported out in the Messages layout the recording
  and `judgement.jsonl` already use, plus `structured_output` and a `claude_code` block the
  Messages API has no place for. Only what the SDK said is recorded: no field is filled
  with a default and no block the SDK does not name is invented, and a request this
  backend cannot send whole (tools, sampling parameters, more than one message) is refused
  rather than trimmed. The request side is `request.body()` unchanged, so a recording made
  here replays byte for byte through `ReplayModelClient`.
- **Every failure is rule 4, told in the CLI's words** (decision 24): a result marked as an
  error, a subtype other than `success`, an assistant error, no answer, a timeout, or any
  exception out of the SDK becomes `ClaudeCodeError` carrying what the CLI said and the
  stderr lines captured while it ran. agentdiag never retries (D13); the one retry a call
  may hold is the CLI's own, when it rejects a structured answer that does not fit the
  schema and re-prompts (decision 42), and that rejected attempt is in the recorded body.

Which binary runs is decided once (decision 22): the one the SDK would spawn, found the
SDK's way, probed for its version and its login (`<cli> auth status`, never its files) by
`credentials.resolve()` in preflight, and handed back to the SDK as `cli_path` so the
version recorded is the binary that ran. `live_client` builds from what preflight resolved
and never probes again.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import anyio
import claude_agent_sdk
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    Message,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from pydantic import BaseModel

from agentdiag.backend import Backend
from agentdiag.model.client import (
    LiveAnthropicClient,
    ModelClient,
    ModelRequest,
    ModelResponse,
    SamplingNotSupported,
)
from agentdiag.types import BackendKind

if TYPE_CHECKING:
    from agentdiag.model.credentials import CredentialSource

CLI_NAME = "claude"

CLI_LOCATIONS = (
    Path.home() / ".npm-global/bin/claude",
    Path("/usr/local/bin/claude"),
    Path.home() / ".local/bin/claude",
    Path.home() / "node_modules/.bin/claude",
    Path.home() / ".yarn/bin/claude",
    Path.home() / ".claude/local/claude",
)
"""Where the SDK looks for the CLI after its bundled binary and PATH, in its order (0.2.158,
`SubprocessCLITransport._find_cli`); a test pins `cli_path()` to that method."""

PROBE_TIMEOUT_S = 30.0
"""`--version` and `auth status` take about 1.5 s each; a probe that hangs is no login."""

DEFAULT_TIMEOUT_S = 600.0
"""One Judge call's ceiling (decision 24): generous for a long Trace, finite for a stuck CLI."""

CLOSE_TIMEOUT_S = 10.0
"""How long closing the SDK's stream may take once a call is over or has timed out."""

STDERR_LINES = 10
"""How many of the CLI's last stderr lines a `ClaudeCodeError` carries."""

VERSION_PATTERN = re.compile(r"^\s*(?P<version>\d+\.\d+\.\d+)")
"""`2.1.280 (Claude Code)` → `2.1.280`."""


class ClaudeCodeCli(BaseModel):
    """What the probes found: the binary the SDK would spawn, and its version."""

    path: str
    version: str


class ClaudeCodeError(RuntimeError):
    """A Claude Code call that produced no judgement, in the CLI's own words (decision 24)."""


class QueryFunction(Protocol):
    """`claude_agent_sdk.query` as the client calls it; a test hands in a fake."""

    def __call__(self, *, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]: ...


def cli_path() -> str | None:
    """The CLI the SDK would spawn, found in the SDK's own order (decision 22): its bundled
    binary, then `claude` on PATH, then the locations it probes. None when there is none."""
    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / CLI_NAME
    if bundled.is_file():
        return str(bundled)
    if found := shutil.which(CLI_NAME):
        return found
    for location in CLI_LOCATIONS:
        if location.is_file():
            return str(location)
    return None


@functools.cache
def cli_version(path: str) -> str:
    """The version `<cli> --version` prints; raises `ClaudeCodeError` when it prints none."""
    try:
        banner = _probe(path, "--version")
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClaudeCodeError(f"{path} --version failed: {exc}") from exc
    match = VERSION_PATTERN.match(banner)
    if match is None:
        raise ClaudeCodeError(f"{path} --version printed no version: {banner.strip()!r}")
    return match.group("version")


@functools.cache
def logged_in(path: str) -> bool:
    """Whether `<cli> auth status` says a login is present (decision 22).

    Asked of the CLI rather than read from its files, as `credentials.py` defers the
    profile layout to the `anthropic` SDK: the CLI's storage is its own business. Anything
    but a JSON `loggedIn: true` is no login.
    """
    try:
        status = json.loads(_probe(path, "auth", "status"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return isinstance(status, dict) and status.get("loggedIn") is True


def cli() -> ClaudeCodeCli | None:
    """The CLI and its version when one is found and logged in, else None: the credential
    probe `credentials.resolve()` tries last (decision 21)."""
    path = cli_path()
    if path is None or not logged_in(path):
        return None
    try:
        return ClaudeCodeCli(path=path, version=cli_version(path))
    except ClaudeCodeError:
        return None


class ClaudeCodeClient:
    """The model reached through the Claude Code CLI, authenticated by its login.

    One `query()` per call, run to completion under `anyio.run`: the Judge is synchronous
    and makes one call at a time, so there is no loop to share.
    """

    def __init__(
        self,
        cli: ClaudeCodeCli,
        *,
        query: QueryFunction | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.cli = cli
        self.query: QueryFunction = query if query is not None else claude_agent_sdk.query
        self.timeout_s = timeout_s

    def options(
        self, request: ModelRequest, *, stderr: Callable[[str], None] | None = None
    ) -> ClaudeAgentOptions:
        """The `ClaudeAgentOptions` for one request: the Judge's prompt and nothing of
        Claude Code's.

        `system_prompt` is a string, so it replaces the preset whole; `tools=[]` sends
        `--tools ""`; `setting_sources=[]` loads no settings file (None would load the
        user's and the project's, CLAUDE.md included) and `skills=[]` lists none.
        `--no-session-persistence` has no field, so it goes in `extra_args` (a None value
        is a bare flag): without it every Judge call is saved as a resumable session. The
        SDK has no `max_tokens`: Claude Code reads `CLAUDE_CODE_MAX_OUTPUT_TOKENS`. `stderr`
        is always a callback, because without one the CLI's stderr is inherited and reaches
        agentdiag's terminal. `max_turns=2` (decision 42, amending decision 23): on this path
        structured output is a `StructuredOutput` tool call the CLI validates after the fact
        and re-prompts once when it does not fit; under one turn that re-prompt had nowhere
        to run and the call ended `error_max_turns`. `verbatim_prompts=True` (ticket 20,
        decision 35) marks the prompt `client_composed`: it is the rendered Trace,
        third-party text included, and without the flag an `@/absolute/path` inside it
        would have the CLI read a local file into the Judge's prompt.
        """
        schema = request.output_schema
        return ClaudeAgentOptions(
            system_prompt=request.system or "",
            tools=[],
            allowed_tools=[],
            mcp_servers={},
            strict_mcp_config=True,
            setting_sources=[],
            skills=[],
            max_turns=2,
            model=request.model,
            effort=request.effort,
            output_format=None if schema is None else {"type": "json_schema", "schema": schema},
            env={
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(request.max_tokens),
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            },
            cli_path=self.cli.path,
            extra_args={"no-session-persistence": None},
            verbatim_prompts=True,
            stderr=stderr if stderr is not None else _discard,
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        prompt = _one_prompt(request)
        stderr: list[str] = []
        options = self.options(request, stderr=stderr.append)
        try:
            messages = anyio.run(self._collect, prompt, options)
        except TimeoutError as exc:
            raise ClaudeCodeError(
                with_stderr(f"the Claude Code CLI did not finish within {self.timeout_s} s", stderr)
            ) from exc
        except Exception as exc:
            raise ClaudeCodeError(with_stderr(f"{type(exc).__name__}: {exc}", stderr)) from exc
        body = self._body(request, messages, stderr)
        return ModelResponse.from_body(request, body)

    async def _collect(self, prompt: str, options: ClaudeAgentOptions) -> list[Message]:
        messages: list[Message] = []
        stream = self.query(prompt=prompt, options=options)
        try:
            with anyio.fail_after(self.timeout_s):
                async for message in stream:
                    messages.append(message)
        finally:
            # Outside the timed scope, so a timed-out call still closes the CLI it spawned.
            close = getattr(stream, "aclose", None)
            if close is not None:
                with anyio.move_on_after(CLOSE_TIMEOUT_S):
                    await close()
        return messages

    def _body(
        self, request: ModelRequest, messages: Sequence[Message], stderr: Sequence[str]
    ) -> dict[str, Any]:
        """The CLI's report in the Messages layout, or `ClaudeCodeError` naming why not.

        The body is the last attempt's (its id, model and blocks); an attempt the CLI
        rejected is under `claude_code.attempts` with the CLI's words (decision 42), and
        `usage` is the result's total over every turn."""
        assistants = [m for m in messages if isinstance(m, AssistantMessage)]
        results = [m for m in messages if isinstance(m, ResultMessage)]

        def fail(detail: str) -> ClaudeCodeError:
            return ClaudeCodeError(with_stderr(f"the Claude Code CLI {detail}", stderr))

        for message in assistants:
            if message.error is not None:
                raise fail(f"returned an assistant error: {message.error}{_said(message)}")
        if not results:
            raise fail("ended with no result")
        outcome = results[-1]
        if outcome.is_error or outcome.subtype != "success":
            facts = [f"subtype {outcome.subtype}"]
            if outcome.is_error:
                facts.append("is_error")
            if outcome.api_error_status is not None:
                facts.append(f"api_error_status {outcome.api_error_status}")
            raise fail(f"ended in an error ({', '.join(facts)}){_errors(outcome)}")
        if not assistants:
            raise fail("returned no assistant message")
        # One id per attempt: the CLI streams one message as several lines sharing an id,
        # and a rejected structured answer is followed by a retry under a new id
        # (decision 42). The last id is the answer; every earlier one is kept verbatim.
        by_id: dict[str | None, list[AssistantMessage]] = {}
        for message in assistants:
            by_id.setdefault(message.message_id, []).append(message)
        identifiers = list(by_id)
        final = by_id[identifiers[-1]]
        rejections = _rejections(messages)
        try:
            content = [_block(block) for message in final for block in message.content]
            attempts = [
                {
                    "id": identifier,
                    "content": [_block(b) for m in by_id[identifier] for b in m.content],
                    "rejected": rejections.get(identifier),
                }
                for identifier in identifiers[:-1]
            ]
        except ClaudeCodeError as unknown:
            raise ClaudeCodeError(with_stderr(str(unknown), stderr)) from unknown
        texts = [block for block in content if block.get("type") == "text" and block.get("text")]
        if request.output_schema is not None and outcome.structured_output is None and not texts:
            raise fail("returned no answer: no structured output and no text")

        # The result's `usage` is the call's total in the Messages API's spelling, split by
        # cache lifetime; an assistant message's is the stream's `message_start` snapshot
        # (seen live: 7 output tokens on a 969-token answer), so it is only the fallback.
        usage = outcome.usage or next(
            (m.usage for m in reversed(final) if m.usage is not None), None
        )
        # The SDK's words or none: `ModelResponse.from_body` defaults its own field, and
        # the recorded body stays what the CLI said (ADR-0004 §1).
        stop_reason = next(
            (m.stop_reason for m in reversed(final) if m.stop_reason is not None),
            outcome.stop_reason,
        )
        report: dict[str, Any] = {
            "total_cost_usd": outcome.total_cost_usd,
            "model_usage": outcome.model_usage,
            "session_id": outcome.session_id,
            "num_turns": outcome.num_turns,
            "duration_api_ms": outcome.duration_api_ms,
            "cli_version": self.cli.version,
        }
        if attempts:
            # Only when there were any: a call answered at once records what it always did.
            report["attempts"] = attempts
        return {
            "id": identifiers[-1],
            "type": "message",
            "role": "assistant",
            "model": final[0].model,
            "content": content,
            "stop_reason": stop_reason,
            "usage": dict(usage or {}),
            "structured_output": outcome.structured_output,
            "claude_code": report,
        }


def backend_for(source: CredentialSource | None, replay: bool) -> Backend | None:
    """The Backend a Run's Judge takes, from what preflight resolved (decision 21).

    A replay is `replay` whatever the environment holds; `claude_code` credentials are the
    `claude_code` Backend at the CLI version the probe read; every API-side kind is
    `anthropic_api`; nothing resolved is None.
    """
    if replay:
        return Backend(kind="replay")
    if source is None:
        return None
    if source.kind == "claude_code":
        return Backend(kind="claude_code", cli_version=source.cli.version if source.cli else None)
    return Backend(kind="anthropic_api")


def live_client(backend: Backend, source: CredentialSource | None) -> ModelClient:
    """The live client `run.json.judge.backend` names, built from what preflight resolved
    (`source`), so execution and `rescore` never ask the environment or the CLI a second
    time (decisions 21, 22). A replay's client is the `ReplayModelClient` over the Run's
    cursor, never this."""
    if backend.kind == "replay":
        raise ValueError(f"no live client for the Judge backend {backend!r}")
    if backend.kind == "anthropic_api":
        return LiveAnthropicClient()
    if source is None or source.cli is None:
        raise ClaudeCodeError(
            "the Judge backend is claude_code and preflight resolved no Claude Code CLI"
        )
    if source.cli.version != backend.cli_version:
        raise ClaudeCodeError(
            f"the Judge backend records Claude Code {backend.cli_version} and the CLI "
            f"resolved is {source.cli.version}"
        )
    return ClaudeCodeClient(source.cli)


def _probe(path: str, *arguments: str) -> str:
    """What `<cli> <arguments>` prints on stdout; never calls a model."""
    # The SDK strips CLAUDECODE from the child it spawns; the probes do the same, so a
    # Run started inside a Claude Code session asks the CLI as the SDK will run it.
    environment = {key: value for key, value in os.environ.items() if key != "CLAUDECODE"}
    completed = subprocess.run(
        [path, *arguments],
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT_S,
        env=environment,
        check=False,
    )
    return completed.stdout


def _one_prompt(request: ModelRequest) -> str:
    """The request's one user message as text: this backend takes one prompt, and refuses
    what it would otherwise have to drop — tools, and sampling parameters, which are a
    `SamplingNotSupported` naming each (ticket 06, phase-5 decision 53)."""
    if request.tools is not None:
        raise ClaudeCodeError("the Claude Code backend offers the model no tools")
    if request.sampling:
        # The same exception the Messages API client raises on a 400 naming a parameter, so
        # the Simulated User meets one refusal whichever Backend it takes (decision 53).
        raise SamplingNotSupported(
            dict.fromkeys(request.sampling, "not_supported"),
            ClaudeCodeError(
                "the Claude Code backend sends no sampling parameters: "
                + ", ".join(sorted(request.sampling))
            ),
        )
    messages = request.messages
    if len(messages) != 1 or messages[0].get("role") != "user":
        raise ClaudeCodeError(
            f"the Claude Code backend sends one user message; this request has {len(messages)}"
        )
    content = messages[0].get("content")
    if not isinstance(content, str):
        raise ClaudeCodeError(
            "the Claude Code backend sends one user message whose content is text; "
            f"this one's is {type(content).__name__}"
        )
    return content


def _block(block: object) -> dict[str, Any]:
    """An SDK content block back to a dict, spelled as the Messages API spells it; a block
    the SDK names that this backend does not know is refused, never given a made-up type."""
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, ThinkingBlock):
        return {"type": "thinking", "thinking": block.thinking, "signature": block.signature}
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": block.is_error,
        }
    raise ClaudeCodeError(
        "the Claude Code CLI returned a content block this backend does not know: "
        f"{type(block).__name__}"
    )


def _rejections(messages: Sequence[Message]) -> dict[str | None, str]:
    """What the CLI said back to each attempt it rejected, by the attempt's message id: the
    text of every `tool_result` marked `is_error` in the `UserMessage`s that follow that id,
    before the next one (decision 42). An accepted `tool_result` is not a rejection."""
    rejected: dict[str | None, list[str]] = {}
    current: str | None = None
    for message in messages:
        if isinstance(message, AssistantMessage):
            current = message.message_id
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock) and block.is_error:
                    rejected.setdefault(current, []).append(_result_text(block.content))
    return {identifier: "\n".join(texts) for identifier, texts in rejected.items()}


def _result_text(content: str | list[dict[str, Any]] | None) -> str:
    """A `tool_result`'s content as text: a string as it is, text blocks joined."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(str(block.get("text", "")) for block in content if block.get("type") == "text")


def _said(message: AssistantMessage) -> str:
    text = "".join(block.text for block in message.content if isinstance(block, TextBlock))
    return f": {text}" if text else ""


def _errors(outcome: ResultMessage) -> str:
    said = [*(outcome.errors or []), *([outcome.result] if outcome.result else [])]
    return f": {'; '.join(said)}" if said else ""


def with_stderr(detail: str, stderr: Iterable[str]) -> str:
    """`detail`, then the last `STDERR_LINES` non-blank lines the CLI wrote, when it wrote
    any: the one formatter the Judge's client and the Target's transport share."""
    lines = [line.rstrip() for line in stderr if line.strip()][-STDERR_LINES:]
    return f"{detail}\nstderr:\n" + "\n".join(lines) if lines else detail


def _discard(line: str) -> None:
    """A stderr callback that keeps nothing, for options built outside a call."""


__all__ = [
    "Backend",
    "BackendKind",
    "ClaudeCodeCli",
    "ClaudeCodeClient",
    "ClaudeCodeError",
    "QueryFunction",
    "backend_for",
    "cli",
    "cli_path",
    "cli_version",
    "live_client",
    "logged_in",
    "with_stderr",
]
