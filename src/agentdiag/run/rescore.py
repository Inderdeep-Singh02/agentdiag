"""`rescore`: judge a stored Run's Traces again, into a new Run (D34, ADR-0005 §4).

Its own module rather than a mode of `execute.run`, because the two change for different
reasons: `run` drives a Target and owns the Trial loop's exact Event sequence, which a
committed fixture pins by the byte; `rescore` never touches a Target, opens no Adapter
session and writes no Trace. What they share — preflight, the Judge built once per Run,
the recording check, `run.json`'s Scenario summaries and the Scorecard's ending — is
imported from `execute`, so the two cannot drift in how a Run is judged or reported.

What a rescore is, and why each piece is where it is:

- **A new Run, never an edit.** The source directory is read and nothing in it is opened
  for writing; a test asserts its bytes are unchanged. The new `run.json` carries
  `traces_from`, and its Trials hold `judgement.jsonl` and `scores.json` and no
  `trace.jsonl`: `run.locate` follows `traces_from` to the Trace, so `show`, `export` and
  the index read a rescored Trial unchanged.
- **The current configuration judges; the source's made the Traces.** `manifest`, `judge`
  and `price_table` are today's, because tuning the Judge or an Eval's threshold is the
  point; `adapter`, `fingerprint`, `sync`, `simulated_user` and `git.target` are copied from
  the source, because the Traces were made under them and a comparison must say so.
- **The Scenarios are the current Suites', restricted to what the source ran**, so an
  edited threshold or a newly declared Eval applies; the selection flags narrow that
  further. Each Scenario keeps the source's Trials, and mechanical Evals are re-run too:
  one pass over one Trace, the same code a Run uses.
- **`traces_from` names the Run that holds the Traces.** A rescore of a rescore points at
  the original, so every rescore of one Run names one source and `compare` can say that
  any two of them share Traces.
- **A Trial's ending is read back from its Trace.** A source Trial that ended `cancelled`
  or `target_error` still scores `incomplete` with that reason, because the Trace records
  how it ended and the Verdicts follow from that, not from the Judge.

- **`--eval` judges what no Suite declares** (ticket 26, phase-6 decision 36). An imported
  Run's Scenario (`imported-<slug>`) is in no Suite, so `--eval <name>` (or `<name>=<value>`,
  the short form's primary parameter) names the Evals to apply to every Trial whose
  Scenario no Suite declares; a Scenario a Suite declares keeps its Suite's Evals. The
  Scenario is rebuilt from the source's `run.json` summary, its Turns the user messages its
  Trace holds.

In replay the Judge's exchanges come from the recording like any other, walked per Trial
from the start (phase-5 decision 19); the Target's exchanges in a whole-Suite recording are
not the rescore's to use, so only the Judge's are asked about when a Trial ends.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import yaml
from pydantic import Field, ValidationError

from agentdiag.eval.judge import Judges
from agentdiag.eval.judged import is_rescored_request
from agentdiag.eval.perform import (
    TrialFailure,
    agentdiag_error,
    cancelled,
    perform_evals,
    simulated_user_failed,
    target_error,
    timed_out,
)
from agentdiag.eval.registry import REGISTRY
from agentdiag.eval.score import Score, ScoresFile
from agentdiag.model.claude_code import Backend
from agentdiag.model.client import ModelClient
from agentdiag.model.replay import Recording, ReplayCursor
from agentdiag.run.directory import RunDirectory, new_run_id
from agentdiag.run.execute import (
    CursorCheck,
    JudgingOptions,
    RunExit,
    agentdiag_client,
    finish,
    judges_for,
    sweeps,
    unstarted,
    warn,
)
from agentdiag.run.locate import (
    JUDGEMENT_FILE,
    RUN_RECORD_FILE,
    SCORECARD_FILE,
    TrialNotFound,
    holding_target,
    locate_run,
    recorded_trials,
    trace_path,
)
from agentdiag.run.manifest import ManifestError, ManifestNotFound, load_manifest
from agentdiag.run.preflight import (
    PREFLIGHT_EXIT,
    Plan,
    PlannedScenario,
    PreflightFailed,
    load_suites,
    preflight,
    undeclared,
)
from agentdiag.run.record import (
    NotRun,
    RunRecord,
    RunStamp,
    ScenarioSummary,
    SelectionSection,
    build_run_record,
)
from agentdiag.run.scorecard import Scorecard, aggregate
from agentdiag.scenario.models import EvalDeclaration, Scenario
from agentdiag.trace import Event, read_trace
from agentdiag.types import FINISHED_TERMINATIONS
from agentdiag.workspace import TargetPaths, Workspace, WorkspaceError


class RescoreOptions(JudgingOptions):
    """What the caller asked `rescore` for. The CLI builds one of these and nothing else.

    The selection flags narrow the Scenarios the source ran; none given: all of them.
    """

    run: str
    """The source Run: an id under the Target's `runs/`, or a path to its directory.
    `locate_source` chooses `target` as the Target whose `runs/` holds it."""

    evals: list[str] = Field(default_factory=list)
    """`--eval`: the Evals to apply to every Trial whose Scenario no Suite declares, each
    `name` or `name=<value>` (decision 36)."""


class SourceUnderAnotherTarget(WorkspaceError):
    """`--target` names one Target and the source Run is under another's `runs/`."""


