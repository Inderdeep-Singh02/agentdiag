"""A fake Target that asks for a tool and never executes it.

Real Targets do this: a loop that gives up at a turn limit, or a caller that reads only
the text blocks. The Adapter must still close the `llm_call` Span rather than leave the
Trace with an unmatched `span/start`, so that behaviour needs a Target that exhibits it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "opaque",
        "description": "A tool this Target requests and never runs.",
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
) -> Callable[[str], str]:
    """One model call per Turn, whatever the response asked for."""

    def respond(message: str) -> str:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system="You are a fake Target that never runs its tools.",
            tools=TOOL_SCHEMAS,
            messages=[{"role": "user", "content": message}],
        )
        return "".join(block.text for block in response.content if block.type == "text")

    return respond


__all__ = ["TOOLS", "TOOL_SCHEMAS", "make_target", "opaque"]
