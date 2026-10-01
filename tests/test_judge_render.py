"""What the Judge is shown, and what the committed recordings claim the API returned.

Two separate worries live here. The rendering must carry every Span id a Score may cite
and nothing that varies between Runs, because the Judge's request body is the key a
recording matches on. And each hand-authored response must be a shape the API could
actually produce, or a passing replay would prove nothing about the live path.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from anthropic.types import Message

from agentdiag.eval import diagnosis, prompt_adherence
from agentdiag.eval.judged import MODULES
from agentdiag.eval.judged_score import CITED_SPAN, cited_spans
from agentdiag.eval.prompt_adherence import (
    prompt_sections,
    render_sections,
    system_prompt_from,
)
from agentdiag.eval.render import (
    BASE_PROPERTIES,
    CITING,
    SPAN_ID,
    SPAN_ID_PATTERN,
    JudgeContext,
    render_judge_prompt,
    render_trace_for_judge,
)
from agentdiag.examples.toy.prompt import SYSTEM_PROMPT
from agentdiag.scenario.load import load_suite
from agentdiag.scenario.models import Scenario
from agentdiag.trace import Event, read_trace

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

JUDGE_RECORDINGS = [
    "judge-pass.jsonl",
    "judge-fail.jsonl",
    "judge-no-evidence.jsonl",
    "judge-unknown-evidence.jsonl",
    "judge-schema-failure.jsonl",
    "judge-refusal.jsonl",
]


def render_prompt(scenario: Scenario, events: list[Event]) -> str | None:
    """`prompt_adherence`'s whole prompt for a Trace, through the shared head."""
    context = JudgeContext.of(
        scenario, scenario.evals[0], events, fidelity="instrumented", notes=None, tool_kinds={}
    )
    return render_judge_prompt(prompt_adherence.PARTS, context)


# --- the prompt is split into the rules a Score can cite (D23) ---


def test_the_toy_targets_prompt_splits_into_a_preamble_and_its_five_numbered_rules() -> None:
    numbers = [number for number, _ in prompt_sections(SYSTEM_PROMPT)]

    assert numbers == [0, 1, 2, 3, 4, 5]


def test_the_preamble_keeps_the_text_before_the_first_numbered_rule() -> None:
    """A rule may depend on which tools exist, and that is stated in the preamble."""
    preamble = dict(prompt_sections(SYSTEM_PROMPT))[0]

    assert "lookup_order" in preamble
    assert "cancel_order" in preamble


def test_a_rule_written_with_a_parenthesis_opens_a_section_too() -> None:
    sections = prompt_sections("Do this.\n\n1) First rule.\n\n2) Second rule.")

    assert [number for number, _ in sections] == [0, 1, 2]


def test_a_prompt_with_no_numbered_rule_is_all_preamble() -> None:
    assert prompt_sections("Be helpful.") == [(0, "Be helpful.")]


def test_the_rendered_sections_label_each_rule_by_its_number() -> None:
    rendered = render_sections(SYSTEM_PROMPT)

    assert "Section 0 (preamble):" in rendered
    assert "Rule 1: Look up before you answer." in rendered
    assert "Rule 5:" in rendered


def test_the_targets_prompt_is_read_from_the_trace_rather_than_the_manifest() -> None:
    """D21: what the Target was actually sent is the only thing it can be judged against."""
    assert system_prompt_from(read_trace(TRACE_FIXTURE)) == SYSTEM_PROMPT


# --- the Trace rendering: every citable id present, nothing that varies (D23) ---


def test_every_span_the_judge_may_cite_appears_with_its_id() -> None:
    rendered = render_trace_for_judge(read_trace(TRACE_FIXTURE))

    for span_id in ("turn-1", "llm_call-1", "retrieval-1", "llm_call-2", "tool_call-1"):
        assert f"[{span_id}]" in rendered


