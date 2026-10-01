"""The text Evals: did the Target say what it must, and nothing it must not (D21)?

Three rows, one matching rule (phase-5 decision 18): a phrase matches when it
is an NFC-normalised, case-insensitive substring of one of the Target's own `message`
Events (`actor: target`, `role: assistant`). Tool results are never screened, and neither
are the user's or the Simulated User's messages: a Target that quotes a forbidden phrase
back from a tool result has not said it, and a customer who types one has not made the
Target say it. `turn: <n>` restricts any of them to one Turn (decision 7).

Evidence is the `turn` Span ids of the messages that matched, or of every Target message
when none did (decision 18), so a reader goes straight to the words in question.

`forbidden_phrases` is `must_not_say` over the Target-level list (decision 6): the
Manifest's `forbidden_phrases`, plus any the Scenario's own declaration adds. When the
Manifest carries the key — an empty list included — preflight gives every Scenario an
inherited declaration, and an empty list scores `pass` saying the Manifest declares none;
when the key is absent, no Score is added, and a Scenario's own list is screened alone. A
declaration with neither list has nothing to screen and is `unverifiable` /
`eval_not_applicable`, never a vacuous `pass`: only the Manifest saying "none" is a pass.

A message marked `truncated` (an imported proxy row whose response the proxy cut, ticket 26)
holds only the start of what the Target said: a phrase found in it is still found, but a
phrase not found proves nothing, so a Verdict resting on absence — `must_not_say` and
`forbidden_phrases` passing, `must_say_any` failing — is `unverifiable` /
`evidence_truncated` (ADR-0013 §6). The same Verdict over a Turn whose reply was never
logged (`response` not observed) or a message with no content (`content` not observed) is
`unverifiable` / `evidence_missing`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import Field, field_validator

from agentdiag.eval.mechanical import (
    Name,
    Parameters,
    Reading,
    TurnNumber,
    normalised,
    perform_mechanical,
    span_ids,
)
from agentdiag.eval.score import Score
from agentdiag.eval.spec import EvalContext, EvalSpec
from agentdiag.trace.events import Event
from agentdiag.trace.spans import Span, not_observed


class PhraseParameters(Parameters):
    """`must_say_any`, `must_not_say`: the phrases, and optionally one Turn."""

    phrases: list[Name] = Field(min_length=1)
    turn: TurnNumber | None = None

    @field_validator("phrases", mode="before")
    @classmethod
    def _listed(cls, value: Any) -> Any:
        return [value] if isinstance(value, str) else value


class ForbiddenPhraseParameters(PhraseParameters):
    """`forbidden_phrases`: a Scenario's additions to the Manifest's list, which may be none."""

    phrases: list[Name] = Field(default_factory=list)


def _said(message: Event, phrases: list[str]) -> list[str]:
    """The phrases this message contains, by the matching rule."""
    content = normalised(str((message.model_extra or {}).get("content") or ""))
    return [phrase for phrase in phrases if normalised(phrase) in content]


def _found(messages: list[Event], phrases: list[str]) -> str:
    """Which phrases each matching message said, for a rationale."""
    return "; ".join(
        f"{_where(message)}: {_quoted(_said(message, phrases))}" for message in messages
    )


def _where(message: Event) -> str:
    return f"Turn {message.turn}" if message.turn is not None else "a message outside a Turn"


def must_say_any(context: EvalContext) -> Score:
    """`pass` when the Target said at least one of the phrases."""
    return perform_mechanical(MUST_SAY_ANY, context, PhraseParameters, _must_say_any)


def _must_say_any(reading: Reading, parameters: PhraseParameters) -> Score:
    phrases = parameters.phrases
    screened = _messages(reading)
    if isinstance(screened, Score):
        return screened
    messages, read = screened
    matched = [message for message in messages if _said(message, phrases)]
    if not matched and (unproven := _absence_unproven(reading, messages, read)) is not None:
        return unproven
    if not matched:
        return reading.score(
            "fail",
            f"The Target said none of {_quoted(phrases)} in {reading.scope()}",
            evidence=read,
            read=read,
        )
    return reading.score(
        "pass",
        f"The Target said {_found(matched, phrases)}",
        evidence=reading.spans_of(matched),
        read=read,
    )


def must_not_say(context: EvalContext) -> Score:
    """`pass` when the Target said none of the phrases."""
    return perform_mechanical(MUST_NOT_SAY, context, PhraseParameters, _must_not_say)


def _must_not_say(reading: Reading, parameters: PhraseParameters) -> Score:
    return _screened(reading, parameters.phrases, listed="")


def forbidden_phrases(context: EvalContext) -> Score:
    """`pass` when the Target said none of the Manifest's forbidden phrases, nor the
    Scenario's additions."""
    return perform_mechanical(
        FORBIDDEN_PHRASES, context, ForbiddenPhraseParameters, _forbidden_phrases
    )


