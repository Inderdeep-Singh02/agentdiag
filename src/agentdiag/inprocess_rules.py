"""What `validate` checks of the in-process Adapter's and Connector's own blocks, offline.

Core checks what every Manifest means the same way (the schema version, the side-effect
classes, that each kind is registered, the Evidence store kinds). The keys inside an
environment block are the kind's own: the in-process Adapter reads `factory` and `tools`, the
in-process Connector `deployed` and Evidence `rows`, each a `module:attr`, and a plugin may
use the same key names for something else. So these rules are the in-process kinds', offered
through the optional `validate_section(section)` hook (`InProcessAdapter.validate_section`,
`InProcessConnector.validate_section`) that `run.manifest_checks` calls; they live here, in a
module that imports no SDK, because the in-process Adapter's module imports one and
`validate` must stay offline (walkthrough frictions 5 and 9, ticket 13).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath

from agentdiag.reference import reference_problem
from agentdiag.run.manifest import DEFAULT_ENVIRONMENT_KEY, AdapterSection, ConnectorSection

ADAPTER_REFERENCE_KEYS = ("factory", "tools")
DEPLOYED_KEY = "deployed"
ROWS_KEY = "rows"
STORE_KEY = "store"

Problem = tuple[str, str]


def adapter_problems(section: AdapterSection) -> list[Problem]:
    """Each environment's `factory` and `tools`: a `module:attr` found without importing it."""
    problems: list[Problem] = []
    for name, block in section.environments.items():
        if name == DEFAULT_ENVIRONMENT_KEY or not isinstance(block, Mapping):
            continue
        for key in ADAPTER_REFERENCE_KEYS:
            if key in block:
                problems += _reference(f"adapter.environments.{name}.{key}", block[key])
    return problems


def connector_problems(section: ConnectorSection) -> list[Problem]:
    """Each environment names its `deployed` set and each Evidence store its `rows`, each a
    `module:attr` found without importing it."""
    problems: list[Problem] = []
    for name, block in section.environments.items():
        where = f"connector.environments.{name}"
        if DEPLOYED_KEY not in block:
            problems.append((where, f"names no `{DEPLOYED_KEY}: module:attr`"))
        else:
            problems += _reference(f"{where}.{DEPLOYED_KEY}", block[DEPLOYED_KEY])
        if STORE_KEY in block:
            problem = store_problem(block[STORE_KEY])
            if problem is not None:
                problems.append((f"{where}.{STORE_KEY}", problem))
    for kind, block in section.evidence.items():
        where = f"connector.evidence.{kind}"
        if ROWS_KEY not in block:
            problems.append(
                (
                    where,
                    f"names no `{ROWS_KEY}: module:attr`, the rows the in-process Connector "
                    "reads (a list of rows, or a mapping of them by store kind)",
                )
            )
        else:
            problems += _reference(f"{where}.{ROWS_KEY}", block[ROWS_KEY])
    return problems


def store_problem(store: object) -> str | None:
    """Why a `store` value is not a relative path under the Target directory, or None."""
    if not isinstance(store, str) or not store.strip():
        return "must be a path relative to the Target directory, such as platform/local.json"
    path = PurePosixPath(store.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or PureWindowsPath(store).drive:
        return (
            f"{store!r} is not under the Target directory: the store is a relative path "
            "inside it, such as platform/local.json"
        )
    return None


def _reference(where: str, reference: object) -> list[Problem]:
    problem = reference_problem(reference)
    return [] if problem is None else [(where, problem)]


__all__ = ["STORE_KEY", "adapter_problems", "connector_problems", "store_problem"]
