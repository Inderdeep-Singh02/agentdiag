"""Seam 1: `agentdiag import` writes a Run of `source: imported`, and `list`, `show`,
`rescore --eval` and `compare` treat it as a Run (ticket 26, phase-6 decision 36).

Every test runs the CLI over a temporary Workspace made by `init`, whose Manifest names the
help desk's tools; the evidence is the committed fixtures under `tests/fixtures/evidence/`,
read from a file (`--rows`) or served by the fake Connector through the plugin registry's
test seam (`--chat`, `--conversation`, `--voice`). Nothing here converses with a Target or
calls a model: an import reads evidence, and the rescores judge with mechanical Evals.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.connector.plugins import registered
from agentdiag.importer.rows import read_rows_file
from agentdiag.run.locate import JUDGEMENT_FILE, RUN_RECORD_FILE, SCORECARD_FILE, SCORES_FILE
from agentdiag.trace import read_trace
from agentdiag.trace.attributes import SPAN_ORIGIN
from agentdiag.trace.spans import project_spans
from tests.fakes.fake_connector import FAKE_KIND, FakeConnector

EVIDENCE = Path(__file__).resolve().parent / "fixtures" / "evidence"
PROXY = EVIDENCE / "helpdesk-proxy-rows.json"
EDGES = EVIDENCE / "helpdesk-proxy-edges.jsonl"
CONVERSATION = EVIDENCE / "helpdesk-conversation.json"
VOICE = EVIDENCE / "helpdesk-voice.json"
SCENARIO = "imported-hd-0001"

runner = CliRunner()


def workspace(tmp_path: Path, **blocks: Any) -> Path:
    """`init`, then the Manifest given the help desk's tools and any other blocks."""
    root = tmp_path / "ws"
    result = runner.invoke(app, ["init", "--root", str(root)])
    assert result.exit_code == 0, result.output
    path = manifest_path(root)
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["tools"] = {
        **manifest.get("tools", {}),
        "search_articles": {"kind": "retrieval"},
        "open_ticket": {"kind": "action"},
        "escalate": {"kind": "action"},
    }
    manifest.update(blocks)
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return root


def manifest_path(root: Path) -> Path:
    return root / ".agentdiag" / "targets" / "default" / "manifest.yaml"


def runs(root: Path) -> list[Path]:
    return sorted((root / ".agentdiag" / "targets" / "default" / "runs").iterdir())


def record(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / RUN_RECORD_FILE).read_text(encoding="utf-8"))


def invoke(*arguments: str) -> Any:
    return runner.invoke(app, list(arguments))


def imported_run(root: Path, *source: str) -> Path:
    before = set(runs(root)) if (root / ".agentdiag/targets/default/runs").exists() else set()
    result = invoke("import", "--root", str(root), *source)
    assert result.exit_code == 0, result.output
    (made,) = set(runs(root)) - before
    return made


@pytest.fixture
def fake() -> Iterator[FakeConnector]:
    connector = FakeConnector(
        {"local": {"prompts": {}, "tools": {}, "model": "claude-sonnet-5"}},
        {
            ("local", "proxy"): read_rows_file(PROXY),
            ("local", "conversation"): [json.loads(CONVERSATION.read_text(encoding="utf-8"))],
            ("local", "voice"): [json.loads(VOICE.read_text(encoding="utf-8"))],
        },
    )
    with registered(FAKE_KIND, connector.as_kind()):
        yield connector


FAKE_BLOCK = {"kind": FAKE_KIND, "environments": {"local": {}}}


