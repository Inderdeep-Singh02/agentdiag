"""The one driver loop: literal Turns, then the `simulate` Turn (D26, D27; phase-5 decisions
47 and 56).

`drive` serves a Scenario's literal Turns exactly as `execute._trial` always has — the
`turn` Span, the user's `message`, `deliver`, the Target's `message`; the sequence
`tests/test_toy_target_trace.py` pins — and then, for the `simulate` Turn, which is the
last and the only one (decision 55):

1. **The stop check**, before the first simulated Turn and after every Turn thereafter:
   agentdiag evaluates the criterion over the Trace so far (`agentdiag.simulate.stop`) and
   writes one `note` per evaluation. When it holds the Trial ends `stop_when`.
2. **The bound**: otherwise, when the Turn count has reached `max_turns`, the Trial ends
   `max_turns`.
3. **The Simulated User**: otherwise it is asked, inside a `simulate` Span (actor
   `simulated_user`, name `simulated user`) opened **outside** any `turn` Span, a sibling
   before the Turn it authors, so a Turn's duration — what `response_latency` reads — is the
   Target's time only (D27). A message opens the next `turn` Span, numbered on from the
   literal ones, with the user's `message` as the Simulated User's; a `Stop` ends the Trial
   with its termination; a message that is (or contains) the declared stop token ends it
   `stop_token`, and the token is never delivered.

`Driven` is what the Trial loop needs afterwards: the termination, its detail for
`trace/end`, and each Simulated User call's sampling outcome, read off its Span as data
(`sampling_outcomes`); the Scorecard alone merges and words them (amended decision 53). The
loop reads the Trial so far back from the Trace file, which every write flushes (amended
decision 56).

**Each `deliver` runs on a worker thread and is waited for `turn_timeout_s`** (decision 56).
A Turn that overruns gets an `error` Event (`TurnTimeout`) inside
its `turn` Span, which ends `error` **without an assistant `message`** — the error text is
never recorded as the Target's reply — and `drive` raises `TurnTimedOut`, which the Trial
loop records as `timeout`. The thread is a daemon and is abandoned: the Trace is sealed at
`trace/end`, so a Target that wakes later cannot append to it. A `KeyboardInterrupt` while
waiting, and any exception the Target raised on the worker (re-raised by the future), leave
exactly as they did before the worker existed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic import BaseModel, Field

from agentdiag.adapter import Session
from agentdiag.deadline import Overdue, within
from agentdiag.model.client import SamplingSupport
from agentdiag.scenario.models import Scenario, SimulateSpec
from agentdiag.simulate.stop import StopCheck
from agentdiag.simulate.user import NextMessage, SimulatedUser, turns_so_far
from agentdiag.trace import Event, SpanHandle, TraceWriter, project_spans, read_trace
from agentdiag.trace.attributes import SAMPLING_PREFIX
from agentdiag.types import Actor, Fidelity, TerminationReason

TRACE_FIDELITY: Fidelity = "instrumented"
"""What the in-process Adapter achieves, and so every Span this loop opens for it; the
Simulated User's `simulate` Span is agentdiag's own and always this."""


def turn_fidelity(adapter_fidelity: Fidelity) -> Fidelity:
    """The Fidelity of the `turn` Spans this loop opens around an Adapter's replies: the
    in-process Adapter's reply is captured inside the Target (`instrumented`); any other's is
    what the response surface showed (`observed`, ticket 17), whatever tool truth it adds, so
    a text Eval grounded on the Turn never claims more than the surface gave."""
    return "instrumented" if adapter_fidelity == "instrumented" else "observed"


class TurnTimedOut(RuntimeError):
    """A Turn ran past `turn_timeout_s` (decision 56). Its `error` Event is already written."""


class Driven(BaseModel):
    """How the conversation ended, for the Trial loop."""

    termination: TerminationReason
    detail: str | None = None
    """What ended it, in words, for `trace/end` (decision 47); None for `completed`."""

    sampling: list[dict[str, SamplingSupport]] = Field(default_factory=list)
    """Each Simulated User call's outcome per sampling key it sent, in call order (decision
    53): data, which `scorecard.merged_sampling` merges over the Run and words."""


def drive(
    scenario: Scenario,
    session: Session,
    writer: TraceWriter,
    *,
    simulated_user_for: Callable[[SimulateSpec], SimulatedUser],
    stop_check: StopCheck,
    turn_timeout_s: float,
    fidelity: Fidelity = TRACE_FIDELITY,
) -> Driven:
    """Every Turn of one Trial, in order, until the conversation ends; each `turn` Span at
    `fidelity` (`turn_fidelity` of the Adapter's)."""
    spec = scenario.simulate
    literal = scenario.literal_turns
    if scenario.simulated and (spec is None or len(literal) != len(scenario.turns) - 1):
        raise ValueError(
            f"Scenario {scenario.id!r}: a Scenario has one simulate Turn and it is the last "
            "(decision 55), which `validate` refuses otherwise"
        )
    turns = _Turns(session, writer, turn_timeout_s, fidelity)
    for message in literal:
        turns.deliver(message, actor="agentdiag")
    if spec is None:
        return Driven(termination="completed")
    return _simulate(scenario, spec, turns, writer, simulated_user_for(spec), stop_check)


def _simulate(
    scenario: Scenario,
    spec: SimulateSpec,
    turns: _Turns,
    writer: TraceWriter,
    user: SimulatedUser,
    stop_check: StopCheck,
) -> Driven:
    bound = scenario.max_turns or len(scenario.turns)
    while True:
        if spec.stop_when is not None:
            held = stop_check(spec.stop_when, writer)
            if held.holds:
                return _driven(writer, "stop_when", held.text)
        if turns.count >= bound:
            return _driven(writer, "max_turns", f"max_turns {bound} reached")
        authoring = writer.span(
            "simulate", actor="simulated_user", name="simulated user", fidelity=TRACE_FIDELITY
        )
        try:
            answer = user.next(read_trace(writer.path), writer)
        except BaseException as exc:
            authoring.end(status="error", error=(type(exc).__name__, str(exc)))
            raise
        authoring.end(status="ok" if isinstance(answer, NextMessage) else "error")
        if not isinstance(answer, NextMessage):
            return _driven(writer, answer.termination, answer.detail)
        if _answered_stop_token(answer.text, spec.stop_token):
            # Detected here, for every implementation, and never delivered (amended 48).
            after = turns_so_far(read_trace(writer.path))
            return _driven(
                writer,
                "stop_token",
                f"the Simulated User answered the stop token after Turn {after}",
            )
        turns.deliver(answer.text, actor="simulated_user")


def _answered_stop_token(message: str, token: str | None) -> bool:
    """Whether a message is the stop token, or contains it (decision 48)."""
    return token is not None and token in message


def _driven(writer: TraceWriter, termination: TerminationReason, detail: str) -> Driven:
    return Driven(
        termination=termination,
        detail=detail,
        sampling=sampling_outcomes(read_trace(writer.path)),
    )


def sampling_outcomes(events: Sequence[Event]) -> list[dict[str, SamplingSupport]]:
    """Each Simulated User `llm_call`'s `agentdiag.sampling.<key>` outcomes, in call order."""
    outcomes: list[dict[str, SamplingSupport]] = []
    for span in project_spans(events):
        if span.actor != "simulated_user" or span.kind != "llm_call":
            continue
        outcomes.append(
            {
                name.removeprefix(SAMPLING_PREFIX): outcome
                for name, outcome in span.attributes.items()
                if name.startswith(SAMPLING_PREFIX) and outcome in ("accepted", "not_supported")
            }
        )
    return outcomes


class _Turns:
    """The Turns of one Trial as they are delivered: the `turn` Span, the two messages, the
    worker thread and its timeout."""

    def __init__(
        self, session: Session, writer: TraceWriter, timeout_s: float, fidelity: Fidelity
    ) -> None:
        self.session = session
        self.writer = writer
        self.timeout_s = timeout_s
        self.fidelity = fidelity
        self.count = 0

    def deliver(self, message: str, *, actor: Actor) -> None:
        self.count += 1
        number = self.count
        with self.writer.span(
            "turn", actor="agentdiag", name=f"turn {number}", fidelity=self.fidelity
        ) as turn:
            turn.event("message", actor=actor, role="user", content=message)
            reply = self._wait(message, number, turn)
            turn.event("message", actor="target", role="assistant", content=reply)

    def _wait(self, message: str, number: int, turn: SpanHandle) -> str:
        try:
            return within(
                lambda: self.session.deliver(message),
                self.timeout_s,
                name=f"agentdiag-turn-{number}",
            )
        except Overdue:
            detail = f"the Target did not answer Turn {number} within {_seconds(self.timeout_s)} s"
            # Into the Turn by name: the abandoned Target may still hold a Span open inside it.
            turn.event("error", actor="agentdiag", error_type="TurnTimeout", message=detail)
            raise TurnTimedOut(detail) from None


def _seconds(value: float) -> str:
    """`600`, `0.2`: a timeout as the Manifest would write it."""
    return f"{value:g}"


__all__ = [
    "TRACE_FIDELITY",
    "Driven",
    "TurnTimedOut",
    "drive",
    "sampling_outcomes",
    "turn_fidelity",
]
