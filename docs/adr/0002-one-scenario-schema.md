---
status: accepted
date: 2026-09-22
---

# 0002 — One Scenario schema, seeded from a studied base schema, with turn sources and typed Evals

## Context

The Scenario schema is seeded from the `schema_version: 1` of a production agent-maintenance
repository studied during design, which is about 95% shared across four agents but is
split into two incompatible file formats (a scripted CI lane and an adaptive extended lane),
mixes mechanical and judged criteria in one `pass_criteria` object that nothing in code
evaluates, and carries dead fields (`seed_turns`, `tool_verification`). The simulated-user
research shows that scripted-vs-adaptive is a per-turn property (openevals, Google ADK) and
that "tool" and "one-shot" are not kinds. An early plan named four Scenario kinds; that axis
is dropped.

## Decision

1. One Scenario shape. A Scenario has a `turns` list whose entries are either a literal user
   message or a `simulate` marker carrying goal, persona, known and unknown facts, hints and
   stop criterion. A one-shot test is one literal turn. A tool test is an Eval selection.
   `kind` is a derived tag for selection and reporting, never a schema branch.
2. `pass_criteria` becomes `evals`: a list of typed Eval declarations, mechanical
   (`expect_tools`, `forbid_tools`, `must_not_say`, ...) and judged (`goal`, `guardrails`).
   The Scenario still carries its own criteria; each is something the engine evaluates.
3. Kept from the reference repository: `id`, `title`, `tags`, `provenance`, `notes`, `continues`, `focus`
   (the id of the one Eval this Scenario exists to prove; the name is inherited from the
   reference repository's `focus_guardrail`), `max_turns`, and structured `adapt_hints`
   (a list; unique to the reference repository, everyone else uses prose). Suite-level guardrails are Evals
   every Scenario inherits, with per-Scenario opt-out.
4. Dropped: `seed_turns`, `tool_verification`, the six `inject_identity` modes (Adapter
   configuration, ADR-0001). `facts_pool` and `identity` become Fixtures.
5. An adaptive Scenario must declare a stop criterion: `stop_when`, an external predicate
   over the Trace, or a stop token. The engine refuses one without. The termination reason is
   always recorded.
6. Ground truth is a field on the Scenario. A human override is a later Score with source
   `human`, not a schema change.
7. Authoring format is YAML validated by a JSON Schema; JSON is accepted. Unknown keys inside
   `evals` are warnings; the reference repository's legacy spellings are hard errors.
8. Scenario ids are strings, unique within a Suite, and stable across regeneration. The
   generator must not re-key historical Runs.

## Considered options

- Two schemas, scripted and adaptive. Rejected: the reference repository paid for two runners,
  two result shapes and two report paths.
- `kind` as a schema branch. Rejected: cannot express the reference repository's own Scenarios, which mix a
  scripted opener with an adaptive tail.
- JSON only. Rejected: goals and hints are multi-line prose, and the generator needs comments.
- A Python DSL. Rejected for v1.
- Expectations as a kind of Score, as MLflow models them. Rejected: more powerful, less
  obvious, and the human-override Score gets the post-hoc benefit without the indirection.

## Consequences

- Suites in the reference repository's shapes are rewritten once into this schema, and
  `validate` refuses the legacy shapes; `adapt_hints` survive verbatim as `hints`.
- One driver loop serves every turn source; scripted replay is one Simulated User
  implementation.
