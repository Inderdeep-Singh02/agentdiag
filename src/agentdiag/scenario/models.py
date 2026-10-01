"""The Suite and Scenario shapes, as authored and as loaded (ADR-0002, D17, D18).

One Scenario shape, whatever kind of test it is (ADR-0002 §1). A Turn is a literal user
message or a `simulate` marker that hands the conversation to a Simulated User, so a
scripted opener with an adaptive tail — the shape of most real Scenarios — is
one list, not two schemas. "One-shot", "conversation", "simulated" and "tool" are derived
from what a Scenario holds (`Scenario.kinds`, phase-5 decision 2) and never written.

These models are what `agentdiag validate` runs and what `schemas/suite.schema.json` is
generated from (decision 15), so the authored spellings live here rather than in a
pre-pass the schema would not know about, and every rule a single object can break is
published in the schema too, through the models' schema hooks:

- an Eval declaration is `- name`, `- name: <value>` (bound to the Eval's primary
  parameter, decision 1), or the long form `- {eval, id, params, threshold, judge}`;
  `bind_short_form` and its inverse `short_form` sit side by side below, so the reading
  and the writing of a short form cannot drift apart;
- a Fixture is authored flat, `{kind?: ..., <any keys>}`, and loaded as `kind` + `data`.

Two meanings of the Suite are defined here because every reader needs them the same way:
which guardrail rules the Suite authors (`parse_guardrail_rules`, decision 16), and the
*effective* Scenario, its own declarations plus the Suite's it inherits (`effective_suite`,
decision 17). Rules relating one object to another are `scenario.validate`'s.

Keys the schema has no slot for are refused, not dropped: a typo in `max_turns` must not
leave a Scenario unbounded. What an author wants carried without being
interpreted goes under `extras`, which nothing in agentdiag reads (decision 9).
"""

from __future__ import annotations

from typing import Annotated, Any, NamedTuple, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    ValidationError,
    field_validator,
    model_validator,
)

from agentdiag.eval.registry import REGISTRY

LEGACY_EVAL_NAMES: tuple[str, ...] = (
    "expect_tool",
    "expect_tool_any",
    "expect_tool_order",
    "expect_tool_first",
    "must_not_call",
    "expect_no_tool",
)
"""Retired legacy `pass_criteria` spellings. Hard errors, not unknown names: each one
meant something, and guessing which standard Eval it becomes is the author's job, done
once and reviewed, not the loader's, done silently every Run."""

CONVERT_HINT = (
    "rewrite it in the standard schema (docs/scenario-schema.md in the agentdiag repository)"
)


# --- the stop criterion and the simulate Turn ---


class StopWhen(BaseModel):
    """One predicate over the Trace that ends an adaptive conversation (ADR-0002 §5).

    One predicate per object, so what stopped a Trial is always one thing a reader can
    name. agentdiag evaluates it over the Trace after each Turn (`agentdiag.simulate.stop`,
    ticket 06). An empty name, an empty phrase list or an empty phrase is refused (phase-5
    decision 55): each would be a predicate that can never hold, or one that always does.
    """

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"minProperties": 1, "maxProperties": 1}
    )

    tool_called: str | None = Field(default=None, min_length=1)
    """The Target called this tool."""

    target_says_any: list[Annotated[str, Field(min_length=1)]] | None = Field(
        default=None, min_length=1
    )
    """The Target's message contained one of these (the text-Eval matching rule)."""

    judged: str | None = Field(default=None, min_length=1)
    """A prose question a Judge call decides, once per Turn (decision 51)."""

    @model_validator(mode="after")
    def _exactly_one_predicate(self) -> Self:
        named = [name for name, value in self if value is not None]
        if len(named) != 1:
            raise ValueError(
                "stop_when holds exactly one of tool_called, target_says_any or judged; "
                f"this one holds {', '.join(named) if named else 'none'}"
            )
        return self


