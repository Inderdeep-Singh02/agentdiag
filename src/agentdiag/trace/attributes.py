"""agentdiag's own Span attribute names, where no convention names the fact (ticket 19).

The OTel GenAI names live in `trace/otel_genai.py` and the OpenInference cost names in
`trace/openinference.py`, each pinned to upstream. These are agentdiag's: a backend's
reported cost for a model call and the check of it against the price table
(`model/prices.py`, ADR-0009), and the Claude Code CLI's start-up inside the first model
call of a session (ticket 20, decision 36, ADR-0010), the Judge Fingerprint a `judge` Span
was made under (ticket 21, decision 40), and how many attempts the Claude Code CLI made at
a structured answer (decision 42), and since ticket 06 the Simulated User's Fingerprint and
attempts, the per-call sampling outcome, and where a Span came from (decisions 48, 53, 58).
They live in the trace package, not
beside `cost_check`, because `show` reads them and `show` must not import `agentdiag.model` —
`validate` runs in the same process and a test asserts no model SDK is loaded there. `prices`
re-exports the two cost names.
"""

from __future__ import annotations

REPORTED_COST_USD = "agentdiag.cost.reported_usd"
"""What a backend said a call cost, USD, beside the table's `llm.cost.*`."""

COST_CHECK = "agentdiag.cost.check"
"""`cost_check`'s verdict on the reported cost: `agrees`, `differs` or `unpriced`."""

BACKEND_STARTUP_MS = "agentdiag.backend.startup_ms"
"""How long the Claude Code CLI took to start, ms, on the first `llm_call` of its session:
that Span's duration encloses it as well as the model's answer."""

JUDGE_FINGERPRINT = "agentdiag.judge.fingerprint"
"""The Judge Fingerprint (ADR-0003 §8) on a `judge` Span's start attributes: the one its
Scores record, so `judgement.jsonl` names the Judge that made each call (ticket 21,
decision 40)."""

JUDGE_ATTEMPTS = "agentdiag.judge.attempts"
"""How many message ids one Judge call through Claude Code took, on the `judge` Span's end
attributes, only when more than one: the CLI rejected a structured answer that did not fit
the schema and re-prompted, and the rejected attempt is in the recorded body (ticket 21,
decision 42)."""

SIMULATED_USER_FINGERPRINT = "agentdiag.simulated_user.fingerprint"
"""The Simulated User Fingerprint (phase-5 decision 52) on the start attributes of each of
its `llm_call` Spans: the one `run.json.simulated_user.fingerprint` records."""

SIMULATED_USER_ATTEMPTS = "agentdiag.simulated_user.attempts"
"""`JUDGE_ATTEMPTS`'s twin on a Simulated User `llm_call` Span: how many message ids one of
its calls through Claude Code took, only when more than one (decisions 42 and 48)."""

SAMPLING_PREFIX = "agentdiag.sampling."
"""Plus a sampling key (`agentdiag.sampling.temperature`): `accepted` or `not_supported`
for each declared key a Simulated User call sent (D12, phase-5 decision 53)."""

SPAN_ORIGIN = "agentdiag.span.origin"
"""Where a Span came from when not recorded live by an Adapter in this Run: `seed` or
`imported` (`types.SpanOrigin`, decision 58); absent means recorded live. The Importers
write `imported` on every Span (ticket 26); nothing writes `seed` yet."""

NOT_OBSERVED = "agentdiag.not_observed"
"""ADR-0006 §4's not-observed state on a Span: the list of the Span's facts its evidence
could not give, each a `types.NotObservedFact`, distinct from a null or a zero. The
Importers write it (phase-6 decisions 34 and 35); absent means every fact was observed. A
`tool/call` Event keeps its own `not_observed` field, as before."""

TURN_ANSWERED_BY = "agentdiag.turn.answered_by"
"""`operator` on an imported Turn a staff member answered (decision 35): the reply in it is
not the Target's, so no Eval holds the Target to it."""

