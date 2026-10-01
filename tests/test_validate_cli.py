"""Seam 1: `agentdiag validate` over Suite files, as an author sees it (ticket 03).

Every rule has a failing fixture and a passing one under `tests/fixtures/suites/`, named
for the rule. The failing one must name what is wrong and where; the passing one must say
nothing at all. Warnings are the same with exit 0 (D19).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.scenario import load_suite, validate_suite

REPO = Path(__file__).resolve().parents[1]
SUITES = REPO / "tests" / "fixtures" / "suites"
EXAMPLE = REPO / "examples" / "toy"
SUITE_SCHEMA = REPO / "schemas" / "suite.schema.json"

EXAMPLE_ORDERS = EXAMPLE / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "orders.yaml"

runner = CliRunner()

ERRORS = {
    "duplicate-scenario-ids": ("scenarios[1].id", "duplicate Scenario id 'cancel-order'"),
    "simulate-without-stop-criterion": (
        "scenarios[0].turns[1].simulate",
        "exactly one of stop_when or stop_token; this one declares neither",
    ),
    "simulate-with-both-stop-criteria": (
        "scenarios[0].turns[0].simulate",
        "exactly one of stop_when or stop_token; this one declares both",
    ),
    "stop-when-two-predicates": (
        "scenarios[0].turns[0].simulate.stop_when",
        "stop_when holds exactly one of",
    ),
    "simulate-without-max-turns": ("scenarios[0]", "declares max_turns"),
    "simulate-turn-not-last": (
        "scenarios[0].turns[2]",
        "a Scenario has one simulate Turn and it is the last",
    ),
    "two-simulate-turns": (
        "scenarios[0].turns[1]",
        "a Scenario has one simulate Turn and it is the last",
    ),
    "stop-when-empty-phrases": (
        "scenarios[0].turns[0].simulate.stop_when.target_says_any",
        "at least 1 item",
    ),
    "max-turns-without-simulate": ("scenarios[0]", "max_turns can only equal the Turn count"),
    "focus-names-nothing": ("scenarios[0].focus", "focuses on 'G9'"),
    "fixture-names-nothing": ("scenarios[0].fixtures[1]", "names Fixture 'csaba'"),
    "continues-names-nothing": ("scenarios[1].continues", "which is no Scenario here"),
    "continues-a-later-scenario": ("scenarios[0].continues", "must come first in the Suite"),
    "continuing-scenario-changes-fixtures": (
        "scenarios[1].fixtures",
        "a continuer's fixtures are empty or exactly those",
    ),
    "authored-inherited": ("scenarios[0].evals[0]", "`inherited` is set by the loader"),
    "authored-kind": ("scenarios[0].kind", "`kind` is derived from the Turns and the Evals"),
    "guardrails-without-rule-ids": (
        "scenarios[0].evals[0]",
        "write `inherit_suite_evals: false`",
    ),
    "guardrail-id-not-in-suite-rules": ("scenarios[0].evals[0]", "guardrail rule 'G3'"),
    "not-run-names-nothing": ("not_run.cancel-refunded-order", "is no Scenario in this Suite"),
    "legacy-eval-spelling": ("scenarios[0].evals[0].eval", "docs/scenario-schema.md"),
    "unknown-key": ("scenarios[0].max_turn", "carry it under `extras`"),
    "params-expect-tools": ("scenarios[0].evals[0].tools", "at least 1 item"),
    "params-expect-tools-order": ("scenarios[0].evals[0].turn", "greater than or equal to 1"),
    "params-expect-tools-any": ("scenarios[0].evals[0].tols", "Extra inputs are not permitted"),
    "params-forbid-tools": ("scenarios[0].evals[0].tools", "at least 1 item"),
    "params-tool-count-max": (
        "scenarios[0].evals[0].counts.lookup_order",
        "greater than or equal to 0",
    ),
    "params-expect-tool-args": (
        "scenarios[0].evals[0].args.order_id.equal",
        "Extra inputs are not permitted",
    ),
    "params-must-say-any": ("scenarios[0].evals[0].phrases[0]", "valid string"),
    "params-must-not-say": ("scenarios[0].evals[0].phrases", "at least 1 item"),
    "params-forbidden-phrases": ("scenarios[0].evals[0].turn", "valid integer"),
    "params-tool-latency": ("scenarios[0].evals[0].threshold.max_ms", "Field required"),
    "params-response-latency": (
        "scenarios[0].evals[0].threshold.max_first_token_ms",
        "Extra inputs are not permitted",
    ),
    "params-first-token-latency": (
        "scenarios[0].evals[0].threshold.max_ms",
        "greater than or equal to 0",
    ),
    "params-goal": ("scenarios[0].evals[0].expected", "Field required"),
    "params-guardrails": ("scenarios[0].evals[0].focus", "Extra inputs are not permitted"),
    "params-tool-argument-types": (
        "scenarios[0].evals[0].types.lookup_order.order_id",
        "Input should be 'string'",
    ),
}
"""Rule -> (the path the error is reported at, a fragment of its message)."""

WARNINGS = {
    "unknown-eval-name": ("scenarios[0].evals[0]", "unknown Eval 'tone_of_voice'"),
    "unregistered-short-form-value": ("scenarios[0].evals[0]", "carried as params.value"),
    "guardrail-rule-without-text": (
        "evals[0].rules[0]",
        "guardrail rule 'constraint:no_invented_data' has no text",
    ),
}

PARAMETERS = "an Eval's parameters depend on its name in the registry, beyond JSON Schema"

CROSS_FIELD = {
    "duplicate-scenario-ids": "uniqueness across list items is beyond JSON Schema",
    "focus-names-nothing": "focus names ids declared elsewhere in the Suite",
    "fixture-names-nothing": "Fixture names point into the Suite's `fixtures`",
    "continues-names-nothing": "continues names another Scenario",
    "continues-a-later-scenario": "continues depends on Scenario order",
    "continuing-scenario-changes-fixtures": "compares two Scenarios' fixtures",
    "not-run-names-nothing": "not_run keys point at Scenario ids",
    "guardrail-id-not-in-suite-rules": "rule ids point into the Suite-level rules",
    "guardrails-without-rule-ids": "only a Scenario-level guardrails must name ids",
    "max-turns-without-simulate": "comparing max_turns with the Turn count is beyond JSON Schema",
    "simulate-turn-not-last": "where a simulate Turn sits in the list is beyond this schema",
    "two-simulate-turns": "counting simulate Turns in the list is beyond this schema",
    "params-expect-tools": PARAMETERS,
    "params-expect-tools-order": PARAMETERS,
    "params-expect-tools-any": PARAMETERS,
    "params-forbid-tools": PARAMETERS,
    "params-tool-count-max": PARAMETERS,
    "params-expect-tool-args": PARAMETERS,
    "params-must-say-any": PARAMETERS,
    "params-must-not-say": PARAMETERS,
    "params-forbidden-phrases": PARAMETERS,
    "params-tool-latency": PARAMETERS,
    "params-response-latency": PARAMETERS,
    "params-first-token-latency": PARAMETERS,
    "params-goal": PARAMETERS,
    "params-guardrails": PARAMETERS,
    "params-tool-argument-types": PARAMETERS,
}
"""Failing fixtures whose rule relates one object to another, so only `validate` can see
it; every other failing fixture must be refused by the published schema too."""


def validate(*arguments: str) -> object:
    return runner.invoke(app, ["validate", *arguments])


def suite(name: str, kind: str) -> Path:
    for suffix in (".yaml", ".json"):
        path = SUITES / f"{name}.{kind}{suffix}"
        if path.exists():
            return path
    raise FileNotFoundError(name)


# --- one failing and one passing fixture per rule ---


@pytest.mark.parametrize("rule", sorted(ERRORS))
def test_a_suite_breaking_a_rule_is_an_error_naming_where_and_exits_3(rule: str) -> None:
    path = suite(rule, "fail")
    where, fragment = ERRORS[rule]

    result = validate(str(path))

    assert result.exit_code == 3, result.output
    errors = [line for line in result.stdout.splitlines() if line.startswith("error: ")]
    assert any(
        line.startswith(f"error: {path}: {where}: ") and fragment in line for line in errors
    ), result.stdout


@pytest.mark.parametrize("rule", sorted({*ERRORS, *WARNINGS}))
def test_a_suite_keeping_the_rule_validates_with_nothing_to_say(rule: str) -> None:
    path = suite(rule, "pass")

    result = validate(str(path))

    assert result.exit_code == 0, result.stdout
    assert "error:" not in result.stdout
    assert "warning:" not in result.stdout
    assert result.stdout.splitlines()[-1] == "validated 1 Suite: 0 errors, 0 warnings"


@pytest.mark.parametrize("rule", sorted(WARNINGS))
def test_a_warning_is_printed_and_the_suite_still_validates(rule: str) -> None:
    path = suite(rule, "warn")
    where, fragment = WARNINGS[rule]

    result = validate(str(path))

    assert result.exit_code == 0, result.stdout
    assert "error:" not in result.stdout
    assert any(
        line.startswith(f"warning: {path}: {where}: ") and fragment in line
        for line in result.stdout.splitlines()
    ), result.stdout


def test_a_legacy_suite_is_refused_with_one_error_naming_the_schema() -> None:
    path = suite("legacy-shape", "fail")

    result = validate(str(path))

    assert result.exit_code == 3
    assert f"error: {path}: this is a legacy extended Suite shape" in result.stdout
    assert "docs/scenario-schema.md in the agentdiag repository" in result.stdout


# --- the command as a whole ---


def test_every_error_in_a_file_is_reported_in_one_pass(tmp_path: Path) -> None:
    """An author fixing a Suite wants the whole list from one command."""
    document = {
        "schema_version": 1,
        "target": "toy-order-desk",
        "not_run": {"nowhere": "gone"},
        "scenarios": [
            {"id": "a", "title": "A", "turns": ["hi"], "fixtures": ["missing"]},
            {"id": "a", "title": "A again", "turns": ["hi"], "continues": "nobody"},
        ],
    }
    path = tmp_path / "many.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    result = validate(str(path))

    assert result.exit_code == 3
    assert result.stdout.count("error: ") == 4, result.stdout
    assert result.stdout.splitlines()[-1] == "validated 1 Suite: 4 errors, 0 warnings"


def test_several_files_are_validated_together_and_one_error_fails_them_all() -> None:
    good, bad = suite("unknown-eval-name", "pass"), suite("legacy-eval-spelling", "fail")

    result = validate(str(good), str(bad))

    assert result.exit_code == 3
    assert result.stdout.splitlines()[-1] == "validated 2 Suites: 1 error, 0 warnings"


def test_with_no_path_every_suite_the_manifest_names_is_validated(tmp_path: Path) -> None:
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs"))

    result = validate("--root", str(root))

    assert result.exit_code == 0, result.stdout
    assert result.stdout.splitlines()[-1] == (
        "validated the Manifest and 2 Suites: 0 errors, 0 warnings"
    )


def test_with_no_path_and_no_manifest_validate_says_so_and_exits_3(tmp_path: Path) -> None:
    result = validate("--root", str(tmp_path))

    assert result.exit_code == 3
    assert "error:" in result.stderr


def test_a_file_that_is_not_yaml_is_one_error_not_a_crash(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("scenarios: [unclosed", encoding="utf-8")

    result = validate(str(path))

    assert result.exit_code == 3
    assert f"error: {path}: not valid yaml" in result.stdout


# --- offline, and in step with the published schema ---


def test_the_scenario_package_imports_no_model_client_and_no_sdk() -> None:
    """`validate` runs with no network and no credentials (ticket 03), and the
    catalogue they read names the judged Evals without importing a prompt module, each of
    which imports the Judge (ticket 05)."""
    probe = (
        "import sys, agentdiag.scenario, agentdiag.scenario.render, agentdiag.scenario.select, "
        "agentdiag.eval.registry, agentdiag.eval.notes; "
        "leaked = sorted(m for m in sys.modules "
        "if m == 'anthropic' or m.startswith(('anthropic.', 'agentdiag.model')) "
        "or m in ('agentdiag.eval.judge', 'agentdiag.eval.judged', 'agentdiag.eval.goal')); "
        "print(','.join(leaked))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=REPO
    )

    assert completed.stdout.strip() == ""


def test_the_validate_command_never_loads_the_sdk_or_a_model_client() -> None:
    """The process `agentdiag validate` runs in, not just the package, stays offline."""
    probe = (
        "import sys\n"
        "from agentdiag.cli import app\n"
        "try:\n"
        f"    app(['validate', {str(EXAMPLE_ORDERS)!r}])\n"
        "except SystemExit as exit:\n"
        "    assert exit.code == 0, exit.code\n"
        "leaked = sorted(m for m in sys.modules "
        "if m == 'anthropic' or m.startswith(('anthropic.', 'agentdiag.model')))\n"
        "print('leaked:' + ','.join(leaked))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=REPO
    )

    assert "leaked:\n" in completed.stdout, completed.stdout


def every_suite_the_models_accept() -> list[Path]:
    candidates = [
        *sorted(SUITES.iterdir()),
        EXAMPLE / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "orders.yaml",
        EXAMPLE / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "guardrails.yaml",
    ]
    return [path for path in candidates if validate_suite(path).ok]


def test_every_suite_the_models_accept_is_accepted_by_the_published_schema() -> None:
    """Decision 15: the committed `suite.schema.json` and `validate` cannot drift apart."""
    schema = json.loads(SUITE_SCHEMA.read_text(encoding="utf-8"))
    accepted = every_suite_the_models_accept()
    assert len(accepted) > 20

    for path in accepted:
        text = path.read_text(encoding="utf-8")
        document = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
        jsonschema.validate(document, schema)


def single_object_failures() -> list[Path]:
    return sorted(
        path for path in SUITES.glob("*.fail.*") if path.name.split(".fail.")[0] not in CROSS_FIELD
    )


def test_every_single_object_rule_is_refused_by_the_published_schema_too() -> None:
    """Decision 15 the other way round: what one object gets wrong, an editor sees too."""
    schema = json.loads(SUITE_SCHEMA.read_text(encoding="utf-8"))
    failures = single_object_failures()
    assert len(failures) >= 8

    for path in failures:
        text = path.read_text(encoding="utf-8")
        document = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(document, schema)
        assert not validate_suite(path).ok, path.name


def test_every_cross_field_exemption_names_a_failing_fixture() -> None:
    names = {path.name.split(".fail.")[0] for path in SUITES.glob("*.fail.*")}

    assert set(CROSS_FIELD) <= names


def schema_doc_examples() -> list[str]:
    """The complete Suites under the schema doc's "Examples, one per kind" heading."""
    text = (REPO / "docs" / "scenario-schema.md").read_text(encoding="utf-8")
    section = text.split("## Examples, one per kind", 1)[1].split("\n## ", 1)[0]
    return [block.split("```", 1)[0] for block in section.split("```yaml\n")[1:]]


