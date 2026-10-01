"""End to end: a local fix pushed to the help desk's protected `staging` through the in-process
Connector, confirmed by the typed name, and read back by `sync --check` as held (ticket 27,
phase-7 decisions 13 to 16).

A temporary copy of `examples/workspace/`, committed in git. The help desk's `staging` is a
platform record of its own (`platform.STAGING`), so the push writes there and `local`
(`platform.DEPLOYED`) is untouched; the test puts both back after itself. No network, no
model: the Connector reads and writes a module's mapping.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentdiag.examples.helpdesk import platform
from agentdiag.sync.pushes import PushRecord
from tests.fakes.push_workspace import commit_all, git, invoke

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = REPO / "examples" / "workspace"
SLUG = "help-desk"
RULE_5 = "5. Never ask for a password, a full card number or a security code"
SCENARIO = "rules-rule-5-changing-the-payment-card-asks-for-no-card"
RECORDING = REPO / "tests" / "fixtures" / "recordings" / "helpdesk.jsonl"
FIXED = "5. Never ask for or repeat a password, a full card number, a security code or a PIN"


@pytest.fixture
def platform_restored() -> Iterator[None]:
    kept = {name: copy.deepcopy(getattr(platform, name)) for name in ("DEPLOYED", "STAGING")}
    yield
    for name, value in kept.items():
        held = getattr(platform, name)
        held.clear()
        held.update(value)


@pytest.fixture
def root(tmp_path: Path, platform_restored: None) -> Path:
    copied = tmp_path / "workspace"
    shutil.copytree(
        WORKSPACE,
        copied,
        ignore=shutil.ignore_patterns("runs", "restore-points", "platform", "index.sqlite"),
    )
    git(copied, "init", "-q")
    commit_all(copied, "the example Workspace")
    return copied


def target_dir(root: Path) -> Path:
    return root / ".agentdiag" / "targets" / SLUG


def test_a_local_fix_reaches_staging_with_the_typed_name_and_sync_then_holds(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = target_dir(root) / "prompts" / "system.md"
    text = local.read_text(encoding="utf-8")
    assert RULE_5 in text
    local.write_text(text.replace(RULE_5, FIXED), encoding="utf-8")
    complaint = root.parent / "complaint.md"
    complaint.write_text("The help desk repeated my PIN back to me.\n", encoding="utf-8")
    opened = invoke(
        "change",
        "open",
        "--root",
        root,
        "--target",
        SLUG,
        "--complaint",
        complaint,
        "--layer",
        "rules",
        "--title",
        "PIN repeated back",
    )
    assert opened.exit_code == 0, opened.output
    record = opened.stdout.split()[1]
    proposed = invoke(
        "change",
        "propose",
        "--root",
        root,
        "--target",
        SLUG,
        record,
        "--section",
        "prompt.system#rules",
        "--file",
        "prompts/system.md",
    )
    assert proposed.exit_code == 0, proposed.output
    local_before = copy.deepcopy(platform.DEPLOYED)

    shown = invoke("push", "--root", root, "--target", SLUG, "--env", "staging")
    assert shown.exit_code == 0, shown.output
    assert "staging is protected: the push needs --push, the environment's name" in shown.stdout
    assert f"+{FIXED}" in shown.stdout

    monkeypatch.setattr("agentdiag.cli._stdin_is_a_terminal", lambda: True)
    pushed = invoke(
        "push",
        "--root",
        root,
        "--target",
        SLUG,
        "--env",
        "staging",
        "--push",
        "--change",
        record,
        input="staging\n",
    )

    assert pushed.exit_code == 0, pushed.output
    assert FIXED in platform.STAGING["prompts"]["system"]
    assert local_before == platform.DEPLOYED, "a push to staging never reaches local"
    (path,) = sorted((target_dir(root) / "pushes").glob("*-staging.json"))
    push_record = PushRecord.model_validate_json(path.read_text(encoding="utf-8"))
    assert push_record.confirmed_by == "typed_name"
    assert push_record.effective_side_effects == "sandboxed"
    assert push_record.sections == ["prompt.system#rules"]
    assert push_record.change_record == record
    changed = json.loads(
        invoke("change", "show", "--root", root, "--target", SLUG, record, "--json").stdout
    )
    assert changed["status"] == "pushed"

    held = invoke("sync", "--root", root, "--target", SLUG, "--env", "staging", "--check")

    assert held.exit_code == 0, held.output
    assert held.stdout.startswith("Sync held")
    fingerprint = json.loads((target_dir(root) / "fingerprint.json").read_text(encoding="utf-8"))
    assert fingerprint["environment"] == "staging"
    assert fingerprint["pushed_from"] == push_record.fingerprint_before


def test_a_push_to_staging_from_a_session_with_no_terminal_is_handed_to_a_person(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = target_dir(root) / "prompts" / "system.md"
    local.write_text(local.read_text(encoding="utf-8").replace(RULE_5, FIXED), encoding="utf-8")
    staging_before = copy.deepcopy(platform.STAGING)
    monkeypatch.setattr("agentdiag.cli._stdin_is_a_terminal", lambda: False)
    complaint = root.parent / "complaint.md"
    complaint.write_text("The help desk repeated my PIN back to me.\n", encoding="utf-8")
    opened = invoke(
        "change",
        "open",
        "--root",
        root,
        "--target",
        SLUG,
        "--complaint",
        complaint,
        "--layer",
        "rules",
        "--title",
        "PIN repeated back",
    )
    record = opened.stdout.split()[1]
    proposed = invoke(
        "change",
        "propose",
        "--root",
        root,
        "--target",
        SLUG,
        record,
        "--section",
        "prompt.system#rules",
    )
    assert proposed.exit_code == 0, proposed.output

    result = invoke(
        "push",
        "--root",
        root,
        "--target",
        SLUG,
        "--env",
        "staging",
        "--push",
        "--change",
        record,
    )

    assert result.exit_code == 3
    assert "hand the push to a person on a terminal or in the UI" in result.stderr
    assert staging_before == platform.STAGING


def fresh(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """`agentdiag` in a new process: what a person's next shell command is."""
    return subprocess.run(
        [str(Path(sys.executable).parent / "agentdiag"), *arguments, "--root", str(root)],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )


