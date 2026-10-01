"""One-way views of a Trace: Chrome trace JSON for Perfetto, and a speedscope profile (D41).

Both exports are *Reports* in the `CONTEXT.md` sense — a rendering, never the source of
truth. Nothing in this module reads a Chrome or a speedscope file: there is no loader, no
parser and no import path back (ADR-0004 §7). The Trace's JSONL stays the only store, and
the reason is that every path into a flame-graph viewer destroys payload.

Both exporters are pure functions over Events. They take the Events, project Spans and
Metrics over them with `project_spans` and `metrics`, and return a `dict`. `render_export`
turns one into the text a file would hold; `write_text` is the only thing here that touches
a filesystem, so "never inside the Run directory" is a property of one function.

## Lanes

Neither format has a parent pointer. Chrome expresses hierarchy positionally, by nesting on
one `(pid, tid)`, and Perfetto states the limit plainly: "Overlapping, non-nested events are
out of spec and inherently ambiguous" (§1.4). speedscope's `evented` profile is stricter
still: its `O`/`C` events must be balanced LIFO. But agentdiag's Spans are referential
(ADR-0006 §3) and siblings may overlap (D11: "Concurrent Spans keep their parent pointers;
renderers assign lanes"). So this module assigns lanes:

    A Span takes its parent's lane, unless it overlaps a Span already placed on that lane
    that is not one of its own ancestors, in which case it takes the next free lane. Its
    descendants follow it. A Span that runs on past its parent's end does not nest either,
    so it starts looking one lane past its parent's.

A properly nested chain — `turn` → `llm_call` → `tool_call`, which is what ticket 01's
Decision 1 produces — therefore stays on one lane and Perfetto draws it as a flame. Two
concurrent `tool_call`s land on separate lanes and so on separate tracks, and so does a
child that outlives its parent, which agentdiag's own Adapter never writes but a
reconstructed Trace can.

Chrome spends its `tid` axis on lanes and its `pid` axis on actors. speedscope has no lane
axis at all, so a Turn whose Spans need more than one lane becomes one profile per lane
(`turn N · lane M`) rather than one profile whose nesting would be a lie.

## What each format is given

Chrome gets everything: a complete (`ph: "X"`) event per closed Span with every Span
attribute and the projected Metrics in `args`, plus instant events for the Events that are
not Spans. speedscope gets only frame names, because a `Frame` has four fields and three of
them are source coordinates (§2.2) — so the name carries kind, name, actor and Fidelity, and
nothing else survives.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from agentdiag import __version__
from agentdiag.run.locate import JUDGEMENT_FILE, require_trace, traces_from, trial_dir
from agentdiag.trace.events import Event
from agentdiag.trace.reader import read_trace, resolve_blobs
from agentdiag.trace.spans import Metrics, Span, metrics, project_spans

ExportFormat = Literal["chrome", "speedscope"]
"""The two views `export` writes. Adding a third is adding a renderer, never a store."""

FORMATS: tuple[str, ...] = ("chrome", "speedscope")

SUFFIXES: dict[str, str] = {"chrome": "chrome.json", "speedscope": "speedscope.json"}
"""The extension each format writes, so a viewer and a reader both recognise the file."""

SPEEDSCOPE_SCHEMA = "https://www.speedscope.app/file-format-schema.json"

ARGUMENT_LIMIT = 2000
"""How much of an Event's field an instant event's `args` carries.

