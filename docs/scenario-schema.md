# The Scenario schema

A Suite is one YAML (or JSON) file: a named, authored collection of Scenarios for one
Target. This page lists every field, the rule it obeys, and the forms an Eval declaration
may take. The machine-readable form is [`schemas/suite.schema.json`](../schemas/suite.schema.json),
generated from the same models `agentdiag validate` runs, so the two never disagree. The
decisions behind it are in [ADR-0002](adr/0002-one-scenario-schema.md).

```bash
agentdiag validate [--root PATH] [PATH...]   # offline; errors exit 3, warnings exit 0
```

## Suite

| Field | Type | Rule |
|---|---|---|
| `schema_version` | int | `1`, the default. Any other value is an error. |
| `target` | string | Required. The Target's name, as its Manifest gives it. |
| `description` | string | Optional. What the Suite covers and where it came from. |
| `evals` | list of Eval declarations | Inherited by every Scenario that does not opt out (see *Inheritance*). |
| `fixtures` | mapping name → Fixture | Named data Scenarios may ask for. A Fixture is written flat: an optional `kind` (default `data`) and any other keys, which are its data, handed to the Adapter as written. |
| `not_run` | mapping Scenario id → reason | Scenarios deliberately skipped. Each id must name a Scenario in this Suite. |
| `scenarios` | list of Scenarios | Required, at least one. Ids unique within the Suite. |
| `extras` | mapping | Carried, never interpreted. What an author or a tool must keep that the schema has no slot for. |

Any other key is an error that says to move it under `extras`: a typo such as `max_turn`
must not leave a Scenario unbounded.

## Scenario

| Field | Type | Rule |
|---|---|---|
| `id` | string | Required, unique within the Suite, stable across rewording. `--scenario` selects by it and a comparison joins on it. |
| `title` | string | Required. One line. |
| `tags` | list of strings | Free vocabulary for selection. |
| `provenance` | string | Where the Scenario came from: a real chat, a ticket, an audit finding. Carried to `run.json` and `show`. |
| `notes` | string | What a pass does and does not prove. Carried to the Judge, `run.json` and `show`. |
| `fixtures` | list of strings | Names into the Suite's `fixtures`; each must exist there. The Adapter receives them when the session opens and records one `fixture/applied` Event each. |
| `turns` | list of Turns | Required, at least one. A Turn is a literal user message (a string) or a `simulate` marker. |
| `max_turns` | int ≥ 1 | Required when any Turn is `simulate`, and then greater than the number of literal Turns. Without a `simulate` Turn it may be left out; written, it must equal the number of Turns. |
| `continues` | string | The id of an earlier Scenario in the same Suite whose Adapter session this one resumes. The continued Scenario must come first and must be selected with it. A continuer's `fixtures` are empty or exactly the continued Scenario's: the session is already open with them applied, and the same chat as a different customer is a Scenario error. |
| `evals` | list of Eval declarations | This Scenario's own; the Suite's are added at load (see *Inheritance*). |
| `focus` | string | The id of the one Eval this Scenario exists to prove: a declaration's `id`, the name of an Eval it declares or inherits, or the id of a guardrail rule that judges it. |
| `ground_truth` | any | Free: the right answer, for the Judge and the Report. |
| `inherit_suite_evals` | bool | Default `true`. `false` opts out of every Suite-level declaration. |
| `extras` | mapping | Carried, never interpreted. |

`kind` is never written. It is derived as a set of tags: exactly one of `one_shot` (one
literal Turn), `conversation` (two or more Turns, all literal) or `simulated` (any
`simulate` Turn), plus `tool` when any effective Eval is in the tool family
(`expect_tools`, `expect_tools_order`, `expect_tools_any`, `forbid_tools`,
`tool_count_max`, `expect_tool_args`, `tool_latency`, `data_query`, `tool_choice`).

### The `simulate` Turn