def test_a_tools_real_result_is_shown_so_a_claim_about_it_can_be_checked() -> None:
    rendered = render_trace_for_judge(read_trace(TRACE_FIXTURE))

    assert '[retrieval-1] tool/result {"item":"Northwind Trailhead gravel bike' in rendered


def test_the_assistants_words_are_shown_in_full() -> None:
    rendered = render_trace_for_judge(read_trace(TRACE_FIXTURE))

    assert "I've cancelled it, and the charge will drop off within a few days." in rendered


def test_the_system_prompt_is_not_repeated_inside_the_trace_rendering() -> None:
    """It is in the prompt sections; a second copy invites a citation against the wrong one."""
    rendered = render_trace_for_judge(read_trace(TRACE_FIXTURE))

    assert "Follow these five rules on every single turn" not in rendered


def test_a_request_is_summarised_to_its_model_rather_than_dumped() -> None:
    rendered = render_trace_for_judge(read_trace(TRACE_FIXTURE))

    assert "[llm_call-1] request to model claude-sonnet-5" in rendered


def test_nothing_that_differs_between_two_runs_reaches_the_rendering() -> None:
    """No timestamps, no run id — or the replay that reproduces a Run would stop matching."""
    events = read_trace(TRACE_FIXTURE)
    rendered = render_trace_for_judge(events)

    assert "1758536100000" not in rendered
    assert "20260922T101500Z" not in rendered


def test_rendering_the_same_trace_twice_produces_the_same_text() -> None:
    events = read_trace(TRACE_FIXTURE)

    assert render_trace_for_judge(events) == render_trace_for_judge(events)


def test_the_prompt_fingerprint_is_stable_across_calls() -> None:
    fingerprint = prompt_adherence.PARTS.fingerprint()
    assert fingerprint == prompt_adherence.PARTS.fingerprint()
    assert len(fingerprint) == 64


def test_the_rendered_prompt_holds_the_scenario_the_sections_and_the_trace() -> None:
    suite, _ = load_suite(SUITE)
    prompt = render_prompt(suite.scenarios[0], read_trace(TRACE_FIXTURE))

    assert prompt is not None
    assert "- id: cancel-processing-order" in prompt
    assert "Rule 1: Look up before you answer." in prompt
    assert "[retrieval-1] tool/call lookup_order" in prompt


# --- the committed recordings claim only what the API could have returned ---


@pytest.mark.parametrize("name", JUDGE_RECORDINGS)
def test_a_recorded_judge_response_is_a_shape_the_api_could_return(name: str) -> None:
    exchange = json.loads((RECORDINGS / name).read_text(encoding="utf-8").splitlines()[0])

    message = Message.model_validate(exchange["response"])

    assert message.model == "claude-opus-5"
    assert message.usage.input_tokens > 0
    assert message.id


@pytest.mark.parametrize("name", JUDGE_RECORDINGS)
def test_the_committed_judge_recordings_still_match_the_current_prompt_rendering(
    name: str,
) -> None:
    """A drift guard, and only that: it compares the renderer with a frozen copy of its
    own output, so it proves the fixtures are current and nothing about the prompt itself.

    It earns its place anyway — the request body is the replay key, so a prompt edit that
    did not regenerate the fixtures would make every Judge recording unreachable, and the
    Seam 2 tests would fail far from the cause. Run `scripts/record_fixtures.py` when it
    goes red. What the prompt must actually contain is asserted below, independently.
    """
    suite, _ = load_suite(SUITE)
    prompt = render_prompt(suite.scenarios[0], read_trace(TRACE_FIXTURE))
    exchange = json.loads((RECORDINGS / name).read_text(encoding="utf-8").splitlines()[0])

    assert exchange["request"]["messages"][0]["content"] == prompt


# --- what the prompt must contain, asserted against the text and not against itself ---


def rendered_prompt() -> str:
    suite, _ = load_suite(SUITE)
    prompt = render_prompt(suite.scenarios[0], read_trace(TRACE_FIXTURE))
    assert prompt is not None
    return prompt


