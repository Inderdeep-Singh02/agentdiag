# agentdiag

A profiler and evaluator for agentic systems, and a kit for maintaining them. It drives a
Target through Scenarios, records everything that happened as a Trace, judges each Trace with
Evals, compares Runs, keeps a Target's local definition and deployed set in Sync, and records
each fix as a Change record. Every file, function, table and CLI command in this repo uses the
words below. Rules and schemas live in `docs/adr/`; this file only names things.

## Language

### The system under test

**Target**:
An agentic system under test, as one invocable thing: its prompts, tools, data sources and
entry points. The definition, not a deployment of it.
_Avoid_: agent (the Judge and the Simulated User are also agents), SUT, bot, app, system

**Manifest**:
The Target's identity document: pointers, not copies, to its prompts, tools, data sources,
entry points and Adapter configuration.
_Avoid_: agent manifest, run manifest, hash manifest, registry, config

**Fingerprint**:
The recorded hashes of a configuration taken at one moment: the Target's deployed set as the
Manifest names it, or the Judge's or Simulated User's configuration.
_Avoid_: hash manifest, snapshot, checksum, version

**Sync**:
The section-by-section comparison of a Target's local files, its deployed set and its last
Fingerprint, naming which of the three is ahead (ADR-0011).
_Avoid_: staleness check, drift, freshness, version check, deploy (that is a push)

**Adapter**:
The component that converses with a Target on agentdiag's behalf: opens a session, delivers
Turns, returns responses. It never writes the Target's deployed set.
_Avoid_: driver, runner, client, harness, connector (the management side)

**Connector**:
The component that manages a Target's live side, its deployed set and its Evidence stores,
through the platform's own management interfaces (ADR-0011).
_Avoid_: adapter (the conversing side), platform client, backend, integration, API wrapper

**Evidence store**:
A store on a Target's live side holding what happened in its deployments: proxy rows,
conversation records, voice conversations, Flow runs.
_Avoid_: log, observability, telemetry, database (the mechanism)

**Dialect**:
A platform's framing of a streamed response, which an Adapter parses into Events.
_Avoid_: protocol, format, SSE (one transport), parser

