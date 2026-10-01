"""Diagnosis: the Judge's narrative for one Trial, explaining its Verdicts (D24).

After a Trial's Evals, every Trial with at least one judged Eval gets one more Judge call:
the same prompt head over the same rendered Trace, with every Score the Trial produced
under `## The Scores`, asking for the most likely reasons the Verdicts came out as they
did, citing Spans and the Target's prompt sections. A marginal pass gets its why as a fail
does — it is the brief's "possible reasoning of why", and a pass nobody can explain is one
nobody can trust.

It is never a Score. It is written as a `note` Event with `actor: judge`, `about:
"diagnosis"`, its `text`, the Span ids it `cites`, the cites agentdiag read as the Span
ids they begin with as the Judge wrote them (`cites_read`, ticket 21, decision 39) and the
prompt `sections` it names, inside the `judge` Span named `judge diagnosis` in
`judgement.jsonl`; it never enters a count, a pass rate or an exit code. A failed
Diagnosis — a refusal, a schema failure, a client error — leaves the `error` Event the
Judge writes for any failed call and a `note` saying no Diagnosis was produced, and touches
no Score: the Verdicts it would have explained stand as they are.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from agentdiag.eval import simulated_user_review
from agentdiag.eval.judge import JUDGE_ACTOR, Judge
from agentdiag.eval.judged_score import (
    citable,
    cited_spans,
    dropped_text,
    read_as_text,
)
from agentdiag.eval.render import (
    JUDGE_ROLE,
    SPAN_ID_ITEM,
    JudgeContext,
    JudgePromptParts,
    asks_for,
    bare_span_ids,
    prompt_sections,
    system_prompt,
)
from agentdiag.eval.score import Score
from agentdiag.eval.template import Value
from agentdiag.trace import TraceWriter

PROMPT_VERSION = "diagnosis.v5"
"""`diagnosis.v5`: the Suppressions preamble now says agentdiag checked the window (phase 6b);
`diagnosis.v4`: the shared head gained two branches that render only when they apply — the
Manifest's `system` pointer as the prompt source when the Trace holds none (note 7) and the
Suppressions in force (phase-6 decisions 15, 16) — so over a Trace that holds its prompt
and a Trial with no Suppression the rendered prompt is `diagnosis.v3`'s, byte for byte."""

ABOUT = "diagnosis"
"""The `about` of the `note` a Diagnosis is written as, which is how `show` finds it."""

NO_DIAGNOSIS = "No Diagnosis was produced:"
"""How the `note` of a failed Diagnosis opens."""


class DiagnosisOutput(BaseModel):
    text: str
    cites: list[str] = Field(default_factory=list)
    sections: list[int] = Field(default_factory=list)


PARTS = JudgePromptParts(
    eval="diagnosis",
    version=PROMPT_VERSION,
    intro=(
        f"{JUDGE_ROLE} The Trial has already been judged: its Scores are listed under `## "
        "The Scores`. Your task is not to judge it again but to explain it: give the most "
        "likely reasons each Verdict came out as it did, as a developer fixing or trusting "
        "this Target would need them.\n\n"
        "Explain passes as well as failures. A pass that rests on one lucky tool result, or "
        "on a rule the Trial never exercised, is worth saying so. Do not change, dispute or "
        "restate a Verdict; say why the Trace led to it."
    ),
    how_to_decide=(
        "Write `text` as a few short paragraphs of plain prose, most important reason first. "
        "Put in `cites` the Span ids your account rests on, and in `sections` the "
        "numbers of the Target's prompt sections it refers to (0 for the preamble). "
        f"{bare_span_ids('cites')} Where the Trace does not show why, say that rather than "
        "guessing."
    ),
    output_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "cites": {"type": "array", "items": SPAN_ID_ITEM},
            "sections": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["text", "cites", "sections"],
        "additionalProperties": False,
    },
    output_model=DiagnosisOutput,
    extra_sections=[
        (
            "## The Scores",
            "{*scores}- {name}: {verdict}{?cause} ({cause}){/cause}, from {source}. Evidence: "
            "{?evidence}{evidence}{/evidence}{!evidence}nothing{/evidence}. Rationale: "
            "{rationale}\n{/scores}{!scores}(the Trial produced no Score){/scores}"
            "{*review}\n\n## The Simulated User review\n\nfinding {finding} ({direction}), "
            "citing {cites}: {rationale}{/review}",
        )
    ],
)


