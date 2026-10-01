"""Turning what a Judge answered into Scores: rules 1 to 3, the source, the Fingerprint.

`agentdiag.eval.judge` makes the call and applies rule 4; this module is the other half of
the four answer rules (D13, ADR-0003 §3, §4), in one place for every judged Eval, so none
of the per-Eval modules builds a Score by hand:

1. a `pass` or `fail` citing Spans that exist is taken as given;
2. a cite that is more than an id — bracketed, or the whole line as the Trace is rendered
   to the Judge — is read as the Span id it begins with and the rationale says so
   (`cited_spans`, `read_as_text`; seen live 2026-09-24, the first Judge call through Claude
   Code cited three rendered lines where the schema asked for ids); cited ids not in the
   Trace are dropped and named, and a `pass` or `fail` left citing nothing is
   `unverifiable` / `evidence_missing` (`dropped_text`; the Diagnosis's own citations go
   through the same);
3. an `unverifiable` is taken as returned.

Every judged Score's `source` is built here with its **Judge Fingerprint** (ADR-0003 §8,
`judge.judge_fingerprint`, asked of the Judge as `Judge.fingerprint`): everything that could
move a Verdict without the Target moving, so `compare` can say the judgement changed instead
of implying the Target did.

`judge_once` is the shape most judged Evals take: the Fidelity gate, the precondition, one
call through `Judge.ask_over`, one Score.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Literal, cast

from agentdiag.eval.judge import (
    JUDGE_ACTOR,
    Judge,
    JudgeAnswer,
    JudgeFailure,
    JudgeOutput,
    judge_fingerprint,
)
from agentdiag.eval.mechanical import lowest_fidelity
from agentdiag.eval.registry import REGISTRY
from agentdiag.eval.render import SPAN_ID, JudgeContext, JudgePromptParts
from agentdiag.eval.score import (
    FAIL_REASONS,
    UNVERIFIABLE_REASONS,
    Score,
    ScoreSource,
    read_as,
)
from agentdiag.trace import TraceWriter
from agentdiag.types import FIDELITY_RANK, FailReason, UnverifiableReason


def citable[T](claimed: Iterable[T], known: Iterable[T]) -> tuple[list[T], list[T]]:
    """Rule 2's split: what the Judge cited that exists, and what it cited that does not,
    each once, in the order cited."""
    present = set(known)
    ordered = list(dict.fromkeys(claimed))
    return (
        [item for item in ordered if item in present],
        [item for item in ordered if item not in present],
    )


CITED_SPAN = re.compile(rf"^\s*\[?\s*(?P<id>{SPAN_ID})\s*\]?")
"""How a cite that is more than an id begins: the id, bare or in square brackets, the way
every line of the rendered Trace begins (`[llm_call-2] assistant text: …`). Seen live on
2026-09-24: the Judge cited three whole Trace lines where the schema asked for ids. Built
from `render.SPAN_ID`, the grammar the schema's pattern guarantees (decision 38), so the
fallback reads exactly the ids the schema would have let through."""


def cited_spans(
    claimed: Iterable[str], known: Iterable[str]
) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """Rule 2 for Span ids: the ids cited that exist, the cites that name none, and the
    cites that were more than an id, each as `(as written, the Span id read out of it)` —
    each once, in the order cited.

    A cite is read as the Span id it begins with when that id is in the Trace: bare,
    bracketed, or the whole rendered Trace line, which is how the Trace shows the Judge each
    Span. What such a cite means is not in doubt; the rationale still says it was read that
    way (`read_as_text`), and the Score keeps the cite as the Judge wrote it
    (`Score.cites_read`, decision 39), so `show` and `compare` can count how often the
    fallback fired rather than leave it to a reader of every rationale. A cite that begins
    with no Span of the Trace is dropped and named, as before.
    """
    present = set(known)
    cited: dict[str, None] = {}
    dropped: dict[str, None] = {}
    read: dict[str, str] = {}
    for claim in claimed:
        if claim in present:
            cited[claim] = None
            continue
        match = CITED_SPAN.match(claim)
        span_id = match.group("id") if match else None
        if span_id is not None and span_id in present:
            cited[span_id] = None
            read.setdefault(claim, span_id)
        else:
            dropped[claim] = None
    return list(cited), list(dropped), list(read.items())


def read_as_text(read: Sequence[tuple[str, str]]) -> str:
    """The sentence a rationale gains when a cite was read as the Span id it begins with.

    It counts the cites read and names each id once, in the order read, with `(n cites)`
    when n cites were read as it, so the count and the list agree; the cites as written are
    on the Score (`cites_read`), where a long rendered line does not bury the rationale."""
    if not read:
        return ""
    counts: dict[str, int] = {}
    for _, span_id in read:
        counts[span_id] = counts.get(span_id, 0) + 1
    named = ", ".join(f"{i} ({n} cites)" if n > 1 else i for i, n in counts.items())
    cites = "1 cite" if len(read) == 1 else f"{len(read)} cites"
    return f"\n\nagentdiag read {cites} as {read_as(len(read), len(counts))}: {named}."


def dropped_text(dropped: Sequence[object], what: str) -> str:
    """The sentence a rationale gains when rule 2 dropped something: named, never silent."""
    if not dropped:
        return ""
    return (
        f"\n\nagentdiag dropped cited {what} that do not appear in the Trace: "
        f"{', '.join(str(item) for item in dropped)}."
    )


class JudgedScores:
    """Every Score one judged Eval produces over one Trial, by the four answer rules.

    Bound to the context, the parts and the Judge, so a per-Eval module states what it
    concluded and never how a Score is assembled: the `source`, the Fingerprint, the
    Fidelity and the shared-model flag are filled here, once, for every judged Eval.
    """

    def __init__(self, context: JudgeContext, parts: JudgePromptParts, judge: Judge) -> None:
        self.context = context
        self.parts = parts
        self.judge = judge
        self.known = {span.span_id for span in context.spans}
        self.suppressions = context.suppressions_in_force
        """The Suppressions offered to this Judge over this Trial (decision 16)."""
        self.fingerprint = judge.fingerprint(parts, context.notes, self.suppressions)
        """Once per Eval over one Trial: every Score it produces records it, and the Judge's
        Span and request carry it (ticket 21, decision 40)."""

    @property
    def eval(self) -> str:
        return self.parts.eval

    def fidelity_gate(self) -> Score | None:
        """`unverifiable` / `fidelity_too_low` when the Trace is below the Eval's minimum."""
        spec = REGISTRY.get(self.eval)
        grounded = lowest_fidelity(self.context.spans, self.context.fidelity)
        if spec is None or FIDELITY_RANK[grounded] >= FIDELITY_RANK[spec.min_fidelity]:
            return None
        return self.unverifiable(
            f"{self.eval} needs evidence at Fidelity {spec.min_fidelity} or better, and this "
            f"Trace is {grounded}",
            "fidelity_too_low",
        )

    def not_applicable(
        self,
        rationale: str,
        judgement: TraceWriter,
        *,
        eval_id: str | None = None,
        reason: UnverifiableReason = "eval_not_applicable",
    ) -> Score:
        """A precondition the Eval needs is unmet: said in a `note`, and no model called."""
        judgement.event("note", actor=JUDGE_ACTOR, text=rationale, about=self.eval, eval_id=eval_id)
        return self.unverifiable(rationale, reason, eval_id=eval_id)

    def from_output(
        self,
        output: JudgeOutput,
        answer: JudgeAnswer,
        *,
        eval_id: str | None = None,
        rationale: str | None = None,
        also_cited: Sequence[str] = (),
        verdict: Literal["pass", "fail"] | None = None,
    ) -> Score:
        """Rules 1 to 3, in the order they decide.

        `rationale` replaces the Judge's own when the Eval folds more into it; `also_cited`
        adds Spans the Eval's extension named (a claim and its support); `verdict`
        overrides a `pass` or `fail` the Eval's extension contradicts.
        """
        text = rationale if rationale is not None else output.rationale.strip()
        resolved = answer.resolved_model

        if output.verdict == "unverifiable":
            reason = output.reason if output.reason in UNVERIFIABLE_REASONS else None
            return self.unverifiable(
                text,
                cast(UnverifiableReason, reason) if reason else "evidence_missing",
                eval_id=eval_id,
                resolved_model=resolved,
            )

        cited, dropped, read = cited_spans([*output.evidence, *also_cited], self.known)
        text += read_as_text(read) + dropped_text(dropped, "Span ids")

        decided = verdict or output.verdict
        if not cited:
            # ADR-0003 §4: a judged pass or fail that cites nothing is recorded as
            # unverifiable. Mapped here rather than in `Score`, so the Score's validator
            # can refuse the same thing as a bug rather than quietly repairing it.
            return self.unverifiable(
                f"{text}\n\nThe Judge returned {decided} but cited no Span in this Trace, "
                "so the judgement cannot be checked against the evidence.",
                "evidence_missing",
                eval_id=eval_id,
                resolved_model=resolved,
            )

        fail_reason = output.reason if output.reason in FAIL_REASONS else None
        return Score(
            verdict=decided,
            rationale=text,
            evidence=cited,
            reason=cast(FailReason, fail_reason) if fail_reason and decided == "fail" else None,
            cites_read=[as_written for as_written, _ in read],
            **self._common(eval_id, resolved),
        )

    def failed(self, failure: JudgeFailure, *, eval_id: str | None = None) -> Score:
        """Rule 4: agentdiag's instrument failed — `invalid`, the Judge's fault."""
        return self.invalid(failure.detail, eval_id=eval_id, resolved_model=failure.resolved_model)

    def invalid(
        self, rationale: str, *, eval_id: str | None = None, resolved_model: str | None = None
    ) -> Score:
        return Score(
            verdict="invalid",
            rationale=rationale,
            evidence=[],
            fault_source="judge",
            fault_direction="none",
            **self._common(eval_id, resolved_model),
        )

    def unverifiable(
        self,
        rationale: str,
        reason: UnverifiableReason,
        *,
        eval_id: str | None = None,
        resolved_model: str | None = None,
    ) -> Score:
        """The Eval ran and the evidence to decide was not there (ADR-0003 §3)."""
        return Score(
            verdict="unverifiable",
            rationale=rationale,
            evidence=[],
            reason=reason,
            **self._common(eval_id, resolved_model),
        )

    def _common(self, eval_id: str | None, resolved_model: str | None) -> dict[str, Any]:
        """What every Score from this Judge carries, whatever its Verdict (D13, D23)."""
        judge = self.judge
        return {
            "eval": self.eval,
            "eval_id": eval_id if eval_id is not None else self.context.declaration.id,
            "source": ScoreSource(
                kind="judge",
                requested_model=judge.model,
                resolved_model=resolved_model,
                prompt_version=self.parts.version,
                judge_fingerprint=self.fingerprint,
                suppressions_in_force=[suppression.id for suppression in self.suppressions],
            ),
            "fidelity": lowest_fidelity(self.context.spans, self.context.fidelity),
            "shared_model": shared_model(self.context, resolved_model),
        }


