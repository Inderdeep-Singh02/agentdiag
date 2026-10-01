"""Turning one finished Trial into its Scores (D19, D24, ADR-0003 §3).

One function decides what a Trial's declared Evals produced, and it decides in one order:

1. **The Trial did not finish.** Nothing was judged, so nothing may claim to have been.
   A `TrialFailure` says which kind of not-finishing it was, and every declared Eval takes
   the matching Verdict — the Target's fault is `incomplete`, agentdiag's own is `invalid`.
2. **The Eval is not registered, or registered and not implemented yet.** `unverifiable`
   with reason `eval_not_applicable`, the open-registry behaviour D19 asks for: a Suite
   written for a newer agentdiag still runs and the Scorecard says plainly which
   judgements were not made. A judged catalogue row with no prompt module is the same fact
   told from the other side, so it gets the same Score, and never a Judge call it has no
   prompt for.
3. **The Eval is registered and mechanical.** Its `perform` gets an `EvalContext` — the
   Trace with blobs resolved, its Spans, its Fidelity, and the two Manifest fields an Eval
   reads (`forbidden_phrases`, the tools' kinds) as plain data — and returns the Score. No
   model, no tokens (ticket 04).
4. **The Eval is registered and judged.** Its prompt module (`agentdiag.eval.judged`, by
   name) gets a `JudgeContext`, the Judge the declaration runs on — the Run's default, or
   the one its `judge` override names (D14, D19) — and the `judgement.jsonl` to write
   into, and returns its Scores: one, or one per guardrail rule (ticket 05).

Then, **when the Scenario has a `simulate` Turn and a Score is `fail`, the Simulated User
reviewer** (D28, phase-5 decision 57): one call on the reviewer's Judge over the Simulated
User's Turns, which may rewrite every `fail` to `invalid` / `simulated_user` with a
direction. It runs after the Evals and before the recording check, so its recorded exchange
is accounted for with theirs.

Then, **when at least one declared Eval is judged and implemented, or a review applied,
the Trial's Diagnosis** (D24, amended decision 57): one more call through the Run's
default Judge over the same Trace and every Score above — the review's rewrite included,
and the review's finding under its own heading — written into `judgement.jsonl` and never
returned as a Score. `execute.py` hands this
function the Trace it recorded and takes back the Scores, so the Trial loop never learns
that a Judge exists.

A judged Eval reaching here with no Judge cannot happen after preflight, which refuses a
Run that would need one and cannot build one (D16). If it does anyway, that is agentdiag's
own bug, and it is recorded as `invalid` / `fault_source: agentdiag` rather than crashed
on: a Run that got this far has Traces worth keeping.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from agentdiag.eval import diagnosis, simulated_user_review
from agentdiag.eval.judge import JUDGE_ACTOR, Judges
from agentdiag.eval.judged import judge_function
from agentdiag.eval.registry import REGISTRY, UNKNOWN_EVAL_CODE, is_implemented, is_judged
from agentdiag.eval.render import JudgeContext
from agentdiag.eval.score import Score, ScoreSource
from agentdiag.eval.spec import EvalContext
from agentdiag.scenario.models import EvalDeclaration, Scenario, SimulateSpec
from agentdiag.trace import Event, Span, TraceWriter, project_spans, resolve_blobs
from agentdiag.types import (
    FaultSource,
    Fidelity,
    IncompleteReason,
    TerminationReason,
    ToolKind,
)


class TrialFailure(BaseModel):
    """A Trial that did not finish, and whose fault that was (D22, ADR-0003 §3).

    One object rather than two parallel optionals, because the termination reason and the
    Verdict are the same fact told twice: a Target that raised terminates `target_error`
    and scores `incomplete`; agentdiag's own replay failing terminates `agentdiag_error`
    and scores `invalid` with `fault_source: agentdiag`. Keeping them together makes a
    mismatched pair unrepresentable.
    """

    termination: TerminationReason
    verdict: Literal["incomplete", "invalid"]
    reason: IncompleteReason | None = None
    fault_source: FaultSource | None = None
    detail: str
    """What happened, in the words the Trace's `trace/end` and the Score's rationale share."""


def target_error(detail: str) -> TrialFailure:
    """The Target raised: a re-run signal, and nothing about agentdiag."""
    return TrialFailure(
        termination="target_error", verdict="incomplete", reason="target_error", detail=detail
    )


def agentdiag_error(detail: str) -> TrialFailure:
    """agentdiag broke its own test (ADR-0003 §3): its own fault, helping no one."""
    return TrialFailure(
        termination="agentdiag_error",
        verdict="invalid",
        fault_source="agentdiag",
        detail=detail,
    )


def cancelled(detail: str = "the Run was cancelled") -> TrialFailure:
    """A Ctrl-C: a fact about the operator, never about the Target (D22)."""
    return TrialFailure(
        termination="cancelled", verdict="incomplete", reason="cancelled", detail=detail
    )


def timed_out(detail: str) -> TrialFailure:
    """The Trial ran past its time: a re-run signal, like a Target that raised."""
    return TrialFailure(
        termination="timeout", verdict="incomplete", reason="timeout", detail=detail
    )


def simulated_user_failed(detail: str) -> TrialFailure:
    """The Simulated User broke the Trial (D28): not the Target's fault, so `invalid`."""
    return TrialFailure(
        termination="simulated_user_error",
        verdict="invalid",
        fault_source="simulated_user",
        detail=detail,
    )