Perfetto parses `args` into a SQL table to be queried, not read as a payload,
and the Trace holds the whole value anyway. A cut value says so:
nothing here ever truncates silently (ADR-0006 §4).
"""

INSTANT_EVENT_TYPES: tuple[str, ...] = (
    "message",
    "tool/call",
    "tool/result",
    "error",
    "fixture/applied",
    "note",
)
"""The Event types that become a point on the timeline rather than a slice."""

MICROSECONDS = 1000
"""Chrome's `ts` and `dur` are microseconds (§1.2); a Trace's `ts` is milliseconds."""

JUDGE_KIND = "judge"
OPEN_SUFFIX = " (open)"
"""What a speedscope frame name says about a Span that never closed (ADR-0004 §2)."""

EXCEEDS_SUFFIX = " (exceeds parent)"
"""What it says about a Span that ran on past its parent, and so does not nest inside it."""


# --- lanes: the one thing both formats need and neither format has ---


def assign_lanes(spans: Sequence[Span], *, per_actor: bool = True) -> dict[str, int]:
    """A lane per Span: its parent's lane, unless something non-nesting already holds it.

    Walked in `dotted_order`, which is execution order (ADR-0006 §3), so a Span is placed
    after every sibling that started before it. A Span always overlaps its own ancestors —
    that is what nesting *is* — so an ancestor never pushes a Span off its lane, and only a
    placed Span that is not an ancestor can.

    A Span that is *not* contained in its parent's interval is the exception: it does not
    nest, so it cannot share its parent's track without putting overlapping non-nested
    events on one `(pid, tid)`, which Perfetto calls out of spec and ambiguous (§1.4). Such
    a Span starts looking one lane past its parent's.

    `per_actor` is how Chrome and speedscope differ. Chrome puts each actor on its own
    process, so lanes are numbered within an actor. speedscope has no actor axis, so one
    profile holds every actor's Spans and lanes must be numbered across all of them.
    """
    by_id = {span.span_id: span for span in spans}
    placed: dict[str, list[tuple[int, Span]]] = {}
    lanes: dict[str, int] = {}
    ancestors: dict[str, frozenset[str]] = {}
    for span in sorted(spans, key=lambda span: span.dotted_order):
        parent = span.parent_span_id or ""
        ancestors[span.span_id] = ancestors.get(parent, frozenset()) | {parent}
        here = placed.setdefault(span.actor if per_actor else "", [])
        lane = lanes.get(parent, 0)
        if exceeds_parent(span, by_id.get(parent)):
            lane += 1
        while any(
            other_lane == lane
            and other.span_id not in ancestors[span.span_id]
            and _overlaps(span, other)
            for other_lane, other in here
        ):
            lane += 1
        lanes[span.span_id] = lane
        here.append((lane, span))
    return lanes


def exceeds_parent(span: Span, parent: Span | None) -> bool:
    """Does this Span run on past its parent's end, so that it does not nest inside it?

    Ticket 01's Decision 1 makes a child's interval lie within its parent's, and agentdiag's
    own Adapter holds to it. A reconstructed Trace (ADR-0001) need not, and neither export
    can draw what does not nest — so both say so rather than drawing a clean nest.
    """
    if parent is None or parent.end_ms is None or span.end_ms is None:
        return False
    return span.end_ms > parent.end_ms


def _overlaps(one: Span, other: Span) -> bool:
    """Do two Spans share any wall time? Touching at an instant does not count."""
    one_end = one.end_ms if one.end_ms is not None else one.start_ms
    other_end = other.end_ms if other.end_ms is not None else other.start_ms
    return one.start_ms < other_end and other.start_ms < one_end


# --- Chrome trace JSON (for Perfetto) ---


def export_chrome(
    events: Sequence[Event], *, judgement: Sequence[Event] | None = None
) -> dict[str, Any]:
    """The Chrome Trace Event Format object form for one Trial (§1.1).

    One complete (`X`) event per closed Span, one begin (`B`) event with no `E` per open
    Span, one instant (`i`) event per Event that is not a Span, and the metadata (`M`)
    events that name each actor's process and each lane's thread. The Judge's Spans, when
    `judgement` is given, are one more actor and so one more process.
    """
    events = resolve_blobs(events)
    judgement = resolve_blobs(judgement) if judgement else []

    trace_events: list[dict[str, Any]] = []
    for source in (events, judgement):
        if source:
            trace_events.extend(_chrome_events(source, pid_base=_pid_base(events, source)))
    return {
        "traceEvents": trace_events,
        "displayTimeUnit": "ms",
        "otherData": _other_data(events),
    }


def _pid_base(trace: Sequence[Event], source: Sequence[Event]) -> int:
    """Where this file's actors start numbering, so a Judge never collides with a Target."""
    return 0 if source is trace else len(_actors(trace))