class SimulateSpec(BaseModel):
    """What the Simulated User is told when it takes over the conversation (D18, D27)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"oneOf": [{"required": ["stop_when"]}, {"required": ["stop_token"]}]},
    )

    goal: str
    """What the simulated user wants — the instruction it authors each message from."""

    persona: str | None = None
    known_facts: dict[str, Any] = Field(default_factory=dict)
    """What the Simulated User may say when asked."""

    unknown_facts: dict[str, Any] = Field(default_factory=dict)
    """What it must not reveal, because the user in the story does not know it (D27)."""

    hints: list[str] = Field(default_factory=list)
    """How the user reacts: a legacy Suite's `adapt_hints`, verbatim."""

    stop_when: StopWhen | None = None
    stop_token: str | None = None

    @model_validator(mode="after")
    def _exactly_one_stop_criterion(self) -> Self:
        """ADR-0002 §5: an adaptive Scenario without a way to end is refused."""
        if (self.stop_when is None) == (self.stop_token is None):
            held = "both" if self.stop_when is not None else "neither"
            raise ValueError(
                f"a simulate Turn declares exactly one of stop_when or stop_token; this one "
                f"declares {held} (ADR-0002 §5)"
            )
        return self


class SimulateTurn(BaseModel):
    """The marker `- simulate: {...}` in a Scenario's `turns`."""

    model_config = ConfigDict(extra="forbid")

    simulate: SimulateSpec


def _turn_source(value: Any) -> str:
    """A string is a literal Turn; anything else is read as a `simulate` marker."""
    return "literal" if isinstance(value, str) else "simulate_turn"


TURN_TAGS = frozenset({"literal", "simulate_turn"})
"""The union's branch names, which Pydantic puts in an error's location; validation drops
them so a path reads `scenarios[2].turns[1].simulate` as authored."""

Turn = Annotated[
    Annotated[str, Tag("literal")] | Annotated[SimulateTurn, Tag("simulate_turn")],
    Discriminator(_turn_source),
]
"""A literal user message, or a `simulate` marker (ADR-0002 §1)."""


# --- Eval declarations ---


class JudgeOverride(BaseModel):
    """A judged Eval's own model and effort, overriding the Run's default (D19, D14)."""

    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    effort: str | None = None


def _authored_declaration_forms(schema: dict[str, Any]) -> None:
    """Publish the three spellings an author may write, not only the loaded shape.

    `validate` and the published schema must agree on what is accepted (decision 15): the
    short forms are accepted, a legacy spelling is refused in each of the three, and so is
    an authored `inherited`, which only the loader may set.
    """
    legacy = {"enum": list(LEGACY_EVAL_NAMES)}
    long_form = {key: value for key, value in schema.items() if key != "title"}
    properties = dict(long_form.get("properties", {}))
    properties.pop("inherited", None)
    properties["eval"] = {**properties.get("eval", {}), "not": legacy}
    long_form["properties"] = properties
    long_form.pop("additionalProperties", None)
    long_form["not"] = {"required": ["inherited"]}
    title = schema.get("title", "EvalDeclaration")
    schema.clear()
    schema.update(
        {
            "title": title,
            "description": (
                "An Eval declaration: `- name`, `- name: <value>` (the value binds to the "
                "Eval's primary parameter), or the long form with an `eval` key."
            ),
            "anyOf": [
                {"type": "string", "minLength": 1, "not": legacy},
                {
                    "type": "object",
                    "minProperties": 1,
                    "maxProperties": 1,
                    "propertyNames": {"not": {"enum": ["eval", *LEGACY_EVAL_NAMES]}},
                },
                long_form,
            ],
        }
    )


class Binding(NamedTuple):
    """What `- name: <value>` means, and why its value is not bound, when it is not."""

    declaration: dict[str, Any]
    """The long form the short form means."""

    unbound: str | None
    """Set when nothing names a parameter for the value, which is then carried as
    `params.value` and warned about (decision 1)."""


def bind_short_form(name: str, value: Any) -> Binding:
    """`- name: <value>` as the long form it means (decision 1). `short_form` inverts it.

    The value binds to the Eval's primary parameter — or to `threshold` for a Metric —
    unless it is a mapping that already names that parameter, in which case the mapping
    *is* the parameters: `- guardrails: [G1]` and `- guardrails: {rules: [G1]}` are the
    same declaration, and `- must_say_any: {phrases: [x], turn: 2}` is how a restricted
    text Eval is written short (decision 7). An unregistered name keeps its value under
    `params.value`, because nothing knows what it should bind to.
    """
    if value is None:
        return Binding({"eval": name}, None)
    spec = REGISTRY.get(name)
    if spec is None:
        return Binding(
            {"eval": name, "params": {"value": value}},
            "is written short and is not registered",
        )
    if spec.primary == "threshold":
        if isinstance(value, dict) and "threshold" in value:
            rest = {key: item for key, item in value.items() if key != "threshold"}
            return Binding({"eval": name, "threshold": value["threshold"], "params": rest}, None)
        return Binding({"eval": name, "threshold": value}, None)
    if spec.primary is None:
        if isinstance(value, dict):
            return Binding({"eval": name, "params": value}, None)
        return Binding({"eval": name, "params": {"value": value}}, "takes no primary parameter")
    if isinstance(value, dict) and spec.primary in value:
        return Binding({"eval": name, "params": value}, None)
    return Binding({"eval": name, "params": {spec.primary: value}}, None)


