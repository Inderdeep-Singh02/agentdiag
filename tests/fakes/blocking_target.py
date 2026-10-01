"""A fake Target whose factory blocks before any model call, as one waiting on a lock or a
network call that never answers would (phase-6 decision 11, the probe's timeout).

Like a real Target, it imports no agentdiag.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any

RELEASE = threading.Event()
"""Never set by the Target: a test sets it once it is done, so the abandoned thread ends."""


def make_target(client: Any, tools: Mapping[str, Callable[..., Any]], **options: Any) -> Any:
    RELEASE.wait(timeout=30)
    return lambda message: ""


__all__ = ["RELEASE", "make_target"]
