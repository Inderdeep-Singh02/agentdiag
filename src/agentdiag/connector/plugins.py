"""Adapter and Connector kinds, and Dialects, found by name through Python entry points
(ADR-0014 §2).

A Manifest names `adapter.kind` and `connector.kind`, and an HTTP environment its `dialect`;
this module turns each into the class that implements it. The mechanism is
`importlib.metadata.entry_points`: an installed distribution registers a kind under
`agentdiag.adapters` or `agentdiag.connectors`, or a Dialect under `agentdiag.dialects`
(phase-8 decision 1), and core imports no plugin (ADR-0014 §1). Core registers its own kinds
(`inprocess` in both groups, `http` and `pending` among the Adapters, ADR-0016 §4) and its
two Dialects (`json`, `sse-json`) the same way, in its `pyproject.toml`, so the one lookup
path is the path every kind takes, and a plugin is never a special case.

- **An unknown kind names the kinds that are installed**, and says a plugin distribution
  registers one.
- **A kind two distributions register is an error naming both** (`AmbiguousKind`): which
  one a Manifest meant is not a guess to make.
- **The lookup is one function, cached per process**: the installed distributions do not
  change under a running command.

`register`, `unregister` and `registered` are the test seam (phase-6 decision 23): an
in-process override consulted before the entry points, so a test can serve a fake
Connector, Adapter or Dialect under a name without being an installed distribution. Nothing
in `src/` uses it.

Offline: reading entry-point metadata imports nothing; `adapter_class("inprocess")` loads
the in-process Adapter, and with it the SDK, only when it is asked for. This module imports
nothing of `agentdiag.adapter`, which is why `DIALECT_GROUP` is defined here and
`agentdiag.adapter.http.dialect` re-exports it.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from functools import cache
from importlib.metadata import EntryPoint, entry_points
from typing import Any, cast

from agentdiag.connector.base import Connector
from agentdiag.connector.environment import resolve_environments
from agentdiag.run.manifest import Manifest

ADAPTER_GROUP = "agentdiag.adapters"
CONNECTOR_GROUP = "agentdiag.connectors"
DIALECT_GROUP = "agentdiag.dialects"

COMPONENT_OF_GROUP: Mapping[str, str] = {
    ADAPTER_GROUP: "Adapter",
    CONNECTOR_GROUP: "Connector",
    DIALECT_GROUP: "Dialect",
}
"""Entry-point group to the component its kinds are, as a message names it."""

CORE_KINDS_OF: Mapping[str, frozenset[str]] = {
    ADAPTER_GROUP: frozenset({"inprocess", "http", "pending"}),
    CONNECTOR_GROUP: frozenset({"inprocess"}),
    DIALECT_GROUP: frozenset({"json", "sse-json"}),
}
"""What core itself registers in its `pyproject.toml`, per group: missing, the checkout's
entry points were never installed."""

CORE_KINDS: frozenset[str] = CORE_KINDS_OF[ADAPTER_GROUP] | CORE_KINDS_OF[CONNECTOR_GROUP]
"""Every Adapter and Connector kind core registers, in either group."""

CORE_DIALECTS: frozenset[str] = CORE_KINDS_OF[DIALECT_GROUP]
"""The Dialects core registers (phase-8 decision 5)."""


class UnknownKind(ValueError):
    """No installed distribution registers this kind."""


class AmbiguousKind(UnknownKind):
    """Two installed distributions register one kind; which a Manifest meant is not a guess
    to make."""


_overrides: dict[str, dict[str, Callable[..., Any]]] = {
    ADAPTER_GROUP: {},
    CONNECTOR_GROUP: {},
    DIALECT_GROUP: {},
}


@cache
def installed(group: str) -> dict[str, EntryPoint]:
    """Every kind registered under `group`, by kind; `AmbiguousKind` naming both
    distributions when two register one kind."""
    found: dict[str, EntryPoint] = {}
    for point in entry_points(group=group):
        if point.name in found:
            first, second = _distribution(found[point.name]), _distribution(point)
            raise AmbiguousKind(
                f"the {COMPONENT_OF_GROUP[group]} kind {point.name!r} is registered by both "
                f"{first} and {second}; uninstall one"
            )
        found[point.name] = point
    return found


def _distribution(point: EntryPoint) -> str:
    return point.dist.name if point.dist is not None else point.value


def _lookup(group: str, kind: str) -> Callable[..., Any]:
    override = _overrides[group].get(kind)
    if override is not None:
        return override
    check_registered(group, kind)
    return cast(Callable[..., Any], installed(group)[kind].load())


def check_registered(group: str, kind: str) -> None:
    """`UnknownKind` when nothing registers `kind` under `group`, loading nothing: what
    `validate` asks, so it can name an unknown kind without importing any plugin (and with
    it the SDK) — walkthrough friction 5."""
    if kind in _overrides[group]:
        return
    points = installed(group)
    if kind in points:
        return
    what = COMPONENT_OF_GROUP[group]
    if kind in CORE_KINDS_OF[group]:
        raise UnknownKind(
            f"no {what} of kind {kind!r} is installed, though agentdiag registers it itself: "
            "a fresh checkout needs `uv sync` so the entry points are installed"
        )
    kinds = ", ".join(sorted(points)) or "none"
    raise UnknownKind(
        f"no {what} of kind {kind!r} is installed (installed: {kinds}); install the plugin "
        f"distribution that registers one under {group}"
    )


def imported_class(group: str, kind: str) -> Any | None:
    """The class registered for `kind` when getting it runs no code: the test seam's, or an
    entry point whose module is already imported; None otherwise. What `validate` asks for a
    plugin's own `validate_section` hook, so it never imports a plugin (and its SDK) to
    check a Manifest."""
    override = _overrides[group].get(kind)
    if override is not None:
        return override
    point = installed(group).get(kind)
    if point is None or point.module not in sys.modules:
        return None
    return point.load()


def adapter_class(kind: str) -> type:
    """The Adapter class registered for `kind`."""
    return cast(type, _lookup(ADAPTER_GROUP, kind))


def dialect_class(name: str) -> type:
    """The Dialect class registered under `name` (phase-8 decision 1), found as `adapter_class`
    finds a kind, with the same override seam (`registered(name, cls, group=DIALECT_GROUP)`);
    constructed with no arguments."""
    return cast(type, _lookup(DIALECT_GROUP, name))


def connector_class(kind: str) -> Callable[..., Connector]:
    """The Connector class registered for `kind` (or the test seam's constructor): called
    as `Kind(section, *, environments)`."""
    return cast(Callable[..., Connector], _lookup(CONNECTOR_GROUP, kind))


def build_connector(
    manifest: Manifest, environ: Mapping[str, str] = os.environ
) -> Connector | None:
    """The Manifest's Connector, constructed; None when it names none.

    Its environments are handed over resolved lazily (`resolve_environments`): each reads
    its own credentials when the Connector reads it, so a missing credential is
    `CredentialMissing` from that read and never blocks another environment's.
    `UnknownKind` when no distribution registers the kind."""
    section = manifest.connector
    if section is None:
        return None
    kind = connector_class(section.kind)
    return kind(section, environments=resolve_environments(manifest, environ))


def register(
    kind: str, implementation: Callable[..., Any], *, group: str = CONNECTOR_GROUP
) -> None:
    """Serve `kind` from `implementation` in this process, before any entry point (tests)."""
    _overrides[group][kind] = implementation


def unregister(kind: str, *, group: str = CONNECTOR_GROUP) -> None:
    """Undo `register`; a kind never registered is no error."""
    _overrides[group].pop(kind, None)


@contextmanager
def registered(
    kind: str, implementation: Callable[..., Any], *, group: str = CONNECTOR_GROUP
) -> Iterator[None]:
    """`register` for the length of a `with` block."""
    register(kind, implementation, group=group)
    try:
        yield
    finally:
        unregister(kind, group=group)


__all__ = [
    "ADAPTER_GROUP",
    "COMPONENT_OF_GROUP",
    "CONNECTOR_GROUP",
    "CORE_DIALECTS",
    "CORE_KINDS",
    "CORE_KINDS_OF",
    "DIALECT_GROUP",
    "AmbiguousKind",
    "UnknownKind",
    "adapter_class",
    "build_connector",
    "check_registered",
    "connector_class",
    "dialect_class",
    "imported_class",
    "installed",
    "register",
    "registered",
    "unregister",
]
