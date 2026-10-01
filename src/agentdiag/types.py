"""The closed sets (D2, ADR-0003 section 1).

One home for every `Literal` alias so the Trace side (`agentdiag.trace.events`) and the
Score side (`agentdiag.eval.score`) cannot drift apart. A string outside a set fails
Pydantic validation on write.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, get_args

# --- Trace side (ADR-0004) ---

Actor = Literal["target", "simulated_user", "adapter", "agentdiag", "judge", "operator"]
"""Who an Event belongs to (ADR-0004 §3). `operator` is a staff member's takeover Turn in an
imported Trace (phase-5 decision 58): the conversation-record Importer writes it on a
takeover's messages (ticket 26), and `show` labels such a reply `operator`."""

Fidelity = Literal["instrumented", "reconstructed", "observed"]
SideEffectClass = Literal["none", "sandboxed", "live"]
SIDE_EFFECT_ORDER: tuple[SideEffectClass, ...] = get_args(SideEffectClass)
"""The side-effect classes from least to most (phase-6 decision 8): an environment's class
is the higher of the Adapter's and its own, so an environment can say it does more, never
less."""
SyncStatus = Literal["held", "broken", "not_checked"]
NotCheckedReason = Literal["no_manifest", "no_fingerprint", "adapter_cannot_observe"]
TerminationReason = Literal[
    "completed",
    "stop_when",
    "stop_token",
    "max_turns",
    "target_error",
    "simulated_user_error",
    "agentdiag_error",
    "timeout",
    "cancelled",
]
FINISHED_TERMINATIONS: frozenset[TerminationReason] = frozenset(
    {"completed", "stop_when", "stop_token", "max_turns"}
)
"""The terminations that end a Trial the way its Scenario meant it to end, so its Evals are
judged as recorded; every other termination forces the Verdict its cause allows (D22)."""

FIDELITY_RANK: dict[Fidelity, int] = {"observed": 0, "reconstructed": 1, "instrumented": 2}
"""Fidelity is ordered (ADR-0001): an Eval that needs `reconstructed` can be grounded in an
`instrumented` Span and not in an `observed` one. The one place the order is written."""

ToolKind = Literal["retrieval", "action"]
"""What a Manifest's `tools.<name>.kind` says a tool is (D37, ADR-0006 §2): a lookup, whose
calls the in-process Adapter records as `retrieval` Spans, or an action (the default)."""

SpanStatus = Literal["ok", "error"]
"""What a `span/end` may say. The `Span` projection adds `open` for an unmatched start."""

SpanOrigin = Literal["seed", "imported"]
"""Where a Span came from when an Adapter did not record it live in this Run (phase-5
decision 58), under the attribute `agentdiag.span.origin`: a seeded exchange (a
cold-open greeting) or an imported row. Absent means recorded live. The Importers write
`imported` on every Span (ticket 26); nothing writes `seed` yet. The writer refuses a value
outside the set."""

NotObservedFact = Literal[
    "arguments",
    "content",
    "end_time",
    "end_time_exact",
    "llm_calls",
    "request_body",
    "response",
    "response_body",
    "result",
    "start_time",
    "time_to_first_token",
    "timestamps",
    "usage",
]
"""What a Span's evidence could not give, under ADR-0006 §4's not-observed state
(`agentdiag.not_observed`, ticket 26): a tool call's `arguments` or `result`; a message's
`content`; a Span's `start_time`, or its `end_time` where the Trace could not hold the
evidence's instant; a proxy row's `end_time_exact` (its end is `created_at + latency_ms`)
and `time_to_first_token`; `timestamps` when a record's times are its order, not its clock;
`llm_calls` on a voice Turn; a proxy row's `request_body`, `response_body` or `usage`; and a
Turn's `response` when its reply was never logged. Never zero, never null: not observed."""

# --- Score side (ADR-0003), owned by slice 4's `agentdiag.eval.score` ---

