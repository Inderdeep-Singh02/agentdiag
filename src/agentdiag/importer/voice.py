"""The voice Importer: a voice conversation's transcript becomes an `observed` Trace
(phase-6 decision 35, ADR-0013 §5-§6).

The transcript's turns are `turn` Spans, its tool calls `tool_call` Spans with the arguments
and result the platform reports (each one it does not report marked not observed), the
call's `duration_s` rides on `trace/end`, and every Turn is marked `llm_calls` not observed:
a voice platform reports no model call, so no Eval may read the absence of `llm_call` Spans
as a fact. Times follow the conversation-record rule (`record_times`).
"""

from __future__ import annotations

from collections.abc import Mapping

from agentdiag.connector.base import EvidenceRows
from agentdiag.importer.base import (
    Imported,
    Lacked,
    Recorder,
    open_trace,
    scenario_id_for,
)
from agentdiag.importer.conversation import (
    UNTIMED_REASON,
    Turns,
    record_times,
    single_record,
    tool_span,
)
from agentdiag.importer.rows import VoiceConversation
from agentdiag.trace.events import Event
from agentdiag.types import EvidenceKind, Fidelity, NotObservedFact, ToolKind


class VoiceConversationImporter:
    """A voice conversation's transcript to an `observed` Trace (decision 35)."""

    kind: EvidenceKind = "voice"
    fidelity: Fidelity = "observed"

    def __init__(self, *, tool_kinds: Mapping[str, ToolKind] | None = None) -> None:
        self.tool_kinds = dict(tool_kinds or {})

    def import_rows(self, rows: EvidenceRows, *, clock_origin: str | None = None) -> Imported:
        voice = single_record(rows, VoiceConversation, "voice conversation")
        learned: list[Lacked] = []
        self._emit(voice, rows, clock_origin, [], learned)
        events = self._emit(voice, rows, clock_origin, learned, [])
        return Imported(
            scenario_id=scenario_id_for(voice.conversation_id),
            events=events,
            lacked=learned,
            conversation_id=voice.conversation_id,
        )

    def _emit(
        self,
        voice: VoiceConversation,
        rows: EvidenceRows,
        clock_origin: str | None,
        lacked: list[Lacked],
        learned: list[Lacked],
    ) -> list[Event]:
        times, origin, own = record_times(
            [t.at for t in voice.transcript], voice.started_at, clock_origin, "voice conversation"
        )
        recorder = Recorder(origin)
        open_trace(
            recorder,
            kind="voice",
            scenario_id=scenario_id_for(voice.conversation_id),
            conversation_id=voice.conversation_id,
            rows=rows,
            lacked=lacked,
        )
        untimed: list[NotObservedFact] = [] if own else ["timestamps"]
        if untimed:
            learned.append(Lacked(field="timestamps", where="trace", reason=UNTIMED_REASON))
        turns = Turns(recorder, self.fidelity, ["llm_calls", *untimed], learned)
        seen = 0
        for entry, at in zip(voice.transcript, times, strict=True):
            if entry.role == "user":
                turn = turns.open(at, entry.text)
            else:
                turn = turns.answer(at, "target")
                if entry.text:
                    turns.say("target", "assistant", entry.text)
            for call in entry.tool_calls:
                # A transcript times the utterance, not the call: a tool Span's instants are
                # its utterance's, and not the tool's own.
                missing: list[NotObservedFact] = ["timestamps"]
                if call.arguments is None:
                    missing.insert(0, "arguments")
                if call.result is None:
                    missing.insert(-1, "result")
                tool_span(
                    recorder,
                    turn,
                    name=call.name,
                    call_id=None,
                    arguments=call.arguments,
                    result=call.result,
                    fidelity=self.fidelity,
                    tool_kinds=self.tool_kinds,
                    not_observed=missing,
                    lacked=learned,
                    why={
                        "arguments": "the transcript reports no arguments for this call",
                        "result": "the transcript reports no result for this call",
                        "timestamps": "a transcript times the utterance, not the tool call",
                    },
                )
            if turns.count > seen:
                seen = turns.count
                learned.append(
                    Lacked(
                        field="llm_calls",
                        where=turn.span_id,
                        reason="a voice platform reports no model call",
                    )
                )
        turns.close()
        if voice.duration_s is not None:
            recorder.writer.end("completed", duration_s=voice.duration_s)
        else:
            recorder.writer.end("completed")
        return recorder.events()


__all__ = ["VoiceConversationImporter"]
