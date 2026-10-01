"""A fake Target whose factory takes no `fixtures` keyword, and which calls no model.

A Target that cannot be told what to set up is a normal Target, and a Scenario that
declares Fixtures for it cannot run against it: the Adapter says so at preflight, before
any session opens, and the Run skips that Scenario as `fixture_unavailable` (phase-5
decision 12). `BUILT` counts the factory's calls, so a test can see that asking the
Adapter built nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

TOOLS: dict[str, Callable[..., Any]] = {}

BUILT: list[str] = []
"""One entry per factory call."""


def make_target(
    client: Any,
    tools: Mapping[str, Callable[..., Any]],
    *,
    model: str = "claude-sonnet-5",
) -> Callable[[str], str]:
    """Answers every Turn the same way, knowing nobody."""
    BUILT.append(model)

    def respond(message: str) -> str:
        return "I have no idea who you are."

    return respond


__all__ = ["BUILT", "TOOLS", "make_target"]
