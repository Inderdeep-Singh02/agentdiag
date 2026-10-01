"""Seam 2: each judged Eval, and the Diagnosis, answered by a worked recording (ticket 05).

Every test runs one Eval's real module over the Trace fixture of the example Scenario that
exercises it, with the Scenario exactly as a Run hands it over (effective, its guardrail
rules resolved), through a recording `scripts/record_fixtures.py --scripted` worked: the
request is what the module renders, the response one authored answer. What varies is only
what the Judge said, and each test says what agentdiag makes of it — the four answer
rules (D13, ADR-0003 §3, §4) held for every judged Eval, and each Eval's own reading of
its answer. No test calls a model.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from anthropic.types import Message
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.eval import diagnosis
from agentdiag.eval.judge import Judge
from agentdiag.eval.judged import MODULES, judge_function
from agentdiag.eval.judged_score import judge_fingerprint
from agentdiag.eval.registry import JUDGED_WITH_A_PROMPT
from agentdiag.eval.render import JudgeContext, trial_values
from agentdiag.eval.score import Score
from agentdiag.eval.template import TOKEN
from agentdiag.model.claude_code import Backend
from agentdiag.model.client import ReplayModelClient
from agentdiag.model.replay import Recording, ReplayCursor
from agentdiag.run.preflight import preflight
from agentdiag.scenario.models import EvalDeclaration, Scenario
from agentdiag.scenario.select import Selection
from agentdiag.trace import Event, TraceWriter, read_trace, resolve_blobs
from tests.fakes.workspace import the_target
from tests.stories import CANCEL_VERDICT

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
RECORDINGS = REPO / "tests" / "fixtures" / "recordings"
TRACES = REPO / "tests" / "fixtures" / "traces"
SUITE_RECORDING = RECORDINGS / "toy-orders.jsonl"
EMPTY = "(no exchange at all)"

JUDGE_MODEL = "claude-opus-5"
REPLAY = Backend(kind="replay")

GOAL = "delivered-order-cannot-be-cancelled"
GUARDRAILS = "cancel-with-a-promised-refund-date"
GROUNDING = "order-details-come-from-the-lookup"
QUERY = "status-lookup-asks-for-the-named-order"
CHOICE = "cancel-looks-up-then-cancels"
AUTHORED = "authored-lists-decide-without-a-judge"
CANCEL = "cancel-processing-order"
GREETING = "greeting-calls-no-tool"
STATUS = "status-question-is-not-a-cancel"


# --- helpers: a judged Eval over one Trace, as a Run would hand it over ---


def scenario_of(identifier: str) -> tuple[Scenario, dict[str, Any], Any]:
    """The example's Scenario as preflight plans it, with the Manifest's tools and notes."""
    plan = preflight(
        the_target(EXAMPLE), Selection(scenario=[identifier]), SUITE_RECORDING, dry_run=True
    )
    return plan.selected[0].scenario, plan.tool_kinds, plan.judge.notes if plan.judge else None


def context(
    identifier: str,
    eval_name: str,
    *,
    events: list[Event] | None = None,
    fidelity: str = "instrumented",
    scenario: Scenario | None = None,
) -> JudgeContext:
    planned, tool_kinds, notes = scenario_of(identifier)
    subject = scenario or planned
    declaration = next(
        (d for d in subject.evals if d.eval == eval_name), EvalDeclaration(eval=eval_name)
    )
    return JudgeContext.of(
        subject,
        declaration,
        events if events is not None else read_trace(TRACES / f"{identifier}.trace.jsonl"),
        fidelity=fidelity,  # type: ignore[arg-type]
        notes=notes,
        tool_kinds=tool_kinds,
    )


class Judged:
    """One judged Eval's run: its Scores, what it wrote, and whether it asked a model."""

    def __init__(self, scores: list[Score], judgement: Path, cursor: ReplayCursor) -> None:
        self.scores = scores
        # Blobs resolved: a rendered prompt is long enough to be stored once as a blob.
        self.events = [
            event.model_dump(mode="json") for event in resolve_blobs(read_trace(judgement))
        ]
        self.cursor = cursor

    @property
    def score(self) -> Score:
        (only,) = self.scores
        return only

    def spans(self) -> list[dict[str, Any]]:
        return [event for event in self.events if event["type"] == "span/start"]

    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event["type"] == kind]

    def by_rule(self) -> dict[str | None, tuple[str, str | None]]:
        return {score.eval_id: (score.verdict, score.reason) for score in self.scores}


def perform(
    recording: str,
    judge_context: JudgeContext,
    tmp_path: Path,
    *,
    run: Callable[[JudgeContext, Judge, TraceWriter], list[Score]] | None = None,
) -> Judged:
    """Run the context's Eval through a Judge answered by `recording`."""
    path = tmp_path / f"{recording}.{len(list(tmp_path.glob('*.judgement.jsonl')))}.judgement.jsonl"
    source = tmp_path / "empty.jsonl"
    source.write_text("", encoding="utf-8")
    cursor = ReplayCursor(
        Recording.load(source if recording == EMPTY else RECORDINGS / f"{recording}.jsonl")
    )
    judge = Judge(ReplayModelClient(cursor), JUDGE_MODEL, None, backend=REPLAY)
    writer = TraceWriter(path)
    writer.start(trace_id="test/judged/1", scenario="test", run="test", trial=1)
    try:
        scores = (run or judge_function(judge_context.declaration.eval))(
            judge_context, judge, writer
        )
    finally:
        writer.end("completed")
        writer.close()
    return Judged(scores, path, cursor)


