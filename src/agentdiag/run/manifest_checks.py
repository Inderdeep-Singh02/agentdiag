"""What `validate` checks in a Manifest beyond its shape, without running anything
(walkthrough frictions 5 and 9, ticket 13).

The Manifest model is lenient where a Run's `run.json` snapshot must still load, so these
rules live beside it and `manifest_report` asks them. Core checks what every kind means the
same way: the schema version is the one agentdiag reads; every `side_effects` (the Adapter's,
each environment's) is one of the closed classes; `adapter.kind` and `connector.kind` are
kinds an installed distribution registers (`plugins.check_registered`, which reads entry-point
metadata and loads nothing); every `connector.evidence` key is an Evidence store kind.

**A kind's own keys are its own rules.** Each Adapter and Connector class may define
`validate_section(section) -> list[tuple[str, str]]` (where, message). Core calls it for the
kinds it owns (`inprocess`, whose rules `agentdiag.inprocess_rules` holds offline: `factory`,
`tools`, `deployed` and `rows` are `module:attr` references found without importing them;
`http`, whose rules `agentdiag.adapter.http.config` holds, offline too, phase-8 decision 2) and
for a plugin kind only when its module is already imported (`plugins.imported_class`), so
`validate` never imports a plugin, and a plugin that reads `tools:` its own way is never
failed by core's reading of it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, get_args

from agentdiag.connector.plugins import (
    ADAPTER_GROUP,
    CONNECTOR_GROUP,
    CORE_KINDS_OF,
    UnknownKind,
    check_registered,
    imported_class,
)
from agentdiag.inprocess_rules import adapter_problems, connector_problems
from agentdiag.run.manifest import DEFAULT_ENVIRONMENT_KEY, Manifest, protected_by_name
from agentdiag.types import SIDE_EFFECT_ORDER, EvidenceKind

MANIFEST_SCHEMA_VERSION = 1

Problem = tuple[str, str]
"""(where, message), as `ManifestProblem` takes them."""


def http_adapter_problems(section: Any) -> list[Problem]:
    """The HTTP Adapter's own rules (phase-8 decision 2), imported when a Manifest names the
    kind, so a Manifest of another kind never loads the HTTP package."""
    from agentdiag.adapter.http.config import http_problems

    return http_problems(section)


CORE_RULES: Mapping[tuple[str, str], Callable[[Any], list[Problem]]] = {
    (ADAPTER_GROUP, "inprocess"): adapter_problems,
    (ADAPTER_GROUP, "http"): http_adapter_problems,
    (CONNECTOR_GROUP, "inprocess"): connector_problems,
}
"""The `validate_section` of each kind core registers, called without loading its class
(the in-process Adapter's module imports the SDK)."""


def manifest_problems(manifest: Manifest) -> list[Problem]:
    """Every rule above the Manifest breaks, in the order its sections are written."""
    problems: list[Problem] = []
    if manifest.schema_version != MANIFEST_SCHEMA_VERSION:
        problems.append(
            (
                "schema_version",
                f"agentdiag reads Manifest schema_version {MANIFEST_SCHEMA_VERSION}, not "
                f"{manifest.schema_version}",
            )
        )
    adapter = manifest.adapter
    problems += _side_effects("adapter.side_effects", adapter.side_effects)
    for name, block in adapter.environments.items():
        if name != DEFAULT_ENVIRONMENT_KEY and isinstance(block, Mapping):
            where = f"adapter.environments.{name}.side_effects"
            problems += _side_effects(where, block.get("side_effects"))
            if block.get("protected") is False and protected_by_name(name):
                problems.append(
                    (
                        f"adapter.environments.{name}.protected",
                        f"{name} is protected by its name (prod, staging and eu_prod are, in "
                        "any case), and `protected: false` cannot unprotect it; rename the "
                        "environment if it reaches no real user",
                    )
                )
    unknown = dict(kind_problems(manifest))
    problems += _kind_rules("adapter.kind", ADAPTER_GROUP, adapter, unknown)
    section = manifest.connector
    if section is None:
        return problems
    kinds = get_args(EvidenceKind)
    for kind in section.evidence:
        if kind not in kinds:
            problems.append(
                (
                    f"connector.evidence.{kind}",
                    f"{kind!r} is not an Evidence store kind; the kinds are {', '.join(kinds)}",
                )
            )
    problems += _kind_rules("connector.kind", CONNECTOR_GROUP, section, unknown)
    return problems


def kind_problems(manifest: Manifest, *, connector: bool = True) -> list[Problem]:
    """`adapter.kind`, and `connector.kind` when `connector` and the Manifest has one, when
    no installed distribution registers it (`plugins.check_registered`, loading nothing).
    The one owner of the rule: `validate` reports it, and preflight refuses a Run on it
    (ADR-0015 §3). `connector` is False for a preflight that will not use the Connector."""
    kinds = [("adapter.kind", ADAPTER_GROUP, manifest.adapter.kind)]
    if connector and manifest.connector is not None:
        kinds.append(("connector.kind", CONNECTOR_GROUP, manifest.connector.kind))
    problems: list[Problem] = []
    for where, group, kind in kinds:
        try:
            check_registered(group, kind)
        except UnknownKind as unknown:
            problems.append((where, str(unknown)))
    return problems


def _side_effects(where: str, declared: Any) -> list[Problem]:
    if declared is None or declared in SIDE_EFFECT_ORDER:
        return []
    return [
        (
            where,
            f"{declared!r} is not a side-effect class; it is one of {', '.join(SIDE_EFFECT_ORDER)}",
        )
    ]


def _kind_rules(where: str, group: str, section: Any, unknown: Mapping[str, str]) -> list[Problem]:
    """The kind unknown (`kind_problems`' message for `where`), or else its own
    `validate_section`'s problems."""
    if where in unknown:
        return [(where, unknown[where])]
    kind = section.kind
    core = CORE_RULES.get((group, kind))
    if core is not None and kind in CORE_KINDS_OF[group]:
        return core(section)
    hook = getattr(imported_class(group, kind), "validate_section", None)
    return list(hook(section)) if callable(hook) else []


__all__ = [
    "CORE_RULES",
    "MANIFEST_SCHEMA_VERSION",
    "http_adapter_problems",
    "kind_problems",
    "manifest_problems",
]
