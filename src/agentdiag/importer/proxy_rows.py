"""The proxy-row Importer: an LLM proxy's logged calls become a `reconstructed` Trace
(phase-6 decision 34, ADR-0013 §5-§6).

A proxy row is one model request and its response, logged outside the Target. It proves
which model was asked what, which tools the response asked for and what they returned in
the next request, but not when the response really ended or when its first token came, so
every Span is `reconstructed` and says what it lacks (`agentdiag.not_observed`, ADR-0006
§4). The rules, in the order they apply:

1. **Sort and deduplicate.** Rows are sorted by `created_at`. Rows sharing a full
   `request_id` are one call logged more than once: the copy that is not a `cache_hit` is
   kept whatever the order, and every other copy is dropped and listed under `lacked`.
2. **Turns by content** (ADR-0001 §3). The last user message with text in a row's request
   opens a `turn` Span when it differs from the previous row's, so a tool loop's several
   rows fall in one Turn. Never by position. A Turn whose last row logged no response has
   `response` not observed.
3. **One `llm_call` per row** under its Turn, with `gen_ai.request.model`, the usage when
   the row reports it (`usage` not observed otherwise, never zero), the cost (`cost_usd`
   when the row has one, checked against the price table when usage lets the table price
   it; else priced by the table from the usage), `gen_ai.conversation.id` and the row's
   request id; it starts at `created_at` and ends at `created_at + latency_ms`, marked not
   observed for `end_time_exact` and `time_to_first_token`. A non-200 `status` ends it in
   error with `error.type` the status. Its `request` and `response` Events carry the bodies;
   a cut or missing body is listed as not observed, a cut one carries `truncated: true`.
4. **Tool calls are child Spans.** Each `tool_use` block (Anthropic) or `tool_calls` entry
   (OpenAI) opens a `tool_call` Span — a `retrieval` when the Manifest's tools say so, as
   the Adapter records it — from the row's end to the next row's `created_at`, both ends
   reconstructed (`start_time` and `end_time_exact` not observed); its result is the next
   row's `tool` message (or `tool_result` block) with the same call id, else `result` is not
   observed. Arguments a cut response may have cut are not observed either.
5. **Flow runs join by name and time.** A `FlowRun` whose `flow_id` is the tool's name and
   whose start lies inside the tool Span's window (equal arguments break a tie) becomes a
   child `flow` Span marked `joined_by: time`; one no window holds is listed as lacked.

**Times.** The Recorder is seeded at the earliest evidence instant, and `clock_origin`
shifts every instant by one offset, so durations are the evidence's. Where rows overlap (a
row created before the previous one's events ended) the Trace cannot hold time running
backwards: the instant is clamped, and the Span marks `start_time` or `end_time` not
observed and is listed as lacked, rather than shrink an interval silently.

The Trace is built twice from one plan: once to learn the Span ids its lacked facts sit on,
once to write `import/source` with them. Both passes are the same pure function of the rows.

**Inside a live Trial** (`reconstruct_turn`, phase-8 decision 8, ADR-0013 §5): the HTTP
Adapter hands over the rows a Connector read after one Turn, the Trial's own writer and the
Turn's `response` Span. The rows are planned as `import_rows` plans them, only those whose
last user text is the message the Turn sent are kept (attribution by content, ADR-0001 §3;
the rest are counted as ignored), and the same `llm_call` and tool Spans are written under
that Span at Fidelity `reconstructed`, with three differences. **The Spans take the
writer's clock**, because a live Trace's `ts` never decreases and the rows arrived after
the Turn: each Span marks `start_time` and `end_time` not observed and carries the row's own
instants as attributes (`agentdiag.evidence.created_at`, `agentdiag.evidence.latency_ms`),
so no latency Eval reads a reconstructed duration. Every Span carries
`agentdiag.evidence.store: proxy` and the row's request id, and **no**
`agentdiag.span.origin`: it was recorded in this Trial, from evidence; `imported` is for a
Trace nobody drove. No `turn` Span and no `message` is written: the runner owns the Turn.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from agentdiag.connector.base import EvidenceRows
from agentdiag.importer.base import (
    Imported,
    ImportRowsError,
    Lacked,
    Marked,
    Recorder,
    instant,
    open_trace,
    parsed,
    scenario_id_for,
    span_attributes,
)
from agentdiag.importer.rows import FLOW_RUN_KEY, FlowRun, ProxyRow
from agentdiag.trace.attributes import (
    COST_CHECK,
    EVIDENCE_CREATED_AT,
    EVIDENCE_LATENCY_MS,
    EVIDENCE_REQUEST_ID,
    EVIDENCE_STORE,
    FLOW_ID,
    FLOW_JOINED_BY,
    NOT_OBSERVED,
    REPORTED_COST_USD,
)
from agentdiag.trace.events import Event
from agentdiag.trace.openinference import COST_TOTAL
from agentdiag.trace.writer import SpanHandle, TraceWriter
from agentdiag.types import EvidenceKind, Fidelity, NotObservedFact, ToolKind

REQUEST_BODY = "request_body"
RESPONSE_BODY = "response_body"
FLOW_OK = frozenset({"ok", "success", "succeeded", "completed", "done"})

LATENCY_ONLY = "the row records created_at and latency_ms, not when the response ended"
NOT_STREAMED = "a proxy row holds the whole response, not when its first token arrived"
NO_USAGE = "the row reports no token usage"
BETWEEN_ROWS = (
    "a tool runs between the response that asked for it and the next row's created_at; "
    "neither end is the tool's own"
)
OVERLAP = (
    "the row's instant is earlier than an Event already written (the rows overlap), and a "
    "Trace's time never runs backwards"
)
LIVE_CLOCK = (
    "reconstructed inside a live Trial, the Span takes the Trace's clock; the row's own "
    "instants are its agentdiag.evidence.created_at and latency_ms"
)


class Reconstructed(BaseModel):
    """What one Turn's proxy rows gave inside a live Trial (phase-8 decision 8)."""

    spans: int
    """How many `reconstructed` Spans were written."""

    rows_used: int
    """The rows whose last user text is the Turn's message."""

    rows_ignored: int
    """The rest: another Turn's, or another conversation's."""

    lacked: list[Lacked]


