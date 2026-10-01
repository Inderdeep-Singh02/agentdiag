"""The toy Target itself: a manual tool loop over an Anthropic SDK client (D8).

`make_target` is the factory the Manifest names. It takes the client and the tools from
its caller rather than building them, which is the whole reason the in-process Adapter can
instrument this Target without the Target knowing (D6, ADR-0001): the Adapter hands in a
client whose transport it owns and tool callables it wraps.

This module imports `anthropic` and nothing of agentdiag. A test enforces that.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from agentdiag.examples.toy.prompt import SYSTEM_PROMPT
from agentdiag.examples.toy.tools import TOOL_SCHEMAS

DEFAULT_MODEL = "claude-sonnet-5"
"""Not the Judge's model: a shipped example that resolved to the same model as the Judge
would trip D23's self-preference warning on its first run (phase-4 interfaces, decision 4)."""

DEFAULT_MAX_TOKENS = 1024


def make_target(
    client: Any,
    tools: Mapping[str, Callable[..., Any]],
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Callable[[str], str]:
    """Build the order desk and return the callable that answers one user message.

    The returned callable holds the conversation: calling it twice continues the same
    thread, which is what a multi-Turn Scenario needs. No sampling parameters and no
    thinking configuration are sent — the Claude 5 models reject the former and default to
    adaptive thinking for the latter.
    """
    messages: list[dict[str, Any]] = []

    def respond(message: str) -> str:
        messages.append({"role": "user", "content": message})

        while True:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )
            content = [block.model_dump(exclude_none=True) for block in response.content]
            messages.append({"role": "assistant", "content": content})

            if response.stop_reason != "tool_use":
                return "".join(block["text"] for block in content if block.get("type") == "text")

            results: list[dict[str, Any]] = []
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                results.append(_execute(tools, block))
            # Every tool result the response asked for goes back in ONE user message: the
            # API rejects a thread where a `tool_use` block has no matching result beside
            # its siblings.
            messages.append({"role": "user", "content": results})

    return respond


def deployed_set() -> dict[str, Any]:
    """The order desk's deployed set, in the in-process Connector's convention (phase-6
    decision 22): what `respond` sends, read from this module's names at the moment of the
    call, so an edit to the prompt the Target runs is an edit on the deployed side."""
    return {
        "prompts": {"system": SYSTEM_PROMPT},
        "tools": {schema["name"]: schema for schema in TOOL_SCHEMAS},
        "model": DEFAULT_MODEL,
        "provider": "anthropic",
    }


def _execute(tools: Mapping[str, Callable[..., Any]], block: Mapping[str, Any]) -> dict[str, Any]:
    """Run one `tool_use` block and shape its outcome as a `tool_result` block."""
    name = str(block["name"])
    arguments = dict(block.get("input") or {})
    try:
        result = tools[name](**arguments)
    except Exception as exc:  # the model gets to see and recover from a tool failure
        return {
            "type": "tool_result",
            "tool_use_id": block["id"],
            "content": [{"type": "text", "text": str(exc)}],
            "is_error": True,
        }
    return {
        "type": "tool_result",
        "tool_use_id": block["id"],
        "content": [{"type": "text", "text": _as_text(result)}],
    }


def _as_text(result: Any) -> str:
    """The text a tool's return value becomes inside a `tool_result` block."""
    try:
        return json.dumps(result)
    except (TypeError, ValueError):
        return repr(result)


__all__ = ["DEFAULT_MAX_TOKENS", "DEFAULT_MODEL", "make_target"]
