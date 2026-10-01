"""The Judge: one model call per judged Eval, and the four ways its answer is read (D13).

A Judge is not trusted. It is a component whose output is checked, and every way it can be
wrong has a Verdict of its own (ADR-0003 §3, §4) — the four answer rules:

1. A valid `pass` or `fail` whose cited Span ids all exist in the Trace is taken as given.
2. A `pass` or `fail` citing ids that are not in the Trace has those ids dropped and named
   in the rationale; if nothing citable is left the Score is `unverifiable` /
   `evidence_missing`, because a judgement that cannot point at the Trace claims more than
   it can show.
3. A valid `unverifiable` is taken as returned, with the reason it gave.
4. A schema failure, a refusal, a `max_tokens` cut-off, or any exception from the client is
   `invalid` with `fault_source: judge` — agentdiag's instrument broke, not the Target. No
   retry, ever: a retry that produced a different answer would hide exactly the flakiness a
   reader needs to see (D13).

This module is the call and rule 4: `Judge.ask` sends the request, writes the `judge`
Span, reads the response as the Eval's answer model, and returns a `JudgeAnswer` or a
`JudgeFailure`; `Judge.ask_over` is the one path from a Trial to a call (render the
prompt, then ask). Rules 1 to 3 turn an answer into Scores in `agentdiag.eval.judged_score`,
and what `run.json` records about the Judge is `agentdiag.eval.judged`'s.

Everything the Judge did is written to the Trial's `judgement.jsonl` in the Trace's own
Event format with actor `judge` (ADR-0004 §3): the Span, the request, the response, and an
`error` Event inside the Span when it failed. The Trace itself is never touched after the
Trial ended, so what the Target did and what agentdiag thought about it stay separable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, ValidationError, field_validator

from agentdiag.eval.notes import JudgeNotes
from agentdiag.eval.render import (
    NO_REASON,
    JudgeContext,
    JudgePromptParts,
    render_judge_prompt,
)
from agentdiag.eval.suppressions import Suppression
from agentdiag.eval.template import Value
from agentdiag.model.claude_code import Backend
from agentdiag.model.client import (
    EFFORT_LEVELS,
    Effort,
    ModelClient,
    ModelRequest,
    ModelResponse,
)
from agentdiag.model.prices import COST_CHECK, REPORTED_COST_USD, cost_attributes, cost_check
from agentdiag.scenario.models import EvalDeclaration, JudgeOverride
from agentdiag.simulate.configuration import ReviewerConfiguration
from agentdiag.trace import SpanHandle, TraceWriter
from agentdiag.trace.attributes import JUDGE_ATTEMPTS, JUDGE_FINGERPRINT
from agentdiag.trace.openinference import COST_TOTAL
from agentdiag.types import Actor, FailReason, UnverifiableReason

JUDGE_MAX_TOKENS = 16000
"""Room for a rationale over a long Trace, under the SDK's non-streaming timeout."""

JUDGE_ACTOR: Actor = "judge"
"""Every Event the Judge writes is the Judge's, never the Target's (ADR-0004 §3)."""

STOPPED_WITHOUT_ANSWER = frozenset({"refusal", "max_tokens"})
"""Two ways a response arrives with no judgement in it.

Both are `invalid` with `fault_source: judge`, but D13 lists them as separate causes and
they ask for different things: a refusal is a safety decision about the prompt and wants
the prompt looked at; a `max_tokens` cut-off is agentdiag's own budget and wants
`JUDGE_MAX_TOKENS` raised. The `error` Event says which happened, so a reader never has
to guess."""


class JudgeOutput(BaseModel):
    """One Verdict as the structured-output schema asks the Judge to fill it (D13).

    The base every judged Eval's answer extends: `prompt_adherence` adds the rules it
    exercised, `data_grounding` the claims it checked, `guardrails` one of these per rule.
    """

    verdict: Literal["pass", "fail", "unverifiable"]
    reason: UnverifiableReason | FailReason | None = None
    rationale: str
    evidence: list[str] = Field(default_factory=list)

    @field_validator("reason", mode="before")
    @classmethod
    def _none_is_no_reason(cls, value: object) -> object:
        """The schema spells "no reason" as the string `none`; agentdiag spells it null."""
        return None if value == NO_REASON else value


