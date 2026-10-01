"""The tool Evals: did the Target use the correct tool, and query the correct data (D21)?

Seven rows of the catalogue, each deciding from the tool Spans alone, so none of them costs a
token. A tool Span is a `tool_call` or a `retrieval` (ADR-0006 §2), named by its
`gen_ai.tool.name`; `turn: <n>` restricts any of them to one Turn (decision 7).

| Eval | `pass` when |
|---|---|
| `expect_tools` | every named tool has a Span |
| `expect_tools_order` | the names occur, in order, as a subsequence of the tool Spans |
| `expect_tools_any` | at least one named tool has a Span |
| `forbid_tools` | no named tool has a Span |
| `tool_count_max` | every named tool's Span count is within its cap |
| `expect_tool_args` | every call in scope that supplies an argument satisfies its operators |
| `tool_argument_types` | every typed argument a call supplies has its declared JSON type |

A Trace with no tool Span at all is read through Fidelity and through the responses
(decision 3, in `agentdiag.eval.mechanical`): at `instrumented`, with no response asking
for a tool, it is a fact, so the `expect` family fails and the `forbid` family passes;
otherwise it is `unverifiable`. Once any tool Span exists the record is read as complete,
at `reconstructed` as at `instrumented`, so a missing tool or a missing matching call is a
`fail` — except that a Verdict resting on a call being absent is `unverifiable` when a
response in scope asked for that tool and no Span answers it: the call may have happened
unrecorded.

`expect_tool_args` never passes on arguments it could not see: a matching `tool/call` that
lists `arguments` under `not_observed`, or a requested call with no Span, makes it
`unverifiable` / `evidence_missing` — "args unverifiable, never PASS". A
seen call that broke an operator still fails, because a failure
the Trace proves must not hide behind one it cannot see. Each argument is judged on the
calls that supply it; a call that does not is ignored (a Turn's calls are
merged), so a CI Suite without `tool` stays meaningful.

`tool_argument_types` is the Manifest's (phase-6 decision 17): a Target-level screen every
Scenario inherits, as `forbidden_phrases` is, from `eval_parameters.tool_argument_types`.
It types arguments and requires no call, so a typed tool never called is no fail; like
`expect_tool_args`, arguments it could not see are `unverifiable`, never a pass.

Evidence: a `pass` of the `expect` family cites the Spans that satisfied it and a `fail`
every tool Span read; the `forbid` family the other way round — what violated it, or
everything it read.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Any

from pydantic import Field, StrictBool, StrictFloat, StrictInt, field_validator, model_validator

from agentdiag.eval.mechanical import (
    Name,
    Parameters,
    Reading,
    TurnNumber,
    as_text,
    normalised,
    perform_mechanical,
    span_ids,
    tool_name,
)
from agentdiag.eval.score import Score
from agentdiag.eval.spec import EvalContext, EvalSpec
from agentdiag.trace.events import Event
from agentdiag.trace.spans import Span
from agentdiag.types import JsonType, Verdict

OPERATORS = ("equals", "contains", "present", "min", "none_in")
"""D21's five argument operators, in the order a rationale names them."""


def _one_or_more(value: Any) -> Any:
    """A single name where a list is expected is that list of one."""
    return [value] if isinstance(value, str) else value


class ToolListParameters(Parameters):
    """`expect_tools`, `expect_tools_order`, `expect_tools_any`, `forbid_tools`."""

    tools: list[Name] = Field(min_length=1)
    turn: TurnNumber | None = None

    @field_validator("tools", mode="before")
    @classmethod
    def _listed(cls, value: Any) -> Any:
        return _one_or_more(value)


class CountParameters(Parameters):
    """`tool_count_max`: a cap from 0 per named tool."""

    counts: dict[Name, Annotated[StrictInt, Field(ge=0)]] = Field(min_length=1)
    turn: TurnNumber | None = None


Candidates = Annotated[list[Name], Field(min_length=1)]


