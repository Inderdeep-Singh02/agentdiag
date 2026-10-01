"""Seam 1: a Workspace of many Targets, and every command resolving the right one (ticket 24).

The Workspace is built the way a developer builds one — `init --target a`, then `init
--target b --adapter …` (`tests/fakes/workspace.py`) — and every assertion is on what a
developer can observe: where a Run directory landed, what `run.json` names, what a command
printed, how it exited. The Phase 4 spelling (a Manifest directly under `.agentdiag/`) is
read as the Target `default`, and the checked-in example still runs from its own root.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.workspace import Workspace
from tests.fakes.workspace import (
    CUSTOM_SCENARIO,
    CUSTOM_SLUG,
    TOY_RECORDING,
    TOY_SCENARIO,
    TOY_SLUG,
    greeting_recording,
    two_target_workspace,
)

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
SUITE_RECORDING = REPO / "tests" / "fixtures" / "recordings" / "toy-orders.jsonl"

runner = CliRunner()


def invoke(*arguments: str) -> object:
    return runner.invoke(app, list(arguments))


def target_dir(root: Path, slug: str) -> Path:
    return root / ".agentdiag" / "targets" / slug


def runs_of(root: Path, slug: str) -> list[Path]:
    runs = target_dir(root, slug) / "runs"
    return sorted(runs.iterdir()) if runs.is_dir() else []


def record_of(run_dir: Path) -> dict:
    return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))


def output(result: object) -> str:
    return result.stdout + result.stderr  # type: ignore[attr-defined]


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Two Targets and one Run in each, made once: every read-only test below shares them."""
    scratch = tmp_path_factory.mktemp("workspace")
    root = two_target_workspace(scratch / "ws")
    toy = invoke(
        "run",
        "--root",
        str(root),
        "--target",
        TOY_SLUG,
        "--scenario",
        TOY_SCENARIO,
        "--replay",
        str(TOY_RECORDING),
    )
    assert toy.exit_code in (0, 1, 2), output(toy)
    custom = invoke(
        "run",
        "--root",
        str(root),
        "--target",
        CUSTOM_SLUG,
        "--scenario",
        CUSTOM_SCENARIO,
        "--replay",
        str(greeting_recording(scratch)),
    )
    assert custom.exit_code in (0, 1, 2), output(custom)
    return root


def toy_run(root: Path) -> Path:
    (run_dir,) = runs_of(root, TOY_SLUG)
    return run_dir


def custom_run(root: Path) -> Path:
    (run_dir,) = runs_of(root, CUSTOM_SLUG)
    return run_dir


# --- init makes a Workspace, and adds Targets to it ---


def test_init_without_a_target_creates_a_workspace_with_the_target_default(
    tmp_path: Path,
) -> None:
    result = invoke("init", "--root", str(tmp_path))

    assert result.exit_code == 0, output(result)
    directory = target_dir(tmp_path, "default")
    assert (directory / "manifest.yaml").is_file()
    assert (directory / "suites" / "sample.yaml").is_file()
    assert (directory / "judge_notes.md").is_file()
    assert ".agentdiag/targets/default/manifest.yaml" in result.stdout


def test_init_with_a_target_adds_that_target_to_an_existing_workspace(tmp_path: Path) -> None:
    root = two_target_workspace(tmp_path)

    assert [target.slug for target in Workspace.find(root).targets()] == ["a", "b"]
    assert "idle-target" in (target_dir(root, "b") / "manifest.yaml").read_text(encoding="utf-8")
    assert "toy-order-desk" in (target_dir(root, "a") / "manifest.yaml").read_text(encoding="utf-8")


def test_init_refuses_a_target_directory_that_exists_and_names_it(tmp_path: Path) -> None:
    root = two_target_workspace(tmp_path)
    manifest = target_dir(root, "a") / "manifest.yaml"
    manifest.write_text("mine: yes\n", encoding="utf-8")

    result = invoke("init", "--root", str(root), "--target", "a")

    assert result.exit_code == 3
    assert str(target_dir(root, "a")) in output(result)
    assert manifest.read_text(encoding="utf-8") == "mine: yes\n"


def test_init_force_rewrites_one_targets_scaffold_and_leaves_the_other_alone(
    tmp_path: Path,
) -> None:
    root = two_target_workspace(tmp_path)
    other = (target_dir(root, "b") / "manifest.yaml").read_text(encoding="utf-8")
    (target_dir(root, "a") / "manifest.yaml").write_text("mine: yes\n", encoding="utf-8")

    result = invoke("init", "--root", str(root), "--target", "a", "--force")

    assert result.exit_code == 0, output(result)
    assert "toy-order-desk" in (target_dir(root, "a") / "manifest.yaml").read_text(encoding="utf-8")
    assert (target_dir(root, "b") / "manifest.yaml").read_text(encoding="utf-8") == other


