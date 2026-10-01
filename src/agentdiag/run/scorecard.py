"""The Scorecard: every Score in a Run, aggregated once (ADR-0003 §2, ADR-0005 §8, D25).

Aggregation is a property of the Verdict, and it lives here and nowhere else. No Eval
chooses how it counts. Two rules do all the work:

- Only `pass` and `fail` enter the pass rate. The other three are counted and enumerated,
  never averaged away, and the rate is `None` — not zero — when nothing decidable ran.
- A negated Eval flips `pass` and `fail` and touches nothing else, so an `unverifiable`
  can never become a `pass` by being asked the opposite question.

The rate is never printed alone. `render_summary` puts it on the same line as the counts
and the Sync status, because a pass rate without what it excluded is the number a
renderer drifts into telling. When any Score came from a Judge that resolved to
the Target's own model, the same line says so (D23): a model grading its own output is
known to favour it, and a pass rate that hides that is not the number it looks like.

**Trials** (ticket 08, ADR-0005 §7). A Run of `trials` N keeps every Trial's Scores, and
this module adds, never replaces, the per-Scenario view `compare` joins on: one
`EvalAggregate` per Eval of a Scenario across its Trials (the mean over decidable Trials,
the Metric's mean value and direction, the abnormal counts beside them), and one
`ScenarioAggregate` per Scenario with tau-bench's pass^k (research
`agent-eval-frameworks` §10): for a Scenario with `c` passing Trials out of `n` decidable
ones, pass^k = C(c, k) / C(n, k), the chance that k Trials drawn from those n all pass,
and the Run's pass^k is the mean over the Scenarios that had at least k decidable Trials.
The same two rules decide which Trials count: a Trial with any abnormal Score is neither a
pass nor a fail, so it sits outside pass^k and is counted in `excluded_trials`; a Trial
with no Score at all decided nothing and sits outside it too. pass^k is never printed
alone either: its line carries the trial count, the excluded count and the Simulated
User's sampling beside it, because pass^k at two samplings measures two different things.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from math import comb
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from agentdiag.eval.score import SHARED_MODEL_WARNING, Score, render_counts, score_name
from agentdiag.scenario.select import NotRun
from agentdiag.sync.compare import SyncSection, status_text
from agentdiag.types import VERDICTS, SamplingSupport, Verdict

RATE_ABSENT = "n/a"
"""What the summary prints where a pass rate, a pass^k or a mean would be, when nothing
decidable ran."""

ABNORMAL: tuple[Verdict, ...] = ("incomplete", "unverifiable", "invalid")
"""The three Verdicts that decide nothing: counted beside every mean, never inside it."""

SAMPLING_NOT_CONFIGURED = "not configured"
"""What the pass^k line says of the Simulated User's sampling when no Simulated User ran
(a Run of literal Turns only): said, rather than left off, so the line never reads alone."""

SAMPLING_NONE_DECLARED = "none declared"
"""What it says when a Simulated User ran with no sampling parameter declared, which is
every v1 default Run (phase-5 decision 53)."""


class ScoreLine(BaseModel):
    """One Score as the Scorecard carries it: enough to read, not the whole Score."""

    eval: str
    eval_id: str | None = None
    """The declaration's id, or the guardrail rule's (decision 16), so a line can say which
    rule broke rather than that `guardrails` did."""

    verdict: Verdict
    reason: str | None = None

    @property
    def name(self) -> str:
        """`guardrails[no-refund-timing]`, or just the Eval when it carries no id."""
        return score_name(self.eval, self.eval_id)


class ScenarioLine(BaseModel):
    """One Trial of one Scenario, and the Scores it produced."""

    id: str
    trial: int
    scores: list[ScoreLine] = Field(default_factory=list)


class EvalAggregate(BaseModel):
    """One Eval of one Scenario across that Scenario's Trials: what `compare` joins on."""

    eval: str
    eval_id: str | None = None
    decidable: int
    """Trials whose Score for this Eval was `pass` or `fail`."""

    passed: int
    mean: float | None = None
    """`passed / decidable`; None when nothing was decidable, never zero."""

    value_mean: float | None = None
    """Metric Evals: the mean of `value` over the Trials whose Score had one."""

    direction: Literal["minimize", "maximize"] | None = None
    """The Metric's direction of better, as its Scores gave it; None for a pass/fail Eval."""

    abnormal: dict[Verdict, int] = Field(default_factory=dict)
    """`incomplete`, `unverifiable` and `invalid` counts, zeros included."""

    judged: bool = False
    """Whether any of its Scores came from a Judge: what a Judge change affects (D33).
    Read from the Scores, not from the catalogue, because a judged Eval whose Scenario
    authored the answer (`data_query` with `expect_tool_args`) is decided mechanically."""

    @property
    def name(self) -> str:
        return score_name(self.eval, self.eval_id)

    @property
    def is_metric(self) -> bool:
        """A Metric Eval's Scores carry a direction of better; a pass/fail Eval's do not."""
        return self.direction is not None


