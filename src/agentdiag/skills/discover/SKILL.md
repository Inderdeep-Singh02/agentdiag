---
name: agentdiag-discover
description: Draft, review and install the agentdiag Manifest of a Target this Workspace has never described.
disable-model-invocation: true
---

# Discover a Target

Turn an unknown agentic system into a Target agentdiag can run: a Manifest of **pointers** to its prompts, tools, data sources, Evidence stores, entry points and Adapter configuration, a Fingerprint from `agentdiag sync`, and a line in the Registry. `agentdiag discover` does the mechanical reading and writes a draft; your work is the review. Every step is marked **code** (run it), **judgement** (decide it and say why in the Manifest's comments) or **human** (hand it to the person and wait for their answer).

`agentdiag <command> --help` is the reference for every flag; this skill names only the ones each step needs.

## Which path

- The Target's prompts and tools live in a **repository** (source files, string constants, JSON schemas): one pass, `--scan <repo>`, then steps 2 to 8.
- They live on a **platform** (records a Connector reads, not files): `--from-connector --env <name>`, which reads through the `connector` block of the Target's `manifest.yaml`. With no such block yet, run the "both" loop below.
- **Both** — the repository holds the Adapter, the platform holds the prompt and the tools. Two passes, each a full review:
  1. step 1 with `--scan <repo>` → step 2 (review) → step 3 (credentials, once) → step 4 (Evidence stores) → step 5 (validate) → `mv` the draft to `manifest.yaml`. The Manifest now names the Connector.
  2. step 1 with `--scan <repo> --from-connector --env <name>` → step 2 again: the second draft is your `manifest.yaml` line for line, comments included, with only new lines inserted and a REVIEW comment above each value the read would change → step 5 → `mv` → step 6 (`sync`, then `sync --check`) → step 7 (the table) → step 8 (the Registry).

`<repo>` is the directory holding the Target's code: the whole repository when the Target is the repository, and the Target's package directory (`src/my_agent/`) when it is one package inside a larger one, so the scan reads the Target and nothing beside it.

## Steps

1. **code** — Pick the Target's slug (lowercase words joined by `-`, e.g. `help-desk`) and draft:
   `agentdiag discover --root <workspace> --target <slug> --scan <repo>` (and/or `--from-connector --env <name>`).
   Done when it exits 0 and prints the draft's path and its count of REVIEW lines. Exit 3 prints why: fix that and rerun.
2. **judgement** — Open the draft and settle every `# REVIEW:` line, in file order. For each: read the code or record the comment cites, then either accept the line (delete the comment) or rewrite it (and delete the comment). A comment ending `proposed: <dotted.key.path>: <value>` means: to accept, write that value at that key (the line below the comment, or inside the one-line mapping there) and delete the comment. Decide in particular:
   - **the Target's identity**: `target.name` is the slug, unless the Target's owners already call it something else (a Report and a comparison print it); `target.description` is one sentence saying what the Target does, never `init`'s placeholder;
   - **each prompt's pointer**: a path when a file on disk is the truth, and a path to the saved copy (`prompts/<name>.md`) when the prompt is a platform record `--from-connector` read. `observed` only when nothing on disk is the truth **and** no Connector reads it (a string the code builds). The path wins over `observed` whenever the Connector reads the prompt: the saved file is then the local side of the Sync and the Connector's read the deployed side, so `sync` can say which one moved (`local_ahead`, `deployed_ahead`, `diverged`), and a pull or a push has a file to write; with `observed` there is only one side. Tool schemas the same way: `schema: tools/<name>.json`;
   - **each tool's `kind`**: `retrieval` for a lookup, `action` for anything that changes state, whatever its name suggests;
   - **every `side_effects` class**, one of `none` (nothing outside this process changes), `sandboxed` (it writes only to a test system), `live` (real users or data; a Run is refused without a flag): the Adapter's, each environment's, and each tool's own (`tools.<name>.side_effects`) when one tool does more than the rest;
   - **`family` and `channel`**: run `agentdiag registry --root <workspace>`; when another Target serves the same persona on another channel (chat, voice), write its `family` here and this Target's `channel`, else delete both commented lines;
   - **Evidence stores**: where the platform keeps what happened (proxy rows, conversation records, voice conversations, Flow runs), under `connector.evidence`, one key per store kind (`proxy`, `conversation`, `voice`, `flows`), each a mapping; the in-process Connector reads `rows: module:attr` naming a list of rows or a mapping of them by store kind, as in `evidence: {proxy: {rows: my_agent.platform:EVIDENCE}}`;
   - **which environments are `protected`**: mark every one that reaches real users.
   A line the scan could not fill is written commented out under its REVIEW (`# factory: …`): fill it in or say in a comment why the Target has none.
   Done when `grep -n 'REVIEW:' <draft>` prints nothing.
3. **human** — When a Connector environment has a `credentials` entry, list for the person the environment variables to export, one per entry, as `export <VAR>=…` lines naming what each holds; write the variable's NAME into the Manifest and let the person set its value in their own shell. With no `credentials` entry anywhere (an in-process Connector, a local platform), ask the person one question — "does any environment of this Target reach a hosted platform that needs a key?" — and go on when the answer is no. Record the question and the answer as a comment above the `connector` block (`# Asked 2026-09-28: no environment reaches a hosted platform; no credentials.`). Done when the person confirms the variables are set in the shell that will run `sync`, or that none are needed, and the comment says so.
4. **judgement** — Look for the Target's Evidence stores if step 2 found none: a proxy log, a conversation table, a voice platform's transcripts. Name each one the platform has (step 2's shape), or say in a comment that it keeps none. Done when `connector.evidence` names every store or the comment says why not.
5. **code** — `agentdiag validate --root <workspace> --target <slug> --manifest <draft>` until the first "Done when" item holds; each error names the key and what is wrong, and the allowed values where there is a closed set.
6. **code** — `mv <draft> <target dir>/manifest.yaml` (the paths `discover` printed), then `agentdiag sync --root <workspace> --target <slug>`, then `agentdiag sync --root <workspace> --target <slug> --check`. The first `sync` of a Target, after `--from-connector` or not, shows every section `deployed_ahead added`: no Fingerprint existed to compare against, and it writes the first one; the `sync --check` after it is the check that must hold. The loop's exit test is the check's exit code: 0 is `held`; 2 is `broken`, run `sync` again (the table says what moved); 3 is `not_checked`, the table's reasons say what to fix (step 2), and `sync` writes no Fingerprint that covers nothing. Done when `sync --check` exits 0.
7. **judgement** — Read `sync`'s table. For each `not_covered` section whose reason you accept, add a comment above its pointer in the Manifest saying why nothing observes it; a reason you do not accept is a pointer to fix (step 2) and a `sync` to rerun. Done when the third "Done when" item holds.
8. **code** — `agentdiag registry --write --root <workspace>`. Done when its table has a line for the slug, naming the environments, the Connector and the Suites the Manifest declares (a Suite that does not run is marked `(draft)` or `(retired)`).

## Manifest authoring rules

- **Pointers, not content.** A prompt, a tool schema or a data source is named by a path, `module:attr`, `observed` or an identity string; its text stays in the file or record the pointer names.
- **One home per fact.** A fact the code or the platform already holds is pointed at where it lives: the model comes from the code or the read, forbidden phrases live in `forbidden_phrases` only, a flag's meaning lives in `--help`. A copy `--from-connector` saved is not a second home: it is the local side of the same fact, which `sync` keeps in step with the deployed side.
- **`local_only: true`** marks a pointer into gitignored or machine-local data, so a fresh checkout warns instead of failing.
- **No secrets.** A credential is the name of an environment variable; a URL or DSN identity is its scheme, host and path, with no user, password, query or token.
- **Paths are relative to the Target directory** (`.agentdiag/targets/<slug>/`), including a pointer back into the repository (`../../../agent/prompts/system.md`).

## Done when

1. `agentdiag validate --manifest <draft>` prints `0 errors`, and the draft holds no `REVIEW:` line;
2. the draft is now `manifest.yaml`;
3. `agentdiag sync --check` exits 0, every section covered (`local`, `adapter` or `connector`) or its `not_covered` reason accepted in a comment;
4. `agentdiag registry` lists the Target.
