"""The Scenario reference the generation skill ships beside it (walkthrough frictions 14-18).

A coding agent in someone else's repository cannot read `docs/scenario-schema.md`, and the
walkthrough found every Eval parameter name by probing `generate --check`. So the skill
carries `scenario-reference.md`, rendered here from the models and the Eval registry: every
key of a Suite, a Scenario, an Eval declaration and a `simulate` Turn, every registered Eval
with its real parameters and a working declaration, and the `expect_tool_args` operators.
The descriptions are written here, keyed by the names the models and the registry hold;
`tests/test_generate_reference.py` asserts that every name has one and that the committed
file is this rendering, so a new Eval, parameter or key without a line fails the suite
rather than leaving the reference stale.

Offline: the models and the registry only.
"""

from __future__ import annotations

from collections.abc import Mapping

from agentdiag.eval.registry import REGISTRY
from agentdiag.eval.tools import OPERATORS
from agentdiag.scenario.models import AUTHORED_OBJECTS, field_keys

REFERENCE_NAME = "scenario-reference.md"

SUITE_KEYS: Mapping[str, str] = {
    "schema_version": "`1`.",
    "target": "The Manifest's `target.name`.",
    "description": "One line: what the Suite covers.",
    "evals": "Declarations every Scenario inherits (a Suite-level `guardrails` holds its rules).",
    "fixtures": "Named Fixtures a Scenario lists by name.",
    "not_run": "Scenario id to the reason it is kept but not run.",
    "scenarios": "The Scenarios.",
    "extras": "Anything to keep that the schema has no slot for.",
}

SCENARIO_KEYS: Mapping[str, str] = {
    "id": "Stable; `generate` derives it from the drafts, never written in a draft unless a "
    "Run already holds it.",
    "title": "What the Scenario proves; lead with the rule number (`Rule 3: …`).",
    "tags": "Selection labels; `generate` adds every tool an Eval names, forbidden ones too.",
    "provenance": "`prompt:<name>#<section>`, `tool:<name>`, `trace:…` or `change:…`.",
    "notes": "Why this Scenario exists; the Judge reads it with the title.",
    "fixtures": "Fixture names from the Suite's `fixtures`.",
    "turns": "Literal user messages, then at most one `simulate` Turn, last.",
    "max_turns": "Required with a `simulate` Turn: above the literal Turns' count.",
    "continues": "The id of an earlier Scenario whose session this one resumes.",
    "evals": "The declarations (below).",
    "focus": "The one Eval the Scenario exists to prove: a declaration's `id` or an Eval name.",
    "ground_truth": "Facts the Judge may check a reply against.",
    "inherit_suite_evals": "`false` to skip the Suite's `evals`.",
    "extras": "Anything to keep that the schema has no slot for.",
}

DECLARATION_KEYS: Mapping[str, str] = {
    "eval": "The Eval's name (the table below).",
    "id": "A label for this declaration: the Score's `eval_id` and what `focus` cites. It "
    "means nothing to the Judge.",
    "params": "The Eval's parameters (the table below).",
    "threshold": "A Metric's `{max_ms: n}`, beside `params`.",
    "judge": "`{model, effort}` for this declaration's Judge.",
    "inherited": "Set by the loader on a Suite-level declaration; never written.",
}

SIMULATE_KEYS: Mapping[str, str] = {
    "goal": "What the simulated user wants.",
    "persona": "Who it is, in a sentence.",
    "known_facts": "What it may say when asked.",
    "unknown_facts": "What it does not know and must not invent.",
    "hints": "How it behaves, one short imperative each.",
    "stop_when": "Exactly one of `tool_called: <tool>`, `target_says_any: [...]`, "
    "`judged: <question>`.",
    "stop_token": "A token the simulated user says to end the conversation.",
}

STOP_KEYS: Mapping[str, str] = {
    "tool_called": "a tool the Target calls",
    "target_says_any": "phrases the Target says",
    "judged": "a question the Judge answers yes to",
}