@pytest.mark.parametrize("slug", ["Order Desk", "-a", "a_b", "A"])
def test_init_refuses_a_target_that_is_not_a_slug(tmp_path: Path, slug: str) -> None:
    result = invoke("init", "--root", str(tmp_path), "--target", slug)

    assert result.exit_code == 3
    assert "slug" in output(result)
    assert not (tmp_path / ".agentdiag").exists()


def test_init_after_a_second_target_says_which_target_the_next_commands_name(
    tmp_path: Path,
) -> None:
    invoke("init", "--root", str(tmp_path), "--target", "a")

    result = invoke("init", "--root", str(tmp_path), "--target", "b")

    assert f"agentdiag run --target b --scenario {TOY_SCENARIO}" in result.stdout


def test_the_gitignore_covers_every_targets_runs_restore_points_and_the_index(
    tmp_path: Path,
) -> None:
    two_target_workspace(tmp_path)

    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()

    for line in (
        ".agentdiag/targets/*/runs/",
        ".agentdiag/targets/*/restore-points/",
        ".agentdiag/targets/*/platform/",
        ".agentdiag/index.sqlite",
    ):
        assert lines.count(line) == 1, line


def test_init_keeps_the_phase_4_gitignore_lines_and_adds_the_new_ones_once(
    tmp_path: Path,
) -> None:
    older = "# Runs are output, not source.\n.agentdiag/runs/\n.agentdiag/index.sqlite\n"
    (tmp_path / ".gitignore").write_text(older, encoding="utf-8")

    invoke("init", "--root", str(tmp_path), "--target", "a")
    invoke("init", "--root", str(tmp_path), "--target", "b")

    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines[:3] == older.splitlines(), "the old lines are kept where they were"
    assert lines.count(".agentdiag/targets/*/runs/") == 1
    assert lines.count(".agentdiag/index.sqlite") == 1


# --- every command resolves the right Target ---


def test_a_run_lands_under_its_targets_runs_and_names_the_target(workspace: Path) -> None:
    toy, custom = toy_run(workspace), custom_run(workspace)

    assert record_of(toy)["target"] == "a"
    assert record_of(custom)["target"] == "b"
    assert record_of(toy)["source"] == "run"
    assert (toy / "trials" / TOY_SCENARIO / "1" / "trace.jsonl").is_file()
    assert (custom / "trials" / CUSTOM_SCENARIO / "1" / "trace.jsonl").is_file()
    assert not (workspace / ".agentdiag" / "runs").exists(), "no Run in the Phase 4 place"


@pytest.mark.parametrize(
    "command",
    [
        ["run", "--dry-run"],
        ["validate"],
    ],
)
def test_a_command_with_several_targets_and_no_target_names_both_slugs(
    workspace: Path, command: list[str]
) -> None:
    result = invoke(*command, "--root", str(workspace))

    assert result.exit_code == 3
    assert "a, b" in output(result)
    assert "--target" in output(result)


def test_run_dry_run_resolves_the_named_target(workspace: Path) -> None:
    toy = invoke("run", "--root", str(workspace), "--target", "a", "--dry-run")
    custom = invoke("run", "--root", str(workspace), "--target", "b", "--dry-run")

    assert toy.exit_code == 0, output(toy)
    assert TOY_SCENARIO in toy.stdout and CUSTOM_SCENARIO not in toy.stdout
    assert CUSTOM_SCENARIO in custom.stdout and TOY_SCENARIO not in custom.stdout


def test_a_target_that_does_not_exist_is_named_with_the_slugs_that_do(workspace: Path) -> None:
    result = invoke("run", "--root", str(workspace), "--target", "c", "--dry-run")

    assert result.exit_code == 3
    assert "'c'" in output(result)
    assert "a, b" in output(result)


def test_validate_reads_the_named_targets_suites(tmp_path: Path) -> None:
    root = two_target_workspace(tmp_path)
    (target_dir(root, "b") / "suites" / "sample.yaml").write_text("scenarios: 3\n")

    toy = invoke("validate", "--root", str(root), "--target", "a")
    custom = invoke("validate", "--root", str(root), "--target", "b")

    assert toy.exit_code == 0, output(toy)
    assert custom.exit_code == 3
    assert "targets/b/suites/sample.yaml" in output(custom)


def test_show_and_export_find_a_run_id_under_any_target(workspace: Path, tmp_path: Path) -> None:
    run_id = custom_run(workspace).name

    shown = invoke("show", run_id, CUSTOM_SCENARIO, "--root", str(workspace))
    exported = invoke(
        "export",
        run_id,
        CUSTOM_SCENARIO,
        "--root",
        str(workspace),
        "--format",
        "chrome",
        "--out",
        str(tmp_path / "out.json"),
    )

    assert shown.exit_code == 0, output(shown)
    assert exported.exit_code == 0, output(exported)
    assert (tmp_path / "out.json").is_file()


