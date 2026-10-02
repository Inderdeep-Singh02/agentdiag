"""Seam 1: `agentdiag run --dry-run` says what a Run would execute and executes nothing.

A dry run is preflight and nothing after it (D32): the Manifest, the Suites, the selection
and the Adapter's construction are checked, and then it prints one line per selected
Scenario (id, kinds, tags, Suite), the not-run list with reasons and detail, and the
selection expression. It opens no Adapter session, calls no model, needs no credentials
and writes no Run directory (ticket 07; D31, ADR-0005 §3, §8).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.cli import app

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
SUITES = REPO / "tests" / "fixtures" / "suites"

runner = CliRunner()


def toy_root(tmp_path: Path) -> Path:
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs"))
    return root


def point_at(root: Path, target: str, tools: str = "TOOLS") -> None:
    """Swap the example's Target for a fake one: `target:make_target`, `target:<tools>`.
    The example's Connector reads the toy's deployed set, not the fake's, so it goes too."""
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["adapter"]["environments"]["local"] = {
        "factory": f"{target}:make_target",
        "tools": f"{target}:{tools}",
    }
    manifest.pop("connector", None)
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")


def with_selection_suite(root: Path) -> Path:
    """The example root with the committed selection Suite in place of the example's two."""
    shutil.copy(
        SUITES / "selection.yaml",
        root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "orders.yaml",
    )
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["suites"] = ["suites/orders.yaml"]
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    point_at(root, "tests.fakes.seeded_target")
    return root


def dry_run(root: Path, *arguments: str) -> Any:
    return runner.invoke(app, ["run", "--root", str(root), "--dry-run", *arguments])


def no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("anthropic.default_credentials", lambda **_: None)


def test_a_dry_run_prints_each_selected_scenario_the_not_run_list_and_the_expression(
    tmp_path: Path,
) -> None:
    root = with_selection_suite(toy_root(tmp_path))

    result = dry_run(root, "--tag", "orders")

    assert result.exit_code == 0, result.stdout
    assert result.stdout.splitlines() == [
        "ask-as-carmen  kinds one_shot  tags orders,lookup  suite orders",
        "cancel-without-a-tool  kinds one_shot,tool  tags orders,cancel  suite orders",
        "greet-the-desk  kinds one_shot  not run  not_selected",
        "refund-in-two-turns  kinds conversation  not run  suite_not_run  "
        "Refunds are frozen while the desk migrates its ledger.",
        "cancel-adaptively  kinds simulated  not run  suite_not_run  "
        "Adaptive, so it runs only when named with --scenario.",
        # Walkthrough friction 27: every selected declaration's parameters are parsed.
        "evals 1 declaration, every parameter parsed",
        "selection tag=orders",
        # The seeded fake calls no model, so the probe observes nothing (decision 14).
        "sync not_checked (adapter_cannot_observe)",
    ]


def test_a_dry_run_writes_no_run_directory(tmp_path: Path) -> None:
    root = with_selection_suite(toy_root(tmp_path))
    before = sorted(path.relative_to(root) for path in root.rglob("*"))

    result = dry_run(root)

    assert result.exit_code == 0, result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()
    assert sorted(path.relative_to(root) for path in root.rglob("*")) == before


def test_a_dry_run_with_no_flag_ends_with_the_expression_all(tmp_path: Path) -> None:
    result = dry_run(with_selection_suite(toy_root(tmp_path)))

    assert result.stdout.strip().splitlines()[-2:] == [
        "selection all",
        "sync not_checked (adapter_cannot_observe)",
    ]


