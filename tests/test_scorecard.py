"""Aggregation happens once, and it never lets a Verdict be averaged away (D25, ADR-0003 §2)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from agentdiag.eval.score import Score, ScoreSource
from agentdiag.run.record import NotRun, SyncSection
from agentdiag.run.scorecard import (
    Scorecard,
    aggregate,
    exit_code,
    merged_sampling,
    negate,
    render_summary,
)
from agentdiag.types import Verdict

NOT_CHECKED = SyncSection(status="not_checked", reason="no_fingerprint")
MECHANICAL = ScoreSource(kind="mechanical", code="agentdiag.eval.registry")


def score(verdict: Verdict, *, eval: str = "prompt_adherence", **fields: object) -> Score:
    """One Score written by hand, so the expected counts come from this file, not the code."""
    return Score(
        eval=eval,
        verdict=verdict,
        rationale="written by hand for this test",
        evidence=[],
        source=MECHANICAL,
        fidelity="instrumented",
        **fields,  # type: ignore[arg-type]
    )


def card(*scores: Score) -> Scorecard:
    return aggregate("20260922T101500Z-k7pq", NOT_CHECKED, 1, [("a-scenario", 1, list(scores))])


# --- counts and the rate ---


def test_aggregate_counts_every_verdict_including_the_ones_that_did_not_happen() -> None:
    """ADR-0005 §8: a Scorecard enumerates what did not happen, zeros included."""
    scorecard = card(
        score("pass"),
        score("pass"),
        score("fail"),
        score("unverifiable", reason="eval_not_applicable"),
    )

    assert scorecard.counts == {
        "pass": 2,
        "fail": 1,
        "incomplete": 0,
        "unverifiable": 1,
        "invalid": 0,
    }


def test_the_pass_rate_counts_only_pass_and_fail() -> None:
    """The three abnormal Verdicts are enumerated, never averaged (ADR-0003 §2)."""
    scorecard = card(
        score("pass"),
        score("fail"),
        score("incomplete", reason="target_error"),
        score("unverifiable", reason="evidence_missing"),
        score("invalid", fault_source="judge", fault_direction="none"),
    )

    assert scorecard.pass_rate == 0.5


def test_the_pass_rate_is_absent_rather_than_zero_when_nothing_decidable_ran() -> None:
    """Inspect AI's refusal to emit a number when filtering leaves it undefined."""
    scorecard = card(score("unverifiable", reason="eval_not_applicable"))

    assert scorecard.pass_rate is None


def test_a_run_with_no_scores_has_five_zero_counts_and_no_rate() -> None:
    scorecard = aggregate("20260922T101500Z-k7pq", NOT_CHECKED, 1, [("a-scenario", 1, [])])

    assert set(scorecard.counts.values()) == {0}
    assert scorecard.pass_rate is None


def test_the_scorecard_lists_each_trial_with_its_scores() -> None:
    scorecard = aggregate(
        "20260922T101500Z-k7pq",
        NOT_CHECKED,
        1,
        [
            ("first", 1, [score("pass")]),
            ("second", 1, [score("incomplete", reason="cancelled")]),
        ],
    )

    assert [line.id for line in scorecard.scenarios] == ["first", "second"]
    assert scorecard.scenarios[1].scores[0].reason == "cancelled"


# --- negation (D25) ---


def test_negate_swaps_pass_and_fail() -> None:
    assert negate("pass") == "fail"
    assert negate("fail") == "pass"


@pytest.mark.parametrize("verdict", ["incomplete", "unverifiable", "invalid"])
def test_a_negated_eval_never_turns_an_abnormal_verdict_into_a_pass(verdict: Verdict) -> None:
    """Asking the opposite question of a judgement nobody could make decides nothing."""
    assert negate(verdict) == verdict
    assert negate(verdict) != "pass"


# --- exit codes (D32) ---


def test_a_run_where_every_score_passed_exits_0() -> None:
    assert exit_code(card(score("pass"), score("pass"))) == 0


def test_a_run_that_asked_no_question_exits_0() -> None:
    assert exit_code(aggregate("r", NOT_CHECKED, 1, [("a-scenario", 1, [])])) == 0


def test_any_fail_exits_1() -> None:
    assert exit_code(card(score("pass"), score("fail"))) == 1


