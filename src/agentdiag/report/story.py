"""One Change record as the Flow view tells it: five lanes (ADR-0012 §5, phase-7 decision 21).

The kit tells a fix as "this is what happened, here was the problem, here is the
fix, so now we can expect so and so to happen". `ChangeRecordStory` is that telling, built
from the record and nothing else a reader would have to trust: `what_happened` (the trigger
and the Spans it cites), `problem` (the Diagnosis and the layer), `fix` (the change set and
each push), `expected` (the Scenarios that should and must not move) and `observed` (the
verifying Run's `compare` and the Scores it cited). It is a rendering, never a store: the
record file is the truth, and nothing here writes.

**Every item links to what it cites, and what it links to is checked where it can be.** A
Run is `#run/<id>`, a Trial `#run/<id>/<scenario>/<trial>`, a comparison
`#compare/<baseline>/<run>`, another record `#flow/<id>`, a Flow `#flow/def/<slug>/<env>/<id>`
(the page routes of decision 23); a file, a Push record and a Restore point are their paths
relative to the Target directory. With the Target in hand a Run is linked only when its
directory is under `runs/`, another record only when its file is there, and a path only when
it resolves inside the Target directory to a file that exists (a path that climbs out is shown
as text and never read). Without one (`target=None`) Runs and records are linked by their ids
alone, unchecked, and paths not at all. A Flow link is never checked: the Flow lives on the
deployed side, which only a Connector read could look at. An imported record names
Runs `unknown`, which is no Run id, so it links none and says that agentdiag recorded no Run
of it. The Span ids a record cites are its own data and are always kept in `cites`; when the
Trial they belong to cannot be linked, the item's text names them instead.

**Never blank.** A record still `open`, `proposed` or `pushed` has lanes nothing has filled
yet; each is `pending` and says, in one sentence, what would fill it. A record that closed
without a change (`wontfix`, `superseded`) says so in the lanes it never reached.

Offline: it reads the record and, for a Connector push, the Push record's file.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from agentdiag.change.record import (
    UNKNOWN,
    ChangeRecord,
    Observation,
    PushEvent,
    is_closed,
    record_path,
)
from agentdiag.run.directory import is_run_id
from agentdiag.sync.fingerprint import short
from agentdiag.sync.pushes import PushFileError, load_push_record
from agentdiag.workspace import TargetPaths

NOT_HERE = "in a Run not under this Target"
"""How an item names the Spans it cites when their Trial cannot be linked."""


class LaneItem(BaseModel):
    """One line of a lane: what it says, where it points, the Spans it cites."""

    text: str
    link: str | None = None
    """A page route (`#run/…`, `#compare/…`, `#flow/…`) or a path relative to the Target
    directory; None when what it names does not exist here."""

    cites: list[str] = Field(default_factory=list)
    """Span ids the record cites, of the Trial `link` names when it names one, which the Run
    screen's `cite()` highlights; kept, and named in `text`, when there is no link."""


class Lane(BaseModel):
    """One of the story's five lanes."""

    title: str
    items: list[LaneItem] = Field(default_factory=list)
    pending: bool = False
    """True while the record is open and nothing has filled the lane yet; its one item says
    what would."""


class ChangeRecordStory(BaseModel):
    """A Change record as a story in five lanes, with the head a reader needs beside it."""

    id: str
    target: str
    title: str
    status: str
    opened_at: str
    opened_by: str
    closed_at: str | None = None
    file: str | None = None
    """The record's file, relative to the Workspace root; None without a Target."""

    imported_from: str | None = None
    what_happened: Lane
    problem: Lane
    fix: Lane
    expected: Lane
    observed: Lane

    @property
    def lanes(self) -> list[Lane]:
        return [self.what_happened, self.problem, self.fix, self.expected, self.observed]


def change_record_story(record: ChangeRecord, target: TargetPaths | None) -> ChangeRecordStory:
    """The story of `record`. `target` is the Target the record belongs to, which says which
    Runs and files exist so that nothing links to what is not there; None tells the story
    from the record alone, linking Runs by their ids and no path."""
    links = _Links(target)
    return ChangeRecordStory(
        id=record.id,
        target=record.target,
        title=record.title,
        status=record.status,
        opened_at=record.opened_at,
        opened_by=record.opened_by,
        closed_at=record.closed_at,
        file=target.relative_of(record_path(target, record.id)) if target else None,
        imported_from=record.imported_from,
        what_happened=_what_happened(record, links),
        problem=_problem(record, links),
        fix=_fix(record, links),
        expected=_expected(record),
        observed=_observed(record, links),
    )


