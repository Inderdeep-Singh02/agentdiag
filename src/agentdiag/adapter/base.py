"""What every Adapter is, and what it promises (D5, ADR-0001).

The Adapter is the only component that touches a Target (ADR-0001 point 1). Six
operations and no more: describe yourself, say whether a Scenario's Fixtures can be
applied (phase-5 decision 12), open a session with them, deliver one user Turn, rebind the
session to a continuing Scenario's Trace (decision 13), close. It emits Events into the
TraceWriter it is handed and never writes a file of its own — the runner owns the Run
directory.

`describe()` is not decoration. A Score has to be able to say what grounded it, so the
Fidelity an Adapter can achieve and the side effects it causes are declared up front and
recorded in `run.json` (ADR-0001 points 3 and 5).

What both Adapters share lives here, so they raise one class for one fault (phase-8
decision 17): `SessionClosed` for a session used or closed after its close, and
`LIVE_REFUSAL`, the words a `live` environment is refused in without `--live` (decision 9).
Offline: the Backend a description names is `agentdiag.backend`'s, so describing an Adapter
imports no model client.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

from agentdiag.backend import Backend
from agentdiag.scenario.models import Fixture
from agentdiag.trace import TraceWriter
from agentdiag.types import Fidelity, SideEffectClass


class LiveSideEffectsRefused(RuntimeError):
    """A Run was asked to open an Adapter that causes live side effects, without the flag.

    ADR-0001 point 5: agentdiag never touches a live system by accident. The refusal is
    the Adapter's, at `check` and at `open`, before anything reaches the Target.
    """


class SessionClosed(RuntimeError):
    """A closed session was used again, or closed a second time (phase-8 decision 17)."""


LIVE_REFUSAL = (
    "The {environment!r} environment causes live side effects. A Run against it needs "
    "allow_live; pass --live to acknowledge it"
)
"""What every Adapter says when it refuses a `live` environment without the flag (decision
9), formatted with the environment's name; `agentdiag run --live` is the remedy it names."""


class AdapterDescription(BaseModel):
    """An Adapter's own account of itself, recorded verbatim in `run.json`.

    `config` is the environment's block from the Manifest as written. Secrets never live
    in a Manifest, so nothing here needs redacting.
    """

    kind: str
    fidelity: Fidelity
    side_effects: SideEffectClass
    environment: str
    observes: list[str] = Field(default_factory=list)
    """What of the deployed set this Adapter's probe can observe for a Fingerprint
    (phase-6 decision 11): `prompts`, `tools`, `model`, `provider` for the in-process
    Adapter. What it could, not what it did; the Fingerprint says what it did."""

    config: dict[str, Any] = Field(default_factory=dict)

    backend: Backend | None = None
    """The Backend this Adapter's Target calls take to the model in this Run (ticket 20,
    decision 28): `replay`, `claude_code` or `anthropic_api`. None only for an Adapter that
    does not own the Target's model client."""

    turn_timeout_s: float | None = None
    """How long one Turn may take in this Run, in seconds: the environment's `turn_timeout`,
    or the default when the Manifest writes none (ticket 06, phase-5 decision 56). The
    driver loop reads it here, never from a root-wide constant. None on a Run recorded
    before ticket 06."""

    live_acknowledged: bool = False
    """The Run was given `--live` (`allow_live`, phase-8 decision 9): recorded whenever it
    was passed, whether or not the environment is `live`; `side_effects` says whether it
    mattered. Written only when true, so every `run.json` recorded before ticket 17, and
    every Run not given the flag, reads the same and `compare` sees no variation between
    them."""

    @model_serializer(mode="wrap")
    def _without_unacknowledged(self, handler: SerializerFunctionWrapHandler) -> Any:
        dumped = handler(self)
        if isinstance(dumped, dict) and dumped.get("live_acknowledged") is False:
            dumped.pop("live_acknowledged")
        return dumped


@runtime_checkable
class Session(Protocol):
    """One conversation with one Target, for the length of one Trial."""

    def deliver(self, message: str) -> str:
        """Deliver one user Turn and return the Target's complete final text."""
        ...

    def rebind(self, trace: TraceWriter) -> None:
        """Emit into `trace` from now on: a continuing Scenario's Trial (decision 13).

        The conversation state stays with the Target; only where its Events are written
        changes, so each Trial still has a Trace of its own. Rebinding a closed session is
        an error, as delivering to one is.
        """
        ...

    def close(self) -> None:
        """End the session. Closing twice is an error: a Trial closes exactly once."""
        ...


@runtime_checkable
class Adapter(Protocol):
    """The only thing that touches a Target."""

    def describe(self) -> AdapterDescription:
        """Kind, the Fidelity it achieves, its side-effect class, its environment."""
        ...

    def check_fixtures(self, fixtures: Sequence[Fixture]) -> str | None:
        """Why these Fixtures cannot be applied, or None when `open` will apply them.

        Asked at preflight, without opening a session (decision 12): a reason makes the
        Scenario `not_run` / `fixture_unavailable` with the reason as its detail, and the
        rest of the Run proceeds. No Fixtures are always applicable. An answer here must
        agree with `open`, which refuses the same Fixtures for the same reason.
        """
        ...

    def open(self, trace: TraceWriter, *, fixtures: Sequence[Fixture] = ()) -> Session:
        """Start a session, applying the Scenario's Fixtures and emitting into `trace`."""
        ...


__all__ = [
    "LIVE_REFUSAL",
    "Adapter",
    "AdapterDescription",
    "Fixture",
    "LiveSideEffectsRefused",
    "Session",
    "SessionClosed",
]
