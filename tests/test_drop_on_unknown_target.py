"""The drop-on-an-unknown-Target check, end to end (ticket 13, phase-6 decision 39).

Every command runs in a temporary copy of `examples/workspace/`, the two-Target Workspace:
the order desk (the first toy, `init --target order-desk`) and the help desk (the second
toy, whose Manifest, drafts and Suite a fresh coding agent produced from the discovery and
generation skills alone). The mechanical parts of that
walkthrough are this module: `discover --from-connector` drafts the checked-in Manifest,
`generate` writes the checked-in Suite, `sync` covers every section and holds; a replayed
Run scores; a platform edit breaks Sync `deployed_ahead`; bringing the local copy in line
re-syncs the next Run, which `show` and `compare` say; the committed proxy rows import and
are judged.

The Runs replay `tests/fixtures/recordings/helpdesk.jsonl`, captured once through the login
(`uv run python scripts/record_fixtures.py --capture --helpdesk`) and committed: a missing
recording is a failure here, never a skip. The byte gates of the checked-in help desk (the
Manifest `discover` drafts over itself, the Suite `generate` writes, the Fingerprint `sync`
writes) are `tests/test_example_workspace.py`'s; this module holds the behaviour, and the
repeatable onboarding from nothing (`test_the_skills_code_steps_onboard_the_help_desk_from_init`).
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
from agentdiag.discover.draft import PROPOSED
from agentdiag.examples.helpdesk import platform
from agentdiag.run.manifest import Manifest

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "workspace"
PROXY_ROWS = REPO / "tests" / "fixtures" / "evidence" / "helpdesk-proxy-rows.json"
RECORDING = REPO / "tests" / "fixtures" / "recordings" / "helpdesk.jsonl"
REVIEW = "# REVIEW:"

HELPDESK_SOURCE = REPO / "src" / "agentdiag" / "examples" / "helpdesk"
CHECKED_IN = EXAMPLE / ".agentdiag" / "targets" / "help-desk"
COMPARED = ("target", "adapter", "connector", "prompts", "tools", "family", "channel")


@pytest.fixture
def recording() -> Path:
    """The committed help desk recording; its absence fails the test that needs it."""
    assert RECORDING.is_file(), (
        f"{RECORDING} is missing: `uv run python scripts/record_fixtures.py --capture "
        "--helpdesk` captures it (live, through the login)"
    )
    return RECORDING


EDIT = ("one-line subject", "one-line summary")
"""A change to rule 3, inside the `# Rules` section, on one line of the prompt: made on the
platform, then in the local copy; replayed by rewriting the recording the same way."""

MECHANICAL = "rules-rule-3-a-broken-app-is-reported-and-a-ticket-is-opened"
"""The Scenario the pre- and post-edit Runs replay: judged by mechanical Evals alone, so the
edit reaches only the Target's own requests."""

runner = CliRunner()


def invoke(*arguments: str | Path) -> Any:
    return runner.invoke(app, [str(argument) for argument in arguments])


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    return root


def help_desk(root: Path) -> Path:
    return root / ".agentdiag" / "targets" / "help-desk"


def sync(root: Path, *arguments: str) -> Any:
    return invoke("sync", "--root", root, "--target", "help-desk", *arguments)


def run_ids(root: Path) -> list[str]:
    runs = help_desk(root) / "runs"
    return sorted(path.name for path in runs.iterdir()) if runs.is_dir() else []


def replayed(root: Path, recording: Path, *arguments: str) -> tuple[Any, str]:
    before = set(run_ids(root))
    result = invoke(
        "run", "--root", root, "--target", "help-desk", "--replay", recording, *arguments
    )
    (new,) = set(run_ids(root)) - before
    return result, new


