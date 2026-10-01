"""Seam 1: `agentdiag serve`, its routes and its page (ticket 16, phase-7 decisions 22-23).

The routes are asked directly (`App.handle`, no socket) over a Workspace of committed
fixtures: a copy of the toy holding every Run fixture and every Change record fixture, and
the help desk beside it for a Connector with a Flow and a protected environment. Each read
answers what the command function answers, dumped as JSON; each missing thing is a 404
with an `error` sentence. Then the server on a socket: the page at `/`, the JSON routes,
every refusal of a request that did not come from the page, the server-sent follow of a
replay Run, and a Run launched through `POST /api/runs` that is the CLI's Run file for file.
Last, the renderer under node over routes captured from these same functions: every screen
routes without a render error, and the Tests screen's expression is the CLI's.

No network, no model: every Run replays a recording.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentdiag.cli import app as cli
from agentdiag.examples.helpdesk import platform
from agentdiag.report.flow import flow_definition_view
from agentdiag.report.html import render_report
from agentdiag.report.story import change_record_story
from agentdiag.report.view import run_view
from agentdiag.run.compare import compare
from agentdiag.run.index import rebuild
from agentdiag.run.locate import REPORT_FILE
from agentdiag.scenario.select import Selection
from agentdiag.serve.app import App, Reply
from agentdiag.workspace import TargetPaths, Workspace
from tests.change_fixtures import BASE, CHANGES, PRMT, RUNS, SLUG, toy_with_records
from tests.serving import call, events, serving

REPO = Path(__file__).resolve().parents[1]
HELP_DESK = REPO / "examples" / "workspace" / ".agentdiag" / "targets" / "help-desk"
RECORDING = REPO / "tests" / "fixtures" / "recordings" / "toy-orders.jsonl"
RENDERER = REPO / "src" / "agentdiag" / "report" / "static" / "renderer.js"
PAGE = REPO / "tests" / "render_page.js"
TRI3 = "20260923T100600Z-tri3"
PAIR = ["cancel-processing-order", "where-is-shipped-order"]
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

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
def toy(tmp_path: Path, platform_restored: None) -> TargetPaths:
    """The toy with every Run fixture and Change record fixture, and the help desk beside it."""
    target = toy_with_records(tmp_path)
    for run in sorted(RUNS.iterdir()):
        if run.is_dir() and not (target.runs / run.name).exists():
            shutil.copytree(run, target.runs / run.name)
    shutil.copytree(
        HELP_DESK,
        target.directory.parent / "help-desk",
        ignore=shutil.ignore_patterns("runs", "platform", "restore-points"),
    )
    return target


def app_of(target: TargetPaths) -> App:
    return App(Workspace.find(target.root))


def get(app: App, path: str) -> Reply:
    return app.handle("GET", path)


def post(app: App, path: str, body: dict[str, Any]) -> Reply:
    return app.handle("POST", path, json.dumps(body).encode("utf-8"))


def assert_error(reply: Reply, status: int, *words: str) -> None:
    assert reply.status == status, reply.body
    assert set(reply.body) == {"error"}, reply.body
    assert "Traceback" not in reply.body["error"]
    for word in words:
        assert word in reply.body["error"], reply.body["error"]


# --- the reads, called directly ---


def test_the_registry_and_a_target_are_the_commands_json(toy: TargetPaths) -> None:
    app = app_of(toy)

    listed = get(app, "/api/registry")
    shown = get(app, f"/api/targets/{SLUG}")

    assert listed.status == 200
    assert [entry["slug"] for entry in listed.body] == ["help-desk", SLUG]
    assert shown.status == 200
    assert shown.body["entry"]["slug"] == SLUG
    assert {record["id"] for record in shown.body["change_records"]} == {
        path.stem for path in CHANGES.glob("*.md")
    }
    assert_error(get(app, "/api/targets/nope"), 404, "no Target 'nope'")
    assert_error(get(app, "/api/targets/..%2F..%2Fetc"), 404)


def test_the_suites_route_lists_every_scenario_by_the_name_suite_takes(toy: TargetPaths) -> None:
    reply = get(get_app := app_of(toy), f"/api/targets/{SLUG}/suites")

    assert reply.status == 200
    body = reply.body
    assert body["default_environment"] == "local"
    assert [suite["name"] for suite in body["suites"]] == ["orders", "guardrails"]
    orders = body["suites"][0]
    assert orders["reference"] == "suites/orders.yaml" and orders["status"] == "runnable"
    first = orders["scenarios"][0]
    assert first["id"] == "cancel-processing-order"
    assert first["kinds"] == sorted(first["kinds"]) and first["kinds"]
    assert first["evals"]
    assert_error(get(get_app, "/api/targets/nope/suites"), 404)


def test_the_runs_listing_is_the_index_and_a_run_is_its_run_view(toy: TargetPaths) -> None:
    app = app_of(toy)

    listed = get(app, f"/api/runs?target={SLUG}")
    shown = get(app, f"/api/runs/{BASE}")

    assert listed.status == 200
    assert {row["run_id"] for row in listed.body} == {
        run.name for run in RUNS.iterdir() if run.is_dir()
    }
    assert listed.body == get(app, "/api/runs").body
    assert shown.status == 200
    assert shown.body == json.loads(json.dumps(run_view(toy.runs / BASE).model_dump(mode="json")))
    assert_error(get(app, "/api/runs?target=nope"), 404)
    assert_error(get(app, "/api/runs/20990101T000000Z-none"), 404, "no Run")
    assert_error(get(app, "/api/runs/..%2Fetc"), 404, "is not a Run id")


def test_a_run_still_being_written_is_a_409_that_names_the_follow_route(toy: TargetPaths) -> None:
    (toy.runs / "20260929T000000Z-open").mkdir()

    reply = get(app_of(toy), "/api/runs/20260929T000000Z-open")

    assert_error(reply, 409, "still being written", "/api/runs/20260929T000000Z-open/follow")


def test_compare_is_the_comparison_with_the_declared_variations(toy: TargetPaths) -> None:
    app = app_of(toy)

    plain = get(app, f"/api/compare?baseline={BASE}&run={PRMT}")
    declared = get(app, f"/api/compare?baseline={BASE}&run={PRMT}&expect=judge&expect=manifest")

    assert plain.status == 200
    assert plain.body == compare(toy.runs / BASE, toy.runs / PRMT).model_dump(mode="json")
    assert declared.body == compare(
        toy.runs / BASE, toy.runs / PRMT, ["judge", "manifest"]
    ).model_dump(mode="json")
    assert_error(get(app, f"/api/compare?baseline={BASE}"), 400, "?baseline=")
    assert_error(get(app, f"/api/compare?baseline={BASE}&run=20990101T000000Z-none"), 404)
    assert_error(get(app, f"/api/compare?baseline={BASE}&run={PRMT}&expect=nothing.here"), 400)


def test_the_change_records_and_one_story(toy: TargetPaths) -> None:
    app = app_of(toy)
    record_id = "20260923-a-pushed-record"

    listed = get(app, f"/api/changes?target={SLUG}")
    story = get(app, f"/api/changes/{record_id}")

    assert listed.status == 200
    assert sorted(record["id"] for record in listed.body) == sorted(
        path.stem for path in CHANGES.glob("*.md")
    )
    from agentdiag.change.record import find_record

    _, record, _ = find_record(toy, record_id)
    assert story.status == 200
    assert story.body == change_record_story(record, toy).model_dump(mode="json")
    assert get(app, f"/api/changes/{record_id}?target={SLUG}").body == story.body
    assert_error(
        get(app, "/api/changes/20990101-nothing"), 404, "no Change record '20990101-nothing'"
    )


def test_a_flow_definition_comes_from_the_connectors_read(toy: TargetPaths) -> None:
    app = app_of(toy)

    reply = get(app, "/api/flows/help-desk/local/open_ticket_flow")

    assert reply.status == 200
    assert reply.body == flow_definition_view(
        "open_ticket_flow", platform.OPEN_TICKET_FLOW
    ).model_dump(mode="json")
    assert_error(get(app, "/api/flows/help-desk/local/nope"), 404, "holds no Flow 'nope'")
    assert_error(get(app, f"/api/flows/{SLUG}/local/nope"), 404)
    assert_error(get(app, "/api/flows/nope/local/x"), 404, "no Target 'nope'")


def test_the_help_desks_sync_screen_is_sync_check_beside_a_push_preview(
    toy: TargetPaths,
) -> None:
    app = app_of(toy)

    local = get(app, "/api/targets/help-desk/sync")
    staging = get(app, "/api/targets/help-desk/sync?env=staging")

    assert local.status == 200, local.body
    body = local.body
    assert body["environment"] == "local" and body["environments"] == ["local", "staging"]
    assert body["code"] == 0 and body["result"]["status"] == "held"
    assert body["protected"] is False and body["error"] is None
    assert body["preview"]["sections"] == []
    assert any("nothing to push" in reason for reason in body["preview"]["refusals"])
    assert "read" not in body["preview"] and "payload" not in body["preview"]
    assert staging.body["protected"] is True
    assert staging.body["preview"]["confirmation"] == "typed_name"
    assert_error(get(app, "/api/targets/help-desk/sync?env=prod"), 404, "no environment 'prod'")


def test_the_dashboard_is_the_commands_json_and_unknown_routes_say_so(toy: TargetPaths) -> None:
    from agentdiag.dashboard import dashboard

    app = app_of(toy)
    workspace = Workspace.find(toy.root)
    unindexed = get(app, "/api/dashboard")
    rebuild(toy.root)

    shown = get(app, "/api/dashboard")
    narrowed = get(app, "/api/dashboard?trend=2")

    assert unindexed.status == 200 and unindexed.body["index_read"] is False
    assert "agentdiag index rebuild" in unindexed.body["problems"][0]
    assert shown.status == 200 and shown.body["problems"] == []
    assert shown.body == dashboard(workspace).model_dump(mode="json", by_alias=True)
    assert [row["entry"]["slug"] for row in shown.body["rows"]] == ["help-desk", SLUG]
    assert narrowed.body == dashboard(workspace, trend=2).model_dump(mode="json", by_alias=True)
    assert len(narrowed.body["rows"][1]["trend"]) == 2
    for given in ("0", "x", "-1"):
        assert_error(get(app, f"/api/dashboard?trend={given}"), 400, "at least 1")
    assert_error(get(app, "/api/nothing"), 404, "no route GET /api/nothing")
    assert_error(post(app, "/api/registry", {}), 405, "answers GET only")
    assert_error(app.handle("GET", "/api/runs/x/cancel"), 405)


def test_a_route_that_fails_answers_a_sentence_never_a_traceback(
    toy: TargetPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*arguments: Any, **options: Any) -> Any:
        raise RuntimeError("the Registry could not be read")

    monkeypatch.setattr("agentdiag.registry.registry", broken)

    reply = get(app_of(toy), "/api/registry")

    assert_error(reply, 500, "RuntimeError: the Registry could not be read")


def test_a_write_body_that_is_not_the_request_is_a_400(toy: TargetPaths) -> None:
    app = app_of(toy)

    assert_error(
        app.handle("POST", "/api/runs", b"not json"), 400, "the request body is not a RunRequest"
    )
    assert_error(post(app, "/api/runs", {"scenarios": ["x"]}), 400, "scenarios")
    assert_error(post(app, "/api/runs", {"target": SLUG, "trials": 0}), 400, "trials")
    assert_error(post(app, "/api/runs", {"target": SLUG, "environment": "local"}), 400, "env")


def test_a_launch_names_its_environment_as_env_and_an_unknown_one_is_a_400(
    toy: TargetPaths,
) -> None:
    """Ticket 38: `env` is `run --env`. The help desk's `staging` dry-runs through the route
    and says so; a name the Adapter block does not declare is refused before any Run."""
    app = app_of(toy)
    suites = get(app, "/api/targets/help-desk/suites").body
    assert suites["adapter_environments"] == ["local", "staging"]
    assert get(app, f"/api/targets/{SLUG}/suites").body["adapter_environments"] == ["local"]

    dry = post(app, "/api/runs", {"target": "help-desk", "env": "staging", "dry_run": True})
    plain = post(app, "/api/runs", {"target": "help-desk", "dry_run": True})

    assert dry.status == 200, dry.body
    assert dry.body["dry_run"]["environment"] == "staging"
    assert plain.status == 200, plain.body
    assert "environment" not in plain.body["dry_run"]
    assert_error(
        post(app, "/api/runs", {"target": "help-desk", "env": "prod"}),
        400,
        "the Manifest names no Adapter environment 'prod'; it names local, staging",
    )
    assert_error(
        post(app, "/api/runs", {"target": SLUG, "env": "staging", "dry_run": True}),
        400,
        "the Manifest names no Adapter environment 'staging'; it names local",
    )
    assert not (toy.directory.parent / "help-desk" / "runs").exists()


# --- the server on a socket ---


def test_the_page_and_the_json_routes_over_http(toy: TargetPaths) -> None:
    with serving(Workspace.find(toy.root)) as server:
        page = call(server, "GET", "/")
        registry = call(server, "GET", "/api/registry")
        shown = call(server, "GET", "/api/dashboard?trend=3")
        missing = call(server, "GET", "/api/targets/nope")

    assert page.status == 200
    assert page.headers["content-type"] == "text/html; charset=utf-8"
    html = page.body.decode("utf-8")
    assert RENDERER.read_text(encoding="utf-8") in html
    assert "<script>window.AGENTDIAG_VIEW=" not in html
    assert registry.status == 200 and registry.headers["content-type"] == "application/json"
    assert [entry["slug"] for entry in registry.json()] == ["help-desk", SLUG]
    assert shown.status == 200 and shown.headers["content-type"] == "application/json"
    assert shown.json()["trend"] == 3
    assert [row["entry"]["slug"] for row in shown.json()["rows"]] == ["help-desk", SLUG]
    assert missing.status == 404 and "error" in missing.json()
    for answer in (page, registry, shown, missing):
        assert not any(name.startswith("access-control-") for name in answer.headers)
        assert answer.headers["x-frame-options"] == "DENY"


def test_a_request_that_did_not_come_from_the_page_is_refused(toy: TargetPaths) -> None:
    dry = {"target": SLUG, "scenario": ["cancel-processing-order"], "dry_run": True}
    with serving(Workspace.find(toy.root)) as server:
        own = f"http://127.0.0.1:{server.port}"
        answers = {
            "no content type": call(
                server, "POST", "/api/runs", dry, page=False, headers={"X-Agentdiag-Request": "1"}
            ),
            "a form": call(
                server,
                "POST",
                "/api/runs",
                dry,
                page=False,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Agentdiag-Request": "1",
                },
            ),
            "no request header": call(
                server,
                "POST",
                "/api/runs",
                dry,
                page=False,
                headers={"Content-Type": "application/json"},
            ),
            "another origin": call(
                server, "POST", "/api/runs", dry, headers={"Origin": "http://evil.example"}
            ),
            "another origin reading": call(
                server, "GET", "/api/registry", headers={"Origin": "http://evil.example"}
            ),
            "another host": call(server, "GET", "/api/registry", headers={"Host": "evil.example"}),
            "a rebound name": call(
                server, "POST", "/api/runs", dry, headers={"Host": f"evil.example:{server.port}"}
            ),
            "cross-site writing": call(
                server, "POST", "/api/runs", dry, headers={"Sec-Fetch-Site": "cross-site"}
            ),
            "cross-site reading": call(
                server, "GET", "/api/registry", headers={"Sec-Fetch-Site": "cross-site"}
            ),
            "no token": call(
                server,
                "POST",
                "/api/runs",
                dry,
                page=False,
                headers={"Content-Type": "application/json", "X-Agentdiag-Request": "1"},
            ),
            "a wrong token": call(
                server, "POST", "/api/runs", dry, headers={"X-Agentdiag-Token": "x" * 32}
            ),
            "a follow with no token": call(server, "GET", f"/api/runs/{BASE}/follow"),
            "a follow with a wrong token": call(
                server, "GET", f"/api/runs/{BASE}/follow?token=nope"
            ),
        }
        same_site = call(server, "GET", "/api/registry", headers={"Sec-Fetch-Site": "same-origin"})
        page = call(server, "GET", "/").body.decode("utf-8")
        token = server.app.token
        allowed = call(server, "POST", "/api/runs", dry, headers={"Origin": own})
        localhost = call(
            server, "GET", "/api/registry", headers={"Host": f"localhost:{server.port}"}
        )

    expected = {
        "no content type": (415, "application/json"),
        "a form": (415, "application/x-www-form-urlencoded"),
        "no request header": (403, "X-Agentdiag-Request"),
        "another origin": (403, "Origin"),
        "another origin reading": (403, "Origin"),
        "another host": (403, "Host"),
        "a rebound name": (403, "Host"),
        "cross-site writing": (403, "cross-site"),
        "cross-site reading": (403, "cross-site"),
        "no token": (403, "X-Agentdiag-Token"),
        "a wrong token": (403, "X-Agentdiag-Token"),
        "a follow with no token": (403, "?token="),
        "a follow with a wrong token": (403, "?token="),
    }
    for case, (status, word) in expected.items():
        answer = answers[case]
        assert answer.status == status, (case, answer.body)
        assert word in answer.json()["error"], (case, answer.json())
        assert not any(name.startswith("access-control-") for name in answer.headers), case
    assert allowed.status == 200, allowed.body
    assert allowed.json()["dry_run"]["expression"] == "scenario=cancel-processing-order"
    assert localhost.status == 200 and same_site.status == 200
    assert f"<script>window.AGENTDIAG_TOKEN={json.dumps(token)};</script>" in page
    assert list((toy.runs).glob("2026092[4-9]*")) == [], "a refused request wrote nothing"


def test_following_a_finished_run_streams_every_event_show_follow_reads(toy: TargetPaths) -> None:
    """The follow of a Run already on disk: its Trials in the Run's order, each with the
    lines `show --follow --json` prints for it, then its Scores, then the end. The fixture
    stopped before its last sweep finished, so the Trials it never started are announced
    and passed with no Events and no Scores."""
    from agentdiag.run.locate import trace_path
    from agentdiag.trace.show import raw_events

    record = json.loads((toy.runs / TRI3 / "run.json").read_text(encoding="utf-8"))
    with serving(Workspace.find(toy.root)) as server:
        stream = events(server, f"/api/runs/{TRI3}/follow")

    names = [name for name, _ in stream]
    assert names[0] == "run" and names[-1] == "finished"
    order = [(n, s["id"]) for n in range(1, record["trials"] + 1) for s in record["scenarios"]]
    assert [(d["trial"], d["scenario"]) for name, d in stream if name == "trial"] == order
    for number, scenario in order:
        streamed = [
            d["event"]
            for name, d in stream
            if name == "event" and (d["trial"], d["scenario"]) == (number, scenario)
        ]
        ran = trace_path(toy.runs / TRI3, scenario, number).exists()
        assert streamed == (
            [json.loads(line) for line in raw_events(toy.runs / TRI3, scenario, number)]
            if ran
            else []
        )
    scores = {(d["trial"], d["scenario"]): d["scores"] for name, d in stream if name == "scores"}
    assert list(scores) == order
    scored = {
        (line["trial"], line["id"])
        for line in json.loads((toy.runs / TRI3 / "scorecard.json").read_text(encoding="utf-8"))[
            "scenarios"
        ]
    }
    assert {key for key, value in scores.items() if value} == scored
    finished = stream[-1][1]
    assert finished == {
        "run_id": TRI3,
        "scorecard": True,
        "code": None,
        "message": None,
        "reason": None,
        "report": f"#run/{TRI3}",
    }


def test_a_run_launched_over_http_is_followed_to_its_end(toy: TargetPaths) -> None:
    with serving(Workspace.find(toy.root)) as server:
        launched = call(
            server,
            "POST",
            "/api/runs",
            {"target": SLUG, "scenario": PAIR, "replay": str(RECORDING)},
        )
        assert launched.status == 202, launched.body
        run_id = launched.json()["run_id"]
        stream = events(server, launched.json()["follow"])
        again = call(server, "POST", f"/api/runs/{run_id}/cancel", {})

    assert launched.json()["selection"] == f"scenario={','.join(sorted(PAIR))}"
    names = [name for name, _ in stream]
    assert names[0] == "run" and names[-1] == "finished"
    assert [d["scenario"] for name, d in stream if name == "trial"] == PAIR
    types = [d["event"]["type"] for name, d in stream if name == "event"]
    assert types.count("trace/start") >= 2 and types.count("trace/end") >= 2
    finished = stream[-1][1]
    assert finished["scorecard"] is True and finished["code"] == 1  # the cancel Trial fails
    assert "pass rate" in finished["message"]
    assert (toy.runs / run_id / REPORT_FILE).exists()
    assert again.status == 409 and "already finished" in again.json()["error"]


# --- the Run the page launches is the Run the CLI writes ---

TIMESTAMP = re.compile(r"\d{18}")
MILLISECONDS = re.compile(r"\b\d+ ms\b")


def _latency_evals() -> set[str]:
    from agentdiag.eval.latency import SPECS

    return {spec.name for spec in SPECS}


def _normalised(value: Any, run_id: str) -> Any:
    """A Run file's content with what two Runs of one selection cannot share taken out: the
    Run id, `created_at`, each Event's `ts` and the `dotted_order` built from it, and what a
    latency Eval measures from those times (its `value`, its aggregate's `value_mean`, the
    milliseconds its rationale quotes). Every Verdict stays."""
    if isinstance(value, dict):
        timed = value.get("eval") in _latency_evals()
        return {
            key: (
                TIMESTAMP.sub("<ts>", item)
                if key == "dotted_order"
                else MILLISECONDS.sub("<n> ms", item)
                if timed and key == "rationale"
                else _normalised(item, run_id)
            )
            for key, item in value.items()
            if key not in {"ts", "created_at"} and not (timed and key in {"value", "value_mean"})
        }
    if isinstance(value, list):
        return [_normalised(item, run_id) for item in value]
    if isinstance(value, str):
        return value.replace(run_id, "<run>")
    return value


def _content(run_dir: Path) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.name == REPORT_FILE:
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            parsed: Any = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            parsed = json.loads(text)
        files[path.relative_to(run_dir).as_posix()] = _normalised(parsed, run_dir.name)
    return files


def test_a_run_launched_through_the_route_is_the_clis_run_file_for_file(
    tmp_path: Path,
) -> None:
    """Launching through `POST /api/runs` calls the `run` function the CLI calls: over the
    same selection and recording the two Run directories hold the same files with the same
    content, but for the Run id, `created_at`, and the Events' `ts` (with `dotted_order`,
    which is built from it, and the milliseconds the latency Evals measure from it). The
    Report differs only as a rendering of those files does, so each is checked to be
    exactly the rendering of its own Run."""
    root = tmp_path / "toy"
    shutil.copytree(
        REPO / "examples" / "toy", root, ignore=shutil.ignore_patterns("runs", "index.sqlite")
    )
    arguments = ["run", "--root", str(root), "--replay", str(RECORDING)]
    for scenario in PAIR:
        arguments += ["--scenario", scenario]
    by_cli = runner.invoke(cli, arguments)
    assert by_cli.exit_code == 1, by_cli.output
    (cli_run,) = sorted((root / ".agentdiag" / "targets" / SLUG / "runs").iterdir())
    app = App(Workspace.find(root))

    launched = post(app, "/api/runs", {"scenario": PAIR, "replay": str(RECORDING)})
    assert launched.status == 202, launched.body
    launch = app._launches[launched.body["run_id"]]
    assert launch.finished.wait(timeout=120)
    ui_run = cli_run.parent / launched.body["run_id"]

    assert launch.exit is not None and launch.exit.code == by_cli.exit_code
    assert sorted(p.relative_to(ui_run).as_posix() for p in ui_run.rglob("*") if p.is_file()) == (
        sorted(p.relative_to(cli_run).as_posix() for p in cli_run.rglob("*") if p.is_file())
    )
    assert _content(ui_run) == _content(cli_run)
    for run in (cli_run, ui_run):
        assert (run / REPORT_FILE).read_text(encoding="utf-8") == render_report(run_view(run))
    recorded = json.loads((ui_run / "run.json").read_text(encoding="utf-8"))
    assert recorded["selection"]["expression"] == launched.body["selection"]


# --- cancel ---


def test_a_cancel_asked_mid_run_lets_the_trial_finish_and_names_the_rest_cancelled(
    toy: TargetPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first Trial is held until the cancel is asked, so the order is fixed: the Trial
    in progress finishes and scores, and the Trial not started is `not_run: cancelled`."""
    from agentdiag.run import execute

    entered, release = threading.Event(), threading.Event()
    original = execute._trial

    def held(*arguments: Any, **options: Any) -> Any:
        entered.set()
        release.wait(timeout=60)
        return original(*arguments, **options)

    monkeypatch.setattr(execute, "_trial", held)
    app = app_of(toy)

    launched = post(app, "/api/runs", {"target": SLUG, "scenario": PAIR, "replay": str(RECORDING)})
    assert launched.status == 202, launched.body
    run_id = launched.body["run_id"]
    assert entered.wait(timeout=60)
    second = post(app, "/api/runs", {"target": SLUG, "scenario": PAIR, "replay": str(RECORDING)})
    cancelled = post(app, f"/api/runs/{run_id}/cancel", {})
    release.set()
    launch = app._launches[run_id]
    assert launch.finished.wait(timeout=120)

    assert_error(second, 409, "still running")
    assert cancelled.status == 202 and cancelled.body["cancelling"] is True
    scorecard = json.loads((toy.runs / run_id / "scorecard.json").read_text(encoding="utf-8"))
    assert [line["id"] for line in scorecard["scenarios"]] == [PAIR[0]]
    record = json.loads((toy.runs / run_id / "run.json").read_text(encoding="utf-8"))
    assert [
        entry["scenario"] for entry in record["not_run"] if entry["reason"] == "cancelled"
    ] == []
    cancelled_trials = [entry for entry in scorecard["not_run"] if entry["reason"] == "cancelled"]
    assert [entry["scenario"] for entry in cancelled_trials] == [PAIR[1]]
    assert_error(post(app, "/api/runs/20990101T000000Z-none/cancel", {}), 404)