class Operators(Parameters):
    """What one argument must satisfy: at least one of D21's five operators."""

    equals: Any = None
    contains: Candidates | None = None
    present: StrictBool | None = None
    min: StrictInt | StrictFloat | None = None
    none_in: Candidates | None = None

    @field_validator("contains", "none_in", mode="before")
    @classmethod
    def _listed(cls, value: Any) -> Any:
        return _one_or_more(value)

    @model_validator(mode="after")
    def _names_an_operator(self) -> Operators:
        if not self.model_fields_set:
            raise ValueError(f"an argument names at least one of {', '.join(OPERATORS)}")
        return self


class ArgumentParameters(Parameters):
    """`expect_tool_args`: operators per argument, optionally of one tool."""

    args: dict[Name, Operators] = Field(min_length=1)
    tool: Name | None = None
    turn: TurnNumber | None = None


def _seen[P: Parameters](
    decide: Callable[[Reading, P], Score],
) -> Callable[[Reading, P], Score]:
    """A tool Eval that never decides on responses it could not read whole (ticket 26):
    with an `llm_call` in scope whose response was cut or never logged, a `pass` or a
    `fail` is `unverifiable` / `evidence_missing`, citing those Spans."""

    def guarded(reading: Reading, parameters: P) -> Score:
        score = decide(reading, parameters)
        if score.verdict not in ("pass", "fail"):
            return score
        unseen = reading.unseen_responses()
        if not unseen:
            return score
        return reading.score(
            "unverifiable",
            f"The response in {span_ids(unseen)} was cut or never logged, so which tools "
            f"the Target asked for is not fully known (the reading was: {score.verdict})",
            evidence=unseen,
            read=[*reading.tool_spans(), *unseen],
            reason="evidence_missing",
        )

    return guarded


def _names(spans: Sequence[Span]) -> str:
    return ", ".join(str(tool_name(span)) for span in spans) or "none"


def _opened(
    reading: Reading, *, fact: Verdict, because: str
) -> tuple[list[Span], list[Span]] | Score:
    """The tool Spans in scope and everything read — or the Score the gate or an absence of
    tool Spans already decides (decision 3). Every tool-list Eval opens with this."""
    tools, models = reading.tool_spans(), reading.model_spans()
    read = [*tools, *models]
    if (gated := reading.gate(read)) is not None:
        return gated
    if not tools:
        return reading.absent(models, fact=fact, because=because)
    return tools, read


# --- the tool lists ---


def expect_tools(context: EvalContext) -> Score:
    """`pass` when every named tool was called."""
    return perform_mechanical(EXPECT_TOOLS, context, ToolListParameters, _seen(_expect_tools))


def _expect_tools(reading: Reading, parameters: ToolListParameters) -> Score:
    wanted = parameters.tools
    opened = _opened(
        reading, fact="fail", because=f"The Target called none of {', '.join(wanted)}."
    )
    if isinstance(opened, Score):
        return opened
    tools, read = opened
    called = {tool_name(span) for span in tools}
    missing = [name for name in wanted if name not in called]
    if missing:
        if asking := reading.unanswered(missing):
            return reading.requested_without_a_span(asking, read=read)
        return reading.score(
            "fail",
            f"{reading.scope()} never called {', '.join(missing)}; the tools it called were "
            f"{_names(tools)}",
            evidence=tools,
            read=read,
        )
    satisfied = [span for span in tools if tool_name(span) in wanted]
    return reading.score(
        "pass",
        f"Every expected tool was called in {reading.scope()}: {span_ids(satisfied)}",
        evidence=satisfied,
        read=read,
    )


def expect_tools_order(context: EvalContext) -> Score:
    """`pass` when the named tools were called in this order, others allowed between."""
    return perform_mechanical(
        EXPECT_TOOLS_ORDER, context, ToolListParameters, _seen(_expect_tools_order)
    )


