"""Seam 1: `agentdiag change` over a copy of the toy with the committed Run fixtures under
its `runs/` (ticket 25, phase-7 decisions 2, 3, 4, 9).

A record opens from a fixture Trial's Diagnosis or from a complaint, is proposed, states its
expected effect, and closes `verified` or `refuted` only through a `compare` of committed
Run fixtures: `20260923T100200Z-prmt` (a Manifest prompt change whose Target says `refund`
about a shipped order, failing `must_not_say`) and `20260923T100000Z-base` (the same four
Scenarios, where it does not). With `prmt` as the pre-change Run and `base` as the post-change
one the shipped-order Scenario improves and the greeting does not move: verified; the other
way round it regresses: refuted. An expectation stated after `base` started is refused for
order, and the comparison without `--expect manifest.prompts` for its undeclared variation.

The Runs were made on 2026-09-23, so an expectation stated before them is written through
the command function with a fixed clock; a record is moved to `pushed` by writing its push
event directly, since no push exists before ticket 27. The `local` push event is made for
real, by a re-syncing replay Run of the help desk after its rule 3 changed on the platform
and in the local copy (`diverged`, `tests/test_drop_on_unknown_target.py`'s edit); a re-sync
of the toy after an edit on the deployed side alone (`deployed_ahead`,
`tests/test_run_sync.py`'s edit) moves no record.

A comparison in which the post-change Scores of a Scenario the expectation names, should-move
or must-not-move, decide nothing (ticket 40, phase-8 decisions 14 and 15, amended) is made
from a copy of `base` whose Scores for that Scenario are rewritten `invalid` and
re-aggregated, as a replay reads after a prompt change: no committed Run fixture holds a
Scenario that decides nothing, and the rescore fixture `resc` decides its Scenarios, so it
closes as a valid comparison.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentdiag.change.checks import check_records
from agentdiag.change.command import expect_change, open_record
from agentdiag.change.record import (
    Observation,
    PushEvent,
    Verification,
    find_record,
    load_record,
    write_record,
)
from agentdiag.cli import app
from agentdiag.eval.score import Score, ScoresFile
from agentdiag.examples.toy import SYSTEM_PROMPT
from agentdiag.run.manifest import Manifest, Redaction
from agentdiag.run.scorecard import Scorecard, aggregate
from agentdiag.sync.fingerprint import Fingerprint
from agentdiag.types import Verdict, VerificationResult
from agentdiag.workspace import TargetPaths, Workspace
from tests.change_fixtures import toy_with_records

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
RUNS = REPO / "tests" / "fixtures" / "runs"
RECORDING = REPO / "tests" / "fixtures" / "recordings" / "toy-orders.jsonl"
SLUG = "toy-order-desk"

BASE = "20260923T100000Z-base"
PRMT = "20260923T100200Z-prmt"
SHIPPED = "where-is-shipped-order"
GREETING = "greeting-calls-no-tool"
DELIVERED = "delivered-order-cannot-be-cancelled"
BEFORE_THE_RUNS = "2026-09-23T09:00:00Z"
AFTER_BASE_STARTED = "2026-09-23T10:01:00Z"

PROMPT_READ_AT = "agentdiag.examples.toy.target.SYSTEM_PROMPT"
EDITED_PROMPT = SYSTEM_PROMPT.replace("at most three sentences", "at most two sentences")

runner = CliRunner()


def toy(tmp_path: Path, *runs: str) -> Path:
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    for run in runs:
        shutil.copytree(RUNS / run, root / ".agentdiag" / "targets" / SLUG / "runs" / run)
    return root


def the_target(root: Path) -> TargetPaths:
    return Workspace.find(root).resolve(None)


def change(root: Path, *arguments: str) -> Any:
    return runner.invoke(app, ["change", *arguments, "--root", str(root)])


def opened_id(result: Any) -> str:
    assert result.exit_code == 0, result.output
    return str(result.stdout.split()[1])


def pushed_record(
    root: Path,
    *,
    trigger_run: str = PRMT,
    stated_at: str = BEFORE_THE_RUNS,
    push: PushEvent | None = None,
) -> str:
    """A record opened from `trigger_run`'s Diagnosis, proposed, its expectation stated at
    `stated_at`, and moved to `pushed` by a push event written directly: `push`, else a local
    one."""
    record_id = opened_id(
        change(
            root,
            "open",
            "--from",
            f"{trigger_run}/{DELIVERED}/1",
            "--layer",
            "rules",
            "--title",
            "Refund promised on a shipped order",
        )
    )
    assert change(root, "propose", record_id, "--section", "prompt.system").exit_code == 0
    target = the_target(root)
    stated = expect_change(
        target,
        record_id,
        should_move=[SHIPPED],
        must_not_move=[GREETING],
        now=lambda: stated_at,
    )
    assert stated.code == 0, stated.message
    _, record, body = find_record(target, record_id)
    event = push or PushEvent(
        kind="local", environment="local", at="2026-09-23T09:30:00Z", run=None
    )
    write_record(target, record.model_copy(update={"status": "pushed", "pushes": [event]}), body)
    return record_id


def record_of(root: Path, record_id: str) -> Any:
    return load_record(the_target(root).directory / "changes" / f"{record_id}.md")


# --- open, propose, expect ---


def test_open_from_a_trial_links_its_diagnosis_and_leaves_the_run_untouched(
    tmp_path: Path,
) -> None:
    root = toy(tmp_path, BASE)
    run_dir = the_target(root).runs / BASE
    before = {path: path.read_bytes() for path in run_dir.rglob("*") if path.is_file()}

    result = change(
        root, "open", "--from", f"{BASE}/{DELIVERED}/1", "--layer", "rules", "--title", "Goal"
    )

    record_id = opened_id(result)
    record = record_of(root, record_id)
    assert record.status == "open"
    assert record.layer == "rules"
    assert record.trigger.kind == "diagnosis"
    assert (record.trigger.run, record.trigger.scenario, record.trigger.trial) == (
        BASE,
        DELIVERED,
        1,
    )
    assert record.trigger.summary.startswith("The goal passed because the Target took the one")
    assert record.trigger.summary.endswith(".")
    assert record.diagnosis is not None
    assert record.diagnosis.text.startswith(record.trigger.summary)
    assert record.trigger.cites
    assert {path: path.read_bytes() for path in run_dir.rglob("*") if path.is_file()} == before


def test_open_from_a_trial_with_no_diagnosis_is_refused_naming_it(tmp_path: Path) -> None:
    root = toy(tmp_path, BASE)

    result = change(
        root, "open", "--from", f"{BASE}/{GREETING}/1", "--layer", "rules", "--title", "x"
    )

    assert result.exit_code == 3
    assert f"Trial 1 of Scenario {GREETING!r} in Run {BASE} has no Diagnosis" in result.output


def test_open_from_a_complaint_records_its_redacted_text_as_the_trigger(tmp_path: Path) -> None:
    root = toy(tmp_path)
    complaint = tmp_path / "complaint.md"
    complaint.write_text(
        "# Told my refund comes in 5 days\n\nWrite to me at sam@example.net or 07700 900123.\n",
        encoding="utf-8",
    )

    result = change(
        root,
        "open",
        "--complaint",
        str(complaint),
        "--layer",
        "rules",
        "--title",
        "Refund timing promised",
        "--by",
        "support",
    )

    record_id = opened_id(result)
    assert record_id.endswith("-refund-timing-promised")
    path = the_target(root).directory / "changes" / f"{record_id}.md"
    _, record, body = find_record(the_target(root), path.stem)
    assert record.trigger.kind == "complaint"
    assert record.trigger.summary == "Told my refund comes in 5 days"
    assert record.opened_by == "support"
    assert (
        body
        == "## Trigger\n\n# Told my refund comes in 5 days\n\nWrite to me at [email] or [phone]."
    )
    assert "sam@example.net" not in path.read_text(encoding="utf-8")


def test_propose_records_the_change_set_and_expect_stamps_the_expectation(
    tmp_path: Path,
) -> None:
    root = toy(tmp_path, BASE)
    record_id = opened_id(
        change(root, "open", "--from", f"{BASE}/{DELIVERED}/1", "--layer", "rules", "--title", "x")
    )

    proposed = change(
        root, "propose", record_id, "--section", "prompt.system", "--file", "judge_notes.md"
    )
    expected = change(
        root,
        "expect",
        record_id,
        "--should-move",
        f"{SHIPPED},{DELIVERED}",
        "--must-not-move",
        GREETING,
    )

    assert proposed.exit_code == 0, proposed.output
    assert expected.exit_code == 0, expected.output
    record = record_of(root, record_id)
    assert record.status == "proposed"
    assert record.change.sections == ["prompt.system"]
    assert record.change.files == ["judge_notes.md"]
    assert record.expected.should_move == [SHIPPED, DELIVERED]
    assert record.expected.must_not_move == [GREETING]
    assert record.expected.stated_at > record.opened_at[:10]


def test_expect_refuses_a_scenario_the_runnable_suites_do_not_hold(tmp_path: Path) -> None:
    root = toy(tmp_path, BASE)
    record_id = opened_id(
        change(root, "open", "--from", f"{BASE}/{DELIVERED}/1", "--layer", "rules", "--title", "x")
    )

    result = change(root, "expect", record_id, "--should-move", "no-such-scenario")

    assert result.exit_code == 3
    assert "no-such-scenario" in result.output


def test_a_transition_the_lifecycle_does_not_allow_is_refused_naming_what_it_allows(
    tmp_path: Path,
) -> None:
    root = toy(tmp_path, BASE, PRMT)
    record_id = opened_id(
        change(root, "open", "--from", f"{BASE}/{DELIVERED}/1", "--layer", "rules", "--title", "x")
    )

    result = change(root, "close", record_id, "--verified", "--run", BASE)

    assert result.exit_code == 3
    assert (
        f"Change record {record_id} is open: it cannot move to verified; from open it can "
        "move to proposed, wontfix, superseded"
    ) in result.output


# --- the four gate cases (decision 3) ---


def test_a_compare_showing_the_expected_movement_closes_the_record_verified(
    tmp_path: Path,
) -> None:
    root = toy(tmp_path, BASE, PRMT)
    record_id = pushed_record(root)

    result = change(
        root, "close", record_id, "--verified", "--run", BASE, "--expect", "manifest.prompts"
    )

    assert result.exit_code == 0, result.output
    assert f"{record_id} verified: {PRMT} -> {BASE}" in result.stdout
    record = record_of(root, record_id)
    assert record.status == "verified"
    assert record.closed_at == record.verification.compared_at
    assert record.verification.baseline == PRMT  # the trigger's Run, a driven one
    assert record.verification.run == BASE
    assert record.verification.expect == ["manifest.prompts", "fingerprint", "sync"]
    assert record.verification.environment == "local"  # the verifying Run's (ticket 38)
    assert record.verification.environment_mismatch is False
    assert "improvement 1" in record.verification.summary
    assert {observation.scenario for observation in record.observed} == {SHIPPED}
    assert ("must_not_say", "pass") in {
        (observation.eval, observation.verdict) for observation in record.observed
    }


def test_a_compare_showing_it_did_not_move_closes_the_record_refuted_and_not_verified(
    tmp_path: Path,
) -> None:
    root = toy(tmp_path, BASE, PRMT)
    record_id = pushed_record(root, trigger_run=BASE)
    arguments = ("--run", PRMT, "--expect", "manifest.prompts")

    as_verified = change(root, "close", record_id, "--verified", *arguments)
    as_refuted = change(root, "close", record_id, "--refuted", *arguments)

    assert as_verified.exit_code == 3
    assert f"{SHIPPED} regressed" in as_verified.output
    assert as_refuted.exit_code == 0, as_refuted.output
    record = record_of(root, record_id)
    assert record.status == "refuted"
    assert record.verification.result == "refuted"
    assert (record.verification.baseline, record.verification.run) == (BASE, PRMT)
    assert ("must_not_say", "fail") in {(o.eval, o.verdict) for o in record.observed}


# --- a comparison that decides nothing (ticket 40, phase-8 decisions 14 and 15) ---

RESC = "20260923T100500Z-resc"


def judged_invalid(root: Path, run: str, scenario: str) -> None:
    """Every Score of `scenario` in the copied `run` made `invalid` and its Scorecard
    re-aggregated, as a replayed Run reads when its recording no longer matches the edited
    prompt: the Judge's request body is not in the recording, so no judged Score decides.
    Each is written as a Judge fault writes one (no evidence, no cites, no value); it stands
    in for a Judge-fault and an `agentdiag_error` `invalid` alike, since the gate reads only
    the Verdict counts."""
    directory = the_target(root).runs / run
    card = Scorecard.model_validate_json((directory / "scorecard.json").read_text("utf-8"))
    scores_by_trial: list[tuple[str, int, list[Score]]] = []
    for line in card.scenarios:
        path = directory / "trials" / line.id / str(line.trial) / "scores.json"
        scores = ScoresFile.model_validate_json(path.read_text("utf-8"))
        if line.id == scenario:
            scores = scores.model_copy(
                update={
                    "scores": [
                        Score.model_validate(
                            {
                                **score.model_dump(),
                                "verdict": "invalid",
                                "reason": None,
                                "fault_source": "judge",
                                "fault_direction": "none",
                                "evidence": [],
                                "cites_read": [],
                                "value": None,
                                "rationale": "the recording holds no exchange for this request",
                            }
                        )
                        for score in scores.scores
                    ]
                }
            )
            path.write_text(scores.model_dump_json(indent=2), encoding="utf-8")
        scores_by_trial.append((line.id, line.trial, list(scores.scores)))
    rebuilt = aggregate(
        card.run_id,
        card.sync,
        card.trials,
        scores_by_trial,
        card.not_run,
        suites={a.id: a.suite for a in card.scenario_aggregates if a.suite is not None},
        simulated_user_sampling=card.simulated_user_sampling,
    )
    (directory / "scorecard.json").write_text(rebuilt.model_dump_json(indent=2), "utf-8")


def evals_of(run: str, scenario: str) -> list[str]:
    path = RUNS / run / "trials" / scenario / "1" / "scores.json"
    return [score.name for score in ScoresFile.model_validate_json(path.read_text()).scores]


def test_a_should_move_scenario_whose_scores_decide_nothing_refuses_both_closes(
    tmp_path: Path,
) -> None:
    root = toy(tmp_path, BASE, PRMT)
    judged_invalid(root, BASE, SHIPPED)
    record_id = pushed_record(root)
    before = (the_target(root).directory / "changes" / f"{record_id}.md").read_bytes()
    arguments = ("--run", BASE, "--expect", "manifest.prompts")

    as_verified = change(root, "close", record_id, "--verified", *arguments)
    as_refuted = change(root, "close", record_id, "--refuted", *arguments)

    phrases = "; ".join(
        f"{SHIPPED}: {name} is invalid 1 in {BASE}" for name in evals_of(BASE, SHIPPED)
    )
    for result in (as_verified, as_refuted):
        assert result.exit_code == 3
        assert (
            f"error: the comparison of {PRMT} and {BASE} decides nothing for {SHIPPED}: "
            f"{phrases}; a Run whose Scores decide (live, or freshly recorded against the "
            "pushed prompt) would make it valid"
        ) in " ".join(result.output.split())
    assert (the_target(root).directory / "changes" / f"{record_id}.md").read_bytes() == before


def test_the_rescore_fixture_decides_its_scenarios_and_closes_as_before(tmp_path: Path) -> None:
    """The committed rescore Run holds `pass` and `fail` Scores for the should-move
    Scenario: a valid comparison, which the gate still closes by its movement."""
    root = toy(tmp_path, BASE, PRMT, RESC)
    record_id = pushed_record(root)
    arguments = ("--run", RESC, "--expect", "manifest.prompts")

    as_verified = change(root, "close", record_id, "--verified", *arguments)

    assert "decides nothing" not in as_verified.output
    assert as_verified.exit_code == 0, as_verified.output
    assert record_of(root, record_id).status == "verified"


def test_a_must_not_move_scenario_that_decides_nothing_refuses_both_closes(
    tmp_path: Path,
) -> None:
    """Amended decision 14: `_unmet` would read it as moved and leave `--refuted` the only
    close, the false refutation the gate exists to stop."""
    root = toy(tmp_path, BASE, PRMT)
    judged_invalid(root, BASE, GREETING)
    record_id = pushed_record(root)
    before = (the_target(root).directory / "changes" / f"{record_id}.md").read_bytes()
    arguments = ("--run", BASE, "--expect", "manifest.prompts")

    as_verified = change(root, "close", record_id, "--verified", *arguments)
    as_refuted = change(root, "close", record_id, "--refuted", *arguments)

    phrases = "; ".join(
        f"{GREETING}: {name} is invalid 1 in {BASE} (must not move)"
        for name in evals_of(BASE, GREETING)
    )
    for result in (as_verified, as_refuted):
        assert result.exit_code == 3
        assert result.stdout == ""
        assert result.stderr.startswith("error: ")
        assert result.stderr.count("\n") == 1  # the error, and nothing beside it
        assert (
            f"error: the comparison of {PRMT} and {BASE} decides nothing for {GREETING}: "
            f"{phrases}; a Run whose Scores decide"
        ) in " ".join(result.stderr.split())
    assert (the_target(root).directory / "changes" / f"{record_id}.md").read_bytes() == before


def closed_on(
    root: Path,
    verdicts: list[Verdict],
    *,
    result: VerificationResult = "refuted",
    scenario: str = SHIPPED,
) -> Path:
    """A record closed `result` whose Observations of `scenario` (its should-move one by
    default) carry `verdicts`, written by hand as a hand edit or an older gate would have
    left it; the should-move Scenario is given one `pass` when `scenario` is another."""
    target = the_target(root)
    record_id = pushed_record(root)
    _, record, body = find_record(target, record_id)
    verification = Verification(
        baseline=PRMT,
        run=BASE,
        compared_at="2026-09-23T10:05:00Z",
        expect=["manifest.prompts"],
        result=result,
        summary="undecided 9",
    )
    observed = [
        Observation(
            run=BASE,
            scenario=scenario,
            trial=1,
            eval=f"eval_{index}",
            verdict=verdict,
            rationale="read",
        )
        for index, verdict in enumerate(verdicts)
    ]
    if scenario != SHIPPED:
        observed.append(
            Observation(
                run=BASE, scenario=SHIPPED, trial=1, eval="goal", verdict="pass", rationale="read"
            )
        )
    closed = record.model_copy(
        update={
            "status": result,
            "closed_at": "2026-09-23T10:05:00Z",
            "verification": verification,
            "observed": observed,
        }
    )
    return write_record(target, closed, body)


def test_validate_warns_on_a_record_closed_on_scores_that_decide_nothing(tmp_path: Path) -> None:
    root = toy(tmp_path, BASE, PRMT)
    path = closed_on(root, ["invalid", "invalid", "unverifiable"])

    result = runner.invoke(app, ["validate", "--root", str(root)])

    assert result.exit_code == 0, result.output
    assert (
        f"warning: {path}: observed: closed refuted on Scores that decide nothing for "
        f"{SHIPPED} (pass 0  fail 0  incomplete 0  unverifiable 1  invalid 2)"
    ) in result.stdout
    assert "0 errors, 1 warning" in result.stdout


def test_validate_warns_on_a_must_not_move_scenario_whose_observations_decide_nothing(
    tmp_path: Path,
) -> None:
    root = toy(tmp_path, BASE, PRMT)
    path = closed_on(root, ["invalid"], scenario=GREETING)

    result = runner.invoke(app, ["validate", "--root", str(root)])

    assert result.exit_code == 0, result.output
    warnings = [line for line in result.stdout.splitlines() if "decide nothing" in line]
    assert warnings == [
        f"warning: {path}: observed: closed refuted on Scores that decide nothing for "
        f"{GREETING} (pass 0  fail 0  incomplete 0  unverifiable 0  invalid 1)"
    ]


def test_validate_is_quiet_on_a_record_closed_on_one_decided_score(tmp_path: Path) -> None:
    root = toy(tmp_path, BASE, PRMT)
    closed_on(root, ["invalid", "fail"], result="refuted")

    result = runner.invoke(app, ["validate", "--root", str(root)])

    assert result.exit_code == 0, result.output
    assert "decide nothing" not in result.stdout


def test_the_committed_change_records_validate_without_a_decides_nothing_warning(
    tmp_path: Path,
) -> None:
    target = toy_with_records(tmp_path)

    report = check_records(target)

    assert not [message for _, message in report.warnings if "decide nothing" in message]


# --- the environment rule (ticket 38, phase-8 decision 12) ---

PUSHED_TO_STAGING = PushEvent(
    kind="connector",
    environment="staging",
    at="2026-09-23T09:30:00Z",
    push_record="pushes/20260923T093000Z-staging.json",
)


def on_environment(root: Path, environment: str, *runs: str) -> None:
    """Rewrite each committed Run's `run.json.adapter.environment`, as a Run made with
    `--env <environment>` would have recorded it."""
    for run in runs:
        path = the_target(root).runs / run / "run.json"
        recorded = json.loads(path.read_text(encoding="utf-8"))
        recorded["adapter"]["environment"] = environment
        path.write_text(json.dumps(recorded, indent=2), encoding="utf-8")


def test_a_verifying_run_on_the_environment_the_fix_was_pushed_to_closes_it(
    tmp_path: Path,
) -> None:
    root = toy(tmp_path, BASE, PRMT)
    on_environment(root, "staging", BASE, PRMT)
    record_id = pushed_record(root, push=PUSHED_TO_STAGING)

    result = change(
        root, "close", record_id, "--verified", "--run", BASE, "--expect", "manifest.prompts"
    )

    assert result.exit_code == 0, result.output
    assert "environment_mismatch" not in result.output
    record = record_of(root, record_id)
    assert record.status == "verified"
    assert record.verification.environment == "staging"
    assert record.verification.environment_mismatch is False


def test_a_verifying_run_on_another_environment_is_refused_naming_both(tmp_path: Path) -> None:
    root = toy(tmp_path, BASE, PRMT)
    record_id = pushed_record(root, push=PUSHED_TO_STAGING)
    before = (the_target(root).directory / "changes" / f"{record_id}.md").read_bytes()

    arguments = ("--run", BASE, "--expect", "manifest.prompts")
    as_verified = change(root, "close", record_id, "--verified", *arguments)
    as_refuted = change(root, "close", record_id, "--refuted", *arguments)

    for result in (as_verified, as_refuted):
        assert result.exit_code == 3
        assert (
            f"the record was pushed to 'staging' and Run {BASE} ran on 'local'; verify on the "
            "environment the fix was pushed to, or pass --any-env"
        ) in result.output
    assert (the_target(root).directory / "changes" / f"{record_id}.md").read_bytes() == before


def test_any_env_closes_it_and_records_the_mismatch(tmp_path: Path) -> None:
    root = toy(tmp_path, BASE, PRMT)
    record_id = pushed_record(root, push=PUSHED_TO_STAGING)

    result = change(
        root,
        "close",
        record_id,
        "--verified",
        "--run",
        BASE,
        "--expect",
        "manifest.prompts",
        "--any-env",
    )

    assert result.exit_code == 0, result.output
    assert f"Run {BASE} ran on 'local', not 'staging' where the record was pushed" in result.stdout
    record = record_of(root, record_id)
    assert record.status == "verified"
    assert record.verification.environment == "local"
    assert record.verification.environment_mismatch is True
    text = (the_target(root).directory / "changes" / f"{record_id}.md").read_text("utf-8")
    assert "environment_mismatch: true" in text


def test_only_the_last_connector_push_names_the_environment(tmp_path: Path) -> None:
    """A later `local` push event (a re-syncing Run) and an imported push naming `unknown`
    ask nothing of the verifying Run's environment; the last Connector push does."""
    root = toy(tmp_path, BASE, PRMT)
    record_id = pushed_record(root, push=PUSHED_TO_STAGING)
    target = the_target(root)
    _, record, body = find_record(target, record_id)
    later = PushEvent(kind="local", environment="local", at="2026-09-23T09:40:00Z", run=None)
    write_record(target, record.model_copy(update={"pushes": [*record.pushes, later]}), body)
    arguments = ("--verified", "--run", BASE, "--expect", "manifest.prompts")

    still_staging = change(root, "close", record_id, *arguments)
    _, record, body = find_record(target, record_id)
    unknown = PUSHED_TO_STAGING.model_copy(update={"environment": "unknown"})
    write_record(target, record.model_copy(update={"pushes": [unknown]}), body)
    imported = change(root, "close", record_id, *arguments)

    assert still_staging.exit_code == 3
    assert "the record was pushed to 'staging'" in still_staging.output
    assert imported.exit_code == 0, imported.output
    assert record_of(root, record_id).verification.environment == "local"


