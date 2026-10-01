"""Seam 1: Sync with a Connector, over the fake (ticket 23, phase-6 decisions 19 to 26).

The Connector's read is the deployed side D (decision 25), so a path pointer now has both a
local file L and a deployed twin, and every direction of decision 12 is reachable: the
tests here move L by editing the file under the Target directory, D through the fake
(`FakeConnector.set_prompt`, `set_tool`, its `deployed` mapping), and R by `sync`. The fake is
served under the kind `fake` through the plugin registry's test seam; the Target is a copy
of the shipped example whose `connector` block is pointed at it. Nothing here converses with
a Target: the Connector reads, and a Run replays.

Also here: the Run-level mapping and `sync --check`'s exit codes with a Connector; a failing
read (`sync` exits 3 naming it, a Run falls back to the probe and warns); one test per
missing credential; the Sync break files `sync` and `sync --check` record and the Registry
counts; the first toy fingerprinted through its own in-process Connector, and the probe
still the source for a Manifest with none.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.connector.base import ConnectorError
from agentdiag.connector.environment import resolve_environment
from agentdiag.connector.plugins import registered
from agentdiag.examples.toy import SYSTEM_PROMPT, deployed_set
from agentdiag.run.manifest import Manifest
from agentdiag.sync.compare import RECORD_IS_STALE
from agentdiag.sync.fingerprint import Fingerprint
from agentdiag.sync.observe import NOT_IN_READ
from tests.fakes.fake_connector import FAKE_KIND, FakeConnector

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
RECORDING = REPO / "tests" / "fixtures" / "recordings" / "toy-orders.jsonl"
SCENARIO = "cancel-processing-order"
PROMPT_READ_AT = "agentdiag.examples.toy.target.SYSTEM_PROMPT"

PROMPT = """You answer for the order desk.

# Persona

A patient order clerk.

# Rules

1. Look up before you answer.
2. Cancel only a processing order.
"""
"""A prompt with a preamble and two headings: three sections, one of them `#rules`."""

EDITED_RULES = PROMPT.replace("Cancel only a processing order.", "Never cancel an order.")
OTHER_RULES = PROMPT.replace("Cancel only a processing order.", "Cancel any order.")

runner = CliRunner()


@pytest.fixture
def fake() -> Iterator[FakeConnector]:
    """The fake, holding the toy's deployed set with `PROMPT` as its system prompt."""
    read = deployed_set()
    read["prompts"] = {"system": PROMPT}
    connector = FakeConnector({"local": read})
    with registered(FAKE_KIND, connector.as_kind()):
        yield connector


def manifest_path(root: Path) -> Path:
    return root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"


def edit_manifest(root: Path, **blocks: Any) -> None:
    path = manifest_path(root)
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key, value in blocks.items():
        if value is None:
            manifest.pop(key, None)
        else:
            manifest[key] = value
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def target_root(tmp_path: Path, *, path_pointer: bool = True, **connector: Any) -> Path:
    """A copy of the example whose Connector is the fake, its system prompt a path pointer
    to `prompts/system.md` holding `PROMPT` unless `path_pointer` is False."""
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    block = {"kind": FAKE_KIND, "environments": {"local": {}}, **connector}
    if path_pointer:
        prompts = root / ".agentdiag" / "targets" / "toy-order-desk" / "prompts"
        prompts.mkdir()
        (prompts / "system.md").write_text(PROMPT, encoding="utf-8")
        edit_manifest(root, connector=block, prompts={"system": "prompts/system.md"})
    else:
        edit_manifest(root, connector=block)
    return root


def local_prompt(root: Path) -> Path:
    return root / ".agentdiag" / "targets" / "toy-order-desk" / "prompts" / "system.md"


def sync(root: Path, *arguments: str) -> Any:
    return runner.invoke(app, ["sync", "--root", str(root), *arguments])


def rows(result: Any) -> dict[str, list[str]]:
    """The section table as `{section: [direction, change, covered, note…]}`."""
    lines = result.stdout.splitlines()
    start = next(i for i, line in enumerate(lines) if line.split()[:1] == ["section"])
    table: dict[str, list[str]] = {}
    for line in lines[start + 1 :]:
        words = line.split()
        if not words or words[1] not in {
            "identical",
            "local_ahead",
            "deployed_ahead",
            "diverged",
            "not_covered",
        }:
            break
        table[words[0]] = words[1:]
    return table