def test_the_prompt_ends_with_the_five_reverse_hallucination_questions() -> None:
    """D23: the reverse-hallucination checklist is the tail, and a Judge that lost it would assert
    things it cannot point to — the failure mode the checklist exists to catch."""
    prompt = rendered_prompt()

    assert "## Before you answer, check these five things" in prompt
    tail = prompt.split("## Before you answer, check these five things", 1)[1]
    for number in range(1, 6):
        assert f"\n{number}. " in tail, f"checklist question {number} is missing"


def test_the_prompt_names_every_span_the_judge_is_allowed_to_cite() -> None:
    """A Judge cannot cite what it was never shown (ADR-0003 §4)."""
    prompt = rendered_prompt()
    present = {
        event["span_id"]
        for event in map(json.loads, TRACE_FIXTURE.read_text(encoding="utf-8").splitlines())
        if event["span_id"]
    }

    assert present
    for span_id in present:
        assert f"[{span_id}]" in prompt


def test_the_prompt_names_the_scenario_it_is_judging() -> None:
    assert "- id: cancel-processing-order" in rendered_prompt()


def test_the_prompt_carries_nothing_that_differs_between_two_runs() -> None:
    """Any epoch-millisecond timestamp would change the replay key between Runs."""
    prompt = rendered_prompt()

    assert not re.search(r"\b1\d{12}\b", prompt), "an epoch-millisecond timestamp leaked in"


# --- cutting, when a caller asks for one, is announced (D23) ---


def test_nothing_is_cut_unless_a_cap_is_asked_for() -> None:
    rendered = render_trace_for_judge(read_trace(TRACE_FIXTURE))

    assert "[truncated" not in rendered


def test_a_field_over_the_cap_says_how_many_characters_were_dropped() -> None:
    """Checklist item 2 asks the Judge whether a quote came from a truncated field, and
    only a count makes that answerable — a silent ellipsis would not."""
    rendered = render_trace_for_judge(read_trace(TRACE_FIXTURE), max_chars=20)

    assert "[truncated" in rendered
    assert re.search(r"\[truncated \d+ chars\]", rendered)


def test_a_cap_larger_than_every_field_cuts_nothing() -> None:
    assert "[truncated" not in render_trace_for_judge(read_trace(TRACE_FIXTURE), max_chars=10_000)


# --- a Span below `instrumented` says so, once, on its first line (ADR-0001) ---


def test_an_instrumented_trace_announces_no_fidelity_at_all() -> None:
    """Nothing changes for the toy: saying "instrumented" on every line is noise."""
    rendered = render_trace_for_judge(read_trace(TRACE_FIXTURE))

    assert "fidelity" not in rendered


def test_a_reconstructed_span_is_announced_once_so_its_evidence_can_be_weighed() -> None:
    events = read_trace(TRACE_FIXTURE)
    lowered = [
        event.model_copy(update={"fidelity": "reconstructed"})
        if event.type == "span/start"
        and (event.model_extra or {}).get("kind") in {"tool_call", "retrieval"}
        else event
        for event in events
    ]

    rendered = render_trace_for_judge(lowered)

    assert rendered.count("(fidelity reconstructed)") == 2, "once per reconstructed Span"
    assert "[retrieval-1] tool/call lookup_order" in rendered


# --- each cite is one bare Span id: the schema guarantees it (ticket 21, decision 38) ---

BARE_ID_SENTENCE = (
    "Each entry of `{field}` is one bare Span id exactly as it appears inside the brackets, "
    "such as `llm_call-2`: not the brackets, not the line it starts."
)
"""The sentence decision 38 adds, verbatim, for `evidence` and for the Diagnosis's `cites`."""


