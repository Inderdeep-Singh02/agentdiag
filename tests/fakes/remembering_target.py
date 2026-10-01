"""A fake Target that remembers its conversation and says which Fixtures it was given.

Two run-time behaviours need a Target whose reply proves something about its session: a
continuing Scenario must reach the *same* conversation (decision 13), so the reply counts
the messages heard so far; and the Suite's Fixtures must reach the factory (ADR-0001 §6),
so the reply names them. It calls no model, so a Run against it needs no recording.

Like a real Target, it imports no agentdiag.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

TOOLS: dict[str, Callable[..., Any]] = {}


def make_target(
    client: Any,
    tools: Mapping[str, Callable[..., Any]],
    *,
    model: str = "claude-sonnet-5",
    fixtures: Sequence[Any] = (),
) -> Callable[[str], str]:
    heard: list[str] = []
    given = ", ".join(f"{fixture.name}={fixture.data}" for fixture in fixtures) or "nothing"

    def respond(message: str) -> str:
        heard.append(message)
        return f"heard {len(heard)}: {' | '.join(heard)}; given {given}"

    return respond


__all__ = ["TOOLS", "make_target"]