def fingerprint(root: Path) -> Fingerprint:
    return Fingerprint.model_validate_json(
        (root / ".agentdiag" / "targets" / "toy-order-desk" / "fingerprint.json").read_text(
            encoding="utf-8"
        )
    )


def breaks(root: Path) -> list[Path]:
    directory = root / ".agentdiag" / "targets" / "toy-order-desk" / "sync-breaks"
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


# --- every direction (decision 12) ---


def test_a_path_pointer_and_its_deployed_twin_agreeing_with_the_record_is_identical(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path)
    assert sync(root).exit_code == 0

    result = sync(root, "--check")

    assert result.exit_code == 0, result.output
    table = rows(result)
    assert table["prompt.system#rules"] == ["identical", "-", "connector"]
    assert table["prompt.system#_preamble"] == ["identical", "-", "connector"]
    assert result.stdout.splitlines()[-1] == "7 sections identical"
    assert fake.calls[-1] == ("read_deployed_set", "local", None)


def test_an_edit_to_the_local_file_alone_is_local_ahead(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path)
    sync(root)
    local_prompt(root).write_text(EDITED_RULES, encoding="utf-8")

    result = sync(root, "--check")

    assert result.exit_code == 2, result.output
    assert rows(result)["prompt.system#rules"] == ["local_ahead", "changed", "connector"]
    assert rows(result)["prompt.system#persona"] == ["identical", "-", "connector"]


def test_an_edit_on_the_deployed_side_alone_is_deployed_ahead(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path)
    sync(root)
    fake.set_prompt("local", "system", EDITED_RULES)

    result = sync(root, "--check")

    assert result.exit_code == 2, result.output
    assert rows(result)["prompt.system#rules"] == ["deployed_ahead", "changed", "connector"]


def test_edits_on_both_sides_are_diverged(tmp_path: Path, fake: FakeConnector) -> None:
    root = target_root(tmp_path)
    sync(root)
    local_prompt(root).write_text(EDITED_RULES, encoding="utf-8")
    fake.set_prompt("local", "system", OTHER_RULES)

    result = sync(root, "--check")

    assert result.exit_code == 2, result.output
    assert rows(result)["prompt.system#rules"] == ["diverged", "changed", "connector"]


def test_the_same_edit_on_both_sides_is_diverged_with_a_stale_record(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path)
    sync(root)
    local_prompt(root).write_text(EDITED_RULES, encoding="utf-8")
    fake.set_prompt("local", "system", EDITED_RULES)

    result = sync(root, "--check", "--json")

    assert result.exit_code == 2, result.output
    state = next(s for s in json.loads(result.stdout)["sections"] if s["id"].endswith("#rules"))
    assert state["direction"] == "diverged"
    assert state["note"] == RECORD_IS_STALE
    assert state["local"] == state["deployed"] != state["recorded"]


def test_an_observed_prompt_the_read_omits_is_not_covered_with_its_reason(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path)
    edit_manifest(root, prompts={"system": "prompts/system.md", "greeting": "observed"})

    result = sync(root)

    assert result.exit_code == 0, result.output
    assert rows(result)["prompt.greeting"][:3] == ["not_covered", "-", "-"]
    assert f"{NOT_IN_READ} for observed pointer prompts.greeting" in result.stdout


def test_a_flow_the_deployed_side_gains_is_deployed_ahead_added_and_one_it_loses_removed(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path)
    sync(root)
    fake.deployed["local"]["flows"] = {"refund": {"steps": ["check", "pay"], "state": "on"}}

    gained = sync(root, "--check")
    sync(root)
    fake.deployed["local"]["flows"] = {}
    lost = sync(root, "--check")

    assert gained.exit_code == 2
    assert rows(gained)["flow.refund"] == ["deployed_ahead", "added", "connector"]
    assert lost.exit_code == 2
    assert rows(lost)["flow.refund"] == ["deployed_ahead", "removed", "-"]


