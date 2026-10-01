"""Seam 1: `agentdiag pull` over the fake Connector (ticket 27, ADR-0011 §5, phase-7
decision 11).

The Workspace is the first toy with its prompt and lookup tool as path pointers, committed
in a git repository (`tests.fakes.push_workspace`); a test moves the deployed side through
the fake and asks `pull` to bring it home. No network, no model.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.fakes.fake_connector import FakeConnector
from tests.fakes.push_workspace import (
    DEPLOYED_TONE,
    LOCAL_RULES,
    LOOKUP,
    PROMPT,
    commit_all,
    fake,
    fingerprint,
    git,
    invoke,
    local_prompt,
    local_tool,
    target_dir,
    workspace,
)

__all__ = ["fake"]


def synced(tmp_path: Path, **manifest: object) -> Path:
    root = workspace(tmp_path, **manifest)
    assert invoke("sync", "--root", root).exit_code == 0
    commit_all(root, "synced")
    return root


def test_pull_writes_the_deployed_section_into_its_heading_and_prints_the_git_diff(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = synced(tmp_path)
    fake.set_prompt("local", "system", DEPLOYED_TONE)
    head = git(root, "rev-parse", "HEAD")
    recorded = (target_dir(root) / "fingerprint.json").read_bytes()

    result = invoke("pull", "--root", root, "--env", "local")

    assert result.exit_code == 0, result.output
    assert local_prompt(root).read_text(encoding="utf-8") == DEPLOYED_TONE
    lines = result.stdout.splitlines()
    assert lines[0] == "Pull from local: 1 section written, 0 skipped"
    assert lines[1].split() == ["written", "prompt.system#tone", "prompts/system.md"]
    assert "-Plain and brief." in result.stdout
    assert "+Warm, and at most three sentences." in result.stdout
    assert "diff --git" in result.stdout
    assert git(root, "rev-parse", "HEAD") == head, "pull never commits"
    assert (target_dir(root) / "fingerprint.json").read_bytes() == recorded


def test_pull_keeps_a_local_edit_to_another_heading(tmp_path: Path, fake: FakeConnector) -> None:
    root = synced(tmp_path)
    local_prompt(root).write_text(LOCAL_RULES, encoding="utf-8")
    commit_all(root, "the local fix")
    fake.set_prompt("local", "system", DEPLOYED_TONE)

    result = invoke("pull", "--root", root, "--env", "local")

    assert result.exit_code == 0, result.output
    assert local_prompt(root).read_text(encoding="utf-8") == LOCAL_RULES.replace(
        "Plain and brief.", "Warm, and at most three sentences."
    )


def test_a_heading_added_on_the_deployed_side_is_added_to_the_file(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = synced(tmp_path)
    fake.set_prompt("local", "system", PROMPT + "\n# Escalation\n\nHand refunds to a person.\n")

    result = invoke("pull", "--root", root, "--env", "local")

    assert result.exit_code == 0, result.output
    assert local_prompt(root).read_text(encoding="utf-8") == (
        PROMPT + "\n# Escalation\n\nHand refunds to a person.\n"
    )


def test_a_tool_schema_is_written_as_discover_spells_it(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = synced(tmp_path)
    edited = {**LOOKUP, "description": "Look up one order by its id."}
    fake.set_tool("local", "lookup_order", edited)

    result = invoke("pull", "--root", root, "--env", "local", "--section", "tool.lookup_order")

    assert result.exit_code == 0, result.output
    assert local_tool(root).read_text(encoding="utf-8") == json.dumps(edited, indent=2) + "\n"


def test_an_uncommitted_file_is_skipped_unless_overwrite_local(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = synced(tmp_path)
    local_prompt(root).write_text(PROMPT + "\nA draft line.\n", encoding="utf-8")
    fake.set_prompt("local", "system", DEPLOYED_TONE)

    kept = invoke("pull", "--root", root, "--env", "local")

    assert kept.exit_code == 0, kept.output
    assert kept.stdout.splitlines()[0] == "Pull from local: 0 sections written, 1 skipped"
    assert "skipped  prompt.system#tone  uncommitted: prompts/system.md" in kept.stdout
    assert local_prompt(root).read_text(encoding="utf-8") == PROMPT + "\nA draft line.\n"

    forced = invoke("pull", "--root", root, "--env", "local", "--overwrite-local")

    assert forced.exit_code == 0, forced.output
    assert "Warm, and at most three sentences." in local_prompt(root).read_text(encoding="utf-8")


def test_outside_git_every_pointed_file_counts_as_uncommitted(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = workspace(tmp_path, in_git=False)
    assert invoke("sync", "--root", root).exit_code == 0
    fake.set_prompt("local", "system", DEPLOYED_TONE)

    result = invoke("pull", "--root", root, "--env", "local")

    assert result.exit_code == 0, result.output
    assert "uncommitted" in result.stdout
    assert local_prompt(root).read_text(encoding="utf-8") == PROMPT


def test_a_pointer_outside_the_workspace_root_is_compared_never_written_and_named(
    tmp_path: Path, fake: FakeConnector
) -> None:
    outside = tmp_path / "elsewhere" / "system.md"
    outside.parent.mkdir()
    outside.write_text(PROMPT, encoding="utf-8")
    root = synced(tmp_path, prompts={"system": "../../../../elsewhere/system.md"})
    fake.set_prompt("local", "system", DEPLOYED_TONE)

    result = invoke("pull", "--root", root, "--env", "local")

    assert result.exit_code == 0, result.output
    assert "skipped  prompt.system#tone  outside the Workspace root: ../../../../elsewhere" in (
        result.stdout
    )
    assert outside.read_text(encoding="utf-8") == PROMPT


def test_a_named_section_that_is_not_a_candidate_is_refused_and_nothing_is_written(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = synced(tmp_path)
    fake.set_prompt("local", "system", DEPLOYED_TONE)

    result = invoke("pull", "--root", root, "--env", "local", "--section", "prompt.system#rules")

    assert result.exit_code == 3, result.output
    assert "not a section this command would write: prompt.system#rules" in result.stderr
    assert "nothing was pulled" in result.stderr
    assert "prompt.system#rules" in result.stderr
    assert local_prompt(root).read_text(encoding="utf-8") == PROMPT


def test_a_section_no_local_file_holds_is_skipped_saying_so(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = synced(tmp_path)
    fake.deployed["local"]["model"] = "claude-opus-5"

    result = invoke("pull", "--root", root, "--env", "local")

    assert result.exit_code == 0, result.output
    assert "skipped  model  no local file holds it" in result.stdout


def test_a_local_edit_alone_leaves_nothing_to_pull(tmp_path: Path, fake: FakeConnector) -> None:
    root = synced(tmp_path)
    local_prompt(root).write_text(LOCAL_RULES, encoding="utf-8")

    result = invoke("pull", "--root", root, "--env", "local")

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("Pull from local: nothing to pull")
    assert local_prompt(root).read_text(encoding="utf-8") == LOCAL_RULES


def test_a_failing_connector_read_refuses_the_pull(tmp_path: Path, fake: FakeConnector) -> None:
    from agentdiag.connector.base import ConnectorError

    root = synced(tmp_path)
    fake.raise_on("read_deployed_set", ConnectorError("the platform answered 503"))

    result = invoke("pull", "--root", root, "--env", "local")

    assert result.exit_code == 3
    assert "the platform answered 503" in result.stderr


def test_an_environment_the_connector_does_not_name_is_refused(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = synced(tmp_path)

    result = invoke("pull", "--root", root, "--env", "qa")

    assert result.exit_code == 3
    assert "names no environment 'qa' (it names local, prod)" in result.stderr


def test_a_manifest_with_no_connector_has_nothing_to_pull_from(tmp_path: Path) -> None:
    root = workspace(tmp_path, connector=None)

    result = invoke("pull", "--root", root, "--env", "local")

    assert result.exit_code == 3
    assert "names no Connector" in result.stderr


def test_pull_never_touches_the_fingerprint_so_the_next_sync_settles_it(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = synced(tmp_path)
    before = fingerprint(root).id
    fake.set_prompt("local", "system", DEPLOYED_TONE)
    assert invoke("pull", "--root", root, "--env", "local").exit_code == 0

    assert fingerprint(root).id == before
    assert invoke("sync", "--root", root).exit_code == 0
    assert invoke("sync", "--root", root, "--check").exit_code == 0


def test_a_malformed_local_tool_schema_is_a_refusal_not_a_traceback(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = synced(tmp_path)
    local_tool(root).write_text("{\n", encoding="utf-8")

    result = invoke("pull", "--root", root, "--env", "local")

    assert result.exit_code == 3
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "is not a JSON or YAML tool schema" in result.stderr


def test_a_pointed_file_pull_creates_is_in_the_diff(tmp_path: Path, fake: FakeConnector) -> None:
    root = synced(tmp_path)
    local_tool(root).unlink()
    commit_all(root, "the lookup schema removed locally")
    edited = {**LOOKUP, "description": "Look up one order by its id."}
    fake.set_tool("local", "lookup_order", edited)

    result = invoke("pull", "--root", root, "--env", "local")

    assert result.exit_code == 0, result.output
    assert local_tool(root).is_file()
    assert "written  tool.lookup_order" in result.stdout
    assert "--- /dev/null" in result.stdout
    assert "tools/lookup_order.json" in result.stdout
    assert '+  "description": "Look up one order by its id."' in result.stdout
    assert "?? " in git(root, "status", "--porcelain"), "nothing is staged"