class JudgeAnswer(BaseModel):
    """What a Judge call returned when it returned a judgement."""

    model_config = {"arbitrary_types_allowed": True}

    output: BaseModel
    """The answer, validated as the parts' `output_model`."""

    resolved_model: str | None
    span_id: str


class JudgeFailure(BaseModel):
    """What a Judge call returned when it did not: rule 4, told in words."""

    detail: str
    resolved_model: str | None = None


Annotate = Callable[[BaseModel | None, str | None], Mapping[str, Any]]
"""What a caller writes as a `note` inside the Judge's Span once the call is over: given the
validated answer (None when the call failed) and the failure's detail."""


def judge_fingerprint(
    parts: JudgePromptParts,
    notes: JudgeNotes | None,
    model: str,
    effort: str | None,
    backend: Backend | None,
    suppressions: Sequence[Suppression] = (),
) -> str:
    """The Judge Fingerprint a judged Score records (ADR-0003 §8): sha256 over the prompt
    version, the prompt text, the notes text, the requested model, the effort, and the
    Backend's kind and CLI version (ticket 19, decision 25), and the Suppressions in force
    for the Trial when there are any (phase-6 decision 16): none adds nothing, so a Judge
    with none hashes as it always did.

    It lives beside the Judge, whose model, effort and Backend it reads, so the Judge can
    say its own (`Judge.fingerprint`); `judged_score` re-exports it for the Scores that
    carry it. Everything that could move a Verdict without the Target moving is in it, so `compare`
    can say "the judgement changed" instead of implying the Target did. The Backend is in
    it because a CLI version wraps the prompt its own way; a replay hashes as `replay`, so a
    replayed Run and a live one differ on every judged Score, which is true.
    """
    joined = "\0".join(
        [
            parts.version,
            parts.template(),
            notes.text if notes else "",
            model,
            effort or "",
            backend.kind if backend else "",
            (backend.cli_version or "") if backend else "",
            *(json.dumps(item.model_dump(mode="json"), sort_keys=True) for item in suppressions),
        ]
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class Judge:
    """The configured model and prompt that performs a judged Eval (`CONTEXT.md`).

    Built once per (model, effort) in a Run, not once per Trial: the model and the prompt
    are Run configuration, and a Judge rebuilt per Trial could silently differ between
    Trials of the same Run. `backend` is the path its calls take (`run.json.judge.backend`,
    ticket 19), carried here only so every Score's Judge Fingerprint covers it; the client
    is what actually takes the path.
    """

    def __init__(
        self,
        client: ModelClient,
        model: str,
        effort: str | None = None,
        *,
        backend: Backend | None,
    ) -> None:
        if (problem := effort_problem(effort)) is not None:
            raise ValueError(problem)
        self.client = client
        self.model = model
        self.effort = effort
        self.backend = backend

    def fingerprint(
        self,
        parts: JudgePromptParts,
        notes: JudgeNotes | None,
        suppressions: Sequence[Suppression] = (),
    ) -> str:
        """This Judge's Fingerprint for one prompt, its calibration notes and the
        Suppressions in force (ADR-0003 §8): the one call `JudgedScores` and the Diagnosis
        both make, so neither spells out the Judge's model, effort and Backend again
        (ticket 21, decision 40)."""
        return judge_fingerprint(parts, notes, self.model, self.effort, self.backend, suppressions)

    def ask(
        self,
        parts: JudgePromptParts,
        prompt: str,
        judgement: TraceWriter,
        *,
        eval: str,
        eval_id: str | None,
        fingerprint: str,
        annotate: Annotate | None = None,
        call: JudgeCall | None = None,
    ) -> JudgeAnswer | JudgeFailure:
        """One model call, recorded into `judgement.jsonl` as it goes (rule 4 lives here).

        The Span stays open until the answer is read, so an `error` for a refusal, a
        cut-off or a schema failure lands inside it, and `annotate` — Diagnosis's `note` —
        does too.

        `fingerprint` is the Judge Fingerprint the caller's Scores record (ADR-0003 §8),
        written on the Span's start attributes so `judgement.jsonl` says which Judge made
        each call without a reader joining it to `scores.json`, and carried on the request
        for a client that records it (ticket 21, decision 40).

        `call` is where the call is recorded and how big it may be: a `judge` Span of actor
        `judge` into `judgement.jsonl` unless the caller says otherwise. The stop check says
        otherwise (phase-5 decision 51): an `llm_call` Span of actor `agentdiag` in the
        Trace itself, capped at its own budget, since it happens during the Trial.
        """
        shape = call or JudgeCall(name=f"judge {eval}")
        actor = shape.actor
        request = self._request(parts, prompt, fingerprint, shape.max_tokens)
        attributes: dict[str, Any] = {"agentdiag.eval": eval}
        if eval_id is not None:
            attributes["agentdiag.eval_id"] = eval_id
        attributes[JUDGE_FINGERPRINT] = fingerprint
        span = model_span(judgement, shape, self.model, attributes)
        span.event("request", actor=actor, body=request.body())

        try:
            response = self.client.complete(request)
        except Exception as exc:
            # Every failure of the client is the same fact: agentdiag could not obtain a
            # judgement. A `ReplayMismatch` here is agentdiag's recording being wrong,
            # which is the Judge's fault source by the contract, not the Target's.
            kind, detail = call_failed(span, actor, exc)
            failure = JudgeFailure(detail=f"The Judge call failed: {kind}: {detail}")
            _annotate(span, annotate, None, failure.detail, actor)
            span.end(status="error", error=(kind, detail))
            return failure

        span.event("response", actor=actor, body=response.body)
        answer = self._read(parts, response, span, actor, shape.max_tokens)
        if isinstance(answer, JudgeFailure):
            _annotate(span, annotate, None, answer.detail, actor)
        else:
            _annotate(span, annotate, answer.output, None, actor)
        span.end(attributes=response_attributes(response))
        return answer

    def ask_over(
        self,
        context: JudgeContext,
        parts: JudgePromptParts,
        judgement: TraceWriter,
        *,
        fingerprint: str,
        eval_id: str | None = None,
        annotate: Annotate | None = None,
        call: JudgeCall | None = None,
        **values: Value,
    ) -> JudgeAnswer | JudgeFailure | None:
        """Render the parts over the Trial, then ask: the one path every judged Eval, the
        Diagnosis, the stop check and the reviewer take. None, and no call, when the prompt
        cannot be rendered — the parts require the Target's system prompt and the Trace
        holds none."""
        prompt = render_judge_prompt(parts, context, **values)
        if prompt is None:
            return None
        return self.ask(
            parts,
            prompt,
            judgement,
            eval=parts.eval,
            eval_id=eval_id,
            fingerprint=fingerprint,
            annotate=annotate,
            call=call,
        )

    def _read(
        self,
        parts: JudgePromptParts,
        response: ModelResponse,
        span: SpanHandle,
        actor: Actor,
        max_tokens: int,
    ) -> JudgeAnswer | JudgeFailure:
        """The response as the parts' output model, or why it is not one."""
        if response.stop_reason in STOPPED_WITHOUT_ANSWER:
            error_type, detail = stopped_without_answer(
                response, error_type="Judge", spoken="Judge", max_tokens=max_tokens
            )
            span.event("error", actor=actor, error_type=error_type, message=detail)
            return JudgeFailure(
                detail=f"The Judge returned no judgement: {detail}",
                resolved_model=response.resolved_model,
            )
        try:
            # The Claude Code backend hands the answer over already parsed; the Messages
            # API puts it in a text block. One schema validates either (decision 23).
            output = (
                parts.output_model.model_validate(response.structured_output)
                if response.structured_output is not None
                else parts.output_model.model_validate_json(response.text())
            )
        except (ValidationError, ValueError) as exc:
            span.event(
                "error",
                actor=actor,
                error_type=type(exc).__name__,
                message=f"the Judge's output did not fit the schema: {exc}",
            )
            return JudgeFailure(
                detail=f"The Judge's output did not fit the schema: {exc}",
                resolved_model=response.resolved_model,
            )
        return JudgeAnswer(
            output=output, resolved_model=response.resolved_model, span_id=span.span_id
        )

    def _request(
        self, parts: JudgePromptParts, prompt: str, fingerprint: str, max_tokens: int
    ) -> ModelRequest:
        # Checked once in `__init__`, so the closed type is honest rather than asserted.
        effort = cast(Effort | None, self.effort)
        return ModelRequest(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            output_schema=parts.output_schema,
            effort=effort,
            fingerprint=fingerprint,
        )


class JudgeCall(BaseModel):
    """Where one of agentdiag's own model calls is recorded and how many tokens it may take:
    a Judge call's `judge` Span in `judgement.jsonl` by default, a stop check's `llm_call` of
    actor `agentdiag` in the Trace (decision 51), a Simulated User's `llm_call` of actor
    `simulated_user` in the Trace (decision 48)."""

    kind: Literal["judge", "llm_call"] = "judge"
    actor: Actor = JUDGE_ACTOR
    name: str
    max_tokens: int = JUDGE_MAX_TOKENS


def model_span(
    trace: TraceWriter, call: JudgeCall, model: str, attributes: Mapping[str, Any]
) -> SpanHandle:
    """Open one of agentdiag's own model Spans with the GenAI start attributes every one of
    them carries, then the caller's own."""
    return trace.span(
        call.kind,
        actor=call.actor,
        name=call.name,
        fidelity="instrumented",
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "anthropic",
            "gen_ai.request.model": model,
            "gen_ai.request.max_tokens": call.max_tokens,
            **attributes,
        },
    )


