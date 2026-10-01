"""One Trial, told as a story: the view model and the terminal rendering (D40, D11).

D40 asks for one renderer over three surfaces, so this module splits in two. `trial_story`
reads a Trial's files and projects a `TrialStory`, and `render_story` turns that story into
terminal text without touching the filesystem. Ticket 15's HTML Report and the Phase 7 UI
render the same `TrialStory`, so the three surfaces cannot disagree about what happened.
Ticket 15 gave the view model what a flame graph needs and the terminal does not print:
each `SpanView`'s offsets from the Trace's first Event and its depth, each Turn's own Span,
and the `message` Events as `MessageView`s. `render_story` reads none of them, and
`tests/test_show_pin.py` holds its output to the byte.

The view model is a projection and nothing else (D11): every number in it is computed from
the Events by `project_spans` and `metrics`, never read from `scores.json` or `run.json`.
Those two files contribute only what the Events cannot know — who the Target was, what the
Adapter is, what the Judge decided — so a Trial with nothing but a `trace.jsonl` still
tells its whole story.

The Diagnosis (D24) is printed after the Scores, under its own heading and never among
them: it is the Judge's narrative about why the Verdicts came out as they did, read from
the `note` Event with `about: "diagnosis"` in `judgement.jsonl`, and a Trial whose
Diagnosis failed says that none was produced rather than printing nothing. When any Score's
Judge resolved to the Target's own model, the Scores carry the self-preference warning the
Scorecard prints (D23). When the Judge cited more than a bare Span id and agentdiag read the
cite as the id it begins with (decision 37), the Score and the Diagnosis say so on a `cites
read` line, quoting the cites as written, and the Scores heading counts them (ticket 21,
decision 39); a Trial whose cites were all bare ids prints neither.

A Run that found Sync broken and re-synced prints, under the header's `Sync broken`, one
`Re-synced from <fingerprint>:` line naming each section that moved, its direction and its
change (phase-6 decision 14), so a re-sync is never silent (ADR-0008).

Cost is printed where it was recorded: on each model Span, read from the attribute the
Adapter or the Judge wrote at capture (phase-5 decision 4), and summed per actor under the
header. A model the price table does not price is `unpriced`, never `$0`. A tool Span's id
names its kind — `retrieval-1` for a lookup the Manifest marks so, `tool_call-1` for an
action — so the kind is on every line that names the Span. The first model Span of a
Claude Code session also prints the CLI's start-up beside its duration (ticket 20,
decision 36), because that duration encloses the start-up as well as the model's answer.

A simulated Trial (ticket 06, phase-5 decision 60) prints the Simulated User's `simulate`
Span, and the stop checks agentdiag ran between Turns, before the Turn they precede — the
check that ended the Trial after the last Turn — so the Turns stay the Target's alone; a
simulated Turn's user line reads `simulated`, the Termination line says what ended the
conversation, and a Score the reviewer rewrote prints its fault with its direction.

What `show` prints never hides what the file holds (ADR-0004 §6). A request body is the
one exception: it is summarised to its model and its stop reason, because the body is the
Trace's bulk and `show --json` is one flag away. Everything else — tool arguments, tool
results, messages, errors, notes — prints in full.
"""

from __future__ import annotations

import json
import shutil
import textwrap
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

from agentdiag.eval.score import SHARED_MODEL_WARNING, read_as, score_name
from agentdiag.run.locate import (
    JUDGEMENT_FILE,
    NOT_FOUND_EXIT,
    RUN_RECORD_FILE,
    SCORES_FILE,
    TRACE_FILE,
    TrialNotFound,
    locate_run,
    require_trace,
    trace_path,
    trial_dir,
)
from agentdiag.sync.compare import resynced_line
from agentdiag.trace.attributes import (
    BACKEND_STARTUP_MS,
    COST_CHECK,
    JUDGE_ATTEMPTS,
    REPORTED_COST_USD,
    TIME_TO_FIRST_FRAME_MS,
)
from agentdiag.trace.events import Event, to_line
from agentdiag.trace.reader import follow, read_trace, resolve_blobs
from agentdiag.trace.spans import (
    MODEL_SPAN_KINDS,
    RESPONSE_KIND,
    ActorTotals,
    Metrics,
    Span,
    is_own_span,
    metrics,
    not_observed,
    project_spans,
    trace_totals,
)
from agentdiag.types import STRUCTURED_OUTPUT, Actor, CostCheck, Fidelity

INDENT = "  "
NOT_OBSERVED = "<not observed>"
"""What an Adapter could not see. Never `{}`: an empty map is a different claim (D6)."""

FOLLOW_WAIT_SECONDS = 30.0
"""How long `--follow` waits for a `judgement.jsonl` after the Trace ends."""

FOLLOW_POLL_MS = 100

FALLBACK_WIDTH = 100
"""The width to fold into when there is no terminal to ask, as in a pipe or a test."""

MIN_TEXT_WIDTH = 20
"""Deeply indented text still gets a usable column rather than one word per line."""

UNPRICED = "unpriced"
"""What a model Span with no recorded cost prints: its model is not in the price table."""


# --- the view model (a projection; never persisted, never in schemas/) ---


class SpanView(BaseModel):
    """One Span as a reader meets it: what it was, how long it took, what happened in it."""

    span_id: str
    kind: str
    name: str
    actor: Actor
    fidelity: Fidelity
    duration_ms: int | None
    model_latency_ms: int | None
    startup_ms: int | None = None
    """The Claude Code CLI's start-up inside this Span, on a session's first model call only
    (ticket 20, decision 36): printed beside the duration so it is not read as the model's."""

    tokens: dict[str, int] = Field(default_factory=dict)
    status: Literal["ok", "error", "open"]
    not_observed: list[str] = Field(default_factory=list)
    """What the Span's evidence could not give (ADR-0006 §4), on an imported Span."""

    stop_reason: str | None = None
    cost_usd: float | None = None
    """What a model Span cost, as recorded on it; None when unpriced or not a model Span."""

    lines: list[str] = Field(default_factory=list)
    """What happened inside this Span, in the order the Events recorded it."""

    holds: bool | None = None
    """A `stop_when judged` check's answer, read from its `note` (decision 60); None for
    every other Span."""

    children: list[SpanView] = Field(default_factory=list)

    start_ms: int
    end_ms: int | None
    """Where the Span sits in its Trace, in milliseconds from the Trace's first Event;
    `end_ms` None while it is open (ADR-0004 §2). The flame graph's x (phase-7 decision
    19); the terminal text prints the duration and never these."""

    depth: int
    """How many Spans enclose this one: 0 for a Turn's own Span and every other top-level
    Span, 1 for what a Turn opened. The flame graph's y."""

    attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    """Every attribute the Span carries, a scalar as recorded and anything else as its first
    200 characters of text: what hovering a Span in the Report shows (ticket 15). The
    terminal text never prints it; `show --json` has the Events whole."""