Verdict = Literal["pass", "fail", "incomplete", "unverifiable", "invalid"]
VERDICTS: tuple[Verdict, ...] = get_args(Verdict)
"""The five, in the order a Scorecard and a listing count and print them (ADR-0003 §1,
ADR-0005 §8)."""
IncompleteReason = Literal["timeout", "transport_error", "no_response", "target_error", "cancelled"]
UnverifiableReason = Literal[
    "evidence_missing",
    "evidence_truncated",
    "fidelity_too_low",
    "eval_not_applicable",
]
FailReason = Literal["refusal", "malformed_output"]
FaultSource = Literal["simulated_user", "scenario", "fixture", "judge", "agentdiag"]
FaultDirection = Literal["helped", "hindered", "none"]
Finding = Literal[
    "none",
    "leaked_hidden_fact",
    "stopped_early",
    "abandoned_goal",
    "contradicted_facts",
    "other",
]
"""What the Simulated User reviewer found in a simulated Trial's user messages (D28,
phase-5 decision 57); anything but `none` is a fault of the Simulated User's."""

# --- Run side (ADR-0005), owned by `agentdiag.run` ---

NotRunReason = Literal["not_selected", "suite_not_run", "fixture_unavailable", "cancelled"]
"""Why a Scenario in a loaded Suite produced no Trial (D31, ADR-0005 sections 3 and 8).

The first three are D31's. `cancelled` is agentdiag's, for the Scenarios a Ctrl-C left
unstarted: they were selected and would have run, which `not_selected` would misreport.
It mirrors D22's `cancelled` under `incomplete` — the same event, seen from the Run rather
than from one Trial.
"""

# --- The Manifest and Sync (ticket 10, phase-6 decisions 8 to 12) ---

SuiteStatus = Literal["runnable", "draft", "retired"]
"""A Manifest's Suite entry: `runnable` runs, `draft` is validated and named `not_run`,
`retired` is neither loaded nor validated (decision 8)."""

JsonType = Literal["string", "number", "integer", "boolean", "object", "array", "null"]
"""The JSON types `eval_parameters.tool_argument_types` may require of an argument."""

PROTECTED_BY_DEFAULT: frozenset[str] = frozenset({"prod", "staging", "eu_prod"})
"""The environment names protected when their block says nothing (ADR-0011 §6d)."""

SectionKind = Literal["prompt", "tool", "data_source", "model", "provider", "flow", "tier"]
"""What one Fingerprint section is (decision 9)."""

CoveredBy = Literal["local", "adapter", "connector"]
"""The source a Fingerprint section's hash was taken from: the local file, the Adapter's
probe, or the Connector's read (ticket 23)."""

DEPLOYED_SOURCES: tuple[CoveredBy, ...] = ("connector", "adapter")
"""The sources of a section's deployed hash, the Connector's read first (decision 25): the
first that covered any section names a comparison's `covered_by`."""

Direction = Literal["identical", "local_ahead", "deployed_ahead", "diverged", "not_covered"]
"""Which of a section's three hashes is ahead (decision 12, ADR-0011 §3)."""

BROKEN_DIRECTIONS: frozenset[Direction] = frozenset({"local_ahead", "deployed_ahead", "diverged"})
"""The directions that break Sync (ADR-0011 §4); `identical` and `not_covered` hold."""

DEPLOYED_MOVED: frozenset[Direction] = frozenset({"deployed_ahead", "diverged"})
"""The directions whose deployed side moved since the Fingerprint: what `pull` brings home,
and what refuses a `push` that would write the section (ADR-0011 §5, §6f)."""

Change = Literal["changed", "added", "removed"] | None
"""What happened to a section against the record; None when it is identical."""

# --- The Connector (ticket 23, phase-6 decisions 19 and 26) ---

Access = Literal["read", "write"]
"""What one Connector operation does to a Target's live side (ADR-0011 §2): a `read` changes
nothing there; a `write` is a push, and only `agentdiag push` makes one."""

EvidenceKind = Literal["proxy", "conversation", "voice", "flows"]
"""The Evidence stores a Connector reads, by kind (ADR-0011 §1): proxy rows, conversation
records, voice conversations and Flow runs, each in its generic row shape (decision 33)."""

SyncBreakOpener = Literal["sync", "watch"]
"""What recorded a Sync break (decision 26): `agentdiag sync` or `sync --check`, or ticket
32's `watch`; never a Run alone (CONTEXT.md **Sync break**)."""

# --- The Change record and the push (tickets 25 and 27, phase-7 decisions 1 and 13) ---

ChangeStatus = Literal["open", "proposed", "pushed", "verified", "refuted", "wontfix", "superseded"]
"""Where a Change record is in its lifecycle (ADR-0012 §2): `refuted` exists so a change that
was pushed and did not fix the problem is not left at "unverified"."""