@dataclass
class ToolUse:
    """One tool call a response asked for."""

    call_id: str | None
    name: str
    arguments: Any
    arguments_observed: bool


@dataclass
class ToolAnswer:
    """A tool result a later request carried back."""

    content: Any
    is_error: bool


@dataclass
class PlannedRow:
    """One kept row, read."""

    row: ProxyRow
    start: int
    end: int
    user: str | None
    request_cut: bool
    response_cut: bool
    reply: str | None
    tools: list[ToolUse]
    answers: dict[str, ToolAnswer]
    opens_turn: bool = False


@dataclass
class PlannedFlow:
    """One Flow run among the rows, with its instants."""

    flow: FlowRun
    start: int
    end: int | None


@dataclass
class RowPlan:
    """What the rows say, read before anything is written."""

    rows: list[PlannedRow]
    flows: list[PlannedFlow]
    dropped: list[Lacked] = field(default_factory=list)
    conversation_id: str | None = None


class ProxyRowImporter:
    """Proxy rows to a `reconstructed` Trace (decision 34)."""

    kind: EvidenceKind = "proxy"
    fidelity: Fidelity = "reconstructed"

    def __init__(self, *, tool_kinds: Mapping[str, ToolKind] | None = None) -> None:
        self.tool_kinds = dict(tool_kinds or {})
        """The Manifest's `tools.<name>.kind`: a `retrieval` tool's calls are `retrieval`
        Spans, as the Adapter records them."""

    def import_rows(self, rows: EvidenceRows, *, clock_origin: str | None = None) -> Imported:
        plan = _plan(rows, clock_origin)
        scenario_id = scenario_id_for(plan.conversation_id)
        learned: list[Lacked] = []
        _Emission(self, plan, rows, scenario_id, [], learned).run()
        lacked = [*plan.dropped, *learned]
        final = _Emission(self, plan, rows, scenario_id, lacked, [])
        return Imported(
            scenario_id=scenario_id,
            events=final.run(),
            lacked=lacked,
            conversation_id=plan.conversation_id,
        )

    def reconstruct_turn(
        self,
        rows: EvidenceRows,
        *,
        writer: TraceWriter,
        parent: SpanHandle,
        user_message: str,
    ) -> Reconstructed:
        """Write the `reconstructed` Spans of the rows attributed to one live Turn under
        `parent` (decision 8); writes nothing when no row is the Turn's, so a caller may
        read again. `ImportRowsError` when a row is not in the generic shape."""
        if not rows.rows:
            return Reconstructed(spans=0, rows_used=0, rows_ignored=0, lacked=[])
        plan = _plan(rows, None)
        kept = [planned for planned in plan.rows if planned.user == user_message]
        ignored = len(plan.rows) - len(kept)
        if not kept:
            return Reconstructed(spans=0, rows_used=0, rows_ignored=ignored, lacked=[])
        turn_plan = RowPlan(
            rows=kept,
            flows=plan.flows,
            dropped=plan.dropped,
            conversation_id=plan.conversation_id,
        )
        emission = _TurnEmission(self, turn_plan, rows, writer)
        emission.run_turn(parent)
        return Reconstructed(
            spans=emission.spans,
            rows_used=len(kept),
            rows_ignored=ignored,
            lacked=[*plan.dropped, *emission.learned],
        )