def _expect_tools_order(reading: Reading, parameters: ToolListParameters) -> Score:
    wanted = parameters.tools
    opened = _opened(
        reading, fact="fail", because=f"The Target called none of {', '.join(wanted)}."
    )
    if isinstance(opened, Score):
        return opened
    tools, read = opened
    # Spans are in `dotted_order`, which is execution order (ADR-0006 §3); the earliest
    # match for each name in turn is the subsequence if there is one.
    matched: list[Span] = []
    for span in tools:
        if len(matched) < len(wanted) and tool_name(span) == wanted[len(matched)]:
            matched.append(span)
    if len(matched) < len(wanted):
        if asking := reading.unanswered(wanted):
            return reading.requested_without_a_span(asking, read=read)
        return reading.score(
            "fail",
            f"{reading.scope()} did not call {' → '.join(wanted)} in that order: the calls "
            f"were {_names(tools)}, and the order broke at {wanted[len(matched)]}",
            evidence=tools,
            read=read,
        )
    return reading.score(
        "pass",
        f"{' → '.join(wanted)} were called in that order: {span_ids(matched)}",
        evidence=matched,
        read=read,
    )


def expect_tools_any(context: EvalContext) -> Score:
    """`pass` when at least one of the named tools was called."""
    return perform_mechanical(
        EXPECT_TOOLS_ANY, context, ToolListParameters, _seen(_expect_tools_any)
    )


def _expect_tools_any(reading: Reading, parameters: ToolListParameters) -> Score:
    wanted = parameters.tools
    opened = _opened(
        reading, fact="fail", because=f"The Target called none of {', '.join(wanted)}."
    )
    if isinstance(opened, Score):
        return opened
    tools, read = opened
    satisfied = [span for span in tools if tool_name(span) in wanted]
    if not satisfied:
        if asking := reading.unanswered(wanted):
            return reading.requested_without_a_span(asking, read=read)
        return reading.score(
            "fail",
            f"{reading.scope()} called none of {', '.join(wanted)}; the tools it called were "
            f"{_names(tools)}",
            evidence=tools,
            read=read,
        )
    return reading.score(
        "pass",
        f"{reading.scope()} called {_names(satisfied)}: {span_ids(satisfied)}",
        evidence=satisfied,
        read=read,
    )


def forbid_tools(context: EvalContext) -> Score:
    """`pass` when none of the named tools was called."""
    return perform_mechanical(FORBID_TOOLS, context, ToolListParameters, _seen(_forbid_tools))


def _forbid_tools(reading: Reading, parameters: ToolListParameters) -> Score:
    forbidden = parameters.tools
    opened = _opened(
        reading, fact="pass", because=f"The Target called none of {', '.join(forbidden)}."
    )
    if isinstance(opened, Score):
        return opened
    tools, read = opened
    violated = [span for span in tools if tool_name(span) in forbidden]
    if violated:
        return reading.score(
            "fail",
            f"{reading.scope()} called a forbidden tool: {_names(violated)} in "
            f"{span_ids(violated)}",
            evidence=violated,
            read=read,
        )
    if asking := reading.unanswered(forbidden):
        return reading.requested_without_a_span(asking, read=read)
    return reading.score(
        "pass",
        f"{reading.scope()} called none of {', '.join(forbidden)}; the tools it called were "
        f"{_names(tools)}",
        evidence=tools,
        read=read,
    )


def tool_count_max(context: EvalContext) -> Score:
    """`pass` when no named tool was called more often than its cap."""
    return perform_mechanical(TOOL_COUNT_MAX, context, CountParameters, _seen(_tool_count_max))


def _tool_count_max(reading: Reading, parameters: CountParameters) -> Score:
    caps = parameters.counts
    opened = _opened(
        reading, fact="pass", because="The Target called no tool, so none exceeded its cap."
    )
    if isinstance(opened, Score):
        return opened
    tools, read = opened
    over: list[Span] = []
    broken: list[str] = []
    for name, cap in caps.items():
        calls = [span for span in tools if tool_name(span) == name]
        if len(calls) > cap:
            over.extend(calls)
            broken.append(f"{name} {len(calls)} times against a cap of {cap}")
    if broken:
        return reading.score(
            "fail",
            f"{reading.scope()} called {'; '.join(broken)}",
            evidence=sorted(over, key=lambda span: span.dotted_order),
            read=read,
        )
    if asking := reading.unanswered(list(caps)):
        return reading.requested_without_a_span(asking, read=read)
    counted = ", ".join(
        f"{name} {sum(tool_name(span) == name for span in tools)} of {cap}"
        for name, cap in caps.items()
    )
    return reading.score(
        "pass", f"Every capped tool is within its cap: {counted}", evidence=tools, read=read
    )