def test_the_run_function_asks_cancelled_before_every_trial(tmp_path: Path) -> None:
    """With `cancelled` true from the start no Trial runs; the Run is still whole: its
    Scorecard names every selected Trial `cancelled`."""
    from agentdiag.run.execute import RunOptions, run

    root = tmp_path / "toy"
    shutil.copytree(
        REPO / "examples" / "toy", root, ignore=shutil.ignore_patterns("runs", "index.sqlite")
    )
    target = Workspace.find(root).resolve(None)

    exit_state = run(
        RunOptions(target=target, scenario=PAIR, replay=RECORDING, trials=2),
        cancelled=lambda: True,
    )

    assert exit_state.run_dir is not None and exit_state.scorecard is not None
    assert exit_state.scorecard.scenarios == []
    assert sorted(
        (entry.scenario, entry.trial)
        for entry in exit_state.scorecard.not_run
        if entry.reason == "cancelled"
    ) == sorted((scenario, n) for scenario in PAIR for n in (1, 2))


# --- the renderer, served, under node ---


def served_routes(app: App, target: TargetPaths) -> dict[str, Any]:
    """Every GET the page makes for the hashes below, answered by the real routes, over an
    Index rebuilt from the files (the Dashboard never builds one)."""
    rebuild(target.root)
    slugs = ["help-desk", SLUG]
    paths = ["/api/dashboard", "/api/registry", "/api/runs", "/api/changes"]
    for slug in slugs:
        paths += [
            f"/api/targets/{slug}",
            f"/api/targets/{slug}/suites",
            f"/api/targets/{slug}/sync",
            f"/api/runs?target={slug}",
            f"/api/changes?target={slug}",
        ]
    paths += ["/api/targets/help-desk/sync?env=staging", f"/api/targets/{SLUG}/sync?env=local"]
    paths += [f"/api/runs/{run.name}" for run in sorted(target.runs.iterdir())]
    paths += [f"/api/changes/{path.stem}" for path in sorted(CHANGES.glob("*.md"))]
    paths += [
        f"/api/compare?baseline={BASE}&run={PRMT}",
        f"/api/compare?baseline={BASE}&run={PRMT}&expect=judge",
        "/api/flows/help-desk/local/open_ticket_flow",
    ]
    answered = {path: get(app, path) for path in paths}
    for path, reply in answered.items():
        assert reply.status == 200, (path, reply.body)
    return {path: reply.body for path, reply in answered.items()}


