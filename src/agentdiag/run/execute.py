"""Executing a Run: the Trial loop and the order the Run directory is written in.

One reason to change: how a Trial is driven and what a Run writes. Preflight lives in
`agentdiag.run.preflight`, and what a finished Trial's Evals produced lives in
`agentdiag.eval.perform`, so the Judge appears here only as an object built once and
passed down.

**Execution never loses what it already recorded.** A Target that raises, a replay that
does not match, a Ctrl-C mid-Trial — each ends its Trial with a termination reason that
says whose fault it was (D22), scores the Trial's declared Evals with the Verdict that
matches (ADR-0003 §3), and still writes the Scorecard over the Trials that did finish. An
interrupt anywhere after the Run directory exists is caught too: a directory without a
`scorecard.json` would be a Run nothing could read.

The Trial loop itself is fixed by `tests/test_toy_target_trace.py`, which regenerates the
committed Trace fixture through exactly this sequence: `start`, `open`, the `turn` Span
with its two `message` Events around `deliver`, `close`, `end`. Since ticket 06 the Turns
are served by the one driver loop, `agentdiag.simulate.drive`, which lays down the literal
Turns in exactly that sequence and then the `simulate` Turn's; a deviation there breaks the
test by a byte, which is the point.

**agentdiag's own model calls take one client** (phase-5 decision 49): `agentdiag_client`
builds it once per Run — the replay client over the Run's cursor, else the live client of
the Run's one Backend — and the Judges, the Simulated User, the stop check and the reviewer
all call through it.

**A continuing Scenario resumes a session; it never opens one** (phase-5 decision 13). The
Trial of the Scenario it continues keeps its session open instead of closing it, the
continuing Trial rebinds that session to its own Trace, and whichever Trial is the last to
use the session closes it. A session whose Trial failed is not handed on: the continuing
Trial ends with the same termination, saying whose session it was, because running the
rest of a conversation whose start went wrong would score a Scenario nobody wrote. And it
is closed there and then (ticket 20, decision 33): a Claude Code session is a CLI subprocess
and a thread, so a session dropped without its `close` would outlive its Trial.

**Trials run in sweeps** (ticket 08, ADR-0005 §7). `--trials N` runs the whole selection
N times in sequence, sweep n writing `trials/<id>/<n>/` for every selected Scenario, so a
continuing Scenario resumes the session of the same sweep's Trial of the Scenario it
continues, and an interrupt leaves whole sweeps behind rather than one Scenario's Trials
and none of the next's. In replay each Trial re-walks its Scenario's view of the recording
from the start (phase-5 decision 19), which is what makes N Trials replayable from one
recording. An interrupt keeps every finished Trial, records the interrupted one
`incomplete` / `cancelled`, and names each unstarted Trial `cancelled` in `not_run`.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from agentdiag.adapter import Session
from agentdiag.change.lifecycle import mark_pushed_by_run
from agentdiag.eval import stop_when
from agentdiag.eval.diagnosis import is_diagnosis_request
from agentdiag.eval.judge import Judges
from agentdiag.eval.judged import judges as build_judges
from agentdiag.eval.perform import (
    TrialFailure,
    agentdiag_error,
    cancelled,
    overrule,
    perform_evals,
    simulated_user_failed,
    target_error,
    timed_out,
)
from agentdiag.eval.registry import (
    DEFAULT_JUDGE_MODEL,
    DEFAULT_REVIEWER_MODEL,
    DEFAULT_SIMULATED_USER_MODEL,
    REGISTRY,
)
from agentdiag.eval.score import Score, ScoresFile
from agentdiag.model.claude_code import live_client
from agentdiag.model.claude_code_transport import ClaudeCodeTransportError
from agentdiag.model.client import ModelClient, ReplayModelClient, SamplingSupport
from agentdiag.model.replay import (
    Recording,
    RecordingNotConsumed,
    ReplayCursor,
    ReplayMismatch,
)
from agentdiag.report.html import write_report
from agentdiag.run import index as run_index
from agentdiag.run.directory import RunDirectory, new_run_id, render_json
from agentdiag.run.locate import JUDGEMENT_FILE, TRACE_FILE
from agentdiag.run.manifest import DEFAULT_TURN_TIMEOUT_S
from agentdiag.run.preflight import (
    PREFLIGHT_EXIT,
    Plan,
    PlannedScenario,
    PreflightFailed,
    preflight,
)
from agentdiag.run.record import (
    NotRun,
    RunRecord,
    RunStamp,
    ScenarioSummary,
    SelectionSection,
    SyncSection,
    build_run_record,
    iso_utc,
)
from agentdiag.run.scorecard import (
    Scorecard,
    aggregate,
    exit_code,
    merged_sampling,
    render_summary,
)
from agentdiag.scenario.models import Scenario, SimulateSpec
from agentdiag.scenario.select import Selection, render_dry_run
from agentdiag.simulate.drive import (
    TRACE_FIDELITY,
    Driven,
    TurnTimedOut,
    drive,
    turn_fidelity,
)
from agentdiag.simulate.stop import StopCheck
from agentdiag.simulate.user import ModelSimulatedUser, SimulatedUser
from agentdiag.sync.check import rebuilt
from agentdiag.sync.compare import status_text
from agentdiag.sync.fingerprint import Fingerprint, write_fingerprint
from agentdiag.trace import TraceWriter, read_trace
from agentdiag.workspace import TargetPaths


class JudgingOptions(BaseModel):
    """What `run` and `rescore` both take: where, which Scenarios, which Judge, how printed."""

    target: TargetPaths
    """The Target the Run is of, as the Workspace resolved it (ADR-0013): its Manifest,
    its Suites and the `runs/` the Run is written under."""
    scenario: list[str] = []
    tag: list[str] = []
    suite: list[str] = []
    """Each repeatable; values of one kind OR, kinds AND (D31). None given: all."""

    json_output: bool = False
    judge_model: str = DEFAULT_JUDGE_MODEL
    judge_effort: str | None = None
    reviewer_model: str = DEFAULT_REVIEWER_MODEL
    """The Simulated User reviewer's model (D14, D28): run again by a rescore, too."""

    replay: Path | None = None
    """Hidden: a recorded model exchange file, so a Run reproduces without credentials."""

    @property
    def selection(self) -> Selection:
        return Selection(scenario=self.scenario, tag=self.tag, suite=self.suite)


