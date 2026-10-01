"""Seam 2: what the Judge produces for each way a Judge can answer (D13, ADR-0003 §3, §4).

Every test runs the real Judge — `prompt_adherence`'s module over `Judge.ask` — over the
committed Trace fixture through a recording that supplies one Judge response. The four
answer rules live in `Judge.ask` and the Score builder every judged Eval shares, so they
are asserted here once, in full, and per Eval in `tests/test_judged_evals.py`. What varies
between them is only what the Judge said, which is exactly the axis ADR-0003 §3 closes: a
Verdict is a judgement about the Target, and every way the instrument itself can fail has
its own Verdict rather than being folded into the Target's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentdiag.eval import judged, prompt_adherence
from agentdiag.eval.judge import Judge
from agentdiag.eval.judged_score import cited_spans, read_as_text
from agentdiag.eval.prompt_adherence import PROMPT_VERSION
from agentdiag.eval.render import JudgeContext
from agentdiag.eval.score import Score
from agentdiag.model.claude_code import Backend
from agentdiag.model.client import ModelRequest, ModelResponse, ReplayModelClient
from agentdiag.model.replay import Recording, ReplayCursor
from agentdiag.scenario.load import load_suite
from agentdiag.scenario.models import Scenario
from agentdiag.trace import Event, TraceWriter, read_trace, resolve_blobs
from tests.stories import CANCEL_BROKEN_AT, CANCEL_VERDICT

REPO = Path(__file__).resolve().parents[1]
RECORDINGS = REPO / "tests" / "fixtures" / "recordings"
TRACE_FIXTURE = REPO / "tests" / "fixtures" / "traces" / "cancel-processing-order.trace.jsonl"
SUITE = (
    REPO
    / "examples"
    / "toy"
    / ".agentdiag"
    / "targets"
    / "toy-order-desk"
    / "suites"
    / "orders.yaml"
)

JUDGE_MODEL = "claude-opus-5"
TRACE_ID = "test/cancel-processing-order/1"
"""The same identity the Trace carries, so the two files join without a third thing."""

REPLAY = Backend(kind="replay")
CLAUDE_CODE = Backend(kind="claude_code", cli_version="2.1.280")

TARGET_MODEL = "claude-sonnet-5-20260815"
"""What the Trace fixture's `llm_call` Spans resolved to — not the Judge's model (D23)."""


@pytest.fixture
def scenario() -> Scenario:
    suite, _ = load_suite(SUITE)
    return suite.scenarios[0]


@pytest.fixture
def events() -> list[Event]:
    return read_trace(TRACE_FIXTURE)


def adherence(
    judge: Judge,
    scenario: Scenario,
    events: list[Event],
    fidelity: str,
    judgement_path: Path,
) -> Score:
    """`prompt_adherence` over one Trace, writing into one `judgement.jsonl`."""
    judgement = TraceWriter(judgement_path)
    judgement.start(trace_id=TRACE_ID, scenario=scenario.id, run="test", trial=1)
    context = JudgeContext.of(
        scenario,
        scenario.evals[0],
        events,
        fidelity=fidelity,  # type: ignore[arg-type]
        notes=None,
        tool_kinds={"lookup_order": "retrieval", "cancel_order": "action"},
    )
    try:
        (score,) = prompt_adherence.judge(context, judge, judgement)
        return score
    finally:
        judgement.end("completed")
        judgement.close()


def judge_over(
    recording: str | Path,
    scenario: Scenario,
    events: list[Event],
    judgement_path: Path,
) -> Score:
    """The Judge, answered by one recording (a committed one by name, or a path), writing
    into one `judgement.jsonl`."""
    path = recording if isinstance(recording, Path) else RECORDINGS / recording
    cursor = ReplayCursor(Recording.load(path))
    judge = Judge(ReplayModelClient(cursor), JUDGE_MODEL, None, backend=REPLAY)
    return adherence(judge, scenario, events, "instrumented", judgement_path)


def recorded(recording: str) -> dict:
    """A committed recording's one exchange."""
    return json.loads((RECORDINGS / recording).read_text(encoding="utf-8").splitlines()[0])


