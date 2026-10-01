"""The in-process Connector: reads a Python module's deployed set (phase-6 decision 22).

A toy Target has no platform, so its "deployed set" is what its running module holds. The
Manifest names it per environment, `connector.environments.<name>.deployed: module:attr`,
and the attribute follows the **deployed-set convention**: a mapping, or an object, with
`prompts` (name → text), `tools` (name → schema entry) and `model`, and optionally `tier`,
`provider` and `flows` (id → definition with its `state`) — or a zero-argument callable
returning one. `read_deployed_set` looks the attribute up again on every call and reads it
*now* (calling it when it is a callable), so mutating the live object — a test that edits
the module's mapping, or `monkeypatch` of a name the callable reads — is an edit on the
deployed side, and `sync` reads it as `deployed_ahead`. The module is imported once and
never reloaded: an edit to its source file is seen by the next process, not this one.

Evidence stores come the same way, from `connector.evidence.<kind>.rows: module:attr` (a
list of rows in the generic shapes of decision 33, or a callable returning one), filtered by
the query's `conversation_id`, `since` and `until` and cut to its `limit`. The attribute may
also be one mapping of a platform's stores by kind — the help desk's `platform.EVIDENCE`
(ticket 13, decision 38) — and the entry of the kind being read is its rows. A kind the block
does not declare, or a mapping with no entry for it, is `ConnectorError`.

**A store keeps the platform between processes.** An environment block may name `store:
<path under the Target directory>`: `read_deployed_set` then loads that file into the
module's mapping *in place* before reading (so a Target that reads the mapping at each
request, the help desk's, sees the stored record from the first Sync on), and a write saves
the mapping to it afterwards (atomic replace). Without a store, a push lasts as long as the
process that made it.

**It writes the module's mapping in place** (phase-7 decision 15), and only through a push
(ADR-0011 §6): the environment's `deployed` attribute must be a mutable mapping — a callable
or an immutable object is `WriteRefused` naming it, which is how the first toy's
`deployed_set` function stays read-only. The write compares first: the deployed Fingerprint
of a read taken now (`sync.observe.deployed_fingerprint`) must equal the one the push
expected, else `ExpectedFingerprintMoved`; then `apply_sections` writes each section (a
heading's body inside its prompt, a whole prompt, a tool, a Flow), and the model, provider
and tier are refused as read-only (ADR-0011 §1). Its class stays `none`: nothing leaves the
process. And it converses with nothing: the deployed set is read from the module, never from
a Turn, so `sync` fingerprints a Target nobody is talking to (ADR-0011 §1).

Offline at import time: the module a Manifest names is imported when it is read, and the
toy's imports no model client.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any, cast, get_args

from pydantic import ValidationError

from agentdiag.connector.base import (
    READ_DEPLOYED_SET,
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
from agentdiag.inprocess_rules import STORE_KEY, connector_problems, store_problem
from agentdiag.reference import import_attribute
from agentdiag.run.manifest import ConnectorSection
from agentdiag.sync.sections import (
    FLOW_PREFIX,
    PROMPT_PREFIX,
    TOOL_PREFIX,
    SectionMissing,
    replace_section,
)
from agentdiag.timestamps import now_utc
from agentdiag.types import EvidenceKind, SectionKind

DEPLOYED_KEY = "deployed"
"""The environment block's key naming the deployed set, `module:attr`."""

ROWS_KEY = "rows"
"""The Evidence block's key naming its rows, `module:attr`."""

OBSERVES: tuple[SectionKind, ...] = ("prompt", "tool", "model", "provider", "flow", "tier")
"""What `read_deployed_set` can observe: every section kind but a data source's identity,
which the Manifest itself holds."""

CONVENTION_KEYS = ("prompts", "tools", "flows", "model", "tier", "provider")
"""The deployed-set convention's keys (decision 22)."""

READ_ONLY = (
    "model, provider and tier are read-only in v1 (ADR-0011 §1): they are set on the platform"
)
"""Why a write naming any section but a prompt, a tool or a Flow is refused."""