def test_a_push_outlives_the_process_that_made_it(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The in-process platform keeps its record in `store` (ticket 27's fix round): after a
    push in one process, a new process's `sync --check` holds and its Run's Target is sent
    the pushed prompt, because Sync loads the store before any Trial."""
    local = target_dir(root) / "prompts" / "system.md"
    local.write_text(local.read_text(encoding="utf-8").replace(RULE_5, FIXED), encoding="utf-8")

    pushed = invoke("push", "--root", root, "--target", SLUG, "--env", "local", "--push")

    assert pushed.exit_code == 0, pushed.output
    store = target_dir(root) / "platform" / "local.json"
    assert FIXED in json.loads(store.read_text(encoding="utf-8"))["prompts"]["system"]
    assert "platform/" not in git(root, "status", "--porcelain"), "the store is gitignored"

    held = fresh(root, "sync", "--target", SLUG, "--check")
    assert held.returncode == 0, held.stdout + held.stderr
    assert held.stdout.startswith("Sync held")

    ran = fresh(root, "run", "--target", SLUG, "--scenario", SCENARIO, "--replay", str(RECORDING))
    assert "Traceback" not in ran.stderr, ran.stderr
    (run,) = sorted((target_dir(root) / "runs").iterdir())
    sent = (run / "trials" / SCENARIO / "1" / "trace.jsonl").read_text(encoding="utf-8")
    assert FIXED in sent, "the Target was sent the pushed prompt"
    recorded = json.loads((run / "run.json").read_text(encoding="utf-8"))
    assert recorded["sync"]["status"] == "held"
