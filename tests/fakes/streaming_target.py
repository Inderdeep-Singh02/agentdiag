"""A fake Target that streams its model call, the way many real Targets do.

The Sync probe answers every request with a whole, non-streamed message (decision 11), so
a streaming Target's loop cannot read it; what the probe needs — the request — was sent
before that, and the probe observes it. Like a real Target, it imports no agentdiag.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

SYSTEM = "You stream. Rule 1: be brief."

SCHEMA = {
    "name": "lookup_order",
    "description": "Look up one order.",
    "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}},
}


def make_target(
    client: Any, tools: Mapping[str, Callable[..., Any]], *, model: str = "claude-sonnet-5"
) -> Callable[[str], str]:
    def respond(message: str) -> str:
        stream = client.messages.create(
            model=model,
            max_tokens=256,
            system=SYSTEM,
            tools=[SCHEMA],
            messages=[{"role": "user", "content": message}],
            stream=True,
        )
        return "".join(getattr(getattr(event, "delta", None), "text", "") or "" for event in stream)

    return respond


__all__ = ["SCHEMA", "SYSTEM", "make_target"]