EVIDENCE_REQUEST_ID = "agentdiag.evidence.request_id"
"""The proxy row an imported `llm_call` Span was read from, by its full request id: where
the Span's facts came from (decision 34). Not a Scenario's Provenance, which says where a
Scenario came from."""

FLOW_ID = "agentdiag.flow.id"
"""The Flow an imported `flow` Span ran (decision 34)."""

FLOW_JOINED_BY = "agentdiag.flow.joined_by"
"""How an imported Flow run was joined to the `tool_call` that started it: `time`, a start
inside the tool Span's window (with equal arguments when the run records them)."""

EVIDENCE_STORE = "agentdiag.evidence.store"
"""The Evidence store a Span recorded in a Trial was reconstructed from (`proxy`, phase-8
decision 8): the HTTP Adapter's tool truth, read through the Connector after the Turn."""

EVIDENCE_CREATED_AT = "agentdiag.evidence.created_at"
"""The proxy row's own `created_at` on a Span reconstructed inside a live Trial, whose
`start_time` is the writer's clock and marked not observed (decision 8)."""

EVIDENCE_LATENCY_MS = "agentdiag.evidence.latency_ms"
"""The proxy row's own `latency_ms` beside `EVIDENCE_CREATED_AT` (decision 8)."""

DIALECT = "agentdiag.dialect"
"""The Dialect an HTTP `response` Span's Turn was framed in (phase-8 decision 7)."""

TIME_TO_FIRST_FRAME_MS = "agentdiag.time_to_first_frame_ms"
"""An HTTP `response` Span's observed latency from the request sent to the first Frame read,
ms; absent when no Frame came (decision 7). `first_token_latency` reads it when the Turn
holds no `llm_call`."""

FRAMES = "agentdiag.frames"
"""A `response` Span's Frames counted by type (`{"text": 3, "tool": 1, "end": 1}`)."""

CONVERSATION_NEW = "agentdiag.conversation.new"
"""True on a `response` Span whose request carried no conversation id: the Target began a
new chat for that Turn (decision 3)."""

REQUEST_MESSAGE = "agentdiag.request.message"
"""The user text an HTTP `response` Span actually sent, when an identity mode changed it
from the Turn's message (the `first_message` mode's prefix, decision 6)."""

STREAM_TOOLS = "agentdiag.stream_tools"
"""The tools an HTTP stream reported on a Turn whose proxy rows reconstructed its tool Spans:
named here instead of written as `observed` Spans beside the `reconstructed` ones (phase-8
decision 8, amended after the ticket 17 reviews), so no tool is counted twice."""

TOOL_TRUTH = "agentdiag.tool_truth"
"""What the proxy-row read after an HTTP Turn gave (decision 8): `{rows_used, rows_ignored,
spans, lacked}`, or `{failed: <reason>}` when the read failed."""

__all__ = [
    "BACKEND_STARTUP_MS",
    "CONVERSATION_NEW",
    "COST_CHECK",
    "DIALECT",
    "EVIDENCE_CREATED_AT",
    "EVIDENCE_LATENCY_MS",
    "EVIDENCE_REQUEST_ID",
    "EVIDENCE_STORE",
    "FLOW_ID",
    "FLOW_JOINED_BY",
    "FRAMES",
    "JUDGE_ATTEMPTS",
    "JUDGE_FINGERPRINT",
    "NOT_OBSERVED",
    "REPORTED_COST_USD",
    "REQUEST_MESSAGE",
    "SAMPLING_PREFIX",
    "SIMULATED_USER_ATTEMPTS",
    "SIMULATED_USER_FINGERPRINT",
    "SPAN_ORIGIN",
    "STREAM_TOOLS",
    "TIME_TO_FIRST_FRAME_MS",
    "TOOL_TRUTH",
    "TURN_ANSWERED_BY",
]
