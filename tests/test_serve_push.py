"""Seam 1: a push from the UI, under ADR-0011 §6 (ticket 16, phase-7 decision 22).

The page previews through `POST /api/targets/<slug>/push/preview` and confirms through
`POST /api/targets/<slug>/push` with the preview's id, a Change record and the confirm step:
the push box (`{"push": true}`, the UI's `--push`) for an unprotected environment, the
environment's name typed (`{"typed_name": …}`) for a protected one, recorded `ui_confirm`.
First over the fake Connector (`tests.fakes.push_workspace`: `local` unprotected, `prod`
protected by its name), every refusal and both confirmations; then the exit-criterion walk:
one push from the UI to the help desk's protected `staging`, through the real routes on a
socket, read back by the next process's `sync --check`.

No network, no model.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agentdiag.examples.helpdesk import platform
from agentdiag.serve.app import App, Reply
from agentdiag.sync.push import PREVIEW_MAX_AGE_S
from agentdiag.sync.pushes import PushRecord
from agentdiag.workspace import Workspace
from tests.fakes.fake_connector import FakeConnector
from tests.fakes.push_workspace import (
    DEPLOYED_TONE,
    LOCAL_RULES,
    PROMPT,
    SLUG,
    commit_all,
    fake,
    fingerprint,
    git,
    invoke,
    local_prompt,
    target_dir,
    workspace,
)
from tests.serving import call, serving

__all__ = ["fake"]

REPO = Path(__file__).resolve().parents[1]
RULES = "prompt.system#rules"


class Clock:
    """The App's monotonic clock, moved by hand: how a preview goes stale in a test."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def ready(tmp_path: Path, env: str) -> Path:
    """Synced against `env`, then the local fix to rule 2."""
    root = workspace(tmp_path)
    assert invoke("sync", "--root", root, "--env", env).exit_code == 0
    local_prompt(root).write_text(LOCAL_RULES, encoding="utf-8")
    return root


def proposed_record(
    root: Path,
    *,
    target: str | None = None,
    complaint_text: str = "My shipped order was cancelled.",
    title: str = "Shipped order cancelled",
) -> str:
    """A Change record opened from a complaint and proposed over the rules section."""
    complaint = root.parent / "complaint.md"
    complaint.write_text(complaint_text + "\n", encoding="utf-8")
    named = ["--target", target] if target is not None else []
    opened = invoke(
        "change", "open", "--root", root, *named, "--complaint", complaint,
        "--layer", "rules", "--title", title,
    )  # fmt: skip
    assert opened.exit_code == 0, opened.output
    record = opened.stdout.split()[1]
    proposed = invoke("change", "propose", "--root", root, *named, record, "--section", RULES)
    assert proposed.exit_code == 0, proposed.output
    return record


def post(app: App, path: str, body: dict[str, Any]) -> Reply:
    return app.handle("POST", path, json.dumps(body).encode("utf-8"))


def previewed(app: App, env: str, slug: str = SLUG) -> Reply:
    reply = post(app, f"/api/targets/{slug}/push/preview", {"env": env})
    assert reply.status == 200, reply.body
    return reply


def pushed(app: App, preview_id: str, confirm: dict[str, Any], change: str | None = None) -> Reply:
    return post(
        app,
        f"/api/targets/{SLUG}/push",
        {"preview_id": preview_id, "change": change, "confirm": confirm},
    )


def push_records(root: Path) -> list[PushRecord]:
    directory = target_dir(root) / "pushes"
    return [
        PushRecord.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]


def refused(reply: Reply, status: int, *words: str) -> None:
    assert reply.status == status, reply.body
    for word in words:
        assert word in reply.body["error"], reply.body["error"]


# --- over the fake Connector ---


def test_the_preview_is_kept_under_an_id_and_shows_the_bytes_and_the_restore_point(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path, "local")
    app = App(Workspace.find(root))

    reply = previewed(app, "local")

    body = reply.body
    assert re.fullmatch(r"[0-9a-f]{16}", body["preview_id"])
    assert body["max_age_s"] == PREVIEW_MAX_AGE_S
    shown = body["preview"]
    assert [section["id"] for section in shown["sections"]] == [RULES]
    assert "+2. Never cancel a shipped order." in shown["sections"][0]["diff"]
    assert shown["restore_point"] == "restore-points/<time of the write>-local.json"
    assert shown["protected"] is False and shown["confirmation"] == "--push"
    assert "read" not in shown and shown["payload"]
    assert fake.writes == []


