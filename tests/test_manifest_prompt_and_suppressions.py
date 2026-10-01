"""Note 7 and Suppressions: what the Judge's head gains, and that nothing recorded moves
(ticket 10, phase-6 decisions 15, 16).

Both are branches of the one template every judged Eval's prompt is filled from, and both
render only when they apply: a Trace that holds its system prompt, judged with no
Suppression in force, renders exactly the prompt it rendered before, so every committed
recording replays unchanged (`tests/test_run_fixtures.py` diffs them by the byte). The
worked recordings of a Judge answering over a Trace with no prompt, and over a Trace inside
a Suppression's window, are the main session's to capture (decision 45); the tests above
need no model, and the last two replay those recordings (`judge-manifest-prompt.jsonl`,
`judge-suppressed.jsonl`, written by `scripts/record_fixtures.py`'s default mode), skipped
until `uv run python scripts/record_fixtures.py --capture` has captured their answers.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from agentdiag.eval import diagnosis, goal, prompt_adherence
from agentdiag.eval.judge import Judge, judge_fingerprint
from agentdiag.eval.judged_score import JudgedScores
from agentdiag.eval.render import (
    MANIFEST_PROMPT_NOTE,
    NO_SYSTEM_PROMPT,
    PROMPT_SECTIONS_NOTE,
    SUPPRESSIONS_NOTE,
    JudgeContext,
    render_judge_prompt,
    system_prompt,
)
from agentdiag.eval.score import Score
from agentdiag.eval.suppressions import Suppression, in_force, trace_start
from agentdiag.model.claude_code import Backend
from agentdiag.model.client import ReplayModelClient
from agentdiag.model.replay import Recording, ReplayCursor, canonical
from agentdiag.run.compare import configuration
from agentdiag.scenario.load import load_suite
from agentdiag.scenario.models import EvalDeclaration, Scenario
from agentdiag.trace import Event, TraceWriter, read_trace
from tests.stories import CANCEL_BROKEN_AT, CANCEL_VERDICT

REPO = Path(__file__).resolve().parents[1]
TRACE = REPO / "tests" / "fixtures" / "traces" / "cancel-processing-order.trace.jsonl"
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
MANIFEST_TEXT = "You are the order desk.\n\n1. Look up before you answer.\n\n2. Never invent."


def scenario() -> Scenario:
    suite, _ = load_suite(SUITE)
    return next(s for s in suite.scenarios if s.id == "cancel-processing-order")


def without_system_prompt(events: list[Event]) -> list[Event]:
    """The same Trace as an Adapter that could not see the prompt would have recorded it."""
    stripped: list[Event] = []
    for event in events:
        body = (event.model_extra or {}).get("body")
        if event.type == "request" and isinstance(body, dict) and "system" in body:
            dumped = event.model_dump()
            dumped["body"] = {key: value for key, value in body.items() if key != "system"}
            event = Event.model_validate(dumped)
        stripped.append(event)
    return stripped


def context(
    events: list[Event] | None = None,
    *,
    eval_name: str = "prompt_adherence",
    manifest_prompts: dict[str, str] | None = None,
    suppressions: list[Suppression] | None = None,
) -> JudgeContext:
    return JudgeContext.of(
        scenario(),
        EvalDeclaration(eval=eval_name),
        events if events is not None else read_trace(TRACE),
        fidelity="instrumented",
        notes=None,
        tool_kinds={},
        manifest_prompts=manifest_prompts,
        suppressions=suppressions or [],
    )


def suppression(**fields: Any) -> Suppression:
    defaults: dict[str, Any] = {
        "id": "sup-001",
        "eval": "prompt_adherence",
        "from": "2025-09-01",
        "until": "2025-09-30",
        "pattern": "a refund window quoted from the policy page",
        "why": "the policy page is a tool result the toy cannot see",
    }
    return Suppression.model_validate({**defaults, **fields})


# --- note 7: the Manifest's `system` pointer as the prompt source ---


def test_a_trace_that_holds_its_prompt_renders_the_same_with_or_without_manifest_text() -> None:
    plain = render_judge_prompt(prompt_adherence.PARTS, context())
    offered = render_judge_prompt(
        prompt_adherence.PARTS, context(manifest_prompts={"system": MANIFEST_TEXT})
    )

    assert plain == offered
    assert plain is not None and PROMPT_SECTIONS_NOTE in plain
    assert MANIFEST_PROMPT_NOTE not in plain


def test_with_no_prompt_in_the_trace_the_manifest_pointer_is_judged_against_and_said_so() -> None:
    events = without_system_prompt(read_trace(TRACE))
    judged = context(events, manifest_prompts={"system": MANIFEST_TEXT})

    rendered = render_judge_prompt(prompt_adherence.PARTS, judged)

    assert system_prompt(judged).from_manifest
    assert rendered is not None
    assert MANIFEST_PROMPT_NOTE in rendered
    assert PROMPT_SECTIONS_NOTE not in rendered
    assert "Rule 2: Never invent." in rendered
    assert prompt_adherence.MANIFEST_PROMPT_RULE in rendered


def test_with_no_prompt_anywhere_the_head_says_so_and_prompt_adherence_asks_no_judge() -> None:
    events = without_system_prompt(read_trace(TRACE))

    assert render_judge_prompt(prompt_adherence.PARTS, context(events)) is None
    head = render_judge_prompt(goal.PARTS, context(events, eval_name="goal"))
    assert head is not None and NO_SYSTEM_PROMPT in head


def test_the_three_notes_are_template_parts_so_the_fingerprint_covers_which_applied() -> None:
    template = prompt_adherence.PARTS.template()

    for note in (MANIFEST_PROMPT_NOTE, PROMPT_SECTIONS_NOTE, NO_SYSTEM_PROMPT, SUPPRESSIONS_NOTE):
        assert note in template
    assert prompt_adherence.PROMPT_VERSION == "prompt_adherence.v5"
    assert diagnosis.PROMPT_VERSION == "diagnosis.v5"
    assert goal.PROMPT_VERSION == "goal.v4"


def test_a_score_judged_against_the_manifest_prompt_says_so_in_its_rationale() -> None:
    output = prompt_adherence.PromptAdherenceOutput(
        verdict="pass", reason="none", rationale="Rule 1 was followed.", evidence=["tool_call-1"]
    )

    worded = prompt_adherence._with_rules(output, from_manifest=True)

    assert worded.endswith(prompt_adherence.FROM_MANIFEST_RATIONALE)


# --- Suppressions (decision 16) ---


def test_a_suppression_is_in_force_for_its_eval_inside_its_window_by_the_traces_start() -> None:
    events = read_trace(TRACE)

    assert trace_start(events) == date(2025, 9, 22)
    assert [s.id for s in in_force([suppression()], "prompt_adherence", events)] == ["sup-001"]
    assert in_force([suppression()], "goal", events) == []
    assert in_force([suppression(eval="*")], "goal", events) != []
    assert in_force([suppression(until="2025-09-22")], "prompt_adherence", events) != []
    assert in_force([suppression(until="2025-09-21")], "prompt_adherence", events) == []
    assert in_force([suppression(**{"from": "2025-09-23"})], "prompt_adherence", events) == []


def test_with_no_suppression_in_force_the_prompt_is_byte_for_byte_what_it_was() -> None:
    plain = render_judge_prompt(prompt_adherence.PARTS, context())
    expired = render_judge_prompt(
        prompt_adherence.PARTS, context(suppressions=[suppression(until="2025-09-01")])
    )

    assert expired == plain
    assert plain is not None and SUPPRESSIONS_NOTE not in plain


def test_a_suppression_in_force_reaches_the_judge_after_the_notes_by_id_pattern_and_why() -> None:
    rendered = render_judge_prompt(prompt_adherence.PARTS, context(suppressions=[suppression()]))

    assert rendered is not None
    notes = rendered.index("## Calibration notes for this Target")
    section = rendered.index("## Suppressions in force for this Trace")
    trace = rendered.index("## The Trace")
    assert notes < section < trace
    assert SUPPRESSIONS_NOTE in rendered
    assert (
        "- sup-001: a refund window quoted from the policy page (why: the policy page is a "
        "tool result the toy cannot see)"
    ) in rendered


def test_the_suppressions_in_force_enter_the_judge_fingerprint_and_the_scores_source() -> None:
    judge = Judge(object(), "claude-opus-5", backend=None)  # type: ignore[arg-type]
    none = JudgedScores(context(), prompt_adherence.PARTS, judge)
    offered = JudgedScores(context(suppressions=[suppression()]), prompt_adherence.PARTS, judge)

    assert none.fingerprint == judge_fingerprint(
        prompt_adherence.PARTS, None, "claude-opus-5", None, None
    )
    assert offered.fingerprint != none.fingerprint
    score = offered.unverifiable("nothing to decide", "evidence_missing")
    assert score.source.suppressions_in_force == ["sup-001"]
    assert none.unverifiable("x", "evidence_missing").source.suppressions_in_force == []


def test_compare_diffs_the_suppressions_by_id() -> None:
    base: dict[str, Any] = {"judge": {"model": "m", "suppressions": []}}
    later: dict[str, Any] = {
        "judge": {"model": "m", "suppressions": [suppression().model_dump(mode="json")]}
    }

    before, after = configuration(base, []), configuration(later, [])

    assert before["judge"]["suppressions"] == {}
    listed = configuration({**later, "manifest": {"suppressions": ["x"], "records": "changes"}}, [])
    assert "suppressions" not in listed["manifest"], "one edit is one difference"
    assert after["judge"]["suppressions"]["sup-001"]["pattern"] == (
        "a refund window quoted from the policy page"
    )


# --- decision 45's worked recordings, replayed ---

RECORDINGS = REPO / "tests" / "fixtures" / "recordings"
CAPTURED = RECORDINGS / "captured-judge-answers.jsonl"


def _record_fixtures() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "record_fixtures_decision_45", REPO / "scripts" / "record_fixtures.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("scripts/record_fixtures.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def replayed(stem: str, tmp_path: Path) -> Score:
    """`prompt_adherence` over the cancel Trace under the recording's Manifest variant,
    answered by the committed recording; skipped while its answer is the authored one."""
    path = RECORDINGS / f"{stem}.jsonl"
    (line,) = [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines()]
    captured = {
        canonical(json.loads(text)["request"])
        for text in CAPTURED.read_text(encoding="utf-8").splitlines()
        if text.strip()
    }
    if canonical(line["request"]) not in captured:
        pytest.skip(
            f"{stem}: no captured Judge answer yet; "
            "run `uv run python scripts/record_fixtures.py --capture`"
        )
    script = _record_fixtures()
    root_for, seen, _ = script.DECISION_45_RECORDINGS[stem]
    root = root_for(tmp_path / "variants")
    context = script.context_for(
        "cancel-processing-order", seen(read_trace(TRACE)), "prompt_adherence", root
    )
    judge = Judge(
        ReplayModelClient(ReplayCursor(Recording.load(path))),
        script.JUDGE_MODEL,
        None,
        backend=Backend(kind="replay"),
    )
    judgement = TraceWriter(tmp_path / "judgement.jsonl")
    judgement.start(trace_id="t", scenario="cancel-processing-order", run="t", trial=1)
    (score,) = prompt_adherence.judge(context, judge, judgement)
    judgement.close()
    return score


def test_the_captured_judge_grounds_the_fail_on_the_manifests_prompt_and_says_so(
    tmp_path: Path,
) -> None:
    """Note 7's fallback, live: no prompt in the Trace, the Manifest's `system` file judged
    against, the same rule-3 fail the cancel Trial tells, and the rationale says where the
    prompt came from."""
    score = replayed("judge-manifest-prompt", tmp_path)

    assert score.verdict == CANCEL_VERDICT
    assert CANCEL_BROKEN_AT in score.evidence
    assert prompt_adherence.FROM_MANIFEST_RATIONALE in score.rationale
    assert not [span for span in score.evidence if span.startswith("request")]


def test_the_captured_judge_names_the_suppression_that_changed_its_verdict(
    tmp_path: Path,
) -> None:
    """A Suppression in force for the cancel Trace, naming the rule-3 false fail: the Judge
    is offered it, and either passes or says it changed the Verdict, naming its id."""
    score = replayed("judge-suppressed", tmp_path)

    assert score.source.suppressions_in_force == ["sup-refund-timing"]
    assert score.verdict == "pass" or "sup-refund-timing" in score.rationale