DESCRIBED: Mapping[str, Mapping[str, str]] = {
    "": SUITE_KEYS,
    "scenarios": SCENARIO_KEYS,
    "evals": DECLARATION_KEYS,
    "simulate": SIMULATE_KEYS,
    "stop_when": STOP_KEYS,
}
"""The descriptions of each authored object's keys, by `AUTHORED_OBJECTS`' key."""

PARAMETERS: Mapping[str, str] = {
    "tools": "tool names (a single name is a list of one)",
    "turn": "the 1-based Turn the check is limited to; absent, the whole Trial",
    "counts": "`{tool: max calls}`, each from 0",
    "args": "`{argument: {operator: value}}` (the operators below)",
    "tool": "the one tool the check is about; absent, any tool",
    "types": "`{tool: {argument: JSON type}}`",
    "phrases": "phrases, matched ignoring case and spacing (a single phrase is a list of one)",
    "threshold": "`{max_ms: n}`, a Metric's pass limit",
    "rules": "Suite level: the rule objects; in a Scenario: the rule ids it picks",
    "expected": "what a pass looks like, stated as a fact about the conversation",
}

EXAMPLES: Mapping[str, str] = {
    "expect_tools": "- expect_tools: [search_articles]",
    "expect_tools_order": "- expect_tools_order: [open_ticket, escalate]",
    "expect_tools_any": "- expect_tools_any: [lookup_order, cancel_order]",
    "forbid_tools": "- {eval: forbid_tools, params: {tools: [escalate], turn: 1}}",
    "tool_count_max": "- tool_count_max: {search_articles: 1}",
    "expect_tool_args": (
        "- {eval: expect_tool_args, params: {tool: open_ticket, "
        "args: {details: {contains: crashes}}}}"
    ),
    "tool_argument_types": "- tool_argument_types: {search_articles: {query: string}}",
    "must_say_any": "- {eval: must_say_any, params: {phrases: [KB-104]}}",
    "must_not_say": "- must_not_say: [Tr1ck-Bike-88]",
    "forbidden_phrases": "- forbidden_phrases",
    "tool_latency": "- tool_latency: {threshold: {max_ms: 1000}, tool: search_articles}",
    "response_latency": "- response_latency: {max_ms: 30000}",
    "first_token_latency": "- first_token_latency: {max_ms: 5000}",
    "prompt_adherence": "- {eval: prompt_adherence, id: rule-2}",
    "guardrails": "- guardrails: [no-refund-timing]",
    "goal": "- goal: The customer is told the ticket id.",
    "data_grounding": "- data_grounding",
    "data_query": "- data_query",
    "tool_choice": "- tool_choice",
}

MANIFEST_NEEDS: Mapping[str, str] = {
    "forbidden_phrases": "forbidden_phrases: [as an AI, language model]",
}
"""What the Manifest must hold for a declaration to check anything: with no list, a
`forbidden_phrases` declaration passes on nothing, and `validate` warns (friction 23)."""

JUDGED_NOTES: Mapping[str, str] = {
    "prompt_adherence": "judges the reply against every rule of the prompt; the Judge reads "
    "the Scenario's title and notes, so name the rule there. `id` only labels the Score",
    "data_grounding": "is every fact in a reply one a tool returned",
    "data_query": "did the tool calls ask for the data the question needs",
    "tool_choice": "was the right tool called, or rightly none",
}

OPERATOR_NOTES: Mapping[str, str] = {
    "equals": "the argument is this value (text compared ignoring case and spacing)",
    "contains": "the argument's text contains one of these",
    "present": "`true`: passed and not empty; `false`: absent or empty",
    "min": "a number at least this",
    "none_in": "the argument's text contains none of these",
}


def _table(rows: Mapping[str, str], keys: list[str]) -> str:
    return "| Key | What |\n|---|---|\n" + "".join(f"| `{key}` | {rows[key]} |\n" for key in keys)