def perform_over(recording: Path, judge_context: JudgeContext, tmp_path: Path) -> Judged:
    """`perform` over a recording this test wrote rather than a committed one."""
    cursor = ReplayCursor(Recording.load(recording))
    path = tmp_path / "over.judgement.jsonl"
    writer = TraceWriter(path)
    writer.start(trace_id="test/judged/1", scenario="test", run="test", trial=1)
    try:
        scores = judge_function(judge_context.declaration.eval)(
            judge_context,
            Judge(ReplayModelClient(cursor), JUDGE_MODEL, None, backend=REPLAY),
            writer,
        )
    finally:
        writer.end("completed")
        writer.close()
    return Judged(scores, path, cursor)


def tells_the_story(score: Score, judge_context: JudgeContext, verdict: str = "pass") -> None:
    """What a test over a natural recording asserts (ticket 21, decision 41): its Verdict,
    that it cites Spans the Trace has, and that it says why. Never its wording: the answer
    is the Judge's own, captured, and a re-capture must not touch a test."""
    assert score.verdict == verdict
    assert score.evidence
    assert set(score.evidence) <= {span.span_id for span in judge_context.spans}
    assert score.rationale.strip()


def answer_in(response: dict[str, Any]) -> dict[str, Any]:
    """A recorded answer, wherever the body carries it: the Claude Code backend's
    `structured_output`, or the Messages API's text block."""
    if response.get("structured_output") is not None:
        return dict(response["structured_output"])
    return dict(json.loads(response["content"][0]["text"]))


def with_answer(response: dict[str, Any], answer: dict[str, Any]) -> None:
    """Put an edited answer back where the body carried it."""
    if response.get("structured_output") is not None:
        response["structured_output"] = answer
        for block in response["content"]:
            if block.get("type") == "tool_use":
                block["input"] = answer
    else:
        response["content"][0]["text"] = json.dumps(answer)


def no_model_was_called(judged: Judged) -> None:
    """An empty recording was enough: nothing asked a model."""
    judged.cursor.assert_consumed()
    assert not judged.spans()


# --- the catalogue and the modules agree ---


def test_every_judged_eval_the_catalogue_names_has_a_prompt_module() -> None:
    """The registry names them as strings so `validate` stays offline; this holds the two
    lists to each other."""
    assert set(MODULES) == set(JUDGED_WITH_A_PROMPT)


def test_every_prompt_module_names_its_version_after_its_eval() -> None:
    for name, module in MODULES.items():
        assert module.PARTS.eval == name
        assert module.PARTS.version == module.PROMPT_VERSION
        assert module.PROMPT_VERSION.startswith(f"{name}.v")


@pytest.mark.parametrize("name", sorted(MODULES))
def test_every_judged_prompt_opens_with_the_eval_and_ends_with_the_checklist(name: str) -> None:
    """D23: one head, in one order, for every judged Eval."""
    template = MODULES[name].PARTS.template()
    headings = [line for line in template.splitlines() if line.startswith("## ")]

    assert template.startswith("You are the Judge in agentdiag")
    assert headings[:6] == [
        "## The Scenario",
        "## Ground truth",  # inside the Scenario's slot, kept when it records one
        "## The Target's prompt sections",
        "## Calibration notes for this Target",
        "## Suppressions in force for this Trace",  # kept only when one is in force
        "## The Trace",
    ]
    assert headings[-2:] == ["## How to decide", "## Before you answer, check these five things"]
    assert template.rstrip().endswith("Answer only in the structured format requested.")


# --- goal ---


def test_a_goal_the_trace_shows_met_is_a_pass_citing_its_spans(tmp_path: Path) -> None:
    judge_context = context(GOAL, "goal")
    judged = perform("goal-pass", judge_context, tmp_path)

    tells_the_story(judged.score, judge_context)
    assert judged.score.source.prompt_version == "goal.v4"


def test_a_goal_the_trace_shows_unmet_is_a_fail(tmp_path: Path) -> None:
    judged = perform("goal-fail", context(GOAL, "goal"), tmp_path)

    assert (judged.score.verdict, judged.score.evidence) == ("fail", ["llm_call-2"])


def test_a_goal_judgement_citing_nothing_is_unverifiable(tmp_path: Path) -> None:
    judged = perform("goal-no-evidence", context(GOAL, "goal"), tmp_path)

    assert (judged.score.verdict, judged.score.reason) == ("unverifiable", "evidence_missing")


def test_a_goal_answer_outside_the_schema_is_invalid_and_the_judges_fault(tmp_path: Path) -> None:
    judged = perform("goal-schema-failure", context(GOAL, "goal"), tmp_path)

    assert (judged.score.verdict, judged.score.fault_source) == ("invalid", "judge")
    (error,) = judged.of_type("error")
    assert error["span_id"] == "judge-1", "the error lands inside the Judge's Span"


def test_the_scenarios_goal_and_ground_truth_reach_the_judge(tmp_path: Path) -> None:
    """D23, and ticket 03's deferred criterion: the prompt head carries both."""
    judged = perform("goal-pass", context(GOAL, "goal"), tmp_path)

    (request,) = judged.of_type("request")
    prompt = request["body"]["messages"][0]["content"]
    assert "- goal: The customer learns that order NB-0688 was already delivered" in prompt
    assert "## Ground truth" in prompt
    assert "status: delivered" in prompt


def test_a_goal_with_no_expected_is_not_applicable_and_asks_no_model(tmp_path: Path) -> None:
    """`validate` refuses it; one built in code is scored, never guessed at."""
    planned, _, _ = scenario_of(GOAL)
    bare = planned.model_copy(update={"evals": [EvalDeclaration(eval="goal")]})

    judged = perform(EMPTY, context(GOAL, "goal", scenario=bare), tmp_path)

    assert (judged.score.verdict, judged.score.reason) == ("unverifiable", "eval_not_applicable")
    no_model_was_called(judged)