class StopNoteView(BaseModel):
    """A stop check that ran outside any Span (`tool_called`, `target_says_any`): what it
    checked and whether it held (phase-5 decision 47)."""

    predicate: str
    holds: bool
    text: str


BetweenTurns = SpanView | StopNoteView
"""What agentdiag and the Simulated User did between two Turns (decision 60)."""


class TurnView(BaseModel):
    """One user message and the Target's complete response to it (`CONTEXT.md`, Turn)."""

    index: int
    user: str | None = None
    simulated: bool = False
    """The user message is the Simulated User's (D27), not the Scenario's author's."""

    assistant: str | None = None
    operator: bool = False
    """The reply is a staff member's (Actor `operator`), an imported takeover Turn (phase-6
    decision 35), not the Target's."""

    duration_ms: int | None = None
    spans: list[SpanView] = Field(default_factory=list)
    before: list[BetweenTurns] = Field(default_factory=list)
    """The top-level Spans and stop notes that precede this Turn: the stop check after the
    previous Turn, and the `simulate` Span that authored this one (decision 60)."""

    after: list[BetweenTurns] = Field(default_factory=list)
    """What ran after the last Turn: the stop check that ended the Trial."""

    span: SpanView | None = None
    """The Turn's own `turn` Span, childless because its children are `spans`: what the
    flame graph draws as the Turn's bar. None for the Turn of index 0 that holds Spans
    outside every Turn."""


class MessageView(BaseModel):
    """One `message` Event: who said what, when, in which Turn (phase-7 decision 19).

    The ladder interleaves these with the Spans in Event order; `at_ms` counts from the
    Trace's first Event, as a Span's `start_ms` does.
    """

    seq: int
    at_ms: int
    turn: int | None
    actor: Actor
    role: str | None
    text: str


class JudgeView(BaseModel):
    """One judged Eval's model call, as `judgement.jsonl` recorded it (ADR-0004 §3)."""

    span_id: str
    eval: str
    requested_model: str
    resolved_model: str | None = None
    duration_ms: int | None = None
    tokens: dict[str, int] = Field(default_factory=dict)
    cost_usd: float | None = None
    reported_cost_usd: float | None = None
    """What the backend said the call cost (`agentdiag.cost.reported_usd`, ticket 19); None
    when it reports nothing, as the Messages API does."""

    cost_check: CostCheck | None = None
    """The reported cost against the price table: `agrees`, `differs` or `unpriced`."""

    attempts: int | None = None
    """How many attempts the Claude Code CLI made at a structured answer, when more than one
    (`agentdiag.judge.attempts`, decision 42); None for a call answered at once."""

    status: Literal["ok", "error", "open"]
    error: str | None = None
    note: str | None = None
    prompt_chars: int | None = None
    """The size of the rendered prompt, never its text: the text is in the file."""


class ScoreView(BaseModel):
    """One Score as `scores.json` wrote it, flattened for printing (ADR-0003 §4)."""

    eval: str
    eval_id: str | None = None
    verdict: str
    reason: str | None = None
    rationale: str = ""
    evidence: list[str] = Field(default_factory=list)
    fault_source: str | None = None
    fault_direction: str | None = None
    shared_model: bool | None = None
    cites_read: list[str] = Field(default_factory=list)
    """The cites read as the Span id they begin with, as the Judge wrote them (decision 39)."""


class DiagnosisView(BaseModel):
    """The Trial's Diagnosis as `judgement.jsonl` recorded it (D24). Never a Score."""

    span_id: str | None = None
    text: str
    cites: list[str] = Field(default_factory=list)
    cites_read: list[str] = Field(default_factory=list)
    sections: list[int] = Field(default_factory=list)


class TrialStory(BaseModel):
    """Everything one Trial has to say, in the order a reader meets it.

    Optional throughout on purpose: a Trial in progress has a Trace and nothing else, and
    `show` is most useful exactly then.
    """

    run_id: str
    scenario_id: str
    trial: int
    sync: dict[str, Any] = Field(default_factory=dict)
    adapter: dict[str, Any] = Field(default_factory=dict)
    judge_backend: dict[str, Any] = Field(default_factory=dict)
    """`run.json.judge.backend`; the Target's is `adapter.backend` (phase-5 decision 44)."""
    target: str | None = None
    target_model: str | None = None
    termination: str | None = None
    """None while the Trial is still running: no `trace/end` has been written."""

    termination_detail: str | None = None
    """What ended the conversation, in words, when `trace/end` says (decision 47)."""

    continues: str | None = None
    """The Scenario whose Adapter session this Trial resumed (phase-5 decision 13)."""

    imported: str | None = None
    """Where an imported Trace's evidence came from, from its `import/source` Event (ticket
    26): the store, the rows and what the evidence lacked, said before the first Turn."""

    rescored_from: str | None = None
    """The Run whose Trace this Trial's Scores read, when the Run is a rescore (D34)."""

    provenance: str | None = None
    notes: str | None = None
    ground_truth: Any = None
    """The three Scenario fields a reader needs beside the Trace, from `run.json` (D18)."""

    duration_ms: int | None = None
    tokens: dict[str, int] = Field(default_factory=dict)
    costs: dict[str, ActorTotals] = Field(default_factory=dict)
    """Per actor with a model Span: the Target's from the Trace, the Judge's from its file."""

    fixtures: list[str] = Field(default_factory=list)
    turns: list[TurnView] = Field(default_factory=list)
    open_spans: list[str] = Field(default_factory=list)
    """Spans with a `span/start` and no `span/end` (ADR-0004 §2: shown as open)."""

    judgement: list[JudgeView] = Field(default_factory=list)
    scores: list[ScoreView] | None = None
    """None when `scores.json` is not written yet — different from a Trial with no Evals."""

    diagnosis: DiagnosisView | None = None
    """None when the Trial judged nothing, so had no Diagnosis to give (D24)."""

    messages: list[MessageView] = Field(default_factory=list)
    """Every `message` Event in the Trace, in order: the ladder's interleaved lines."""


# --- locating what to read ---
#
# `locate_run`, `trial_dir`, `trace_path`, `require_trace` and `TrialNotFound` live in
# `agentdiag.run.locate`: finding a Trial is its own reason to change, so `export`
# can find one without importing this renderer. Re-exported here for the callers that
# already ask `show` for them.


# --- the projection ---


def trial_story(run_dir: Path, scenario_id: str, trial: int = 1) -> TrialStory:
    """One Trial's whole story, projected over its files (D11, D40).

    The Trace is required; `run.json`, `judgement.jsonl` and `scores.json` each add what
    they know and are each optional, because a Trial in progress has none of them.
    """
    run_dir = Path(run_dir)
    path = require_trace(run_dir, scenario_id, trial)
    events = resolve_blobs(read_trace(path))
    story = story_from_events(events, run_dir=run_dir, scenario_id=scenario_id, trial=trial)

    judgement = trial_dir(run_dir, scenario_id, trial) / JUDGEMENT_FILE
    if judgement.exists():
        judge_events = resolve_blobs(read_trace(judgement))
        story.judgement = judge_views(judge_events)
        story.diagnosis = diagnosis_view(judge_events)
        story.costs.update(actor_costs(project_spans(judge_events), judge_events))

    scores = trial_dir(run_dir, scenario_id, trial) / SCORES_FILE
    if scores.exists():
        story.scores = score_views(json.loads(scores.read_text(encoding="utf-8")))
    return story