def test_import_rows_writes_one_trial_of_a_run_of_source_imported(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    run_dir = imported_run(root, "--rows", str(PROXY))
    written = record(run_dir)
    assert written["source"] == "imported"
    assert written["target"] == "default"
    importer = written["importer"]
    assert (importer["kind"], importer["fidelity"]) == ("proxy", "reconstructed")
    # Outside the Workspace root, the file is named by its absolute path.
    assert importer["query"]["extra"] == {"rows": PROXY.resolve().as_posix()}
    assert importer["evidence_read_at"]
    assert {entry["field"] for entry in importer["lacked"]} == {
        "end_time_exact",
        "start_time",
        "time_to_first_token",
    }
    assert written["selection"]["expression"] == "import proxy hd-0001"
    (scenario,) = written["scenarios"]
    assert scenario["id"] == SCENARIO
    assert scenario["title"] == "Imported proxy hd-0001"
    assert scenario["provenance"] == "evidence:proxy:hd-0001"
    assert scenario["evals"] == []
    assert written["simulated_user"] is None
    assert written["judge"] is None
    adapter = written["adapter"]
    assert (adapter["kind"], adapter["fidelity"], adapter["side_effects"]) == (
        "import",
        "reconstructed",
        "none",
    )
    assert adapter["observes"] == []
    assert written["manifest"]["tools"]["search_articles"]["kind"] == "retrieval"
    assert written["sync"]["status"] == "not_checked"

    trial = run_dir / "trials" / SCENARIO / "1"
    events = read_trace(trial / "trace.jsonl")
    start = events[0].model_extra or {}
    assert start["run"] == run_dir.name
    assert start["trace_id"] == f"{run_dir.name}/{SCENARIO}/1"
    assert events[1].type == "import/source"
    spans = project_spans(events)
    assert all(span.attributes[SPAN_ORIGIN] == "imported" for span in spans)
    assert [span.kind for span in spans if span.attributes.get("gen_ai.tool.name")] == [
        "retrieval",
        "retrieval",
    ]
    assert not (trial / JUDGEMENT_FILE).exists()
    assert json.loads((trial / SCORES_FILE).read_text(encoding="utf-8"))["scores"] == []
    scorecard = json.loads((run_dir / SCORECARD_FILE).read_text(encoding="utf-8"))
    assert set(scorecard["counts"].values()) == {0}
    assert scorecard["sync"]["status"] == "not_checked"


def test_import_prints_what_it_made_and_what_the_evidence_lacked(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    result = invoke("import", "--root", str(root), "--rows", str(EDGES))
    assert result.exit_code == 0, result.output
    (run_dir,) = runs(root)
    lines = result.stdout.splitlines()
    assert lines[0] == f"Imported proxy hd-0002 as Run {run_dir.name} (Target default)"
    assert "Spans at reconstructed, every one marked imported" in lines[1]
    assert (
        "Not observed: end_time_exact (6 Spans), time_to_first_token (4 Spans), start_time "
        "(2 Spans), response_body (1 Span), result (1 Span)"
    ) in result.stdout
    assert "Dropped 1 duplicate row (same request id)" in result.stdout
    assert f"agentdiag rescore {run_dir.name} --eval <name>" in result.stdout


def test_list_shows_the_imported_run_in_the_source_column(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    run_dir = imported_run(root, "--rows", str(PROXY))
    result = invoke("list", "--root", str(root))
    assert result.exit_code == 0, result.output
    header, line = result.stdout.splitlines()
    assert header.split()[:3] == ["run", "target", "source"]
    assert line.split()[:3] == [run_dir.name, "default", "imported"]
    rows = json.loads(invoke("list", "--root", str(root), "--json").stdout)
    assert rows[0]["source"] == "imported"


def test_show_and_export_read_the_imported_trial(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    run_dir = imported_run(root, "--rows", str(PROXY))
    shown = invoke("show", "--root", str(root), run_dir.name, SCENARIO)
    assert shown.exit_code == 0, shown.output
    assert "Adapter import, reconstructed, side effects none" in shown.stdout
    assert "How do I change the billing address on my account?" in shown.stdout
    assert "search_articles" in shown.stdout
    exported = invoke(
        "export",
        "--root",
        str(root),
        run_dir.name,
        SCENARIO,
        "--format",
        "chrome",
        "--out",
        str(tmp_path / "t.json"),
    )
    assert exported.exit_code == 0, exported.output


def test_chat_reads_the_proxy_store_through_the_connector(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = workspace(tmp_path, connector=FAKE_BLOCK)
    run_dir = imported_run(root, "--chat", "hd-0001")
    reads = [call for call in fake.calls if call[0] == "read_evidence"]
    assert reads
    kind, query = reads[0][2]
    assert (reads[0][1], kind, query.conversation_id) == ("local", "proxy", "hd-0001")
    written = record(run_dir)
    assert written["importer"]["query"]["conversation_id"] == "hd-0001"
    assert written["selection"]["expression"] == "import proxy hd-0001"
    assert written["sync"]["environment"] == "local"
    assert not [call for call in fake.calls if call[0] == "write_deployed_set"]


def test_conversation_imports_the_operator_takeover_and_show_names_the_operator(
    tmp_path: Path, fake: FakeConnector
) -> None:
    root = workspace(tmp_path, connector=FAKE_BLOCK)
    run_dir = imported_run(root, "--conversation", "c-0001")
    written = record(run_dir)
    assert written["importer"]["fidelity"] == "observed"
    assert written["scenarios"][0]["id"] == "imported-c-0001"
    events = read_trace(run_dir / "trials" / "imported-c-0001" / "1" / "trace.jsonl")
    assert [e.actor for e in events if e.type == "message" and e.actor == "operator"] == [
        "operator"
    ]
    shown = invoke("show", "--root", str(root), run_dir.name, "imported-c-0001")
    assert shown.exit_code == 0, shown.output
    assert "operator" in shown.stdout
    assert "this is Sam from the help desk" in shown.stdout


def test_voice_imports_the_transcript(tmp_path: Path, fake: FakeConnector) -> None:
    root = workspace(tmp_path, connector=FAKE_BLOCK)
    run_dir = imported_run(root, "--voice", "v-0001")
    written = record(run_dir)
    assert written["selection"]["expression"] == "import voice v-0001"
    assert {entry["field"] for entry in written["importer"]["lacked"]} == {
        "llm_calls",
        "timestamps",
    }


@pytest.mark.parametrize(
    "arguments",
    [[], ["--rows", str(PROXY), "--chat", "hd-0001"]],
    ids=["no source", "two sources"],
)
def test_import_names_exactly_one_source_or_exits_3(tmp_path: Path, arguments: list[str]) -> None:
    root = workspace(tmp_path)
    result = invoke("import", "--root", str(root), *arguments)
    assert result.exit_code == 3
    assert "name exactly one source" in result.output
    assert not (root / ".agentdiag" / "targets" / "default" / "runs").exists()


def test_chat_with_a_connector_that_reads_no_proxy_store_exits_3(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    result = invoke("import", "--root", str(root), "--chat", "hd-0001")
    assert result.exit_code == 3
    assert "could not read the proxy store" in result.output


def test_rows_not_in_the_generic_shape_exit_3_naming_the_row(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps([{"request_id": "x"}]), encoding="utf-8")
    result = invoke("import", "--root", str(root), "--rows", str(broken))
    assert result.exit_code == 3
    assert "proxy row 0 is not a ProxyRow" in result.output


# --- judging it: rescore --eval ---


def test_rescore_eval_judges_an_imported_run_no_suite_declares(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    source = imported_run(root, "--rows", str(PROXY))
    result = invoke(
        "rescore",
        "--root",
        str(root),
        source.name,
        "--eval",
        "expect_tools=[search_articles]",
        "--eval",
        "response_latency={max_ms: 30000}",
        "--eval",
        "first_token_latency={max_ms: 1000}",
    )
    # 2: nothing failed, and the first token could not be decided (D32).
    assert result.exit_code == 2, result.output
    (rescored,) = set(runs(root)) - {source}
    written = record(rescored)
    assert written["source"] == "imported"
    assert written["traces_from"] == source.name
    assert written["importer"] == record(source)["importer"]
    (scenario,) = written["scenarios"]
    assert scenario["id"] == SCENARIO
    assert [e["eval"] for e in scenario["evals"]] == [
        "expect_tools",
        "response_latency",
        "first_token_latency",
    ]
    scores = json.loads(
        (rescored / "trials" / SCENARIO / "1" / SCORES_FILE).read_text(encoding="utf-8")
    )["scores"]
    verdicts = {score["eval"]: (score["verdict"], score["reason"]) for score in scores}
    assert verdicts == {
        "expect_tools": ("pass", None),
        "response_latency": ("pass", None),
        "first_token_latency": ("unverifiable", "evidence_missing"),
    }
    assert not (rescored / "trials" / SCENARIO / "1" / "trace.jsonl").exists()
    shown = invoke("show", "--root", str(root), rescored.name, SCENARIO)
    assert shown.exit_code == 0, shown.output
    assert "first_token_latency" in shown.stdout


def test_rescore_of_an_imported_run_without_eval_says_to_name_one(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    source = imported_run(root, "--rows", str(PROXY))
    result = invoke("rescore", "--root", str(root), source.name)
    assert result.exit_code == 3
    assert f"no Suite declares {SCENARIO}" in result.output
    assert "--eval" in result.output


def test_rows_under_the_workspace_root_are_named_relative_to_it(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    copy = root / "evidence" / "hd-0001.json"
    copy.parent.mkdir()
    shutil.copy(PROXY, copy)
    run_dir = imported_run(root, "--rows", str(copy))
    assert record(run_dir)["importer"]["query"]["extra"] == {"rows": "evidence/hd-0001.json"}


def test_rescore_eval_of_a_metric_with_no_value_says_how_to_give_one(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    source = imported_run(root, "--rows", str(PROXY))
    result = invoke("rescore", "--root", str(root), source.name, "--eval", "response_latency")
    assert result.exit_code == 3
    assert "--eval response_latency needs a threshold" in result.output
    assert "response_latency={max_ms: 5000}" in result.output
    assert "eval_parameters.latency" in result.output
    assert set(runs(root)) == {source}


def test_rescore_eval_that_is_not_a_declaration_exits_3(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    source = imported_run(root, "--rows", str(PROXY))
    result = invoke("rescore", "--root", str(root), source.name, "--eval", "expect_tools=[a")
    assert result.exit_code == 3
    assert "--eval" in result.output


# --- compare refuses run against imported unless declared ---


def as_driven(run_dir: Path, into: Path) -> Path:
    """A copy of an imported Run's directory whose `run.json` says `source: run`: the one
    difference the comparison is about."""
    copy = into / f"{run_dir.name[:-4]}copy"
    shutil.copytree(run_dir, copy)
    written = record(copy)
    written["source"] = "run"
    written["run_id"] = copy.name
    written.pop("importer")
    (copy / RUN_RECORD_FILE).write_text(json.dumps(written, indent=2), encoding="utf-8")
    return copy


def test_compare_refuses_a_run_against_an_imported_run_naming_both(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    source = imported_run(root, "--rows", str(PROXY))
    result = invoke(
        "rescore", "--root", str(root), source.name, "--eval", "expect_tools=[search_articles]"
    )
    assert result.exit_code == 0, result.output
    (rescored,) = set(runs(root)) - {source}
    driven = as_driven(rescored, tmp_path)
    refused = invoke("compare", str(driven), str(rescored))
    assert refused.exit_code == 3
    assert "source run" in refused.output and "source imported" in refused.output
    assert "--expect source" in refused.output

    declared = invoke("compare", str(driven), str(rescored), "--expect", "source", "--json")
    assert declared.exit_code == 0, declared.output
    comparison = json.loads(declared.stdout)
    assert comparison["deltas"]
    assert {delta["label"] for delta in comparison["deltas"]} == {"undecided"}
    assert all("source" in delta["because"] for delta in comparison["deltas"])
    text = invoke("compare", str(driven), str(rescored), "--expect", "source")
    assert "source" in text.stdout