def call_failed(span: SpanHandle, actor: Actor, exc: Exception) -> tuple[str, str]:
    """A client that raised: one `error` Event inside the call's Span, and what it said.
    The caller ends the Span (after its own `note`, when it writes one)."""
    kind, detail = type(exc).__name__, str(exc)
    span.event("error", actor=actor, error_type=kind, message=detail)
    return kind, detail


def _annotate(
    span: SpanHandle,
    annotate: Annotate | None,
    output: BaseModel | None,
    failure: str | None,
    actor: Actor,
) -> None:
    if annotate is not None:
        span.event("note", actor=actor, **dict(annotate(output, failure)))


def effort_problem(effort: str | None, whose: str = "The Judge's") -> str | None:
    """Why an effort cannot be sent, or None: the one check, for the Run's Judge, an
    override's and the Simulated User's effort alike (`whose` names which)."""
    if effort is None or effort in EFFORT_LEVELS:
        return None
    return f"{whose} effort is one of {sorted(EFFORT_LEVELS)}; got {effort!r}"


def resolve_override(
    override: JudgeOverride | None, model: str, effort: str | None
) -> tuple[str, str | None]:
    """The (model, effort) a declaration's Judge runs on: its override's, each field it
    leaves unsaid the Run's (D14, D19). The one resolution preflight and execution share."""
    if override is None:
        return model, effort
    return (
        override.model or model,
        override.effort if override.effort is not None else effort,
    )


