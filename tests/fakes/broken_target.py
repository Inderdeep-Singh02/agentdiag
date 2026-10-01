"""A Target whose factory raises, so a Run can be watched failing the way Targets fail.

A Target that cannot be built is the Target's problem, not agentdiag's, and the Trial must
say so: `termination: target_error` on the Trace and `incomplete` / `target_error` on every
declared Eval (D22, ADR-0003 section 3). Nothing here is unusual — a missing credential, a
bad config, an import that fails at construction all look like this from outside.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

BREAKAGE = "the order desk is closed"
"""What the factory raises, so a test can assert the message reached the Trace."""


def make_target(
    client: Any,
    tools: Mapping[str, Callable[..., Any]],
    **options: Any,
) -> Callable[[str], str]:
    """Refuse to build. The Adapter imports this fine and it fails at `open`."""
    raise RuntimeError(BREAKAGE)


def make_tools() -> dict[str, Callable[..., Any]]:
    """No tools: this Target never gets far enough to use one."""
    return {}


__all__ = ["BREAKAGE", "make_target", "make_tools"]