TIME_KEYS = ("created_at", "started_at")
"""Where a generic row says when it happened, for `since` and `until`."""


class InProcessConnector:
    """Reads the deployed set and Evidence of a Target that lives in this process."""

    kind = "inprocess"

    def __init__(
        self,
        section: ConnectorSection,
        *,
        environments: Mapping[str, ResolvedEnvironment],
    ) -> None:
        self.section = section
        self.environments = environments
        """Kept as handed over, never copied: `build_connector`'s mapping resolves an
        environment's credentials only when a read looks it up (ADR-0011 §2)."""
        unknown = sorted(set(section.evidence) - set(get_args(EvidenceKind)))
        if unknown:
            raise ConnectorError(
                f"connector.evidence names {', '.join(unknown)}; the Evidence stores are "
                f"{', '.join(get_args(EvidenceKind))}"
            )
        self.evidence = cast(list[EvidenceKind], list(section.evidence))

    @staticmethod
    def validate_section(section: ConnectorSection) -> list[tuple[str, str]]:
        """What `validate` adds for this kind (walkthrough friction 9): every environment
        names its `deployed` set and every Evidence store its `rows`, each a `module:attr`
        found without importing it (`agentdiag.inprocess_rules`)."""
        return connector_problems(section)

    def describe(self) -> ConnectorDescription:
        """One read of the deployed set, one read per declared Evidence store, one write;
        every one of class `none`, because nothing here leaves the process."""
        operations = [
            Operation(
                name=READ_DEPLOYED_SET, access="read", side_effects="none", observes=list(OBSERVES)
            ),
            *(
                Operation(name=evidence_operation(kind), access="read", side_effects="none")
                for kind in self.evidence
            ),
            Operation(name=WRITE_DEPLOYED_SET, access="write", side_effects="none"),
        ]
        return ConnectorDescription(
            kind=self.kind,
            operations=operations,
            environments=list(self.environments),
            evidence=self.evidence,
        )

    def read_deployed_set(self, environment: str) -> DeployedSet:
        """Import the environment's `deployed` attribute and read it now."""
        resolved = self._environment(environment)
        reference = resolved.identifiers.get(DEPLOYED_KEY)
        if not isinstance(reference, str):
            raise ConnectorError(
                f"connector.environments.{environment} names no `{DEPLOYED_KEY}: module:attr`"
            )
        store = _store(resolved)
        if store is not None and store.is_file():
            _load_store(store, _held_mapping(reference), reference)
        read = _convention(_resolve(reference), reference)
        try:
            return DeployedSet.model_validate(
                {**read, "read_at": now_utc(), "environment": environment}
            )
        except ValidationError as exc:
            raise ConnectorError(
                f"{reference} does not follow the deployed-set convention: {exc}"
            ) from exc

    def read_evidence(
        self, environment: str, kind: EvidenceKind, query: EvidenceQuery
    ) -> EvidenceRows:
        """The declared store's rows, filtered by the query, in the order the store holds."""
        self._environment(environment)
        block = self.section.evidence.get(kind)
        if block is None:
            raise ConnectorError(f"this Target declares no {kind} Evidence store")
        reference = block.get(ROWS_KEY)
        if not isinstance(reference, str):
            raise ConnectorError(f"connector.evidence.{kind} names no `{ROWS_KEY}: module:attr`")
        rows = _resolve(reference)
        if isinstance(rows, Mapping):
            if kind not in rows:
                raise ConnectorError(
                    f"{reference} holds no {kind} rows; it holds {', '.join(map(str, rows))}"
                )
            rows = rows[kind]
        if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
            raise ConnectorError(f"{reference} is not a list of rows")
        kept = [dict(row) for row in rows if _matches(row, query)]
        truncated = query.limit is not None and len(kept) > query.limit
        if query.limit is not None:
            kept = kept[: query.limit]
        return EvidenceRows(
            kind=kind, query=query, rows=kept, read_at=now_utc(), truncated=truncated
        )

    def write_deployed_set(
        self,
        environment: str,
        sections: Mapping[str, str | dict[str, Any]],
        *,
        expected_fingerprint: str,
        change_record: str | None,
    ) -> WriteReceipt:
        """Write `sections` into the environment's mapping, if its deployed Fingerprint is
        still `expected_fingerprint` (phase-7 decision 15); a receipt naming both ids."""
        from agentdiag.sync.observe import deployed_fingerprint

        resolved = self._environment(environment)
        reference = resolved.identifiers.get(DEPLOYED_KEY)
        if not isinstance(reference, str):
            raise WriteRefused(
                f"connector.environments.{environment} names no `{DEPLOYED_KEY}: module:attr`"
            )
        held = _held_mapping(reference)
        found = deployed_fingerprint(self.read_deployed_set(environment))
        if found != expected_fingerprint:
            raise ExpectedFingerprintMoved(
                f"the deployed set of {environment!r} moved: the push expected fingerprint "
                f"{expected_fingerprint[:8]} and found {found[:8]}; preview the push again"
            )
        apply_sections(held, sections)
        store = _store(resolved)
        if store is not None:
            _save_store(store, held)
        after = deployed_fingerprint(self.read_deployed_set(environment))
        return WriteReceipt(
            environment=environment,
            written_at=now_utc(),
            sections=sorted(sections),
            fingerprint_before=found,
            fingerprint_after=after,
        )

    def _environment(self, environment: str) -> ResolvedEnvironment:
        if environment not in self.environments:
            names = ", ".join(self.environments) or "none"
            raise ConnectorError(
                f"the {self.kind} Connector names no environment {environment!r}; it names {names}"
            )
        return self.environments[environment]