def short_form(declaration: EvalDeclaration) -> Any:
    """The short spelling that `bind_short_form` reads back as this declaration, or None.

    None whenever no short spelling round-trips: an `id` or a `judge` needs the long form,
    and so does a value `bind_short_form` would bind differently (a mapping that happens
    to contain the primary parameter's own name, a null, a threshold beside parameters of
    an Eval that is no Metric). Returning None there is what keeps rendering idempotent.
    """
    name, params, threshold = declaration.eval, declaration.params, declaration.threshold
    if declaration.id is not None or declaration.judge is not None:
        return None
    if not params and threshold is None:
        return name
    spec = REGISTRY.get(name)
    if spec is None:
        if threshold is None and set(params) == {"value"} and params["value"] is not None:
            return {name: params["value"]}
        return None
    if spec.primary == "threshold":
        if threshold is None or "threshold" in params:
            return None
        if not params and "threshold" not in threshold:
            return {name: threshold}
        return {name: {"threshold": threshold, **params}}
    if threshold is not None:
        return None
    if spec.primary is None:
        if set(params) == {"value"}:
            value = params["value"]
            return None if value is None or isinstance(value, dict) else {name: value}
        return {name: dict(params)}
    if set(params) == {spec.primary}:
        value = params[spec.primary]
        if value is not None and not (isinstance(value, dict) and spec.primary in value):
            return {name: value}
    if spec.primary in params:
        return {name: dict(params)}
    return None


def expand_declaration(value: Any) -> Any:
    """Any authored spelling of a declaration, as the long form; other input unchanged."""
    if isinstance(value, str):
        return {"eval": value}
    if isinstance(value, dict) and "eval" not in value:
        if len(value) != 1:
            keys = ", ".join(repr(key) for key in value) or "none"
            raise ValueError(
                "an Eval declaration is `- name`, `- name: <value>`, or a mapping with an "
                f"`eval` key; this one has keys {keys}"
            )
        ((name, item),) = value.items()
        return bind_short_form(str(name), item).declaration
    if isinstance(value, dict) and "inherited" in value:
        raise ValueError(
            "`inherited` is set by the loader when a Suite-level declaration is copied into "
            "a Scenario; it is never authored"
        )
    return value


class EvalDeclaration(BaseModel):
    """One Eval a Scenario or a Suite asks for (D19)."""

    model_config = ConfigDict(json_schema_extra=_authored_declaration_forms)

    eval: str
    id: str | None = None
    """Cites this one declaration when a Scenario declares the same Eval twice, and is
    what `focus` names and what the replacement rule matches on (decision 17)."""

    params: dict[str, Any] = Field(default_factory=dict)
    threshold: dict[str, Any] | None = None
    """For a Metric Eval: where pass turns into fail (D19)."""

    judge: JudgeOverride | None = None
    inherited: bool = False
    """Set by `effective_evals` on a Suite-level declaration copied into a Scenario, so a
    Report can say where a Score came from (decision 17). Authoring it is an error."""

    @model_validator(mode="before")
    @classmethod
    def _authored_spellings(cls, value: Any) -> Any:
        return expand_declaration(value)

    @field_validator("eval")
    @classmethod
    def _not_a_legacy_spelling(cls, name: str) -> str:
        if name in LEGACY_EVAL_NAMES:
            raise ValueError(f"{name!r} is a legacy spelling; {CONVERT_HINT}")
        return name

    def key(self) -> tuple[str, str | None]:
        """What the replacement rule compares: the Eval and its id (decision 17)."""
        return (self.eval, self.id)


# --- guardrails (decision 16) ---


