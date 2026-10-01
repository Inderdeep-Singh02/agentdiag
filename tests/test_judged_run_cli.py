"""Seam 1: the judged Evals, the calibration notes and the Diagnosis through `agentdiag run`.

The example's judged Scenarios replay from `toy-orders.jsonl`, whose Target side came from
`scripts/record_fixtures.py --scripted` and whose Judge side it worked. Every assertion is
on what a user can observe — the summary, the exit code, `scores.json`, `judgement.jsonl`,
`run.json`, `show` — and no test calls a model (ticket 05).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.eval.notes import JUDGE_NOTES_MAX_WORDS
from agentdiag.eval.render import prompt_sections, system_prompt_from
from agentdiag.trace import read_trace, resolve_blobs
from agentdiag.trace.attributes import JUDGE_FINGERPRINT
from agentdiag.trace.show import follow_story
from tests.stories import CANCEL_EXIT, CANCEL_VERDICT

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
RECORDINGS = REPO / "tests" / "fixtures" / "recordings"
SUITE_RECORDING = RECORDINGS / "toy-orders.jsonl"

CANCEL = "cancel-processing-order"
GOAL = "delivered-order-cannot-be-cancelled"
GUARDRAILS = "cancel-with-a-promised-refund-date"
AUTHORED = "authored-lists-decide-without-a-judge"
TARGET_MODEL = "claude-sonnet-5-20260815"

runner = CliRunner()


def target_root(tmp_path: Path) -> Path:
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs"))
    return root


def run(root: Path, *scenarios: str, replay: Path = SUITE_RECORDING) -> Any:
    selection = [argument for scenario in scenarios for argument in ("--scenario", scenario)]
    return runner.invoke(app, ["run", "--root", str(root), *selection, "--replay", str(replay)])


def only_run(root: Path) -> Path:
    (run_dir,) = (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir()
    return run_dir


def trial(root: Path, scenario: str) -> Path:
    return only_run(root) / "trials" / scenario / "1"


def scores(root: Path, scenario: str) -> list[dict[str, Any]]:
    path = trial(root, scenario) / "scores.json"
    return list(json.loads(path.read_text(encoding="utf-8"))["scores"])


def judgement(root: Path, scenario: str) -> list[dict[str, Any]]:
    """`judgement.jsonl` with blobs resolved: a rendered prompt is stored once as a blob."""
    events = resolve_blobs(read_trace(trial(root, scenario) / "judgement.jsonl"))
    return [event.model_dump(mode="json") for event in events]


def diagnosis_note(root: Path, scenario: str) -> dict[str, Any]:
    (note,) = [
        event
        for event in judgement(root, scenario)
        if event["type"] == "note" and event.get("about") == "diagnosis"
    ]
    return note


def trace_span_ids(root: Path, scenario: str) -> set[str]:
    """Every Span id of the Trial's Trace: what a Score or a Diagnosis may cite."""
    events = read_trace(trial(root, scenario) / "trace.jsonl")
    return {str(event.span_id) for event in events if event.type == "span/start"}


def prompt_section_numbers(root: Path, scenario: str) -> set[int]:
    """The numbers of the Target's prompt sections, read from the Trial's Trace as the
    Diagnosis reads them: what its `sections` may name."""
    system = system_prompt_from(resolve_blobs(read_trace(trial(root, scenario) / "trace.jsonl")))
    assert system, "the Trace records the Target's system prompt"
    return {number for number, _ in prompt_sections(system)}


def run_record(root: Path) -> dict[str, Any]:
    return dict(json.loads((only_run(root) / "run.json").read_text(encoding="utf-8")))


def show(root: Path, scenario: str) -> str:
    result = runner.invoke(app, ["show", only_run(root).name, scenario, "--root", str(root)])
    assert result.exit_code == 0, result.output
    return str(result.stdout)


def edit_suite(root: Path, name: str, edit: Any) -> None:
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / f"{name}.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(document)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def with_exchange(tmp_path: Path, scenario: str, change: Any) -> Path:
    """The example's recording with one of `scenario`'s Judge exchanges changed by `change`,
    so a test can say one new thing without hand-writing a request body."""
    lines = [json.loads(line) for line in SUITE_RECORDING.read_text(encoding="utf-8").splitlines()]
    for line in lines:
        if line.get("scenario") == scenario and "output_config" in line["request"]:
            change(line)
            break
    path = tmp_path / "changed.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


# --- each judged Eval of the example, in replay ---


