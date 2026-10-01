"""A Target whose factory raises `KeyboardInterrupt`, so cancellation is testable at the seam.

A Ctrl-C arrives as an exception at whatever line was executing, and nothing about that is
special to the terminal. Raising it from a factory puts it exactly where an operator's
interrupt during `open` would land, which lets a test assert what D22 requires: termination
`cancelled`, Scores `incomplete` / `cancelled`, the Scenarios never started named as
`cancelled` in the Scorecard, and a Run directory that is still readable.

The Sync probe builds the Target too, before any Run directory exists (phase-6 decision
11); an interrupt there would abort the Run before it started, which is not what these tests
watch, so the factory lets the probe's client through.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from agentdiag.adapter.inprocess import PROBE_API_KEY


def make_target(
    client: Any,
    tools: Mapping[str, Callable[..., Any]],
    **options: Any,
) -> Callable[[str], str]:
    """Interrupt the Trial the way an operator would."""
    if getattr(client, "api_key", None) == PROBE_API_KEY:
        return lambda message: ""
    raise KeyboardInterrupt


def make_tools() -> dict[str, Callable[..., Any]]:
    """No tools: this Target never gets far enough to use one."""
    return {}


__all__ = ["make_target", "make_tools"]