class RunOptions(JudgingOptions):
    """What the caller asked `run` for. The CLI builds one of these and nothing else."""

    dry_run: bool = False
    """Preflight, then print what would run instead of running it (ticket 07)."""

    trials: int = Field(default=1, ge=1)
    """Trials per selected Scenario (ADR-0005 §7, ticket 08)."""

    simulated_user_model: str = DEFAULT_SIMULATED_USER_MODEL
    simulated_user_effort: str | None = None
    simulated_user_temperature: float | None = None
    """Declared sampling for the Simulated User, recorded as sent and as the API answered
    it per call (D12, phase-5 decision 53). Nothing else is declarable in v1."""

    no_resync: bool = False
    """On a broken Sync, run labelled `broken` against the old Fingerprint (D30, ADR-0008)."""

    strict: bool = False
    """On a broken Sync, refuse to run: exit 3 with the section table (D30)."""

    live: bool = False
    """`--live`: acknowledge an Adapter environment of side-effect class `live`, which is
    refused without it, and record that it was given (ADR-0001 point 5, phase-8 decision 9)."""

    environment: str | None = None
    """`--env`: the Adapter environment the Run opens, the Manifest's default when None
    (phase-8 decision 12). The Sync checked, the Connector twin read and `run.json.adapter`
    follow it; a name the Adapter block does not declare is refused at preflight."""


class RunExit(BaseModel):
    """What the `run` command returns: where the Run went, what it said, how it exits.

    Not "outcome": that word belongs to the Verdict in `CONTEXT.md`, and this is a fact
    about the command, not a judgement about the Target.
    """

    run_dir: Path | None = None
    scorecard: Scorecard | None = None
    code: int
    message: str | None = None


