# Test fixtures

Everything the suite replays or compares against lives here, and none of it is hand-edited.
Most of it is written by `scripts/record_fixtures.py`; the rest is written by the test that
reads it. This page says which is which, how to regenerate it, and why some recorded Judge
answers are the Judge's real ones and others are authored.

## The tree

| Path | Holds | Written by |
|------|-------|------------|
| `traces/cancel-processing-order.trace.jsonl` | The cancel Scenario's Trace | `tests/test_toy_target_trace.py` (an input to the script) |
| `traces/<scenario>.trace.jsonl` | One Trace per scripted Scenario of the example, the adaptive `cancel-without-the-order-number` included, and one per Scenario of `suites/simulated.yaml` | `record_fixtures.py --scripted` |
| `recordings/toy-cancel-target.jsonl` | The Target's three exchanges for the cancel Scenario, captured in Phase 4 | an input to the script, never written by it |
| `recordings/captured-judge-answers.jsonl` | The Judge's real answers to the natural requests, one line per request | `record_fixtures.py --capture` (an input to every other mode) |
| `recordings/judge-*.jsonl`, `recordings/diagnosis-*.jsonl`, `recordings/toy-cancel.jsonl` | `prompt_adherence` and the Diagnosis over the cancel Trace, and the whole cancel Trial `run --replay` takes; `judge-manifest-prompt` (the Trace with no system prompt, judged against a Manifest `system` path pointer) and `judge-suppressed` (one Suppression in force) are over Manifest variants (phase-6 decision 45) | `record_fixtures.py` (default mode) |
| `recordings/helpdesk.jsonl`, `traces/helpdesk/<scenario>.trace.jsonl`, `recordings/captured-helpdesk-target.jsonl` | The second toy's scoped recording over its generated Suite (the adaptive Scenario's Simulated User included) and over the rescore of the committed proxy rows imported under it (`imported-hd-0001`), its Traces, and the help desk's own captured model calls, keyed by Scenario and request | `record_fixtures.py --helpdesk` (after ticket 13's walkthrough; the calls by `--capture --helpdesk`) |
| `recordings/toy-orders.jsonl`, `recordings/<eval>-<behaviour>.jsonl` | The example Suite's scoped recording, and one exchange per judged Eval behaviour | `record_fixtures.py --scripted` |
| `suites/simulated.yaml`, `recordings/simulated_user_review-*.jsonl`, `recordings/stop_when-*.jsonl`, `recordings/simulate-*.jsonl` | The test-only Suite of worked Simulated User Trials, and one recording per behaviour: the reviewer's, the `judged` stop check's, and the ways a simulated conversation ends | `record_fixtures.py --scripted` |
| `recordings/runs-*.jsonl`, `runs/<run id>/` | The Manifest variants' recordings, and the Run directories `compare`, `rescore` and pass^k are tested over | `record_fixtures.py --runs` |
| `recordings/fake-*.jsonl`, `recordings/toy-cancel-shipped.jsonl` | Adapter-level exchanges for the conformance suite and the in-process Adapter | the tests that read them |
| `evidence/` | Evidence rows the Importers read (ticket 26): `helpdesk-proxy-rows.json` (the help desk's four proxy rows, two Turns each a question, a `search_articles` call, its tool result and the answer: `import --rows`, `--chat` over the fake Connector, the README), `helpdesk-proxy-edges.jsonl` (OpenAI-shaped rows with a `FlowRun` of `open_ticket` that joins its call and one of `send_survey` that joins nothing, a cache-hit duplicate, a truncated response, a 529 and a tool call no row answers), `helpdesk-conversation.json` (a conversation record with no times and an `operator` takeover) and `helpdesk-voice.json` (a voice transcript with a tool call and `duration_s`) | written by hand in decision 33's shapes, invented, no customer content; read by `test_importers.py` and `test_import_cli.py` |
| `complaints/shipped-order-refund.md` | A complaint carrying an e-mail address and a phone number, for the Change record (ticket 25): `change open --complaint`, the guide's "Record a fix as a Change record" | written by hand, invented (an `example.com` address, a 555 number), no customer content; an example input, read by no test |
| `changes/<id>.md` | One Change record per status the Flow view must tell (ticket 28): open from a Diagnosis, proposed with a change set, pushed through the Connector with a Push record path, verified and refuted with a verification and Observations, wontfix with its reason, superseded, and one imported entry (`20260918-fb-004`) whose Runs are `unknown`. They cite the Run fixtures in lifecycle order: the trigger's Trial and the baseline in `20260923T100000Z-base` (10:00), the record opened after it, the expectation stated before the verifying Run `20260923T100200Z-prmt` (10:02). The refuted record's Observations carry that Run's real verdicts; the verified record's outcome is invented (`validate` re-runs no compare). The pushed ones name `pushes/20260923T100130Z-local.json`, which `tests/change_fixtures.py` writes with the same Fingerprints | written by hand, invented, redaction-clean (a test asserts it); read through `tests/change_fixtures.py` by `test_flow_view.py` and `test_change_cli.py` |
| `dashboard/three-targets.txt` | `agentdiag dashboard` over three Targets built from committed files: the toy with every Run fixture, an imported copy of `20260923T100000Z-base` and the Change record fixtures; the example help desk and order desk (ticket 29) | written once from `render_dashboard` by `tests/test_dashboard.py`'s own fixture; regenerate only on purpose |
| `show/<run id>.txt`, `show/traces.txt` | `show`'s text over every Trial of every Run fixture and every committed Trace, at width 100, pinned before ticket 15 extended the view model | written once from `show` by `tests/test_show_pin.py`'s own functions; regenerate only on purpose |
| `manifests/http-echo.yaml`, `manifests/suites/http-echo.yaml` | The reference Manifest of an HTTP Target (phase-8 decision 11: `kind: http`, the `sse-json` Dialect, a `token` credential by variable name, the `header` identity mode) and its one-Scenario Suite; `base_url` is replaced by the port of the fake endpoint `tests/fakes/http_target.py` serves on 127.0.0.1 | written by hand; read by `test_http_adapter.py`, `test_http_tool_truth.py` and `test_readme.py`, whose "Drive a Target over HTTP" block must parse to the same `adapter` block |
| `suites/` (but `simulated.yaml`), `exports/`, `openinference/`, `otel_genai/` | Scenario schema cases, known-good exports, pinned attribute lists | the tests that read them |

## The four generating modes, in order

```bash
uv run python scripts/record_fixtures.py              # default: judge-*, diagnosis-*, toy-cancel
uv run python scripts/record_fixtures.py --scripted   # traces/, toy-orders, <eval>-<behaviour>, the simulated set
uv run python scripts/record_fixtures.py --runs       # runs-*, runs/
uv run python scripts/record_fixtures.py --capture    # all three, with the natural answers captured
uv run python scripts/record_fixtures.py --helpdesk   # helpdesk, traces/helpdesk/ (ticket 13)
```

The order matters: `--scripted` reads the `toy-cancel.jsonl` the default mode wrote, and
`--runs` replays the `toy-orders.jsonl` `--scripted` wrote. `--capture` runs the three in
that order in one process. None of the first three needs credentials, a network or a clock;
each is deterministic, and `tests/test_run_fixtures.py` regenerates all three into a scratch
directory and diffs them by the byte. (`--live`, the fifth mode,
records one exchange into `judge-live.jsonl` for a maintainer re-grounding one prompt; it is
not part of this tree.)

`--helpdesk` stands apart: it records the second toy from the Suite the ticket 13
walkthrough generated for it (`examples/workspace/.agentdiag/targets/help-desk/suites/
generated.yaml`) and refuses until that Suite exists. Both sides are natural and neither has
an authored story (the Scenarios were generated, not written against a known Verdict): the
help desk's model calls come from `captured-helpdesk-target.jsonl` and the Judge's answers
from `captured-judge-answers.jsonl`, and a call either lacks is refused, exit 1, naming
`--capture --helpdesk`, which makes the missing calls live through the login (a Trial's
Target calls captured whole) and a second run none.

## Captured and worked answers

A recording is `{"request": ..., "response": ...}` lines. The request is the key a replay
matches on, and it is always rendered by agentdiag itself. The response is one of two kinds.

**Natural answers are captured.** An authored answer is *natural* when the story it tells is
what the Judge is expected to say over that Trace: a pass over a Trace that passes, a fail
over one that breaks a rule, the Diagnosis of either. Those recordings carry the Judge's real
answer, captured once through the Claude Code login into `captured-judge-answers.jsonl`; the
authored answer, marked with `natural(body)` where it is defined in the script, stays as the
story the capture is checked against. The natural recordings are:

- `judge-manifest-prompt` and `judge-suppressed` (phase-6 decision 45): the same cancel
  Trial judged once with no prompt in its Trace, against the Manifest's `system` file, whose
  story is the rule-3 fail grounded on the Manifest and saying so, and once inside a
  Suppression's window, whose story is a pass naming `sup-refund-timing`. Until
  `--capture` has captured them they carry their authored bodies, the default mode exits 1,
  and their tests in `test_manifest_prompt_and_suppressions.py` skip;
- `judge-fail`, `diagnosis-ok` and `toy-cancel` (the cancel Trial's fail on rule 3 and its
  Diagnosis: the Target's closing reply invents a refund window the cancel tool never
  returned; "ok" names the Diagnosis's behaviour, a well-formed answer, not the Verdict it
  explains);
- `goal-pass`, `data_grounding-pass`, `data_query-pass`, `tool_choice-pass` and
  `tool_choice-no-tool-called`;
- `toy-orders`: every Judge line in it, each judged Scenario's Evals and Diagnosis, the
  guardrails fail on `no-refund-timing` included (that Trace does promise a refund date);
  and, since ticket 06, the adaptive `cancel-without-the-order-number`'s three natural
  lines: the Simulated User's message (asked for the order number, it gives `NB-1042`), the
  `goal` pass and the Diagnosis. The Simulated User's message is agentdiag's own model call,
  so it is natural by the same definition — what the model is expected to say — and it has
  no Verdict, so the story check skips it. The Target's replies around it are scripted per
  call and read nothing of its wording, so a captured message worded otherwise changes the
  Target's request bodies in the recording and nothing else;
- `runs-model-swap` and `runs-prompt-change`, and the Run fixtures replayed from them.

**Worked answers are the fixture.** The recordings that exercise a code path — a fail over a
passing Trace, a pass over a failing one, no evidence, a schema failure, a refusal, an
omitted rule, an unsupported claim, the other Judge failing the goal — are fixtures of code
paths, not of model behaviour. Worked is correct for them, and they are never re-captured: a
captured refusal is hard to obtain on demand, and a captured schema failure is a bug you have
to wait for, but both are behaviours agentdiag must handle every time. They are every
`-fail` recording but `judge-fail`, and every `-no-evidence`, `-unknown-evidence`,
`-schema-failure`, `-refusal`, `-omits-a-rule` and `-pass-beside-an-unsupported-claim` one;
`guardrails-pass`, whose request is the one `toy-orders` asks over a Trace whose natural
story is a fail, so an answer where every rule held is a code path; `judge-pass`, for the
same reason: the code path for a pass whose cited Spans exist, over the cancel Trace whose
natural story is the fail; and `runs-judge-swap`, the other Judge (`claude-opus-5-5`)
failing the goal the default Judge passed, which exists to exercise `compare`'s Judge
statement. The whole simulated set over `suites/simulated.yaml` is worked too — the
reviewer's `simulated_user_review-leak` (`invalid` / `helped`), `-premature-stop`
(`invalid` / `hindered`), `-none`, `-cites-nothing` and `-schema-failure`; the `judged`
stop check's `stop_when-judged` (`holds: false`, then `true`) and `stop_when-judged-failure`;
and `simulate-stop-token`, `simulate-max-turns`, `simulate-target-says-any` and
`simulate-refusal` — because a leaked fact, a premature stop, a refusal and a check that
fails are code paths a live model cannot be asked for. The `not_supported` sampling resend
has no recording: a replay cannot return a 400, so its test refuses the call in a fake
client. Do not "fix" a worked recording by capturing it.

Tests over a natural recording assert its story — the Verdict, that every cited id is a Span
of the Trace, that the rationale is not empty — and never its wording, so a re-capture does
not touch a test. Tests over a worked recording assert its wording, because the wording is
the fixture.

## The captured file

One line per distinct request, matched by `replay.canonical(request)`; a request appears
once, and the script refuses to append a duplicate:

```json
{"request": {"model": "claude-opus-5", "...": "..."},
 "response": {"id": "msg_...", "content": ["..."], "structured_output": {"...": "..."}},
 "captured": {"at": "2026-09-24T12:00:00Z",
              "backend": {"kind": "claude_code", "cli_version": "2.1.280"},
              "judge_fingerprint": "<sha256>"}}
```

`backend` is the Backend that made the call, with its CLI version as the Backend records it.
`judge_fingerprint` is the live Judge's Fingerprint (Backend `claude_code` at that CLI
version), which is not the `replay` one the replayed Runs record: the two differ by design
(phase-5 decision 25). The Backend is not in the request, so nothing else moves with it.