# --- the plan: rows read, sorted, deduplicated, grouped ---


def _plan(evidence: EvidenceRows, clock_origin: str | None) -> RowPlan:
    proxies: list[tuple[int, ProxyRow, int]] = []
    flows: list[PlannedFlow] = []
    for index, raw in enumerate(evidence.rows):
        if FLOW_RUN_KEY in raw:
            flow = parsed(FlowRun, raw, index, "flow")
            ended = instant(flow.ended_at, f"flow row {index} ended_at") if flow.ended_at else None
            started = instant(flow.started_at, f"flow row {index} started_at")
            flows.append(PlannedFlow(flow, started, ended))
            continue
        row = parsed(ProxyRow, raw, index, "proxy")
        proxies.append((index, row, instant(row.created_at, f"proxy row {index} created_at")))
    if not proxies:
        raise ImportRowsError("the evidence holds no proxy rows to import")
    proxies.sort(key=lambda item: (item[2], item[0]))

    earliest = min([start for _, _, start in proxies] + [flow.start for flow in flows])
    offset = instant(clock_origin, "clock_origin") - earliest if clock_origin else 0
    for planned_flow in flows:
        planned_flow.start += offset
        if planned_flow.end is not None:
            planned_flow.end += offset

    keepers: dict[str, tuple[int, ProxyRow, int]] = {}
    for item in proxies:
        held = keepers.get(item[1].request_id)
        if held is None or (held[1].cache_hit and not item[1].cache_hit):
            keepers[item[1].request_id] = item
    kept: list[PlannedRow] = []
    dropped: list[Lacked] = []
    for item in proxies:
        _, row, start = item
        if keepers[row.request_id] is not item:
            what = "a cache-hit duplicate" if row.cache_hit else "a duplicate"
            dropped.append(
                Lacked(
                    field="row",
                    where=f"request {row.request_id}",
                    reason=f"{what} of another row with the same request id; dropped",
                )
            )
            continue
        kept.append(_read_row(row, start + offset))

    previous: str | None = None
    for position, planned in enumerate(kept):
        if position == 0 or (planned.user is not None and planned.user != previous):
            planned.opens_turn = True
        if planned.user is not None:
            previous = planned.user
    conversation = evidence.query.conversation_id or next(
        (planned.row.conversation_id for planned in kept if planned.row.conversation_id), None
    )
    return RowPlan(rows=kept, flows=flows, dropped=dropped, conversation_id=conversation)