class ScenarioAggregate(BaseModel):
    """One Scenario across its Trials, with its pass^k (ADR-0005 §7, ticket 08)."""

    id: str
    suite: str | None = None
    trials: int
    """Trials recorded, the excluded ones included."""

    decidable: int
    """Trials in which every Score was `pass` or `fail`."""

    passed: int
    """Trials in which every Score was `pass`."""

    pass_k: dict[int, float | None] = Field(default_factory=dict)
    """k = 1..the Run's `trials`: C(passed, k) / C(decidable, k); None when decidable < k."""

    evals: list[EvalAggregate] = Field(default_factory=list)


class Scorecard(BaseModel):
    """The aggregate of all Scores in a Run (`CONTEXT.md`, ADR-0005 §8)."""

    run_id: str
    sync: SyncSection
    trials: int
    counts: dict[Verdict, int]
    """Every Verdict, zeros included: what did not happen is never left off (ADR-0005 §8).

    Closed on both sides (D2). The keys are the five Verdicts and a sixth cannot be
    written; the validator below also refuses a missing one, because a Scorecard that
    simply omitted `invalid` would read as a Run where nothing went wrong."""

    pass_rate: float | None = None
    scenarios: list[ScenarioLine] = Field(default_factory=list)
    not_run: list[NotRun] = Field(default_factory=list)
    """Every Scenario a loaded Suite declared and this Run did not execute (ADR-0005 §8)."""

    shared_model: bool = False
    """Whether any Score's Judge resolved to its Target's model (D23): the self-preference
    risk the summary warns of."""

    scenario_aggregates: list[ScenarioAggregate] = Field(default_factory=list)
    """One per Scenario that recorded a Trial, in the order they first ran (ticket 08)."""

    pass_k: dict[int, float | None] = Field(default_factory=dict)
    """k = 1..`trials`: the mean of the Scenarios' pass^k over those with decidable >= k;
    None when no Scenario had k decidable Trials."""

    excluded_trials: int = 0
    """Trials with any abnormal Score: neither a pass nor a fail, so outside pass^k."""

    simulated_user_sampling: dict[str, str] | None = None
    """The sampling the Simulated User sent and whether the API accepted it (D12, D14,
    amended decision 53): every declared key as `<value> accepted`, `<value> not_supported`
    (winning over the Run's calls) or `<value> not sent`; `{}` when a Simulated User was
    configured with nothing declared; None when none was (`merged_sampling`)."""

    cites_read: int = 0
    """How many cites the Run's Scores had read as the Span id they begin with (decision 37),
    summed over every Score's `cites_read` (ticket 21, decision 39). Always written: a Judge
    that answers outside its schema is a fact about the instrument, and `compare` states it
    beside the Judge's configuration. The Diagnosis is not a Score and is not in it."""

    @model_validator(mode="after")
    def _counts_hold_every_verdict(self) -> Self:
        """All five, exactly: a Scorecard cannot leave a Verdict out (D2, ADR-0005 §8)."""
        if set(self.counts) != set(VERDICTS):
            missing = sorted(set(VERDICTS) - set(self.counts))
            raise ValueError(
                f"counts must hold exactly the five Verdicts {list(VERDICTS)}; missing {missing}"
            )
        return self