def judge_once(
    context: JudgeContext,
    parts: JudgePromptParts,
    judge: Judge,
    judgement: TraceWriter,
    *,
    unmet: str | None = None,
    read: Callable[[Any], Mapping[str, Any]] | None = None,
) -> list[Score]:
    """The shape every one-call judged Eval takes: gate, precondition, call, one Score.

    `unmet` is the rationale when the Eval's precondition does not hold (no call is made);
    a prompt that cannot be rendered — the parts require the system prompt and the Trace
    holds none — is the same fact told by the head, scored `unverifiable` /
    `evidence_missing`. `read` turns the Eval's extension of the answer into `from_output`'s
    overrides: a rationale that folds it in, Spans it adds to the evidence, a Verdict it
    contradicts.
    """
    scores = JudgedScores(context, parts, judge)
    if (gated := scores.fidelity_gate()) is not None:
        return [gated]
    if unmet is not None:
        return [scores.not_applicable(unmet, judgement)]
    answer = judge.ask_over(
        context, parts, judgement, eval_id=context.declaration.id, fingerprint=scores.fingerprint
    )
    if answer is None:
        return [
            scores.not_applicable(
                f"The Trace holds no system prompt for the Target, and {parts.eval} judges "
                "the Trial against it, so there is nothing to judge against",
                judgement,
                reason="evidence_missing",
            )
        ]
    if isinstance(answer, JudgeFailure):
        return [scores.failed(answer)]
    output = cast(JudgeOutput, answer.output)
    overrides = dict(read(output)) if read is not None else {}
    return [scores.from_output(output, answer, **overrides)]


def shared_model(context: JudgeContext, resolved_model: str | None) -> bool | None:
    """Whether the Target and the Judge resolved to the same model (D23).

    None when the Judge resolved to nothing — no call was made, or it failed before the
    API said which model answered — because "not shared" would be a claim nobody checked.
    """
    if resolved_model is None:
        return None
    for span in context.spans:
        # The Target's model calls only: the Simulated User's are not the Target's (D59).
        if span.kind != "llm_call" or span.actor != "target":
            continue
        target_model = span.attributes.get("gen_ai.response.model")
        if isinstance(target_model, str) and target_model == resolved_model:
            return True
    return False


__all__ = [
    "JudgedScores",
    "citable",
    "cited_spans",
    "dropped_text",
    "judge_fingerprint",
    "judge_once",
    "read_as_text",
    "shared_model",
]
