"""`compare`: a Baseline and a Run, configuration first (D33, ADR-0005 §6, ADR-0003 §8).

A difference in Scores means nothing until the reader knows what else differed. A model
swap reported as a regression is the failure this module exists to prevent, so the
comparison is built, and printed, in one order and no other:

1. **Header**: the two Runs, their Targets, when each was made, and whether they share
   Traces (a rescore and its source, or two rescores of one source): then only the
   judgement can differ.
2. **Configuration diff**: every leaf of the listed `run.json` paths that differs, each
   `declared` when an `--expect` path or prefix covers it, `undeclared` otherwise. The
   paths are the ones that change what a Trial does or how it is judged, and they include
   each intersecting Scenario's Eval declarations (`scenarios.<id>.evals.<eval[id]>`, their
   `params` and `threshold`), because an edited threshold moves a Verdict without the
   Target changing at all. `adapter.config.*` is left out: it is the Manifest's
   environment block copied verbatim, already under `manifest.adapter.environments.*`, and
   diffing it twice would leave a declared change with an undeclared twin. So are `run_id`,
   `created_at`, the selection, `not_run` and agentdiag's own git state: every Run differs
   in them and none of them is the Target's or the Judge's configuration.
3. **Judge statement**, before any Score, when any `judge.*` path differs (ADR-0003 §8: the
   Judge's configuration is what a judged Score measures against). A Judge model change
   makes the judged comparison invalid; declared, the judged deltas measure the Judge, not
   the Target. A prompt, notes or effort change says the same in the words "the Judge
   configuration changed". A Backend change (`judge.backend.*`, ticket 19) — the same
   model reached by a different path — says the same in the words "the Judge Backend
   changed", naming, when the kind changed, how each side held a structured answer to its
   schema (`structured output constrained at decoding -> checked after the fact, one
   retry`, phase-5 decision 44), or that one Run records no Backend and whether it changed
   is unknown. Either
   way every judged delta it touches is labelled `changed` and marked `judge changed`;
   mechanical Scores are unaffected by the Judge. The block also says, when either side's
   Scorecard counts any, how many cites each Judge had read as the Span ids they begin with
   (decision 37, counted by ticket 21's decision 39): a Judge that answered outside its
   schema is part of what the judged deltas measure.
4. **Scenario sets**: the intersection by Scenario id, and the Scenarios in one Run only,
   counted and named. Nothing below is aggregated over anything but the intersection.
5. **Score deltas**, joined by (Scenario, eval, eval_id), each side the `EvalAggregate` its
   Scorecard already holds. The delta is always the change in `mean`, the pass rate over
   decidable Trials, because a Verdict that flipped is the change whatever kind of Eval it
   is; a Metric's mean value and direction are shown beside it as `value_delta`. The
   aggregation is the Scorecard's (D25) and is not redone here. Abnormal counts sit beside
   each delta and never inside it.
6. **Summary line**: the count of each label that occurs, then the undeclared count, the
   declared paths, or that nothing differed.

**A driven Run and an imported one are not measured alike** (ADR-0013 §5, phase-6 decision
36): an imported Trace is reconstructed or observed from evidence, a driven one recorded by
the Adapter. So `compare` refuses a `run` Run against an `imported` one — naming both
sources — unless `--expect source` declares the difference, and then every Score delta is
`undecided`, its `because` naming `source`: the comparison is shown, and nothing in it is
called a regression or an improvement.

A label claims direction (`regression`, `improvement`) only when no undeclared difference
exists and no Judge statement touches the Score; otherwise a moved delta is `changed`, with
`because` naming the paths. `unchanged` and `undecided` claim no direction, so they stand
either way, and still carry `because` when a difference could have moved them. The word
"regression" is printed only as the label of an actual one.

`compare` reads the two Run directories and nothing else: `run.json` and `scorecard.json`,
each validated through its model (D2), no index, no Trace.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, ValidationError

from agentdiag.eval.score import score_name
from agentdiag.exits import USAGE_EXIT
from agentdiag.run.locate import RUN_RECORD_FILE, SCORECARD_FILE
from agentdiag.run.record import RunRecord
from agentdiag.run.scorecard import EvalAggregate, Scorecard, ratio
from agentdiag.types import DEFAULT_RUN_SOURCE, STRUCTURED_OUTPUT

SOURCE_PATH = "source"
"""The `run.json` path `--expect` names to compare a driven Run with an imported one."""

COMPARE_EXIT_USAGE = USAGE_EXIT
"""A Run not found, a directory that is not a Run, or an `--expect` naming nothing (D32)."""

Label = Literal["regression", "improvement", "unchanged", "undecided", "changed"]
LABELS: tuple[Label, ...] = get_args(Label)

SHARED_TRACES = "the two Runs share Traces: only the judgement differs"
JUDGE_MODEL_INVALID = "the Score comparison is invalid across a Judge change"
JUDGE_MODEL_DECLARED = "the judged deltas measure the Judge, not the Target"
JUDGE_CONFIGURATION = "the Judge configuration changed"
JUDGE_BACKEND = "the Judge Backend changed"
JUDGE_BACKEND_UNRECORDED = "one Run records no Judge Backend"
JUDGE_LABELLED = "judged deltas are labelled judge changed"

EPSILON = 1e-9
"""Two means closer than this are the same number printed twice."""


class CompareUsageError(ValueError):
    """What `compare` cannot compare: a missing Run, or an `--expect` naming nothing."""


class RunHeader(BaseModel):
    """One side of the comparison, as its `run.json` names it."""

    run_id: str
    target: str | None = None
    created_at: str | None = None
    traces_from: str | None = None


class ConfigDifference(BaseModel):
    """One `run.json` leaf that differs between the two Runs."""

    path: str
    baseline: Any = None
    run: Any = None
    declared: bool


class ScenarioSets(BaseModel):
    """Which Scenarios both Runs recorded, and which only one did."""

    intersection: list[str] = Field(default_factory=list)
    only_in_baseline: list[str] = Field(default_factory=list)
    only_in_run: list[str] = Field(default_factory=list)


class ScoreDelta(BaseModel):
    """One Eval of one Scenario, Baseline against Run."""

    scenario: str
    eval: str
    eval_id: str | None = None
    baseline: EvalAggregate | None = None
    run: EvalAggregate | None = None
    delta: float | None = None
    """Run minus Baseline of `mean`, the pass rate over decidable Trials."""

    value_delta: float | None = None
    """For a Metric: Run minus Baseline of `value_mean`, read with its `direction`."""

    label: Label
    because: list[str] = Field(default_factory=list)
    """The configuration paths that could have moved this delta: every undeclared
    difference, and the Judge paths that touch this Score."""

    judge_changed: bool = False
    """A `judge.*` difference touches this Score: the Judge statement applies to it."""

    @property
    def name(self) -> str:
        return score_name(self.eval, self.eval_id)


class CitesRead(BaseModel):
    """How often each side's Judge had a cite read as the Span id it begins with
    (decision 37): each Run's `Scorecard.cites_read`, as two sides, the way every two-sided
    value here is carried (ticket 21, decision 39)."""

    baseline: int = 0
    run: int = 0


class Comparison(BaseModel):
    """What `compare` found, in the order it is printed (D33, ADR-0005 §6)."""

    baseline: RunHeader
    run: RunHeader
    shared_traces: bool = False
    differences: list[ConfigDifference] = Field(default_factory=list)
    judge_statements: list[str] = Field(default_factory=list)
    scenarios: ScenarioSets = Field(default_factory=ScenarioSets)
    deltas: list[ScoreDelta] = Field(default_factory=list)
    counts: dict[Label, int] = Field(default_factory=dict)
    """How many deltas carry each label; a label no delta carries is left out."""

    cites_read: CitesRead = Field(default_factory=CitesRead)
    """Each side's `Scorecard.cites_read`: how often its Judge's cites were read as the Span
    ids they begin with (decision 37; ticket 21, decision 39). Printed in the Judge block
    when either is non-zero: a Judge answering outside its schema is part of what a judged
    delta measures."""

    summary: str = ""


# --- which paths are compared ---


def configuration(record: Mapping[str, Any], scenarios: Sequence[str]) -> dict[str, Any]:
    """The part of a `run.json` a comparison diffs, laid out under its own dotted paths.

    Included: `manifest.*` except `suites` and `suppressions`; `adapter.*` except
    `config`; `judge.model`, `.effort`, `.backend.kind` and `.cli_version`,
    `.prompts.<eval>.version` and `.fingerprint`, `.notes.fingerprint`, `.overrides.*`,
    `.suppressions.<id>.*`, `.reviewer.*` except `.prompt.text`; `simulated_user.*` except
    `.prompt.text` (phase-5 decision 52);
    `price_table.version`; `fingerprint.*`; `sync.status`; `agentdiag.version` and
    `.packages.*`; `git.target.commit` and `.dirty`; `trials`; and
    `scenarios.<id>.evals.<eval[id]>.params` and `.threshold` for each id in `scenarios`,
    the intersection.
    """
    manifest = dict(record.get("manifest") or {})
    manifest.pop("suites", None)
    # Diffed under `judge.suppressions.<id>` (decision 16): one edit, one difference.
    manifest.pop("suppressions", None)
    adapter = record.get("adapter")
    judge = record.get("judge")
    backend = (judge or {}).get("backend") or {}
    agentdiag = record.get("agentdiag") or {}
    target_git = (record.get("git") or {}).get("target") or {}
    declared = {
        summary["id"]: {
            score_name(e["eval"], e.get("id")): {
                "params": e.get("params") or {},
                "threshold": e.get("threshold"),
            }
            for e in summary.get("evals") or []
        }
        for summary in record.get("scenarios") or []
        if summary["id"] in scenarios
    }
    return {
        "source": record.get("source") or DEFAULT_RUN_SOURCE,
        "manifest": manifest,
        "adapter": None
        if adapter is None
        else {key: value for key, value in adapter.items() if key != "config"},
        "judge": None
        if judge is None
        else {
            "model": judge.get("model"),
            "effort": judge.get("effort"),
            "backend": {"kind": backend.get("kind"), "cli_version": backend.get("cli_version")},
            "prompts": {
                name: {"version": prompt.get("version"), "fingerprint": prompt.get("fingerprint")}
                for name, prompt in (judge.get("prompts") or {}).items()
            },
            "notes": {"fingerprint": (judge.get("notes") or {}).get("fingerprint")},
            "overrides": judge.get("overrides") or {},
            # By id, so an added, expired or reworded Suppression is its own path (decision 16).
            "suppressions": {
                str(suppression.get("id")): suppression
                for suppression in judge.get("suppressions") or []
                if isinstance(suppression, Mapping)
            },
            **(
                {"reviewer": _without_prompt_text(judge["reviewer"])}
                if judge.get("reviewer") is not None
                else {}
            ),
        },
        "simulated_user": _without_prompt_text(record.get("simulated_user")),
        "price_table": {"version": (record.get("price_table") or {}).get("version")},
        "fingerprint": record.get("fingerprint"),
        "sync": {"status": (record.get("sync") or {}).get("status")},
        "agentdiag": {"version": agentdiag.get("version"), "packages": agentdiag.get("packages")},
        "git": {"target": {"commit": target_git.get("commit"), "dirty": target_git.get("dirty")}},
        "trials": record.get("trials", 1),
        "scenarios": {identifier: {"evals": evals} for identifier, evals in declared.items()},
    }


def _without_prompt_text(section: Any) -> Any:
    """A `run.json` section with its prompt's full text left out: the version and the
    Fingerprint say whether it changed, and a diff of the text would be the whole prompt."""
    if not isinstance(section, Mapping):
        return section
    prompt = section.get("prompt")
    if not isinstance(prompt, Mapping):
        return dict(section)
    return {**section, "prompt": {k: v for k, v in prompt.items() if k != "text"}}


def leaves(value: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Every (dotted path, leaf) under `value`; a list is a leaf, compared whole."""
    if isinstance(value, Mapping) and value:
        for key in sorted(value):
            yield from leaves(value[key], f"{prefix}.{key}" if prefix else str(key))
    else:
        yield prefix, value


