"""Closed types are validated on write: a string outside the set cannot be recorded.

Two halves of one rule (D2, ADR-0003 section 1). On the Trace side a string outside a set
is refused before the writer changes any state, so a rejected write leaves no line. On the
Score side each Verdict carries exactly the cause its Verdict allows, so a Score that means
two things at once cannot be built and cannot reach `scores.json`.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from agentdiag.eval.score import Score, ScoreSource
from agentdiag.run.manifest import SuiteEntry
from agentdiag.sync.compare import SectionState
from agentdiag.sync.fingerprint import Section
from agentdiag.trace import Event, TraceWriter, read_trace
from agentdiag.trace.attributes import SPAN_ORIGIN
from agentdiag.types import (
    BROKEN_DIRECTIONS,
    DEPLOYED_MOVED,
    DEPLOYED_SOURCES,
    PROTECTED_BY_DEFAULT,
    SIDE_EFFECT_ORDER,
    STRUCTURED_OUTPUT,
    Access,
    Actor,
    BackendKind,
    Change,
    ChangeStatus,
    CloseReason,
    Confirmation,
    CoveredBy,
    Direction,
    EvidenceKind,
    Finding,
    JsonType,
    Layer,
    NotObservedFact,
    PushKind,
    SectionKind,
    SpanOrigin,
    SuiteStatus,
    SyncBreakOpener,
    TriggerKind,
    VerificationResult,
)


def test_an_event_with_an_actor_outside_the_closed_set_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Event(seq=0, ts=1, type="note", actor="user", turn=None, span_id=None, parent_span_id=None)  # type: ignore[arg-type]


def test_a_span_end_status_outside_the_closed_set_is_rejected(tmp_path: Path) -> None:
    writer = TraceWriter(tmp_path / "trace.jsonl")
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    span = writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented")
    with pytest.raises(ValidationError):
        span.end(status="done")  # type: ignore[arg-type]
    writer.close()


def test_a_trace_end_with_an_unknown_termination_is_rejected(tmp_path: Path) -> None:
    writer = TraceWriter(tmp_path / "trace.jsonl")
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    with pytest.raises(ValidationError):
        writer.end("finished")  # type: ignore[arg-type]
    writer.close()


def test_a_fidelity_outside_the_closed_set_is_rejected(tmp_path: Path) -> None:
    writer = TraceWriter(tmp_path / "trace.jsonl")
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    with pytest.raises(ValidationError):
        writer.span("turn", actor="agentdiag", name="turn 1", fidelity="guessed")  # type: ignore[arg-type]
    writer.close()


def test_a_sync_status_outside_the_closed_set_is_rejected_and_nothing_is_written(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    before = path.read_text(encoding="utf-8")

    with pytest.raises(ValidationError):
        writer.event("sync/checked", actor="agentdiag", status="wobbly", reason="no idea")

    assert path.read_text(encoding="utf-8") == before
    writer.event("sync/checked", actor="agentdiag", status="not_checked", reason="no_fingerprint")
    writer.end("completed")
    writer.close()

    events = read_trace(path)
    assert [event.seq for event in events] == [0, 1, 2]
    assert (events[1].model_extra or {})["status"] == "not_checked"


def test_a_sync_reason_outside_the_closed_set_is_rejected(tmp_path: Path) -> None:
    writer = TraceWriter(tmp_path / "trace.jsonl")
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    with pytest.raises(ValidationError):
        writer.event("sync/checked", actor="agentdiag", status="not_checked", reason="dunno")
    writer.close()


def test_a_raw_span_end_event_with_a_status_outside_the_closed_set_is_rejected(
    tmp_path: Path,
) -> None:
    """The guard is on the Event type, not on the method that happened to write it."""
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    with pytest.raises(ValidationError):
        writer.event("span/end", actor="agentdiag", status="nope", attributes={}, error=None)
    with pytest.raises(ValidationError):
        writer.event("span/start", actor="agentdiag", kind="turn", fidelity="vibes")
    writer.close()

    assert [event.type for event in read_trace(path)] == ["trace/start"]


def test_a_rejected_span_end_leaves_the_span_open_and_the_trace_whole(tmp_path: Path) -> None:
    """A refused write changes nothing: the Span is still open and still ends cleanly."""
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    turn = writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented")

    with pytest.raises(ValidationError):
        turn.end(status="done")  # type: ignore[arg-type]

    # The Span is still open, so the next Event still lands inside it.
    after = writer.event("note", actor="agentdiag", text="the stack survived")
    assert after.span_id == "turn-1"
    assert after.turn == 1

    # And ending it properly still works, rather than raising SpanEndedTwice.
    turn.end()
    writer.end("completed")
    writer.close()

    events = read_trace(path)
    assert [event.type for event in events] == [
        "trace/start",
        "span/start",
        "note",
        "span/end",
        "trace/end",
    ]
    assert [event.seq for event in events] == [0, 1, 2, 3, 4]

    starts = {event.span_id for event in events if event.type == "span/start"}
    ends = {event.span_id for event in events if event.type == "span/end"}
    assert starts == ends


# --- the Score side (ADR-0003 section 3): each Verdict carries exactly its own cause ---


def _source() -> ScoreSource:
    return ScoreSource(kind="mechanical", code="agentdiag.eval.registry")


def test_a_verdict_outside_the_closed_set_is_rejected() -> None:
    """Five Verdicts, lowercase, as ADR-0003 section 1 names them — and no sixth."""
    with pytest.raises(ValidationError):
        Score(
            eval="prompt_adherence",
            verdict="PASS",  # type: ignore[arg-type]
            rationale="shouting is not a Verdict",
            evidence=[],
            source=_source(),
            fidelity="instrumented",
        )


def test_an_unverifiable_without_a_reason_is_rejected() -> None:
    """`unverifiable` exists to say why the evidence was not enough; it must say it."""
    with pytest.raises(ValidationError):
        Score(
            eval="prompt_adherence",
            verdict="unverifiable",
            rationale="no reason given",
            evidence=[],
            source=_source(),
            fidelity="instrumented",
        )


def test_an_incomplete_without_a_reason_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Score(
            eval="prompt_adherence",
            verdict="incomplete",
            rationale="no reason given",
            evidence=[],
            source=_source(),
            fidelity="instrumented",
        )


def test_an_invalid_without_a_fault_source_is_rejected() -> None:
    """An `invalid` says agentdiag broke the test; it must say which part of it."""
    with pytest.raises(ValidationError):
        Score(
            eval="prompt_adherence",
            verdict="invalid",
            fault_direction="none",
            rationale="whose fault?",
            evidence=[],
            source=_source(),
            fidelity="instrumented",
        )


def test_a_pass_that_carries_a_fault_source_is_rejected() -> None:
    """Only an `invalid` has a fault: a pass with one would be two claims at once."""
    with pytest.raises(ValidationError):
        Score(
            eval="prompt_adherence",
            verdict="pass",
            fault_source="agentdiag",
            fault_direction="none",
            rationale="passed, allegedly",
            evidence=[],
            source=_source(),
            fidelity="instrumented",
        )


def test_a_pass_that_carries_a_reason_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Score(
            eval="prompt_adherence",
            verdict="pass",
            reason="refusal",
            rationale="passed by refusing?",
            evidence=[],
            source=_source(),
            fidelity="instrumented",
        )


def test_a_fail_may_carry_a_target_side_reason_and_nothing_else() -> None:
    """ADR-0003 section 3: refusal rate is a first-class quantity, so `refusal` is allowed."""
    refused = Score(
        eval="prompt_adherence",
        verdict="fail",
        reason="refusal",
        rationale="the Target refused",
        evidence=["turn-1"],
        source=_source(),
        fidelity="instrumented",
    )
    assert refused.reason == "refusal"

    with pytest.raises(ValidationError):
        Score(
            eval="prompt_adherence",
            verdict="fail",
            reason="evidence_missing",
            rationale="that is an unverifiable's reason",
            evidence=[],
            source=_source(),
            fidelity="instrumented",
        )


def test_a_judged_pass_with_no_evidence_cannot_be_constructed() -> None:
    """ADR-0003 section 4: a Score that cites nothing claims more than it can show.

    The Judge maps this case to `unverifiable` / `evidence_missing` before it builds a
    Score, so reaching the constructor with one means a bug — and a bug that repaired
    itself quietly would be worse than one that raised.
    """
    with pytest.raises(ValidationError):
        Score(
            eval="prompt_adherence",
            verdict="pass",
            rationale="the Target followed every rule",
            evidence=[],
            source=ScoreSource(kind="judge", requested_model="claude-opus-5"),
            fidelity="instrumented",
        )


def test_a_judged_fail_with_no_evidence_cannot_be_constructed_either() -> None:
    with pytest.raises(ValidationError):
        Score(
            eval="prompt_adherence",
            verdict="fail",
            rationale="the Target broke rule 1",
            evidence=[],
            source=ScoreSource(kind="judge", requested_model="claude-opus-5"),
            fidelity="instrumented",
        )


def test_a_mechanical_score_may_decide_without_citing_a_span() -> None:
    """ADR-0003 section 4 binds judged Scores: an unregistered Eval read no Span at all."""
    score = Score(
        eval="tone_of_voice",
        verdict="unverifiable",
        reason="eval_not_applicable",
        rationale="no Eval of that name is registered",
        evidence=[],
        source=ScoreSource(kind="mechanical", code="agentdiag.eval.registry"),
        fidelity="instrumented",
    )

    assert score.evidence == []


def test_every_backend_kind_says_how_its_structured_output_is_held_to_the_schema() -> None:
    """Phase-5 decision 44: one phrase per kind, total over the closed set, so `show` and
    `compare` never meet a Backend they cannot describe."""
    assert set(STRUCTURED_OUTPUT) == set(get_args(BackendKind))
    assert dict(STRUCTURED_OUTPUT) == {
        "anthropic_api": "constrained at decoding",
        "claude_code": "checked after the fact, one retry",
        "replay": "as recorded",
    }


def test_an_operator_is_an_actor_a_trace_may_record() -> None:
    """Phase-5 decision 58: a staff member's takeover Turn in an imported Trace."""
    assert "operator" in get_args(Actor)
    assert set(get_args(Actor)) == {
        "target",
        "simulated_user",
        "adapter",
        "agentdiag",
        "judge",
        "operator",
    }


