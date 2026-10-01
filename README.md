# agentdiag

A profiler and evaluator for agentic systems, and a kit for maintaining them. agentdiag
drives a **Target** (your agent) through **Scenarios**, records everything that happened as
a **Trace**, judges each Trace with **Evals**, and compares **Runs**. Around that core it
keeps a Target's local definition and deployed set in **Sync**, imports the conversations
your platform already logged, and records every fix as a **Change record** with the Runs
that verified it.

A Run holds one or more Trials per Scenario; each Score carries a Verdict (`pass`, `fail`,
`incomplete`, `unverifiable` or `invalid`) and cites the Spans it judged. Every one of those
words means one thing here; [`CONTEXT.md`](CONTEXT.md) is the dictionary.
[`docs/guide.md`](docs/guide.md) is the full guide; this page is the five-minute start.

## Install

Python 3.12 or newer.

```bash
pip install git+https://github.com/Inderdeep-Singh02/agentdiag
agentdiag --help
```

From a checkout, with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
uv run agentdiag --help
```

## The first Run

`init` scaffolds a Target, `run` records one Trial, `show` reads it back. With no
`--adapter`, the scaffold points at the toy Target that ships with agentdiag, an order desk
with five numbered rules, a lookup tool and an action tool, so the first Run works before
you have written anything. From a checkout every command runs with `uv run` and names its
Workspace with `--root`; with `agentdiag` on your PATH, drop `uv run` and run from the
directory that holds `.agentdiag/` instead of passing `--root`.

```bash
uv run agentdiag init --root /tmp/agentdiag-demo
```

```
Wrote the scaffold into /tmp/agentdiag-demo:
  .agentdiag/targets/default/manifest.yaml
  .agentdiag/targets/default/suites/sample.yaml
  .agentdiag/targets/default/judge_notes.md
  .gitignore

Target default (toy-order-desk), driven by the agentdiag.examples.toy:make_target factory.

Next:
  agentdiag run --scenario cancel-processing-order
  agentdiag show <run> cancel-processing-order