# --- guardrails ---


def test_guardrails_asks_about_every_rule_in_one_call_and_scores_each_rule(
    tmp_path: Path,
) -> None:
    judged = perform("guardrails-fail", context(GUARDRAILS, "guardrails"), tmp_path)

    assert judged.by_rule() == {
        "no-refund-timing": ("fail", None),
        "speaks-as-the-desk": ("pass", None),
    }
    assert [span["name"] for span in judged.spans()] == ["judge guardrails"]
    assert len({score.source.judge_fingerprint for score in judged.scores}) == 1


def test_each_guardrail_score_cites_its_own_evidence(tmp_path: Path) -> None:
    judged = perform("guardrails-fail", context(GUARDRAILS, "guardrails"), tmp_path)

    by_id = {score.eval_id: score for score in judged.scores}
    assert by_id["no-refund-timing"].evidence == ["tool_call-1", "llm_call-3"]
    assert by_id["speaks-as-the-desk"].evidence == ["llm_call-3"]


def test_guardrails_that_all_held_are_each_a_pass(tmp_path: Path) -> None:
    judged = perform("guardrails-pass", context(GUARDRAILS, "guardrails"), tmp_path)

    assert set(judged.by_rule().values()) == {("pass", None)}
    assert len(judged.scores) == 2


def test_a_guardrail_judged_on_no_evidence_is_unverifiable(tmp_path: Path) -> None:
    judged = perform("guardrails-no-evidence", context(GUARDRAILS, "guardrails"), tmp_path)

    assert set(judged.by_rule().values()) == {("unverifiable", "evidence_missing")}


def test_a_guardrails_answer_outside_the_schema_invalidates_every_rule_it_asked(
    tmp_path: Path,
) -> None:
    judged = perform("guardrails-schema-failure", context(GUARDRAILS, "guardrails"), tmp_path)

    assert {score.eval_id for score in judged.scores} == {"no-refund-timing", "speaks-as-the-desk"}
    assert {(score.verdict, score.fault_source) for score in judged.scores} == {
        ("invalid", "judge")
    }


def test_a_rule_the_judges_answer_omits_is_invalid_not_missing(tmp_path: Path) -> None:
    judged = perform("guardrails-omits-a-rule", context(GUARDRAILS, "guardrails"), tmp_path)

    by_id = {score.eval_id: score for score in judged.scores}
    assert (by_id["no-refund-timing"].verdict, by_id["no-refund-timing"].fault_source) == (
        "invalid",
        "judge",
    )
    assert by_id["speaks-as-the-desk"].verdict == "pass"


def with_rules(rules: list[Any]) -> Scenario:
    planned, _, _ = scenario_of(GUARDRAILS)
    return planned.model_copy(
        update={"evals": [EvalDeclaration(eval="guardrails", params={"rules": rules})]}
    )


def test_a_rule_with_no_text_is_not_applicable_and_the_rest_are_still_asked(
    tmp_path: Path,
) -> None:
    """Decision 16: namespaced `constraint:*` ids carry no text, so no Judge holds the
    Target to them — and the rules that do have text are judged exactly as before."""
    planned, _, _ = scenario_of(GUARDRAILS)
    authored = planned.evals[0].params["rules"]
    scenario = with_rules([*authored, {"id": "constraint:no_invented_data", "rule": None}])

    judged = perform(
        "guardrails-fail", context(GUARDRAILS, "guardrails", scenario=scenario), tmp_path
    )

    assert judged.by_rule() == {
        "no-refund-timing": ("fail", None),
        "speaks-as-the-desk": ("pass", None),
        "constraint:no_invented_data": ("unverifiable", "eval_not_applicable"),
    }


def test_guardrails_whose_rules_all_lack_text_ask_no_model_at_all(tmp_path: Path) -> None:
    scenario = with_rules([{"id": "constraint:a", "rule": None}, "an-unresolved-id"])

    judged = perform(EMPTY, context(GUARDRAILS, "guardrails", scenario=scenario), tmp_path)

    assert judged.by_rule() == {
        "constraint:a": ("unverifiable", "eval_not_applicable"),
        "an-unresolved-id": ("unverifiable", "eval_not_applicable"),
    }
    no_model_was_called(judged)


def test_the_rules_are_shown_to_the_judge_by_id_name_and_text(tmp_path: Path) -> None:
    judged = perform("guardrails-fail", context(GUARDRAILS, "guardrails"), tmp_path)

    (request,) = judged.of_type("request")
    prompt = request["body"]["messages"][0]["content"]
    assert "## The rules" in prompt
    assert "- no-refund-timing (No promised refund timing): Never tell the customer" in prompt


# --- data_grounding ---


def test_claims_every_one_of_which_is_supported_are_a_pass_citing_claims_and_support(
    tmp_path: Path,
) -> None:
    judge_context = context(GROUNDING, "data_grounding")
    judged = perform("data_grounding-pass", judge_context, tmp_path)

    tells_the_story(judged.score, judge_context)


def test_an_unsupported_claim_is_a_fail_whose_rationale_names_it(tmp_path: Path) -> None:
    judged = perform("data_grounding-fail", context(GROUNDING, "data_grounding"), tmp_path)

    assert judged.score.verdict == "fail"
    assert "Unsupported claims: 'it cost $64.00' in llm_call-2." in judged.score.rationale