class _Links:
    """What exists for the record's Target, asked by each lane."""

    def __init__(self, target: TargetPaths | None) -> None:
        self.target = target

    def run(self, run_id: str | None) -> str | None:
        return f"#run/{run_id}" if self.has_run(run_id) else None

    def has_run(self, run_id: str | None) -> bool:
        if run_id is None or run_id == UNKNOWN or not is_run_id(run_id):
            return False
        return self.target is None or (self.target.runs / run_id).is_dir()

    def trial(self, run_id: str | None, scenario: str | None, trial: int | None) -> str | None:
        if scenario is None or trial is None or not self.has_run(run_id):
            return None
        return f"#run/{run_id}/{scenario}/{trial}"

    def path(self, relative: str | None) -> str | None:
        """`relative` when it names a file inside the Target directory that exists; None
        otherwise, and for a path that climbs out of the directory, which is never read."""
        found = self.inside(relative)
        return relative if found is not None and found.exists() else None

    def inside(self, relative: str | None) -> Path | None:
        """Where `relative` resolves under the Target directory; None outside it."""
        if relative is None or self.target is None:
            return None
        directory = self.target.directory.resolve()
        resolved = (directory / relative).resolve()
        return resolved if resolved.is_relative_to(directory) else None

    def record(self, record_id: str) -> str | None:
        exists = self.target is None or record_path(self.target, record_id).is_file()
        return f"#flow/{record_id}" if exists else None


def _citing(text: str, link: str | None, cites: list[str]) -> LaneItem:
    """An item citing Spans of the Trial `link` names; with no link, the text names them."""
    if cites and link is None:
        text = f"{text} (cites {', '.join(cites)} {NOT_HERE})"
    return LaneItem(text=text, link=link, cites=cites)


def _pending(title: str, sentence: str) -> Lane:
    return Lane(title=title, items=[LaneItem(text=sentence)], pending=True)


# --- 1 · what happened ---


def _what_happened(record: ChangeRecord, links: _Links) -> Lane:
    trigger = record.trigger
    items: list[LaneItem] = []
    if trigger.kind == "diagnosis" and trigger.run is not None:
        where = f"Trial {trigger.scenario} / {trigger.trial} of Run {trigger.run}"
        link = links.trial(trigger.run, trigger.scenario, trigger.trial)
        items.append(_citing(f"Diagnosis of {where}: {trigger.summary}", link, list(trigger.cites)))
    elif record.imported_from is not None:
        items.append(LaneItem(text=f"Imported from {record.imported_from}: {trigger.summary}"))
        items.append(
            LaneItem(
                text="agentdiag recorded no Run of it; the entry's own words are kept under "
                "## Imported in the record file."
            )
        )
    else:
        items.append(LaneItem(text=f"Complaint: {trigger.summary}"))
        items.append(LaneItem(text="Its redacted text is under ## Trigger in the record file."))
    items.append(LaneItem(text=f"Opened {record.opened_at} by {record.opened_by}."))
    return Lane(title="What happened", items=items)


# --- 2 · the problem ---


def _problem(record: ChangeRecord, links: _Links) -> Lane:
    items: list[LaneItem] = []
    diagnosis = record.diagnosis
    if diagnosis is not None:
        link = links.trial(diagnosis.run, diagnosis.scenario, diagnosis.trial)
        trigger = record.trigger
        same = (trigger.run, trigger.scenario, trigger.trial) == (
            diagnosis.run,
            diagnosis.scenario,
            diagnosis.trial,
        )
        items.append(_citing(diagnosis.text, link, list(trigger.cites) if same else []))
    elif record.imported_from is not None:
        items.append(
            LaneItem(text=f"No Diagnosis: the entry was imported from {record.imported_from}.")
        )
    else:
        items.append(LaneItem(text="No Diagnosis: the record was opened from a complaint."))
    if record.layer is not None:
        items.append(LaneItem(text=f"Layer: {record.layer}."))
    else:
        items.append(LaneItem(text="Layer: not recorded (an imported entry names none)."))
    return Lane(title="The problem", items=items)


# --- 3 · the fix ---


def _fix(record: ChangeRecord, links: _Links) -> Lane:
    title = "The fix"
    change = record.change
    if change is None:
        if is_closed(record.status):
            return Lane(
                title=title,
                items=[LaneItem(text=_closed_without(record, "No change was proposed"))],
            )
        return _pending(
            title,
            "Not proposed yet: `change propose` names the sections, files and Flows the "
            "change touches, and each push of it lands here.",
        )
    items = [LaneItem(text=f"Section {section}.") for section in change.sections]
    items += [LaneItem(text=f"File {name}.", link=links.path(name)) for name in change.files]
    environment = _flow_environment(record)
    items += [
        LaneItem(
            text=f"Flow {flow}.",
            link=f"#flow/def/{record.target}/{environment}/{flow}" if environment else None,
        )
        for flow in change.flow_ids
    ]
    if change.git_sha:
        items.append(LaneItem(text=f"Proposed at commit {change.git_sha[:12]}."))
    for event in record.pushes:
        items += _push_items(event, links)
    if record.pushes:
        return Lane(title=title, items=items)
    if is_closed(record.status):
        items.append(LaneItem(text=_closed_without(record, "No push was made")))
        return Lane(title=title, items=items)
    items.append(
        LaneItem(
            text="Not pushed yet: a push through the Connector (`agentdiag push`), or a Run "
            "that re-syncs onto the edited files, records where the change went."
        )
    )
    return Lane(title=title, items=items, pending=True)


