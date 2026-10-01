"""`data_grounding`: did the Target stick to the data it was given (D21, D23)?

Every factual claim in the Target's messages must be supported by a tool result, a Fixture
or the Target's own prompt. The Judge lists the claims it found, each with the Span it was
made in and the Span that supports it — or `null`, naming its absence — so the Score cites
both sides of every claim and a reader can check any one of them against the Trace.

agentdiag holds the Judge to its own list: a `pass` beside a claim with no support — none
named, or a supporting Span id that is not in the Trace — is recorded as a `fail`, because
"every claim is grounded" and "this claim is not" cannot both be true, and the list is the
more specific statement. The rationale names every
unsupported claim. Minimum Fidelity is `reconstructed` (D21): a claim checked against a
tool result the Adapter only observed from the conversation would be checked against the
Target's own account of it.

With no Target message there is no claim to check, and the Score is `unverifiable` /
`eval_not_applicable` with no model called.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agentdiag.eval.judge import Judge, JudgeOutput
from agentdiag.eval.judged_score import judge_once
from agentdiag.eval.render import JUDGE_ROLE, JudgeContext, JudgePromptParts, extended_schema
from agentdiag.eval.score import Score
from agentdiag.trace import TraceWriter

PROMPT_VERSION = "data_grounding.v4"
"""`data_grounding.v4`: the Suppressions preamble now says agentdiag has already checked each
Suppression's window against the Trace (phase 6b). That part renders only when a Suppression
is in force, so with none the rendered prompt is `data_grounding.v3`'s, byte for byte; `.v3`
added the Manifest's `system` pointer as the prompt source when the Trace holds none (note
7) and the Suppressions section itself (phase-6 decisions 15, 16)."""


class Claim(BaseModel):
    claim: str
    span: str
    """The Span the claim was made in."""

    supported_by: str | None = None
    """The Span that supports it, or None: nothing in the Trace does."""


class DataGroundingOutput(JudgeOutput):
    claims: list[Claim] = Field(default_factory=list)


CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claim": {"type": "string"},
        "span": {"type": "string"},
        "supported_by": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["claim", "span", "supported_by"],
    "additionalProperties": False,
}

PARTS = JudgePromptParts(
    eval="data_grounding",
    version=PROMPT_VERSION,
    intro=(
        f"{JUDGE_ROLE} Your only question is data grounding: is every factual claim the "
        "Target made to the user supported by a tool result in the Trace, by a Fixture the "
        "Scenario set up, or by the Target's own prompt sections?\n\n"
        "A factual claim is anything the user could check: an order's status, contents, "
        "price or date, a policy, an amount, a name. Greetings, offers of help and questions "
        "back to the user are not claims. You are not judging whether the claims were "
        "useful, only whether each one rests on data the Target was given."
    ),
    how_to_decide=(
        "List every factual claim in `claims`: the claim in a few words, `span` the Span id "
        "of the line where the Target made it, and `supported_by` the Span id of the "
        "`tool/result` or other line that supports it, or null when nothing in the Trace or "
        "the prompt sections does.\n\n"
        "- `pass`: every claim has support. Cite the claims' Spans and their support.\n"
        "- `fail`: at least one claim has none, or contradicts its source. Name it and cite "
        "the Span where it was made.\n"
        "- `unverifiable`: the Trace does not show what the Target said or what the tools "
        "returned. Use reason `evidence_missing`."
    ),
    output_schema=extended_schema(claims={"type": "array", "items": CLAIM_SCHEMA}),
    output_model=DataGroundingOutput,
)

NO_MESSAGE = "The Target sent no message in this Trial, so it made no claim to check"


def judge(context: JudgeContext, judge: Judge, judgement: TraceWriter) -> list[Score]:
    """One Score: whether every claim the Target made rests on data it was given."""
    unmet = None if _target_spoke(context) else NO_MESSAGE
    known = {span.span_id for span in context.spans}
    return judge_once(
        context, PARTS, judge, judgement, unmet=unmet, read=lambda output: _read(output, known)
    )


def _read(output: DataGroundingOutput, known: set[str]) -> dict[str, Any]:
    """The claims folded into the Score: cited, the unsupported ones named, and a `pass`
    beside an unsupported claim recorded as the `fail` the claim says it is.

    A claim is unsupported when the Judge names no support for it, and equally when the
    support it names is no Span in this Trace: a phantom id supports nothing, and rule 2
    would otherwise drop it and leave the claim looking grounded (ticket 05 Spec review).
    """
    unsupported = [
        claim
        for claim in output.claims
        if claim.supported_by is None or claim.supported_by not in known
    ]
    rationale = output.rationale.strip()
    if unsupported:
        named = "; ".join(
            f"{claim.claim!r} in {claim.span}"
            + (
                f" (its cited support {claim.supported_by} is not in the Trace)"
                if claim.supported_by is not None
                else ""
            )
            for claim in unsupported
        )
        rationale += f" Unsupported claims: {named}."
    contradicted = bool(unsupported) and output.verdict == "pass"
    if contradicted:
        rationale += (
            " The Judge returned pass beside a claim it found no support for, so agentdiag "
            "records what the claim says: fail."
        )
    cited = [claim.span for claim in output.claims]
    cited += [claim.supported_by for claim in output.claims if claim.supported_by is not None]
    return {
        "rationale": rationale,
        "also_cited": cited,
        "verdict": "fail" if contradicted else None,
    }


def _target_spoke(context: JudgeContext) -> bool:
    return any(
        event.type == "message"
        and event.actor == "target"
        and (event.model_extra or {}).get("role") == "assistant"
        for event in context.events
    )


__all__ = ["PARTS", "PROMPT_VERSION", "Claim", "DataGroundingOutput", "judge"]