def test_a_pass_beside_an_unsupported_claim_is_recorded_as_the_fail_the_claim_says(
    tmp_path: Path,
) -> None:
    judged = perform(
        "data_grounding-pass-beside-an-unsupported-claim",
        context(GROUNDING, "data_grounding"),
        tmp_path,
    )

    assert judged.score.verdict == "fail"
    assert "'it is in black' in llm_call-2" in judged.score.rationale


def claims_answer(tmp_path: Path, claims: list[dict[str, Any]], verdict: str = "pass") -> Path:
    """`data_grounding-fail`'s exchange (worked, and the same request as the pass), its
    answer's claims replaced: the request is the one the module renders, so only what the
    Judge said differs."""
    exchange = json.loads((RECORDINGS / "data_grounding-fail.jsonl").read_text())
    answer = json.loads(exchange["response"]["content"][0]["text"])
    answer.update(verdict=verdict, claims=claims)
    exchange["response"]["content"][0]["text"] = json.dumps(answer)
    path = tmp_path / "claims.jsonl"
    path.write_text(json.dumps(exchange) + "\n", encoding="utf-8")
    return path


def test_a_claim_whose_cited_support_is_not_in_the_trace_is_unsupported(tmp_path: Path) -> None:
    """A phantom supporting Span supports nothing: rule 2 would drop it and leave the
    claim looking grounded (ticket 05 Spec review)."""
    recording = claims_answer(
        tmp_path,
        [
            {"claim": "it has shipped", "span": "llm_call-2", "supported_by": "retrieval-1"},
            {"claim": "it cost $64.00", "span": "llm_call-2", "supported_by": "tool_call-99"},
        ],
    )
    judged = perform_over(recording, context(GROUNDING, "data_grounding"), tmp_path)

    assert judged.score.verdict == "fail"
    assert "'it cost $64.00' in llm_call-2" in judged.score.rationale
    assert "tool_call-99 is not in the Trace" in judged.score.rationale


def test_a_grounding_judgement_citing_nothing_is_unverifiable(tmp_path: Path) -> None:
    judged = perform("data_grounding-no-evidence", context(GROUNDING, "data_grounding"), tmp_path)

    assert (judged.score.verdict, judged.score.reason) == ("unverifiable", "evidence_missing")


def test_a_grounding_answer_outside_the_schema_is_invalid(tmp_path: Path) -> None:
    judged = perform(
        "data_grounding-schema-failure", context(GROUNDING, "data_grounding"), tmp_path
    )

    assert (judged.score.verdict, judged.score.fault_source) == ("invalid", "judge")


def test_a_trace_with_no_target_message_has_no_claim_to_ground(tmp_path: Path) -> None:
    events = [
        event
        for event in read_trace(TRACES / f"{GROUNDING}.trace.jsonl")
        if not (event.type == "message" and event.actor == "target")
    ]

    judged = perform(EMPTY, context(GROUNDING, "data_grounding", events=events), tmp_path)

    assert (judged.score.verdict, judged.score.reason) == ("unverifiable", "eval_not_applicable")
    no_model_was_called(judged)


def test_data_grounding_below_reconstructed_fidelity_asks_no_model(tmp_path: Path) -> None:
    """D21: a claim checked against a tool result the Adapter only observed would be
    checked against the Target's own account of it."""
    events = [
        event.model_copy(update={"fidelity": "observed"}) if event.type == "span/start" else event
        for event in read_trace(TRACES / f"{GROUNDING}.trace.jsonl")
    ]

    judged = perform(
        EMPTY, context(GROUNDING, "data_grounding", events=events, fidelity="observed"), tmp_path
    )

    assert (judged.score.verdict, judged.score.reason) == ("unverifiable", "fidelity_too_low")
    no_model_was_called(judged)


# --- data_query ---


def test_a_lookup_the_judge_finds_right_is_a_pass(tmp_path: Path) -> None:
    judged = perform("data_query-pass", context(QUERY, "data_query"), tmp_path)

    assert (judged.score.verdict, judged.score.evidence) == ("pass", ["retrieval-1"])
    assert judged.score.source.kind == "judge"


def test_a_lookup_the_judge_finds_wrong_is_a_fail(tmp_path: Path) -> None:
    judged = perform("data_query-fail", context(QUERY, "data_query"), tmp_path)

    assert judged.score.verdict == "fail"


def test_a_query_judgement_citing_nothing_is_unverifiable(tmp_path: Path) -> None:
    judged = perform("data_query-no-evidence", context(QUERY, "data_query"), tmp_path)

    assert (judged.score.verdict, judged.score.reason) == ("unverifiable", "evidence_missing")


def test_a_query_answer_outside_the_schema_is_invalid(tmp_path: Path) -> None:
    judged = perform("data_query-schema-failure", context(QUERY, "data_query"), tmp_path)

    assert (judged.score.verdict, judged.score.fault_source) == ("invalid", "judge")


def test_a_trial_with_no_lookup_has_no_query_to_judge(tmp_path: Path) -> None:
    judged = perform(
        EMPTY,
        context(QUERY, "data_query", events=read_trace(TRACES / f"{GREETING}.trace.jsonl")),
        tmp_path,
    )

    assert (judged.score.verdict, judged.score.reason) == ("unverifiable", "eval_not_applicable")
    no_model_was_called(judged)


def test_authored_expect_tool_args_make_data_query_mechanical(tmp_path: Path) -> None:
    judged = perform(EMPTY, context(AUTHORED, "data_query"), tmp_path)

    assert judged.score.verdict == "pass"
    assert judged.score.source.kind == "mechanical"
    assert judged.score.source.code == "agentdiag.eval.data_query:mechanical_data_query"
    assert "expect_tool_args on lookup_order: pass" in judged.score.rationale
    no_model_was_called(judged)