class RecordingCheck(Protocol):
    """The replayed Run's question to its recording, asked around the Diagnosis.

    Before it: were the Evals' exchanges all used? If not, the Trial took a path nobody
    recorded, and the Scores are overruled. After it: was the Diagnosis's? If not, that is
    reported and nothing else happens, because the Diagnosis never touches a Score or a
    count (D24; phase-5 interfaces, ticket 05 Spec review). `execute.py` implements it over
    the Run's cursor; this package never learns that a recording exists.
    """

    def before_diagnosis(self, scores: list[Score]) -> list[Score]:
        """The Scores, overruled when the Evals left a recorded exchange unused."""
        ...

    def after_diagnosis(self) -> str | None:
        """What the Diagnosis left unused, as a sentence, or None."""
        ...


def perform_evals(
    scenario: Scenario,
    *,
    trace_events: Sequence[Event],
    fidelity: Fidelity,
    failure: TrialFailure | None = None,
    judges: Judges | None = None,
    judgement_path: Path | None = None,
    forbidden_phrases: list[str] | None,
    tool_kinds: Mapping[str, ToolKind],
    recording: RecordingCheck | None = None,
    clock: Callable[[], int] | None = None,
) -> list[Score]:
    """Every Score the Scenario's declared Evals produced, reviewed when the Scenario is
    simulated and a Score failed, then the Trial's Diagnosis.

    `judgement.jsonl` is opened only if a judged Eval actually runs or the reviewer does,
    and closed once: a Trial that judged nothing must not leave an empty file that `show`
    would then render as a judgement that happened (ADR-0005 §2).

    `recording` is asked about the Evals' exchanges before the Diagnosis and about the
    Diagnosis's after it, so a Diagnosis the recording does not fit is a `note`, never
    an overruled Score.

    `forbidden_phrases` and `tool_kinds` are the Manifest's, extracted by preflight: this
    package never imports the Manifest (phase-5 interfaces, `EvalContext`). `clock` is the
    Run's, so a Run reproduced with a fixed clock reproduces its `judgement.jsonl` too.
    """
    judged = failure is None and any(is_judged(d.eval) for d in scenario.evals)
    opened: list[TraceWriter] = []

    def judgement_file() -> TraceWriter | None:
        """`judgement.jsonl`, opened on first use: by a judged Eval, or by the reviewer."""
        if judgement_path is None:
            return None
        if not opened:
            opened.append(_open_judgement(judgement_path, trace_events, clock))
        return opened[0]

    trace = _Trace(trace_events, fidelity, forbidden_phrases, dict(tool_kinds))
    try:
        judgement = judgement_file() if judged and judges is not None else None
        scores: list[Score] = []
        for declaration in scenario.evals:
            scores.extend(
                _score_one(
                    scenario,
                    declaration,
                    trace=trace,
                    failure=failure,
                    judges=judges,
                    judgement=judgement,
                )
            )
        review: simulated_user_review.ReviewOutput | None = None
        spec = scenario.simulate
        reviewer = judges.reviewer if judges is not None else None
        if (
            failure is None
            and spec is not None
            and reviewer is not None
            and judges is not None
            and any(score.verdict == "fail" for score in scores)
            and (reviewing := judgement_file()) is not None
        ):
            scores, review = _review(scenario, spec, trace, judges, reviewing, scores)
            judgement = reviewing
        if recording is not None:
            scores = recording.before_diagnosis(scores)
        # A Diagnosis for every Trial a judged Eval judged, and for every Trial whose review
        # applied: the reviewer's finding is a Judge's word, and the `invalid` it caused
        # wants its why (amended decision 57).
        if (judged or review is not None) and judgement is not None and judges is not None:
            _diagnose(scenario, trace, judges, judgement, scores, review)
        left = recording.after_diagnosis() if recording is not None else None
        if left is not None and judgement is not None:
            judgement.event("note", actor="agentdiag", about="recording", text=left)
        return scores
    finally:
        for written in opened:
            written.end("completed")
            written.close()


