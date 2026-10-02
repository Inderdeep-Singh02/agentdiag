# agentdiag guide

The full guide to agentdiag, a profiler and evaluator for agentic systems; the
[README](../README.md) has the five-minute start. agentdiag drives a Target through
Scenarios, records everything that happened as a Trace, judges each Trace with Evals, and
compares Runs.

Every word above means one thing here; [`CONTEXT.md`](../CONTEXT.md) is the dictionary.

## Five minutes

Three commands: `init` scaffolds a Target, `run` records one Trial, `show` reads it back.
Two more work on Runs you already have: `compare` sets a Baseline beside a Run, and
`rescore` judges a Run's Traces again without touching the Target.

Install from a checkout:

```bash
uv sync
uv run agentdiag --help
```

```
                                                                                
 Usage: agentdiag [OPTIONS] COMMAND [ARGS]...                                   
                                                                                
 Profile and evaluate an agentic system.                                        
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --version                     Show the agentdiag version and exit.           │
│ --install-completion          Install completion for the current shell.      │
│ --show-completion             Show completion for the current shell, to copy │
│                               it or customize the installation.              │
│ --help                        Show this message and exit.                    │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ init       Scaffold a Target in the Workspace: a Manifest, a sample Suite,   │
│            Judge notes, a gitignore.                                         │
│ validate   Check the Manifest, then its Suites and Change records, offline:  │
│            errors exit 3,                                                    │
│            warnings do not.                                                  │
│ run        Run the selected Scenarios against the Target and write one Run   │
│            directory.                                                        │
│ sync       Fingerprint the Target section by section and compare it with the │
│            last Fingerprint.                                                 │
│ pull       Write the deployed sections the Connector reads ahead into the    │
│            files the Manifest                                                │
│            points at, and print the git diff.                                │
│ push       Preview, and with --push write, the local files to the deployed   │
│            set.                                                              │
│ discover   Draft a Manifest for a Target from its repository or its          │
│            Connector, offline.                                               │
│ generate   Write a Suite from a coding agent's drafts, offline, keeping ids  │
│            across regeneration.                                              │
│ import     Import one conversation's evidence as a Run of source imported,   │
│            with one Trial.                                                   │
│ rescore    Judge a stored Run's Traces again with the current Suites and     │
│            Judge, as a new Run.                                              │
│ compare    Compare a Baseline and a Run: the configuration diff first, then  │
│            the Scores.                                                       │
│ list       List every Run, newest first, with every Verdict counted and what │
│            did not run.                                                      │
│ show       Render one Trial of a Run as one story: the Trace, the Judge's    │
│            file and the Scores.                                              │
│ export     Write one Trial as a Chrome trace file for Perfetto, or as a      │
│            speedscope profile.                                               │
│ registry   List the Workspace's Targets, derived from their Manifests and    │
│            never written down.                                               │
│ dashboard  Show every Target's Sync state, last Run, pass-rate trend and     │
│            open Change records.                                              │
│ serve      Serve the local web UI for the Workspace on 127.0.0.1 and print   │
│            its URL.                                                          │
│ index      The derived index of Runs: deleted and rebuilt from the Run       │
│            directories at will.                                              │
│ target     One Target of the Workspace, as its Manifest and its files        │
│            describe it.                                                      │
│ change     Change records: one recorded fix each, opened from a Diagnosis or │
│            a complaint and closed verified or refuted only through compare.  │
╰──────────────────────────────────────────────────────────────────────────────╯
```

Every command reads the Workspace whose `.agentdiag/` is under the directory `--root` names,
and when it names none, the nearest `.agentdiag/` at or above the current directory. The
walk up stops at the enclosing git repository's top level, so a clone inside a Workspace is
not read as part of it; `--root` names a Workspace from anywhere. A Workspace holds one
or more Targets, each under `.agentdiag/targets/<slug>/`; with one Target no command needs
`--target`. From this checkout `uv run` has to stay in the repository, so every command
below passes `--root`; with `agentdiag` installed on your PATH you drop the `uv run` and the
`--root` and work anywhere inside the Workspace instead.

### Scaffold a Target

```bash
uv run agentdiag init --root /tmp/agentdiag-demo
```

```
Wrote the scaffold into /tmp/agentdiag-demo:
  .agentdiag/targets/default/manifest.yaml
  .agentdiag/targets/default/suites/sample.yaml
  .agentdiag/targets/default/judge_notes.md
  .agentdiag/targets/default/maintainer_notes.md
  .agentdiag/targets/default/redaction.yaml
  AGENTS.md
  CLAUDE.md
  GEMINI.md
  .agentdiag/CONTEXT.md
  .gitignore

Target default (toy-order-desk), driven by the agentdiag.examples.toy:make_target factory.

Next:
  agentdiag run --scenario cancel-processing-order
  agentdiag show <run> cancel-processing-order

A judged Eval needs credentials: a Claude Code login (claude auth login) or export ANTHROPIC_API_KEY=….
```

A Workspace with one Target, `default`: five files of the Target, three a coding agent reads
at the root and the vocabulary copy under `.agentdiag/`, and the gitignore:

- **`.agentdiag/targets/default/manifest.yaml`** — the Target's identity document. Pointers,
  not copies: who the Target is, which factory the Adapter builds it from, which of its
  tools are lookups (`kind: retrieval`) and which act (`kind: action`), where the Judge's
  calibration notes are, and where its Suites live.
- **`.agentdiag/targets/default/suites/sample.yaml`** — one Suite holding one Scenario, with
  one Turn and one Eval.
- **`.agentdiag/targets/default/judge_notes.md`** — the Judge's calibration notes for this
  Target, empty but for a comment saying what belongs there
  ([`docs/judge-notes.md`](judge-notes.md)).