def locate_source(
    workspace: Workspace, run: str, named: TargetPaths | None
) -> tuple[TargetPaths, Path]:
    """The source Run's directory and the Target the rescore belongs to.

    A rescore is always written beside its source, under the same `runs/`, because that is
    where `traces_from` finds the Traces (`locate.trace_path`): so the Target whose `runs/`
    holds the source wins, and a `--target` naming another is refused naming both rather
    than obeyed. The id is looked for under every Target for the same reason. A source
    directory under no Target's `runs/` (a path from elsewhere) is rescored into the named
    Target, or the one Target. `TrialNotFound` or a `WorkspaceError` otherwise.
    """
    source_dir = locate_run(workspace, run)
    holder = holding_target(workspace.root, source_dir)
    if holder is not None and named is not None and holder.slug != named.slug:
        raise SourceUnderAnotherTarget(
            f"Run {source_dir.name} is under Target {holder.slug} ({holder.runs}), not "
            f"Target {named.slug}; a rescore is written beside its source, so name "
            f"--target {holder.slug} or leave --target out"
        )
    return holder or named or workspace.resolve(None), source_dir


def rescore(
    options: RescoreOptions,
    *,
    clock: Callable[[], int] | None = None,
    run_id: str | None = None,
    stamp: RunStamp | None = None,
    client: ModelClient | None = None,
    client_backend: Backend | None = None,
) -> RunExit:
    """Judge the source Run's Traces again under the current configuration, as a new Run.

    `clock`, `run_id` and `stamp` exist so a fixture can reproduce a rescore byte for byte;
    `client` (with the `client_backend` it takes) answers the Judge's calls instead of the
    one `--replay` or the credentials would build, which is how `scripts/record_fixtures.py
    --helpdesk` captures the imported Trace's Judge answers; nothing else passes them. Exit
    codes are `run`'s (D32).
    """
    if (client is None) != (client_backend is None):
        raise ValueError("rescore takes a client together with the Backend it takes")
    try:
        source_dir = locate_run(
            Workspace.at(options.target.root), options.run, within=[options.target]
        )
        source = _read_record(source_dir)
    except TrialNotFound as missing:
        return RunExit(code=PREFLIGHT_EXIT, message=str(missing))

    recorded = recorded_trials(source_dir)
    outside = f"no Trial in the source Run {source.run_id}"
    try:
        adhoc = _adhoc(options.evals, source, source_dir, recorded, options.target)
    except ValueError as exc:
        return RunExit(code=PREFLIGHT_EXIT, message=str(exc))
    try:
        cursor = ReplayCursor(Recording.load(options.replay)) if options.replay else None
        plan = preflight(
            options.target,
            options.selection,
            options.replay,
            cursor=cursor,
            judge_model=options.judge_model,
            judge_effort=options.judge_effort,
            within=set(recorded),
            within_detail=outside,
            reviewer_model=options.reviewer_model,
            check_sync=False,
            drives=False,
            adhoc=adhoc,
            client_backend=client_backend,
            environment=_source_environment(options.target, source),
        )
    except (OSError, ValueError) as exc:
        return RunExit(code=PREFLIGHT_EXIT, message=str(exc))
    except PreflightFailed as failure:
        return RunExit(code=PREFLIGHT_EXIT, message=str(failure) + _eval_hint(options, source))
    warn(plan.warnings)

    source_scorecard = _source_scorecard(source_dir)
    source_not_run = source_scorecard.not_run if source_scorecard is not None else []
    not_run = _carry_cancelled(plan, source_not_run, source.run_id, outside)
    stamp = stamp or RunStamp.now(options.target.directory)
    identifier = run_id or new_run_id(stamp.created_at)
    directory = RunDirectory.create(options.target, identifier)
    directory.write_run_record(
        build_run_record(
            run_id=identifier,
            stamp=stamp,
            target=options.target,
            manifest=plan.manifest,
            selection=SelectionSection.of(plan.selected_set.selection),
            scenarios=[
                ScenarioSummary.of(planned.scenario, plan.defaulted) for planned in plan.selected
            ],
            not_run=not_run,
            judge=plan.judge,
            source=source,
        )
    )

    judges = judges_for(client or agentdiag_client(cursor, plan), plan)
    order = [
        (number, planned)
        for number, planned in sweeps(source.trials, plan.selected)
        if number in recorded[planned.scenario.id]
    ]
    scores_by_trial = _rejudge(order, source, source_dir, directory, plan, judges, cursor, clock)
    scorecard = aggregate(
        identifier,
        source.sync,
        source.trials,
        scores_by_trial,
        [*not_run, *unstarted(order, scores_by_trial, numbered=source.trials > 1)],
        suites=plan.suite_names,
        # The Simulated User never runs again: its sampling is the source's, as its
        # configuration is (decision 52).
        simulated_user_sampling=(
            source_scorecard.simulated_user_sampling if source_scorecard is not None else None
        ),
    )
    return finish(
        options.target, directory, scorecard, options.json_output, plan.selected_set.several_suites
    )