def recorded_answer(recording: str) -> dict:
    """What the recorded Judge answered: the Claude Code backend's `structured_output`, or
    the JSON of the Messages API's text block. A test over a natural recording, captured
    from the Judge (ticket 21, decision 41), reads its story from here rather than from any
    authored wording."""
    response = recorded(recording)["response"]
    if response.get("structured_output") is not None:
        return response["structured_output"]
    return json.loads("".join(b["text"] for b in response["content"] if b["type"] == "text"))


def span_ids(events: list[Event]) -> set[str]:
    return {str(event.span_id) for event in events if event.type == "span/start"}


def judgement_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def strip_system_prompt(events: list[Event]) -> list[Event]:
    """The same Trace with nothing that could stand in for the Target's prompt."""
    kept: list[Event] = []
    for event in events:
        if event.type == "blob":
            continue
        if event.type == "request":
            body = dict((event.model_extra or {}).get("body") or {})
            body.pop("system", None)
            event = event.model_copy(update={"body": body})
        kept.append(event)
    return kept


# --- a judged pass that cites its evidence is taken as given ---


def test_a_judged_pass_whose_cited_spans_exist_is_recorded_as_a_pass(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    """Over `judge-pass.jsonl`, a worked recording since decision 43: its wording is the
    fixture, so the evidence is asserted as authored."""
    score = judge_over("judge-pass.jsonl", scenario, events, tmp_path / "judgement.jsonl")

    assert score.verdict == "pass"
    assert score.eval == "prompt_adherence"
    assert score.evidence == [
        "llm_call-1",
        "retrieval-1",
        "llm_call-2",
        "tool_call-1",
        "llm_call-3",
    ]
    assert score.rationale.startswith("The Target called lookup_order before saying anything")
    assert score.cites_read == []


def test_a_judged_score_names_the_model_asked_for_and_the_one_the_api_resolved(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    score = judge_over("judge-pass.jsonl", scenario, events, tmp_path / "judgement.jsonl")

    assert score.source.kind == "judge"
    assert score.source.requested_model == JUDGE_MODEL
    assert score.source.resolved_model == recorded("judge-pass.jsonl")["response"]["model"]
    assert score.source.prompt_version == PROMPT_VERSION


def test_a_judged_score_records_the_fidelity_that_grounded_it(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    score = judge_over("judge-pass.jsonl", scenario, events, tmp_path / "judgement.jsonl")

    assert score.fidelity == "instrumented"


def test_a_target_on_a_different_model_from_the_judge_is_not_a_shared_model(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    """D23: the shipped example must not trip the self-preference warning on its first Run."""
    score = judge_over("judge-pass.jsonl", scenario, events, tmp_path / "judgement.jsonl")

    assert score.shared_model is False


def test_a_target_the_judge_shares_a_resolved_model_with_is_flagged(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    same = [
        event.model_copy(
            update={
                "attributes": {
                    **((event.model_extra or {}).get("attributes") or {}),
                    "gen_ai.response.model": recorded("judge-pass.jsonl")["response"]["model"],
                }
            }
        )
        if event.type == "span/end"
        and (event.model_extra or {}).get("attributes", {}).get("gen_ai.response.model")
        else event
        for event in events
    ]

    score = judge_over("judge-pass.jsonl", scenario, same, tmp_path / "judgement.jsonl")

    assert score.shared_model is True


def test_the_rationale_says_which_rules_were_never_exercised(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    """A pass over four rules and a pass over five are different claims (D23): whatever
    rules the Judge said it exercised and did not are folded into the rationale."""
    score = judge_over("judge-pass.jsonl", scenario, events, tmp_path / "judgement.jsonl")
    answer = recorded_answer("judge-pass.jsonl")

    for label, key in (
        ("Rules exercised", "rules_exercised"),
        ("Rules not exercised", "rules_not_exercised"),
    ):
        if answer[key]:
            numbers = ", ".join(str(number) for number in answer[key])
            assert f"{label}: {numbers}." in score.rationale
        else:
            assert f"{label}:" not in score.rationale


def test_a_judged_fail_whose_cited_spans_exist_is_recorded_as_a_fail(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    """One broken rule is a `fail`, and the Span that shows it is among the evidence. Over
    `judge-fail.jsonl`, the cancel Trace's natural story (decision 43): the story is
    asserted, never the captured wording. The Judge cited bare Span ids, so none was read."""
    score = judge_over("judge-fail.jsonl", scenario, events, tmp_path / "judgement.jsonl")

    assert score.verdict == CANCEL_VERDICT
    assert CANCEL_BROKEN_AT in score.evidence
    assert set(score.evidence) <= span_ids(events)
    assert score.rationale.strip()
    assert score.cites_read == []
    assert score.model_dump(mode="json")["cites_read"] == []


def test_a_judged_fail_carries_no_reason_unless_the_judge_named_a_target_side_one(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    """ADR-0003 §3 allows a `fail` a reason from (`refusal`, `malformed_output`) and
    nothing else; this Judge named none, so none is recorded."""
    score = judge_over("judge-fail.jsonl", scenario, events, tmp_path / "judgement.jsonl")

    assert score.reason is None


# --- a judgement that cannot point at the Trace is unverifiable (ADR-0003 §4) ---


def test_a_judged_pass_that_cites_nothing_is_recorded_as_unverifiable(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    score = judge_over("judge-no-evidence.jsonl", scenario, events, tmp_path / "judgement.jsonl")

    assert score.verdict == "unverifiable"
    assert score.reason == "evidence_missing"
    assert score.evidence == []


def test_a_judged_fail_citing_only_spans_that_are_not_in_the_trace_is_unverifiable(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    score = judge_over(
        "judge-unknown-evidence.jsonl", scenario, events, tmp_path / "judgement.jsonl"
    )

    assert score.verdict == "unverifiable"
    assert score.reason == "evidence_missing"
    # Named rather than silently dropped: a reader must be able to see what was claimed.
    assert "llm_call-9" in score.rationale


def with_evidence(recording: str, evidence: list[str], tmp_path: Path) -> Path:
    """The committed recording's one exchange with its answer's `evidence` replaced: the same
    request, so the replay matches, and a Judge that cites the way this one does."""
    exchange = json.loads((RECORDINGS / recording).read_text(encoding="utf-8"))
    answer = json.loads(exchange["response"]["content"][0]["text"])
    answer["evidence"] = evidence
    exchange["response"]["content"][0]["text"] = json.dumps(answer)
    path = tmp_path / "with-evidence.jsonl"
    path.write_text(json.dumps(exchange) + "\n", encoding="utf-8")
    return path


def test_a_cite_that_is_a_rendered_trace_line_is_read_as_the_span_id_it_begins_with(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    """Seen live on 2026-09-24, through Claude Code: the Judge cited three whole Trace lines
    (`[llm_call-1] assistant tool_use …`) where the schema asked for ids. They are read as
    the ids they begin with and the rationale says so, rather than dropped as unknown and
    the pass made `unverifiable`."""
    rendered = [
        '[llm_call-1] assistant tool_use lookup_order {"order_id":"NB-1042"}',
        '[retrieval-1] tool/result {"order_id":"NB-1042","status":"processing"}',
        "[llm_call-3]",
    ]
    recording = with_evidence("judge-pass.jsonl", [*rendered, "llm_call-2"], tmp_path)

    score = judge_over(recording, scenario, events, tmp_path / "judgement.jsonl")

    assert score.verdict == "pass"
    assert score.evidence == ["llm_call-1", "retrieval-1", "llm_call-3", "llm_call-2"]
    assert (
        "agentdiag read 3 cites as the Span ids they begin with: llm_call-1, retrieval-1, "
        "llm_call-3." in score.rationale
    )
    assert "dropped" not in score.rationale
    # Counted, not only said (ticket 21, decision 39): the cites as the Judge wrote them.
    assert score.cites_read == rendered


def test_a_score_whose_cites_were_all_bare_span_ids_read_none() -> None:
    """The case the schema guarantees (decision 38): nothing for the fallback to count."""
    cited, dropped, read = cited_spans(["llm_call-3", "retrieval-1"], ["llm_call-3", "retrieval-1"])

    assert (cited, dropped, read) == (["llm_call-3", "retrieval-1"], [], [])


def test_a_cite_that_begins_with_no_span_of_the_trace_is_still_dropped_and_named() -> None:
    """Only a Span the Trace has can be read out of a cite: an order id shaped like one, a
    line about a Span that does not exist, or prose are dropped and named as before."""
    cited, dropped, read = cited_spans(
        ["[llm_call-9] assistant text: hello", "the lookup", "NB-1042", "llm_call-1"],
        ["llm_call-1", "retrieval-1"],
    )

    assert (cited, read) == (["llm_call-1"], [])
    assert dropped == ["[llm_call-9] assistant text: hello", "the lookup", "NB-1042"]


def test_each_cite_read_is_paired_with_the_span_id_read_out_of_it() -> None:
    """Decision 39: `cited_spans` hands back each decorated cite as written beside its id."""
    cited, dropped, read = cited_spans(
        ["[llm_call-1] assistant text: hello", "[retrieval-1]", "llm_call-1"],
        ["llm_call-1", "retrieval-1"],
    )

    assert (cited, dropped) == (["llm_call-1", "retrieval-1"], [])
    assert read == [
        ("[llm_call-1] assistant text: hello", "llm_call-1"),
        ("[retrieval-1]", "retrieval-1"),
    ]


def test_the_rationale_counts_the_cites_read_and_names_each_id_once_with_how_many() -> None:
    """The count and the list agree: two cites read as one id say so, rather than "2 cites
    … : llm_call-2". `Score.cites_read` keeps every cite as written."""
    assert read_as_text([("[llm_call-2]", "llm_call-2")]) == (
        "\n\nagentdiag read 1 cite as the Span id it begins with: llm_call-2."
    )
    assert read_as_text(
        [("[llm_call-2]", "llm_call-2"), ("[llm_call-2] assistant text: hi", "llm_call-2")]
    ) == ("\n\nagentdiag read 2 cites as the Span id they begin with: llm_call-2 (2 cites).")
    assert read_as_text(
        [
            ("[llm_call-2]", "llm_call-2"),
            ("[retrieval-1]", "retrieval-1"),
            ("[llm_call-2] assistant text: hi", "llm_call-2"),
        ]
    ) == (
        "\n\nagentdiag read 3 cites as the Span ids they begin with: "
        "llm_call-2 (2 cites), retrieval-1."
    )
    assert read_as_text([]) == ""


# --- everything the instrument can get wrong is `invalid`, the Judge's fault (D13) ---


def test_a_judge_output_that_does_not_fit_the_schema_is_invalid_and_the_judges_fault(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    score = judge_over("judge-schema-failure.jsonl", scenario, events, tmp_path / "judgement.jsonl")

    assert score.verdict == "invalid"
    assert score.fault_source == "judge"
    assert score.fault_direction == "none"


def test_a_judge_output_that_does_not_fit_the_schema_leaves_an_error_event(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    path = tmp_path / "judgement.jsonl"
    judge_over("judge-schema-failure.jsonl", scenario, events, path)

    errors = [event for event in judgement_events(path) if event["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["actor"] == "judge"


def test_a_judge_that_refused_is_invalid_and_never_retried(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    score = judge_over("judge-refusal.jsonl", scenario, events, tmp_path / "judgement.jsonl")

    assert score.verdict == "invalid"
    assert score.fault_source == "judge"
    assert "refused" in score.rationale
    # The category is what tells an author which part of the prompt to look at.
    assert "reasoning_extraction" in score.rationale


def test_a_refusal_and_a_token_cut_off_are_told_apart_in_the_error_event(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    """D13 lists them as separate causes: one wants the prompt looked at, the other a
    bigger budget, and an `error` Event that said only "stopped" would help with neither."""
    path = tmp_path / "judgement.jsonl"
    judge_over("judge-refusal.jsonl", scenario, events, path)

    error = next(event for event in judgement_events(path) if event["type"] == "error")
    assert error["error_type"] == "JudgeRefused"
    assert "refused" in error["message"]


def test_a_judge_cut_off_by_max_tokens_says_so_rather_than_claiming_a_refusal(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    path = tmp_path / "judgement.jsonl"
    recording = tmp_path / "cut-off.jsonl"
    exchange = recorded("judge-pass.jsonl")
    exchange["response"]["stop_reason"] = "max_tokens"
    exchange["response"]["content"] = [{"type": "text", "text": '{"verdict": "pa'}]
    recording.write_text(json.dumps(exchange) + "\n", encoding="utf-8")

    judge = Judge(
        ReplayModelClient(ReplayCursor(Recording.load(recording))),
        JUDGE_MODEL,
        None,
        backend=REPLAY,
    )
    score = adherence(judge, scenario, events, "instrumented", path)

    assert score.verdict == "invalid"
    assert score.fault_source == "judge"
    error = next(event for event in judgement_events(path) if event["type"] == "error")
    assert error["error_type"] == "JudgeRanOutOfTokens"
    assert "ran out of tokens" in error["message"]


def test_a_replay_that_does_not_match_inside_the_judge_is_the_judges_fault(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    """A recording agentdiag cannot satisfy broke the judgement, not the Target's Trial."""
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    cursor = ReplayCursor(Recording.load(empty))
    judge = Judge(ReplayModelClient(cursor), JUDGE_MODEL, None, backend=REPLAY)
    score = adherence(judge, scenario, events, "instrumented", tmp_path / "judgement.jsonl")

    assert score.verdict == "invalid"
    assert score.fault_source == "judge"


# --- no prompt to judge against: unverifiable, and no model was called ---


def test_a_trace_with_no_system_prompt_is_unverifiable_without_calling_a_model(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    """D21: `prompt_adherence` needs the prompt text, and a guess would be worse than none."""
    empty = tmp_path / "no-exchanges.jsonl"
    empty.write_text("", encoding="utf-8")
    cursor = ReplayCursor(Recording.load(empty))
    judge = Judge(ReplayModelClient(cursor), JUDGE_MODEL, None, backend=REPLAY)

    score = adherence(
        judge, scenario, strip_system_prompt(events), "observed", tmp_path / "judgement.jsonl"
    )

    assert score.verdict == "unverifiable"
    assert score.reason == "evidence_missing"
    # No exchange was taken, so an empty recording was enough: nothing called a model.
    cursor.assert_consumed()


def test_a_trace_with_no_system_prompt_says_why_in_a_note(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    path = tmp_path / "judgement.jsonl"
    empty = tmp_path / "no-exchanges.jsonl"
    empty.write_text("", encoding="utf-8")
    judge = Judge(
        ReplayModelClient(ReplayCursor(Recording.load(empty))), JUDGE_MODEL, None, backend=REPLAY
    )
    adherence(judge, scenario, strip_system_prompt(events), "observed", path)

    notes = [event for event in judgement_events(path) if event["type"] == "note"]
    assert len(notes) == 1
    assert "system prompt" in notes[0]["text"]


# --- `judgement.jsonl` holds what the Judge did, in the Trace's own Event format ---


def test_the_judgement_file_holds_the_judges_span_with_its_request_and_response(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    path = tmp_path / "judgement.jsonl"
    judge_over("judge-fail.jsonl", scenario, events, path)

    events_written = judgement_events(path)
    # A rendered prompt, and a captured answer (decision 41), may each be longer than the
    # blob threshold, so the writer may store either once as a `blob` its Event references
    # (D10) — the same rule the Trace follows. The sequence is asserted with the blobs set
    # aside, and `resolve_blobs` must put every body back whole.
    assert [event["type"] for event in events_written if event["type"] != "blob"] == [
        "trace/start",
        "span/start",
        "request",
        "response",
        "span/end",
        "trace/end",
    ]
    resolved = resolve_blobs(read_trace(path))
    request = next(event for event in resolved if event.type == "request")
    response = next(event for event in resolved if event.type == "response")
    assert recorded("judge-fail.jsonl")["request"] == (request.model_extra or {})["body"]
    assert recorded("judge-fail.jsonl")["response"] == (response.model_extra or {})["body"]


def test_every_event_in_the_judgement_file_is_the_judges(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    """ADR-0004 §3: the Judge writes its own file, and the Trace is untouched afterwards."""
    path = tmp_path / "judgement.jsonl"
    judge_over("judge-pass.jsonl", scenario, events, path)

    actors = {
        event["actor"]
        for event in judgement_events(path)
        if event["type"] not in {"trace/start", "trace/end"}
    }
    assert actors == {"judge"}


def test_the_judges_span_is_a_judge_span_naming_the_eval_it_performed(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    path = tmp_path / "judgement.jsonl"
    judge_over("judge-pass.jsonl", scenario, events, path)

    start = next(event for event in judgement_events(path) if event["type"] == "span/start")
    assert start["kind"] == "judge"
    assert start["span_id"] == "judge-1"
    assert start["name"] == "judge prompt_adherence"
    assert start["fidelity"] == "instrumented"
    assert start["attributes"]["agentdiag.eval"] == "prompt_adherence"


def test_the_recorded_judge_request_asks_for_structured_output_and_no_sampling(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    """D13: the Verdict is typed at the boundary; the Claude 5 models reject `temperature`."""
    path = tmp_path / "judgement.jsonl"
    judge_over("judge-pass.jsonl", scenario, events, path)

    request = next(event for event in judgement_events(path) if event["type"] == "request")
    assert request["body"]["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in request["body"]


def test_the_judgement_file_carries_the_same_trace_id_as_the_trace_it_judged(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    path = tmp_path / "judgement.jsonl"
    judge_over("judge-pass.jsonl", scenario, events, path)

    start = next(event for event in judgement_events(path) if event["type"] == "trace/start")
    assert start["trace_id"] == TRACE_ID


def test_a_judge_effort_outside_the_closed_set_is_refused_when_the_judge_is_built() -> None:
    """A `--judge-effort` typo is a message at construction, not a 400 mid-Run."""
    empty = ReplayModelClient(ReplayCursor(Recording(path=Path("none"), exchanges=())))

    with pytest.raises(ValueError) as raised:
        Judge(empty, JUDGE_MODEL, "very-high", backend=REPLAY)

    assert "very-high" in str(raised.value)


def test_every_documented_effort_level_is_accepted() -> None:
    empty = ReplayModelClient(ReplayCursor(Recording(path=Path("none"), exchanges=())))

    for level in ("low", "medium", "high", "xhigh", "max"):
        assert Judge(empty, JUDGE_MODEL, level, backend=REPLAY).effort == level


# --- the Judge configuration `run.json` freezes ---


def test_the_judge_configuration_records_each_evals_prompt_with_its_fingerprint() -> None:
    configuration = judged.configuration(["prompt_adherence"], JUDGE_MODEL, "high")

    prompt = configuration.prompts["prompt_adherence"]
    assert configuration.model == JUDGE_MODEL
    assert configuration.effort == "high"
    assert prompt.version == PROMPT_VERSION
    assert prompt.fingerprint == prompt_adherence.PARTS.fingerprint()
    assert prompt.text.startswith("You are the Judge in agentdiag")
    assert set(configuration.prompts) == {"prompt_adherence", "diagnosis"}


# --- the Judge request is byte-stable, or replay would stop matching between Runs ---


def test_two_judgements_of_the_same_trace_send_the_same_request_body(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    """Nothing in the rendering may vary between Runs: no timestamps, no run id."""
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    judge_over("judge-pass.jsonl", scenario, events, first)
    judge_over("judge-pass.jsonl", scenario, events, second)

    bodies = [
        next(event for event in judgement_events(path) if event["type"] == "request")["body"]
        for path in (first, second)
    ]
    assert bodies[0] == bodies[1]


# --- what a judgement cost is written on its Span at capture (phase-5 decision 4) ---


def test_the_judges_span_records_what_its_call_cost_under_the_price_table(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    """claude-opus-5 at $5 in and $25 out per million: 3184 in and 211 out cost $0.021195.
    Over `judge-pass.jsonl`, a worked recording, whose usage is authored."""
    path = tmp_path / "judgement.jsonl"
    judge_over("judge-pass.jsonl", scenario, events, path)

    end = next(e for e in judgement_events(path) if e["type"] == "span/end")
    assert end["attributes"]["llm.cost.total"] == 0.021195
    assert end["attributes"]["llm.cost.prompt"] == 0.01592
    assert end["attributes"]["llm.cost.completion"] == 0.005275


# --- the Claude Code backend: the answer in `structured_output`, the reported cost checked ---


class Answering:
    """A client that answers every request with one body, the way `ClaudeCodeClient`
    reassembles the CLI's report (ticket 19, decision 23)."""

    def __init__(self, body: dict) -> None:
        self.body = body

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse.from_body(request, self.body)


PASS_ANSWER = {
    "verdict": "pass",
    "reason": "none",
    "rationale": "The Target looked the order up, cancelled it as asked, and invented nothing.",
    "evidence": ["llm_call-1", "retrieval-1", "llm_call-2", "tool_call-1", "llm_call-3"],
    "rules_exercised": [1, 2, 3, 4],
    "rules_not_exercised": [5],
}
"""A pass authored here, for the tests of how the Claude Code body is read: they are about
the reading, not about what any recorded Judge said."""


def claude_code_body(model: str = JUDGE_MODEL, reported: float | None = 0.0212) -> dict:
    """A pass as the CLI delivers it: a `StructuredOutput` tool call, no text, and the
    answer in `structured_output`."""
    answer = PASS_ANSWER
    return {
        "id": "msg_cc_01",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [
            {"type": "tool_use", "id": "toolu_01", "name": "StructuredOutput", "input": answer}
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 3184, "output_tokens": 211},
        "structured_output": answer,
        "claude_code": {"total_cost_usd": reported, "cli_version": "2.1.280"},
    }


def judge_end(body: dict, scenario: Scenario, events: list[Event], path: Path) -> dict:
    adherence(
        Judge(Answering(body), JUDGE_MODEL, None, backend=CLAUDE_CODE),
        scenario,
        events,
        "instrumented",
        path,
    )
    return next(e for e in judgement_events(path) if e["type"] == "span/end")


def test_an_answer_only_in_the_structured_output_is_read_as_the_judgement(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    judge = Judge(Answering(claude_code_body()), JUDGE_MODEL, None, backend=CLAUDE_CODE)

    score = adherence(judge, scenario, events, "instrumented", tmp_path / "judgement.jsonl")

    assert score.verdict == "pass"
    assert score.evidence == [
        "llm_call-1",
        "retrieval-1",
        "llm_call-2",
        "tool_call-1",
        "llm_call-3",
    ]


def test_a_structured_output_that_does_not_fit_the_schema_is_invalid(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    body = claude_code_body() | {"structured_output": {"verdict": "maybe"}}

    score = adherence(
        Judge(Answering(body), JUDGE_MODEL, None, backend=CLAUDE_CODE),
        scenario,
        events,
        "instrumented",
        tmp_path / "judgement.jsonl",
    )

    assert (score.verdict, score.fault_source) == ("invalid", "judge")


def test_a_reported_cost_that_agrees_with_the_table_is_recorded_beside_it(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    """claude-opus-5 prices this call at $0.021195; the CLI said $0.0212."""
    end = judge_end(claude_code_body(), scenario, events, tmp_path / "judgement.jsonl")

    assert end["attributes"]["llm.cost.total"] == 0.021195
    assert end["attributes"]["agentdiag.cost.reported_usd"] == 0.0212
    assert end["attributes"]["agentdiag.cost.check"] == "agrees"


def test_a_one_hour_cache_write_the_cli_reported_agrees_with_the_table(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    """The live `goal` call of 2026-09-23: the CLI wrote the prompt cache with the 1-hour
    lifetime and said $0.059755. The Judge passes the whole `usage`, split included, to the
    table, so the Span's own cost is the same figure and the check agrees."""
    body = claude_code_body(reported=0.059755) | {
        "usage": {
            "input_tokens": 2,
            "cache_creation_input_tokens": 3552,
            "cache_read_input_tokens": 0,
            "output_tokens": 969,
            "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 3552},
            "service_tier": "standard",
        }
    }

    end = judge_end(body, scenario, events, tmp_path / "judgement.jsonl")

    assert end["attributes"]["llm.cost.total"] == 0.059755
    assert end["attributes"]["llm.cost.prompt_details.cache_write"] == 0.03552
    assert end["attributes"]["gen_ai.usage.cache_write.input_tokens"] == 3552
    assert end["attributes"]["agentdiag.cost.check"] == "agrees"


def test_a_reported_cost_the_table_disagrees_with_is_marked_differs(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    end = judge_end(claude_code_body(reported=0.03), scenario, events, tmp_path / "j.jsonl")

    assert end["attributes"]["agentdiag.cost.reported_usd"] == 0.03
    assert end["attributes"]["agentdiag.cost.check"] == "differs"


def test_a_reported_cost_for_a_model_the_table_does_not_price_is_unpriced(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    body = claude_code_body(model="claude-mystery-1")

    end = judge_end(body, scenario, events, tmp_path / "judgement.jsonl")

    assert "llm.cost.total" not in end["attributes"]
    assert end["attributes"]["agentdiag.cost.reported_usd"] == 0.0212
    assert end["attributes"]["agentdiag.cost.check"] == "unpriced"


def test_a_backend_that_reports_no_cost_writes_no_cost_check(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    """Over `judge-pass.jsonl`, a worked Messages API body: no Backend report in it."""
    path = tmp_path / "judgement.jsonl"
    judge_over("judge-pass.jsonl", scenario, events, path)

    end = next(e for e in judgement_events(path) if e["type"] == "span/end")
    assert "agentdiag.cost.reported_usd" not in end["attributes"]
    assert "agentdiag.cost.check" not in end["attributes"]


# --- a call Claude Code retried once is counted on the Span (decision 42) ---


def with_attempts(count: int) -> dict:
    """`claude_code_body()` as `ClaudeCodeClient` reports a call the CLI retried: every
    earlier message id is an attempt with the CLI's rejection."""
    body = claude_code_body()
    rejected = [
        {"id": f"msg_try_{n}", "content": [], "rejected": "Output does not match"}
        for n in range(count - 1)
    ]
    return body | {"claude_code": {**body["claude_code"], "attempts": rejected}}


def test_a_call_claude_code_retried_carries_its_attempts_on_the_judge_span(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    end = judge_end(with_attempts(2), scenario, events, tmp_path / "judgement.jsonl")

    assert end["attributes"]["agentdiag.judge.attempts"] == 2


def test_a_call_answered_at_the_first_attempt_carries_no_attempts(
    scenario: Scenario, events: list[Event], tmp_path: Path
) -> None:
    end = judge_end(claude_code_body(), scenario, events, tmp_path / "judgement.jsonl")

    assert "agentdiag.judge.attempts" not in end["attributes"]
