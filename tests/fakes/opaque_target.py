"""A fake Target whose tool hides its arguments, for the conformance suite (Seam 3).

The conformance suite runs against every Adapter with a fake Target behind it, so what is
under test is the Adapter, not the toy Target's prompt. This one exists for a behaviour the
toy Target cannot exercise: a tool declared as `*args` cannot be bound to names, so the
Adapter has to record "we could not look" and not "there were no arguments" (ADR-0001).

Like a real Target, it imports no agentdiag.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "opaque",
        "description": "A tool whose arguments an Adapter cannot bind to names.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
]


def opaque(*args: Any) -> dict[str, Any]:
    """Takes positionals only, so `inspect.signature(...).bind` cannot name what it got."""
    return {"seen": len(args)}


TOOLS: dict[str, Callable[..., Any]] = {"opaque": opaque}


def make_target(
    client: Any,
    tools: Mapping[str, Callable[..., Any]],
    *,
    model: str = "claude-sonnet-5",
    max_tokens: int = 256,
    fixtures: Sequence[Any] = (),
) -> Callable[[str], str]:
    """The same manual loop shape a real Target has, with one tool and one rule.

    It accepts `fixtures` because the conformance suite applies one; what it does with
    them does not matter here, only that a Target that is handed Fixtures can take them.
    """
    messages: list[dict[str, Any]] = []

    def respond(message: str) -> str:
        messages.append({"role": "user", "content": message})
        while True:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system="You are a fake Target. Call the opaque tool, then answer.",
                tools=TOOL_SCHEMAS,
                messages=messages,
            )
            content = [block.model_dump(exclude_none=True) for block in response.content]
            messages.append({"role": "assistant", "content": content})
            if response.stop_reason != "tool_use":
                return "".join(block["text"] for block in content if block.get("type") == "text")
            results = [
                {
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(tools[block["name"]](*block["input"].values())),
                        }
                    ],
                }
                for block in content
                if block.get("type") == "tool_use"
            ]
            messages.append({"role": "user", "content": results})

    return respond


__all__ = ["TOOLS", "TOOL_SCHEMAS", "make_target", "opaque"]
