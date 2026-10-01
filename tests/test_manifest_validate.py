"""Seam 1: the Manifest v1 and `agentdiag validate` checking it first (ticket 10, decision 8).

Every rule has a failing (or warning) fixture and a passing one under
`tests/fixtures/manifests/`, named for the rule; each is placed as the Manifest of a Phase 4
root holding the example's Suites and Calibration Notes. The failing one must name what is
wrong and where; the passing one must say nothing at all. A warning exits 0.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.run.manifest import (
    Manifest,
    ManifestError,
    PromptPointer,
    load_manifest,
    manifest_report,
)
from agentdiag.types import PROTECTED_BY_DEFAULT
from agentdiag.workspace import Workspace

REPO = Path(__file__).resolve().parents[1]
MANIFESTS = REPO / "tests" / "fixtures" / "manifests"
EXAMPLE = REPO / "examples" / "toy"

runner = CliRunner()

ERRORS = {
    "prompt-pointer-missing": ("prompts.rules", "the prompt file prompts/rules.md does not exist"),
    "tool-schema-missing": (
        "tools.lookup_order.schema",
        "the tool schema file tools/lookup_order.json does not exist",
    ),
    "suite-missing": ("suites[1]", "the Suite suites/nowhere.yaml does not exist"),
    "suppression-window": ("suppressions[0]", "ends on 2026-09-01 before it starts on 2026-09-30"),
    # Walkthrough friction 5: validate printed `0 errors` for each of these.
    "side-effects-unknown": (
        "adapter.side_effects",
        "'bogus' is not a side-effect class; it is one of none, sandboxed, live",
    ),
    "environment-side-effects-unknown": (
        "adapter.environments.local.side_effects",
        "'bogus' is not a side-effect class; it is one of none, sandboxed, live",
    ),
    "tool-side-effects-unknown": (
        "tools.cancel_order.side_effects",
        "Input should be 'none', 'sandboxed' or 'live'",
    ),
    "schema-version": ("schema_version", "agentdiag reads Manifest schema_version 1, not 7"),
    "adapter-kind-unknown": (
        "adapter.kind",
        "no Adapter of kind 'bogus' is installed (installed: http, inprocess)",
    ),
    "connector-kind-unknown": (
        "connector.kind",
        "no Connector of kind 'bogus' is installed (installed: inprocess)",
    ),
    "reference-module-missing": (
        "adapter.environments.local.factory",
        "no module agentdiag.examples.nowhere can be found",
    ),
    "reference-attribute-missing": (
        "adapter.environments.local.factory",
        "agentdiag.examples.toy binds no 'nope' at its top level",
    ),
    "reference-malformed": (
        "adapter.environments.local.factory",
        "'make_target' is not a `module:attr` reference",
    ),
    "deployed-module-missing": (
        "connector.environments.local.deployed",
        "no module agentdiag.examples.nowhere can be found",
    ),
    "evidence-kind-unknown": (
        "connector.evidence.chats",
        "'chats' is not an Evidence store kind; the kinds are proxy, conversation, voice, flows",
    ),
    "evidence-rows-missing": (
        "connector.evidence.proxy",
        "names no `rows: module:attr`",
    ),
}
"""Rule -> (the path the error is reported at, a fragment of its message)."""

WARNINGS = {
    "local-only-pointer-missing": (
        "prompts.rules",
        "the prompt file prompts/rules.local.md does not exist",
    ),
    "suppression-mechanical": ("suppressions[0]", "names the mechanical Eval 'must_not_say'"),
}


def root_with(tmp_path: Path, fixture: str) -> Path:
    """A Phase 4 root whose Manifest is the fixture, beside the example's Suites and notes."""
    root = tmp_path / "toy"
    directory = root / ".agentdiag" / "targets" / "toy-order-desk"
    shutil.copytree(
        EXAMPLE / ".agentdiag" / "targets" / "toy-order-desk" / "suites", directory / "suites"
    )
    shutil.copytree(MANIFESTS / "suites", directory / "suites", dirs_exist_ok=True)
    shutil.copy(
        EXAMPLE / ".agentdiag" / "targets" / "toy-order-desk" / "judge_notes.md",
        directory / "judge_notes.md",
    )
    shutil.copy(MANIFESTS / f"{fixture}.yaml", directory / "manifest.yaml")
    return root


def validate(root: Path) -> object:
    return runner.invoke(app, ["validate", "--root", str(root)])