def run(
    options: RunOptions,
    *,
    clock: Callable[[], int] | None = None,
    run_id: str | None = None,
    stamp: RunStamp | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> RunExit:
    """Execute one Run: preflight, the Trials, the Scorecard (ADR-0005 §2, D29).

    `clock`, `run_id` and `stamp` exist so a test or a fixture can reproduce a Run byte for
    byte, and so `agentdiag serve` can name a Run before it starts (the UI follows the id it
    was given back). `cancelled` is the UI's cancel: asked before every Trial, and once it
    answers True no further Trial starts; the Trial in progress finishes, and every Trial
    not started is named `cancelled` in `not_run`, as a Ctrl-C between Trials leaves them.
    """
    # One cursor over one recording, opened before preflight so the Adapter it builds and
    # the Judge built below draw from the same walk: `assert_consumed()` is then asked
    # once per Trial across both (D12). Preflight itself only checks; it never opens.
    try:
        cursor = ReplayCursor(Recording.load(options.replay)) if options.replay else None
    except (OSError, ValueError) as exc:
        return RunExit(run_dir=None, scorecard=None, code=PREFLIGHT_EXIT, message=str(exc))

    try:
        plan = preflight(
            options.target,
            options.selection,
            options.replay,
            cursor=cursor,
            judge_model=options.judge_model,
            judge_effort=options.judge_effort,
            dry_run=options.dry_run,
            simulated_user_model=options.simulated_user_model,
            simulated_user_effort=options.simulated_user_effort,
            simulated_user_temperature=options.simulated_user_temperature,
            reviewer_model=options.reviewer_model,
            no_resync=options.no_resync,
            strict=options.strict,
            live=options.live,
            environment=options.environment,
        )
    except PreflightFailed as failure:
        return RunExit(run_dir=None, scorecard=None, code=PREFLIGHT_EXIT, message=str(failure))

    warn(plan.warnings)
    sync = plan.sync or SyncSection(status="not_checked", reason="no_fingerprint")

    if options.dry_run:
        # Preflight passed, so this is what a Run would execute; nothing opens, nothing
        # is written — not even a re-synced Fingerprint — and no model is called (ticket 07).
        count, problems = parsed_evals(plan)
        if problems:
            return RunExit(code=PREFLIGHT_EXIT, message="\n".join(problems))
        return RunExit(
            code=0,
            message=dry_run_message(
                plan,
                sync,
                options.json_output,
                count,
                environment=plan.adapter.environment if options.environment else None,
            ),
        )

    client = agentdiag_client(cursor, plan)
    judges = judges_for(client, plan)

    stamp = stamp or RunStamp.now(options.target.directory)
    identifier = run_id or new_run_id(stamp.created_at)
    directory = RunDirectory.create(options.target, identifier)
    # After the directory, so a Run that could not create one leaves the tree untouched;
    # before `run.json`, which records the Fingerprint the Run proceeds under.
    fingerprint, sync = resynced(options.target, plan, stamp, sync)
    run_record = _record(options, plan, identifier, stamp, sync, fingerprint)
    directory.write_run_record(run_record)
    # A re-sync onto edited files is the `local` push of every proposed Change record that
    # names a moved section (ADR-0012 §2, phase-7 decision 4); the Run itself is not touched,
    # and a record that cannot be written is a warning, never a failed Run.
    try:
        mark_pushed_by_run(options.target, run_record)
    except (OSError, ValueError) as exc:
        warn([f"the Change records of Target {options.target.slug} were not updated: {exc}"])

    own = OwnCalls(plan, client, judges)
    scores_by_trial, skipped, sampled = _trials(
        options, plan, directory, identifier, clock, own, cursor, cancelled
    )

    scorecard = aggregate(
        identifier,
        sync,
        options.trials,
        scores_by_trial,
        [*plan.not_run, *skipped],
        suites=plan.suite_names,
        simulated_user_sampling=merged_sampling(
            plan.simulated_user.sampling if plan.simulated_user else None, sampled
        ),
    )
    return finish(
        options.target, directory, scorecard, options.json_output, plan.selected_set.several_suites
    )


def finish(
    target: TargetPaths,
    directory: RunDirectory,
    scorecard: Scorecard,
    json_output: bool,
    several_suites: bool,
) -> RunExit:
    """Write the Scorecard and the Report, index the Run, and say what the command prints
    and exits with: `run`'s ending and `rescore`'s alike, so the two can never report a Run
    differently.

    The Report (`report.html`, ADR-0005 §11, phase-7 decision 20) is rendered from the Run
    directory's files once the Scorecard is among them; a Report that cannot be written is
    a warning, like a failure to index.

    The Run is indexed only now, once its Scorecard is on disk (phase-5 decision 63), into
    the Index of the Workspace the Target was resolved in: a Run directory's root is never
    inferred from its path. A failure to index is a warning from `index.record`, and the
    exit code stays the Run's.
    """
    directory.write_scorecard(scorecard)
    write_report(directory.path)
    run_index.record(target, directory.path)
    message = (
        render_json(scorecard).rstrip("\n")
        if json_output
        else render_summary(scorecard, with_suites=several_suites)
    )
    return RunExit(
        run_dir=directory.path,
        scorecard=scorecard,
        code=exit_code(scorecard),
        message=message,
    )


def agentdiag_client(cursor: ReplayCursor | None, plan: Plan) -> ModelClient | None:
    """The Run's one client for agentdiag's own model calls (phase-5 decision 49), or None
    when the selection makes none — no judged Eval and no `simulate` Turn (D14).

    Built here rather than per Trial: the model and the prompt are Run configuration, and
    a Judge rebuilt per Trial could differ between Trials of one Run. In replay the client
    draws from the same cursor the Target's transport walks, so one recording answers the
    Target, the Simulated User and the Judge alike. A live client is the one `plan.backend`
    names, built from what preflight resolved (`plan.credentials`): the environment and the
    CLI are never asked a second time (ticket 19).
    """
    if plan.judge is None and plan.simulated_user is None:
        return None
    if cursor is not None:
        return ReplayModelClient(cursor)
    if plan.backend is None:
        raise ValueError("a live Run that calls a model reached execution with no Backend")
    return live_client(plan.backend, plan.credentials)


def judges_for(client: ModelClient | None, plan: Plan) -> Judges | None:
    """The Run's Judges over its one client, or none when it builds no Judge; each context
    they judge reads the Manifest's prompt texts preflight read (note 7)."""
    if client is None or plan.judge is None:
        return None
    return build_judges(client, plan.judge, manifest_prompts=plan.manifest_prompts)


def resynced(
    target: TargetPaths, plan: Plan, stamp: RunStamp, sync: SyncSection
) -> tuple[Fingerprint | None, SyncSection]:
    """The Fingerprint in force for the Run and the Sync `run.json` records (decision 14).

    On a broken Sync that re-syncs: the one rebuilt from what preflight observed, built at
    the Run's own moment and naming the one it replaces, written to `fingerprint.json`.
    A rebuild whose sections hash to the current Fingerprint's id changes nothing — no
    Fingerprint may name itself as the one it came from — so the file is left as it is
    and the Run records no re-sync. Otherwise the one found. The previous one lives on in
    every earlier `run.json`.
    """
    if not plan.resync or plan.sync_check is None:
        return plan.fingerprint, sync
    fresh = rebuilt(plan.sync_check, built_at=iso_utc(stamp.created_at), resynced=True)
    if plan.fingerprint is not None and fresh.id == plan.fingerprint.id:
        return plan.fingerprint, sync.model_copy(update={"resynced_from": None})
    write_fingerprint(target, fresh)
    return fresh, sync


def parsed_evals(plan: Plan) -> tuple[int, list[str]]:
    """Every selected Scenario's Eval declarations read by their Evals' parameter models, as
    a Trial would read them: how many, and the error of each that does not parse. What lets
    `--dry-run` stand in for a Run's `invalid / scenario` check offline (walkthrough friction
    27)."""
    count, problems = 0, []
    for planned in plan.selected:
        for declaration in planned.scenario.evals:
            spec = REGISTRY.get(declaration.eval)
            if spec is None:
                continue
            count += 1
            try:
                spec.parameters(declaration)
            except ValidationError as exc:
                problems.append(
                    f"error: Scenario {planned.scenario.id!r}: {declaration.eval}: "
                    + "; ".join(str(error["msg"]) for error in exc.errors())
                )
    return count, problems


def dry_run_message(
    plan: Plan,
    sync: SyncSection,
    json_output: bool,
    parsed: int | None = None,
    *,
    environment: str | None = None,
) -> str:
    """What `run --dry-run` prints: the selection, how many Eval declarations parsed, then
    the Sync the Run would find, which a dry run reports and never acts on (decision 14).

    `environment` is given when `--env` chose one (phase-8 decision 12): the dry run names
    it on a line of its own before the Sync (`environment` in the JSON), so the environment a
    Run would open is read before it opens. Without `--env` the output is as it was."""
    listing = render_dry_run(plan.selected_set, json_output=json_output)
    # Nothing was re-synced: the record says only what a Run would find.
    found = sync.model_copy(update={"resynced_from": None})
    if json_output:
        extra: dict[str, object] = {} if parsed is None else {"evals_parsed": parsed}
        if environment is not None:
            extra["environment"] = environment
        return json.dumps(
            {**json.loads(listing), **extra, "sync": found.model_dump(mode="json")}, indent=2
        )
    would = "; a Run re-syncs first" if plan.resync else ""
    lines = listing.split("\n")
    if parsed is not None:
        noun = "declaration" if parsed == 1 else "declarations"
        lines.insert(len(lines) - 1, f"evals {parsed} {noun}, every parameter parsed")
    if environment is not None:
        lines.append(f"environment {environment}")
    return "\n".join(lines) + f"\nsync {status_text(found.model_dump(mode='json'))}{would}"


class OwnCalls:
    """The model calls agentdiag makes for itself in a Trial, all over the Run's one client
    (decision 49): the Judges, and the Simulated User and the stop check a `simulate` Turn
    needs. Not the Target's: the Target is the agent."""

    def __init__(self, plan: Plan, client: ModelClient | None, judges: Judges | None) -> None:
        self.plan = plan
        self.client = client
        self.judges = judges

    def simulated_user_for(self, scenario: Scenario) -> Callable[[SimulateSpec], SimulatedUser]:
        def build(spec: SimulateSpec) -> SimulatedUser:
            if self.client is None or self.plan.simulated_user is None:
                raise ValueError("a simulate Turn reached execution with no Simulated User")
            return ModelSimulatedUser(self.client, self.plan.simulated_user, spec, scenario)

        return build

    def stop_check(self, scenario: Scenario) -> StopCheck:
        """The stop check; its `judged` predicate asks the Run's default Judge (decision 51)."""
        if self.judges is None:
            return StopCheck()
        return StopCheck(
            stop_when.checker(
                scenario,
                self.judges.default,
                notes=self.judges.notes,
                tool_kinds=self.plan.tool_kinds,
            )
        )


def warn(warnings: list[str]) -> None:
    """Print load warnings to stderr, so `--json` stdout stays machine-readable (D29)."""
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)


