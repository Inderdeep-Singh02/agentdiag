"""The conversation-record and voice Importers: what a platform stored of a chat or a call
becomes an `observed` Trace (phase-6 decision 35, ADR-0013 §5-§6).

A conversation record holds the messages, not the model calls behind them, so its Trace is
`observed`: one `turn` Span per user message with the Target's replies as `message` Events,
an `operator` message as a message of Actor `operator` in the Turn it answered (a staff
takeover Turn is a Turn the operator answered, and one the Target already answered gives the
operator a Turn of its own), and a `tool` message as a `tool_call` Span holding only the
result, its arguments and start not observed. A voice conversation is its transcript: the
same Turns, its tool calls as the platform reports them, `duration_s` on `trace/end`, and
every Turn marked `llm_calls` not observed, since a voice platform reports no model call.

A staff member's reply sits in the Turn it answered, and that Turn carries
`agentdiag.turn.answered_by: operator`, so `show` and the Evals can tell it from the
Target's. A message with no content is a message whose body is `content` not observed, never
the text "None".

**Times.** When every message carries `at`, the Spans take those instants, shifted by one
offset when `clock_origin` is given. When any does not, none is trusted: Span times follow
the record's order at one-millisecond steps from `started_at`, every Span is marked
`timestamps` not observed, and every latency Eval is therefore `unverifiable` /
`evidence_missing` rather than a measurement of nothing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from agentdiag.connector.base import EvidenceRows
from agentdiag.evidence import Lacked
from agentdiag.importer.base import (
    Imported,
    ImportRowsError,
    Marked,
    Recorder,
    instant,
    open_trace,
    parsed,
    scenario_id_for,
    span_attributes,
)
from agentdiag.importer.rows import ConversationRecord
from agentdiag.trace.attributes import TURN_ANSWERED_BY
from agentdiag.trace.events import Event
from agentdiag.types import Actor, EvidenceKind, Fidelity, NotObservedFact, ToolKind

STEP_MS = 1
UNTIMED_REASON = "the record gives no time for every message; Span times follow its order"
NO_CONTENT = "the record holds this message with no content"


def record_times(
    stamps: Sequence[str | None], started_at: str, clock_origin: str | None, where: str
) -> tuple[list[int], int, bool]:
    """Each entry's instant, the instant the Trace starts, and whether the times are the
    record's own (else stepped at one millisecond from `started_at`). `clock_origin` moves
    every instant by one offset, so no interval changes."""
    started = instant(started_at, f"{where} started_at")
    offset = instant(clock_origin, "clock_origin") - started if clock_origin else 0
    if stamps and all(stamp is not None for stamp in stamps):
        times = [instant(str(stamp), f"{where} at") + offset for stamp in stamps]
        return times, min([started + offset, *times]), True
    stepped = [started + offset + STEP_MS * (index + 1) for index in range(len(stamps))]
    return stepped, started + offset, False


def single_record[M: BaseModel](evidence: EvidenceRows, model: type[M], kind: str) -> M:
    """The one record the rows hold; a store read by conversation id returns one."""
    if not evidence.rows:
        raise ImportRowsError(f"the evidence holds no {kind} to import")
    if len(evidence.rows) > 1:
        raise ImportRowsError(
            f"the evidence holds {len(evidence.rows)} {kind}s; an import reads one conversation"
        )
    return parsed(model, evidence.rows[0], 0, kind)


class Turns:
    """The Turn being written, who answered it, and what it has not observed."""

    def __init__(
        self,
        recorder: Recorder,
        fidelity: Fidelity,
        not_observed: Sequence[NotObservedFact],
        learned: list[Lacked],
    ) -> None:
        self.recorder = recorder
        self.fidelity = fidelity
        self.not_observed = list(not_observed)
        self.learned = learned
        self.current: Marked | None = None
        self.answered_by: str | None = None
        self.count = 0

    def open(self, at: int, user: str | None = None, *, user_given: bool = True) -> Marked:
        """A new Turn at `at`, opened by the user's message when there is one."""
        self.close()
        self.count += 1
        writer = self.recorder.at(at)
        handle = writer.span(
            "turn",
            actor="agentdiag",
            name=f"turn {self.count}",
            fidelity=self.fidelity,
            attributes=span_attributes({}, self.not_observed),
        )
        self.current = Marked(handle, self.not_observed)
        self.answered_by = None
        if user_given:
            self.say("agentdiag", "user", user)
        return self.current

    def answer(self, at: int, by: str) -> Marked:
        """The Turn a reply by `by` (`target` or `operator`) belongs to: the current one,
        unless someone else already answered it, which gives this reply a Turn of its own."""
        if self.current is None or self.answered_by not in (None, by):
            self.open(at, user_given=False)
        assert self.current is not None
        self.answered_by = by
        self.recorder.at(at)
        return self.current

    def any(self, at: int) -> Marked:
        if self.current is None:
            self.open(at, user_given=False)
        assert self.current is not None
        self.recorder.at(at)
        return self.current

    def say(self, actor: Actor, role: str, content: Any) -> Event:
        """One message in the current Turn; one with no content is marked, not invented."""
        assert self.current is not None
        if content is None:
            self.current.add("content")
            self.learned.append(
                Lacked(field="content", where=self.current.span_id, reason=NO_CONTENT)
            )
            return self.current.handle.event(
                "message", actor=actor, role=role, content=None, not_observed=["content"]
            )
        return self.current.handle.event("message", actor=actor, role=role, content=content)

    def close(self) -> None:
        if self.current is not None:
            closing = {TURN_ANSWERED_BY: "operator"} if self.answered_by == "operator" else {}
            self.current.end(attributes=closing)
            self.current = None