def test_any_env_is_refused_on_a_close_that_names_no_run(tmp_path: Path) -> None:
    root = toy(tmp_path, BASE, PRMT)
    record_id = pushed_record(root)

    result = change(root, "close", record_id, "--wontfix", "--why", "not now", "--any-env")

    assert result.exit_code == 3
    assert "--any-env applies to --verified and --refuted" in result.output
    assert record_of(root, record_id).status == "pushed"


def test_an_expectation_stated_after_the_verifying_run_started_is_refused(tmp_path: Path) -> None:
    root = toy(tmp_path, BASE, PRMT)
    record_id = pushed_record(root, stated_at=AFTER_BASE_STARTED)

    result = change(
        root, "close", record_id, "--verified", "--run", BASE, "--expect", "manifest.prompts"
    )

    assert result.exit_code == 3
    assert "the expectation was recorded after the verifying Run started" in result.output
    assert record_of(root, record_id).status == "pushed"


def test_an_undeclared_variation_refuses_the_close_naming_the_paths(tmp_path: Path) -> None:
    root = toy(tmp_path, BASE, PRMT)
    record_id = pushed_record(root)

    result = change(root, "close", record_id, "--verified", "--run", BASE)

    assert result.exit_code == 3
    assert "undeclared variation" in result.output
    assert "manifest.prompts.rules.path" in result.output
    assert record_of(root, record_id).status == "pushed"