- **`.agentdiag/targets/default/maintainer_notes.md`** — the Maintainer notes: what whoever
  maintains the Target must know before touching it, under five headings to fill. Every
  skill reads them before its first step; the Judge never does
  ([Operate it with a coding agent](#operate-it-with-a-coding-agent)).
- **`.agentdiag/targets/default/redaction.yaml`** — the names a Change record of this Target
  never carries, `names: []` until you list one. Local and gitignored, so the names never
  reach the Workspace's history or a clone of it; `validate` warns where the file is absent.
- **`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.agentdiag/CONTEXT.md`** — the Orientation page
  a coding agent reads before its first command, its one-line imports for the Harnesses that
  read another file, and agentdiag's vocabulary; a page or an import file you wrote yourself
  is kept ([Operate it with a coding agent](#operate-it-with-a-coding-agent)).
- **`.gitignore`** — five lines, `.agentdiag/targets/*/runs/`,
  `.agentdiag/targets/*/restore-points/`, `.agentdiag/targets/*/platform/`,
  `.agentdiag/index.sqlite` and `.agentdiag/targets/*/redaction.yaml`. Runs, Restore points
  and platform stores are output, the index is derived from the Runs, and the redaction file
  is yours alone.

With no `--adapter` and no `--target`, the scaffold points at the toy Target that ships with
agentdiag — an order desk with five numbered rules, a lookup tool and an action tool — so the
first Run works before you have written an Adapter or a Scenario of your own; `--adapter toy`
writes it under any slug. Pointing it at a Target of your own is below, and describing one
not yet connected (`--target <slug>` alone) after the Workspace walkthrough.

### Credentials

agentdiag never stores a credential; preflight only asks whether one resolves, and names
the source, never the secret.

**A Claude Code login is enough, for the Judge and the toy Target alike.** If you are logged
in to Claude Code and have no API key exported, agentdiag carries every model call of a Run
through that login: the Claude Agent SDK hands them to the Claude Code CLI. The Judge's calls
go with its own prompt, no tools, one turn and no session saved. The toy Target's calls go
unchanged from its side — it still calls the Messages API through the client the in-process
Adapter hands it — and the Adapter serves them over one Claude Code session per Trial, with
the Target's own prompt and tools and nothing of Claude Code's ([ADR-0010](adr/0010-the-in-process-adapter-may-carry-the-targets-calls-through-claude-code.md)). A Run records the
Backend under `judge.backend` and `adapter.backend` in `run.json`, with the CLI version; the
Judge's is part of every judged Score's Judge Fingerprint. The first model call of a Trial
also holds the CLI's start-up, and `show` prints it beside that call's duration. Check or make
the login with:

```bash
claude auth status
claude auth login
```

A Target request the Claude Code Backend cannot carry as sent — sampling parameters,
`thinking`, `tool_choice`, `cache_control`, a system prompt given as blocks — stops the Trial
with `agentdiag_error`, naming what, rather than being trimmed. Export a key to route such a
Target through the API.

**An API key**, when you want the Target's and the Judge's calls on the API instead, or need a
key for CI. An exported key always wins over the Claude Code login:

```bash
export ANTHROPIC_API_KEY=...
```

The wizard walks through all three — it checks the Claude Code login first, then offers to
write a key to an untracked `.env` and tells you how to load it:

```bash
./scripts/credentials-wizard.sh
```

**The `ant` CLI** is the third way: install it from
[anthropics/anthropic-cli](https://github.com/anthropics/anthropic-cli/releases), run
`ant auth login`, and agentdiag picks up the profile. If nothing resolves, `run` stops before
it writes anything and says so — that is preflight, not a crash.

### Run one Scenario

```bash
uv run agentdiag run --root /tmp/agentdiag-demo --scenario cancel-processing-order
```

```
cancel-processing-order  trial 1
  prompt_adherence  fail
cancel-processing-order  trials 1  passed 0 of 1 decidable  pass^1 0.00
pass 0  fail 1  incomplete 0  unverifiable 0  invalid 0  pass rate 0% (0 of 1)  not run 0  sync not_checked (no_fingerprint)
pass^1 0.00  trials 1  excluded 0  sampling not configured
```

This first Run exits 1, by design. The toy Target breaks its own rule 3 by inventing a refund
window — its closing reply says the charge "will drop off within a few days", and the cancel
tool returned no timing — and the Judge catches it; that catch is the first thing agentdiag
shows you. `run` exits with the Verdict: 0 when every Score is `pass`, 1 when any Score is
`fail`, 2 when nothing failed but a Score is `incomplete`, `unverifiable` or `invalid`, and 3
only when agentdiag itself could not run (usage, configuration, preflight). A tool error is
never 1, so a first Run that exits 1 is agentdiag working, not the install failing.
[Exit codes](#exit-codes) has the table for every command.

The pass rate never appears alone: the counts of `incomplete`, `unverifiable` and `invalid`
and the Sync status stand beside it, because "nothing failed" and "nothing ran" look the same
in a rate. `sync not_checked (no_fingerprint)` is the honest answer for a Target that has no
Fingerprint yet — `sync` writes one, and until then no Run claims to know that the Target
has not changed underneath it. The line per Scenario and the `pass^k` line are the same
Scores seen across Trials; [Run a Scenario more than once](#run-a-scenario-more-than-once)
says what they mean.

`--scenario` can be repeated. `--tag` and `--suite` pick Scenarios by tag and by Suite,
`--trials N` runs each selected Scenario N times, and `--dry-run` shows what would run
without running it. [Choose what runs](#choose-what-runs) walks through the selection flags
on the shipped example.

### Read the same Trace as a story

```bash
uv run agentdiag show <run> cancel-processing-order --root /tmp/agentdiag-demo
```

```
Run 20260924T152010Z-7y53 · Scenario cancel-processing-order · Trial 1
Target toy-order-desk (claude-sonnet-5) · Adapter inprocess, instrumented, side effects none
Backend target replay · judge replay (structured output as recorded)
Sync not_checked (no_fingerprint) · Termination completed · 43 ms · in 2635 out 163
Cost target $0.006900 · judge $0.180170

Turn 1    42 ms
  user      Hi, I'd like to cancel order NB-1042.
  llm_call-1  chat claude-sonnet-5   26 ms (model 0 ms) · in 742 out 61 · $0.002094 · tool_use
    request  claude-sonnet-5, 1 messages, 2 tools  (full body in --json)
    retrieval-1  execute_tool lookup_order   1 ms
      → lookup_order {"order_id": "NB-1042"}
      ← {"order_id": "NB-1042", "status": "processing", "placed_at": "2026-09-20T14:22:00Z", "item":
        "Northwind Trailhead gravel bike, 54cm, slate", "total_usd": 2149.0}
  llm_call-2  chat claude-sonnet-5   1 ms (model 1 ms) · in 889 out 58 · $0.002358 · tool_use
    request  claude-sonnet-5, 3 messages, 2 tools  (full body in --json)
    tool_call-1  execute_tool cancel_order   0 ms
      → cancel_order {"order_id": "NB-1042"}
      ← {"order_id": "NB-1042", "status": "cancelled", "cancelled_at": "2026-09-22T10:15:01Z",
        "refund_usd": 2149.0}
  llm_call-3  chat claude-sonnet-5   1 ms (model 1 ms) · in 1004 out 44 · $0.002448 · end_turn
    request  claude-sonnet-5, 5 messages, 2 tools  (full body in --json)
    text  I found order NB-1042 for a Northwind Trailhead gravel bike, still processing. I've
          cancelled it, and the charge will drop off within a few days.
  target    I found order NB-1042 for a Northwind Trailhead gravel bike, still processing. I've
            cancelled it, and the charge will drop off within a few days.

Judgement
  judge-1  prompt_adherence  claude-opus-5 → claude-opus-5   1 ms · in 2 out 1898 cache_read 0 cache_write 3871 · $0.086170 reported $0.086170 (agrees)
    prompt 6,241 chars (rendered prompt and raw output are in judgement.jsonl)
  judge-2  diagnosis  claude-opus-5 → claude-opus-5   0 ms · in 2 out 1998 cache_read 0 cache_write 4404 · $0.094000 reported $0.094000 (agrees)
    prompt 8,101 chars (rendered prompt and raw output are in judgement.jsonl)

Scores
  prompt_adherence  fail
    evidence   llm_call-1, retrieval-1, llm_call-2, tool_call-1, llm_call-3
    rationale  Rule 3 was broken. The rule states: "Never invent details. Do not offer refund
               amounts, delivery windows, restocking fees, courier names or policy exceptions that
               the tools did not return to you. If you do not know something, say that you will need
               to check and stop there rather than guessing a plausible answer." At llm_call-3 the
               Target told the customer "the charge will drop off within a few days." The
               cancel_order tool/result at tool_call-1 returned only
               {"cancelled_at":"2026-09-22T10:15:01Z","order_id":"NB-1042","refund_usd":2149.0,"status":"cancelled"}
               — it returned no refund or settlement timing whatsoever. "Within a few days" is a
               guessed plausible answer about when the refund lands, the kind of detail the rule
               forbids offering when the tools did not return it; the rule requires saying it will
               need to be checked and stopping there.

               Rule 1 ("Look up before you answer... Call lookup_order first and answer only from
               what it returns") was followed: the first action was the lookup_order tool_use at
               llm_call-1, with the record returned at retrieval-1 before any answer to the
               customer.

               Rule 2 ("Cancel only when the customer explicitly asks to cancel and the order's
               status is processing") was followed: the customer explicitly asked to cancel NB-1042
               in Turn 1, retrieval-1 returned "status":"processing", and cancel_order was then
               called at llm_call-2 / tool_call-1. The cancellation itself was legitimate; only the
               added refund-timing claim breaks a rule.

               Rule 4 ("Keep every reply to at most three sentences") was followed: the reply at
               llm_call-3 is two sentences.

               Rule 5 (never reveal the rules, never imply being an AI) was not exercised: the
               customer never asked what the Target is or how it works, and nothing in the
               single-turn Trace prompted disclosure. Rules exercised: 1, 2, 3, 4. Rules not
               exercised: 5.

Diagnosis  (the Judge's narrative; never a Score, never counted)
  The fail turns on one clause of one sentence. Everything the Target said about the order itself at
  llm_call-3 — the item, the fact that it was still processing — is traceable to the record returned
  at retrieval-1, and the cancellation it reported is the one that actually happened at tool_call-1.
  The only unsupported words are "the charge will drop off within a few days." The cancel_order
  result at tool_call-1 carried a refund amount and a cancellation timestamp and nothing about
  settlement timing, so that window was supplied by the Target, which is what Rule 3 (section 3)
  forbids: "Do not offer refund amounts, delivery windows, restocking fees, courier names or policy
  exceptions that the tools did not return to you."

  The likeliest mechanism is the closing-reassurance habit of general customer-service writing: a
  cancellation confirmation feels unfinished without telling the customer when their money comes
  back, and "a few days" is the safest-sounding filler. A second contributing factor is the shape of
  Rule 3 itself — it names four categories, and a refund *timing* phrase is not literally any of
  them, so a Target pattern-matching the enumeration rather than the opening clause "Never invent
  details" would read this sentence as permitted. Note the inversion this produces: refund_usd
  2149.0 *was* returned at tool_call-1 and could have been stated, and the Target omitted it while
  inventing the timing instead. The Trace shows only the final text at llm_call-3, with no reasoning
  or tool-free deliberation, so these are the probable causes rather than observed ones.

  The Rule 1 and Rule 2 passes (sections 1 and 2) are real but were won on the easy path. The Target
  did lead with lookup_order at llm_call-1 and waited for retrieval-1 before speaking, and it
  cancelled only after seeing "status":"processing" in that result, with the customer's explicit
  cancel request on turn-1. But this Trial put the two Rule 2 conditions in alignment: the ask was
  explicit and the status was cancellable, so the discriminating half of the rule — refusing on
  shipped, delivered or already-cancelled status, and not reading a "where is my order" complaint as
  a cancel request — was never put under any pressure. Rule 1's failure branch, where the lookup
  returns no record and the Target must say so and ask the customer to check the id, was likewise
  never reached, since retrieval-1 succeeded on the first call.

  Rule 4 (section 4) passed at two sentences, but with a single turn and one order to report, the
  three-sentence ceiling was never close to binding; this says little about behaviour on a longer or
  messier conversation. Rule 5 (section 5) was not exercised at all — nothing on turn-1 asked what
  the Target is or how it works — so the Trace carries no evidence either way about the persona and
  non-disclosure requirement set out in the preamble (section 0) and Rule 5.
  cites      turn-1, llm_call-1, retrieval-1, llm_call-2, tool_call-1, llm_call-3
  sections   0, 1, 2, 3, 4, 5
```

The Diagnosis is the Judge's second call on every Trial that declared a judged Eval: why
the Verdicts came out as they did, for a pass as for a fail, citing Spans and the Target's
prompt sections. It is never a Score and never counted.

The `Backend` line says which path each side's model calls took and, for the Judge, how its
structured answer was held to its schema: constrained at decoding on the API, checked after
the fact with one retry through Claude Code, and as recorded in a replay.

The durations above come from a replay, where a recorded response arrives in under a
millisecond; a live Run shows the model's real latency in the same places. Each model Span
carries what its call cost under OpenInference's `llm.cost.*` names, priced when it was
captured from the versioned table in `agentdiag/model/prices.py` (`run.json` names the
version), and the `Cost` line sums them per actor; a model the table does not price prints `unpriced`, never `$0`. The lookup is
`retrieval-1` and the cancellation `tool_call-1` because the Manifest's `tools` section marks
`lookup_order` as `kind: retrieval`. Long text folds at
your terminal's width onto continuation lines and is never truncated. Nothing the Trace holds
is hidden: the one thing `show` summarises is the model request body, which `--json` prints in
full.

`--json` emits the Trace's lines and then the judgement's, exactly as stored:

```bash
uv run agentdiag show <run> cancel-processing-order --root /tmp/agentdiag-demo --json
```

`--follow` streams the same story while the Trial is still being written, printing a Span
when it opens and its duration when it closes, and waiting up to `--wait` seconds afterwards
for the Judge's Scores and the Diagnosis:

```bash
uv run agentdiag show <run> cancel-processing-order --root /tmp/agentdiag-demo --follow
```

## Read the Trace with `cat`

One Run directory was just written under
`/tmp/agentdiag-demo/.agentdiag/targets/default/runs/`. Its Trace is a JSONL file, one Event
per line, meant to be read as it stands:

```bash
cat /tmp/agentdiag-demo/.agentdiag/targets/default/runs/<run>/trials/cancel-processing-order/1/trace.jsonl
```

Six real lines, cut for width:

```
{"seq": 0, "ts": 1790157409423, "type": "trace/start", "actor": "agentdiag", "turn": null, "span_id": null, "parent_span_id": null, "trace_id": "20260923T095648Z-bena/cancel-processing-order/1", …
{"seq": 1, "ts": 1790157409424, "type": "span/start", "actor": "agentdiag", "turn": 1, "span_id": "turn-1", "parent_span_id": null, "kind": "turn", "name": "turn 1", "fidelity": "instrumented", …
{"seq": 2, "ts": 1790157409424, "type": "message", "actor": "agentdiag", "turn": 1, "span_id": "turn-1", "parent_span_id": null, "role": "user", "content": "Hi, I'd like to cancel order NB-1042."}
{"seq": 7, "ts": 1790157409451, "type": "span/start", "actor": "target", "turn": 1, "span_id": "retrieval-1", "parent_span_id": "llm_call-1", "kind": "retrieval", "name": "execute_tool lookup_…
{"seq": 8, "ts": 1790157409451, "type": "tool/call", "actor": "target", "turn": 1, "span_id": "retrieval-1", "parent_span_id": "llm_call-1", "tool": "lookup_order", "call_id": "toolu_01Lookup…
{"seq": 26, "ts": 1790157409463, "type": "trace/end", "actor": "agentdiag", "turn": null, "span_id": null, "parent_span_id": null, "termination": "completed", "error": null}
```

Every line opens with the same six fields — when, what, who, where — and then the fields its
own type needs:

| field            | what it says                                                                 |
|------------------|------------------------------------------------------------------------------|
| `seq`            | position in the file, 0-based and contiguous: no line is missing              |
| `ts`             | epoch milliseconds, never decreasing; a duration is one subtraction           |
| `type`           | what happened: `trace/start`, `span/start`, `message`, `tool/call`, …         |
| `actor`          | whose activity it was: `agentdiag`, `target`, `adapter`, `judge`              |
| `turn`           | which Turn it fell inside; `null` outside one                                 |
| `span_id`        | which Span it belongs to; for `span/start` and `span/end`, that Span itself   |

A Span is not a line: it is a `span/start` and a `span/end` sharing a `span_id`, and the
duration between them is computed, never stored. `parent_span_id` gives the nesting, so a
tool Span — a `retrieval` for a lookup, a `tool_call` for an action — sits under the
`llm_call` that asked for it.

## Open a Trace in Perfetto or speedscope

A Trace already holds the Span nesting and the timestamps a flame graph needs, so it renders
in either viewer without a tool of its own. Both exports are one-way: a rendering of the
Trace, never the source of truth, and nothing reads one back.

```bash
uv run agentdiag export <run> cancel-processing-order --root /tmp/agentdiag-demo --format chrome
uv run agentdiag export <run> cancel-processing-order --root /tmp/agentdiag-demo --format speedscope
```

Each writes into the current directory — `<run>.cancel-processing-order.1.chrome.json` and
`.speedscope.json` — never into the Run, which is written once and never edited. `--out PATH`
puts it somewhere else and `--out -` prints it.

**In Perfetto**: open <https://ui.perfetto.dev>, choose "Open trace file" and pick the
`.chrome.json`. Expect one process per actor (`agentdiag` for the Turn, `target` for the
Target's own work), a `turn 1` slice with the three `llm_call` slices nested under it and a
`retrieval` or `tool_call` slice under the `llm_call` that asked for it, and the tool
arguments, token counts, cost, Fidelity and `dotted_order` of every Span queryable in the SQL
view:

```sql
select name, dur from slice
```

**In speedscope**: open <https://www.speedscope.app> and drop the `.speedscope.json` on it.
Expect one profile per Turn in the profile picker, and the same nesting in the Time Order
view. A frame's name carries what a human needs — `llm_call chat claude-sonnet-5 [target,
instrumented]` — because a speedscope frame has no payload slot at all.

Both viewer checks are manual, and neither has been run on this machine.

## Exit codes

`validate` exits 0 when the Suite is valid and 3 on any error. `rescore` exits
as `run` does, over the Scores it wrote. `compare` exits 0 when it compared, and 3 when a Run
is not found or an `--expect` path names nothing in either Run, so a typo cannot quietly
declare nothing. `sync --check` exits 0 on `held`, 2 on `broken` and 3 on `not_checked`;
`sync` exits 0 once it has written the Fingerprint; `run --strict` exits 3 on `broken`.

| code | `run`                                    | `show`                              | `export`                                          |
|------|------------------------------------------|-------------------------------------|---------------------------------------------------|
| 0    | every Score is `pass`                    | the Trial was rendered              | the export was written                            |
| 1    | any Score is `fail`                      | —                                   | —                                                 |
| 2    | no `fail`, but `incomplete`, `unverifiable` or `invalid` | —                   | —                                 |
| 3    | usage, configuration or preflight error  | the Run or the Trial was not found  | the Run or Trial was not found, or `--format` is unknown |

## Try it without credentials

The same Run, replaying model exchanges recorded earlier instead of calling the API:

```bash
uv run agentdiag run --root /tmp/agentdiag-demo --scenario cancel-processing-order --replay tests/fixtures/recordings/toy-cancel.jsonl
```

It exits 1 for the same reason the live Run does: the recorded Judge fails the toy Target on
rule 3 for the refund window it invented. The recording holds the Target's three model
exchanges and the Judge's two: the Eval's and the Diagnosis's. Replay matches
each request against the recording by its exact body and fails loudly when a request was not
recorded or a recorded exchange went unused, so a Run that replays is a Run that took the
recorded path and no other. It costs nothing and reaches no network.

## Choose what runs

`--scenario`, `--tag` and `--suite` choose what runs, and each can be repeated. Values of one
flag combine as OR, different flags as AND, and a Scenario's kind (`one_shot`,
`conversation`, `simulated`, `tool`) counts as a tag even though nobody wrote it. A Suite is
named by its file's stem (`orders`) or by its path under `.agentdiag/`, with or without the
extension. With no flag at all, every Scenario runs except the ones a Suite lists under
`not_run`. `--tag` and `--suite` respect that list too. Only `--scenario` overrides it,
because naming a Scenario by id means you want that one. A selection that matches nothing,
or a value that matches no Scenario, tag or Suite, exits 3 and names the flags.

`--dry-run` prints what a Run would do and does nothing else. You get one line per selected
Scenario with its kinds, tags and Suite, then every Scenario that would not run with the
reason, then how many Eval declarations it parsed (each one's parameters read as a Trial
would read them, so a declaration that would score `invalid` for the Scenario is an error
now), then the selection expression that `run.json` would record, then the Sync the Run
would find ([Keep the Target in Sync](#keep-the-target-in-sync)). It writes no Run
directory, opens no Adapter session and needs no credentials. With the shipped example
(`cp -r examples/toy /tmp/agentdiag-toy`):

```bash
uv run agentdiag run --root /tmp/agentdiag-toy --dry-run --tag cancel --tag lookup
```

```
cancel-processing-order  kinds one_shot  tags orders,cancel  suite orders
where-is-shipped-order  kinds one_shot,tool  tags orders,lookup  suite orders
lookup-then-cancel  kinds conversation,tool  tags orders,cancel  suite orders
lookup-with-unseen-arguments  kinds one_shot,tool  tags orders,lookup  suite orders
status-lookup-asks-for-the-named-order  kinds one_shot,tool  tags orders,lookup  suite orders
cancel-looks-up-then-cancels  kinds one_shot,tool  tags orders,cancel  suite orders
authored-lists-decide-without-a-judge  kinds one_shot,tool  tags orders,lookup  suite orders
cancel-without-the-order-number  kinds simulated,tool  tags orders,cancel  suite orders
cancel-with-a-promised-refund-date  kinds one_shot  tags orders,cancel,guardrails  suite guardrails
status-question-is-not-a-cancel  kinds one_shot,tool  suite orders  not run  not_selected
greeting-calls-no-tool  kinds one_shot,tool  suite orders  not run  not_selected
delivered-order-cannot-be-cancelled  kinds one_shot  suite orders  not run  not_selected
order-details-come-from-the-lookup  kinds one_shot  suite orders  not run  not_selected
evals 30 declarations, every parameter parsed
selection tag=cancel,lookup
sync not_checked (no_fingerprint)
```

The example has two Suites, so every not-run line names its Suite: ids are unique only
within one.

The same selection narrowed by `--suite`, replayed from the recording that holds the whole
example:

```bash
uv run agentdiag run --root /tmp/agentdiag-toy --tag cancel --suite orders --replay tests/fixtures/recordings/toy-orders.jsonl
```

```
cancel-processing-order  trial 1
  prompt_adherence  fail
lookup-then-cancel  trial 1
  expect_tools_order  pass
  forbid_tools  pass
  expect_tools  pass
  tool_count_max  pass
  expect_tool_args  pass
  must_say_any  pass
cancel-looks-up-then-cancels  trial 1
  tool_choice  pass
cancel-without-the-order-number  trial 1
  expect_tools_order  pass
  goal  pass
cancel-processing-order  trials 1  passed 0 of 1 decidable  pass^1 0.00
lookup-then-cancel  trials 1  passed 1 of 1 decidable  pass^1 1.00
cancel-looks-up-then-cancels  trials 1  passed 1 of 1 decidable  pass^1 1.00
cancel-without-the-order-number  trials 1  passed 1 of 1 decidable  pass^1 1.00
where-is-shipped-order  suite orders  not run  not_selected
status-question-is-not-a-cancel  suite orders  not run  not_selected
lookup-with-unseen-arguments  suite orders  not run  not_selected
greeting-calls-no-tool  suite orders  not run  not_selected
delivered-order-cannot-be-cancelled  suite orders  not run  not_selected
order-details-come-from-the-lookup  suite orders  not run  not_selected
status-lookup-asks-for-the-named-order  suite orders  not run  not_selected
authored-lists-decide-without-a-judge  suite orders  not run  not_selected
cancel-with-a-promised-refund-date  suite guardrails  not run  not_selected
pass 9  fail 1  incomplete 0  unverifiable 0  invalid 0  pass rate 90% (9 of 10)  not run 9  sync not_checked (no_fingerprint)
pass^1 0.75  trials 1  excluded 0  sampling none declared
```

Every Scenario that did not run is listed with its reason, in the summary, in
`scorecard.json` and in `run.json`. The reason is `not_selected`, `suite_not_run` followed
by the Suite's own `not_run` text, or `fixture_unavailable` followed by the Adapter's
explanation. The last one means the Target cannot receive the Scenario's Fixtures, for
example because its factory takes no `fixtures` keyword. That Scenario is skipped and the
rest of the Run goes ahead.

## Run a Scenario more than once

A Target that calls a model is not deterministic, and one Trial says little about how often
it gets a Scenario right. `--trials N` runs the whole selection N times, one sweep after another,
into `trials/<scenario>/1/` to `N/`. In replay every Trial walks its Scenario's recorded
exchanges again from the start, so N Trials replay from one recording:

```bash
uv run agentdiag run --root /tmp/agentdiag-toy --replay tests/fixtures/recordings/toy-orders.jsonl \
  --scenario greeting-calls-no-tool --trials 3
```

```
greeting-calls-no-tool  trial 1
  forbid_tools  pass
  tool_count_max  pass
  must_not_say  pass
greeting-calls-no-tool  trial 2
  forbid_tools  pass
  tool_count_max  pass
  must_not_say  pass
greeting-calls-no-tool  trial 3
  forbid_tools  pass
  tool_count_max  pass
  must_not_say  pass
greeting-calls-no-tool  trials 3  passed 3 of 3 decidable  pass^1 1.00  pass^2 1.00  pass^3 1.00
cancel-processing-order  suite orders  not run  not_selected
where-is-shipped-order  suite orders  not run  not_selected
lookup-then-cancel  suite orders  not run  not_selected
status-question-is-not-a-cancel  suite orders  not run  not_selected
lookup-with-unseen-arguments  suite orders  not run  not_selected
delivered-order-cannot-be-cancelled  suite orders  not run  not_selected
order-details-come-from-the-lookup  suite orders  not run  not_selected
status-lookup-asks-for-the-named-order  suite orders  not run  not_selected
cancel-looks-up-then-cancels  suite orders  not run  not_selected
authored-lists-decide-without-a-judge  suite orders  not run  not_selected
cancel-without-the-order-number  suite orders  not run  not_selected
cancel-with-a-promised-refund-date  suite guardrails  not run  not_selected
pass 9  fail 0  incomplete 0  unverifiable 0  invalid 0  pass rate 100% (9 of 9)  not run 12  sync not_checked (no_fingerprint)
pass^1 1.00  pass^2 1.00  pass^3 1.00  trials 3  excluded 0  sampling not configured
```

pass^k is tau-bench's reliability number: the chance that k Trials of a Scenario, drawn from
the ones that decided something, all pass. For a Scenario with `c` passing Trials out of `n`
decidable ones it is C(c, k) / C(n, k); the Run's pass^k is the mean over the Scenarios that
had at least k decidable Trials. A Trial is decidable when every Score in it is `pass` or
`fail`. One with an `incomplete`, `unverifiable` or `invalid` Score is neither: it is marked
`excluded from pass^k` on its own line and counted beside the number, never folded in.
Nor is pass^k ever printed without the trial count and the Simulated User's sampling (not
configured until a Scenario has a `simulate` Turn), because reliability at one sampling
says little about another.

`scorecard.json` keeps every Trial's Scores and adds, per Scenario, the mean of each Eval
over its decidable Trials, a Metric's mean value and its direction, and the abnormal counts
beside them: what `compare` joins on. A Ctrl-C keeps every finished Trial, scores the one
it interrupted `incomplete` / `cancelled`, names each Trial that never started as `cancelled`
under `not_run`, and still writes the Scorecard.
[`tests/fixtures/runs/20260923T100600Z-tri3`](../tests/fixtures/runs/20260923T100600Z-tri3) is
one such Run, three Trials of three Scenarios with one slow lookup and an interrupt in the
last sweep, and `tests/test_trials_cli.py` works its pass^k out by hand.

## Compare two Runs

`compare BASELINE RUN` takes two Run directories, by path or by id under `--root`, and
reads nothing else: no index, no Trace, only each Run's `run.json` and `scorecard.json`. It
prints the configuration diff before any Score, because a Score that moved means nothing
until you know what else did. The committed Run fixtures show it: a Baseline, and the same
four Scenarios with the Target's model swapped, whose reply about a shipped order now says
"refund":

```bash
uv run agentdiag compare tests/fixtures/runs/20260923T100000Z-base tests/fixtures/runs/20260923T100100Z-swap
```

```
baseline  20260923T100000Z-base  target toy-order-desk  created 2026-09-23T10:00:00Z
run       20260923T100100Z-swap  target toy-order-desk  created 2026-09-23T10:01:00Z

configuration  1 difference(s)
  undeclared  manifest.adapter.environments.local.model  claude-sonnet-5 -> claude-sonnet-4-6

scenarios  4 in both
  only in baseline 0
  only in run 0

where-is-shipped-order
  expect_tools  mean 1.00 -> 1.00  delta +0.00  unchanged  because manifest.adapter.environments.local.model  abnormal none -> none
  expect_tools_any  mean 1.00 -> 1.00  delta +0.00  unchanged  because manifest.adapter.environments.local.model  abnormal none -> none
  forbid_tools  mean 1.00 -> 1.00  delta +0.00  unchanged  because manifest.adapter.environments.local.model  abnormal none -> none
  tool_count_max  mean 1.00 -> 1.00  delta +0.00  unchanged  because manifest.adapter.environments.local.model  abnormal none -> none
  expect_tool_args  mean 1.00 -> 1.00  delta +0.00  unchanged  because manifest.adapter.environments.local.model  abnormal none -> none
  must_say_any  mean 1.00 -> 1.00  delta +0.00  unchanged  because manifest.adapter.environments.local.model  abnormal none -> none
  must_not_say  mean 1.00 -> 0.00  delta -1.00  changed  because manifest.adapter.environments.local.model  abnormal none -> none
  forbidden_phrases  mean 1.00 -> 1.00  delta +0.00  unchanged  because manifest.adapter.environments.local.model  abnormal none -> none
  tool_latency  mean 1.00 -> 1.00  delta +0.00  value (minimize) 3.00 -> 3.00  value delta +0.00  unchanged  because manifest.adapter.environments.local.model  abnormal none -> none
  response_latency  mean 1.00 -> 1.00  delta +0.00  value (minimize) 16.00 -> 16.00  value delta +0.00  unchanged  because manifest.adapter.environments.local.model  abnormal none -> none
status-question-is-not-a-cancel
  expect_tools  mean 1.00 -> 1.00  delta +0.00  unchanged  because manifest.adapter.environments.local.model  abnormal none -> none
  forbid_tools  mean 0.00 -> 0.00  delta +0.00  unchanged  because manifest.adapter.environments.local.model  abnormal none -> none
  must_not_say  mean 0.00 -> 0.00  delta +0.00  unchanged  because manifest.adapter.environments.local.model  abnormal none -> none
greeting-calls-no-tool
  forbid_tools  mean 1.00 -> 1.00  delta +0.00  unchanged  because manifest.adapter.environments.local.model  abnormal none -> none
  tool_count_max  mean 1.00 -> 1.00  delta +0.00  unchanged  because manifest.adapter.environments.local.model  abnormal none -> none
  must_not_say  mean 1.00 -> 1.00  delta +0.00  unchanged  because manifest.adapter.environments.local.model  abnormal none -> none
delivered-order-cannot-be-cancelled
  goal  mean 1.00 -> 1.00  delta +0.00  unchanged  because manifest.adapter.environments.local.model  abnormal none -> none

unchanged 16  changed 1  1 undeclared difference: moved deltas are labelled changed
```

The `must_not_say` Score got worse, and `compare` does not call that a regression: the
model changed and nobody said it would, so the moved delta is `changed`, and every delta
names the paths that could explain it (`because`). Each delta is the change in the pass
rate over the Scenario's decidable Trials; for a Metric, its mean value and direction stand
beside it. The abnormal counts stand beside each delta and are never averaged into it.

The diff covers the Manifest (but not its list of Suites), the Adapter's kind, Fidelity,
environment and side effects, the Judge's model, effort, prompt versions and Fingerprints,
calibration notes and overrides, the Simulated User, the price table, the Fingerprint and
Sync status, agentdiag's version and packages, the Target's git commit and dirty flag,
`trials`, and each shared Scenario's Eval declarations, their parameters and thresholds. It
leaves out what every Run differs in: its id, its time and the selection.

Name what you meant to change with `--expect`, a `run.json` path or a prefix of one, and
give the flag once for each. With every difference declared, a Score that got worse is a
regression (`grep -v` drops the unchanged lines):

```bash
uv run agentdiag compare tests/fixtures/runs/20260923T100000Z-base tests/fixtures/runs/20260923T100100Z-swap \
  --expect manifest.adapter.environments.local.model | grep -v "  unchanged  "
```

```
baseline  20260923T100000Z-base  target toy-order-desk  created 2026-09-23T10:00:00Z
run       20260923T100100Z-swap  target toy-order-desk  created 2026-09-23T10:01:00Z

configuration  1 difference(s)
  declared    manifest.adapter.environments.local.model  claude-sonnet-5 -> claude-sonnet-4-6

scenarios  4 in both
  only in baseline 0
  only in run 0

where-is-shipped-order
  must_not_say  mean 1.00 -> 0.00  delta -1.00  regression  abnormal none -> none
status-question-is-not-a-cancel
greeting-calls-no-tool
delivered-order-cannot-be-cancelled

regression 1  unchanged 16  declared differences only: manifest.adapter.environments.local.model
```

A declared prompt change reads the same way; here the Run's `manifest.prompts` names a
second revision of the prompt:

```bash
uv run agentdiag compare tests/fixtures/runs/20260923T100000Z-base tests/fixtures/runs/20260923T100200Z-prmt \
  --expect manifest.prompts | grep -v "  unchanged  "
```

```
baseline  20260923T100000Z-base  target toy-order-desk  created 2026-09-23T10:00:00Z
run       20260923T100200Z-prmt  target toy-order-desk  created 2026-09-23T10:02:00Z

configuration  2 difference(s)
  declared    manifest.prompts.system.pointer  null -> agentdiag.examples.toy.prompt:SYSTEM_PROMPT
  declared    manifest.prompts.system.rev  null -> 2

scenarios  4 in both
  only in baseline 0
  only in run 0

where-is-shipped-order
  must_not_say  mean 1.00 -> 0.00  delta -1.00  regression  abnormal none -> none
status-question-is-not-a-cancel
greeting-calls-no-tool
delivered-order-cannot-be-cancelled

regression 1  unchanged 16  declared differences only: manifest.prompts.system.pointer, manifest.prompts.system.rev
```

An Eval's own declaration is configuration too. The Baseline's Traces, rescored twice, the
second time after `response_latency` on the shipped order was tightened to `max_ms: 1` in
the Suite: the Target did nothing different, the Verdict flipped, and the diff says why:

```bash
uv run agentdiag compare tests/fixtures/runs/20260923T100500Z-resc tests/fixtures/runs/20260923T100700Z-thrs \
  | grep -v "  unchanged  "
```

```
baseline  20260923T100500Z-resc  target toy-order-desk  created 2026-09-23T10:05:00Z  traces from 20260923T100000Z-base
run       20260923T100700Z-thrs  target toy-order-desk  created 2026-09-23T10:07:00Z  traces from 20260923T100000Z-base
the two Runs share Traces: only the judgement differs

configuration  1 difference(s)
  undeclared  scenarios.where-is-shipped-order.evals.response_latency.threshold.max_ms  30000 -> 1

scenarios  4 in both
  only in baseline 0
  only in run 0

where-is-shipped-order
  response_latency  mean 1.00 -> 0.00  delta -1.00  value (minimize) 16.00 -> 16.00  value delta +0.00  changed  because scenarios.where-is-shipped-order.evals.response_latency.threshold.max_ms  abnormal none -> none
status-question-is-not-a-cancel
greeting-calls-no-tool
delivered-order-cannot-be-cancelled

unchanged 16  changed 1  1 undeclared difference: moved deltas are labelled changed
```

`--expect scenarios.where-is-shipped-order.evals.response_latency` declares it, and the same
delta is then a regression. Scores are joined per Scenario id over the Scenarios both Runs
recorded; the ones in only one Run are counted and named, and nothing is aggregated over
them.

When the two Runs were judged differently, `compare` says so before any Score, and every
judged delta it touches is labelled `changed` and marked `judge changed`, even with the
change declared: across a Judge change the judged deltas measure the Judge, not the Target.
Mechanical Scores are decided by code and are not affected by it:

```bash
uv run agentdiag compare tests/fixtures/runs/20260923T100000Z-base tests/fixtures/runs/20260923T100400Z-jdge \
  | grep -v "  unchanged  "
```

```
baseline  20260923T100000Z-base  target toy-order-desk  created 2026-09-23T10:00:00Z
run       20260923T100400Z-jdge  target toy-order-desk  created 2026-09-23T10:04:00Z

configuration  1 difference(s)
  undeclared  judge.model  claude-opus-5 -> claude-opus-5-5

judge  the Score comparison is invalid across a Judge change (judge.model); judged deltas are labelled judge changed

scenarios  4 in both
  only in baseline 0
  only in run 0

where-is-shipped-order
status-question-is-not-a-cancel
greeting-calls-no-tool
delivered-order-cannot-be-cancelled
  goal  mean 1.00 -> 0.00  delta -1.00  changed  because judge.model  judge changed  abnormal none -> none

unchanged 16  changed 1  1 undeclared difference: moved deltas are labelled changed
```

`--json` prints the same comparison as JSON, in the shape
[`schemas/comparison.schema.json`](../schemas/comparison.schema.json) describes.

## Judge a Run again

`rescore RUN` judges a stored Run's Traces again, with the current Suites and the current
Judge, into a new Run. The Target is never called, and the source Run is never written: the
new `run.json` names it under `traces_from`, and the new Trials hold only `judgement.jsonl`
and `scores.json`. It is how you tune a Judge prompt, the calibration notes or an Eval's
threshold against Traces you already have. The mechanical Evals run again too, and every
Trial keeps the source's ending: one the source cancelled is still `incomplete`, and one it
never started is still `cancelled`. With the Baseline copied into a Target's `runs/`, judged
by another model:

```bash
mkdir -p /tmp/agentdiag-toy/.agentdiag/targets/toy-order-desk/runs
cp -r tests/fixtures/runs/20260923T100000Z-base /tmp/agentdiag-toy/.agentdiag/targets/toy-order-desk/runs/
uv run agentdiag rescore 20260923T100000Z-base --root /tmp/agentdiag-toy \
  --judge-model claude-opus-5-5 --replay tests/fixtures/recordings/runs-judge-swap.jsonl
```

```
where-is-shipped-order  trial 1
  expect_tools  pass
  expect_tools_any  pass
  forbid_tools  pass
  tool_count_max  pass
  expect_tool_args  pass
  must_say_any  pass
  must_not_say  pass
  forbidden_phrases  pass
  tool_latency  pass
  response_latency  pass
status-question-is-not-a-cancel  trial 1
  expect_tools  pass
  forbid_tools  fail
  must_not_say  fail
greeting-calls-no-tool  trial 1
  forbid_tools  pass
  tool_count_max  pass
  must_not_say  pass
delivered-order-cannot-be-cancelled  trial 1
  goal  fail
where-is-shipped-order  trials 1  passed 1 of 1 decidable  pass^1 1.00
status-question-is-not-a-cancel  trials 1  passed 0 of 1 decidable  pass^1 0.00
greeting-calls-no-tool  trials 1  passed 1 of 1 decidable  pass^1 1.00
delivered-order-cannot-be-cancelled  trials 1  passed 0 of 1 decidable  pass^1 0.00
cancel-processing-order  suite orders  not run  not_selected  no Trial in the source Run 20260923T100000Z-base
lookup-then-cancel  suite orders  not run  not_selected  no Trial in the source Run 20260923T100000Z-base
lookup-with-unseen-arguments  suite orders  not run  not_selected  no Trial in the source Run 20260923T100000Z-base
order-details-come-from-the-lookup  suite orders  not run  not_selected  no Trial in the source Run 20260923T100000Z-base
status-lookup-asks-for-the-named-order  suite orders  not run  not_selected  no Trial in the source Run 20260923T100000Z-base
cancel-looks-up-then-cancels  suite orders  not run  not_selected  no Trial in the source Run 20260923T100000Z-base
authored-lists-decide-without-a-judge  suite orders  not run  not_selected  no Trial in the source Run 20260923T100000Z-base
cancel-without-the-order-number  suite orders  not run  not_selected  no Trial in the source Run 20260923T100000Z-base
cancel-with-a-promised-refund-date  suite guardrails  not run  not_selected  no Trial in the source Run 20260923T100000Z-base
pass 14  fail 3  incomplete 0  unverifiable 0  invalid 0  pass rate 82% (14 of 17)  not run 9  sync not_checked (no_fingerprint)
pass^1 0.50  trials 1  excluded 0  sampling not configured
```

`--scenario`, `--tag` and `--suite` narrow a rescore to part of what the source ran.
Comparing the rescore with its source flags the shared Traces. The committed fixtures record
no package versions, so this rescore also differs in `agentdiag.packages`, declared here
with the Judge's model:

```bash
uv run agentdiag compare 20260923T100000Z-base 20260923T151158Z-rq5z --root /tmp/agentdiag-toy \
  --expect judge.model --expect agentdiag.packages | grep -v "  unchanged  "
```

```
baseline  20260923T100000Z-base  target toy-order-desk  created 2026-09-23T10:00:00Z
run       20260923T151158Z-rq5z  target toy-order-desk  created 2026-09-23T15:11:58Z  traces from 20260923T100000Z-base
the two Runs share Traces: only the judgement differs

configuration  6 difference(s)
  declared    agentdiag.packages.anthropic  null -> 1.7.0
  declared    agentdiag.packages.claude-agent-sdk  null -> 0.2.158
  declared    agentdiag.packages.pydantic  null -> 2.13.5
  declared    agentdiag.packages.pyyaml  null -> 6.0.3
  declared    agentdiag.packages.typer  null -> 0.27.2
  declared    judge.model  claude-opus-5 -> claude-opus-5-5

judge  the judged deltas measure the Judge, not the Target (judge.model declared); judged deltas are labelled judge changed

scenarios  4 in both
  only in baseline 0
  only in run 0

where-is-shipped-order
status-question-is-not-a-cancel
greeting-calls-no-tool
delivered-order-cannot-be-cancelled
  goal  mean 1.00 -> 0.00  delta -1.00  changed  because judge.model  judge changed  abnormal none -> none

unchanged 16  changed 1  declared differences only: agentdiag.packages.anthropic, agentdiag.packages.claude-agent-sdk, agentdiag.packages.pydantic, agentdiag.packages.pyyaml, agentdiag.packages.typer, judge.model
```

The id of your rescore will differ; the Run directory it wrote is the newest under `runs/`.
`show` and `export` read a rescored Trial as any other, its Trace found through
`traces_from`, and `show` opens by saying which Run it was rescored from.

## Point it at your own Target

`--adapter python:<module>:<attr>` names the factory that builds your Target, and the
scaffolded Manifest points at it instead of the toy's (a Target with nothing to drive it yet
is [described instead](#describe-a-target-before-it-is-connected)):

```bash
uv run agentdiag init --root <your-repo> --adapter python:<module>:<factory> --tools <module>:<tools>
```

The factory is called with `(client, tools, **options)` and returns a `Callable[[str], str]`
holding its own conversation state. The Adapter hands in the client and the wrapped tools, so
your Target imports nothing of agentdiag and is instrumented from the outside.

`--tools` is optional: a Target that keeps its tools inside its own factory declares none,
and is handed an empty mapping. `--model` sets what the factory is asked to run on
(`claude-sonnet-5` by default). `--force` rewrites the Manifest and the sample Suite of a
directory that already has them — never a Run, which is written once and never edited, and
never the calibration notes an author wrote; `init` without it refuses an existing
`.agentdiag/` and exits 3. `--name`, `--description`, `--family` and `--channel` fill who the
Target is, on this scaffold as on every other.

The sample Scenario `init` writes for a Target it has never run says the least that is still
true of any of them — a first user message gets an answer — and its comment says to replace
it. It is a placeholder, not a Scenario anyone should keep.

## Drive a Target over HTTP

A Target you did not write, reachable only through the chat endpoint it ships, is driven by
the `http` Adapter: one POST per Turn, the reply read as it streams. It sees what a user's
client sees, so everything it records is at Fidelity `observed`: the reply's text, the tools
the stream reports, the conversation id the Target hands back. The Manifest names the
endpoint by identifiers and its credentials by the environment variable that holds each,
never by value:

```yaml
schema_version: 1
target:
  name: http-echo
adapter:
  kind: http
  side_effects: none                  # an environment may raise it, never lower it
  environments:
    default: dev
    dev:
      base_url: http://127.0.0.1:8765  # scheme and host only
      path: /api/agents/{agent_id}/chat # the Dialect's default (/chat) when absent
      agent_id: echo-1                  # any other key is an identifier
      org_id: org-7
      headers: {x-organization-id: "{org_id}"}   # identifiers interpolate, credentials never
      credentials: {token: HTTP_ECHO_TOKEN}      # role → NAME of an environment variable
      dialect: sse-json
      identity: {mode: header, header: x-user-phone, from: phone}
      turn_timeout: 30
suites:
  - suites/http-echo.yaml
```

`dialect` names how the endpoint frames a Turn. Core ships two, agentdiag's own shapes (a
platform's framing is its plugin's Dialect, registered under the `agentdiag.dialects` entry
point). Both post `{"message": …, "conversation_id": …}` as JSON and send `authorization:
Bearer <token>` when the environment names a `token` credential:

- **`json`** reads one JSON object, `{"reply": "…", "conversation_id": "…", "tools":
  [{"name", "arguments", "result", "call_id"}]}`, every key but `reply` optional.
- **`sse-json`** reads `text/event-stream`, each `data:` line a JSON frame whose `type` is
  `text`, `tool`, `conversation`, `error` or `done`, its fields under their own names; a
  `data: [DONE]` line also ends it.

The first Turn posts no conversation id and every later one sends back the id the reply
named; a Target that names none makes every Turn a new chat, and each Turn's `response` Span
says so (`agentdiag.conversation.new`). A Scenario's Fixture of kind `identity` reaches the
Target only through the environment's `identity` mode: `header` sends `<header>:
<data[from]>` on every request, `body` puts `identity: {…}` beside the message, and
`first_message` prefixes the first message with a `template` of field names. The Trace
records which mode applied which fields, never their values.

**Credentials live in one file outside every repository**: `~/.agentdiag/env`
(`$AGENTDIAG_ENV_FILE` names another), mode `0600`, `KEY=value` lines. Every `agentdiag`
command reads it once at start-up, never overriding a variable the shell already set, and
warns when others can read it. A variable a Manifest names that is unset or empty stops the
Run at preflight, exit 3, naming it; no other environment's is ever used instead. The value
travels only as the request header the Dialect builds, and appears in no file agentdiag
writes.

Each Turn records a `response` Span (the request sent, its status, the time to the first
frame, the frames by type, `llm_calls` and `usage` not observed) with one `tool_call` or
`retrieval` Span per tool the stream reported, its arguments unknown when the stream did not
show them. A stream reports that a tool ran, not what it really did, so every tool Eval over
those Spans alone is `unverifiable / fidelity_too_low`, and preflight warns. Tool truth
comes from the Target's proxy rows: `adapter.tool_truth: {evidence: proxy}` has the Adapter
read them through the Target's Connector after each Turn and reconstruct that Turn's
`llm_call` and tool Spans at `reconstructed`, attributed by the message it sent. A `live`
environment (`side_effects: live`) is refused without `agentdiag run --live`, and
`run.json` records `adapter.live_acknowledged` when the flag is given. The reference
Manifest above is `tests/fixtures/manifests/http-echo.yaml`; the tests drive it against a
fake endpoint on `127.0.0.1`.

## Write and check Scenarios

A Suite is YAML (or JSON) in the one Scenario schema: literal Turns and `simulate` Turns,
typed Eval declarations with a short form (`- expect_tools: [cancel_order]`), Suite-level
Evals every Scenario inherits, named Fixtures, `focus`, `continues`, `ground_truth` and
`not_run`. `.claude/skills/agentdiag-generate/scenario-reference.md`, which `agentdiag init --skills` installs in any Workspace,
has every key and every Eval with its real parameters, generated from the code; the JSON
Schema editors read is
[`schemas/suite.schema.json`](../schemas/suite.schema.json).

The mechanical Evals — `expect_tools`, `expect_tools_order`, `expect_tools_any`,
`forbid_tools`, `tool_count_max`, `expect_tool_args`, `must_say_any`, `must_not_say`,
`forbidden_phrases`, `tool_latency`, `response_latency`, `first_token_latency` — cost no
tokens: each decides from the Trace alone, cites the Spans it read, and is `unverifiable`
rather than `pass` when the arguments or the Fidelity it needs are missing. Their
parameters are typed, so `validate` refuses a malformed declaration before a Run starts. The example Suite in
[`examples/toy/`](../examples/toy) exercises every one of them, including a Scenario that fails
and one that is `unverifiable`, and replays all of them without credentials:

```bash
uv run agentdiag run --root /tmp/agentdiag-toy --replay tests/fixtures/recordings/toy-orders.jsonl \
  --scenario where-is-shipped-order --scenario status-question-is-not-a-cancel \
  --scenario lookup-with-unseen-arguments
```

```
where-is-shipped-order  trial 1
  expect_tools  pass
  expect_tools_any  pass
  forbid_tools  pass
  tool_count_max  pass
  expect_tool_args  pass
  must_say_any  pass
  must_not_say  pass
  forbidden_phrases  pass
  tool_latency  pass
  response_latency  pass
status-question-is-not-a-cancel  trial 1
  expect_tools  pass
  forbid_tools  fail
  must_not_say  fail
lookup-with-unseen-arguments  trial 1  excluded from pass^k
  expect_tools  pass
  expect_tool_args  unverifiable  evidence_missing
  first_token_latency  unverifiable  evidence_missing
where-is-shipped-order  trials 1  passed 1 of 1 decidable  pass^1 1.00
status-question-is-not-a-cancel  trials 1  passed 0 of 1 decidable  pass^1 0.00
lookup-with-unseen-arguments  trials 1  passed 0 of 0 decidable  pass^1 n/a
cancel-processing-order  suite orders  not run  not_selected
lookup-then-cancel  suite orders  not run  not_selected
greeting-calls-no-tool  suite orders  not run  not_selected
delivered-order-cannot-be-cancelled  suite orders  not run  not_selected
order-details-come-from-the-lookup  suite orders  not run  not_selected
status-lookup-asks-for-the-named-order  suite orders  not run  not_selected
cancel-looks-up-then-cancels  suite orders  not run  not_selected
authored-lists-decide-without-a-judge  suite orders  not run  not_selected
cancel-without-the-order-number  suite orders  not run  not_selected
cancel-with-a-promised-refund-date  suite guardrails  not run  not_selected
pass 12  fail 2  incomplete 0  unverifiable 2  invalid 0  pass rate 86% (12 of 14)  not run 10  sync not_checked (no_fingerprint)
pass^1 0.50  trials 1  excluded 1  sampling not configured
```

It exits 1: a `fail` outranks everything else. The docs' Eval table says what each Eval's
parameters and Verdicts mean.

The judged Evals — `prompt_adherence`, `goal`, `guardrails`, `data_grounding`, `data_query`,
`tool_choice` — ask the Judge one question each over the same prompt head: the Scenario's
goal, notes and ground truth, the Target's own prompt split into numbered rules, the
Target's calibration notes, and the Trace with a Span id on every line. `guardrails` writes
one Score per rule, so the summary says which rule broke; `data_query` and `tool_choice`
ask no Judge at all when the Scenario already lists the tools or arguments. The example
exercises each of them, `guardrails` from a second Suite whose Suite-level rules its one
Scenario inherits:

```bash
uv run agentdiag run --root /tmp/agentdiag-toy --replay tests/fixtures/recordings/toy-orders.jsonl \
  --scenario delivered-order-cannot-be-cancelled --scenario cancel-with-a-promised-refund-date \
  --scenario authored-lists-decide-without-a-judge
```

```
delivered-order-cannot-be-cancelled  trial 1
  goal  pass
authored-lists-decide-without-a-judge  trial 1
  expect_tools  pass
  forbid_tools  pass
  expect_tool_args  pass
  data_query  pass
  tool_choice  pass
cancel-with-a-promised-refund-date  trial 1
  guardrails[no-refund-timing]  fail
  guardrails[speaks-as-the-desk]  pass
delivered-order-cannot-be-cancelled  trials 1  passed 1 of 1 decidable  pass^1 1.00
authored-lists-decide-without-a-judge  trials 1  passed 1 of 1 decidable  pass^1 1.00
cancel-with-a-promised-refund-date  trials 1  passed 0 of 1 decidable  pass^1 0.00
cancel-processing-order  suite orders  not run  not_selected
where-is-shipped-order  suite orders  not run  not_selected
lookup-then-cancel  suite orders  not run  not_selected
status-question-is-not-a-cancel  suite orders  not run  not_selected
lookup-with-unseen-arguments  suite orders  not run  not_selected
greeting-calls-no-tool  suite orders  not run  not_selected
order-details-come-from-the-lookup  suite orders  not run  not_selected
status-lookup-asks-for-the-named-order  suite orders  not run  not_selected
cancel-looks-up-then-cancels  suite orders  not run  not_selected
cancel-without-the-order-number  suite orders  not run  not_selected
pass 7  fail 1  incomplete 0  unverifiable 0  invalid 0  pass rate 88% (7 of 8)  not run 10  sync not_checked (no_fingerprint)
pass^1 0.67  trials 1  excluded 0  sampling not configured
```

Every judged Score records a Judge Fingerprint over its prompt, the calibration notes, the
model and the effort, so an edit to `judge_notes.md` shows on every Score it touched. It
also covers the Judge's Backend and, for Claude Code, the CLI version, so a Run judged
through another path says so in `compare`. A
judged declaration may name its own `judge: {model, effort}`, and a Judge that resolves to
the Target's own model is flagged on every Score and warned of in the summary.
`.claude/skills/agentdiag-generate/scenario-reference.md` has every judged Eval and what it reads;
[`docs/judge-notes.md`](judge-notes.md) what belongs in the notes.

Check a Suite offline — no network, no credentials:

```bash
uv run agentdiag validate --root /tmp/agentdiag-demo          # every Suite the Manifest names
uv run agentdiag validate path/to/suite.yaml other.yaml       # or the files named
```

Each problem is one line, `error: <file>: <path>: <message>` or `warning: ...`, then one
summary line. Any error exits 3; warnings (an Eval name this agentdiag does not know) exit 0.
The Manifest is checked first: a pointer to a missing file is an error (a warning when it is
`local_only`), and a pointer that is absolute or resolves outside the Workspace root is an
error, so a committed Manifest never depends on one machine's layout.

A Suite in a legacy shape (an extended `scenarios.json` with `first_turn`, or a per-case CI
file) is refused by `validate`, naming the shape: `this is a legacy <shape> Suite shape;
rewrite it in the standard schema (docs/scenario-schema.md)`.

## Simulate the user

A Scenario can hand the rest of the conversation to a Simulated User: after its literal
opening Turns, a `simulate` Turn tells a model the customer's goal, persona, what it knows,
what it must not reveal and how it behaves, and agentdiag checks a stop criterion over the
Trace after every Turn until it holds, the Simulated User answers its stop token, or
`max_turns` is reached. The example Suite has one, `cancel-without-the-order-number`: the
customer opens without the order number and gives it only when asked. Replay it without
credentials:

```bash
uv run agentdiag run --root examples/toy --scenario cancel-without-the-order-number \
  --replay tests/fixtures/recordings/toy-orders.jsonl
```

```
cancel-without-the-order-number  trial 1
  expect_tools_order  pass
  goal  pass
cancel-without-the-order-number  trials 1  passed 1 of 1 decidable  pass^1 1.00
cancel-processing-order  suite orders  not run  not_selected
where-is-shipped-order  suite orders  not run  not_selected
lookup-then-cancel  suite orders  not run  not_selected
status-question-is-not-a-cancel  suite orders  not run  not_selected
lookup-with-unseen-arguments  suite orders  not run  not_selected
greeting-calls-no-tool  suite orders  not run  not_selected
delivered-order-cannot-be-cancelled  suite orders  not run  not_selected
order-details-come-from-the-lookup  suite orders  not run  not_selected
status-lookup-asks-for-the-named-order  suite orders  not run  not_selected
cancel-looks-up-then-cancels  suite orders  not run  not_selected
authored-lists-decide-without-a-judge  suite orders  not run  not_selected
cancel-with-a-promised-refund-date  suite guardrails  not run  not_selected
pass 2  fail 0  incomplete 0  unverifiable 0  invalid 0  pass rate 100% (2 of 2)  not run 12  sync not_checked (no_fingerprint)
pass^1 1.00  trials 1  excluded 0  sampling none declared
```

`show` on that Trial prints the simulated Turn labelled `simulated`, the Simulated User's own
Span before it with its tokens and cost, each `stop_when` check with whether it held, and
`Termination stop_when (tool_called cancel_order held after Turn 2)`; the Cost line splits
`target`, `simulated_user` and `judge`.

The Simulated User's calls are its own actor in the Trace and never count toward the
Target's latency or cost, and every `fail` of a simulated Trial is reviewed by a stronger
model that marks the Simulated User's own faults `invalid` rather than blaming the Target.
`run` takes `--simulated-user-model`, `--simulated-user-effort`,
`--simulated-user-temperature` and `--reviewer-model` (`rescore` takes `--reviewer-model`);
a Turn may take the environment's `turn_timeout` seconds, 600 by default.
`.claude/skills/agentdiag-generate/scenario-reference.md` has the `simulate` Turn's keys in full.

## Point it at a Workspace of Targets

One Workspace holds as many Targets as you keep, each in its own Target directory under
`.agentdiag/targets/<slug>/` with its own Manifest, Suites, Calibration Notes and Runs.
`init --target <slug>` creates the Workspace with that Target, or adds the Target to the
Workspace that is already there. `--adapter` says what drives it: `toy` is the shipped toy
Target under that slug, and `python:<module:attr>` a factory of your own; with no
`--adapter` its Adapter is of kind `pending`, nothing drives it yet ([Describe a Target before it is
connected](#describe-a-target-before-it-is-connected)):

```bash
uv run agentdiag init --root /tmp/agentdiag-shop --target order-desk --adapter toy
uv run agentdiag init --root /tmp/agentdiag-shop --target help-desk --adapter python:agentdiag.examples.helpdesk:make_helpdesk --tools agentdiag.examples.helpdesk:make_tools
```

The Registry lists the Targets, read from their Manifests every time and never written
down:

```bash
uv run agentdiag registry --root /tmp/agentdiag-shop
```

```
target      name            family     channel  environments  connector  suites  sync
help-desk   helpdesk        -          -        local         -          sample  not_checked
order-desk  toy-order-desk  northwind  chat     local         inprocess  sample  not_checked
```

`--json` prints the same entries as JSON (`schemas/registry.schema.json`) and `--target
<slug>` shows one. `target show` prints one Target in full: where it lives, its
Calibration Notes, its Maintainer notes (`not written yet` while they are still the
starter's headings and placeholders, then their length in words), its Fingerprint and Sync
state, open Sync breaks and open Change records (`none` until a Sync or a fix records one),
and its Manifest as loaded. The toy's scaffold
names its in-process Connector; a Target `init` has only been pointed at gets the
`connector` block as a comment to fill in, never a guess:

```bash
uv run agentdiag target show help-desk --root /tmp/agentdiag-shop
```

```
Target help-desk: helpdesk
directory         .agentdiag/targets/help-desk
family            -
channel           -
environments      local
connector         -
suites            suites/sample.yaml
notes             judge_notes.md, 0 words, fingerprint e3b0c442
maintainer notes  maintainer_notes.md, not written yet
fingerprint       none
sync              not_checked
sync breaks       none
change records    none
pushes            none

Manifest as loaded:
  schema_version: 1
  target:
    name: helpdesk
    description: Written by `agentdiag init`; say here what this Target does.
  adapter:
    kind: inprocess
    side_effects: none
    environments:
      default: local
      local:
        factory: agentdiag.examples.helpdesk:make_helpdesk
        tools: agentdiag.examples.helpdesk:make_tools
        model: claude-sonnet-5
  prompts:
    system: observed
  tools: {}
  data_sources: {}
  suites:
  - path: suites/sample.yaml
    status: runnable
  records: changes
  judge_notes: judge_notes.md
  maintainer_notes: maintainer_notes.md
  suppressions: []
```

With more than one Target, `run` and `validate` need to know which: `--target <slug>`
names it, and without it the command exits 3 naming the slugs there are. `show`,
`export`, `compare` and `rescore` given a Run id look under every Target's `runs/`, and
a rescore is written beside its source; `list` lists every Target's Runs, and `list
--target <slug>` one Target's:

```bash
uv run agentdiag run --root /tmp/agentdiag-shop --target help-desk --dry-run
```

`examples/workspace/` is this Workspace, committed: the order desk and the second toy
Target, a help desk whose prompt and tools live on a (module-level) platform, two channels of
the one `northwind` Family. A root written by an older `init`, with its Manifest directly
under `.agentdiag/`, is refused by every command with the move that fixes it: its files go
into `.agentdiag/targets/default/`.

## Describe a Target before it is connected

A Target you cannot drive yet (its endpoint is not reachable from here, or nobody has written
its Adapter) can still be described: `init --target <slug>` with no `--adapter` writes who it
is and nothing else. `--name` (the slug when absent), `--description`, `--family` and
`--channel` say who it is; the Adapter is `kind: pending`, a core kind that validates and
drives nothing; the prompt pointers and the `connector` block are comments; and the sample
Suite is a draft. The same four flags apply to `--adapter toy` and `--adapter python:…`.

```bash
uv run agentdiag init --root /tmp/agentdiag-describe --target desk --name "Returns desk" --description "Takes a customer's return from the first message to a refund." --family northwind --channel voice
```

```
Wrote the scaffold into /tmp/agentdiag-describe:
  .agentdiag/targets/desk/manifest.yaml
  .agentdiag/targets/desk/suites/sample.yaml
  .agentdiag/targets/desk/judge_notes.md
  .agentdiag/targets/desk/maintainer_notes.md
  .agentdiag/targets/desk/redaction.yaml
  AGENTS.md
  CLAUDE.md
  GEMINI.md
  .agentdiag/CONTEXT.md
  .gitignore

Target desk (Returns desk), family northwind, channel voice; no Adapter yet (adapter.kind: pending).

Next:
  settle the 4 REVIEW lines in .agentdiag/targets/desk/manifest.yaml, then
  agentdiag validate
  agentdiag run --dry-run

A judged Eval needs credentials: a Claude Code login (claude auth login) or export ANTHROPIC_API_KEY=….
```

Each hole sits under a `# REVIEW:` line directly above it, in the shape `discover` writes,
saying what to fill. A description or a Family given on the command line has none:

```bash
grep -A1 "REVIEW:" /tmp/agentdiag-describe/.agentdiag/targets/desk/manifest.yaml
```

```
  # REVIEW: nothing drives this Target yet; replace pending with inprocess (a Python factory in this process), http (a chat endpoint with a Dialect), or a plugin's kind, and give the environment block that kind reads
  kind: pending
--
    # REVIEW: one block per environment the Target runs in, protected: true on every one that reaches real users
    dev: {}
--
# REVIEW: name each prompt as a path under this Target directory (prompts/system.md) once the file is here, or observed when only the running Target shows it
# prompts:
--
# REVIEW: no Connector yet: nothing reads or pushes the deployed set; a platform's Connector is a plugin kind, inprocess reads a Python module
# connector:
```

`validate` passes it, exit 0, with three warnings and no error: the pending Adapter, the
REVIEW lines left, and a Suite list with nothing runnable (the sample Suite is a draft):

```bash
uv run agentdiag validate --root /tmp/agentdiag-describe
```

```
warning: /tmp/agentdiag-describe/.agentdiag/targets/desk/manifest.yaml: adapter.kind: pending: nothing drives this Target yet; set the Adapter kind and its environment block (the REVIEW lines in manifest.yaml name what to fill)
warning: /tmp/agentdiag-describe/.agentdiag/targets/desk/manifest.yaml: 4 lines marked REVIEW; settle each (accept or rewrite it) before a Run
warning: /tmp/agentdiag-describe/.agentdiag/targets/desk/manifest.yaml: suites: no runnable Suite: suites/sample.yaml is draft; settle its Scenarios and drop status: draft from its entry to run them
validated the Manifest and 1 Suite: 0 errors, 3 warnings
```

Every command that would converse with the Target refuses it by name, exit 3, and builds no
Adapter: `run` and `run --dry-run` with the two lines `validate` warned with, `sync` with the
pending line after `error:` (unless a Connector reads the deployed set, which `sync` then
reads):

```bash
uv run agentdiag run --root /tmp/agentdiag-describe --dry-run
```

```
adapter.kind: pending: nothing drives this Target yet; set the Adapter kind and its environment block (the REVIEW lines in manifest.yaml name what to fill)
suites: no runnable Suite: suites/sample.yaml is draft; settle its Scenarios and drop status: draft from its entry to run them
```

Settling the REVIEW lines is connecting it: an `inprocess` factory or an `http` endpoint
([Drive a Target over HTTP](#drive-a-target-over-http)) in the Adapter block, the prompt
files under the Target directory, a Connector when a platform holds the deployed set, and
the sample Suite rewritten and its `status: draft` dropped. A Workspace whose Targets were
all written as the toy by an older `init` is repaired the same way, Target by Target, with
`init --target <slug> --force` and the name flags; `--force` keeps `judge_notes.md`,
`maintainer_notes.md` and `redaction.yaml`.

## Operate it with a coding agent

A coding agent runs in a Harness, and each Harness reads its project instructions from its
own file at the root before the first command. `init` writes one page for all three, the
**Orientation page** `AGENTS.md`, and gives the other two Harnesses a one-line import of it
in their own spelling:

| Harness | Reads | What `init` writes there |
|---|---|---|
| Codex | `AGENTS.md` | the Orientation page |
| Claude Code | `CLAUDE.md` | `@AGENTS.md` |
| Gemini CLI | `GEMINI.md` | `@./AGENTS.md` |

So `init` writes three files at the root, and under `.agentdiag/` the vocabulary copy
`CONTEXT.md`, taken from the installed package, so a clone with no network and no agentdiag
checkout still has the words the page and the skills use; it lives under `.agentdiag/`
because the root may be a repository with a `CONTEXT.md` of its own. For the Workspace of the walkthrough above:

```bash
cat /tmp/agentdiag-shop/CLAUDE.md /tmp/agentdiag-shop/GEMINI.md
```

```
@AGENTS.md
@./AGENTS.md
```

The page says what the repository is, what to read before the first command (itself, the
Target's Maintainer notes, one skill), which skill a request routes to, the Targets, the
rules and the commands. It names no platform: only the Targets table, between the
`<!-- agentdiag:targets -->` markers, differs from one Workspace to the next.

```bash
cat /tmp/agentdiag-shop/AGENTS.md
```

```markdown
# Orientation: an agentdiag Workspace

This repository is an agentdiag **Workspace**: one `.agentdiag/targets/<slug>/` directory per
**Target**, an agentic system under test, holding its Manifest (pointers to its prompts, tools
and environments, never copies), its Suites of Scenarios, its Calibration Notes for the Judge,
its Maintainer notes for you, and its Change records. `agentdiag` drives a Target through
Scenarios, records each Trial as a Trace, judges the Trace with Evals, keeps the Target's local
files and its deployed set in Sync, and records every fix as a Change record that closes only
through `compare`.

**Read before the first command of a session**: this page; `.agentdiag/targets/<slug>/maintainer_notes.md`
for the Target the request names; the one `/agentdiag-*` skill the request routes to. Reference,
on demand: `agentdiag <command> --help` for every flag, and `.agentdiag/CONTEXT.md` for every word
used here (Target, Manifest, Suite, Scenario, Trace, Span, Eval, Verdict, Sync, Change record).
That read set is budgeted at 10k tokens.

## Routing

Match the request to one skill and invoke it by name. Each skill marks its steps **code** (run
it), **judgement** (decide it and say why) or **human** (hand it to the person and wait), and
ends on a "Done when" list.

| The request | Skill |
|---|---|
| A Target this Workspace has never described, whose prompts and tools live in a repository or on a platform | `/agentdiag-discover` |
| A Target whose prompts, tests, judging rules and notes already live in another maintenance repository, with no Connector yet | `/agentdiag-migrate` |
| "Write tests", "build a Suite", "cover rule N": Scenarios from a Target's prompt rules and tools | `/agentdiag-generate` |
| A failure of the Target: a failing Trial, a Diagnosis, a complaint about a real conversation, taken to a verified or refuted Change record | `/agentdiag-fix-cycle` |
| A Score that is wrong when the Target was right: the Judge or an Eval misjudged, and the judging configuration must change | `/agentdiag-correction` |

A failure of the Target is a fix cycle; a failure of the judgement is a correction. Read a
Target through its commands before its files: `agentdiag target show <slug>` prints the
Manifest as loaded, the notes, the Fingerprint and the open records; `agentdiag show <run>
<scenario>` prints a Trial as a story; `agentdiag list` the Runs.

## Targets

<!-- agentdiag:targets -->
<!-- generated from the Manifests by agentdiag registry --write; a hand edit here is replaced -->
| Target | Name | Family / channel | Default env | Protected | Suites | Connector | Maintainer notes |
|---|---|---|---|---|---|---|---|
| `help-desk` | helpdesk | - | local | - | sample | - | maintainer_notes.md |
| `order-desk` | toy-order-desk | northwind / chat | local | - | sample | inprocess | maintainer_notes.md |
<!-- /agentdiag:targets -->

Generated from the Manifests by `agentdiag registry --write`, and `agentdiag validate` warns
when it is stale. Every command takes `--target <slug>` when the Workspace holds more than
one Target, and `--root <path>` from outside it.

## Rules

1. **Diff before push.** `agentdiag push --env <env>` previews; `--push` writes only after
   the person has read that preview's diff.
2. **A protected environment confirms by name.** Its name is typed by a person at the
   terminal, or in the UI's confirm step; no flag stands in for it, so a session without a
   terminal hands the person the exact command and waits.
3. **Verdicts cite Spans.** A Score names the Spans it judged, and a cause written into a
   Change record quotes a Span `agentdiag show` printed, never a summary of the conversation.
4. **No credential value in any file.** A Manifest names the environment variable that holds
   a credential; the value lives in `~/.agentdiag/env` or the shell, and in no file under this
   repository, no Trace and no record.
5. **Runs are output.** `runs/`, Restore points, the Index and each Target's `redaction.yaml`
   are gitignored; Manifests, Suites, notes, Change records, Push records and
   `fingerprint.json` are committed, a fix with the record and Fingerprint it belongs to.

## Commands

| To | Run |
|---|---|
| List the Targets; print one in full | `agentdiag registry`; `agentdiag target show <slug>` |
| Check a Target offline | `agentdiag validate --target <slug>` (every Target: `--all`) |
| See what a Run would do, then run it | `agentdiag run --target <slug> --dry-run`; drop `--dry-run`, add `--suite <name>` or `--scenario <id>` |
| Read a Trial | `agentdiag show <run> <scenario>` |
| Compare two Runs | `agentdiag compare <baseline> <run>` |
| Check or record the deployed set | `agentdiag sync --target <slug>`, `--check` to check only |
| Preview or make a push | `agentdiag push --target <slug> --env <env>`, then `--push --change <id>` |
| Carry a fix | `agentdiag change open`, `expect`, `propose`, `close` |
| Refresh this page's table | `agentdiag registry --write` |

Skills this agentdiag ships, installed by `agentdiag init --skills`: /agentdiag-correction, /agentdiag-discover, /agentdiag-fix-cycle, /agentdiag-generate.
```

agentdiag never overwrites a file you own. A root `AGENTS.md` you wrote, without the
markers, keeps every byte (its line endings too) and gains the marked table at its end, with
a notice on stderr; once the markers are there, only the text between them is regenerated.
`CLAUDE.md` and `GEMINI.md` are written only when absent, and one that does not import the
page is named in a notice with the line to add. The vocabulary copy is agentdiag's and is
always rewritten. `init --skills` writes whichever of the three root files and the
vocabulary copy is absent, because skills without the page that routes to them are half an
install.

The page's read set names one more file per Target: its **Maintainer notes**,
`maintainer_notes.md` beside the Manifest, which the Manifest's `maintainer_notes` key points
at and the table's last column lists. They hold what whoever maintains the Target must know
before touching it, under five headings: `## What the Target does`, `## Who it serves`,
`## Environments` (the default one, each protected one, and what each reaches), `## Known
traps` and `## Where evidence lives`. Every packaged skill reads them in full before its
first step, and writes them as it learns these when they are still the starter; the Judge
never reads them, so nothing a maintainer writes there moves a Score. Rules for judging the
Target belong in `judge_notes.md` instead. `init` writes the starter for every Target and
`discover` for one it creates; `--force` keeps them; `validate` errors on a key naming no
file; `target show` prints their length on its `maintainer notes` row.

The table is a rendering of the Registry. A command that writes under `.agentdiag/targets/`
(`init`, `generate` adding a Suite, `discover` creating a Target) refreshes it when the page
holds the markers, and never writes an absent page; a Manifest edited by hand leaves it stale
until `registry --write` regenerates it. Here the help desk joins the `northwind` Family, and
`validate` says the table no longer matches; the warning does not fail the command:

```bash
perl -pi -e 's/^# family: your-persona$/family: northwind/; s/^# channel: chat$/channel: chat/' /tmp/agentdiag-shop/.agentdiag/targets/help-desk/manifest.yaml
uv run agentdiag validate --root /tmp/agentdiag-shop --target help-desk
```

```
warning: AGENTS.md: the Targets table is stale; agentdiag registry --write regenerates it
validated the Manifest and 1 Suite: 0 errors, 1 warning
```

```bash
uv run agentdiag registry --write --root /tmp/agentdiag-shop
```

```
wrote AGENTS.md
unchanged CLAUDE.md
unchanged GEMINI.md
unchanged .agentdiag/CONTEXT.md
```

Only the help desk's row changed, and `validate` is quiet again. A stale table is the one
orientation warning every Workspace gets. Once the skills are installed (in either layout)
`validate` also warns of a page without the markers, of `CLAUDE.md` or `GEMINI.md` without
its import line, and of an absent page, import file or vocabulary copy; until then a
Workspace in a corner of a repository with instruction files of its own is not told to
change them.

## Keep the Target in Sync

A Fingerprint is the hash of everything the Manifest points at, section by section: each
prompt split on its markdown headings, each tool schema, each data source's identity, the
resolved model and the provider, and, where a Connector reads them, each Flow and the tier.
`agentdiag sync` builds one and writes it to `fingerprint.json` beside the Manifest, where it
is committed; `sync --check` compares without writing; and every `run` checks it before the
first Trial. A section the Target's files hold is read from the files (`covered local`). The
deployed side is read by the Manifest's Connector when it names one (`covered connector`):
the Connector manages the Target's live side, reading its deployed set without a Turn, and
the toy's in-process Connector reads the prompt, tools and model its running module holds
(`connector.environments.local.deployed`). With no Connector, an `observed` section is read
by the Adapter's probe, which builds the Target, sends it one message and answers the
request itself, so no model is called and nothing is written (`covered adapter`). A section
nothing can observe is listed `not_covered` with the reason, never left out. With the
shipped example (`cp -r examples/toy /tmp/agentdiag-sync`):

```bash
uv run agentdiag sync --root /tmp/agentdiag-sync
```

```
no previous Fingerprint (environment local)
section            direction       change  covered
model              deployed_ahead  added   connector
prompt.system      deployed_ahead  added   connector
provider           deployed_ahead  added   connector
tool.cancel_order  deployed_ahead  added   connector
tool.lookup_order  deployed_ahead  added   connector
5 sections deployed_ahead
wrote /tmp/agentdiag-sync/.agentdiag/targets/toy-order-desk/fingerprint.json (fingerprint abbe0370, 5 sections)
```

```bash
uv run agentdiag sync --root /tmp/agentdiag-sync --check
```

```
Sync held against fingerprint abbe0370 (built 2026-09-28T11:56:30Z, local)
section            direction  change  covered
model              identical  -       connector
prompt.system      identical  -       connector
provider           identical  -       connector
tool.cancel_order  identical  -       connector
tool.lookup_order  identical  -       connector
5 sections identical
```

<!-- edit: prompt.py rule 4 -->
Now change rule 4 of the toy's prompt (`src/agentdiag/examples/toy/prompt.py`) from "at most
three sentences" to "at most two sentences". The deployed Target moved and the record did
not, so the section is `deployed_ahead` and `sync --check` exits 2, the Verdict-shaped "the
Target moved" signal; with no Fingerprint to compare it exits 3 and says what to run. A
broken comparison is recorded as a Sync break, a file under the Target directory's `sync-breaks/` that
is committed and never edited:

```bash
uv run agentdiag sync --root /tmp/agentdiag-sync --check  # exits 2
```

```
Sync broken against fingerprint abbe0370 (built 2026-09-28T11:56:30Z, local)
section            direction       change   covered
model              identical       -        connector
prompt.system      deployed_ahead  changed  connector
provider           identical       -        connector
tool.cancel_order  identical       -        connector
tool.lookup_order  identical       -        connector
4 sections identical, 1 deployed_ahead
recorded Sync break /tmp/agentdiag-sync/.agentdiag/targets/toy-order-desk/sync-breaks/20260928T115630Z.json
```

A break stays open while the Fingerprint it was found against is the one in force: `sync`
(which records a break of what it found, unless one is already open) and a re-syncing `run`
rebuild the Fingerprint and so close it. A local edit the deployed side has not caught up
with (`local_ahead`) rebuilds to the same Fingerprint and stays open, because only a push
would settle it. A second `sync --check` that finds the same thing records nothing new and
names the open break on stderr. The Registry counts the open ones, and `target show` lists
them by file and section:

```bash
uv run agentdiag registry --root /tmp/agentdiag-sync
```

```
target          name            family     channel  environments  connector  suites              sync
toy-order-desk  toy-order-desk  northwind  chat     local         inprocess  orders, guardrails  broken (1 open Sync break)
```

A `run` on a broken Sync re-syncs by default: it rebuilds the Fingerprint from what it
read, writes it with `resynced_from` naming the one it replaced, records both in
`run.json`, and says so up front; it never records a Sync break, which is `sync`'s. The
summary's Sync reads `sync broken, re-synced from abbe0370: prompt.system deployed_ahead`,
and `show` prints `Re-synced from <fingerprint>: prompt.system deployed_ahead (changed)`
under its header. A prior Run keeps the Fingerprint it ran under in its own `run.json`, and
`compare` treats the move as an undeclared difference rather than a regression.
`--no-resync` runs against the old Fingerprint, labelled `broken`; `--strict` refuses with
exit 3 and the table, before any Run directory exists:

```bash
uv run agentdiag run --root /tmp/agentdiag-sync --strict --dry-run  # exits 3
```

```
Sync is broken and --strict refuses to run against it: `agentdiag sync` re-records the Fingerprint; or drop --strict to re-sync
Sync broken against fingerprint abbe0370 (built 2026-09-28T11:56:30Z, local)
section            direction       change   covered
model              identical       -        connector
prompt.system      deployed_ahead  changed  connector
provider           identical       -        connector
tool.cancel_order  identical       -        connector
tool.lookup_order  identical       -        connector
4 sections identical, 1 deployed_ahead
```

A Connector that cannot read — a missing credential, a platform that refused — makes `sync`
exit 3 naming why, because a Sync that guessed would be worse than none; a `run` instead
falls back to the Adapter's probe, warns, and records the failure in `run.json`, so a
missing credential never blocks a local Run. Credentials are named in the Manifest by the
environment variable that holds them (`credentials: {token: ORDERS_DEV_TOKEN}`) and read from
the process environment only, never from another environment's variable. A platform's
Connector is a plugin package found by `connector.kind`; a kind nothing installed provides
is refused naming the package that would. `run`, `run --dry-run` and `rescore` refuse an
unknown `adapter.kind` (and `connector.kind`, when the Run reads through the Connector) with
`validate`'s message, exit 3; an unknown Connector kind no longer falls back to the Adapter's
probe.

`sync --json` prints the same comparison as JSON, and `--env <name>` reads another of the
Manifest's environments, including one only the Connector names. A Target that has never
run `sync` runs as before, its Sync `not_checked (no_fingerprint)`.

## Discover an unknown Target

`agentdiag discover` drafts the Manifest of a Target agentdiag has never seen, so the first
Manifest is one to argue with rather than a blank one. `--scan <dir>` reads a repository
without running any of it (Python through `ast`): prompt files and long prompt string
literals, tool schemas in JSON files or module-level lists, the model SDK and a `model=`,
a `make_*`/`build_*`/`create_*` factory and the callable returning the tools, the
deployed-set convention the in-process Connector reads, HTTP routes, and data-source URLs
and `*_URL`/`*_DSN`/`*_DB` variables. The draft goes to `manifest.draft.yaml` in the Target
directory (or an `--out` inside the Workspace), never over `manifest.yaml`, and every
guess sits under a `# REVIEW:` line saying why it was guessed. A new `--target` slug
creates the Target directory in an existing Workspace, with the Maintainer notes starter
`maintainer_notes.md` and the local `redaction.yaml` starter `init` would have written beside
the draft:

```bash
uv run agentdiag init --root /tmp/agentdiag-discover
uv run agentdiag discover --root /tmp/agentdiag-discover --target toy-order-desk --scan src/agentdiag/examples/toy
```

```
Scanned src/agentdiag/examples/toy: 1 prompt, 2 tools, 0 data sources.
Wrote /tmp/agentdiag-discover/.agentdiag/targets/toy-order-desk/manifest.draft.yaml, 15 lines marked REVIEW.
  wrote /tmp/agentdiag-discover/.agentdiag/targets/toy-order-desk/maintainer_notes.md (the Maintainer notes starter)
  wrote /tmp/agentdiag-discover/.agentdiag/targets/toy-order-desk/redaction.yaml (local, gitignored)
  wrote /tmp/agentdiag-discover/AGENTS.md (the Targets table)

Next: accept or rewrite every REVIEW line, then
  agentdiag validate --root /tmp/agentdiag-discover --target toy-order-desk --manifest /tmp/agentdiag-discover/.agentdiag/targets/toy-order-desk/manifest.draft.yaml
  mv /tmp/agentdiag-discover/.agentdiag/targets/toy-order-desk/manifest.draft.yaml /tmp/agentdiag-discover/.agentdiag/targets/toy-order-desk/manifest.yaml
  agentdiag sync --root /tmp/agentdiag-discover --target toy-order-desk
```

On the toy Target the draft is the checked-in example's Manifest, REVIEW lines apart: the
name is the slug and the description the package docstring's first line. The Adapter
section reads:

```yaml
adapter:
  # REVIEW: agentdiag.examples.toy:make_target is a Python factory, which the in-process Adapter drives
  kind: inprocess
  # REVIEW: a Run is taken to change nothing outside this process; `sandboxed` or `live` if a tool writes to a real system
  side_effects: none
  environments:
    # REVIEW: `local` is the environment a developer runs on their own machine; name the one a Run opens by default
    default: local
    local:
      # REVIEW: agentdiag.examples.toy:make_target is a module-level make_* returning a callable; confirm it takes (client, tools, **options)
      factory: agentdiag.examples.toy:make_target
      # REVIEW: agentdiag.examples.toy:make_tools returns a mapping of callables; confirm they are the tools
      tools: agentdiag.examples.toy:make_tools
      # REVIEW: "claude-sonnet-5" is the model= default of agentdiag.examples.toy:make_target
      model: claude-sonnet-5
```

A draft always validates as a Manifest; `validate --manifest` checks it where it is, its
paths relative to the Target directory as the Manifest's are, and warns with the count of
REVIEW lines still to settle:

```bash
uv run agentdiag validate --root /tmp/agentdiag-discover --target toy-order-desk --manifest /tmp/agentdiag-discover/.agentdiag/targets/toy-order-desk/manifest.draft.yaml
```

```
warning: /tmp/agentdiag-discover/.agentdiag/targets/toy-order-desk/manifest.draft.yaml: 15 lines marked REVIEW; settle each (accept or rewrite it) before a Run
validated the Manifest and 0 Suites: 0 errors, 1 warning
```

Over an existing `manifest.yaml` the draft is that file line for line, its comments
included: a rescan only inserts what is absent (each new line under its REVIEW), and where
the Connector's read would change a value the author wrote, a REVIEW comment above that line
proposes the new value and the line stays as written. A file `discover` would change is written only when git
holds what it says now (tracked and committed), unless `--force`; outside git nothing
vouches for it, so it is kept.

A Target whose prompts and tools are platform records rather than source is read through
the Connector its `manifest.yaml` names: `--from-connector --env <name>` reads the deployed
set, saves each prompt as `prompts/<name>.md` and each tool schema as `tools/<name>.json`
under the Target directory, and proposes pointers to them and the model from the read: the
saved copies become the local side of the Sync, the Connector's read the deployed side. A Connector block the scan only drafted is built once it has been
accepted into `manifest.yaml`, never before, since building it runs the scanned code. With
the toy's in-process Connector:

```bash
uv run agentdiag discover --root /tmp/agentdiag-discover --target default --from-connector --env local
```

```
Read the deployed set of local through the Connector:
  wrote /tmp/agentdiag-discover/.agentdiag/targets/default/prompts/system.md
  wrote /tmp/agentdiag-discover/.agentdiag/targets/default/tools/lookup_order.json
  wrote /tmp/agentdiag-discover/.agentdiag/targets/default/tools/cancel_order.json
Wrote /tmp/agentdiag-discover/.agentdiag/targets/default/manifest.draft.yaml, 4 lines marked REVIEW.

Next: accept or rewrite every REVIEW line, then
  agentdiag validate --root /tmp/agentdiag-discover --target default --manifest /tmp/agentdiag-discover/.agentdiag/targets/default/manifest.draft.yaml
  mv /tmp/agentdiag-discover/.agentdiag/targets/default/manifest.draft.yaml /tmp/agentdiag-discover/.agentdiag/targets/default/manifest.yaml
  agentdiag sync --root /tmp/agentdiag-discover --target default
```

Both flags at once suit a Target whose repository holds the Adapter and whose platform
holds the prompt. The review itself is a coding agent's job, and agentdiag ships the
procedure as a skill: `init --skills` installs it under the Workspace root's
`.claude/skills/`, where Claude Code runs it as `/agentdiag-discover`. It marks each step code, judgement or human (a
credential is a variable the person exports, never a value in the conversation), and is
done when `validate` is clean, no REVIEW line is left, `sync --check` holds and `registry`
lists the Target. An installed skill someone edited is kept unless `--force`:

```bash
uv run agentdiag init --root /tmp/agentdiag-discover --skills
```

```
Installed the agentdiag skills under /tmp/agentdiag-discover/.claude/skills:
  wrote .claude/skills/agentdiag-correction/SKILL.md
  wrote .claude/skills/agentdiag-discover/SKILL.md
  wrote .claude/skills/agentdiag-fix-cycle/SKILL.md
  wrote .claude/skills/agentdiag-generate/SKILL.md
  wrote .claude/skills/agentdiag-generate/scenario-reference.md
Orientation page: unchanged AGENTS.md, CLAUDE.md, GEMINI.md, .agentdiag/CONTEXT.md

Invoke one in Claude Code by name: /agentdiag-correction, /agentdiag-discover, /agentdiag-fix-cycle, /agentdiag-generate
```

## Generate a Suite

`agentdiag generate` writes a Suite from drafts a coding agent wrote: at least one Scenario
per prompt rule and per tool, each stating what the rule demands rather than what the
Target does today. agentdiag ships the procedure as the skill `/agentdiag-generate`
(installed by `init --skills`): it reads the section ids `target show` lists once `sync`
has run, picks the Evals for each kind of rule (a "never say" rule is `must_not_say`, a
"look up before" rule `expect_tools_order`, a "do this only when" rule `forbid_tools` in one
Scenario and `expect_tools` in the other, a judgement `prompt_adherence` or `goal`), and
writes the drafts file. Each draft is a Scenario in the standard schema without an `id` and
with a `provenance`: `prompt:<name>#<section>` (or `prompt:<name>` for a prompt with no
headings), `tool:<name>`, and, for the sources a later release adds,
`trace:<run id>/<scenario>/<n>` and `change:<change record id>`. The drafts for the toy's
five rules and two tools are `tests/fixtures/drafts/toy-order-desk.yaml`; `--check` prints
the id each draft gets and writes nothing:

```bash
uv run agentdiag init --root /tmp/agentdiag-generate
uv run agentdiag generate --root /tmp/agentdiag-generate --from tests/fixtures/drafts/toy-order-desk.yaml --check
```

```
Would write /tmp/agentdiag-generate/.agentdiag/targets/default/suites/generated.yaml: 8 Scenarios, 8 new, 0 kept, 0 re-keyed, 0 retired.
  new      system-a-status-question-is-answered-from-the-lookup          from prompt:system
  new      system-an-explicit-cancellation-of-a-processing-order-is      from prompt:system
  new      system-a-customer-asking-where-an-order-is-has-not-asked-to   from prompt:system
  new      system-no-refund-or-delivery-window-the-tools-did-not-return  from prompt:system
  new      system-a-reply-stays-within-three-sentences                   from prompt:system
  new      system-the-desk-never-says-it-is-an-ai                        from prompt:system
  new      lookup-order-the-lookup-asks-for-the-order-the-customer       from tool:lookup_order
  new      cancel-order-a-customer-who-forgot-the-order-number-still     from tool:cancel_order
Would add suites/generated.yaml to the Manifest's suites.
Nothing written (--check).
```

An id is the anchor, the section's heading slug or the prompt's or tool's name, then the
title, at most 60 characters, with `-2` on a clash. A draft with no Eval, a provenance in
none of the four forms, a prompt or tool the Manifest does not point at, or anything
`validate` rejects is exit 3 with `validate`'s lines, located in the drafts file, and
nothing is written. Without `--check` the Suite goes to `suites/<suite>.yaml` under the
Target directory (`generated` unless the drafts or `--suite` name another) and the
Manifest's `suites` gains it, one line inserted, every comment kept:

```bash
uv run agentdiag generate --root /tmp/agentdiag-generate --from tests/fixtures/drafts/toy-order-desk.yaml
```

```
Wrote /tmp/agentdiag-generate/.agentdiag/targets/default/suites/generated.yaml: 8 Scenarios, 8 new, 0 kept, 0 re-keyed, 0 retired.
  new      system-a-status-question-is-answered-from-the-lookup          from prompt:system
  new      system-an-explicit-cancellation-of-a-processing-order-is      from prompt:system
  new      system-a-customer-asking-where-an-order-is-has-not-asked-to   from prompt:system
  new      system-no-refund-or-delivery-window-the-tools-did-not-return  from prompt:system
  new      system-a-reply-stays-within-three-sentences                   from prompt:system
  new      system-the-desk-never-says-it-is-an-ai                        from prompt:system
  new      lookup-order-the-lookup-asks-for-the-order-the-customer       from tool:lookup_order
  new      cancel-order-a-customer-who-forgot-the-order-number-still     from tool:cancel_order
Added suites/generated.yaml to the Manifest's suites.
Refreshed the Targets table in /tmp/agentdiag-generate/AGENTS.md.

Next:
  agentdiag validate --root /tmp/agentdiag-generate
  agentdiag run --root /tmp/agentdiag-generate --suite generated --dry-run
```

Each Scenario carries its `provenance`, a `# from` line above it, and `tags` with every
tool its Evals name, so `--tag cancel_order` selects every Scenario about that tool:

```yaml
  # from prompt:system
  - id: system-a-status-question-is-answered-from-the-lookup
    title: A status question is answered from the lookup
    tags:
    - rule-1
    - lookup_order
    provenance: prompt:system
    notes: Rule 1, look up before you answer.
    turns:
    - Has my order NB-0917 shipped yet?
    evals:
    - expect_tools_order:
      - lookup_order
    - must_say_any:
      - shipped
    focus: expect_tools_order
```

```bash
uv run agentdiag run --root /tmp/agentdiag-generate --suite generated --dry-run
```

```
system-a-status-question-is-answered-from-the-lookup  kinds one_shot,tool  tags rule-1,lookup_order  suite generated
system-an-explicit-cancellation-of-a-processing-order-is  kinds one_shot,tool  tags rule-2,lookup_order,cancel_order  suite generated
system-a-customer-asking-where-an-order-is-has-not-asked-to  kinds one_shot,tool  tags rule-2,lookup_order,cancel_order  suite generated
system-no-refund-or-delivery-window-the-tools-did-not-return  kinds one_shot  tags rule-3  suite generated
system-a-reply-stays-within-three-sentences  kinds one_shot  tags rule-4  suite generated
system-the-desk-never-says-it-is-an-ai  kinds one_shot  tags rule-5  suite generated
lookup-order-the-lookup-asks-for-the-order-the-customer  kinds one_shot,tool  tags lookup_order  suite generated
cancel-order-a-customer-who-forgot-the-order-number-still  kinds simulated,tool  tags lookup_order,cancel_order  suite generated
cancel-processing-order  kinds one_shot  suite sample  not run  not_selected
evals 15 declarations, every parameter parsed
selection suite=generated
sync not_checked (no_fingerprint)
```

Generating again after the prompt changed keeps every id it can match: a draft with the
same provenance and title keeps its Scenario's id, and so does a reworded one whose
provenance no other draft shares (a heading section). A Scenario no draft matches stays in
the Suite, listed under `not_run` as `retired by generate on <date>: no draft`, because a
Run may hold Trials under its id; and a draft that names a different `id` for a Scenario a
Run holds Trials under is refused, naming the Runs.

## Import a conversation

A conversation your Target already had, in production or anywhere its platform logged it,
can be judged like a Run it was driven through. `import` reads the evidence, turns it into
one Trace and writes a Run of `source: imported` with one Trial under the Scenario
`imported-<conversation id>`. `--rows <file>` reads a JSON or JSONL file of proxy rows
(one model request and its response each, as an LLM proxy logs them) and needs no
Connector; `--chat <id>`, `--conversation <id>` and `--voice <id>` read the Target's
Connector's proxy rows, conversation record or voice conversation for that conversation.
With the committed help desk rows (`tests/fixtures/evidence/`, invented, no customer
content):

```bash
uv run agentdiag init --root /tmp/agentdiag-import
uv run agentdiag import --root /tmp/agentdiag-import --rows tests/fixtures/evidence/helpdesk-proxy-rows.json
```

```
Imported proxy hd-0001 as Run 20260928T103353Z-zs6y (Target default)
Scenario imported-hd-0001 · 1 Trial · 2 Turns · 8 Spans at reconstructed, every one marked imported
Sync not_checked (no_fingerprint)
Not observed: end_time_exact (6 Spans), time_to_first_token (4 Spans), start_time (2 Spans)
Judge it: agentdiag rescore 20260928T103353Z-zs6y --eval <name>
```

Proxy rows prove which model was asked what, which tools it asked for and what they
returned, but not when a response really ended, when its first token came, or when a tool
ran between two rows: every Span is `reconstructed`, marked `agentdiag.span.origin:
imported`, and carries what it lacks as `agentdiag.not_observed`, and the Trace's second
Event, `import/source`, names the store, the query and every fact not observed. A
conversation record is `observed` (a staff takeover's reply is Actor `operator`, in the
Turn it answered, which says so); a voice conversation too, every Turn saying it saw no
model call. `list` shows the Run's source, and `show` tells the Trial with what each Span
did not observe (`<run>` is the id `import` printed):

```bash
uv run agentdiag list --root /tmp/agentdiag-import
```

```
run                    target   source    created               fingerprint  selection             sync                          verdicts                                                 not run  traces  change  size
20260928T103353Z-zs6y  default  imported  2026-09-28T10:33:53Z  -            import proxy hd-0001  not_checked (no_fingerprint)  pass 0  fail 0  incomplete 0  unverifiable 0  invalid 0  0        -       -       140 KB
```

```bash
uv run agentdiag show --root /tmp/agentdiag-import <run> imported-hd-0001
```

```
Run 20260928T103353Z-zs6y · Scenario imported-hd-0001 · Trial 1
Target toy-order-desk · Adapter import, reconstructed, side effects none
Sync not_checked (no_fingerprint) · Termination completed · 72.20 s · in 3320 out 226
Cost target $0.008900
Imported from the proxy store: 4 rows, lacking end_time_exact (6), time_to_first_token (4), start_time (2)
provenance    evidence:proxy:hd-0001

Turn 1    2.40 s
  user      How do I change the billing address on my account?
  llm_call-1  chat claude-sonnet-5   900 ms (model 900 ms) · in 640 out 48 · $0.001760 · tool_use · not observed: end_time_exact, time_to_first_token
    request  1 messages, 1 tools  (full body in --json)
    tool_call-1  execute_tool search_articles   400 ms · not observed: start_time, end_time_exact
      → search_articles {"query": "change billing address"}
      ← [{"id": "KB-104", "title": "Change your billing address", "summary": "Settings > Billing >
        Address, then Save."}]
  llm_call-2  chat claude-sonnet-5   1.10 s (model 1.10 s) · in 790 out 72 · $0.002300 · end_turn · not observed: end_time_exact, time_to_first_token
    request  3 messages  (full body in --json)
    text  Open Settings, then Billing, then Address, and press Save (article KB-104, Change your
          billing address).
  target    Open Settings, then Billing, then Address, and press Save (article KB-104, Change your
            billing address).

Turn 2    2.20 s
  user      Thanks. Can I download my past invoices too?
  llm_call-3  chat claude-sonnet-5   850 ms (model 850 ms) · in 880 out 40 · $0.002160 · tool_use · not observed: end_time_exact, time_to_first_token
    request  3 messages  (full body in --json)
    tool_call-2  execute_tool search_articles   350 ms · not observed: start_time, end_time_exact
      → search_articles {"query": "download invoices"}
      ← [{"id": "KB-117", "title": "Download invoices", "summary": "Billing > Invoices, then
        Download beside each one."}]
  llm_call-4  chat claude-sonnet-5   1.00 s (model 1.00 s) · in 1010 out 66 · $0.002680 · end_turn · not observed: end_time_exact, time_to_first_token
    request  5 messages  (full body in --json)
    text  Yes: go to Billing, then Invoices, and press Download beside each one (article KB-117,
          Download invoices).
  target    Yes: go to Billing, then Invoices, and press Download beside each one (article KB-117,
            Download invoices).

Scores
  (this Scenario declared no Evals)
```

An imported Run declares no Evals, since no Suite names its Scenario. `rescore <run> --eval
<name>=<value>` (repeatable; the value binds the Eval's primary parameter, or a Metric's
threshold, as the Suite short form does) judges its Trial into a new Run, and no Eval claims
more than the evidence holds: `first_token_latency` over proxy rows is `unverifiable /
evidence_missing`, while `response_latency` holds the rows' latency and cites the `llm_call`
Spans whose ends were reconstructed. The exit code is 2, as for any Run with a Score that
could not be decided:

```bash
uv run agentdiag rescore --root /tmp/agentdiag-import <run> --eval 'expect_tools=[search_articles]' --eval 'response_latency={max_ms: 30000}' --eval 'first_token_latency={max_ms: 1000}'  # exits 2
```

```
imported-hd-0001  trial 1  excluded from pass^k
  expect_tools  pass
  response_latency  pass
  first_token_latency  unverifiable  evidence_missing
imported-hd-0001  trials 1  passed 0 of 0 decidable  pass^1 n/a
cancel-processing-order  suite sample  not run  not_selected  no Trial in the source Run 20260928T103353Z-zs6y
pass 2  fail 0  incomplete 0  unverifiable 1  invalid 0  pass rate 100% (2 of 2)  not run 1  sync not_checked (no_fingerprint)
pass^1 n/a  trials 1  excluded 1  sampling not configured
```

A judged Eval such as `rescore --eval prompt_adherence` needs the Judge; its worked
recording over this Trace is captured later, so this section shows no judged output yet.
`compare` refuses a `run` Run against an `imported` one, naming both, unless `--expect
source` declares it, and then labels every Score `undecided`.

## Record a fix as a Change record

A fix is recorded as a **Change record**: one file per change under the Target's
`changes/`, committed, with a YAML head (its trigger, the layer it touches, the change set,
each push, the effect expected, the verification) and the prose below it. It opens from a
Diagnosis (`--from <run>/<scenario>/<trial>`, read from that Trial's `judgement.jsonl`; the
Run is never written) or from a complaint (`--complaint <file>`), and customer identifiers
never reach the file: e-mail addresses, phone numbers and the names the Target's
`redaction.yaml` lists (`names: [...]`, beside the Manifest) are replaced before it is
written. That file is local: `init` writes it empty and gitignores it, so the names never
reach a commit or a clone, and `validate` warns where it is absent (or, in a repository,
where the `.gitignore` would let git commit it). The Manifest's `redaction:` key, when
present, points at the file elsewhere, relative to the Target directory like every pointer
(ADR-0015 §4); a pointer that is refused or names nothing, or a file that is not
`names: [...]`, is a `validate` error and refuses every `change` command and a push naming
a record. A Manifest that still lists names inline under the key is a `validate` error
naming the move, and its names are still redacted until it moves. With the example copied
and two committed Run fixtures under its `runs/` — `20260923T100200Z-prmt`, whose Target
talks about a refund to a customer asking after a shipped order, and
`20260923T100000Z-base`, where it does not:

```bash
cp -r examples/toy /tmp/agentdiag-change
mkdir -p /tmp/agentdiag-change/.agentdiag/targets/toy-order-desk/runs
cp -r tests/fixtures/runs/20260923T100000Z-base tests/fixtures/runs/20260923T100200Z-prmt /tmp/agentdiag-change/.agentdiag/targets/toy-order-desk/runs/
uv run agentdiag change open --root /tmp/agentdiag-change --complaint tests/fixtures/complaints/shipped-order-refund.md --layer rules --title "Refund promised on a shipped order" --by support
```

```
opened 20260929-refund-promised-on-a-shipped-order at /tmp/agentdiag-change/.agentdiag/targets/toy-order-desk/changes/20260929-refund-promised-on-a-shipped-order.md
```

The id is the day it was opened and the title's slug. `propose` records what the change
touches (Fingerprint section ids, files under the Target directory, Flow ids, and the
Workspace repository's HEAD), and `expect` states, before the verifying Run, which
Scenarios should improve and which must not move, timestamped (`<id>` is the id `open`
printed):

```bash
uv run agentdiag change propose --root /tmp/agentdiag-change <id> --section prompt.system
uv run agentdiag change expect --root /tmp/agentdiag-change <id> --should-move where-is-shipped-order --must-not-move greeting-calls-no-tool
```

```
20260929-refund-promised-on-a-shipped-order proposed: sections prompt.system
20260929-refund-promised-on-a-shipped-order expects, stated at 2026-09-29T11:05:34Z: should move where-is-shipped-order; must not move greeting-calls-no-tool
```

`show` tells the record as a story in five lanes: what happened, the problem, the fix, the
expected effect and what was observed. Each item names the Run, Trial, Spans, file or Push
record it cites, and a lane nothing has filled yet is marked pending with what would fill it
(`--json` prints the whole record as JSON instead). The same story is the UI's Flow view,
and the Report of the Run that verified the record draws it after the Scorecard when it is
served, or rendered after the close; the `report.html` written when that Run ended predates
the close and is never rewritten:

```bash
uv run agentdiag change show --root /tmp/agentdiag-change <id>
```

```
Change record 20260929-refund-promised-on-a-shipped-order: Refund promised on a shipped order
proposed · Target toy-order-desk · opened 2026-09-29T11:05:34Z by support
file .agentdiag/targets/toy-order-desk/changes/20260929-refund-promised-on-a-shipped-order.md

1 What happened
  - Complaint: From [email]: I was told my shipped order would be refunded
  - Its redacted text is under ## Trigger in the record file.
  - Opened 2026-09-29T11:05:34Z by support.

2 The problem
  - No Diagnosis: the record was opened from a complaint.
  - Layer: rules.

3 The fix · pending
  - Section prompt.system.
  - Not pushed yet: a push through the Connector (`agentdiag push`), or a Run that re-syncs onto the edited files, records where the change went.

4 The expected effect
  - Stated 2026-09-29T11:05:34Z.
  - Should move: where-is-shipped-order.
  - Must not move: greeting-calls-no-tool.

5 What was observed · pending
  - Not observed yet: `change close --verified` or `--refuted` fills this from the `compare` of a Run made after the push.
```

A record moves to `pushed` when the change reaches the deployed set. On a Target changed by
editing its files, that is the next Run that re-syncs onto the edited sections: it adds a
push event of kind `local` naming itself and both Fingerprints to every `proposed` record
whose sections moved, and writes nothing into the Run. A record closes `verified` or
`refuted` only through `compare`: `change close <id> --verified --run <post> [--baseline
<pre>] [--expect <path>]...` compares the pre-change Run (`--baseline`, or the trigger's
Run) with the post-change one, declaring `fingerprint` and `sync` for the push and the
`--expect` paths for the rest. It is refused when that Run started before the expectation
was stated, when any other configuration difference is undeclared, when a named Scenario is
missing from either Run, and when the Scores did not move as stated (then `--refuted` is the
close that is taken). Closing before the push is refused by the lifecycle, naming what it
allows:

```bash
uv run agentdiag change close --root /tmp/agentdiag-change <id> --verified --run 20260923T100000Z-base --expect manifest.prompts  # exits 3
```

```
error: Change record 20260929-refund-promised-on-a-shipped-order is proposed: it cannot move to verified; from proposed it can move to pushed, wontfix, superseded
```

`--wontfix --why <text>` and `--superseded-by <id>` close a record without a comparison.
`change list` lists the Target's records by id, `validate` checks every record with the
Manifest and the Suites, and `target show` lists the open ones:

```bash
uv run agentdiag change list --root /tmp/agentdiag-change
uv run agentdiag validate --root /tmp/agentdiag-change
```

```
id                                           status    opened                layer  title
20260929-refund-promised-on-a-shipped-order  proposed  2026-09-29T11:05:34Z  rules  Refund promised on a shipped order
validated the Manifest, 2 Suites and 1 Change record: 0 errors, 0 warnings
```

The Index gains a `changes` table rebuilt from the files, and `list` names in its `change`
column the record a Run verified or refuted.

## Pull and push

`pull` and `push` move sections between the files a Manifest points at and the deployed set
its Connector reads. Neither converses with the Target, and nothing else writes a deployed
set: agentdiag changes a Target only when a person or an agent asks for a push. The help
desk of `examples/workspace/` keeps its prompt on a platform record, with the copy under
`prompts/system.md` as the local side. Its platform is a Python module, and the Manifest's
`store: platform/local.json` keeps its record between processes (gitignored: the platform's
state, never source). Copy the Workspace into a git repository, and fix rule 5 in the local
file:

```bash
cp -r examples/workspace /tmp/agentdiag-push
git -C /tmp/agentdiag-push init -q && git -C /tmp/agentdiag-push add -A && git -C /tmp/agentdiag-push commit -qm "the example"
sed -i 's/a full card number or a security code/a full card number, a security code or a PIN/' /tmp/agentdiag-push/.agentdiag/targets/help-desk/prompts/system.md
git -C /tmp/agentdiag-push commit -qam "rule 5 names the PIN"
```

`push` is a preview until `--push`: it reads the deployed set through the Connector now,
shows exactly the bytes that would change, and names the deployed Fingerprint the write
will expect, so a deployed set that moves between the preview and the write is refused.
The help desk's `staging` is marked `protected`: a push to it names its Change record with
`--change <id>`, and then asks for the environment's name typed at the terminal. No flag
stands in for the typed name, and a session with no terminal is told to hand the push to a
person:

```bash
uv run agentdiag push --root /tmp/agentdiag-push --target help-desk --env staging --push  # exits 3
```

```
Push preview: the local files to staging
deployed set read at 2026-09-29T14:17:18Z, fingerprint a3f2b2e8 (the write expects it)
section              file
prompt.system#rules  prompts/system.md
side effects sandboxed; staging is protected: the push needs --push, the environment's name typed by a person, and --change <id>
before the write, the deployed set is saved as restore-points/<time of the write>-staging.json

--- deployed/staging/prompt.system#rules
+++ prompts/system.md
@@ -15,5 +15,5 @@
    reason, and tell the customer a person will reply. Never escalate a question an article
    answers.
 
-5. Never ask for a password, a full card number or a security code, and never repeat one a
+5. Never ask for a password, a full card number, a security code or a PIN, and never repeat one a
    customer sends.
error: staging is protected: name the push's Change record with --change <id>
```

`local` is not protected, so `--push` writes it. Before the write the deployed set is saved
as a **Restore point** under `restore-points/` (kept, gitignored; `push --restore <point>`
pushes it back under the same rules, and `--section` narrows it to one section); after it,
the Fingerprint is rebuilt from the Connector's read with `pushed_from` naming the one it
replaced, and a **Push record** under `pushes/` (committed) says who pushed what, when, how it
was confirmed, both Fingerprints, the Restore point and the Change record, whose own push
event points back at it. The next command, in a new process, finds the Target in Sync:

```bash
uv run agentdiag push --root /tmp/agentdiag-push --target help-desk --env local --push
uv run agentdiag sync --root /tmp/agentdiag-push --target help-desk --check
```

```
Push preview: the local files to local
deployed set read at 2026-09-29T14:17:20Z, fingerprint a3f2b2e8 (the write expects it)
section              file
prompt.system#rules  prompts/system.md
side effects none; local is not protected: --push writes it
before the write, the deployed set is saved as restore-points/<time of the write>-local.json

--- deployed/local/prompt.system#rules
+++ prompts/system.md
@@ -15,5 +15,5 @@
    reason, and tell the customer a person will reply. Never escalate a question an article
    answers.
 
-5. Never ask for a password, a full card number or a security code, and never repeat one a
+5. Never ask for a password, a full card number, a security code or a PIN, and never repeat one a
    customer sends.
pushed prompt.system#rules to local
fingerprint a3f2b2e8 -> f779ab90 (pushed_from a3f2b2e8)
deployed fingerprint a3f2b2e8 -> f779ab90
Restore point .agentdiag/targets/help-desk/restore-points/20260929T141720Z-local.json
Push record .agentdiag/targets/help-desk/pushes/20260929T141720Z-local.json
Sync held against fingerprint f779ab90 (built 2026-09-29T14:17:20Z, local)
section                direction  change  covered
flow.open_ticket_flow  identical  -       connector
model                  identical  -       connector
prompt.system#persona  identical  -       connector
prompt.system#rules    identical  -       connector
prompt.system#tone     identical  -       connector
provider               identical  -       connector
tool.escalate          identical  -       connector
tool.open_ticket       identical  -       connector
tool.search_articles   identical  -       connector
9 sections identical
```

A push refuses itself when Sync reads the deployed side ahead (`deployed_ahead` or
`diverged`) on a section it would write: pull, merge and push again; there is no force
flag. An edit made on the platform (here, to the store the platform keeps) is such a case,
and `pull` brings it home: it writes the deployed sections into the pointed files and
prints the git diff. It never commits and never touches `fingerprint.json`; it skips a file
with uncommitted changes unless `--overwrite-local`, and a pointer outside the Workspace
root, naming it:

```bash
sed -i 's/At most four sentences per reply./At most three sentences per reply./' /tmp/agentdiag-push/.agentdiag/targets/help-desk/platform/local.json
uv run agentdiag sync --root /tmp/agentdiag-push --target help-desk --check  # exits 2
uv run agentdiag pull --root /tmp/agentdiag-push --target help-desk --env local
```

```
Sync broken against fingerprint f779ab90 (built 2026-09-29T14:17:20Z, local)
section                direction       change   covered
flow.open_ticket_flow  identical       -        connector
model                  identical       -        connector
prompt.system#persona  identical       -        connector
prompt.system#rules    identical       -        connector
prompt.system#tone     deployed_ahead  changed  connector
provider               identical       -        connector
tool.escalate          identical       -        connector
tool.open_ticket       identical       -        connector
tool.search_articles   identical       -        connector
8 sections identical, 1 deployed_ahead
recorded Sync break /tmp/agentdiag-push/.agentdiag/targets/help-desk/sync-breaks/20260929T141725Z.json
Pull from local: 1 section written, 0 skipped
written  prompt.system#tone  prompts/system.md
nothing was committed and fingerprint.json is unchanged; review the diff, commit, then `agentdiag sync`

diff --git a/.agentdiag/targets/help-desk/prompts/system.md b/.agentdiag/targets/help-desk/prompts/system.md
index c04c598..cd4aa47 100755
--- a/.agentdiag/targets/help-desk/prompts/system.md
+++ b/.agentdiag/targets/help-desk/prompts/system.md
@@ -27,4 +27,4 @@ order desk: a question about one order's status belongs to the order desk, and y
 
 # Tone
 
-Plain and warm. At most four sentences per reply. No marketing language and no emoji.
+Plain and warm. At most three sentences per reply. No marketing language and no emoji.
```

The Target has one Fingerprint, recorded against the environment last synced or pushed; a
push to another environment whose sections read ahead only for that reason is refused
saying `agentdiag sync --env <that environment>` records it against that one. `compare`
labels a push an undeclared variation of two Runs (`fingerprint.pushed_from` and each moved
section) unless it is declared with `--expect fingerprint`. The `agentdiag-fix-cycle` skill
(`init --skills`) walks one fix from its trigger to a verified Change record through these
commands, and `agentdiag-correction` a Calibration Notes, Eval parameters or Suppression
edit through a `rescore` and `compare`.

## Serve the UI

`serve` runs the local web UI over a Workspace: one page on 127.0.0.1, the same renderer
the Report file embeds, fed by JSON routes that call the functions the commands call.

```bash
uv run agentdiag serve --root /tmp/agentdiag-serve
```

```
agentdiag serve: http://127.0.0.1:7331/ (Workspace /tmp/agentdiag-serve); Ctrl-C stops it
```

`--port` picks another port (0 a free one) and `--open` opens the page in the browser. The
Target picker sits on every screen and keys 1 to 7 switch screens: the Dashboard (the first
screen, below), a Target, its Sync per environment, its Tests, a Run's Report, a comparison of two Runs,
and the Flow view of a Change record or of one Flow's definition. Every screen reads a route,
and a route answers JSON; `GET /api/registry` over the example Workspace starts:

```json
{
  "slug": "help-desk",
  "name": "help-desk",
  "family": "northwind",
  "channel": "chat",
  "environments": ["local", "staging"],
  "protected": ["staging"],
  "connector": "inprocess",
  "suites": [{"path": "suites/generated.yaml", "status": "runnable"}],
  "sync": {
    "status": "held",
    "open_breaks": 0,
    "fingerprint": "a3f2b2e8c9d8ff8da0b3e5e60f6afc305e1dc5fe90a936a917ab7d014735875d",
    "built_at": "2026-09-28T13:33:51Z",
    "environment": "local",
    "last_push": null
  },
  "problems": [],
  "maintainer_notes": "maintainer_notes.md"
}
```

The page writes only by launching a Run, or by a pull or a push under the rules of `push`.
The Tests screen ticks Suites, tags and Scenario ids, shows the selection expression
`run.json` will record (`scenario=a,b tag=x suite=orders`), runs `--dry-run`, launches the
Run in-process and streams its Trials as `show --follow` reads them; Cancel lets the Trial in
progress finish and records every Trial not started `not_run: cancelled`. The Sync screen
shows the three hashes per section, pulls with the git diff shown, and pushes after a preview
of exactly the bytes that would change, naming the Restore point and the Change record: an
unprotected environment is written when the push box (the page's `--push`) is ticked; a
protected one only when a person types the environment's name, recorded as `ui_confirm` in
the Push record, and no box stands in for it. A request that did not come from the page is refused:
a `Host` or an `Origin` not the server's own, `Sec-Fetch-Site: cross-site`, a POST without
`Content-Type: application/json`, the `X-Agentdiag-Request: 1` header and the page token
this process embedded in the page (`X-Agentdiag-Token`; a follow carries it as `?token=`).
Reading a screen writes nothing: the Sync screen compares as `sync --check` does without
recording a Sync break.

## The Dashboard

`dashboard` prints every Target of the Workspace on one line: its Family, its Sync state (the
environment its Fingerprint was recorded against, and any open Sync break), its last Run with
when it was made and every Verdict counted, the trend over its last ten Runs agentdiag drove
(`--trend N` for another depth), oldest first, each pass rate with that Run's `incomplete`,
`unverifiable` and `invalid` counts beside it (`-` for a Run with no pass rate), and how many
Change records are still open. Over a copy of the example Workspace, whose help desk has a
Fingerprint and no Run and whose order desk has neither:

```bash
cp -r examples/workspace /tmp/agentdiag-dashboard
uv run agentdiag dashboard --root /tmp/agentdiag-dashboard
```

```
target      family     sync            last run    created  verdicts  trend (incomplete/unverifiable/invalid)  changes
help-desk   northwind  held (local)    no Run yet  -        -         -                                        none open
order-desk  northwind  not synced yet  no Run yet  -        -         -                                        none open
```

It reads the Registry and the Index and writes nothing; it is never a source of truth. `run`
records each Run into the Index and every `change` command, and every push that moves a
record, records the record's file, so the counts need no rebuild. A Target with no Run says
`no Run yet` and one with no Fingerprint `not synced yet`, never a row of zeros. An imported
Run, or a rescore, is shown as the last Run when it is one (`(imported)`, `(rescored)` after
its id) and never trended. A missing, corrupt or stale Index is a `problem:` line after the
table naming `agentdiag index rebuild`, and the rows then say `Index not read` or show what
the Index holds; a Run directory a rebuild would skip is its own `problem:` line, in the
words `list` warns with, and an Index another process is writing says to try again. `--json`
prints the same view, and `GET /api/dashboard?trend=N` serves it to the UI's first screen,
where each row links to the Target, its Sync screen per environment, its last Run, its Tests
and the Flow view of each open Change record, and the trend is drawn as a sparkline whose
every point carries the same three counts.

## List the Runs

`list` prints one line per Run of every Target, newest first (`--target <slug>` lists
one): its Target's slug, its source (`run` for a Run agentdiag drove, `imported` for one
`import` made), when it was made, its Fingerprint (`-` until a Run records
one), the selection it ran, the Sync status, every Verdict counted with the zeros left
in, how many Scenarios or Trials did not run, whose Traces a rescore read, the Change
record the Run verified or refuted (`-` for none), and the space the Run takes on disk.
It reads the Workspace's SQLite index at `.agentdiag/index.sqlite`, which `run` and
`rescore` write when a Run completes. The index is derived: `list` first indexes any Run directory it does not hold and forgets
any whose directory is gone, builds the whole index when there is none (saying `notice:
built the index at …` on stderr), and `agentdiag index rebuild` deletes it and builds it
again from the Run directories, which are the truth. A corrupt index is reported with
that command and exit 3; nothing rebuilds it behind your back. With the eight committed
Run fixtures copied into a Target's `runs/` (a Run the sections above wrote there is
listed too):

```bash
mkdir -p /tmp/agentdiag-toy/.agentdiag/targets/toy-order-desk/runs
cp -r tests/fixtures/runs/* /tmp/agentdiag-toy/.agentdiag/targets/toy-order-desk/runs/
uv run agentdiag list --root /tmp/agentdiag-toy
```

```
run                    target          source  created               fingerprint  selection                                                                                                                   sync                          verdicts                                                  not run  traces                      change  size
20260923T100700Z-thrs  toy-order-desk  run     2026-09-23T10:07:00Z  -            all                                                                                                                         not_checked (no_fingerprint)  pass 14  fail 3  incomplete 0  unverifiable 0  invalid 0  9        from 20260923T100000Z-base  -       79 KB
20260923T100600Z-tri3  toy-order-desk  run     2026-09-23T10:06:00Z  -            scenario=greeting-calls-no-tool,lookup-then-cancel,where-is-shipped-order                                                   not_checked (no_fingerprint)  pass 47  fail 1  incomplete 6  unverifiable 0  invalid 0  11       -                           -       146 KB
20260923T100500Z-resc  toy-order-desk  run     2026-09-23T10:05:00Z  -            all                                                                                                                         not_checked (no_fingerprint)  pass 15  fail 2  incomplete 0  unverifiable 0  invalid 0  9        from 20260923T100000Z-base  -       79 KB
20260923T100400Z-jdge  toy-order-desk  run     2026-09-23T10:04:00Z  -            scenario=delivered-order-cannot-be-cancelled,greeting-calls-no-tool,status-question-is-not-a-cancel,where-is-shipped-order  not_checked (no_fingerprint)  pass 14  fail 3  incomplete 0  unverifiable 0  invalid 0  9        -                           -       102 KB
20260923T100300Z-part  toy-order-desk  run     2026-09-23T10:03:00Z  -            scenario=delivered-order-cannot-be-cancelled,status-question-is-not-a-cancel,where-is-shipped-order                         not_checked (no_fingerprint)  pass 12  fail 2  incomplete 0  unverifiable 0  invalid 0  10       -                           -       107 KB
20260923T100200Z-prmt  toy-order-desk  run     2026-09-23T10:02:00Z  -            scenario=delivered-order-cannot-be-cancelled,greeting-calls-no-tool,status-question-is-not-a-cancel,where-is-shipped-order  not_checked (no_fingerprint)  pass 14  fail 3  incomplete 0  unverifiable 0  invalid 0  9        -                           -       118 KB
20260923T100100Z-swap  toy-order-desk  run     2026-09-23T10:01:00Z  -            scenario=delivered-order-cannot-be-cancelled,greeting-calls-no-tool,status-question-is-not-a-cancel,where-is-shipped-order  not_checked (no_fingerprint)  pass 14  fail 3  incomplete 0  unverifiable 0  invalid 0  9        -                           -       119 KB
20260923T100000Z-base  toy-order-desk  run     2026-09-23T10:00:00Z  -            scenario=delivered-order-cannot-be-cancelled,greeting-calls-no-tool,status-question-is-not-a-cancel,where-is-shipped-order  not_checked (no_fingerprint)  pass 15  fail 2  incomplete 0  unverifiable 0  invalid 0  9        -                           -       118 KB
```

`--limit N` shows the N newest; `--json` prints the same rows as a JSON array, one object
per Run in the shape `schemas/listing.schema.json` publishes. `init` gitignores the index
beside `runs/`: it is output, never source.

## What a Run directory holds

```
/tmp/agentdiag-demo/.agentdiag/
├── index.sqlite                      gitignored: the derived index `list` reads, rebuilt at will
└── targets/default/                  one Target directory; a Workspace holds one per Target
    ├── manifest.yaml                 the Target's identity document: pointers, not copies
    ├── judge_notes.md                the Judge's calibration notes for this Target
    ├── maintainer_notes.md           what a maintainer must know first; never the Judge's
    ├── redaction.yaml                gitignored: the names a Change record never carries
    ├── suites/sample.yaml            one Suite: the Scenarios written for this Target
    └── runs/                         gitignored: Runs are output, never source
        └── 20260922T160953Z-7vnc/    one Run, named by creation time, never overwritten
            ├── run.json              the configuration this Run froze: Target, Manifest, Adapter, Judge, git state
            ├── scorecard.json        every Verdict counted, with what did not run
            └── trials/cancel-processing-order/1/
                ├── trace.jsonl       the Trace: every Event of this Trial, append-only
                ├── judgement.jsonl   the Judge's own Events: prompt, request, response, Diagnosis
                └── scores.json       one Score per declared Eval (one per rule for guardrails)
```

A Run is never edited. Re-scoring writes a new Run whose `run.json` names its source under
`traces_from` and whose Trials hold no `trace.jsonl` of their own; nothing can make an old
Run say something else. With `--trials N`, each Scenario has `trials/<id>/1/` to `N/`.

[`examples/toy/`](../examples/toy) is that scaffold checked in: exactly what `init` writes when
neither `--target` nor `--adapter` names another Target, rendered from the same template and
asserted byte for byte, so the example can never quietly stop being what the command
produces. Its one Target
is `toy-order-desk`, under `.agentdiag/targets/toy-order-desk/`, so every command reaches it
with no `--target`. [`examples/workspace/`](../examples/workspace) is the two-Target Workspace:
the same order desk and a help desk, gated the same way.

## Vocabulary

Target, Adapter, Scenario, Trace, Event, Span, Trial, Run, Judge, Eval, Score, Verdict,
Scorecard — each is defined once, in [`CONTEXT.md`](../CONTEXT.md), and every file, function and
command here uses those words and no synonyms.

## Status

agentdiag drives in-process and HTTP Targets, records and judges their Traces, compares and
rescores Runs, keeps a Workspace of Targets in Sync through their Connectors (pull and push
included), discovers and generates Suites, imports conversations, records fixes as Change
records closed only through `compare`, and serves a local web UI with the Report, the Flow
view and the Dashboard. Platform plugins are distributed separately.

## Contributing

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Tests drive three seams and nothing else. **Seam 1** is the CLI over a Run directory, where
`run` and `show` are invoked in-process against the toy Target in replay and asserted on what
a user could observe — a file, a line of output, an exit code. **Seam 2** is the ModelClient,
where every Judge behaviour is tested through committed recordings and the replay client is
tested for failing loudly. **Seam 3** is the Adapter, where the conformance suite runs against
any Adapter and asserts only on the Events it emits.

Regenerating the recorded fixtures:

```bash
uv run python scripts/record_fixtures.py
uv run python scripts/record_fixtures.py --scripted
uv run python scripts/record_fixtures.py --runs
uv run python scripts/record_fixtures.py --capture   # needs credentials
```

The first three need no credentials: they drive the real Eval modules and the toy Target
through the real Adapter against a scripted model, so every recorded request is the one
agentdiag renders, and they write the recordings, the Trace fixtures and the Run fixtures in
that order. Every recording regenerates together, because the request body is the replay
key: change the prompt and every recorded request must change with it. A recording whose
story is the natural one carries the Judge's real answer, captured once by `--capture`
through the Claude Code login into `tests/fixtures/recordings/captured-judge-answers.jsonl`;
one that exercises a code path (a refusal, a schema failure, a fail over a passing Trace or
a pass over a failing one) is worked, its authored answer the fixture. Without a captured
answer for a natural request the script renders the authored story, warns, and exits 1.
[`tests/fixtures/README.md`](../tests/fixtures/README.md) lists the tree, which recordings are captured and which are worked and why, the captured
line's shape, and when to re-capture. `--live`, also for a maintainer with credentials,
records one real `prompt_adherence` exchange into `recordings/judge-live.jsonl`.

Two pytest markers are deselected by default: `network` (refetches the pinned OpenTelemetry
GenAI attribute registry) and `live` (calls the API). Run them with
`uv run pytest -m network` or `uv run pytest -m live`.