def test_a_fail_outranks_an_abnormal_verdict() -> None:
    assert (
        exit_code(
            card(score("fail"), score("invalid", fault_source="judge", fault_direction="none"))
        )
        == 1
    )


@pytest.mark.parametrize(
    "abnormal",
    [
        {"verdict": "incomplete", "reason": "target_error"},
        {"verdict": "unverifiable", "reason": "eval_not_applicable"},
        {"verdict": "invalid", "fault_source": "agentdiag", "fault_direction": "none"},
    ],
)
def test_no_fail_but_an_abnormal_verdict_exits_2(abnormal: dict) -> None:
    """A transport blip is a re-run signal, not a regression (D32)."""
    verdict = abnormal.pop("verdict")
    assert exit_code(card(score("pass"), score(verdict, **abnormal))) == 2


# --- the summary (ADR-0005 §8) ---


def test_the_summary_never_shows_the_pass_rate_without_the_counts_beside_it() -> None:
    summary = render_summary(
        card(
            score("pass"),
            score("invalid", fault_source="agentdiag", fault_direction="none"),
        )
    )

    rate_lines = [line for line in summary.splitlines() if "pass rate" in line]
    assert len(rate_lines) == 1
    assert "invalid" in rate_lines[0]
    assert "sync not_checked (no_fingerprint)" in rate_lines[0]


def test_the_summary_names_each_scenario_and_indents_its_scores() -> None:
    summary = render_summary(card(score("unverifiable", reason="eval_not_applicable")))

    lines = summary.splitlines()
    assert lines[0] == "a-scenario  trial 1  excluded from pass^k"
    assert lines[1] == "  prompt_adherence  unverifiable  eval_not_applicable"


def test_the_summary_says_the_rate_is_absent_rather_than_printing_zero() -> None:
    summary = render_summary(card(score("unverifiable", reason="eval_not_applicable")))

    assert "pass rate n/a (0 of 0)" in summary


# --- counts are closed on both sides (D2) ---


def test_a_scorecard_whose_counts_hold_a_sixth_key_is_rejected() -> None:
    """A Verdict outside the closed set cannot be counted, let alone reported."""
    with pytest.raises(ValidationError):
        Scorecard(
            run_id="r",
            sync=NOT_CHECKED,
            trials=1,
            counts={
                "pass": 0,
                "fail": 0,
                "incomplete": 0,
                "unverifiable": 0,
                "invalid": 0,
                "bogus": 7,
            },
        )


def test_a_scorecard_that_leaves_a_verdict_out_of_its_counts_is_rejected() -> None:
    """Omitting `invalid` would read as a Run where nothing went wrong (ADR-0005 §8)."""
    with pytest.raises(ValidationError) as raised:
        Scorecard(
            run_id="r",
            sync=NOT_CHECKED,
            trials=1,
            counts={"pass": 1, "fail": 0, "incomplete": 0, "unverifiable": 0},
        )

    assert "invalid" in str(raised.value)


# --- what did not run (D31, ADR-0005 §3, §8) ---


def test_aggregate_carries_every_not_run_scenario_onto_the_scorecard() -> None:
    scorecard = aggregate(
        "r",
        NOT_CHECKED,
        1,
        [("ran", 1, [score("pass")])],
        [NotRun(scenario="skipped", reason="not_selected")],
    )

    assert [entry.scenario for entry in scorecard.not_run] == ["skipped"]
    assert scorecard.not_run[0].reason == "not_selected"


def test_the_summary_names_each_not_run_scenario_before_the_counts() -> None:
    summary = render_summary(
        aggregate(
            "r",
            NOT_CHECKED,
            1,
            [("ran", 1, [score("pass")])],
            [NotRun(scenario="skipped", reason="cancelled")],
        )
    )

    lines = summary.splitlines()
    counts = next(index for index, line in enumerate(lines) if "pass rate" in line)
    assert lines[counts - 1] == "skipped  not run  cancelled"
    assert "not run 1" in lines[counts]


def test_the_counts_line_says_none_did_not_run_when_every_scenario_ran() -> None:
    assert "not run 0" in render_summary(card(score("pass")))


# --- a Judge on the Target's own model (D23) ---