def test_the_goal_scenario_passes_its_judged_goal_and_exits_0(tmp_path: Path) -> None:
    root = target_root(tmp_path)

    result = run(root, GOAL)

    assert result.exit_code == 0, result.stdout
    (score,) = scores(root, GOAL)
    assert (score["eval"], score["verdict"], score["source"]["kind"]) == ("goal", "pass", "judge")
    # The story, not the wording: the goal's answer is captured (ticket 21, decision 41).
    assert score["evidence"]
    assert set(score["evidence"]) <= trace_span_ids(root, GOAL)
    assert score["rationale"].strip()


def test_guardrails_writes_one_score_per_rule_and_the_summary_names_the_rule_that_broke(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)

    result = run(root, GUARDRAILS)

    assert result.exit_code == 1, result.stdout
    assert [(s["eval"], s["eval_id"], s["verdict"]) for s in scores(root, GUARDRAILS)] == [
        ("guardrails", "no-refund-timing", "fail"),
        ("guardrails", "speaks-as-the-desk", "pass"),
    ]
    assert "  guardrails[no-refund-timing]  fail" in result.stdout
    assert "  guardrails[speaks-as-the-desk]  pass" in result.stdout
    assert "guardrails[no-refund-timing]  fail" in show(root, GUARDRAILS)


def test_a_scenario_level_guardrails_pick_is_judged_on_the_picked_rule_only(
    tmp_path: Path,
) -> None:
    """Decision 16: the Scenario names an id; preflight hands the Eval the rule's text."""
    root = target_root(tmp_path)
    edit_suite(
        root,
        "guardrails",
        lambda document: document["scenarios"][0].update(
            {"evals": [{"guardrails": ["no-refund-timing"]}]}
        ),
    )

    run(root, GUARDRAILS)

    assert [score["eval_id"] for score in scores(root, GUARDRAILS)] == ["no-refund-timing"]
    request = next(event for event in judgement(root, GUARDRAILS) if event["type"] == "request")
    prompt = request["body"]["messages"][0]["content"]
    assert "- no-refund-timing (No promised refund timing): Never tell the customer" in prompt
    assert "speaks-as-the-desk" not in prompt.split("## The rules", 1)[1].split("##", 1)[0]


def test_authored_lists_make_data_query_and_tool_choice_mechanical_in_a_run(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)

    result = run(root, AUTHORED)

    assert result.exit_code == 0, result.stdout
    by_eval = {score["eval"]: score for score in scores(root, AUTHORED)}
    assert by_eval["data_query"]["source"]["kind"] == "mechanical"
    assert by_eval["tool_choice"]["source"]["kind"] == "mechanical"
    # Only the Diagnosis asked the Judge anything.
    names = [e["name"] for e in judgement(root, AUTHORED) if e["type"] == "span/start"]
    assert names == ["judge diagnosis"]


# --- the Diagnosis (D24) ---


def test_every_judged_trial_gets_a_diagnosis_after_its_scores_for_a_pass_too(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)

    run(root, GOAL)

    note = diagnosis_note(root, GOAL)
    assert note["actor"] == "judge"
    # A captured narrative (decision 41): its story, never its wording.
    assert note["text"].strip()
    assert not note["text"].startswith("No Diagnosis was produced")
    assert set(note["cites"]) <= trace_span_ids(root, GOAL)
    assert set(note["sections"]) <= prompt_section_numbers(root, GOAL)
    spans = [e for e in judgement(root, GOAL) if e["type"] == "span/start"]
    assert [span["name"] for span in spans] == ["judge goal", "judge diagnosis"]
    assert note["span_id"] == spans[-1]["span_id"]


def test_each_judge_span_carries_the_judge_fingerprint_its_call_was_made_under(
    tmp_path: Path,
) -> None:
    """Ticket 21, decision 40: `judgement.jsonl` names the Judge of each call (ADR-0003 §8)
    without a reader joining it to `scores.json`; the Diagnosis's prompt is its own, so its
    Fingerprint is too."""
    root = target_root(tmp_path)

    run(root, GOAL)

    (score,) = scores(root, GOAL)
    goal, narrative = [e for e in judgement(root, GOAL) if e["type"] == "span/start"]
    assert goal["attributes"][JUDGE_FINGERPRINT] == score["source"]["judge_fingerprint"]
    assert len(narrative["attributes"][JUDGE_FINGERPRINT]) == 64
    assert narrative["attributes"][JUDGE_FINGERPRINT] != goal["attributes"][JUDGE_FINGERPRINT]


