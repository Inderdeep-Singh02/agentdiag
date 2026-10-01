# Scenario reference

Rendered from agentdiag's models and Eval registry (`agentdiag.generate.reference`); every
key and Eval here is one `agentdiag validate` accepts. A Suite is a YAML file of Scenarios;
`agentdiag generate` writes one from drafts, which are Scenarios without an `id`.

## A Suite

| Key | What |
|---|---|
| `schema_version` | `1`. |
| `target` | The Manifest's `target.name`. |
| `description` | One line: what the Suite covers. |
| `evals` | Declarations every Scenario inherits (a Suite-level `guardrails` holds its rules). |
| `fixtures` | Named Fixtures a Scenario lists by name. |
| `not_run` | Scenario id to the reason it is kept but not run. |
| `scenarios` | The Scenarios. |
| `extras` | Anything to keep that the schema has no slot for. |

## A Scenario

| Key | What |
|---|---|
| `id` | Stable; `generate` derives it from the drafts, never written in a draft unless a Run already holds it. |
| `title` | What the Scenario proves; lead with the rule number (`Rule 3: …`). |
| `tags` | Selection labels; `generate` adds every tool an Eval names, forbidden ones too. |
| `provenance` | `prompt:<name>#<section>`, `tool:<name>`, `trace:…` or `change:…`. |
| `notes` | Why this Scenario exists; the Judge reads it with the title. |
| `fixtures` | Fixture names from the Suite's `fixtures`. |
| `turns` | Literal user messages, then at most one `simulate` Turn, last. |
| `max_turns` | Required with a `simulate` Turn: above the literal Turns' count. |
| `continues` | The id of an earlier Scenario whose session this one resumes. |
| `evals` | The declarations (below). |
| `focus` | The one Eval the Scenario exists to prove: a declaration's `id` or an Eval name. |
| `ground_truth` | Facts the Judge may check a reply against. |
| `inherit_suite_evals` | `false` to skip the Suite's `evals`. |
| `extras` | Anything to keep that the schema has no slot for. |

## An Eval declaration