def _declarations(evals: Sequence[str]) -> list[EvalDeclaration]:
    """`--eval name` or `--eval name=<value>`, the value read as YAML and bound to the
    Eval's primary parameter as the Suite short form binds it (phase-5 decision 1)."""
    declarations: list[EvalDeclaration] = []
    for given in evals:
        name, _, value = given.partition("=")
        try:
            authored = {name.strip(): yaml.safe_load(value)} if value else name.strip()
            declarations.append(EvalDeclaration.model_validate(authored))
        except (yaml.YAMLError, ValidationError, ValueError) as exc:
            raise ValueError(
                f"--eval {given!r} is not an Eval declaration (`name` or `name=<value>`): {exc}"
            ) from exc
    return declarations


def _require_thresholds(declarations: Sequence[EvalDeclaration], target: TargetPaths) -> None:
    """A Metric named by `--eval` with no value and no Manifest default has nothing to hold
    the Trace to: say how to give one, rather than score it `invalid`."""
    try:
        defaults = load_manifest(target).latency_thresholds
    except (ManifestNotFound, ManifestError):
        defaults = {}
    for declaration in declarations:
        spec = REGISTRY.get(declaration.eval)
        if (
            spec is not None
            and spec.metric
            and declaration.threshold is None
            and declaration.eval not in defaults
        ):
            raise ValueError(
                f"--eval {declaration.eval} needs a threshold: give one as "
                f"--eval '{declaration.eval}={{max_ms: 5000}}', or add "
                "eval_parameters.latency to the Manifest"
            )


def _adhoc(
    evals: Sequence[str],
    source: RunRecord,
    source_dir: Path,
    recorded: dict[str, list[int]],
    target: TargetPaths,
) -> list[Scenario]:
    """One Scenario per source Scenario, declaring `--eval`'s Evals; preflight keeps the
    ones no Suite declares. Its Turns are the user messages its first Trace holds."""
    declarations = _declarations(evals)
    if not declarations:
        return []
    _require_thresholds(declarations, target)
    scenarios: list[Scenario] = []
    for summary in source.scenarios:
        trials = recorded.get(summary.id) or []
        turns: list[str] = []
        if trials:
            events = read_trace(trace_path(source_dir, summary.id, trials[0]))
            turns = [
                str((event.model_extra or {}).get("content") or "")
                for event in events
                if event.type == "message" and (event.model_extra or {}).get("role") == "user"
            ]
        scenarios.append(
            Scenario.model_validate(
                {
                    "id": summary.id,
                    "title": summary.title,
                    "provenance": summary.provenance,
                    "notes": summary.notes,
                    "turns": turns or [summary.title],
                    "evals": declarations,
                }
            )
        )
    return scenarios


def _eval_hint(options: RescoreOptions, source: RunRecord) -> str:
    """What to add when the source's Scenarios are in no Suite and no `--eval` was given."""
    if options.evals:
        return ""
    try:
        manifest = load_manifest(options.target)
    except (ManifestNotFound, ManifestError):
        return ""
    suites, _, _ = load_suites(options.target, manifest)
    missing = undeclared(suites, [summary.id for summary in source.scenarios])
    if not missing:
        return ""
    return (
        f"\nno Suite declares {', '.join(missing)}: name the Evals to apply to "
        f"{'it' if len(missing) == 1 else 'them'} with --eval <name> or --eval <name>=<value>"
    )