def story_from_events(
    events: Sequence[Event],
    *,
    run_dir: Path | None = None,
    scenario_id: str | None = None,
    trial: int | None = None,
) -> TrialStory:
    """The part of the story the Trace alone can tell.

    Separated from `trial_story` so `--follow` can build a story out of the Events it has
    so far without waiting for a file that may never arrive.
    """
    start = _first(events, "trace/start")
    end = _first(events, "trace/end")
    header = start.model_extra or {} if start else {}
    spans = project_spans(events)
    story = TrialStory(
        run_id=str(header.get("run") or (Path(run_dir).name if run_dir else "unknown")),
        scenario_id=str(header.get("scenario") or scenario_id or "unknown"),
        trial=int(header.get("trial") or trial or 1),
        termination=str((end.model_extra or {}).get("termination")) if end else None,
        termination_detail=_optional_str((end.model_extra or {}).get("detail")) if end else None,
        continues=_optional_str(header.get("continues")),
        imported=_imported_line(_first(events, "import/source")),
        duration_ms=_trace_duration_ms(events),
        tokens=_sum_tokens(spans),
        costs=actor_costs(spans, events),
        fixtures=_fixture_lines(events),
        turns=_turn_views(spans, events),
        open_spans=[span.span_id for span in spans if span.end_ms is None],
        messages=_message_views(events),
    )
    if run_dir is not None:
        _add_run_record(story, Path(run_dir))
    return story


def _imported_line(event: Event | None) -> str | None:
    """`Imported from the proxy store: 4 rows, lacking end_time_exact (4), …` (ticket 26)."""
    if event is None:
        return None
    fields = event.model_extra or {}
    counted: dict[str, int] = {}
    for entry in fields.get("lacked") or []:
        if isinstance(entry, Mapping):
            name = str(entry.get("field"))
            counted[name] = counted.get(name, 0) + 1
    lacking = ", ".join(f"{name} ({count})" for name, count in counted.items()) or "nothing"
    store, rows = fields.get("store"), fields.get("rows")
    return f"Imported from the {store} store: {rows} rows, lacking {lacking}"


def actor_costs(spans: Sequence[Span], events: Sequence[Event]) -> dict[str, ActorTotals]:
    """The actors whose model Spans cost something or had no price (decision 4)."""
    return {
        actor: totals
        for actor, totals in trace_totals(spans, events).actors.items()
        if totals.cost_usd or totals.unpriced
    }


def _add_run_record(story: TrialStory, run_dir: Path) -> None:
    """What only `run.json` knows: the Target, the Adapter, the Backends and the Sync
    result (D29, phase-5 decision 44)."""
    path = run_dir / RUN_RECORD_FILE
    if not path.exists():
        return
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    story.sync = dict(record.get("sync") or {})
    story.adapter = dict(record.get("adapter") or {})
    story.judge_backend = dict((record.get("judge") or {}).get("backend") or {})
    rescored_from = _optional_str(record.get("traces_from"))
    if rescored_from is not None:
        # The Trace names the Run that recorded it; the story is this Run's judgement of it.
        story.rescored_from = rescored_from
        story.run_id = str(record.get("run_id") or run_dir.name)
    manifest = record.get("manifest") or {}
    target = manifest.get("target") or {}
    story.target = target.get("name")
    story.target_model = story.adapter.get("config", {}).get("model")
    summary = next(
        (
            entry
            for entry in record.get("scenarios") or []
            if isinstance(entry, dict) and entry.get("id") == story.scenario_id
        ),
        {},
    )
    story.provenance = _optional_str(summary.get("provenance"))
    story.notes = _optional_str(summary.get("notes"))
    story.ground_truth = summary.get("ground_truth")


def _origin(events: Sequence[Event]) -> int:
    """The instant every offset in the story counts from: the Trace's first Event."""
    return events[0].ts if events else 0


def _message_views(events: Sequence[Event]) -> list[MessageView]:
    origin = _origin(events)
    views: list[MessageView] = []
    for event in events:
        if event.type != "message":
            continue
        fields = event.model_extra or {}
        views.append(
            MessageView(
                seq=event.seq,
                at_ms=event.ts - origin,
                turn=event.turn,
                actor=event.actor,
                role=_optional_str(fields.get("role")),
                text=_text_of(fields.get("content")),
            )
        )
    return views


def _turn_views(spans: Sequence[Span], events: Sequence[Event]) -> list[TurnView]:
    """One `TurnView` per `turn` Span, with the Spans inside it as a tree.

    A Span outside every Turn is not lost. The Judge's are read from `judgement.jsonl`
    (`judge_views`). The Simulated User's `simulate` Span, agentdiag's stop checks and their
    top-level notes go before the Turn they precede, or after the last one (decision 60).
    Anything else — an Adapter's Span opened before the first Turn — lands in a Turn of
    index 0, so a `cat` and a `show` still agree.
    """
    by_parent: dict[str | None, list[Span]] = {}
    for span in spans:
        by_parent.setdefault(span.parent_span_id, []).append(span)
    origin = _origin(events)

    def view(span: Span, depth: int) -> SpanView:
        return _span_view(span, by_parent, events, origin=origin, depth=depth)

    ordered: list[tuple[int, Span | StopNoteView]] = [
        (min(span.events) if span.events else 0, span)
        for span in by_parent.get(None, [])
        if span.actor != "judge"
    ]
    ordered.extend(
        (event.seq, _stop_note(event)) for event in events if _is_top_level_stop_note(event)
    )
    ordered.sort(key=lambda item: item[0])

    turns: list[TurnView] = []
    orphans: list[SpanView] = []
    pending: list[BetweenTurns] = []
    for _, item in ordered:
        if isinstance(item, StopNoteView):
            pending.append(item)
        elif item.kind == "turn":
            user = _message(events, item.span_id, "user")
            reply = _message(events, item.span_id, "assistant")
            staff = _message(events, item.span_id, "operator") if reply is None else None
            turns.append(
                TurnView(
                    index=item.turn or len(turns) + 1,
                    user=_content(user),
                    simulated=user is not None and user.actor == "simulated_user",
                    assistant=_content(reply or staff),
                    operator=staff is not None,
                    duration_ms=(item.end_ms - item.start_ms) if item.end_ms is not None else None,
                    spans=[view(child, 1) for child in by_parent.get(item.span_id, [])],
                    before=pending,
                    span=_span_view(item, {}, events, origin=origin, depth=0),
                )
            )
            pending = []
        elif is_own_span(item):
            # A `simulate` Span or a stop check's `llm_call` (decision 60).
            pending.append(view(item, 0))
        else:
            orphans.append(view(item, 0))
    if pending and turns:
        turns[-1].after = pending
    if orphans or (pending and not turns):
        turns.insert(0, TurnView(index=0, spans=orphans, after=pending if not turns else []))
    return turns


