"""Seam 1: `agentdiag run --env` (ticket 38, phase-8 decision 12).

A copy of `examples/workspace/`, whose help desk declares two Adapter environments: `local`
(the default, `make_helpdesk` over `platform.DEPLOYED`) and the protected `staging`
(`make_staging_helpdesk` over `platform.STAGING`, a copy of the local record). `run --env
staging` replays the committed scoped recording `tests/fixtures/recordings/helpdesk.jsonl`
unchanged, because staging's prompt is the local one copied; `run.json.adapter` and
`run.json.sync` name `staging`, and the Sync is the staging Connector environment's. A
marker written into `platform.STAGING` alone reaches the Target only on `--env staging`,
which is what "talks to `platform.STAGING`" means. A name the Adapter block does not
declare, a Connector-only one included, is refused at preflight (exit 3) naming the ones it
does. The close gate's environment rule is exercised end to end here over two replayed
staging Runs, and over the committed toy Run fixtures in `tests/test_change_cli.py`.

No network, no model: every Run replays the recording.
"""

from __future__ import annotations

import copy
import json
import shutil
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.change.command import expect_change
from agentdiag.change.record import PushEvent, find_record, write_record
from agentdiag.cli import app
from agentdiag.examples.helpdesk import platform
from agentdiag.workspace import TargetPaths, Workspace

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = REPO / "examples" / "workspace"
RECORDING = REPO / "tests" / "fixtures" / "recordings" / "helpdesk.jsonl"
SLUG = "help-desk"
SCENARIO = "rules-rule-3-a-broken-app-is-reported-and-a-ticket-is-opened"
MARKER = "Staging marker: this line is only on the staging record."

runner = CliRunner()


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
    return copied


def target_of(root: Path) -> TargetPaths:
    return Workspace.find(root).resolve(SLUG)


def run(root: Path, *arguments: str) -> Any:
    return runner.invoke(
        app, ["run", "--root", str(root), "--target", SLUG, "--scenario", SCENARIO, *arguments]
    )


def replayed(root: Path, *arguments: str) -> Path:
    """One replayed Run of the rule-3 Scenario, and its directory."""
    runs = target_of(root).runs
    before = set(runs.iterdir()) if runs.is_dir() else set()
    result = run(root, "--replay", str(RECORDING), *arguments)
    assert "Traceback" not in result.output, result.output
    (made,) = set(runs.iterdir()) - before
    return made


def a_second_before(created_at: str) -> str:
    """One second before a Run's `created_at`: an expectation stated then is strictly before
    any Run made in the same second, which a fast disk makes common (the close gate orders
    the two at second granularity)."""
    moment = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ") - timedelta(seconds=1)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def run_json(run_dir: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    return loaded


def sent(run_dir: Path) -> str:
    return (run_dir / "trials" / SCENARIO / "1" / "trace.jsonl").read_text(encoding="utf-8")


# --- the environment a Run opens ---


def test_the_help_desk_on_staging_replays_the_scoped_recording_and_records_staging(
    root: Path,
) -> None:
    result = run(root, "--env", "staging", "--replay", str(RECORDING))

    assert result.exit_code == 0, result.output
    (run_dir,) = target_of(root).runs.iterdir()
    recorded = run_json(run_dir)
    assert recorded["adapter"]["environment"] == "staging"
    assert (
        recorded["adapter"]["config"]["factory"]
        == "agentdiag.examples.helpdesk:make_staging_helpdesk"
    )
    assert recorded["adapter"]["side_effects"] == "sandboxed"
    sync = recorded["sync"]
    assert (sync["environment"], sync["covered_by"], sync["status"]) == (
        "staging",
        "connector",
        "held",
    )
    scores = json.loads(
        (run_dir / "trials" / SCENARIO / "1" / "scores.json").read_text(encoding="utf-8")
    )
    assert {score["verdict"] for score in scores["scores"]} == {"pass"}


def test_a_run_with_no_env_opens_the_default_environment_as_before(root: Path) -> None:
    run_dir = replayed(root)

    recorded = run_json(run_dir)
    assert recorded["adapter"]["environment"] == "local"
    assert recorded["sync"]["environment"] == "local"


