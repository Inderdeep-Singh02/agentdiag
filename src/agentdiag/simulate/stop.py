"""The stop criterion, evaluated by agentdiag over the Trace so far (D27, ADR-0002 §5;
phase-5 decisions 47, 50, 51).

`holds(stop_when, events, spans, *, judged)` is the one reading of a `stop_when` predicate:

- `tool_called: <name>` holds when any tool Span (`tool_call` or `retrieval`) of that tool
  exists in the Trace so far, literal Turns included: a criterion the opener already met
  ends the Trial before the Simulated User writes anything, which is the truth of it;
- `target_says_any: [phrases]` holds when any Target message contains any phrase under the
  text Evals' rule (NFC-normalised, case-insensitive substring, decision 18);
- `judged: <criterion>` is a Judge call (`agentdiag.eval.stop_when`), handed in as `judged`.

`StopCheck` is what the driver loop calls after each Turn: it evaluates the predicate and
writes one `note` (`actor: agentdiag`, `about: stop_when`, `predicate`, `holds`, `text`) per
evaluation — at the top level of the Trace for the two mechanical predicates, inside its own
model Span for `judged`. It reads the Trial so far back from the Trace file (amended
decision 56): every write is flushed.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from pydantic import BaseModel

from agentdiag.eval.mechanical import TOOL_SPAN_KINDS, normalised
from agentdiag.eval.render import STOP_WHEN
from agentdiag.scenario.models import StopWhen
from agentdiag.simulate.user import turns_so_far
from agentdiag.trace import Event, Span, TraceWriter, project_spans, read_trace, resolve_blobs


class Held(BaseModel):
    """Whether the criterion held, and what held, in words."""

    holds: bool
    text: str


Judged = Callable[[str, TraceWriter], Held]
"""The `judged` predicate's check: the criterion and the Trace so far, one Judge call over
it (`agentdiag.eval.stop_when.check`, bound to the Run's Judge)."""


def predicate_of(stop_when: StopWhen) -> str:
    """How a note and `show` name the predicate: `tool_called cancel_order`, …"""
    if stop_when.tool_called is not None:
        return f"tool_called {stop_when.tool_called}"
    if stop_when.target_says_any is not None:
        return f"target_says_any {json.dumps(stop_when.target_says_any, ensure_ascii=False)}"
    return "judged"


def holds(
    stop_when: StopWhen,
    events: Sequence[Event],
    spans: Sequence[Span],
    *,
    judged: Callable[[str], Held] | None,
) -> Held:
    """Whether the Scenario's stop criterion holds over the Trace so far (decision 50)."""
    after = turns_so_far(events)
    if stop_when.tool_called is not None:
        name = stop_when.tool_called
        called = any(
            span.kind in TOOL_SPAN_KINDS and span.attributes.get("gen_ai.tool.name") == name
            for span in spans
        )
        return Held(
            holds=called,
            text=f"tool_called {name} {'held' if called else 'did not hold'} after Turn {after}",
        )
    if stop_when.target_says_any is not None:
        phrases = stop_when.target_says_any
        for event in resolve_blobs(events):
            fields = event.model_extra or {}
            if not (
                event.type == "message"
                and event.actor == "target"
                and fields.get("role") == "assistant"
            ):
                continue
            content = normalised(str(fields.get("content") or ""))
            for phrase in phrases:
                if normalised(phrase) in content:
                    return Held(
                        holds=True,
                        text=f'target_says_any matched "{phrase}" in Turn {event.turn}',
                    )
        return Held(holds=False, text=f"target_says_any matched nothing by Turn {after}")
    if judged is None or stop_when.judged is None:
        raise ValueError("a judged stop criterion reached a Run with no Judge to check it")
    return judged(stop_when.judged)


class StopCheck:
    """The stop check the driver loop runs after each Turn, writing its `note`."""

    def __init__(self, judged: Judged | None = None) -> None:
        self.judged = judged
        """The `judged` predicate's Judge call; None when no Judge is built, which preflight
        makes impossible for a Scenario that declares one."""

    def __call__(self, stop_when: StopWhen, trace: TraceWriter) -> Held:
        events = read_trace(trace.path)
        check = self.judged
        held = holds(
            stop_when,
            events,
            project_spans(events),
            judged=(lambda criterion: check(criterion, trace)) if check is not None else None,
        )
        if stop_when.judged is None:
            # A `judged` check wrote its note inside its own model Span, by the call itself.
            trace.event(
                "note",
                actor="agentdiag",
                about=STOP_WHEN,
                predicate=predicate_of(stop_when),
                holds=held.holds,
                text=held.text,
            )
        return held


__all__ = ["Held", "Judged", "StopCheck", "holds", "predicate_of"]