Layer = Literal["persona", "rules", "memory", "checklist", "flow", "runtime", "data"]
"""The layer of the Target a Change record's Diagnosis names (ADR-0012 §3)."""

TriggerKind = Literal["diagnosis", "complaint"]
"""What opened a Change record: a Diagnosis with the Trial it explains, or a complaint."""

PushKind = Literal["connector", "local"]
"""How a change reached the deployed set: a Connector push with its Push record, or, on a
Target changed by editing its files, the Run that re-synced onto them (ADR-0012 §2)."""

VerificationResult = Literal["verified", "refuted"]
"""How a `compare` closed a Change record (phase-7 decision 3)."""

CloseReason = Literal["wontfix", "superseded"]
"""The two closes that take no comparison: judged not worth changing, or replaced by another
record (phase-7 decision 2)."""

Confirmation = Literal["--push", "typed_name", "ui_confirm"]
"""How a person or an agent confirmed a push (ADR-0011 §6d, phase-7 decisions 13 and 14):
`--push` on an unprotected environment; the environment's name typed at a terminal prompt,
or in the UI's confirm step, on a protected one. No flag stands in for the typed name."""

# --- Judge side (ticket 19), owned by `agentdiag.model` ---

BackendKind = Literal["anthropic_api", "claude_code", "replay"]
"""The paths a Judge call can take to the model (`CONTEXT.md` **Backend**, ADR-0009)."""

STRUCTURED_OUTPUT: Mapping[BackendKind, str] = {
    "anthropic_api": "constrained at decoding",
    "claude_code": "checked after the fact, one retry",
    "replay": "as recorded",
}
"""How each Backend holds a structured answer to its schema (phase-5 decision 44). On
`anthropic_api` the schema constrains decoding; on `claude_code` the CLI checks the
`StructuredOutput` tool call after the model has written it and re-prompts once
(`max_turns=2`); a replay returns what was recorded. So a schema `pattern` is a guarantee
on the first and an instruction plus a check on the second. `show` and `compare` read the
phrase from here, since `show` must not import `agentdiag.model`."""

SamplingSupport = Literal["accepted", "not_supported"]
"""Whether a model call accepted a sampling parameter (D12). Here rather than in
`agentdiag.model.client` so the Scorecard, which records it, reads without the SDK."""

CostCheck = Literal["agrees", "differs", "unpriced"]
"""A backend's reported cost against the price table, on the Judge's Span (ADR-0009)."""

RunSource = Literal["run", "imported"]
"""Where an indexed Run came from (phase-5 decision 62): `run` for every Run agentdiag drove;
`imported` for one `agentdiag import` wrote from evidence (ticket 26, ADR-0013 §5)."""

DEFAULT_RUN_SOURCE: RunSource = "run"
"""The `source` of a Run agentdiag drove, and of a `run.json` written before the field
existed (phase-6 decision 6)."""

__all__ = [
    "BROKEN_DIRECTIONS",
    "DEFAULT_RUN_SOURCE",
    "DEPLOYED_MOVED",
    "DEPLOYED_SOURCES",
    "FIDELITY_RANK",
    "FINISHED_TERMINATIONS",
    "PROTECTED_BY_DEFAULT",
    "SIDE_EFFECT_ORDER",
    "STRUCTURED_OUTPUT",
    "VERDICTS",
    "Access",
    "Actor",
    "BackendKind",
    "Change",
    "ChangeStatus",
    "CloseReason",
    "Confirmation",
    "CostCheck",
    "CoveredBy",
    "Direction",
    "EvidenceKind",
    "FailReason",
    "FaultDirection",
    "FaultSource",
    "Fidelity",
    "Finding",
    "IncompleteReason",
    "JsonType",
    "Layer",
    "NotCheckedReason",
    "NotObservedFact",
    "NotRunReason",
    "PushKind",
    "RunSource",
    "SectionKind",
    "SideEffectClass",
    "SpanOrigin",
    "SpanStatus",
    "SuiteStatus",
    "SyncBreakOpener",
    "SyncStatus",
    "TerminationReason",
    "ToolKind",
    "TriggerKind",
    "UnverifiableReason",
    "Verdict",
    "VerificationResult",
]