def differences(baseline: Any, run: Any, prefix: str = "") -> Iterator[tuple[str, Any, Any]]:
    """Every leaf that differs, recursing while both sides are mappings.

    Where one side has a mapping and the other nothing, each leaf of the mapping is its
    own difference, so `--expect` can name any of them; the one diff there is.
    """
    if isinstance(baseline, Mapping) and isinstance(run, Mapping):
        for key in sorted(set(baseline) | set(run)):
            yield from differences(
                baseline.get(key), run.get(key), f"{prefix}.{key}" if prefix else str(key)
            )
        return
    if isinstance(baseline, Mapping) and baseline and run is None:
        for path, value in leaves(baseline, prefix):
            yield path, value, None
        return
    if isinstance(run, Mapping) and run and baseline is None:
        for path, value in leaves(run, prefix):
            yield path, None, value
        return
    if baseline != run:
        yield prefix, baseline, run


def covers(expect: str, path: str) -> bool:
    """An `--expect` names a path exactly or as a prefix of dotted segments."""
    return path == expect or path.startswith(f"{expect}.")


# --- reading the two Runs ---


class _Side:
    """One Run as `compare` reads it: its `run.json` and its Scorecard, nothing else."""

    def __init__(self, run_dir: Path) -> None:
        self.dir = run_dir
        record = _read(run_dir / RUN_RECORD_FILE, run_dir, RunRecord)
        self.scorecard = _read(run_dir / SCORECARD_FILE, run_dir, Scorecard)
        self.id = record.run_id
        self.record: dict[str, Any] = record.model_dump(mode="json")

    @property
    def header(self) -> RunHeader:
        target = (self.record.get("manifest") or {}).get("target") or {}
        return RunHeader(
            run_id=self.id,
            target=target.get("name"),
            created_at=self.record.get("created_at"),
            traces_from=self.record.get("traces_from"),
        )

    @property
    def scenario_ids(self) -> list[str]:
        return [scenario.id for scenario in self.scorecard.scenario_aggregates]

    @property
    def simulated(self) -> set[str]:
        """The Scenarios a Simulated User played in this Run (`scenarios[*].simulated`)."""
        return {
            summary["id"]
            for summary in self.record.get("scenarios") or []
            if summary.get("simulated")
        }

    @property
    def overridden(self) -> set[str]:
        return set(((self.record.get("judge") or {}).get("overrides") or {}).keys())

    def aggregates(self) -> dict[str, dict[tuple[str, str | None], EvalAggregate]]:
        return {
            scenario.id: {(e.eval, e.eval_id): e for e in scenario.evals}
            for scenario in self.scorecard.scenario_aggregates
        }


