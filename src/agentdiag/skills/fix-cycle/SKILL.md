---
name: agentdiag-fix-cycle
description: Take one failure of a Target from its trigger to a verified or refuted Change record, through agentdiag's Change record, push and compare.
disable-model-invocation: true
---

# One fix cycle

Take one failure of a Target — a Diagnosis in a Run, or a complaint about a real conversation — through one **cycle**: trigger → evidence → Diagnosis → Change record → fix → push → verifying Run → compare → close. The **Change record** (`<target dir>/changes/<id>.md`) is the cycle's spine: every step either writes into it through `agentdiag change` or is checked against it, and the cycle ends only when `compare` closes it `verified` or `refuted`. Every step is marked **code** (run it), **judgement** (decide it, and say why in the record's `## Notes`) or **human** (hand it to the person and wait for their answer).

`agentdiag <command> --help` is the reference for every flag; this skill names only the ones each step needs. Every command takes `--root <workspace> --target <slug>`; they are left out below.

## Budgets

- **Two evidence commands.** The evidence for the Diagnosis is what two commands print: the one that makes or brings in the Trace (`run --scenario`, or `import`) and `show`. A third means the trigger is not yet specific: go back to step 1.
- **One read bundle.** Read the Target once, as one bundle, before you edit: `target show` (the section ids) and every file the sections you will touch live in (`prompts/<name>.md`, `tools/<name>.json`), in full.
- **Diff first.** Before a command that writes anything outside the Change record — `change propose`, `push --push`, a commit — print the diff it will carry (`git diff`, the `push` preview) and read it.

## Steps