def test_a_mechanical_data_query_fails_when_an_authored_argument_does_not_hold(
    tmp_path: Path,
) -> None:
    planned, _, _ = scenario_of(AUTHORED)
    wrong = EvalDeclaration.model_validate(
        {"expect_tool_args": {"tool": "lookup_order", "args": {"order_id": {"equals": "NB-9999"}}}}
    )
    scenario = planned.model_copy(update={"evals": [wrong, EvalDeclaration(eval="data_query")]})

    judged = perform(EMPTY, context(AUTHORED, "data_query", scenario=scenario), tmp_path)

    assert (judged.score.verdict, judged.score.source.kind) == ("fail", "mechanical")
    assert judged.score.evidence == ["retrieval-1"]


# --- tool_choice ---


def test_tools_the_judge_finds_right_for_the_intent_are_a_pass(tmp_path: Path) -> None:
    judge_context = context(CHOICE, "tool_choice")
    judged = perform("tool_choice-pass", judge_context, tmp_path)

    tells_the_story(judged.score, judge_context)


def test_a_tool_the_judge_finds_unnecessary_is_a_fail(tmp_path: Path) -> None:
    judged = perform("tool_choice-fail", context(CHOICE, "tool_choice"), tmp_path)

    assert (judged.score.verdict, judged.score.evidence) == ("fail", ["tool_call-1"])


def test_a_tool_choice_citing_nothing_is_unverifiable(tmp_path: Path) -> None:
    judged = perform("tool_choice-no-evidence", context(CHOICE, "tool_choice"), tmp_path)

    assert (judged.score.verdict, judged.score.reason) == ("unverifiable", "evidence_missing")


def test_a_tool_choice_answer_outside_the_schema_is_invalid(tmp_path: Path) -> None:
    judged = perform("tool_choice-schema-failure", context(CHOICE, "tool_choice"), tmp_path)

    assert (judged.score.verdict, judged.score.fault_source) == ("invalid", "judge")


def test_no_tool_span_in_a_trace_that_may_have_missed_calls_is_not_applicable(
    tmp_path: Path,
) -> None:
    """Decision 3: only at `instrumented` is no tool Span the fact that none was called."""
    events = [
        event.model_copy(update={"fidelity": "reconstructed"})
        if event.type == "span/start"
        else event
        for event in read_trace(TRACES / f"{GREETING}.trace.jsonl")
    ]

    judged = perform(
        EMPTY,
        context(CHOICE, "tool_choice", events=events, fidelity="reconstructed"),
        tmp_path,
    )

    assert (judged.score.verdict, judged.score.reason) == ("unverifiable", "eval_not_applicable")
    no_model_was_called(judged)


def test_authored_tool_lists_make_tool_choice_mechanical(tmp_path: Path) -> None:
    judged = perform(EMPTY, context(AUTHORED, "tool_choice"), tmp_path)

    assert (judged.score.verdict, judged.score.source.kind) == ("pass", "mechanical")
    assert judged.score.source.code == "agentdiag.eval.tool_choice:mechanical_tool_choice"
    no_model_was_called(judged)


def test_a_mechanical_score_carries_no_judge_fields(tmp_path: Path) -> None:
    """No Judge ran, so the Score names the code that decided and nothing of a Judge."""
    for eval_name, module in (("data_query", "data_query"), ("tool_choice", "tool_choice")):
        judged = perform(EMPTY, context(AUTHORED, eval_name), tmp_path)

        source = judged.score.source
        assert source.kind == "mechanical"
        assert source.code == f"agentdiag.eval.{module}:mechanical_{module}"
        assert (source.requested_model, source.prompt_version, source.judge_fingerprint) == (
            None,
            None,
            None,
        )


def test_a_mechanical_tool_choice_below_its_fidelity_is_gated_once_as_mechanical(
    tmp_path: Path,
) -> None:
    events = [
        event.model_copy(update={"fidelity": "observed"}) if event.type == "span/start" else event
        for event in read_trace(TRACES / f"{AUTHORED}.trace.jsonl")
    ]

    judged = perform(
        EMPTY, context(AUTHORED, "tool_choice", events=events, fidelity="observed"), tmp_path
    )

    assert (judged.score.verdict, judged.score.reason) == ("unverifiable", "fidelity_too_low")
    assert judged.score.source.kind == "mechanical"
    no_model_was_called(judged)


def test_forbid_tools_alone_leaves_tool_choice_to_the_judge(tmp_path: Path) -> None:
    """Amended contract: a forbidden list says which tools were wrong, not which were right,
    so it does not make `tool_choice` mechanical."""
    planned, _, _ = scenario_of(CHOICE)
    forbid = EvalDeclaration.model_validate({"forbid_tools": ["lookup_order"]})
    scenario = planned.model_copy(update={"evals": [forbid, EvalDeclaration(eval="tool_choice")]})

    judged = perform(
        "tool_choice-pass", context(CHOICE, "tool_choice", scenario=scenario), tmp_path
    )

    assert (judged.score.verdict, judged.score.source.kind) == ("pass", "judge")
    assert [span["name"] for span in judged.spans()] == ["judge tool_choice"]