def node_page(tmp_path: Path, entries: list[Any], routes: dict[str, Any]) -> list[dict[str, Any]]:
    assert NODE is not None
    page = tmp_path / "page.html"
    from agentdiag.report.html import render_page

    page.write_text(render_page(), encoding="utf-8")
    answers = tmp_path / "routes.json"
    answers.write_text(json.dumps(routes), encoding="utf-8")
    completed = subprocess.run(
        [NODE, str(PAGE), str(page), json.dumps(entries), str(answers)],
        capture_output=True,
        text=True,
        check=True,
    )
    return [json.loads(line) for line in completed.stdout.splitlines()]


@needs_node
def test_every_screen_routes_without_a_render_error_over_the_real_routes(
    toy: TargetPaths, tmp_path: Path
) -> None:
    routes = served_routes(app_of(toy), toy)
    hashes = [
        "#dashboard",
        "#target/help-desk",
        f"#target/{SLUG}",
        "#sync/help-desk/staging",
        f"#sync/{SLUG}/local",
        f"#tests/{SLUG}",
        "#tests/help-desk",
        f"#run/{BASE}",
        f"#run/{TRI3}/where-is-shipped-order/2",
        f"#run/{BASE}/status-question-is-not-a-cancel/1",
        f"#report/{BASE}",
        f"#compare/{BASE}/{PRMT}",
        f"#compare/{BASE}/{PRMT}?expect=judge",
        "#flow",
        "#flow/20260923-a-pushed-record",
        "#flow/def/help-desk/local/open_ticket_flow",
    ]

    results = node_page(tmp_path, hashes, routes)

    for hash, result in zip(hashes, results, strict=True):
        assert result["error"] is None, (hash, result["error"])
    main = {hash: result["main"] for hash, result in zip(hashes, results, strict=True)}
    board = main["#dashboard"]
    assert "not served yet" not in board
    rows = re.findall(r'<tr data-slug="([^"]+)">(.*?)</tr>', board, flags=re.DOTALL)
    assert [slug for slug, _ in rows] == ["help-desk", SLUG]
    help_desk, toy_row = (row for _, row in rows)
    assert 'href="#target/help-desk"' in help_desk and 'href="#tests/help-desk"' in help_desk
    for environment in ("local", "staging"):
        assert f'href="#sync/help-desk/{environment}"' in help_desk
    assert "no Run yet" in help_desk and "held · local" in help_desk
    assert "not synced yet" in toy_row
    assert 'href="#run/20260923T100700Z-thrs"' in toy_row and f'href="#tests/{SLUG}"' in toy_row
    assert 'href="#flow/20260923-an-open-record"' in toy_row
    assert '<svg class="spark"' in toy_row
    (beside,) = re.findall(r'data-section="trend-counts">([^<]*)<', toy_row)
    assert beside == "98% · incomplete 6 · unverifiable 0 · invalid 0"
    titles = re.findall(r"<title>([^<]*)</title>", toy_row)
    assert len(titles) == toy_row.count("<circle") > 1
    assert all("incomplete" in title and "invalid" in title for title in titles)
    assert "Prompt sections" in main[f"#target/{SLUG}"]
    staging = main["#sync/help-desk/staging"]
    assert "Sync · help-desk · staging" in staging and "protected" in staging
    assert 'data-act="push-ticked"' in staging and "Push records" in staging
    tests = main[f"#tests/{SLUG}"]
    assert 'data-act="scenario" data-id="cancel-processing-order"' in tests
    assert 'id="sel-expr">all<' in tests and 'data-act="launch"' in tests
    compared = main[f"#compare/{BASE}/{PRMT}"]
    assert "Configuration diff" in compared and "Scores per Scenario" in compared
    opened = main[f"#run/{BASE}/status-question-is-not-a-cancel/1"]
    assert 'data-section="opened-record"' in opened
    assert 'href="#flow/20260923-a-pushed-record"' in opened
    assert 'data-section="change-records"' in main["#flow"]
    assert "20260923-a-pushed-record" in main["#flow"]
    assert (
        'data-section="open-record"' in main[f"#run/{BASE}"]
        or "no Diagnosis" in main[f"#run/{BASE}"]
    )


