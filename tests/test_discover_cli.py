"""Seam 1: `agentdiag discover` over a temporary Workspace built from `init` (ticket 11).

The scan path is asked of the shipped toy Target, whose checked-in Manifest is the answer
(phase-6 decision 27's last bullet); the Connector path over the fake Connector, served
through the plugin registry's test seam, and over the toy's own in-process Connector. Every
test reads what the command wrote: the draft, its REVIEW lines, the files saved from the
deployed set, and what `validate --manifest` and `sync` make of an accepted draft.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.connector.plugins import registered
from agentdiag.discover.draft import REVIEW
from agentdiag.examples.toy import SYSTEM_PROMPT, TOOL_SCHEMAS, deployed_set
from tests.fakes.fake_connector import FAKE_KIND, FakeConnector

REPO = Path(__file__).resolve().parents[1]
TOY_SOURCE = REPO / "src" / "agentdiag" / "examples" / "toy"
HELPDESK_SOURCE = Path("src") / "agentdiag" / "examples" / "helpdesk"
TOY_MANIFEST = (
    REPO / "examples" / "toy" / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
)
COMPARED = ("target", "adapter", "tools", "prompts", "connector")

FAKE_PROMPT = "# Persona\n\nA patient clerk.\n\n# Rules\n\n1. Look up first.\n"

runner = CliRunner()


def invoke(*arguments: str) -> Any:
    return runner.invoke(app, list(arguments))


def workspace(tmp_path: Path) -> Path:
    """A Workspace from `init`: the Target `default`, the toy's scaffold."""
    root = tmp_path / "shop"
    result = invoke("init", "--root", str(root))
    assert result.exit_code == 0, result.output
    return root


def target_dir(root: Path, slug: str = "default") -> Path:
    return root / ".agentdiag" / "targets" / slug


def draft_of(root: Path, slug: str = "default") -> Path:
    return target_dir(root, slug) / "manifest.draft.yaml"