def test_every_evidence_item_the_schema_asks_for_is_a_bare_span_id_and_nothing_else_changed() -> (
    None
):
    """A literal on purpose: the schema is the guarantee, so a later edit to it is a new
    Judge Fingerprint and should be one on purpose too."""
    assert SPAN_ID == r"[A-Za-z][A-Za-z0-9_]*-[0-9]+"
    assert SPAN_ID_PATTERN == r"^[A-Za-z][A-Za-z0-9_]*-[0-9]+$"
    assert BASE_PROPERTIES == {
        "verdict": {"type": "string", "enum": ["pass", "fail", "unverifiable"]},
        "reason": {
            "type": "string",
            "enum": [
                "none",
                "evidence_missing",
                "evidence_truncated",
                "refusal",
                "malformed_output",
            ],
        },
        "rationale": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string", "pattern": SPAN_ID_PATTERN}},
    }
    assert diagnosis.PARTS.output_schema == {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "cites": {"type": "array", "items": {"type": "string", "pattern": SPAN_ID_PATTERN}},
            "sections": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["text", "cites", "sections"],
        "additionalProperties": False,
    }


def committed_span_ids() -> set[str]:
    return {
        str(event.span_id)
        for path in (REPO / "tests" / "fixtures" / "traces").glob("*.trace.jsonl")
        for event in read_trace(path)
        if event.span_id is not None
    }


def test_the_span_id_pattern_matches_every_span_id_of_every_committed_trace() -> None:
    ids = committed_span_ids()

    assert {"llm_call-1", "retrieval-1", "tool_call-1", "turn-1"} <= ids
    assert all(re.fullmatch(SPAN_ID_PATTERN, span_id) for span_id in ids), sorted(ids)


@pytest.mark.parametrize(
    "cite",
    [
        "[llm_call-1]",
        'llm_call-1 assistant tool_use lookup_order {"order_id":"NB-1042"}',
        "llm_call",
    ],
)
def test_the_span_id_pattern_rejects_what_is_more_or_less_than_an_id(cite: str) -> None:
    assert re.search(SPAN_ID_PATTERN, cite) is None


def test_an_order_id_shaped_like_a_span_id_fits_the_pattern_and_is_dropped_as_no_span() -> None:
    """The schema can only constrain a shape; `NB-0688` has it, and `cited_spans` then
    drops it because the Trace has no such Span (rule 2)."""
    assert re.fullmatch(SPAN_ID_PATTERN, "NB-0688")

    cited, dropped, read = cited_spans(["NB-0688", "llm_call-1"], ["llm_call-1"])

    assert (cited, dropped, read) == (["llm_call-1"], ["NB-0688"], [])


def test_the_fallback_reads_cites_by_the_same_span_id_grammar_as_the_schema() -> None:
    assert CITED_SPAN.pattern == rf"^\s*\[?\s*(?P<id>{SPAN_ID})\s*\]?"


def test_the_judge_is_told_each_evidence_entry_is_one_bare_span_id() -> None:
    assert BARE_ID_SENTENCE.format(field="evidence") in CITING
    assert CITING.index(BARE_ID_SENTENCE.format(field="evidence")) < CITING.index("Cite only ids")
    assert BARE_ID_SENTENCE.format(field="evidence") in rendered_prompt()


def test_the_diagnosis_is_told_each_cites_entry_is_one_bare_span_id() -> None:
    assert BARE_ID_SENTENCE.format(field="cites") in diagnosis.PARTS.how_to_decide


def test_every_judged_prompt_version_counts_each_change_to_the_shared_head() -> None:
    """One wording change in the shared head is one version step for every prompt that
    renders it: the bare Span id sentence, the Diagnosis's conditional review section
    (phase-5 decision 57), the head's Manifest-prompt and Suppressions branches (phase-6
    decisions 15, 16), and the Suppressions preamble saying agentdiag checked the window
    (phase 6b). The Simulated User, the stop check and the reviewer render no such head and
    keep their own versions."""
    assert {name: module.PROMPT_VERSION for name, module in MODULES.items()} | {
        "diagnosis": diagnosis.PROMPT_VERSION
    } == {
        "prompt_adherence": "prompt_adherence.v5",
        "goal": "goal.v4",
        "guardrails": "guardrails.v4",
        "data_grounding": "data_grounding.v4",
        "data_query": "data_query.v4",
        "tool_choice": "tool_choice.v4",
        "diagnosis": "diagnosis.v5",
    }