def test_forbid_tools_beside_a_positive_list_still_forbids_in_the_mechanical_form(
    tmp_path: Path,
) -> None:
    planned, _, _ = scenario_of(CHOICE)
    evals = [
        EvalDeclaration.model_validate({"expect_tools_any": ["lookup_order", "cancel_order"]}),
        EvalDeclaration.model_validate({"forbid_tools": ["cancel_order"]}),
        EvalDeclaration(eval="tool_choice"),
    ]
    scenario = planned.model_copy(update={"evals": evals})

    judged = perform(EMPTY, context(CHOICE, "tool_choice", scenario=scenario), tmp_path)

    assert (judged.score.verdict, judged.score.source.kind) == ("fail", "mechanical")
    assert "forbidden and called: tool_call-1" in judged.score.rationale


def test_no_tool_span_in_an_instrumented_trace_is_judged_as_the_fact_that_none_was_called(
    tmp_path: Path,
) -> None:
    """Decision 3's positive case: at `instrumented` no tool Span means no tool ran, so the
    precondition holds and the Judge is asked."""
    judged = perform("tool_choice-no-tool-called", context(GREETING, "tool_choice"), tmp_path)

    assert (judged.score.verdict, judged.score.evidence) == ("pass", ["llm_call-1"])
    assert [span["name"] for span in judged.spans()] == ["judge tool_choice"]
    judged.cursor.assert_consumed()


def test_a_forbidden_tool_called_fails_mechanical_tool_choice_naming_it(tmp_path: Path) -> None:
    """`status-question-is-not-a-cancel`: the Target cancelled an order it was asked about."""
    planned, _, _ = scenario_of(STATUS)
    scenario = planned.model_copy(
        update={"evals": [*planned.evals, EvalDeclaration(eval="tool_choice")]}
    )

    judged = perform(
        EMPTY,
        context(
            STATUS,
            "tool_choice",
            scenario=scenario,
            events=read_trace(TRACES / f"{STATUS}.trace.jsonl"),
        ),
        tmp_path,
    )

    assert (judged.score.verdict, judged.score.source.kind) == ("fail", "mechanical")
    assert "called though no list allows it: cancel_order" in judged.score.rationale
    assert "forbidden and called: tool_call-1" in judged.score.rationale


def test_a_list_restricted_to_one_turn_forbids_in_that_turn_only(tmp_path: Path) -> None:
    """`lookup-then-cancel` forbids cancel_order in Turn 1 and cancels in Turn 2."""
    planned, _, _ = scenario_of("lookup-then-cancel")
    scenario = planned.model_copy(
        update={"evals": [*planned.evals, EvalDeclaration(eval="tool_choice")]}
    )

    judged = perform(
        EMPTY,
        context(
            "lookup-then-cancel",
            "tool_choice",
            scenario=scenario,
            events=read_trace(TRACES / "lookup-then-cancel.trace.jsonl"),
        ),
        tmp_path,
    )

    assert (judged.score.verdict, judged.score.source.kind) == ("pass", "mechanical")


# --- the Diagnosis (D24) ---


def cancel_scores(tmp_path: Path) -> list[Score]:
    """The cancel Trial's Scores as the Judge gives them, which is what the Diagnosis
    recordings were asked over (decision 43): the natural `judge-fail`, not the worked pass."""
    return perform("judge-fail", context(CANCEL, "prompt_adherence"), tmp_path).scores


def diagnose(recording: str, tmp_path: Path) -> Judged:
    scores = cancel_scores(tmp_path)

    def run(judge_context: JudgeContext, judge: Judge, writer: TraceWriter) -> list[Score]:
        return diagnosis.judge(judge_context, judge, writer, scores=scores)

    return perform(recording, context(CANCEL, "diagnosis"), tmp_path, run=run)


def test_a_diagnosis_is_a_note_inside_the_judge_diagnosis_span_and_never_a_score(
    tmp_path: Path,
) -> None:
    judged = diagnose("diagnosis-ok", tmp_path)

    assert judged.scores == []
    (span,) = judged.spans()
    assert span["name"] == "judge diagnosis"
    (note,) = judged.of_type("note")
    assert note["actor"] == "judge"
    assert note["about"] == "diagnosis"
    assert note["span_id"] == span["span_id"]
    # A captured narrative (decision 41): what it rests on is in the Trial, whatever it says.
    known = context(CANCEL, "diagnosis")
    assert set(note["cites"]) <= {s.span_id for s in known.spans}
    assert set(note["sections"]) <= {0, 1, 2, 3, 4, 5}
    assert note["text"].strip()
    assert not note["text"].startswith("No Diagnosis was produced")


def test_the_diagnosis_is_asked_over_the_trace_and_every_score(tmp_path: Path) -> None:
    judged = diagnose("diagnosis-ok", tmp_path)

    (request,) = judged.of_type("request")
    prompt = request["body"]["messages"][0]["content"]
    assert "## The Scores" in prompt
    assert f"- prompt_adherence: {CANCEL_VERDICT}, from judge. Evidence: " in prompt
    assert "[retrieval-1] tool/call lookup_order" in prompt


def test_a_failed_diagnosis_leaves_an_error_and_a_note_saying_none_was_produced(
    tmp_path: Path,
) -> None:
    judged = diagnose("diagnosis-schema-failure", tmp_path)

    assert judged.scores == []
    (error,) = judged.of_type("error")
    (note,) = judged.of_type("note")
    assert error["span_id"] == note["span_id"] == "judge-1"
    assert note["text"].startswith("No Diagnosis was produced:")
    assert note["cites"] == [] and note["sections"] == []