```yaml
- simulate:
    goal: Get order NB-1042 cancelled.          # required: what the simulated user wants
    persona: A terse customer.                   # optional: its character
    known_facts: {order_id: NB-1042}             # what it may say when asked
    unknown_facts: {order_status: processing}    # what it must not reveal
    hints:                                       # how it reacts to the Target
      - If asked for the order number, give it.
    stop_when: {tool_called: cancel_order}       # exactly one of stop_when or stop_token
```

`stop_when` holds exactly one predicate: `tool_called: <tool>`, `target_says_any: [..]`
(a non-empty list of non-empty phrases), or `judged: <a prose question a Judge decides>`.
`stop_token: <string>` is the alternative. Neither, or both, is an error (ADR-0002 §5). A
Scenario has one `simulate` Turn, and it is the last: literal Turns may open the
conversation, and nothing follows the Turn whose stop criterion ends it. A Scenario with a
`simulate` Turn declares `max_turns`, counting its literal Turns.

When a Run reaches the `simulate` Turn, a Simulated User plays the user (D26–D28). It is a
model — `claude-sonnet-5` unless `run --simulated-user-model` names another, at
`--simulated-user-effort` — told the goal, the persona, the known facts, the unknown facts it
must never state, hint at or ask about, and the hints in order, and shown the conversation
so far. Before the first simulated Turn and after every Turn, agentdiag checks the stop
criterion over the Trace: `tool_called` and `target_says_any` by reading it, `judged` by one
Judge call per Turn, recorded in the Trace under `agentdiag` and priced there. The Trial
ends `stop_when` when the criterion holds, `stop_token` when the Simulated User answers the
token (which is never delivered to the Target), and `max_turns` at the bound; `show` prints
which, with what held. A Simulated User that refuses, is cut off or answers outside its
schema ends the Trial `simulated_user_error`, and every Eval scores `invalid` with
`fault_source: simulated_user`: its failure is never the Target's.

The Simulated User's calls are in the Trace as its own actor, `simulated_user`, in a
`simulate` Span between Turns, so a Turn's duration and the Target's cost are the Target's
alone; the Judge is shown the user's messages and never the Simulated User's own calls.
`run.json` records the Simulated User's model, effort, Backend, full prompt and sampling
with its own Fingerprint. `--simulated-user-temperature` declares a temperature, recorded
per call as `accepted` or `not_supported` (a refusal is resent once without it) and printed
beside pass^k.

Every `fail` of a simulated Trial is reviewed by the Simulated User reviewer, a stronger
model (`claude-opus-5`, `--reviewer-model` on `run` and `rescore`) that reads the Simulated
User's messages against what it was told. When a message of the Simulated User's broke the
Trial — a hidden fact leaked, the conversation stopped early — every `fail` is rewritten
`invalid` with `fault_source: simulated_user` and `fault_direction` `helped` or `hindered`,
and the Diagnosis says so. Passes are never reviewed.

Each Turn, literal or simulated, may take `turn_timeout` seconds, set per environment in the
Manifest's `adapter.environments.<name>` block (default 600). A Turn that overruns ends the
Trial `timeout`, every Eval `incomplete`, and records no reply from the Target.

## Eval declarations

Three spellings of one declaration:

```yaml
evals:
  - prompt_adherence                             # a name alone: no parameters
  - expect_tools: [lookup_order, cancel_order]   # name: value, bound to the primary parameter
  - eval: expect_tools                           # the long form
    id: both-tools
    params: {tools: [lookup_order, cancel_order]}
```