def _read[M: BaseModel](path: Path, run_dir: Path, model: type[M]) -> M:
    """A Run file through the model that wrote it: a malformed one never compares (D2)."""
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise CompareUsageError(f"{run_dir} is not a Run: {path} cannot be read ({exc})") from exc


# --- the comparison ---


def compare(baseline_dir: Path, run_dir: Path, expect: Sequence[str] = ()) -> Comparison:
    """Compare two Run directories; raises `CompareUsageError` on a usage error (D33)."""
    baseline, run = _Side(Path(baseline_dir)), _Side(Path(run_dir))
    run_ids = set(run.scenario_ids)
    base_ids = set(baseline.scenario_ids)
    sets = ScenarioSets(
        intersection=[i for i in baseline.scenario_ids if i in run_ids],
        only_in_baseline=[i for i in baseline.scenario_ids if i not in run_ids],
        only_in_run=[i for i in run.scenario_ids if i not in base_ids],
    )
    base_config = configuration(baseline.record, sets.intersection)
    run_config = configuration(run.record, sets.intersection)

    known = {path for path, _ in leaves(base_config)} | {path for path, _ in leaves(run_config)}
    unknown = [e for e in expect if not any(covers(e, path) for path in known)]
    if unknown:
        raise CompareUsageError(
            "--expect names nothing in either Run: "
            + ", ".join(unknown)
            + " (a path of run.json such as manifest.adapter.environments.local.model, "
            "judge.model or scenarios.<id>.evals.<eval>)"
        )

    sources = (
        str(baseline.record.get("source") or DEFAULT_RUN_SOURCE),
        str(run.record.get("source") or DEFAULT_RUN_SOURCE),
    )
    across_sources = sources[0] != sources[1]
    if across_sources and not any(covers(e, SOURCE_PATH) for e in expect):
        raise CompareUsageError(
            f"the Baseline {baseline.id} is of source {sources[0]} and the Run {run.id} of "
            f"source {sources[1]}: an imported Trace is not measured as a driven one is, so "
            "compare refuses to aggregate them; declare it with --expect source to see the "
            "comparison with every Score undecided"
        )

    found = [
        ConfigDifference(
            path=path,
            baseline=before,
            run=after,
            declared=any(covers(e, path) for e in expect),
        )
        for path, before, after in differences(base_config, run_config)
    ]
    undeclared = [d.path for d in found if not d.declared]
    judge_paths = [d for d in found if d.path.startswith("judge.")]

    overridden = baseline.overridden | run.overridden
    simulated = baseline.simulated | run.simulated
    deltas = [
        _delta(
            scenario,
            key,
            before,
            after,
            undeclared,
            judge_paths,
            overridden,
            simulated=scenario in simulated,
            across_sources=across_sources,
        )
        for scenario, key, before, after in _joined(baseline, run, sets.intersection)
    ]
    counts: dict[Label, int] = {}
    for label in LABELS:
        count = sum(1 for delta in deltas if delta.label == label)
        if count:
            counts[label] = count

    comparison = Comparison(
        baseline=baseline.header,
        run=run.header,
        shared_traces=_share_traces(baseline.header, run.header),
        differences=found,
        judge_statements=_judge_statements(judge_paths),
        scenarios=sets,
        deltas=deltas,
        counts=counts,
        cites_read=CitesRead(
            baseline=baseline.scorecard.cites_read,
            run=run.scorecard.cites_read,
        ),
    )
    comparison.summary = _summary(comparison)
    return comparison