class GuardrailRule(BaseModel):
    """One authored rule under a Suite-level `guardrails` declaration (decision 16).

    `rule` may be null: namespaced ids (`constraint:*`) name rules whose text
    lives in a graph the Suite does not carry, and dropping them would lose the Scenario's
    intent. `validate` warns about each; ticket 05 scores it `eval_not_applicable`.
    """

    id: str
    rule: str | None
    name: str | None = None


class GuardrailRules(NamedTuple):
    """The one reading of a Suite's guardrail rules, with what was wrong with them."""

    rules: dict[str, GuardrailRule]
    where: dict[str, str]
    """Each rule id's path in the document, for a problem or warning about it."""

    problems: list[tuple[str, str]]
    """(path, message) for every malformed or duplicated rule; `validate` reports them."""


def parse_guardrail_rules(suite: Suite) -> GuardrailRules:
    """Every rule the Suite-level `guardrails` declarations author, by id."""
    rules: dict[str, GuardrailRule] = {}
    where_of: dict[str, str] = {}
    problems: list[tuple[str, str]] = []
    for number, declaration in enumerate(suite.evals):
        if declaration.eval != "guardrails":
            continue
        where = f"evals[{number}]"
        listed = declaration.params.get("rules")
        if not isinstance(listed, list) or not listed:
            problems.append(
                (where, "a Suite-level guardrails declaration authors `rules: [{id, rule}]`")
            )
            continue
        for position, entry in enumerate(listed):
            try:
                rule = GuardrailRule.model_validate(entry)
            except ValidationError:
                problems.append(
                    (
                        f"{where}.rules[{position}]",
                        "a guardrail rule is a mapping with an `id` and a `rule`",
                    )
                )
                continue
            if rule.id in rules:
                problems.append(
                    (f"{where}.rules[{position}]", f"duplicate guardrail rule id {rule.id!r}")
                )
                continue
            rules[rule.id] = rule
            where_of[rule.id] = f"{where}.rules[{position}]"
    return GuardrailRules(rules, where_of, problems)


def picked_rule_ids(declaration: EvalDeclaration) -> tuple[list[str], str | None]:
    """The rule ids a Scenario-level `guardrails` picks, and what is wrong if anything is."""
    listed = declaration.params.get("rules")
    if isinstance(listed, str):
        listed = [listed]
    if not listed:
        return [], (
            "a Scenario-level guardrails declaration names at least one rule id; to judge "
            "this Scenario by no Suite-level Eval at all, write `inherit_suite_evals: false`"
        )
    if not isinstance(listed, list) or not all(isinstance(rule, str) for rule in listed):
        return [], (
            "a Scenario-level guardrails declaration names rule ids from the Suite's "
            "guardrails; rules are authored at Suite level"
        )
    return list(listed), None


# --- Fixtures ---


def _flat_fixture_form(schema: dict[str, Any]) -> None:
    """A Fixture is authored as its data plus an optional `kind`, so that is what's published."""
    schema.clear()
    schema.update(
        {
            "title": "FixtureSpec",
            "description": (
                "A named Fixture: `kind` (default `data`) and any further keys, which are "
                "its data, handed to the Adapter as written."
            ),
            "type": "object",
            "properties": {"kind": {"type": "string"}},
            "additionalProperties": True,
        }
    )


class FixtureSpec(BaseModel):
    """Data a Scenario needs to exist before it runs, as the Suite names it (ADR-0001 §6)."""

    model_config = ConfigDict(json_schema_extra=_flat_fixture_form)

    kind: str = "data"
    data: dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def _flat(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                "kind": value.get("kind", "data"),
                "data": {key: item for key, item in value.items() if key != "kind"},
            }
        return value


class Fixture(BaseModel):
    """Data a Scenario needs to exist before it runs, resolved by name from its Suite.

    What `Adapter.open` and `Adapter.check_fixtures` take. The Scenario declares it; the
    mechanism that injects it is Adapter configuration, not Scenario schema (ADR-0001
    point 6), which is why `kind` and `data` are open. It lives here rather than beside the
    Adapter because selection resolves it (ticket 07), and `agentdiag.scenario` must stay
    offline while `agentdiag.adapter` brings the SDK.
    """

    name: str
    kind: str
    data: dict[str, Any] = Field(default_factory=dict)


# --- Scenario and Suite ---