@needs_node
def test_the_compare_pickers_hold_only_the_shown_targets_runs(
    toy: TargetPaths, tmp_path: Path
) -> None:
    """The help desk gets a Run of its own; comparing two toy Runs, and then picking the
    help desk, lists one Target's Runs in both pickers, never the other's."""
    theirs = "20260923T100900Z-hdsk"
    made = toy.directory.parent / "help-desk" / "runs" / theirs
    shutil.copytree(toy.runs / BASE, made)
    record = json.loads((made / "run.json").read_text(encoding="utf-8"))
    record.update(run_id=theirs, target="help-desk")
    (made / "run.json").write_text(json.dumps(record), encoding="utf-8")
    routes = served_routes(app_of(toy), toy)
    toy_ids = {row["run_id"] for row in routes[f"/api/runs?target={SLUG}"]}

    results = node_page(
        tmp_path, [f"#compare/{BASE}/{PRMT}", "#tests/help-desk", "#compare"], routes
    )

    for result in results:
        assert result["error"] is None, result["error"]
    ours, _, help_desk = (result["main"] for result in results)
    for main, expected in ((ours, toy_ids), (help_desk, {theirs})):
        for picker in ("c-base", "c-run"):
            (options,) = re.findall(rf'<select id="{picker}"[^>]*>(.*?)</select>', main, re.S)
            assert set(re.findall(r'<option value="([^"]+)"', options)) == expected