# --- the arguments ---


def expect_tool_args(context: EvalContext) -> Score:
    """`pass` when every call that supplies an argument satisfies its operators."""
    return perform_mechanical(
        EXPECT_TOOL_ARGS, context, ArgumentParameters, _seen(_expect_tool_args)
    )


def _expect_tool_args(reading: Reading, parameters: ArgumentParameters) -> Score:
    tool = parameters.tool
    what = tool or "any tool"
    matching, models = reading.tool_spans(tool), reading.model_spans()
    read = [*matching, *models]
    if (gated := reading.gate(read)) is not None:
        return gated
    every_tool = reading.tool_spans()
    if not every_tool:
        return reading.absent(
            models, fact="fail", because=f"No call to {what} was made, so no argument held."
        )
    asking = reading.unanswered([tool] if tool else None)
    if not matching:
        if asking:
            return reading.requested_without_a_span(asking, read=read)
        return reading.score(
            "fail",
            f"{reading.scope()} made no call to {what}; the tools it called were "
            f"{_names(every_tool)}",
            evidence=every_tool,
            read=[*every_tool, *models],
        )

    seen: list[tuple[Span, dict[str, Any]]] = []
    unseen: list[Span] = []
    for span in matching:
        arguments = _observed_arguments(_tool_call(reading.context.events, span))
        if arguments is None:
            unseen.append(span)
        else:
            seen.append((span, arguments))

    broken: list[Span] = []
    problems: list[str] = []
    for argument, operators in parameters.args.items():
        supplying = [(span, arguments) for span, arguments in seen if argument in arguments]
        for span, arguments in supplying:
            found = _broken_by_value(arguments[argument], operators)
            if found:
                broken.append(span)
                problems.extend(f"{span.span_id} {argument} {problem}" for problem in found)
        if not supplying and not unseen and not asking:
            found = _broken_when_absent(operators)
            if found:
                broken.extend(matching)
                problems.extend(f"{argument} {problem}" for problem in found)

    if broken:
        return reading.score(
            "fail",
            f"A call's arguments did not hold: {'; '.join(problems)}",
            evidence=sorted(broken, key=lambda span: span.dotted_order),
            read=read,
        )
    if unseen or asking:
        return reading.score(
            "unverifiable",
            f"The arguments of {span_ids([*unseen, *asking])} were not recorded (not observed "
            "by the Adapter, or a requested call left no Span), so what they held is not "
            "known; an argument nobody could see is never a pass",
            evidence=[*unseen, *asking],
            read=read,
            reason="evidence_missing",
        )
    return reading.score(
        "pass",
        f"Every call that supplied {', '.join(parameters.args)} held: {span_ids(matching)}",
        evidence=matching,
        read=read,
    )


def _tool_call(events: Sequence[Event], span: Span) -> Event | None:
    """The `tool/call` Event recorded inside a tool Span."""
    for event in events:
        if event.type == "tool/call" and event.span_id == span.span_id:
            return event
    return None


def _observed_arguments(call: Event | None) -> dict[str, Any] | None:
    """The call's arguments, or None when the Adapter recorded them as unknown (D6)."""
    if call is None:
        return None
    fields = call.model_extra or {}
    if "arguments" in (fields.get("not_observed") or []):
        return None
    arguments = fields.get("arguments")
    return dict(arguments) if isinstance(arguments, Mapping) else None