@pytest.mark.parametrize("rule", sorted(ERRORS))
def test_a_failing_manifest_names_the_rule_it_breaks_where_it_breaks_it(
    rule: str, tmp_path: Path
) -> None:
    root = root_with(tmp_path, f"{rule}.fail")
    where, fragment = ERRORS[rule]

    result = validate(root)

    assert result.exit_code == 3, result.stdout
    lines = [line for line in result.stdout.splitlines() if line.startswith("error:")]
    assert any(f": {where}: " in line and fragment in line for line in lines), result.stdout


@pytest.mark.parametrize("rule", sorted(WARNINGS))
def test_a_warning_manifest_names_the_rule_and_still_exits_0(rule: str, tmp_path: Path) -> None:
    root = root_with(tmp_path, f"{rule}.warn")
    where, fragment = WARNINGS[rule]

    result = validate(root)

    assert result.exit_code == 0, result.stdout
    lines = [line for line in result.stdout.splitlines() if line.startswith("warning:")]
    assert any(f": {where}: " in line and fragment in line for line in lines), result.stdout


@pytest.mark.parametrize(
    "rule", sorted({*ERRORS, *WARNINGS, "suite-status-unknown", "latency-default"})
)
def test_the_passing_manifest_of_every_rule_says_nothing(rule: str, tmp_path: Path) -> None:
    result = validate(root_with(tmp_path, f"{rule}.pass"))

    assert result.exit_code == 0, result.stdout
    assert "error:" not in result.stdout
    assert "warning:" not in result.stdout


def test_an_unknown_suite_status_is_an_error_naming_the_entry(tmp_path: Path) -> None:
    result = validate(root_with(tmp_path, "suite-status-unknown.fail"))

    assert result.exit_code == 3
    (line,) = [line for line in result.stdout.splitlines() if line.startswith("error:")]
    assert line.endswith(
        "manifest.yaml: suites.0.status: Input should be 'runnable', 'draft' or 'retired' "
        "(it is 'paused')"
    )
    assert (
        result.stdout.splitlines()[-1] == "validated the Manifest and 0 Suites: 1 error, 0 warnings"
    )


def test_a_retired_suite_is_named_as_skipped_and_never_read(tmp_path: Path) -> None:
    result = validate(root_with(tmp_path, "suite-missing.pass"))

    assert result.exit_code == 0, result.stdout
    assert any(
        line.startswith("skipped: ") and line.endswith("nowhere.yaml: Suite status: retired")
        for line in result.stdout.splitlines()
    )
    assert result.stdout.splitlines()[-1] == (
        "validated the Manifest and 1 Suite: 0 errors, 0 warnings"
    )


def test_a_draft_suite_is_validated(tmp_path: Path) -> None:
    result = validate(root_with(tmp_path, "suite-status-unknown.pass"))

    assert result.stdout.splitlines()[-1] == (
        "validated the Manifest and 1 Suite: 0 errors, 0 warnings"
    )


def test_a_latency_eval_without_a_threshold_is_an_error_only_without_a_manifest_default(
    tmp_path: Path,
) -> None:
    refused = validate(root_with(tmp_path / "a", "latency-default.fail"))
    allowed = validate(root_with(tmp_path / "b", "latency-default.pass"))

    assert refused.exit_code == 3
    assert "scenarios[0].evals[0].threshold: response_latency" in refused.stdout
    assert allowed.exit_code == 0, allowed.stdout


# --- the model (decision 8) ---


def manifest(**blocks: object) -> Manifest:
    document: dict[str, object] = {
        "target": {"name": "t"},
        "adapter": {"kind": "inprocess", "environments": {"default": "local", "local": {}}},
    }
    document.update(blocks)
    return Manifest.model_validate(document)


def test_the_example_manifest_still_loads_and_names_its_system_prompt_observed() -> None:
    loaded = load_manifest(Workspace.find(EXAMPLE).resolve(None))

    assert loaded.prompts == {"system": "observed"}
    assert loaded.suite_paths == ["suites/orders.yaml", "suites/guardrails.yaml"]
    assert loaded.records == "changes"


