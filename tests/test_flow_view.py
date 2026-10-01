"""Seam 1: the Flow view — a Change record as a story, a Flow from its definition (ticket 28).

Phase-7 decision 21: `change_record_story` tells a record in five lanes, each item linked to
what it cites and to nothing that is not there, and a record still `open`, `proposed` or
`pushed` shows its later lanes pending, each with the sentence that says what would fill it.
`flow_definition_view` reads one `DeployedSet.flows` entry in any of three shapes and names
what it found when it is none of them. Both are asserted over `tests/fixtures/changes/` (one
hand-written record per status and one imported entry, no customer content), over a
copy of the toy holding the Run fixtures the records cite, and, for the Flow, over the help
desk's platform, whose one Flow is also a Fingerprint section.

The renderer draws both under node through `tests/render_page.js`, served (no view embedded)
with the two routes ticket 16 will answer fed as JSON: `#flow/<id>` draws five lanes whose
cites are `data-cites`, and `#flow/def/<slug>/<env>/<id>` one box per step and one line per
edge. Those tests skip when node is not on PATH.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentdiag.change.record import ChangeRecord, PushEvent, load_record, redactable_fields
from agentdiag.change.redact import redact
from agentdiag.connector.environment import ResolvedEnvironment
from agentdiag.connector.inprocess import InProcessConnector
from agentdiag.report.flow import FlowDefinitionUnreadable, flow_definition_view
from agentdiag.report.story import ChangeRecordStory, change_record_story
from agentdiag.run.manifest import ConnectorSection
from tests.change_fixtures import (
    BASE,
    CHANGES,
    PRMT,
    PUSH_RECORD,
    RESTORE_POINT,
    SLUG,
    TRIGGER_TRIAL,
    toy_with_records,
)

REPO = Path(__file__).resolve().parents[1]
HELP_DESK = REPO / "examples" / "workspace" / ".agentdiag" / "targets" / "help-desk"
RENDERER = REPO / "src" / "agentdiag" / "report" / "static" / "renderer.js"
PAGE = REPO / "tests" / "render_page.js"
TRIGGER_LINK = f"#run/{BASE}/{TRIGGER_TRIAL}/1"
TRIGGER_CITES = ["turn-1", "llm_call-1"]
LANES = ("what_happened", "problem", "fix", "expected", "observed")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

PENDING: dict[str, set[str]] = {
    "20260923-an-open-record": {"fix", "expected", "observed"},
    "20260923-a-proposed-record": {"fix", "expected", "observed"},
    "20260923-a-pushed-record": {"observed"},
    "20260923-a-verified-record": set(),
    "20260923-a-refuted-record": set(),
    "20260923-a-wontfix-record": set(),
    "20260923-a-superseded-record": set(),
    "20260918-fb-004": set(),
}
"""Every fixture record, with the lanes its status leaves pending."""


def fixture(record_id: str) -> ChangeRecord:
    return load_record(CHANGES / f"{record_id}.md")


def lane_links(story: ChangeRecordStory) -> list[str]:
    return [item.link for lane in story.lanes for item in lane.items if item.link is not None]


# --- the fixtures ---


def test_the_fixtures_are_one_record_per_status_and_carry_nothing_to_redact() -> None:
    records = {path.stem: load_record(path) for path in sorted(CHANGES.glob("*.md"))}

    assert set(records) == set(PENDING)
    assert {record.status for record in records.values()} == {
        "open",
        "proposed",
        "pushed",
        "verified",
        "refuted",
        "wontfix",
        "superseded",
    }
    assert records["20260918-fb-004"].imported_from == "HISTORY.md FB-004"
    for record_id, record in records.items():
        assert record.id == record_id
        assert all(redact(text, []) == text for _, text in redactable_fields(record)), record_id


def test_the_fixtures_times_run_forward_as_the_lifecycle_has_them() -> None:
    """The trigger's Run precedes `opened_at`, the expectation is stated after the record
    opened and before the verifying Run began, which is later than its baseline."""
    created = {
        run: json.loads((REPO / "tests" / "fixtures" / "runs" / run / "run.json").read_text())[
            "created_at"
        ]
        for run in (BASE, PRMT)
    }
    for record_id in PENDING:
        record = fixture(record_id)
        if record.trigger.run is not None:
            assert created[record.trigger.run] < record.opened_at, record_id
        if record.expected is not None:
            assert record.opened_at < record.expected.stated_at, record_id
        for event in record.pushes:
            assert record.opened_at <= event.at, record_id
        verification = record.verification
        if verification is not None and record.imported_from is None:
            assert (verification.baseline, verification.run) == (BASE, PRMT), record_id
            assert record.expected is not None
            assert created[BASE] < record.expected.stated_at < created[PRMT], record_id
            assert created[PRMT] < verification.compared_at, record_id


def test_the_fixtures_validate_inside_a_target(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from agentdiag.cli import app

    target = toy_with_records(tmp_path)

    result = CliRunner().invoke(app, ["validate", "--root", str(target.root)])

    assert result.exit_code == 0, result.output
    assert "8 Change records: 0 errors" in result.output


# --- every lane, never blank ---


@pytest.mark.parametrize("record_id", sorted(PENDING))
def test_every_fixture_renders_every_lane_and_its_pending_lanes_say_what_would_fill_them(
    record_id: str,
) -> None:
    story = change_record_story(fixture(record_id), None)

    assert [lane.title for lane in story.lanes] == [
        "What happened",
        "The problem",
        "The fix",
        "The expected effect",
        "What was observed",
    ]
    for name in LANES:
        lane = getattr(story, name)
        assert lane.items and all(item.text.strip() for item in lane.items), (record_id, name)
        assert lane.pending == (name in PENDING[record_id]), (record_id, name)
        if lane.pending:
            assert lane.items[-1].text.startswith("Not "), (record_id, name)
            assert lane.items[-1].text.rstrip().endswith("."), (record_id, name)


def test_a_verified_records_observed_lane_lists_each_observation_and_links_the_compare(
    tmp_path: Path,
) -> None:
    target = toy_with_records(tmp_path)
    record = fixture("20260923-a-verified-record")

    observed = change_record_story(record, target).observed

    assert observed.items[0].link == f"#compare/{BASE}/{PRMT}"
    assert observed.items[0].text.startswith(f"Verified: Run {BASE} -> Run {PRMT}")
    listed = [item for item in observed.items if " · " in item.text]
    assert len(listed) == len(record.observed) == 2
    for item, observation in zip(listed, record.observed, strict=True):
        assert observation.eval in item.text and observation.rationale in item.text
        assert item.link == f"#run/{PRMT}/{observation.scenario}/{observation.trial}"


def test_a_refuted_record_links_its_compare_and_its_failing_scores(tmp_path: Path) -> None:
    story = change_record_story(fixture("20260923-a-refuted-record"), toy_with_records(tmp_path))

    assert story.observed.items[0].link == f"#compare/{BASE}/{PRMT}"
    assert story.observed.items[0].text.startswith("Refuted")
    assert any("must_not_say fail" in item.text for item in story.observed.items)


def test_items_link_to_the_trial_its_spans_the_file_the_push_record_and_the_restore_point(
    tmp_path: Path,
) -> None:
    target = toy_with_records(tmp_path, restore_point=True)
    record = fixture("20260923-a-pushed-record")

    story = change_record_story(record, target)

    (trigger, opened) = story.what_happened.items
    assert trigger.link == TRIGGER_LINK
    assert trigger.cites == TRIGGER_CITES
    assert story.problem.items[0].link == trigger.link
    assert story.problem.items[0].cites == trigger.cites
    assert opened.link is None
    links = [item.link for item in story.fix.items]
    assert "manifest.yaml" in links
    assert PUSH_RECORD in links
    assert RESTORE_POINT in links
    assert story.file == f".agentdiag/targets/{SLUG}/changes/20260923-a-pushed-record.md"


def test_nothing_links_to_what_is_not_there_and_the_cites_stay_named(tmp_path: Path) -> None:
    """The Restore point is gitignored: named, not linked. A Run that is not under `runs/` is
    not linked, and the Spans its Trial cites stay in `cites` and are named in the text.
    Without a Target, paths are never linked."""
    target = toy_with_records(tmp_path)
    shutil.rmtree(target.runs / BASE)
    record = fixture("20260923-a-pushed-record")

    story = change_record_story(record, target)
    alone = change_record_story(record, None)

    restore = next(item for item in story.fix.items if item.text.startswith("Restore point"))
    assert restore.link is None and "gitignored" in restore.text
    trigger = story.what_happened.items[0]
    assert trigger.link is None
    assert trigger.cites == TRIGGER_CITES
    assert trigger.text.endswith("(cites turn-1, llm_call-1 in a Run not under this Target)")
    assert story.problem.items[0].cites == TRIGGER_CITES
    assert all(not link.startswith("#run/") for link in lane_links(story))
    assert alone.what_happened.items[0].link == TRIGGER_LINK
    assert all(link.startswith("#") for link in lane_links(alone))


def test_a_diagnosis_of_another_trial_is_linked_without_the_triggers_cites() -> None:
    record = fixture("20260923-an-open-record")
    assert record.diagnosis is not None
    record = record.model_copy(
        update={"diagnosis": record.diagnosis.model_copy(update={"trial": 2})}
    )

    problem = change_record_story(record, None).problem.items[0]

    assert problem.link == f"#run/{BASE}/{TRIGGER_TRIAL}/2"
    assert problem.cites == []


def test_a_push_event_with_one_fingerprint_prints_the_one_it_has() -> None:
    record = fixture("20260923-a-pushed-record")
    events = [
        PushEvent(
            kind="local",
            environment="local",
            at="2026-09-23T10:01:30Z",
            run=PRMT,
            fingerprint_before="a" * 64,
        ),
        PushEvent(
            kind="local",
            environment="local",
            at="2026-09-23T10:01:40Z",
            run=PRMT,
            fingerprint_after="b" * 64,
        ),
    ]

    fix = change_record_story(record.model_copy(update={"pushes": events}), None).fix

    texts = [item.text for item in fix.items if item.text.startswith("Re-synced")]
    assert "Fingerprint from aaaaaaaa" in texts[0]
    assert "Fingerprint to bbbbbbbb" in texts[1]


def test_a_push_record_path_outside_the_target_is_shown_and_never_read(tmp_path: Path) -> None:
    target = toy_with_records(tmp_path)
    outside = tmp_path / "elsewhere.json"
    outside.write_text(json.dumps({"restore_point": "leaked"}), encoding="utf-8")
    record = fixture("20260923-a-pushed-record")
    for reference in (str(outside), "../../../../../elsewhere.json"):
        event = record.pushes[0].model_copy(update={"push_record": reference})
        assert record.change is not None
        change = record.change.model_copy(update={"files": [reference]})
        moved = record.model_copy(update={"pushes": [event], "change": change})

        fix = change_record_story(moved, target).fix

        assert all(item.link is None for item in fix.items if reference in item.text)
        assert not any(item.text.startswith("Restore point") for item in fix.items), reference


def test_an_imported_record_says_its_runs_are_not_recorded_and_links_nothing(
    tmp_path: Path,
) -> None:
    story = change_record_story(fixture("20260918-fb-004"), toy_with_records(tmp_path))

    assert lane_links(story) == []
    assert story.imported_from == "HISTORY.md FB-004"
    assert "agentdiag recorded no Run" in story.what_happened.items[1].text
    assert "recorded neither Run" in story.observed.items[0].text
    assert "Both Scenarios pass" in story.observed.items[1].text
    assert "an environment the record does not name" in story.fix.items[-1].text


def test_a_record_closed_without_a_change_says_so_in_the_lanes_it_never_reached(
    tmp_path: Path,
) -> None:
    target = toy_with_records(tmp_path)
    wontfix = change_record_story(fixture("20260923-a-wontfix-record"), target)
    superseded = change_record_story(fixture("20260923-a-superseded-record"), target)

    assert wontfix.fix.items[0].text == "No change was proposed: the record closed as wontfix."
    assert "Rule 4's cap" in wontfix.observed.items[0].text
    assert superseded.fix.items[-1].text == "No push was made: the record closed as superseded."
    assert superseded.observed.items[0].link == "#flow/20260923-a-verified-record"
    (target.changes / "20260923-a-verified-record.md").unlink()
    gone = change_record_story(fixture("20260923-a-superseded-record"), target)
    assert gone.observed.items[0].link is None


def test_a_changed_flow_links_its_definition_in_the_pushed_environment() -> None:
    record = fixture("20260923-a-pushed-record")
    assert record.change is not None
    record = record.model_copy(
        update={"change": record.change.model_copy(update={"flow_ids": ["open_ticket_flow"]})}
    )

    story = change_record_story(record, None)

    flow = next(item for item in story.fix.items if item.text.startswith("Flow "))
    assert flow.link == f"#flow/def/{SLUG}/local/open_ticket_flow"


# --- the Flow definition ---

STEPS_AND_EDGES: dict[str, Any] = {
    "tool": "open_ticket",
    "state": "on",
    "version": 2,
    "steps": [
        {"id": "a", "name": "Receive", "kind": "trigger", "calls": "open_ticket"},
        {"id": "b", "name": "File", "type": "action", "tool": "queue.add"},
        {"id": "c"},
    ],
    "edges": [["a", "b"], {"from": "b", "to": "c"}, {"source": "a", "target": "c"}],
}
NODES_AND_LINKS: dict[str, Any] = {
    "state": "off",
    "nodes": [{"id": "a", "name": "Receive"}, {"id": "b", "name": "File"}, {"id": "c"}],
    "links": [{"source": "a", "target": "b"}, ["b", "c"], ["a", "c"]],
}
NEXT_POINTERS: dict[str, Any] = {
    "steps": [
        {"id": "a", "name": "Receive", "next": ["b", "c"]},
        {"id": "b", "name": "File", "next": "c"},
        {"id": "c", "kind": "respond"},
    ]
}


@pytest.mark.parametrize(
    "definition", [STEPS_AND_EDGES, NODES_AND_LINKS, NEXT_POINTERS], ids=["edges", "links", "next"]
)
def test_each_accepted_shape_reads_every_step_once_and_every_edge(
    definition: dict[str, Any],
) -> None:
    view = flow_definition_view("f", definition)

    assert [step.id for step in view.steps] == ["a", "b", "c"]
    assert sorted(view.edges) == [("a", "b"), ("a", "c"), ("b", "c")]
    assert view.steps[2].name == "c"  # a step with no name is named by its id


def test_kind_calls_tool_state_and_version_pass_through_as_text() -> None:
    view = flow_definition_view("f", STEPS_AND_EDGES)

    assert (view.tool, view.state, view.version) == ("open_ticket", "on", "2")
    assert [(step.kind, step.calls) for step in view.steps] == [
        ("trigger", "open_ticket"),
        ("action", "queue.add"),
        (None, None),
    ]


@pytest.mark.parametrize(
    ("definition", "named"),
    [
        ({"blocks": [{"id": "a"}], "wires": []}, "found blocks (list), wires (list)"),
        ({"steps": [{"id": "a"}, {"id": "b"}]}, "found steps (list)"),
        ({"nodes": [{"id": "a"}]}, "found nodes (list)"),
        ({}, "found an empty mapping"),
        ({"steps": [{"id": "a"}], "edges": [["a", "z"]]}, "does not hold: z"),
        ({"steps": [{"name": "a"}], "edges": []}, "step 1 has no id (found name)"),
        ({"steps": [{"id": "a"}, {"id": "a"}], "edges": []}, "step ids repeat: a"),
        ({"steps": [{"id": "a"}], "edges": [["a"]]}, "an edge is not a pair"),
    ],
)
def test_an_unknown_shape_is_the_named_error_saying_what_was_found(
    definition: dict[str, Any], named: str
) -> None:
    with pytest.raises(FlowDefinitionUnreadable, match=r"^Flow f: ") as raised:
        flow_definition_view("f", definition)

    assert named in str(raised.value)


def test_no_platform_vocabulary_in_the_reader_or_the_renderer() -> None:
    """The step kinds of the help desk's Flow are the platform's words; neither the reader
    nor the renderer spells them."""
    from agentdiag.examples.helpdesk.platform import OPEN_TICKET_FLOW

    kinds = {step["kind"] for step in OPEN_TICKET_FLOW["steps"]}
    reader = (REPO / "src" / "agentdiag" / "report" / "flow.py").read_text(encoding="utf-8")
    renderer = RENDERER.read_text(encoding="utf-8")
    style = (REPO / "src" / "agentdiag" / "report" / "static" / "style.css").read_text("utf-8")
    flow_code = renderer[renderer.index("function flowDefHtml") :]

    for kind in kinds - {"action"}:  # `action` is also a Span kind the flame graph colours
        assert f"'{kind}'" not in flow_code and f'"{kind}"' not in reader, kind
        assert f".box.{kind}" not in style, kind


# --- the help desk's Flow ---


def help_desk_connector() -> InProcessConnector:
    block = {"deployed": "agentdiag.examples.helpdesk.platform:DEPLOYED"}
    section = ConnectorSection(kind="inprocess", environments={"local": dict(block)})
    resolved = ResolvedEnvironment(
        name="local", identifiers=dict(block), protected=False, side_effects="none"
    )
    return InProcessConnector(section, environments={"local": resolved})


def test_the_help_desk_platform_holds_one_flow_the_connector_reads_and_the_view_draws() -> None:
    read = help_desk_connector().read_deployed_set("local")

    assert set(read.flows) == {"open_ticket_flow"}
    view = flow_definition_view("open_ticket_flow", read.flows["open_ticket_flow"])
    assert view.tool == "open_ticket" and view.state == "on" and view.version == "3"
    assert [step.id for step in view.steps] == ["receive", "file", "safety", "page", "acknowledge"]
    assert len(view.edges) == 5
    assert view.steps[0].calls == "open_ticket"


def test_the_help_desk_fingerprint_has_the_flow_section() -> None:
    from agentdiag.examples.helpdesk import platform

    fingerprint = json.loads((HELP_DESK / "fingerprint.json").read_text(encoding="utf-8"))

    assert fingerprint["sections"]["flow.open_ticket_flow"]["kind"] == "flow"
    assert fingerprint["sections"]["flow.open_ticket_flow"]["covered_by"] == "connector"
    assert platform.STAGING["flows"] == platform.DEPLOYED["flows"]
    assert platform.STAGING["flows"] is not platform.DEPLOYED["flows"]


# --- the renderer, served ---


def served(hashes: list[str], routes: dict[str, Any], tmp_path: Path) -> list[dict[str, Any]]:
    """Boot the renderer as `serve` would send it (no view embedded) under node, answer the
    routes from `routes`, and route to each hash."""
    assert NODE is not None
    page = tmp_path / "page.html"
    page.write_text(
        f"<!DOCTYPE html><html><body><script>\n{RENDERER.read_text(encoding='utf-8')}</script>"
        "</body></html>",
        encoding="utf-8",
    )
    answers = tmp_path / "routes.json"
    answers.write_text(json.dumps(routes), encoding="utf-8")
    completed = subprocess.run(
        [NODE, str(PAGE), str(page), json.dumps(hashes), str(answers)],
        capture_output=True,
        text=True,
        check=True,
    )
    return [json.loads(line) for line in completed.stdout.splitlines()]


@needs_node
def test_the_flow_route_renders_the_five_lanes_with_cites_as_data_cites(tmp_path: Path) -> None:
    target = toy_with_records(tmp_path)
    routes = {
        f"/api/changes/{record_id}": change_record_story(fixture(record_id), target).model_dump(
            mode="json"
        )
        for record_id in PENDING
    }

    results = served([f"#flow/{record_id}" for record_id in PENDING], routes, tmp_path)

    for record_id, result in zip(PENDING, results, strict=True):
        main = result["main"]
        assert result["error"] is None, (record_id, result["error"])
        assert main.count("data-lane=") == 5, record_id
        assert main.count('class="node ') == 5, record_id
        assert main.count(' pending"') == len(PENDING[record_id]), record_id
    pushed = results[list(PENDING).index("20260923-a-pushed-record")]["main"]
    for span in TRIGGER_CITES:
        assert (
            f'data-cites="[&quot;{span}&quot;]" data-run="{BASE}" '
            f'data-scenario="{TRIGGER_TRIAL}" data-trial="1"'
        ) in pushed
    assert f'href="{TRIGGER_LINK}"' in pushed
    verified = results[list(PENDING).index("20260923-a-verified-record")]["main"]
    assert f'href="#compare/{BASE}/{PRMT}"' in verified


@needs_node
def test_the_flow_definition_route_draws_one_box_per_step_and_one_line_per_edge(
    tmp_path: Path,
) -> None:
    from agentdiag.examples.helpdesk.platform import OPEN_TICKET_FLOW

    cases = {
        "help-desk/local/open_ticket_flow": flow_definition_view(
            "open_ticket_flow", OPEN_TICKET_FLOW
        ),
        "t/e/edges": flow_definition_view("edges", STEPS_AND_EDGES),
        "t/e/links": flow_definition_view("links", NODES_AND_LINKS),
        "t/e/next": flow_definition_view("next", NEXT_POINTERS),
        "t/e/cycle": flow_definition_view(
            "cycle", {"steps": [{"id": "a", "next": "b"}, {"id": "b", "next": "a"}]}
        ),
    }
    routes = {f"/api/flows/{path}": view.model_dump(mode="json") for path, view in cases.items()}

    results = served([f"#flow/def/{path}" for path in cases], routes, tmp_path)

    for (path, view), result in zip(cases.items(), results, strict=True):
        main = result["main"]
        assert result["error"] is None, (path, result["error"])
        assert main.count('<rect class="box"') == len(view.steps), path
        assert main.count('<path class="edge"') == len(view.edges), path
        for step in view.steps:
            assert main.count(f'data-step="{step.id}"') == 1, (path, step.id)
    assert 'called by tool <span class="mono">open_ticket</span>' in results[0]["main"]


@needs_node
def test_the_flow_screen_without_an_id_says_what_to_name_and_a_missing_record_is_an_error(
    tmp_path: Path,
) -> None:
    empty, missing = served(["#flow", "#flow/nothing-here"], {}, tmp_path)

    assert empty["error"] is None and "Name a Change record" in empty["main"]
    assert missing["error"] is not None and "/api/changes/nothing-here: 404" in missing["main"]


TRI3 = "20260923T100600Z-tri3"


def tri3_routes() -> tuple[dict[str, Any], str]:
    """The served routes of the three-Trial Run fixture, and a Span id its second Trial of
    `where-is-shipped-order` holds (Span ids repeat across Trials)."""
    from agentdiag.report.view import run_view

    view = run_view(REPO / "tests" / "fixtures" / "runs" / TRI3)
    story = next(
        t for t in view.trials if (t.scenario_id, t.trial) == ("where-is-shipped-order", 2)
    )
    span = story.turns[0].spans[0].span_id
    routes = {
        "/api/runs": [{"run_id": TRI3, "target": view.header.target, "source": "run"}],
        f"/api/runs/{TRI3}": view.model_dump(mode="json"),
    }
    return routes, span


@needs_node
def test_a_cite_handoff_scrolls_to_the_top_first_and_to_the_cited_span_last(
    tmp_path: Path,
) -> None:
    routes, span = tri3_routes()

    (result,) = served([f"#run/{TRI3}/where-is-shipped-order/2?cite={span}"], routes, tmp_path)

    assert result["error"] is None, result["error"]
    assert result["scrolls"][0] == {"top": True}
    assert result["scrolls"][-1] == {"into": span}


@needs_node
def test_a_cite_of_another_trial_of_the_shown_run_opens_that_trial_and_marks_nothing_here(
    tmp_path: Path,
) -> None:
    routes, span = tri3_routes()

    def click(run: str, trial: str) -> dict[str, Any]:
        href = f"#run/{run}/where-is-shipped-order/{trial}"
        return {
            "click": {
                "cites": json.dumps([span]),
                "run": run,
                "scenario": "where-is-shipped-order",
                "trial": trial,
                "href": href,
            }
        }

    shown = f"#run/{TRI3}/where-is-shipped-order/2"
    _, other_trial, other_run, same = served(
        [shown, click(TRI3, "1"), click(PRMT, "1"), click(TRI3, "2")], routes, tmp_path
    )

    assert other_trial["cited"] == []
    assert other_trial["hash"] == f"run/{TRI3}/where-is-shipped-order/1?cite={span}"
    assert other_run["cited"] == []
    assert other_run["hash"] == f"run/{PRMT}/where-is-shipped-order/1?cite={span}"
    assert same["cited"] == [span]  # the shown Trial's own cite highlights in place