def test_a_complaint_record_needs_a_baseline_to_close(tmp_path: Path) -> None:
    root = toy(tmp_path, BASE, PRMT)
    record_id = pushed_record(root)
    target = the_target(root)
    path = target.directory / "changes" / f"{record_id}.md"
    _, record, body = find_record(the_target(root), path.stem)
    trigger = record.trigger.model_copy(
        update={"kind": "complaint", "run": None, "scenario": None, "trial": None}
    )
    write_record(target, record.model_copy(update={"trigger": trigger, "diagnosis": None}), body)

    refused = change(root, "close", record_id, "--verified", "--run", BASE)
    closed = change(
        root,
        "close",
        record_id,
        "--verified",
        "--run",
        BASE,
        "--baseline",
        PRMT,
        "--expect",
        "manifest.prompts",
    )

    assert refused.exit_code == 3
    assert "--baseline" in refused.output
    assert closed.exit_code == 0, closed.output


# --- wontfix, superseded, show, list ---


def test_wontfix_needs_a_reason_and_superseded_needs_a_record_that_exists(tmp_path: Path) -> None:
    root = toy(tmp_path, BASE)
    opened = [
        opened_id(
            change(
                root,
                "open",
                "--from",
                f"{BASE}/{DELIVERED}/1",
                "--layer",
                "rules",
                "--title",
                title,
            )
        )
        for title in ("first", "second", "third")
    ]

    assert change(root, "close", opened[0], "--wontfix").exit_code == 3
    assert change(root, "close", opened[0], "--wontfix", "--why", "by design").exit_code == 0
    assert change(root, "close", opened[1], "--superseded-by", "20200101-none").exit_code == 3
    assert change(root, "close", opened[1], "--superseded-by", opened[2]).exit_code == 0
    assert record_of(root, opened[0]).why == "by design"
    assert record_of(root, opened[1]).superseded_by == opened[2]
    assert record_of(root, opened[1]).status == "superseded"