def _broken_by_value(value: Any, operators: Operators) -> list[str]:
    """What each operator finds wrong with an argument a call supplied."""
    problems: list[str] = []
    named = operators.model_fields_set
    if "present" in named and operators.present != _present(value):
        problems.append("is empty" if operators.present else f"was passed as {value!r}")
    if "equals" in named and not _equals(value, operators.equals):
        problems.append(f"is {value!r}, not {operators.equals!r}")
    if operators.contains is not None and not any(
        normalised(candidate) in normalised(as_text(value)) for candidate in operators.contains
    ):
        problems.append(f"is {value!r}, containing none of {operators.contains!r}")
    if operators.min is not None and not _at_least(value, operators.min):
        problems.append(f"is {value!r}, below the minimum {operators.min!r}")
    if operators.none_in is not None:
        hit = [c for c in operators.none_in if normalised(c) in normalised(as_text(value))]
        if hit:
            problems.append(f"is {value!r}, containing {', '.join(map(repr, hit))}")
    return problems


def _broken_when_absent(operators: Operators) -> list[str]:
    """What fails when no call supplied the argument: everything but `none_in` and
    `present: false`, which an absent argument satisfies."""
    named = operators.model_fields_set
    failing = [
        f"{operator} {getattr(operators, operator)!r}"
        for operator in ("equals", "contains", "min")
        if operator in named
    ]
    if operators.present:
        failing.append("present")
    return [f"was supplied by no call ({', '.join(failing)})"] if failing else []


def _present(value: Any) -> bool:
    """Non-empty: a legacy `expect_arg_present`, for an argument that was passed."""
    return value not in (None, "", [], {})


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _equals(value: Any, expected: Any) -> bool:
    """Numeric equality when both sides are numbers, otherwise equality of the values or of
    their text: a legacy `expect_arg_equals`."""
    left, right = _number(value), _number(expected)
    if left is not None and right is not None:
        return left == right
    return bool(value == expected or as_text(value) == as_text(expected))


def _at_least(value: Any, minimum: float) -> bool:
    number = _number(value)
    return number is not None and number >= minimum


# --- the argument types (phase-6 decision 17) ---


class ArgumentTypeParameters(Parameters):
    """`tool_argument_types`: `{tool: {argument: JSON type}}`, the Manifest's
    `eval_parameters.tool_argument_types`, which preflight copies into every Scenario."""

    types: dict[Name, dict[Name, JsonType]] = Field(min_length=1)


JSON_TYPE_OF: tuple[tuple[type, JsonType], ...] = (
    (bool, "boolean"),
    (int, "integer"),
    (float, "number"),
    (str, "string"),
    (dict, "object"),
    (list, "array"),
    (type(None), "null"),
)
"""The JSON type a recorded argument value has, `bool` before `int` because a Python
`True` is an `int` too."""


def json_type(value: Any) -> JsonType:
    """What JSON type an argument value is, by the names JSON Schema uses."""
    for python, named in JSON_TYPE_OF:
        if isinstance(value, python):
            return named
    return "object"


def _has_type(value: Any, expected: JsonType) -> bool:
    """JSON Schema's reading: an `integer` is also a `number`."""
    seen = json_type(value)
    return seen == expected or (expected == "number" and seen == "integer")


def tool_argument_types(context: EvalContext) -> Score:
    """`pass` when every recorded call of a typed tool carries each typed argument it
    supplies at its declared JSON type."""
    return perform_mechanical(
        TOOL_ARGUMENT_TYPES, context, ArgumentTypeParameters, _seen(_tool_argument_types)
    )


