"""What one Eval said about one Trace, and the rules that keep it honest (ADR-0003).

A Score is a judgement, so every field that could soften it is closed. The Verdict is one
of five; each abnormal Verdict carries exactly the cause its Verdict allows and nothing
else (ADR-0003 section 3); and `evidence` is required, because a Score that cites nothing
claims more than it can show (ADR-0003 section 4).

The validator here is the one place those rules live. A Score that breaks them cannot be
constructed, so it cannot reach `scores.json`, the Scorecard, or a Report.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self, get_args

from pydantic import BaseModel, Field, model_validator

from agentdiag.types import (
    VERDICTS,
    FailReason,
    FaultDirection,
    FaultSource,
    Fidelity,
    IncompleteReason,
    UnverifiableReason,
    Verdict,
)

SHARED_MODEL_WARNING = "judge shares the Target's model (self-preference risk)"
"""What the Scorecard's counts line and `show` print when any Score's Judge resolved to its
Target's model (D23). Here, beside `shared_model`, so the two surfaces share one spelling."""


def read_as(cites: int, ids: int) -> str:
    """How a sentence about decision 37's reading names what it read: `the Span id it
    begins with` for one cite, `the Span id they begin with` for several cites of one id,
    `the Span ids they begin with` otherwise. Here, beside `cites_read`, so the rationale
    (`judged_score.read_as_text`) and `show`'s `cites read` line share one spelling
    (ticket 21, decision 39)."""
    span_ids = "Span id" if ids == 1 else "Span ids"
    begin = "it begins" if cites == 1 else "they begin"
    return f"the {span_ids} {begin} with"


FAIL_REASONS = frozenset(get_args(FailReason))
INCOMPLETE_REASONS = frozenset(get_args(IncompleteReason))
UNVERIFIABLE_REASONS = frozenset(get_args(UnverifiableReason))


class ScoreSource(BaseModel):
    """Who or what produced a Score, in enough detail to re-examine it (ADR-0003 §4).

    A judged Score names the model asked for and the model the API actually resolved to,
    because those differ and a Verdict from an alias is not reproducible. A mechanical
    Score names the code that computed it instead.
    """

    kind: Literal["judge", "mechanical", "human"]
    requested_model: str | None = None
    resolved_model: str | None = None
    prompt_version: str | None = None
    code: str | None = None
    """Mechanical only: the dotted reference of the code that decided, for citation."""

    judge_fingerprint: str | None = None
    """Judged only: sha256 over the prompt version, the prompt text, the calibration notes,
    the requested model, the effort and the Backend with its CLI version (ADR-0003 §8,
    ticket 19). What `compare` diffs, so an edit to any of them — the notes above all — is
    a visible change on every Score it touched."""

    suppressions_in_force: list[str] = Field(default_factory=list)
    """Judged only: the ids of the Suppressions the Judge was shown for this Trial (ADR-0003
    §8, phase-6 decision 16), so a reader sees which were offered without re-deriving the
    window. Empty when none was in force, and on every mechanical Score."""