def loaded(path: Path) -> dict[str, Any]:
    """The draft with its REVIEW comments (every comment) stripped: YAML's own reading."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def edit_manifest(root: Path, **blocks: Any) -> None:
    path = target_dir(root) / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest.update(blocks)
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def reviewed(path: Path, key: str) -> str:
    """The REVIEW line directly above the first line opening with `key:`."""
    return reviewed_in(path.read_text(encoding="utf-8"), key)


def reviewed_in(text: str, key: str) -> str:
    lines = text.splitlines()
    wanted = key if ":" in key else f"{key}:"
    at = next(i for i, line in enumerate(lines) if line.strip().startswith(wanted))
    above = lines[at - 1].strip()
    assert above.startswith(REVIEW), f"{key}: has no REVIEW line above it"
    return above


# --- the scan ---


def test_the_scan_of_the_toy_drafts_the_checked_in_manifest(tmp_path: Path) -> None:
    root = workspace(tmp_path)

    result = invoke(
        "discover", "--root", str(root), "--target", "toy-order-desk", "--scan", str(TOY_SOURCE)
    )

    assert result.exit_code == 0, result.output
    draft = loaded(draft_of(root, "toy-order-desk"))
    expected = yaml.safe_load(TOY_MANIFEST.read_text(encoding="utf-8"))
    assert {key: draft[key] for key in COMPARED} == {key: expected[key] for key in COMPARED}
    assert "suites" not in draft
    assert "judge_notes" not in draft


def test_every_guessed_line_of_the_scan_draft_carries_a_review_line(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    invoke("discover", "--root", str(root), "--target", "toy", "--scan", str(TOY_SOURCE))
    path = draft_of(root, "toy")

    for key in ("name", "description", "side_effects", "default", "factory", "model"):
        assert reviewed(path, key)
    for key in ("kind: inprocess", "deployed"):
        assert reviewed(path, key)
    text = path.read_text(encoding="utf-8")
    connector = text[text.index("\nconnector:") :]
    assert reviewed_in(connector, "kind")
    for key in ("system", "lookup_order", "cancel_order"):
        assert reviewed(path, key)
    assert "agentdiag.examples.toy:SYSTEM_PROMPT" in reviewed(path, "system")
    assert 'starts with "lookup"' in reviewed(path, "lookup_order")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert sum(line.strip().startswith(REVIEW) for line in lines) == 15
    # Friction 7: the persona and the channel, commented out under REVIEW.
    assert "# family: your-persona" in lines and "# channel: chat" in lines
    # Friction 8: the header never spells the marker, so the grep can come back empty.
    header = "\n".join(lines[:6])
    assert "REVIEW:" not in header


def test_the_draft_validates_and_an_accepted_draft_syncs(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    invoke("discover", "--root", str(root), "--target", "toy", "--scan", str(TOY_SOURCE))
    draft = draft_of(root, "toy")

    checked = invoke("validate", "--root", str(root), "--target", "toy", "--manifest", str(draft))
    assert checked.exit_code == 0, checked.output
    assert "0 errors" in checked.stdout

    draft.rename(target_dir(root, "toy") / "manifest.yaml")
    synced = invoke("sync", "--root", str(root), "--target", "toy")
    assert synced.exit_code == 0, synced.output
    assert (target_dir(root, "toy") / "fingerprint.json").is_file()
    assert invoke("sync", "--root", str(root), "--target", "toy", "--check").exit_code == 0


def test_the_draft_lists_the_suites_and_notes_the_target_directory_holds(
    tmp_path: Path,
) -> None:
    root = workspace(tmp_path)
    (target_dir(root) / "manifest.yaml").unlink()

    result = invoke("discover", "--root", str(root), "--scan", str(TOY_SOURCE))

    assert result.exit_code == 0, result.output
    draft = loaded(draft_of(root))
    assert draft["suites"] == ["suites/sample.yaml"]
    assert draft["judge_notes"] == "judge_notes.md"
    assert draft["target"]["name"] == "toy"


def test_the_existing_manifest_s_facts_are_kept_without_review_lines(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    suppression = {
        "id": "sup-001",
        "eval": "prompt_adherence",
        "from": "2026-09-01",
        "until": "2026-09-30",
        "pattern": "refund",
        "why": "known",
    }
    edit_manifest(root, family="order-desk", channel="chat", suppressions=[suppression])

    invoke("discover", "--root", str(root), "--scan", str(TOY_SOURCE))

    draft = loaded(draft_of(root))
    assert draft["target"]["name"] == "toy-order-desk"
    assert (draft["family"], draft["channel"]) == ("order-desk", "chat")
    assert draft["suites"] == ["suites/sample.yaml"]
    assert draft["suppressions"][0]["id"] == "sup-001"
    text = draft_of(root).read_text(encoding="utf-8")
    target_block = text.split("target:")[1].split("adapter:")[0]
    # Friction 10: a name that is not the slug is the one thing said about the identity.
    assert target_block.count(REVIEW) == 1
    assert "the name differs from the slug default" in target_block


def test_a_rescan_keeps_every_existing_entry_and_adds_only_what_is_absent(
    tmp_path: Path,
) -> None:
    root = workspace(tmp_path)
    manifest = yaml.safe_load((target_dir(root) / "manifest.yaml").read_text(encoding="utf-8"))
    environments = manifest["adapter"]["environments"]
    environments["prod"] = {"factory": "desk.live:make_target", "protected": True}
    del environments["local"]["tools"]
    manifest["tools"]["cancel_order"] = {"kind": "action", "side_effects": "live"}
    edit_manifest(root, adapter=manifest["adapter"], tools=manifest["tools"])

    result = invoke("discover", "--root", str(root), "--scan", str(TOY_SOURCE))

    assert result.exit_code == 0, result.output
    draft = loaded(draft_of(root))
    assert draft["adapter"]["environments"]["prod"] == {
        "factory": "desk.live:make_target",
        "protected": True,
    }
    assert draft["tools"]["cancel_order"] == {"kind": "action", "side_effects": "live"}
    assert draft["adapter"]["environments"]["local"]["tools"] == "agentdiag.examples.toy:make_tools"
    path = draft_of(root)
    assert "make_tools returns a mapping" in reviewed(path, "tools: agentdiag")
    text = path.read_text(encoding="utf-8")
    for line in ("factory: desk.live:make_target", "cancel_order:", "lookup_order:", "prod:"):
        above = text.splitlines()[
            text.splitlines().index(
                next(
                    candidate
                    for candidate in text.splitlines()
                    if candidate.strip().startswith(line)
                )
            )
            - 1
        ]
        assert REVIEW not in above, line


def test_the_scan_defaults_to_the_workspace_root_and_finds_prompt_files(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "agent" / "prompts").mkdir(parents=True)
    (root / "agent" / "prompts" / "system.md").write_text("# Rules\n\n1. Be brief.\n")
    (root / "agent" / "tools").mkdir()
    (root / "agent" / "tools" / "get_order.json").write_text(
        json.dumps({"name": "get_order", "input_schema": {"type": "object"}})
    )
    (root / ".agentdiag").mkdir()

    result = invoke("discover", "--root", str(root))

    assert result.exit_code == 0, result.output
    draft = loaded(draft_of(root))
    assert draft["prompts"] == {"system": "../../../agent/prompts/system.md"}
    assert draft["tools"] == {
        "get_order": {"kind": "retrieval", "schema": "../../../agent/tools/get_order.json"}
    }
    # Nothing named a factory or a model: both are written commented out, under REVIEW.
    assert draft["adapter"]["environments"]["local"] is None
    assert "# factory:" in draft_of(root).read_text(encoding="utf-8")
    checked = invoke("validate", "--root", str(root), "--manifest", str(draft_of(root)))
    assert checked.exit_code == 0, checked.output


def test_discover_with_no_workspace_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "fresh"
    root.mkdir()
    monkeypatch.chdir(root)

    result = invoke("discover", "--scan", str(TOY_SOURCE))

    assert result.exit_code == 3
    assert "agentdiag init" in result.output
    assert not (root / ".agentdiag").exists()


def test_discover_never_writes_the_manifest_itself(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    before = (target_dir(root) / "manifest.yaml").read_bytes()

    refused = invoke(
        "discover",
        "--root",
        str(root),
        "--scan",
        str(TOY_SOURCE),
        "--out",
        str(target_dir(root) / "manifest.yaml"),
    )
    written = invoke("discover", "--root", str(root), "--scan", str(TOY_SOURCE))

    assert refused.exit_code == 3
    assert "the Manifest itself" in refused.output
    assert written.exit_code == 0
    assert (target_dir(root) / "manifest.yaml").read_bytes() == before


def test_out_names_where_the_draft_goes_inside_the_workspace(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    out = root / "drafts" / "draft.yaml"

    result = invoke("discover", "--root", str(root), "--scan", str(TOY_SOURCE), "--out", str(out))

    assert result.exit_code == 0, result.output
    assert out.is_file()
    assert not draft_of(root).exists()


def test_out_outside_the_workspace_root_is_refused(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    out = tmp_path / "elsewhere" / "draft.yaml"

    result = invoke("discover", "--root", str(root), "--scan", str(TOY_SOURCE), "--out", str(out))

    assert result.exit_code == 3
    assert str(root) in result.output
    assert not out.exists()


def test_a_draft_edited_in_a_workspace_outside_git_is_kept_unless_forced(
    tmp_path: Path,
) -> None:
    root = workspace(tmp_path)
    invoke("discover", "--root", str(root), "--scan", str(TOY_SOURCE))
    edited = draft_of(root).read_text(encoding="utf-8").replace("# REVIEW:", "# accepted:")
    draft_of(root).write_text(edited, encoding="utf-8")

    refused = invoke("discover", "--root", str(root), "--scan", str(TOY_SOURCE))

    assert refused.exit_code == 3
    assert str(draft_of(root)) in refused.output
    assert draft_of(root).read_text(encoding="utf-8") == edited
    forced = invoke("discover", "--root", str(root), "--scan", str(TOY_SOURCE), "--force")
    assert forced.exit_code == 0, forced.output
    assert draft_of(root).read_text(encoding="utf-8") != edited


def test_a_rerun_that_would_write_the_same_bytes_is_no_overwrite(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    invoke("discover", "--root", str(root), "--scan", str(TOY_SOURCE))

    again = invoke("discover", "--root", str(root), "--scan", str(TOY_SOURCE))

    assert again.exit_code == 0, again.output
    assert "Unchanged:" in again.stdout


def test_several_targets_and_no_target_is_exit_3_naming_them(tmp_path: Path) -> None:
    root = tmp_path / "shop"
    for slug in ("a", "b"):
        assert invoke("init", "--root", str(root), "--target", slug).exit_code == 0

    result = invoke("discover", "--root", str(root), "--scan", str(TOY_SOURCE))

    assert result.exit_code == 3
    assert "a, b" in result.output


def test_env_without_from_connector_and_from_connector_without_env_are_refused(
    tmp_path: Path,
) -> None:
    root = workspace(tmp_path)

    assert invoke("discover", "--root", str(root), "--env", "local").exit_code == 3
    assert invoke("discover", "--root", str(root), "--from-connector").exit_code == 3


# --- the Connector's read ---


@pytest.fixture
def fake() -> Iterator[FakeConnector]:
    read = deployed_set()
    read["prompts"] = {"system": FAKE_PROMPT}
    read["model"] = "claude-haiku-5"
    connector = FakeConnector({"dev": read})
    with registered(FAKE_KIND, connector.as_kind()):
        yield connector


def fake_workspace(tmp_path: Path) -> Path:
    root = workspace(tmp_path)
    edit_manifest(
        root,
        connector={"kind": FAKE_KIND, "environments": {"dev": {"site": "desk-dev"}}},
    )
    return root


def test_from_connector_saves_the_deployed_set_and_points_the_draft_at_it(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = fake_workspace(tmp_path)

    result = invoke("discover", "--root", str(root), "--from-connector", "--env", "dev")

    assert result.exit_code == 0, result.output
    directory = target_dir(root)
    assert (directory / "prompts" / "system.md").read_text(encoding="utf-8") == FAKE_PROMPT
    for schema in TOOL_SCHEMAS:
        saved = directory / "tools" / f"{schema['name']}.json"
        assert json.loads(saved.read_text(encoding="utf-8")) == schema
    draft = loaded(draft_of(root))
    manifest = loaded(target_dir(root) / "manifest.yaml")
    # Friction 2: the Manifest's values stay as written; the read's pointers are proposals.
    # Friction 2: the Manifest's values stay as written; a value the read would change is a
    # proposal above it, and an absent key under a block is inserted under REVIEW.
    assert draft["prompts"] == manifest["prompts"]
    assert draft["tools"] == {
        name: {**entry, "schema": f"tools/{name}.json"} for name, entry in manifest["tools"].items()
    }
    assert "schema saved from the Connector's read of dev" in reviewed(draft_of(root), "schema")
    assert draft["connector"]["environments"] == {"dev": {"site": "desk-dev"}}
    assert draft["adapter"]["environments"]["local"]["model"] == "claude-sonnet-5"
    model = reviewed(draft_of(root), "model")
    assert (
        "Connector read from dev" in model
        and "proposed: adapter.environments.local.model: claude-haiku-5" in model
    )
    assert ("read_deployed_set", "dev", None) in fake.calls
    assert not [call for call in fake.calls if call[0] == "write_deployed_set"]
    checked = invoke("validate", "--root", str(root), "--manifest", str(draft_of(root)))
    assert checked.exit_code == 0, checked.output


def test_from_connector_on_an_unknown_environment_names_the_ones_there_are(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = fake_workspace(tmp_path)

    result = invoke("discover", "--root", str(root), "--from-connector", "--env", "prod")

    assert result.exit_code == 3
    assert "names no environment 'prod'" in result.output
    assert "dev" in result.output
    assert not draft_of(root).exists()


def test_from_connector_with_a_missing_credential_names_the_variable(
    tmp_path: Path, fake: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = workspace(tmp_path)
    monkeypatch.delenv("DESK_DEV_TOKEN", raising=False)
    edit_manifest(
        root,
        connector={
            "kind": FAKE_KIND,
            "environments": {"dev": {"credentials": {"token": "DESK_DEV_TOKEN"}}},
        },
    )

    result = invoke("discover", "--root", str(root), "--from-connector", "--env", "dev")

    assert result.exit_code == 3
    assert "DESK_DEV_TOKEN" in result.output
    assert not (target_dir(root) / "prompts").exists()


def test_from_connector_with_no_connector_anywhere_is_refused(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    edit_manifest(root, connector=None)

    result = invoke("discover", "--root", str(root), "--from-connector", "--env", "local")

    assert result.exit_code == 3
    assert "builds the Connector manifest.yaml names" in result.output


def test_from_connector_on_a_target_with_no_manifest_and_no_scan_is_refused(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    (root / ".agentdiag").mkdir(parents=True)

    result = invoke("discover", "--root", str(root), "--from-connector", "--env", "local")

    assert result.exit_code == 3
    assert "no Manifest yet" in result.output
    assert not draft_of(root).exists()


def git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *arguments],
        check=True,
        capture_output=True,
    )


def test_from_connector_refuses_to_overwrite_uncommitted_changes_unless_forced(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = fake_workspace(tmp_path)
    assert (
        invoke("discover", "--root", str(root), "--from-connector", "--env", "dev").exit_code == 0
    )
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "discovered")
    prompt = target_dir(root) / "prompts" / "system.md"
    prompt.write_text("# Rules\n\nAn edit nobody committed.\n", encoding="utf-8")
    fake.set_prompt("dev", "system", FAKE_PROMPT + "2. A new rule.\n")

    refused = invoke("discover", "--root", str(root), "--from-connector", "--env", "dev")
    assert refused.exit_code == 3
    assert "git does not hold" in refused.output
    assert str(prompt) in refused.output
    assert prompt.read_text(encoding="utf-8") == "# Rules\n\nAn edit nobody committed.\n"

    forced = invoke("discover", "--root", str(root), "--from-connector", "--env", "dev", "--force")
    assert forced.exit_code == 0, forced.output
    assert prompt.read_text(encoding="utf-8") == FAKE_PROMPT + "2. A new rule.\n"


def test_from_connector_overwrites_a_committed_unchanged_file(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = fake_workspace(tmp_path)
    assert (
        invoke("discover", "--root", str(root), "--from-connector", "--env", "dev").exit_code == 0
    )
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "discovered")
    fake.set_prompt("dev", "system", "# Rules\n\nCommitted before.\n")

    again = invoke("discover", "--root", str(root), "--from-connector", "--env", "dev")

    assert again.exit_code == 0, again.output
    prompt = target_dir(root) / "prompts" / "system.md"
    assert prompt.read_text(encoding="utf-8") == "# Rules\n\nCommitted before.\n"


def test_from_connector_outside_git_refuses_a_file_it_would_change_unless_forced(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = fake_workspace(tmp_path)
    assert (
        invoke("discover", "--root", str(root), "--from-connector", "--env", "dev").exit_code == 0
    )
    fake.set_prompt("dev", "system", "# Rules\n\nOutside git.\n")
    prompt = target_dir(root) / "prompts" / "system.md"

    refused = invoke("discover", "--root", str(root), "--from-connector", "--env", "dev")
    assert refused.exit_code == 3
    assert str(prompt) in refused.output
    assert prompt.read_text(encoding="utf-8") == FAKE_PROMPT

    forced = invoke("discover", "--root", str(root), "--from-connector", "--env", "dev", "--force")
    assert forced.exit_code == 0, forced.output
    assert prompt.read_text(encoding="utf-8") == "# Rules\n\nOutside git.\n"


def test_two_section_names_with_one_file_name_get_distinct_files(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = fake_workspace(tmp_path)
    fake.deployed["dev"]["prompts"] = {"greeting a": "Hello.\n", "greeting-a": "Hi.\n"}

    result = invoke("discover", "--root", str(root), "--from-connector", "--env", "dev")

    assert result.exit_code == 0, result.output
    prompts = target_dir(root) / "prompts"
    assert (prompts / "greeting-a.md").read_text(encoding="utf-8") == "Hello.\n"
    assert (prompts / "greeting-a-2.md").read_text(encoding="utf-8") == "Hi.\n"
    assert loaded(draft_of(root))["prompts"] == {
        "system": "observed",
        "greeting a": "prompts/greeting-a.md",
        "greeting-a": "prompts/greeting-a-2.md",
    }


def test_from_connector_over_the_toy_s_in_process_connector_syncs_identical(
    tmp_path: Path,
) -> None:
    root = workspace(tmp_path)

    result = invoke("discover", "--root", str(root), "--from-connector", "--env", "local")

    assert result.exit_code == 0, result.output
    directory = target_dir(root)
    assert (directory / "prompts" / "system.md").read_text(encoding="utf-8") == SYSTEM_PROMPT + "\n"
    draft = loaded(draft_of(root))
    assert draft["connector"] == {
        "kind": "inprocess",
        "environments": {"local": {"deployed": "agentdiag.examples.toy:deployed_set"}},
    }
    assert draft["target"]["name"] == "toy-order-desk"
    draft_of(root).replace(directory / "manifest.yaml")
    assert invoke("sync", "--root", str(root)).exit_code == 0
    checked = invoke("sync", "--root", str(root), "--check")
    assert checked.exit_code == 0, checked.output
    table = checked.stdout
    assert "prompt.system" in table and "identical" in table


def test_from_connector_never_builds_the_connector_the_scan_only_guessed(
    tmp_path: Path,
) -> None:
    root = workspace(tmp_path)
    edit_manifest(root, connector=None)

    result = invoke(
        "discover",
        "--root",
        str(root),
        "--scan",
        str(TOY_SOURCE),
        "--from-connector",
        "--env",
        "local",
    )

    assert result.exit_code == 3
    assert "manifest.yaml" in result.output
    assert "accept" in result.output
    assert not (target_dir(root) / "prompts").exists()


def test_scan_and_from_connector_together_read_the_manifest_s_connector(tmp_path: Path) -> None:
    root = workspace(tmp_path)

    result = invoke(
        "discover",
        "--root",
        str(root),
        "--scan",
        str(TOY_SOURCE),
        "--from-connector",
        "--env",
        "local",
    )

    assert result.exit_code == 0, result.output
    text = draft_of(root).read_text(encoding="utf-8")
    assert "proposed: prompts.system: prompts/system.md" in text
    assert loaded(draft_of(root))["prompts"] == {"system": "observed"}


def test_friction_2_a_second_pass_keeps_every_existing_line_byte_for_byte(tmp_path: Path) -> None:
    """Walkthrough friction 2: the second pass rewrote the draft from the Manifest's keys and
    dropped every review comment. Now the Manifest's text is kept, comments included, and
    only new lines are inserted, each under its REVIEW line."""
    root = workspace(tmp_path)
    path = target_dir(root) / "manifest.yaml"
    authored = path.read_text(encoding="utf-8").replace(
        "  side_effects: none\n",
        "  # Nothing leaves this process: the order table is a copy per session.\n"
        "  side_effects: none\n",
    )
    path.write_text(authored, encoding="utf-8")

    result = invoke("discover", "--root", str(root), "--scan", str(TOY_SOURCE))

    assert result.exit_code == 0, result.output
    draft = draft_of(root).read_text(encoding="utf-8")
    kept = [line for line in draft.splitlines(keepends=True) if REVIEW not in line]
    inserted = [line for line in kept if line not in authored.splitlines(keepends=True)]
    assert "  # Nothing leaves this process: the order table is a copy per session.\n" in kept
    assert "".join(line for line in kept if line not in inserted) == authored
    assert all(line.lstrip().startswith("#") or not line.strip() for line in inserted), inserted


def test_friction_9_the_scan_drafts_the_evidence_store_beside_the_deployed_set(
    tmp_path: Path,
) -> None:
    """Walkthrough friction 9: the help desk's `platform.EVIDENCE` was absent from both
    drafts; a mapping of stores by kind in the deployed set's module is drafted as
    `connector.evidence.<kind>.rows` under REVIEW."""
    root = workspace(tmp_path)

    invoke("discover", "--root", str(root), "--target", "hd", "--scan", str(REPO / HELPDESK_SOURCE))

    draft = loaded(draft_of(root, "hd"))
    assert draft["connector"]["evidence"] == {
        "proxy": {"rows": "agentdiag.examples.helpdesk.platform:EVIDENCE"}
    }
    assert "holds proxy rows" in reviewed(draft_of(root, "hd"), "proxy")
    assert sorted(draft["tools"]) == ["escalate", "open_ticket", "search_articles"]


def test_friction_10_init_s_placeholder_description_is_marked_for_review(tmp_path: Path) -> None:
    root = tmp_path / "shop"
    invoke(
        "init", "--root", str(root), "--adapter", "python:agentdiag.examples.helpdesk:make_helpdesk"
    )

    invoke("discover", "--root", str(root), "--scan", str(REPO / HELPDESK_SOURCE))

    assert "this is the placeholder `agentdiag init` writes" in reviewed(
        draft_of(root), "description"
    )


def test_a_json_file_holding_no_tool_leaves_discover_at_exit_0(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    (root / "data").mkdir()
    (root / "data" / "empty.json").write_text("null", encoding="utf-8")
    (root / "data" / "list.json").write_text("[1, 2]", encoding="utf-8")

    result = invoke("discover", "--root", str(root))

    assert result.exit_code == 0, result.output


def test_the_discover_package_imports_no_model_client_and_no_sdk() -> None:
    """`discover` is offline at import time (the Phase 6 gate), as `sync` is."""
    probe = (
        "import sys, agentdiag.discover.command, agentdiag.discover.scan, "
        "agentdiag.discover.draft; "
        "leaked = sorted(m for m in sys.modules if m == 'anthropic' "
        "or m.startswith(('anthropic.', 'agentdiag.model', 'agentdiag.adapter'))); "
        "print(','.join(leaked))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=REPO
    )

    assert completed.stdout.strip() == ""


# --- the second walk ---


def custom_help_desk(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    invoke(
        "init",
        "--root",
        str(root),
        "--target",
        "help-desk",
        "--adapter",
        "python:agentdiag.examples.helpdesk:make_helpdesk",
        "--tools",
        "agentdiag.examples.helpdesk:make_tools",
    )
    return root


def test_second_walk_2_a_commented_key_is_never_proposed_twice(tmp_path: Path) -> None:
    """Second walk friction 2: over init's scaffold, which already holds `# family:` and
    `# channel:` commented out, the draft proposed both again; and it added a real
    `connector:` block beside init's commented one without a word."""
    root = custom_help_desk(tmp_path)

    invoke(
        "discover",
        "--root",
        str(root),
        "--target",
        "help-desk",
        "--scan",
        str(REPO / HELPDESK_SOURCE),
    )

    text = draft_of(root, "help-desk").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert sum(line.startswith("# family:") for line in lines) == 1
    assert sum(line.startswith("# channel:") for line in lines) == 1
    above = lines[lines.index("connector:") - 1]
    assert above.startswith(REVIEW) and "commented-out `connector` block" in above
    assert "# connector:" in lines, "the placeholder stays: deleting it is the reviewer's"


