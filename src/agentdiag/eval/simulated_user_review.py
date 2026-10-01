"""The Simulated User reviewer: is a failed simulated Trial the Target's fault? (D28, D14,
ADR-0003 §3 and its consequence; phase-5 decision 57.)

One failure in eight of a simulated conversation is the Simulated User's, not the Target's
(D21's `simulated_user_review` row). So after the Evals of a Trial whose Scenario has a
`simulate` Turn, when at least one Score is `fail`, agentdiag asks the reviewer — a Judge
on a stronger model than the Simulated User's (`--reviewer-model`, default
`claude-opus-5`) — to read the Simulated User's messages against what it was told: the
`simulate` spec rendered exactly as the Simulated User saw it (`render_simulate_spec`), the
conversation by Turn with each user message marked `scripted` or `simulated`, how the
conversation ended, and the failed Scores. Its prompt carries tau2-bench's over-attribution
guard verbatim: the Target is responsible for its own behaviour, and when in doubt the
finding is none.

A finding other than `none` whose evidence, after rule 2, cites at least one Span of the
Trace rewrites **every `fail`** of the Trial to `invalid` / `fault_source: simulated_user` /
`fault_direction: <direction>`, citing the reviewer's Spans, the rationale saying what the
reviewer found and what the Eval had said; passes and abnormal Scores stand (D28 reviews
fails). A finding citing nothing is not applied, and a `note` says so; `none` applies
nothing; a failed call is rule 4's `error` and a `note`, and the Scores stand — an
instrument failing never changes a Verdict.

The call is written into `judgement.jsonl` as a `judge` Span named `judge
simulated_user_review` (D12). It is never declared, so it stays out of the catalogue.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, get_args

from pydantic import BaseModel

from agentdiag.eval.judge import Judge
from agentdiag.eval.judged_score import cited_spans
from agentdiag.eval.render import (
    SPAN_ID_ITEM,
    JudgeContext,
    JudgePromptParts,
    asks_for,
    bare_span_ids,
)
from agentdiag.eval.score import Score
from agentdiag.eval.template import Value
from agentdiag.scenario.models import SimulateSpec
from agentdiag.simulate.user import render_simulate_spec
from agentdiag.trace import Event, Span, TraceWriter
from agentdiag.types import FaultDirection, Finding

PROMPT_VERSION = "simulated_user_review.v1"

ABOUT = "simulated_user_review"
"""The Eval name on the reviewer's `judge` Span, and the `about` of its `note`."""

GUARD = (
    "The Target is responsible for its own behaviour. Report a fault only when a specific "
    "message of the Simulated User departs from its goal, its facts or its hints in a way that "
    "could have changed what the Target did. A Target that failed on its own is not the "
    "Simulated User's fault. When in doubt, the finding is none."
)
"""tau2-bench's over-attribution guard, verbatim in the template (D28)."""

INTRO = (
    "You are the Simulated User reviewer in agentdiag, a profiler for agentic systems. A "
    "Simulated User played the user in this Trial of an adaptive Scenario, and some of the "
    "Trial's Scores failed. Your only question is whether the Simulated User, not the Target, "
    "is at fault: whether a message it wrote departed from what it was told."
)

HOW_TO_DECIDE = (
    f"{GUARD}\n\n"
    "- `finding`: `none`, or what the Simulated User did: `leaked_hidden_fact` (it stated, "
    "hinted at or asked about something it was told it does not know), `stopped_early` (it "
    "ended the conversation before its goal was reached, while it still could be), "
    "`abandoned_goal` (it gave up on or changed its goal), `contradicted_facts` (it said "
    "something its known facts contradict), or `other`.\n"
    "- `direction`: `helped` when the fault made the Target's task easier, `hindered` when it "
    "made it harder, `none` with the finding `none` or when you cannot tell which way it "
    "pushed.\n"
    "- `evidence`: the Span ids of the Turns whose simulated messages show the fault, empty "
    f"when the finding is `none`. {bare_span_ids('evidence')}\n"
    "- `rationale`: which message did what, in a sentence or two."
)

