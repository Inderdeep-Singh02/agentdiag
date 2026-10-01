"""The Simulated User: one interface, two implementations, room for a third (D26, D27).

`SimulatedUser.next(events, trace)` is given every Event of the Trial so far (as written,
blobs unresolved) and the Trace to write its own Events into, and returns the next user
message or a `Stop` with the termination it stops for (phase-5 decision 46). Two
implementations ship:

- `ScriptedSimulatedUser(messages)` returns its messages in order and, when they run out
  before the Scenario's stop criterion held, stops `simulated_user_error`: a script too
  short for its Scenario is the Simulated User's fault, not the Target's. The driver-loop
  tests use it; no Suite can reach it in v1.
- `ModelSimulatedUser` asks a model through the Run's one `ModelClient` (decisions 48, 49):
  a recorded template prompt, a structured answer `{"message": …}` (the driver loop reads
  it for the stop token), and rule 4 as the Judge has it — a refusal, a `max_tokens`
  cut-off, a schema failure or a client exception is `simulated_user_error`, never a
  retry, except the one resend without a sampling parameter the API refused (decision 53).
  A replay that does not fit is agentdiag's, and ends the Trial `agentdiag_error`.

Nothing here assumes the message comes from a model call: a coding-agent driver is the
third implementation this interface leaves room for, and a gpt-4o customer (`{message,
done, reason}`) is the precedent — `done` and `reason` are the `Stop`.

**The prompt is one template, recorded whole** (`simulated_user.v1`, `PARTS.own_template`,
decision 54): the system prompt says who the Simulated User is and what it is told —
`render_simulate_spec`, the one renderer this prompt and the reviewer's share — and the one
user message carries the conversation so far. One message, not a transcript of `user` and
`assistant` turns: the Claude Code Backend sends one user message and refuses anything else
(ticket 19, decision 24), and the Simulated User's calls take the Run's one Backend
whichever it is, so its request is shaped once for both.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast

from pydantic import BaseModel, ValidationError

from agentdiag.eval.judge import (
    STOPPED_WITHOUT_ANSWER,
    JudgeCall,
    call_failed,
    model_span,
    response_attributes,
    stopped_without_answer,
)
from agentdiag.eval.render import JudgePromptParts
from agentdiag.eval.template import Value, fill
from agentdiag.model.client import (
    Effort,
    ModelClient,
    ModelRequest,
    ModelResponse,
    SamplingNotSupported,
)
from agentdiag.model.replay import RecordingNotConsumed, ReplayMismatch
from agentdiag.scenario.models import Scenario, SimulateSpec
from agentdiag.simulate.configuration import SimulatedUserConfiguration
from agentdiag.trace import Event, SpanHandle, TraceWriter, resolve_blobs
from agentdiag.trace.attributes import (
    SAMPLING_PREFIX,
    SIMULATED_USER_ATTEMPTS,
    SIMULATED_USER_FINGERPRINT,
)
from agentdiag.types import Actor, TerminationReason

ACTOR: Actor = "simulated_user"
"""Every Event the Simulated User writes is its own (ADR-0004 §3, D27)."""

PROMPT_VERSION = "simulated_user.v1"

SIMULATED_USER_MAX_TOKENS = 2000
"""Room for one message as a person writes it, and for thinking before it."""

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
    "additionalProperties": False,
}
"""The answer is structured output (decision 48, the author's instruction of 2026-09-25): on
`claude_code` the CLI then checks it and re-prompts once, and the rejected attempt is in the
record (`agentdiag.simulated_user.attempts`)."""


class NextMessage(BaseModel):
    """The next user message, to be delivered to the Target as its own Turn."""

    text: str


class Stop(BaseModel):
    """The Simulated User stops, and says why (D22): `simulated_user_error` from either
    implementation; `stop_token` is the driver loop's to call (amended decision 48)."""

    termination: TerminationReason
    detail: str


class SimulatedUser(Protocol):
    """The component that plays the user in an adaptive Scenario (`CONTEXT.md`)."""

    def next(self, events: Sequence[Event], trace: TraceWriter) -> NextMessage | Stop:
        """The next message given the Trial so far, or a stop with its reason."""
        ...


class ScriptedSimulatedUser:
    """Messages in order, then a stop: the implementation the driver-loop tests use."""

    def __init__(self, messages: Sequence[str]) -> None:
        self.messages = list(messages)
        self._given = 0

    def next(self, events: Sequence[Event], trace: TraceWriter) -> NextMessage | Stop:
        if self._given >= len(self.messages):
            return Stop(
                termination="simulated_user_error",
                detail=f"the scripted Simulated User ran out of messages after {self._given}",
            )
        self._given += 1
        return NextMessage(text=self.messages[self._given - 1])


# --- the prompt (decision 48) ---

SPEC_TEMPLATE = """\
{?persona}## Who you are

{persona}

{/persona}## Your goal

{goal}

## What you know

{?known_facts}{*known_facts}{key}: {value}
{/known_facts}{/known_facts}{!known_facts}Nothing beyond your goal.{/known_facts}

## What you do not know

{?unknown_facts}These are true, but the person you are playing does not know them. Never \
state them, hint at them or ask about them.

{*unknown_facts}{key}: {value}
{/unknown_facts}{/unknown_facts}{!unknown_facts}Nothing is kept from you.{/unknown_facts}

## How you behave

{?hints}{*hints}{number}. {text}
{/hints}{/hints}{!hints}No further guidance: behave as this person would.{/hints}"""
"""What a `simulate` Turn tells the Simulated User, as it reads it: the persona when
declared, the goal, the known facts as `key: value` lines sorted by key, the unknown facts
with the rule never to reveal them, and the hints numbered in authored order, verbatim —
the numbering and the order are what make the rendering deterministic (D27)."""

SYSTEM_TEMPLATE = (
    """\
You are playing a person who is writing to a service to get something done. You are that \
person, not an assistant: you do not help, advise or serve anyone, and you never say or \
suggest that you are simulated, playing a part or following instructions.

"""
    + SPEC_TEMPLATE
    + """

## How you write

Write one message at a time, the way this person would type it: plain text, no stage \
directions, no quotation marks around it, no notes about what you are doing.{?stop_token} \
When your goal is achieved, or the conversation cannot make progress, answer with exactly \
{stop_token} and nothing else.{/stop_token}

Answer only in the structured format requested: your message goes in `message`."""
)

MESSAGE_TEMPLATE = """\
{?transcript}The conversation so far, oldest first. "You" is you; "They" is the service \
you are writing to.

{*transcript}{?own}You{/own}{!own}They{/own}: {text}

{/transcript}

Write your next message.{/transcript}{!transcript}(The conversation has not started. Write \
your opening message.){/transcript}"""
"""The one user message: the conversation so far from the user's side, or the fixed opening
line when no Turn precedes the `simulate` Turn."""

TEMPLATE = f"System prompt:\n\n{SYSTEM_TEMPLATE}\n\nUser message:\n\n{MESSAGE_TEMPLATE}\n"
"""The whole prompt as `run.json.simulated_user.prompt.text` records it and the Simulated
User Fingerprint hashes: every fixed word the model reads, the two parts labelled."""


class SimulatedUserAnswer(BaseModel):
    """The structured answer, validated at the boundary."""

    message: str


PARTS = JudgePromptParts(
    eval="simulated_user",
    version=PROMPT_VERSION,
    intro="",
    how_to_decide="",
    output_schema=OUTPUT_SCHEMA,
    output_model=SimulatedUserAnswer,
    own_template=TEMPLATE,
)
"""Its own template, not the Judge head (decision 54): the Simulated User fills it without a
`JudgeContext`, and `RecordedPrompt.of(PARTS)` is what `run.json` records."""


def _text(value: Any) -> str:
    """A fact's value as the prompt shows it: a string as it is, anything else its JSON."""
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def _facts(facts: Mapping[str, Any]) -> list[dict[str, Value]]:
    return [{"key": key, "value": _text(facts[key])} for key in sorted(facts)]


def spec_values(spec: SimulateSpec) -> dict[str, Value]:
    """What `SPEC_TEMPLATE`'s slots read from a `simulate` Turn: data, never wording."""
    return {
        "persona": spec.persona,
        "goal": spec.goal,
        "known_facts": _facts(spec.known_facts),
        "unknown_facts": _facts(spec.unknown_facts),
        "hints": [
            {"number": str(number), "text": hint} for number, hint in enumerate(spec.hints, start=1)
        ],
    }


def render_simulate_spec(spec: SimulateSpec) -> str:
    """The `simulate` Turn exactly as the Simulated User is told it: the one renderer its
    prompt and the Simulated User reviewer's share (decision 57)."""
    return fill(SPEC_TEMPLATE, spec_values(spec))


def transcript(events: Sequence[Event]) -> list[dict[str, Value]]:
    """The conversation so far from the user's side: every user message inside a Turn,
    literal or simulated, marked `own` (the template writes `You`), every Target reply not
    (it writes `They`), in order. Data only: the labels are the template's."""
    lines: list[dict[str, Value]] = []
    for event in resolve_blobs(events):
        fields = event.model_extra or {}
        if event.type != "message" or event.turn is None:
            continue
        text = str(fields.get("content") or "")
        if fields.get("role") == "user":
            lines.append({"own": "own", "text": text})
        elif fields.get("role") == "assistant" and event.actor == "target":
            lines.append({"own": None, "text": text})
    return lines


def turns_so_far(events: Sequence[Event]) -> int:
    """How many Turns the Trial has had: the `turn` Spans opened so far."""
    return sum(
        1
        for event in events
        if event.type == "span/start" and (event.model_extra or {}).get("kind") == "turn"
    )


class ModelSimulatedUser:
    """A model plays the user, through the Run's one client (decisions 48, 49)."""

    def __init__(
        self,
        client: ModelClient,
        configuration: SimulatedUserConfiguration,
        spec: SimulateSpec,
        scenario: Scenario,
    ) -> None:
        self.client = client
        self.configuration = configuration
        self.spec = spec
        self.scenario = scenario

    def request(self, events: Sequence[Event]) -> ModelRequest:
        """The call for the Trial so far: the system prompt from the spec, the conversation
        in the one user message, the Fingerprint on the request and never in its body."""
        system = fill(
            SYSTEM_TEMPLATE, {**spec_values(self.spec), "stop_token": self.spec.stop_token}
        )
        message = fill(MESSAGE_TEMPLATE, {"transcript": transcript(events)})
        return ModelRequest(
            model=self.configuration.model,
            max_tokens=SIMULATED_USER_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": message}],
            output_schema=OUTPUT_SCHEMA,
            # Checked in preflight (`effort_problem`), so the closed type is honest.
            effort=cast(Effort | None, self.configuration.effort),
            sampling=dict(self.configuration.sampling),
            fingerprint=self.configuration.fingerprint,
        )

    def next(self, events: Sequence[Event], trace: TraceWriter) -> NextMessage | Stop:
        """The message as the model wrote it; the driver loop reads it for the stop token
        (amended decision 48)."""
        request = self.request(events)
        answer = self._call(trace, request)
        if isinstance(answer, SamplingNotSupported):
            # Decision 53: the one resend, in a second Span, without the refused keys; the
            # Judge's no-retry policy (D13) is untouched, this is the Simulated User's own.
            refused = set(answer.sampling_accepted)
            resend = request.model_copy(
                update={"sampling": {k: v for k, v in request.sampling.items() if k not in refused}}
            )
            answer = self._call(trace, resend)
            if isinstance(answer, SamplingNotSupported):
                return _failed(f"The API refused the Simulated User's sampling twice: {answer}")
        return answer

    def _call(
        self, trace: TraceWriter, request: ModelRequest
    ) -> NextMessage | Stop | SamplingNotSupported:
        """One `llm_call` Span inside the `simulate` Span the driver loop opened, with the
        Judge's attributes, and rule 4 as the Judge applies it. A replay that does not fit is
        agentdiag's own recording being wrong, not the Simulated User's fault: it propagates,
        and the Trial loop ends the Trial `agentdiag_error` (amended decision 48)."""
        call = JudgeCall(
            kind="llm_call",
            actor=ACTOR,
            name=f"chat {request.model}",
            max_tokens=request.max_tokens,
        )
        span = model_span(
            trace, call, request.model, {SIMULATED_USER_FINGERPRINT: self.configuration.fingerprint}
        )
        span.event("request", actor=ACTOR, body=request.body())
        try:
            response = self.client.complete(request)
        except (ReplayMismatch, RecordingNotConsumed) as exc:
            span.end(status="error", error=call_failed(span, ACTOR, exc))
            raise
        except SamplingNotSupported as refused:
            span.end(
                status="error",
                attributes=_sampling_attributes(refused.sampling_accepted),
                error=call_failed(span, ACTOR, refused),
            )
            return refused
        except Exception as exc:
            kind, detail = call_failed(span, ACTOR, exc)
            span.end(status="error", error=(kind, detail))
            return _failed(f"The Simulated User call failed: {kind}: {detail}")
        span.event("response", actor=ACTOR, body=response.body)
        answer = _read(response, span)
        span.end(
            attributes={
                **response_attributes(response, attempts_name=SIMULATED_USER_ATTEMPTS),
                **_sampling_attributes(response.sampling_accepted),
            }
        )
        return answer


def _read(response: ModelResponse, span: SpanHandle) -> NextMessage | Stop:
    """The response as the next message, or rule 4 told in words (decision 48)."""
    if response.stop_reason in STOPPED_WITHOUT_ANSWER:
        error_type, detail = stopped_without_answer(
            response,
            error_type="SimulatedUser",
            spoken="Simulated User",
            max_tokens=SIMULATED_USER_MAX_TOKENS,
        )
        span.event("error", actor=ACTOR, error_type=error_type, message=detail)
        return _failed(f"The Simulated User returned no message: {detail}")
    try:
        answer = (
            SimulatedUserAnswer.model_validate(response.structured_output)
            if response.structured_output is not None
            else SimulatedUserAnswer.model_validate_json(response.text())
        )
    except (ValidationError, ValueError) as exc:
        return _failed_in(
            span, type(exc).__name__, f"The Simulated User's output did not fit the schema: {exc}"
        )
    if not answer.message.strip():
        return _failed_in(span, "EmptyMessage", "The Simulated User answered an empty message")
    return NextMessage(text=answer.message)


def _failed_in(span: SpanHandle, error_type: str, detail: str) -> Stop:
    """Rule 4 after a response: the `error` Event inside the call, in the words that stop
    the Trial."""
    span.event("error", actor=ACTOR, error_type=error_type, message=detail)
    return _failed(detail)


def _failed(detail: str) -> Stop:
    return Stop(termination="simulated_user_error", detail=detail)


def _sampling_attributes(outcomes: Mapping[str, str]) -> dict[str, str]:
    """`agentdiag.sampling.<key>: accepted | not_supported` per declared key (decision 53)."""
    return {f"{SAMPLING_PREFIX}{key}": outcome for key, outcome in sorted(outcomes.items())}


__all__ = [
    "OUTPUT_SCHEMA",
    "PARTS",
    "PROMPT_VERSION",
    "SIMULATED_USER_MAX_TOKENS",
    "TEMPLATE",
    "ModelSimulatedUser",
    "NextMessage",
    "ScriptedSimulatedUser",
    "SimulatedUser",
    "SimulatedUserAnswer",
    "Stop",
    "render_simulate_spec",
    "spec_values",
    "transcript",
    "turns_so_far",
]
