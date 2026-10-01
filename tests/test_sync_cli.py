"""Seam 1: `agentdiag sync` and `sync --check` over the toy Target (ticket 10, decision 13).

The toy's Manifest names `prompts: {system: observed}`, and with its `connector` block
removed the in-process Adapter's probe (decision 11) is what fingerprints its system prompt,
its two tools, its model and its provider: the path every Manifest with no Connector takes.
An edit to the toy's prompt module, made through `monkeypatch` where `target.py` reads it,
is an edit on the deployed side: `deployed_ahead`. Nothing here reaches a model: the probe
is answered in-process.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.adapter import InProcessAdapter, LiveSideEffectsRefused
from agentdiag.cli import app
from agentdiag.examples.toy import SYSTEM_PROMPT, TOOL_SCHEMAS
from agentdiag.sync.compare import SyncResult
from agentdiag.sync.fingerprint import Fingerprint

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
PROMPT_READ_AT = "agentdiag.examples.toy.target.SYSTEM_PROMPT"
"""Where the toy reads its prompt: `target.py` imports the name, so it is patched there."""

EDITED_PROMPT = SYSTEM_PROMPT.replace("at most three sentences", "at most two sentences")

TOY_SECTIONS = ["model", "prompt.system", "provider", "tool.cancel_order", "tool.lookup_order"]

runner = CliRunner()


def toy_root(tmp_path: Path) -> Path:
    """A copy of the example without its `connector` block, so the probe is the deployed
    side: the toy through its own Connector is `test_connector_sync.py`'s (ticket 23)."""
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest.pop("connector")
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return root


def sync(root: Path, *arguments: str) -> object:
    return runner.invoke(app, ["sync", "--root", str(root), *arguments])


def fingerprint_file(root: Path) -> Path:
    return root / ".agentdiag" / "targets" / "toy-order-desk" / "fingerprint.json"


def edit_manifest(root: Path, **blocks: object) -> None:
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest.update(blocks)
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def test_a_first_sync_says_there_is_no_previous_fingerprint_and_writes_one(
    tmp_path: Path,
) -> None:
    root = toy_root(tmp_path)

    result = sync(root)

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "no previous Fingerprint (environment local)"
    assert lines[1].split() == ["section", "direction", "change", "covered"]
    assert [line.split() for line in lines[2:7]] == [
        [section, "deployed_ahead", "added", "adapter"] for section in TOY_SECTIONS
    ]
    assert lines[7] == "5 sections deployed_ahead"
    assert lines[8].startswith(f"wrote {fingerprint_file(root)} (fingerprint ")
    written = Fingerprint.model_validate_json(fingerprint_file(root).read_text())
    assert list(written.sections) == TOY_SECTIONS
    assert {section.covered_by for section in written.sections.values()} == {"adapter"}
    assert written.environment == "local"
    assert written.not_covered == []
    assert written.resynced_from is None
    assert written.sections["model"].summary == "claude-sonnet-5"


def test_sync_check_after_a_sync_holds_exits_0_and_writes_nothing(tmp_path: Path) -> None:
    root = toy_root(tmp_path)
    sync(root)
    before = fingerprint_file(root).read_bytes()
    recorded = Fingerprint.model_validate_json(before)

    result = sync(root, "--check")

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == (
        f"Sync held against fingerprint {recorded.id[:8]} (built {recorded.built_at}, local)"
    )
    assert lines[-1] == "5 sections identical"
    assert fingerprint_file(root).read_bytes() == before


