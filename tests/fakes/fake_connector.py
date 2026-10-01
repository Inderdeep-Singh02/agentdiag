"""A Connector that serves fixtures and records every call (phase-6 decision 23).

The seam-1 tests of Sync need a deployed side they can move at will — a prompt edited on the
platform, a tool schema changed, a Flow switched off, a read that fails — without a
platform. `FakeConnector` holds one deployed set per environment in the deployed-set shape,
serves Evidence rows by `(environment, kind)`, refuses writes by default, and appends every
operation to `calls`, so a test can assert what was read and that nothing was written. With
`refuse_writes=False` it writes into its own `deployed` mapping with the compare-and-swap the
in-process Connector makes (phase-7 decision 16), and appends each write it took to `writes`.

It is not an entry point: entry points come from installed distributions, and the tests are
not one. A test serves it under a kind through the plugin registry's test seam,
`plugins.registered("fake", fake.as_kind())`, and a Manifest naming `connector: {kind:
fake}` then builds this very instance, so the test keeps its handle on it.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

from agentdiag.connector.base import (
    READ_DEPLOYED_SET,
    READ_EVIDENCE,
    WRITE_DEPLOYED_SET,
    ConnectorDescription,
    ConnectorError,
    DeployedSet,
    EvidenceQuery,
    EvidenceRows,
    ExpectedFingerprintMoved,
    Operation,
    WriteReceipt,
    WriteRefused,
    evidence_operation,
)
from agentdiag.connector.environment import ResolvedEnvironment
from agentdiag.connector.inprocess import apply_sections
from agentdiag.run.manifest import ConnectorSection
from agentdiag.timestamps import now_utc
from agentdiag.types import EvidenceKind

FAKE_KIND = "fake"

Call = tuple[str, str | None, Any]
"""(operation, environment, argument): what `calls` records; `describe` names none."""

DESCRIBE = "describe"

Write = tuple[str, dict[str, Any], str]
"""(environment, sections, expected fingerprint): one write the fake took."""


class FakeConnector:
    """Fixture deployed sets and Evidence rows behind the Connector protocol."""

    kind = FAKE_KIND

    def __init__(
        self,
        deployed: Mapping[str, Mapping[str, Any]],
        evidence: Mapping[tuple[str, EvidenceKind], list[dict[str, Any]]] | None = None,
        *,
        refuse_writes: bool = True,
    ) -> None:
        self.deployed: dict[str, dict[str, Any]] = {
            environment: copy.deepcopy(dict(read)) for environment, read in deployed.items()
        }
        self.evidence = {key: list(rows) for key, rows in (evidence or {}).items()}
        self.refuse_writes = refuse_writes
        self.calls: list[Call] = []
        self.writes: list[Write] = []
        self.failures: dict[str, Exception] = {}
        self.section: ConnectorSection | None = None
        self.environments: Mapping[str, ResolvedEnvironment] = {}

    # --- the test's handles ---

    def as_kind(self) -> Callable[..., FakeConnector]:
        """What `plugins.registered` serves: `build_connector` hands this the Manifest's
        block and the resolved environments, and gets this instance back."""

        def construct(
            section: ConnectorSection, *, environments: Mapping[str, ResolvedEnvironment]
        ) -> FakeConnector:
            self.section = section
            self.environments = environments
            return self

        return construct

    def set_prompt(self, environment: str, name: str, text: str) -> None:
        """An edit to a prompt on the deployed side."""
        self.deployed[environment].setdefault("prompts", {})[name] = text

    def set_tool(self, environment: str, name: str, schema: dict[str, Any]) -> None:
        """An edit to a tool schema on the deployed side."""
        self.deployed[environment].setdefault("tools", {})[name] = schema

    def raise_on(self, operation: str, exc: Exception) -> None:
        """Make `operation` raise `exc` from now on: the failure paths."""
        self.failures[operation] = exc

    # --- the protocol ---

    def describe(self) -> ConnectorDescription:
        self.calls.append((DESCRIBE, None, None))
        kinds = sorted({kind for _, kind in self.evidence})
        return ConnectorDescription(
            kind=self.kind,
            operations=[
                Operation(
                    name=READ_DEPLOYED_SET,
                    access="read",
                    side_effects="none",
                    observes=["prompt", "tool", "model", "provider", "flow", "tier"],
                ),
                *(
                    Operation(name=evidence_operation(kind), access="read", side_effects="none")
                    for kind in kinds
                ),
                Operation(name=WRITE_DEPLOYED_SET, access="write", side_effects="sandboxed"),
            ],
            environments=list(self.deployed),
            evidence=kinds,
        )

    def read_deployed_set(self, environment: str) -> DeployedSet:
        self.calls.append((READ_DEPLOYED_SET, environment, None))
        self._fail(READ_DEPLOYED_SET)
        read = self._environment(environment)
        return DeployedSet.model_validate(
            {**copy.deepcopy(read), "read_at": now_utc(), "environment": environment}
        )

    def read_evidence(
        self, environment: str, kind: EvidenceKind, query: EvidenceQuery
    ) -> EvidenceRows:
        self.calls.append((READ_EVIDENCE, environment, (kind, query)))
        self._fail(READ_EVIDENCE)
        self._environment(environment)
        if (environment, kind) not in self.evidence:
            raise ConnectorError(f"this Target declares no {kind} Evidence store")
        return EvidenceRows(
            kind=kind,
            query=query,
            rows=copy.deepcopy(self.evidence[(environment, kind)]),
            read_at=now_utc(),
        )

    def write_deployed_set(
        self,
        environment: str,
        sections: Mapping[str, str | dict[str, Any]],
        *,
        expected_fingerprint: str,
        change_record: str | None,
    ) -> WriteReceipt:
        self.calls.append((WRITE_DEPLOYED_SET, environment, dict(sections)))
        self._fail(WRITE_DEPLOYED_SET)
        self._environment(environment)
        if self.refuse_writes:
            raise WriteRefused("the fake Connector refuses writes")
        from agentdiag.sync.observe import deployed_fingerprint

        found = deployed_fingerprint(self._read(environment))
        if found != expected_fingerprint:
            raise ExpectedFingerprintMoved(
                f"the fake's {environment!r} moved: expected {expected_fingerprint[:8]}, "
                f"found {found[:8]}"
            )
        apply_sections(self.deployed[environment], sections)
        self.writes.append((environment, copy.deepcopy(dict(sections)), expected_fingerprint))
        return WriteReceipt(
            environment=environment,
            written_at=now_utc(),
            sections=sorted(sections),
            fingerprint_before=found,
            fingerprint_after=deployed_fingerprint(self._read(environment)),
        )

    def _read(self, environment: str) -> DeployedSet:
        """The fixture read, unrecorded: what the write's compare-and-swap looks at."""
        return DeployedSet.model_validate(
            {
                **copy.deepcopy(self.deployed[environment]),
                "read_at": now_utc(),
                "environment": environment,
            }
        )

    def _environment(self, environment: str) -> dict[str, Any]:
        """The fixture read of `environment`; when a Manifest built this fake, the
        environment is resolved first, so a missing credential refuses as a real one does."""
        if environment in self.environments:
            _ = self.environments[environment]
        if environment not in self.deployed:
            names = ", ".join(self.deployed) or "none"
            raise ConnectorError(
                f"the fake Connector names no environment {environment!r}; it names {names}"
            )
        return self.deployed[environment]

    def _fail(self, operation: str) -> None:
        if operation in self.failures:
            raise self.failures[operation]


__all__ = ["FAKE_KIND", "Call", "FakeConnector", "Write"]