@needs_node
def test_the_tests_screens_environment_picker_lists_the_adapters_and_sends_env(
    toy: TargetPaths, tmp_path: Path
) -> None:
    """Ticket 38: the picker lists every Adapter environment, the default chosen and none
    disabled; a launch on the default sends no `env` and prints the command it always did,
    and another one chosen goes out as `env` and the printed command says `--env`."""
    routes = served_routes(app_of(toy), toy)
    entries: list[Any] = [
        "#tests/help-desk",
        {"eval": "JSON.stringify(launchBody(true))"},
        {"eval": "commandLine(launchBody(true))"},
        {"eval": "pickEnv('staging'),JSON.stringify(launchBody(false))"},
        {"eval": "commandLine(launchBody(true))"},
    ]

    screen, default, default_command, chosen, command = node_page(tmp_path, entries, routes)

    assert screen["error"] is None, screen["error"]
    (options,) = re.findall(r'<select id="t-env"[^>]*>(.*?)</select>', screen["main"], re.S)
    assert re.findall(r'<option value="([^"]+)"', options) == ["local", "staging"]
    assert "disabled" not in options and "ticket 38" not in screen["main"]
    assert re.search(r'<option value="local" selected>', options)
    assert "env" not in json.loads(default["value"]), "the default is sent as before"
    assert "--env" not in default_command["value"]
    assert json.loads(chosen["value"])["env"] == "staging"
    assert " --env staging" in command["value"] and command["value"].endswith("--dry-run")
    sent = json.loads(chosen["value"])
    reply = post(app_of(toy), "/api/runs", {**sent, "dry_run": True})
    assert reply.status == 200, reply.body
    assert reply.body["dry_run"]["environment"] == "staging"