def _is_top_level_stop_note(event: Event) -> bool:
    fields = event.model_extra or {}
    return event.type == "note" and event.span_id is None and fields.get("about") == STOP_WHEN


def _stop_note(event: Event) -> StopNoteView:
    fields = event.model_extra or {}
    return StopNoteView(
        predicate=str(fields.get("predicate") or ""),
        holds=bool(fields.get("holds")),
        text=str(fields.get("text") or ""),
    )


def _span_view(
    span: Span,
    by_parent: Mapping[str | None, list[Span]],
    events: Sequence[Event],
    *,
    origin: int,
    depth: int,
) -> SpanView:
    measured = metrics(span, events)
    children = [
        _span_view(child, by_parent, events, origin=origin, depth=depth + 1)
        for child in by_parent.get(span.span_id, [])
    ]
    tokens, cost = measured.tokens, measured.cost_usd
    if span.kind == SIMULATE_KIND:
        # The Simulated User's authoring carries what its model calls used and cost, so its
        # one line reads as a Span of its own (decision 60).
        tokens, cost = _summed(children)
    return SpanView(
        span_id=span.span_id,
        kind=span.kind,
        name=span.name,
        actor=span.actor,
        fidelity=span.fidelity,
        duration_ms=measured.duration_ms,
        model_latency_ms=measured.model_latency_ms,
        startup_ms=_optional_int(span.attributes.get(BACKEND_STARTUP_MS)),
        tokens=tokens,
        status=span.status,
        not_observed=not_observed(span),
        stop_reason=_stop_reason(span),
        cost_usd=cost,
        lines=_span_lines(span, events),
        holds=_holds(span, events),
        children=children,
        start_ms=span.start_ms - origin,
        end_ms=None if span.end_ms is None else span.end_ms - origin,
        depth=depth,
        attributes={key: _scalar(value) for key, value in span.attributes.items()},
    )


ATTRIBUTE_CHARS = 200
"""How much of a non-scalar attribute a `SpanView` keeps: enough to recognise on hover."""


def _scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)[:ATTRIBUTE_CHARS]


SIMULATE_KIND = "simulate"
"""The Span the Simulated User authors a message in (phase-5 decision 47)."""


def _summed(views: Sequence[SpanView]) -> tuple[dict[str, int], float | None]:
    """The tokens and the cost of some Spans together; the cost None when none was priced."""
    tokens: dict[str, int] = {}
    cost: float | None = None
    for view in views:
        for key, value in view.tokens.items():
            tokens[key] = tokens.get(key, 0) + value
        if view.cost_usd is not None:
            cost = round((cost or 0.0) + view.cost_usd, 6)
    return tokens, cost


def _holds(span: Span, events: Sequence[Event]) -> bool | None:
    """What a stop check's own Span answered, from the `note` inside it (decision 60)."""
    for event in events:
        fields = event.model_extra or {}
        if (
            event.type == "note"
            and event.span_id == span.span_id
            and fields.get("about") == STOP_WHEN
        ):
            return bool(fields.get("holds"))
    return None


def _span_lines(span: Span, events: Sequence[Event]) -> list[str]:
    """What happened inside one Span, one printable line per Event that says something.

    `span/start`, `span/end` and `blob` say nothing a reader needs here: the first two are
    the Span itself, and a blob's content appears where it is referenced.
    """
    lines: list[str] = []
    for event in events:
        if event.span_id != span.span_id:
            continue
        line = _event_line(event)
        if line is not None:
            lines.append(line)
    return lines


def _event_line(event: Event) -> str | None:
    """One Event as the story prints it, or None when the Span line already says it."""
    fields = event.model_extra or {}
    if event.type == "tool/call":
        return f"→ {fields.get('tool')} {_arguments(fields)}"
    if event.type == "tool/result":
        marker = "← error" if fields.get("is_error") else "←"
        return f"{marker} {_compact(fields.get('result'))}"
    if event.type == "response":
        text = _assistant_text(fields.get("body"))
        return f"text  {text}" if text else None
    if event.type == "request":
        return f"request  {_request_summary(fields.get('body'))}"
    if event.type == "error":
        return f"error {fields.get('error_type')}: {fields.get('message')}"
    if event.type == "note":
        about = fields.get("about")
        return f"note  {fields.get('text')}" + (f" (about {about})" if about else "")
    if event.type == "message":
        return None
    if event.type in {"span/start", "span/end", "blob", "trace/start", "trace/end"}:
        return None
    return f"{event.type}  {_compact({k: v for k, v in fields.items() if k != 'attributes'})}"


def _arguments(fields: Mapping[str, Any]) -> str:
    """A tool's arguments in full, or `<not observed>` when the Adapter could not see them."""
    if "arguments" in (fields.get("not_observed") or []):
        return NOT_OBSERVED
    arguments = fields.get("arguments")
    return NOT_OBSERVED if arguments is None else _compact(arguments)


def _request_summary(body: Any) -> str:
    """The one thing `show` summarises: model and message count; the body is in `--json`.

    The stop reason is deliberately absent here: it belongs to the response, and
    `_span_facts` already prints it on the enclosing Span's own line, where a reader
    scanning the column finds it. Repeating it would say the same fact twice.
    """
    if not isinstance(body, Mapping):
        return _compact(body)
    messages = body.get("messages")
    count = len(messages) if isinstance(messages, list) else 0
    tools = body.get("tools")
    # An imported proxy row's body may name no model; the Span's name does.
    parts = [str(body["model"])] if body.get("model") else []
    parts.append(f"{count} messages")
    if isinstance(tools, list) and tools:
        parts.append(f"{len(tools)} tools")
    return ", ".join(parts) + "  (full body in --json)"


def _assistant_text(body: Any) -> str | None:
    """The text the model wrote, concatenated; None when it only asked for tools."""
    if not isinstance(body, Mapping):
        return None
    blocks = body.get("content")
    if not isinstance(blocks, list):
        return None
    text = "".join(
        str(block.get("text", ""))
        for block in blocks
        if isinstance(block, Mapping) and block.get("type") == "text"
    )
    return text or None


def _stop_reason(span: Span) -> str | None:
    reasons = span.attributes.get("gen_ai.response.finish_reasons")
    if isinstance(reasons, list) and reasons:
        return str(reasons[0])
    return None


def _message(events: Sequence[Event], span_id: str, role: str) -> Event | None:
    """The `message` Event of `role` inside a Turn: the one lookup for both of its messages."""
    for event in events:
        fields = event.model_extra or {}
        if event.type == "message" and event.span_id == span_id and fields.get("role") == role:
            return event
    return None


def _content(message: Event | None) -> str | None:
    return None if message is None else str((message.model_extra or {}).get("content"))