def negate(verdict: Verdict) -> Verdict:
    """The Verdict a negated Eval records (D25).

    `pass` and `fail` swap. `incomplete`, `unverifiable` and `invalid` are statements about
    whether a judgement could be made at all, so negating the question leaves them exactly
    where they were: nothing was decided, and asking the opposite decides nothing either.
    """
    if verdict == "pass":
        return "fail"
    if verdict == "fail":
        return "pass"
    return verdict


def aggregate(
    run_id: str,
    sync: SyncSection,
    trials: int,
    scores_by_trial: list[tuple[str, int, list[Score]]],
    not_run: list[NotRun] | None = None,
    *,
    suites: Mapping[str, str] | None = None,
    simulated_user_sampling: dict[str, str] | None = None,
) -> Scorecard:
    """The one aggregation in agentdiag (D25).

    `scores_by_trial` is `(scenario id, trial number, Scores)` in execution order, and
    `not_run` is what never produced one, so the Scorecard says both (ADR-0005 §8).
    `suites` names each Scenario's Suite for its aggregate; `trials` is the Run's
    parameter, and pass^k is reported for every k up to it.
    """
    counts: dict[Verdict, int] = dict.fromkeys(VERDICTS, 0)
    scenarios: list[ScenarioLine] = []
    shared = False

    for scenario_id, trial, scores in scores_by_trial:
        for score in scores:
            counts[score.verdict] += 1
            shared = shared or score.shared_model is True
        scenarios.append(
            ScenarioLine(
                id=scenario_id,
                trial=trial,
                scores=[
                    ScoreLine(
                        eval=score.eval,
                        eval_id=score.eval_id,
                        verdict=score.verdict,
                        reason=score.reason,
                    )
                    for score in scores
                ],
            )
        )

    decidable = counts["pass"] + counts["fail"]
    scenario_aggregates = _scenario_aggregates(scores_by_trial, trials, suites or {})
    return Scorecard(
        run_id=run_id,
        sync=sync,
        trials=trials,
        counts=counts,
        pass_rate=(counts["pass"] / decidable) if decidable else None,
        scenarios=scenarios,
        not_run=list(not_run or []),
        shared_model=shared,
        scenario_aggregates=scenario_aggregates,
        pass_k=_run_pass_k(scenario_aggregates, trials),
        excluded_trials=sum(1 for _, _, scores in scores_by_trial if _excluded(scores)),
        simulated_user_sampling=simulated_user_sampling,
        cites_read=sum(
            len(score.cites_read) for _, _, scores in scores_by_trial for score in scores
        ),
    )


def pass_hat_k(passed: int, decidable: int, k: int) -> float | None:
    """tau-bench's estimator: C(passed, k) / C(decidable, k), None when decidable < k.

    The chance that k Trials drawn without replacement from the decidable ones all pass;
    `math.comb` is zero when passed < k, which is the right answer, not a special case.
    """
    if decidable < k:
        return None
    return comb(passed, k) / comb(decidable, k)


def _excluded(scores: Sequence[Score]) -> bool:
    """A Trial with any abnormal Score: neither a pass nor a fail (ADR-0003 §2)."""
    return any(score.verdict in ABNORMAL for score in scores)


def _decidable(scores: Sequence[Score]) -> bool:
    """A Trial that decided something, and nothing it declared went undecided."""
    return bool(scores) and not _excluded(scores)


def _scenario_aggregates(
    scores_by_trial: Sequence[tuple[str, int, list[Score]]],
    trials: int,
    suites: Mapping[str, str],
) -> list[ScenarioAggregate]:
    by_scenario: dict[str, list[list[Score]]] = {}
    for scenario_id, _, scores in scores_by_trial:
        by_scenario.setdefault(scenario_id, []).append(scores)

    aggregates: list[ScenarioAggregate] = []
    for scenario_id, trial_scores in by_scenario.items():
        decidable = [scores for scores in trial_scores if _decidable(scores)]
        passed = sum(1 for scores in decidable if all(s.verdict == "pass" for s in scores))
        aggregates.append(
            ScenarioAggregate(
                id=scenario_id,
                suite=suites.get(scenario_id),
                trials=len(trial_scores),
                decidable=len(decidable),
                passed=passed,
                pass_k={k: pass_hat_k(passed, len(decidable), k) for k in range(1, trials + 1)},
                evals=_eval_aggregates(trial_scores),
            )
        )
    return aggregates