def test_a_dry_run_needs_no_credentials_for_a_judged_eval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The example Suite declares `prompt_adherence`; a dry run calls no Judge (D16)."""
    root = toy_root(tmp_path)
    no_credentials(monkeypatch)

    result = dry_run(root, "--scenario", "cancel-processing-order")

    assert result.exit_code == 0, result.stdout
    assert result.stdout.splitlines()[0] == (
        "cancel-processing-order  kinds one_shot  tags orders,cancel  suite orders"
    )


def test_a_dry_run_opens_no_adapter_session(tmp_path: Path) -> None:
    """A Target whose factory raises at `open` is never built by a dry run."""
    root = toy_root(tmp_path)
    point_at(root, "tests.fakes.broken_target", tools="make_tools")

    result = dry_run(root, "--scenario", "cancel-processing-order")

    assert result.exit_code == 0, result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()


def test_a_dry_run_still_checks_the_adapter_can_be_built(tmp_path: Path) -> None:
    root = toy_root(tmp_path)
    point_at(root, "tests.fakes.no_such_target")

    result = dry_run(root, "--scenario", "cancel-processing-order")

    assert result.exit_code == 3, result.stdout
    assert "Adapter" in result.stdout


def test_a_dry_run_whose_selection_matches_nothing_exits_3_naming_the_flags(
    tmp_path: Path,
) -> None:
    root = with_selection_suite(toy_root(tmp_path))

    result = dry_run(root, "--tag", "greeting", "--scenario", "ask-as-carmen")

    assert result.exit_code == 3, result.stdout
    assert "--scenario ask-as-carmen --tag greeting" in result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()


def test_a_dry_run_names_a_scenario_whose_fixtures_the_adapter_cannot_apply(
    tmp_path: Path,
) -> None:
    root = with_selection_suite(toy_root(tmp_path))
    point_at(root, "tests.fakes.fixtureless_target")

    result = dry_run(root, "--tag", "orders")

    assert result.exit_code == 0, result.stdout
    assert "ask-as-carmen  kinds one_shot  not run  fixture_unavailable  " in result.stdout


def test_a_dry_run_with_json_prints_the_same_listing_as_json(tmp_path: Path) -> None:
    root = with_selection_suite(toy_root(tmp_path))

    result = runner.invoke(
        app, ["run", "--root", str(root), "--dry-run", "--json", "--tag", "orders"]
    )

    assert result.exit_code == 0, result.stdout
    listing = json.loads(result.stdout)
    assert listing["expression"] == "tag=orders"
    assert listing["selected"][0] == {
        "id": "ask-as-carmen",
        "suite": "orders",
        "kinds": ["one_shot"],
        "tags": ["orders", "lookup"],
    }
    assert [entry["id"] for entry in listing["selected"]] == [
        "ask-as-carmen",
        "cancel-without-a-tool",
    ]
    assert listing["not_run"][1] == {
        "scenario": "refund-in-two-turns",
        "suite": "orders",
        "reason": "suite_not_run",
        "detail": "Refunds are frozen while the desk migrates its ledger.",
        "trial": None,
        "kinds": ["conversation"],
    }
    assert [entry["scenario"] for entry in listing["not_run"]] == [
        "greet-the-desk",
        "refund-in-two-turns",
        "cancel-adaptively",
    ]


def test_friction_27_a_declaration_that_does_not_parse_fails_the_dry_run() -> None:
    """A dry run parses every selected Eval declaration as a Trial would, so a Scenario
    fault that would score `invalid` is found offline, before any Run."""
    from types import SimpleNamespace

    from agentdiag.run.execute import parsed_evals
    from agentdiag.scenario.models import Scenario

    scenario = Scenario.model_validate(
        {"id": "a", "title": "A", "turns": ["hi"], "evals": [{"eval": "goal"}]}
    )
    plan = SimpleNamespace(selected=[SimpleNamespace(scenario=scenario)])

    count, problems = parsed_evals(plan)  # type: ignore[arg-type]

    assert count == 1
    (problem,) = problems
    assert problem.startswith("error: Scenario 'a': goal: ")


def test_a_dry_run_refuses_an_absolute_prompt_pointer_as_validate_does(tmp_path: Path) -> None:
    """ADR-0015 §2: `run` refuses what `validate` refuses, in `validate`'s words."""
    root = toy_root(tmp_path)
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["prompts"] = {"system": "/etc/system.md"}
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    problem = (
        "prompts.system: the prompt file /etc/system.md is absolute; a Manifest pointer is "
        "relative to the Target directory .agentdiag/targets/toy-order-desk"
    )

    checked = runner.invoke(app, ["validate", "--root", str(root)])
    result = dry_run(root, "--scenario", "cancel-processing-order")

    assert f"error: {path}: {problem}" in checked.stdout.splitlines()
    assert result.exit_code == 3, result.stdout
    assert problem in result.stdout.splitlines(), result.stdout


def test_a_dry_run_of_a_pending_target_refuses_with_validates_lines(tmp_path: Path) -> None:
    """ADR-0016 §4: a Target `init --target` described and nothing yet drives is refused by
    name, beside the draft-only Suite list, each line word for word as `validate` warns it,
    and no Adapter is built or Run written."""
    root = tmp_path / "shop"
    made = runner.invoke(app, ["init", "--root", str(root), "--target", "desk"])
    assert made.exit_code == 0, made.output
    validated = runner.invoke(app, ["validate", "--root", str(root)])
    manifest = root / ".agentdiag" / "targets" / "desk" / "manifest.yaml"
    warned = [
        line.removeprefix(f"warning: {manifest}: ")
        for line in validated.stdout.splitlines()
        if line.startswith("warning:") and "REVIEW;" not in line
    ]

    result = runner.invoke(app, ["run", "--root", str(root), "--dry-run"])

    assert result.exit_code == 3, result.output
    assert warned == [
        "adapter.kind: pending: nothing drives this Target yet; set the Adapter kind and its "
        "environment block (the REVIEW lines in manifest.yaml name what to fill)",
        "suites: no runnable Suite: suites/sample.yaml is draft; settle its Scenarios and drop "
        "status: draft from its entry to run them",
    ]
    assert result.output.splitlines() == warned
    assert not (root / ".agentdiag" / "targets" / "desk" / "runs").exists()


def test_a_dry_run_whose_only_suite_is_a_draft_is_refused_by_name(tmp_path: Path) -> None:
    """The ticket 46 amendment: a Run that drives, over a Manifest whose every Suite is a
    draft, is refused rather than recording a Run of nothing; a retired one is named too."""
    root = toy_root(tmp_path)
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["suites"] = [
        {"path": "suites/orders.yaml", "status": "draft"},
        {"path": "suites/guardrails.yaml", "status": "retired"},
    ]
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    result = runner.invoke(app, ["run", "--root", str(root), "--dry-run"])

    assert result.exit_code == 3, result.output
    assert (
        "suites: no runnable Suite: suites/orders.yaml is draft; settle its Scenarios and drop "
        "status: draft from its entry to run them. suites/guardrails.yaml is retired; a "
        "retired Suite never runs"
    ) in result.output.splitlines()
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()