def test_show_with_a_target_looks_only_under_that_target(workspace: Path) -> None:
    run_id = custom_run(workspace).name

    result = invoke("show", run_id, CUSTOM_SCENARIO, "--root", str(workspace), "--target", "a")

    assert result.exit_code == 3
    assert str(target_dir(workspace, "a") / "runs") in output(result)


def test_list_fills_the_target_column_and_filters_by_target(workspace: Path) -> None:
    every = invoke("list", "--root", str(workspace), "--json")
    only_b = invoke("list", "--root", str(workspace), "--target", "b", "--json")

    assert every.exit_code == 0, output(every)
    assert sorted(row["target"] for row in json.loads(every.stdout)) == ["a", "b"]
    assert [row["run_id"] for row in json.loads(only_b.stdout)] == [custom_run(workspace).name]


def test_index_rebuild_indexes_every_targets_runs(tmp_path: Path, workspace: Path) -> None:
    root = tmp_path / "copy"
    shutil.copytree(workspace, root)

    every = invoke("index", "rebuild", "--root", str(root))
    one = invoke("index", "rebuild", "--root", str(root), "--target", "a")

    assert every.exit_code == 0, output(every)
    assert every.stdout.startswith("indexed 2 Runs into")
    assert one.stdout.startswith("indexed 1 Runs of Target a into")
    listed = invoke("list", "--root", str(root), "--json")
    assert sorted(row["target"] for row in json.loads(listed.stdout)) == ["a", "b"]


def test_compare_finds_both_runs_by_id_across_targets(workspace: Path) -> None:
    result = invoke(
        "compare", toy_run(workspace).name, custom_run(workspace).name, "--root", str(workspace)
    )

    assert result.exit_code == 0, output(result)


def test_rescore_of_a_run_id_writes_under_the_sources_target_and_records_it(
    tmp_path: Path, workspace: Path
) -> None:
    root = tmp_path / "copy"
    shutil.copytree(workspace, root)
    source = toy_run(root)

    result = invoke("rescore", source.name, "--root", str(root), "--replay", str(TOY_RECORDING))

    assert result.exit_code in (0, 1, 2), output(result)
    (rescored,) = [run for run in runs_of(root, "a") if run != source]
    assert record_of(rescored)["target"] == "a"
    assert record_of(rescored)["traces_from"] == source.name
    assert runs_of(root, "b") == [custom_run(root)], "the other Target is untouched"


def test_rescore_refuses_a_target_other_than_the_one_holding_the_source_naming_both(
    workspace: Path,
) -> None:
    source = toy_run(workspace)

    result = invoke("rescore", source.name, "--root", str(workspace), "--target", "b")

    assert result.exit_code == 3
    assert output(result).startswith("error: ")
    assert "Target a" in output(result) and "Target b" in output(result)
    assert runs_of(workspace, "b") == [custom_run(workspace)], "nothing was written"


def test_locate_source_picks_the_target_holding_the_run_and_records_it(
    tmp_path: Path, workspace: Path
) -> None:
    from agentdiag.run.rescore import locate_source

    root = tmp_path / "copy"
    shutil.copytree(workspace, root)
    source = toy_run(root)
    record = json.loads((source / "run.json").read_text(encoding="utf-8"))
    del record["target"]
    (source / "run.json").write_text(json.dumps(record), encoding="utf-8")

    target, found = locate_source(Workspace.find(root), source.name, None)
    result = invoke("rescore", source.name, "--root", str(root), "--replay", str(TOY_RECORDING))

    assert (target.slug, found) == ("a", source)
    (rescored,) = [run for run in runs_of(root, "a") if run != source]
    assert result.exit_code in (0, 1, 2), output(result)
    assert record_of(rescored)["target"] == "a"


def test_rescore_of_a_run_that_does_not_exist_is_an_error_on_stderr(workspace: Path) -> None:
    result = invoke("rescore", "no-such-run", "--root", str(workspace))

    assert result.exit_code == 3
    assert result.stdout == ""
    assert result.stderr.startswith("error: no Run 'no-such-run'")


# --- the registry and target show take --target ---


def test_registry_with_a_target_shows_only_that_entry(workspace: Path) -> None:
    result = invoke("registry", "--root", str(workspace), "--target", "b")

    assert [line.split()[0] for line in result.stdout.splitlines()] == ["target", "b"]