def test_every_example_in_the_schema_doc_validates_and_is_the_kind_it_claims(
    tmp_path: Path,
) -> None:
    examples = schema_doc_examples()
    assert len(examples) == 4

    kinds = []
    for number, example in enumerate(examples):
        path = tmp_path / f"example-{number}.yaml"
        path.write_text(example, encoding="utf-8")
        result = validate(str(path))
        assert result.exit_code == 0, result.stdout
        assert "warning:" not in result.stdout
        kinds.append(load_suite(path)[0].scenarios[0].kinds)

    assert [sorted(k) for k in kinds] == [
        ["one_shot"],
        ["conversation"],
        ["simulated"],
        ["one_shot", "tool"],
    ]


def test_friction_16_an_unknown_key_names_the_keys_there_are_and_a_param_error_the_params(
    tmp_path: Path,
) -> None:
    """Walkthrough friction 16: `generate --check` named a bad key and never the allowed
    ones, so each Eval parameter name took a probe."""
    from agentdiag.scenario.validate import validate_rendered

    suite = """schema_version: 1
target: t
scenarios:
  - id: a
    title: A
    turns: [hi]{extra}
    evals:
      - {{eval: must_say_any, params: {{words: [x]}}}}
"""
    unknown = validate_rendered(suite.format(extra="\n    must_say_any: [x]"), tmp_path / "s.yaml")
    params = validate_rendered(suite.format(extra=""), tmp_path / "s.yaml")

    (message,) = [problem.message for problem in unknown.errors]
    assert "unknown key 'must_say_any'" in message
    assert "the keys here are id, title, tags, provenance, notes" in message
    assert all(p.message.endswith("; it takes phrases, turn") for p in params.errors)
    assert params.errors


def test_second_walk_5_a_suite_naming_another_target_is_a_warning(tmp_path: Path) -> None:
    """Second walk friction 5: a Suite whose `target` was not the Manifest's name passed."""
    root = tmp_path / "shop"
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0
    suite = root / ".agentdiag" / "targets" / "default" / "suites" / "sample.yaml"
    suite.write_text(
        suite.read_text(encoding="utf-8").replace("target: toy-order-desk", "target: helpdesk"),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", "--root", str(root)])

    assert result.exit_code == 0, result.stdout
    (line,) = [line for line in result.stdout.splitlines() if line.startswith("warning:")]
    assert line.endswith(
        "target: the Suite names the Target 'helpdesk' and the Manifest names 'toy-order-desk'; "
        "a Report and a comparison print the Manifest's"
    )
