"""The Adapter: the only component that converses with a Target (ADR-0001, ADR-0011 §1).

`base` holds what every Adapter is; `inprocess` is the Phase 4 Adapter for Python Targets;
`http` is the Phase 8 Adapter for a Target's shipped chat endpoint (ticket 17);
`conformance` is the suite every Adapter must pass (D9).

The in-process names are loaded when first asked for, not at import: that module brings the
Anthropic SDK, and the HTTP Adapter — which `validate` may import — must stay offline
(phase-8 decision 1). `from agentdiag.adapter import InProcessAdapter` works as before.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from agentdiag.adapter.base import (
    Adapter,
    AdapterDescription,
    Fixture,
    LiveSideEffectsRefused,
    Session,
    SessionClosed,
)

if TYPE_CHECKING:
    from agentdiag.adapter.inprocess import (
        CapturingTransport,
        InProcessAdapter,
        InProcessSession,
        Observation,
        ObservationFailed,
        ObservingTransport,
        TraceEmitter,
        import_attribute,
    )

_IN_PROCESS = frozenset(
    {
        "CapturingTransport",
        "InProcessAdapter",
        "InProcessSession",
        "Observation",
        "ObservationFailed",
        "ObservingTransport",
        "TraceEmitter",
        "import_attribute",
    }
)
"""The names `agentdiag.adapter.inprocess` provides, imported on first use."""


def __getattr__(name: str) -> Any:
    if name in _IN_PROCESS:
        return getattr(importlib.import_module("agentdiag.adapter.inprocess"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Adapter",
    "AdapterDescription",
    "CapturingTransport",
    "Fixture",
    "InProcessAdapter",
    "InProcessSession",
    "LiveSideEffectsRefused",
    "Observation",
    "ObservationFailed",
    "ObservingTransport",
    "Session",
    "SessionClosed",
    "TraceEmitter",
    "import_attribute",
]
