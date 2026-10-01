"""The three Importers, pure: Evidence rows in, a Trace out, and what the evidence lacked
(ticket 26, phase-6 decisions 32 to 35 and 37; ADR-0013 §5-§6).

Every Trace here is imported from a committed fixture under `tests/fixtures/evidence/`
(invented help desk conversations, no customer content) with no Connector, then read back
through the Span projection and scored through `perform_evals`, as a rescore scores it.
Decision 37's rule is asserted per Importer over every registered Eval: each one's Verdict
and reason over the fixture is stated, every field the `import/source` Event names as lacked
makes the Evals whose evidence it is `unverifiable` with the right reason, and no `pass`
cites a Span whose not-observed facts cover what that Eval reads.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from agentdiag.connector.base import EvidenceQuery, EvidenceRows
from agentdiag.eval.judge import Judges
from agentdiag.eval.perform import perform_evals
from agentdiag.eval.registry import REGISTRY
from agentdiag.eval.score import Score
from agentdiag.importer import IMPORTERS, importer_for
from agentdiag.importer.base import Imported, Importer, ImportRowsError
from agentdiag.importer.rows import read_rows_file
from agentdiag.model.client import ModelRequest, ModelResponse
from agentdiag.scenario.models import Scenario
from agentdiag.trace import Event, Span, project_spans, resolve_blobs
from agentdiag.trace.attributes import (
    COST_CHECK,
    NOT_OBSERVED,
    REPORTED_COST_USD,
    SPAN_ORIGIN,
    TURN_ANSWERED_BY,
)
from agentdiag.trace.openinference import COST_TOTAL
from agentdiag.trace.spans import not_observed
from agentdiag.types import FIDELITY_RANK, EvidenceKind, Fidelity

EVIDENCE = Path(__file__).resolve().parent / "fixtures" / "evidence"
PROXY = EVIDENCE / "helpdesk-proxy-rows.json"
EDGES = EVIDENCE / "helpdesk-proxy-edges.jsonl"
CONVERSATION = EVIDENCE / "helpdesk-conversation.json"
VOICE = EVIDENCE / "helpdesk-voice.json"

TOOL_KINDS = {"search_articles": "retrieval", "open_ticket": "action", "escalate": "action"}


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def rows(kind: EvidenceKind, path: Path, conversation_id: str | None = None) -> EvidenceRows:
    loaded = (
        read_rows_file(path) if kind == "proxy" else [json.loads(path.read_text(encoding="utf-8"))]
    )
    return EvidenceRows(
        kind=kind,
        query=EvidenceQuery(conversation_id=conversation_id),
        rows=loaded,
        read_at="2026-09-28T12:00:00Z",
    )


def imported(kind: EvidenceKind, path: Path, clock_origin: str | None = None) -> Imported:
    return importer_for(kind, tool_kinds=TOOL_KINDS).import_rows(
        rows(kind, path), clock_origin=clock_origin
    )


def imported_rows(kind: EvidenceKind, loaded: list[dict[str, Any]]) -> Imported:
    """The Importer over the fixture's rows changed in code in the one way a test is about."""
    evidence = EvidenceRows(
        kind=kind, query=EvidenceQuery(), rows=loaded, read_at="2026-09-28T12:00:00Z"
    )
    return importer_for(kind, tool_kinds=TOOL_KINDS).import_rows(evidence)


