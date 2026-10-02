"""Seam 1: `agentdiag push` over the fake Connector (ticket 27, ADR-0011 §6 to §8, phase-7
decisions 12 to 17).

The Workspace is the first toy with its prompt and lookup tool as path pointers, committed in
git, its Connector the fake serving `local` (unprotected) and `prod` (protected by its name)
and writing with the compare-and-swap (`tests.fakes.push_workspace`). A test makes the local
fix, previews, pushes, and reads what the push left: the fake's deployed set, the Restore
point, the rebuilt Fingerprint, the Push record, the Change record. Every refusal of
decisions 12 and 13, both confirmations (a faked terminal for the typed name), the Restore
point round trip, and `compare` across a push. No network, no model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from agentdiag.connector.base import ExpectedFingerprintMoved, WriteRefused
from agentdiag.run.manifest import load_manifest
from agentdiag.sync.push import PREVIEW_MAX_AGE_S, PushMoved, PushRefused, preview, push
from agentdiag.sync.pushes import PushRecord, RestorePoint
from agentdiag.workspace import Workspace
from tests.fakes.fake_connector import FakeConnector
from tests.fakes.push_workspace import (
    DEPLOYED_TONE,
    LOCAL_RULES,
    LOOKUP,
    PROMPT,
    fake,
    fingerprint,
    invoke,
    local_prompt,
    local_tool,
    target_dir,
    workspace,
)

__all__ = ["fake"]

REPO = Path(__file__).resolve().parents[1]
RECORDING = REPO / "tests" / "fixtures" / "recordings" / "toy-orders.jsonl"
SCENARIO = "cancel-processing-order"
RULES = "prompt.system#rules"
PULL_FIRST_TEXT = "pull, merge and push again"
HAND_OFF = "this environment is protected: hand the push to a person on a terminal or in the UI"


def ready(tmp_path: Path, env: str = "local", **manifest: Any) -> Path:
    """Synced against `env`, then the local fix to rule 2."""
    root = workspace(tmp_path, **manifest)
    assert invoke("sync", "--root", root, "--env", env).exit_code == 0
    local_prompt(root).write_text(LOCAL_RULES, encoding="utf-8")
    return root


def terminal(monkeypatch: pytest.MonkeyPatch, interactive: bool = True) -> None:
    monkeypatch.setattr("agentdiag.cli._stdin_is_a_terminal", lambda: interactive)


def push_records(root: Path) -> list[PushRecord]:
    directory = target_dir(root) / "pushes"
    return (
        [
            PushRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        ]
        if directory.is_dir()
        else []
    )


def restore_points(root: Path) -> list[Path]:
    directory = target_dir(root) / "restore-points"
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def open_proposed(root: Path) -> str:
    opened = invoke(
        "change",
        "open",
        "--root",
        root,
        "--complaint",
        complaint(root),
        "--layer",
        "rules",
        "--title",
        "Shipped order cancelled",
    )
    assert opened.exit_code == 0, opened.output
    record = opened.stdout.split()[1]
    proposed = invoke("change", "propose", "--root", root, record, "--section", RULES)
    assert proposed.exit_code == 0, proposed.output
    return record


def complaint(root: Path) -> Path:
    path = root.parent / "complaint.md"
    path.write_text("My shipped order was cancelled.\n", encoding="utf-8")
    return path


def nothing_written(fake: FakeConnector, root: Path) -> None:
    assert fake.writes == []
    assert push_records(root) == []
    assert fake.deployed["local"]["prompts"]["system"] == PROMPT
    assert fake.deployed["prod"]["prompts"]["system"] == PROMPT


# --- the preview (decision 12) ---


def test_without_push_it_previews_the_bytes_that_would_change_and_writes_nothing(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)

    result = invoke("push", "--root", root, "--env", "local")

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "Push preview: the local files to local"
    assert lines[1].startswith("deployed set read at ") and lines[1].endswith(
        "(the write expects it)"
    )
    assert f"{RULES}  prompts/system.md" in result.stdout
    assert "-2. Cancel only a processing order." in result.stdout
    assert "+2. Never cancel a shipped order." in result.stdout
    assert "local is not protected: --push writes it" in result.stdout
    nothing_written(fake, root)
    assert restore_points(root) == []


def test_the_json_preview_names_the_expected_fingerprint_class_and_confirmation(
    tmp_path: Path, fake: FakeConnector
) -> None:
    from agentdiag.sync.observe import deployed_fingerprint

    root = ready(tmp_path, "prod")

    result = invoke("push", "--root", root, "--env", "prod", "--json")

    assert result.exit_code == 0, result.output
    shown = json.loads(result.stdout)
    assert shown["expected_fingerprint"] == deployed_fingerprint(fake.read_deployed_set("prod"))
    assert [section["id"] for section in shown["sections"]] == [RULES]
    assert shown["protected"] is True
    assert shown["confirmation"] == "typed_name"
    assert shown["change_record_required"] is True
    assert shown["effective_side_effects"] == "sandboxed", "the fake's write is sandboxed"
    assert shown["restore_point"] == "restore-points/<time of the write>-prod.json"
    assert shown["refusals"] == []


def test_a_tool_schema_edit_is_previewed_and_pushed_as_the_file_holds_it(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = workspace(tmp_path)
    assert invoke("sync", "--root", root).exit_code == 0
    edited = {**LOOKUP, "description": "Look up one order by its id."}
    local_tool(root).write_text(json.dumps(edited, indent=2) + "\n", encoding="utf-8")

    result = invoke("push", "--root", root, "--env", "local", "--push")

    assert result.exit_code == 0, result.output
    assert fake.deployed["local"]["tools"]["lookup_order"] == edited


def test_a_heading_added_locally_is_added_to_the_deployed_prompt(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = workspace(tmp_path)
    assert invoke("sync", "--root", root).exit_code == 0
    added = PROMPT + "\n# Escalation\n\nHand refunds to a person.\n"
    local_prompt(root).write_text(added, encoding="utf-8")

    result = invoke("push", "--root", root, "--env", "local", "--push")

    assert result.exit_code == 0, result.output
    assert fake.deployed["local"]["prompts"]["system"] == added


# --- every refusal of decision 12 ---


def test_a_manifest_with_no_connector_is_refused(tmp_path: Path) -> None:
    root = workspace(tmp_path, connector=None)

    result = invoke("push", "--root", root, "--env", "local")

    assert result.exit_code == 3
    assert "refused: the Manifest names no Connector" in result.stdout


def test_a_failing_connector_read_is_refused(tmp_path: Path, fake: FakeConnector) -> None:
    from agentdiag.connector.base import ConnectorError

    root = ready(tmp_path)
    fake.raise_on("read_deployed_set", ConnectorError("the platform answered 503"))

    result = invoke("push", "--root", root, "--env", "local", "--push")

    assert result.exit_code == 3
    assert "the platform answered 503" in result.stdout
    assert fake.writes == []


def test_nothing_to_push_is_refused(tmp_path: Path, fake: FakeConnector) -> None:
    root = workspace(tmp_path)
    assert invoke("sync", "--root", root).exit_code == 0

    result = invoke("push", "--root", root, "--env", "local", "--push")

    assert result.exit_code == 3
    assert "refused: nothing to push" in result.stdout


def test_a_section_the_deployed_side_is_ahead_on_refuses_the_push_with_no_force(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)
    fake.set_prompt("local", "system", DEPLOYED_TONE)

    result = invoke("push", "--root", root, "--env", "local", "--push")

    assert result.exit_code == 3
    assert "prompt.system#tone deployed_ahead" in result.stdout
    assert "pull, merge and push again; there is no force flag" in result.stdout
    assert fake.writes == []
    assert "--force" not in invoke("push", "--help").stdout


def test_naming_only_the_local_fix_pushes_it_past_a_deployed_edit_elsewhere(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)
    fake.set_prompt("local", "system", DEPLOYED_TONE)

    result = invoke("push", "--root", root, "--env", "local", "--section", RULES, "--push")

    assert result.exit_code == 0, result.output
    assert fake.deployed["local"]["prompts"]["system"] == LOCAL_RULES.replace(
        "Plain and brief.", "Warm, and at most three sentences."
    )


def test_a_diverged_section_refuses_the_push(tmp_path: Path, fake: FakeConnector) -> None:
    root = ready(tmp_path)
    fake.set_prompt("local", "system", PROMPT.replace("Look up before you answer.", "Look up."))

    result = invoke("push", "--root", root, "--env", "local", "--push")

    assert result.exit_code == 3
    assert f"{RULES} diverged" in result.stdout
    assert fake.writes == []


def test_a_pointer_outside_the_workspace_root_is_refused(
    tmp_path: Path, fake: FakeConnector
) -> None:
    outside = tmp_path / "elsewhere" / "system.md"
    outside.parent.mkdir()
    outside.write_text(PROMPT, encoding="utf-8")
    root = workspace(tmp_path, prompts={"system": "../../../../elsewhere/system.md"})
    assert invoke("sync", "--root", root).exit_code == 0
    outside.write_text(LOCAL_RULES, encoding="utf-8")

    result = invoke("push", "--root", root, "--env", "local", "--push")

    assert result.exit_code == 3
    assert f"refused: {RULES} is outside the Workspace root" in result.stdout
    assert fake.writes == []


def test_a_named_section_that_is_not_a_candidate_is_refused(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)

    result = invoke("push", "--root", root, "--env", "local", "--section", "model", "--push")

    assert result.exit_code == 3
    assert "not a section this command would write: model" in result.stdout
    assert f"(the candidates are {RULES})" in result.stdout


def test_an_environment_the_connector_does_not_name_is_refused(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)

    result = invoke("push", "--root", root, "--env", "qa", "--push")

    assert result.exit_code == 3
    assert "names no environment 'qa' (it names local, prod)" in result.stdout


def test_a_target_with_no_fingerprint_is_refused_until_sync(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = workspace(tmp_path)
    local_prompt(root).write_text(LOCAL_RULES, encoding="utf-8")

    result = invoke("push", "--root", root, "--env", "local", "--push")

    assert result.exit_code == 3
    assert "has no Fingerprint to push against: run `agentdiag sync --env local` first" in (
        result.stdout
    )


def test_a_fix_pushed_to_one_environment_reaches_the_next_after_sync_against_it(
    tmp_path: Path, fake: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Target has one Fingerprint: after a push to `local` it is `local`'s, so `prod`'s
    older rule reads ahead of it. Pulling would undo the fix; the refusal says to record the
    Fingerprint against `prod` first (the fix-cycle walkthrough's friction)."""
    root = ready(tmp_path)
    assert invoke("push", "--root", root, "--env", "local", "--push").exit_code == 0
    record = open_proposed(root)

    refused = invoke("push", "--root", root, "--env", "prod")

    assert refused.exit_code == 3
    assert "that Fingerprint was recorded against 'local': run `agentdiag sync --env prod`" in (
        refused.stdout
    )
    assert PULL_FIRST_TEXT not in refused.stdout
    assert invoke("sync", "--root", root, "--env", "prod").exit_code == 0
    terminal(monkeypatch)
    pushed = invoke(
        "push", "--root", root, "--env", "prod", "--push", "--change", record, input="prod\n"
    )
    assert pushed.exit_code == 0, pushed.output
    assert fake.deployed["prod"]["prompts"]["system"] == LOCAL_RULES