1. **judgement** — Name the trigger: a Trial (`<run>/<scenario>/<trial>`, from `agentdiag list` and the Run summary) whose Scores or Diagnosis show the failure, or a complaint (a file holding the customer's words, and the conversation's id when there is one). Done when you hold exactly one of the two, and one sentence saying what went wrong.
2. **code** — Evidence, two commands. A Trial: `agentdiag show <run> <scenario> --trial <n>` (with `agentdiag run --scenario <id>` first when no Run holds it). A complaint about a conversation the Connector's Evidence stores hold: `agentdiag import --chat <id> --env <env>` (or `--conversation`, `--voice`), then `agentdiag show <imported run> <scenario>`. Done when `show` has printed the Turns where it went wrong and, for a Trial, its Diagnosis; for a complaint whose conversation no store holds, when the complaint's own words are the evidence.
3. **judgement** — The Diagnosis. Read `agentdiag target show` and the pointed files (the one read bundle). Name the **layer** (`persona`, `rules`, `memory`, `checklist`, `flow`, `runtime`, `data`), the section ids the fix will touch (`prompt.system#rules`, `tool.escalate`) and the cause in one sentence that cites a Span `show` printed, or, with only a complaint, quotes the complaint and the section text that let it happen. Done when all three are written down; a cause that cites neither is a guess: go back to step 2.
4. **code** — `agentdiag change open --from <run>/<scenario>/<trial> --layer <layer> --title "<what went wrong>"`, or `--complaint <file>` in place of `--from`. Customer identifiers are redacted on write. Done when it prints `opened <id>`; `<id>` names the record from here on.
5. **judgement** — The expected effect, stated before any verifying Run: the Scenarios the fix should improve (each one failing today) and the ones that must not move (a Scenario of each other rule the edited section holds, and one of an unrelated rule), ids from the Target's runnable Suites. A failure no Scenario catches cannot be verified: write the Scenario that does first (the `agentdiag-generate` skill), then come back. `agentdiag change expect <id> --should-move <ids> --must-not-move <ids>`. Done when it prints `expects, stated at …`.
6. **code** — The pre-change Run, before the edit: `agentdiag run --env <env> --scenario <id>` for every Scenario of step 5 (repeat `--scenario`), where `<env>` is the Adapter environment the fix will be pushed to in step 9 and verified on in step 11 (the Manifest's `adapter.environments`; a protected `staging` when it has one, else the default). Both Runs open the same one, or `compare` names `adapter.environment` as an undeclared difference. A Diagnosis trigger's own Run serves when it already holds all of them and ran on `<env>`. Done when the Run id is written down and each should-move Scenario fails in it; one that passes is not the failure: go back to step 5.
7. **judgement** — The fix: edit the pointed file, inside the sections step 3 named and nowhere else. Then `git diff -- <file>` (diff first). Done when the diff changes only those sections and each changed line serves the cause.
8. **code** — `agentdiag change propose <id> --section <section id> --file <path under the Target directory>`, each repeated per section and file. Done when it prints `<id> proposed`.
9. **code** — The push preview: `agentdiag push --env <env>`, the environment of step 6. Read it (diff first): the sections and diffs must be exactly step 7's. Then, by what the preview says:
   - **`is not protected: --push writes it`** — `agentdiag push --env <env> --push --change <id>`. Done when it prints `Push record …` and `Change record … gained a push event`.
   - **`is protected`** — go to step 10.
   - **`refused: the deployed side moved on a section this push would write`** — someone edited the deployed side meanwhile. Commit your fix first (`git commit` of the pointed file: `pull` skips a file with uncommitted changes), then `agentdiag pull --env <env>`, merge the pulled section with your fix in the file (the diff `pull` prints shows what it replaced; your fix is in the last commit), commit the merge, and repeat from step 9.
   - **`refused: … that Fingerprint was recorded against '<other>'`** — the Target's one Fingerprint belongs to the environment last synced or pushed: `agentdiag sync --env <env>`, then preview again.
   - **`refused: the Manifest names no Connector: nothing reads or writes the deployed set`** — there is no push: the next Run re-syncs onto the edited files and moves the record to `pushed` itself (a `local` push event). Go to step 11.
10. **human** — A protected environment needs its name typed by a person at a terminal (or in the UI's confirm step); no flag stands in for it, and a session without a terminal is refused. Give the person the exact command, `agentdiag push --root <workspace> --target <slug> --env <env> --push --change <id>`, and the preview's diff, and wait. Done when the person reports the `Push record …` line, or declines (then close the record `--wontfix --why` and stop).
11. **code** — The verifying Run: `agentdiag run --env <env> --scenario …`, the same command as step 6 on the environment step 9 pushed to; `change close` refuses a verifying Run on any other. Done when it prints the Run id; its summary's Sync reads `held` after a push, or names the re-sync after a Target with no Connector.
12. **code** — `agentdiag compare <step-6 run> <step-11 run> --expect fingerprint --expect sync`: the push is the declared variation. Read the deltas of the step-5 Scenarios. Any other undeclared difference means the two Runs are not comparable: find which path, and make a new Run without it. Done when the comparison's last line counts no `undeclared difference`.
13. **code** — `agentdiag change close <id> --verified --run <step-11 run> --baseline <step-6 run>` when every should-move Scenario improved and no must-not-move one moved, else `--refuted`. The gate re-runs the comparison and refuses a close the Scores do not support, naming why. A replayed Run cannot verify a prompt change: the recording matches on the request body, and the edited prompt no longer matches it, so every judged Score is `invalid`. The verifying Run is live (through the login) or freshly recorded against the pushed prompt. The gate refuses a comparison that decides nothing for a Scenario the expectation names (its Scores all `invalid`, `unverifiable` or `incomplete`), for `--verified` and `--refuted` alike: make a Run whose Scores decide, and never close `--refuted` on one that does not. Done when `change show <id>` prints its `status` line as `verified` or `refuted`.
14. **code** — Commit the edited files, the Change record, the Push record (`pushes/`) and `fingerprint.json` together (diff first: `git status`, `git diff --cached`). A refuted record is a finding: open a new cycle from step 1 with it as the trigger.

## Done when

1. `agentdiag change show <id>` prints its `status` line as `verified` or `refuted`, with a push event and a verification;
2. the fix's diff touches only the sections the record's change names;
3. the edited files, the record, the Push record and the Fingerprint are in one commit.