TEMPLATE = f"""\
{INTRO}

## What the Simulated User was told

{{simulate_spec}}

## The conversation, by Turn

Each line starts with its Turn's Span id in square brackets. A user message is marked \
`scripted` when the Scenario's author wrote it and `simulated` when the Simulated User did; \
only the simulated ones are the Simulated User's.

{{*turns}}[{{span}}] user \
({{?simulated}}simulated{{/simulated}}{{!simulated}}scripted{{/simulated}}): {{user}}
[{{span}}] target: {{?reply}}{{reply}}{{/reply}}{{!reply}}(no reply){{/reply}}

{{/turns}}

The conversation ended: {{?ending}}{{ending}}{{?detail}} ({{detail}}){{/detail}}{{/ending}}\
{{!ending}}(not recorded){{/ending}}

## The Scores that failed

{{*fails}}- {{name}}: {{rationale}}
{{/fails}}

## How to decide

{HOW_TO_DECIDE}

Answer only in the structured format requested.
"""


class ReviewOutput(BaseModel):
    """The reviewer's answer, validated at the boundary; after rule 2 (its evidence the Spans
    of the Trace it cites) it is the review applied, which the Diagnosis reads (amended 57)."""

    finding: Finding
    direction: FaultDirection
    rationale: str
    evidence: list[str]


PARTS = JudgePromptParts(
    eval=ABOUT,
    version=PROMPT_VERSION,
    intro=INTRO,
    how_to_decide=HOW_TO_DECIDE,
    output_schema={
        "type": "object",
        "properties": {
            "finding": {"type": "string", "enum": list(get_args(Finding))},
            "direction": {"type": "string", "enum": list(get_args(FaultDirection))},
            "rationale": {"type": "string"},
            "evidence": {"type": "array", "items": SPAN_ID_ITEM},
        },
        "required": ["finding", "direction", "rationale", "evidence"],
        "additionalProperties": False,
    },
    output_model=ReviewOutput,
    own_template=TEMPLATE,
)


def is_review_request(request: Mapping[str, Any]) -> bool:
    """Whether a recorded request body is the reviewer's: its schema is this module's."""
    return asks_for(request, PARTS.output_schema)


def review(
    context: JudgeContext,
    judge: Judge,
    judgement: TraceWriter,
    *,
    scores: Sequence[Score],
    spec: SimulateSpec,
) -> tuple[list[Score], ReviewOutput | None]:
    """Review a simulated Trial with at least one `fail`: its Scores, rewritten when a
    finding applies, and the finding applied — the answer with its evidence after rule 2 —
    or None when nothing was."""
    fails = [score for score in scores if score.verdict == "fail"]
    if not fails:
        return list(scores), None
    known = [span.span_id for span in context.spans]
    applied: list[ReviewOutput] = []
    read_cites: list[str] = []

    def annotate(output: BaseModel | None, failure: str | None) -> dict[str, Any]:
        if not isinstance(output, ReviewOutput):
            return {"about": ABOUT, "text": f"No review was applied: {failure}"}
        said = f"finding {output.finding} ({output.direction}): {output.rationale.strip()}"
        if output.finding == "none":
            return {"about": ABOUT, "text": f"{said} Nothing is rewritten.", "applied": False}
        cited, dropped, read = cited_spans(output.evidence, known)
        if not cited:
            return {
                "about": ABOUT,
                "text": (
                    f"{said} The finding cites no Span of this Trace"
                    + (f" (it cited {', '.join(dropped)})" if dropped else "")
                    + ", so it is not applied and the Scores stand."
                ),
                "applied": False,
            }
        # A direction of `none` is applied too: the fault is the Simulated User's whether or
        # not the reviewer can say which way it pushed (amended decision 57).
        applied.append(
            output.model_copy(update={"rationale": output.rationale.strip(), "evidence": cited})
        )
        read_cites.extend(as_written for as_written, _ in read)
        return {
            "about": ABOUT,
            "text": f"{said} Every fail of the Trial is rewritten invalid / simulated_user.",
            "applied": True,
            "evidence": cited,
        }

    values = review_values(context, spec, fails)
    judge.ask_over(
        context,
        PARTS,
        judgement,
        fingerprint=judge.fingerprint(PARTS, context.notes),
        annotate=annotate,
        simulate_spec=values["simulate_spec"],
        turns=values["turns"],
        ending=values["ending"],
        detail=values["detail"],
        fails=values["fails"],
    )
    if not applied:
        return list(scores), None
    (found,) = applied
    return [_rewritten(score, found, read_cites) for score in scores], found