def _fixture_lines(events: Sequence[Event]) -> list[str]:
    lines = []
    for event in events:
        if event.type != "fixture/applied":
            continue
        fields = event.model_extra or {}
        lines.append(f"{fields.get('fixture')} via {fields.get('mechanism')}")
    return lines


def _trace_duration_ms(events: Sequence[Event]) -> int | None:
    start = _first(events, "trace/start")
    end = _first(events, "trace/end")
    if start is None or end is None:
        return None
    return end.ts - start.ts


def _sum_tokens(spans: Sequence[Span]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for span in spans:
        for key, value in _span_tokens(span).items():
            totals[key] = totals.get(key, 0) + value
    return totals


def _span_tokens(span: Span) -> dict[str, int]:
    """One Span's token counts, from its own attributes (ADR-0006 §4)."""
    return metrics(span, []).tokens


def _first(events: Sequence[Event], type: str) -> Event | None:
    for event in events:
        if event.type == type:
            return event
    return None


# --- the Judge's own file, and the Scores it produced ---


def judge_views(events: Sequence[Event]) -> list[JudgeView]:
    """One `JudgeView` per `judge` Span in a `judgement.jsonl` (ADR-0004 §3).

    Every Judge Span is a root, so there is no nesting to rebuild here — only the error
    and the note Events beside each one, which are what a reader needs when a Score came
    back `invalid`.
    """
    views: list[JudgeView] = []
    for span in project_spans(events):
        if span.kind != "judge":
            continue
        measured: Metrics = metrics(span, events)
        views.append(
            JudgeView(
                span_id=span.span_id,
                eval=str(span.attributes.get("agentdiag.eval") or span.name),
                requested_model=str(span.attributes.get("gen_ai.request.model") or "unknown"),
                resolved_model=_optional_str(span.attributes.get("gen_ai.response.model")),
                duration_ms=measured.duration_ms,
                tokens=measured.tokens,
                cost_usd=measured.cost_usd,
                reported_cost_usd=_optional_float(span.attributes.get(REPORTED_COST_USD)),
                cost_check=_cost_check(span.attributes.get(COST_CHECK)),
                attempts=_optional_int(span.attributes.get(JUDGE_ATTEMPTS)),
                status=span.status,
                error=_judge_error(span, events),
                note=_judge_note(span, events),
                prompt_chars=_prompt_chars(span, events),
            )
        )
    return views


def _judge_error(span: Span, events: Sequence[Event]) -> str | None:
    """What went wrong, from the `error` Event or from the `span/end`'s own error."""
    for event in events:
        fields = event.model_extra or {}
        if event.type == "error" and event.span_id == span.span_id:
            return f"{fields.get('error_type')}: {fields.get('message')}"
        if event.type == "span/end" and event.span_id == span.span_id:
            error = fields.get("error")
            if isinstance(error, Mapping):
                return f"{error.get('type')}: {error.get('message')}"
    return None


def _judge_note(span: Span, events: Sequence[Event]) -> str | None:
    for event in events:
        fields = event.model_extra or {}
        if (
            event.type == "note"
            and event.span_id == span.span_id
            and fields.get("about") != DIAGNOSIS
        ):
            return str(fields.get("text"))
    return None


STOP_WHEN = "stop_when"
"""The `about` of a stop check's `note` (`agentdiag.eval.render.STOP_WHEN`), spelled here
for the reason `DIAGNOSIS` is below: `show` must not import the Judge."""

DIAGNOSIS = "diagnosis"
"""The `about` of the `note` a Diagnosis is written as: `agentdiag.eval.diagnosis.ABOUT`,
spelled again here on purpose. That module imports the Judge and so the Anthropic SDK, and
`show` reads a Run directory with neither; `tests/test_judged_run_cli.py` holds the two
spellings equal."""


def diagnosis_view(events: Sequence[Event]) -> DiagnosisView | None:
    """The Diagnosis `note` in a `judgement.jsonl`, or None when there is none (D24)."""
    for event in events:
        fields = event.model_extra or {}
        if event.type == "note" and event.actor == "judge" and fields.get("about") == DIAGNOSIS:
            return DiagnosisView(
                span_id=event.span_id,
                text=str(fields.get("text") or ""),
                cites=[str(span_id) for span_id in fields.get("cites") or []],
                cites_read=[str(cite) for cite in fields.get("cites_read") or []],
                sections=[int(section) for section in fields.get("sections") or []],
            )
    return None


def _prompt_chars(span: Span, events: Sequence[Event]) -> int | None:
    """How long the rendered prompt was; the prompt itself stays in `judgement.jsonl`."""
    for event in events:
        if event.type != "request" or event.span_id != span.span_id:
            continue
        body = (event.model_extra or {}).get("body")
        if not isinstance(body, Mapping):
            return None
        messages = body.get("messages")
        if not isinstance(messages, list):
            return None
        return sum(len(_text_of(message.get("content"))) for message in messages)
    return None


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_text_of(block) for block in content)
    if isinstance(content, Mapping):
        return str(content.get("text", ""))
    return ""


def score_views(payload: Mapping[str, Any]) -> list[ScoreView]:
    """`scores.json` as the story prints it. Verdicts are never recomputed here."""
    return [
        ScoreView(
            eval=str(score.get("eval")),
            eval_id=_optional_str(score.get("eval_id")),
            verdict=str(score.get("verdict")),
            reason=_optional_str(score.get("reason")),
            rationale=str(score.get("rationale") or ""),
            evidence=[str(span_id) for span_id in score.get("evidence") or []],
            fault_source=_optional_str(score.get("fault_source")),
            fault_direction=_optional_str(score.get("fault_direction")),
            shared_model=score.get("shared_model"),
            cites_read=[str(cite) for cite in score.get("cites_read") or []],
        )
        for score in payload.get("scores") or []
    ]


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _cost_check(value: Any) -> CostCheck | None:
    return value if value in get_args(CostCheck) else None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


# --- the terminal rendering (no file IO lives below this line) ---


def render_story(story: TrialStory, width: int | None = None) -> str:
    """The whole story as terminal text. A pure function of the view model (D40).

    `width` is the terminal to fold free text into; None asks the terminal itself. Only
    the free text wraps — a Span's own line keeps its measured tail where the eye expects
    it — and wrapping never cuts, so what a `cat` shows is still all here (ADR-0004 §6).
    """
    columns = width if width is not None else terminal_width()
    lines = _header_lines(story)
    lines.extend(_scenario_lines(story, columns))
    for turn in story.turns:
        lines.append("")
        lines.extend(_turn_lines(turn, columns))
    lines.extend(_judgement_lines(story, columns))
    lines.extend(_scores_lines(story, columns))
    lines.extend(_diagnosis_lines(story, columns))
    return "\n".join(lines)


def terminal_width() -> int:
    """How wide the terminal is, or 100 columns when there is no terminal to ask."""
    return shutil.get_terminal_size(fallback=(FALLBACK_WIDTH, 24)).columns