def score_values(scores: list[Score]) -> list[dict[str, Value]]:
    """Every Score of the Trial as the `{*scores}` block reads it: what it said and cited."""
    return [
        {
            "name": score.name,
            "verdict": score.verdict,
            "cause": score.reason or score.fault_source,
            "source": score.source.kind,
            "evidence": ", ".join(score.evidence),
            "rationale": score.rationale,
        }
        for score in scores
    ]


def is_diagnosis_request(request: Mapping[str, Any]) -> bool:
    """Whether a recorded request body is a Diagnosis's: its structured-output schema is
    this module's. How a replayed Run tells the Diagnosis's exchange apart from the Evals'
    when it checks the recording before the Diagnosis runs (phase-5 interfaces)."""
    return asks_for(request, PARTS.output_schema)


def judge(
    context: JudgeContext,
    judge: Judge,
    judgement: TraceWriter,
    *,
    scores: list[Score],
    review: simulated_user_review.ReviewOutput | None = None,
) -> list[Score]:
    """Write the Trial's Diagnosis into `judgement.jsonl`. Returns no Score, ever.

    `review` is the Simulated User reviewer's finding when one was applied (D28, phase-5
    decision 57): the Diagnosis is told it under its own heading, after the Scores it
    rewrote. With none, the rendered prompt is exactly what it was before ticket 06, so no
    recording key moved; the template gained the conditional tail, hence `diagnosis.v3`.
    """
    known_spans = [span.span_id for span in context.spans]
    system = system_prompt(context).text
    known_sections = [number for number, _ in prompt_sections(system)] if system else []

    def annotate(output: BaseModel | None, failure: str | None) -> dict[str, Any]:
        if not isinstance(output, DiagnosisOutput):
            return nothing_produced(str(failure))
        # Rule 2 of the Judge's answer rules, as the Scores' citations go through it: what
        # is not in the Trace, or not a section of the Target's prompt, is dropped and named.
        cites, dropped_cites, read = cited_spans(output.cites, known_spans)
        sections, dropped_sections = citable(output.sections, known_sections)
        text = (
            output.text.strip()
            + read_as_text(read)
            + dropped_text(dropped_cites, "Span ids")
            + dropped_text(dropped_sections, "prompt section numbers")
        )
        return {
            "about": ABOUT,
            "text": text,
            "cites": cites,
            "cites_read": [as_written for as_written, _ in read],
            "sections": sections,
        }

    # The Diagnosis's own Fingerprint, as a judged Eval's is: its prompt is its own, so the
    # Span says which Judge wrote the narrative (ticket 21, decision 40).
    fingerprint = judge.fingerprint(PARTS, context.notes, context.suppressions_in_force)
    answer = judge.ask_over(
        context,
        PARTS,
        judgement,
        fingerprint=fingerprint,
        annotate=annotate,
        scores=score_values(scores),
        review=simulated_user_review.diagnosis_values(review),
    )
    if answer is None:
        # Unreachable while the Diagnosis never requires the system prompt; recorded, not
        # asserted, because a Diagnosis must never cost a Trial its Scores.
        judgement.event("note", actor=JUDGE_ACTOR, **nothing_produced("the prompt did not render"))
    return []


def nothing_produced(why: str) -> dict[str, Any]:
    """The `note` a Trial gets when its Diagnosis failed: said, never silent."""
    return {
        "about": ABOUT,
        "text": f"{NO_DIAGNOSIS} {why}",
        "cites": [],
        "cites_read": [],
        "sections": [],
    }


__all__ = [
    "ABOUT",
    "NO_DIAGNOSIS",
    "PARTS",
    "PROMPT_VERSION",
    "DiagnosisOutput",
    "is_diagnosis_request",
    "judge",
    "nothing_produced",
    "score_values",
]
