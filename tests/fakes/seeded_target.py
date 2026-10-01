"""A fake Target whose factory accepts the Scenario's Fixtures.

Some Targets can seed themselves if they are told what to seed; the Adapter passes the
Fixtures to a factory whose signature asks for them, and leaves alone one that does not
(ADR-0001 point 6: the mechanism is Adapter configuration, not Scenario schema).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

TOOLS: dict[str, Callable[..., Any]] = {}


def make_target(
    client: Any,
    tools: Mapping[str, Callable[..., Any]],
    *,
    fixtures: Sequence[Any] = (),
) -> Callable[[str], str]:
    """Answers with the name of the identity it was seeded with, and calls no model."""
    seeded = [fixture.name for fixture in fixtures if fixture.kind == "identity"]

    def respond(message: str) -> str:
        return seeded[0] if seeded else "nobody"

    return respond


__all__ = ["TOOLS", "make_target"]