def test_an_unprotected_push_writes_on_the_push_box_and_refuses_without_it(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path, "local")
    app = App(Workspace.find(root))
    preview_id = previewed(app, "local").body["preview_id"]

    missing = pushed(app, preview_id, {})
    unticked = pushed(app, preview_id, {"push": False})
    typed = pushed(app, preview_id, {"typed_name": "local"})
    assert fake.writes == []
    written = pushed(app, preview_id, {"push": True})

    refused(missing, 400, "explicit push", "(--push)")
    refused(unticked, 400, "explicit push")
    refused(typed, 400, "not protected", "not a typed name")
    assert written.status == 200, written.body
    body = written.body
    assert body["sections"] == [RULES]
    assert body["confirmed_by"] == "--push"
    assert body["push_record"].startswith("pushes/") and body["push_record"].endswith("-local.json")
    assert (target_dir(root) / body["restore_point"]).is_file()
    assert body["fingerprint_after"] == fingerprint(root).id
    assert fingerprint(root).pushed_from == body["fingerprint_before"]
    assert fake.deployed["local"]["prompts"]["system"] == LOCAL_RULES
    (record,) = push_records(root)
    assert record.confirmed_by == "--push" and record.change_record is None
    refused(pushed(app, preview_id, {"push": True}), 404, "preview again")


def test_a_protected_push_needs_the_record_and_the_typed_name_and_records_ui_confirm(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path, "prod")
    record = proposed_record(root)
    app = App(Workspace.find(root))
    reply = previewed(app, "prod")
    preview_id = reply.body["preview_id"]
    assert reply.body["preview"]["protected"] is True
    assert reply.body["preview"]["confirmation"] == "typed_name"
    assert reply.body["preview"]["change_record_required"] is True

    box_alone = pushed(app, preview_id, {"push": True}, change=record)
    no_record = pushed(app, preview_id, {"typed_name": "prod"})
    wrong_name = pushed(app, preview_id, {"typed_name": "production"}, change=record)
    unknown_record = pushed(app, preview_id, {"typed_name": "prod"}, change="20990101-nothing")
    assert fake.writes == [] and not (target_dir(root) / "restore-points").exists()
    written = pushed(app, preview_id, {"typed_name": "prod"}, change=record)

    refused(box_alone, 400, "prod is protected", "does not stand in")
    refused(no_record, 400, "prod is protected", "Change record")
    refused(wrong_name, 400, "'production' is not 'prod'", "nothing was written")
    assert unknown_record.status == 400
    assert written.status == 200, written.body
    assert written.body["confirmed_by"] == "ui_confirm"
    (push_record,) = push_records(root)
    assert push_record.confirmed_by == "ui_confirm"
    assert push_record.environment == "prod" and push_record.change_record == record
    assert (target_dir(root) / push_record.restore_point).is_file()
    assert fake.deployed["prod"]["prompts"]["system"] == LOCAL_RULES
    assert fake.deployed["local"]["prompts"]["system"] == PROMPT
    shown = json.loads(invoke("change", "show", "--root", root, record, "--json").stdout)
    assert shown["status"] == "pushed"
    assert shown["pushes"][0]["push_record"] == written.body["push_record"]


def test_a_stale_preview_is_refused_and_forgotten(tmp_path: Path, fake: FakeConnector) -> None:
    root = ready(tmp_path, "local")
    clock = Clock()
    app = App(Workspace.find(root), clock=clock)
    preview_id = previewed(app, "local").body["preview_id"]

    clock.now += PREVIEW_MAX_AGE_S + 1
    stale = pushed(app, preview_id, {"push": True})

    refused(stale, 409, f"not within the last {PREVIEW_MAX_AGE_S} s", "preview again")
    refused(pushed(app, preview_id, {"push": True}), 404)
    assert fake.writes == [] and push_records_or_none(root) == []


def push_records_or_none(root: Path) -> list[PushRecord]:
    return push_records(root) if (target_dir(root) / "pushes").is_dir() else []