def _share_traces(baseline: RunHeader, run: RunHeader) -> bool:
    """Either Run's `traces_from` names the other, or both name one source."""
    return (
        baseline.traces_from == run.run_id
        or run.traces_from == baseline.run_id
        or (baseline.traces_from is not None and baseline.traces_from == run.traces_from)
    )


def _joined(
    baseline: _Side, run: _Side, intersection: Sequence[str]
) -> Iterator[tuple[str, tuple[str, str | None], EvalAggregate | None, EvalAggregate | None]]:
    """(Scenario, (eval, eval_id), Baseline side, Run side) over the intersection only."""
    base, other = baseline.aggregates(), run.aggregates()
    for scenario in intersection:
        keys = list(base[scenario]) + [k for k in other[scenario] if k not in base[scenario]]
        for key in keys:
            yield scenario, key, base[scenario].get(key), other[scenario].get(key)


def _judge_touches(
    path: str,
    key: tuple[str, str | None],
    judged: bool,
    overridden: set[str],
    *,
    simulated: bool = False,
) -> bool:
    """Whether a `judge.*` difference at `path` bears on this Score (ADR-0003 §8).

    The reviewer and the stop check reach every Score of a simulated Scenario, mechanical
    ones included — the reviewer rewrites every `fail`, the stop check moves the
    conversation every Score reads — and no Score of a Scenario without a `simulate` Turn
    (amended decision 52)."""
    parts = path.split(".")
    if parts[1] == "reviewer" or parts[1:3] == ["prompts", "stop_when"]:
        return simulated
    if not judged:
        return False
    name, eval_id = key
    declaration = eval_id or name
    if parts[1] in ("model", "effort"):
        return declaration not in overridden
    if parts[1] == "overrides":
        return len(parts) > 2 and parts[2] == declaration
    if parts[1] == "prompts":
        return len(parts) > 2 and parts[2] == name
    # The calibration notes reach every judged prompt, and every Judge call, overridden or
    # not, took the Run's one Backend.
    return True


