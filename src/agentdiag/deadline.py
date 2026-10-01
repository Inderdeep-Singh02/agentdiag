"""Waiting on a Target's code for at most so long, on a daemon thread (phase-5 decision 56).

A Target is somebody else's code, and it can block: on a network call, on a lock, on a
prompt nobody will answer. agentdiag's two calls into it that must end — a Turn the driver
loop delivers, and the Sync probe that builds the Target before any Run directory exists
(phase-6 decision 11) — run it on a daemon worker thread and wait for it `seconds` at most.
Past that the thread is abandoned, never joined: nothing can safely stop foreign code, and a
daemon thread cannot keep the process alive once agentdiag is done.

Standard library only: the driver loop and the in-process Adapter both import this, and
neither may import the other.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout


class Overdue(TimeoutError):
    """The work did not finish within its time; its thread was abandoned."""


def within[T](work: Callable[[], T], seconds: float, *, name: str) -> T:
    """`work()`'s result, or its exception re-raised here, or `Overdue` after `seconds`."""
    future: Future[T] = Future()

    def run() -> None:
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(work())
        except BaseException as exc:  # re-raised by `result()` on the waiting side
            future.set_exception(exc)

    threading.Thread(target=run, name=name, daemon=True).start()
    try:
        return future.result(timeout=seconds)
    except FutureTimeout:
        raise Overdue(f"{name} did not finish within {seconds:g} s") from None


__all__ = ["Overdue", "within"]