def _trials(
    options: RunOptions,
    plan: Plan,
    directory: RunDirectory,
    run_id: str,
    clock: Callable[[], int] | None,
    own: OwnCalls,
    cursor: ReplayCursor | None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[list[tuple[str, int, list[Score]]], list[NotRun], list[dict[str, SamplingSupport]]]:
    """Every sweep of Trials in turn, whatever a cancellation left unstarted, and each
    driven Trial's Simulated User sampling (decision 53).

    The interrupt guard is around the whole loop, not only around `_trial`: a Ctrl-C
    between Trials, or while a Trial's `scores.json` is being written, must still leave a
    Run whose Scorecard can be read. `cancelled` is asked before each Trial (the UI's
    cancel, ticket 16); a session a continuing Scenario would have resumed is closed then,
    since no Trial is left to close it.
    """
    scores_by_trial: list[tuple[str, int, list[Score]]] = []
    sampled: list[dict[str, SamplingSupport]] = []
    order = sweeps(options.trials, plan.selected)
    sessions = Sessions.for_selection(plan.selected)
    sweep = 1

    try:
        for number, planned in order:
            if number != sweep:
                # A new sweep: its continuing Scenarios resume its own sessions (decision 13).
                sweep, sessions = number, Sessions.for_selection(plan.selected)
            if cancelled is not None and cancelled():
                sessions.close_all()
                break
            scenario = planned.scenario
            scores, stopped, sampling = _trial(
                plan, directory, run_id, planned, number, clock, own, cursor, sessions
            )
            sampled.extend(sampling)
            directory.write_scores(
                scenario.id, number, ScoresFile(scenario=scenario.id, trial=number, scores=scores)
            )
            scores_by_trial.append((scenario.id, number, scores))
            if stopped:
                break
    except KeyboardInterrupt:
        # Between Trials, or mid-write: the Trials already recorded stand, and the rest
        # are named as cancelled rather than silently missing.
        pass

    return (
        scores_by_trial,
        unstarted(order, scores_by_trial, numbered=options.trials > 1),
        sampled,
    )


def sweeps(trials: int, selected: Sequence[PlannedScenario]) -> list[tuple[int, PlannedScenario]]:
    """(Trial number, Scenario) in execution order: `trials` sweeps over the selection.

    A sweep is one pass over every selected Scenario, in Suite order; sweep n writes Trial
    n of each. Not "round", which `CONTEXT.md` keeps off Turns' sense of the word.
    """
    return [(number, planned) for number in range(1, trials + 1) for planned in selected]


def unstarted(
    order: Sequence[tuple[int, PlannedScenario]],
    scored: Sequence[tuple[str, int, list[Score]]],
    *,
    numbered: bool,
    detail: str | None = None,
) -> list[NotRun]:
    """Each Trial of `order` that produced no Scores, named `cancelled` (ADR-0005 §8)."""
    recorded = {(scenario, number) for scenario, number, _ in scored}
    return [
        NotRun(
            scenario=planned.scenario.id,
            suite=planned.suite.name,
            reason="cancelled",
            detail=detail,
            trial=number if numbered else None,
        )
        for number, planned in order
        if (planned.scenario.id, number) not in recorded
    ]


def _record(
    options: RunOptions,
    plan: Plan,
    run_id: str,
    stamp: RunStamp,
    sync: SyncSection,
    fingerprint: Fingerprint | None,
) -> RunRecord:
    """Freeze the configuration into `run.json` (ADR-0005 §3), the selection normalised.

    Slice 4 builds the Judge configuration during preflight and passes it here as `judge`;
    `run.json` is written once, before any Trial, so it cannot be amended afterwards. The
    Sync and the Fingerprint are the ones preflight found, or the one a re-sync rebuilt.
    """
    return build_run_record(
        run_id=run_id,
        stamp=stamp,
        target=options.target,
        manifest=plan.manifest,
        adapter=plan.description,
        selection=SelectionSection.of(plan.selected_set.selection),
        scenarios=[
            ScenarioSummary.of(planned.scenario, plan.defaulted) for planned in plan.selected
        ],
        not_run=plan.not_run,
        judge=plan.judge,
        simulated_user=plan.simulated_user,
        trials=options.trials,
        sync=sync,
        fingerprint=fingerprint,
    )


# --- one Trial (the sequence tests/test_toy_target_trace.py pins) ---


def _trial(
    plan: Plan,
    directory: RunDirectory,
    run_id: str,
    planned: PlannedScenario,
    number: int,
    clock: Callable[[], int] | None,
    own: OwnCalls,
    cursor: ReplayCursor | None,
    sessions: Sessions,
) -> tuple[list[Score], bool, list[dict[str, SamplingSupport]]]:
    """Run Trial `number` of one Scenario; return its Scores, whether the caller cancelled,
    and its Simulated User sampling.

    Five failures are told apart because they ask for five different things (D22): a
    Target that raised is `target_error` and a re-run; a Turn past its `turn_timeout` is
    `timeout`, also a re-run (decision 56); the Simulated User failing is
    `simulated_user_error`, never the Target's (decision 48); agentdiag's own replay
    failing, or its Claude Code Backend failing to carry the Target's request (ticket 20,
    decision 31), is `agentdiag_error` and a fix to agentdiag (or a key exported to route
    around it); a Ctrl-C is `cancelled` and means nothing about the Target at all. A failed
    Trial closes the session it holds, since nobody else will (decision 33).

    `assert_consumed()` runs only after a Trial that finished as its Scenario meant it to,
    and only after its Evals, because the Judge draws from the same recording as the
    Target: asked any earlier it would report the Judge's own exchange as one nobody
    wanted. It is asked in two halves around the Diagnosis (`CursorCheck`): the Evals'
    exchanges can overrule the Scores, the Diagnosis's never can (D24). A recording with
    exchanges left over after a Target crashed is a consequence of the crash, not a
    finding: reporting it would blame agentdiag for the Target's failure and bury the
    `target_error` that a reader actually needs.
    """
    scenario = planned.scenario
    trial_dir = directory.trial_dir(scenario.id, number)
    path = trial_dir / TRACE_FILE
    writer = TraceWriter(path, clock=clock)
    trace_id = f"{run_id}/{scenario.id}/{number}"

    failure: TrialFailure | None = None
    driven: Driven | None = None
    stopped = False
    session: Session | None = None
    if cursor is not None:
        # This Trial's view of the recording: its own Scenario's exchanges and the
        # unscoped ones, walked from the start (phase-5 decision 19).
        cursor.begin(scenario.id)

    try:
        writer.start(
            trace_id=trace_id,
            scenario=scenario.id,
            run=run_id,
            trial=number,
            continues=scenario.continues,
        )
        failure = sessions.inherited_failure(planned)
        if failure is None:
            session = sessions.resume(planned, writer) or plan.adapter.open(
                writer, fixtures=planned.fixtures
            )
            driven = drive(
                scenario,
                session,
                writer,
                simulated_user_for=own.simulated_user_for(scenario),
                stop_check=own.stop_check(scenario),
                turn_timeout_s=plan.description.turn_timeout_s or DEFAULT_TURN_TIMEOUT_S,
                fidelity=turn_fidelity(plan.description.fidelity),
            )
            if driven.termination == "simulated_user_error":
                failure = simulated_user_failed(driven.detail or "the Simulated User failed")
            else:
                held, session = session, None
                sessions.finished(planned, held)
    except TurnTimedOut as exc:
        # Its `error` Event is already inside the Turn it ended (decision 56).
        failure = timed_out(str(exc))
    except (ReplayMismatch, RecordingNotConsumed, ClaudeCodeTransportError) as exc:
        failure = agentdiag_error(_record_error(writer, exc))
    except KeyboardInterrupt:
        failure, stopped = cancelled(), True
        writer.event(
            "error", actor="agentdiag", error_type="KeyboardInterrupt", message="cancelled"
        )
    except Exception as exc:
        failure = target_error(_record_error(writer, exc))
    finally:
        if failure is not None:
            # The session this Trial holds (opened, or resumed from the Trial it continues),
            # and one its chain kept open when the Trial failed before resuming it.
            for unclosed in (session, sessions.failed(planned, failure)):
                if unclosed is not None:
                    _close_quietly(unclosed)
        writer.end(
            failure.termination if failure else driven.termination if driven else "completed",
            error=failure.detail if failure else None,
            # What ended a conversation that ended as its Scenario meant (decision 47); a
            # failure's words are in `error` already.
            detail=driven.detail if driven is not None and failure is None else None,
        )
        writer.close()

    scores = perform_evals(
        scenario,
        trace_events=read_trace(path),
        # The Fidelity the Adapter achieves grounds a Score that read no Span: an HTTP
        # Trial's is `observed` or `reconstructed`, never the in-process `instrumented`.
        fidelity=plan.description.fidelity,
        failure=failure,
        judges=own.judges,
        judgement_path=trial_dir / JUDGEMENT_FILE,
        forbidden_phrases=plan.forbidden_phrases,
        tool_kinds=plan.tool_kinds,
        # A recording with exchanges left over after a clean Trial means the Trial took a
        # different path than the one recorded: agentdiag's problem, never the Target's.
        # Asked only after a Trial that finished (the docstring says why), and around the
        # Diagnosis, which never touches a Score (D24).
        recording=CursorCheck(cursor) if failure is None and cursor is not None else None,
        clock=clock,
    )
    return scores, stopped, driven.sampling if driven is not None else []


class CursorCheck:
    """The one cursor's consumption check, split around the Trial's Diagnosis.

    One cursor covers the Target and the Judge, so this asks about both at once, and only
    about this Trial's view: another Scenario's exchanges are not its to use. The Scores are
    overruled in memory rather than recomputed: `judgement.jsonl` is already written, it is
    append-only, and what the Judge said belongs beside the Score even when agentdiag's own
    recording turned out to be wrong (ADR-0003 §7). A Diagnosis exchange left unused is
    said on stderr and in a `note`, and overrules nothing.

    `only` narrows the question to the exchanges it picks: a rescore makes no Target call,
    so it asks about the Judge's exchanges and nothing else (ticket 08).
    """

    def __init__(
        self, cursor: ReplayCursor, only: Callable[[dict[str, Any]], bool] | None = None
    ) -> None:
        self.cursor = cursor
        self.only = only or (lambda request: True)

    def before_diagnosis(self, scores: list[Score]) -> list[Score]:
        try:
            self.cursor.assert_consumed(
                lambda request: self.only(request) and not is_diagnosis_request(request)
            )
        except RecordingNotConsumed as exc:
            return overrule(scores, str(exc))
        return scores

    def after_diagnosis(self) -> str | None:
        try:
            self.cursor.assert_consumed(is_diagnosis_request)
        except RecordingNotConsumed as exc:
            left = f"The Diagnosis did not use its recorded exchange; no Score is affected. {exc}"
            warn([left])
            return left
        return None


Key = tuple[str, str]
"""A selected Scenario, as (Suite name, Scenario id): `continues` names one in its Suite."""


class Sessions:
    """The Adapter sessions continuing Scenarios resume, and when each one closes.

    A session belongs to the chain of Scenarios that share it: the one that opened it and
    every selected Scenario that continues it, directly or through another. It is closed
    by the last Trial of its chain in the Run, and never earlier (decision 13).
    """

    def __init__(self, chain_of: dict[Key, Key], last_of: dict[Key, Key]) -> None:
        self._chain_of = chain_of
        self._last_of = last_of
        self._open: dict[Key, Session] = {}
        self._failed: dict[Key, tuple[str, TrialFailure]] = {}

    @classmethod
    def for_selection(cls, selected: list[PlannedScenario]) -> Sessions:
        chain_of: dict[Key, Key] = {}
        last_of: dict[Key, Key] = {}
        for planned in selected:
            continues = planned.scenario.continues
            if continues is None:
                chain = planned.key
            else:
                continued = (planned.suite.name, continues)
                chain = chain_of.get(continued, continued)
            chain_of[planned.key] = chain
            last_of[chain] = planned.key
        return cls(chain_of, last_of)

    def _chain(self, planned: PlannedScenario) -> Key:
        return self._chain_of.get(planned.key, planned.key)

    def inherited_failure(self, planned: PlannedScenario) -> TrialFailure | None:
        """Why this continuing Scenario cannot run: its session's Trial failed."""
        continues = planned.scenario.continues
        if continues is None:
            return None
        failed = self._failed.get(self._chain(planned))
        if failed is None:
            return None
        owner, failure = failed
        return failure.model_copy(
            update={
                "detail": (
                    f"it continues {continues!r}, and the session it would resume "
                    f"ended {failure.termination} in {owner!r}: {failure.detail}"
                )
            }
        )

    def resume(self, planned: PlannedScenario, writer: TraceWriter) -> Session | None:
        """The session this Scenario continues, now emitting into its Trace; None to open."""
        if planned.scenario.continues is None:
            return None
        chain = self._chain(planned)
        session = self._open[chain]
        # Rebound before it leaves `_open`: a rebind that fails leaves the session where
        # `failed` finds it, and the failed Trial closes it (decision 33).
        session.rebind(writer)
        del self._open[chain]
        return session

    def finished(self, planned: PlannedScenario, session: Session) -> None:
        """Close the session after its chain's last Trial; otherwise keep it for the next."""
        chain = self._chain(planned)
        if self._last_of.get(chain, planned.key) == planned.key:
            session.close()
        else:
            self._open[chain] = session

    def close_all(self) -> None:
        """Close every session still kept for a continuing Scenario: the Run stops here
        (a cancel), and nobody is left to close them (decision 33)."""
        for session in self._open.values():
            _close_quietly(session)
        self._open.clear()

    def failed(self, planned: PlannedScenario, failure: TrialFailure) -> Session | None:
        """A failed Trial hands its session to nobody (the module docstring says why); the
        session its chain still kept open, if any, is returned for the Trial to close."""
        chain = self._chain(planned)
        dropped = self._open.pop(chain, None)
        self._failed.setdefault(chain, (planned.scenario.id, failure))
        return dropped


def _close_quietly(session: Session) -> None:
    """Close a failed Trial's session, best effort (decision 33): the Trial already failed
    for a reason of its own, so an error from `close` is said on stderr and goes no further."""
    try:
        session.close()
    except Exception as exc:
        warn([f"closing a failed Trial's session: {type(exc).__name__}: {exc}"])


def _record_error(writer: TraceWriter, exc: BaseException) -> str:
    """One `error` Event, and the text the Trace's `trace/end` carries (ADR-0004 §1)."""
    message = str(exc) or type(exc).__name__
    writer.event("error", actor="agentdiag", error_type=type(exc).__name__, message=message)
    return message


__all__ = [
    "TRACE_FIDELITY",
    "CursorCheck",
    "JudgingOptions",
    "RunExit",
    "RunOptions",
    "agentdiag_client",
    "dry_run_message",
    "finish",
    "judges_for",
    "resynced",
    "run",
    "sweeps",
    "unstarted",
    "warn",
]