def tool_span(
    recorder: Recorder,
    turn: Marked,
    *,
    name: str,
    call_id: str | None,
    arguments: Any,
    result: Any,
    fidelity: Fidelity,
    tool_kinds: Mapping[str, ToolKind],
    not_observed: Sequence[NotObservedFact],
    lacked: list[Lacked],
    why: Mapping[str, str],
) -> None:
    """One tool call as a record reports it: what it gives, and what it does not."""
    attributes: dict[str, Any] = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name}
    if call_id is not None:
        attributes["gen_ai.tool.call.id"] = call_id
    span = recorder.writer.span(
        "retrieval" if tool_kinds.get(name) == "retrieval" else "tool_call",
        actor="target",
        name=f"execute_tool {name}",
        fidelity=fidelity,
        attributes=span_attributes(attributes, not_observed),
        parent=turn.handle,
    )
    for fact in not_observed:
        if fact in why:
            lacked.append(Lacked(field=fact, where=span.span_id, reason=why[fact]))
    call_unseen = ["arguments"] if "arguments" in not_observed else []
    span.event(
        "tool/call",
        actor="target",
        tool=name,
        call_id=call_id,
        arguments=None if call_unseen else arguments,
        not_observed=call_unseen,
    )
    if "result" not in not_observed:
        span.event(
            "tool/result", actor="target", tool=name, call_id=call_id, result=result, is_error=False
        )
    span.end()


class ConversationRecordImporter:
    """A conversation record to an `observed` Trace (decision 35)."""

    kind: EvidenceKind = "conversation"
    fidelity: Fidelity = "observed"

    def __init__(self, *, tool_kinds: Mapping[str, ToolKind] | None = None) -> None:
        self.tool_kinds = dict(tool_kinds or {})

    def import_rows(self, rows: EvidenceRows, *, clock_origin: str | None = None) -> Imported:
        record = single_record(rows, ConversationRecord, "conversation record")
        learned: list[Lacked] = []
        self._emit(record, rows, clock_origin, [], learned)
        events = self._emit(record, rows, clock_origin, learned, [])
        return Imported(
            scenario_id=scenario_id_for(record.conversation_id),
            events=events,
            lacked=learned,
            conversation_id=record.conversation_id,
        )

    def _emit(
        self,
        record: ConversationRecord,
        rows: EvidenceRows,
        clock_origin: str | None,
        lacked: list[Lacked],
        learned: list[Lacked],
    ) -> list[Event]:
        times, origin, own = record_times(
            [m.at for m in record.messages], record.started_at, clock_origin, "conversation record"
        )
        recorder = Recorder(origin)
        open_trace(
            recorder,
            kind="conversation",
            scenario_id=scenario_id_for(record.conversation_id),
            conversation_id=record.conversation_id,
            rows=rows,
            lacked=lacked,
        )
        untimed: list[NotObservedFact] = [] if own else ["timestamps"]
        if untimed:
            learned.append(Lacked(field="timestamps", where="trace", reason=UNTIMED_REASON))
        turns = Turns(recorder, self.fidelity, untimed, learned)
        for message, at in zip(record.messages, times, strict=True):
            if message.role == "user":
                turns.open(at, message.content)
            elif message.role == "assistant":
                turns.answer(at, "target")
                turns.say("target", "assistant", message.content)
            elif message.role == "operator":
                turns.answer(at, "operator")
                turns.say("operator", "operator", message.content)
            else:
                tool_span(
                    recorder,
                    turns.any(at),
                    name=message.tool_name or "unknown",
                    call_id=message.tool_call_id,
                    arguments=None,
                    result=message.content,
                    fidelity=self.fidelity,
                    tool_kinds=self.tool_kinds,
                    not_observed=["arguments", "start_time", *untimed],
                    lacked=learned,
                    why={
                        "arguments": "a conversation record keeps a tool's result, not its call",
                        "start_time": "a conversation record keeps when the result arrived only",
                    },
                )
        turns.close()
        recorder.writer.end("completed")
        return recorder.events()


__all__ = [
    "UNTIMED_REASON",
    "ConversationRecordImporter",
    "Turns",
    "record_times",
    "single_record",
    "tool_span",
]
