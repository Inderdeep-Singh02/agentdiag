"""Seam 1: the Report — one Run's `RunView` embedded with the one renderer (ticket 15).

Phase-7 decisions 19 and 20: `run_view` projects a Run directory into the view model the
Report, `show` and the UI share, and `render_report` embeds it with `renderer.js` and
`style.css` into one self-contained, deterministic HTML file that `run`, `rescore` and
`import` write beside `scorecard.json`. The committed Run fixtures carry no Report (they
predate it, D40), so the view is asserted over them directly; freshly made Runs — a replay
of the toy, a re-synced one, an imported one — are asserted through the file they wrote.

The renderer's string-building functions are pure, so the flame graph is checked under
node with a two-method `document` shim: one `<rect data-id>` per Span in the icicle, one
ladder row per Span, and the Spans a Score cites marked. Those tests skip when node is not
on PATH; nothing else needs it.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.examples.toy import SYSTEM_PROMPT
from agentdiag.report import REPORT_FILE, RunView, render_report, run_view, write_report
from agentdiag.run.locate import SCORECARD_FILE, trace_path
from agentdiag.trace.reader import read_trace
from agentdiag.trace.show import SpanView, TrialStory
from agentdiag.trace.spans import project_spans

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
RUNS = FIXTURES / "runs"
JUDGED = RUNS / "20260923T100400Z-jdge"
RESCORED = RUNS / "20260923T100500Z-resc"
RENDERER = REPO / "src" / "agentdiag" / "report" / "static" / "renderer.js"
PAGE = REPO / "tests" / "render_page.js"
VARIANTS = ("BC", "A", "B", "C")
EXAMPLE = REPO / "examples" / "toy"
RECORDING = FIXTURES / "recordings" / "toy-orders.jsonl"
PROXY = FIXTURES / "evidence" / "helpdesk-proxy-rows.json"
SCENARIO = "cancel-processing-order"
PROMPT_READ_AT = "agentdiag.examples.toy.target.SYSTEM_PROMPT"
EMBED_START = "<script>window.AGENTDIAG_VIEW="
EMBED_END = ";</script>"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

runner = CliRunner()


def invoke(*arguments: str) -> Any:
    return runner.invoke(app, list(arguments))


def flattened(story: TrialStory) -> Iterator[SpanView]:
    """Every SpanView of a Trial, the Turns' own Spans included, as the renderer walks them."""

    def walk(view: SpanView) -> Iterator[SpanView]:
        yield view
        for child in view.children:
            yield from walk(child)

    for turn in story.turns:
        for item in [*turn.before, *([turn.span] if turn.span else []), *turn.spans, *turn.after]:
            if isinstance(item, SpanView):
                yield from walk(item)


def expected_placement(trace: Path) -> dict[str, tuple[int, int | None, int]]:
    """Each Span's offsets from the Trace's first Event and its depth, from the Events."""
    events = read_trace(trace)
    spans = [span for span in project_spans(events) if span.actor != "judge"]
    by_id = {span.span_id: span for span in spans}
    origin = events[0].ts

    def depth(span_id: str) -> int:
        parent = by_id[span_id].parent_span_id
        return 0 if parent is None or parent not in by_id else 1 + depth(parent)

    return {
        span.span_id: (
            span.start_ms - origin,
            None if span.end_ms is None else span.end_ms - origin,
            depth(span.span_id),
        )
        for span in spans
    }


def embedded(html: str) -> dict[str, Any]:
    """The RunView JSON a Report file carries, read back."""
    start = html.index(EMBED_START) + len(EMBED_START)
    loaded: dict[str, Any] = json.loads(html[start : html.index(EMBED_END, start)])
    return loaded