def test_second_walk_3_every_proposal_takes_the_one_form_and_the_short_pointer(
    tmp_path: Path,
) -> None:
    """Second walk friction 3: the description's REVIEW said "the scan proposes: …", and
    pointer proposals were spelled `{path: …, local_only: false}` where the skill teaches a
    bare path."""
    root = custom_help_desk(tmp_path)
    invoke(
        "discover",
        "--root",
        str(root),
        "--target",
        "help-desk",
        "--scan",
        str(REPO / HELPDESK_SOURCE),
    )
    description = reviewed(draft_of(root, "help-desk"), "description")
    draft_of(root, "help-desk").rename(target_dir(root, "help-desk") / "manifest.yaml")

    invoke(
        "discover",
        "--root",
        str(root),
        "--target",
        "help-desk",
        "--from-connector",
        "--env",
        "local",
    )

    text = draft_of(root, "help-desk").read_text(encoding="utf-8")
    assert (
        "; proposed: target.description: A help desk with a headed markdown prompt" in description
    )
    assert "proposes:" not in text + description
    assert "; proposed: prompts.system: prompts/system.md" in text
    assert "; proposed: tools.escalate: {kind: action, schema: tools/escalate.json}" in text
    assert "local_only" not in text


def test_second_walk_4_a_record_is_pointed_at_the_module_that_defines_it(
    tmp_path: Path,
) -> None:
    """Second walk friction 4: the scan named the package that re-exports the deployed set
    and the Evidence (`agentdiag.examples.helpdesk:DEPLOYED`) where their home is
    `…helpdesk.platform`. A data record is named where it is defined; a callable keeps the
    package path its users import it by (the toy's `deployed_set`, a factory, the tools)."""
    root = workspace(tmp_path)

    invoke("discover", "--root", str(root), "--target", "hd", "--scan", str(REPO / HELPDESK_SOURCE))

    draft = loaded(draft_of(root, "hd"))
    assert draft["connector"]["environments"]["local"]["deployed"] == (
        "agentdiag.examples.helpdesk.platform:DEPLOYED"
    )
    assert draft["connector"]["evidence"]["proxy"]["rows"] == (
        "agentdiag.examples.helpdesk.platform:EVIDENCE"
    )
    assert draft["adapter"]["environments"]["local"]["tools"] == (
        "agentdiag.examples.helpdesk:make_tools"
    ), "a callable keeps the package path its users import it by"
