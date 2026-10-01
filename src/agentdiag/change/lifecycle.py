"""The Change record's lifecycle: named transitions and the gates (ADR-0012 §2, §4;
phase-7 decisions 2 to 4).

`open → proposed` (`propose`), `proposed → pushed` (a Connector push,
`record_connector_push`, or a re-syncing Run, `mark_pushed_by_run`), `pushed → verified |
refuted` (`close_by_compare`), and any open status `→ wontfix | superseded`. Every other
transition is `TransitionRefused`, naming the status and what it allows. Once a record is
closed no field changes but `superseded_by`.

**`verified` and `refuted` go through `compare`** (decision 3). The post-change Run must
have started after the expectation was stated; the comparison against the pre-change Run
declares the given paths, plus `fingerprint` and `sync` when the record has a push event (the
push *is* the declared variation), and any other difference refuses the close by name. The
record is `verified` when every `should_move` Scenario has an `improvement` and no
`regression` and every `must_not_move` Scenario is `unchanged` throughout; `refuted` when the
same valid comparison shows anything else. A comparison in which the post-change Scores of
a Scenario the expectation names (`should_move` or `must_not_move`) decide nothing (all
`incomplete`, `unverifiable` or `invalid`) is not a valid one for it and refuses both
results (phase-8 decision 14, amended). The verifying Run opens the environment the record's
last Connector push wrote (phase-8 decision 12), else `--any-env` says so and the
verification records the mismatch. `compare` reaches the model client through the
Scorecard's model, so it is imported inside the gate, never here at import time.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from agentdiag.change.record import (
    UNKNOWN,
    ChangeRecord,
    ChangeSet,
    Expectation,
    Observation,
    PushEvent,
    Verification,
    find_record,
    load_records,
    record_body,
    write_record,
)
from agentdiag.run.directory import is_run_id
from agentdiag.run.locate import RUN_RECORD_FILE, SCORES_FILE, TRIALS_DIRNAME
from agentdiag.types import (
    DEFAULT_RUN_SOURCE,
    ChangeStatus,
    CloseReason,
    Direction,
    VerificationResult,
)
from agentdiag.workspace import TargetPaths

if TYPE_CHECKING:
    from agentdiag.run.compare import Comparison
    from agentdiag.run.record import RunRecord

RunRole = Literal["pre-change", "post-change", "trigger's"]
"""Which Run of a close a refusal names."""

LOCAL_SIDE_MOVED: frozenset[Direction] = frozenset({"local_ahead", "diverged"})
"""The directions of a moved section whose local side moved: only these are the local push
of a record that edited the file (decision 4, amended); `deployed_ahead` is a change made
on the deployed side, which no record here made."""

TRANSITIONS: dict[ChangeStatus, tuple[ChangeStatus, ...]] = {
    "open": ("proposed", "wontfix", "superseded"),
    "proposed": ("pushed", "wontfix", "superseded"),
    "pushed": ("verified", "refuted", "wontfix", "superseded"),
    "verified": (),
    "refuted": (),
    "wontfix": (),
    "superseded": (),
}
"""Every transition the lifecycle allows, from each status (decision 2)."""

PUSHABLE: frozenset[ChangeStatus] = frozenset({"proposed", "pushed"})
"""The statuses a record may be in when a push names it (phase-7 decision 13)."""

EXPECT_ALLOWED: frozenset[ChangeStatus] = frozenset({"open", "proposed", "pushed"})
"""Where `expect` may state or restate the expected effect."""

DECLARED_BY_A_PUSH = ("fingerprint", "sync")
"""The `run.json` paths a push moves, declared for a record that has a push event."""

ORDER_REFUSED = "the expectation was recorded after the verifying Run started"


class ChangeRefused(ValueError):
    """A Change record command that cannot do what was asked; the message says why."""


class TransitionRefused(ChangeRefused):
    """A transition the lifecycle does not allow from the record's status."""

    def __init__(self, record: ChangeRecord, wanted: str) -> None:
        allowed = TRANSITIONS[record.status]
        then = ", ".join(allowed) if allowed else "nothing: the record is closed"
        super().__init__(
            f"Change record {record.id} is {record.status}: it cannot move to {wanted}; "
            f"from {record.status} it can move to {then}"
        )


class ExpectationRefused(ChangeRefused):
    """An expected effect that cannot be stated as given."""


class CloseRefused(ChangeRefused):
    """A `verified` or `refuted` close the gate refuses; the message says why."""


def require(record: ChangeRecord, to: ChangeStatus) -> None:
    if to not in TRANSITIONS[record.status]:
        raise TransitionRefused(record, to)


def propose(record: ChangeRecord, change: ChangeSet) -> ChangeRecord:
    """`open → proposed`, recording the change set."""
    require(record, "proposed")
    return record.model_copy(update={"status": "proposed", "change": change})