def test_target_show_takes_the_slug_as_argument_or_option_or_the_one_target(
    tmp_path: Path, workspace: Path
) -> None:
    by_option = invoke("target", "show", "--root", str(workspace), "--target", "b")
    ambiguous = invoke("target", "show", "--root", str(workspace))
    single = tmp_path / "single"
    invoke("init", "--root", str(single))
    alone = invoke("target", "show", "--root", str(single))

    assert by_option.stdout.startswith("Target b: idle-target")
    assert ambiguous.exit_code == 3 and "a, b" in output(ambiguous)
    assert alone.stdout.startswith("Target default: toy-order-desk")


# --- init inside an existing Workspace ---


def test_init_without_a_root_below_a_workspace_says_it_nests_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke("init", "--root", str(tmp_path))
    below = tmp_path / "service"
    below.mkdir()
    monkeypatch.chdir(below)

    result = invoke("init")

    assert result.exit_code == 0, output(result)
    assert f"notice: a Workspace already exists at {tmp_path.resolve()}" in result.stderr
    assert (below / ".agentdiag" / "targets" / "default" / "manifest.yaml").is_file()


def test_init_in_the_workspace_root_itself_adds_a_target_without_a_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke("init", "--root", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    result = invoke("init", "--target", "b")

    assert result.exit_code == 0, output(result)
    assert "notice:" not in result.stderr


# --- the root: given, or found walking up ---


def test_every_command_finds_the_workspace_walking_up_from_the_current_directory(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    below = target_dir(workspace, "a") / "suites"
    monkeypatch.chdir(below)

    listed = invoke("list", "--json")
    shown = invoke("show", toy_run(workspace).name, TOY_SCENARIO)

    assert listed.exit_code == 0, output(listed)
    assert len(json.loads(listed.stdout)) == 2
    assert shown.exit_code == 0, output(shown)


def test_no_workspace_above_the_current_directory_is_an_error_saying_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = invoke("list")

    assert result.exit_code == 3
    assert output(result).startswith("error: no Workspace found")


def test_a_root_without_agentdiag_is_an_error_naming_it(tmp_path: Path) -> None:
    result = invoke("list", "--root", str(tmp_path))

    assert result.exit_code == 3
    assert f"error: no Workspace at {tmp_path}" in output(result)


# --- the example, and the Phase 4 spelling refused (ticket 13) ---


def example_root(tmp_path: Path) -> Path:
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    return root


def test_the_example_is_one_target_toy_order_desk_under_targets(tmp_path: Path) -> None:
    root = example_root(tmp_path)

    (target,) = Workspace.find(root).targets()
    shown = invoke("run", "--root", str(root), "--dry-run")

    assert target.slug == "toy-order-desk"
    assert target.directory == root / ".agentdiag" / "targets" / "toy-order-desk"
    assert shown.exit_code == 0, output(shown)


def test_the_example_runs_from_its_own_root_in_replay_with_no_target_named(
    tmp_path: Path,
) -> None:
    root = example_root(tmp_path)

    result = invoke("run", "--root", str(root), "--replay", str(SUITE_RECORDING))

    assert result.exit_code in (0, 1, 2), output(result)
    (run_dir,) = sorted((root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir())
    assert (run_dir / "scorecard.json").is_file()
    assert record_of(run_dir)["target"] == "toy-order-desk"


def phase_4_root(tmp_path: Path) -> Path:
    """A root as the Phase 4 `init` left it: the example's files directly under
    `.agentdiag/`."""
    root = example_root(tmp_path)
    shutil.copytree(
        root / ".agentdiag" / "targets" / "toy-order-desk", root / "moved", dirs_exist_ok=True
    )
    shutil.rmtree(root / ".agentdiag")
    shutil.move(root / "moved", root / ".agentdiag")
    return root


def test_a_phase_4_root_is_refused_by_every_command_naming_the_move(tmp_path: Path) -> None:
    """The legacy reader is gone: a Manifest directly under `.agentdiag/` is neither read as
    a Target nor taken for an empty Workspace, and the message says where it goes."""
    root = phase_4_root(tmp_path)
    before = sorted(path.relative_to(root) for path in root.rglob("*"))

    for command in (["list"], ["registry"], ["run", "--dry-run"], ["validate"], ["init"]):
        result = invoke(*command, "--root", str(root))

        assert result.exit_code == 3, command
        assert command == ["init"] or output(result).startswith("error: "), command
        assert "Phase 4 spelling" in output(result), command
        assert "targets/default" in output(result).replace("\\", "/"), command
    assert sorted(path.relative_to(root) for path in root.rglob("*")) == before


def test_suites_or_runs_alone_under_agentdiag_are_not_a_target(tmp_path: Path) -> None:
    """Only a Manifest marks the old spelling; stray directories are nobody's Target."""
    root = tmp_path / "stray"
    (root / ".agentdiag" / "runs").mkdir(parents=True)

    assert Workspace.find(root).targets() == []