def test_bare_strings_are_a_path_pointer_a_runnable_suite_and_a_data_source_identity() -> None:
    loaded = manifest(
        prompts={"system": "observed", "rules": "prompts/rules.md"},
        suites=["suites/a.yaml", {"path": "suites/b.yaml", "status": "draft"}],
        data_sources={"orders": "sqlite:///orders.db", "kb": {"identity": "kb", "kind": "index"}},
        tools={"lookup_order": {"schema": "tools/lookup.json", "side_effects": "sandboxed"}},
    )

    assert loaded.prompts["rules"] == PromptPointer(path="prompts/rules.md")
    assert [(entry.path, entry.status) for entry in loaded.suites] == [
        ("suites/a.yaml", "runnable"),
        ("suites/b.yaml", "draft"),
    ]
    assert loaded.suite_paths == ["suites/a.yaml"]
    assert loaded.data_sources["orders"].identity == "sqlite:///orders.db"
    assert loaded.tools["lookup_order"].schema_ == PromptPointer(path="tools/lookup.json")
    assert loaded.model_dump(mode="json")["tools"]["lookup_order"]["schema"] == {
        "path": "tools/lookup.json",
        "local_only": False,
    }


def test_prod_staging_and_eu_prod_are_protected_whatever_their_block_or_case() -> None:
    """ADR-0011 §6d, the ticket 27 fix round: the default names are protected in any case,
    and `protected: false` cannot unprotect one; any other environment is protected when
    its block says so."""
    environments = {
        "default": "local",
        "local": {},
        "prod": {},
        "staging": {"protected": False},
        "EU_Prod": {},
        "Staging-2": {},
        "dev": {"protected": True},
    }
    loaded = manifest(adapter={"kind": "inprocess", "environments": environments})

    assert frozenset({"prod", "staging", "eu_prod"}) == PROTECTED_BY_DEFAULT
    assert [name for name in loaded.adapter.environment_names if loaded.is_protected(name)] == [
        "prod",
        "staging",
        "EU_Prod",
        "dev",
    ]


def test_protected_false_on_a_default_name_is_a_validate_error() -> None:
    from agentdiag.run.manifest_checks import manifest_problems

    environments = {"default": "local", "local": {}, "Prod": {"protected": False}}
    loaded = manifest(adapter={"kind": "inprocess", "environments": environments})

    problems = dict(manifest_problems(loaded))
    assert "adapter.environments.Prod.protected" in problems
    assert "cannot unprotect it" in problems["adapter.environments.Prod.protected"]
    assert loaded.is_protected("Prod")


def test_a_store_outside_the_target_directory_is_a_validate_error() -> None:
    from agentdiag.inprocess_rules import store_problem

    assert store_problem("platform/local.json") is None
    for outside in ("../platform.json", "/tmp/local.json", "platform/../../x.json", "", 7):
        assert store_problem(outside) is not None, outside


def test_an_environment_raises_the_side_effect_class_and_never_lowers_it() -> None:
    environments = {
        "default": "local",
        "local": {},
        "sandbox": {"side_effects": "sandboxed"},
        "prod": {"side_effects": "live"},
    }
    loaded = manifest(
        adapter={"kind": "inprocess", "side_effects": "sandboxed", "environments": environments}
    )

    assert loaded.side_effects_of("local") == "sandboxed"
    assert loaded.side_effects_of("sandbox") == "sandboxed"
    assert loaded.side_effects_of("prod") == "live"
    with pytest.raises(ManifestError, match="side_effects 'wild'"):
        manifest(
            adapter={
                "kind": "inprocess",
                "environments": {"default": "local", "local": {"side_effects": "wild"}},
            }
        ).side_effects_of("local")


def test_a_suppression_reads_its_window_as_dates_and_writes_from_back_as_from() -> None:
    loaded = manifest(
        suppressions=[
            {
                "id": "sup-001",
                "eval": "*",
                "from": "2026-09-01",
                "until": "2026-09-30",
                "pattern": "p",
                "why": "w",
            }
        ]
    )

    (suppression,) = loaded.suppressions
    assert (suppression.from_, suppression.until) == (date(2026, 9, 1), date(2026, 9, 30))
    assert loaded.model_dump(mode="json")["suppressions"][0]["from"] == "2026-09-01"


def test_the_latency_block_is_each_latency_evals_default_threshold() -> None:
    loaded = manifest(
        eval_parameters={
            "latency": {"response_max_ms": 30000, "first_token_max_ms": 5000, "tool_max_ms": 1000}
        }
    )

    assert loaded.latency_thresholds == {
        "response_latency": {"max_ms": 30000},
        "first_token_latency": {"max_ms": 5000},
        "tool_latency": {"max_ms": 1000},
    }
    assert manifest().latency_thresholds == {}
    assert manifest().tool_argument_types is None