def _cut(row: ProxyRow, body: str) -> bool:
    return any(path == body or path.startswith(f"{body}.") for path in row.truncated)


def _read_row(row: ProxyRow, start: int) -> PlannedRow:
    request = row.request_body or {}
    response = row.response_body or {}
    response_cut = _cut(row, RESPONSE_BODY)
    reply, tools = _response(response)
    if response_cut:
        # A cut response may have cut a call's arguments: never read them as whole.
        tools = [ToolUse(t.call_id, t.name, t.arguments, False) for t in tools]
    messages = request.get("messages")
    messages = messages if isinstance(messages, list) else []
    return PlannedRow(
        row=row,
        start=start,
        end=start + round(row.latency_ms),
        user=_last_user_text(messages),
        request_cut=_cut(row, REQUEST_BODY),
        response_cut=response_cut,
        reply=reply,
        tools=tools,
        answers=_answers(messages),
    )


def _text_of(content: Any) -> str | None:
    """A message's text: a string, or its text blocks joined; None when it has none."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, Mapping) and block.get("type") == "text"
        ]
        return "\n".join(texts) if texts else None
    return None


def _last_user_text(messages: Sequence[Any]) -> str | None:
    for message in reversed(messages):
        if isinstance(message, Mapping) and message.get("role") == "user":
            text = _text_of(message.get("content"))
            if text:
                return text
    return None


def _answers(messages: Sequence[Any]) -> dict[str, ToolAnswer]:
    """Every tool result the request carries, by call id: OpenAI `tool` messages and
    Anthropic `tool_result` blocks alike."""
    answers: dict[str, ToolAnswer] = {}
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        if message.get("role") == "tool" and message.get("tool_call_id"):
            answers[str(message["tool_call_id"])] = ToolAnswer(message.get("content"), False)
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, Mapping) and block.get("type") == "tool_result":
                inner = block.get("content")
                answers[str(block.get("tool_use_id"))] = ToolAnswer(
                    _text_of(inner) if isinstance(inner, list) else inner,
                    bool(block.get("is_error")),
                )
    return answers


def _response(body: Mapping[str, Any]) -> tuple[str | None, list[ToolUse]]:
    """The reply text and the tool calls of an Anthropic or OpenAI response body."""
    tools: list[ToolUse] = []
    content = body.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "tool_use":
                tools.append(
                    ToolUse(
                        str(block["id"]) if block.get("id") else None,
                        str(block.get("name")),
                        block.get("input"),
                        isinstance(block.get("input"), Mapping),
                    )
                )
        return _text_of(content), tools
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        message = choices[0].get("message") or {}
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            arguments, observed = _json_arguments(function.get("arguments"))
            tools.append(
                ToolUse(
                    str(call["id"]) if call.get("id") else None,
                    str(function.get("name")),
                    arguments,
                    observed,
                )
            )
        return _text_of(message.get("content")), tools
    return None, tools


def _json_arguments(value: Any) -> tuple[Any, bool]:
    if isinstance(value, Mapping):
        return dict(value), True
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return None, False
        return (loaded, True) if isinstance(loaded, dict) else (None, False)
    return None, False


def _finish_reason(body: Mapping[str, Any]) -> str | None:
    if body.get("stop_reason"):
        return str(body["stop_reason"])
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        reason = choices[0].get("finish_reason")
        return str(reason) if reason else None
    return None


def _error_message(body: Mapping[str, Any], status: int) -> str:
    error = body.get("error")
    if isinstance(error, Mapping) and error.get("message"):
        return str(error["message"])
    return f"the proxy logged status {status}"


def _usage(row: ProxyRow) -> dict[str, int] | None:
    """The row's usage as the price table reads it; None when the row reports none."""
    if row.prompt_tokens is None or row.completion_tokens is None:
        return None
    usage = {"input_tokens": row.prompt_tokens, "output_tokens": row.completion_tokens}
    if row.cache_read_tokens is not None:
        usage["cache_read_input_tokens"] = row.cache_read_tokens
    if row.cache_write_tokens is not None:
        usage["cache_creation_input_tokens"] = row.cache_write_tokens
    return usage