def test_where_a_span_came_from_is_one_of_two_origins_and_anything_else_is_refused(
    tmp_path: Path,
) -> None:
    """Decision 58: `agentdiag.span.origin` is `seed` or `imported`; absent means live."""
    assert set(get_args(SpanOrigin)) == {"seed", "imported"}
    writer = TraceWriter(tmp_path / "trace.jsonl")
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    with pytest.raises(ValidationError):
        writer.span(
            "turn",
            actor="agentdiag",
            name="turn 1",
            fidelity="instrumented",
            attributes={SPAN_ORIGIN: "guessed"},
        )
    seeded = writer.span(
        "turn",
        actor="operator",
        name="turn 1",
        fidelity="observed",
        attributes={SPAN_ORIGIN: "seed"},
    )
    seeded.end()
    writer.close()

    assert seeded.span_id == "turn-1", "the refused Span consumed no id"


def test_the_reviewers_findings_are_a_closed_set() -> None:
    assert get_args(Finding) == (
        "none",
        "leaked_hidden_fact",
        "stopped_early",
        "abandoned_goal",
        "contradicted_facts",
        "other",
    )


# --- the Manifest and Sync (ticket 10, phase-6 decisions 8 to 12, 42) ---


def test_the_manifest_and_sync_sets_are_closed_and_refused_outside_them() -> None:
    assert get_args(SuiteStatus) == ("runnable", "draft", "retired")
    assert set(get_args(JsonType)) == {
        "string",
        "number",
        "integer",
        "boolean",
        "object",
        "array",
        "null",
    }
    assert frozenset({"prod", "staging", "eu_prod"}) == PROTECTED_BY_DEFAULT
    assert SIDE_EFFECT_ORDER == ("none", "sandboxed", "live")
    assert set(get_args(SectionKind)) == {
        "prompt",
        "tool",
        "data_source",
        "model",
        "provider",
        "flow",
        "tier",
    }
    assert get_args(CoveredBy) == ("local", "adapter", "connector")
    assert get_args(Direction) == (
        "identical",
        "local_ahead",
        "deployed_ahead",
        "diverged",
        "not_covered",
    )
    assert frozenset({"local_ahead", "deployed_ahead", "diverged"}) == BROKEN_DIRECTIONS
    assert frozenset({"deployed_ahead", "diverged"}) == DEPLOYED_MOVED
    assert DEPLOYED_MOVED <= BROKEN_DIRECTIONS
    changes, absent = get_args(Change)
    assert get_args(changes) == ("changed", "added", "removed")
    assert absent is type(None)

    with pytest.raises(ValidationError):
        SectionState(id="x", kind="prompt", direction="drifted")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        SuiteEntry(path="s.yaml", status="paused")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Section(id="x", kind="prompt", sha256="h", covered_by="guess")  # type: ignore[arg-type]