def _wrapped(pad: str, label: str, text: str, width: int) -> list[str]:
    """`<pad><label><text>`, folded so continuation lines start at the text column.

    Never truncates: a word longer than the remaining width (a URL, a compact JSON blob)
    overflows its line rather than losing characters, because the Trace is the source of
    truth and `show` must not hide what it holds.
    """
    column = len(pad) + len(label)
    room = max(width - column, MIN_TEXT_WIDTH)
    folded: list[str] = []
    for paragraph in text.splitlines() or [""]:
        folded.extend(
            textwrap.wrap(paragraph, width=room, break_long_words=False, break_on_hyphens=False)
            or [""]
        )
    head, *rest = folded
    return [f"{pad}{label}{head}", *(f"{' ' * column}{line}" for line in rest)]


def _header_lines(story: TrialStory) -> list[str]:
    """Who, what and how it ended — degrading to `unknown` rather than to silence."""
    lines = [f"Run {story.run_id} · Scenario {story.scenario_id} · Trial {story.trial}"]
    target = story.target or "unknown"
    model = f" ({story.target_model})" if story.target_model else ""
    lines.append(f"Target {target}{model} · Adapter {_adapter_text(story.adapter)}")
    backends = _backend_text(story.adapter.get("backend") or {}, story.judge_backend)
    if backends:
        lines.append(f"Backend {backends}")
    ended = _termination(story.termination or "running", story.termination_detail)
    lines.append(
        f"Sync {_sync_text(story.sync)} · Termination {ended}"
        f" · {_duration(story.duration_ms)} · {_tokens(story.tokens)}"
    )
    # ADR-0008: a Run that rebuilt its Fingerprint says which sections moved, up front.
    if (resynced := resynced_line(story.sync)) is not None:
        lines.append(resynced)
    if story.costs:
        lines.append(
            "Cost "
            + " · ".join(f"{actor} {_actor_cost(cost)}" for actor, cost in story.costs.items())
        )
    if story.rescored_from:
        lines.append(
            f"Rescored from Run {story.rescored_from} (its Trace, judged again in this Run)"
        )
    if story.continues:
        lines.append(f"Continues {story.continues} (the same Adapter session)")
    if story.imported:
        lines.append(story.imported)
    lines.extend(f"fixture {fixture}" for fixture in story.fixtures)
    if story.open_spans:
        lines.append(f"open Spans  {', '.join(story.open_spans)}")
    return lines


def _scenario_lines(story: TrialStory, width: int) -> list[str]:
    """What the Scenario says about itself: where it came from, what a pass proves, and
    the right answer, each in full (D18)."""
    lines: list[str] = []
    for label, value in (
        ("provenance", story.provenance),
        ("notes", story.notes),
        ("ground truth", story.ground_truth),
    ):
        if value is not None:
            lines.extend(_wrapped("", f"{label:<14}", _compact(value), width))
    return lines


def _adapter_text(adapter: Mapping[str, Any]) -> str:
    if not adapter:
        return "unknown"
    return (
        f"{adapter.get('kind', 'unknown')}, {adapter.get('fidelity', 'unknown')}, "
        f"side effects {adapter.get('side_effects', 'unknown')}"
    )


def _backend_text(target: Mapping[str, Any], judge: Mapping[str, Any]) -> str | None:
    """`target <kind>[ <version>] · judge <kind>[ <version>] (structured output <phrase>)`,
    each part only when `run.json` records it (phase-5 decision 44). The clause is the
    Judge's alone: the Target never asks for structured output."""
    parts: list[str] = []
    if target.get("kind"):
        parts.append(f"target {_backend_name(target)}")
    if judge.get("kind"):
        phrase = STRUCTURED_OUTPUT.get(judge["kind"])
        clause = f" (structured output {phrase})" if phrase else ""
        parts.append(f"judge {_backend_name(judge)}{clause}")
    return " · ".join(parts) or None


def _backend_name(backend: Mapping[str, Any]) -> str:
    version = backend.get("cli_version")
    return str(backend["kind"]) + (f" {version}" if version else "")


def _sync_text(sync: Mapping[str, Any]) -> str:
    if not sync:
        return "unknown"
    reason = sync.get("reason")
    return f"{sync.get('status')}" + (f" ({reason})" if reason else "")


def _turn_lines(turn: TurnView, width: int) -> list[str]:
    lines = _between_lines(turn.before, width)
    measured = f"    {_duration(turn.duration_ms)}" if turn.duration_ms is not None else ""
    lines.append(f"Turn {turn.index}{measured}")
    if turn.user is not None:
        lines.extend(_wrapped(INDENT, _user_label(turn.simulated), turn.user, width))
    for span in turn.spans:
        lines.extend(_span_lines_rendered(span, depth=1, width=width))
    if turn.assistant is not None:
        speaker = "operator  " if turn.operator else "target    "
        lines.extend(_wrapped(INDENT, speaker, turn.assistant, width))
    if turn.after:
        lines.append("")
        lines.extend(_between_lines(turn.after, width))
    return lines


def _between_lines(items: Sequence[BetweenTurns], width: int) -> list[str]:
    """The Simulated User's Spans and the stop checks between two Turns, unindented, so a
    reader sees they are not the Target's (decision 60)."""
    lines: list[str] = []
    for item in items:
        if isinstance(item, StopNoteView):
            lines.append(_stop_note_line(item.predicate, item.holds))
        else:
            lines.extend(_span_lines_rendered(item, depth=0, width=width))
    return lines


def _user_label(simulated: bool) -> str:
    """A user message's label: `simulated` for the Simulated User's (decision 60)."""
    return "simulated " if simulated else "user      "


def _termination(termination: str, detail: Any) -> str:
    """`stop_when (tool_called cancel_order held after Turn 3)`: the termination and, when
    `trace/end` says, what ended it (decision 47)."""
    return f"{termination} ({detail})" if detail else termination


def _stop_note_line(predicate: str, holds: bool) -> str:
    """`stop_when tool_called cancel_order: held`, or `: not held`."""
    return f"{STOP_WHEN} {predicate}: {'held' if holds else 'not held'}"


def _span_lines_rendered(span: SpanView, *, depth: int, width: int) -> list[str]:
    """A Span's own line, then what happened inside it, then its children.

    The Span line itself is not wrapped: its measured tail is a column a reader scans
    down. Everything inside it is free text, so it folds.
    """
    pad = INDENT * depth
    lines = [f"{pad}{span.span_id}  {span.name}{_span_facts(span)}"]
    for line in span.lines:
        label, text = _split_label(line)
        lines.extend(_wrapped(pad + INDENT, label, text, width))
    for child in span.children:
        lines.extend(_span_lines_rendered(child, depth=depth + 1, width=width))
    return lines


LINE_LABELS = ("← error ", "← ", "→ ", "text  ", "note  ", "error ", "request  ")
"""The openers `_event_line` writes, longest first, so a fold aligns under the text."""