@needs_node
def test_the_tests_screens_expression_is_the_one_the_cli_records(
    toy: TargetPaths, tmp_path: Path
) -> None:
    """The picker's expression for a chosen selection equals `Selection.expression()`, and
    equals what a dry run through the route (the run function's own normalisation) says."""
    chosen = {
        "scenario": ["where-is-shipped-order", "cancel-processing-order", "where-is-shipped-order"],
        "tag": ["tool", "one_shot"],
        "suite": ["orders"],
    }
    cases = [chosen, {"scenario": [], "tag": [], "suite": []}, {"tag": ["tool"], "suite": []}]
    entries = [{"eval": f"selectionExpression({json.dumps(case)})"} for case in cases]

    results = node_page(tmp_path, entries, {})

    app = app_of(toy)
    for case, result in zip(cases, results, strict=True):
        expected = Selection(**case).expression()
        assert result["value"] == expected, (case, result)
        dry = post(app, "/api/runs", {"target": SLUG, "dry_run": True, **case})
        if dry.status == 200:
            assert dry.body["dry_run"]["expression"] == expected
    assert results[0]["value"] == (
        "scenario=cancel-processing-order,where-is-shipped-order tag=one_shot,tool suite=orders"
    )


def test_serve_imports_no_sdk() -> None:
    """The offline-import gate: the server and its routes load no model client; the
    executor is imported only when the page launches a Run."""
    probe = (
        "import sys, agentdiag.serve, agentdiag.serve.app, agentdiag.serve.server; "
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] in "
        "('anthropic', 'claude_agent_sdk') "
        "or m.startswith(('agentdiag.model', 'agentdiag.adapter', 'agentdiag.run.execute'))); "
        "print(','.join(leaked))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=REPO
    )

    assert completed.stdout.strip() == ""