class Score(BaseModel):
    """The output of one Eval on one Trace (`CONTEXT.md`, ADR-0003)."""

    eval: str
    eval_id: str | None = None
    verdict: Verdict
    value: float | None = None
    threshold: dict[str, Any] | None = None
    direction: Literal["minimize", "maximize"] | None = None
    """The Metric's direction of better — never the `invalid` fault's direction, which is
    `fault_direction` (phase-4 interfaces, decision 3)."""

    rationale: str
    reason: IncompleteReason | UnverifiableReason | FailReason | None = None
    fault_source: FaultSource | None = None
    fault_direction: FaultDirection | None = None
    evidence: list[str]
    """Span ids from the Trace this Score read. Required; may be empty for a mechanical
    Score that decided without reading a Span (ADR-0003 §4 binds judged Scores)."""

    source: ScoreSource
    fidelity: Fidelity
    shared_model: bool | None = None
    cites_read: list[str] = Field(default_factory=list)
    """The cites agentdiag read as the Span id they begin with, as the Judge wrote them, in
    the order read (ticket 21, decisions 37 and 39); empty when every cite was a bare id.
    Always written, so the Scorecard, `show` and `compare` can count how often the fallback
    fired: a Judge that answers outside its schema is a fact about the instrument."""

    @property
    def name(self) -> str:
        """`guardrails[no-refund-timing]`, or the Eval alone when it carries no id: the one
        spelling a summary, `show` and the Diagnosis name a Score by."""
        return score_name(self.eval, self.eval_id)

    @model_validator(mode="after")
    def _cause_matches_the_verdict(self) -> Self:
        """ADR-0003 §3: each Verdict has exactly one closed carrier for its cause."""
        verdict = self.verdict
        if verdict == "pass" and self.reason is not None:
            raise ValueError("a pass carries no reason")
        if verdict == "fail" and self.reason is not None and self.reason not in FAIL_REASONS:
            raise ValueError(
                f"a fail's reason is one of {sorted(FAIL_REASONS)}; got {self.reason!r}"
            )
        if verdict == "incomplete" and self.reason not in INCOMPLETE_REASONS:
            raise ValueError(
                f"an incomplete carries a reason from {sorted(INCOMPLETE_REASONS)}; "
                f"got {self.reason!r}"
            )
        if verdict == "unverifiable" and self.reason not in UNVERIFIABLE_REASONS:
            raise ValueError(
                f"an unverifiable carries a reason from {sorted(UNVERIFIABLE_REASONS)}; "
                f"got {self.reason!r}"
            )
        if verdict == "invalid":
            if self.fault_source is None or self.fault_direction is None:
                raise ValueError("an invalid carries both fault_source and fault_direction")
        elif self.fault_source is not None or self.fault_direction is not None:
            raise ValueError("only an invalid carries fault_source or fault_direction")
        return self

    @model_validator(mode="after")
    def _a_judged_pass_or_fail_cites_its_evidence(self) -> Self:
        """ADR-0003 §4: a judged `pass` or `fail` with no evidence cannot be constructed.

        The Judge already maps that case to `unverifiable` / `evidence_missing` before it
        builds a Score, so reaching here means a bug upstream rather than an unlucky
        model. Refusing it is the point: Fidelity (ADR-0001) is meaningless if a Score can
        claim more than its evidence, and a repaired Score would hide the bug that made it.
        """
        if self.source.kind == "judge" and self.verdict in {"pass", "fail"} and not self.evidence:
            raise ValueError(
                f"a judged {self.verdict} cites the Spans that ground it; "
                "one with no evidence is recorded as unverifiable / evidence_missing"
            )
        return self


def render_counts(counts: Mapping[Verdict, int]) -> str:
    """`pass 3  fail 1  incomplete 0  unverifiable 0  invalid 0`: every Verdict, zeros
    included, in one order (ADR-0005 §8). The one spelling the Scorecard's summary and
    `list` share, so a pass count never stands without the other four."""
    return "  ".join(f"{verdict} {counts.get(verdict, 0)}" for verdict in VERDICTS)


def score_name(eval: str, eval_id: str | None) -> str:
    """An Eval with its declaration's or rule's id: `guardrails[no-refund-timing]`."""
    return f"{eval}[{eval_id}]" if eval_id else eval


class ScoresFile(BaseModel):
    """What a Trial's `scores.json` holds (phase-4 interfaces, `agentdiag.eval`)."""

    scenario: str
    trial: int
    scores: list[Score] = Field(default_factory=list)


__all__ = [
    "FAIL_REASONS",
    "INCOMPLETE_REASONS",
    "SHARED_MODEL_WARNING",
    "UNVERIFIABLE_REASONS",
    "Score",
    "ScoreSource",
    "ScoresFile",
    "read_as",
    "render_counts",
    "score_name",
]