def test_staging_is_the_staging_record_and_its_sync_is_checked_against_staging(
    root: Path,
) -> None:
    """The marker is on `platform.STAGING` alone: `--env staging` sends it to the Target and
    finds the staging Connector environment ahead of the Fingerprint; the default Run sends
    the local record and holds. The recording no longer matches the staging request, which
    ends that Trial; what was sent is in the Trace all the same."""
    platform.STAGING["prompts"]["system"] = platform.STAGING["prompts"]["system"] + "\n" + MARKER
    local = replayed(root)
    assert MARKER not in sent(local)
    assert run_json(local)["sync"]["status"] == "held"

    staging = replayed(root, "--env", "staging")

    assert MARKER in sent(staging), "the staging Run's Target was sent the staging record"
    sync = run_json(staging)["sync"]
    assert sync["environment"] == "staging"
    assert sync["sections"]
    assert {section["direction"] for section in sync["sections"]} == {"deployed_ahead"}
    assert sync["resynced_from"] is not None
    assert MARKER not in platform.DEPLOYED["prompts"]["system"]


# --- refusals ---


def test_an_environment_the_manifest_does_not_name_exits_3_naming_the_ones_it_does(
    root: Path,
) -> None:
    result = run(root, "--env", "prod", "--replay", str(RECORDING))

    assert result.exit_code == 3
    assert "the Manifest names no Adapter environment 'prod'; it names local, staging" in (
        result.output
    )
    assert not target_of(root).runs.exists() or not any(target_of(root).runs.iterdir())
    empty = run(root, "--env", "", "--dry-run")
    assert empty.exit_code == 3, "an empty name is not the default"
    assert "the Manifest names no Adapter environment ''" in empty.output


def test_a_connector_only_environment_is_not_runnable(root: Path) -> None:
    """Nothing converses with an environment the Connector alone names: it is read and
    pushed to, never run (decision 12)."""
    manifest_path = target_of(root).directory / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["connector"]["environments"]["qa"] = {
        "deployed": "agentdiag.examples.helpdesk.platform:STAGING"
    }
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    synced = runner.invoke(
        app, ["sync", "--root", str(root), "--target", SLUG, "--env", "qa", "--check"]
    )
    assert synced.exit_code == 0, synced.output
    assert synced.stdout.startswith("Sync held"), "sync reads the Connector-only environment"

    result = run(root, "--env", "qa", "--dry-run")

    assert result.exit_code == 3
    assert "the Manifest names no Adapter environment 'qa'; it names local, staging" in (
        result.output
    )


# --- the dry run ---


def test_a_dry_run_with_env_prints_the_environment_and_without_it_prints_none(
    root: Path,
) -> None:
    chosen = run(root, "--env", "staging", "--dry-run")
    as_json = run(root, "--env", "staging", "--dry-run", "--json")
    plain = run(root, "--dry-run")

    assert chosen.exit_code == 0, chosen.output
    lines = chosen.stdout.splitlines()
    assert "environment staging" in lines
    assert lines.index("environment staging") == len(lines) - 2, "just before the Sync line"
    assert lines[-1].startswith("sync ")
    assert json.loads(as_json.stdout)["environment"] == "staging"
    assert plain.exit_code == 0, plain.output
    assert not any(line.startswith("environment ") for line in plain.stdout.splitlines())
    assert not target_of(root).runs.exists() or not any(target_of(root).runs.iterdir())


# --- the close gate on the pushed environment ---


def change(root: Path, *arguments: str) -> Any:
    return runner.invoke(app, ["change", *arguments, "--root", str(root), "--target", SLUG])