def _actors(events: Sequence[Event]) -> list[str]:
    """Every actor that opened a Span, in the order it first did."""
    seen: list[str] = []
    for span in project_spans(events):
        if span.actor not in seen:
            seen.append(span.actor)
    return seen


def _other_data(events: Sequence[Event]) -> dict[str, Any]:
    """The `trace/start` Event's coordinates, in the container's metadata slot (§1.1)."""
    start = next((event for event in events if event.type == "trace/start"), None)
    fields = (start.model_extra or {}) if start else {}
    return {
        "agentdiag": __version__,
        "trace_id": fields.get("trace_id"),
        "run": fields.get("run"),
        "scenario": fields.get("scenario"),
        "trial": fields.get("trial"),
    }


def _chrome_events(events: Sequence[Event], *, pid_base: int) -> list[dict[str, Any]]:
    spans = project_spans(events)
    lanes = assign_lanes(spans)
    actors = _actors(events)
    pids = {actor: pid_base + index for index, actor in enumerate(actors)}

    produced: list[dict[str, Any]] = []
    for actor in actors:
        produced.append(_metadata(pids[actor], 0, "process_name", actor))
        actor_lanes = sorted({lanes[span.span_id] for span in spans if span.actor == actor})
        for lane in actor_lanes:
            produced.append(_metadata(pids[actor], lane, "thread_name", f"lane {lane}"))

    by_id = {span.span_id: span for span in spans}
    for span in spans:
        produced.append(_slice(span, events, pid=pids[span.actor], tid=lanes[span.span_id]))
    for event in events:
        if event.type in INSTANT_EVENT_TYPES:
            owner = by_id.get(event.span_id or "")
            produced.append(
                _instant(
                    event,
                    pid=pids.get(owner.actor if owner else event.actor, pid_base),
                    tid=lanes[owner.span_id] if owner else 0,
                )
            )
    return produced


def _metadata(pid: int, tid: int, name: str, value: str) -> dict[str, Any]:
    """An `M`-phase event: the only `args` key any viewer interprets (§1.5)."""
    return {"ph": "M", "pid": pid, "tid": tid, "name": name, "args": {"name": value}}


def _slice(span: Span, events: Sequence[Event], *, pid: int, tid: int) -> dict[str, Any]:
    """One Span as a slice: complete (`X`) when it closed, begin (`B`) while it is open."""
    args = _span_args(span, events)
    produced: dict[str, Any] = {
        "name": span.name,
        "cat": span.kind,
        "ph": "X" if span.end_ms is not None else "B",
        "pid": pid,
        "tid": tid,
        "ts": span.start_ms * MICROSECONDS,
        "args": args,
    }
    if span.end_ms is not None:
        produced["dur"] = (span.end_ms - span.start_ms) * MICROSECONDS
    return produced


def _span_args(span: Span, events: Sequence[Event]) -> dict[str, Any]:
    """Every Span attribute, plus what the Span is and what it measured (D41).

    `args` has no schema and no size limit (§1.5), and Perfetto makes it queryable in SQL,
    so this is where the Fidelity, the actor and the token counts go — everything the
    flame-graph geometry cannot say.
    """
    measured: Metrics = metrics(span, events)
    args: dict[str, Any] = dict(span.attributes)
    args.update(
        {
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "kind": span.kind,
            "actor": span.actor,
            "fidelity": span.fidelity,
            "status": span.status,
            "turn": span.turn,
            "dotted_order": span.dotted_order,
            "duration_ms": measured.duration_ms,
            "model_latency_ms": measured.model_latency_ms,
        }
    )
    if measured.time_to_first_token_ms is not None:
        args["time_to_first_token_ms"] = measured.time_to_first_token_ms
    for key, value in measured.tokens.items():
        args[f"tokens.{key}"] = value
    return args