def record(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def spans_of(result: Imported) -> dict[str, Span]:
    return {span.span_id: span for span in project_spans(result.events)}


def of_type(events: Sequence[Event], type: str) -> list[Event]:
    return [event for event in events if event.type == type]


def fields(event: Event) -> dict[str, Any]:
    return dict(event.model_extra or {})


class NoModel:
    """A model client that must never be asked: the `fidelity_too_low` path needs none."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.asked.append(request.model)
        raise RuntimeError("no model is available in this test")


def scored(
    declaration: Any,
    result: Imported,
    fidelity: Fidelity,
    *,
    tmp_path: Path | None = None,
    judges: Judges | None = None,
) -> Score:
    scenario = Scenario.model_validate(
        {"id": result.scenario_id, "title": "Imported", "turns": ["-"], "evals": [declaration]}
    )
    (only, *_) = perform_evals(
        scenario,
        trace_events=result.events,
        fidelity=fidelity,
        judges=judges,
        judgement_path=(tmp_path / "judgement.jsonl") if tmp_path else None,
        forbidden_phrases=None,
        tool_kinds=TOOL_KINDS,
    )
    return only


MECHANICAL: dict[str, Any] = {
    "expect_tools": {"expect_tools": ["search_articles"]},
    "expect_tools_order": {"expect_tools_order": ["search_articles"]},
    "expect_tools_any": {"expect_tools_any": ["search_articles"]},
    "forbid_tools": {"forbid_tools": ["escalate"]},
    "tool_count_max": {"tool_count_max": {"search_articles": 2}},
    "expect_tool_args": {
        "expect_tool_args": {"tool": "search_articles", "args": {"query": {"present": True}}}
    },
    "must_say_any": {"must_say_any": ["Billing"]},
    "must_not_say": {"must_not_say": ["guarantee"]},
    "forbidden_phrases": {"forbidden_phrases": ["as an AI"]},
    "tool_latency": {"tool_latency": {"max_ms": 60000}},
    "response_latency": {"response_latency": {"max_ms": 30000}},
    "first_token_latency": {"first_token_latency": {"max_ms": 1000}},
    "tool_argument_types": {"tool_argument_types": {"search_articles": {"query": "string"}}},
}
"""One declaration of every mechanical Eval, over the help desk's tools."""


def test_every_mechanical_eval_is_declared_here() -> None:
    assert sorted(MECHANICAL) == sorted(
        name for name, spec in REGISTRY.items() if spec.kind == "mechanical"
    )


def test_every_importer_is_registered_by_its_evidence_kind_and_follows_the_protocol() -> None:
    assert set(IMPORTERS) == {"proxy", "conversation", "voice"}
    for kind, importer in IMPORTERS.items():
        assert isinstance(importer, Importer)
        assert importer.kind == kind
    assert IMPORTERS["proxy"].fidelity == "reconstructed"
    assert IMPORTERS["conversation"].fidelity == "observed"
    assert IMPORTERS["voice"].fidelity == "observed"


# --- decision 37: every registered Eval, per Importer ---

JUDGED: dict[str, Any] = {
    "prompt_adherence": "prompt_adherence",
    "guardrails": {"guardrails": {"rules": [{"id": "G1", "rule": "Stay polite."}]}},
    "goal": {"goal": "the user learns where to go"},
    "data_grounding": "data_grounding",
    "data_query": "data_query",
    "tool_choice": "tool_choice",
}
"""One declaration of every judged Eval."""

UNVERIFIABLE_MISSING = ("unverifiable", "evidence_missing")
TOO_LOW = ("unverifiable", "fidelity_too_low")
TOOL_EVALS = (
    "expect_tools",
    "expect_tools_order",
    "expect_tools_any",
    "forbid_tools",
    "tool_count_max",
    "expect_tool_args",
    "tool_argument_types",
)

EXPECTED: dict[str, dict[str, tuple[str, str | None]]] = {
    "proxy": {
        **dict.fromkeys(TOOL_EVALS, ("pass", None)),
        "must_say_any": ("pass", None),
        "must_not_say": ("pass", None),
        "forbidden_phrases": ("pass", None),
        "tool_latency": UNVERIFIABLE_MISSING,
        "response_latency": ("pass", None),
        "first_token_latency": UNVERIFIABLE_MISSING,
    },
    "edges": {
        **dict.fromkeys(TOOL_EVALS, UNVERIFIABLE_MISSING),
        "must_say_any": ("unverifiable", "evidence_truncated"),
        "must_not_say": ("unverifiable", "evidence_truncated"),
        "forbidden_phrases": ("unverifiable", "evidence_truncated"),
        "tool_latency": UNVERIFIABLE_MISSING,
        "response_latency": ("pass", None),
        "first_token_latency": UNVERIFIABLE_MISSING,
    },
    "conversation": {
        **dict.fromkeys(TOOL_EVALS, TOO_LOW),
        "must_say_any": ("pass", None),
        "must_not_say": ("pass", None),
        "forbidden_phrases": ("pass", None),
        "tool_latency": TOO_LOW,
        "response_latency": UNVERIFIABLE_MISSING,
        "first_token_latency": UNVERIFIABLE_MISSING,
    },
    "voice": {
        **dict.fromkeys(TOOL_EVALS, TOO_LOW),
        "must_say_any": ("fail", None),
        "must_not_say": ("pass", None),
        "forbidden_phrases": ("pass", None),
        "tool_latency": TOO_LOW,
        "response_latency": ("pass", None),
        "first_token_latency": UNVERIFIABLE_MISSING,
    },
}
"""Every mechanical Eval's Verdict and reason over each fixture, as decisions 34, 35 and 37
read the evidence: a cut response hides tool calls (edges), a cut reply proves no phrase
absent (edges), an untimed record measures no latency (conversation), and the Fidelity gate
runs before anything else (conversation and voice are `observed`)."""

LACKED_READ_BY: dict[str, dict[str, dict[str, tuple[str, str]]]] = {
    "proxy": {
        "end_time_exact": {"tool_latency": UNVERIFIABLE_MISSING},
        "start_time": {"tool_latency": UNVERIFIABLE_MISSING},
        "time_to_first_token": {"first_token_latency": UNVERIFIABLE_MISSING},
    },
    "edges": {
        "end_time_exact": {"tool_latency": UNVERIFIABLE_MISSING},
        "start_time": {"tool_latency": UNVERIFIABLE_MISSING},
        "time_to_first_token": {"first_token_latency": UNVERIFIABLE_MISSING},
        "result": {"tool_latency": UNVERIFIABLE_MISSING},
        "response_body": {
            **dict.fromkeys(TOOL_EVALS, UNVERIFIABLE_MISSING),
            "must_not_say": ("unverifiable", "evidence_truncated"),
        },
        "row": {},
        "flow_run": {},
    },
    "conversation": {
        "timestamps": {"response_latency": UNVERIFIABLE_MISSING},
        "arguments": {"expect_tool_args": TOO_LOW, "tool_argument_types": TOO_LOW},
        "start_time": {"tool_latency": TOO_LOW},
    },
    "voice": {
        "llm_calls": {"first_token_latency": UNVERIFIABLE_MISSING},
        "timestamps": {"tool_latency": TOO_LOW},
    },
}
"""For each fact a fixture's `import/source` lists as lacked, the Evals whose evidence it is
and the Verdict each must take. `row` (a dropped duplicate) and `flow_run` (a Flow run that
joined nothing) are what no Eval reads. Where the Fidelity gate runs first the reason is
`fidelity_too_low` (decision 37 allows either)."""

READS: dict[str, set[str]] = {
    **{name: {"response_body", "response"} for name in TOOL_EVALS[:5]},
    "expect_tool_args": {"response_body", "response", "arguments"},
    "tool_argument_types": {"response_body", "response", "arguments"},
    "must_say_any": {"response", "content"},
    "must_not_say": {"response", "content"},
    "forbidden_phrases": {"response", "content"},
    "tool_latency": {"timestamps", "start_time", "end_time", "end_time_exact", "result"},
    "response_latency": {"timestamps", "start_time", "end_time"},
    "first_token_latency": {"time_to_first_token"},
}
"""The not-observed facts each mechanical Eval's `pass` would rest on."""

FIXTURES: dict[str, tuple[EvidenceKind, Path, Fidelity]] = {
    "proxy": ("proxy", PROXY, "reconstructed"),
    "edges": ("proxy", EDGES, "reconstructed"),
    "conversation": ("conversation", CONVERSATION, "observed"),
    "voice": ("voice", VOICE, "observed"),
}


def every_mechanical_score(result: Imported, fidelity: Fidelity) -> dict[str, Score]:
    return {name: scored(declaration, result, fidelity) for name, declaration in MECHANICAL.items()}


@pytest.mark.parametrize("fixture", sorted(FIXTURES))
def test_every_mechanical_eval_takes_the_verdict_its_evidence_allows(fixture: str) -> None:
    kind, path, fidelity = FIXTURES[fixture]
    scores = every_mechanical_score(imported(kind, path), fidelity)
    assert {name: (score.verdict, score.reason) for name, score in scores.items()} == (
        EXPECTED[fixture]
    )


@pytest.mark.parametrize("fixture", sorted(FIXTURES))
def test_every_lacked_fact_makes_the_evals_that_read_it_unverifiable(fixture: str) -> None:
    kind, path, fidelity = FIXTURES[fixture]
    result = imported(kind, path)
    lacked = {entry.field for entry in result.lacked}
    assert lacked == set(LACKED_READ_BY[fixture])
    scores = every_mechanical_score(result, fidelity)
    for fact in lacked:
        for name, expected in LACKED_READ_BY[fixture][fact].items():
            assert (scores[name].verdict, scores[name].reason) == expected, (fact, name)


@pytest.mark.parametrize("fixture", sorted(FIXTURES))
def test_no_pass_cites_a_span_that_did_not_observe_what_its_eval_reads(fixture: str) -> None:
    kind, path, fidelity = FIXTURES[fixture]
    result = imported(kind, path)
    spans = spans_of(result)
    for name, score in every_mechanical_score(result, fidelity).items():
        if score.verdict != "pass":
            continue
        for span_id in score.evidence:
            assert not set(not_observed(spans[span_id])) & READS[name], (name, span_id)


@pytest.mark.parametrize("fixture", sorted(FIXTURES))
def test_no_judged_eval_claims_more_than_the_fidelity_and_none_passes_unasked(
    fixture: str, tmp_path: Path
) -> None:
    """With a client that refuses every call, a judged Eval below its Fidelity is
    `fidelity_too_low` without asking; one above it asks and never passes."""
    kind, path, fidelity = FIXTURES[fixture]
    result = imported(kind, path)
    for name, declaration in JUDGED.items():
        client = NoModel()
        judges = Judges(client, "claude-opus-5", backend=None)
        score = scored(declaration, result, fidelity, tmp_path=tmp_path / name, judges=judges)
        if FIDELITY_RANK[REGISTRY[name].min_fidelity] > FIDELITY_RANK[fidelity]:
            assert (score.verdict, score.reason) == TOO_LOW, name
        else:
            assert score.verdict != "pass", name


# --- the proxy-row Importer (decision 34) ---


def test_a_proxy_row_trace_opens_with_import_source_and_ends_completed() -> None:
    result = imported("proxy", PROXY)
    first, second, *_, last = result.events
    assert first.type == "trace/start"
    assert fields(first)["scenario"] == "imported-hd-0001"
    assert second.type == "import/source"
    assert second.actor == "agentdiag"
    source = fields(second)
    assert source["store"] == "proxy"
    assert source["rows"] == 4
    assert source["query"]["conversation_id"] is None
    assert source["lacked"] == [lacked.model_dump() for lacked in result.lacked]
    assert last.type == "trace/end"
    assert fields(last)["termination"] == "completed"
    assert result.scenario_id == "imported-hd-0001"
    assert result.conversation_id == "hd-0001"


def test_every_imported_span_is_marked_imported_at_the_importers_fidelity() -> None:
    for kind, path, fidelity in FIXTURES.values():
        spans = project_spans(imported(kind, path).events)
        assert spans
        for span in spans:
            assert span.attributes[SPAN_ORIGIN] == "imported", span.span_id
            assert span.fidelity == fidelity, span.span_id


def test_proxy_rows_fall_into_turns_by_the_content_of_their_last_user_message() -> None:
    result = imported("proxy", PROXY)
    spans = spans_of(result)
    assert [s.span_id for s in spans.values() if s.kind == "turn"] == ["turn-1", "turn-2"]
    calls = [span for span in spans.values() if span.kind == "llm_call"]
    assert [(span.span_id, span.turn, span.parent_span_id) for span in calls] == [
        ("llm_call-1", 1, "turn-1"),
        ("llm_call-2", 1, "turn-1"),
        ("llm_call-3", 2, "turn-2"),
        ("llm_call-4", 2, "turn-2"),
    ]
    users = [
        fields(e)["content"]
        for e in of_type(result.events, "message")
        if fields(e)["role"] == "user"
    ]
    assert users == [
        "How do I change the billing address on my account?",
        "Thanks. Can I download my past invoices too?",
    ]
    replies = [e for e in of_type(result.events, "message") if fields(e)["role"] == "assistant"]
    assert [(e.actor, e.span_id) for e in replies] == [("target", "turn-1"), ("target", "turn-2")]


def test_one_llm_call_per_row_with_its_times_usage_and_cost() -> None:
    spans = spans_of(imported("proxy", PROXY))
    first = spans["llm_call-1"]
    assert first.start_ms == ms("2026-09-20T09:00:00.000Z")
    assert first.end_ms == ms("2026-09-20T09:00:00.000Z") + 900
    assert first.attributes["gen_ai.request.model"] == "claude-sonnet-5"
    assert first.attributes["gen_ai.conversation.id"] == "hd-0001"
    assert first.attributes["gen_ai.usage.input_tokens"] == 640
    assert first.attributes["gen_ai.usage.output_tokens"] == 48
    # Priced by the table (decision 5): 640 in at $2/M plus 48 out at $10/M.
    assert first.attributes[COST_TOTAL] == pytest.approx(0.00176)
    assert COST_CHECK not in first.attributes
    assert first.attributes[NOT_OBSERVED] == ["end_time_exact", "time_to_first_token"]
    assert first.attributes["agentdiag.evidence.request_id"] == "req-hd-0001-1"
    reported = spans["llm_call-2"]
    assert reported.attributes[COST_TOTAL] == pytest.approx(0.0023)
    assert reported.attributes[REPORTED_COST_USD] == pytest.approx(0.0023)
    # 790 in and 72 out is $0.00230 by the table too.
    assert reported.attributes[COST_CHECK] == "agrees"


def test_a_row_without_usage_records_none_is_not_priced_and_says_so() -> None:
    loaded = read_rows_file(PROXY)
    for row in loaded[:2]:
        del row["prompt_tokens"], row["completion_tokens"]
    spans = spans_of(imported_rows("proxy", loaded))
    unpriced, reported = spans["llm_call-1"], spans["llm_call-2"]
    for span in (unpriced, reported):
        assert not [key for key in span.attributes if key.startswith("gen_ai.usage.")]
        assert "usage" in not_observed(span)
        assert COST_CHECK not in span.attributes
    assert COST_TOTAL not in unpriced.attributes
    # The row's own cost_usd still stands, reported; nothing checks it against no usage.
    assert reported.attributes[COST_TOTAL] == pytest.approx(0.0023)


def test_every_span_lasts_what_the_evidence_says() -> None:
    """Seeded at the earliest instant, nothing the evidence says is clamped: each Span's
    duration is the rows' `latency_ms`, the gap between rows, or the `at` differences."""
    spans = spans_of(imported("proxy", PROXY))
    loaded = read_rows_file(PROXY)
    for index, row in enumerate(loaded, start=1):
        call = spans[f"llm_call-{index}"]
        assert call.end_ms is not None
        assert call.end_ms - call.start_ms == row["latency_ms"]
    search, invoices = spans["retrieval-1"], spans["retrieval-2"]
    assert (search.start_ms, search.end_ms) == (
        ms("2026-09-20T09:00:00.900Z"),
        ms(loaded[1]["created_at"]),
    )
    assert (invoices.start_ms, invoices.end_ms) == (
        ms("2026-09-20T09:01:10.850Z"),
        ms(loaded[3]["created_at"]),
    )
    voice = spans_of(imported("voice", VOICE))
    assert voice["turn-1"].end_ms - voice["turn-1"].start_ms == 10000  # type: ignore[operator]
    assert voice["turn-2"].end_ms - voice["turn-2"].start_ms == 4000  # type: ignore[operator]


def test_a_clock_origin_moves_every_instant_by_one_offset() -> None:
    plain = spans_of(imported("proxy", PROXY))
    moved = spans_of(imported("proxy", PROXY, clock_origin="2030-01-01T00:00:00Z"))
    offset = ms("2030-01-01T00:00:00Z") - plain["turn-1"].start_ms
    for span_id, span in plain.items():
        assert moved[span_id].start_ms == span.start_ms + offset, span_id
        assert moved[span_id].end_ms == span.end_ms + offset, span_id  # type: ignore[operator]
    for kind, path in (("conversation", CONVERSATION), ("voice", VOICE)):
        before = spans_of(imported(kind, path))  # type: ignore[arg-type]
        after = spans_of(imported(kind, path, clock_origin="2030-01-01T00:00:00Z"))  # type: ignore[arg-type]
        for span_id, span in before.items():
            assert after[span_id].end_ms - after[span_id].start_ms == span.end_ms - span.start_ms  # type: ignore[operator]


def test_overlapping_rows_are_marked_where_the_trace_cannot_hold_their_times() -> None:
    loaded = read_rows_file(PROXY)
    loaded[1]["created_at"] = "2026-09-20T09:00:00.500Z"  # before row 1's end at .900
    result = imported_rows("proxy", loaded)
    spans = spans_of(result)
    assert "start_time" in not_observed(spans["llm_call-2"])
    assert "end_time" in not_observed(spans["retrieval-1"])
    assert any(e.field == "start_time" and e.where == "llm_call-2" for e in result.lacked)
    assert [e.ts for e in result.events] == sorted(e.ts for e in result.events)


def test_a_tool_use_block_is_a_child_span_answered_by_the_next_rows_tool_result() -> None:
    result = imported("proxy", PROXY)
    spans = spans_of(result)
    search = spans["retrieval-1"]
    assert search.parent_span_id == "llm_call-1"
    assert search.attributes["gen_ai.tool.name"] == "search_articles"
    assert search.attributes["gen_ai.tool.call.id"] == "toolu_hd0001_search"
    assert search.start_ms == spans["llm_call-1"].end_ms
    assert search.end_ms == ms("2026-09-20T09:00:01.300Z")
    # Both ends are the rows around the call, not the tool's own.
    assert not_observed(search) == ["start_time", "end_time_exact"]
    (call,) = [e for e in of_type(result.events, "tool/call") if e.span_id == "retrieval-1"]
    assert fields(call)["arguments"] == {"query": "change billing address"}
    assert fields(call)["not_observed"] == []
    (answer,) = [e for e in of_type(result.events, "tool/result") if e.span_id == "retrieval-1"]
    assert "KB-104" in str(fields(answer)["result"])


def test_a_tool_the_manifest_does_not_call_retrieval_is_a_tool_call_span() -> None:
    importer = importer_for("proxy", tool_kinds={})
    spans = project_spans(importer.import_rows(rows("proxy", PROXY)).events)
    kinds = [span.kind for span in spans if span.attributes.get("gen_ai.tool.name")]
    assert kinds == ["tool_call", "tool_call"]


def test_the_copy_that_is_not_a_cache_hit_is_kept_whatever_the_order() -> None:
    result = imported("proxy", EDGES)
    calls = [span for span in project_spans(result.events) if span.kind == "llm_call"]
    assert [span.attributes["agentdiag.evidence.request_id"] for span in calls] == [
        "req-hd-0002-1",
        "req-hd-0002-2",
        "req-hd-0002-3",
        "req-hd-0002-4",
    ]
    dropped = [lacked for lacked in result.lacked if lacked.field == "row"]
    assert [lacked.where for lacked in dropped] == ["request req-hd-0002-1"]
    assert "cache-hit duplicate" in dropped[0].reason

    earlier = read_rows_file(EDGES)
    cached = next(row for row in earlier if row.get("cache_hit"))
    cached["created_at"] = "2026-09-21T13:59:59.000Z"  # the cache hit now sorts first
    spans = spans_of(imported_rows("proxy", earlier))
    assert spans["llm_call-1"].start_ms == ms("2026-09-21T14:00:00.000Z")
    assert spans["llm_call-1"].end_ms == ms("2026-09-21T14:00:00.700Z")


def test_a_non_200_row_is_an_llm_call_in_error_named_by_its_status() -> None:
    spans = spans_of(imported("proxy", EDGES))
    failed = spans["llm_call-3"]
    assert failed.status == "error"
    assert failed.attributes["error.type"] == "529"
    # The error and the retry share their last user message, so one Turn holds both.
    assert failed.turn == spans["llm_call-4"].turn == 2


def test_a_truncated_body_carries_the_marker_and_its_span_says_so() -> None:
    result = imported("proxy", EDGES)
    spans = spans_of(result)
    assert "response_body" in not_observed(spans["llm_call-2"])
    (response,) = [e for e in of_type(result.events, "response") if e.span_id == "llm_call-2"]
    assert fields(response)["truncated"] is True
    reply = next(
        e
        for e in of_type(result.events, "message")
        if fields(e)["role"] == "assistant" and e.turn == 1
    )
    assert fields(reply)["truncated"] is True
    assert any(e.field == "response_body" and e.where == "llm_call-2" for e in result.lacked)


def test_a_cut_response_makes_every_tool_eval_unverifiable_citing_it() -> None:
    result = imported("proxy", EDGES)
    for name in TOOL_EVALS:
        score = scored(MECHANICAL[name], result, "reconstructed")
        assert (score.verdict, score.reason) == UNVERIFIABLE_MISSING, name
        assert "llm_call-2" in score.evidence, name


def test_arguments_a_cut_response_may_have_cut_are_not_observed_on_the_tool_span() -> None:
    loaded = read_rows_file(EDGES)
    loaded[0]["truncated"] = ["response_body"]
    result = imported_rows("proxy", loaded)
    ticket = spans_of(result)["tool_call-1"]
    assert "arguments" in not_observed(ticket)
    assert any(e.field == "arguments" and e.where == "tool_call-1" for e in result.lacked)


def test_a_turn_whose_reply_was_never_logged_is_marked_and_no_phrase_is_proven_absent() -> None:
    loaded = read_rows_file(PROXY)
    loaded[3]["response_body"] = None
    result = imported_rows("proxy", loaded)
    spans = spans_of(result)
    assert "response" in not_observed(spans["turn-2"])
    screened = scored(MECHANICAL["must_not_say"], result, "reconstructed")
    assert (screened.verdict, screened.reason) == UNVERIFIABLE_MISSING
    assert "turn-2" in screened.evidence
    said = scored({"must_say_any": {"phrases": ["KB-117"], "turn": 2}}, result, "reconstructed")
    assert (said.verdict, said.reason) == UNVERIFIABLE_MISSING
    found = scored({"must_say_any": ["KB-104"]}, result, "reconstructed")
    assert found.verdict == "pass"
    tools = scored(MECHANICAL["expect_tools"], result, "reconstructed")
    assert (tools.verdict, tools.reason) == UNVERIFIABLE_MISSING


def test_an_openai_shaped_tool_call_with_no_tool_row_is_a_span_with_no_result() -> None:
    result = imported("proxy", EDGES)
    spans = spans_of(result)
    ticket = spans["tool_call-1"]
    assert ticket.attributes["gen_ai.tool.name"] == "open_ticket"
    assert ticket.end_ms == ms("2026-09-21T14:00:01.600Z")
    escalate = spans["tool_call-2"]
    assert escalate.attributes["gen_ai.tool.name"] == "escalate"
    assert "result" in not_observed(escalate)
    assert escalate.status == "ok"
    assert not [e for e in of_type(result.events, "tool/result") if e.span_id == "tool_call-2"]
    (call,) = [e for e in of_type(result.events, "tool/call") if e.span_id == "tool_call-2"]
    assert fields(call)["arguments"] == {"ticket": "T-311"}
    assert any(e.field == "result" and e.where == "tool_call-2" for e in result.lacked)


def test_a_flow_run_joins_the_call_of_its_tool_by_name_and_time() -> None:
    result = imported("proxy", EDGES)
    spans = spans_of(result)
    (flow,) = [span for span in spans.values() if span.kind == "flow"]
    assert flow.parent_span_id == "tool_call-1"
    assert flow.attributes["agentdiag.flow.id"] == "open_ticket"
    assert flow.attributes["agentdiag.flow.joined_by"] == "time"
    assert flow.start_ms == ms("2026-09-21T14:00:00.900Z")
    assert flow.end_ms == ms("2026-09-21T14:00:01.400Z")
    # A Flow run no call of its tool was open for is listed, never dropped silently.
    (unjoined,) = [e for e in result.lacked if e.field == "flow_run"]
    assert unjoined.where == "send_survey"


def test_a_flow_run_of_another_tool_does_not_join_whatever_its_time() -> None:
    loaded = read_rows_file(EDGES)
    flow = next(row for row in loaded if row.get("flow_id") == "open_ticket")
    flow["flow_id"] = "archive_ticket"
    result = imported_rows("proxy", loaded)
    assert not [span for span in project_spans(result.events) if span.kind == "flow"]
    assert {e.where for e in result.lacked if e.field == "flow_run"} == {
        "archive_ticket",
        "send_survey",
    }


def test_a_row_that_is_not_in_the_generic_shape_is_refused_by_its_index() -> None:
    broken = rows("proxy", PROXY)
    broken.rows[2] = {"request_id": "x"}
    with pytest.raises(ImportRowsError, match="row 2"):
        importer_for("proxy", tool_kinds={}).import_rows(broken)


def test_response_latency_over_proxy_rows_holds_their_latency_citing_reconstructed_ends() -> None:
    result = imported("proxy", PROXY)
    held = scored({"response_latency": {"max_ms": 30000}}, result, "reconstructed")
    assert held.verdict == "pass"
    # Turn 1 runs from the first row's created_at to the second row's end: 1300 + 1100 ms.
    assert held.value == 2400
    assert "llm_call-2" in held.evidence
    broken = scored({"response_latency": {"max_ms": 1000}}, result, "reconstructed")
    assert broken.verdict == "fail"
    assert "llm_call-2" in broken.evidence


# --- the conversation-record Importer (decision 35) ---


def test_an_operator_reply_sits_in_the_turn_it_answered_which_says_so() -> None:
    result = imported("conversation", CONVERSATION)
    assert result.scenario_id == "imported-c-0001"
    spans = spans_of(result)
    assert len([span for span in spans.values() if span.kind == "turn"]) == 3
    (operator,) = [e for e in of_type(result.events, "message") if e.actor == "operator"]
    assert operator.span_id == "turn-2"
    assert fields(operator)["role"] == "operator"
    assert spans["turn-2"].attributes[TURN_ANSWERED_BY] == "operator"
    assert TURN_ANSWERED_BY not in spans["turn-1"].attributes
    assert not [
        e
        for e in of_type(result.events, "message")
        if e.actor == "target" and e.span_id == "turn-2"
    ]


def test_a_tool_message_is_a_result_only_tool_call_span() -> None:
    result = imported("conversation", CONVERSATION)
    (tool,) = [span for span in spans_of(result).values() if span.kind == "retrieval"]
    assert tool.parent_span_id == "turn-1"
    assert tool.attributes["gen_ai.tool.name"] == "search_articles"
    assert {"arguments", "start_time"} <= set(not_observed(tool))
    (call,) = [e for e in of_type(result.events, "tool/call") if e.span_id == tool.span_id]
    assert fields(call)["not_observed"] == ["arguments"]
    assert fields(call)["arguments"] is None


def test_a_record_without_times_steps_by_a_millisecond_and_says_so() -> None:
    result = imported("conversation", CONVERSATION)
    events = [e for e in result.events if e.type != "trace/start"]
    origin = ms("2026-09-22T08:30:00Z")
    assert events[0].ts >= origin
    assert events[-1].ts - origin < len(events) + 1
    for span in project_spans(result.events):
        assert "timestamps" in not_observed(span), span.span_id
    assert any(lacked.field == "timestamps" for lacked in result.lacked)


def test_a_message_with_no_content_is_not_observed_never_the_text_none() -> None:
    loaded = record(CONVERSATION)
    loaded["messages"][2]["content"] = None
    result = imported_rows("conversation", [loaded])
    (reply,) = [
        e
        for e in of_type(result.events, "message")
        if e.actor == "target" and e.span_id == "turn-1"
    ]
    assert fields(reply)["content"] is None
    assert fields(reply)["not_observed"] == ["content"]
    assert "content" in not_observed(spans_of(result)["turn-1"])
    assert "None" not in [fields(e)["content"] for e in of_type(result.events, "message")]
    screened = scored(MECHANICAL["must_not_say"], result, "observed")
    assert (screened.verdict, screened.reason) == UNVERIFIABLE_MISSING


# --- the voice Importer (decision 35) ---


def test_a_voice_conversation_is_its_transcripts_turns_and_tool_calls() -> None:
    result = imported("voice", VOICE)
    spans = spans_of(result)
    turns = [span for span in spans.values() if span.kind == "turn"]
    assert len(turns) == 2
    assert turns[0].start_ms == ms("2026-09-23T16:00:02Z")
    for turn in turns:
        assert not_observed(turn) == ["llm_calls"]
    (tool,) = [span for span in spans.values() if span.kind == "retrieval"]
    assert tool.turn == 1
    assert not_observed(tool) == ["timestamps"]
    (call,) = [e for e in of_type(result.events, "tool/call") if e.span_id == tool.span_id]
    assert fields(call)["arguments"] == {"query": "reset password"}
    assert fields(of_type(result.events, "trace/end")[0])["duration_s"] == 48.5
    assert len([e for e in of_type(result.events, "message") if e.actor == "target"]) == 3
    assert {e.where for e in result.lacked if e.field == "llm_calls"} == {"turn-1", "turn-2"}
    assert {e.where for e in result.lacked if e.field == "timestamps"} == {tool.span_id}


def test_an_importer_is_pure_the_same_rows_give_the_same_events() -> None:
    for kind, path, _ in FIXTURES.values():
        once = imported(kind, path).events
        again = imported(kind, path).events
        assert [e.model_dump() for e in once] == [e.model_dump() for e in again]
        assert resolve_blobs(once)


def test_the_importer_package_imports_no_model_client_and_no_sdk() -> None:
    """The Importers are offline at import time (the Phase 6 gate): only pricing a row reads
    the price table, and only the `import` command (`importer.command`) writes a Run."""
    probe = (
        "import sys, agentdiag.importer, agentdiag.importer.rows; "
        "leaked = sorted(m for m in sys.modules if m == 'anthropic' "
        "or m.startswith(('anthropic.', 'agentdiag.model', 'agentdiag.adapter', "
        "'agentdiag.importer.command'))); "
        "print(','.join(leaked))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert completed.stdout.strip() == ""