def test_the_diagnosis_is_never_a_score_and_never_counted(tmp_path: Path) -> None:
    root = target_root(tmp_path)

    result = run(root, GOAL)

    assert [score["eval"] for score in scores(root, GOAL)] == ["goal"]
    scorecard = json.loads((only_run(root) / "scorecard.json").read_text(encoding="utf-8"))
    assert sum(scorecard["counts"].values()) == 1
    assert "diagnosis" not in result.stdout


def test_show_renders_the_diagnosis_after_the_scores(tmp_path: Path) -> None:
    root = target_root(tmp_path)
    run(root, GUARDRAILS)

    shown = show(root, GUARDRAILS)

    assert shown.index("\nDiagnosis") > shown.index("\nScores")
    diagnosis = shown.split("\nDiagnosis", 1)[1]
    # The captured narrative's own words are the Judge's (decision 41); what `show` must
    # do is print them, and what they cite, after the Scores.
    note = diagnosis_note(root, GUARDRAILS)
    assert note["text"].split()[0] in diagnosis
    if note["cites"]:
        assert f"cites      {', '.join(note['cites'])}" in diagnosis
    if note["sections"]:
        assert f"sections   {', '.join(str(n) for n in note['sections'])}" in diagnosis


def test_follow_streams_the_diagnosis_once_the_scores_are_written(tmp_path: Path) -> None:
    root = target_root(tmp_path)
    run(root, GOAL)

    streamed = list(follow_story(only_run(root), GOAL, wait_seconds=0))

    assert "Scores" in streamed
    assert any(line.startswith("Diagnosis") for line in streamed)
    assert streamed.index("Scores") < next(
        index for index, line in enumerate(streamed) if line.startswith("Diagnosis")
    )


def test_a_failed_diagnosis_says_none_was_produced_and_touches_no_score(tmp_path: Path) -> None:
    root = target_root(tmp_path)
    recording = tmp_path / "no-diagnosis.jsonl"
    lines = SUITE_RECORDING.read_text(encoding="utf-8").splitlines()
    # Everything but the goal Scenario's last exchange, which is its Diagnosis.
    goal_lines = [i for i, line in enumerate(lines) if json.loads(line).get("scenario") == GOAL]
    recording.write_text(
        "\n".join(line for i, line in enumerate(lines) if i != goal_lines[-1]) + "\n",
        encoding="utf-8",
    )

    result = run(root, GOAL, replay=recording)

    assert result.exit_code == 0, result.stdout
    assert [score["verdict"] for score in scores(root, GOAL)] == ["pass"]
    assert diagnosis_note(root, GOAL)["text"].startswith("No Diagnosis was produced:")
    errors = [e for e in judgement(root, GOAL) if e["type"] == "error"]
    assert len(errors) == 1
    assert "No Diagnosis was produced" in show(root, GOAL)


def test_run_json_records_every_judged_prompt_and_the_diagnosis(tmp_path: Path) -> None:
    root = target_root(tmp_path)

    run(root, GOAL, GUARDRAILS)

    prompts = run_record(root)["judge"]["prompts"]
    assert set(prompts) == {"goal", "guardrails", "diagnosis"}
    assert prompts["diagnosis"]["version"] == "diagnosis.v5"
    assert "## The Scores" in prompts["diagnosis"]["text"]


# --- calibration notes (ADR-0003 §8) ---


NOTES = (
    "A refund amount the cancel tool returned is data the Target may repeat; only a refund "
    "timing is invented."
)


def write_notes(root: Path, text: str) -> None:
    (root / ".agentdiag" / "targets" / "toy-order-desk" / "judge_notes.md").write_text(
        text, encoding="utf-8"
    )


def test_the_notes_reach_every_judge_prompt_verbatim(tmp_path: Path) -> None:
    root = target_root(tmp_path)
    write_notes(root, f"<!-- for the author only -->\n{NOTES}\n")

    run(root, GOAL)

    requests = [e for e in judgement(root, GOAL) if e["type"] == "request"]
    assert len(requests) == 2, "the Eval's call and the Diagnosis's"
    for request in requests:
        prompt = request["body"]["messages"][0]["content"]
        assert "## Calibration notes for this Target\n\n" in prompt
        assert NOTES in prompt
        assert "for the author only" not in prompt


def test_the_notes_are_recorded_in_run_json(tmp_path: Path) -> None:
    root = target_root(tmp_path)
    write_notes(root, NOTES)

    run(root, GOAL)

    notes = run_record(root)["judge"]["notes"]
    assert notes["path"] == "judge_notes.md"
    assert notes["text"] == NOTES
    assert len(notes["fingerprint"]) == 64


