"""A fake Target that calls a tool the model never asked for.

Real Targets do this: a retrieval step before the first model call, or a cleanup call
after the last one. The Adapter cannot attribute such a call to any `tool_use` block, so
it must say the call id was not observed rather than invent one (ADR-0001 point 3).

`before` calls the tool with no `llm_call` open at all; `after` calls it while the model's
own request is still open and pending.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "opaque",
        "description": "A tool this Target calls on its own initiative.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
]


def opaque(query: str) -> dict[str, Any]:
    return {"query": query}


TOOLS: dict[str, Callable[..., Any]] = {"opaque": opaque}


def make_target(
    client: Any,
    tools: Mapping[str, Callable[..., Any]],
    *,
    model: str = "claude-sonnet-5",
    max_tokens: int = 256,
    when: str = "before",
) -> Callable[[str], str]:
    """One model call per Turn, with one unrequested tool call around it."""

    def respond(message: str) -> str:
        if when == "before":
            tools["opaque"](query="unrequested")
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system="You are a fake Target that calls tools nobody asked for.",
            tools=TOOL_SCHEMAS,
            messages=[{"role": "user", "content": message}],
        )
        if when == "after":
            # The response asked for a tool, so its `llm_call` is still open and pending;
            # this call is not the one it asked for.
            tools["opaque"](query="unrequested")
        return "".join(block.text for block in response.content if block.type == "text")

    return respond


__all__ = ["TOOLS", "TOOL_SCHEMAS", "make_target", "opaque"]
