---
status: accepted
date: 2026-09-27
---

# 0011 — The Adapter converses, the Connector manages, and a Target changes only through an explicit push

Proposed on 2026-09-25; the push rules (§4 to §8) went through one review round of nineteen
questions, and the author accepted every recommendation on 2026-09-27. Three answers added text: §1 (model and tier are
read-only), §6b (every Restore point is kept) and §6d (`init` marks prod-class environments
`protected`).

## Context

The kit must sync the prompt files, tool JSON files and manifests to the local repository, or
to the online agent config, and back the other way, and must hold all the information on an
agent. A production agent-maintenance repository studied during design does this today by hand and by procedure: `character_cli pull |
diff | deploy | restore`, a dry-run-by-default deploy that snapshots the live record before a
full-replace PUT, and a string-matching hook that asks before staging and prod writes.
Its evidence for judging a
production chat comes from proxy rows, conversation records and voice conversations read
through management APIs and a SELECT-only database session.

agentdiag forbade all of it. ADR-0001 §1 let only the Adapter touch a Target and the Adapter
only converses (`open / deliver / rebind / close`, `observes=[]` always); §5 said agentdiag
never modifies a Target's code or configuration. So a platform-hosted Target, whose prompt is a
database record behind an API rather than a file or a blob on the wire, could be neither
fingerprinted (ADR-0007 §1 covers "what the Adapter can observe", which for it is nothing) nor
brought back into Sync. The HTTP Adapter's design already smuggled one non-conversational
read in as the "proxy-trace source". A study of agentdiag and the reference repository tested
the hypothesis against both code bases and confirmed it: the live side of a Target needs its own component, with different
credentials, different side-effect classes and no session lifecycle.

## Decision

1. **Two components touch a Target, each in one way.** The **Adapter** converses: it opens a
   session, delivers Turns, applies Fixtures and returns responses, as ADR-0001 says, and it
   may observe what passes through that conversation (the system prompt at session open,
   ADR-0007 §1). The **Connector** manages: it reads a Target's deployed set (prompt sections,
   tool schemas, Flow definitions and their state, the resolved model and tier) and its
   Evidence stores (proxy rows, conversation records, voice conversations, Flow runs), and it
   writes the deployed set only through a push. A push writes prompt sections, tool schemas
   and Flow definitions; the resolved model and tier are read-only Fingerprint sections in
   v1, set on the platform. Nothing else reaches a Target; only these two
   import Target code. ADR-0001 §1 is narrowed to that and §5 is replaced by rule 7 below.
2. **A Connector declares every operation.** Each operation is `read` or `write`, carries a
   side-effect class (`none`, `sandboxed`, `live`, as ADR-0001 §5 names them) and names the
   Fingerprint sections it can observe. A Connector for a platform is a plugin, never core
   (ADR-0014); core ships the protocol, an in-process Connector for the toy Targets and a fake
   for tests. A Connector resolves environment identifiers and credential *names* from the
   Manifest's `environments` block, reads the credentials themselves from the process
   environment, and fails closed on a missing one: it never falls back to another
   environment's (the reference repository's `eu_prod → dev` trap).
3. **Fingerprint coverage widens to what the Adapter or the Connector can observe.** ADR-0007
   §1 gains a one-line pointer here and is otherwise unchanged. A section neither can observe is
   still recorded as `not_covered` with a reason.
4. **Sync compares three states per section**: the local file the Manifest points at, the
   deployed set read through the Connector, and the last recorded Fingerprint. It names a
   direction per section: `identical`, `local_ahead`, `deployed_ahead`, `diverged` or
   `not_covered`. The Run-level Sync status stays ADR-0005 §3's: `held` when every covered
   section is `identical`, `broken` when any is `local_ahead`, `deployed_ahead` or `diverged`,
   `not_checked` as before; a break names the sections and their directions. ADR-0007 §2
   stands: a break labels and never invalidates a prior Run; ADR-0008's re-sync on `broken`
   stands and rebuilds from the Connector's read where it covers.
5. **`pull` writes the deployed set into the local files** the Manifest points at, section by
   section, and shows the resulting git diff. It never commits. It refuses to overwrite a pointed
   file with uncommitted changes unless told to, and it refuses a pointer outside the Workspace
   root (ADR-0013 §2): such a section is compared but never written, and `pull` says so.
