"""The Connector: the component that manages a Target's live side (ADR-0011, ADR-0014).

`base` holds what every Connector is; `environment` resolves one environment's identifiers
and credentials, failing closed; `plugins` finds a kind by entry point; `inprocess` is the
Connector for the toy Targets, which reads a module's deployed set; `conformance` is the
suite every Connector must pass, as the Adapter has one.

Offline at import time, like `agentdiag.sync`: nothing here reaches a model client.
"""

from agentdiag.connector.base import (
    Connector,
    ConnectorDescription,
    ConnectorError,
    CredentialMissing,
    DeployedSet,
    EvidenceQuery,
    EvidenceRows,
    ExpectedFingerprintMoved,
    Operation,
    WriteReceipt,
    WriteRefused,
)
from agentdiag.connector.environment import ResolvedEnvironment, resolve_environment
from agentdiag.connector.plugins import (
    UnknownKind,
    adapter_class,
    build_connector,
    connector_class,
)

__all__ = [
    "Connector",
    "ConnectorDescription",
    "ConnectorError",
    "CredentialMissing",
    "DeployedSet",
    "EvidenceQuery",
    "EvidenceRows",
    "ExpectedFingerprintMoved",
    "Operation",
    "ResolvedEnvironment",
    "UnknownKind",
    "WriteReceipt",
    "WriteRefused",
    "adapter_class",
    "build_connector",
    "connector_class",
    "resolve_environment",
]