def _max_turns_when_simulated(schema: dict[str, Any]) -> None:
    """Decision 10 in the published schema: a `simulate` Turn requires `max_turns`."""
    schema["if"] = {
        "properties": {"turns": {"contains": {"type": "object"}}},
        "required": ["turns"],
    }
    schema["then"] = {"required": ["max_turns"]}


class Scenario(BaseModel):
    """One test definition: the Turns the user produces, the Fixtures that must exist, and
    the Evals that judge the result (CONTEXT.md)."""

    model_config = ConfigDict(extra="forbid", json_schema_extra=_max_turns_when_simulated)

    id: str
    """Unique within its Suite and stable across rewording (ADR-0002 §8)."""

    title: str
    tags: list[str] = Field(default_factory=list)
    provenance: str | None = None
    """Where this Scenario came from: a real chat, a ticket, an audit finding."""

    notes: str | None = None
    """What a pass does and does not prove, for the Judge and for a reader."""

    fixtures: list[str] = Field(default_factory=list)
    """Names into the Suite's `fixtures`."""

    turns: list[Turn] = Field(min_length=1)
    max_turns: int | None = Field(default=None, ge=1)
    """Required with a `simulate` Turn, optional otherwise (decision 10)."""

    continues: str | None = None
    """An earlier Scenario whose Adapter session this one resumes (decision 13)."""

    evals: list[EvalDeclaration] = Field(default_factory=list)
    """As authored; once loaded, the effective list with the Suite's inherited (D17)."""

    focus: str | None = None
    """The id of the one Eval, or guardrail rule, this Scenario exists to prove."""

    ground_truth: Any = None
    """Free: what the right answer is, carried to the Judge and to the Report."""

    inherit_suite_evals: bool = True
    extras: dict[str, Any] = Field(default_factory=dict)
    """Carried, never interpreted (decision 9)."""

    @model_validator(mode="after")
    def _turn_budget(self) -> Self:
        """Decision 10: a simulate Turn needs a bound; literal Turns are their own bound."""
        literal = len(self.literal_turns)
        if self.simulated:
            if self.max_turns is None:
                raise ValueError(
                    "a Scenario with a simulate Turn declares max_turns: the stop criterion "
                    "says how the conversation ends, max_turns says by when (decision 10)"
                )
            if self.max_turns <= literal:
                raise ValueError(
                    f"max_turns is {self.max_turns} and the Scenario already has {literal} "
                    "literal Turns, which leaves the simulate Turn no room"
                )
        elif self.max_turns is not None and self.max_turns != literal:
            raise ValueError(
                f"max_turns is {self.max_turns} and the Scenario has {literal} literal Turns "
                "and no simulate Turn; without one, max_turns can only equal the Turn count"
            )
        return self

    @property
    def literal_turns(self) -> list[str]:
        return [turn for turn in self.turns if isinstance(turn, str)]

    @property
    def simulated(self) -> bool:
        return any(isinstance(turn, SimulateTurn) for turn in self.turns)

    @property
    def simulate(self) -> SimulateSpec | None:
        """The `simulate` Turn's spec: the last Turn's, the one place `validate` lets it be
        (phase-5 decision 55). None for a Scenario of literal Turns."""
        last = self.turns[-1]
        return last.simulate if isinstance(last, SimulateTurn) else None

    @property
    def kinds(self) -> frozenset[str]:
        """The derived kind tags (D18, decision 2), never authored and never serialised.

        Exactly one of `one_shot`, `conversation`, `simulated`; plus `tool` when any
        effective Eval is in the tool family.
        """
        if self.simulated:
            shape = "simulated"
        elif len(self.turns) == 1:
            shape = "one_shot"
        else:
            shape = "conversation"
        tool = any(
            (spec := REGISTRY.get(declaration.eval)) is not None and spec.tool_family
            for declaration in self.evals
        )
        return frozenset({shape, "tool"} if tool else {shape})


class Suite(BaseModel):
    """A named, authored collection of Scenarios for one Target (CONTEXT.md, D17)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    target: str
    """The Target's name, as its Manifest gives it."""

    description: str | None = None
    evals: list[EvalDeclaration] = Field(default_factory=list)
    """Inherited by every Scenario that does not opt out (decision 17)."""

    fixtures: dict[str, FixtureSpec] = Field(default_factory=dict)
    not_run: dict[str, str] = Field(default_factory=dict)
    """Scenario id to the reason it is deliberately skipped."""

    scenarios: list[Scenario] = Field(min_length=1)
    extras: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def _version_one(cls, version: int) -> int:
        if version != 1:
            raise ValueError(f"this agentdiag reads schema_version 1; the Suite says {version}")
        return version

    def guardrail_rules(self) -> dict[str, GuardrailRule]:
        """The rules the Suite authors, by id; `validate` reports the malformed ones."""
        return parse_guardrail_rules(self).rules


