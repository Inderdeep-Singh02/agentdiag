---
status: accepted
date: 2026-09-23
---

# 0009 — Judge calls may go through Claude Code

## Context

The Judge reached the model one way: the Messages API through the `anthropic` SDK, paid for
with an API key or an `ant auth login` profile (D15). Most developers who will run agentdiag
already have a Claude Code login and no API key. The Claude Agent SDK (`claude-agent-sdk`,
bundling the Claude Code CLI) can send one prompt through that login, with the system prompt
replaced, no tools, JSON-schema output and one turn. It is still Anthropic's model; what
differs is the path, and the CLI adds its own wrapping around the prompt, which can move a
Verdict without the Target moving (ADR-0003 §8).

## Options

a. API only. A developer without a key cannot judge.
b. Claude Code only. CI and anyone with a key would need a login they may not have.
c. Both, behind the `ModelClient` seam: the credentials that resolve decide which, once,
   in preflight, and the Run records the choice.

## Decision

Option c. The path is a **Backend**
(`CONTEXT.md`): `anthropic_api`, `claude_code` or `replay`. `credentials.resolve()` tries the
Claude Code login last, asked of the CLI (`claude auth status`) rather than read from its
files, so an exported key always wins and a login is the default only when nothing else
resolves. `run.json.judge.backend` records `{kind, cli_version}`; execution and `rescore`
build the client from that record and never ask the environment again. No `--judge-backend`
flag until two credentials coexist and someone needs to pick.

## Consequences

- **The response body is reassembled.** The CLI speaks its own stream; `ClaudeCodeClient`
  lays what it reported out in the Messages layout plus `structured_output` and a
  `claude_code` block (cost, per-model usage, session, turns, CLI version). It is not wire
  bytes, and its docstring says so. The request side is `request.body()` unchanged, so a
  recording made through Claude Code replays without it.
- **A Judge call is not a Claude Code session.** Every call passes
  `--no-session-persistence`, so no transcript holding the Judge's prompt and the Target's
  Trace is saved under `~/.claude/projects/` or offered for resume.
- **Structured output on this path is validate-and-retry, not constrained decoding**
  (found by the first capture batch of 2026-09-24). The CLI implements
  `output_format` as a `StructuredOutput` tool call: it checks the tool's input against the
  schema after the model has written it and re-prompts on a mismatch, where the API constrains
  decoding to the schema. So a schema `pattern` is a guarantee on `anthropic_api` and an
  instruction plus a check on `claude_code`. The Judge's session allows the one retry the CLI
  is built for (`max_turns=2`); a rejected attempt is kept verbatim under
  `claude_code.attempts`, the Judge's Span carries `agentdiag.judge.attempts`, and `show`
  prints it. The rule that a cite beginning with a Span id is read as it stays the fallback
  on both paths, and `cites_read` counts when it fired. `show`'s `Backend` line and `compare`'s
  Backend statement say which of the two a Run's Judge had.
- **Only what the SDK said is recorded, and what the backend cannot send is refused.** A
  missing `stop_reason` stays null in the body rather than becoming `end_turn`; a content
  block the backend does not know raises rather than being given a made-up type; a request
  carrying tools, sampling parameters or more than one message raises rather than being
  trimmed and reported as sent. Each is rule 4.
- **The environment is asked once.** Preflight resolves the credentials (and so probes the
  CLI) and hands them to execution on the Plan; the client is built from that, never from a
  second probe, and a CLI whose version differs from the one the Run records is refused.
- **The Judge Fingerprint covers the Backend and the CLI version.** A replayed Run and a
  live one differ on every judged Score's Fingerprint, and `compare` states a Backend change
  before any Score and labels every judged delta `judge changed`, as for any Judge change
  (D33).
- **Cost is checked, not trusted.** The CLI reports each call's cost; the Judge's Span
  carries it as `agentdiag.cost.reported_usd` beside the `llm.cost.*` priced from
  `prices.py`, with `agentdiag.cost.check` (`agrees`, `differs`, `unpriced`). The table is
  never edited by code; a `differs` is evidence for a human. The first correction came from
  that evidence the same day: the CLI writes its prompt cache with the one-hour lifetime, so
  the table gained a `cache_write_1h` column, `cost_attributes` prices a cache write by
  lifetime when the usage carries the `cache_creation` split, and `PRICE_TABLE_VERSION`
  moved to 2026-09-23. `llm.cost.prompt_details.cache_write` still carries both lifetimes'
  cost together, so no Span shape changed; only `run.json.price_table.version` reads
  differently, and `compare` diffs it.
- **Failures stay rule 4.** Any error the CLI reports, a timeout, or an SDK exception is
  `invalid` / `judge`, told in the CLI's words with its stderr. No retry (D13).
- **The test suite switches the Claude Code probe off** for every test not marked `live`,
  so no test reaches a model through a developer's login.
- The Target is untouched: it still calls whatever its own Adapter calls, with its own
  credentials. ADR-0010 lets the in-process Adapter carry those calls through the
  same Claude Code login, under its capture and without the Target changing.