def review_values(
    context: JudgeContext, spec: SimulateSpec, fails: Sequence[Score]
) -> dict[str, Value]:
    """What the template's slots read from the Trial: data, never wording."""
    end = next((e for e in reversed(context.events) if e.type == "trace/end"), None)
    ending = (end.model_extra or {}) if end is not None else {}
    return {
        "simulate_spec": render_simulate_spec(spec),
        "turns": _turns(context.events, context.spans),
        "ending": _optional(ending.get("termination")),
        "detail": _optional(ending.get("detail")),
        "fails": [{"name": score.name, "rationale": score.rationale} for score in fails],
    }


def _turns(events: Sequence[Event], spans: Sequence[Span]) -> list[dict[str, Value]]:
    turns: list[dict[str, Value]] = []
    for span in spans:
        if span.kind != "turn":
            continue
        user = _said(events, span.span_id, "user")
        target = _said(events, span.span_id, "assistant")
        turns.append(
            {
                "span": span.span_id,
                "simulated": "simulated"
                if user is not None and user.actor == "simulated_user"
                else None,
                "user": _content(user),
                "reply": _content(target) if target is not None else None,
            }
        )
    return turns


def _said(events: Sequence[Event], span_id: str, role: str) -> Event | None:
    return next(
        (
            e
            for e in events
            if e.span_id == span_id
            and e.type == "message"
            and (e.model_extra or {}).get("role") == role
        ),
        None,
    )


def _content(event: Event | None) -> str:
    return str((event.model_extra or {}).get("content") or "") if event is not None else ""


def _optional(value: Any) -> str | None:
    return None if value is None else str(value)


def _rewritten(score: Score, found: ReviewOutput, cites_read: list[str]) -> Score:
    """A `fail` the finding explains, as `invalid` / `simulated_user` (decision 57)."""
    if score.verdict != "fail":
        return score
    # Validated again, not copied: the rewritten Score keeps every rule a Score keeps.
    return Score.model_validate(
        {
            **score.model_dump(),
            "verdict": "invalid",
            "reason": None,
            "fault_source": "simulated_user",
            "fault_direction": found.direction,
            "evidence": list(found.evidence),
            "cites_read": list(cites_read),
            "rationale": (
                f"The Simulated User reviewer found {found.finding} ({found.direction}): "
                f"{found.rationale} The {score.name} had scored fail: {score.rationale}"
            ),
        }
    )


def diagnosis_values(found: ReviewOutput | None) -> list[dict[str, Value]]:
    """What the Diagnosis's `{*review}` block reads of an applied review: data; its wording
    is the Diagnosis's template (amended decision 48)."""
    if found is None:
        return []
    return [
        {
            "finding": found.finding,
            "direction": found.direction,
            "cites": ", ".join(found.evidence),
            "rationale": found.rationale,
        }
    ]


__all__ = [
    "ABOUT",
    "GUARD",
    "PARTS",
    "PROMPT_VERSION",
    "ReviewOutput",
    "diagnosis_values",
    "is_review_request",
    "review",
    "review_values",
]