def _review(
    scenario: Scenario,
    spec: SimulateSpec,
    trace: _Trace,
    judges: Judges,
    judgement: TraceWriter,
    scores: list[Score],
) -> tuple[list[Score], simulated_user_review.ReviewOutput | None]:
    """The Simulated User reviewer over a simulated Trial with a `fail` (D28). Whatever it
    does, a Score it does not rewrite stands as it was."""
    assert judges.reviewer is not None
    context = trace.judge_context(
        scenario, EvalDeclaration(eval=simulated_user_review.ABOUT), judges
    )
    try:
        return simulated_user_review.review(
            context, judges.reviewer, judgement, scores=scores, spec=spec
        )
    except Exception as exc:
        # The Judge turns every failure of its own call into Events; reaching here is
        # agentdiag's own bug, and an instrument failing never changes a Verdict.
        judgement.event(
            "note",
            actor=JUDGE_ACTOR,
            about=simulated_user_review.ABOUT,
            text=f"No review was applied: agentdiag raised {type(exc).__name__}: {exc}",
        )
        return scores, None


def _diagnose(
    scenario: Scenario,
    trace: _Trace,
    judges: Judges,
    judgement: TraceWriter,
    scores: list[Score],
    review: simulated_user_review.ReviewOutput | None = None,
) -> None:
    """The Trial's Diagnosis (D24). Whatever it does, the Scores above stand unchanged."""
    context = trace.judge_context(scenario, EvalDeclaration(eval=diagnosis.ABOUT), judges)
    try:
        diagnosis.judge(context, judges.default, judgement, scores=scores, review=review)
    except Exception as exc:
        # The Judge turns every failure of its own call into Events; reaching here is
        # agentdiag's own bug, and it still must not cost the Trial its Scores.
        judgement.event(
            "note",
            actor=JUDGE_ACTOR,
            **diagnosis.nothing_produced(f"agentdiag raised {type(exc).__name__}: {exc}"),
        )