def _delta(
    scenario: str,
    key: tuple[str, str | None],
    before: EvalAggregate | None,
    after: EvalAggregate | None,
    undeclared: Sequence[str],
    judge_paths: Sequence[ConfigDifference],
    overridden: set[str],
    *,
    simulated: bool,
    across_sources: bool = False,
) -> ScoreDelta:
    judged = bool((before and before.judged) or (after and after.judged))
    touching = [
        d.path
        for d in judge_paths
        if _judge_touches(d.path, key, judged, overridden, simulated=simulated)
    ]
    # Every undeclared difference withholds a direction everywhere (D33); a Judge
    # difference, declared or not, withholds it from the judged Scores it touches.
    because = list(dict.fromkeys([*undeclared, *touching]))
    if across_sources:
        because = list(dict.fromkeys([SOURCE_PATH, *because]))
    undecided = ScoreDelta(
        scenario=scenario,
        eval=key[0],
        eval_id=key[1],
        baseline=before,
        run=after,
        value_delta=_value_delta(before, after),
        label="undecided",
        because=because,
        judge_changed=bool(touching),
    )
    if before is None or after is None or before.mean is None or after.mean is None:
        return undecided

    delta = after.mean - before.mean
    if across_sources:
        return undecided.model_copy(update={"delta": delta})
    label: Label = (
        "regression" if delta < -EPSILON else "improvement" if delta > EPSILON else "unchanged"
    )
    if label != "unchanged" and because:
        label = "changed"
    return undecided.model_copy(update={"delta": delta, "label": label})


