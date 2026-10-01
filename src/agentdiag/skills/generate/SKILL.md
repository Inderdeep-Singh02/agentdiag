---
name: agentdiag-generate
description: Draft a Suite of Scenarios from a Target's prompt rules and tools, and write it with agentdiag generate.
disable-model-invocation: true
---

# Generate a Suite

Turn a Target's prompt rules and tools into a Suite: at least one Scenario per rule and per tool, each stating the **correct** behaviour the rule demands, each judged by the Evals that can catch its breach. You write the drafts; `agentdiag generate` checks them, derives stable ids, and writes the Suite with a `# from <provenance>` line above every Scenario. Every step is marked **code** (run it), **judgement** (decide it, and say why in the draft's `notes`) or **human** (ask the person and record their answer).

`agentdiag <command> --help` is the reference for every flag. `scenario-reference.md`, beside this file, lists every Scenario key and every Eval with its real parameters and a declaration that validates; `agentdiag validate` names every field it rejects and the fields there are.

## Steps

1. **code** — `agentdiag target show --root <workspace> --target <slug>`. Its `Sections:` list is every prompt section id and every tool (`tool.<name>`), each with the heading or name it is read by. With no `Sections:` list, run `agentdiag sync --root <workspace> --target <slug>` first. A section is one markdown **heading** of the prompt (`prompt.system#rules` for `# Rules`), or the whole prompt (`prompt.system`) when it has none: a numbered list inside one heading is one section. A draft's `provenance` spells the section with `:` for the first `.`: `prompt.system#rules` is `prompt:system#rules`, `tool.escalate` is `tool:escalate`. Done when you hold the list of section ids.
2. **code** — Read the text behind each id: the files the Manifest's pointers name under the Target directory (`prompts/<name>.md`, `tools/<name>.json`); for an `observed` prompt, `agentdiag discover --root <workspace> --target <slug> --from-connector --env <name>` saves them there, and with no Connector, read the code the Manifest's `adapter.environments.<env>.factory` names. Done when you have read every section and every tool schema in full.
3. **judgement** — List the **rules**: every sentence that says what the Target must, must not, or may only conditionally do, numbered as the prompt numbers them, with the section id it lives in. Then list each tool with what a correct call looks like (when it is called, with which arguments). Done when every section and tool maps to at least one rule or a written reason it holds none (a greeting, a persona paragraph).
4. **human** — Two sentences of the prompt that disagree on one case (one rule says always confirm by email, another says never contact a customer who opted out) are the person's to settle, not yours: ask which wins, and record the question and the answer as a comment in the drafts file above the Scenarios it decides. A persona or tone sentence can conflict with a rule just as two rules can, and takes the same treatment. Done when every conflict step 3 found has a recorded answer.
5. **judgement** — Draft the Scenarios in `<target dir>/drafts/<suite>.yaml` (committed beside the Suite it generates; the shape is below): for each rule, a user message that puts the rule to the test, and Evals that pass only when the Target obeys it. Write the **correct expectation**: what the rule says should happen, whatever the Target does today. Lead each title with the rule it proves (`Rule 3: …`) and say the rule in `notes`, because the Judge reads both and every rule under one heading shares its provenance. A rule with a condition gets two Scenarios: one where the condition holds, checking the rule's action happens; and one where it does not, checking that nothing the rule would trigger happens (no ticket, no escalation), never forbidding what the rule is silent about. Pick Evals from the table below; each draft names its `provenance` and declares at least one Eval. Done when every rule and every tool of step 3 has a draft.
6. **code** — `agentdiag generate --root <workspace> --target <slug> --from <target dir>/drafts/<suite>.yaml --check`. It prints the id each draft gets and whether it is `new`, `kept`, `re-keyed` or `retired`; an error names the draft (`scenarios[n]`) and the field, and a warning names a declaration that would pass on nothing. Fix the drafts and rerun until the first "Done when" item holds.
7. **code** — The same command without `--check`, which writes `suites/<suite>.yaml` under the Target directory and adds it to the Manifest's `suites`. Delete `init`'s placeholder `suites/sample.yaml` and its line in `suites` (or mark it `{path: suites/sample.yaml, status: retired}`), so a plain `run` never spends a Judge on "Hello!".
8. **judgement** — Open `judge_notes.md`. When the Target and the Judge run on the same model (the Manifest's `model` against `agentdiag run --help`'s `--judge-model` default), say so there: a model judging its own output tends to favour it. Done when the notes say what a Judge must know about this Target, or nothing is needed.
9. **code** — `agentdiag run --root <workspace> --target <slug> --suite <suite> --dry-run`. It lists every selected Scenario, parses every Eval declaration's parameters as a Trial would (`evals N declarations, every parameter parsed`; a declaration that does not parse is an error naming it), and exits 0. Then, when a Run is affordable (it spends the Target's and the Judge's quota), `agentdiag run --root <workspace> --target <slug> --suite <suite>`, and for each Scenario the summary counts an `invalid` Score in, `agentdiag show <run id> <scenario id>`; an `invalid` Score whose fault is `scenario` is a draft to fix at step 5. Done when the second and third "Done when" items hold.