def apply_sections(
    deployed: MutableMapping[str, Any], sections: Mapping[str, str | dict[str, Any]]
) -> None:
    """Write `sections` into a mapping in the deployed-set convention, in place: a
    `prompt.<name>#<slug>` value into that heading of `deployed["prompts"][name]`
    (`sections.replace_section`, headings kept), a `prompt.<name>` value as the whole text,
    a `tool.<name>` as `deployed["tools"][name]`, a `flow.<id>` as `deployed["flows"][id]`.
    Every section is checked before any is written, so a refused write writes nothing:
    any other id is `WriteRefused` (read-only, ADR-0011 §1), and so is a value of the
    wrong shape or a heading the prompt does not hold."""
    prompts: dict[str, str] = dict(deployed.get("prompts") or {})
    tools: dict[str, Any] = {}
    flows: dict[str, Any] = {}
    for identifier in sorted(sections):
        value = sections[identifier]
        if identifier.startswith(PROMPT_PREFIX):
            if not isinstance(value, str):
                raise WriteRefused(f"{identifier} is a prompt section: its value must be text")
            name, _, heading = identifier.removeprefix(PROMPT_PREFIX).partition("#")
            if not heading:
                prompts[name] = value
                continue
            try:
                prompts[name] = replace_section(prompts.get(name, ""), heading, value)
            except SectionMissing as missing:
                raise WriteRefused(f"{identifier}: {missing}") from missing
        elif identifier.startswith((TOOL_PREFIX, FLOW_PREFIX)):
            if not isinstance(value, dict):
                raise WriteRefused(f"{identifier}: its value must be a JSON object")
            if identifier.startswith(TOOL_PREFIX):
                tools[identifier.removeprefix(TOOL_PREFIX)] = value
            else:
                flows[identifier.removeprefix(FLOW_PREFIX)] = value
        else:
            raise WriteRefused(f"{identifier}: {READ_ONLY}")
    if prompts != dict(deployed.get("prompts") or {}):
        _held(deployed, "prompts").update(prompts)
    if tools:
        _held(deployed, "tools").update(tools)
    if flows:
        _held(deployed, "flows").update(flows)


def _held(deployed: MutableMapping[str, Any], key: str) -> MutableMapping[str, Any]:
    """The convention's mapping under `key`, created when absent, edited in place so every
    holder of it (the Target reading its prompt at each request) sees the write."""
    held = deployed.get(key)
    if not isinstance(held, MutableMapping):
        held = {} if held is None else dict(held)
        deployed[key] = held
    return held