A judged Eval needs credentials: a Claude Code login (claude auth login) or export ANTHROPIC_API_KEY=….
```

A judged Eval needs a model: the Claude Code CLI logged in (`claude auth login`), or
`ANTHROPIC_API_KEY` in the environment, or a `~/.agentdiag/env` file of `KEY=value` lines
that every command reads once ([credentials](docs/guide.md#drive-a-target-over-http)).
agentdiag never stores a credential; before driving anything, `run` names the source it
found, never the value. A live first Run takes about a minute and a few cents of API or
plan quota.

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

The toy Target is built to break its own rule 3 by inventing a refund window, and the
Judge catches it, so this first Run exits 1, by design. `run` exits with the Verdict (0 all pass, 1 any
fail, 2 nothing failed but something undecided, 3 agentdiag itself could not run), so an
exit of 1 is agentdiag working, not the install failing.

From a checkout, without credentials, the same Run replays model exchanges recorded in the
repository and exits 1 for the same reason (`--replay` is a test flag, not shown in
`--help`):

```bash
uv run agentdiag run --root /tmp/agentdiag-demo --scenario cancel-processing-order --replay tests/fixtures/recordings/toy-cancel.jsonl
```

Read the Trial back as one story, the Trace, the Judge's reasoning and the Scores, with the
Run id `run` printed in place of `<run>`:

```bash
uv run agentdiag show <run> cancel-processing-order --root /tmp/agentdiag-demo
```

Then: `list` lists every Run, `compare <baseline> <run>` leads with the configuration diff
before the Scores, `rescore <run>` judges a Run's Traces again without touching the Target,
`export` writes a Trial for Perfetto or speedscope, and `serve` opens the local UI. Each is
walked through in [`docs/guide.md`](docs/guide.md).

## Point it at your own Target

A Python Target is a factory; the Adapter hands it a model client and its tools and
instruments it from the outside, so the Target imports nothing of agentdiag:

```bash
uv run agentdiag init --root <your-repo> --adapter python:<module>:<factory> --tools <module>:<tools>
```

A Target you did not write, reachable only through the chat endpoint it ships, is driven by
the `http` Adapter: one POST per Turn, the reply read as it streams, credentials named by
environment variable and never by value. See
[Drive a Target over HTTP](docs/guide.md#drive-a-target-over-http).

## The Workspace

Everything agentdiag writes lives under one `.agentdiag/` directory, the **Workspace**, with
one directory per Target. Runs, Restore points, the Index and the in-process Connector's
platform store are output and gitignored; everything else is source.

```
.agentdiag/
├── index.sqlite                      gitignored: the derived Index `list` and the Dashboard read
└── targets/<slug>/                   one Target; a Workspace holds as many as you keep
    ├── manifest.yaml                 the Target's identity document: pointers, not copies
    ├── judge_notes.md                the Judge's calibration notes for this Target
    ├── suites/*.yaml                 Suites: the Scenarios written for this Target
    ├── changes/*.md                  Change records: one recorded fix each
    ├── fingerprint.json              the deployed set's hashes at the last Sync
    ├── sync-breaks/                  open and closed Sync breaks
    ├── pushes/                       Push records: what each push wrote, and when
    ├── restore-points/               gitignored: the deployed set as read before each push
    ├── platform/                     gitignored: the in-process Connector's deployed set
    └── runs/<run-id>/                gitignored: one immutable Run
        ├── run.json                  the configuration this Run froze
        ├── scorecard.json            every Verdict counted, with what did not run
        ├── report.html               gitignored: the HTML Report
        └── trials/<scenario>/<n>/    trace.jsonl, judgement.jsonl, scores.json
```

`agentdiag init --target <slug>` adds a Target; `agentdiag registry` lists them;
`agentdiag dashboard` shows every Target's Sync state, last Run and Score trend.
[`examples/workspace`](examples/workspace) is a two-Target Workspace checked in.

## Plugins

Core knows no platform. A platform's Connector (which reads and pushes the deployed set and
reads the logs the platform keeps), its Dialect (how its chat endpoint frames a Turn) and
its identity modes (how a conversation says who the user is) live in a separate distribution that registers them under the
`agentdiag.adapters`, `agentdiag.connectors` and `agentdiag.dialects` entry-point groups; a
Manifest names a kind, and core finds the class by name. A plugin imports only the public
API named in [`docs/plugins.md`](docs/plugins.md) and proves itself with the conformance
suites core ships.

## Skills

Four Claude Code skills, invoked by name (`/agentdiag-fix-cycle`), make a coding agent
operate agentdiag; each is a procedure calling agentdiag commands and never a platform's:

| Skill | Does |
|-------|------|
| `agentdiag-discover` | Draft, review and install the Manifest of a Target the Workspace has never described |
| `agentdiag-generate` | Draft a Suite of Scenarios from a Target's prompt rules and tools, and write it with `generate` |
| `agentdiag-fix-cycle` | Take one failure from its trigger to a verified or refuted Change record, through `change`, `push` and `compare` |
| `agentdiag-correction` | Correct a Score the Judge or an Eval got wrong, through the Judge's notes, an Eval's parameters or a Suppression that sets a known false fail aside, and prove it with `rescore` and `compare` |

```bash
uv run agentdiag init --root /tmp/agentdiag-demo --skills   # installs them under the Workspace root's .claude/skills/
```

## Documentation

- [`docs/guide.md`](docs/guide.md): every command, walked through on the shipped examples.
- [`docs/scenario-schema.md`](docs/scenario-schema.md): the Scenario schema and the Eval catalogue.
- [`docs/judge-notes.md`](docs/judge-notes.md): what belongs in a Target's calibration notes.
- [`docs/plugins.md`](docs/plugins.md): the plugin contract.
- [`docs/adr/`](docs/adr): the design record, one decision per file, written as each was made.
- [`CONTEXT.md`](CONTEXT.md): the vocabulary.

## Contributing

```bash
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```

Tests drive three seams and nothing else: the CLI over a Run directory, the ModelClient
through committed recordings, and the Adapter through its conformance suite.
[`AGENTS.md`](AGENTS.md) has the rules a contributor, human or agent, follows.

## License

[MIT](LICENSE).
