"""A model that answers every request with the next scripted response body, and keeps each
request: a replay `Cursor` (`agentdiag.model.replay`) that matches nothing.

For tests that drive a Target through the real in-process Adapter and care what the Target
*sent* (the help desk reads its platform at every request, ticket 13) rather than whether a
recording's keys still match. The script cycles, so one cursor serves every session an
Adapter opens.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from agentdiag.model.replay import Exchange


def text_reply(identifier: str, text: str) -> dict[str, Any]:
    """A final answer: one text block, `end_turn`."""
    return _message(identifier, [{"type": "text", "text": text}], "end_turn")


def tool_reply(identifier: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """A reply asking for one tool."""
    block = {"type": "tool_use", "id": f"toolu_{identifier}", "name": name, "input": arguments}
    return _message(identifier, [block], "tool_use")


def _message(identifier: str, content: list[dict[str, Any]], stop: str) -> dict[str, Any]:
    return {
        "id": f"msg_{identifier}",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-20260815",
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }


class ScriptedCursor:
    """Answers in script order, cycling; `requests` holds every request body it was sent."""

    def __init__(self, script: Sequence[dict[str, Any]]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    def begin(self, scenario: str) -> None:
        """One script serves every Trial."""

    def take(self, request: Any) -> Exchange:
        response = self.script[len(self.requests) % len(self.script)]
        self.requests.append(request)
        return Exchange(request=request, response=response)

    def assert_consumed(self, which: Callable[[dict[str, Any]], bool] | None = None) -> None:
        """A cycling script is never left unconsumed."""


__all__ = ["ScriptedCursor", "text_reply", "tool_reply"]
