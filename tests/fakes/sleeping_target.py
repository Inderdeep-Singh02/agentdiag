"""A fake Target that answers after its Turn's timeout, then tries to write (decision 56).

It sleeps `sleep_s` before answering, longer than the `turn_timeout` the test's Manifest
sets, so the driver loop abandons its Turn; when it wakes it calls its one tool, which the
Adapter records into the Trace — or tries to, since the Trace has ended and is sealed.
`WOKE` is set once it has tried, so a test can wait for the late write to have happened.

Like a real Target, it imports no agentdiag.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

WOKE = threading.Event()
"""Set when the Target has woken past its timeout and tried its late tool call."""

LATE: list[str] = []
"""What the late tool call ended in: the exception's name, when the Trace refused it."""


def lookup_order(order_id: str) -> dict[str, Any]:
    return {"order_id": order_id, "status": "processing"}


TOOLS: dict[str, Callable[..., Any]] = {"lookup_order": lookup_order}


def make_target(
    client: Any,
    tools: Mapping[str, Callable[..., Any]],
    *,
    model: str = "claude-sonnet-5",
    sleep_s: float = 1.0,
) -> Callable[[str], str]:
    def respond(message: str) -> str:
        time.sleep(sleep_s)
        try:
            tools["lookup_order"]("NB-1042")
        except Exception as exc:
            LATE.append(type(exc).__name__)
        finally:
            WOKE.set()
        return "Sorry for the wait."

    return respond


__all__ = ["LATE", "TOOLS", "WOKE", "make_target"]