**Flow**:
An automation a Target's platform runs when a tool is called: its steps, edges and state, read
through the Connector.
_Avoid_: workflow, pipeline, automation (the platform's word), tool (what calls it)

**Push record**:
The committed record of one push: environment, when, who, the Fingerprints before and after,
the Restore point and the Change record.
_Avoid_: deploy log, audit trail, push event (the Change record's pointer to it)

**Sync break**:
A recorded finding that a Target's local files, its deployed set or both differ from its last
Fingerprint, naming the sections and their directions (ADR-0011 §4); opened by `sync` or
`watch`, never by a Run alone, and open while the Fingerprint it was found against is in force.
_Avoid_: drift, alert, incident, violation

**Restore point**:
The deployed set as the Connector read it immediately before a push, kept so the push can be
undone.
_Avoid_: snapshot, backup, before-copy, rollback (the act, not the thing)

### The kit

**Workspace**:
One directory tree holding many Targets, each with its own Manifest, Suites, Change records
and Runs; a tree with one Target is still a Workspace (ADR-0013).
_Avoid_: project, repo, root (the path, not the thing), agents directory

**Family**:
The Targets that share one persona across channels, such as the chat build and the voice
build of one deployed agent, named in each Manifest.
_Avoid_: group, agent (the persona is one Target per channel), parent, cluster

**Registry**:
The list of a Workspace's Targets with their families, environments and Suites, derived from
the Manifests and never authored.
_Avoid_: catalogue, inventory, manifest, index (the SQLite file), agent table

**Change record**:
One recorded fix for a Target: its trigger, the Diagnosis it answers, the change made, each
push of it, the effect expected before verification, and the verifying Runs (ADR-0012).
_Avoid_: history entry, feedback, ticket, incident, fix log, post-mortem, HISTORY

**Importer**:
The component that turns an evidence store the Connector reads (proxy rows, conversation
records, a voice conversation) into a Trace at the Fidelity the evidence supports (ADR-0013).
_Avoid_: ingester, loader, receiver, ETL, trace source

**Dashboard**:
The Workspace-wide view of every Target's Sync state, last Run and Score trend, read from the
Registry and the Index; never a source of truth.
_Avoid_: overview, home, summary, scorecard (per Run), report (per Run)

**Flow view**:
A rendering of one Change record as a story (what happened, the problem, the fix, the expected
effect, what was observed), or of one Flow's definition; never a store.
_Avoid_: diagram, story view, timeline, handoff package

**Eval parameters**:
Per-Target data an Eval reads from the Manifest, such as forbidden phrases, required tool
argument types or latency thresholds; never prose for the Judge.
_Avoid_: config, thresholds (one kind), rules, standard

**Suppression**:
A per-Target instruction with an id and an effective-date window that tells the Judge a known
false fail to disregard for Traces inside the window; part of the Judge's Fingerprint.
_Avoid_: exception, waiver, known issue, false-fail list, ignore rule

### Tests

**Scenario**:
One test definition in the standard schema: the Turns the user produces, the Fixtures that
must exist, and the Evals that judge the result.
_Avoid_: test, test case, case, task, snapshot

**Suite**:
A named, authored collection of Scenarios for a Target.
_Avoid_: test set, dataset, collection, battery

**Fixture**:
Data a Scenario needs to exist before it runs, such as an identity or seeded records.
_Avoid_: facts pool, seed, identity, test data, setup

**Turn**:
One user message and the Target's complete response to it.
_Avoid_: step, round, exchange, message

**Simulated User**:
The configured component that plays the user in an adaptive Scenario.
_Avoid_: driver, driving session, simulator, persona (its character is one input), user proxy

**Provenance**:
Where a Scenario came from, recorded on it: a prompt section, a tool, a Trace or a Change
record. Never how a Span was obtained (that is Fidelity).
_Avoid_: source (a Run's is `run` or `imported`), origin (a Span's), derived-from, lineage

### Recording

**Trace**:
The complete recording of one Trial as an append-only sequence of Events.
_Avoid_: log, transcript (only the messages), recording, session

**Event**:
One line in a Trace: something that happened at an instant, such as a message sent, a tool
result received, or a Span opening or closing.
_Avoid_: entry, record, row, log line

**Span**:
One timed unit inside a Trace, derived from its opening and closing Events.
_Avoid_: step, call, node, observation

**Fidelity**:
How a Span was obtained: `instrumented`, `reconstructed` or `observed` (ADR-0001).
_Avoid_: provenance, confidence, quality, source

**Metric**:
A measured quantity of a Span, such as duration or tokens, recorded as fact and never as a
judgement.
_Avoid_: measurement, stat, KPI, score

### Judgement

**Eval**:
A judgement applied to a Trace, producing one Score. Mechanical (code) or judged (a Judge).
_Avoid_: metric, assertion, check, scorer, grader, evaluator, criterion

**Judge**:
The configured model and prompt that performs a judged Eval.
_Avoid_: evaluator, grader, LLM-as-judge, auditor, rubric (its input)

**Backend**:
The path a model call takes to the model — the Judge's, or the Target's when the in-process
Adapter makes them: `anthropic_api`, `claude_code` or `replay`. Recorded in the Run for each,
and part of the Judge's Fingerprint (ADR-0009, ADR-0010). On `claude_code` structured output
is checked after the fact and retried once, not constrained at decoding (ADR-0009): a schema
`pattern` is a guarantee on `anthropic_api` and an instruction plus a check on `claude_code`.
_Avoid_: provider (Anthropic in every case), transport, client, path

**Calibration Notes**:
Per-Target guidance for the Judge, kept beside the Manifest, that reaches every Judge prompt
verbatim and is part of the Judge's Fingerprint.
_Avoid_: audit notes, judge notes (only the file is named that), hints, rubric, few-shot examples

**Score**:
The output of one Eval on one Trace: a Verdict, a rationale, and the Spans it cites as
evidence.
_Avoid_: result, grade, assessment, feedback

**Verdict**:
The closed-set outcome of a Score: `pass`, `fail`, `incomplete`, `unverifiable` or `invalid`
(ADR-0003).
_Avoid_: status, result, outcome, reward, label

**Diagnosis**:
The Judge's narrative for one Trial explaining why its Scores came out as they did, citing
Spans; recorded beside the Scores and never a Score itself.
_Avoid_: explanation, rationale (the per-Score field), root cause, summary, post-mortem

### Runs

**Run**:
One recorded evaluation of a Target: a selection of Scenarios executed at one Fingerprint,
with the Traces, Scores and Scorecard produced and the configuration that produced them.
_Avoid_: experiment, session, job, batch, test run, evaluation

**Trial**:
One execution of one Scenario within a Run.
_Avoid_: repeat, epoch, attempt, sample

**Scorecard**:
The aggregate of all Scores in a Run.
_Avoid_: summary, results, dashboard, leaderboard

**Baseline**:
A Run designated as the reference in a comparison.
_Avoid_: golden run, reference, control, main

**Report**:
A rendering of a Run or a comparison for humans, never the source of truth.
_Avoid_: results, output, summary, dashboard

**Index**:
The derived SQLite file under `.agentdiag/` that `list` and the Dashboard read: rows for Runs,
Trials, Spans, Scores and Change records, written when a Run completes and rebuilt from the
Run directories and the Change record files at any time. The files are the truth (ADR-0005 §5).
_Avoid_: database, cache, catalogue, registry
