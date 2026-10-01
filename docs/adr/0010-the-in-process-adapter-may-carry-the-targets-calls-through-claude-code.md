---
status: accepted
date: 2026-09-24
---

# 0010 — The in-process Adapter may carry the Target's calls through Claude Code

## Context

ADR-0009 let the Judge's calls go through the developer's Claude Code login, and closed with
"the Target is untouched: it still calls whatever its own Adapter calls, with its own
credentials". For the shipped example that meant a key after all: the toy Target calls the
Messages API through the `anthropic` client the in-process Adapter hands it (D6), so with a
Claude Code login and no `ANTHROPIC_API_KEY` a live `run` judged nothing, because the Target
failed at its first call. The Adapter already owns that client's transport — the HTTP one
live, the replay one in a reproduction — and captures above it, so the question is only what
sits underneath.

## Options

a. A key only. Every developer without one replays or brings a key, and the shipped example
   cannot run live on the credential most of them have.
b. A Claude Code branch inside the toy Target. The toy would import the Agent SDK and
   branch on credentials, and so stop being a Target like any other: ADR-0001 says the
   Target imports nothing of agentdiag and is driven from outside, and the Trace would then
   record calls the Adapter did not capture at the HTTP boundary.
c. A third transport under the Adapter's capture, beside the HTTP and replay ones, that
   serves `/v1/messages` over one Claude Code session, chosen by the credentials preflight
   resolved.

## Decision

Option c. The **Backend** (`CONTEXT.md`)
widens from the path of the Judge's calls to any model call's: the Target's when the
in-process Adapter makes them. Preflight resolves credentials once for every Run that is
neither a dry run nor a replay, before building the Adapter; replay wins, then the Claude
Code login selects `ClaudeCodeTransport`, else the HTTP transport and the SDK's own
credential chain. `ClaudeCodeTransport` runs one `ClaudeSDKClient` session per Adapter
session on its own thread's event loop, sends each new user message into it, offers the
Target's tools to the model as in-process MCP tools whose handlers hand the CLI the Target's
own tool results, and lays each API call's answer out from the stream events the CLI
forwards. No flag: the environment decides, and a developer who wants the API exports a key.

## Consequences

- **The request is what the Target sent; the CLI's wrapping is the Backend's own.** The
  `request` Event is still the body the SDK put on the wire, captured above the transport.
  What the CLI adds around it — whatever surrounds a replaced system prompt, the tool
  plumbing — is recorded only by the CLI version in `run.json.adapter.backend`, as ADR-0009
  says of the Judge.
- **The response body is reassembled, not wire bytes.** It is the Messages layout rebuilt per
  API call from `message_start`, the `content_block_*` events, `message_delta` and
  `message_stop` (the API's own events, not the SDK's per-block messages, whose parser drops
  block types it does not know), plus a `claude_code` block with the session id, the CLI
  version, the start-up on a session's first call and the Turn's result figures on its last.
  The transport's docstring says so.
- **The model sees prefixed tool names.** The CLI names the Target's tools
  `mcp__target__<name>`; the transport strips the prefix, so the Target reads the names it
  offered, and a tool name without it is a session error rather than a call the Target never
  offered. A tool call is matched to the Target's result by content, never by position
  (ADR-0001 point 3).
- **What this Backend cannot carry is refused, never trimmed.** Sampling parameters,
  `thinking`, `tool_choice`, `stop_sequences`, `metadata`, a list-form system prompt, a tool
  carrying `cache_control`, a server tool or a schema the SDK would rebuild, a tool result
  carrying `cache_control`, a request that changes the model, the prompt or the tools
  mid-session, and a history the Target rewrote are `ClaudeCodeRefused` by name, each ending
  with the way round it; a CLI that does not answer, ends its stream, answers before taking a
  tool result, or calls a tool that matches no block is `ClaudeCodeSessionError`. Either ends
  the Trial `agentdiag_error`: this Backend could not carry it, which is agentdiag's to fix
  or the developer's to route around with a key. What the CLI reports as a model failure is
  an HTTP error response, so the Target raises the SDK's own error and the Trial ends
  `target_error`, as it would on the API; the session answers nothing after one, since the
  CLI holds a user message the Target would send again.
- **The first Span holds the start-up.** The first `llm_call` of a session encloses the CLI's
  start (7.9 s in the first live Run, 8.9 s in the probe) as well as the model's answer; it
  carries `agentdiag.backend.startup_ms`, and `show` prints it beside that Span's duration so
  a latency read off the Trace is not mistaken for the model's.
- **`run.json.adapter.backend` records the Backend.** `{kind, cli_version}`, as for the Judge;
  `compare` diffs it like every `adapter.*` path, a configuration difference a comparison
  declares with `--expect adapter.backend`.
- **A session is a CLI subprocess for the length of the Adapter session.** A continuing
  Scenario continues it; `close` ends it, and every failed Trial now closes the session it
  holds rather than dropping it. `--no-session-persistence` keeps the conversation out of
  `~/.claude/projects/`.
- **The toy Target's code is untouched, and ADR-0001 stands.** It imports nothing of agentdiag
  and does not know which Backend its calls took. On this Backend the transport serves the
  Messages API at `api.anthropic.com` only: a Target that points its client at another host
  (a gateway, another provider) is refused, not answered by Claude Code in that host's place
  and not passed through; with a key exported it takes the HTTP transport and goes where it
  points.