def test_an_edited_prompt_is_deployed_ahead_and_check_exits_2_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = toy_root(tmp_path)
    sync(root)
    before = fingerprint_file(root).read_bytes()
    monkeypatch.setattr(PROMPT_READ_AT, EDITED_PROMPT)

    result = sync(root, "--check")

    assert result.exit_code == 2, result.output
    assert result.stdout.splitlines()[0].startswith("Sync broken against fingerprint ")
    rows = {line.split()[0]: line.split()[1:] for line in result.stdout.splitlines()[2:7]}
    assert rows["prompt.system"] == ["deployed_ahead", "changed", "adapter"]
    assert rows["model"] == ["identical", "-", "adapter"]
    assert result.stdout.splitlines()[-2] == "4 sections identical, 1 deployed_ahead"
    assert result.stdout.splitlines()[-1].startswith("recorded Sync break ")
    assert fingerprint_file(root).read_bytes() == before


def test_sync_after_an_edit_rewrites_the_fingerprint_and_the_next_check_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = toy_root(tmp_path)
    sync(root)
    monkeypatch.setattr(PROMPT_READ_AT, EDITED_PROMPT)

    rewritten = sync(root)
    checked = sync(root, "--check")

    assert rewritten.exit_code == 0
    assert "prompt.system      deployed_ahead  changed" in rewritten.stdout
    assert checked.exit_code == 0, checked.output


def test_sync_check_as_json_prints_the_sync_result(tmp_path: Path) -> None:
    root = toy_root(tmp_path)
    sync(root)

    result = sync(root, "--check", "--json")

    parsed = SyncResult.model_validate(json.loads(result.stdout))
    assert parsed.status == "held"
    assert [state.id for state in parsed.sections] == TOY_SECTIONS


def test_sync_check_with_no_fingerprint_is_not_checked_exit_3_and_says_what_to_run(
    tmp_path: Path,
) -> None:
    root = toy_root(tmp_path)

    result = sync(root, "--check")

    assert result.exit_code == 3
    assert result.stdout.splitlines()[-1] == (
        "not checked: no_fingerprint; `agentdiag sync` records the first Fingerprint"
    )
    assert not fingerprint_file(root).exists()


def test_a_target_that_calls_no_model_cannot_be_observed(tmp_path: Path) -> None:
    root = toy_root(tmp_path)
    edit_manifest(
        root,
        adapter={
            "kind": "inprocess",
            "environments": {
                "default": "local",
                "local": {"factory": "tests.fakes.seeded_target:make_target"},
            },
        },
        tools={},
    )

    result = sync(root, "--check", "--json")

    assert result.exit_code == 3
    parsed = SyncResult.model_validate(json.loads(result.stdout))
    assert (parsed.status, parsed.reason) == ("not_checked", "adapter_cannot_observe")
    assert {state.direction for state in parsed.sections} == {"not_covered"}
    assert all(
        (state.note or "").startswith("the Adapter's probe failed: the Target answered")
        for state in parsed.sections
    )


def test_a_factory_that_raises_leaves_every_observed_section_not_covered_with_its_reason(
    tmp_path: Path,
) -> None:
    root = toy_root(tmp_path)
    edit_manifest(
        root,
        adapter={
            "kind": "inprocess",
            "environments": {
                "default": "local",
                "local": {
                    "factory": "tests.fakes.broken_target:make_target",
                    "tools": "tests.fakes.broken_target:make_tools",
                },
            },
        },
        prompts={"system": "observed", "notes": "judge_notes.md"},
    )

    result = sync(root)

    assert result.exit_code == 0, result.output
    written = Fingerprint.model_validate_json(fingerprint_file(root).read_text())
    assert list(written.sections) == ["prompt.notes"]
    assert written.sections["prompt.notes"].covered_by == "local"
    reasons = {entry.id: entry.reason for entry in written.not_covered}
    assert reasons["prompt.system"].startswith(
        "the Adapter's probe failed: the Target raised before its first model call: "
        "RuntimeError: the order desk is closed"
    )
    assert set(reasons) == {
        "prompt.system",
        "tool.lookup_order",
        "tool.cancel_order",
        "model",
        "provider",
    }


