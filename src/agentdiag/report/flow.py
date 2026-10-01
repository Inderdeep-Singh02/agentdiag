"""One Flow's definition as the Flow view draws it: steps and edges (phase-7 decision 21).

A Connector reads a Target's Flows under `DeployedSet.flows`, each id mapped to the
platform's own definition. Platforms spell a graph differently, so `flow_definition_view`
is a reader that knows no platform: it accepts the three shapes a definition is found in,

- `steps` (a list of step mappings) with `edges` (pairs, or mappings `from`/`to` or
  `source`/`target`);
- `nodes` with `links`, the same two spellings of an edge;
- `steps` alone, each step pointing at the ones after it with `next` (one id or a list),

and names what it found when a definition is none of them (`FlowDefinitionUnreadable`). A
step's `kind` and `calls` are whatever the definition says (`kind` or `type`; `calls`, or the
`tool` it calls), passed through as text: the renderer draws a box per step and a line per
edge and has no word of any platform's vocabulary. The Flow's `tool` (what calls it), `state`
and `version` come the same way.

Offline: it reads a mapping.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, TypeGuard

from pydantic import BaseModel, Field

EDGE_ENDS = (("from", "to"), ("source", "target"))
NEXT_KEY = "next"


class FlowStep(BaseModel):
    """One step of a Flow, as its definition names it."""

    id: str
    name: str
    kind: str | None = None
    calls: str | None = None
    """What the step calls, as the definition spells it: a tool, an endpoint, a function."""


class FlowDefinitionView(BaseModel):
    """One Flow's definition as steps and edges, from the Connector's read."""

    id: str
    tool: str | None = None
    """The tool whose call runs the Flow, when the definition names it."""

    state: str | None = None
    version: str | None = None
    steps: list[FlowStep] = Field(default_factory=list)
    edges: list[tuple[str, str]] = Field(default_factory=list)


class FlowDefinitionUnreadable(ValueError):
    """A Flow definition in none of the accepted shapes; the message says what was found."""


def flow_definition_view(flow_id: str, definition: Mapping[str, Any]) -> FlowDefinitionView:
    """The steps and edges of Flow `flow_id`'s definition (one `DeployedSet.flows` entry)."""
    if not isinstance(definition, Mapping):
        raise FlowDefinitionUnreadable(
            f"Flow {flow_id}: the definition is a {type(definition).__name__}, not a mapping"
        )
    raw_steps, edges = _graph(flow_id, definition)
    steps = [_step(flow_id, index, raw) for index, raw in enumerate(raw_steps)]
    known = [step.id for step in steps]
    repeated = sorted({step for step in known if known.count(step) > 1})
    if repeated:
        raise FlowDefinitionUnreadable(f"Flow {flow_id}: step ids repeat: {', '.join(repeated)}")
    if edges is None:
        edges = [
            (step_id, after)
            for step_id, raw in zip(known, raw_steps, strict=True)
            for after in _next(flow_id, step_id, raw.get(NEXT_KEY))
        ]
    dangling = sorted({end for edge in edges for end in edge if end not in known})
    if dangling:
        raise FlowDefinitionUnreadable(
            f"Flow {flow_id}: edges name steps the definition does not hold: {', '.join(dangling)}"
        )
    return FlowDefinitionView(
        id=flow_id,
        tool=_text(definition.get("tool")),
        state=_text(definition.get("state")),
        version=_text(definition.get("version")),
        steps=steps,
        edges=edges,
    )


def _graph(
    flow_id: str, definition: Mapping[str, Any]
) -> tuple[list[Mapping[str, Any]], list[tuple[str, str]] | None]:
    """The raw steps and, when the definition lists them, the edges; None means the steps
    point at each other with `next`."""
    steps = definition.get("steps")
    if _step_list(steps):
        edges = definition.get("edges")
        if isinstance(edges, list):
            return steps, [_edge(flow_id, edge) for edge in edges]
        if edges is None and (len(steps) == 1 or any(NEXT_KEY in step for step in steps)):
            return steps, None
    nodes, links = definition.get("nodes"), definition.get("links")
    if _step_list(nodes) and isinstance(links, list):
        return nodes, [_edge(flow_id, edge) for edge in links]
    found = ", ".join(
        f"{key} ({type(value).__name__})" for key, value in sorted(definition.items())
    )
    raise FlowDefinitionUnreadable(
        f"Flow {flow_id}: not steps/edges, nodes/links, or steps with next pointers; "
        f"found {found or 'an empty mapping'}"
    )


def _step_list(value: Any) -> TypeGuard[list[Mapping[str, Any]]]:
    return isinstance(value, list) and all(isinstance(item, Mapping) for item in value)


def _step(flow_id: str, index: int, raw: Mapping[str, Any]) -> FlowStep:
    step_id = _text(raw.get("id"))
    if step_id is None:
        found = ", ".join(sorted(str(key) for key in raw)) or "nothing"
        raise FlowDefinitionUnreadable(
            f"Flow {flow_id}: step {index + 1} has no id (found {found})"
        )
    return FlowStep(
        id=step_id,
        name=_text(raw.get("name")) or _text(raw.get("title")) or step_id,
        kind=_text(raw.get("kind")) or _text(raw.get("type")),
        calls=_text(raw.get("calls")) or _text(raw.get("tool")),
    )


def _edge(flow_id: str, raw: Any) -> tuple[str, str]:
    if isinstance(raw, Sequence) and not isinstance(raw, str) and len(raw) == 2:
        start, end = _text(raw[0]), _text(raw[1])
        if start is not None and end is not None:
            return start, end
    if isinstance(raw, Mapping):
        for start_key, end_key in EDGE_ENDS:
            start, end = _text(raw.get(start_key)), _text(raw.get(end_key))
            if start is not None and end is not None:
                return start, end
    raise FlowDefinitionUnreadable(
        f"Flow {flow_id}: an edge is not a pair, from/to or source/target: {_shown(raw)}"
    )


def _next(flow_id: str, step_id: str, raw: Any) -> list[str]:
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    after = [_text(item) for item in items]
    if any(item is None for item in after):
        raise FlowDefinitionUnreadable(
            f"Flow {flow_id}: step {step_id}'s next is not a step id or a list of them: "
            f"{_shown(raw)}"
        )
    return [item for item in after if item is not None]


def _text(value: Any) -> str | None:
    """A definition's scalar as text; None for an absent or empty value, JSON for the rest."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float):
        return json.dumps(value)
    return _shown(value)


def _shown(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)[:200]


__all__ = ["FlowDefinitionUnreadable", "FlowDefinitionView", "FlowStep", "flow_definition_view"]