Three spellings, one meaning: `- prompt_adherence` (the name alone), the short form
`- must_not_say: [X]` (the value binds to the Eval's primary parameter, below), and the long
form `- {eval: must_not_say, id: …, params: {phrases: [X]}}`. A Metric's threshold is
`- response_latency: {max_ms: 30000}` or `threshold:` beside `params`.

| Key | What |
|---|---|
| `eval` | The Eval's name (the table below). |
| `id` | A label for this declaration: the Score's `eval_id` and what `focus` cites. It means nothing to the Judge. |
| `params` | The Eval's parameters (the table below). |
| `threshold` | A Metric's `{max_ms: n}`, beside `params`. |
| `judge` | `{model, effort}` for this declaration's Judge. |
| `inherited` | Set by the loader on a Suite-level declaration; never written. |

## Every Eval

| Eval | Kind | Short form binds | Parameters | A working declaration |
|---|---|---|---|---|
| `expect_tools` | mechanical | `tools` | `tools`: tool names (a single name is a list of one); `turn`: the 1-based Turn the check is limited to; absent, the whole Trial | `- expect_tools: [search_articles]` |
| `expect_tools_order` | mechanical | `tools` | `tools`: tool names (a single name is a list of one); `turn`: the 1-based Turn the check is limited to; absent, the whole Trial | `- expect_tools_order: [open_ticket, escalate]` |
| `expect_tools_any` | mechanical | `tools` | `tools`: tool names (a single name is a list of one); `turn`: the 1-based Turn the check is limited to; absent, the whole Trial | `- expect_tools_any: [lookup_order, cancel_order]` |
| `forbid_tools` | mechanical | `tools` | `tools`: tool names (a single name is a list of one); `turn`: the 1-based Turn the check is limited to; absent, the whole Trial | `- {eval: forbid_tools, params: {tools: [escalate], turn: 1}}` |
| `tool_count_max` | mechanical | `counts` | `counts`: `{tool: max calls}`, each from 0; `turn`: the 1-based Turn the check is limited to; absent, the whole Trial | `- tool_count_max: {search_articles: 1}` |
| `expect_tool_args` | mechanical | `args` | `args`: `{argument: {operator: value}}` (the operators below); `tool`: the one tool the check is about; absent, any tool; `turn`: the 1-based Turn the check is limited to; absent, the whole Trial | `- {eval: expect_tool_args, params: {tool: open_ticket, args: {details: {contains: crashes}}}}` |
| `tool_argument_types` | mechanical | `types` | `types`: `{tool: {argument: JSON type}}` | `- tool_argument_types: {search_articles: {query: string}}` |
| `must_say_any` | mechanical | `phrases` | `phrases`: phrases, matched ignoring case and spacing (a single phrase is a list of one); `turn`: the 1-based Turn the check is limited to; absent, the whole Trial | `- {eval: must_say_any, params: {phrases: [KB-104]}}` |
| `must_not_say` | mechanical | `phrases` | `phrases`: phrases, matched ignoring case and spacing (a single phrase is a list of one); `turn`: the 1-based Turn the check is limited to; absent, the whole Trial | `- must_not_say: [Tr1ck-Bike-88]` |
| `forbidden_phrases` | mechanical | `phrases` | `phrases`: phrases, matched ignoring case and spacing (a single phrase is a list of one); `turn`: the 1-based Turn the check is limited to; absent, the whole Trial | `- forbidden_phrases`, with the Manifest's `forbidden_phrases: [as an AI, language model]` |
| `tool_latency` | mechanical | `threshold` | `threshold`: `{max_ms: n}`, a Metric's pass limit; `tool`: the one tool the check is about; absent, any tool | `- tool_latency: {threshold: {max_ms: 1000}, tool: search_articles}` |
| `response_latency` | mechanical | `threshold` | `threshold`: `{max_ms: n}`, a Metric's pass limit | `- response_latency: {max_ms: 30000}` |
| `first_token_latency` | mechanical | `threshold` | `threshold`: `{max_ms: n}`, a Metric's pass limit | `- first_token_latency: {max_ms: 5000}` |
| `prompt_adherence` | judged | - | none; judges the reply against every rule of the prompt; the Judge reads the Scenario's title and notes, so name the rule there. `id` only labels the Score | `- {eval: prompt_adherence, id: rule-2}` |
| `guardrails` | judged | `rules` | `rules`: Suite level: the rule objects; in a Scenario: the rule ids it picks | `- guardrails: [no-refund-timing]` |
| `goal` | judged | `expected` | `expected`: what a pass looks like, stated as a fact about the conversation | `- goal: The customer is told the ticket id.` |
| `data_grounding` | judged | - | none; is every fact in a reply one a tool returned | `- data_grounding` |
| `data_query` | judged | - | none; did the tool calls ask for the data the question needs | `- data_query` |
| `tool_choice` | judged | - | none; was the right tool called, or rightly none | `- tool_choice` |

Mechanical Evals decide without a Judge and cost nothing; a judged one asks the Judge.
`forbidden_phrases` checks the Manifest's `forbidden_phrases` list (plus any phrases the
declaration adds), and `tool_argument_types` the Manifest's `eval_parameters`; with the
Manifest declaring them, every Scenario inherits both without a declaration.

## `expect_tool_args` operators

Each argument names at least one. When the tool (with no `tool`, any tool) is never called, the Score is `fail`: no call was made, so no argument held. It is `unverifiable` (evidence missing) instead when the Trace cannot prove the absence: below `instrumented` Fidelity, with no model call recorded, or when a response asked for a tool no Span answers. When calls are made but none passes an argument, every operator but `none_in` and `present: false` fails for it.

- `equals`: the argument is this value (text compared ignoring case and spacing)
- `contains`: the argument's text contains one of these
- `present`: `true`: passed and not empty; `false`: absent or empty
- `min`: a number at least this
- `none_in`: the argument's text contains none of these

## A `simulate` Turn

| Key | What |
|---|---|
| `goal` | What the simulated user wants. |
| `persona` | Who it is, in a sentence. |
| `known_facts` | What it may say when asked. |
| `unknown_facts` | What it does not know and must not invent. |
| `hints` | How it behaves, one short imperative each. |
| `stop_when` | Exactly one of `tool_called: <tool>`, `target_says_any: [...]`, `judged: <question>`. |
| `stop_token` | A token the simulated user says to end the conversation. |

`stop_when` is exactly one of `tool_called` (a tool the Target calls), `target_says_any` (phrases the Target says), `judged` (a question the Judge answers yes to).

## Prompt sections and rules

A section is one markdown heading of the prompt (`prompt.system#rules` for `# Rules`); a
numbered list inside one heading is one section, so every rule under it shares the
provenance `prompt:system#rules` and the anchor `rules`. Lead each title with the rule it
proves (`Rule 3: …`) and say the rule in `notes`: that is what tells the Judge, and a
reader, which rule a `prompt_adherence` Scenario is about.
