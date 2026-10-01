"""One Connector environment, resolved: its identifiers, its credentials, its classes.

ADR-0011 §2: a Connector reads environment identifiers and credential *names* from the
Manifest's `connector.environments` block and the credentials themselves from the process
environment, and **fails closed**. A variable that is unset or empty is `CredentialMissing`
naming it; nothing falls back to another environment's variable or to a default, because the
`eu_prod → dev` trap was exactly that fallback, reading one environment with another's
key.

`protected` and `side_effects` are the *Adapter* environment's of the same name when there is
one (`Manifest.is_protected`, `Manifest.side_effects_of`): the two components touch one
environment, and it has one class. A Connector environment with no Adapter twin is allowed —
a platform Target may have a `prod` nobody converses with from here — and is then protected
by its name alone (`prod`, `staging`, `eu_prod`) and of class `none` for reads.

**A credential value is never written anywhere.** `ResolvedEnvironment.credentials` is
excluded from every dump, so `run.json`, `--json` output and a Sync break cannot carry one.

Offline: it reads the Manifest model and a mapping.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agentdiag.connector.base import ConnectorError, CredentialMissing
from agentdiag.run.manifest import Manifest, protected_by_name
from agentdiag.types import SideEffectClass

CREDENTIALS_KEY = "credentials"
"""The key of an environment block that maps a credential role to an environment variable's
name; everything else in the block is an identifier the Connector kind reads."""


class ResolvedEnvironment(BaseModel):
    """One `connector.environments.<name>` block, its credentials read from the process."""

    name: str
    identifiers: dict[str, Any] = Field(default_factory=dict)
    """The block as written, minus `credentials`."""

    credentials: dict[str, str] = Field(default_factory=dict, exclude=True, repr=False)
    """Role to value, read from the process environment; never dumped, never printed."""

    protected: bool
    side_effects: SideEffectClass
    directory: Path | None = Field(default=None, exclude=True)
    """The Target directory the Manifest was loaded from, which a relative identifier such
    as the in-process `store` is resolved against; None for a Manifest built in memory."""


def resolve_environment(
    manifest: Manifest, name: str, environ: Mapping[str, str] = os.environ
) -> ResolvedEnvironment:
    """Resolve Connector environment `name` of `manifest`, reading only its own variables.

    `ConnectorError` when the Manifest names no Connector or no such environment (naming the
    ones there are), or when `credentials` is not a mapping of role to variable name;
    `CredentialMissing` naming the first variable that is unset or empty.
    """
    section = manifest.connector
    if section is None:
        raise ConnectorError("the Manifest names no Connector")
    if name not in section.environments:
        present = ", ".join(section.environments) or "none"
        raise ConnectorError(
            f"the Manifest's connector block names no environment {name!r}; it names {present}"
        )
    block = dict(section.environments[name] or {})
    declared = block.pop(CREDENTIALS_KEY, None) or {}
    credentials = read_credentials(declared, name, environ, where="connector")
    twin = name in manifest.adapter.environment_names
    return ResolvedEnvironment(
        name=name,
        identifiers=block,
        credentials=credentials,
        protected=manifest.is_protected(name) if twin else protected_by_name(name),
        side_effects=manifest.side_effects_of(name) if twin else "none",
        directory=manifest.directory,
    )


def read_credentials(
    declared: Any, environment: str, environ: Mapping[str, str], *, where: str
) -> dict[str, str]:
    """Role to value for one environment's `credentials: {role: VAR}`, each read from
    `environ[VAR]`, failing closed (ADR-0011 §2): the one function the Connector and the HTTP
    Adapter both read credentials through (phase-8 decision 2).

    `ConnectorError` when `declared` is not a mapping of role to a non-empty variable name
    (`where` is the block's top key, `connector` or `adapter`, for the message);
    `CredentialMissing` naming the first variable unset or empty — never another
    environment's, never a default."""
    if not isinstance(declared, Mapping) or not all(
        isinstance(role, str) and isinstance(variable, str) and variable
        for role, variable in declared.items()
    ):
        raise ConnectorError(
            f"{where}.environments.{environment}.credentials must map each role to the name "
            "of an environment variable"
        )
    credentials: dict[str, str] = {}
    for role, variable in declared.items():
        value = environ.get(variable)
        if not value:
            raise CredentialMissing(
                f"environment {environment!r} needs ${variable} for its {role}; it is not set"
            )
        credentials[role] = value
    return credentials


def scrub_values(text: str, declared: Any, environ: Mapping[str, str] = os.environ) -> str:
    """`text` with the value of every variable `declared` (`{role: VAR}`) names replaced by
    `$<VAR>`: a credential value never reaches a file or a terminal."""
    if not isinstance(declared, Mapping):
        return text
    for variable in declared.values():
        value = environ.get(variable) if isinstance(variable, str) else None
        if value:
            text = text.replace(value, f"${variable}")
    return text


class ResolvedEnvironments(Mapping[str, ResolvedEnvironment]):
    """Every environment the Manifest's `connector` block names, each resolved only when it
    is looked up: what `build_connector` hands a Connector.

    Resolution fails closed *per environment* (ADR-0011 §2): iterating, `len` and `in` read
    the names alone, and only `[name]` — which a Connector does when it reads that
    environment — reads that environment's own variables, once. So a missing `$PROD_TOKEN`
    refuses a read of `prod` and never a read of `local`."""

    def __init__(self, manifest: Manifest, environ: Mapping[str, str] = os.environ) -> None:
        self._manifest = manifest
        self._environ = environ
        section = manifest.connector
        self._names = list(section.environments) if section is not None else []
        self._resolved: dict[str, ResolvedEnvironment] = {}

    def __getitem__(self, name: str) -> ResolvedEnvironment:
        if name not in self._names:
            raise KeyError(name)
        if name not in self._resolved:
            self._resolved[name] = resolve_environment(self._manifest, name, self._environ)
        return self._resolved[name]

    def __contains__(self, name: object) -> bool:
        return name in self._names

    def __iter__(self) -> Iterator[str]:
        return iter(self._names)

    def __len__(self) -> int:
        return len(self._names)


def resolve_environments(
    manifest: Manifest, environ: Mapping[str, str] = os.environ
) -> ResolvedEnvironments:
    """Every environment the Manifest's `connector` block names, resolved lazily."""
    return ResolvedEnvironments(manifest, environ)


def scrub_credentials(
    text: str, manifest: Manifest, environment: str, environ: Mapping[str, str] = os.environ
) -> str:
    """`text` with every credential value `environment` names replaced by `$<VAR>`: what a
    Connector's error says may reach a file or a terminal, and a value never may."""
    section = manifest.connector
    block = section.environments.get(environment) if section is not None else None
    declared = (block or {}).get(CREDENTIALS_KEY) or {}
    return scrub_values(text, declared, environ)


__all__ = [
    "CREDENTIALS_KEY",
    "ResolvedEnvironment",
    "ResolvedEnvironments",
    "read_credentials",
    "resolve_environment",
    "resolve_environments",
    "scrub_credentials",
    "scrub_values",
]