def test_an_adapter_that_is_not_in_process_observes_nothing_and_path_pointers_still_hash(
    tmp_path: Path,
) -> None:
    root = toy_root(tmp_path)
    edit_manifest(
        root,
        adapter={"kind": "http", "environments": {"default": "local", "local": {}}},
        prompts={"system": "observed", "notes": "judge_notes.md"},
        tools={},
        data_sources={"orders": "sqlite:///orders.db"},
    )

    result = sync(root, "--json")

    parsed = SyncResult.model_validate(json.loads(result.stdout.split("\nwrote ")[0]))
    by_id = {state.id: state for state in parsed.sections}
    assert by_id["prompt.system"].direction == "not_covered"
    assert (by_id["prompt.system"].note or "").startswith(
        "adapter_cannot_observe: the http Adapter observes nothing of the deployed set"
    )
    assert (by_id["prompt.notes"].direction, by_id["prompt.notes"].covered) == (
        "local_ahead",
        "local",
    )
    assert by_id["data_source.orders"].covered == "local"


def test_a_path_pointer_with_headings_is_a_section_per_heading_and_an_edit_is_local_ahead(
    tmp_path: Path,
) -> None:
    root = toy_root(tmp_path)
    rules = root / ".agentdiag" / "targets" / "toy-order-desk" / "prompts" / "rules.md"
    rules.parent.mkdir()
    rules.write_text("Preamble.\n# Rule 1\nLook up first.\n# Rule 3\nNever invent.\n")
    edit_manifest(root, prompts={"system": "observed", "rules": "prompts/rules.md"})
    sync(root)
    rules.write_text("Preamble.\n# Rule 1\nLook up first.\n# Rule 3\nNever invent a date.\n")

    result = sync(root, "--check")

    assert result.exit_code == 2, result.output
    rows = {line.split()[0]: line.split()[1:] for line in result.stdout.splitlines()[2:-1]}
    assert rows["prompt.rules#rule-3"] == ["local_ahead", "changed", "local"]
    assert rows["prompt.rules#rule-1"] == ["identical", "-", "local"]
    assert rows["prompt.rules#_preamble"] == ["identical", "-", "local"]


def test_an_unknown_environment_is_a_usage_error_naming_the_ones_there_are(
    tmp_path: Path,
) -> None:
    result = sync(toy_root(tmp_path), "--env", "prod")

    assert result.exit_code == 3
    assert "names no Adapter or Connector environment 'prod'; it names local" in result.output


def test_a_target_with_no_manifest_is_not_checked_no_manifest(tmp_path: Path) -> None:
    (tmp_path / ".agentdiag" / "targets" / "empty").mkdir(parents=True)

    result = sync(tmp_path, "--check")

    assert result.exit_code == 3
    assert "No Manifest at" in result.output


# --- the probe (decision 11) ---


def toy_adapter(**environment: object) -> InProcessAdapter:
    block = {
        "factory": "agentdiag.examples.toy:make_target",
        "tools": "agentdiag.examples.toy:make_tools",
        "model": "claude-sonnet-5",
        **environment,
    }
    return InProcessAdapter(
        {"kind": "inprocess", "environments": {"default": "local", "local": block}},
        environment="local",
    )


def test_the_probe_observes_the_system_prompt_the_tool_schemas_and_the_model() -> None:
    observation = toy_adapter().observe()

    assert observation.system_prompt == SYSTEM_PROMPT
    assert observation.tool_schemas == {schema["name"]: schema for schema in TOOL_SCHEMAS}
    assert observation.model == "claude-sonnet-5"


def test_the_probe_refuses_a_live_environment_as_open_does() -> None:
    with pytest.raises(LiveSideEffectsRefused):
        toy_adapter(side_effects="live").observe()


def test_the_sync_package_imports_no_model_client_and_no_sdk() -> None:
    """`sync`'s modules are offline at import time (decision 18): only a probe, when one is
    made, imports the Adapter and the SDK."""
    probe = (
        "import sys, agentdiag.sync.check, agentdiag.sync.compare, agentdiag.sync.observe, "
        "agentdiag.sync.fingerprint, agentdiag.sync.sections; "
        "leaked = sorted(m for m in sys.modules if m == 'anthropic' "
        "or m.startswith(('anthropic.', 'agentdiag.model', 'agentdiag.adapter'))); "
        "print(','.join(leaked))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=REPO
    )

    assert completed.stdout.strip() == ""