class Judges:
    """The Run's Judges: its default, and one per distinct (model, effort) an Eval
    declaration overrides it with (D14, D19), all over the Run's one client."""

    def __init__(
        self,
        client: ModelClient,
        model: str,
        effort: str | None = None,
        *,
        notes: JudgeNotes | None = None,
        overrides: Iterable[JudgeOverride] = (),
        backend: Backend | None,
        reviewer: ReviewerConfiguration | None = None,
        manifest_prompts: Mapping[str, str] | None = None,
        suppressions: Sequence[Suppression] = (),
    ) -> None:
        self.client = client
        self.notes = notes
        self.manifest_prompts = dict(manifest_prompts or {})
        """The text of the Manifest's path prompt pointers (note 7), for every context."""
        self.suppressions = list(suppressions)
        """The Manifest's Suppressions (decision 16); each context picks those in force."""
        self.backend = backend
        self.default = Judge(client, model, effort, backend=backend)
        self.reviewer = (
            Judge(client, reviewer.model, reviewer.effort, backend=backend) if reviewer else None
        )
        """The Simulated User reviewer's Judge (D28, phase-5 decision 57), on its own,
        stronger model over the same client; None when no simulated Scenario is selected."""
        self._by_pair: dict[tuple[str, str | None], Judge] = {(model, effort): self.default}
        for override in overrides:
            self._for_pair(*resolve_override(override, model, effort))

    def for_declaration(self, declaration: EvalDeclaration) -> Judge:
        """The Judge this declaration runs on: its override's, or the Run's default."""
        return self._for_pair(
            *resolve_override(declaration.judge, self.default.model, self.default.effort)
        )

    def _for_pair(self, model: str, effort: str | None) -> Judge:
        key = (model, effort)
        if key not in self._by_pair:
            self._by_pair[key] = Judge(self.client, model, effort, backend=self.backend)
        return self._by_pair[key]