def test_a_score_whose_judge_shared_the_targets_model_flags_the_scorecard() -> None:
    scorecard = card(score("pass"), score("pass", shared_model=True))

    assert scorecard.shared_model is True
    counts = next(line for line in render_summary(scorecard).splitlines() if "pass rate" in line)
    assert counts.endswith("judge shares the Target's model (self-preference risk)")


def test_scores_that_do_not_know_or_did_not_share_leave_the_scorecard_unflagged() -> None:
    """None — no call was made, or it failed before a model answered — is not a claim of
    sharing, and must not raise the warning."""
    scorecard = card(score("pass", shared_model=False), score("pass", shared_model=None))

    assert scorecard.shared_model is False
    assert "self-preference" not in render_summary(scorecard)


def test_a_guardrail_score_is_named_by_its_rule_in_the_summary() -> None:
    scorecard = card(score("fail", eval="guardrails", eval_id="no-refund-timing"))

    assert "  guardrails[no-refund-timing]  fail" in render_summary(scorecard)


# --- Trials and pass^k (ticket 08, ADR-0005 §7) ---
#
# Every expected value below is worked by hand from tau-bench's estimator, pass^k =
# C(c, k) / C(n, k) for c passing Trials out of n decidable ones, never read off the code.


def trials_card(trials: int, *by_trial: tuple[str, int, list[Score]]) -> Scorecard:
    return aggregate("20260923T100600Z-tri3", NOT_CHECKED, trials, list(by_trial))


def test_pass_k_is_the_chance_that_k_trials_drawn_from_the_decidable_ones_all_pass() -> None:
    """Two of three Trials pass: pass^1 = 2/3, pass^2 = C(2,2)/C(3,2) = 1/3, pass^3 = 0."""
    scorecard = trials_card(
        3,
        ("a-scenario", 1, [score("pass")]),
        ("a-scenario", 2, [score("fail")]),
        ("a-scenario", 3, [score("pass")]),
    )

    (aggregate_,) = scorecard.scenario_aggregates
    assert (aggregate_.trials, aggregate_.decidable, aggregate_.passed) == (3, 3, 2)
    assert aggregate_.pass_k == pytest.approx({1: 2 / 3, 2: 1 / 3, 3: 0.0})


def test_a_trial_with_an_abnormal_score_is_neither_a_pass_nor_a_fail_and_is_excluded() -> None:
    """One Trial cancelled: pass^k is over the other two, and pass^3 cannot be estimated."""
    scorecard = trials_card(
        3,
        ("a-scenario", 1, [score("pass"), score("pass", eval="must_say_any")]),
        ("a-scenario", 2, [score("pass"), score("incomplete", reason="cancelled")]),
        ("a-scenario", 3, [score("pass"), score("pass", eval="must_say_any")]),
    )

    (aggregate_,) = scorecard.scenario_aggregates
    assert (aggregate_.trials, aggregate_.decidable, aggregate_.passed) == (3, 2, 2)
    assert aggregate_.pass_k == {1: 1.0, 2: 1.0, 3: None}
    assert scorecard.excluded_trials == 1


def test_the_runs_pass_k_is_the_mean_over_scenarios_with_enough_decidable_trials() -> None:
    """A: 2 of 3 pass (2/3, 1/3, 0). B: 1 of 1 decidable passes (1, n/a, n/a).
    Run: pass^1 = (2/3 + 1) / 2 = 5/6; pass^2 = 1/3 and pass^3 = 0, from A alone."""
    scorecard = trials_card(
        3,
        ("a", 1, [score("pass")]),
        ("b", 1, [score("pass")]),
        ("a", 2, [score("fail")]),
        ("b", 2, [score("unverifiable", reason="evidence_missing")]),
        ("a", 3, [score("pass")]),
        ("b", 3, [score("invalid", fault_source="judge", fault_direction="none")]),
    )

    assert scorecard.pass_k == pytest.approx({1: 5 / 6, 2: 1 / 3, 3: 0.0})
    assert scorecard.excluded_trials == 2


def test_an_eval_aggregate_keeps_the_abnormal_counts_beside_its_mean() -> None:
    scorecard = trials_card(
        3,
        ("a", 1, [score("pass")]),
        ("a", 2, [score("fail")]),
        ("a", 3, [score("incomplete", reason="target_error")]),
    )

    (eval_,) = scorecard.scenario_aggregates[0].evals
    assert (eval_.decidable, eval_.passed, eval_.mean) == (2, 1, 0.5)
    assert eval_.abnormal == {"incomplete": 1, "unverifiable": 0, "invalid": 0}


