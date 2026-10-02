"""Everything a Run can know before it touches a Target, checked in one pass (D32).

Preflight writes nothing and decides nothing about the Target. It exists so that a Run
which cannot possibly work never leaves a Run directory behind: a half-written one is
worse than none, because it would be listed, indexed and compared as if something had been
measured.

Two habits make it useful rather than merely defensive:

- **Problems are collected, not raised one at a time.** A caller with three things wrong in
  a Manifest should learn all three from one run, not from three. Everything gathered here
  arrives as one message with exit 3.
- **A warning is not a problem.** An Eval name the catalogue does not hold still runs and
  still produces a Score that says `eval_not_applicable` (D19), so it is carried out of
  here and printed, never raised.

Selection itself is `agentdiag.scenario.select` (ticket 07); preflight hands it the
loaded Suites and the Adapter's `check_fixtures`, so a Scenario whose Fixtures cannot be
applied is named `fixture_unavailable` and the rest of the Run proceeds (phase-5
decision 12). One Scenario-schema rule that only a selection can break is checked here
(ticket 03): a selected Scenario that `continues` another needs that one selected with it,
because its Adapter session is the one it resumes (decision 13). Decision 14's refusal of a
selected `simulate` Turn is retired: ticket 06 runs it.

Two Manifest fields reach the Evals from here, as plain data, because `agentdiag.eval`
never imports the Manifest: the tools' kinds, which the Adapter records as `retrieval` or
`tool_call` Spans, and `forbidden_phrases`. When the Manifest carries that key — an empty
list included — every selected Scenario that does not declare `forbidden_phrases` itself
gets one inherited declaration of it, so the Target-level list is screened everywhere and
an empty list is a Score saying so, while an absent key adds nothing (phase-5 decision 6).

Ticket 05 adds three Judge-side facts only preflight has in hand. The calibration notes the
Manifest names (`judge_notes`) are read and budgeted here, so notes that are missing or
over the word budget refuse the Run rather than reach the Judge cut or not at all
(ADR-0003 §8). Each selected Scenario's `guardrails` declarations get their rules resolved
against its Suite (`with_guardrail_rules`), because the Eval is handed a Scenario and a
Scenario-level declaration names only ids. And every declaration that overrides the Judge
(`judge: {model, effort}`, D14, D19) is collected by its id or Eval name for `run.json`,
with an effort outside the closed set, or two declarations of one name asking for
different Judges, refused.

Two things beyond checking are worked out here because this is where the answer is known:
the Judge configuration `run.json` freezes (the selection decides which judged Evals it
covers) with the Judge's Backend (ticket 19: the credentials resolved here decide it, once,
and execution and `rescore` build their client from the record), and the one
`ReplayCursor` a replayed Run walks. One cursor, not two, because the Target's transport
and the Judge's client draw from the same recording, and a per-consumer cursor would report
the other's exchanges as never requested (D12).

Ticket 20 extends the Backend to the Target (decision 29): every Run that is neither a dry
run nor a replay resolves its credentials once, before the Adapter is built, and the Adapter
carries the Target's calls through the Claude Code CLI when what resolved is the Claude Code
login; `run.json.adapter.backend` records that Backend.

Ticket 06 adds the Simulated User (phase-5 decisions 49, 52, 56, 57). A selected `simulate`
Turn needs a model as a judged Eval does, so credentials are asked for it the same way (D16);
the Simulated User's configuration and the reviewer's are worked out here and frozen into
`run.json` with their Fingerprints; the Judge configuration exists whenever a judged Eval or
a `simulate` Turn is selected, because the stop check and the reviewer are Judge calls; and
the environment's `turn_timeout` is read into the Adapter's description, where the driver
loop reads it. The Run's one Backend for agentdiag's own calls is `Plan.backend`.

Ticket 10 adds Sync and the rest of the Manifest (phase-6 decisions 8 and 14 to 17). Once the
Adapter is built, and before credentials are checked, preflight probes it once and compares
the Target with its last Fingerprint (`agentdiag.sync.check`): `held` proceeds; `broken`
proceeds and `execute` rebuilds the Fingerprint before the Run directory exists, unless
`no_resync` (proceed labelled `broken`) or `strict` (a problem, exit 3, with the table)
says otherwise; `not_checked` proceeds with its reason. A `draft` Suite is loaded and its
Scenarios named `not_run` / `suite_not_run`; a `retired` one is not loaded. The Manifest's
latency defaults make a latency declaration without a threshold valid, and are applied
here with a record of which declarations took them; `eval_parameters.tool_argument_types`
gives every Scenario an inherited `tool_argument_types` screen, as `forbidden_phrases`
does; the text of every path prompt pointer is read once (`Plan.manifest_prompts`, note 7)
and the Manifest's Suppressions go into the Judge configuration.

Ticket 17 opens the Run to every registered Adapter kind (phase-8 decisions 1, 8, 9).
`build_adapter` builds whatever `adapter.kind` resolves to, handing every kind the common
arguments (`environment`, `tool_kinds`, `allow_live`) and the in-process kind alone its own
(`replay`, `credentials`); the HTTP kind is handed the Target's Connector when its
`tool_truth` reads one. `live` is `run --live`: without it a `live` environment is refused
here, exit 3, and with it the description records `live_acknowledged`. A selection that
declares a tool Eval against the HTTP Adapter with no proxy Evidence store to reconstruct
from is warned of, once, and runs: those Evals are `unverifiable / fidelity_too_low`.

Once the Manifest loads (a missing or unloadable one is refused before anything else), an
`adapter.kind` nothing registers is a problem, with the message `validate` gives for it, and
so is a `connector.kind` when this preflight uses the Connector (a Sync check, or an HTTP
Adapter whose `tool_truth` reads through it): the Adapter is then neither built nor probed,
and the rest is collected as usual (ADR-0015 §3). A `pending` Adapter, the identity
scaffold's (ADR-0016 §4), is the same: `validate`'s warning becomes the problem, word for
word, and no Adapter is built; and a Manifest whose every Suite is a draft or retired is
`no runnable Suite` for a Run that drives.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agentdiag.adapter import (
    Adapter,
    AdapterDescription,
    InProcessAdapter,
    LiveSideEffectsRefused,
)
from agentdiag.adapter.http.config import TOOL_TRUTH_KEY, tool_truth_of
from agentdiag.adapter.http.session import HttpAdapter
from agentdiag.connector.base import ConnectorError
from agentdiag.connector.environment import scrub_credentials
from agentdiag.connector.plugins import UnknownKind, adapter_class, build_connector
from agentdiag.eval import judged
from agentdiag.eval.judge import effort_problem, resolve_override
from agentdiag.eval.judged import JudgeConfiguration
from agentdiag.eval.notes import JudgeNotes, JudgeNotesProblem, read_judge_notes
from agentdiag.eval.registry import (
    DEFAULT_JUDGE_MODEL,
    DEFAULT_REVIEWER_MODEL,
    DEFAULT_SIMULATED_USER_MODEL,
    REGISTRY,
    is_implemented,
    is_judged,
)
from agentdiag.eval.render import RecordedPrompt
from agentdiag.eval.text import FORBIDDEN_PHRASES
from agentdiag.eval.tools import TOOL_ARGUMENT_TYPES
from agentdiag.exits import USAGE_EXIT
from agentdiag.model import credentials
from agentdiag.model.claude_code import Backend, backend_for
from agentdiag.model.credentials import CredentialSource
from agentdiag.model.replay import Cursor, ReplayCursor
from agentdiag.run.manifest import (
    Manifest,
    ManifestError,
    ManifestNotFound,
    PromptPointer,
    load_manifest,
    pointer_problems,
)
from agentdiag.run.manifest_checks import (
    kind_problems,
    no_runnable_suite,
    pending_problem,
    render_problem,
)
from agentdiag.run.record import Defaulted, SyncSection
from agentdiag.scenario.load import LoadedSuite, load_suite_files
from agentdiag.scenario.models import (
    EvalDeclaration,
    JudgeOverride,
    Scenario,
    Suite,
    with_guardrail_rules,
)
from agentdiag.scenario.select import NotRun, PlannedScenario, Selected, Selection, select
from agentdiag.scenario.validate import with_default_threshold
from agentdiag.simulate import user
from agentdiag.simulate.configuration import (
    SimulatedUserConfiguration,
    simulated_user_configuration,
)
from agentdiag.sync.check import SyncCheck, check_target
from agentdiag.sync.compare import render_sync
from agentdiag.sync.fingerprint import Fingerprint, FingerprintError
from agentdiag.sync.observe import deployed_side
from agentdiag.types import ToolKind
from agentdiag.workspace import TargetPaths

PREFLIGHT_EXIT = USAGE_EXIT
"""Usage, configuration and preflight errors (D32)."""

NO_CREDENTIALS = (
    "A judged Eval or a simulate Turn is selected ({citations}) and no credentials resolve: "
    "set ANTHROPIC_API_KEY, log in to Claude Code (`claude auth login`), or run `ant auth "
    "login`; from a checkout, scripts/credentials-wizard.sh walks through the three"
)
"""The one message D16 asks for: what is needed, and the three ways to provide it."""

TOOL_EVALS_WITHOUT_TOOL_TRUTH = (
    "tool Evals over the http Adapter are unverifiable / fidelity_too_low without "
    "adapter.tool_truth and a proxy Evidence store"
)
"""Phase-8 decision 8: warned of once per Run, never refused (ticket 17's amendment)."""


@runtime_checkable
class BuiltAdapter(Adapter, Protocol):
    """An Adapter as preflight builds it: the protocol, the environment it opens, and the
    check that runs before any Run directory exists."""

    environment: str

    def check(self) -> None: ...


SAME_REVIEWER = (
    "the Simulated User reviewer runs on {model}, the Simulated User's own model; D14 asks "
    "for a stronger reviewer (--reviewer-model)"
)
"""Decision 57: warned of, never refused."""


class PreflightFailed(Exception):
    """Preflight collected problems. Every one of them, in one message (D32)."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


@dataclass
class Plan:
    """What preflight worked out, handed to execution intact."""

    manifest: Manifest
    adapter: BuiltAdapter
    description: AdapterDescription
    selected_set: Selected
    """The selection's whole answer (D31): the Scenarios to run, in the order the Suites
    declare them so a continued Scenario runs first, every loaded Scenario that will not
    run with its Suite and reason (decisions 11, 12), the normalised selection `run.json`
    records, and the Suites it chose from."""

    warnings: list[str] = field(default_factory=list)
    judge: JudgeConfiguration | None = None
    """What `run.json` records under `judge`; None when no judged Eval and no `simulate`
    Turn is selected."""

    simulated_user: SimulatedUserConfiguration | None = None
    """What `run.json` records under `simulated_user`; None when no selected Scenario has a
    `simulate` Turn (phase-5 decision 52)."""

    backend: Backend | None = None
    """The Run's one Backend for agentdiag's own calls — the Judge's, the Simulated User's,
    the stop check's and the reviewer's (decision 49): `backend_for` what resolved, for
    every Run that is not a dry run."""

    credentials: CredentialSource | None = None
    """What preflight resolved for a live Run (None for a dry run, a replay, or nothing
    configured): the one probe of the environment and the Claude Code CLI a Run makes, which
    the Adapter carries the Target's calls by and execution builds the Judge's client from
    (ticket 19, decision 22; ticket 20, decision 29)."""

    replay: Path | None = None
    """The recording a replayed Run walks, if any. `execute.run` opens the one cursor the
    Target's transport and the Judge share, because opening a file is not a check."""

    sync: SyncSection | None = None
    """What `run.json.sync` records (decision 14); None for a rescore, which carries its
    source's."""

    fingerprint: Fingerprint | None = None
    """The Fingerprint found, in force for the Run unless it re-syncs."""

    sync_check: SyncCheck | None = None
    """The comparison itself, when `resync` asks `execute` to rebuild the Fingerprint from
    what it gathered."""

    resync: bool = False
    """Sync was `broken` and neither `no_resync` nor `strict` was asked: `execute` writes a
    rebuilt `fingerprint.json` naming the old one before the Run directory exists (ADR-0008)."""

    manifest_prompts: dict[str, str] = field(default_factory=dict)
    """The text of every path pointer in the Manifest's `prompts`, by name (note 7)."""

    defaulted: Defaulted = ()
    """The declarations whose threshold the Manifest's latency defaults supplied, as
    (Scenario id, Eval, id), which `run.json` marks `from_manifest` (decision 17)."""

    @property
    def selected(self) -> list[PlannedScenario]:
        return self.selected_set.scenarios

    @property
    def not_run(self) -> list[NotRun]:
        return self.selected_set.not_run

    @property
    def forbidden_phrases(self) -> list[str] | None:
        """The Manifest's list, None when the key is absent (decision 6)."""
        return self.manifest.forbidden_phrases

    @property
    def suite_names(self) -> dict[str, str]:
        """Each selected Scenario's Suite, by id, for the Scorecard's aggregates."""
        return {planned.scenario.id: planned.suite.name for planned in self.selected}

    @property
    def tool_kinds(self) -> dict[str, ToolKind]:
        """The Manifest's tools' kinds, by name, for the Evals (ticket 04)."""
        return self.manifest.tool_kinds


def preflight(
    target: TargetPaths,
    selection: Selection,
    replay: Path | None,
    *,
    cursor: ReplayCursor | None = None,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    judge_effort: str | None = None,
    dry_run: bool = False,
    within: Collection[str] | None = None,
    within_detail: str | None = None,
    simulated_user_model: str = DEFAULT_SIMULATED_USER_MODEL,
    simulated_user_effort: str | None = None,
    simulated_user_temperature: float | None = None,
    reviewer_model: str = DEFAULT_REVIEWER_MODEL,
    check_sync: bool = True,
    no_resync: bool = False,
    strict: bool = False,
    adhoc: Sequence[Scenario] = (),
    client_backend: Backend | None = None,
    live: bool = False,
    drives: bool = True,
    environment: str | None = None,
) -> Plan:
    """Check everything checkable, then hand execution a Plan it can trust.

    `replay` is the path, used only to decide whether the credentials check applies;
    `cursor` is the already-opened recording, passed in by `execute.run` because reading
    a file is not a check and this module only checks. A `dry_run` calls no model, so it
    needs no credentials; everything else is asked of it exactly as of a Run.

    The Adapter is built before selection because selection asks it whether each
    matched Scenario's Fixtures can be applied (phase-5 decision 12); when it cannot be
    built, that is the problem reported, and no Fixture is asked about.

    `within` restricts the selection to those Scenario ids — a rescore's, to what its
    source Run ran (D34) — before anything is worked out from it, so the Judge
    configuration covers exactly the Scenarios that will be judged; a selected Scenario
    outside it is `not_selected` with `within_detail` as its detail.

    `check_sync` is False for a rescore, which drives nothing and carries its source's Sync;
    `no_resync` and `strict` are `run`'s (decision 14).

    `adhoc` are `rescore --eval`'s Scenarios (ticket 26, decision 36): each one no loaded
    Suite declares joins the selection in a Suite of its own named `--eval`, so an imported
    Trace is judged with the named Evals when no Suite entry names its Scenario.

    `client_backend` is the Backend of a model client the caller brings for agentdiag's own
    calls (`rescore(client=…)`, the fixture script's seam): nothing is resolved from the
    environment and no credential is asked for, since that client is what will answer.

    `live` is `run --live` (phase-8 decision 9): the Adapter is built with `allow_live`, so a
    `live` environment opens, and its description records the flag.

    `drives` is False for a rescore, which opens no session: the Adapter is built for its
    description and its Fixture answers, never checked, so no Target credential is read and a
    `live` environment needs no `--live` (ticket 17 reviews).

    `environment` is `run --env` (phase-8 decision 12): the Adapter environment the Run opens,
    the Manifest's default when None. Everything after the Adapter reads it from there: the
    Sync checked, the Connector twin read, what `run.json.adapter` records.
    """
    problems: list[str] = []

    try:
        manifest = load_manifest(target)
    except (ManifestNotFound, ManifestError) as exc:
        # Nothing after this can be checked without a Manifest, so this one short-circuits
        # rather than producing a cascade of failures that all mean "no Manifest".
        raise PreflightFailed([str(exc)]) from exc
    # A kind nothing registers can never be built: `validate`'s problem, word for word, and
    # no Adapter, so the Adapter's probe never stands in for a Connector that cannot exist.
    # Everything that does not need the Adapter is still collected below.
    unknown = unknown_kinds(manifest, connector=check_sync or reads_tool_truth(manifest))
    problems.extend(unknown)
    # A `pending` Adapter drives nothing (ADR-0016 §4): `validate`'s warning is this refusal,
    # word for word, and no Adapter is built, so nothing of the Target is ever reached.
    pending = pending_problem(manifest)
    if pending is not None:
        problems.append(render_problem(pending))
    # Every pointer relative and inside the Workspace root (ADR-0015 §2): `validate`'s
    # problem, word for word, so `run` refuses what `validate` refuses.
    problems.extend(f"{where}: {message}" for where, message in pointer_problems(target, manifest))

    suites, warnings, suite_problems = load_suites(target, manifest)
    problems.extend(suite_problems)
    # Nothing a Run could execute is refused by name; a rescore (the only caller with ad-hoc
    # Scenarios) drives nothing and is not asked.
    if drives and (nothing := no_runnable_suite(manifest)) is not None:
        problems.append(render_problem(nothing))
    suites = with_adhoc(suites, adhoc, manifest)
    # Before the Adapter: what resolves decides the Backend of the Target's calls too.
    source = None if client_backend is not None else resolve_credentials(replay, dry_run)
    adapter, description, adapter_problems = (
        (None, None, [])
        if unknown or pending is not None
        else build_adapter(
            manifest, cursor, source, allow_live=live, drives=drives, environment=environment
        )
    )
    # Sync, once the Adapter exists and before credentials are asked about, so a `strict`
    # refusal costs nothing more (decision 14). One probe; no Run directory yet.
    sync, sync_problems = (
        sync_plan(target, manifest, adapter, no_resync=no_resync, strict=strict)
        if check_sync and adapter is not None
        else (SyncOutcome(), [])
    )
    problems.extend(sync_problems)
    warnings.extend(sync.warnings)
    chosen = select(suites, selection, check_fixtures=adapter.check_fixtures if adapter else None)
    problems.extend(chosen.problems)
    chosen = without_drafts(chosen, draft_suites(suites, manifest))
    if within is not None:
        chosen, outside = restrict(chosen, within, within_detail)
        problems.extend(outside)
    defaulted: list[tuple[str, str, str | None]] = []
    selected = [
        with_default_thresholds(
            resolve_guardrails(
                inherit_tool_argument_types(inherit_forbidden_phrases(planned, manifest), manifest)
            ),
            manifest,
            defaulted,
        )
        for planned in chosen.scenarios
    ]
    problems.extend(schema_problems(selected))
    scenarios = [planned.scenario for planned in selected]
    warnings.extend(not_implemented_warnings(scenarios))
    warnings.extend(tool_truth_warnings(adapter, scenarios))

    judged_evals = list(judged_declarations(scenarios))
    simulated = [f"simulate in {scenario.id}" for scenario in scenarios if scenario.simulated]
    if not dry_run and replay is None and client_backend is None:
        problems.extend(
            credential_problems(source, [citation for _, citation in judged_evals], simulated)
        )
    problems.extend(adapter_problems)
    notes, notes_problems = load_judge_notes(target, manifest)
    problems.extend(notes_problems)
    manifest_prompts, prompt_warnings = read_manifest_prompts(target, manifest)
    warnings.extend(prompt_warnings)
    overrides, override_problems = judge_overrides(scenarios, judge_model, judge_effort)
    problems.extend(override_problems)
    if simulated and (
        problem := effort_problem(simulated_user_effort, whose="The Simulated User's")
    ):
        problems.append(problem)

    if problems:
        raise PreflightFailed(problems)

    assert adapter is not None and description is not None  # nothing was collected above
    # A dry run resolves nothing and calls nothing, so it names no Backend (decision 49).
    backend = (
        client_backend
        if client_backend is not None
        else None
        if dry_run
        else backend_for(source, replay is not None)
    )
    if simulated and reviewer_model == simulated_user_model:
        warnings.append(SAME_REVIEWER.format(model=reviewer_model))
    return Plan(
        manifest=manifest,
        adapter=adapter,
        description=description,
        selected_set=chosen.model_copy(update={"scenarios": selected}),
        warnings=warnings,
        judge=(
            judged.configuration(
                [name for name, _ in judged_evals],
                judge_model,
                judge_effort,
                notes=notes,
                overrides=overrides,
                backend=backend,
                stop_check=any(
                    (spec := scenario.simulate) is not None
                    and spec.stop_when is not None
                    and spec.stop_when.judged is not None
                    for scenario in scenarios
                ),
                reviewer_model=reviewer_model if simulated else None,
                suppressions=manifest.suppressions,
            )
            if judged_evals or simulated
            else None
        ),
        simulated_user=(
            simulated_user_configuration(
                RecordedPrompt.of(user.PARTS),
                model=simulated_user_model,
                effort=simulated_user_effort,
                temperature=simulated_user_temperature,
                backend=backend,
            )
            if simulated
            else None
        ),
        backend=backend,
        credentials=source,
        replay=replay,
        sync=sync.section,
        fingerprint=sync.found,
        sync_check=sync.check,
        resync=sync.resync,
        manifest_prompts=manifest_prompts,
        defaulted=frozenset(defaulted),
    )


# --- Sync before the first Trial (decision 14, D30, ADR-0008) ---


@dataclass
class SyncOutcome:
    """What a Run's Sync check decided: the record, the Fingerprint found, and whether
    `execute` rebuilds it. The empty outcome is a rescore's, or an Adapter that did not
    build (whose problem is reported instead)."""

    section: SyncSection | None = None
    found: Fingerprint | None = None
    check: SyncCheck | None = None
    resync: bool = False
    warnings: list[str] = field(default_factory=list)
    """The Connector's read failed and the probe stood in (decision 25)."""


STRICT_REFUSAL = "Sync is broken and --strict refuses to run against it: {remedy}"
"""What `run --strict` says above the table when Sync is broken (D30, decision 14), with
the remedy each broken direction asks for."""

STRICT_REMEDIES: dict[str, str] = {
    "local_ahead": "the deployed set is behind the local file",
    "deployed_ahead": "`agentdiag sync` re-records the Fingerprint",
    "diverged": (
        "the local file and the deployed set both moved; settle which is right, then "
        "`agentdiag sync` re-records the Fingerprint"
    ),
}
"""By direction, in the table's order; `--strict` names every one its broken sections have,
or drop --strict to re-sync by default."""


def sync_plan(
    target: TargetPaths,
    manifest: Manifest,
    adapter: BuiltAdapter,
    *,
    no_resync: bool,
    strict: bool,
) -> tuple[SyncOutcome, list[str]]:
    """Read the deployed side once, compare with the last Fingerprint, and decide
    (decision 14).

    The deployed side is the Connector's read when the Manifest names a Connector for the
    Adapter's environment, else one probe of the Adapter (decision 25). A Connector read that
    fails does not refuse the Run: the probe stands in, the Run warns, and `run.json.sync`
    records why (`connector_failed`), so a missing credential never blocks a local Run. What
    only the Connector covered is `not_covered` for that Run, never `removed`, and a broken
    comparison is not re-synced, since a probe-only Fingerprint would lose those sections.

    `held` proceeds with the Fingerprint found. `broken` proceeds and re-syncs by default,
    proceeds labelled `broken` under `no_resync`, and is a problem under `strict`, carrying
    the table. `not_checked` proceeds with its reason: a scaffolded Target that never ran
    `sync` is `no_fingerprint`, and the five-minute walkthrough is unchanged. A Run never
    records a Sync break (CONTEXT.md): that is `sync`'s.
    """
    environment = adapter.environment
    # Only the in-process Adapter has a probe (phase-6 decision 11); any other kind's
    # deployed side is the Connector's, or `adapter_cannot_observe`.
    side = deployed_side(
        manifest, environment, adapter=adapter if isinstance(adapter, InProcessAdapter) else None
    )
    failed = side.connector_failed
    warnings = (
        [f"Sync: the Adapter's probe stands in, because {failed}"] if failed is not None else []
    )
    try:
        check = check_target(target, manifest, environment, side)
    except (FingerprintError, ManifestError) as exc:
        return SyncOutcome(), [f"Sync: {exc}"]
    result = check.result
    section = SyncSection(
        status=result.status,
        reason=result.reason,
        environment=environment,
        fingerprint=result.fingerprint,
        covered_by=result.covered_by,
        connector_failed=failed,
    )
    if result.status != "broken":
        return SyncOutcome(
            section=section, found=check.recorded, check=check, warnings=warnings
        ), []
    if strict:
        directions = {state.direction for state in result.broken}
        remedy = "; ".join(
            text for direction, text in STRICT_REMEDIES.items() if direction in directions
        )
        refusal = STRICT_REFUSAL.format(remedy=remedy)
        return SyncOutcome(warnings=warnings), [
            f"{refusal}; or drop --strict to re-sync\n{render_sync(result)}"
        ]
    # A Fingerprint rebuilt from the probe alone would drop what only the Connector covers,
    # so a Run whose Connector could not read never re-syncs: it runs labelled `broken`.
    resync = not no_resync and failed is None
    if failed is not None and not no_resync:
        warnings.append(f"Sync: not re-synced: {failed}")
    section = section.model_copy(
        update={
            "sections": result.broken,
            "resynced_from": result.fingerprint if resync else None,
        }
    )
    outcome = SyncOutcome(
        section=section, found=check.recorded, check=check, resync=resync, warnings=warnings
    )
    return outcome, []


ADHOC_SUITE = "--eval"
"""The name the Scenarios `rescore --eval` judges are listed under when no Suite declares
them: the flag that put them there."""


def undeclared(suites: Sequence[LoadedSuite], ids: Collection[str]) -> list[str]:
    """The Scenario ids no loaded Suite declares, in the order given."""
    declared = {scenario.id for loaded in suites for scenario in loaded.suite.scenarios}
    return [identifier for identifier in ids if identifier not in declared]


def with_adhoc(
    suites: list[LoadedSuite], adhoc: Sequence[Scenario], manifest: Manifest
) -> list[LoadedSuite]:
    """The loaded Suites, plus one named `--eval` holding every `adhoc` Scenario no Suite
    declares (decision 36): a declared Scenario is judged by its Suite's Evals, as always."""
    missing = set(undeclared(suites, [scenario.id for scenario in adhoc]))
    kept = [scenario for scenario in adhoc if scenario.id in missing]
    if not kept:
        return suites
    suite = Suite(target=manifest.target.name, scenarios=kept)
    return [*suites, LoadedSuite(path=Path(ADHOC_SUITE), name=ADHOC_SUITE, suite=suite)]


def read_manifest_prompts(
    target: TargetPaths, manifest: Manifest
) -> tuple[dict[str, str], list[str]]:
    """The text of every path pointer in `prompts`, by name, read once (note 7): what the
    Judge falls back to when a Trace holds no system prompt. A missing file is a warning
    (and `validate`'s error, or its warning when `local_only`); `observed` pointers have no
    text here."""
    texts: dict[str, str] = {}
    warnings: list[str] = []
    for name, pointer in manifest.prompts.items():
        if not isinstance(pointer, PromptPointer):
            continue
        try:
            texts[name] = target.relative(pointer.path).read_text(encoding="utf-8")
        except OSError:
            if not pointer.local_only:
                warnings.append(
                    f"the Manifest's prompts.{name} names {pointer.path}, which does not exist; "
                    "the Judge will not fall back to it"
                )
    return texts, warnings


def draft_suites(suites: Sequence[LoadedSuite], manifest: Manifest) -> set[str]:
    """The names of the loaded Suites the Manifest marks `draft`."""
    drafts = {entry.path for entry in manifest.draft_suites}
    return {loaded.name for loaded in suites if loaded.reference in drafts}


DRAFT_DETAIL = "Suite status: draft"


def without_drafts(chosen: Selected, drafts: set[str]) -> Selected:
    """The selection with every Scenario of a `draft` Suite moved to `not_run` /
    `suite_not_run` (decision 8), so `run --dry-run` names them and nothing runs them."""
    if not drafts:
        return chosen
    kept = [planned for planned in chosen.scenarios if planned.suite.name not in drafts]
    moved = [
        NotRun(
            scenario=planned.scenario.id,
            suite=planned.suite.name,
            reason="suite_not_run",
            detail=DRAFT_DETAIL,
        )
        for planned in chosen.scenarios
        if planned.suite.name in drafts
    ]
    return chosen.model_copy(update={"scenarios": kept, "not_run": [*chosen.not_run, *moved]})


def inherit_tool_argument_types(planned: PlannedScenario, manifest: Manifest) -> PlannedScenario:
    """Add the Target-level `tool_argument_types` screen a Scenario inherits (decision 17),
    as `inherit_forbidden_phrases` adds its own: only when the Manifest declares the types,
    and only to a Scenario that does not declare the Eval itself."""
    types = manifest.tool_argument_types
    scenario = planned.scenario
    if types is None or any(
        declaration.eval == TOOL_ARGUMENT_TYPES.name for declaration in scenario.evals
    ):
        return planned
    inherited = EvalDeclaration(eval=TOOL_ARGUMENT_TYPES.name, params={"types": types}).model_copy(
        update={"inherited": True}
    )
    return planned.model_copy(
        update={"scenario": scenario.model_copy(update={"evals": [*scenario.evals, inherited]})}
    )


def with_default_thresholds(
    planned: PlannedScenario, manifest: Manifest, defaulted: list[tuple[str, str, str | None]]
) -> PlannedScenario:
    """Every Metric declaration without a threshold held to its Eval's Manifest default
    (decision 17), each one so treated appended to `defaulted`."""
    defaults = manifest.latency_thresholds
    scenario = planned.scenario
    if not defaults:
        return planned
    evals: list[EvalDeclaration] = []
    for declaration in scenario.evals:
        held = with_default_threshold(declaration, defaults)
        if held is not None:
            defaulted.append((scenario.id, held.eval, held.id))
        evals.append(held or declaration)
    return planned.model_copy(update={"scenario": scenario.model_copy(update={"evals": evals})})


def restrict(
    chosen: Selected, within: Collection[str], detail: str | None
) -> tuple[Selected, list[str]]:
    """The selection narrowed to `within`, the rest named `not_selected`, and the problem
    when nothing is left (D31: a selection that matches nothing exits 3)."""
    kept = [planned for planned in chosen.scenarios if planned.scenario.id in within]
    dropped = [
        NotRun(
            scenario=planned.scenario.id,
            suite=planned.suite.name,
            reason="not_selected",
            detail=detail,
        )
        for planned in chosen.scenarios
        if planned.scenario.id not in within
    ]
    problems = (
        [f"nothing to run: every selected Scenario has {detail or 'been left out'}"]
        if chosen.scenarios and not kept
        else []
    )
    narrowed = chosen.model_copy(update={"scenarios": kept, "not_run": [*chosen.not_run, *dropped]})
    return narrowed, problems


def judged_declarations(selected: Sequence[Scenario]) -> list[tuple[str, str]]:
    """Every judged Eval in the selection, as (name, where it was declared).

    Two things want this list and they want it differently: the Judge configuration wants
    the names, and the D16 message wants "prompt_adherence in cancel-processing-order",
    because "a judged Eval is selected" leaves a reader hunting for the line to change.
    Both come off one walk, so the two can never disagree about what is judged.

    Ordered by declaration and de-duplicated by name: one Eval declared in three
    Scenarios is one prompt in `run.json`, and the citation names where it was first seen.
    """
    seen: dict[str, tuple[str, str]] = {}
    for scenario in selected:
        for declaration in scenario.evals:
            if is_judged(declaration.eval) and declaration.eval not in seen:
                seen[declaration.eval] = (
                    declaration.eval,
                    f"{declaration.eval} in {scenario.id}",
                )
    return list(seen.values())


def resolve_credentials(replay: Path | None, dry_run: bool) -> CredentialSource | None:
    """The one probe of the environment and the Claude Code CLI a Run makes (decision 29).

    A dry run calls no model and a replay calls none live, so neither asks anything. Every
    other Run asks once, whatever it selected: the answer decides the Target's Backend as well
    as the Judge's, and a Run with no judged Eval still drives a Target that calls a model.
    """
    if dry_run or replay is not None:
        return None
    return credentials.resolve()


def credential_problems(
    source: CredentialSource | None, judged: Sequence[str], simulated: Sequence[str] = ()
) -> list[str]:
    """D16: a judged Eval or a `simulate` Turn needs a model, and a Run that cannot reach
    one never starts. `judged` cites each judged Eval where it was first declared, and
    `simulated` each `simulate` Turn (`simulate in <scenario id>`, decision 49).

    Asked only of a live Run: a recorded exchange is a model call that already happened, so
    a reproduction needs no key, and refusing it anyway would make the shipped example
    unrunnable for anyone without credentials. Whether the Target needs credentials is the
    Target's business (it may reach another provider through the same client), so nothing
    resolving is a problem only when agentdiag itself needs a model.
    """
    citations = [*judged, *simulated]
    if not citations or source is not None:
        return []
    return [NO_CREDENTIALS.format(citations=", ".join(citations))]


def load_suites(
    target: TargetPaths, manifest: Manifest
) -> tuple[list[LoadedSuite], list[str], list[str]]:
    """Every `runnable` and `draft` Suite the Manifest names (`scenario.load.
    load_suite_files`), with warnings and load problems, each problem naming its file."""
    loaded = load_suite_files(target, manifest)
    problems = [f"Suite {reference}: {problem}" for reference, problem in loaded.problems.items()]
    return loaded.suites, loaded.warnings, problems


def schema_problems(selected: Sequence[PlannedScenario]) -> list[str]:
    """What the selection cannot run: a continuing Scenario without the one it continues,
    from its own Suite (decision 13)."""
    chosen = {planned.key for planned in selected}
    return [
        f"Scenario {planned.scenario.id!r} continues {planned.scenario.continues!r}, whose "
        f"Adapter session it resumes; select {planned.scenario.continues!r} too, or neither"
        for planned in selected
        if planned.scenario.continues is not None
        and (planned.suite.name, planned.scenario.continues) not in chosen
    ]


def tool_truth_warnings(adapter: object, selected: Sequence[Scenario]) -> list[str]:
    """Decision 8: a selected tool Eval over the HTTP Adapter with no proxy Evidence store
    to reconstruct its tool Spans from, once per Run."""
    if not isinstance(adapter, HttpAdapter):
        return []
    tools = any(
        (spec := REGISTRY.get(declaration.eval)) is not None and spec.tool_family
        for scenario in selected
        for declaration in scenario.evals
    )
    if not tools or adapter.fidelity == "reconstructed":
        return []
    return [TOOL_EVALS_WITHOUT_TOOL_TRUTH]


def not_implemented_warnings(selected: Sequence[Scenario]) -> list[str]:
    """A registered Eval whose ticket has not landed runs, and says it will not judge."""
    seen: dict[tuple[str, str], None] = {}
    for scenario in selected:
        for declaration in scenario.evals:
            if declaration.eval in REGISTRY and not is_implemented(declaration.eval):
                seen[(declaration.eval, scenario.id)] = None
    return [
        f"Eval {name!r} in Scenario {scenario!r} is not implemented in this agentdiag yet; "
        "it scores unverifiable / eval_not_applicable"
        for name, scenario in seen
    ]


def inherit_forbidden_phrases(planned: PlannedScenario, manifest: Manifest) -> PlannedScenario:
    """Add the Target-level `forbidden_phrases` declaration a Scenario inherits (decision 6).

    Only when the Manifest carries the key, and only to a Scenario that does not already
    declare it (its own, or its Suite's): a Scenario's declaration adds its phrases to the
    Manifest's in the same Score, so a second one would screen the list twice.
    """
    scenario = planned.scenario
    if manifest.forbidden_phrases is None or any(
        declaration.eval == FORBIDDEN_PHRASES.name for declaration in scenario.evals
    ):
        return planned
    # Set by construction, as the loader sets it on a Suite-level copy: never authored.
    inherited = EvalDeclaration(eval=FORBIDDEN_PHRASES.name).model_copy(update={"inherited": True})
    return planned.model_copy(
        update={"scenario": scenario.model_copy(update={"evals": [*scenario.evals, inherited]})}
    )


def resolve_guardrails(planned: PlannedScenario) -> PlannedScenario:
    """The Scenario with its `guardrails` rules resolved against its own Suite (ticket 05)."""
    scenario = with_guardrail_rules(planned.scenario, planned.suite.suite)
    return planned.model_copy(update={"scenario": scenario})


def load_judge_notes(
    target: TargetPaths, manifest: Manifest
) -> tuple[JudgeNotes | None, list[str]]:
    """The calibration notes the Manifest names, or the problem that keeps them out."""
    if manifest.judge_notes is None:
        return None, []
    try:
        return read_judge_notes(target.directory, manifest.judge_notes), []
    except (JudgeNotesProblem, OSError) as exc:
        return None, [str(exc)]


def judge_overrides(
    selected: Sequence[Scenario], judge_model: str, judge_effort: str | None
) -> tuple[dict[str, JudgeOverride], list[str]]:
    """Every judged declaration's own Judge, by its id or Eval name, with the Run default
    filled in where the override leaves a field unsaid (D14, D19).

    Keyed the way `run.json` names them, so every judged declaration under one key must run
    on one Judge: two overrides that differ, or one declaration overriding and another under
    the same key not, would make the record say something untrue of one of them. Both are
    refused, naming the Scenarios, rather than keyed apart, so `overrides` stays the short
    map by declaration id or Eval name the interfaces fix.
    """
    judges: dict[str, tuple[str, str | None]] = {}
    overridden: dict[str, bool] = {}
    where: dict[str, str] = {}
    problems: list[str] = []
    for scenario in selected:
        for declaration in scenario.evals:
            if not is_judged(declaration.eval):
                continue
            override = declaration.judge
            if override is not None and (problem := effort_problem(override.effort)):
                problems.append(
                    f"{declaration.eval} in {scenario.id} overrides the Judge: {problem}"
                )
                continue
            key = declaration.id or declaration.eval
            pair = resolve_override(override, judge_model, judge_effort)
            asks = override is not None
            if key in judges and (judges[key] != pair or overridden[key] != asks):
                problems.append(
                    f"{key!r} runs on a different Judge in {where[key]} and in {scenario.id} "
                    "(an override in one and not the other, or two overrides that differ); "
                    "give each declaration its own `id`"
                )
                continue
            judges.setdefault(key, pair)
            overridden[key] = overridden.get(key, False) or asks
            where.setdefault(key, scenario.id)
    overrides = {
        key: JudgeOverride(model=model, effort=effort)
        for key, (model, effort) in judges.items()
        if overridden[key]
    }
    return overrides, problems


def build_adapter(
    manifest: Manifest,
    replay: Path | Cursor | None,
    credentials: CredentialSource | None = None,
    *,
    allow_live: bool = False,
    drives: bool = True,
    environment: str | None = None,
) -> tuple[BuiltAdapter | None, AdapterDescription | None, list[str]]:
    """Construct and check the Adapter, without opening a session (ADR-0001 point 5).

    `adapter.kind` resolves through the entry points (`connector.plugins.adapter_class`,
    phase-6 decision 21); a kind no distribution registers is a problem naming the package
    that would, a branch only a direct caller reaches, since `preflight` reports an unknown
    kind itself and then does not call this (ADR-0015 §3). Every kind is constructed as
    `Kind(config, *, environment, tool_kinds, allow_live, **kind_specific)` (phase-8
    decision 1) and built when what it constructs is an `Adapter` with a `check`: the
    in-process kind alone takes `replay` and `credentials` (what preflight resolved: the
    Claude Code login has it carry the Target's calls through the CLI, decision 29), and the
    HTTP kind the Target's Connector when its `tool_truth` reads one (decision 8).
    `allow_live` is `run --live` (decision 9). With `drives` False (a rescore) the Adapter
    is described and never checked: no credential read, no `live` refusal, nothing imported
    that `check` would import.

    `environment` is `run --env` (phase-8 decision 12): the Adapter's default when None,
    else the name given (an empty one included); a name the Adapter block does not declare
    is a problem naming the ones it does, since only an Adapter environment converses (one
    the Connector alone names is read and pushed to, never run).

    The description also records the environment's `turn_timeout` in force (decision 56),
    read through the Manifest and refused here when it is not a positive number.
    """
    problems: list[str] = []
    name = manifest.adapter.kind
    try:
        kind = adapter_class(name)
        chosen = manifest.adapter.default_environment if environment is None else environment
        declared = manifest.adapter.environment_names
        if chosen not in declared:
            problems.append(unknown_environment(chosen, declared))
            return None, None, problems
        specific: dict[str, Any] = {}
        if isinstance(kind, type) and issubclass(kind, InProcessAdapter):
            specific = {"replay": replay, "credentials": credentials}
        elif isinstance(kind, type) and issubclass(kind, HttpAdapter):
            specific = _connector_for(manifest, chosen)
        adapter = kind(
            manifest.adapter.as_adapter_config(),
            environment=chosen,
            tool_kinds=manifest.tool_kinds,
            allow_live=allow_live,
            **specific,
        )
        if not isinstance(adapter, BuiltAdapter):
            problems.append(
                f"Adapter: the {name} Adapter is installed, and what it builds is not an "
                "Adapter (describe, check_fixtures, open and check)"
            )
            return None, None, problems
        built = adapter
        if drives:
            built.check()
        description = built.describe().model_copy(
            update={"turn_timeout_s": manifest.adapter.turn_timeout_s(chosen)}
        )
        return built, description, problems
    except LiveSideEffectsRefused as exc:
        problems.append(str(exc))
    except (ImportError, KeyError, TypeError, ValueError, ManifestError, ConnectorError) as exc:
        problems.append(f"Adapter: {exc}")
    return None, None, problems


def unknown_kinds(manifest: Manifest, *, connector: bool) -> list[str]:
    """`manifest_checks.kind_problems` rendered as `validate` renders a problem,
    `<where>: <message>` (ADR-0015 §3)."""
    return [
        f"{where}: {message}" for where, message in kind_problems(manifest, connector=connector)
    ]


def reads_tool_truth(manifest: Manifest) -> bool:
    """Whether the Adapter block sets `tool_truth`, which hands the HTTP kind the Connector
    (`_connector_for`'s condition, decision 8). Read as the key's presence, without loading
    the Adapter class, so a plugin kind built on the HTTP Adapter counts too and a malformed
    block is left to `build_adapter` to name."""
    return (manifest.adapter.model_extra or {}).get(TOOL_TRUTH_KEY) is not None


def unknown_environment(environment: str, declared: Sequence[str]) -> str:
    """`run --env` naming an environment the Adapter block does not declare (phase-8
    decision 12): the problem names the ones it does."""
    return (
        f"the Manifest names no Adapter environment {environment!r}; it names "
        f"{', '.join(declared) or 'none'}"
    )


def _connector_for(manifest: Manifest, environment: str) -> dict[str, Any]:
    """The HTTP kind's own arguments: the Target's Connector, when the Adapter block's
    `tool_truth` names a store to read (decision 8), and how to scrub its errors."""
    if tool_truth_of(manifest.adapter) is None:
        return {}
    try:
        connector = build_connector(manifest)
    except UnknownKind as unknown:
        raise ValueError(f"adapter.tool_truth reads through the Connector, and {unknown}") from None
    return {
        "connector": connector,
        "scrub_evidence": lambda text: scrub_credentials(text, manifest, environment),
    }


__all__ = [
    "ADHOC_SUITE",
    "DRAFT_DETAIL",
    "NO_CREDENTIALS",
    "PREFLIGHT_EXIT",
    "SAME_REVIEWER",
    "STRICT_REFUSAL",
    "STRICT_REMEDIES",
    "TOOL_EVALS_WITHOUT_TOOL_TRUTH",
    "BuiltAdapter",
    "Plan",
    "PlannedScenario",
    "PreflightFailed",
    "SyncOutcome",
    "build_adapter",
    "credential_problems",
    "draft_suites",
    "inherit_forbidden_phrases",
    "inherit_tool_argument_types",
    "judge_overrides",
    "judged_declarations",
    "load_judge_notes",
    "load_suites",
    "not_implemented_warnings",
    "preflight",
    "read_manifest_prompts",
    "reads_tool_truth",
    "resolve_credentials",
    "resolve_guardrails",
    "restrict",
    "schema_problems",
    "sync_plan",
    "tool_truth_warnings",
    "undeclared",
    "unknown_environment",
    "unknown_kinds",
    "with_adhoc",
    "with_default_thresholds",
    "without_drafts",
]