def state_expectation(
    record: ChangeRecord, should_move: Sequence[str], must_not_move: Sequence[str], stated_at: str
) -> ChangeRecord:
    """The expected effect, timestamped; allowed while the record is open, proposed or
    pushed, and restated with a new timestamp when stated again."""
    if record.status not in EXPECT_ALLOWED:
        raise TransitionRefused(record, "a new expectation")
    both = sorted(set(should_move) & set(must_not_move))
    if both:
        raise ExpectationRefused(
            "a Scenario cannot both move and not move: " + ", ".join(both) + " is in both lists"
        )
    expected = Expectation(
        stated_at=stated_at, should_move=list(should_move), must_not_move=list(must_not_move)
    )
    return record.model_copy(update={"expected": expected})


def close_without_compare(
    record: ChangeRecord,
    *,
    status: CloseReason,
    closed_at: str,
    why: str | None = None,
    superseded_by: str | None = None,
) -> ChangeRecord:
    """`wontfix` (with its reason) or `superseded` (with the record that replaces it). On a
    record already closed by a verification, `superseded` sets the pointer and nothing else:
    the verification stands."""
    if status == "superseded" and record.is_closed and record.verification is not None:
        return record.model_copy(update={"superseded_by": superseded_by})
    require(record, status)
    update: dict[str, object] = {"status": status, "closed_at": closed_at}
    if status == "wontfix":
        update["why"] = why
    else:
        update["superseded_by"] = superseded_by
    return record.model_copy(update=update)


def close_by_compare(
    target: TargetPaths,
    record: ChangeRecord,
    *,
    result: VerificationResult,
    run: str,
    baseline: str | None,
    expect: Sequence[str],
    compared_at: str,
    any_env: bool = False,
) -> tuple[ChangeRecord, Comparison]:
    """`pushed → verified | refuted` through `compare` (decision 3); `CloseRefused` saying
    why when the gate does not open, `TransitionRefused` from any status but `pushed`.

    The verifying Run opens the environment the fix was pushed to (phase-8 decision 12): when
    the record's last Connector push names an environment, a Run whose `run.json.adapter`
    records another is refused, unless `any_env` (`--any-env`), which the verification
    records as `environment_mismatch`."""
    from agentdiag.run.compare import CompareUsageError, compare

    require(record, result)
    if record.expected is None:
        raise CloseRefused(
            f"Change record {record.id} states no expected effect: run `agentdiag change "
            f"expect {record.id} --should-move … --must-not-move …` before the verifying Run"
        )
    run_dir = _run_under(target, run, "post-change")
    run_json = _run_json(run_dir)
    created_at = str(run_json.get("created_at") or "")
    if not created_at > record.expected.stated_at:
        raise CloseRefused(
            f"{ORDER_REFUSED}: the expectation is stated at {record.expected.stated_at} and "
            f"Run {run} started at {created_at}; state it again and make a new Run"
        )
    environment = _environment_of(run_json)
    pushed_to = pushed_environment(record)
    mismatch = pushed_to is not None and environment != pushed_to
    if mismatch and not any_env:
        raise CloseRefused(
            f"the record was pushed to {pushed_to!r} and Run {run} ran on {environment!r}; "
            "verify on the environment the fix was pushed to, or pass --any-env"
        )
    baseline_id = _baseline(target, record, baseline)
    baseline_dir = _run_under(target, baseline_id, "pre-change")
    declared = list(dict.fromkeys([*expect, *(DECLARED_BY_A_PUSH if record.pushes else ())]))
    try:
        comparison = compare(baseline_dir, run_dir, declared)
    except CompareUsageError as usage:
        raise CloseRefused(str(usage)) from usage

    undeclared = [
        difference.path for difference in comparison.differences if not difference.declared
    ]
    if undeclared:
        raise CloseRefused(
            f"the comparison of {baseline_id} and {run} shows an undeclared variation, so it "
            "claims no movement: " + ", ".join(undeclared) + "; declare what you meant to vary "
            "with --expect"
        )
    expected = record.expected
    absent = [
        scenario
        for scenario in [*expected.should_move, *expected.must_not_move]
        if scenario not in comparison.scenarios.intersection
    ]
    if absent:
        raise CloseRefused(
            f"the expectation names Scenarios not in both {baseline_id} and {run}: "
            + ", ".join(absent)
        )
    abnormal = _abnormal(comparison, expected)
    if abnormal:
        raise CloseRefused(
            f"the comparison of {baseline_id} and {run} decides nothing for "
            + ", ".join(dict.fromkeys(scenario for scenario, _ in abnormal))
            + ": "
            + "; ".join(phrase for _, phrase in abnormal)
            + "; a Run whose Scores decide (live, or freshly recorded against the pushed "
            "prompt) would make it valid"
        )
    unmet = _unmet(comparison, expected)
    if result == "verified" and unmet:
        raise CloseRefused(
            f"the comparison of {baseline_id} and {run} does not show the expected movement: "
            + "; ".join(unmet)
            + "; close it --refuted if that is the finding"
        )
    if result == "refuted" and not unmet:
        raise CloseRefused(
            f"the comparison of {baseline_id} and {run} shows the expected movement; close it "
            "--verified"
        )
    verification = Verification(
        baseline=baseline_id,
        run=run,
        compared_at=compared_at,
        expect=declared,
        result=result,
        summary=comparison.summary,
        environment=environment,
        environment_mismatch=mismatch,
    )
    closed = record.model_copy(
        update={
            "status": result,
            "closed_at": compared_at,
            "verification": verification,
            "observed": observations(run_dir, run, expected.should_move),
        }
    )
    return closed, comparison