def _costs(row: ProxyRow) -> dict[str, Any]:
    """`llm.cost.*`: the row's `cost_usd` when it has one (recorded as reported, and checked
    against the table when the usage lets the table price it, ADR-0009), else the table's
    price of the usage; nothing when the row gives neither — an unpriced Span, never $0."""
    # The price table's module sits in `agentdiag.model`, whose package imports the SDK; the
    # Importer stays offline at import time and reads it only when a row is priced.
    from agentdiag.model.prices import cost_attributes, cost_check, cost_usd

    usage = _usage(row)
    if row.cost_usd is None:
        return dict(cost_attributes(row.model, usage)) if usage is not None else {}
    reported: dict[str, Any] = {COST_TOTAL: row.cost_usd, REPORTED_COST_USD: row.cost_usd}
    if usage is not None:
        reported[COST_CHECK] = cost_check(cost_usd(row.model, usage), row.cost_usd)
    return reported


# --- writing the Trace from the plan ---


@dataclass
class _Pending:
    span: Marked
    use: ToolUse
    opened: int


class _Emission:
    """One pass of writing the plan into an in-memory Trace."""

    def __init__(
        self,
        importer: ProxyRowImporter,
        plan: RowPlan,
        evidence: EvidenceRows,
        scenario_id: str,
        lacked: list[Lacked],
        learned: list[Lacked],
    ) -> None:
        self._init_state(importer, plan, evidence, scenario_id, lacked, learned)
        starts = [row.start for row in plan.rows] + [flow.start for flow in plan.flows]
        self.recorder = Recorder(min(starts))

    def _init_state(
        self,
        importer: ProxyRowImporter,
        plan: RowPlan,
        evidence: EvidenceRows,
        scenario_id: str,
        lacked: list[Lacked],
        learned: list[Lacked],
    ) -> None:
        """What every emission holds, an import's and a live Turn's alike; only an import
        also has a Recorder, since a live Turn writes on the Trial's clock."""
        self.importer = importer
        self.plan = plan
        self.evidence = evidence
        self.scenario_id = scenario_id
        self.lacked = lacked
        self.learned = learned
        self.joined: set[int] = set()
        self.turns = 0

    def lack(self, fact: str, where: str, reason: str) -> None:
        self.learned.append(Lacked(field=fact, where=where, reason=reason))

    def mark(self, span: Marked, fact: NotObservedFact, reason: str) -> None:
        span.add(fact)
        self.lack(fact, span.span_id, reason)

    def at(self, ms: int, span: Marked | None = None, fact: NotObservedFact = "start_time") -> None:
        """Move the clock; an instant the Trace cannot hold marks `span`'s `fact`."""
        self.recorder.at(ms)
        if self.recorder.clamped and span is not None:
            self.mark(span, fact, OVERLAP)

    def open(
        self,
        kind: str,
        *,
        actor: Any,
        name: str,
        at: int,
        attributes: Mapping[str, Any],
        facts: Sequence[NotObservedFact],
        parent: SpanHandle | None,
    ) -> Marked:
        """Open a Span at the evidence's instant; a clamped start is marked `start_time`."""
        writer = self.recorder.at(at)
        clamped = self.recorder.clamped
        opened = [*facts, "start_time"] if clamped else list(facts)
        handle = writer.span(
            kind,
            actor=actor,
            name=name,
            fidelity=self.importer.fidelity,
            attributes=span_attributes(attributes, opened),
            parent=parent,
        )
        span = Marked(handle, opened)
        if clamped:
            self.lack("start_time", span.span_id, OVERLAP)
        return span

    def run(self) -> list[Event]:
        open_trace(
            self.recorder,
            kind="proxy",
            scenario_id=self.scenario_id,
            conversation_id=self.plan.conversation_id,
            rows=self.evidence,
            lacked=self.lacked,
        )
        rows = self.plan.rows
        turn: Marked | None = None
        pending: list[_Pending] = []
        for position, planned in enumerate(rows):
            self.close(pending, planned)
            if planned.opens_turn or turn is None:
                if turn is not None:
                    self.end_turn(turn, rows[position - 1])
                turn = self.open_turn(planned)
            pending = self.model_call(turn, planned, self.reply_row(rows, position))
        self.close(pending, None)
        if turn is not None:
            self.end_turn(turn, rows[-1])
        for index, planned_flow in enumerate(self.plan.flows):
            if index not in self.joined:
                self.lack(
                    "flow_run",
                    planned_flow.flow.flow_id,
                    "no call of a tool of that name was open when the Flow run started",
                )
        self.recorder.writer.end("completed")
        return self.recorder.events()

    def reply_row(self, rows: Sequence[PlannedRow], position: int) -> bool:
        """Whether this row's reply is its Turn's closing words: the last in the Turn with
        text."""
        planned = rows[position]
        if not planned.reply:
            return False
        for later in rows[position + 1 :]:
            if later.opens_turn:
                break
            if later.reply:
                return False
        return True

    def open_turn(self, planned: PlannedRow) -> Marked:
        self.turns += 1
        turn = self.open(
            "turn",
            actor="agentdiag",
            name=f"turn {self.turns}",
            at=planned.start,
            attributes={},
            facts=[],
            parent=None,
        )
        if planned.user is not None:
            marker = {"truncated": True} if planned.request_cut else {}
            turn.handle.event(
                "message", actor="agentdiag", role="user", content=planned.user, **marker
            )
        return turn

    def end_turn(self, turn: Marked, last: PlannedRow) -> None:
        """End a Turn; one whose last row logged no response has `response` not observed."""
        if last.row.response_body is None:
            self.mark(turn, "response", "the Turn's last row logged no response")
        turn.end()

    def model_call(self, turn: Marked, planned: PlannedRow, closes_turn: bool) -> list[_Pending]:
        row = planned.row
        facts: list[NotObservedFact] = ["end_time_exact", "time_to_first_token"]
        reasons: dict[NotObservedFact, str] = {
            "end_time_exact": LATENCY_ONLY,
            "time_to_first_token": NOT_STREAMED,
        }
        for body, value, cut in (
            ("request_body", row.request_body, planned.request_cut),
            ("response_body", row.response_body, planned.response_cut),
        ):
            fact: NotObservedFact = "request_body" if body == "request_body" else "response_body"
            if value is None:
                facts.append(fact)
                reasons[fact] = "the proxy logged no body"
            elif cut:
                facts.append(fact)
                reasons[fact] = "the proxy cut the body it logged"
        usage = _usage(row)
        if usage is None:
            facts.append("usage")
            reasons["usage"] = NO_USAGE
        attributes: dict[str, Any] = {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": row.model,
            EVIDENCE_REQUEST_ID: row.request_id,
        }
        if row.provider:
            attributes["gen_ai.provider.name"] = row.provider
        if row.conversation_id or self.plan.conversation_id:
            attributes["gen_ai.conversation.id"] = row.conversation_id or self.plan.conversation_id
        call = self.open(
            "llm_call",
            actor="target",
            name=f"chat {row.model}",
            at=planned.start,
            attributes=attributes,
            facts=facts,
            parent=turn.handle,
        )
        for fact in facts:
            self.lack(fact, call.span_id, reasons[fact])
        if row.request_body is not None:
            marker = {"truncated": True} if planned.request_cut else {}
            call.handle.event("request", actor="target", body=dict(row.request_body), **marker)

        self.at(planned.end, call, "end_time")
        response = row.response_body or {}
        if row.response_body is not None:
            marker = {"truncated": True} if planned.response_cut else {}
            call.handle.event("response", actor="target", body=dict(row.response_body), **marker)
        end: dict[str, Any] = {}
        if row.prompt_tokens is not None:
            end["gen_ai.usage.input_tokens"] = row.prompt_tokens
        if row.completion_tokens is not None:
            end["gen_ai.usage.output_tokens"] = row.completion_tokens
        if response.get("model"):
            end["gen_ai.response.model"] = response["model"]
        if response.get("id"):
            end["gen_ai.response.id"] = response["id"]
        if (finish := _finish_reason(response)) is not None:
            end["gen_ai.response.finish_reasons"] = [finish]
        if row.cache_read_tokens is not None:
            end["gen_ai.usage.cache_read.input_tokens"] = row.cache_read_tokens
        if row.cache_write_tokens is not None:
            end["gen_ai.usage.cache_write.input_tokens"] = row.cache_write_tokens
        end.update(_costs(row))
        if row.status != 200:
            message = _error_message(response, row.status)
            call.handle.event("error", actor="target", error_type=str(row.status), message=message)
            end["error.type"] = str(row.status)
            call.end(status="error", attributes=end, error=(str(row.status), message))
        else:
            call.end(attributes=end)
        if closes_turn and planned.reply:
            marker = {"truncated": True} if planned.response_cut else {}
            turn.handle.event(
                "message", actor="target", role="assistant", content=planned.reply, **marker
            )
        return [self.open_tool(call, use, planned.end) for use in planned.tools]

    def open_tool(self, call: Marked, use: ToolUse, at: int) -> _Pending:
        attributes: dict[str, Any] = {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": use.name,
        }
        if use.call_id is not None:
            attributes["gen_ai.tool.call.id"] = use.call_id
        facts: list[NotObservedFact] = ["start_time", "end_time_exact"]
        if not use.arguments_observed:
            facts.append("arguments")
        kind = "retrieval" if self.importer.tool_kinds.get(use.name) == "retrieval" else "tool_call"
        span = self.open(
            kind,
            actor="target",
            name=f"execute_tool {use.name}",
            at=at,
            attributes=attributes,
            facts=facts,
            parent=call.handle,
        )
        self.lack("start_time", span.span_id, BETWEEN_ROWS)
        self.lack("end_time_exact", span.span_id, BETWEEN_ROWS)
        if not use.arguments_observed:
            self.lack("arguments", span.span_id, "the logged response does not hold them whole")
        span.handle.event(
            "tool/call",
            actor="target",
            tool=use.name,
            call_id=use.call_id,
            arguments=use.arguments if use.arguments_observed else None,
            not_observed=[] if use.arguments_observed else ["arguments"],
        )
        return _Pending(span, use, at)

    def close(self, pending: Sequence[_Pending], following: PlannedRow | None) -> None:
        """End the previous row's tool Spans at the following row's `created_at`, each
        answered by the matching tool message of that row's request, else marked `result`
        not observed; with no following row, at the response that asked for them."""
        next_start = following.start if following is not None else None
        answers = following.answers if following is not None else {}
        for item in pending:
            closing = next_start if next_start is not None else item.opened
            self.flows(item, closing)
            self.at(closing, item.span, "end_time")
            answer = answers.get(item.use.call_id) if item.use.call_id else None
            if answer is None:
                self.mark(
                    item.span,
                    "result",
                    "no later row carries a tool message with this call id"
                    if next_start is not None
                    else "no later row was logged",
                )
                item.span.end()
                continue
            item.span.handle.event(
                "tool/result",
                actor="target",
                tool=item.use.name,
                call_id=item.use.call_id,
                result=answer.content,
                is_error=answer.is_error,
            )
            if answer.is_error:
                item.span.end(
                    status="error",
                    attributes={"error.type": "tool_error"},
                    error=("tool_error", str(answer.content)),
                )
            else:
                item.span.end()

    def flows(self, item: _Pending, closing: int) -> None:
        """The Flow runs of this tool call: its name, a start in its window; equal
        arguments win a tie."""
        candidates = [
            index
            for index, planned_flow in enumerate(self.plan.flows)
            if index not in self.joined
            and planned_flow.flow.flow_id == item.use.name
            and item.opened <= planned_flow.start <= closing
        ]
        candidates.sort(
            key=lambda index: self.plan.flows[index].flow.arguments != item.use.arguments
        )
        for index in candidates[:1]:
            self.joined.add(index)
            self.flow_span(item, self.plan.flows[index], closing)

    def flow_span(self, item: _Pending, planned_flow: PlannedFlow, closing: int) -> None:
        flow = planned_flow.flow
        span = self.open(
            "flow",
            actor="target",
            name=f"flow {flow.flow_id}",
            at=planned_flow.start,
            attributes={FLOW_ID: flow.flow_id, FLOW_JOINED_BY: "time"},
            facts=[] if planned_flow.end is not None else ["end_time"],
            parent=item.span.handle,
        )
        span.handle.event(
            "flow/run",
            actor="target",
            flow=flow.flow_id,
            status=flow.status,
            arguments=flow.arguments,
            steps=flow.steps,
        )
        if planned_flow.end is None:
            self.lack("end_time", span.span_id, "the Flow run records no end")
        self.at(planned_flow.end if planned_flow.end is not None else closing, span, "end_time")
        ok = flow.status.lower() in FLOW_OK
        span.end(
            status="ok" if ok else "error", attributes={} if ok else {"error.type": flow.status}
        )