# --- the effective Scenario (decision 17) ---


def effective_evals(scenario: Scenario, suite: Suite) -> list[EvalDeclaration]:
    """The Scenario's authored declarations, then each Suite-level one it did not replace.

    `inherited` is set here, by construction, on the copies this function makes, and
    never read from what was authored.
    """
    if not scenario.inherit_suite_evals:
        return list(scenario.evals)
    replaced = {declaration.key() for declaration in scenario.evals}
    inherited = [
        declaration.model_copy(update={"inherited": True})
        for declaration in suite.evals
        if declaration.key() not in replaced
    ]
    return [*scenario.evals, *inherited]


def effective_suite(suite: Suite) -> Suite:
    """Every Scenario with the Suite-level declarations it inherits: what a Run executes."""
    return suite.model_copy(
        update={
            "scenarios": [
                scenario.model_copy(update={"evals": effective_evals(scenario, suite)})
                for scenario in suite.scenarios
            ]
        }
    )


def applicable_rule_ids(scenario: Scenario, suite: Suite) -> list[str]:
    """The guardrail rules that judge an effective Scenario.

    A Scenario-level `guardrails` picks rule ids, and replaces the inherited declaration
    when their ids match (decision 17); an inherited Suite-level declaration that survived
    applies every rule the Suite authors.
    """
    picked: dict[str, None] = {}
    for declaration in scenario.evals:
        if declaration.eval != "guardrails":
            continue
        if declaration.inherited:
            picked.update(dict.fromkeys(suite.guardrail_rules()))
        else:
            picked.update(dict.fromkeys(picked_rule_ids(declaration)[0]))
    return list(picked)


def with_guardrail_rules(scenario: Scenario, suite: Suite) -> Scenario:
    """The Scenario with each `guardrails` declaration carrying its rules, not their ids.

    A Scenario-level `guardrails` names rule ids into its Suite (decision 16), and the
    Eval that judges them is handed a Scenario, not its Suite; so preflight resolves the
    ids here, where both are known. An inherited declaration already carries the Suite's
    rules. An id the Suite does not author stays an id — `validate` refuses it, and the
    Eval scores it as a rule with no text.
    """
    authored = suite.guardrail_rules()
    evals = []
    for declaration in scenario.evals:
        if declaration.eval == "guardrails" and not declaration.inherited:
            picked, _ = picked_rule_ids(declaration)
            rules = [authored[rule].model_dump() if rule in authored else rule for rule in picked]
            declaration = declaration.model_copy(
                update={"params": {**declaration.params, "rules": rules}}
            )
        evals.append(declaration)
    return scenario.model_copy(update={"evals": evals})


def field_keys(model: type[BaseModel]) -> list[str]:
    """The keys an author writes for `model`, in declaration order: each field's alias
    where it has one (`from` for `from_`), else its name."""
    return [field.alias or name for name, field in model.model_fields.items()]


AUTHORED_OBJECTS: dict[str, type[BaseModel]] = {
    "": Suite,
    "scenarios": Scenario,
    "evals": EvalDeclaration,
    "simulate": SimulateSpec,
    "stop_when": StopWhen,
}
"""Each object an author writes in a Suite, by the key it is written under (`""` for the
Suite itself): what an unknown key's message lists the keys of, and what the generation
skill's reference describes."""

__all__ = [
    "AUTHORED_OBJECTS",
    "CONVERT_HINT",
    "LEGACY_EVAL_NAMES",
    "TURN_TAGS",
    "EvalDeclaration",
    "Fixture",
    "FixtureSpec",
    "GuardrailRule",
    "JudgeOverride",
    "Scenario",
    "SimulateSpec",
    "SimulateTurn",
    "StopWhen",
    "Suite",
    "Turn",
    "applicable_rule_ids",
    "bind_short_form",
    "effective_evals",
    "effective_suite",
    "field_keys",
    "parse_guardrail_rules",
    "picked_rule_ids",
    "short_form",
    "with_guardrail_rules",
]
