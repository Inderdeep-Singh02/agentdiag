---
status: accepted
date: 2026-10-02
---

# 0016 — The Workspace explains itself: an Orientation page, Maintainer notes, an identity scaffold, and skills in one layout every Harness reads

## Context

ADR-0014's context says the reference repository's product is "its `AGENTS.md` routing plus
six maintenance skills". Decision §3 took the skills and dropped the routing page. The first
Workspace built with 0.1.1 for ten real Targets showed the result: `init --target <slug>`
could not say who the Target was and wrote the toy's Manifest ten times over; nothing a
coding agent reads was written at the root; and the skills were installed only where Claude
Code looks, so a Codex or Gemini CLI session cloning that Workspace found no instructions and
no skills. A Target also had nowhere to keep what a maintainer must know before touching it
(what it does, who it serves, which environments reach real users, the traps, where the
evidence is) that the Judge must never read: `judge_notes.md` reaches every Judge prompt
verbatim and is the wrong home for an operational fact.

Where each Harness reads project instructions and skills, from its documentation as read on
2026-10-02:

| Harness | Project instructions | Skills |
|---|---|---|
| Claude Code | `./CLAUDE.md` or `./.claude/CLAUDE.md`, loaded from the working directory and every directory above; `AGENTS.md` is read natively only when no `CLAUDE.md` or `CLAUDE.local.md` is on that path, and an `@AGENTS.md` import inside `CLAUDE.md` is the documented way to share one file; nothing under `.agents/` is read as instructions ([memory](https://code.claude.com/docs/en/memory)) | `.claude/skills/<name>/SKILL.md` in the working directory and each parent up to the repository root, and `~/.claude/skills/`; a `<name>` entry may be a symlink to a directory elsewhere; `.agents/skills/` is not read ([skills](https://code.claude.com/docs/en/skills)) |
| Codex | `AGENTS.override.md`, else `AGENTS.md`, one per directory from the git root down to the working directory, after `~/.codex/AGENTS.md`; 32 KiB in all ([agents-md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)) | `.agents/skills/` in the working directory, each parent and the repository root, then `~/.agents/skills` and `/etc/codex/skills`; symlinked skill folders are followed ([build-skills](https://learn.chatgpt.com/docs/build-skills)) |
| Gemini CLI | `GEMINI.md` in the workspace directories and their parents, and `~/.gemini/GEMINI.md`; `@./file.md` imports a file (relative with `./`, or absolute; depth 5); `AGENTS.md` is read only when `context.fileName` in `settings.json` names it ([gemini-md](https://geminicli.com/docs/cli/gemini-md/), [memport](https://geminicli.com/docs/reference/memport/)) | `.gemini/skills/` or its alias `.agents/skills/` in the workspace, `~/.gemini/skills/` or `~/.agents/skills/` for the user; `.agents/skills/` wins within a tier ([skills](https://geminicli.com/docs/cli/skills/)) |

One directory, `.agents/skills/`, is read by two of the three Harnesses directly and by the
third through a symlink its documentation allows. The reference repository already keeps its
skills there, with `.claude/skills` a symlink to it.

## Decision

1. **A Workspace has an Orientation page, `AGENTS.md` at its root**, written by `init` and
   refreshed by `registry --write`: what the repository is; the read set a coding agent reads
   before its first command (this page, the Target's Maintainer notes, one skill, with
   `agentdiag <command> --help` and the vocabulary as reference); the request-to-skill routing
   table; the Targets table between the markers `<!-- agentdiag:targets -->` and
   `<!-- /agentdiag:targets -->` (slug, name, Family and channel, default environment,
   protected environments, Suites with status, Connector kind, Maintainer notes); the standing
   rules that are agentdiag's and no platform's (diff before push, a protected environment
   confirms by name, Verdicts cite Spans, no credential value in any file, Runs are output);
   and the commands. `CLAUDE.md` is the one line `@AGENTS.md` and `GEMINI.md` the one line
   `@./AGENTS.md`, each Harness's own import spelling, so the page is read whichever Harness
   opens the clone. The page names no platform: the routing and the rules are generic and the
   table is generated. The read set is budgeted at 10k tokens, measured as characters over
   four: the page with ten Targets, the vocabulary and the longest skill fit inside it, and a
   test holds the budget.
2. **The vocabulary ships with the page.** `init` and `registry --write` write
   `.agentdiag/CONTEXT.md`, a copy of agentdiag's `CONTEXT.md` taken from the installed
   package, so a clone with no network and no agentdiag checkout still has the words the page
   and the skills use. It sits under `.agentdiag/` because the Workspace root may be a
   repository with a `CONTEXT.md` of its own; it is agentdiag's file and is rewritten freely.
3. **agentdiag never overwrites a file the user owns.** A root `AGENTS.md` without the markers
   is the user's: `init` and `registry --write` append the marked Targets section at its end
   and print a notice; one with the markers has the text between them regenerated and nothing
   outside them touched; `CLAUDE.md` and `GEMINI.md` are written only when absent, and one
   that exists without the import line gets a notice naming the line to add. `validate`
   warns, never errors, when the table between the markers no longer matches the Manifests,
   when `CLAUDE.md` or `GEMINI.md` exists without its import line, and, in a Workspace where
   the skills are installed, when the page or either import file is absent; a Workspace with
   no skills installed is not told to add a page it has no routing for.
4. **`init --target <slug>` scaffolds an identity, never the toy.** `--name` (default: the
   slug), `--description`, `--family` and `--channel` fill `target`, `family` and `channel`;
   the Adapter is `kind: pending`, a core kind that validates with a warning and refuses to
   drive, under `# REVIEW:` lines saying what to fill in the shape `discover` writes; the
   `connector` block and the `prompts` pointers are comments under `# REVIEW:` lines; the
   sample Suite is a draft (`{path: suites/sample.yaml, status: draft}`). Such a Target
   validates with warnings and no errors (the pending Adapter, the REVIEW lines, the draft),
   and `run --dry-run`, `run` and `sync` refuse it naming the pending Adapter and the draft
   Suite rather than driving anything. The toy stays the scaffold of the first `init` with no
   `--target`, so the five-minute path is unchanged, and `--adapter toy` scaffolds it under
   any slug; `--adapter python:<module:attr>` keeps scaffolding a Target of one's own. The
   identity flags apply to every scaffold.
5. **A Target has Maintainer notes, `maintainer_notes.md` beside the Manifest**, pointed at by
   the Manifest's `maintainer_notes` key, scaffolded by `init` (and by `discover` for a Target
   it creates) with the headings a maintenance cycle needs — what the Target does, who it
   serves, default and protected environments, known traps, where evidence lives — and kept
   on `--force` as `judge_notes.md` is. Every skill reads them before its first step; the
   Judge never does: nothing under `agentdiag.eval` reads the key, and a test asserts the
   notes' text reaches no Judge prompt. `target show` prints them, the Registry entry carries
   the pointer, and the Targets table lists it. The name is not "operator notes" because
   `operator` is already the Actor of a staff-takeover Turn (ADR-0013 §5).
6. **Skills live in one tracked copy every Harness reads.** `init --skills` writes each
   packaged skill to `.agents/skills/agentdiag-<name>/`, which Codex and Gemini CLI read
   directly, and makes `.claude/skills/agentdiag-<name>` a relative symlink to it
   (`../../.agents/skills/agentdiag-<name>`), per skill, so a Workspace's own skills beside it
   are untouched; where the filesystem refuses the symlink, a copy is written and the command
   says so. A 0.1.1 install, a real directory under `.claude/skills/`, is replaced by the
   symlink when its files match the package's and refused by name otherwise unless
   `--force`, as an edited skill always was. `validate` warns when a Claude Code copy differs
   from the tracked copy, when one layout holds a skill the other lacks, and, as decision 3
   says, when the instruction files have gone stale. `init --skills` also writes the
   Orientation page files that are absent, because skills without the routing page is the
   gap this ADR closes.
7. **A migrate skill, `agentdiag-migrate`**, onboards a Target whose prompts, Scenarios,
   judging rules and notes already live in another maintenance repository, offline and with
   no Connector yet: the identity (decision 4), the prompts and tool schemas copied under the
   Target directory and pointed at, the source's tests read and written as drafts for
   `generate`, the judging rules carried into `judge_notes.md` and the operational facts into
   `maintainer_notes.md`, then `validate` and `registry --write`. The source repository's
   closed fix history is out of its scope: a Change record closes only through `compare`
   (ADR-0012 §3), so importing closed records needs an ADR-0012 amendment, left as a ticket.
8. **Three frictions of the first Workspace build.** `validate --all` validates every Target,
   one summary line each, the Workspace-level warnings once, one exit code; `discover
   --from-connector --env <x>` on the in-process Connector says that it reads a module in this
   process and no platform, and names the plugin Connector as the next step, instead of
   "names no environment"; and `init` with no `--root` adds the Target to the nearest
   Workspace at or above the current directory, found by ADR-0015 §1's bounded walk, and
   creates one in the current directory only when none is found, so `init --target` inside a
   Workspace needs no `--root` and nests nothing.

ADR-0014 §3 is amended: the skills are installed under `.agents/skills/` with
`.claude/skills/` reaching them, not under `.claude/skills/` alone, and the Orientation page
is part of the operating procedure the kit installs. ADR-0013 §1's Workspace layout gains, at
the root, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.agents/skills/` and `.claude/skills/`, and
under `.agentdiag/` the vocabulary copy; and in each Target directory `maintainer_notes.md`.

## Considered options

- Writing only `CLAUDE.md`, as 0.1.1 did implicitly by installing only there. Rejected: two
  of the three Harnesses never read it, and Claude Code itself reads `AGENTS.md` natively.
- Symlinking the whole `.claude/skills` directory to `.agents/skills`, as the reference
  repository does. Rejected for a Workspace inside a repository that has skills of its own
  under `.claude/skills/`: the link would swallow them. A per-skill link leaves them alone.
- Copies in every Harness directory, no symlinks. Rejected: three copies drift, and
  `validate` would be warning about agentdiag's own output; one tracked copy is the only
  arrangement where an edit has one home. The copy remains the fallback where a symlink is
  refused, and `validate` watches it.
- An Adapter block left out of the identity scaffold, or `kind: inprocess` with a commented
  factory. Rejected: the Manifest model requires an Adapter, and an in-process block with no
  factory is an error, not a state. `pending` is a state: it validates, it says what it is,
  and every command that would drive refuses by name.
- Writing the toy under `init --target <slug>` when no `--adapter` is given, as before.
  Rejected by the observed result: ten Targets named `toy-order-desk` of Family `northwind`.
- `operator_notes.md`. Rejected: `operator` names the staff member who takes over a Turn.
- A URL to the vocabulary instead of a copy under `.agentdiag/`. Rejected: a Codex or Gemini
  session in a clone has no reason to be able to fetch it, and the skills lean on the words.
- Warning on an absent Orientation page in every Workspace. Rejected: a one-Target Workspace
  in a corner of a repository with its own instruction files has nothing to route; the page
  is checked where the skills are installed, because that is where a session reaches for it.

## Consequences

- `agentdiag.orientation` renders and writes the page, the import files and the vocabulary
  copy, regenerates the marked table, and reports the staleness warnings `validate` prints;
  `init`, `init --skills` and `registry --write` call it. `CONTEXT.md` is package data, gated
  byte for byte against the repository's.
- `agentdiag.adapter.pending` registers the `pending` kind; `manifest_checks` owns its
  warning and the preflight's refusal; `validate` counts `# REVIEW:` lines in a Manifest as a
  warning.
- `Manifest.maintainer_notes`, `TargetPaths.maintainer_notes`, `RegistryEntry.maintainer_notes`
  and the regenerated schemas; `render_target` gains a `maintainer notes` row.
- `agentdiag.run.skills` writes the tracked copy, the per-skill links and the fallback copies,
  migrates a 0.1.1 install, and reports where each Harness finds the skills.
- `examples/workspace` carries the page, the import files, the vocabulary copy, the Maintainer
  notes starters and the two skill layouts, gated as before; its order desk is scaffolded with
  `--adapter toy`.
- A Workspace built with 0.1.1 gains the page and the shared skill layout from one
  `init --skills` (with `--force` when its installed skills are 0.1.1's); its toy Manifests
  remain the owner's to replace, Target by Target, with `init --target <slug> --force` and
  the identity flags, which also writes the Maintainer notes starter where none exists.
