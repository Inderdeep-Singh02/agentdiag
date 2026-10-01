"""The generation skill's `scenario-reference.md` cannot go stale (walkthrough frictions 14-18).

It is rendered from the models and the Eval registry; these assert every key, Eval,
parameter and operator has a description, and that the packaged file is the rendering.
"""

from __future__ import annotations

from pathlib import Path

from agentdiag.eval.registry import REGISTRY
from agentdiag.eval.tools import OPERATORS
from agentdiag.generate import reference
from agentdiag.scenario.models import AUTHORED_OBJECTS, field_keys

REPO = Path(__file__).resolve().parents[1]
PACKAGED = REPO / "src" / "agentdiag" / "skills" / "generate" / reference.REFERENCE_NAME


def test_every_key_of_every_authored_object_is_described() -> None:
    assert set(reference.DESCRIBED) == set(AUTHORED_OBJECTS)
    for where, model in AUTHORED_OBJECTS.items():
        assert set(reference.DESCRIBED[where]) == set(field_keys(model)), where


def test_every_registered_eval_and_parameter_and_operator_is_described() -> None:
    assert set(reference.EXAMPLES) == set(REGISTRY)
    for spec in REGISTRY.values():
        if spec.params_model is None:
            assert spec.name in reference.JUDGED_NOTES, spec.name
            continue
        for name in field_keys(spec.params_model):
            assert name in reference.PARAMETERS, (spec.name, name)
    assert set(reference.OPERATOR_NOTES) == set(OPERATORS)


def test_every_example_declaration_validates() -> None:
    """Friction 15: the skill's table wrote Evals in forms `validate` rejects."""
    import yaml

    from agentdiag.scenario.validate import ManifestFacts, validate_rendered

    evals = [
        yaml.safe_load(example)[0]
        for name, example in reference.EXAMPLES.items()
        if name not in {"guardrails", "forbidden_phrases"}
    ]
    suite = {
        "schema_version": 1,
        "target": "t",
        "scenarios": [{"id": "a", "title": "A", "turns": ["hi"], "evals": evals}],
    }
    report = validate_rendered(yaml.safe_dump(suite), Path("reference.yaml"))

    assert report.errors == []
    listed = validate_rendered(
        yaml.safe_dump(
            {**suite, "scenarios": [{**suite["scenarios"][0], "evals": ["forbidden_phrases"]}]}
        ),
        Path("reference.yaml"),
        facts=ManifestFacts(forbidden_listed=True),
    )
    assert listed.errors == [] and listed.warnings == []


def test_the_packaged_reference_is_the_rendering() -> None:
    """Regenerate with `python -c "from agentdiag.generate.reference import *; ..."`, or
    delete the file and run this test's fix: the text below."""
    assert PACKAGED.read_text(encoding="utf-8") == reference.render_scenario_reference()