# --- the write's own refusals (decision 13) ---


def target_and_manifest(root: Path) -> tuple[Any, Any]:
    target = Workspace.find(root).resolve(None)
    return target, load_manifest(target)


def test_a_protected_push_without_a_change_record_is_refused(
    tmp_path: Path, fake: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = ready(tmp_path, "prod")
    terminal(monkeypatch)

    result = invoke("push", "--root", root, "--env", "prod", "--push", input="prod\n")
    explicit = invoke(
        "push", "--root", root, "--env", "prod", "--push", "--change", "none", input="prod\n"
    )

    for refused in (result, explicit):
        assert refused.exit_code == 3
        assert "prod is protected: name the push's Change record with --change <id>" in (
            refused.stderr
        )
    nothing_written(fake, root)


def test_a_change_record_that_is_not_proposed_or_pushed_is_refused(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)
    opened = invoke(
        "change",
        "open",
        "--root",
        root,
        "--complaint",
        complaint(root),
        "--layer",
        "rules",
        "--title",
        "Still open",
    )
    record = opened.stdout.split()[1]

    result = invoke("push", "--root", root, "--env", "local", "--push", "--change", record)
    missing = invoke(
        "push", "--root", root, "--env", "local", "--push", "--change", "20990101-no-such"
    )

    assert result.exit_code == 3
    assert f"Change record {record} is open: a push names a record that is proposed" in (
        result.stderr
    )
    assert missing.exit_code == 3 and "no Change record '20990101-no-such'" in missing.stderr
    nothing_written(fake, root)
    assert restore_points(root) == [], "nothing is saved before the refusals are checked"


def test_push_on_a_protected_environment_is_never_confirmed_by_the_flag(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path, "prod")
    record = open_proposed(root)
    target, manifest = target_and_manifest(root)
    shown = preview(target, manifest, "prod")

    with pytest.raises(PushRefused, match="--push does not stand in"):
        push(target, manifest, "prod", shown, change_record=record, confirmed="--push")
    nothing_written(fake, root)


def test_a_preview_older_than_five_minutes_is_refused(tmp_path: Path, fake: FakeConnector) -> None:
    from datetime import UTC, datetime, timedelta

    root = ready(tmp_path)
    target, manifest = target_and_manifest(root)
    shown = preview(target, manifest, "local")
    assert shown.read_at is not None
    read = datetime.strptime(shown.read_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    later = (read + timedelta(seconds=PREVIEW_MAX_AGE_S + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    with pytest.raises(PushRefused, match="preview again"):
        push(target, manifest, "local", shown, change_record=None, confirmed="--push", now=later)
    nothing_written(fake, root)


def test_a_deployed_set_that_moved_since_the_preview_is_refused_and_the_restore_point_kept(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)
    target, manifest = target_and_manifest(root)
    shown = preview(target, manifest, "local")
    fake.set_prompt("local", "system", DEPLOYED_TONE)
    before = fingerprint(root)

    with pytest.raises(PushMoved, match="moved"):
        push(target, manifest, "local", shown, change_record=None, confirmed="--push")

    assert fake.writes == []
    assert fake.deployed["local"]["prompts"]["system"] == DEPLOYED_TONE
    assert push_records(root) == []
    assert len(restore_points(root)) == 1, "the Restore point stays, harmless"
    assert fingerprint(root) == before


def test_a_moved_deployed_set_exits_2_and_a_refused_write_3(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)
    fake.raise_on("write_deployed_set", ExpectedFingerprintMoved("the deployed set moved"))
    moved = invoke("push", "--root", root, "--env", "local", "--push")
    fake.raise_on("write_deployed_set", WriteRefused("the platform is read-only today"))
    refused = invoke("push", "--root", root, "--env", "local", "--push")

    assert moved.exit_code == 2, moved.output
    assert "nothing was written" in moved.stderr
    assert refused.exit_code == 3, refused.output
    assert "the Connector refused the write: " in refused.stderr
    assert (
        "nothing was written; Restore point .agentdiag/targets/toy-order-desk/restore-points/"
        in (refused.stderr)
    )
    assert refused.stderr.rstrip().endswith("is kept")
    assert push_records(root) == []


# --- the confirmations (decision 14) ---


def test_push_on_an_unprotected_environment_writes_and_says_how_it_was_confirmed(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)

    result = invoke("push", "--root", root, "--env", "local", "--push")

    assert result.exit_code == 0, result.output
    assert fake.deployed["local"]["prompts"]["system"] == LOCAL_RULES
    assert fake.deployed["prod"]["prompts"]["system"] == PROMPT
    assert f"pushed {RULES} to local" in result.stdout
    (record,) = push_records(root)
    assert record.confirmed_by == "--push"
    assert record.change_record is None


def test_a_protected_push_asks_for_the_environments_name_on_a_terminal(
    tmp_path: Path, fake: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = ready(tmp_path, "prod")
    record = open_proposed(root)
    terminal(monkeypatch)

    result = invoke(
        "push", "--root", root, "--env", "prod", "--push", "--change", record, input="prod\n"
    )

    assert result.exit_code == 0, result.output
    assert "Type the environment's name to confirm the push to prod:" in result.stdout
    assert fake.deployed["prod"]["prompts"]["system"] == LOCAL_RULES
    (pushed,) = push_records(root)
    assert pushed.confirmed_by == "typed_name"
    assert pushed.change_record == record


def test_a_wrong_name_typed_is_refused_and_nothing_is_written(
    tmp_path: Path, fake: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = ready(tmp_path, "prod")
    record = open_proposed(root)
    terminal(monkeypatch)

    result = invoke(
        "push", "--root", root, "--env", "prod", "--push", "--change", record, input="local\n"
    )

    assert result.exit_code == 3
    assert "'local' is not 'prod'; nothing was written" in result.stderr
    nothing_written(fake, root)
    assert restore_points(root) == []


def test_a_non_interactive_session_is_told_to_hand_a_protected_push_to_a_person(
    tmp_path: Path, fake: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = ready(tmp_path, "prod")
    record = open_proposed(root)
    terminal(monkeypatch, interactive=False)

    result = invoke(
        "push", "--root", root, "--env", "prod", "--push", "--change", record, input="prod\n"
    )

    assert result.exit_code == 3
    assert HAND_OFF in result.stderr
    nothing_written(fake, root)


# --- what a push leaves (decisions 13, 17) ---


def test_the_push_record_holds_every_field(
    tmp_path: Path, fake: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = ready(tmp_path, "prod")
    record = open_proposed(root)
    before = fingerprint(root).id
    shown = json.loads(invoke("push", "--root", root, "--env", "prod", "--json").stdout)
    terminal(monkeypatch)
    monkeypatch.setenv("AGENTDIAG_AGENT", "claude-code")

    result = invoke(
        "push", "--root", root, "--env", "prod", "--push", "--change", record, input="prod\n"
    )

    assert result.exit_code == 0, result.output
    (path,) = sorted((target_dir(root) / "pushes").glob("*.json"))
    assert path.name.endswith("-prod.json")
    pushed = PushRecord.model_validate_json(path.read_text(encoding="utf-8"))
    after = fingerprint(root)
    assert pushed.schema_version == 1
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", pushed.pushed_at)
    assert pushed.target == "toy-order-desk"
    assert pushed.environment == "prod"
    assert pushed.by == "claude-code" == pushed.agent
    assert pushed.os_user and pushed.os_user != "claude-code"
    assert pushed.effective_side_effects == "sandboxed"
    assert pushed.confirmed_by == "typed_name"
    assert pushed.sections == [RULES]
    assert pushed.fingerprint_before == before
    assert pushed.fingerprint_after == after.id != before
    assert (target_dir(root) / pushed.restore_point).is_file()
    assert pushed.diff_sha256 == shown["diff_sha256"]
    assert pushed.change_record == record
    assert pushed.receipt.environment == "prod"
    assert pushed.receipt.sections == [RULES]
    assert pushed.receipt.fingerprint_before == shown["expected_fingerprint"]
    assert f"Push record .agentdiag/targets/toy-order-desk/pushes/{path.name}" in result.stdout


def test_the_restore_point_is_the_deployed_set_the_preview_read(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)

    assert invoke("push", "--root", root, "--env", "local", "--push").exit_code == 0

    (path,) = restore_points(root)
    point = RestorePoint.model_validate_json(path.read_text(encoding="utf-8"))
    assert point.environment == "local" and point.target == "toy-order-desk"
    assert point.deployed.prompts == {"system": PROMPT}
    (pushed,) = push_records(root)
    assert point.fingerprint == pushed.receipt.fingerprint_before
    assert f"restore-points/{path.name}" == pushed.restore_point


def test_the_rebuilt_fingerprint_names_the_one_it_replaced_and_sync_then_holds(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)
    before = fingerprint(root).id

    assert invoke("push", "--root", root, "--env", "local", "--push").exit_code == 0

    after = fingerprint(root)
    assert after.pushed_from == before
    assert after.resynced_from is None
    assert "pushed_from" in (target_dir(root) / "fingerprint.json").read_text(encoding="utf-8")
    assert invoke("sync", "--root", root, "--check").exit_code == 0


def test_a_fingerprint_no_push_rebuilt_is_written_without_pushed_from(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = workspace(tmp_path)

    assert invoke("sync", "--root", root).exit_code == 0

    assert "pushed_from" not in (target_dir(root) / "fingerprint.json").read_text(encoding="utf-8")


def test_the_change_record_gains_a_push_event_and_moves_to_pushed(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)
    record = open_proposed(root)

    result = invoke("push", "--root", root, "--env", "local", "--push", "--change", record)

    assert result.exit_code == 0, result.output
    shown = json.loads(invoke("change", "show", "--root", root, record, "--json").stdout)
    assert shown["status"] == "pushed"
    (event,) = shown["pushes"]
    (pushed,) = sorted((target_dir(root) / "pushes").glob("*.json"))
    assert event["kind"] == "connector"
    assert event["environment"] == "local"
    assert event["push_record"] == f"pushes/{pushed.name}"
    assert event["fingerprint_after"] == fingerprint(root).id
    assert f"Change record .agentdiag/targets/toy-order-desk/changes/{record}.md" in result.stdout

    again = local_prompt(root).read_text(encoding="utf-8").replace("Plain", "Short")
    local_prompt(root).write_text(again, encoding="utf-8")
    assert (
        invoke("push", "--root", root, "--env", "local", "--push", "--change", record).exit_code
        == 0
    )
    shown = json.loads(invoke("change", "show", "--root", root, record, "--json").stdout)
    assert shown["status"] == "pushed" and len(shown["pushes"]) == 2


# --- the Restore point round trip (decision 13, ADR-0011 §6b) ---


def test_push_restore_puts_the_deployed_set_back_under_the_same_rules(
    tmp_path: Path, fake: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = ready(tmp_path, "prod")
    record = open_proposed(root)
    terminal(monkeypatch)
    pushed = invoke(
        "push", "--root", root, "--env", "prod", "--push", "--change", record, input="prod\n"
    )
    assert pushed.exit_code == 0, pushed.output
    (point,) = restore_points(root)

    shown = invoke("push", "--root", root, "--env", "prod", "--restore", point.stem)
    assert shown.exit_code == 0, shown.output
    assert f"Push preview: Restore point restore-points/{point.name} to prod" in shown.stdout
    assert "-2. Never cancel a shipped order." in shown.stdout
    unconfirmed = invoke("push", "--root", root, "--env", "prod", "--restore", point.stem, "--push")
    assert unconfirmed.exit_code == 3, "a restore to a protected environment names its record"

    restored = invoke(
        "push",
        "--root",
        root,
        "--env",
        "prod",
        "--restore",
        str(point),
        "--push",
        "--change",
        record,
        input="prod\n",
    )

    assert restored.exit_code == 0, restored.output
    assert fake.deployed["prod"]["prompts"]["system"] == PROMPT
    first, second = push_records(root)
    assert second.sections == ["prompt.system"]
    assert second.confirmed_by == "typed_name"
    assert second.fingerprint_before == first.fingerprint_after
    assert len(restore_points(root)) == 2, "a restore is a push: it saves its own point"
    assert fingerprint(root).pushed_from == first.fingerprint_after


def test_a_restore_point_of_another_environment_is_refused(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)
    assert invoke("push", "--root", root, "--env", "local", "--push").exit_code == 0
    (point,) = restore_points(root)

    result = invoke("push", "--root", root, "--env", "prod", "--restore", point.stem)

    assert result.exit_code == 3
    assert "was saved from 'local', not 'prod'" in result.stdout


def test_an_unknown_restore_point_is_refused_naming_the_ones_there_are(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)

    result = invoke("push", "--root", root, "--env", "local", "--restore", "20260101T000000Z-x")

    assert result.exit_code == 3
    assert "the Restore point '20260101T000000Z-x' names nothing in" in result.stdout
    assert "restore-points, where Target toy-order-desk's Restore points are" in result.stdout


# --- compare across a push (ADR-0011 §6g, ADR-0005 §6) ---


def replayed(root: Path) -> str:
    """One replayed Run, by name: the directory that appeared, not the last one sorted,
    because two Runs made in one second share a timestamp and sort by their suffix."""
    runs = target_dir(root) / "runs"
    before = set(runs.iterdir()) if runs.is_dir() else set()
    result = invoke(
        "run", "--root", root, "--scenario", SCENARIO, "--replay", RECORDING, "--no-resync"
    )
    assert result.exit_code in (0, 1), result.output
    (made,) = set(runs.iterdir()) - before
    return made.name


def test_compare_labels_the_push_undeclared_unless_expect_fingerprint(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = workspace(tmp_path)
    assert invoke("sync", "--root", root).exit_code == 0
    baseline = replayed(root)
    local_prompt(root).write_text(LOCAL_RULES, encoding="utf-8")
    assert invoke("push", "--root", root, "--env", "local", "--push").exit_code == 0
    after = replayed(root)

    plain = json.loads(invoke("compare", "--root", root, baseline, after, "--json").stdout)
    declared = json.loads(
        invoke(
            "compare", "--root", root, baseline, after, "--expect", "fingerprint", "--json"
        ).stdout
    )

    moved = {
        d["path"]: d["declared"]
        for d in plain["differences"]
        if d["path"].startswith("fingerprint.")
    }
    assert "fingerprint.pushed_from" in moved
    assert f"fingerprint.sections.{RULES}.sha256" in moved
    assert not any(moved.values()), "undeclared without --expect fingerprint"
    declared_moved = {
        d["path"]: d["declared"]
        for d in declared["differences"]
        if d["path"].startswith("fingerprint.")
    }
    assert declared_moved and all(declared_moved.values())


def test_pull_push_and_the_push_records_import_no_sdk() -> None:
    """The offline-import gate (phase-7 tests): `sync.pull`, `sync.push`, `sync.pushes` and
    what they reach import no model client, as `sync` does not."""
    import subprocess
    import sys

    probe = (
        "import sys, agentdiag.sync.pull, agentdiag.sync.push, agentdiag.sync.pushes, "
        "agentdiag.sync.pointed; "
        "leaked = sorted(m for m in sys.modules if m == 'anthropic' "
        "or m.startswith(('anthropic.', 'agentdiag.model', 'agentdiag.adapter'))); "
        "print(','.join(leaked))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=REPO
    )

    assert completed.stdout.strip() == ""


def test_the_registry_and_target_show_name_the_last_pushes(
    tmp_path: Path, fake: FakeConnector
) -> None:
    """Phase-7 decision 17: `SyncSummary.last_push`, and `target show` lists the last three
    Push records, newest first."""
    root = ready(tmp_path)
    for tone in ("Short.", "Shorter.", "Shortest.", "Terse."):
        text = local_prompt(root).read_text(encoding="utf-8")
        local_prompt(root).write_text(
            re.sub(r"(# Tone\n\n).*\n", rf"\g<1>{tone}\n", text), encoding="utf-8"
        )
        assert invoke("push", "--root", root, "--env", "local", "--push").exit_code == 0
    records = push_records(root)

    (entry,) = json.loads(invoke("registry", "--root", root, "--json").stdout)
    shown = invoke("target", "show", "--root", root).stdout.splitlines()

    assert len(records) == 4
    assert entry["sync"]["last_push"] == records[-1].pushed_at
    assert "pushes            last 3, newest first" in shown
    listed = [line.strip() for line in shown if "/pushes/" in line]
    assert len(listed) == 3
    assert listed[0].startswith(".agentdiag/targets/toy-order-desk/pushes/")
    assert "local at " in listed[0] and "(--push), " in listed[0] and "change none" in listed[0]


# --- after the write (the ticket 27 fix round) ---


def test_a_failing_re_read_after_the_write_still_keeps_the_push_record(
    tmp_path: Path, fake: FakeConnector
) -> None:
    from agentdiag.connector.base import ConnectorError
    from agentdiag.sync.push import PushIncomplete

    root = ready(tmp_path)
    target, manifest = target_and_manifest(root)
    shown = preview(target, manifest, "local")
    before = fingerprint(root)
    fake.raise_on("read_deployed_set", ConnectorError("the platform answered 503"))

    with pytest.raises(PushIncomplete, match="the Fingerprint was not rebuilt after the write"):
        push(target, manifest, "local", shown, change_record=None, confirmed="--push")

    assert fake.deployed["local"]["prompts"]["system"] == LOCAL_RULES, "the write was made"
    (record,) = push_records(root)
    assert record.fingerprint_after == record.receipt.fingerprint_after
    assert any("the platform answered 503" in problem for problem in record.problems)
    assert fingerprint(root) == before, "nothing rebuilt it"


def test_a_change_record_step_that_fails_after_the_write_exits_3_with_no_traceback(
    tmp_path: Path, fake: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentdiag.change.lifecycle import ChangeRefused

    root = ready(tmp_path)
    record = open_proposed(root)

    def closed_meanwhile(*_: object, **__: object) -> Path:
        raise ChangeRefused(f"Change record {record} is wontfix: it cannot move to pushed")

    monkeypatch.setattr("agentdiag.sync.push.record_connector_push", closed_meanwhile)

    result = invoke("push", "--root", root, "--env", "local", "--push", "--change", record)

    assert result.exit_code == 3
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "the write was made and recorded in .agentdiag/targets/toy-order-desk/pushes/" in (
        result.stderr
    )
    assert f"Change record {record} gained no push event" in result.stderr
    (pushed,) = push_records(root)
    assert pushed.problems and "gained no push event" in pushed.problems[0]
    assert f"pushed {RULES} to local" in result.stdout


def test_a_change_record_closed_between_the_preview_and_the_write_refuses_it(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)
    record = open_proposed(root)
    target, manifest = target_and_manifest(root)
    shown = preview(target, manifest, "local")
    closed = invoke("change", "close", "--root", root, record, "--wontfix", "--why", "not worth it")
    assert closed.exit_code == 0, closed.output

    with pytest.raises(PushRefused, match=f"Change record {record} is wontfix"):
        push(target, manifest, "local", shown, change_record=record, confirmed="--push")
    nothing_written(fake, root)


def test_a_preview_whose_read_moment_does_not_parse_counts_as_stale(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)
    target, manifest = target_and_manifest(root)
    shown = preview(target, manifest, "local").model_copy(update={"read_at": "yesterday"})

    with pytest.raises(PushRefused, match="read at 'yesterday'"):
        push(target, manifest, "local", shown, change_record=None, confirmed="--push")
    nothing_written(fake, root)


def test_restore_with_a_section_writes_only_that_section_from_the_point(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)
    both = LOCAL_RULES.replace("Plain and brief.", "Terse.")
    local_prompt(root).write_text(both, encoding="utf-8")
    assert invoke("push", "--root", root, "--env", "local", "--push").exit_code == 0
    (point,) = restore_points(root)

    missing = invoke(
        "push", "--root", root, "--env", "local", "--restore", point.stem, "--section", "tool.x"
    )
    restored = invoke(
        "push",
        "--root",
        root,
        "--env",
        "local",
        "--restore",
        point.stem,
        "--section",
        "prompt.system#tone",
        "--push",
    )

    assert missing.exit_code == 3
    assert "not a section this command would write: tool.x" in missing.stdout
    assert "prompt.system#rules" in missing.stdout and "prompt.system#tone" in missing.stdout
    assert restored.exit_code == 0, restored.output
    assert fake.deployed["local"]["prompts"]["system"] == LOCAL_RULES, "rules kept, tone back"
    assert push_records(root)[-1].sections == ["prompt.system#tone"]


def test_a_restore_point_outside_the_restore_points_directory_is_refused(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path)
    assert invoke("push", "--root", root, "--env", "local", "--push").exit_code == 0
    (point,) = restore_points(root)
    elsewhere = tmp_path / "copied.json"
    elsewhere.write_bytes(point.read_bytes())

    result = invoke("push", "--root", root, "--env", "local", "--restore", elsewhere)

    assert result.exit_code == 3
    assert "is not under .agentdiag/targets/toy-order-desk/restore-points" in result.stdout


def test_a_redaction_file_that_does_not_read_refuses_the_push_before_any_write(
    tmp_path: Path, fake: FakeConnector
) -> None:
    """ADR-0015 §4: the push event is written redacted, so a redaction file of the wrong
    shape refuses the push before the Connector writes, and its contents are never echoed."""
    root = ready(tmp_path)
    record = open_proposed(root)
    redaction = target_dir(root) / "redaction.yaml"
    redaction.write_text("names: Zebulon Quist\n", encoding="utf-8")

    result = invoke("push", "--root", root, "--env", "local", "--push", "--change", record)

    assert result.exit_code == 3, result.output
    assert f"the redaction file {redaction} gives names that is not a list of strings" in (
        result.stderr
    )
    assert "Zebulon" not in result.output
    nothing_written(fake, root)
    assert restore_points(root) == []
    leaked = [
        path
        for path in root.rglob("*")
        if path.is_file() and path != redaction and b"Zebulon" in path.read_bytes()
    ]
    assert leaked == []
