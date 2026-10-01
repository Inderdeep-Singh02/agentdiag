"""What every Connector is, and what it promises (ADR-0011 §1, §2, ADR-0014 §2).

The Adapter converses with a Target; the Connector manages its live side. It reads the
deployed set — prompt sections, tool schemas, Flow definitions and their state, the resolved
model and tier — and the Evidence stores, and writes the deployed set only through a push.
Four operations and no more: describe yourself, read the deployed set of one environment,
read one Evidence store, write the deployed set. No session: a Connector is safe to
construct without touching anything, and every operation is one call.

`describe()` is not decoration, as the Adapter's is not. Every operation declares whether it
reads or writes, its side-effect class, and the Fingerprint section kinds it can observe
(ADR-0011 §2), so `sync` knows what a Connector could cover before it asks, and ticket 27's
push can weigh a write's class against the environment's.

**A write is refused or receipted, never silent.** `write_deployed_set` is called only by
`agentdiag push` (`sync.push`, ADR-0011 §6), carrying the deployed Fingerprint the push's
preview saw (`sync.observe.deployed_fingerprint`); a Connector that finds its deployed set
moved since raises `ExpectedFingerprintMoved` (the compare-and-swap, §6a) and writes
nothing, and one that will not make a write raises `WriteRefused`. A push sends only the
sections its preview listed: a Connector whose platform needs a full replace fills the rest
from its own read.

A Connector is constructed as `Kind(section, *, environments)` — the Manifest's `connector`
block and every environment it names, resolved (`connector.environment`) — by
`connector.plugins.build_connector`. The Evidence rows are generic dicts in the shapes of
decision 33, which the Importers (ticket 26) read; a plugin returns its platform's rows in
those shapes, and core never learns a platform's own.

Offline: this module declares shapes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from agentdiag.types import Access, EvidenceKind, SectionKind, SideEffectClass

READ_DEPLOYED_SET = "read_deployed_set"
WRITE_DEPLOYED_SET = "write_deployed_set"
READ_EVIDENCE = "read_evidence"
"""The operation names `describe` uses; an Evidence read is `read_evidence:<kind>`, one per
store, so each can carry its own class."""


def evidence_operation(kind: EvidenceKind) -> str:
    """The name of the operation that reads Evidence store `kind`."""
    return f"{READ_EVIDENCE}:{kind}"


class Operation(BaseModel):
    """One thing a Connector can do to a Target's live side (ADR-0011 §2)."""

    name: str
    """`read_deployed_set`, `read_evidence:<kind>` or `write_deployed_set`."""

    access: Access
    side_effects: SideEffectClass
    observes: list[SectionKind] = Field(default_factory=list)
    """The Fingerprint section kinds this operation can observe; empty for an Evidence read
    and for a write."""


class ConnectorDescription(BaseModel):
    """A Connector's own account of itself for one Target."""

    kind: str
    operations: list[Operation]
    environments: list[str]
    """The environments the Manifest's `connector` block names."""

    evidence: list[EvidenceKind] = Field(default_factory=list)
    """The Evidence stores this Connector reads for this Target."""


class DeployedSet(BaseModel):
    """The deployed set of one environment, as the Connector read it at `read_at`."""

    read_at: str
    """ISO-8601 in UTC, `run.json`'s spelling."""

    environment: str
    prompts: dict[str, str] = Field(default_factory=dict)
    """Prompt name to its text; `sync` splits each on its headings (decision 10)."""

    tools: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """Tool name to its schema entry, the shape a request's `tools` list carries."""

    flows: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """Flow id to its definition, `state` included: a Flow switched off has moved."""

    model: str | None = None
    tier: str | None = None
    provider: str | None = None
    """Read-only sections in v1 (ADR-0011 §1): set on the platform, never pushed."""


class EvidenceQuery(BaseModel):
    """What to read from one Evidence store."""

    conversation_id: str | None = None
    since: str | None = None
    until: str | None = None
    limit: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    """What a plugin's store takes beyond these, carried through to it untouched."""


class EvidenceRows(BaseModel):
    """What one Evidence read returned, in the generic row shapes (decision 33)."""

    kind: EvidenceKind
    query: EvidenceQuery
    rows: list[dict[str, Any]] = Field(default_factory=list)
    read_at: str
    truncated: bool = False
    """True when the store held more rows than were returned (a `limit`, a page size)."""


class WriteReceipt(BaseModel):
    """What a push wrote, and the deployed Fingerprints on either side of it (ticket 27)."""

    environment: str
    written_at: str
    sections: list[str]
    fingerprint_before: str
    fingerprint_after: str


class ConnectorError(RuntimeError):
    """A Connector could not do what it was asked: an environment it does not name, an
    Evidence store the Target does not declare, a read the platform refused. `sync` exits 3
    on one naming it; a Run falls back to the Adapter's probe and warns (decision 25)."""


class CredentialMissing(ConnectorError):
    """An environment needs a credential its variable does not hold; names the variable
    (ADR-0011 §2: resolution fails closed, never borrowing another environment's)."""


class WriteRefused(ConnectorError):
    """A write the Connector will not make: a read-only section (the model, the provider, the
    tier), a deployed set it cannot edit, a value of the wrong shape."""


class ExpectedFingerprintMoved(ConnectorError):
    """The deployed set moved between a push's preview and its write (ADR-0011 §6a): the
    compare-and-swap failed, and nothing was written."""


@runtime_checkable
class Connector(Protocol):
    """The component that manages a Target's live side (CONTEXT.md **Connector**)."""

    def describe(self) -> ConnectorDescription:
        """Kind, every operation with its access, class and observations, the environments
        and Evidence stores it serves for this Target."""
        ...

    def read_deployed_set(self, environment: str) -> DeployedSet:
        """The deployed set of `environment` as it is now; `ConnectorError` naming the
        environments there are when it names none."""
        ...

    def read_evidence(
        self, environment: str, kind: EvidenceKind, query: EvidenceQuery
    ) -> EvidenceRows:
        """The rows of one Evidence store; `ConnectorError` when the Target declares none
        of that kind."""
        ...

    def write_deployed_set(
        self,
        environment: str,
        sections: Mapping[str, str | dict[str, Any]],
        *,
        expected_fingerprint: str,
        change_record: str | None,
    ) -> WriteReceipt:
        """Write `sections` to `environment` if its deployed Fingerprint is still
        `expected_fingerprint`; a receipt, or `WriteRefused`, never a silent success."""
        ...


__all__ = [
    "READ_DEPLOYED_SET",
    "READ_EVIDENCE",
    "WRITE_DEPLOYED_SET",
    "Connector",
    "ConnectorDescription",
    "ConnectorError",
    "CredentialMissing",
    "DeployedSet",
    "EvidenceQuery",
    "EvidenceRows",
    "ExpectedFingerprintMoved",
    "Operation",
    "WriteReceipt",
    "WriteRefused",
    "evidence_operation",
]