def overrule(scores: Sequence[Score], detail: str) -> list[Score]:
    """Replace a finished Trial's Scores when agentdiag discovers its own fault afterwards.

    Some of agentdiag's failures are only visible once the Evals have run — an unconsumed
    recording is the one Phase 4 has. Re-scoring through `perform_evals` would be the
    obvious fix and is the wrong one: it would re-open `judgement.jsonl` and overwrite the
    Judge's request and response, which ADR-0003 §7 keeps beside the Score so a disputed
    Verdict can be re-examined without re-running the Judge. So the Scores are rewritten
    here, in memory, and every file the Trial already wrote is left exactly as it is.

    What each Eval had said is carried into the new rationale rather than discarded: the
    Verdict is `invalid` because agentdiag broke its own test, but "the Judge scored pass"
    is still a fact a reader needs to interpret the Run.
    """
    return [
        Score(
            eval=score.eval,
            eval_id=score.eval_id,
            verdict="invalid",
            rationale=(
                f"agentdiag could not run the Trial: {detail} "
                f"The {score.source.kind} scored {score.verdict}"
                + ("; see judgement.jsonl" if score.source.kind == "judge" else "")
                + "."
            ),
            evidence=[],
            fault_source="agentdiag",
            fault_direction="none",
            source=score.source,
            fidelity=score.fidelity,
            shared_model=score.shared_model,
        )
        for score in scores
    ]


def _open_judgement(
    path: Path, trace_events: Sequence[Event], clock: Callable[[], int] | None
) -> TraceWriter:
    """The Judge's own file, opened on the Trace's identity (ADR-0004 §3).

    Same `trace_id` as the Trace it judged, so the two files join without a third thing
    holding the pairing; a rescore's file therefore names the source Run's Trial, whose
    Trace it judged.
    """
    start = next((event for event in trace_events if event.type == "trace/start"), None)
    fields = (start.model_extra or {}) if start is not None else {}
    writer = TraceWriter(path, clock=clock)
    writer.start(
        trace_id=str(fields.get("trace_id", "")),
        scenario=str(fields.get("scenario", "")),
        run=str(fields.get("run", "")),
        trial=int(fields.get("trial", 1)),
    )
    return writer


class _Trace:
    """The finished Trace, read once however many Evals read it."""

    def __init__(
        self,
        events: Sequence[Event],
        fidelity: Fidelity,
        forbidden_phrases: list[str] | None,
        tool_kinds: dict[str, ToolKind],
    ) -> None:
        self.events = events
        self.fidelity = fidelity
        self.forbidden_phrases = forbidden_phrases
        self.tool_kinds = tool_kinds
        self._resolved: tuple[list[Event], list[Span]] | None = None

    def _read(self) -> tuple[list[Event], list[Span]]:
        if self._resolved is None:
            resolved = resolve_blobs(self.events)
            self._resolved = (resolved, project_spans(resolved))
        return self._resolved

    def judge_context(
        self, scenario: Scenario, declaration: EvalDeclaration, judges: Judges
    ) -> JudgeContext:
        """What a judged Eval reads: the same Trace, and the Run's calibration notes."""
        events, spans = self._read()
        return JudgeContext(
            scenario=scenario,
            declaration=declaration,
            events=events,
            spans=spans,
            fidelity=self.fidelity,
            notes=judges.notes,
            tool_kinds=self.tool_kinds,
            manifest_prompts=judges.manifest_prompts,
            suppressions=judges.suppressions,
        )

    def context(self, scenario: Scenario, declaration: EvalDeclaration) -> EvalContext:
        """What a mechanical Eval reads: blobs resolved, Spans projected, once per Trial."""
        events, spans = self._read()
        return EvalContext(
            scenario=scenario,
            declaration=declaration,
            events=events,
            spans=spans,
            fidelity=self.fidelity,
            forbidden_phrases=self.forbidden_phrases,
            tool_kinds=self.tool_kinds,
        )


