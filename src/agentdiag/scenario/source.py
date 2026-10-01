"""Reading a Suite file as data, and telling which of the three shapes it is in (D20).

Kept apart from validating and loading because both readers of a Suite file start here —
`validate` and `load_suite` — and the shape decides what each does next: a legacy shape is
an error naming the schema doc (`docs/scenario-schema.md`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml

Shape = Literal["standard", "extended", "ci"]
"""The standard schema, a legacy adaptive `scenarios.json`, or legacy scripted CI cases."""


class UnreadableSuite(ValueError):
    """The file could not be read as data at all: missing, unparseable, or an unknown
    extension. Nothing about its content can be said."""


def parse_document(path: Path) -> Any:
    """The file's content as data: YAML (`.yaml`, `.yml`) or JSON (`.json`), by extension."""
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UnreadableSuite(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        if suffix in {".yaml", ".yml"}:
            return yaml.safe_load(text)
        if suffix == ".json":
            return json.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise UnreadableSuite(f"not valid {suffix.lstrip('.')}: {exc}") from exc
    raise UnreadableSuite(f"{path.name} is neither .yaml, .yml nor .json")


def document_shape(raw: dict[str, Any]) -> Shape:
    """Which shape a Suite mapping is in; anything that is no legacy shape is `standard`,
    and the standard schema's own validation says what is wrong with it."""
    tests = raw.get("tests")
    if isinstance(tests, list) and any(
        isinstance(test, dict) and isinstance(test.get("turns"), list) for test in tests
    ):
        return "ci"
    scenarios = raw.get("scenarios")
    if isinstance(scenarios, list) and any(
        isinstance(scenario, dict) and "first_turn" in scenario for scenario in scenarios
    ):
        return "extended"
    return "standard"


__all__ = ["Shape", "UnreadableSuite", "document_shape", "parse_document"]