def test_an_edit_to_the_notes_changes_the_judge_fingerprint_on_every_judged_score(
    tmp_path: Path,
) -> None:
    before_root = target_root(tmp_path / "before")
    run(before_root, GUARDRAILS)
    after_root = target_root(tmp_path / "after")
    write_notes(after_root, NOTES)
    run(after_root, GUARDRAILS)

    before = {s["source"]["judge_fingerprint"] for s in scores(before_root, GUARDRAILS)}
    after = {s["source"]["judge_fingerprint"] for s in scores(after_root, GUARDRAILS)}
    assert len(before) == len(after) == 1
    assert before != after


def test_the_example_without_notes_says_the_target_has_none(tmp_path: Path) -> None:
    root = target_root(tmp_path)

    run(root, GOAL)

    request = next(e for e in judgement(root, GOAL) if e["type"] == "request")
    assert "This Target has no calibration notes." in request["body"]["messages"][0]["content"]


def test_notes_over_the_word_budget_refuse_the_run_before_anything_is_written(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)
    write_notes(root, "word " * (JUDGE_NOTES_MAX_WORDS + 1))

    result = run(root, GOAL)

    assert result.exit_code == 3, result.stdout
    assert f"{JUDGE_NOTES_MAX_WORDS + 1} words" in result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()


def test_notes_the_manifest_names_and_nobody_wrote_refuse_the_run(tmp_path: Path) -> None:
    root = target_root(tmp_path)
    (root / ".agentdiag" / "targets" / "toy-order-desk" / "judge_notes.md").unlink()

    result = run(root, GOAL)

    assert result.exit_code == 3, result.stdout
    assert "judge_notes 'judge_notes.md'" in result.stdout


# --- the per-Eval Judge override (D14, D19) ---


def override_goal(root: Path, judge: dict[str, Any]) -> None:
    def edit(document: dict[str, Any]) -> None:
        for scenario in document["scenarios"]:
            if scenario["id"] == GOAL:
                expected = scenario["evals"][0]["goal"]
                scenario["evals"] = [
                    {"eval": "goal", "params": {"expected": expected}, "judge": judge}
                ]

    edit_suite(root, "orders", edit)


def as_the_target_model(line: dict[str, Any]) -> None:
    """The goal exchange as a Judge on the Target's own model would have had it."""
    line["request"]["model"] = "claude-sonnet-5"
    line["response"]["model"] = TARGET_MODEL


def test_an_eval_may_override_the_judge_and_the_score_names_the_override(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)
    override_goal(root, {"model": "claude-sonnet-5"})

    run(root, GOAL, replay=with_exchange(tmp_path, GOAL, as_the_target_model))

    (score,) = scores(root, GOAL)
    assert score["source"]["requested_model"] == "claude-sonnet-5"
    assert score["source"]["resolved_model"] == TARGET_MODEL
    assert score["verdict"] == "pass"
    assert run_record(root)["judge"]["overrides"] == {
        "goal": {"model": "claude-sonnet-5", "effort": None}
    }
    # The Run's default Judge still writes the Diagnosis.
    spans = [e for e in judgement(root, GOAL) if e["type"] == "span/start"]
    assert [span["attributes"]["gen_ai.request.model"] for span in spans] == [
        "claude-sonnet-5",
        "claude-opus-5",
    ]


def test_an_override_effort_outside_the_closed_set_refuses_the_run(tmp_path: Path) -> None:
    root = target_root(tmp_path)
    override_goal(root, {"effort": "very-high"})

    result = run(root, GOAL)

    assert result.exit_code == 3, result.stdout
    assert "'very-high'" in result.stdout


# --- a Judge on the Target's own model (D23) ---