def _held_mapping(reference: str) -> MutableMapping[str, Any]:
    """The mapping `reference` names, which a write edits and a store loads into in place;
    `WriteRefused` naming it when it is a callable or an immutable object."""
    try:
        held = import_attribute(reference)
    except (ImportError, ValueError) as exc:
        raise ConnectorError(f"{reference}: {exc}") from exc
    if not isinstance(held, MutableMapping):
        shape = "a callable" if callable(held) else f"a {type(held).__name__}"
        raise WriteRefused(
            f"{reference} is {shape}, not a mutable mapping: the in-process Connector "
            "writes only a deployed set it can edit in place"
        )
    return held


def _store(resolved: ResolvedEnvironment) -> Path | None:
    """The environment's `store` file, resolved against the Target directory; None when
    the block names none. The store keeps an in-process platform's deployed set between
    processes, as a hosted platform keeps its records: without one, what a push writes
    lasts as long as the process that wrote it."""
    store = resolved.identifiers.get(STORE_KEY)
    if store is None:
        return None
    problem = store_problem(store)
    if problem is not None:
        raise ConnectorError(f"connector.environments.{resolved.name}.{STORE_KEY} {problem}")
    if resolved.directory is None:
        raise ConnectorError(
            f"connector.environments.{resolved.name}.{STORE_KEY} is relative to the Target "
            "directory, and this Manifest was not loaded from one"
        )
    return resolved.directory / str(store)


def _load_store(store: Path, held: MutableMapping[str, Any], reference: str) -> None:
    """Replace the mapping's contents with the store's, in place, so every holder of the
    mapping (the Target reading its prompt at each request) sees the stored record."""
    try:
        loaded = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConnectorError(f"the store {store} of {reference} cannot be read: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConnectorError(f"the store {store} of {reference} does not hold a mapping")
    held.clear()
    held.update(loaded)


def _save_store(store: Path, held: Mapping[str, Any]) -> None:
    """Write the mapping to its store: indented, keys sorted, replaced atomically, so a
    process that dies mid-write leaves the last record whole."""
    store.parent.mkdir(parents=True, exist_ok=True)
    partial = store.with_name(f".{store.name}.partial")
    partial.write_text(json.dumps(dict(held), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(partial, store)


def _resolve(reference: str) -> Any:
    """A `module:attr` imported, and called when it is a zero-argument callable."""
    try:
        value = import_attribute(reference)
    except (ImportError, ValueError) as exc:
        raise ConnectorError(f"{reference}: {exc}") from exc
    if callable(value) and not isinstance(value, type):
        try:
            value = value()
        except Exception as exc:
            raise ConnectorError(f"{reference} raised: {type(exc).__name__}: {exc}") from exc
    return value


def _convention(value: Any, reference: str) -> dict[str, Any]:
    """The convention's keys from a mapping or an object's attributes; absent ones left out."""
    if isinstance(value, Mapping):
        found = {key: value[key] for key in CONVENTION_KEYS if key in value}
    else:
        found = {key: getattr(value, key) for key in CONVENTION_KEYS if hasattr(value, key)}
    if not found:
        raise ConnectorError(
            f"{reference} holds none of {', '.join(CONVENTION_KEYS)} (the deployed-set convention)"
        )
    return {key: dict(item) if isinstance(item, Mapping) else item for key, item in found.items()}


def _matches(row: Mapping[str, Any], query: EvidenceQuery) -> bool:
    if query.conversation_id is not None and row.get("conversation_id") != query.conversation_id:
        return False
    at = next((str(row[key]) for key in TIME_KEYS if row.get(key) is not None), None)
    if at is None:
        return True
    if query.since is not None and at < query.since:
        return False
    return not (query.until is not None and at > query.until)


__all__ = [
    "DEPLOYED_KEY",
    "OBSERVES",
    "READ_ONLY",
    "ROWS_KEY",
    "STORE_KEY",
    "InProcessConnector",
    "apply_sections",
]