def _eval_aggregates(trial_scores: Sequence[Sequence[Score]]) -> list[EvalAggregate]:
    """One aggregate per (eval, eval_id), in the order the Scores first appear."""
    by_eval: dict[tuple[str, str | None], list[Score]] = {}
    for scores in trial_scores:
        for score in scores:
            by_eval.setdefault((score.eval, score.eval_id), []).append(score)

    aggregates: list[EvalAggregate] = []
    for (name, eval_id), scores in by_eval.items():
        decided = [score for score in scores if score.verdict in ("pass", "fail")]
        passed = sum(1 for score in decided if score.verdict == "pass")
        values = [score.value for score in scores if score.value is not None]
        aggregates.append(
            EvalAggregate(
                eval=name,
                eval_id=eval_id,
                decidable=len(decided),
                passed=passed,
                mean=(passed / len(decided)) if decided else None,
                value_mean=(sum(values) / len(values)) if values else None,
                direction=next((s.direction for s in scores if s.direction is not None), None),
                abnormal={
                    verdict: sum(1 for score in scores if score.verdict == verdict)
                    for verdict in ABNORMAL
                },
                judged=any(score.source.kind == "judge" for score in scores),
            )
        )
    return aggregates


def _run_pass_k(aggregates: Sequence[ScenarioAggregate], trials: int) -> dict[int, float | None]:
    """Per k, the mean over the Scenarios with at least k decidable Trials (tau-bench)."""
    pass_k: dict[int, float | None] = {}
    for k in range(1, trials + 1):
        values = [a.pass_k[k] for a in aggregates if a.pass_k.get(k) is not None]
        pass_k[k] = (sum(v for v in values if v is not None) / len(values)) if values else None
    return pass_k


def exit_code(scorecard: Scorecard) -> int:
    """What `run` exits with (D32).

    0 when every Score passed — and when there were none at all, because a Run that
    declared no Eval asked no question and failed none. 1 on any `fail`. 2 when nothing
    failed but something could not be decided: "a transport blip is a re-run
    signal, not a regression".
    """
    counts = scorecard.counts
    if counts.get("fail"):
        return 1
    if any(counts.get(verdict) for verdict in ("incomplete", "unverifiable", "invalid")):
        return 2
    return 0


def render_summary(scorecard: Scorecard, *, with_suites: bool = False) -> str:
    """The terminal summary: the Trials and their Scores, one line per Scenario across its
    Trials, what did not run, the counts line, and the pass^k line.

    What did not happen is printed as plainly as what did: every not-run Scenario gets its
    own line before the counts, with its reason and any detail the reason carries, and
    with its Suite when the Run loaded more than one (`with_suites`), since ids are unique
    only within a Suite. A Trial left out of pass^k says so on its own line. The counts
    line carries the pass rate, the numbers behind it, how many Scenarios never ran and the
    Sync status together; the pass^k line carries the trial count, the excluded Trials and
    the Simulated User's sampling. Neither number appears without them (ADR-0005 §7, §8).
    """
    lines: list[str] = []
    for scenario in scorecard.scenarios:
        excluded = any(score.verdict in ABNORMAL for score in scenario.scores)
        note = "  excluded from pass^k" if excluded else ""
        lines.append(f"{scenario.id}  trial {scenario.trial}{note}")
        for score in scenario.scores:
            reason = f"  {score.reason}" if score.reason else ""
            lines.append(f"  {score.name}  {score.verdict}{reason}")
    lines.extend(_scenario_line(aggregate) for aggregate in scorecard.scenario_aggregates)
    for skipped in scorecard.not_run:
        lines.append(skipped.line(with_suite=with_suites))
    lines.append(_counts_line(scorecard))
    lines.append(_pass_k_line(scorecard))
    return "\n".join(lines)