def test_show_prints_the_story_and_list_filters_by_status(tmp_path: Path) -> None:
    root = toy(tmp_path, BASE, PRMT)
    record_id = pushed_record(root)
    other = opened_id(
        change(
            root, "open", "--from", f"{BASE}/{DELIVERED}/1", "--layer", "persona", "--title", "y"
        )
    )

    shown = change(root, "show", record_id)
    as_json = change(root, "show", record_id, "--json")
    listed = change(root, "list")
    pushed = change(root, "list", "--status", "pushed")

    assert shown.exit_code == 0, shown.output
    lines = shown.stdout.splitlines()
    assert lines[0] == f"Change record {record_id}: Refund promised on a shipped order"
    assert lines[1].startswith("pushed · Target toy-order-desk · opened ")
    assert [line for line in lines if line[:1].isdigit()] == [
        "1 What happened",
        "2 The problem",
        "3 The fix",
        "4 The expected effect",
        "5 What was observed · pending",
    ]
    assert f"  - Should move: {SHIPPED}." in lines
    assert f"    -> #run/{PRMT}/{DELIVERED}/1" in lines
    assert as_json.stdout == record_of(root, record_id).model_dump_json(indent=2) + "\n"
    assert listed.stdout.splitlines()[0].split() == ["id", "status", "opened", "layer", "title"]
    assert {line.split()[0] for line in listed.stdout.splitlines()[1:]} == {record_id, other}
    assert [line.split()[0] for line in pushed.stdout.splitlines()[1:]] == [record_id]
    assert change(root, "list", "--status", "done").exit_code == 3