def _carry_cancelled(
    plan: Plan, source_not_run: Sequence[NotRun], source_id: str, outside: str
) -> list[NotRun]:
    """What did not run, with every Trial the source never started still `cancelled`.

    The rescore's Scorecard enumerates what did not happen exactly as the source's did
    (ADR-0005 §8). The source's Scorecard says which Trials a Ctrl-C left unstarted, not its
    `run.json`, which was frozen before any Trial ran. A Scenario the source never started
    at all has no Trial to rescore, so the restriction to the source's Trials named it
    `not_selected`; it is `cancelled` in the source, and stays so.
    """
    selected = {planned.scenario.id for planned in plan.selected}
    detail = f"never started in the source Run {source_id}"
    cancelled_here = [entry for entry in source_not_run if entry.reason == "cancelled"]
    never = {entry.scenario: entry for entry in cancelled_here if entry.trial is None}
    return [
        *(
            never[entry.scenario].model_copy(update={"detail": detail})
            if entry.detail == outside and entry.scenario in never
            else entry
            for entry in plan.not_run
        ),
        *(
            entry.model_copy(update={"detail": detail})
            for entry in cancelled_here
            if entry.trial is not None and entry.scenario in selected
        ),
    ]


def _source_environment(target: TargetPaths, source: RunRecord) -> str | None:
    """The Adapter environment the source Run opened, when the Manifest still declares it,
    so the rescore's Fixture checks ask the Adapter of the environment the Traces came from
    (ticket 38); else None, the default, and a Manifest that does not load is preflight's to
    report."""
    try:
        declared = load_manifest(target).adapter.environment_names
    except (ManifestNotFound, ManifestError):
        return None
    environment = source.adapter.environment
    return environment if environment in declared else None


def _read_record(run_dir: Path) -> RunRecord:
    """The source's `run.json`, or `TrialNotFound` saying the directory is not a Run."""
    path = run_dir / RUN_RECORD_FILE
    try:
        return RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise TrialNotFound(f"{run_dir} is not a Run: {path} cannot be read ({exc})") from exc


def _source_scorecard(run_dir: Path) -> Scorecard | None:
    """The source's Scorecard — what it says did not run, and the Simulated User's
    sampling — or None when it has none to read."""
    try:
        return Scorecard.model_validate_json((run_dir / SCORECARD_FILE).read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return None


def _rejudge(
    order: Sequence[tuple[int, PlannedScenario]],
    source: RunRecord,
    source_dir: Path,
    directory: RunDirectory,
    plan: Plan,
    judges: Judges | None,
    cursor: ReplayCursor | None,
    clock: Callable[[], int] | None,
) -> list[tuple[str, int, list[Score]]]:
    """Every recorded Trial, sweep by sweep as `run` ran them, judged again.

    A Ctrl-C keeps the Trials already judged; the caller names the rest `cancelled`.
    """
    scores_by_trial: list[tuple[str, int, list[Score]]] = []
    try:
        for number, planned in order:
            scenario = planned.scenario
            if cursor is not None:
                cursor.begin(scenario.id)
            events = read_trace(trace_path(source_dir, scenario.id, number))
            failure = _ending(events)
            scores = perform_evals(
                scenario,
                trace_events=events,
                fidelity=source.adapter.fidelity,
                failure=failure,
                judges=judges,
                judgement_path=directory.trial_dir(scenario.id, number) / JUDGEMENT_FILE,
                forbidden_phrases=plan.forbidden_phrases,
                tool_kinds=plan.tool_kinds,
                recording=(
                    CursorCheck(cursor, only=is_rescored_request)
                    if failure is None and cursor is not None
                    else None
                ),
                clock=clock,
            )
            directory.write_scores(
                scenario.id, number, ScoresFile(scenario=scenario.id, trial=number, scores=scores)
            )
            scores_by_trial.append((scenario.id, number, scores))
    except KeyboardInterrupt:
        pass
    return scores_by_trial


def _ending(events: Sequence[Event]) -> TrialFailure | None:
    """How the source Trial ended, as the failure its Verdicts follow from (D22)."""
    end = next((event for event in reversed(events) if event.type == "trace/end"), None)
    if end is None:
        return agentdiag_error("the source Trace has no trace/end: it was never finished")
    fields = end.model_extra or {}
    termination = fields.get("termination")
    detail = str(fields.get("error") or f"the source Trial ended {termination}")
    if termination in FINISHED_TERMINATIONS:
        return None
    endings: dict[str, Callable[[str], TrialFailure]] = {
        "cancelled": cancelled,
        "target_error": target_error,
        "timeout": timed_out,
        "simulated_user_error": simulated_user_failed,
    }
    return endings.get(str(termination), agentdiag_error)(detail)


__all__ = ["RescoreOptions", "rescore"]