def test_the_connectors_closed_sets_are_the_contracts() -> None:
    """Phase-6 decisions 19 and 26: what a Connector operation does, the Evidence stores it
    reads, and what may record a Sync break."""
    from agentdiag.connector.base import Operation
    from agentdiag.sync.breaks import SyncBreak

    assert get_args(Access) == ("read", "write")
    assert get_args(EvidenceKind) == ("proxy", "conversation", "voice", "flows")
    assert get_args(SyncBreakOpener) == ("sync", "watch")
    assert DEPLOYED_SOURCES == ("connector", "adapter")
    assert set(DEPLOYED_SOURCES) <= set(get_args(CoveredBy))

    with pytest.raises(ValidationError):
        Operation(name="x", access="delete", side_effects="none")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        SyncBreak(opened_at="t", opened_by="run")  # type: ignore[arg-type]


def test_what_a_span_may_mark_not_observed_is_a_closed_set() -> None:
    """ADR-0006 §4's not-observed state names one of these facts (ticket 26)."""
    assert set(get_args(NotObservedFact)) == {
        "arguments",
        "content",
        "end_time",
        "end_time_exact",
        "llm_calls",
        "request_body",
        "response",
        "response_body",
        "result",
        "start_time",
        "time_to_first_token",
        "timestamps",
        "usage",
    }