def _split_label(line: str) -> tuple[str, str]:
    """One rendered line as its opener and its text, so continuations line up.

    `_event_line` joins the two because a Span's lines are also read whole (by the HTML
    Report, which does its own layout); only the terminal needs them apart.
    """
    for label in LINE_LABELS:
        if line.startswith(label):
            return label, line[len(label) :]
    return "", line


def _span_facts(span: SpanView) -> str:
    """The measured tail of a Span's line: how long, how many tokens, how it stopped."""
    parts = [_duration(span.duration_ms)]
    if span.model_latency_ms is not None:
        parts[0] = f"{parts[0]} (model {_duration(span.model_latency_ms)})"
    if span.startup_ms is not None:
        parts[0] = f"{parts[0]} (Claude Code start-up {_duration(span.startup_ms)})"
    if span.kind == RESPONSE_KIND:
        # An HTTP reply, drawn as an `llm_call` is (phase-8 decision 7): its observed
        # latency in the model's place, and no cost, since it shows no model call.
        first = span.attributes.get(TIME_TO_FIRST_FRAME_MS)
        if isinstance(first, int) and not isinstance(first, bool):
            parts[0] = f"{parts[0]} (first frame {_duration(first)})"
    if span.tokens:
        parts.append(_tokens(span.tokens))
    if span.kind in MODEL_SPAN_KINDS or span.kind == SIMULATE_KIND:
        parts.append(_cost(span.cost_usd))
    if span.stop_reason:
        parts.append(span.stop_reason)
    if span.holds is not None:
        parts.append(f"holds {'yes' if span.holds else 'no'}")
    if span.status == "error":
        parts.append("error")
    if span.not_observed:
        parts.append(f"not observed: {', '.join(span.not_observed)}")
    return "   " + " · ".join(parts)


def _judgement_lines(story: TrialStory, width: int = FALLBACK_WIDTH) -> list[str]:
    """Nothing at all when no judged Eval ran: an absent file is normal, not an error."""
    if not story.judgement:
        return []
    lines = ["", "Judgement"]
    for view in story.judgement:
        resolved = f" → {view.resolved_model}" if view.resolved_model else ""
        facts = [_duration(view.duration_ms)]
        if view.tokens:
            facts.append(_tokens(view.tokens))
        facts.append(_cost(view.cost_usd) + _reported(view))
        lines.append(
            f"{INDENT}{view.span_id}  {view.eval}  {view.requested_model}{resolved}"
            f"   {' · '.join(facts)}{_attempts(view.attempts)}"
        )
        if view.prompt_chars is not None:
            lines.extend(
                _wrapped(
                    INDENT * 2,
                    "",
                    f"prompt {view.prompt_chars:,} chars "
                    f"(rendered prompt and raw output are in {JUDGEMENT_FILE})",
                    width,
                )
            )
        if view.note:
            lines.extend(_wrapped(INDENT * 2, "note  ", view.note, width))
        if view.error:
            lines.extend(_wrapped(INDENT * 2, "error ", view.error, width))
    return lines


def _scores_lines(story: TrialStory, width: int = FALLBACK_WIDTH) -> list[str]:
    if story.scores is None:
        return ["", "Scores  (not yet written)"]
    read = sum(len(score.cites_read) for score in story.scores)
    lines = ["", f"Scores  ({read} {_cites(read)} read as Span ids)" if read else "Scores"]
    if not story.scores:
        lines.append(f"{INDENT}(this Scenario declared no Evals)")
    if any(score.shared_model for score in story.scores):
        lines.append(f"{INDENT}warning: {SHARED_MODEL_WARNING}")
    for score in story.scores:
        reason = f"  ({score.reason})" if score.reason else ""
        shared = f"  ({SHARED_MODEL_WARNING})" if score.shared_model else ""
        lines.append(
            f"{INDENT}{score_name(score.eval, score.eval_id)}  {score.verdict}{reason}{shared}"
        )
        if score.fault_source:
            # The direction beside its source when it has one: `simulated_user (helped)`.
            direction = (
                f" ({score.fault_direction})"
                if score.fault_direction and score.fault_direction != "none"
                else ""
            )
            lines.append(f"{INDENT * 2}fault      {score.fault_source}{direction}")
        if score.evidence:
            lines.extend(_wrapped(INDENT * 2, "evidence   ", ", ".join(score.evidence), width))
        if score.rationale:
            lines.extend(_wrapped(INDENT * 2, "rationale  ", score.rationale, width))
        if score.cites_read:
            lines.extend(_wrapped(INDENT * 2, CITES_READ, _read_text(score.cites_read), width))
    return lines


CITES_READ = "cites read "
"""The label of the line that says decision 37's fallback fired (ticket 21, decision 39),
as wide as the labels beside it so the text column stays one column."""


def _cites(count: int) -> str:
    return "cite" if count == 1 else "cites"


def _read_text(cites: Sequence[str]) -> str:
    """`N read as the Span ids they begin with: "<as written>", …`: the cites as the Judge
    wrote them, quoted, so a reader sees what it did instead of answering with bare ids.
    The wording is the rationale's (`score.read_as`)."""
    quoted = ", ".join(f'"{cite}"' for cite in cites)
    return f"{len(cites)} read as {read_as(len(cites), len(cites))}: {quoted}"


def _diagnosis_lines(story: TrialStory, width: int = FALLBACK_WIDTH) -> list[str]:
    """After the Scores and apart from them: the Judge's why, and what it rests on (D24)."""
    diagnosis = story.diagnosis
    if diagnosis is None:
        return []
    lines = ["", "Diagnosis  (the Judge's narrative; never a Score, never counted)"]
    lines.extend(_wrapped(INDENT, "", diagnosis.text, width))
    if diagnosis.cites:
        lines.extend(_wrapped(INDENT, "cites      ", ", ".join(diagnosis.cites), width))
    if diagnosis.cites_read:
        lines.extend(_wrapped(INDENT, CITES_READ, _read_text(diagnosis.cites_read), width))
    if diagnosis.sections:
        sections = ", ".join(str(section) for section in diagnosis.sections)
        lines.extend(_wrapped(INDENT, "sections   ", sections, width))
    return lines


def _cost(usd: float | None) -> str:
    """`$0.002094`, six decimals as recorded, or `unpriced` — never `$0` for an unknown."""
    return UNPRICED if usd is None else f"${usd:.6f}"


def _reported(view: JudgeView) -> str:
    """` reported $0.012300 (agrees)` after the Judge's cost, when the backend reported one."""
    if view.reported_cost_usd is None:
        return ""
    check = f" ({view.cost_check})" if view.cost_check else ""
    return f" reported {_cost(view.reported_cost_usd)}{check}"


def _attempts(attempts: int | None) -> str:
    """`   (2 attempts: Claude Code rejected the first)` after a judge Span the CLI
    re-prompted (decision 42); nothing at one attempt."""
    if attempts is None or attempts < 2:
        return ""
    first = "the first" if attempts == 2 else f"the first {attempts - 1}"
    return f"   ({attempts} attempts: Claude Code rejected {first})"