## When to re-capture

Re-capture when a natural request changes: a prompt's wording or `PROMPT_VERSION`, an output
schema, the rendering of the Trace, a Scenario, the calibration notes, or the Judge's model
or effort. Run the three modes first. Each prints a warning naming every natural recording
whose request is not in the captured file, renders it from its authored story so everything
is still written, and exits 1. Then run `--capture` (it needs credentials and spends
team-plan quota): it makes only the calls whose requests are missing, one at a time, prints
one line per call (`captured <n> for <recording> (<scenario>): <verdict(s)>, <seconds> s`),
and appends each answer. The old lines for requests that no longer occur can be deleted by
hand before the capture; nothing reads them.

## When the Judge is right

A recording is named for what the Judge says over the Trace. When a capture shows the Judge
is right and the authored story wrong, the story moves, not the Judge: the recording is
renamed for the Verdict, the authored answer becomes a worked fixture if a code path still
needs it, and the tests follow the story. The precedent is the swap of 2026-09-24 (phase-5
decision 43): the Judge failed the cancel Trace on rule 3 where the authored story said
pass, so `judge-fail` became the natural recording, carrying the captured fail, the authored
pass stayed as the worked `judge-pass`, the old worked rule-4 fail was deleted, and
`toy-cancel` now replays to exit 1 by design. `tests/stories.py` tells that story once for
the tests that depend on it.

## The story check

Every natural answer, read from the file or just captured, is compared with its authored
story. A Verdict that differs is printed as `story differs: <recording>: authored <a>,
captured <b>` (per rule for `guardrails`); nothing is re-authored. That is a finding for the
author, and the tests that assert the story are what break.