def _instant(event: Event, *, pid: int, tid: int) -> dict[str, Any]:
    """One Event that is not a Span, as a point on its Span's track (`ph: "i"`, §1.3)."""
    fields = event.model_extra or {}
    return {
        "name": _instant_name(event, fields),
        "cat": event.type,
        "ph": "i",
        "s": "t",
        "pid": pid,
        "tid": tid,
        "ts": event.ts * MICROSECONDS,
        "args": {key: _cut(value) for key, value in sorted(fields.items())},
    }


def _instant_name(event: Event, fields: Mapping[str, Any]) -> str:
    """A short name: the type, and the one field that says which one it was."""
    for key in ("role", "tool", "fixture"):
        value = fields.get(key)
        if isinstance(value, str):
            return f"{event.type} {value}"
    if event.type == "error":
        error_type = fields.get("error_type")
        if isinstance(error_type, str):
            return f"error {error_type}"
    return event.type


def _cut(value: Any) -> Any:
    """A value small enough to query, and told when it was cut (ADR-0006 §4)."""
    if isinstance(value, str):
        return _cut_text(value)
    if isinstance(value, Mapping):
        return {key: _cut(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_cut(item) for item in value]
    return value


def _cut_text(text: str) -> str:
    if len(text) <= ARGUMENT_LIMIT:
        return text
    dropped = len(text) - ARGUMENT_LIMIT
    return f"{text[:ARGUMENT_LIMIT]}[truncated {dropped} chars]"


# --- speedscope ---


def export_speedscope(
    events: Sequence[Event], *, judgement: Sequence[Event] | None = None, name: str
) -> dict[str, Any]:
    """A speedscope `File` for one Trial: one evented profile per Turn (§2.1, §2.3).

    A profile's `events` are `O`/`C` pairs in strict LIFO order, which is what the format
    requires ("the ordering must be balanced", §2.3). That is only expressible for Spans
    that nest, so a Turn with overlapping siblings becomes one profile per lane.
    """
    events = resolve_blobs(events)
    judgement = resolve_blobs(judgement) if judgement else []

    frames: list[str] = []
    profiles: list[dict[str, Any]] = []
    spans = project_spans(events)
    last_ms = max((event.ts for event in events), default=0)

    for turn in _turns(spans):
        turn_spans = [span for span in spans if span.turn == turn]
        profiles.extend(_profiles(turn_spans, frames, label=f"turn {turn}", last_ms=last_ms))

    outside = [span for span in spans if span.turn is None]
    if outside:
        profiles.extend(_profiles(outside, frames, label="outside turns", last_ms=last_ms))

    if judgement:
        judge_spans = [span for span in project_spans(judgement) if span.kind == JUDGE_KIND]
        judge_last = max((event.ts for event in judgement), default=last_ms)
        profiles.extend(_profiles(judge_spans, frames, label="judgement", last_ms=judge_last))

    return {
        "$schema": SPEEDSCOPE_SCHEMA,
        "name": name,
        "exporter": f"agentdiag {__version__}",
        "activeProfileIndex": 0,
        "shared": {"frames": [{"name": frame} for frame in frames]},
        "profiles": profiles,
    }


def _turns(spans: Sequence[Span]) -> list[int]:
    """Every Turn index the Spans belong to, in order."""
    return sorted({span.turn for span in spans if span.turn is not None})


def _profiles(
    spans: Sequence[Span], frames: list[str], *, label: str, last_ms: int
) -> list[dict[str, Any]]:
    """One profile for these Spans, or one per lane when they cannot nest in one.

    The lane assignment is the same one Chrome uses, so the two exports agree about which
    Spans ran concurrently.
    """
    if not spans:
        return []
    lanes = assign_lanes(spans, per_actor=False)
    used = sorted(set(lanes.values()))
    by_id = {span.span_id: span for span in spans}
    if len(used) == 1:
        return [_profile(spans, frames, name=label, last_ms=last_ms, by_id=by_id)]
    return [
        _profile(
            [span for span in spans if lanes[span.span_id] == lane],
            frames,
            name=f"{label} · lane {lane}",
            last_ms=last_ms,
            by_id=by_id,
        )
        for lane in used
    ]


def _profile(
    spans: Sequence[Span],
    frames: list[str],
    *,
    name: str,
    last_ms: int,
    by_id: Mapping[str, Span],
) -> dict[str, Any]:
    """One `evented` profile: `O` and `C` events in milliseconds, strictly nested (§2.3).

    Walked in `dotted_order`, so a Span is reached after its parent and after every sibling
    that started before it. Everything still on the stack whose end is at or before this
    Span's start is closed first, innermost out; what remains is the chain of ancestors this
    Span nests inside. Lanes have already guaranteed that nothing on this lane overlaps it
    without containing it, so the result is balanced. `by_id` holds every Span of the Turn,
    not only this lane's, so a Span still knows the parent it was split away from.

    Every `C` is emitted at `max(its end, the last `at` written)`, because the format's one
    ordering rule is that `at` is non-decreasing. A Trace whose child outlives its parent
    would otherwise close the parent behind events already written; the frame name says
    `(exceeds parent)` so the profile is read as the departure it is, not as a clean nest.
    """
    ordered = sorted(spans, key=lambda span: span.dotted_order)
    opens: list[tuple[int, int, int]] = []
    for span in ordered:
        frame = _frame(span, frames, parent=by_id.get(span.parent_span_id or ""))
        end = span.end_ms if span.end_ms is not None else last_ms
        opens.append((span.start_ms, end, frame))

    produced: list[dict[str, Any]] = []
    stack: list[tuple[int, int]] = []
    last_at = ordered[0].start_ms

    def close_to(at: int) -> None:
        nonlocal last_at
        while stack and stack[-1][0] <= at:
            closing_at, closing_frame = stack.pop()
            last_at = max(closing_at, last_at)
            produced.append({"type": "C", "frame": closing_frame, "at": last_at})

    for start, end, frame in opens:
        close_to(start)
        last_at = max(start, last_at)
        produced.append({"type": "O", "frame": frame, "at": last_at})
        stack.append((end, frame))
    while stack:
        closing_at, closing_frame = stack.pop()
        last_at = max(closing_at, last_at)
        produced.append({"type": "C", "frame": closing_frame, "at": last_at})

    return {
        "type": "evented",
        "name": name,
        "unit": "milliseconds",
        "startValue": ordered[0].start_ms,
        "endValue": max(end for _, end, _ in opens),
        "events": produced,
    }


def _frame(span: Span, frames: list[str], *, parent: Span | None = None) -> int:
    """The index of this Span's frame, added to `shared.frames` the first time it is seen.

    A `Frame` has no payload (§2.2) and frames are deduplicated by name, so the name is the
    only place kind, name, actor and Fidelity can be said — and two Spans that say the same
    thing are deliberately one frame, which is what makes Left Heavy work.

    The kind is dropped when the Span's own name already opens with it, so a Turn reads
    `turn 1 [...]` rather than `turn turn 1 [...]`. Span names follow OTel (`chat <model>`,
    `execute_tool <tool>`, `turn <n>`), so this fires for `turn` Spans and no other.
    """
    name = f"{span.kind} {span.name} [{span.actor}, {span.fidelity}]"
    if span.name == span.kind or span.name.startswith(f"{span.kind} "):
        name = f"{span.name} [{span.actor}, {span.fidelity}]"
    if span.end_ms is None:
        name += OPEN_SUFFIX
    elif exceeds_parent(span, parent):
        name += EXCEEDS_SUFFIX
    if name not in frames:
        frames.append(name)
    return frames.index(name)


# --- writing one out ---


def render(export: Mapping[str, Any]) -> str:
    """The one JSON spelling both exports use: indented, sorted, newline-terminated."""
    return json.dumps(export, indent=2, sort_keys=True) + "\n"


def default_out(run_id: str, scenario_id: str, trial: int, format: str) -> Path:
    """Where an export lands when the caller named no path: the current directory.

    Never inside the Run directory. A Run is written once and never edited (ADR-0005 §2),
    and an export is a Report — a rendering, regenerated from the Trace whenever it is
    wanted, so it is not part of what the Run recorded.
    """
    return Path(f"{run_id}.{scenario_id}.{trial}.{SUFFIXES[format]}")


class UnknownFormat(ValueError):
    """A `--format` that names no view. The set is closed: these are the two we render."""


def build_export(
    run_dir: Path, scenario_id: str, trial: int, format: str
) -> tuple[dict[str, Any], str]:
    """The export `format` asks for, and the Run id it belongs to.

    Raises `TrialNotFound` when the Trial has no Trace, and `UnknownFormat` when the
    format names no view.
    """
    if format not in FORMATS:
        raise UnknownFormat(
            f"no export format {format!r}: agentdiag exports {' and '.join(FORMATS)}"
        )
    path = require_trace(run_dir, scenario_id, trial)
    events = read_trace(path)
    judgement_path = trial_dir(run_dir, scenario_id, trial) / JUDGEMENT_FILE
    judgement = read_trace(judgement_path) if judgement_path.exists() else None

    # A rescored Trial's Trace names the Run that recorded it; the export belongs to the
    # Run that was asked for, whose judgement it carries (D34).
    recorded_here = traces_from(run_dir) is None
    run_id = str((_other_data(events).get("run") if recorded_here else None) or Path(run_dir).name)
    if format == "chrome":
        return export_chrome(events, judgement=judgement), run_id
    name = f"{run_id} · {scenario_id} · trial {trial}"
    return export_speedscope(events, judgement=judgement, name=name), run_id


def render_export(run_dir: Path, scenario_id: str, trial: int, format: str) -> tuple[str, str]:
    """One Trial's export as the text a file would hold, and the Run id it belongs to.

    Split from `write_export` so a caller that prints (`--out -`) and a caller that writes
    take the same path up to the last step: there is one rendering and one write, not one
    of each per destination.
    """
    export, run_id = build_export(run_dir, scenario_id, trial, format)
    return render(export), run_id


def write_export(run_dir: Path, scenario_id: str, trial: int, format: str, out_path: Path) -> Path:
    """Write one export to `out_path`, and return where it landed.

    `out_path` is the caller's: `write_export` never chooses a path and never writes into
    the Run directory. `default_out` is what the CLI passes when the caller named none.
    """
    text, _ = render_export(run_dir, scenario_id, trial, format)
    return write_text(text, out_path)


def write_text(text: str, out_path: Path) -> Path:
    """Put one rendered export at `out_path`, making the directory it names if it must.

    The one place an export reaches a filesystem, so "never inside the Run directory" is a
    property of one function rather than a rule each caller has to remember.
    """
    out_path = Path(out_path)
    if out_path.parent != Path(""):
        out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path


__all__ = [
    "ARGUMENT_LIMIT",
    "EXCEEDS_SUFFIX",
    "FORMATS",
    "INSTANT_EVENT_TYPES",
    "MICROSECONDS",
    "OPEN_SUFFIX",
    "SPEEDSCOPE_SCHEMA",
    "SUFFIXES",
    "ExportFormat",
    "UnknownFormat",
    "assign_lanes",
    "build_export",
    "default_out",
    "exceeds_parent",
    "export_chrome",
    "export_speedscope",
    "render",
    "render_export",
    "write_export",
    "write_text",
]
