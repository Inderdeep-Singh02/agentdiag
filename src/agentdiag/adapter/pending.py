"""The `pending` Adapter kind: a Target described before anything drives it (ADR-0016 §4).

`init --target <slug>` scaffolds who a Target is, and the Manifest model requires an
Adapter block. An in-process block with no factory is an error, not a state; `pending` is
a state: a core kind registered through the entry points like `inprocess` and `http`, so
`validate` knows it (ADR-0015 §3) and warns rather than errors, while every command that
would converse with the Target refuses by name. The preflight refuses it before building
any Adapter (`manifest_checks.pending_problem`), and a direct construction refuses too, so
no path reaches a session: the constructor raises `AdapterPending` with the one message
`validate`, `run` and `sync` print.

The conformance suite is not run over it (0.1.2 interfaces, Tests): it opens no session.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentdiag.run.manifest_checks import PENDING_KIND, PENDING_MESSAGE


class AdapterPending(ValueError):
    """The Manifest's Adapter is `pending`: nothing drives this Target yet."""


class PendingAdapter:
    """The identity scaffold's placeholder: it validates, says what it is, and refuses to
    be built, so nothing is ever driven through it."""

    kind = PENDING_KIND

    def __init__(self, config: Mapping[str, Any], *, environment: str, **_: Any) -> None:
        del config, environment
        raise AdapterPending(PENDING_MESSAGE)

    @staticmethod
    def validate_section(section: Any) -> list[tuple[str, str]]:
        """No rule of its own: the warning is `manifest_checks.pending_problem`'s."""
        del section
        return []


__all__ = ["PENDING_KIND", "PENDING_MESSAGE", "AdapterPending", "PendingAdapter"]