def test_a_judge_sharing_the_targets_model_is_flagged_on_the_score_scorecard_and_show(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)
    override_goal(root, {"model": "claude-sonnet-5"})

    result = run(root, GOAL, replay=with_exchange(tmp_path, GOAL, as_the_target_model))

    (score,) = scores(root, GOAL)
    assert score["shared_model"] is True
    scorecard = json.loads((only_run(root) / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["shared_model"] is True
    warning = "judge shares the Target's model (self-preference risk)"
    assert warning in next(line for line in result.stdout.splitlines() if "pass rate" in line)
    assert f"warning: {warning}" in show(root, GOAL)


def test_the_example_judge_does_not_share_the_targets_model(tmp_path: Path) -> None:
    root = target_root(tmp_path)

    result = run(root, GOAL)

    assert json.loads((only_run(root) / "scorecard.json").read_text())["shared_model"] is False
    assert "self-preference" not in result.stdout


def test_two_declarations_of_one_name_asking_for_different_judges_refuse_the_run(
    tmp_path: Path,
) -> None:
    """`run.json` names an override by its declaration's id or Eval name, so two under one
    name that disagree would make the record ambiguous."""
    root = target_root(tmp_path)

    def edit(document: dict[str, Any]) -> None:
        goal = {"eval": "goal", "params": {"expected": "The order is explained."}}
        for scenario in document["scenarios"]:
            if scenario["id"] == GOAL:
                scenario["evals"] = [{**goal, "judge": {"model": "claude-sonnet-5"}}]
            if scenario["id"] == CANCEL:
                scenario["evals"] = [{**goal, "judge": {"model": "claude-haiku-4-5"}}]

    edit_suite(root, "orders", edit)

    result = run(root, GOAL, CANCEL)

    assert result.exit_code == 3, result.stdout
    assert "'goal' runs on a different Judge" in result.stdout
    assert "give each declaration its own `id`" in result.stdout


def test_one_declaration_overriding_and_another_under_its_name_not_refuses_the_run(
    tmp_path: Path,
) -> None:
    """Keyed by id or Eval name, `run.json` could not say which of the two ran on which."""
    root = target_root(tmp_path)

    def edit(document: dict[str, Any]) -> None:
        goal = {"eval": "goal", "params": {"expected": "The order is explained."}}
        for scenario in document["scenarios"]:
            if scenario["id"] == GOAL:
                scenario["evals"] = [{**goal, "judge": {"model": "claude-sonnet-5"}}]
            if scenario["id"] == CANCEL:
                scenario["evals"] = [goal]

    edit_suite(root, "orders", edit)

    result = run(root, CANCEL, GOAL)

    assert result.exit_code == 3, result.stdout
    assert "'goal' runs on a different Judge in cancel-processing-order" in result.stdout


# --- a recording the Diagnosis does not fit never overrules a Score (D24) ---


def test_a_replay_mismatch_confined_to_the_diagnosis_leaves_the_scores_as_they_are(
    tmp_path: Path,
) -> None:
    """The review's repro: the Diagnosis's recorded request differs by one field, so its
    exchange is never taken. The Evals' exchanges were all used, so the fail stands
    (`tests/stories.py`); the unused Diagnosis exchange is said in a `note` and on stderr,
    and nothing is invalid."""
    root = target_root(tmp_path)
    lines = [
        json.loads(line)
        for line in (RECORDINGS / "toy-cancel.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    lines[-1]["request"]["max_tokens"] = 15999  # the Diagnosis is the recording's last line
    recording = tmp_path / "toy-cancel-diagnosis-mismatch.jsonl"
    recording.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    result = run(root, CANCEL, replay=recording)

    assert result.exit_code == CANCEL_EXIT, result.stdout
    assert [(s["eval"], s["verdict"]) for s in scores(root, CANCEL)] == [
        ("prompt_adherence", CANCEL_VERDICT)
    ]
    assert diagnosis_note(root, CANCEL)["text"].startswith("No Diagnosis was produced:")
    left = [
        event
        for event in judgement(root, CANCEL)
        if event["type"] == "note" and event.get("about") == "recording"
    ]
    assert len(left) == 1
    assert "no Score is affected" in left[0]["text"]
    assert "no Score is affected" in result.stderr


def test_an_evals_exchange_left_unused_still_overrules_the_scores(tmp_path: Path) -> None:
    """The half of the check that runs before the Diagnosis keeps its Phase 4 meaning."""
    root = target_root(tmp_path)
    lines = (RECORDINGS / "toy-cancel.jsonl").read_text(encoding="utf-8").splitlines()
    judge_line = json.loads(lines[3])  # the Eval's exchange, after the Target's three
    judge_line["request"]["max_tokens"] = 15999
    unused = [*lines[:3], json.dumps(judge_line), lines[4]]
    recording = tmp_path / "toy-cancel-eval-mismatch.jsonl"
    recording.write_text("\n".join(unused) + "\n", encoding="utf-8")

    run(root, CANCEL, replay=recording)

    (score,) = scores(root, CANCEL)
    assert (score["verdict"], score["fault_source"]) == ("invalid", "agentdiag")


def test_show_spells_the_diagnosis_note_as_the_diagnosis_writes_it() -> None:
    """`show` keeps its own copy so it can read a Run without the SDK; they must agree."""
    from agentdiag.eval import diagnosis
    from agentdiag.trace import show as show_module

    assert show_module.DIAGNOSIS == diagnosis.ABOUT