def test_diagnosis_citations_and_sections_not_in_the_trial_are_dropped_and_named(
    tmp_path: Path,
) -> None:
    """Rule 2, for the Diagnosis as for a Score: a Span id or a prompt section number the
    Trial does not have is dropped, and the text says so."""
    exchange = json.loads((RECORDINGS / "diagnosis-ok.jsonl").read_text())
    answer = answer_in(exchange["response"])
    answer.update(cites=["retrieval-1", "llm_call-9"], sections=[1, 9])
    with_answer(exchange["response"], answer)
    recording = tmp_path / "diagnosis-phantoms.jsonl"
    recording.write_text(json.dumps(exchange) + "\n", encoding="utf-8")
    scores = cancel_scores(tmp_path)
    cursor = ReplayCursor(Recording.load(recording))
    writer = TraceWriter(tmp_path / "phantoms.judgement.jsonl")
    writer.start(trace_id="test/judged/1", scenario="test", run="test", trial=1)
    diagnosis.judge(
        context(CANCEL, "diagnosis"),
        Judge(ReplayModelClient(cursor), JUDGE_MODEL, None, backend=REPLAY),
        writer,
        scores=scores,
    )
    writer.end("completed")
    writer.close()

    (note,) = Judged([], tmp_path / "phantoms.judgement.jsonl", cursor).of_type("note")
    assert (note["cites"], note["sections"]) == (["retrieval-1"], [1])
    assert note["cites_read"] == [], "a bare id, kept or dropped, is never a cite read"
    assert "dropped cited Span ids that do not appear in the Trace: llm_call-9" in note["text"]
    assert (
        "dropped cited prompt section numbers that do not appear in the Trace: 9" in (note["text"])
    )


def test_diagnosis_cites_that_are_rendered_trace_lines_are_read_as_their_span_ids(
    tmp_path: Path,
) -> None:
    """As for a Score (seen live 2026-09-24): a cite that begins with a Span id the Trace
    has, bracketed or as the whole rendered line, is that Span, and the text says so."""
    exchange = json.loads((RECORDINGS / "diagnosis-ok.jsonl").read_text())
    answer = answer_in(exchange["response"])
    answer.update(
        cites=['[retrieval-1] tool/call lookup_order {"order_id":"NB-1042"}', "[tool_call-1]"]
    )
    with_answer(exchange["response"], answer)
    recording = tmp_path / "diagnosis-rendered-lines.jsonl"
    recording.write_text(json.dumps(exchange) + "\n", encoding="utf-8")
    scores = cancel_scores(tmp_path)
    cursor = ReplayCursor(Recording.load(recording))
    writer = TraceWriter(tmp_path / "rendered-lines.judgement.jsonl")
    writer.start(trace_id="test/judged/1", scenario="test", run="test", trial=1)
    diagnosis.judge(
        context(CANCEL, "diagnosis"),
        Judge(ReplayModelClient(cursor), JUDGE_MODEL, None, backend=REPLAY),
        writer,
        scores=scores,
    )
    writer.end("completed")
    writer.close()

    (note,) = Judged([], tmp_path / "rendered-lines.judgement.jsonl", cursor).of_type("note")
    assert note["cites"] == ["retrieval-1", "tool_call-1"]
    assert note["cites_read"] == [
        '[retrieval-1] tool/call lookup_order {"order_id":"NB-1042"}',
        "[tool_call-1]",
    ]
    assert (
        "agentdiag read 2 cites as the Span ids they begin with: retrieval-1, tool_call-1."
        in note["text"]
    )


# --- the prompt on record holds every word the Judge reads (ADR-0003 §8, ADR-0005 §3) ---


def leaves(value: Any) -> list[str]:
    """Every piece of Trial data a slot was filled with."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in leaves(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in leaves(item)]
    return []


FIXED_WORDING = [
    ("judge-pass", CANCEL, "prompt_adherence", {}),
    ("goal-pass", GOAL, "goal", {}),
    ("guardrails-pass", GUARDRAILS, "guardrails", {"rules": "rules"}),
    ("data_grounding-pass", GROUNDING, "data_grounding", {}),
    ("data_query-pass", QUERY, "data_query", {}),
    ("tool_choice-pass", CHOICE, "tool_choice", {}),
    ("diagnosis-ok", CANCEL, "diagnosis", {"scores": "scores"}),
]


@pytest.mark.parametrize(("recording", "scenario", "eval_name", "_extra"), FIXED_WORDING)
def test_every_fixed_word_of_a_recorded_request_is_in_the_prompt_run_json_records(
    recording: str, scenario: str, eval_name: str, _extra: dict[str, str], tmp_path: Path
) -> None:
    """Split the request the Judge was sent at every piece of Trial data; whatever is left
    is wording, and every line of it must be in the template `run.json` records, or a
    rewording would change every request with no change on record."""
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs"))
    ran = CliRunner().invoke(
        app, ["run", "--root", str(root), "--scenario", scenario, "--replay", str(SUITE_RECORDING)]
    )
    (run_dir,) = (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir()
    assert ran.exit_code in {0, 1}, ran.stdout
    template = json.loads((run_dir / "run.json").read_text())["judge"]["prompts"][eval_name]["text"]
    exchange = json.loads((RECORDINGS / f"{recording}.jsonl").read_text().splitlines()[0])
    sent = exchange["request"]["messages"][0]["content"]

    data = leaves(trial_values(context(scenario, eval_name)))
    judgement = resolve_blobs(read_trace(run_dir / "trials" / scenario / "1" / "judgement.jsonl"))
    if eval_name in {"guardrails", "diagnosis"}:
        # What the Eval's own section was filled with: the rules, or the Trial's Scores.
        planned, _, _ = scenario_of(scenario)
        data += leaves(planned.model_dump(mode="json")["evals"])
        data += leaves(
            json.loads((run_dir / "trials" / scenario / "1" / "scores.json").read_text())
        )
    assert judgement
    fragments = [sent]
    for piece in sorted({piece for piece in data if piece}, key=len, reverse=True):
        fragments = [part for fragment in fragments for part in fragment.split(piece)]
    wording = [fragment for fragment in fragments if fragment.strip()]

    # The template's wording, its slots and block markers taken out: a request line that
    # is not in it is wording the record does not hold.
    recorded = TOKEN.sub("", template)
    assert wording
    for fragment in wording:
        for line in fragment.splitlines():
            assert line in recorded, line


# --- every recording is a shape the API could return, and every request is stable ---


RECORDED = sorted(
    path.stem
    for path in RECORDINGS.glob("*.jsonl")
    if path.stem.split("-")[0] in {*MODULES, "judge", "diagnosis"}
)


@pytest.mark.parametrize("name", RECORDED)
def test_a_worked_judge_response_is_a_shape_the_api_could_return(name: str) -> None:
    for line in (RECORDINGS / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
        message = Message.model_validate(json.loads(line)["response"])
        assert message.model == JUDGE_MODEL
        assert message.id


SAME_REQUEST = [
    ("goal-pass", GOAL, "goal"),
    ("guardrails-pass", GUARDRAILS, "guardrails"),
    ("data_grounding-pass", GROUNDING, "data_grounding"),
    ("data_query-pass", QUERY, "data_query"),
    ("tool_choice-pass", CHOICE, "tool_choice"),
    ("judge-pass", CANCEL, "prompt_adherence"),
]


@pytest.mark.parametrize(("recording", "scenario", "eval_name"), SAME_REQUEST)
def test_two_judgements_of_one_trace_send_the_same_request_body(
    recording: str, scenario: str, eval_name: str, tmp_path: Path
) -> None:
    """The head is a pure function of the Events, the Scenario, the notes and the parts:
    two Runs of one replayed Trial render the recording's key identically."""
    first = perform(recording, context(scenario, eval_name), tmp_path / "first")
    second = perform(recording, context(scenario, eval_name), tmp_path / "second")

    assert first.of_type("request")[0]["body"] == second.of_type("request")[0]["body"]
    first.cursor.assert_consumed()