def _flow_environment(record: ChangeRecord) -> str | None:
    """The environment a changed Flow is read from: the last push's, when it names one."""
    named = [event.environment for event in record.pushes if event.environment != UNKNOWN]
    return named[-1] if named else None


def _push_items(event: PushEvent, links: _Links) -> list[LaneItem]:
    before, after = event.fingerprint_before, event.fingerprint_after
    if before and after:
        moved = f", Fingerprint {short(before)} -> {short(after)}"
    elif before or after:
        moved = (
            f", Fingerprint from {short(before)}" if before else f", Fingerprint to {short(after)}"
        )
    else:
        moved = ""
    if event.kind == "local":
        by = f"by Run {event.run}" if event.run else "by a Run the event does not name"
        return [
            LaneItem(
                text=f"Re-synced {by} on {event.environment} at {event.at}{moved}.",
                link=links.run(event.run),
            )
        ]
    where = (
        "an environment the record does not name"
        if event.environment == UNKNOWN
        else event.environment
    )
    if event.push_record is None:
        return [LaneItem(text=f"Pushed to {where} at {event.at}{moved}; no Push record was kept.")]
    items = [
        LaneItem(
            text=f"Pushed to {where} at {event.at}{moved}: Push record {event.push_record}.",
            link=links.path(event.push_record),
        )
    ]
    restore = _restore_point(links, event.push_record)
    if restore is not None:
        items.append(
            LaneItem(
                text=f"Restore point {restore}"
                + ("." if links.path(restore) else " (kept where the push ran; gitignored)."),
                link=links.path(restore),
            )
        )
    return items


def _restore_point(links: _Links, push_record: str) -> str | None:
    """The Restore point a Push record names, when that record's file is inside the Target
    directory and loads."""
    found = links.inside(push_record)
    if found is None or not found.is_file():
        return None
    try:
        return load_push_record(found).restore_point
    except PushFileError:
        return None


# --- 4 · the expected effect ---


def _expected(record: ChangeRecord) -> Lane:
    title = "The expected effect"
    expected = record.expected
    if expected is None:
        if is_closed(record.status):
            return Lane(
                title=title,
                items=[LaneItem(text="No expectation was stated before the record closed.")],
            )
        return _pending(
            title,
            "Not stated yet: `change expect` names, before the verifying Run, the Scenarios "
            "that should move and those that must not.",
        )
    items = [LaneItem(text=f"Stated {expected.stated_at}.")]
    items += [LaneItem(text=f"Should move: {scenario}.") for scenario in expected.should_move]
    items += [LaneItem(text=f"Must not move: {scenario}.") for scenario in expected.must_not_move]
    if not expected.must_not_move:
        items.append(LaneItem(text="Must not move: no Scenario named."))
    return Lane(title=title, items=items)


# --- 5 · what was observed ---


def _observed(record: ChangeRecord, links: _Links) -> Lane:
    title = "What was observed"
    verification = record.verification
    if verification is None:
        if is_closed(record.status):
            return Lane(title=title, items=[_closed_item(record, links)])
        return _pending(
            title,
            "Not observed yet: `change close --verified` or `--refuted` fills this from the "
            "`compare` of a Run made after the push.",
        )
    compared = links.has_run(verification.baseline) and links.has_run(verification.run)
    if verification.baseline == UNKNOWN or verification.run == UNKNOWN:
        head = (
            f"{verification.result.capitalize()}, as the imported entry says: agentdiag "
            "recorded neither Run, so there is no comparison to open."
        )
    else:
        head = (
            f"{verification.result.capitalize()}: Run {verification.baseline} -> Run "
            f"{verification.run}, compared {verification.compared_at}."
        )
    items = [
        LaneItem(
            text=head,
            link=f"#compare/{verification.baseline}/{verification.run}" if compared else None,
        ),
        LaneItem(text=verification.summary),
    ]
    if verification.expect:
        items.append(LaneItem(text="Declared: " + ", ".join(verification.expect) + "."))
    items += [_observation(observation, links) for observation in record.observed]
    if record.superseded_by is not None:
        items.append(_closed_item(record, links))
    return Lane(title=title, items=items)


def _observation(observation: Observation, links: _Links) -> LaneItem:
    name = observation.eval + (f"[{observation.eval_id}]" if observation.eval_id else "")
    return LaneItem(
        text=f"{observation.scenario} / {observation.trial} · {name} {observation.verdict}: "
        f"{observation.rationale}",
        link=links.trial(observation.run, observation.scenario, observation.trial),
    )


def _closed_item(record: ChangeRecord, links: _Links) -> LaneItem:
    """How a record that closed without a verification ended, and what it points at."""
    at = f" at {record.closed_at}" if record.closed_at else ""
    if record.status == "wontfix" or record.superseded_by is None:
        why = f": {record.why}" if record.why else "."
        return LaneItem(text=f"Closed as {record.status}{at}{why}")
    other = record.superseded_by
    return LaneItem(text=f"Superseded by {other}{at}.", link=links.record(other))


def _closed_without(record: ChangeRecord, what: str) -> str:
    return f"{what}: the record closed as {record.status}."


__all__ = ["ChangeRecordStory", "Lane", "LaneItem", "change_record_story"]