The long form carries `eval`, `id` (to cite this declaration, and for `focus`), `params`,
`threshold` (for a Metric Eval) and `judge: {model, effort}` (for a judged Eval, overriding
the Run's Judge). A key beyond those is a warning and is ignored. `inherited` is never
written: the loader sets it on the declarations a Scenario inherits, and an authored one is
an error.

In `name: value`, the value binds to the Eval's primary parameter, or to `threshold` for a
Metric. A mapping that already names the primary parameter *is* the parameters, which is
how a declaration with a second parameter is written short:

```yaml
- must_say_any: {phrases: [cancelled], turn: 2}  # only the second Turn's messages
- response_latency: {max_ms: 8000}               # a Metric: the value is the threshold
```

| Eval | Kind | Primary parameter |
|---|---|---|
| `expect_tools`, `expect_tools_order`, `expect_tools_any`, `forbid_tools` | mechanical | `tools` |
| `tool_count_max` | mechanical | `counts` (`{tool: cap}`) |
| `expect_tool_args` | mechanical | `args` (`{arg: {equals \| contains \| present \| min \| none_in: ..}}`), plus optional `tool` |
| `must_say_any`, `must_not_say`, `forbidden_phrases` | mechanical | `phrases` |
| `tool_latency`, `response_latency`, `first_token_latency` | mechanical Metric | `threshold` (`{max_ms}`) |
| `prompt_adherence` | judged | none |
| `guardrails` | judged | `rules` |
| `goal` | judged | `expected` (what a pass looks like) |
| `data_grounding`, `data_query`, `tool_choice` | judged | none |

Every tool and text Eval also takes `turn: <n>`, restricting it to that Turn. An Eval name
the catalogue does not hold is a warning at `validate` and scores `unverifiable` /
`eval_not_applicable` at run time; written short, its value is kept as `params.value`, with
a second warning. Every row of the table is performed; a judged row needs credentials for
a live Run (a replayed one needs none).

### The mechanical Evals

A mechanical Eval decides from the Trace alone and costs no tokens. Every one of its Scores
cites the Span ids it read as `evidence`, names its code as `source.code`
(`agentdiag.eval.tools:expect_tools`, …), and records as `fidelity` the lowest Fidelity among
the Spans it read, or the Trace's when it read none.

| Eval | Parameters (primary first) | Threshold | Min Fidelity | `pass` when | Evidence |
|---|---|---|---|---|---|
| `expect_tools` | `tools: [names]`, `turn?` | — | `reconstructed` | every named tool has a tool Span | the Spans that satisfied it; on `fail`, every tool Span read |
| `expect_tools_order` | `tools: [names]`, `turn?` | — | `reconstructed` | the names occur, in that order, as a subsequence of the tool Spans (others may come between) | the subsequence; on `fail`, every tool Span read |
| `expect_tools_any` | `tools: [names]`, `turn?` | — | `reconstructed` | at least one named tool has a tool Span | the Spans that satisfied it; on `fail`, every tool Span read |
| `forbid_tools` | `tools: [names]`, `turn?` | — | `reconstructed` | no named tool has a tool Span | every tool Span read; on `fail`, the ones that violated it |
| `tool_count_max` | `counts: {tool: cap}`, `turn?` | — | `reconstructed` | every named tool's Span count is at most its cap | every tool Span read; on `fail`, the calls of the tools over their cap |
| `expect_tool_args` | `args: {arg: {operator: value}}`, `tool?`, `turn?` | — | `reconstructed`, arguments visible | every matching call that supplies an argument satisfies its operators | the matching calls; on `fail`, the calls that broke an operator; on `unverifiable`, the calls whose arguments were not seen |
| `must_say_any` | `phrases: [..]`, `turn?` | — | `observed` | the Target said at least one phrase | the `turn` Spans of the messages that matched; on `fail`, of every Target message |
| `must_not_say` | `phrases: [..]`, `turn?` | — | `observed` | the Target said none of the phrases | the `turn` Spans of every Target message; on `fail`, of the ones that matched |
| `forbidden_phrases` | `phrases: [..]` (added to the Manifest's), `turn?` | — | `observed` | the Target said none of the Manifest's `forbidden_phrases` nor the Scenario's additions | as `must_not_say` |
| `tool_latency` | `tool?` | `{max_ms}` | `reconstructed` | `value`, the longest `duration_ms` over the tool Spans considered, is at most `max_ms` | the tool Spans considered |
| `response_latency` | — | `{max_ms}` | `observed` | `value`, the longest `turn` Span's `duration_ms`, is at most `max_ms` | the `turn` Spans |
| `first_token_latency` | — | `{max_ms}` | `observed` | `value`, the latest time over the Turns from a Turn's start to the first token of its first `llm_call` that observed one, is at most `max_ms` | the `turn` Spans |

Every parameter is typed: a declaration its Eval cannot read — an empty `tools`, a negative
cap, an operator that is not one of the five, an unknown key such as `tols`, a latency
threshold without `max_ms`, a `turn` that is not a Turn number — is a `validate` error at
the path of the offending value (`scenarios[0].evals[2].counts.lookup_order`).

A tool Span is a `tool_call` or a `retrieval` (a tool the Manifest's `tools` section marks
`kind: retrieval`), named by its `gen_ai.tool.name`. `expect_tool_args` matches the calls
of `tool` when it is given and every tool call otherwise, and judges each argument on the
matching calls that supply it: a call that does not supply the argument is ignored, as if
a Turn's calls were merged. Its five operators, each per argument, are:

| Operator | Holds when |
|---|---|
| `equals: v` | the argument equals `v`, numerically when both are numbers (`"2"` equals `2`), otherwise as written |
| `contains: s` or `[s, ..]` | the argument's text contains `s`, or any one of the list, case-insensitively |
| `present: true` | the argument was passed and is not empty (`present: false`: it was not) |
| `min: n` | the argument is a number of at least `n` |
| `none_in: s` or `[s, ..]` | the argument's text contains none of them; an argument never passed holds |

When no call supplies the argument, `equals`, `contains`, `min` and `present: true` fail,
and `none_in` and `present: false` hold.

**What the Verdicts mean.** Beyond `pass` and `fail`:

- **The Fidelity gate comes first.** When any Span the Eval would read is below its minimum
  Fidelity — or, reading none, the Trace is — the Score is `unverifiable` /
  `fidelity_too_low`, citing the Spans below it.
- **No tool Span at all is read through Fidelity and the responses.** In an
  `instrumented` Trace (the in-process Adapter's), `llm_call` Spans whose responses asked
  for no tool prove the Target called nothing, so `expect_tools`, `expect_tools_order`,
  `expect_tools_any` and `expect_tool_args` `fail`, and `forbid_tools` and
  `tool_count_max` `pass`, citing the `llm_call` Spans. In a `reconstructed` Trace the
  Adapter may have missed a call, so the same absence is `unverifiable` /
  `evidence_missing`. A Trace with no `llm_call` Span either is `unverifiable` /
  `evidence_missing` at any Fidelity. Once any tool Span exists the record is read as
  complete at either Fidelity: a missing tool, or a missing call to the named tool, is a
  `fail`.
- **A requested tool with no Span is never read as absent.** When a response in scope
  carries a `tool_use` block that no tool Span answers (matched by name and, when recorded,
  `gen_ai.tool.call.id`) — an unwrapped or server tool, a Target that stopped after asking —
  any Verdict that would rest on that call being absent (`forbid_tools` or `tool_count_max`
  passing, an `expect` family Eval failing, an argument never seen) is `unverifiable` /
  `evidence_missing`, citing the `llm_call` that asked.
- **Arguments the Adapter could not see are never a pass.** When a matching `tool/call`
  lists `arguments` under `not_observed`, `expect_tool_args` is `unverifiable` /
  `evidence_missing` — unless a call whose arguments were seen broke an operator, which is a
  `fail` the Trace proves.
- **The text Evals screen the Target's own messages only** (`message` Events with `actor:
  target`, `role: assistant`): never a tool result, never the user's or the Simulated User's
  messages. A phrase matches as an NFC-normalised, case-insensitive substring. With no Target
  message in scope, the Score is `unverifiable` / `evidence_missing`.
- **`forbidden_phrases` reads the Manifest.** When the Manifest carries
  `forbidden_phrases` — an empty list included — every Scenario that does not declare
  `forbidden_phrases` itself gets one inherited declaration of it, marked `inherited` in
  `run.json`; an empty list scores `pass` saying the Manifest declares none. When the key is
  absent no Score is added. A Scenario's own `- forbidden_phrases: [..]` adds its phrases to
  the Manifest's in one Score, and with no Manifest list is screened alone. A bare
  `- forbidden_phrases` with no Manifest list has nothing to screen and is `unverifiable` /
  `eval_not_applicable`, never a vacuous `pass`.
- **The latency Evals are Metrics plus a threshold.** Each Score stores `value`,
  `threshold` and `direction: minimize` beside the Verdict, so a later change to the
  threshold never re-interprets a stored Score; the durations themselves are on every Span
  whether or not an Eval thresholds them. With no finished Span to measure there is no
  value, and the Score is `unverifiable` / `evidence_missing`. `first_token_latency` over
  responses that were not streamed is `unverifiable` / `evidence_missing`: nothing
  non-streaming observes a first token.
- **A declaration the Eval cannot read** that reaches a Run anyway (a Suite built in code,
  not through `validate`) scores `invalid` with `fault_source: scenario`.
- **`turn: <n>`** restricts a tool or text Eval to the Spans and messages of that Turn. It
  exists for per-turn expectations written against one Turn of a longer conversation; most
  Suites rarely need it.

A negated Eval only swaps `pass` and `fail`: an `unverifiable` or `invalid` from any of
these stays what it is.

### The judged Evals

A judged Eval asks a Judge — `claude-opus-5` unless the Run or the declaration says
otherwise — one question about the Trace, and returns the Judge's Verdict, its rationale
and the Span ids it cites. Every judged prompt is the same head around the Eval's own
question, in this order: the Eval's question; `## The Scenario` (id, title, notes, the
`goal` declaration's `expected` as `goal`, a `simulate` Turn's goal as `user goal`, and
`## Ground truth` as YAML when the Scenario records one); `## The Target's prompt sections`
(its system prompt split into numbered rules, from the Trace); `## Calibration notes for
this Target` ([docs/judge-notes.md](judge-notes.md)); `## The Trace`, every line with the
Span id a Score may cite; any section the Eval adds; the Eval's rule for deciding; and the
reverse-hallucination checklist. The prompt is a pure function of the Trace, the Scenario,
the notes and the Eval, so a replayed Trial sends the same request every time.

| Eval | Parameters | Precondition (else `unverifiable` / `eval_not_applicable`, no model called) | Minimum Fidelity |
|---|---|---|---|
| `prompt_adherence` | none | a system prompt in the Trace (else `unverifiable` / `evidence_missing`) | `observed` |
| `guardrails` | `rules` (Suite: `[{id, rule, name?}]`; Scenario: `[ids]`) | at least one rule with text | `observed` |
| `goal` | `expected` (required; `validate` refuses a `goal` without it) | `expected` declared | `observed` |
| `data_grounding` | none | at least one Target message | `reconstructed` |
| `data_query` | none | at least one lookup (a `retrieval` Span) | `reconstructed` |
| `tool_choice` | none | a tool Span, or an `instrumented` Trace (no tool Span is then the fact that none was called) | `reconstructed` |

What each Verdict means:

- **`prompt_adherence`**: `pass`, every rule of the Target's own prompt that the Trial
  exercised was followed (the rationale names the rules exercised and not exercised);
  `fail`, one rule was broken, cited by number and Span.
- **`goal`**: `pass`, what `expected` describes happened by the end of the Trial; `fail`,
  it did not, or its opposite did.
- **`guardrails`**: one Score per rule, `eval_id` the rule's id, all from one Judge call:
  `pass`, the rule was kept; `fail`, it broke, cited by Span. A rule with no text scores
  `unverifiable` / `eval_not_applicable` without a call; a rule the Judge's answer leaves
  out is `invalid` / `fault_source: judge`.
- **`data_grounding`**: the Judge lists every factual claim in the Target's messages with
  the Span it was made in and the Span that supports it, or none. `pass`, every claim is
  supported; `fail`, one is not — no support named, or a supporting Span id that is not in
  the Trace; the rationale names it, and a Judge's `pass` beside an
  unsupported claim is recorded as the `fail` the claim says it is. Evidence is the claims'
  Spans and their support.
- **`data_query`**: `pass`, every lookup asked for what the request needed; `fail`, one
  asked for the wrong thing. **Mechanical** when the Scenario declares `expect_tool_args`
  over a lookup tool (a Manifest `kind: retrieval` tool, or no `tool`): the Score is those
  declarations re-evaluated together, `source.kind: mechanical`, and no Judge is asked.
- **`tool_choice`**: `pass`, the tools called fit the intent, none unnecessary; `fail`, a
  needed tool was missing or an unneeded one called. **Mechanical** when the Scenario
  declares a positive tool list — `expect_tools`, `expect_tools_order` or
  `expect_tools_any`: required are the `expect_tools` and `expect_tools_order` names,
  allowed those plus the `expect_tools_any` names, and it passes when every required tool
  was called, every call was allowed, and no `forbid_tools` tool was called (a list with
  `turn: n` requires or forbids in that Turn only). `forbid_tools` alone says which tools
  were wrong, not which were right, so it leaves the question to the Judge. A mechanical
  Score names its code in `source.code` and carries no Judge fields.

Every judged Score also follows the Judge's four answer rules: a cited Span id that is not
in the Trace is dropped and named in the rationale; a `pass` or `fail` citing nothing is
`unverifiable` / `evidence_missing`; a refusal, a `max_tokens` cut-off, an answer outside
the schema or a failed call is `invalid` / `fault_source: judge`, never retried.

**The Judge Fingerprint.** Every judged Score's `source` carries the requested and resolved
model, the prompt version and `judge_fingerprint`: sha256 over the prompt version, the
prompt's template (every fixed word the Judge reads, with slots for the Trial's data), the calibration notes, the requested model and the effort. Any of them
changing — a notes edit above all — changes the Fingerprint on every Score it touched, so a
comparison shows the judgement moved rather than implying the Target did. `run.json` records
under `judge` the model, the effort, every judged Eval's prompt text with its Fingerprint,
the notes, and the overrides.

**The per-Eval Judge.** A judged declaration's `judge: {model, effort}` overrides the Run's
Judge for that declaration only; a field it leaves out is the Run's. The Score's `source`
names the override's model, and `run.json`'s `judge.overrides` records it under the
declaration's `id`, or the Eval's name. Two declarations under one name asking for
different Judges — or one with an override and one without — or an effort outside `low`,
`medium`, `high`, `xhigh`, `max`, refuse the Run; give such declarations their own `id`.

```yaml
- eval: goal
  params: {expected: The order is cancelled and the customer is told so.}
  judge: {model: claude-sonnet-5, effort: high}
```

When the Judge resolves to the same model as the Target, every Score from it records
`shared_model: true`, and the Scorecard's summary and `show` warn `judge shares the Target's
model (self-preference risk)`.

**The Diagnosis.** After a Trial's Evals, every Trial with at least one judged Eval gets one
more Judge call, over the same Trace and every Score: the most likely reasons each Verdict
came out as it did, for passes as for fails, citing Spans and prompt sections. It is
written to `judgement.jsonl` as a `note` with `about: diagnosis`, and `show` prints it after
the Scores. It is never a Score and never counted; when it fails, the note says none was
produced and every Score stands. In a replayed Run the recording is checked around it: an
Eval's recorded exchange left unused overrules the Trial's Scores as before, a Diagnosis's
left unused is only reported, in a `note` and on stderr.

### Guardrails

Rules are authored once, at Suite level; a Scenario picks them by id.

```yaml
evals:
  - guardrails:
      rules:
        - {id: G1, name: No repetition, rule: Never re-send what was already said.}
        - {id: G2, rule: Answer in the customer's language.}
scenarios:
  - id: address-once
    evals:
      - guardrails: [G1]       # replaces the inherited declaration: only G1 judges this one
    focus: G1
```

Each rule id a Scenario names must be one the Suite authors, and a Scenario-level
`guardrails` names at least one: a bare `- guardrails` would replace every inherited rule
with none, so it is an error, and opting out is `inherit_suite_evals: false`. Each rule
produces one Score. A rule's `rule` may be `null` when its text lives outside the Suite (a
namespaced `constraint:*` id, say): the id is kept, `validate` warns, and it scores
`unverifiable` / `eval_not_applicable`. The rules a Scenario picks are resolved against its
Suite before the Run starts, and every rule with text is judged in one call.

### Inheritance

A Scenario's effective Evals are its own plus every Suite-level declaration, unless it says
`inherit_suite_evals: false`. A Scenario-level declaration with the same `eval` and the same
`id` as a Suite-level one (no `id` on either counts as the same) replaces it; otherwise both
apply. Inherited declarations are marked as such in the loaded Suite and in the Report.

## Examples, one per kind

Each block below is a complete Suite; `tests/test_validate_cli.py` validates every one.

**One-shot** — one literal Turn:

```yaml
schema_version: 1
target: toy-order-desk
scenarios:
  - id: cancel-processing-order
    title: Cancel an order that is still processing
    tags: [orders, cancel]
    turns:
      - "Hi, I'd like to cancel order NB-1042."
    evals:
      - prompt_adherence
      - must_say_any: [cancelled]
```

**Conversation** — two or more literal Turns, a Fixture, a Scenario that continues it:

```yaml
schema_version: 1
target: toy-order-desk
fixtures:
  carmen: {kind: identity, name: Carmen, phone: "<redacted phone>"}
scenarios:
  - id: ask-then-confirm
    title: Ask about an order, then confirm the answer
    fixtures: [carmen]
    turns:
      - "Where is order NB-1042?"
      - "So it has not shipped yet?"
    notes: A pass says the status was reported; it says nothing about the cancel path.
    ground_truth: {status: processing}
  - id: then-cancel-it
    title: Cancel it in the same conversation
    continues: ask-then-confirm
    turns:
      - "Then cancel it, please."
```

**Simulated** — a scripted opener and an adaptive tail:

```yaml
schema_version: 1
target: toy-order-desk
evals:
  - guardrails:
      rules:
        - {id: G1, rule: Never cancel an order that has shipped.}
scenarios:
  - id: cancel-adaptively
    title: Cancel an order, the customer improvising
    provenance: support ticket 4411
    turns:
      - "Hi, I want to cancel something I bought."
      - simulate:
          goal: Get order NB-1042 cancelled.
          known_facts: {order_id: NB-1042}
          hints:
            - Give the order number only when asked.
          stop_when:
            judged: The order is cancelled and the customer was told so.
    max_turns: 5
    evals:
      - goal: The order is cancelled and the customer is told so.
    focus: G1
```

**Tool** — an Eval selection over tool calls:

```yaml
schema_version: 1
target: toy-order-desk
scenarios:
  - id: lookup-before-cancel
    title: Look the order up before cancelling it
    turns:
      - "Cancel NB-1042."
    evals:
      - expect_tools_order: [lookup_order, cancel_order]
      - expect_tool_args:
          tool: cancel_order
          args: {order_id: {equals: NB-1042}}
      - tool_count_max: {cancel_order: 1}
      - {eval: tool_latency, id: fast-lookup, params: {tool: lookup_order}, threshold: {max_ms: 2000}}
```

## Legacy shapes agentdiag refuses

A Suite in a legacy shape — an extended shape (`scenarios[*].first_turn`) or a per-case CI
shape (`tests[*].turns[*].user`) — is refused by `validate` before anything else is read,
naming the shape: `this is a legacy <shape> Suite shape; rewrite it in the standard schema
(docs/scenario-schema.md)`.

### Legacy spellings

Six retired legacy `pass_criteria` names are errors wherever they appear in `evals`, and the
message says to rewrite them in the standard schema: `expect_tool`, `expect_tool_any`, `expect_tool_order`,
`expect_tool_first`, `must_not_call`, `expect_no_tool`.