def test_serve_prints_its_url_and_binds_to_localhost(toy: TargetPaths) -> None:
    from agentdiag.serve.server import HOST, make_server

    server = make_server(Workspace.find(toy.root), port=0)
    try:
        assert server.server_address[0] == HOST == "127.0.0.1"
        assert server.url == f"http://127.0.0.1:{server.port}/"
    finally:
        server.server_close()
    result = runner.invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0 and "--port" in result.stdout and "--open" in result.stdout


# --- the fix round: no read writes, ids in their grammar, the follow's edges ---


def test_reading_a_broken_sync_through_the_route_records_no_sync_break(toy: TargetPaths) -> None:
    """The Sync screen compares as `sync --check` does and writes nothing; the command still
    records the break, and the screen then lists the one recorded."""
    help_desk = Workspace.find(toy.root).resolve("help-desk")
    local = help_desk.directory / "prompts" / "system.md"
    local.write_text(local.read_text(encoding="utf-8") + "\nOne more line.\n", encoding="utf-8")
    app = app_of(toy)

    read = get(app, "/api/targets/help-desk/sync")

    assert read.status == 200, read.body
    assert read.body["result"]["status"] == "broken"
    assert read.body["sync_breaks"] == []
    assert not help_desk.sync_breaks.exists() or list(help_desk.sync_breaks.iterdir()) == []
    checked = runner.invoke(
        cli, ["sync", "--root", str(toy.root), "--target", "help-desk", "--check"]
    )
    assert checked.exit_code == 2, checked.output
    assert len(list(help_desk.sync_breaks.iterdir())) == 1
    assert len(get(app, "/api/targets/help-desk/sync").body["sync_breaks"]) == 1
    assert len(list(help_desk.sync_breaks.iterdir())) == 1