def pushed_environment(record: ChangeRecord) -> str | None:
    """The environment the record's last Connector push wrote, None when it has none or
    that push names no environment (an imported entry's `unknown`): the one a verifying Run
    must open (phase-8 decision 12). A `local` push event is the Run that re-synced, so it
    names no environment a later Run must match."""
    connector = [push for push in record.pushes if push.kind == "connector"]
    if not connector or connector[-1].environment in ("", UNKNOWN):
        return None
    return connector[-1].environment


def _environment_of(run_json: dict[str, object]) -> str:
    """The Adapter environment a Run opened, as its `run.json.adapter` records it;
    `unknown` when it records none."""
    adapter = run_json.get("adapter")
    environment = adapter.get("environment") if isinstance(adapter, dict) else None
    return environment if isinstance(environment, str) and environment else UNKNOWN


def _abnormal(comparison: Comparison, expected: Expectation) -> list[tuple[str, str]]:
    """`(scenario, "<scenario>: <eval> is <verdict counts> in <run>[ (must not move)]")` for
    every delta of a Scenario the expectation names whose post-change side decided no Trial:
    its Scores were all `incomplete`, `unverifiable` or `invalid` (phase-8 decision 14,
    amended: `must_not_move` too, or `_unmet` would read it as moved and leave `--refuted`
    the only close). The counts are the aggregate's abnormal ones, never folded into a rate
    (ADR-0005 §8); an aggregate exists only for an Eval that wrote a Score, so one that
    decided nothing always has an abnormal count to show."""
    from agentdiag.run.compare import render_abnormal

    marks = {
        **dict.fromkeys(expected.must_not_move, " (must not move)"),
        **dict.fromkeys(expected.should_move, ""),
    }
    found: list[tuple[str, str]] = []
    for delta in comparison.deltas:
        side = delta.run
        if delta.scenario not in marks or side is None or side.decidable != 0:
            continue
        found.append(
            (
                delta.scenario,
                f"{delta.scenario}: {delta.name} is {render_abnormal(side)} in "
                f"{comparison.run.run_id}{marks[delta.scenario]}",
            )
        )
    return found


def _unmet(comparison: Comparison, expected: Expectation) -> list[str]:
    """What in the comparison is not the expected movement, one phrase per Scenario."""
    labels: dict[str, list[str]] = {}
    for delta in comparison.deltas:
        labels.setdefault(delta.scenario, []).append(delta.label)
    unmet: list[str] = []
    for scenario in expected.should_move:
        seen = labels.get(scenario, [])
        if "regression" in seen:
            unmet.append(f"{scenario} regressed")
        elif "improvement" not in seen:
            unmet.append(f"{scenario} shows no improvement")
    for scenario in expected.must_not_move:
        moved = sorted({label for label in labels.get(scenario, []) if label != "unchanged"})
        if moved:
            unmet.append(f"{scenario} moved ({', '.join(moved)})")
    return unmet


def observations(run_dir: Path, run: str, scenarios: Sequence[str]) -> list[Observation]:
    """Every Score the post-change Run holds over `scenarios`, as cited observations."""
    from agentdiag.eval.score import ScoresFile

    found: list[Observation] = []
    for scenario in scenarios:
        directory = run_dir / TRIALS_DIRNAME / scenario
        if not directory.is_dir():
            continue
        trials = sorted(
            (int(path.name) for path in directory.iterdir() if path.name.isdigit()),
        )
        for trial in trials:
            path = directory / str(trial) / SCORES_FILE
            if not path.is_file():
                continue
            scores = ScoresFile.model_validate_json(path.read_text(encoding="utf-8"))
            found += [
                Observation(
                    run=run,
                    scenario=scenario,
                    trial=trial,
                    eval=score.eval,
                    eval_id=score.eval_id,
                    verdict=score.verdict,
                    rationale=score.rationale,
                )
                for score in scores.scores
            ]
    return found