def test_a_flow_switched_off_moves_its_section(tmp_path: Path, fake: FakeConnector) -> None:
    root = target_root(tmp_path)
    fake.deployed["local"]["flows"] = {"refund": {"steps": ["check"], "state": "on"}}
    sync(root)
    fake.deployed["local"]["flows"]["refund"]["state"] = "off"

    result = sync(root, "--check")

    assert rows(result)["flow.refund"] == ["deployed_ahead", "changed", "connector"]


def test_the_tier_and_a_tool_schema_are_covered_by_the_connector(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path)
    fake.deployed["local"]["tier"] = "standard"
    sync(root)
    fake.deployed["local"]["tier"] = "premium"
    schema = dict(fake.deployed["local"]["tools"]["lookup_order"], description="Look it up.")
    fake.set_tool("local", "lookup_order", schema)

    result = sync(root, "--check")

    assert rows(result)["tier"] == ["deployed_ahead", "changed", "connector"]
    assert rows(result)["tool.lookup_order"] == ["deployed_ahead", "changed", "connector"]
    assert fingerprint(root).sections["tier"].summary == "standard"


# --- the Run-level mapping and the exit codes (ADR-0011 §4, D32) ---


def test_with_no_fingerprint_sync_check_is_not_checked_exit_3(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path)

    result = sync(root, "--check")

    assert result.exit_code == 3, result.output
    assert "not checked: no_fingerprint" in result.stdout
    assert breaks(root) == []


def test_a_read_that_covers_nothing_is_not_checked_adapter_cannot_observe(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path, path_pointer=False)
    fake.deployed["local"] = {}

    result = sync(root, "--check", "--json")

    assert result.exit_code == 3, result.output
    assert json.loads(result.stdout)["reason"] == "adapter_cannot_observe"


def test_held_is_exit_0_and_broken_exit_2_naming_the_sections(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path)
    sync(root)
    held = sync(root, "--check", "--json")
    fake.set_prompt("local", "system", EDITED_RULES)
    broken = sync(root, "--check", "--json")

    assert held.exit_code == 0
    assert json.loads(held.stdout)["status"] == "held"
    assert json.loads(held.stdout)["covered_by"] == "connector"
    assert broken.exit_code == 2
    result = json.loads(broken.stdout)
    assert result["status"] == "broken"
    moved = [s["id"] for s in result["sections"] if s["direction"] != "identical"]
    assert moved == ["prompt.system#rules"]


def test_sync_writes_the_fingerprint_from_the_connectors_read(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path)

    sync(root)

    written = fingerprint(root)
    assert {section.covered_by for section in written.sections.values()} == {"connector"}
    assert written.not_covered == []


# --- a failing read (decision 25) ---


def test_a_failing_connector_read_is_a_sync_error_naming_it(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path)
    sync(root)
    fake.raise_on("read_deployed_set", ConnectorError("the platform answered 503"))

    for arguments in ((), ("--check",)):
        result = sync(root, *arguments)

        assert result.exit_code == 3
        assert "the Connector's read failed: the platform answered 503" in result.output
    assert breaks(root) == []


def test_an_unknown_connector_kind_is_refused_naming_the_installed_kinds(tmp_path: Path) -> None:
    root = target_root(tmp_path, kind="acme")

    result = sync(root)

    assert result.exit_code == 3
    assert "no Connector of kind 'acme' is installed (installed: inprocess)" in result.output