def _value_delta(before: EvalAggregate | None, after: EvalAggregate | None) -> float | None:
    """A Metric's change in mean value; shown beside the pass-rate delta, never for it."""
    if before is None or after is None or before.value_mean is None or after.value_mean is None:
        return None
    return after.value_mean - before.value_mean


def _judge_statements(judge_paths: Sequence[ConfigDifference]) -> list[str]:
    """What must be said before any Score when the Judge differs (D33, ADR-0003 §8)."""
    statements: list[str] = []
    models = [d for d in judge_paths if _is_model_path(d.path)]
    backends = [d for d in judge_paths if _is_backend_path(d.path)]
    others = [d for d in judge_paths if d not in models and d not in backends]
    if models:
        paths = ", ".join(d.path for d in models)
        statements.append(
            f"{JUDGE_MODEL_DECLARED} ({paths} declared); {JUDGE_LABELLED}"
            if all(d.declared for d in models)
            else f"{JUDGE_MODEL_INVALID} ({paths}); {JUDGE_LABELLED}"
        )
    if backends:
        statements.append(_backend_statement(backends))
    if others:
        paths = ", ".join(d.path for d in others)
        statements.append(
            f"{JUDGE_CONFIGURATION} ({paths} declared): {JUDGE_MODEL_DECLARED}; {JUDGE_LABELLED}"
            if all(d.declared for d in others)
            else f"{JUDGE_CONFIGURATION} ({paths}): the Score comparison is invalid across it; "
            f"{JUDGE_LABELLED}"
        )
    return statements


def _is_model_path(path: str) -> bool:
    parts = path.split(".")
    return path == "judge.model" or (parts[:2] == ["judge", "overrides"] and parts[-1] == "model")


def _is_backend_path(path: str) -> bool:
    return path.startswith("judge.backend.")


def _backend_statement(backends: Sequence[ConfigDifference]) -> str:
    """The Judge Backend statement: unrecorded, declared, or invalid across the change, with
    the structured-output modes named when the kind changed (phase-5 decision 44)."""
    paths = ", ".join(d.path for d in backends)
    if any(_unrecorded(d) for d in backends):
        return (
            f"{JUDGE_BACKEND_UNRECORDED} ({paths}): whether the path changed is unknown; "
            f"{JUDGE_LABELLED}"
        )
    modes = _modes(backends)
    if all(d.declared for d in backends):
        return (
            f"{JUDGE_BACKEND} ({paths} declared{modes}): {JUDGE_MODEL_DECLARED}; {JUDGE_LABELLED}"
        )
    return (
        f"{JUDGE_BACKEND} ({paths}{modes}): the Score comparison is invalid across it; "
        f"{JUDGE_LABELLED}"
    )


def _modes(backends: Sequence[ConfigDifference]) -> str:
    """`; structured output <baseline's> -> <run's>` when the Backend's kind changed and both
    sides record one (phase-5 decision 44); empty when only `cli_version` moved."""
    kind = next((d for d in backends if d.path == "judge.backend.kind"), None)
    if kind is None:
        return ""
    before, after = STRUCTURED_OUTPUT.get(kind.baseline), STRUCTURED_OUTPUT.get(kind.run)
    if before is None or after is None:
        return ""
    return f"; structured output {before} -> {after}"


def _unrecorded(difference: ConfigDifference) -> bool:
    """A side records no Backend at all (a Run from before ticket 19). Only `kind` says so:
    `cli_version` is null by design for the API and replay Backends."""
    return difference.path == "judge.backend.kind" and (
        difference.baseline is None or difference.run is None
    )


def _summary(comparison: Comparison) -> str:
    counts = "  ".join(f"{label} {count}" for label, count in comparison.counts.items())
    undeclared = [d for d in comparison.differences if not d.declared]
    if undeclared:
        plural = "" if len(undeclared) == 1 else "s"
        tail = f"{len(undeclared)} undeclared difference{plural}: moved deltas are labelled changed"
    elif comparison.differences:
        tail = "declared differences only: " + ", ".join(d.path for d in comparison.differences)
    else:
        tail = "no configuration difference"
    return f"{counts}  {tail}" if counts else tail


# --- the text ---