def _run_under(target: TargetPaths, run: str, which: RunRole) -> Path:
    if not is_run_id(run):
        raise CloseRefused(
            f"the {which} Run {run!r} is not a Run id (such as 20260923T100000Z-base)"
        )
    directory = target.runs / run
    if not (directory / RUN_RECORD_FILE).is_file():
        raise CloseRefused(f"no {which} Run {run!r} under Target {target.slug}'s {target.runs}")
    return directory


def _run_json(run_dir: Path) -> dict[str, object]:
    try:
        loaded = json.loads((run_dir / RUN_RECORD_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CloseRefused(f"{run_dir} is not a Run: its run.json cannot be read ({exc})") from exc
    return loaded if isinstance(loaded, dict) else {}


def _baseline(target: TargetPaths, record: ChangeRecord, baseline: str | None) -> str:
    """`--baseline`, else the trigger's Run when it is a Run agentdiag drove (decision 3)."""
    if baseline is not None:
        return baseline
    trigger_run = record.trigger.run
    if trigger_run is None:
        raise CloseRefused(
            f"Change record {record.id} was opened from a {record.trigger.kind} with no Run: "
            "name the pre-change Run with --baseline"
        )
    source = _run_json(_run_under(target, trigger_run, "trigger's")).get("source")
    if (source or DEFAULT_RUN_SOURCE) != DEFAULT_RUN_SOURCE:
        raise CloseRefused(
            f"the trigger's Run {trigger_run} is of source {source}: an imported Trace is not "
            "measured as a driven one is; name the pre-change Run with --baseline"
        )
    return trigger_run


def require_pushable(record: ChangeRecord) -> None:
    """`ChangeRefused` unless a push may name `record`: it is `proposed` or `pushed`."""
    if record.status not in PUSHABLE:
        raise ChangeRefused(
            f"Change record {record.id} is {record.status}: a push names a record that is "
            "proposed or pushed (`agentdiag change propose` first)"
        )


def record_connector_push(
    target: TargetPaths,
    record_id: str,
    *,
    environment: str,
    at: str,
    push_record: str,
    fingerprint_before: str | None,
    fingerprint_after: str | None,
) -> Path:
    """A Connector push of the change (ADR-0011 §8, phase-7 decision 13 step 5): the record
    gains a push event pointing at the Push record and moves `proposed → pushed`; a record
    already `pushed` gains the event and keeps its status. Any other status is
    `TransitionRefused` (the push checked it before writing). The record's path."""
    _, record, body = find_record(target, record_id)
    require_pushable(record)
    event = PushEvent(
        kind="connector",
        environment=environment,
        at=at,
        push_record=push_record,
        fingerprint_before=fingerprint_before,
        fingerprint_after=fingerprint_after,
    )
    pushed = record.model_copy(update={"status": "pushed", "pushes": [*record.pushes, event]})
    return write_record(target, pushed, body)


def mark_pushed_by_run(target: TargetPaths, run_record: RunRecord) -> list[Path]:
    """The `local` push event (decision 4): after a Run re-synced onto moved sections, every
    `proposed` record of the Target whose change names one whose local side moved
    (`local_ahead` or `diverged`) moves to `pushed`, with a push event pointing at the Run.
    The one place a Run touches a Change record, and it never writes into the Run. Returns
    the records written."""
    sync = run_record.sync
    if sync.resynced_from is None:
        return []
    moved = {section.id for section in sync.sections if section.direction in LOCAL_SIDE_MOVED}
    fingerprint = run_record.fingerprint
    written: list[Path] = []
    for path, record in load_records(target):
        if record.status != "proposed" or record.change is None:
            continue
        if not moved & set(record.change.sections):
            continue
        event = PushEvent(
            kind="local",
            environment=sync.environment or run_record.adapter.environment or UNKNOWN,
            at=run_record.created_at,
            run=run_record.run_id,
            fingerprint_before=sync.resynced_from,
            fingerprint_after=fingerprint.id if fingerprint is not None else None,
        )
        pushed = record.model_copy(update={"status": "pushed", "pushes": [*record.pushes, event]})
        written.append(write_record(target, pushed, record_body(path)))
    return written


__all__ = [
    "DECLARED_BY_A_PUSH",
    "EXPECT_ALLOWED",
    "LOCAL_SIDE_MOVED",
    "ORDER_REFUSED",
    "PUSHABLE",
    "TRANSITIONS",
    "ChangeRefused",
    "CloseRefused",
    "ExpectationRefused",
    "TransitionRefused",
    "close_by_compare",
    "close_without_compare",
    "mark_pushed_by_run",
    "observations",
    "propose",
    "pushed_environment",
    "record_connector_push",
    "require",
    "require_pushable",
    "state_expectation",
]