def test_a_manifest_report_of_a_manifest_that_does_not_load_is_one_error(tmp_path: Path) -> None:
    root = root_with(tmp_path, "suite-status-unknown.fail")

    report, loaded = manifest_report(Workspace.find(root).resolve(None))

    assert loaded is None
    assert len(report.errors) == 1


def test_friction_5_a_reference_is_found_without_importing_its_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The module check locates `module:attr` by asking the import finders and parsing the
    source: a package whose `__init__` raises is found, its attribute checked, never run."""
    import sys

    from agentdiag.reference import reference_problem

    package = tmp_path / "site" / "tripwire_pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("raise RuntimeError('imported')\n", encoding="utf-8")
    (package / "target.py").write_text(
        "raise RuntimeError('imported')\ndef make():\n    pass\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path / "site"))

    assert reference_problem("tripwire_pkg.target:make") is None
    assert "binds no 'other'" in str(reference_problem("tripwire_pkg.target:other"))
    assert "no module tripwire_pkg.absent" in str(reference_problem("tripwire_pkg.absent:x"))
    assert "tripwire_pkg" not in sys.modules


def test_defect_2_a_module_a_meta_path_finder_serves_is_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An editable install is served by a finder on `sys.meta_path`, not by a path entry:
    asking `PathFinder` alone called such a module missing."""
    import sys
    from importlib.machinery import ModuleSpec

    class Served:
        def __init__(self) -> None:
            self.asked: list[str] = []

        def find_spec(self, name: str, path: object, target: object = None) -> ModuleSpec | None:
            self.asked.append(name)
            return ModuleSpec(name, None) if name == "served_only" else None

    finder = Served()
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])

    from agentdiag.reference import reference_problem

    assert reference_problem("served_only:make") is None
    assert reference_problem("served_only.deeper.module:make") is None
    assert "no module never_served" in str(reference_problem("never_served:make"))
    assert "served_only" not in sys.modules
    assert finder.asked[:1] == ["served_only"]


def test_defect_1_validate_loads_no_plugin_and_asks_only_an_imported_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plugin's kind is checked from entry-point metadata; its class, and with it the
    plugin's SDK, is never loaded by `validate`. Its own `validate_section` is asked only
    when its module is already imported, and core's in-process rule for `tools:` never
    fails it."""
    from agentdiag.connector import plugins
    from agentdiag.run import manifest_checks

    loaded: list[str] = []

    class Point:
        def __init__(self, name: str, module: str) -> None:
            self.name, self.module, self.value, self.dist = name, module, f"{module}:X", None

        def load(self) -> object:
            loaded.append(self.name)
            return type("Plugin", (), {"validate_section": staticmethod(lambda s: [("x", "own")])})

    points = {plugins.ADAPTER_GROUP: {"remote": Point("remote", "not_imported_plugin")}}
    monkeypatch.setattr(plugins, "installed", lambda group: points.get(group, {}))
    manifest = Manifest.model_validate(
        {
            "target": {"name": "t"},
            "adapter": {
                "kind": "remote",
                "environments": {"default": "prod", "prod": {"tools": "all of them"}},
            },
        }
    )

    assert manifest_checks.manifest_problems(manifest) == []
    assert loaded == []
    points[plugins.ADAPTER_GROUP]["remote"].module = "json"  # already imported
    assert manifest_checks.manifest_problems(manifest) == [("x", "own")]
    assert loaded == ["remote"]


def test_validate_of_a_manifest_imports_no_sdk_and_no_target_code() -> None:
    """`validate` over the help desk's Manifest (a Connector, `module:attr` references)
    imports neither the SDK nor the modules its references name."""
    import subprocess
    import sys

    probe = (
        "import sys\nfrom agentdiag.cli import app\n"
        "try:\n    app(['validate', '--root', 'examples/workspace', '--target', 'help-desk'])\n"
        "except SystemExit as exit:\n    assert exit.code == 0, exit.code\n"
        "leaked = sorted(m for m in sys.modules if m == 'anthropic' or m.startswith(("
        "'anthropic.', 'agentdiag.model', 'agentdiag.examples')))\n"
        "print('leaked:' + ','.join(leaked))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=REPO
    )
    assert completed.stdout.strip().splitlines()[-1] == "leaked:"
