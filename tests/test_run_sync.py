"""Seam 1: a Run checks Sync before its first Trial (ticket 10, D30, ADR-0008, decision 14).

The D30 matrix over the toy in replay: `held` proceeds; `broken` re-syncs by default,
records `resynced_from` and opens `show` with the section that moved; `--no-resync` runs
labelled `broken`; `--strict` refuses with exit 3 and the table; `not_checked` carries its
reason. The prompt is edited through `monkeypatch` where the toy's `target.py` reads it,
which is an edit on the deployed side: the toy's in-process Connector reads it there
(ticket 23), as the Adapter's probe did before it. A Trial of the edited Target no longer
matches the recording, so it ends `agentdiag_error`: what is asserted here is the Sync,
not the Trial.
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
from agentdiag.examples.toy import SYSTEM_PROMPT
from agentdiag.sync.fingerprint import Fingerprint

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
RECORDING = REPO / "tests" / "fixtures" / "recordings" / "toy-orders.jsonl"
SCENARIO = "cancel-processing-order"
PROMPT_READ_AT = "agentdiag.examples.toy.target.SYSTEM_PROMPT"
EDITED_PROMPT = SYSTEM_PROMPT.replace("at most three sentences", "at most two sentences")

runner = CliRunner()


def toy_root(tmp_path: Path) -> Path:
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    return root


def invoke(*arguments: str) -> Any:
    return runner.invoke(app, list(arguments))


def run(root: Path, *arguments: str) -> Any:
    return invoke(
        "run", "--root", str(root), "--scenario", SCENARIO, "--replay", str(RECORDING), *arguments
    )


def runs(root: Path) -> list[Path]:
    directory = root / ".agentdiag" / "targets" / "toy-order-desk" / "runs"
    return sorted(directory.iterdir()) if directory.exists() else []


def record(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))


def fingerprint(root: Path) -> Fingerprint:
    return Fingerprint.model_validate_json(
        (root / ".agentdiag" / "targets" / "toy-order-desk" / "fingerprint.json").read_text(
            encoding="utf-8"
        )
    )


def synced(tmp_path: Path) -> Path:
    root = toy_root(tmp_path)
    assert invoke("sync", "--root", str(root)).exit_code == 0
    return root


def test_a_held_sync_runs_and_records_the_fingerprint_it_ran_under(tmp_path: Path) -> None:
    root = synced(tmp_path)
    found = fingerprint(root)

    result = run(root)

    assert result.exit_code in {0, 1}, result.output
    (run_dir,) = runs(root)
    written = record(run_dir)
    assert written["sync"] == {
        "status": "held",
        "reason": None,
        "environment": "local",
        "fingerprint": found.id,
        "resynced_from": None,
        "sections": [],
        "covered_by": "connector",
        "connector_failed": None,
    }
    assert Fingerprint.model_validate(written["fingerprint"]) == found
    assert "  sync held" in result.stdout


def test_a_broken_sync_resyncs_records_where_from_and_leaves_the_prior_run_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = synced(tmp_path)
    old = fingerprint(root)
    run(root)
    (prior,) = runs(root)
    prior_bytes = {path: path.read_bytes() for path in prior.rglob("*") if path.is_file()}
    monkeypatch.setattr(PROMPT_READ_AT, EDITED_PROMPT)

    result = run(root)

    later = next(path for path in runs(root) if path != prior)
    written = record(later)
    assert written["sync"]["status"] == "broken"
    assert written["sync"]["fingerprint"] == old.id
    assert written["sync"]["resynced_from"] == old.id
    assert [
        (section["id"], section["direction"], section["change"])
        for section in written["sync"]["sections"]
    ] == [("prompt.system", "deployed_ahead", "changed")]
    new = fingerprint(root)
    assert new.resynced_from == old.id
    assert new.id != old.id
    assert Fingerprint.model_validate(written["fingerprint"]) == new
    assert f"sync broken, re-synced from {old.id[:8]}: prompt.system deployed_ahead" in (
        result.stdout
    )
    # The prior Run keeps its own Fingerprint, byte for byte (ADR-0007 §2).
    assert {path: path.read_bytes() for path in prior.rglob("*") if path.is_file()} == prior_bytes
    assert Fingerprint.model_validate(record(prior)["fingerprint"]) == old


def test_show_of_a_resynced_run_opens_with_the_section_that_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = synced(tmp_path)
    old = fingerprint(root)
    monkeypatch.setattr(PROMPT_READ_AT, EDITED_PROMPT)
    run(root)
    (run_dir,) = runs(root)

    shown = invoke("show", str(run_dir), SCENARIO)

    lines = shown.stdout.splitlines()
    sync_line = next(index for index, line in enumerate(lines) if line.startswith("Sync broken"))
    assert lines[sync_line + 1] == (
        f"Re-synced from {old.id}: prompt.system deployed_ahead (changed)"
    )
    assert sync_line + 1 < next(i for i, line in enumerate(lines) if line.startswith("Turn 1"))


def test_no_resync_runs_labelled_broken_against_the_old_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = synced(tmp_path)
    old_bytes = (
        root / ".agentdiag" / "targets" / "toy-order-desk" / "fingerprint.json"
    ).read_bytes()
    old = fingerprint(root)
    monkeypatch.setattr(PROMPT_READ_AT, EDITED_PROMPT)

    result = run(root, "--no-resync")

    (run_dir,) = runs(root)
    written = record(run_dir)
    assert (written["sync"]["status"], written["sync"]["resynced_from"]) == ("broken", None)
    assert [section["id"] for section in written["sync"]["sections"]] == ["prompt.system"]
    assert Fingerprint.model_validate(written["fingerprint"]) == old
    assert (
        root / ".agentdiag" / "targets" / "toy-order-desk" / "fingerprint.json"
    ).read_bytes() == old_bytes
    assert "  sync broken: prompt.system deployed_ahead" in result.stdout


def test_strict_refuses_a_broken_sync_with_exit_3_and_the_table_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = synced(tmp_path)
    old_bytes = (
        root / ".agentdiag" / "targets" / "toy-order-desk" / "fingerprint.json"
    ).read_bytes()
    monkeypatch.setattr(PROMPT_READ_AT, EDITED_PROMPT)

    result = run(root, "--strict")

    assert result.exit_code == 3
    assert "Sync is broken and --strict refuses to run against it" in result.stdout
    assert "prompt.system      deployed_ahead  changed  connector" in result.stdout
    assert runs(root) == []
    assert (
        root / ".agentdiag" / "targets" / "toy-order-desk" / "fingerprint.json"
    ).read_bytes() == old_bytes


def test_strict_on_a_held_sync_runs(tmp_path: Path) -> None:
    root = synced(tmp_path)

    result = run(root, "--strict")

    assert result.exit_code in {0, 1}, result.output
    assert record(runs(root)[0])["sync"]["status"] == "held"


def test_no_resync_and_strict_together_are_a_usage_error(tmp_path: Path) -> None:
    result = run(toy_root(tmp_path), "--no-resync", "--strict")

    assert result.exit_code == 3
    assert "--no-resync and --strict" in result.output


def test_a_target_that_never_ran_sync_is_not_checked_no_fingerprint(tmp_path: Path) -> None:
    root = toy_root(tmp_path)

    run(root)

    (run_dir,) = runs(root)
    assert record(run_dir)["sync"]["status"] == "not_checked"
    assert record(run_dir)["sync"]["reason"] == "no_fingerprint"
    assert record(run_dir)["fingerprint"] is None
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "fingerprint.json").exists()


def test_a_target_nothing_can_observe_is_not_checked_adapter_cannot_observe(
    tmp_path: Path,
) -> None:
    root = toy_root(tmp_path)
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["adapter"]["environments"]["local"] = {
        "factory": "tests.fakes.seeded_target:make_target",
        "tools": "tests.fakes.seeded_target:TOOLS",
    }
    manifest["tools"] = {}
    # The toy's own Connector would read its deployed set; this Target has none.
    manifest.pop("connector")
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    result = invoke("run", "--root", str(root), "--dry-run")

    assert result.stdout.splitlines()[-1] == "sync not_checked (adapter_cannot_observe)"


def test_a_dry_run_reports_a_broken_sync_and_rebuilds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = synced(tmp_path)
    old_bytes = (
        root / ".agentdiag" / "targets" / "toy-order-desk" / "fingerprint.json"
    ).read_bytes()
    monkeypatch.setattr(PROMPT_READ_AT, EDITED_PROMPT)

    result = run(root, "--dry-run")
    as_json = run(root, "--dry-run", "--json")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[-1] == (
        "sync broken: prompt.system deployed_ahead; a Run re-syncs first"
    )
    assert json.loads(as_json.stdout)["sync"]["status"] == "broken"
    assert runs(root) == []
    assert (
        root / ".agentdiag" / "targets" / "toy-order-desk" / "fingerprint.json"
    ).read_bytes() == old_bytes


def test_a_rescore_carries_its_sources_sync_and_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = synced(tmp_path)
    run(root)
    (source,) = runs(root)
    monkeypatch.setattr(PROMPT_READ_AT, EDITED_PROMPT)

    invoke("rescore", str(source), "--root", str(root), "--replay", str(RECORDING))

    rescored = next(path for path in runs(root) if path != source)
    assert record(rescored)["sync"] == record(source)["sync"]
    assert record(rescored)["fingerprint"] == record(source)["fingerprint"]
    assert record(rescored)["traces_from"] == source.name


def test_strict_on_a_local_file_ahead_says_the_deployed_set_is_behind_it(
    tmp_path: Path,
) -> None:
    root = toy_root(tmp_path)
    rules = root / ".agentdiag" / "targets" / "toy-order-desk" / "prompts" / "rules.md"
    rules.parent.mkdir()
    rules.write_text("# Rule 1\nLook up first.\n")
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["prompts"] = {"system": "observed", "rules": "prompts/rules.md"}
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    invoke("sync", "--root", str(root))
    rules.write_text("# Rule 1\nLook up first, always.\n")

    result = run(root, "--strict")

    assert result.exit_code == 3
    assert (
        "Sync is broken and --strict refuses to run against it: the deployed set is behind "
        "the local file; or drop --strict to re-sync"
    ) in result.stdout


def test_a_rebuild_that_hashes_to_the_current_fingerprint_rewrites_nothing(
    tmp_path: Path,
) -> None:
    """A re-sync whose rebuilt sections equal the recorded ones is no re-sync: the file is
    left as it is and no Fingerprint names itself as the one it came from. Forced here on a
    held Target, since no broken comparison the toy can produce rebuilds to the same id."""
    from dataclasses import replace

    from agentdiag.run.execute import resynced
    from agentdiag.run.preflight import preflight
    from agentdiag.run.record import RunStamp, SyncSection
    from agentdiag.scenario.select import Selection
    from agentdiag.workspace import Workspace

    root = synced(tmp_path)
    target = Workspace.find(root).resolve(None)
    before = (root / ".agentdiag" / "targets" / "toy-order-desk" / "fingerprint.json").read_bytes()
    plan = preflight(target, Selection(scenario=[SCENARIO]), RECORDING)
    assert plan.fingerprint is not None
    forced = replace(plan, resync=True)
    old = plan.fingerprint.id
    broken = SyncSection(status="broken", fingerprint=old, resynced_from=old)

    fingerprint, recorded = resynced(target, forced, RunStamp.now(root), broken)

    assert fingerprint == plan.fingerprint
    assert recorded.resynced_from is None
    assert (
        root / ".agentdiag" / "targets" / "toy-order-desk" / "fingerprint.json"
    ).read_bytes() == before