def test_the_change_records_closed_sets_are_the_contracts() -> None:
    """Phase-7 decisions 1 to 3 and 25: a Change record's status, layer, trigger and push
    kinds, how a comparison closes it and the two closes that take none; a record holding
    a value outside them is refused."""
    from agentdiag.change.record import ChangeRecord, PushEvent, Trigger, Verification

    assert get_args(ChangeStatus) == (
        "open",
        "proposed",
        "pushed",
        "verified",
        "refuted",
        "wontfix",
        "superseded",
    )
    assert get_args(Layer) == ("persona", "rules", "memory", "checklist", "flow", "runtime", "data")
    assert get_args(TriggerKind) == ("diagnosis", "complaint")
    assert get_args(PushKind) == ("connector", "local")
    assert get_args(VerificationResult) == ("verified", "refuted")
    assert get_args(CloseReason) == ("wontfix", "superseded")

    with pytest.raises(ValidationError):
        Trigger(kind="ticket", summary="s")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        PushEvent(kind="deploy", environment="local", at="t")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Verification(
            baseline="b",
            run="r",
            compared_at="t",
            expect=[],
            result="kept",
            summary="",  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        ChangeRecord(
            id="x",
            target="t",
            status="fixed-unverified",  # type: ignore[arg-type]
            opened_at="t",
            opened_by="me",
            title="x",
            trigger=Trigger(kind="complaint", summary="s"),
        )


def test_a_pushs_confirmation_is_the_contracts_closed_set() -> None:
    """Phase-7 decisions 13, 14 and 25: `--push` on an unprotected environment, the typed
    name on a protected one at a terminal or in the UI; a Push record holding any other
    value is refused."""
    from agentdiag.sync.pushes import PushRecord

    assert get_args(Confirmation) == ("--push", "typed_name", "ui_confirm")
    with pytest.raises(ValidationError):
        PushRecord.model_validate({"confirmed_by": "--force"})


def test_what_an_http_turn_marks_not_observed_is_in_the_closed_set() -> None:
    """Phase-8 decision 7: the `response` Span and the tool Spans a stream reports mark only
    facts `NotObservedFact` names, so the writer's closed set needs no new member."""
    from agentdiag.adapter.http.session import RESPONSE_NOT_OBSERVED
    from agentdiag.trace.spans import RESPONSE_KIND

    facts = set(get_args(NotObservedFact))
    assert RESPONSE_KIND == "response"
    assert set(RESPONSE_NOT_OBSERVED) <= facts
    assert {"start_time", "end_time", "arguments", "result"} <= facts