## Which Eval for which rule

| The rule says | Evals |
|---|---|
| never say X (a phrase, a claim, a disclosure) | `must_not_say`; a phrase the whole Target must never say goes in the Manifest's `forbidden_phrases` list instead, and every Scenario is screened for it |
| always say X / confirm X | `must_say_any` |
| look up (or check, or verify) before answering | `expect_tools_order`, plus `expect_tool_args` when the arguments are the rule |
| do Y only when Z | when Z holds: `expect_tools`; when Z does not: `forbid_tools` — two Scenarios |
| call a tool with the right arguments | `expect_tool_args` |
| a judgement (tone, length, invent nothing, stay on topic) | `prompt_adherence` with the Scenario's `focus` on it; its `id` labels the Score, and the title and notes name the rule |
| the task gets done | `goal` |

Every Eval's parameters, a declaration of it that validates, and the `expect_tool_args` operators are in `scenario-reference.md` beside this file; write each declaration from there.

`focus` names the one Eval the Scenario exists to prove, by its `id` or its name. Mechanical Evals (the tool and phrase rows) decide without a Judge and cost nothing; add a judged one only where no mechanical check can see the breach. `generate` adds to `tags` every tool an Eval names, the ones a Scenario forbids included, so `run --tag escalate` also selects the Scenarios where escalate must not happen.

## Simulated users

A rule that only shows over several Turns (asking for missing information, recovering from a refusal) gets a `simulate` Turn after a literal opener:

```yaml
turns:
  - "Hi, I need to cancel an order I placed last week."
  - simulate:
      goal: Get order NB-1042 cancelled.
      persona: A hurried customer who gives one piece of information at a time.
      known_facts: {order_id: NB-1042}
      hints:
        - Give the order number only when asked for it.
      stop_when: {tool_called: cancel_order}
max_turns: 4
```

`goal` is what the simulated user wants; `known_facts` is what it may say when asked; `hints` are how it behaves, one short imperative each. Every `simulate` Turn declares one stop criterion — `stop_when: {tool_called: …}`, `{target_says_any: […]}` or `{judged: <question>}` — and the Scenario a `max_turns` above its literal Turns.

## The drafts file

```yaml
target: <the Manifest's target.name>
suite: <suite name; default generated>
description: <one line: what this Suite covers>
scenarios:
  # Asked 2026-09-28: rule 2 (always confirm by email) and rule 5 (never contact an
  # opted-out customer) disagree for an opted-out customer. Answer: rule 5 wins.
  - provenance: prompt:system#rules     # or tool:open_ticket
    title: "Rule 2: with no matching article it says so and offers a ticket"
    notes: Rule 2, never invent article content.
    tags: [rule-2]
    turns:
      - "How do I export my ride history to Strava?"
    evals:
      - expect_tools_order: [search_articles]
      - {eval: prompt_adherence, id: rule-2}
    focus: rule-2
```

Each draft is a Scenario in the standard schema without an `id`: `generate` derives it as `<anchor>-<title>`, the anchor being the section's heading slug (`rules`), the prompt's name for a prompt with no heading, or the tool's name; a title opening with the anchor's words does not repeat them, and an id is cut to 60 characters at a word boundary, so keep each title short (the rule number and a few words) to keep the whole of it in the id. On a Suite that exists, a draft with the same provenance and title keeps its id, and so does a reworded draft whose provenance no other draft shares; a Scenario no draft matches stays, listed under `not_run` as retired. Keep a title stable once a Suite has Runs, and write `id:` in a draft only to keep an id a Run already holds. Two more provenance forms name the later sources, a Trace (`trace:<run id>/<scenario>/<n>`) and a Change record (`change:<change record id>`), anchored on the Trace's Scenario id and on the Change record id.

## Done when

1. `agentdiag generate --check` exits 0 with no warning, and every rule and tool of step 3 has a Scenario in its output;
2. `agentdiag run --dry-run --suite <suite>` lists every generated Scenario and parses every declaration;
3. when a Run was affordable, one Run of the Suite has no `invalid` Score attributed to `scenario`; when not, say so in your reply and leave the Run to the person.
