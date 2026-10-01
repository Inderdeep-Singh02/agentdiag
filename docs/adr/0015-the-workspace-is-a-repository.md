---
status: accepted
date: 2026-10-01
---

# 0015 — A Workspace is one repository: discovery stops at its top level, pointers stay inside it, and the names to redact stay out of it

## Context

ADR-0013 §2 made the Workspace root the user's choice, found by walking up from the current
directory, and placed it outside a Target's own repository so that agentdiag never writes
into that repository. ADR-0014 §4 made `agentdiag init` at a repository root the install
shape. A read-only verification of the kit against that shape, before the first Workspace
repository is created and shared, found four places where the code assumed one checkout on
one machine:

- Root discovery walked past a clone's top level: from a Target's clone inside a Workspace,
  the Workspace above was found and written to.
- A Manifest pointer could be absolute, or `..` its way out of the Workspace, so a committed
  Manifest could depend on one machine's layout and a clone could not resolve it.
- `validate` refused an `adapter.kind` or `connector.kind` no installed distribution
  registers, but `run --dry-run` and the preflight let the Adapter's probe stand in for the
  Connector and ran anyway, so the refusal happened only when the author remembered to
  validate.
- The customer names a Change record must never carry (`redaction.names`) lived in the
  committed Manifest, so the names to hide were themselves committed and cloned.

## Decision

1. **Root discovery stops at the enclosing git repository's top level.** The walk up from
   the current directory, and the walk `Workspace.holding` makes from a Run directory, look
   at each directory for `.agentdiag/` and stop after the first one holding a `.git` (a
   directory, or a worktree's file): that directory is still looked in, its parents never
   are. With no `.git` on the way up the walk reaches the filesystem root, as before, since a
   Workspace need not be a repository. `--root` names a Workspace from anywhere and is
   unchanged. When the walk stopped at a top level and a Workspace sits above it, the error
   names it and says `--root <outer>`; it never suggests `init` there, because a Workspace
   inside a Target's clone is what ADR-0013 §2 rules out. `init` uses the same walk for its
   nesting notice, so `init` and every other command agree on what is inside a Workspace.
2. **Every Manifest pointer is relative and resolves inside the Workspace root.** A pointer
   (`prompts.*`, `tools.*.schema`, `suites[].path`, `records`, `judge_notes`, `redaction`)
   that is absolute (a leading `/`, a drive letter, a UNC path) or that resolves outside the
   Workspace root is a `validate` error naming the pointer and where it leads, and the same
   problem refuses `run`, `run --dry-run` and `rescore` in the preflight. The check is
   lexical: separators normalised, the pointer joined to the Target directory's path under
   the root and normalised, never resolved through the filesystem, so a symlink or a NUL
   byte on one machine cannot change the answer and a committed pointer means the same in
   every clone. A pointer may name another Target's directory under the same root; it may
   not leave the root. The in-process `store` keeps its stricter rule (inside the Target
   directory).
3. **An unknown kind is refused wherever a Manifest is read to act on it.** `run`,
   `run --dry-run` and the preflight a `rescore` runs refuse a Manifest whose `adapter.kind`
   no installed distribution registers, and whose `connector.kind` none registers when that
   preflight reads through the Connector (the Sync check, or tool truth over HTTP; a
   `rescore` needs no Connector and is not refused for one), before credentials, the
   Adapter and the Sync plan, with character for character the problem `validate` prints
   (ADR-0014 §2's message, naming the installed kinds and the entry-point group a plugin
   registers under), alongside every other problem the pass finds. The Adapter's probe
   stands in for a Connector that cannot *read* (a missing credential, decision 25 of phase
   6); it never stands in for one that cannot *exist*.
4. **The names to redact are local, not committed.** They live in `redaction.yaml` beside
   the Manifest (`names: [...]`), which `init` writes as an empty starter (and `discover`,
   for a Target it creates) and whose line `init` adds to the Workspace `.gitignore`. The Manifest's `redaction` key is a pointer to
   that file, relative to the Target directory and `redaction.yaml` by default; a Manifest
   that still lists names inline is a `validate` error naming the move. A clone without the
   file redacts e-mail addresses and phone numbers as before and no names, and `validate`
   warns that the file is absent, so an author knows the list is theirs to supply; it warns
   too when the Workspace `.gitignore` lacks the line that keeps the file local. A pointer
   the author set that names no file, a file of the wrong shape, or a pointer §2 refuses is
   an error, and every command that writes a Change record refuses on it before it writes
   anything (a push reads the names before the Connector writes). No message ever quotes
   the file's contents: the file exists to keep them out of committed text.

## Considered options

- Bounding the walk at the Workspace's own `.gitignore` or a marker file instead of `.git`.
  Rejected: a git repository is the boundary teams already share and clone, and ADR-0014 §4
  made it the install shape.
- Refusing only absolute pointers and allowing `..`. Rejected: a pointer that leaves the root
  depends on the checkout layout exactly as an absolute one does.
- Keeping `redaction.names` in the Manifest and scrubbing it from the committed copy.
  Rejected: the Manifest is one file, committed as written, and a scrub step is one more
  thing a clone has to remember.
- A Workspace-wide redaction file. Deferred: the list is per Target today, where the Change
  records are; a shared list can join it as a second pointer when two Targets need one.

## Consequences

- `Workspace.find`, `Workspace.holding` and `init`'s nesting notice share one bounded walk;
  `find_repository` moves to `agentdiag.workspace`, the one definition of "enclosing
  repository".
- `manifest_report` gains the pointer rules before its existence checks; a pointer refused
  for leaving the root is not also reported missing, and `validate` never reads a Suite it
  refused; `preflight` renders the same problems beside the kind problems.
- `preflight` gains the kind check at its top, short-circuiting as the missing-Manifest case
  does.
- `TargetPaths.redaction`, the gitignore line, the starter file, and `redaction_names`
  reading the file; the Manifest model keeps loading an older `run.json` snapshot that
  carried names inline.
- The first shared Workspace repository (ADR-0013 §2) can be cloned by a colleague and used
  with `--root` or from inside it without reading a neighbouring checkout.