@pytest.fixture(autouse=True)
def _directories(tmp_path: Path) -> None:
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()


# --- the Judge Fingerprint covers the Backend and the CLI version (ticket 19, decision 25) ---


class FixedParts:
    """The two things of a prompt's parts the Fingerprint reads, fixed, so its literal
    pins the hash's layout rather than today's prompt wording."""

    version = "v1"

    def template(self) -> str:
        return "TEMPLATE"


def fingerprint(backend: Backend | None) -> str:
    return judge_fingerprint(FixedParts(), None, "claude-opus-5", "high", backend)  # type: ignore[arg-type]


def test_the_judge_fingerprint_joins_the_backend_and_cli_version_after_the_effort() -> None:
    """sha256 of `v1␀TEMPLATE␀␀claude-opus-5␀high␀claude_code␀2.1.280`."""
    assert fingerprint(Backend(kind="claude_code", cli_version="2.1.280")) == (
        "8a3e15e494a516eedf0bd563e201e57345ce8d7409dffeb3863e14bac2ee2f58"
    )


def test_another_cli_version_or_another_backend_is_another_fingerprint() -> None:
    assert {
        "2.1.281": fingerprint(Backend(kind="claude_code", cli_version="2.1.281")),
        "anthropic_api": fingerprint(Backend(kind="anthropic_api")),
        "replay": fingerprint(Backend(kind="replay")),
    } == {
        "2.1.281": "b3e8bd506fc8ae36f26a5a4a4f20827dfdb25c5b7337f0ce1db2dfe896eb216d",
        "anthropic_api": "02ed3c8856b37cb61e66328aaba77ef69db4c7724a9d8b0318d1d0beb788fae8",
        "replay": "c5419b79da0ae87d9077105d8a9af3fcf3cfd1d15555bc1a17974fc7f162a676",
    }


def test_the_judge_computes_its_own_fingerprint_from_its_model_effort_and_backend() -> None:
    """`Judge.fingerprint(parts, notes)`: the one place the Judge's own three facts join the
    prompt's, which `JudgedScores` and the Diagnosis both call."""
    judge = Judge(
        ReplayModelClient(ReplayCursor(Recording(path=Path("unused.jsonl"), exchanges=()))),
        "claude-opus-5",
        "high",
        backend=Backend(kind="claude_code", cli_version="2.1.280"),
    )

    assert judge.fingerprint(FixedParts(), None) == (  # type: ignore[arg-type]
        "8a3e15e494a516eedf0bd563e201e57345ce8d7409dffeb3863e14bac2ee2f58"
    )


def test_every_judged_score_carries_its_judges_backend_in_its_fingerprint(tmp_path: Path) -> None:
    def goal_under(backend: Backend | None, number: int) -> str | None:
        cursor = ReplayCursor(Recording.load(RECORDINGS / "goal-pass.jsonl"))
        judge = Judge(ReplayModelClient(cursor), JUDGE_MODEL, backend=backend)
        writer = TraceWriter(tmp_path / f"judgement-{number}.jsonl")
        writer.start(trace_id="test/judged/1", scenario="test", run="test", trial=1)
        (score,) = judge_function("goal")(context(GOAL, "goal"), judge, writer)
        writer.end("completed")
        writer.close()
        return score.source.judge_fingerprint

    replayed = goal_under(Backend(kind="replay"), 1)
    under_claude_code = goal_under(Backend(kind="claude_code", cli_version="2.1.280"), 2)

    assert replayed is not None and under_claude_code is not None
    assert replayed != under_claude_code