def test_a_streaming_target_is_observed_from_the_request_it_sent() -> None:
    from tests.fakes import streaming_target

    adapter = InProcessAdapter(
        {
            "kind": "inprocess",
            "environments": {
                "default": "local",
                "local": {"factory": "tests.fakes.streaming_target:make_target"},
            },
        },
        environment="local",
    )

    observation = adapter.observe()

    assert observation.system_prompt == streaming_target.SYSTEM
    assert observation.tool_schemas == {"lookup_order": streaming_target.SCHEMA}
    assert observation.model == "claude-sonnet-5"


def test_a_target_that_blocks_before_its_first_model_call_times_out_as_not_covered(
    tmp_path: Path,
) -> None:
    from tests.fakes import blocking_target

    root = toy_root(tmp_path)
    edit_manifest(
        root,
        adapter={
            "kind": "inprocess",
            "environments": {
                "default": "local",
                "local": {
                    "factory": "tests.fakes.blocking_target:make_target",
                    "turn_timeout": 0.2,
                },
            },
        },
    )
    try:
        result = sync(root, "--check", "--json")
    finally:
        blocking_target.RELEASE.set()

    parsed = SyncResult.model_validate(json.loads(result.stdout))
    notes = {state.id: state.note for state in parsed.sections}
    assert notes["prompt.system"] == (
        "the Adapter's probe failed: the Target did not answer the probe; timed out after "
        "0.2 s for observed pointer prompts.system"
    )


def test_a_path_tool_schema_is_the_local_files_alone_and_settles_after_a_sync(
    tmp_path: Path,
) -> None:
    """The probe is D for `observed` pointers only (decision 11 amended): a tool schema
    file is L until a Connector reads its deployed twin, so it never compares against the
    probe's reading of the request."""
    root = toy_root(tmp_path)
    schema = root / ".agentdiag" / "targets" / "toy-order-desk" / "tools" / "lookup_order.json"
    schema.parent.mkdir()
    schema.write_text(json.dumps(TOOL_SCHEMAS[0]))
    edit_manifest(
        root,
        tools={
            "lookup_order": {"kind": "retrieval", "schema": "tools/lookup_order.json"},
            "cancel_order": {"kind": "action"},
        },
        prompts={"system": {"path": "judge_notes.md"}},
    )
    sync(root)

    result = sync(root, "--check", "--json")

    parsed = SyncResult.model_validate(json.loads(result.stdout))
    by_id = {state.id: state for state in parsed.sections}
    assert parsed.status == "held", result.stdout
    assert (by_id["tool.lookup_order"].covered, by_id["tool.lookup_order"].deployed) == (
        "local",
        None,
    )
    assert by_id["tool.cancel_order"].covered == "adapter"
    assert by_id["prompt.system"].covered == "local"


def test_friction_5_a_sync_that_covers_no_section_writes_no_fingerprint(tmp_path: Path) -> None:
    """Walkthrough friction 5: a probe that could not run left every section `not_covered`,
    and `sync` still exited 0 with a 0-section Fingerprint that the next `sync` turned into a
    Sync break. Now it is `not_checked / adapter_cannot_observe`, exit 3, with the reasons."""
    root = toy_root(tmp_path)
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["adapter"]["environments"]["local"]["factory"] = "agentdiag.examples.nowhere:make"
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    result = sync(root)

    assert result.exit_code == 3, result.output
    assert "adapter_cannot_observe" in result.output
    assert "agentdiag.examples.nowhere" in result.output
    assert not fingerprint_file(root).exists()
    assert not (fingerprint_file(root).parent / "sync-breaks").exists()