PINNED_STORY = """\
Change record 20260923-a-pushed-record: Cancel called on a status question
pushed · Target toy-order-desk · opened 2026-09-23T10:00:30Z by support
file .agentdiag/targets/toy-order-desk/changes/20260923-a-pushed-record.md

1 What happened
  - Diagnosis of Trial status-question-is-not-a-cancel / 1 of Run 20260923T100000Z-base: \
The Target called cancel_order on a question about an order's status (llm_call-1).
    -> #run/20260923T100000Z-base/status-question-is-not-a-cancel/1
    cites turn-1, llm_call-1
  - Opened 2026-09-23T10:00:30Z by support.

2 The problem
  - The Target called cancel_order on a question about an order's status (llm_call-1), \
which Rule 2 forbids: the customer asked only where the order was (turn-1), and nothing \
asked for a cancel.
    -> #run/20260923T100000Z-base/status-question-is-not-a-cancel/1
    cites turn-1, llm_call-1
  - Layer: rules.

3 The fix
  - Section prompt.system.
  - File manifest.yaml.
    -> manifest.yaml
  - Proposed at commit 4711963aa1b2.
  - Pushed to local at 2026-09-23T10:01:30Z, Fingerprint 1f0e2d3c -> 2a1b3c4d: Push \
record pushes/20260923T100130Z-local.json.
    -> pushes/20260923T100130Z-local.json
  - Restore point restore-points/20260923T100130Z-local.json (kept where the push ran; \
gitignored).

4 The expected effect
  - Stated 2026-09-23T10:01:00Z.
  - Should move: status-question-is-not-a-cancel.
  - Must not move: greeting-calls-no-tool.

5 What was observed · pending
  - Not observed yet: `change close --verified` or `--refuted` fills this from the \
`compare` of a Run made after the push.
"""
"""`change show` over the pushed fixture record, byte for byte (ticket 28): the head, then
the five lanes, each item's link and cites on lines of their own."""