def _forbidden_phrases(reading: Reading, parameters: ForbiddenPhraseParameters) -> Score:
    manifest = reading.context.forbidden_phrases
    added = parameters.phrases
    phrases = list(dict.fromkeys([*(manifest or []), *added]))
    if manifest is None and not added:
        return reading.score(
            "unverifiable",
            "Neither the Manifest nor this Scenario lists a forbidden phrase, so there is "
            "nothing this Eval could hold the Target to",
            evidence=[],
            read=[],
            reason="eval_not_applicable",
        )
    if not phrases:
        return reading.score(
            "pass",
            "The Manifest declares no forbidden phrases, so there was nothing to screen",
            evidence=[],
            read=[],
        )
    listed = (
        f" (the Manifest's {len(manifest)} and the Scenario's {len(added)})"
        if manifest and added
        else " (the Manifest's)"
        if manifest
        else " (the Scenario's)"
    )
    return _screened(reading, phrases, listed=listed)


def _screened(reading: Reading, phrases: list[str], *, listed: str) -> Score:
    """The Target said none of `phrases`: `must_not_say` and `forbidden_phrases` alike."""
    screened = _messages(reading)
    if isinstance(screened, Score):
        return screened
    messages, read = screened
    matched = [message for message in messages if _said(message, phrases)]
    if matched:
        return reading.score(
            "fail",
            f"The Target said a phrase it must not{listed}: {_found(matched, phrases)}",
            evidence=reading.spans_of(matched),
            read=read,
        )
    if (unproven := _absence_unproven(reading, messages, read)) is not None:
        return unproven
    return reading.score(
        "pass",
        f"The Target said none of {_quoted(phrases)}{listed} in {reading.scope()}",
        evidence=read,
        read=read,
    )


def _messages(reading: Reading) -> tuple[list[Event], list[Span]] | Score:
    """The Target's messages in scope and the `turn` Spans holding them — or the Score that
    says they cannot be screened: below the Fidelity gate, or none at all."""
    messages = reading.target_messages()
    read = reading.spans_of(messages) or reading.turn_spans()
    if (gated := reading.gate(read)) is not None:
        return gated
    if not messages:
        return reading.score(
            "unverifiable",
            f"{reading.scope()} holds no message from the Target, so there is nothing to screen",
            evidence=[],
            read=read,
            reason="evidence_missing",
        )
    return messages, read


def _absence_unproven(reading: Reading, messages: list[Event], read: list[Span]) -> Score | None:
    """Why a phrase's absence proves nothing here, when it does not: a Turn in scope whose
    reply was never logged, or a message with no content (`unverifiable` /
    `evidence_missing`), or a message cut short (`unverifiable` / `evidence_truncated`)."""
    unlogged = [
        span for span in reading.turn_spans() if {"response", "content"} & set(not_observed(span))
    ]
    unlogged += [
        span
        for span in reading.spans_of(
            [m for m in messages if "content" in ((m.model_extra or {}).get("not_observed") or [])]
        )
        if span.span_id not in {turn.span_id for turn in unlogged}
    ]
    if unlogged:
        return reading.score(
            "unverifiable",
            f"{span_ids(unlogged)} hold a reply that was never recorded, so a phrase missing "
            "from what was recorded may have been said",
            evidence=unlogged,
            read=[*read, *(span for span in unlogged if span not in read)],
            reason="evidence_missing",
        )
    cut = [message for message in messages if (message.model_extra or {}).get("truncated")]
    if not cut:
        return None
    return reading.score(
        "unverifiable",
        f"The Target's message in {', '.join(_where(m) for m in cut)} was cut short where it "
        "was recorded, so a phrase missing from it may have been said after the cut",
        evidence=reading.spans_of(cut),
        read=read,
        reason="evidence_truncated",
    )


def _quoted(phrases: list[str]) -> str:
    return ", ".join(repr(phrase) for phrase in phrases)


def _phrases(
    name: str,
    perform_row: Callable[[EvalContext], Score],
    parameters: type[PhraseParameters] = PhraseParameters,
) -> EvalSpec:
    return EvalSpec(
        name=name,
        kind="mechanical",
        min_fidelity="observed",
        primary="phrases",
        answers="stick to the prompt",
        perform=perform_row,
        params_model=parameters,
    )


MUST_SAY_ANY = _phrases("must_say_any", must_say_any)
MUST_NOT_SAY = _phrases("must_not_say", must_not_say)
FORBIDDEN_PHRASES = _phrases("forbidden_phrases", forbidden_phrases, ForbiddenPhraseParameters)

SPECS: tuple[EvalSpec, ...] = (MUST_SAY_ANY, MUST_NOT_SAY, FORBIDDEN_PHRASES)
"""This module's rows, in the catalogue's order (D21)."""


__all__ = [
    "FORBIDDEN_PHRASES",
    "SPECS",
    "ForbiddenPhraseParameters",
    "PhraseParameters",
    "forbidden_phrases",
    "must_not_say",
    "must_say_any",
]