def test_a_record_pushed_to_staging_closes_on_a_staging_run_and_is_refused_on_a_local_one(
    root: Path,
) -> None:
    """Two identical staging Runs (the pre- and post-change) close a record pushed to staging
    `--refuted` (replay moves nothing), naming `staging`; a local verifying Run is refused
    by the environment rule before any comparison."""
    baseline = replayed(root, "--env", "staging").name
    complaint = root.parent / "complaint.md"
    complaint.write_text("# The app ticket was not opened\n", encoding="utf-8")
    opened = change(
        root, "open", "--complaint", str(complaint), "--layer", "rules", "--title", "App ticket"
    )
    assert opened.exit_code == 0, opened.output
    record_id = opened.stdout.split()[1]
    assert change(root, "propose", record_id, "--section", "prompt.system#rules").exit_code == 0
    target = target_of(root)
    stated = expect_change(
        target,
        record_id,
        should_move=[SCENARIO],
        must_not_move=[],
        now=lambda: a_second_before(run_json(target.runs / baseline)["created_at"]),
    )
    assert stated.code == 0, stated.message
    _, record, body = find_record(target, record_id)
    event = PushEvent(
        kind="connector",
        environment="staging",
        at=run_json(target.runs / baseline)["created_at"],
        push_record="pushes/staging.json",
    )
    write_record(target, record.model_copy(update={"status": "pushed", "pushes": [event]}), body)
    on_local = replayed(root).name
    on_staging = replayed(root, "--env", "staging").name

    refused = change(
        root, "close", record_id, "--refuted", "--run", on_local, "--baseline", baseline
    )
    closed = change(
        root, "close", record_id, "--refuted", "--run", on_staging, "--baseline", baseline
    )

    assert refused.exit_code == 3
    assert (
        f"the record was pushed to 'staging' and Run {on_local} ran on 'local'; verify on the "
        "environment the fix was pushed to, or pass --any-env"
    ) in refused.output
    assert closed.exit_code == 0, closed.output
    _, record, _ = find_record(target, record_id)
    assert record.status == "refuted"
    assert record.verification is not None
    assert (record.verification.run, record.verification.environment) == (on_staging, "staging")
    assert record.verification.environment_mismatch is False


def test_a_live_environment_chosen_with_env_is_refused_without_live(root: Path) -> None:
    """`--live` is orthogonal to `--env` (decision 12): a `live` environment named by
    `--env` is refused at preflight without it, and opens with it."""
    manifest_path = target_of(root).directory / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["adapter"]["environments"]["shop"] = {
        **manifest["adapter"]["environments"]["staging"],
        "side_effects": "live",
    }
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    refused = run(root, "--env", "shop", "--dry-run")
    acknowledged = run(root, "--env", "shop", "--dry-run", "--live")

    assert refused.exit_code == 3
    assert "The 'shop' environment causes live side effects" in refused.output
    assert "pass --live to acknowledge it" in refused.output
    assert acknowledged.exit_code == 0, acknowledged.output
    assert "environment shop" in acknowledged.stdout.splitlines()


# --- a rescore asks the source Run's environment ---


def test_a_rescore_of_a_staging_run_builds_the_staging_adapter(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rescore's Fixture checks ask the Adapter of the environment the source Run opened
    (`staging`, whose factory is `make_staging_helpdesk`); a source on an environment the
    Manifest no longer declares falls back to the default."""
    from agentdiag.run import rescore as rescore_module

    source = replayed(root, "--env", "staging").name
    built: list[tuple[str, str]] = []
    original = rescore_module.preflight

    def spied(*arguments: Any, **options: Any) -> Any:
        plan = original(*arguments, **options)
        built.append((plan.adapter.environment, plan.description.config["factory"]))
        return plan

    monkeypatch.setattr(rescore_module, "preflight", spied)

    rescored = runner.invoke(
        app,
        ["rescore", source, "--root", str(root), "--target", SLUG, "--replay", str(RECORDING)],
    )
    path = target_of(root).runs / source / "run.json"
    recorded = run_json(target_of(root).runs / source)
    recorded["adapter"]["environment"] = "retired"
    path.write_text(json.dumps(recorded), encoding="utf-8")
    fallback = runner.invoke(
        app,
        ["rescore", source, "--root", str(root), "--target", SLUG, "--replay", str(RECORDING)],
    )

    assert "Traceback" not in rescored.output + fallback.output
    assert built == [
        ("staging", "agentdiag.examples.helpdesk:make_staging_helpdesk"),
        ("local", "agentdiag.examples.helpdesk:make_helpdesk"),
    ]
