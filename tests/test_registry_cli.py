"""Seam 1: `agentdiag registry` and `agentdiag target show`, derived from the Manifests.

The Registry is a projection, never a file (phase-6 decision 5): these tests read what the
two commands print over a Workspace `init` built, change a Manifest, and read again. Nothing
is ever written by either command.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.registry import RegistryEntry, TargetView
from tests.fakes.workspace import two_target_workspace

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"

runner = CliRunner()


def invoke(*arguments: str) -> object:
    return runner.invoke(app, list(arguments))


def output(result: object) -> str:
    return result.stdout + result.stderr  # type: ignore[attr-defined]


def manifest_path(root: Path, slug: str) -> Path:
    return root / ".agentdiag" / "targets" / slug / "manifest.yaml"


def edit_manifest(root: Path, slug: str, **changes: object) -> None:
    path = manifest_path(root, slug)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document.update(changes)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def files_under(root: Path) -> list[tuple[str, bytes]]:
    return sorted(
        (str(path.relative_to(root)), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    )


# --- registry ---


def test_the_registry_lists_every_target_with_its_name_environments_and_suites(
    tmp_path: Path,
) -> None:
    root = two_target_workspace(tmp_path)

    result = invoke("registry", "--root", str(root))

    assert result.exit_code == 0, output(result)
    assert result.stdout.splitlines() == [
        "target  name            family     channel  environments  connector  suites  sync",
        "a       toy-order-desk  northwind  chat     local         inprocess  sample  not_checked",
        "b       idle-target     -          -        local         -          sample  not_checked",
    ]


def test_the_registry_as_json_is_one_entry_per_target(tmp_path: Path) -> None:
    root = two_target_workspace(tmp_path)

    result = invoke("registry", "--root", str(root), "--json")

    entries = [RegistryEntry.model_validate(row) for row in json.loads(result.stdout)]
    assert [entry.slug for entry in entries] == ["a", "b"]
    assert entries[0].suites[0].path == "suites/sample.yaml"
    assert entries[0].sync is not None
    assert entries[0].sync.status == "not_checked", "no Sync state until a Fingerprint exists"
    assert (entries[0].sync.open_breaks, entries[0].sync.fingerprint) == (0, None)


def test_the_registry_reads_family_channel_and_protected_environments_from_the_manifest(
    tmp_path: Path,
) -> None:
    root = two_target_workspace(tmp_path)
    document = yaml.safe_load(manifest_path(root, "a").read_text(encoding="utf-8"))
    document["adapter"]["environments"]["prod"] = {"factory": "x:y", "protected": True}
    manifest_path(root, "a").write_text(yaml.safe_dump(document), encoding="utf-8")
    edit_manifest(root, "a", family="order-desk", channel="chat")

    (entry, _) = [
        RegistryEntry.model_validate(row)
        for row in json.loads(invoke("registry", "--root", str(root), "--json").stdout)
    ]

    assert (entry.family, entry.channel) == ("order-desk", "chat")
    assert entry.environments == ["local", "prod"]
    assert entry.protected == ["prod"]
    assert "local, prod (protected)" in invoke("registry", "--root", str(root)).stdout


def test_a_manifest_that_does_not_load_is_still_an_entry_with_its_problem(
    tmp_path: Path,
) -> None:
    root = two_target_workspace(tmp_path)
    manifest_path(root, "b").write_text("target: [unclosed\n", encoding="utf-8")

    result = invoke("registry", "--root", str(root))

    assert result.exit_code == 0, output(result)
    assert result.stdout.splitlines()[2].startswith("b       -")
    assert any(line.startswith("problem: b: ") for line in result.stdout.splitlines())


def test_the_registry_writes_nothing(tmp_path: Path) -> None:
    root = two_target_workspace(tmp_path)
    before = files_under(root)

    invoke("registry", "--root", str(root))
    invoke("registry", "--root", str(root), "--json")
    invoke("target", "show", "a", "--root", str(root))

    assert files_under(root) == before


def test_the_example_is_one_entry_of_the_northwind_family(tmp_path: Path) -> None:
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))

    result = invoke("registry", "--root", str(root))

    assert result.stdout.splitlines()[1].split()[:4] == [
        "toy-order-desk",
        "toy-order-desk",
        "northwind",
        "chat",
    ]
    assert "orders, guardrails" in result.stdout


# --- target show ---


def test_target_show_prints_the_manifest_notes_fingerprint_breaks_and_change_records(
    tmp_path: Path,
) -> None:
    root = two_target_workspace(tmp_path)

    result = invoke("target", "show", "a", "--root", str(root))

    assert result.exit_code == 0, output(result)
    lines = result.stdout.splitlines()
    assert lines[0] == "Target a: toy-order-desk"
    assert "directory       .agentdiag/targets/a" in lines
    assert any(
        line.startswith("notes           judge_notes.md, 0 words, fingerprint ") for line in lines
    )
    assert "fingerprint     none" in lines
    assert "sync breaks     none" in lines
    assert "change records  none" in lines
    assert "Manifest as loaded:" in lines
    assert "    name: toy-order-desk" in lines


def test_target_show_as_json_carries_the_manifest_and_empty_lists(tmp_path: Path) -> None:
    root = two_target_workspace(tmp_path)

    result = invoke("target", "show", "b", "--root", str(root), "--json")

    view = TargetView.model_validate_json(result.stdout)
    assert view.entry.slug == "b"
    assert view.manifest is not None and view.manifest["target"]["name"] == "idle-target"
    assert view.calibration_notes is not None and view.calibration_notes.words == 0
    assert (view.fingerprint, view.sync_breaks, view.change_records) == (None, [], [])


def test_target_show_of_a_slug_that_does_not_exist_names_the_ones_that_do(
    tmp_path: Path,
) -> None:
    root = two_target_workspace(tmp_path)

    result = invoke("target", "show", "c", "--root", str(root))

    assert result.exit_code == 3
    assert output(result).startswith("error: no Target 'c'")
    assert "a, b" in output(result)


# --- ticket 10: the Fingerprint `sync` wrote, and the names protected by default ---


def test_target_show_says_when_the_fingerprint_was_built_and_what_it_covers(
    tmp_path: Path,
) -> None:
    root = two_target_workspace(tmp_path)
    assert invoke("sync", "--root", str(root), "--target", "a").exit_code == 0  # type: ignore[attr-defined]

    shown = invoke("target", "show", "a", "--root", str(root))
    as_json = invoke("target", "show", "a", "--root", str(root), "--json")

    line = next(line for line in shown.stdout.splitlines() if line.startswith("fingerprint "))  # type: ignore[attr-defined]
    assert line.endswith("(local), 5 sections covered, 0 not covered")
    view = TargetView.model_validate_json(as_json.stdout)  # type: ignore[attr-defined]
    assert view.fingerprint is not None
    assert line.split()[1] == f"{view.fingerprint.id[:8]},"


def test_prod_staging_and_eu_prod_are_listed_protected_whatever_their_block_says(
    tmp_path: Path,
) -> None:
    root = two_target_workspace(tmp_path)
    path = manifest_path(root, "a")
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    local = manifest["adapter"]["environments"]["local"]
    manifest["adapter"]["environments"].update(
        {"prod": dict(local), "staging": {**local, "protected": False}}
    )
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    result = invoke("registry", "--root", str(root), "--json")

    (entry, _) = [RegistryEntry.model_validate(item) for item in json.loads(result.stdout)]  # type: ignore[attr-defined]
    assert entry.protected == ["prod", "staging"], "a default name cannot be unprotected"


def test_friction_13_the_table_marks_a_suite_that_does_not_run(tmp_path: Path) -> None:
    """Walkthrough friction 13: `registry` listed a retired Suite beside a runnable one with
    no status, so the table could not say which Suites run."""
    root = two_target_workspace(tmp_path)
    edit_manifest(root, "a", suites=[{"path": "suites/sample.yaml", "status": "retired"}])
    edit_manifest(
        root, "b", suites=["suites/sample.yaml", {"path": "suites/next.yaml", "status": "draft"}]
    )

    table = invoke("registry", "--root", str(root))
    shown = invoke("target", "show", "a", "--root", str(root))

    rows = {line.split()[0]: line for line in table.stdout.splitlines()[1:]}
    assert "sample (retired)" in rows["a"]
    assert "sample, next (draft)" in rows["b"]
    assert "suites          suites/sample.yaml (retired)" in shown.stdout.splitlines()