def edited_recording(tmp_path: Path) -> Path:
    """The recording as the edited Target would ask it: the phrase changed in every request."""
    old, new = (json.dumps(text)[1:-1] for text in EDIT)
    path = tmp_path / "helpdesk-edited.jsonl"
    path.write_text(RECORDING.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    return path


# --- the Registry ---


def test_the_registry_lists_both_targets_of_the_one_family(workspace: Path) -> None:
    result = invoke("registry", "--root", workspace, "--json")

    assert result.exit_code == 0, result.output
    entries = {entry["slug"]: entry for entry in json.loads(result.stdout)}
    assert sorted(entries) == ["help-desk", "order-desk"]
    for entry in entries.values():
        assert (entry["family"], entry["channel"]) == ("northwind", "chat")
        assert entry["problems"] == []
    assert entries["help-desk"]["connector"] == "inprocess"
    assert entries["help-desk"]["sync"]["status"] == "held"


def test_a_command_with_no_target_names_both_slugs(workspace: Path) -> None:
    result = invoke("sync", "--root", workspace, "--check")

    assert result.exit_code == 3
    assert "help-desk, order-desk" in result.output


def test_sync_fingerprints_the_order_desk_through_its_connector_and_then_holds(
    workspace: Path,
) -> None:
    written = invoke("sync", "--root", workspace, "--target", "order-desk")
    checked = invoke("sync", "--root", workspace, "--target", "order-desk", "--check")

    assert written.exit_code == 0, written.output
    assert (workspace / ".agentdiag" / "targets" / "order-desk" / "fingerprint.json").is_file()
    assert checked.exit_code == 0, checked.output
    assert "not_covered" not in checked.stdout


# --- the skills' code steps, from nothing ---


def accepted(draft: Path, manifest: Path) -> None:
    """Accept a draft mechanically, as a reviewer who agrees with every guess: each REVIEW
    comment that proposes a value is applied at its key path, every other REVIEW line is
    dropped (a commented-out placeholder under one stays a comment), and the draft becomes
    `manifest.yaml`."""
    text = draft.read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    for line in text.splitlines():
        if REVIEW in line and PROPOSED in line:
            path, _, value = line.split(PROPOSED, 1)[1].partition(": ")
            *parents, key = path.split(".")
            holder = document
            for parent in parents:
                holder = holder[parent]
            holder[key] = yaml.safe_load(value)
    manifest.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    draft.unlink()


WALKTHROUGH_JUDGEMENT: dict[str, Any] = {
    "target": {
        "name": "help-desk",
        "description": (
            "Northwind Bicycles' account help desk (sign-in, billing, invoices, addresses, the "
            "app), with one article search and two ticket actions, its prompt and tools held "
            "on a platform."
        ),
    },
    "family": "northwind",
    "channel": "chat",
}
"""What the walkthrough agent decided that no scan can know (discover step 2): the name is
the slug, the description says what the Target does, and it is the chat channel of the
Northwind persona."""


STAGING_ADAPTER: dict[str, Any] = {
    "factory": "agentdiag.examples.helpdesk:make_staging_helpdesk",
    "tools": "agentdiag.examples.helpdesk:make_tools",
    "model": "claude-sonnet-5",
    "side_effects": "sandboxed",
    "protected": True,
}
"""The protected `staging` environment ticket 27 added (phase-7 decision 16): a person's
decision about the platform's environments, which no scan and no read of `local` proposes."""


def judged(manifest: dict[str, Any]) -> dict[str, Any]:
    """The walkthrough's judgement applied to an accepted Manifest, as one documented patch:
    the identity and the Family above, `escalate` `sandboxed`, since it hands work to a
    person, the protected `staging` environment on both the Adapter and the Connector, and a
    `store` for each Connector environment (ticket 27's fix round: the platform outlives a
    process).
    Everything else the scan and the Connector's read proposed is accepted as is."""
    manifest = {**manifest, **WALKTHROUGH_JUDGEMENT}
    manifest["tools"]["escalate"]["side_effects"] = "sandboxed"
    manifest["adapter"]["environments"]["staging"] = dict(STAGING_ADAPTER)
    manifest["connector"]["environments"]["local"]["store"] = "platform/local.json"
    manifest["connector"]["environments"]["staging"] = {
        "deployed": "agentdiag.examples.helpdesk.platform:STAGING",
        "store": "platform/staging.json",
    }
    return manifest


def test_the_skills_code_steps_onboard_the_help_desk_from_init(tmp_path: Path) -> None:
    """The discovery skill's "both" loop, its code steps run and its review accepted
    mechanically: `init` → `discover --scan` → accept → `discover --from-connector` → accept
    → the walkthrough's judgement (`judged`) → the checked-in help desk's Manifest, and its
    saved prompt and tool schemas byte for byte. The scan's own guesses reach every other
    checked-in value (the tools and their kinds, the Connector's kind and Evidence store, the
    Adapter, the prompt pointer the Connector's read proposes)."""
    root = tmp_path / "shop"
    target = root / ".agentdiag" / "targets" / "help-desk"
    manifest = target / "manifest.yaml"
    draft = target / "manifest.draft.yaml"
    initialised = invoke(
        "init",
        "--root",
        root,
        "--target",
        "help-desk",
        "--adapter",
        "python:agentdiag.examples.helpdesk:make_helpdesk",
        "--tools",
        "agentdiag.examples.helpdesk:make_tools",
        "--model",
        "claude-sonnet-5",
    )
    assert initialised.exit_code == 0, initialised.output

    scanned = invoke("discover", "--root", root, "--target", "help-desk", "--scan", HELPDESK_SOURCE)
    assert scanned.exit_code == 0, scanned.output
    accepted(draft, manifest)
    read = invoke(
        "discover", "--root", root, "--target", "help-desk", "--from-connector", "--env", "local"
    )
    assert read.exit_code == 0, read.output
    accepted(draft, manifest)
    manifest.write_text(
        yaml.safe_dump(judged(yaml.safe_load(manifest.read_text(encoding="utf-8")))),
        encoding="utf-8",
    )

    ours = Manifest.model_validate(yaml.safe_load(manifest.read_text(encoding="utf-8")))
    shipped = Manifest.model_validate(
        yaml.safe_load((CHECKED_IN / "manifest.yaml").read_text(encoding="utf-8"))
    )
    assert {key: getattr(ours, key) for key in COMPARED} == {
        key: getattr(shipped, key) for key in COMPARED
    }
    for folder in ("prompts", "tools"):
        for path in (CHECKED_IN / folder).iterdir():
            assert (target / folder / path.name).read_bytes() == path.read_bytes(), path.name
    validated = invoke("validate", "--root", root, "--target", "help-desk")
    assert "0 errors" in validated.stdout, validated.output


def test_sync_covers_every_help_desk_section_and_check_holds(workspace: Path) -> None:
    written = sync(workspace, "--json")
    checked = sync(workspace, "--check")

    assert written.exit_code == 0, written.output
    sections = json.loads(written.stdout)["sections"]
    assert {section["id"] for section in sections} == {
        "flow.open_ticket_flow",
        "model",
        "provider",
        "prompt.system#persona",
        "prompt.system#rules",
        "prompt.system#tone",
        "tool.escalate",
        "tool.open_ticket",
        "tool.search_articles",
    }
    assert all(section["direction"] == "identical" for section in sections)
    assert checked.exit_code == 0, checked.output
    assert checked.stdout.startswith("Sync held")


def test_a_platform_edit_to_the_rules_breaks_sync_deployed_ahead(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        platform.DEPLOYED["prompts"], "system", platform.SYSTEM_PROMPT.replace(*EDIT)
    )

    checked = sync(workspace, "--check")

    assert checked.exit_code == 2, checked.output
    (row,) = [
        line for line in checked.stdout.splitlines() if line.startswith("prompt.system#rules")
    ]
    assert row.split()[1:3] == ["deployed_ahead", "changed"]
    unmoved = [line for line in checked.stdout.splitlines() if line.startswith("prompt.system#")]
    assert len(unmoved) == 3 and sum("identical" in line for line in unmoved) == 2


# --- Runs, replayed ---


def test_a_replayed_run_scores_with_no_invalid_score_for_scenario(
    workspace: Path, recording: Path
) -> None:
    result, run_id = replayed(workspace, recording)

    assert result.exit_code == 0, result.output
    scores = [
        score
        for path in (help_desk(workspace) / "runs" / run_id / "trials").rglob("scores.json")
        for score in json.loads(path.read_text(encoding="utf-8"))["scores"]
    ]
    assert scores
    assert not [
        score
        for score in scores
        if score["verdict"] == "invalid" and score.get("fault_source") == "scenario"
    ]


def test_a_platform_edit_brought_into_the_local_copy_re_syncs_the_next_run(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording: Path
) -> None:
    """The platform's rule 3 changes; the developer brings `prompts/system.md` in line; the
    next Run re-syncs (`resynced_from`), `show` names the section, and `compare` labels the
    Fingerprint difference undeclared and claims no regression."""
    before, pre = replayed(workspace, recording, "--scenario", MECHANICAL)
    assert before.exit_code in (0, 1), before.output

    edited = platform.SYSTEM_PROMPT.replace(*EDIT)
    monkeypatch.setitem(platform.DEPLOYED["prompts"], "system", edited)
    (help_desk(workspace) / "prompts" / "system.md").write_text(edited, encoding="utf-8")
    after, post = replayed(workspace, edited_recording(tmp_path), "--scenario", MECHANICAL)

    assert after.exit_code in (0, 1), after.output
    record = json.loads((help_desk(workspace) / "runs" / post / "run.json").read_text("utf-8"))
    previous = json.loads((help_desk(workspace) / "runs" / pre / "run.json").read_text("utf-8"))
    from agentdiag.sync.fingerprint import Fingerprint

    assert record["sync"]["resynced_from"] == Fingerprint.model_validate(previous["fingerprint"]).id
    assert [s["id"] for s in record["sync"]["sections"]] == ["prompt.system#rules"]
    shown = invoke("show", "--root", workspace, post, MECHANICAL)
    assert shown.exit_code == 0, shown.output
    assert "Re-synced from" in shown.stdout and "prompt.system#rules" in shown.stdout

    compared = invoke("compare", "--root", workspace, pre, post, "--json")
    assert compared.exit_code in (0, 1), compared.output
    comparison = json.loads(compared.stdout)
    moved = [d for d in comparison["differences"] if d["path"].startswith("fingerprint.")]
    assert moved and not any(d["declared"] for d in moved)
    assert not [delta for delta in comparison["deltas"] if delta["label"] == "regression"]


def test_the_proxy_rows_import_under_the_help_desk_as_a_run_list_shows(workspace: Path) -> None:
    imported = invoke(
        "import", "--root", workspace, "--target", "help-desk", "--rows", PROXY_ROWS, "--json"
    )
    listed = invoke("list", "--root", workspace, "--target", "help-desk", "--json")

    assert imported.exit_code == 0, imported.output
    (row,) = json.loads(listed.stdout)
    assert (row["target"], row["source"]) == ("help-desk", "imported")
    record = json.loads(
        (Path(json.loads(imported.stdout)["run_dir"]) / "run.json").read_text(encoding="utf-8")
    )
    assert record["source"] == "imported" and record["target"] == "help-desk"


def test_the_imported_run_is_judged_by_rescore_eval_prompt_adherence(
    workspace: Path, recording: Path
) -> None:
    imported = invoke(
        "import", "--root", workspace, "--target", "help-desk", "--rows", PROXY_ROWS, "--json"
    )
    run_id = json.loads(imported.stdout)["run_id"]

    judged = invoke(
        "rescore",
        run_id,
        "--root",
        workspace,
        "--eval",
        "prompt_adherence",
        "--replay",
        recording,
    )

    assert judged.exit_code in (0, 1, 2), judged.output
    (rescored,) = set(run_ids(workspace)) - {run_id}
    (scores,) = (help_desk(workspace) / "runs" / rescored / "trials").rglob("scores.json")
    (score,) = json.loads(scores.read_text(encoding="utf-8"))["scores"]
    assert score["eval"] == "prompt_adherence"
    assert score["verdict"] != "invalid"