def _score_one(
    scenario: Scenario,
    declaration: EvalDeclaration,
    *,
    trace: _Trace,
    failure: TrialFailure | None,
    judges: Judges | None,
    judgement: TraceWriter | None,
) -> list[Score]:
    """What one declared Eval produced, in the order this module's docstring gives."""
    name, eval_id = declaration.eval, declaration.id
    fidelity = trace.fidelity
    if failure is not None:
        return [_from_failure(name, eval_id, fidelity=fidelity, failure=failure)]

    spec = REGISTRY.get(name)
    if spec is None:
        return [
            _mechanical_score(
                name,
                eval_id,
                fidelity=fidelity,
                verdict="unverifiable",
                rationale=f"No Eval named {name!r} is registered in this agentdiag",
                reason="eval_not_applicable",
            )
        ]

    if not is_implemented(name):
        return [
            _mechanical_score(
                name,
                eval_id,
                fidelity=fidelity,
                verdict="unverifiable",
                rationale=(
                    f"{name!r} is a registered {spec.kind} Eval with no implementation in "
                    "this agentdiag yet"
                ),
                reason="eval_not_applicable",
            )
        ]

    try:
        if spec.kind == "judged":
            if judges is None or judgement is None:
                raise _NoJudge(
                    f"{name!r} is a judged Eval and this Run built no Judge; preflight should "
                    "have refused the Run"
                )
            context = trace.judge_context(scenario, declaration, judges)
            return judge_function(name)(context, judges.for_declaration(declaration), judgement)
        if spec.perform is None:
            raise RuntimeError(f"{name!r} is registered as implemented and has no perform")
        return [spec.perform(trace.context(scenario, declaration))]
    except Exception as exc:
        # An Eval that raised is agentdiag's own bug, never the Target's: the Trace stays
        # worth keeping, so the Run records it rather than crashing on it. The Judge's own
        # failures never reach here; `Judge.ask` turns them into `invalid` / `judge`.
        rationale = (
            str(exc)
            if isinstance(exc, _NoJudge)
            else (f"agentdiag's {name} raised {type(exc).__name__}: {exc}")
        )
        return [
            _mechanical_score(
                name,
                eval_id,
                fidelity=fidelity,
                verdict="invalid",
                rationale=rationale,
                fault_source="agentdiag",
                fault_direction="none",
            )
        ]


class _NoJudge(RuntimeError):
    """A judged Eval reached execution with no Judge to ask: agentdiag's own bug."""


def _from_failure(
    name: str, eval_id: str | None, *, fidelity: Fidelity, failure: TrialFailure
) -> Score:
    """The Verdict a Trial that did not finish forces on every Eval it declared."""
    if failure.verdict == "invalid":
        return _mechanical_score(
            name,
            eval_id,
            fidelity=fidelity,
            verdict="invalid",
            rationale=f"agentdiag could not run the Trial: {failure.detail}",
            fault_source=failure.fault_source,
            fault_direction="none",
        )
    return _mechanical_score(
        name,
        eval_id,
        fidelity=fidelity,
        verdict="incomplete",
        rationale=f"The Trial ended {failure.reason}: {failure.detail}",
        reason=failure.reason,
    )


def _mechanical_score(
    name: str,
    eval_id: str | None,
    *,
    fidelity: Fidelity,
    verdict: str,
    rationale: str,
    **cause: Any,
) -> Score:
    """One Score that no model produced, with whichever cause its Verdict allows.

    `cause` carries `reason`, or `fault_source` and `fault_direction`; `Score`'s validator
    is what refuses a pairing the Verdict does not permit (ADR-0003 §3), so there is no
    second rule here to fall out of step with it.
    """
    return Score(
        eval=name,
        eval_id=eval_id,
        verdict=verdict,  # type: ignore[arg-type]
        rationale=rationale,
        evidence=[],
        source=ScoreSource(kind="mechanical", code=UNKNOWN_EVAL_CODE),
        fidelity=fidelity,
        **cause,
    )


__all__ = [
    "RecordingCheck",
    "TrialFailure",
    "agentdiag_error",
    "cancelled",
    "overrule",
    "perform_evals",
    "simulated_user_failed",
    "target_error",
    "timed_out",
]