def test_show_prints_the_story_of_a_fixture_record_byte_for_byte(tmp_path: Path) -> None:
    target = toy_with_records(tmp_path)

    shown = change(target.root, "show", "20260923-a-pushed-record")

    assert shown.exit_code == 0, shown.output
    assert shown.stdout == PINNED_STORY


# --- the `local` push event (decision 4) ---

WORKSPACE = REPO / "examples" / "workspace"
HELPDESK_RECORDING = REPO / "tests" / "fixtures" / "recordings" / "helpdesk.jsonl"
HELPDESK_EDIT = ("one-line subject", "one-line summary")
HELPDESK_SCENARIO = "rules-rule-3-a-broken-app-is-reported-and-a-ticket-is-opened"


def help_desk_root(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    shutil.copytree(WORKSPACE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    return root


def help_desk_change(root: Path, *arguments: str) -> Any:
    return runner.invoke(app, ["change", *arguments, "--root", str(root), "--target", "help-desk"])


def replay_help_desk(root: Path, recording: Path) -> Path:
    runs = root / ".agentdiag" / "targets" / "help-desk" / "runs"
    before = set(runs.iterdir()) if runs.is_dir() else set()
    result = runner.invoke(
        app,
        [
            "run",
            "--root",
            str(root),
            "--target",
            "help-desk",
            "--scenario",
            HELPDESK_SCENARIO,
            "--replay",
            str(recording),
        ],
    )
    assert result.exit_code in (0, 1), result.output
    (new,) = set(runs.iterdir()) - before
    return new


def test_a_run_resyncing_onto_an_edited_file_moves_a_proposed_record_to_pushed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentdiag.examples.helpdesk import platform

    root = help_desk_root(tmp_path)
    replay_help_desk(root, HELPDESK_RECORDING)
    complaint = tmp_path / "complaint.md"
    complaint.write_text("# Tickets say subject, the form says summary\n", encoding="utf-8")
    opened = [
        opened_id(
            help_desk_change(
                root, "open", "--complaint", str(complaint), "--layer", "rules", "--title", title
            )
        )
        for title in ("moved", "untouched")
    ]
    for record_id, section in zip(opened, ("prompt.system#rules", "tool.open_ticket"), strict=True):
        proposed = help_desk_change(root, "propose", record_id, "--section", section)
        assert proposed.exit_code == 0, proposed.output
    target = Workspace.find(root).resolve("help-desk")
    edited = platform.SYSTEM_PROMPT.replace(*HELPDESK_EDIT)
    monkeypatch.setitem(platform.DEPLOYED["prompts"], "system", edited)
    (target.directory / "prompts" / "system.md").write_text(edited, encoding="utf-8")
    old, new = (json.dumps(text)[1:-1] for text in HELPDESK_EDIT)
    recording = tmp_path / "helpdesk-edited.jsonl"
    recording.write_text(
        HELPDESK_RECORDING.read_text(encoding="utf-8").replace(old, new), encoding="utf-8"
    )

    run_dir = replay_help_desk(root, recording)

    run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert [(s["id"], s["direction"]) for s in run_json["sync"]["sections"]] == [
        ("prompt.system#rules", "diverged")
    ]
    _, record, _ = find_record(target, opened[0])
    assert record.status == "pushed"
    (event,) = record.pushes
    assert (event.kind, event.run, event.environment) == ("local", run_dir.name, "local")
    assert event.at == run_json["created_at"]
    assert event.push_record is None
    assert event.fingerprint_before == run_json["sync"]["resynced_from"]
    assert event.fingerprint_after == Fingerprint.model_validate(run_json["fingerprint"]).id
    _, untouched, _ = find_record(target, opened[1])
    assert (untouched.status, untouched.pushes) == ("proposed", [])


def test_a_resync_after_a_deployed_side_edit_alone_moves_no_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The toy's prompt changed where its Connector reads it, not in a local file: the Run
    re-syncs `deployed_ahead`, which no Change record made, so a record naming the section
    stays `proposed`."""
    root = toy(tmp_path, BASE)
    assert runner.invoke(app, ["sync", "--root", str(root)]).exit_code == 0
    record_id = opened_id(
        change(root, "open", "--from", f"{BASE}/{DELIVERED}/1", "--layer", "rules", "--title", "a")
    )
    assert change(root, "propose", record_id, "--section", "prompt.system").exit_code == 0
    monkeypatch.setattr(PROMPT_READ_AT, EDITED_PROMPT)

    runner.invoke(
        app,
        [
            "run",
            "--root",
            str(root),
            "--scenario",
            "cancel-processing-order",
            "--replay",
            str(RECORDING),
        ],
    )

    (run_dir,) = [path for path in the_target(root).runs.iterdir() if path.name != BASE]
    run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_json["sync"]["resynced_from"] is not None
    assert [s["direction"] for s in run_json["sync"]["sections"]] == ["deployed_ahead"]
    record = record_of(root, record_id)
    assert (record.status, record.pushes) == ("proposed", [])


# --- refusals at the edges ---


def test_a_layer_outside_the_closed_set_is_the_commands_refusal(tmp_path: Path) -> None:
    root = toy(tmp_path, BASE)

    exit_state = open_record(
        the_target(root), layer="prompt", title="x", from_trial=f"{BASE}/{DELIVERED}/1"
    )

    assert exit_state.code == 3
    assert exit_state.message.startswith("error: --layer 'prompt' is not one of persona, rules")
    assert not (the_target(root).directory / "changes").exists()


def test_a_run_id_that_is_a_path_is_refused_before_it_names_a_directory(tmp_path: Path) -> None:
    root = toy(tmp_path, BASE, PRMT)
    record_id = pushed_record(root)

    opened = change(
        root, "open", "--from", f"../x/{DELIVERED}/1", "--layer", "rules", "--title", "x"
    )
    as_run = change(root, "close", record_id, "--verified", "--run", "../x")
    as_baseline = change(
        root, "close", record_id, "--verified", "--run", BASE, "--baseline", "../x"
    )

    assert opened.exit_code == 3
    assert "'../x', which is not a Run id" in opened.output
    for result in (as_run, as_baseline):
        assert result.exit_code == 3
        assert "'../x' is not a Run id" in result.output
    assert record_of(root, record_id).status == "pushed"


def test_target_show_prints_the_open_records_and_not_the_closed_ones(tmp_path: Path) -> None:
    root = toy(tmp_path, BASE)
    kept = opened_id(
        change(
            root, "open", "--from", f"{BASE}/{DELIVERED}/1", "--layer", "rules", "--title", "kept"
        )
    )
    closed = opened_id(
        change(
            root, "open", "--from", f"{BASE}/{DELIVERED}/1", "--layer", "rules", "--title", "shut"
        )
    )
    assert change(root, "close", closed, "--wontfix", "--why", "by design").exit_code == 0

    shown = runner.invoke(app, ["target", "show", "--root", str(root)])
    as_json = runner.invoke(app, ["target", "show", "--root", str(root), "--json"])

    assert shown.exit_code == 0, shown.output
    lines = shown.stdout.splitlines()
    assert "change records  1 open" in lines
    assert f"                {kept} open: kept" in lines
    assert closed not in shown.stdout
    assert [record["id"] for record in json.loads(as_json.stdout)["change_records"]] == sorted(
        [kept, closed]
    )


# --- the names to redact are local (ADR-0015 §4) ---

INLINE_NAMES = '\nredaction:\n  names: ["Dana Whitfield"]\n'
MOVE_THE_NAMES = (
    "redaction.names: the names to redact are committed with the Manifest; move them to "
    "redaction.yaml beside it (gitignored by init) and drop the key, or point at that file "
    "with redaction: <path>"
)


def open_titled(root: Path, title: str) -> Any:
    return change(
        root, "open", "--from", f"{BASE}/{DELIVERED}/1", "--layer", "rules", "--title", title
    )


def test_a_name_the_redaction_file_lists_never_reaches_a_record_opened_from_a_trial(
    tmp_path: Path,
) -> None:
    root = toy(tmp_path, BASE)
    the_target(root).redaction.write_text('names: ["Dana Whitfield"]\n', encoding="utf-8")

    record_id = opened_id(open_titled(root, "Dana Whitfield was told it cannot be cancelled"))

    text = (the_target(root).changes / f"{record_id}.md").read_text(encoding="utf-8")
    assert "Dana" not in text and "Whitfield" not in text
    assert record_of(root, record_id).title == "[name] was told it cannot be cancelled"
    assert "dana" not in record_id


def test_names_still_listed_in_the_manifest_redact_and_validate_names_the_move(
    tmp_path: Path,
) -> None:
    root = toy(tmp_path, BASE)
    manifest = the_target(root).manifest
    manifest.write_text(manifest.read_text(encoding="utf-8") + INLINE_NAMES, encoding="utf-8")

    record_id = opened_id(open_titled(root, "Dana Whitfield was told it cannot be cancelled"))
    validated = runner.invoke(app, ["validate", "--root", str(root)])

    assert record_of(root, record_id).title == "[name] was told it cannot be cancelled"
    assert validated.exit_code == 3, validated.stdout
    assert f"error: {manifest}: {MOVE_THE_NAMES}" in validated.stdout.splitlines()


@pytest.mark.parametrize(
    ("written", "problem"),
    [
        (
            'names: ["Zebulon Quist"]\nemails: [zq@example.org]\n',
            "holds keys besides names: emails",
        ),
        ("names: Zebulon Quist\n", "gives names that is not a list of strings"),
        ("names: [Zebulon Quist\n", "is not valid YAML at line 2"),
        ("- Zebulon Quist\n", "is not a mapping; it holds the one key names (names: [...])"),
    ],
    ids=["extra-key", "names-not-a-list", "not-yaml", "not-a-mapping"],
)
def test_a_redaction_file_of_the_wrong_shape_refuses_a_change_command_and_fails_validate(
    written: str, problem: str, tmp_path: Path
) -> None:
    root = toy(tmp_path, BASE)
    redaction = the_target(root).redaction
    redaction.write_text(written, encoding="utf-8")

    opened = open_titled(root, "Dana Whitfield was told it cannot be cancelled")
    validated = runner.invoke(app, ["validate", "--root", str(root)])

    assert opened.exit_code == 3, opened.output
    assert opened.output == f"error: the redaction file {redaction} {problem}\n"
    assert not the_target(root).changes.exists() or not any(the_target(root).changes.iterdir())
    assert validated.exit_code == 3, validated.stdout
    (line,) = [line for line in validated.stdout.splitlines() if ": redaction: " in line]
    assert line.startswith(f"error: {the_target(root).manifest}: redaction: the redaction file ")
    assert line.endswith(f": redaction: the redaction file {redaction} {problem}")
    assert "Zebulon" not in validated.stdout and "zq@" not in validated.stdout


def test_the_redaction_file_and_names_still_listed_inline_are_both_redacted(
    tmp_path: Path,
) -> None:
    root = toy(tmp_path, BASE)
    the_target(root).redaction.write_text('names: ["Smith"]\n', encoding="utf-8")
    manifest = the_target(root).manifest
    manifest.write_text(manifest.read_text(encoding="utf-8") + INLINE_NAMES, encoding="utf-8")

    record_id = opened_id(open_titled(root, "Dana Whitfield and Smith were told no"))

    assert record_of(root, record_id).title == "[name] and [name] were told no"


def manifest_with(root: Path, text: str) -> None:
    manifest = the_target(root).manifest
    manifest.write_text(manifest.read_text(encoding="utf-8") + text, encoding="utf-8")


def validate_line(root: Path) -> tuple[int, str]:
    validated = runner.invoke(app, ["validate", "--root", str(root)])
    (line,) = [line for line in validated.stdout.splitlines() if ": redaction: " in line]
    return validated.exit_code, line


@pytest.mark.parametrize(
    ("pointer", "message"),
    [
        (
            "/abs/x.yaml",
            "the redaction file /abs/x.yaml is absolute; a Manifest pointer is relative to the "
            f"Target directory .agentdiag/targets/{SLUG}",
        ),
        (
            "../../../../x.yaml",
            "the redaction file ../../../../x.yaml leaves the Workspace root (it normalises to "
            "../x.yaml); move the file under the root and point at it relative to the Target "
            "directory",
        ),
    ],
    ids=["absolute", "leaves-the-root"],
)
def test_a_refused_redaction_pointer_refuses_change_open_with_validates_message(
    pointer: str, message: str, tmp_path: Path
) -> None:
    root = toy(tmp_path, BASE)
    (tmp_path / "x.yaml").write_text('names: ["Dana Whitfield"]\n', encoding="utf-8")
    manifest_with(root, f"\nredaction: {pointer}\n")

    opened = open_titled(root, "Dana Whitfield was told no")
    code, line = validate_line(root)

    assert opened.exit_code == 3, opened.output
    assert opened.output == f"error: {message}\n"
    assert code == 3 and line == f"error: {the_target(root).manifest}: redaction: {message}"


def test_a_redaction_pointer_to_nothing_fails_validate_and_refuses_change_open(
    tmp_path: Path,
) -> None:
    root = toy(tmp_path, BASE)
    manifest_with(root, "\nredaction: private/names.yaml\n")
    message = (
        "the redaction file private/names.yaml the Manifest points at does not exist as a "
        f"file under the Target directory {the_target(root).directory}"
    )

    opened = open_titled(root, "Dana Whitfield was told no")
    code, line = validate_line(root)

    assert opened.exit_code == 3 and opened.output == f"error: {message}\n"
    assert code == 3 and line == f"error: {the_target(root).manifest}: redaction: {message}"


@pytest.mark.parametrize("loads", [True, False], ids=["manifest-loads", "manifest-does-not-load"])
def test_a_redaction_pointer_elsewhere_under_the_root_is_honoured(
    loads: bool, tmp_path: Path
) -> None:
    root = toy(tmp_path, BASE)
    (root / "private").mkdir()
    (root / "private" / "names.yaml").write_text('names: ["Dana Whitfield"]\n', encoding="utf-8")
    manifest_with(root, "\nredaction: ../../../private/names.yaml\n")
    if not loads:
        manifest_with(root, "schema_version: [not, a, number]\n")
        assert runner.invoke(app, ["validate", "--root", str(root)]).exit_code == 3

    record_id = opened_id(
        change(
            root,
            "open",
            "--complaint",
            str(complaint_file(tmp_path)),
            "--layer",
            "rules",
            "--title",
            "Dana Whitfield was told no",
        )
    )

    assert record_of(root, record_id).title == "[name] was told no"


def complaint_file(tmp_path: Path) -> Path:
    path = tmp_path / "complaint.md"
    path.write_text("# Told no\n\nDana Whitfield asked twice.\n", encoding="utf-8")
    return path


def test_an_older_run_snapshot_carrying_names_inline_still_loads(tmp_path: Path) -> None:
    root = toy(tmp_path, BASE)
    run_json = the_target(root).runs / BASE / "run.json"
    snapshot = json.loads(run_json.read_text(encoding="utf-8"))
    snapshot["manifest"]["redaction"] = {"names": ["Dana Whitfield"]}
    run_json.write_text(json.dumps(snapshot), encoding="utf-8")

    shown = runner.invoke(app, ["show", BASE, DELIVERED, "--root", str(root)])

    assert shown.exit_code == 0, shown.output
    manifest = Manifest.model_validate(snapshot["manifest"])
    assert isinstance(manifest.redaction, Redaction)
    assert manifest.redaction.names == ["Dana Whitfield"]
    assert manifest.model_dump(mode="json")["redaction"] == {"names": ["Dana Whitfield"]}