def render_scenario_reference() -> str:
    """The reference's whole text."""
    evals = [
        "| Eval | Kind | Short form binds | Parameters | A working declaration |",
        "|---|---|---|---|---|",
    ]
    for name, spec in REGISTRY.items():
        fields = field_keys(spec.params_model) if spec.params_model is not None else []
        described = "; ".join(f"`{field}`: {PARAMETERS[field]}" for field in fields)
        if spec.kind == "judged" and not fields:
            described = f"none; {JUDGED_NOTES[name]}"
        primary = f"`{spec.primary}`" if spec.primary else "-"
        example = f"`{EXAMPLES[name]}`"
        if name in MANIFEST_NEEDS:
            example += f", with the Manifest's `{MANIFEST_NEEDS[name]}`"
        evals.append(f"| `{name}` | {spec.kind} | {primary} | {described or '-'} | {example} |")
    operators_intro = (
        "Each argument names at least one. When the tool (with no `tool`, any tool) is never "
        "called, the Score is `fail`: no call was made, so no argument held. It is "
        "`unverifiable` (evidence missing) instead when the Trace cannot prove the absence: "
        "below `instrumented` Fidelity, with no model call recorded, or when a response asked "
        "for a tool no Span answers. When calls are made but none passes an argument, every "
        "operator but `none_in` and `present: false` fails for it."
    )
    operators = "".join(f"- `{operator}`: {OPERATOR_NOTES[operator]}\n" for operator in OPERATORS)
    stops = ", ".join(
        f"`{key}` ({STOP_KEYS[key]})" for key in field_keys(AUTHORED_OBJECTS["stop_when"])
    )
    return f"""# Scenario reference

Rendered from agentdiag's models and Eval registry (`agentdiag.generate.reference`); every
key and Eval here is one `agentdiag validate` accepts. A Suite is a YAML file of Scenarios;
`agentdiag generate` writes one from drafts, which are Scenarios without an `id`.

## A Suite

{_table(SUITE_KEYS, field_keys(AUTHORED_OBJECTS[""]))}
## A Scenario

{_table(SCENARIO_KEYS, field_keys(AUTHORED_OBJECTS["scenarios"]))}
## An Eval declaration

Three spellings, one meaning: `- prompt_adherence` (the name alone), the short form
`- must_not_say: [X]` (the value binds to the Eval's primary parameter, below), and the long
form `- {{eval: must_not_say, id: …, params: {{phrases: [X]}}}}`. A Metric's threshold is
`- response_latency: {{max_ms: 30000}}` or `threshold:` beside `params`.

{_table(DECLARATION_KEYS, field_keys(AUTHORED_OBJECTS["evals"]))}
## Every Eval

{chr(10).join(evals)}

Mechanical Evals decide without a Judge and cost nothing; a judged one asks the Judge.
`forbidden_phrases` checks the Manifest's `forbidden_phrases` list (plus any phrases the
declaration adds), and `tool_argument_types` the Manifest's `eval_parameters`; with the
Manifest declaring them, every Scenario inherits both without a declaration.

## `expect_tool_args` operators

{operators_intro}

{operators}
## A `simulate` Turn

{_table(SIMULATE_KEYS, field_keys(AUTHORED_OBJECTS["simulate"]))}
`stop_when` is exactly one of {stops}.

## Prompt sections and rules

A section is one markdown heading of the prompt (`prompt.system#rules` for `# Rules`); a
numbered list inside one heading is one section, so every rule under it shares the
provenance `prompt:system#rules` and the anchor `rules`. Lead each title with the rule it
proves (`Rule 3: …`) and say the rule in `notes`: that is what tells the Judge, and a
reader, which rule a `prompt_adherence` Scenario is about.
"""


__all__ = ["REFERENCE_NAME", "render_scenario_reference"]
