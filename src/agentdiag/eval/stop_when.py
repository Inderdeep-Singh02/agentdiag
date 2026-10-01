"""The `judged` stop predicate: one bounded Judge call per Turn, recorded in the Trace (D27,
phase-5 decision 51).

A `simulate` Turn may end on a prose criterion (`stop_when: {judged: …}`, what a
Scenario's goal is often written as). After each Turn agentdiag asks the Run's default Judge
one question over the Trace so far: does the criterion already hold? A criterion that will
hold after another Turn does not hold yet. The answer is `{"holds", "rationale"}`.

The call happens during the Trial and is agentdiag's, so it is written into the **Trace**,
not `judgement.jsonl`: an `llm_call` Span of actor `agentdiag`, named `stop_when judged`, a
sibling after the Turn it checks, with the Judge's Span attributes and Fingerprint, its
request and response, and the stop check's `note` inside it — so `show`'s Cost line prices
it under `agentdiag`, and the Judge of any later Eval never sees it (decision 59). The
budget is the bound: one call per Turn, at most `max_turns` of them, each capped at
`STOP_WHEN_MAX_TOKENS`, each priced on its Span.

A failed call (rule 4) never stops the conversation and never fails the Trial: the note says
`holds: false` with the failure, the `error` Event is inside the Span, and the loop goes on
to `max_turns`. agentdiag's instrument failing must not be read as the criterion met, nor end
a Trial the Target is still playing.

Its own template (decision 54): the Scenario section of the head, the Trace so far rendered
as for the Judge, the criterion, and the one question.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from agentdiag.eval.judge import Judge, JudgeCall
from agentdiag.eval.notes import JudgeNotes
from agentdiag.eval.render import (
    JUDGE_ROLE,
    SCENARIO,
    STOP_WHEN,
    TRACE_NOTE,
    JudgeContext,
    JudgePromptParts,
    asks_for,
)
from agentdiag.scenario.models import EvalDeclaration, Scenario
from agentdiag.simulate.stop import Held, Judged
from agentdiag.trace import TraceWriter, read_trace
from agentdiag.types import ToolKind

PROMPT_VERSION = "stop_when.v1"

STOP_WHEN_MAX_TOKENS = 4000
"""One check's cap: a yes or no and a sentence or two of why."""

NAME = "stop_when judged"
"""The `llm_call` Span's name, which `show` prints."""

PREDICATE = "judged"


class StopWhenOutput(BaseModel):
    holds: bool
    rationale: str


INTRO = (
    f"{JUDGE_ROLE} The Trial is still running: a Simulated User is playing the user, and "
    "after each Turn agentdiag asks you one question, whether the conversation should stop "
    "now. You are not judging the Target."
)

HOW_TO_DECIDE = (
    "Answer `holds: true` only when the Trace above already shows the stop criterion met. A "
    "criterion that will hold after another Turn does not hold yet, and neither does one the "
    "Trace does not show. Say why in `rationale` in a sentence or two, naming the Span ids "
    "that show it."
)

TEMPLATE = f"""\
{INTRO}

## The Scenario

{SCENARIO}

## The Trace so far

{TRACE_NOTE}

{{trace}}

## The stop criterion

{{criterion}}

## How to decide

{HOW_TO_DECIDE}

Answer only in the structured format requested.
"""

PARTS = JudgePromptParts(
    eval=STOP_WHEN,
    version=PROMPT_VERSION,
    intro=INTRO,
    how_to_decide=HOW_TO_DECIDE,
    output_schema={
        "type": "object",
        "properties": {"holds": {"type": "boolean"}, "rationale": {"type": "string"}},
        "required": ["holds", "rationale"],
        "additionalProperties": False,
    },
    output_model=StopWhenOutput,
    own_template=TEMPLATE,
)


def is_stop_when_request(request: Mapping[str, Any]) -> bool:
    """Whether a recorded request body is a stop check's: its schema is this module's. A
    rescore does not drive a Trial, so its recording check leaves these alone."""
    return asks_for(request, PARTS.output_schema)


def check(context: JudgeContext, judge: Judge, trace: TraceWriter, *, criterion: str) -> Held:
    """Ask once whether `criterion` holds over the Trace so far.

    Writes the `llm_call` Span and its `note` into `trace` (the Trial's own). The `Held`'s
    text is what the note and a `trace/end` detail say: `judged: <the Judge's rationale>`,
    or the failure, which never holds.
    """
    said = Held(holds=False, text="judged: the check failed, so it does not hold")

    def annotate(output: BaseModel | None, failure: str | None) -> dict[str, Any]:
        nonlocal said
        if isinstance(output, StopWhenOutput):
            said = Held(holds=output.holds, text=f"judged: {output.rationale.strip()}")
        else:
            said = Held(holds=False, text=f"{said.text}: {failure}")
        return {"about": STOP_WHEN, "predicate": PREDICATE, **said.model_dump()}

    judge.ask_over(
        context,
        PARTS,
        trace,
        fingerprint=judge.fingerprint(PARTS, context.notes),
        annotate=annotate,
        call=JudgeCall(
            kind="llm_call", actor="agentdiag", name=NAME, max_tokens=STOP_WHEN_MAX_TOKENS
        ),
        criterion=criterion,
    )
    return said


def checker(
    scenario: Scenario,
    judge: Judge,
    *,
    notes: JudgeNotes | None,
    tool_kinds: Mapping[str, ToolKind],
) -> Judged:
    """The `judged` predicate of one Trial's stop check, over the Run's Judge: each call
    reads the Trace so far back from its file and asks `check` once (amended 56)."""

    def judged(criterion: str, trace: TraceWriter) -> Held:
        context = JudgeContext.of(
            scenario,
            EvalDeclaration(eval=STOP_WHEN),
            read_trace(trace.path),
            fidelity="instrumented",
            notes=notes,
            tool_kinds=tool_kinds,
        )
        return check(context, judge, trace, criterion=criterion)

    return judged


__all__ = [
    "NAME",
    "PARTS",
    "PROMPT_VERSION",
    "STOP_WHEN_MAX_TOKENS",
    "StopWhenOutput",
    "check",
    "checker",
    "is_stop_when_request",
]