@pytest.mark.parametrize("missing", ["AGENTDIAG_TEST_TOKEN", "AGENTDIAG_TEST_SECRET"])
def test_each_missing_credential_refuses_sync_naming_its_variable(
    tmp_path: Path, fake: FakeConnector, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    credentials = {"token": "AGENTDIAG_TEST_TOKEN", "secret": "AGENTDIAG_TEST_SECRET"}
    root = target_root(tmp_path, environments={"local": {"credentials": credentials}})
    for variable in credentials.values():
        monkeypatch.setenv(variable, f"value-of-{variable}")
    monkeypatch.delenv(missing)

    result = sync(root)

    role = next(role for role, variable in credentials.items() if variable == missing)
    assert result.exit_code == 3
    assert f"environment 'local' needs ${missing} for its {role}; it is not set" in result.output
    assert "the Connector's credentials for 'local' did not resolve" in result.output
    assert "read failed" not in result.output
    assert fake.calls == [("read_deployed_set", "local", None)]


def test_a_missing_credential_of_another_environment_blocks_neither_sync_nor_a_run(
    tmp_path: Path, fake: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0011 §2 fails closed per environment: `$PROD_TOKEN` is `prod`'s alone."""
    root = target_root(
        tmp_path,
        path_pointer=False,
        environments={"local": {}, "prod": {"credentials": {"token": "PROD_TOKEN"}}},
    )
    monkeypatch.delenv("PROD_TOKEN", raising=False)
    fake.set_prompt("local", "system", SYSTEM_PROMPT)

    synced = sync(root)
    ran = run(root)
    refused = sync(root, "--env", "prod", "--check")

    assert synced.exit_code == 0, synced.output
    assert ran.exit_code in (0, 1), ran.output
    assert "stands in" not in ran.stderr
    assert refused.exit_code == 3
    assert "environment 'prod' needs $PROD_TOKEN for its token" in refused.output


def test_a_credential_value_an_error_echoes_is_scrubbed_everywhere_it_is_written(
    tmp_path: Path, fake: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = target_root(
        tmp_path,
        path_pointer=False,
        environments={"local": {"credentials": {"token": "AGENTDIAG_TEST_TOKEN"}}},
    )
    monkeypatch.setenv("AGENTDIAG_TEST_TOKEN", "tok-s3cret-123")
    fake.set_prompt("local", "system", SYSTEM_PROMPT)
    sync(root)
    fake.raise_on("read_deployed_set", ConnectorError("401 for bearer tok-s3cret-123 at /agents/7"))

    refused = sync(root, "--check")
    ran = run(root)

    (directory,) = sorted((root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir())
    written = (directory / "run.json").read_text(encoding="utf-8")
    for text in (refused.output, ran.stderr, ran.stdout, written):
        assert "tok-s3cret-123" not in text
    assert "401 for bearer $AGENTDIAG_TEST_TOKEN at /agents/7" in refused.output
    assert "$AGENTDIAG_TEST_TOKEN" in json.loads(written)["sync"]["connector_failed"]


@pytest.mark.parametrize("missing", ["DEV_TOKEN", "PROD_TOKEN"])
def test_a_missing_credential_is_never_borrowed_from_another_environment(missing: str) -> None:
    """ADR-0011 §2, the `eu_prod → dev` trap: only the environment's own variable
    is read, and nothing else."""
    manifest = Manifest.model_validate(
        {
            "target": {"name": "t"},
            "adapter": {"kind": "inprocess", "environments": {"default": "dev", "dev": {}}},
            "connector": {
                "kind": "inprocess",
                "environments": {
                    "dev": {"credentials": {"token": "DEV_TOKEN"}},
                    "prod": {"credentials": {"token": "PROD_TOKEN"}},
                },
            },
        }
    )
    environment = "dev" if missing == "DEV_TOKEN" else "prod"
    present = {"DEV_TOKEN": "d", "PROD_TOKEN": "p"}
    del present[missing]
    read: list[str] = []

    class Recording(dict[str, str]):
        def get(self, key: str, default: Any = None) -> Any:
            read.append(key)
            return super().get(key, default)

    with pytest.raises(ConnectorError, match=rf"needs \${missing} for its token"):
        resolve_environment(manifest, environment, Recording(present))
    assert read == [missing]


def test_a_resolved_environment_never_dumps_its_credentials() -> None:
    manifest = Manifest.model_validate(
        {
            "target": {"name": "t"},
            "adapter": {"kind": "inprocess", "environments": {"default": "prod", "prod": {}}},
            "connector": {
                "kind": "inprocess",
                "environments": {"prod": {"agent_id": 7, "credentials": {"token": "TOKEN"}}},
            },
        }
    )

    resolved = resolve_environment(manifest, "prod", {"TOKEN": "s3cret"})

    assert resolved.credentials == {"token": "s3cret"}
    assert resolved.identifiers == {"agent_id": 7}
    assert resolved.protected is True
    assert "s3cret" not in resolved.model_dump_json() and "s3cret" not in repr(resolved)


def run(root: Path, *arguments: str) -> Any:
    return runner.invoke(
        app,
        [
            "run",
            "--root",
            str(root),
            "--scenario",
            SCENARIO,
            "--replay",
            str(RECORDING),
            *arguments,
        ],
    )


def test_a_run_whose_connector_read_fails_falls_back_to_the_probe_and_warns(
    tmp_path: Path, fake: FakeConnector
) -> None:
    """What only the Connector covered (a Flow, the tier) is `not_covered` for this Run,
    never `removed`, and survives in `fingerprint.json`."""
    root = target_root(tmp_path, path_pointer=False)
    fake.set_prompt("local", "system", SYSTEM_PROMPT)
    fake.deployed["local"]["flows"] = {"refund": {"steps": ["check"], "state": "on"}}
    fake.deployed["local"]["tier"] = "standard"
    sync(root)
    before = (root / ".agentdiag" / "targets" / "toy-order-desk" / "fingerprint.json").read_bytes()
    fake.raise_on("read_deployed_set", ConnectorError("the platform answered 503"))

    result = run(root)

    assert result.exit_code in (0, 1), result.output
    assert "the Adapter's probe stands in, because the Connector's read failed" in result.stderr
    (directory,) = sorted((root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir())
    recorded = json.loads((directory / "run.json").read_text(encoding="utf-8"))["sync"]
    assert recorded["covered_by"] == "adapter"
    assert "the platform answered 503" in recorded["connector_failed"]
    assert recorded["status"] == "held", "the probe reads what the fake read: nothing moved"
    assert (
        root / ".agentdiag" / "targets" / "toy-order-desk" / "fingerprint.json"
    ).read_bytes() == before
    assert {"flow.refund", "tier"} <= set(fingerprint(root).sections)
    assert breaks(root) == []


def test_a_run_whose_connector_read_fails_on_a_broken_sync_does_not_resync(
    tmp_path: Path, fake: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = target_root(tmp_path, path_pointer=False)
    fake.set_prompt("local", "system", SYSTEM_PROMPT)
    fake.deployed["local"]["flows"] = {"refund": {"steps": ["check"], "state": "on"}}
    fake.deployed["local"]["tier"] = "standard"
    sync(root)
    before = (root / ".agentdiag" / "targets" / "toy-order-desk" / "fingerprint.json").read_bytes()
    fake.raise_on("read_deployed_set", ConnectorError("the platform answered 503"))
    monkeypatch.setattr(PROMPT_READ_AT, SYSTEM_PROMPT.replace("three sentences", "two sentences"))

    result = run(root)

    # The edited Target no longer matches the recording, so its Trial is `invalid` (exit 2):
    # what is asserted here is the Sync, not the Trial.
    assert result.exit_code in (0, 1, 2), result.output
    assert "not re-synced: the Connector's read failed" in result.stderr
    (directory,) = sorted((root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir())
    recorded = json.loads((directory / "run.json").read_text(encoding="utf-8"))["sync"]
    assert recorded["status"] == "broken"
    assert recorded["resynced_from"] is None
    assert [(s["id"], s["direction"]) for s in recorded["sections"]] == [
        ("prompt.system", "deployed_ahead")
    ]
    assert (
        root / ".agentdiag" / "targets" / "toy-order-desk" / "fingerprint.json"
    ).read_bytes() == before


def test_a_run_reads_the_deployed_side_through_the_connector(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path, path_pointer=False)
    sync(root)

    result = run(root)

    assert result.exit_code in (0, 1), result.output
    (directory,) = sorted((root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir())
    recorded = json.loads((directory / "run.json").read_text(encoding="utf-8"))["sync"]
    assert recorded["status"] == "held"
    assert recorded["covered_by"] == "connector"
    assert recorded["connector_failed"] is None


# --- Sync breaks (decision 26) ---


def test_sync_check_on_broken_records_a_sync_break_and_on_held_none(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path)
    sync(root)
    sync(root, "--check")
    assert breaks(root) == []
    recorded = fingerprint(root)
    fake.set_prompt("local", "system", EDITED_RULES)

    result = sync(root, "--check")

    (path,) = breaks(root)
    assert result.stdout.splitlines()[-1] == f"recorded Sync break {path}"
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["opened_by"] == "sync"
    assert written["fingerprint"] == recorded.id
    assert written["environment"] == "local"
    assert written["covered_by"] == "connector"
    assert [(s["id"], s["direction"]) for s in written["sections"]] == [
        ("prompt.system#rules", "deployed_ahead")
    ]


def test_the_registry_counts_open_breaks_and_a_sync_closes_them(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path)
    sync(root)
    fake.set_prompt("local", "system", EDITED_RULES)
    sync(root, "--check")
    local_prompt(root).write_text(OTHER_RULES, encoding="utf-8")
    sync(root, "--check")

    opened = runner.invoke(app, ["registry", "--root", str(root), "--json"])
    table = runner.invoke(app, ["registry", "--root", str(root)])
    shown = runner.invoke(app, ["target", "show", "--root", str(root)])
    first = breaks(root)[0].read_bytes()
    local_prompt(root).write_text(EDITED_RULES, encoding="utf-8")
    sync(root)
    closed = runner.invoke(app, ["registry", "--root", str(root), "--json"])

    (entry,) = json.loads(opened.stdout)
    assert entry["sync"]["status"] == "broken"
    assert entry["sync"]["open_breaks"] == 2
    assert "broken (2 open Sync breaks)" in table.stdout
    lines = shown.stdout.splitlines()
    assert "sync            broken (2 open Sync breaks)" in lines
    assert "sync breaks     2 open; run `agentdiag sync` to re-record the Fingerprint" in lines
    assert any(
        line.strip().startswith(".agentdiag/targets/toy-order-desk/sync-breaks/")
        and line.endswith("prompt.system#rules deployed_ahead")
        for line in lines
    )
    (after,) = json.loads(closed.stdout)
    assert after["sync"]["status"] == "held"
    assert after["sync"]["open_breaks"] == 0
    assert after["sync"]["fingerprint"] == fingerprint(root).id
    assert breaks(root)[0].read_bytes() == first  # nothing edits a break file


def test_a_repeated_check_records_no_duplicate_and_says_which_break_is_open(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path)
    sync(root)
    fake.set_prompt("local", "system", EDITED_RULES)

    sync(root, "--check")
    again = sync(root, "--check")

    (path,) = breaks(root)
    assert again.exit_code == 2
    assert f"notice: this break is already open as {path}" in again.stderr
    assert "recorded Sync break" not in again.stdout


def test_break_files_opened_in_one_second_sort_oldest_first(tmp_path: Path) -> None:
    from agentdiag.sync.breaks import load_breaks, record_break
    from agentdiag.sync.compare import SectionState, SyncResult
    from agentdiag.workspace import TargetPaths

    target = TargetPaths(slug="t", directory=tmp_path)
    written = []
    for number, direction in enumerate(("deployed_ahead", "local_ahead", "diverged")):
        result = SyncResult(
            status="broken",
            fingerprint="f" * 64,
            sections=[SectionState(id=f"tool.t{number}", kind="tool", direction=direction)],
        )
        recorded = record_break(target, result, opened_at="2026-09-27T10:00:00Z")
        assert recorded is not None
        written.append(recorded.path.name)

    assert written == [
        "20260927T100000Z.json",
        "20260927T100000Z_002.json",
        "20260927T100000Z_003.json",
    ]
    assert [path.name for path, _ in load_breaks(target)] == written
    assert sorted(written) == written


def test_a_local_edit_sync_cannot_settle_leaves_its_break_open(
    tmp_path: Path, fake: FakeConnector
) -> None:
    """Every broken section `local_ahead`: the Fingerprint records the deployed side, which
    did not move, so re-recording changes nothing and only a push would settle it."""
    root = target_root(tmp_path)
    sync(root)
    local_prompt(root).write_text(EDITED_RULES, encoding="utf-8")

    sync(root)
    listed = runner.invoke(app, ["registry", "--root", str(root), "--json"])

    (entry,) = json.loads(listed.stdout)
    assert entry["sync"] == {
        "status": "broken",
        "open_breaks": 1,
        "fingerprint": fingerprint(root).id,
        "built_at": fingerprint(root).built_at,
        "environment": fingerprint(root).environment,
        "last_push": None,
    }


def test_a_run_on_a_broken_sync_records_no_sync_break(tmp_path: Path, fake: FakeConnector) -> None:
    root = target_root(tmp_path, path_pointer=False)
    sync(root)
    fake.set_tool(
        "local",
        "lookup_order",
        dict(fake.deployed["local"]["tools"]["lookup_order"], description="Changed."),
    )

    result = run(root)

    assert result.exit_code in (0, 1), result.output
    assert breaks(root) == []
    assert fingerprint(root).resynced_from is not None


# --- the first toy's own Connector, and the probe without one ---


def toy(tmp_path: Path) -> Path:
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    return root


def test_the_first_toy_is_fingerprinted_through_its_in_process_connector(tmp_path: Path) -> None:
    root = toy(tmp_path)

    result = sync(root)

    assert result.exit_code == 0, result.output
    assert {row[2] for row in rows(result).values()} == {"connector"}
    assert set(rows(result)) == {
        "model",
        "prompt.system",
        "provider",
        "tool.cancel_order",
        "tool.lookup_order",
    }


def test_a_manifest_with_no_connector_is_fingerprinted_by_the_adapters_probe(
    tmp_path: Path,
) -> None:
    root = toy(tmp_path)
    edit_manifest(root, connector=None)

    result = sync(root)

    assert result.exit_code == 0, result.output
    assert {row[2] for row in rows(result).values()} == {"adapter"}


def test_the_toy_through_its_connector_and_through_the_probe_hash_alike(tmp_path: Path) -> None:
    """The in-process Connector reads what the running module sends: one Fingerprint id."""
    through_connector = toy(tmp_path / "c")
    through_probe = toy(tmp_path / "p")
    edit_manifest(through_probe, connector=None)

    sync(through_connector)
    sync(through_probe)

    assert fingerprint(through_connector).id == fingerprint(through_probe).id


def test_an_environment_only_the_connector_names_is_read_without_a_probe(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = target_root(tmp_path, environments={"local": {}, "prod": {}})
    fake.deployed["prod"] = dict(fake.deployed["local"])

    result = sync(root, "--env", "prod")
    unknown = sync(root, "--env", "nowhere")

    assert result.exit_code == 0, result.output
    assert fingerprint(root).environment == "prod"
    assert fake.calls[-1] == ("read_deployed_set", "prod", None)
    assert unknown.exit_code == 3
    assert "names no Adapter or Connector environment 'nowhere'; it names local, prod" in (
        unknown.output
    )


def test_the_in_process_connector_refuses_to_write_the_first_toys_callable(tmp_path: Path) -> None:
    """Phase-7 decision 15: the first toy's deployed set is a function, which cannot be
    edited in place, so the write is refused naming it."""
    from agentdiag.connector.base import WriteRefused
    from agentdiag.connector.plugins import build_connector
    from agentdiag.run.manifest import load_manifest
    from agentdiag.workspace import Workspace

    manifest = load_manifest(Workspace.find(toy(tmp_path)).resolve(None))
    connector = build_connector(manifest)
    assert connector is not None

    with pytest.raises(WriteRefused, match="deployed_set is a callable, not a mutable mapping"):
        connector.write_deployed_set(
            "local", {"prompt.system": "x"}, expected_fingerprint="0" * 64, change_record=None
        )


def test_the_connector_package_imports_no_model_client_and_no_sdk() -> None:
    """Decision 18: `connector` is offline at import time, as `sync` is."""
    probe = (
        "import sys, agentdiag.connector, agentdiag.connector.inprocess, "
        "agentdiag.connector.plugins, agentdiag.sync.breaks; "
        "leaked = sorted(m for m in sys.modules if m == 'anthropic' "
        "or m.startswith(('anthropic.', 'agentdiag.model', 'agentdiag.adapter'))); "
        "print(','.join(leaked))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=REPO
    )

    assert completed.stdout.strip() == ""