def ratio(value: float | None) -> str:
    """A pass^k or a mean as the summary and `compare` print it: two decimals, or n/a."""
    return RATE_ABSENT if value is None else f"{value:.2f}"


def _pass_k_text(pass_k: Mapping[int, float | None]) -> str:
    return "  ".join(f"pass^{k} {ratio(value)}" for k, value in sorted(pass_k.items()))


def _scenario_line(aggregate: ScenarioAggregate) -> str:
    """`<id>  trials N  passed c of n decidable  pass^1 .. pass^N`."""
    return (
        f"{aggregate.id}  trials {aggregate.trials}"
        f"  passed {aggregate.passed} of {aggregate.decidable} decidable"
        f"  {_pass_k_text(aggregate.pass_k)}"
    )


SAMPLING_NOT_SENT = "not sent"
"""A declared key no Simulated User call sent: the Trial ended before it was asked, or its
call failed before the request went out (amended decision 53)."""


def merged_sampling(
    declared: Mapping[str, float | int] | None,
    outcomes: Iterable[Mapping[str, SamplingSupport]],
) -> dict[str, str] | None:
    """The Run's Simulated User sampling, as the Scorecard carries it: None when no Simulated
    User was configured, `{}` when nothing was declared, and otherwise every declared key as
    `<value> <outcome>` — `not_supported` when any call's API refused it, else `accepted`
    when any call sent it, else `not sent` (amended decision 53). The one merge, over every
    call of every Trial."""
    if declared is None:
        return None
    seen: dict[str, set[SamplingSupport]] = {}
    for outcome in outcomes:
        for key, support in outcome.items():
            seen.setdefault(key, set()).add(support)
    merged: dict[str, str] = {}
    for key, value in declared.items():
        said = seen.get(key, set())
        worded = (
            "not_supported"
            if "not_supported" in said
            else "accepted"
            if "accepted" in said
            else SAMPLING_NOT_SENT
        )
        merged[key] = f"{value} {worded}"
    return merged


def _pass_k_line(scorecard: Scorecard) -> str:
    """pass^k with the trial count, the excluded Trials and the sampling beside it."""
    sampling = scorecard.simulated_user_sampling
    sampling_text = (
        SAMPLING_NOT_CONFIGURED
        if sampling is None
        else ", ".join(f"{key} {value}" for key, value in sorted(sampling.items()))
        or SAMPLING_NONE_DECLARED
    )
    return (
        f"{_pass_k_text(scorecard.pass_k)}"
        f"  trials {scorecard.trials}"
        f"  excluded {scorecard.excluded_trials}"
        f"  sampling {sampling_text}"
    )


def _counts_line(scorecard: Scorecard) -> str:
    counts = scorecard.counts
    decidable = counts["pass"] + counts["fail"]
    rate = RATE_ABSENT if scorecard.pass_rate is None else f"{scorecard.pass_rate:.0%}"
    sync_text = status_text(scorecard.sync.model_dump(mode="json"))
    return (
        render_counts(counts)
        + f"  pass rate {rate} ({counts['pass']} of {decidable})"
        + f"  not run {len(scorecard.not_run)}"
        + f"  sync {sync_text}"
        + (f"  {SHARED_MODEL_WARNING}" if scorecard.shared_model else "")
    )


__all__ = [
    "ABNORMAL",
    "RATE_ABSENT",
    "SAMPLING_NONE_DECLARED",
    "SAMPLING_NOT_CONFIGURED",
    "SAMPLING_NOT_SENT",
    "SHARED_MODEL_WARNING",
    "EvalAggregate",
    "ScenarioAggregate",
    "ScenarioLine",
    "ScoreLine",
    "Scorecard",
    "aggregate",
    "exit_code",
    "merged_sampling",
    "negate",
    "pass_hat_k",
    "ratio",
    "render_summary",
]