def test_a_metric_eval_aggregate_carries_its_mean_value_and_direction() -> None:
    latency = {"eval": "tool_latency", "direction": "minimize"}
    scorecard = trials_card(
        2,
        ("a", 1, [score("pass", value=40.0, **latency)]),
        ("a", 2, [score("fail", value=1200.0, **latency)]),
    )

    (eval_,) = scorecard.scenario_aggregates[0].evals
    assert (eval_.value_mean, eval_.direction) == (620.0, "minimize")


def test_nothing_decidable_leaves_pass_k_absent_rather_than_zero() -> None:
    scorecard = trials_card(1, ("a", 1, [score("unverifiable", reason="eval_not_applicable")]))

    assert scorecard.pass_k == {1: None}
    assert scorecard.scenario_aggregates[0].evals[0].mean is None


def test_the_summary_prints_one_line_per_scenario_before_the_counts() -> None:
    summary = render_summary(
        trials_card(
            2,
            ("a", 1, [score("pass")]),
            ("a", 2, [score("fail")]),
        )
    )

    lines = summary.splitlines()
    counts = next(index for index, line in enumerate(lines) if "pass rate" in line)
    assert lines[counts - 1] == "a  trials 2  passed 1 of 2 decidable  pass^1 0.50  pass^2 0.00"


def test_pass_k_is_never_printed_without_the_trial_count_the_excluded_and_the_sampling() -> None:
    summary = render_summary(
        trials_card(
            2,
            ("a", 1, [score("pass")]),
            ("a", 2, [score("incomplete", reason="cancelled")]),
        )
    )

    lines = summary.splitlines()
    assert lines[-1] == ("pass^1 1.00  pass^2 n/a  trials 2  excluded 1  sampling not configured")
    assert "a  trial 2  excluded from pass^k" in lines


# --- decision 37's fallback is counted (ticket 21, decision 39) ---


def test_the_scorecard_counts_every_cite_the_scores_read_as_a_span_id() -> None:
    scorecard = card(
        score("pass", cites_read=["[llm_call-1]", "llm_call-2 assistant text: done"]),
        score("fail"),
        score("pass", cites_read=["[retrieval-1]"]),
    )

    assert scorecard.cites_read == 3


def test_a_scorecard_whose_scores_read_no_cite_counts_zero_and_still_writes_it() -> None:
    scorecard = card(score("pass"), score("fail"))

    assert scorecard.cites_read == 0
    assert scorecard.model_dump(mode="json")["cites_read"] == 0


# --- the Simulated User's sampling beside pass^k (ticket 06, amended decision 53) ---


@pytest.mark.parametrize(
    ("declared", "outcomes", "said"),
    [
        (None, [], "sampling not configured"),
        ({}, [{}], "sampling none declared"),
        ({"temperature": 0.7}, [{"temperature": "accepted"}], "sampling temperature 0.7 accepted"),
        (
            {"temperature": 0.7},
            [{"temperature": "not_supported"}, {}],
            "sampling temperature 0.7 not_supported",
        ),
        ({"temperature": 0.7}, [], "sampling temperature 0.7 not sent"),
    ],
    ids=["no Simulated User", "nothing declared", "accepted", "refused", "never asked"],
)
def test_the_pass_k_line_says_what_was_declared_and_what_the_api_answered(
    declared: dict[str, float] | None, outcomes: list[dict[str, Any]], said: str
) -> None:
    card = aggregate(
        "r",
        SyncSection(status="not_checked", reason="no_fingerprint"),
        1,
        [("a", 1, [score("pass")])],
        simulated_user_sampling=merged_sampling(declared, outcomes),
    )

    assert render_summary(card).splitlines()[-1].endswith(f"  {said}")


def test_a_refusal_in_any_call_is_the_runs_sampling_outcome() -> None:
    assert merged_sampling(
        {"temperature": 0.7},
        [{"temperature": "accepted"}, {"temperature": "not_supported"}, {}],
    ) == {"temperature": "0.7 not_supported"}
