"""The canonical YAML spelling of a loaded Suite: what `generate` writes.

`render_suite` is the one canonical dump: model field order, defaults left out, and every
declaration in the short form `models.short_form` gives, the exact inverse of the
`bind_short_form` every value is bound through, so a rendered Suite reads back to the
Suite it was rendered from. `render_commented_suite` is the same dump with a comment line
above each Scenario, for provenance.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence
from typing import Any

import yaml

from agentdiag.scenario.models import EvalDeclaration, Scenario, SimulateTurn, Suite, short_form


def render_suite(suite: Suite) -> str:
    """The one canonical YAML spelling of a Suite.

    Inherited declarations are left out (they are written once, on the Suite), defaults
    are left out, and every declaration is written in the shortest form that reads back
    as the same declaration.
    """
    return _dump(_suite_document(suite))


def render_commented_suite(suite: Suite, *, header: str, above: Sequence[str | None]) -> str:
    """`render_suite`'s spelling with comment lines: `header` (already `# `-prefixed) at the
    top, and `above[n]` as a `# ` line directly above the n-th Scenario. What `generate`
    writes (phase-6 decision 29): the same canonical dump, so a generated Suite reads back
    to the Suite it was rendered from, with the provenance of each Scenario beside it."""
    document = _suite_document(suite)
    scenarios = document.pop("scenarios")
    extras = document.pop("extras", None)
    parts = [header, _dump(document), "scenarios:\n"]
    for position, scenario in enumerate(scenarios):
        if position:
            parts.append("\n")
        comment = above[position] if position < len(above) else None
        if comment:
            parts.extend(f"  # {line}\n" for line in comment.splitlines())
        parts.append(textwrap.indent(_dump([scenario]), "  "))
    if extras:
        parts.append(_dump({"extras": extras}))
    return "".join(parts)


def _dump(value: Any) -> str:
    return yaml.safe_dump(
        value,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )


def _suite_document(suite: Suite) -> dict[str, Any]:
    """The Suite as plain data in authored spelling, in the models' field order."""
    document: dict[str, Any] = {"schema_version": suite.schema_version, "target": suite.target}
    if suite.description is not None:
        document["description"] = suite.description
    evals = [_declaration_document(d) for d in suite.evals]
    if evals:
        document["evals"] = evals
    if suite.fixtures:
        document["fixtures"] = {
            name: ({"kind": spec.kind} if spec.kind != "data" else {}) | spec.data
            for name, spec in suite.fixtures.items()
        }
    if suite.not_run:
        document["not_run"] = dict(suite.not_run)
    document["scenarios"] = [_scenario_document(scenario) for scenario in suite.scenarios]
    if suite.extras:
        document["extras"] = suite.extras
    return document


def _scenario_document(scenario: Scenario) -> dict[str, Any]:
    """A Scenario as an author writes it: empty lists and defaults left out, so a rendered
    Suite reads like a hand-written one, and inherited declarations left on the Suite,
    where they were written, so rendering a loaded Suite does not copy them into every
    Scenario."""
    document: dict[str, Any] = {"id": scenario.id, "title": scenario.title}
    for key in ("tags", "provenance", "notes", "fixtures"):
        value = getattr(scenario, key)
        if value:
            document[key] = value
    document["turns"] = [
        turn if isinstance(turn, str) else _simulate_document(turn) for turn in scenario.turns
    ]
    if scenario.max_turns is not None:
        document["max_turns"] = scenario.max_turns
    if scenario.continues is not None:
        document["continues"] = scenario.continues
    evals = [_declaration_document(d) for d in scenario.evals if not d.inherited]
    if evals:
        document["evals"] = evals
    if scenario.focus is not None:
        document["focus"] = scenario.focus
    if scenario.ground_truth is not None:
        document["ground_truth"] = scenario.ground_truth
    if not scenario.inherit_suite_evals:
        document["inherit_suite_evals"] = False
    if scenario.extras:
        document["extras"] = scenario.extras
    return document


def _simulate_document(turn: SimulateTurn) -> dict[str, Any]:
    spec = turn.simulate
    document: dict[str, Any] = {"goal": spec.goal}
    if spec.persona is not None:
        document["persona"] = spec.persona
    for key in ("known_facts", "unknown_facts", "hints"):
        value = getattr(spec, key)
        if value:
            document[key] = value
    if spec.stop_when is not None:
        document["stop_when"] = spec.stop_when.model_dump(exclude_none=True)
    if spec.stop_token is not None:
        document["stop_token"] = spec.stop_token
    return {"simulate": document}


def _declaration_document(declaration: EvalDeclaration) -> Any:
    """The shortest authored spelling that loads back as this declaration (decision 1)."""
    short = short_form(declaration)
    if short is not None:
        return short
    document: dict[str, Any] = {"eval": declaration.eval}
    if declaration.id is not None:
        document["id"] = declaration.id
    if declaration.params:
        document["params"] = declaration.params
    if declaration.threshold is not None:
        document["threshold"] = declaration.threshold
    if declaration.judge is not None:
        document["judge"] = declaration.judge.model_dump(exclude_none=True)
    return document


__all__ = [
    "render_commented_suite",
    "render_suite",
]