def test_a_change_record_id_outside_the_grammar_is_never_joined_onto_a_path(
    toy: TargetPaths,
) -> None:
    from agentdiag.change.record import ChangeRecordNotFound, find_record, is_record_id

    (toy.root / "x.md").write_text("---\nnot: a record\n---\n", encoding="utf-8")
    app = app_of(toy)

    for path in (
        "/api/changes/..%2F..%2F..%2Fx",
        "/api/changes/..%2F..%2F..%2Fx?target=toy-order-desk",
        "/api/changes/..%5Cx",
        "/api/changes/20260923-a-pushed-record.md",
    ):
        reply = get(app, path)
        assert_error(reply, 404, "is not a Change record id")
        assert str(toy.root) not in reply.body["error"]
    assert get(app, "/api/changes/../x").status == 404
    assert not is_record_id("../x") and is_record_id("20260918-fb-004")
    with pytest.raises(ChangeRecordNotFound, match="is not a Change record id"):
        find_record(toy, "../../../x")
    shown = runner.invoke(
        cli, ["change", "show", "--root", str(toy.root), "--target", SLUG, "../../../x"]
    )
    assert shown.exit_code == 3 and "is not a Change record id" in shown.output


def test_an_index_at_an_old_schema_version_is_a_409_naming_the_rebuild(toy: TargetPaths) -> None:
    import sqlite3

    from agentdiag.run.locate import index_path

    index = index_path(toy.root)
    index.unlink(missing_ok=True)
    connection = sqlite3.connect(index)
    connection.execute("PRAGMA user_version = 1")
    connection.close()

    reply = get(app_of(toy), "/api/runs")

    assert_error(reply, 409, "schema version 1", "agentdiag index rebuild")


def test_a_follow_that_fails_ends_in_an_error_event_and_a_finished_one(
    toy: TargetPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*arguments: Any, **options: Any) -> Iterator[str]:
        raise RuntimeError("the Trace could not be tailed")

    monkeypatch.setattr("agentdiag.trace.show.follow_story", broken)
    with serving(Workspace.find(toy.root)) as server:
        stream = events(server, f"/api/runs/{BASE}/follow")

    names = [name for name, _ in stream]
    assert names[-2:] == ["error", "finished"]
    assert stream[-2][1] == {"error": "RuntimeError: the Trace could not be tailed"}
    assert stream[-1][1]["reason"] == "error"


def test_a_follow_rereads_a_half_written_run_json_keeps_alive_and_ends_when_idle(
    toy: TargetPaths,
) -> None:
    """A Run this server did not launch, whose `run.json` is first read half written: the
    follow reads it again at the next poll instead of failing, sends `: keepalive` while it
    waits, and, nothing changing for `follow_idle_s`, ends with `finished` saying `idle`."""
    run_dir = toy.runs / "20260929T000000Z-hlfw"
    run_dir.mkdir()
    whole = json.loads((toy.runs / BASE / "run.json").read_text(encoding="utf-8"))
    whole.update(run_id=run_dir.name, scenarios=[whole["scenarios"][0]], trials=1)
    text = json.dumps(whole)
    (run_dir / "run.json").write_text(text[: len(text) // 2], encoding="utf-8")
    app = App(Workspace.find(toy.root), follow_idle_s=1.0, keepalive_s=0.1)

    def complete() -> None:
        (run_dir / "run.json").write_text(text, encoding="utf-8")

    timer = threading.Timer(0.4, complete)
    timer.start()
    frames = list(app._follow(run_dir, None))
    timer.join()

    assert ": keepalive\n\n" in frames
    named = [frame.split("\n")[0] for frame in frames if frame.startswith("event: ")]
    assert named == ["event: run", "event: trial", "event: scores", "event: finished"]
    finished = json.loads(frames[-1].split("data: ", 1)[1])
    assert finished["reason"] == "idle" and finished["scorecard"] is False