# --- ticket 06: what the Judge is shown of a simulated Trial (phase-5 decision 59) ---


def with_agentdiags_own_calls(events: list[Event]) -> list[Event]:
    """The cancel Trace with a Simulated User's authoring, a stop check's model call and a
    top-level stop note spliced in before its `trace/end`, as a simulated Trial holds them."""
    *body, end = events
    seq = end.seq

    def event(type: str, actor: str, span_id: str | None, **fields: object) -> Event:
        nonlocal seq
        made = Event.model_validate(
            {
                "seq": seq,
                "ts": end.ts,
                "type": type,
                "actor": actor,
                "turn": None,
                "span_id": span_id,
                "parent_span_id": "simulate-1" if span_id == "llm_call-8" else None,
                **fields,
            }
        )
        seq += 1
        return made

    request = {"model": "claude-sonnet-5", "system": "You are playing a person.", "messages": []}
    spliced = [
        event(
            "note",
            "agentdiag",
            None,
            about="stop_when",
            predicate="tool_called x",
            holds=False,
            text="tool_called x did not hold after Turn 1",
        ),
        event(
            "span/start",
            "simulated_user",
            "simulate-1",
            kind="simulate",
            name="simulated user",
            fidelity="instrumented",
            dotted_order="9",
            attributes={},
        ),
        event(
            "span/start",
            "simulated_user",
            "llm_call-8",
            kind="llm_call",
            name="chat claude-sonnet-5",
            fidelity="instrumented",
            dotted_order="9.1",
            attributes={"gen_ai.request.model": "claude-sonnet-5"},
        ),
        event("request", "simulated_user", "llm_call-8", body=request),
        event(
            "response",
            "simulated_user",
            "llm_call-8",
            body={"content": [{"type": "text", "text": '{"message": "It is NB-1042."}'}]},
        ),
        event("span/end", "simulated_user", "llm_call-8", status="ok", attributes={}),
        event("span/end", "simulated_user", "simulate-1", status="ok", attributes={}),
        event(
            "span/start",
            "agentdiag",
            "llm_call-9",
            kind="llm_call",
            name="stop_when judged",
            fidelity="instrumented",
            dotted_order="10",
            attributes={},
        ),
        event("request", "agentdiag", "llm_call-9", body={"model": "claude-opus-5"}),
        event(
            "note",
            "agentdiag",
            "llm_call-9",
            about="stop_when",
            predicate="judged",
            holds=True,
            text="judged: it holds",
        ),
        event("span/end", "agentdiag", "llm_call-9", status="ok", attributes={}),
    ]
    return [*body, *spliced, end.model_copy(update={"seq": seq})]


def test_the_judge_never_sees_the_simulated_users_calls_or_agentdiags_stop_checks() -> None:
    """A Trace with them renders as the same Trace without them, byte for byte, so every
    rendered prompt recorded before ticket 06 is unchanged."""
    events = read_trace(TRACE_FIXTURE)

    assert render_trace_for_judge(with_agentdiags_own_calls(events)) == render_trace_for_judge(
        events
    )


def test_the_targets_system_prompt_is_never_read_from_the_simulated_users_request() -> None:
    events = read_trace(TRACE_FIXTURE)
    first_is_the_simulated_users = [events[0], *with_agentdiags_own_calls(events)[-12:-1]]

    assert system_prompt_from(first_is_the_simulated_users) is None
    assert system_prompt_from([*first_is_the_simulated_users, *events[1:]]) == SYSTEM_PROMPT