def _actor_cost(cost: ActorTotals) -> str:
    if cost.unpriced and not cost.cost_usd:
        return UNPRICED
    unpriced = f" + {cost.unpriced} {UNPRICED}" if cost.unpriced else ""
    return f"{_cost(cost.cost_usd)}{unpriced}"


def _duration(milliseconds: int | None) -> str:
    """`245 ms` under a second, `2.40 s` above it, `open` when the Span never closed."""
    if milliseconds is None:
        return "open"
    if milliseconds < 1000:
        return f"{milliseconds} ms"
    return f"{milliseconds / 1000:.2f} s"


def _tokens(tokens: Mapping[str, int]) -> str:
    named = [f"{key} {tokens[key]}" for key in ("input", "output") if key in tokens]
    summed = {"input", "output", "total"}
    extra = [f"{key} {value}" for key, value in tokens.items() if key not in summed]
    parts = named + extra
    return " ".join(parts).replace("input ", "in ").replace("output ", "out ") or "no tokens"


def _compact(value: Any) -> str:
    """Compact JSON, in full: a Trace's own spelling, so `show` and `cat` agree."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "), sort_keys=False)


# --- the raw Events, and the live view ---


def raw_events(run_dir: Path, scenario_id: str, trial: int = 1) -> Iterator[str]:
    """The Trace's lines exactly as stored, then the judgement's (show_format, `--json`).

    Byte for byte: a reader piping this into `jq` is reading the file, not a re-rendering
    of it, so nothing downstream can disagree with what was recorded.
    """
    run_dir = Path(run_dir)
    path = require_trace(run_dir, scenario_id, trial)
    yield from _lines(path)
    judgement = trial_dir(run_dir, scenario_id, trial) / JUDGEMENT_FILE
    if judgement.exists():
        yield from _lines(judgement)


def _lines(path: Path) -> Iterator[str]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield line


def follow_story(
    run_dir: Path,
    scenario_id: str,
    trial: int = 1,
    *,
    wait_seconds: float = FOLLOW_WAIT_SECONDS,
    raw: bool = False,
    poll_ms: int = FOLLOW_POLL_MS,
    stop: Callable[[], bool] | None = None,
    now: Callable[[], float] | None = None,
) -> Iterator[str]:
    """The story as it is written: the header once, then a line per Event as it lands.

    A Span's opening line is printed when its `span/start` arrives and its measured line
    when its `span/end` does, so a live reader sees a Span appear and later sees how long
    it took — the same two facts the finished story prints on one line.

    After `trace/end` this waits up to `wait_seconds` for a `judgement.jsonl` or a
    `scores.json`. `wait_seconds=0` means the Trial is not being judged and the caller
    wants the story closed now.
    """
    clock = now or time.monotonic
    columns = terminal_width()
    run_dir = Path(run_dir)
    directory = trial_dir(run_dir, scenario_id, trial)
    path = trace_path(run_dir, scenario_id, trial)

    events: list[Event] = []
    for event in follow(path, poll_ms=poll_ms, stop=stop):
        events.append(event)
        if raw:
            yield to_line(event)
            continue
        yield from _live_lines(event, events, run_dir, scenario_id, trial, columns)

    if not events:
        return

    judgement = directory / JUDGEMENT_FILE
    scores = directory / SCORES_FILE
    deadline = clock() + wait_seconds
    # `scores.json` is written after every Eval and the Diagnosis, so waiting for it —
    # not for `judgement.jsonl`, which opens at the first judged Eval — is what lets the
    # Scores and the Diagnosis stream whole.
    while clock() < deadline and not scores.exists():
        time.sleep(poll_ms / 1000)
        if stop is not None and stop():
            break

    if raw:
        # `--follow` decides when a reader sees an Event, never which Events they get:
        # following a judged Trial ends at the same bytes as `--json` on the finished
        # one, so a live view piped into `jq` loses nothing the Judge said.
        if judgement.exists():
            yield from _lines(judgement)
        return

    story = trial_story(run_dir, scenario_id, trial)
    if story.judgement:
        yield from _judgement_lines(story)
    if scores.exists():
        yield from _scores_lines(story)
    yield from _diagnosis_lines(story)


def _live_lines(
    event: Event,
    events: Sequence[Event],
    run_dir: Path,
    scenario_id: str,
    trial: int,
    width: int = FALLBACK_WIDTH,
) -> Iterator[str]:
    """One Event's contribution to the live story, as it arrives."""
    fields = event.model_extra or {}
    if event.type == "trace/start":
        story = story_from_events(events, run_dir=run_dir, scenario_id=scenario_id, trial=trial)
        yield from _header_lines(story)
        yield from _scenario_lines(story, width)
        return
    if event.type == "span/start":
        if fields.get("kind") == "turn":
            yield ""
            yield f"Turn {event.turn}"
        else:
            yield f"{INDENT}{event.span_id}  {fields.get('name')}  ..."
        return
    if event.type == "span/end":
        yield f"{INDENT}{event.span_id}  {_live_span_facts(event, events)}"
        return
    if event.type == "message":
        label = (
            _user_label(event.actor == "simulated_user")
            if fields.get("role") == "user"
            else "target    "
        )
        yield from _wrapped(INDENT, label, str(fields.get("content")), width)
        return
    if _is_top_level_stop_note(event):
        # A stop check as it lands (decision 60): `stop_when tool_called cancel_order: held`.
        yield _stop_note_line(str(fields.get("predicate") or ""), bool(fields.get("holds")))
        return
    if event.type == "trace/end":
        yield ""
        yield f"Termination {_termination(str(fields.get('termination')), fields.get('detail'))}"
        return
    line = _event_line(event)
    if line is not None:
        yield from _wrapped(INDENT * 2, "", line, width)


def _live_span_facts(end: Event, events: Sequence[Event]) -> str:
    """A closing Span's measured line, from the Events seen so far."""
    for span in project_spans(events):
        if span.span_id == end.span_id:
            view = _span_view(span, {}, events, origin=_origin(events), depth=0)
            return _span_facts(view).strip()
    return "done"


__all__ = [
    "FOLLOW_WAIT_SECONDS",
    "JUDGEMENT_FILE",
    "NOT_FOUND_EXIT",
    "NOT_OBSERVED",
    "RUN_RECORD_FILE",
    "SCORES_FILE",
    "TRACE_FILE",
    "UNPRICED",
    "BetweenTurns",
    "DiagnosisView",
    "JudgeView",
    "ScoreView",
    "SpanView",
    "StopNoteView",
    "TrialNotFound",
    "TrialStory",
    "TurnView",
    "actor_costs",
    "diagnosis_view",
    "follow_story",
    "judge_views",
    "locate_run",
    "raw_events",
    "render_story",
    "score_views",
    "story_from_events",
    "trace_path",
    "trial_dir",
    "trial_story",
]