def in_node(view: dict[str, Any] | RunView, probe: str, tmp_path: Path) -> Any:
    """Load renderer.js under node with `view` embedded, run `probe` after it in the same
    script (so it sees the renderer's constants), and return what it set `__result` to."""
    assert NODE is not None
    data = view.model_dump(mode="json") if isinstance(view, RunView) else view
    (tmp_path / "view.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "probe.js").write_text(probe, encoding="utf-8")
    harness = tmp_path / "harness.js"
    harness.write_text(
        "const fs=require('fs'),vm=require('vm');\n"
        "const [renderer,view,probe]=process.argv.slice(2).map(p=>fs.readFileSync(p,'utf8'));\n"
        "const context={window:{AGENTDIAG_VIEW:JSON.parse(view)},location:{hash:''},"
        "document:{querySelectorAll:()=>[],querySelector:()=>null}};\n"
        "vm.createContext(context);\n"
        "vm.runInContext(renderer+'\\n'+probe,context);\n"
        "process.stdout.write(JSON.stringify(context.__result));\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            NODE,
            str(harness),
            str(RENDERER),
            str(tmp_path / "view.json"),
            str(tmp_path / "probe.js"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def toy_root(tmp_path: Path) -> Path:
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    return root


def toy_runs(root: Path) -> list[Path]:
    directory = root / ".agentdiag" / "targets" / "toy-order-desk" / "runs"
    return sorted(directory.iterdir()) if directory.exists() else []


def replay(root: Path) -> Any:
    return invoke("run", "--root", str(root), "--scenario", SCENARIO, "--replay", str(RECORDING))


# --- the view model over committed Run fixtures ---


def test_the_view_of_a_committed_run_holds_its_scorecard_counts_and_diagnosis() -> None:
    view = run_view(JUDGED)
    scorecard = json.loads((JUDGED / SCORECARD_FILE).read_text(encoding="utf-8"))

    assert view.header.run_id == JUDGED.name
    assert view.scorecard.counts == scorecard["counts"]
    assert [(story.scenario_id, story.trial) for story in view.trials] == [
        (line["id"], line["trial"]) for line in scorecard["scenarios"]
    ]
    diagnosed = [story for story in view.trials if story.diagnosis is not None]
    assert diagnosed, "the judged fixture carries a Diagnosis"
    html = render_report(view)
    for story in diagnosed:
        assert story.diagnosis is not None
        assert json.dumps(story.diagnosis.text, ensure_ascii=False)[1:-1] in html
    assert embedded(html)["scorecard"]["counts"] == scorecard["counts"]


def test_every_span_of_a_trace_has_one_span_view_placed_as_the_events_place_it() -> None:
    view = run_view(JUDGED)

    for story in view.trials:
        placed = {
            span.span_id: (span.start_ms, span.end_ms, span.depth) for span in flattened(story)
        }
        assert len(placed) == len(list(flattened(story)))
        assert placed == expected_placement(trace_path(JUDGED, story.scenario_id, story.trial))


def test_each_message_event_is_a_message_view_with_its_offset() -> None:
    story = run_view(JUDGED).trials[0]
    events = read_trace(trace_path(JUDGED, story.scenario_id, story.trial))

    assert [(m.seq, m.at_ms, m.role, m.text) for m in story.messages] == [
        (
            event.seq,
            event.ts - events[0].ts,
            (event.model_extra or {})["role"],
            (event.model_extra or {})["content"],
        )
        for event in events
        if event.type == "message"
    ]


def test_a_rescores_view_carries_traces_from_and_tells_the_trials_from_the_source_trace() -> None:
    view = run_view(RESCORED)
    source = json.loads((RESCORED / "run.json").read_text(encoding="utf-8"))["traces_from"]

    assert view.header.traces_from == source
    assert all(story.turns and story.rescored_from == source for story in view.trials)
    assert f'"traces_from":"{source}"' in render_report(view)


def test_the_report_is_deterministic_self_contained_and_escapes_the_script_end(
    tmp_path: Path,
) -> None:
    view = run_view(JUDGED)
    html = render_report(view)

    assert html == render_report(run_view(JUDGED))
    for external in ("http://", "https://", "<link", " src=", "@import"):
        assert external not in html
    story = next(story for story in view.trials if story.diagnosis is not None)
    assert story.diagnosis is not None
    story.diagnosis.text = "closes early</script><b>not markup</b>"
    tricky = render_report(view)
    assert "</script><b>" not in tricky
    assert "closes early\\u003c/script\\u003e" in tricky
    texts = [
        trial["diagnosis"]["text"] for trial in embedded(tricky)["trials"] if trial["diagnosis"]
    ]
    assert "closes early</script><b>not markup</b>" in texts


def test_a_report_is_written_once(tmp_path: Path) -> None:
    run_dir = tmp_path / JUDGED.name
    shutil.copytree(JUDGED, run_dir)

    first = write_report(run_dir)
    written = (run_dir / REPORT_FILE).read_bytes()
    second = write_report(run_dir)

    assert first == run_dir / REPORT_FILE
    assert second is None
    assert (run_dir / REPORT_FILE).read_bytes() == written


def test_the_report_imports_no_sdk() -> None:
    probe = (
        "import sys, agentdiag.report, agentdiag.report.view, agentdiag.report.html; "
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] in "
        "('anthropic', 'claude_agent_sdk') "
        "or m.startswith(('agentdiag.model', 'agentdiag.adapter'))); "
        "print(','.join(leaked))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=REPO
    )

    assert completed.stdout.strip() == ""


# --- the Runs that write one ---


def test_run_replay_writes_a_report_after_the_scorecard_that_opens_on_the_run(
    tmp_path: Path,
) -> None:
    root = toy_root(tmp_path)

    result = replay(root)

    assert result.exit_code in {0, 1}, result.output
    (run_dir,) = toy_runs(root)
    html = (run_dir / REPORT_FILE).read_text(encoding="utf-8")
    view = embedded(html)
    assert view["header"]["run_id"] == run_dir.name
    assert f"agentdiag Report · {run_dir.name}" in html
    # Rendered from the Scorecard on disk: it was written first.
    scorecard = json.loads((run_dir / SCORECARD_FILE).read_text(encoding="utf-8"))
    assert view["scorecard"] == json.loads(json.dumps(scorecard))
    assert html == render_report(run_view(run_dir))
    if NODE is not None:
        assert in_node(view, "globalThis.__result=startHash();", tmp_path) == (
            f"#report/{run_dir.name}"
        )


def test_a_resynced_runs_report_opens_with_the_sync_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = toy_root(tmp_path)
    assert invoke("sync", "--root", str(root)).exit_code == 0
    replay(root)
    (prior,) = toy_runs(root)
    monkeypatch.setattr(
        PROMPT_READ_AT, SYSTEM_PROMPT.replace("at most three sentences", "at most two sentences")
    )

    replay(root)

    later = next(path for path in toy_runs(root) if path != prior)
    html = (later / REPORT_FILE).read_text(encoding="utf-8")
    view = embedded(html)
    assert view["sync"]["resynced_from"] is not None
    assert [section["id"] for section in view["sync"]["sections"]] == ["prompt.system"]
    payload = html[html.index(EMBED_START) :]
    assert payload.index('"sync":') < payload.index('"scorecard":')
    if NODE is not None:
        order = in_node(
            view,
            "const html=runHtml(VIEW,[VIEW.header.run_id],{},true,[]);"
            "globalThis.__result=[html.indexOf('data-section=\"sync\"'),"
            "html.indexOf('data-section=\"scorecard\"'),html.indexOf('prompt.system')];",
            tmp_path,
        )
        sync_at, scorecard_at, section_at = order
        assert 0 <= sync_at < section_at < scorecard_at


def import_workspace(tmp_path: Path) -> Path:
    """`init`, then the Manifest given the help desk's tools, as `test_import_cli` does."""
    root = tmp_path / "ws"
    assert invoke("init", "--root", str(root)).exit_code == 0
    path = root / ".agentdiag" / "targets" / "default" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["tools"] = {
        **manifest.get("tools", {}),
        "search_articles": {"kind": "retrieval"},
        "open_ticket": {"kind": "action"},
        "escalate": {"kind": "action"},
    }
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return root


def test_an_imported_run_writes_a_report_whose_spans_have_offsets_and_depth(
    tmp_path: Path,
) -> None:
    root = import_workspace(tmp_path)

    result = invoke("import", "--root", str(root), "--rows", str(PROXY))

    assert result.exit_code == 0, result.output
    (run_dir,) = sorted((root / ".agentdiag" / "targets" / "default" / "runs").iterdir())
    view = run_view(run_dir)
    assert view.header.source == "imported" and view.header.importer is not None
    spans = [span for story in view.trials for span in flattened(story)]
    assert any(span.start_ms > 0 for span in spans)
    assert any(span.depth > 0 for span in spans)
    (story,) = view.trials
    assert {
        span.span_id: (span.start_ms, span.end_ms, span.depth) for span in flattened(story)
    } == expected_placement(trace_path(run_dir, story.scenario_id, 1))
    assert embedded((run_dir / REPORT_FILE).read_text(encoding="utf-8"))["header"]["source"] == (
        "imported"
    )


# --- the renderer, under node ---

FLAME_PROBE = """
const s=VIEW.trials.find(t=>t.scenario_id===SCENARIO);const tl=spanRows(s);
const ids=(html,re)=>[...html.matchAll(re)].map(m=>m[1]);
const RECT=/<rect class="[^"]*" data-id="([^"]*)"/g, ROW=/<tr class="[^"]*" data-id="([^"]*)"/g;
const b=flameB(tl), c=flameC(tl,s);
variant='BC';const both=flame(tl,s);
cite(CITED);
const citedB=flameB(tl), citedC=flameC(tl,s);
globalThis.__result={
  icicle:ids(b,RECT), ladder:ids(c,ROW),
  default:[both.includes('<svg class="flame"'),both.includes('<table class="ladder"'),
    both.indexOf('<svg')<both.indexOf('<table')],
  markedB:ids(citedB,/<rect class="[^"]* cited" data-id="([^"]*)"/g),
  dimmedB:ids(citedB,/<rect class="[^"]* dim" data-id="([^"]*)"/g),
  markedC:ids(citedC,/<tr class="cited" data-id="([^"]*)"/g),
  dimmedC:ids(citedC,/<tr class="dim" data-id="([^"]*)"/g),
};
"""


@needs_node
def test_the_icicle_and_the_ladder_draw_every_span_once_and_cite_marks_the_cited(
    tmp_path: Path,
) -> None:
    view = run_view(JUDGED)
    story = next(
        story
        for story in view.trials
        if any(score.evidence for score in story.scores or []) and len(list(flattened(story))) > 2
    )
    cited = next(score.evidence for score in story.scores or [] if score.evidence)
    probe = f"const SCENARIO={json.dumps(story.scenario_id)},CITED={json.dumps(cited)};"
    result = in_node(view, probe + FLAME_PROBE, tmp_path)

    every = [span.span_id for span in flattened(story)]
    assert sorted(result["icicle"]) == sorted(every)
    assert sorted(result["ladder"]) == sorted(every)
    assert result["default"] == [True, True, True]
    assert sorted(result["markedB"]) == sorted(set(cited) & set(every))
    assert sorted(result["markedC"]) == sorted(set(cited) & set(every))
    assert sorted(result["dimmedB"]) == sorted(set(every) - set(cited))
    assert sorted(result["dimmedC"]) == sorted(set(every) - set(cited))


@needs_node
def test_the_imported_runs_icicle_places_nested_spans_below_their_turn(tmp_path: Path) -> None:
    root = import_workspace(tmp_path)
    assert invoke("import", "--root", str(root), "--rows", str(PROXY)).exit_code == 0
    (run_dir,) = sorted((root / ".agentdiag" / "targets" / "default" / "runs").iterdir())
    view = run_view(run_dir)

    rows = in_node(
        view,
        "const tl=spanRows(VIEW.trials[0]);const b=flameB(tl);"
        'globalThis.__result=[...b.matchAll(/<rect [^>]*data-id="([^"]*)"[^>]* y="([0-9.]+)"/g)]'
        ".map(m=>[m[1],Number(m[2])]);",
        tmp_path,
    )

    depth = {span.span_id: span.depth for span in flattened(view.trials[0])}
    ys = dict(rows)
    assert set(ys) == set(depth)
    deeper = [span_id for span_id in depth if depth[span_id] > 0]
    assert deeper
    for span_id in deeper:
        assert ys[span_id] > min(ys[other] for other in depth if depth[other] == 0)


# --- the whole page, booted, and what Trace text cannot do to it (ticket 15 fix round) ---


def routed(html: str, hashes: list[str], tmp_path: Path) -> list[dict[str, Any]]:
    """Boot a Report file under node (`tests/render_page.js`) and route to each hash."""
    assert NODE is not None
    page = tmp_path / "page.html"
    page.write_text(html, encoding="utf-8")
    completed = subprocess.run(
        [NODE, str(PAGE), str(page), json.dumps(hashes)], capture_output=True, text=True, check=True
    )
    return [json.loads(line) for line in completed.stdout.splitlines()]


def every_route(view: RunView) -> list[str]:
    run_id = view.header.run_id
    return [
        f"#report/{run_id}/{story.scenario_id}/{story.trial}?variant={variant}"
        for variant in VARIANTS
        for story in view.trials
    ]


def made_runs(tmp_path: Path) -> list[Path]:
    """A toy replay Run and an imported Run, each with the Report it wrote."""
    root = toy_root(tmp_path / "toy-run")
    assert replay(root).exit_code in {0, 1}
    (tmp_path / "import-run").mkdir()
    imported = import_workspace(tmp_path / "import-run")
    assert invoke("import", "--root", str(imported), "--rows", str(PROXY)).exit_code == 0
    (import_dir,) = sorted((imported / ".agentdiag" / "targets" / "default" / "runs").iterdir())
    return [*toy_runs(root), import_dir]


@needs_node
def test_every_run_renders_every_trial_in_every_variant_through_the_booted_page(
    tmp_path: Path,
) -> None:
    for run_dir in [*sorted(RUNS.iterdir()), *made_runs(tmp_path)]:
        view = run_view(run_dir)
        report = run_dir / REPORT_FILE
        html = report.read_text(encoding="utf-8") if report.exists() else render_report(view)
        results = routed(html, every_route(view), tmp_path)
        assert len(results) == len(every_route(view))
        for result in results:
            assert result["error"] is None, (run_dir.name, result["hash"], result["error"])
            assert 'data-section="scorecard"' in result["main"], result["hash"]


@needs_node
def test_each_trial_of_a_multi_trial_run_is_reachable_from_the_report(tmp_path: Path) -> None:
    view = run_view(RUNS / "20260923T100600Z-tri3")
    assert view.header.trials > 1
    hashes = [f"#report/{view.header.run_id}/{s.scenario_id}/{s.trial}" for s in view.trials]

    results = routed(render_report(view), hashes, tmp_path)

    for story, result in zip(view.trials, results, strict=True):
        assert (
            f'Trial · <span class="mono">{story.scenario_id} / {story.trial}</span>'
            in (result["main"])
        )
        assert 'data-section="trials"' in result["main"]
        assert f"/{story.scenario_id}/{story.trial}?variant=" in result["main"]


HOSTILE = ['llm_call-1"x', "&quot;);alert(1);//", "</script><b>"]


@needs_node
def test_evidence_ids_from_the_judge_render_as_data_and_cite_without_a_throw(
    tmp_path: Path,
) -> None:
    view = run_view(JUDGED)
    story = next(story for story in view.trials if story.scores)
    assert story.scores is not None
    story.scores[0].evidence = HOSTILE
    html = render_report(view)

    (result,) = routed(html, [f"#report/{view.header.run_id}/{story.scenario_id}/1"], tmp_path)

    assert result["error"] is None
    assert 'onclick="cite(' not in result["main"]
    assert "</script><b>" not in result["main"]
    assert "&lt;/script&gt;&lt;b&gt;" in result["main"]
    assert 'data-cites="[&quot;llm_call-1\\&quot;x&quot;,' in result["main"]
    cited = in_node(
        view,
        "const el={dataset:{id:'x'},classList:{toggle(){},add(){},remove(){}},scrollIntoView(){}};"
        "document.querySelectorAll=()=>[el];"
        f"cite({json.dumps(HOSTILE)},null);globalThis.__result=[...citedSpans];",
        tmp_path,
    )
    assert cited == HOSTILE


def test_trace_text_cannot_form_markup_in_the_embedded_json() -> None:
    view = run_view(JUDGED)
    view.trials[0].messages[0].text = "a <!--<script> b </script> c & d > e"
    html = render_report(view)

    start = html.index(EMBED_START) + len(EMBED_START)
    payload = html[start : html.index(EMBED_END, start)]
    assert not {"<", ">", "&"} & set(payload)
    assert html.count("<script>") == 2
    assert embedded(html)["trials"][0]["messages"][0]["text"] == (
        "a <!--<script> b </script> c & d > e"
    )


def test_a_report_that_cannot_be_rendered_leaves_the_run_indexed_and_its_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = toy_root(tmp_path / "plain")
    expected = replay(plain).exit_code

    def broken(view: RunView) -> str:
        raise TypeError("a renderer bug")

    monkeypatch.setattr("agentdiag.report.html.render_report", broken)
    root = toy_root(tmp_path / "broken")
    result = replay(root)

    assert result.exit_code == expected
    (run_dir,) = toy_runs(root)
    assert not (run_dir / REPORT_FILE).exists()
    assert "TypeError: a renderer bug" in result.output
    with sqlite3.connect(root / ".agentdiag" / "index.sqlite") as connection:
        rows = connection.execute("SELECT run_id FROM runs").fetchall()
    assert rows == [(run_dir.name,)]


def test_a_span_view_carries_every_attribute_of_its_span() -> None:
    story = run_view(JUDGED).trials[0]
    events = read_trace(trace_path(JUDGED, story.scenario_id, story.trial))
    by_id = {span.span_id: span for span in project_spans(events)}

    for view in flattened(story):
        attributes = by_id[view.span_id].attributes
        assert set(view.attributes) == set(attributes)
        for key, value in attributes.items():
            scalar = value is None or isinstance(value, str | int | float | bool)
            assert view.attributes[key] == (value if scalar else str(value)[:200])


# --- the Change record a Run verified (ticket 28) ---


def test_a_run_outside_a_workspace_carries_no_change_record() -> None:
    assert all(run_view(run_dir).change_record is None for run_dir in sorted(RUNS.iterdir()))


def test_the_view_of_a_verifying_run_carries_the_story_and_a_report_rendered_from_it_draws_it(
    tmp_path: Path,
) -> None:
    """The verified close over committed Run fixtures (`test_change_cli.py`'s gate): the
    post-change Run's view, made after the close, carries the record's story, and a Report
    rendered from that view (as `serve` renders one) embeds it and draws it after the
    Scorecard and before the Scenarios, its cites `data-cites`. The `report.html` the Run
    wrote when it ended predates the close and is never rewritten (ADR-0005 §2)."""
    from tests.test_change_cli import BASE, PRMT, change, pushed_record, the_target, toy

    root = toy(tmp_path, BASE, PRMT)
    record_id = pushed_record(root)
    closed = change(
        root, "close", record_id, "--verified", "--run", BASE, "--expect", "manifest.prompts"
    )
    assert closed.exit_code == 0, closed.output
    target = the_target(root)

    view = run_view(target.runs / BASE)
    html = render_report(view)

    assert view.change_record is not None and view.change_record.id == record_id
    assert run_view(target.runs / PRMT).change_record is None
    story = embedded(html)["change_record"]
    assert story["id"] == record_id and story["status"] == "verified"
    assert story["observed"]["items"][0]["link"] == f"#compare/{PRMT}/{BASE}"
    if NODE is None:
        pytest.skip("node is not on PATH")
    (result,) = routed(html, [f"#report/{BASE}"], tmp_path)
    main = result["main"]
    assert result["error"] is None, result["error"]
    assert main.count("data-lane=") == 5
    order = [
        main.index(marker)
        for marker in ('data-section="scorecard"', 'data-section="change-record"', "<h3>Scenarios")
    ]
    assert order == sorted(order)
    assert f'data-cites="[&quot;llm_call-1&quot;]" data-run="{PRMT}"' in main
    assert f'href="#report/{BASE}/where-is-shipped-order/1"' in main  # its own Trials open
    assert 'href="#compare/' not in main  # a Report file opens no other screen
