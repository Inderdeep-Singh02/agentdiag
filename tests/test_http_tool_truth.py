"""Seam 1: tool truth for an HTTP Trial from proxy rows read through the Connector (ticket 17,
phase-8 decision 8, ADR-0013 §5).

The fake HTTP Target reports no tool on its stream, as a black-box endpoint may not; the
fake Connector serves the help desk's four committed proxy rows (`tests/fixtures/evidence/
helpdesk-proxy-rows.json`: two Turns, each a question, a `search_articles` call and its
answer) for the conversation. So what these tests see is exactly what tool truth adds:
`reconstructed` Spans under each Turn's `response` Span, attributed by the message the Turn
sent and never by position, the other Turn's rows ignored, a failing read recorded and the
Turn kept, the preflight warning when there is nothing to reconstruct from, and every tool
Eval `unverifiable / fidelity_too_low` without tool truth and decided with it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.connector.base import READ_EVIDENCE, ConnectorError, EvidenceQuery, EvidenceRows
from agentdiag.connector.plugins import registered
from agentdiag.importer.proxy_rows import ProxyRowImporter
from agentdiag.run.preflight import TOOL_EVALS_WITHOUT_TOOL_TRUTH
from agentdiag.trace import Event, TraceWriter, event_fields, project_spans, read_trace
from agentdiag.trace.attributes import (
    EVIDENCE_CREATED_AT,
    EVIDENCE_LATENCY_MS,
    EVIDENCE_REQUEST_ID,
    EVIDENCE_STORE,
    NOT_OBSERVED,
    SPAN_ORIGIN,
    STREAM_TOOLS,
    TOOL_TRUTH,
)
from tests.fakes.fake_connector import FAKE_KIND, FakeConnector
from tests.fakes.http_target import (
    SLUG,
    TOKEN_VARIABLE,
    Reply,
    Tool,
    http_workspace,
    serving_target,
)

REPO = Path(__file__).resolve().parents[1]
ROWS = REPO / "tests" / "fixtures" / "evidence" / "helpdesk-proxy-rows.json"
FIRST = "How do I change the billing address on my account?"
SECOND = "Thanks. Can I download my past invoices too?"
CONVERSATION = "hd-0001"

runner = CliRunner()

SUITE: dict[str, Any] = {
    "schema_version": 1,
    "target": SLUG,
    "scenarios": [
        {
            "id": "billing",
            "title": "Billing address, then invoices",
            "turns": [FIRST, SECOND],
            "evals": [
                {"expect_tools": ["search_articles"]},
                {
                    "expect_tool_args": {
                        "tool": "search_articles",
                        "turn": 1,
                        "args": {"query": {"contains": "billing"}},
                    }
                },
                {"tool_count_max": {"max": 2}},
                {"must_not_say": ["as an AI"]},
            ],
        }
    ],
}


def rows() -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = json.loads(ROWS.read_text(encoding="utf-8"))
    return loaded


def fake_connector(evidence: bool = True) -> FakeConnector:
    return FakeConnector(
        {"dev": {"prompts": {}, "tools": {}}},
        {("dev", "proxy"): rows()} if evidence else {},
    )


def workspace(root: Path, base_url: str, *, tool_truth: bool = True) -> Path:
    adapter: dict[str, Any] = (
        {"tool_truth": {"evidence": "proxy", "wait_s": 0}} if tool_truth else {}
    )
    return http_workspace(
        root,
        base_url,
        adapter=adapter,
        drop=["identity"],
        manifest={"connector": {"kind": FAKE_KIND, "environments": {"dev": {}}}},
        suite=SUITE,
    )


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(TOKEN_VARIABLE, "tok-not-a-real-secret")
    yield


def trial(root: Path) -> tuple[list[Event], dict[str, dict[str, Any]], dict[str, Any]]:
    (run_dir,) = sorted((root / ".agentdiag" / "targets" / SLUG / "runs").iterdir())
    trial_dir = run_dir / "trials" / "billing" / "1"
    scores = json.loads((trial_dir / "scores.json").read_text(encoding="utf-8"))["scores"]
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    return read_trace(trial_dir / "trace.jsonl"), {s["eval"]: s for s in scores}, record


# --- reconstruct_turn itself ---


def test_reconstruct_turn_writes_the_turns_rows_under_the_parent_on_the_writers_clock(
    tmp_path: Path,
) -> None:
    ticks = iter(range(10_000, 10_000_000, 10))
    writer = TraceWriter(tmp_path / "trace.jsonl", clock=lambda: next(ticks))
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    parent = writer.span("response", actor="target", name="response sse-json", fidelity="observed")
    evidence = EvidenceRows(
        kind="proxy", query=EvidenceQuery(conversation_id=CONVERSATION), rows=rows(), read_at="x"
    )

    got = ProxyRowImporter(tool_kinds={"search_articles": "retrieval"}).reconstruct_turn(
        evidence, writer=writer, parent=parent, user_message=FIRST
    )
    parent.end()
    writer.end("completed")
    writer.close()

    assert (got.rows_used, got.rows_ignored, got.spans) == (2, 2, 3)
    events = read_trace(writer.path)
    spans = [span for span in project_spans(events) if span.span_id != parent.span_id]
    assert [span.kind for span in spans] == ["llm_call", "retrieval", "llm_call"]
    calls = [span for span in spans if span.kind == "llm_call"]
    assert {span.parent_span_id for span in calls} == {parent.span_id}
    assert spans[1].parent_span_id == calls[0].span_id
    for span in spans:
        assert span.fidelity == "reconstructed"
        assert span.attributes[EVIDENCE_STORE] == "proxy"
        assert span.attributes[EVIDENCE_REQUEST_ID].startswith("req-hd-0001-")
        assert EVIDENCE_CREATED_AT in span.attributes and EVIDENCE_LATENCY_MS in span.attributes
        assert {"start_time", "end_time"} <= set(span.attributes[NOT_OBSERVED])
        assert SPAN_ORIGIN not in span.attributes
    (call,) = [event for event in events if event.type == "tool/call"]
    assert event_fields(call)["arguments"] == {"query": "change billing address"}
    (result,) = [event for event in events if event.type == "tool/result"]
    assert "KB-104" in json.dumps(event_fields(result)["result"])
    stamps = [event.ts for event in events]
    assert stamps == sorted(stamps) and stamps[0] >= 10_000
    assert not [event for event in events if event.type == "message"]


def test_reconstruct_turn_writes_nothing_for_a_message_no_row_carries(tmp_path: Path) -> None:
    writer = TraceWriter(tmp_path / "trace.jsonl")
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    parent = writer.span("response", actor="target", name="response json", fidelity="observed")
    evidence = EvidenceRows(kind="proxy", query=EvidenceQuery(), rows=rows(), read_at="x")

    got = ProxyRowImporter().reconstruct_turn(
        evidence, writer=writer, parent=parent, user_message="Something nobody asked"
    )
    parent.end()
    writer.close()

    assert (got.rows_used, got.rows_ignored, got.spans) == (0, 4, 0)
    assert [span.kind for span in project_spans(read_trace(writer.path))] == ["response"]


# --- inside a Run ---


def test_each_turns_proxy_rows_become_reconstructed_spans_under_its_response(
    tmp_path: Path, token: None
) -> None:
    connector = fake_connector()
    root, result = _run(tmp_path, connector)

    assert result.exit_code == 0, result.output
    events, _, record = trial(root)
    assert record["adapter"]["fidelity"] == "reconstructed"
    spans = project_spans(events)
    responses = [span for span in spans if span.kind == "response"]
    assert len(responses) == 2
    for number, response in enumerate(responses, start=1):
        under = [span for span in spans if span.parent_span_id == response.span_id]
        assert [span.kind for span in under] == ["llm_call", "llm_call"]
        assert {span.turn for span in under} == {number}
        truth = response.attributes[TOOL_TRUTH]
        assert (truth["rows_used"], truth["rows_ignored"], truth["spans"]) == (2, 2, 3)
    ids = [span.attributes[EVIDENCE_REQUEST_ID] for span in spans if span.kind == "llm_call"]
    assert ids == ["req-hd-0001-1", "req-hd-0001-2", "req-hd-0001-3", "req-hd-0001-4"]
    queries = [
        argument[1] for operation, _, argument in connector.calls if operation == READ_EVIDENCE
    ]
    assert [query.conversation_id for query in queries] == [CONVERSATION, CONVERSATION]
    assert all(query.since for query in queries)


def test_every_tool_eval_is_decided_with_tool_truth(tmp_path: Path, token: None) -> None:
    root, result = _run(tmp_path, fake_connector())

    assert result.exit_code == 0, result.output
    _, scores, _ = trial(root)
    for name in ("expect_tools", "expect_tool_args", "tool_count_max"):
        assert scores[name]["verdict"] == "pass", scores[name]
        assert scores[name]["fidelity"] == "reconstructed"
    assert TOOL_EVALS_WITHOUT_TOOL_TRUTH not in result.output


def test_without_tool_truth_every_tool_eval_is_unverifiable_and_preflight_warns(
    tmp_path: Path, token: None
) -> None:
    connector = fake_connector()
    root, result = _run(tmp_path, connector, tool_truth=False)

    assert result.output.count(TOOL_EVALS_WITHOUT_TOOL_TRUTH) == 1
    events, scores, record = trial(root)
    assert record["adapter"]["fidelity"] == "observed"
    for name in ("expect_tools", "expect_tool_args", "tool_count_max"):
        assert (scores[name]["verdict"], scores[name]["reason"]) == (
            "unverifiable",
            "fidelity_too_low",
        ), scores[name]
    assert scores["must_not_say"]["verdict"] == "pass"
    assert not [operation for operation, _, _ in connector.calls if operation == READ_EVIDENCE]
    assert all(TOOL_TRUTH not in span.attributes for span in project_spans(events))


def test_a_connector_with_no_proxy_store_is_warned_of_too(tmp_path: Path, token: None) -> None:
    root, result = _run(tmp_path, fake_connector(evidence=False))

    assert TOOL_EVALS_WITHOUT_TOOL_TRUTH in result.output
    _, scores, record = trial(root)
    assert record["adapter"]["fidelity"] == "observed"
    assert scores["expect_tools"]["reason"] == "fidelity_too_low"


def test_a_failing_read_is_recorded_on_the_response_and_the_turn_is_kept(
    tmp_path: Path, token: None
) -> None:
    connector = fake_connector()
    connector.raise_on(READ_EVIDENCE, ConnectorError("the proxy store is down"))
    root, result = _run(tmp_path, connector)

    events, scores, _ = trial(root)
    assert event_fields(events[-1])["termination"] == "completed", result.output
    responses = [span for span in project_spans(events) if span.kind == "response"]
    assert [span.status for span in responses] == ["ok", "ok"]
    assert [span.attributes[TOOL_TRUTH] for span in responses] == [
        {"failed": "the proxy store is down"}
    ] * 2
    replies = [
        event_fields(event)["content"]
        for event in events
        if event.type == "message" and event.actor == "target"
    ]
    assert replies == ["Open Settings, then Billing.", "Yes: go to Billing, then Invoices."]
    assert scores["must_not_say"]["verdict"] == "pass"


def test_attribution_is_by_content_whatever_order_the_turns_come_in(tmp_path: Path) -> None:
    """ADR-0001 §3: the second Turn's message picks the second Turn's rows even when it is
    the first to be reconstructed; a "next unused group" rule would take rows 1-2."""
    writer = TraceWriter(tmp_path / "trace.jsonl")
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    parent = writer.span("response", actor="target", name="response json", fidelity="observed")
    evidence = EvidenceRows(kind="proxy", query=EvidenceQuery(), rows=rows(), read_at="x")

    got = ProxyRowImporter().reconstruct_turn(
        evidence, writer=writer, parent=parent, user_message=SECOND
    )
    parent.end()
    writer.close()

    assert (got.rows_used, got.rows_ignored) == (2, 2)
    ids = [
        span.attributes[EVIDENCE_REQUEST_ID]
        for span in project_spans(read_trace(writer.path))
        if span.kind == "llm_call"
    ]
    assert ids == ["req-hd-0001-3", "req-hd-0001-4"]


def test_a_stream_that_reports_the_tools_is_superseded_by_the_proxy_rows(
    tmp_path: Path, token: None
) -> None:
    """Decision 8 amended: with rows for the Turn, the stream's tools are named on the
    `response` Span and not written, so each tool is counted once, at `reconstructed`."""
    script = tuple(
        Reply(text=reply.text, conversation=reply.conversation, tools=(Tool("search_articles"),))
        for reply in SCRIPT
    )
    root, result = _run(tmp_path, fake_connector(), script=script)

    assert result.exit_code == 0, result.output
    events, scores, _ = trial(root)
    spans = project_spans(events)
    assert not [
        span
        for span in spans
        if span.fidelity == "observed" and span.kind not in ("response", "turn")
    ]
    for response in [span for span in spans if span.kind == "response"]:
        assert response.attributes[STREAM_TOOLS] == ["search_articles"]
    for name in ("expect_tools", "expect_tool_args", "tool_count_max"):
        assert (scores[name]["verdict"], scores[name]["fidelity"]) == ("pass", "reconstructed")


def test_without_rows_the_streams_tools_are_written_as_observed(
    tmp_path: Path, token: None
) -> None:
    script = tuple(
        Reply(text=reply.text, conversation=reply.conversation, tools=(Tool("search_articles"),))
        for reply in SCRIPT
    )
    root, _ = _run(tmp_path, fake_connector(evidence=False), script=script)

    events, _, _ = trial(root)
    tools = [span for span in project_spans(events) if span.kind == "tool_call"]
    assert [span.fidelity for span in tools] == ["observed", "observed"]
    assert all(STREAM_TOOLS not in span.attributes for span in project_spans(events))


def test_a_turn_that_fails_after_its_tools_keeps_its_proxy_evidence(
    tmp_path: Path, token: None
) -> None:
    script = (
        Reply(text="Let me look", conversation=CONVERSATION, error=("overloaded", "try later")),
    )
    root, result = _run(tmp_path, fake_connector(), script=script, served=1)

    events, _, _ = trial(root)
    assert event_fields(events[-1])["termination"] == "target_error", result.output
    (response,) = [span for span in project_spans(events) if span.kind == "response"]
    assert response.status == "error"
    assert response.attributes[TOOL_TRUTH]["rows_used"] == 2
    under = [span for span in project_spans(events) if span.parent_span_id == response.span_id]
    assert [span.kind for span in under] == ["llm_call", "llm_call"]


def test_tool_truth_waits_on_the_adapters_clock(tmp_path: Path) -> None:
    """`wait_s` and `poll_s` are read on the injected clock: a scripted clock and sleep poll
    three times over two seconds and never really wait."""
    from agentdiag.adapter.http import HttpAdapter

    now = [0]

    def sleep(seconds: float) -> None:
        now[0] += round(seconds * 1000)

    connector = fake_connector()
    with serving_target("json") as fake:
        adapter = HttpAdapter(
            {
                "kind": "http",
                "tool_truth": {"evidence": "proxy", "wait_s": 2, "poll_s": 1},
                "environments": {
                    "default": "dev",
                    "dev": {"base_url": fake.base_url, "dialect": "json"},
                },
            },
            environment="dev",
            connector=connector,
            clock=lambda: now[0],
            sleep=sleep,
            environ={},
        )
        trace = TraceWriter(tmp_path / "trace.jsonl")
        trace.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
        session = adapter.open(trace)
        with trace.span("turn", actor="agentdiag", name="turn 1", fidelity="observed"):
            session.deliver("A message no proxy row carries")
        session.close()
        trace.close()

    reads = [call for call in connector.calls if call[0] == READ_EVIDENCE]
    assert len(reads) == 3
    assert now[0] == 2000


# --- helpers ---


SCRIPT = (
    Reply(text="Open Settings, then Billing.", conversation=CONVERSATION),
    Reply(text="Yes: go to Billing, then Invoices."),
)


def _run(
    tmp_path: Path,
    connector: FakeConnector,
    *,
    tool_truth: bool = True,
    script: tuple[Reply, ...] = SCRIPT,
    served: int = 2,
) -> tuple[Path, Any]:
    """`run` over a fake served now, the Workspace built at its port, with `connector`
    serving the Manifest's `connector: {kind: fake}`; the root and the result."""
    with (
        serving_target("sse-json", script=script) as fake,
        registered(FAKE_KIND, connector.as_kind()),
    ):
        root = workspace(tmp_path, fake.base_url, tool_truth=tool_truth)
        result = runner.invoke(app, ["run", "--root", str(root), "--target", SLUG])
    assert fake.served == served, result.output
    return root, result