def test_a_deployed_set_that_moved_since_the_preview_is_a_409(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path, "local")
    app = App(Workspace.find(root))
    preview_id = previewed(app, "local").body["preview_id"]
    fake.set_prompt("local", "system", DEPLOYED_TONE)

    moved = pushed(app, preview_id, {"push": True})

    refused(moved, 409, "moved", "nothing was written", "Restore point")
    assert fake.writes == [] and push_records_or_none(root) == []
    assert fake.deployed["local"]["prompts"]["system"] == DEPLOYED_TONE
    refused(pushed(app, preview_id, {"push": True}), 404, "preview again")


def test_a_preview_with_refusals_keeps_nothing_and_a_pull_writes_through_its_route(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = workspace(tmp_path)
    assert invoke("sync", "--root", root, "--env", "local").exit_code == 0
    fake.set_prompt("local", "system", DEPLOYED_TONE)
    app = App(Workspace.find(root))

    shown = previewed(app, "local").body
    pulled = post(app, f"/api/targets/{SLUG}/pull", {"env": "local"})
    again = post(app, f"/api/targets/{SLUG}/pull", {"env": "local", "sections": ["nope"]})

    assert shown["preview_id"] is None and shown["preview"]["refusals"]
    assert pulled.status == 200, pulled.body
    assert [w["section"] for w in pulled.body["result"]["written"]] == ["prompt.system#tone"]
    assert "+Warm, and at most three sentences." in pulled.body["result"]["diff"]
    assert local_prompt(root).read_text(encoding="utf-8") == DEPLOYED_TONE
    refused(again, 400, "nope")


def test_one_preview_confirmed_twice_at_once_writes_exactly_once(
    tmp_path: Path, fake: FakeConnector
) -> None:
    """A kept preview is used once: taken under the lock before `push_target` runs, so two
    confirmations racing with one id make one write, and the other finds no preview."""
    root = ready(tmp_path, "local")
    app = App(Workspace.find(root))
    preview_id = previewed(app, "local").body["preview_id"]
    start = threading.Barrier(2)
    replies: list[Reply] = []

    def confirm() -> None:
        start.wait()
        replies.append(pushed(app, preview_id, {"push": True}))

    racers = [threading.Thread(target=confirm) for _ in range(2)]
    for racer in racers:
        racer.start()
    for racer in racers:
        racer.join(timeout=60)

    assert sorted(reply.status for reply in replies) == [200, 404]
    assert len(fake.writes) == 1 and len(push_records(root)) == 1


def test_a_change_record_outside_the_grammar_is_refused_before_the_preview_is_used(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = ready(tmp_path, "local")
    app = App(Workspace.find(root))
    preview_id = previewed(app, "local").body["preview_id"]

    refused(
        pushed(app, preview_id, {"push": True}, change="../../x"), 404, "not a Change record id"
    )
    assert pushed(app, preview_id, {"push": True}).status == 200, "the preview was kept"


# --- the exit-criterion walk: the help desk's protected staging, from the UI ---

WORKSPACE = REPO / "examples" / "workspace"
HELP = "help-desk"
RULE_5 = "5. Never ask for a password, a full card number or a security code"
FIXED = "5. Never ask for or repeat a password, a full card number, a security code or a PIN"


@pytest.fixture
def platform_restored() -> Iterator[None]:
    kept = {name: copy.deepcopy(getattr(platform, name)) for name in ("DEPLOYED", "STAGING")}
    yield
    for name, value in kept.items():
        held = getattr(platform, name)
        held.clear()
        held.update(value)


def trimmed(value: Any) -> Any:
    """A response as the walkthrough records it: long strings cut, diffs to their changed lines."""
    if isinstance(value, dict):
        return {key: trimmed(item) for key, item in value.items() if key not in {"payload"}}
    if isinstance(value, list):
        return [trimmed(item) for item in value[:6]]
    if isinstance(value, str) and "\n@@" in value:
        return [
            line for line in value.splitlines() if line[:1] in "+-" and line[:3] not in "+++---"
        ]
    if isinstance(value, str) and len(value) > 120:
        return value[:117] + "..."
    return value


def test_one_ui_push_to_the_help_desks_protected_staging_is_seen_by_the_next_process(
    tmp_path: Path, platform_restored: None, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "workspace"
    shutil.copytree(
        WORKSPACE,
        root,
        ignore=shutil.ignore_patterns("runs", "restore-points", "platform", "index.sqlite"),
    )
    git(root, "init", "-q")
    commit_all(root, "the example Workspace")
    target = root / ".agentdiag" / "targets" / HELP
    walk: list[dict[str, Any]] = []

    synced = invoke("sync", "--root", root, "--target", HELP, "--env", "staging")
    assert synced.exit_code == 0, synced.output
    before = json.loads((target / "fingerprint.json").read_text(encoding="utf-8"))
    local = target / "prompts" / "system.md"
    local.write_text(local.read_text(encoding="utf-8").replace(RULE_5, FIXED), encoding="utf-8")
    record = proposed_record(
        root,
        target=HELP,
        complaint_text="The help desk repeated my PIN back to me.",
        title="PIN repeated back",
    )

    with serving(Workspace.find(root)) as server:

        def step(method: str, path: str, body: Any = None) -> Any:
            answer = call(server, method, path, body)
            walk.append({"call": f"{method} {path}", "body": body, "status": answer.status,
                         "response": trimmed(answer.json())})  # fmt: skip
            return answer

        screen = step("GET", f"/api/targets/{HELP}/sync?env=staging")
        assert screen.status == 200
        assert screen.json()["protected"] is True
        assert [s["id"] for s in screen.json()["preview"]["sections"]] == [RULES]

        shown = step("POST", f"/api/targets/{HELP}/push/preview", {"env": "staging"})
        assert shown.status == 200, shown.body
        preview = shown.json()["preview"]
        assert f"+{FIXED}" in preview["sections"][0]["diff"]
        assert f"-{RULE_5}" in preview["sections"][0]["diff"]
        assert preview["restore_point"] == "restore-points/<time of the write>-staging.json"
        assert preview["confirmation"] == "typed_name" and preview["change_record_required"]
        preview_id = shown.json()["preview_id"]

        push = f"/api/targets/{HELP}/push"
        box = step("POST", push, {"preview_id": preview_id, "change": record,
                                  "confirm": {"push": True}})  # fmt: skip
        wrong = step("POST", push, {"preview_id": preview_id, "change": record,
                                    "confirm": {"typed_name": "Staging!"}})  # fmt: skip
        assert box.status == 400 and wrong.status == 400
        assert platform.STAGING["prompts"]["system"].count(FIXED) == 0
        written = step("POST", push, {"preview_id": preview_id, "change": record,
                                      "confirm": {"typed_name": "staging"}})  # fmt: skip

    assert written.status == 200, written.body
    body = written.json()
    assert body["confirmed_by"] == "ui_confirm" and body["sections"] == [RULES]
    push_record = PushRecord.model_validate_json(
        (target / body["push_record"]).read_text(encoding="utf-8")
    )
    assert push_record.confirmed_by == "ui_confirm"
    assert push_record.environment == "staging" and push_record.change_record == record
    assert re.fullmatch(r"restore-points/\d{8}T\d{6}Z-staging\.json", push_record.restore_point)
    assert (target / push_record.restore_point).is_file()
    after = json.loads((target / "fingerprint.json").read_text(encoding="utf-8"))
    assert after["pushed_from"] == before_id(before) == push_record.fingerprint_before
    changed = json.loads(
        invoke("change", "show", "--root", root, "--target", HELP, record, "--json").stdout
    )
    assert changed["status"] == "pushed"
    assert changed["pushes"][0]["push_record"] == body["push_record"]
    assert (
        FIXED
        in json.loads((target / "platform" / "staging.json").read_text("utf-8"))["prompts"][
            "system"
        ]
    )

    fresh = subprocess.run(
        [str(Path(sys.executable).parent / "agentdiag"), "sync", "--root", str(root),
         "--target", HELP, "--env", "staging", "--check"],
        capture_output=True, text=True, cwd=REPO, check=False,
    )  # fmt: skip
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    assert fresh.stdout.startswith("Sync held")
    walk.append({"call": "agentdiag sync --check --env staging (a new process)",
                 "status": fresh.returncode, "response": fresh.stdout.splitlines()[0]})  # fmt: skip
    if os.environ.get("AGENTDIAG_PRINT_WALK"):  # the walkthrough's record, when asked for
        with capsys.disabled():
            print("\nWALK " + json.dumps(walk, indent=1))


def before_id(fingerprint_file: dict[str, Any]) -> str:
    from agentdiag.sync.fingerprint import Fingerprint

    return Fingerprint.model_validate(fingerprint_file).id
