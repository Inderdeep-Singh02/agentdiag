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
| `help-desk` | help-desk | northwind / chat | local | staging | generated | inprocess | - |
| `order-desk` | toy-order-desk | northwind / chat | local | - | sample | inprocess | - |
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