6. **`push` writes the local files to the deployed set, and only under all of these:**
   a. a preview: the rendered diff of exactly the bytes that will change, computed against a
      Connector read of the deployed set taken at that moment (never a `pull`), and the push
      carries the deployed Fingerprint it saw, so a deployed set that moved between preview and
      write is refused (compare-and-swap);
   b. a **Restore point**: the deployed set as read before the write, saved under the Workspace
      and named in the Push record, so `push --restore <point>` (itself a push under these
      rules) can put it back; every Restore point is kept until retention (D36) is decided;
   c. an explicit request: `push` is a preview until `--push` is given;
   d. a confirmation decided by the effective side-effect class, the higher of the operation's
      and the environment's: `sandboxed` and unprotected `live` push on `--push`, and the Push
      record says which; a `live` environment the Manifest marks `protected` requires a person
      to type the environment's name at the prompt, on a terminal or in the UI's confirm step;
      there is no flag that stands in for the typed name, so a coding agent hands a protected
      push to a person; `init` marks any environment named `prod`, `staging` or `eu_prod`
      `protected` by default and the Manifest may mark others. This replaces the reference repository's
      string-matching hook, which `--base-url` could bypass;
   e. a **Change record** (ADR-0012) reference, required for a `protected` environment and
      `none` allowed elsewhere;
   f. a refusal when Sync reads `deployed_ahead` or `diverged` for any section the push would
      write: the operator pulls, merges and pushes again; there is no force flag in v1;
   g. afterwards, a Fingerprint rebuilt from the Connector's read and recorded with
      `pushed_from: <old fingerprint>`; `compare` treats the change as an undeclared variation
      (ADR-0005 §6) unless declared.
7. **agentdiag never modifies a Target implicitly.** Only a Connector push does, only under
   rule 6, and only because a person or an agent asked for that push. There is no loop that
   pushes on its own; `watch` opens a **Sync break** and may launch a Run, never a
   push. The reference repository's lesson "profile, do not self-heal" survives as this rule; its
   "never deploy" rule does not, because the kit must push.
8. **Every push is recorded as a Push record**, a file under `targets/<slug>/pushes/`,
   committed: environment, when, who (the operating-system user and the agent name when an
   agent session asked), the effective side-effect class, the Fingerprints before and after,
   the Restore point, the diff's hash and the Change record. The Change record's own push event
   points at it.

## Considered options

- Extend the Adapter with config and log operations. Rejected: different credentials (a
  management token against a chat endpoint's), different side-effect classes (a PUT is a write;
  database reads are read-only by law), no session, and it must work with no Turn running
  (Sync before a Run, a Fingerprint of a Target nobody is talking to).
- Keep detect-only Sync and leave the push to the platform's own tools. Rejected: the kit must sync
  both ways, and a Sync that can only ever say `not_covered` for platform-hosted Targets is
  no Sync.
- A force flag on push. Rejected for v1: the reference repository's FB-055 shows drift in both directions at
  once; a merge is a person's job and a pull makes it visible.
- A flag in place of the typed environment name, so an agent can push to a protected
  environment. Rejected: the requirement is the agent in the loop, not the agent alone at the
  production boundary; the UI is the person's typed confirmation.
- An autonomous push when a Run passes. Rejected: the reference repository's retired `self_heal/`; the
  Change record keeps the loop explicit.

## Consequences

- ADR-0001 §1 narrows and §5 is replaced; ADR-0007 §1 and ADR-0005 §1 gain pointers. No prior
  Run's Fingerprint is rewritten.
- The Manifest gains `connector` (kind, per-environment identifiers and credential
  prefixes, Evidence stores), `protected` and a side-effect class per environment, `channel`
  and `family`.
- The out-of-scope line changes from "modifying a Target, deploying prompts, or any
  self-healing loop" to forbidding only the autonomous loop.
- The HTTP Adapter's proxy-trace source becomes a Connector read fed to the proxy-row Importer
  (ADR-0013 §5); discovery gains `--from-connector`; a tool Eval on a Target whose
  stream carries no tool frames is `unverifiable / fidelity_too_low` unless a Connector
  Evidence store supplies the rows, and preflight warns when none is declared.
- The Restore point is a copy of the deployed set, gitignored under the Workspace, and never a
  Fingerprint.