def stopped_without_answer(
    response: ModelResponse, *, error_type: str, spoken: str, max_tokens: int
) -> tuple[str, str]:
    """Which of the two no-answer stops happened, and what a reader should do about it.

    D13 counts them as separate causes: a refusal is a decision about the prompt, and a
    `max_tokens` cut-off is agentdiag's own budget. Naming them apart is what lets one be
    fixed by editing a prompt and the other by raising a number. `error_type` opens the
    error's type and `spoken` names the caller in its words: `Judge` and `Judge`, or
    `SimulatedUser` and `Simulated User` (decision 48).
    """
    if response.stop_reason == "max_tokens":
        return (
            f"{error_type}RanOutOfTokens",
            f"the {spoken} ran out of tokens before answering (max_tokens {max_tokens})",
        )
    category = (response.stop_details or {}).get("category")
    suffix = f" (category {category})" if category else ""
    return f"{error_type}Refused", f"the {spoken} refused to answer{suffix}"


def response_attributes(
    response: ModelResponse, *, attempts_name: str = JUDGE_ATTEMPTS
) -> dict[str, Any]:
    """The `span/end` attributes an `llm_call` carries, for agentdiag's own model Spans:
    the Judge's, the stop check's and the Simulated User's (whose attempts are counted under
    `agentdiag.simulated_user.attempts`, decision 48)."""
    usage = response.usage
    attributes: dict[str, Any] = {
        "gen_ai.response.model": response.resolved_model,
        "gen_ai.response.finish_reasons": [response.stop_reason],
    }
    identifier = response.body.get("id")
    if identifier is not None:
        attributes["gen_ai.response.id"] = identifier
    for key, name in (
        ("input_tokens", "gen_ai.usage.input_tokens"),
        ("output_tokens", "gen_ai.usage.output_tokens"),
        ("cache_read_input_tokens", "gen_ai.usage.cache_read.input_tokens"),
        ("cache_creation_input_tokens", "gen_ai.usage.cache_write.input_tokens"),
    ):
        if usage.get(key) is not None:
            attributes[name] = usage[key]
    # Priced at capture, as the Target's calls are, so a later price edit cannot change
    # what a recorded judgement cost (phase-5 decision 4).
    costs = cost_attributes(response.resolved_model or response.requested_model, usage)
    attributes.update(costs)
    if response.reported_cost_usd is not None:
        # Checked, never substituted: a `differs` is evidence for a human to edit the
        # table, and nothing here edits it (decision 27).
        attributes[REPORTED_COST_USD] = response.reported_cost_usd
        attributes[COST_CHECK] = cost_check(costs.get(COST_TOTAL), response.reported_cost_usd)
    report = response.body.get("claude_code")
    attempts = report.get("attempts") if isinstance(report, dict) else None
    if attempts:
        # The rejected attempts plus the answer: said on the Span, so a reader of `show`
        # sees that the CLI re-prompted without opening the body (decision 42).
        attributes[attempts_name] = len(attempts) + 1
    return attributes


__all__ = [
    "JUDGE_ACTOR",
    "JUDGE_MAX_TOKENS",
    "STOPPED_WITHOUT_ANSWER",
    "Annotate",
    "Judge",
    "JudgeAnswer",
    "JudgeCall",
    "JudgeFailure",
    "JudgeOutput",
    "Judges",
    "call_failed",
    "effort_problem",
    "judge_fingerprint",
    "model_span",
    "resolve_override",
    "response_attributes",
    "stopped_without_answer",
]