def render_comparison(comparison: Comparison) -> str:
    """The six sections, in the order the module docstring gives and no other."""
    lines: list[str] = []
    for label, side in (("baseline", comparison.baseline), ("run", comparison.run)):
        rescored = f"  traces from {side.traces_from}" if side.traces_from else ""
        lines.append(
            f"{label:<8}  {side.run_id}  target {side.target or 'unknown'}"
            f"  created {side.created_at or 'unknown'}{rescored}"
        )
    if comparison.shared_traces:
        lines.append(SHARED_TRACES)

    lines.append("")
    lines.append(f"configuration  {len(comparison.differences)} difference(s)")
    for difference in comparison.differences:
        mark = "declared  " if difference.declared else "undeclared"
        lines.append(
            f"  {mark}  {difference.path}  {_value(difference.baseline)} -> "
            f"{_value(difference.run)}"
        )

    for statement in comparison.judge_statements:
        lines.append("")
        lines.append(f"judge  {statement}")
    if comparison.cites_read.baseline or comparison.cites_read.run:
        lines.append("")
        lines.append(cites_read_line(comparison.cites_read))

    sets = comparison.scenarios
    lines.append("")
    lines.append(f"scenarios  {len(sets.intersection)} in both")
    lines.append(f"  only in baseline {len(sets.only_in_baseline)}" + _named(sets.only_in_baseline))
    lines.append(f"  only in run {len(sets.only_in_run)}" + _named(sets.only_in_run))

    lines.append("")
    scenario = None
    for delta in comparison.deltas:
        if delta.scenario != scenario:
            scenario = delta.scenario
            lines.append(scenario)
        lines.append(_delta_line(delta))

    lines.append("")
    lines.append(comparison.summary)
    return "\n".join(lines)


def cites_read_line(cites_read: CitesRead) -> str:
    """The Judge block's count of decision 37's fallback, both sides on one line."""
    return (
        f"Judge cites read as Span ids: baseline {cites_read.baseline}, "
        f"run {cites_read.run} (decision 37)"
    )


def _named(ids: Sequence[str]) -> str:
    return f": {', '.join(ids)}" if ids else ""


def _value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def _delta_line(delta: ScoreDelta) -> str:
    """`<name>  mean b -> r  delta d[  value (dir) b -> r  value delta v]  <label>
    [  because p, q][  judge changed]  abnormal b -> r`: the JSON's content, on one line."""
    moved = "" if delta.delta is None else f"  delta {delta.delta:+.2f}"
    side = delta.baseline or delta.run
    value = ""
    if side is not None and side.is_metric:
        change = "" if delta.value_delta is None else f"  value delta {delta.value_delta:+.2f}"
        value = (
            f"  value ({side.direction}) {_value_mean(delta.baseline)} -> "
            f"{_value_mean(delta.run)}{change}"
        )
    because = f"  because {', '.join(delta.because)}" if delta.because else ""
    judge = "  judge changed" if delta.judge_changed else ""
    return (
        f"  {delta.name}  mean {_mean(delta.baseline)} -> {_mean(delta.run)}{moved}{value}"
        f"  {delta.label}{because}{judge}"
        f"  abnormal {render_abnormal(delta.baseline)} -> {render_abnormal(delta.run)}"
    )


def _mean(side: EvalAggregate | None) -> str:
    return "absent" if side is None else ratio(side.mean)


def _value_mean(side: EvalAggregate | None) -> str:
    if side is None:
        return "absent"
    return "n/a" if side.value_mean is None else f"{side.value_mean:.2f}"


def render_abnormal(side: EvalAggregate | None) -> str:
    """`incomplete 1, invalid 2`, or `none`: beside the delta, never folded into it; the one
    spelling of a side's abnormal counts, which the close gate's refusal reads too (phase-8
    decision 14)."""
    if side is None:
        return "absent"
    shown = [f"{verdict} {count}" for verdict, count in side.abnormal.items() if count]
    return ", ".join(shown) or "none"


__all__ = [
    "COMPARE_EXIT_USAGE",
    "JUDGE_BACKEND",
    "JUDGE_BACKEND_UNRECORDED",
    "JUDGE_CONFIGURATION",
    "JUDGE_LABELLED",
    "JUDGE_MODEL_DECLARED",
    "JUDGE_MODEL_INVALID",
    "LABELS",
    "SHARED_TRACES",
    "CitesRead",
    "CompareUsageError",
    "Comparison",
    "ConfigDifference",
    "RunHeader",
    "ScenarioSets",
    "ScoreDelta",
    "cites_read_line",
    "compare",
    "configuration",
    "covers",
    "differences",
    "leaves",
    "render_abnormal",
    "render_comparison",
]