class _TurnEmission(_Emission):
    """The rows of one live Turn written into the Trial's own Trace (decision 8): the same
    Spans an import writes, on the writer's clock, under the Turn's `response` Span."""

    def __init__(
        self,
        importer: ProxyRowImporter,
        plan: RowPlan,
        evidence: EvidenceRows,
        writer: TraceWriter,
    ) -> None:
        # No Recorder: the Trial's writer keeps the clock, and there is no Trace to open.
        self._init_state(importer, plan, evidence, "", [], [])
        self.writer = writer
        self.row: ProxyRow | None = None
        self.spans = 0

    def at(self, ms: int, span: Marked | None = None, fact: NotObservedFact = "start_time") -> None:
        """The writer's clock is the Trial's; an evidence instant moves nothing."""

    def open(
        self,
        kind: str,
        *,
        actor: Any,
        name: str,
        at: int,
        attributes: Mapping[str, Any],
        facts: Sequence[NotObservedFact],
        parent: SpanHandle | None,
    ) -> Marked:
        row = self.row
        if row is None:
            raise ImportRowsError("a reconstructed Span was opened before any row was read")
        opened: list[NotObservedFact] = list(dict.fromkeys([*facts, "start_time", "end_time"]))
        marked: dict[str, Any] = {
            **attributes,
            EVIDENCE_STORE: self.importer.kind,
            EVIDENCE_REQUEST_ID: row.request_id,
            EVIDENCE_CREATED_AT: row.created_at,
            EVIDENCE_LATENCY_MS: row.latency_ms,
            NOT_OBSERVED: opened,
        }
        handle = self.writer.span(
            kind,
            actor=actor,
            name=name,
            fidelity=self.importer.fidelity,
            attributes=marked,
            parent=parent,
        )
        self.spans += 1
        span = Marked(handle, opened)
        self.lack("start_time", span.span_id, LIVE_CLOCK)
        self.lack("end_time", span.span_id, LIVE_CLOCK)
        return span

    def run_turn(self, parent: SpanHandle) -> None:
        """Each kept row's `llm_call` and its tools under `parent`, in `created_at` order."""
        turn = Marked(parent, [])
        pending: list[_Pending] = []
        for planned in self.plan.rows:
            self.close(pending, planned)
            self.row = planned.row
            pending = self.model_call(turn, planned, False)
        self.close(pending, None)
        for index, planned_flow in enumerate(self.plan.flows):
            if index not in self.joined:
                self.lack(
                    "flow_run",
                    planned_flow.flow.flow_id,
                    "no call of a tool of that name was open when the Flow run started",
                )


__all__ = ["ProxyRowImporter", "Reconstructed"]