def _tool_argument_types(reading: Reading, parameters: ArgumentTypeParameters) -> Score:
    typed = list(parameters.types)
    calls = [span for span in reading.tool_spans() if tool_name(span) in parameters.types]
    models = reading.model_spans()
    read = [*calls, *models]
    if (gated := reading.gate(read)) is not None:
        return gated
    unseen: list[Span] = []
    broken: list[Span] = []
    problems: list[str] = []
    for span in calls:
        arguments = _observed_arguments(_tool_call(reading.context.events, span))
        if arguments is None:
            unseen.append(span)
            continue
        name = tool_name(span) or ""
        for argument, expected in parameters.types[name].items():
            if argument in arguments and not _has_type(arguments[argument], expected):
                if span not in broken:
                    broken.append(span)
                seen = json_type(arguments[argument])
                problems.append(f"{span.span_id} {name}.{argument} is {seen}, declared {expected}")
    if broken:
        return reading.score(
            "fail",
            f"A call carried an argument of the wrong type: {'; '.join(problems)}",
            evidence=broken,
            read=read,
        )
    asking = reading.unanswered(typed)
    if unseen or asking:
        return reading.score(
            "unverifiable",
            f"The arguments of {span_ids([*unseen, *asking])} were not recorded (not observed "
            "by the Adapter, or a requested call left no Span), so their types are not known",
            evidence=[*unseen, *asking],
            read=read,
            reason="evidence_missing",
        )
    if not calls:
        if not models:
            return reading.absent(models, fact="pass", because="")
        return reading.score(
            "pass",
            f"{reading.scope()} made no call to a typed tool ({', '.join(typed)}), so no "
            "argument had a type to hold; the Manifest types arguments, it requires no call",
            evidence=models,
            read=read,
        )
    return reading.score(
        "pass",
        f"Every typed argument of {span_ids(calls)} had its declared type",
        evidence=calls,
        read=read,
    )


# --- the catalogue rows (registered by `agentdiag.eval.registry`) ---


def _tool_list(name: str, perform_row: Callable[[EvalContext], Score]) -> EvalSpec:
    return EvalSpec(
        name=name,
        kind="mechanical",
        min_fidelity="reconstructed",
        primary="tools",
        tool_family=True,
        answers="use the correct tool",
        perform=perform_row,
        params_model=ToolListParameters,
    )


EXPECT_TOOLS = _tool_list("expect_tools", expect_tools)
EXPECT_TOOLS_ORDER = _tool_list("expect_tools_order", expect_tools_order)
EXPECT_TOOLS_ANY = _tool_list("expect_tools_any", expect_tools_any)
FORBID_TOOLS = _tool_list("forbid_tools", forbid_tools)
TOOL_COUNT_MAX = EvalSpec(
    name="tool_count_max",
    kind="mechanical",
    min_fidelity="reconstructed",
    primary="counts",
    tool_family=True,
    answers="use the correct tool",
    perform=tool_count_max,
    params_model=CountParameters,
)
EXPECT_TOOL_ARGS = EvalSpec(
    name="expect_tool_args",
    kind="mechanical",
    min_fidelity="reconstructed",
    primary="args",
    tool_family=True,
    answers="query the correct data",
    perform=expect_tool_args,
    params_model=ArgumentParameters,
)

TOOL_ARGUMENT_TYPES = EvalSpec(
    name="tool_argument_types",
    kind="mechanical",
    min_fidelity="reconstructed",
    primary="types",
    answers="query the correct data",
    perform=tool_argument_types,
    params_model=ArgumentTypeParameters,
)
"""A Target-level screen like `forbidden_phrases` (decision 17): preflight adds it to every
Scenario when the Manifest declares `eval_parameters.tool_argument_types`. Not in the tool
family, because a screen every Scenario inherits must not make every Scenario a `tool` one."""

SPECS: tuple[EvalSpec, ...] = (
    EXPECT_TOOLS,
    EXPECT_TOOLS_ORDER,
    EXPECT_TOOLS_ANY,
    FORBID_TOOLS,
    TOOL_COUNT_MAX,
    EXPECT_TOOL_ARGS,
    TOOL_ARGUMENT_TYPES,
)
"""This module's rows, in the catalogue's order (D21)."""


__all__ = [
    "OPERATORS",
    "SPECS",
    "TOOL_ARGUMENT_TYPES",
    "ArgumentParameters",
    "ArgumentTypeParameters",
    "CountParameters",
    "Operators",
    "ToolListParameters",
    "expect_tool_args",
    "expect_tools",
    "expect_tools_any",
    "expect_tools_order",
    "forbid_tools",
    "json_type",
    "tool_argument_types",
    "tool_count_max",
]
