"""The HTTP Adapter: converses with a Target through the chat endpoint it ships, one POST
per Turn, and records what the response surface shows (ticket 17; phase-8 decisions 3, 6-9).

ADR-0001 §2: an Adapter drives a Target only through the interfaces it ships to its users.
For a black-box Target that is an HTTP endpoint, so this Adapter sees what a user's client
sees and no more: the reply's text, whatever tools the stream reports, the conversation id
the Target hands back. Everything it writes is `observed` (ADR-0001 §3), and every fact the
surface cannot show is said to be missing rather than guessed.

- **A session is a conversation the Target holds** (decision 3). `open` applies the
  identity Fixture through the environment's identity mode (decision 6) and holds no
  conversation; the first `deliver` posts without one, the Dialect reads the id from the
  reply, and every later `deliver` sends it back. The Adapter never fabricates an id: a
  Dialect that reports none leaves every Turn a new chat, and the Turn says so
  (`agentdiag.conversation.new`).
- **What one Turn records** (decision 7): a `response` Span named `response <dialect>`
  inside the runner's `turn` Span, opened when the request is sent and ended when the last
  Frame is read; a `tool_call` or `retrieval` Span per tool the stream reports, written once
  the stream has ended, with `start_time` and `end_time` not observed, since a stream
  reports that a tool ran and not when — unless the Turn's proxy rows reconstruct its tools,
  when the stream's are only named (`agentdiag.stream_tools`, decision 8 amended).
  Arguments the stream did not show are unknown, never empty. A non-2xx status, an
  `ErrorFrame`, a `DialectError` or any other fault of the Dialect ends the Turn `error` with
  one `error` Event and raises `TargetResponseError`, which the Trial records as
  `target_error` (D22). The Trace holds the path sent and never a header's value.
- **Tool truth comes from the Connector** (decision 8, ADR-0013 §5). When the Manifest's
  `adapter.tool_truth` names `evidence: proxy`, after the stream ends the Adapter reads the
  Turn's proxy rows through the Target's Connector and hands them to
  `ProxyRowImporter.reconstruct_turn`, which writes the `reconstructed` Spans under the
  `response` Span, attributed by the message sent — after a failed Turn too, so a Turn that
  errored after calling tools keeps its evidence. A failed read or no matching row is
  recorded on the `response` Span (`agentdiag.tool_truth`) and the Turn keeps its
  `observed` Spans: a missing proxy row is not a Target error.
- **A `live` environment is refused** at `check` and at `open` without `allow_live`
  (`--live`, decision 9), in `LIVE_REFUSAL`'s words; the description records the flag.
- **Credentials are values in memory only** (decision 2): read from the process at
  `check` and `open` through the Connector's own function, sent as the Dialect frames them,
  and scrubbed from every message this module raises or records.

`HttpTransport` is the seam under the requests: `UrllibTransport`, standard library, is the
one a Run uses, and a test may hand another. Offline at import: no SDK, no `anthropic`.
"""

from __future__ import annotations

import http.client
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any, Protocol
from urllib.parse import urlsplit

from agentdiag.adapter.base import (
    LIVE_REFUSAL,
    AdapterDescription,
    Fixture,
    LiveSideEffectsRefused,
    Session,
    SessionClosed,
)
from agentdiag.adapter.http.config import (
    HttpEnvironmentError,
    IdentityApplied,
    ResolvedHttpEnvironment,
    ToolTruth,
    environment_block,
    fixture_problem,
    http_problems,
    identity_mode,
    resolve_http_environment,
    scrub_environment,
    tool_truth_of,
)
from agentdiag.adapter.http.dialect import (
    FRAME_TYPES,
    ConversationFrame,
    Dialect,
    DialectError,
    EndFrame,
    ErrorFrame,
    HttpRequest,
    HttpResponse,
    TextFrame,
    ToolFrame,
    dialect_identity_modes,
    load_dialect,
)
from agentdiag.connector.base import Connector, ConnectorError, EvidenceQuery
from agentdiag.importer.base import ImportRowsError
from agentdiag.importer.proxy_rows import ProxyRowImporter
from agentdiag.run.manifest import AdapterSection, higher_side_effects
from agentdiag.timestamps import now_utc
from agentdiag.trace import SpanHandle, TraceWriter
from agentdiag.trace.attributes import (
    CONVERSATION_NEW,
    DIALECT,
    FRAMES,
    NOT_OBSERVED,
    REQUEST_MESSAGE,
    STREAM_TOOLS,
    TIME_TO_FIRST_FRAME_MS,
    TOOL_TRUTH,
)
from agentdiag.trace.otel_genai import TOOL_CALL_ID, TOOL_NAME
from agentdiag.trace.spans import RESPONSE_KIND
from agentdiag.types import Fidelity, NotObservedFact, SideEffectClass, ToolKind

RESPONSE_NOT_OBSERVED: tuple[NotObservedFact, ...] = ("llm_calls", "usage")
"""What every `response` Span marks not observed: an HTTP response shows no model call and
no tokens (decision 7)."""

TRANSPORT_MARGIN_S = 5.0
"""How much longer than the Turn timeout a socket may wait: the runner's Turn timeout, not the
socket's, is what records a slow Turn as `timeout` (phase-5 decision 56); the socket's only
lets an abandoned worker thread end."""

ERROR_BODY_BYTES = 500
"""How much of a non-2xx body an `error` Event quotes, scrubbed; no more is read."""

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
"""Hosts `UrllibTransport` reaches without any proxy the environment configures."""


class FixtureNotApplicable(ValueError):
    """`open` was handed Fixtures its identity mode cannot apply (decision 6); the reason is
    the one `check_fixtures` gives."""


class TargetResponseError(RuntimeError):
    """The Target answered a Turn with a non-2xx status, an error Frame, or a body its
    Dialect cannot read, or could not be reached; the Trial records `target_error`."""


# --- the transport ---


class TransportResponse(HttpResponse, Protocol):
    """A response a transport opened, which the Adapter closes once it is read."""

    def close(self) -> None: ...


class HttpTransport(Protocol):
    """Sends one request and returns its response as it arrives: the seam a test replaces."""

    def send(self, request: HttpRequest, *, base_url: str, timeout_s: float) -> TransportResponse:
        """The response to `request` at `base_url`; a non-2xx status is a response, and an
        unreachable host raises."""
        ...


class UrllibResponse:
    """A `urllib` response (or an `HTTPError`, which is one) behind `TransportResponse`."""

    CHUNK = 8192

    def __init__(
        self,
        raw: http.client.HTTPResponse | urllib.error.HTTPError,
        status: int,
        headers: Mapping[str, str],
    ) -> None:
        self._raw = raw
        self.status = status
        self.headers: Mapping[str, str] = {name.lower(): value for name, value in headers.items()}

    def iter_bytes(self) -> Iterator[bytes]:
        read = (
            self._raw.read1 if isinstance(self._raw, http.client.HTTPResponse) else self._raw.read
        )
        while True:
            chunk = read(self.CHUNK)
            if not chunk:
                return
            yield bytes(chunk)

    def close(self) -> None:
        self._raw.close()


class UrllibTransport:
    """urllib, from the standard library: what a Run's HTTP Adapter posts through."""

    def send(self, request: HttpRequest, *, base_url: str, timeout_s: float) -> TransportResponse:
        url = base_url + request.path
        prepared = urllib.request.Request(
            url, data=request.body, headers=dict(request.headers), method=request.method
        )
        handlers: list[urllib.request.BaseHandler] = []
        if urlsplit(url).hostname in LOOPBACK:
            handlers.append(urllib.request.ProxyHandler({}))
        opener = urllib.request.build_opener(*handlers)
        try:
            raw = opener.open(prepared, timeout=timeout_s)
        except urllib.error.HTTPError as answered:
            return UrllibResponse(answered, answered.code, dict(answered.headers.items()))
        return UrllibResponse(raw, raw.status, dict(raw.headers.items()))


# --- what one Turn writes ---


class TurnRecorder:
    """Everything the HTTP Adapter writes into one Trace; `retarget` moves it, as the
    in-process emitter's does, when a continuing Scenario rebinds the session."""

    def __init__(
        self, writer: TraceWriter, *, tool_kinds: Mapping[str, ToolKind], dialect: str
    ) -> None:
        self.writer = writer
        self.tool_kinds = dict(tool_kinds)
        self.dialect = dialect

    def retarget(self, writer: TraceWriter) -> None:
        """Write into `writer` from now on (phase-5 decision 13)."""
        self.writer = writer

    def response(
        self, *, method: str, path: str, conversation: str | None, sent: str, message: str
    ) -> SpanHandle:
        """Open the Turn's `response` Span as the request goes out (decision 7)."""
        attributes: dict[str, Any] = {
            "http.request.method": method,
            "url.path": path,
            DIALECT: self.dialect,
        }
        if conversation is not None:
            attributes["gen_ai.conversation.id"] = conversation
        if sent != message:
            attributes[REQUEST_MESSAGE] = sent
        return self.writer.span(
            RESPONSE_KIND,
            actor="target",
            name=f"{RESPONSE_KIND} {self.dialect}",
            fidelity="observed",
            attributes=attributes,
        )

    def tool(self, parent: SpanHandle, frame: ToolFrame) -> None:
        """One tool the stream reported, as a Span at the instant its Frame arrived."""
        attributes: dict[str, Any] = {
            "gen_ai.operation.name": "execute_tool",
            TOOL_NAME: frame.name,
        }
        called: list[str] = []
        if frame.call_id is not None:
            attributes[TOOL_CALL_ID] = frame.call_id
        else:
            called.append(TOOL_CALL_ID)
        facts: list[NotObservedFact] = ["start_time", "end_time"]
        if frame.arguments is None:
            facts.append("arguments")
            called.insert(0, "arguments")
        if frame.result is None:
            facts.append("result")
        attributes[NOT_OBSERVED] = facts
        kind = "retrieval" if self.tool_kinds.get(frame.name) == "retrieval" else "tool_call"
        span = self.writer.span(
            kind,
            actor="target",
            name=f"execute_tool {frame.name}",
            fidelity="observed",
            attributes=attributes,
            parent=parent,
        )
        span.event(
            "tool/call",
            actor="target",
            tool=frame.name,
            call_id=frame.call_id,
            arguments=frame.arguments,
            not_observed=called,
        )
        if frame.result is not None:
            span.event(
                "tool/result",
                actor="target",
                tool=frame.name,
                call_id=frame.call_id,
                result=frame.result,
                is_error=False,
            )
        span.end(status="ok")


class _Turn:
    """What one Turn's reply showed, as it was read."""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.frames: dict[str, int] = {}
        self.tools: list[ToolFrame] = []
        """The tools the stream reported, held until tool truth says whether they are
        written (phase-8 decision 8 amended: a Turn's reconstructed Spans supersede them)."""
        self.first_frame_ms: int | None = None
        self.status: int | None = None
        self.conversation: str | None = None
        self.error: tuple[str, str] | None = None


# --- the session ---


class HttpSession:
    """One conversation with an HTTP Target (decision 3)."""

    def __init__(
        self,
        adapter: HttpAdapter,
        env: ResolvedHttpEnvironment,
        dialect: Dialect,
        recorder: TurnRecorder,
        identity: IdentityApplied,
    ) -> None:
        self._adapter = adapter
        self._env = env
        self._dialect = dialect
        self._recorder = recorder
        self._identity = identity
        self.conversation: str | None = None
        """The id the Target holds this chat under, once a reply reported one."""
        self._delivered = False
        self._closed = False

    def deliver(self, message: str) -> str:
        """Post one Turn, record what the reply showed, return its text (decision 7).

        Whatever goes wrong while the reply is read — a status, an error frame, a Dialect
        that cannot read it or raises anything at all — ends the `response` Span `error`
        with a scrubbed message and raises `TargetResponseError`, after tool truth has
        been read: a Turn that failed after calling tools keeps its proxy evidence."""
        if self._closed:
            raise SessionClosed("This session is closed")
        sent = message
        prefix = self._identity.first_message_prefix
        if prefix is not None and not self._delivered:
            sent = f"{prefix}\n{message}"
        self._delivered = True
        env, dialect = self._env, self._dialect
        request = dialect.request(
            sent, conversation=self.conversation, env=env, identity=self._identity.body
        )
        request = request.model_copy(
            update={"headers": {**env.headers, **request.headers, **self._identity.headers}}
        )
        asked = self.conversation
        since = now_utc()
        span = self._recorder.response(
            method=request.method, path=request.path, conversation=asked, sent=sent, message=message
        )
        turn = _Turn()
        try:
            self._read(request, span, turn)
        except Exception as exc:  # a plugin Dialect's own fault ends the Turn, never the Span
            turn.error = (type(exc).__name__, env.scrub(str(exc)))
        if turn.conversation is not None:
            self.conversation = turn.conversation
        attributes: dict[str, Any] = {
            NOT_OBSERVED: list(RESPONSE_NOT_OBSERVED),
            FRAMES: dict(turn.frames),
            CONVERSATION_NEW: asked is None,
        }
        if turn.status is not None:
            attributes["http.response.status_code"] = turn.status
        if turn.first_frame_ms is not None:
            attributes[TIME_TO_FIRST_FRAME_MS] = turn.first_frame_ms
        if turn.conversation is not None:
            attributes["gen_ai.conversation.id"] = turn.conversation
        used = 0
        truth = self._adapter.tool_truth
        if truth is not None:
            try:
                read = self._tool_truth(truth, span, sent, since)
            except Exception as exc:
                kind = type(exc).__name__
                text = self._adapter.scrub_evidence(env.scrub(str(exc)))
                span.end(
                    status="error",
                    attributes={**attributes, "error.type": kind},
                    error=(kind, text),
                )
                raise
            attributes[TOOL_TRUTH] = read
            used = int(read.get("rows_used") or 0)
        if used and turn.tools:
            # The proxy rows are the Turn's tool truth; the stream's own report is named, not
            # written twice (decision 8, amended after the ticket 17 reviews).
            attributes[STREAM_TOOLS] = [frame.name for frame in turn.tools]
        else:
            for frame in turn.tools:
                self._recorder.tool(span, frame)
        if turn.error is not None:
            error_type, text = turn.error
            span.event("error", actor="target", error_type=error_type, message=text)
            span.end(
                status="error",
                attributes={**attributes, "error.type": error_type},
                error=turn.error,
            )
            raise TargetResponseError(f"{error_type}: {text}")
        span.end(status="ok", attributes=attributes)
        return "".join(turn.text)

    def _read(self, request: HttpRequest, span: SpanHandle, turn: _Turn) -> None:
        """Send `request` and read its reply into `turn` Frame by Frame, timestamping each."""
        env, clock = self._env, self._adapter.clock
        started = clock()
        try:
            response = self._adapter.transport.send(
                request, base_url=env.base_url, timeout_s=self._adapter.transport_timeout_s
            )
        except (OSError, http.client.HTTPException, ValueError) as exc:
            turn.error = (type(exc).__name__, env.scrub(f"the Target could not be reached: {exc}"))
            return
        try:
            turn.status = response.status
            if not 200 <= response.status < 300:
                said = _head(response.iter_bytes(), ERROR_BODY_BYTES)
                turn.error = (
                    str(response.status),
                    env.scrub(
                        f"the Target answered HTTP {response.status}"
                        + (f": {said}" if said else "")
                    ),
                )
                return
            for frame in self._dialect.frames(response):
                if turn.first_frame_ms is None:
                    turn.first_frame_ms = max(0, clock() - started)
                name = FRAME_TYPES[type(frame)]
                turn.frames[name] = turn.frames.get(name, 0) + 1
                if isinstance(frame, TextFrame):
                    turn.text.append(frame.text)
                elif isinstance(frame, ToolFrame):
                    turn.tools.append(frame)
                elif isinstance(frame, ConversationFrame):
                    turn.conversation = frame.id
                elif isinstance(frame, ErrorFrame):
                    turn.error = (env.scrub(frame.error_type), env.scrub(frame.message))
                    break
                elif isinstance(frame, EndFrame):
                    break
        except DialectError as exc:
            turn.error = ("DialectError", env.scrub(str(exc)))
        except (OSError, http.client.HTTPException) as exc:
            turn.error = (type(exc).__name__, env.scrub(f"the reply broke off: {exc}"))
        finally:
            response.close()

    def _tool_truth(
        self, truth: ToolTruth, span: SpanHandle, sent: str, since: str
    ) -> dict[str, Any]:
        """Read the Turn's proxy rows through the Connector and reconstruct its tool Spans
        under `span` (decision 8): polled every `poll_s` for `wait_s` on the Adapter's clock,
        or read once."""
        adapter = self._adapter
        connector = adapter.connector
        if connector is None:
            return {"failed": "the Manifest names no Connector to read proxy rows through"}
        query = EvidenceQuery(conversation_id=self.conversation, since=since)
        deadline = adapter.clock() + truth.wait_s * 1000
        while True:
            try:
                rows = connector.read_evidence(self._env.name, truth.evidence, query)
                got = adapter.importer.reconstruct_turn(
                    rows, writer=self._recorder.writer, parent=span, user_message=sent
                )
            except (ConnectorError, ImportRowsError) as exc:
                return {"failed": adapter.scrub_evidence(self._env.scrub(str(exc)))}
            remaining_ms = deadline - adapter.clock()
            if got.rows_used or remaining_ms <= 0:
                return {
                    "rows_used": got.rows_used,
                    "rows_ignored": got.rows_ignored,
                    "spans": got.spans,
                    "lacked": [entry.model_dump() for entry in got.lacked],
                }
            adapter.sleep(min(truth.poll_s, remaining_ms / 1000))

    def rebind(self, trace: TraceWriter) -> None:
        """Write this conversation's Turns into a continuing Scenario's Trace (decision 13);
        the Target keeps the conversation, the recorder moves."""
        if self._closed:
            raise SessionClosed("This session is closed")
        self._recorder.retarget(trace)

    def close(self) -> None:
        """End the session; the Target keeps its chat. A second close is an error."""
        if self._closed:
            raise SessionClosed("This session is already closed")
        self._closed = True


# --- the Adapter ---


class HttpAdapter:
    """Drives a Target through its shipped chat endpoint (ticket 17)."""

    kind = "http"

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        environment: str,
        tool_kinds: Mapping[str, ToolKind] | None = None,
        allow_live: bool = False,
        connector: Connector | None = None,
        scrub_evidence: Callable[[str], str] | None = None,
        transport: HttpTransport | None = None,
        clock: Callable[[], int] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.config = dict(config)
        self.section = AdapterSection.model_validate(self.config)
        self.environment = environment
        self.tool_kinds = dict(tool_kinds or {})
        """`{name: kind}` from the Manifest's `tools` section; preflight passes it."""
        self.allow_live = allow_live
        self.connector = connector
        """The Target's Connector, when `tool_truth` names a store it reads (decision 8)."""
        self.scrub_evidence = scrub_evidence or (lambda text: text)
        """Takes the Connector's credential values out of its error text."""
        self.transport: HttpTransport = transport or UrllibTransport()
        self.clock = clock or _monotonic_ms
        """Milliseconds, for the first-frame latency: a scripted clock reproduces it."""
        self.sleep = sleep
        self.environ = environ if environ is not None else os.environ
        self.importer = ProxyRowImporter(tool_kinds=self.tool_kinds)

    @staticmethod
    def validate_section(section: AdapterSection) -> list[tuple[str, str]]:
        """What `validate` adds for this kind (decision 2): `config.http_problems`, which
        `run.manifest_checks` calls directly, without loading this module."""
        return http_problems(section)

    # --- description ---

    @property
    def side_effects(self) -> SideEffectClass:
        """The higher of the Adapter block's class and the environment's own."""
        block = self.section.environments.get(self.environment)
        own = block.get("side_effects") if isinstance(block, Mapping) else None
        return higher_side_effects(self.section.side_effects, own)

    @property
    def tool_truth(self) -> ToolTruth | None:
        """`adapter.tool_truth`, read (decision 8)."""
        return tool_truth_of(self.section)

    @property
    def fidelity(self) -> Fidelity:
        """The best this Adapter achieves: `reconstructed` when `tool_truth` names a store
        its Connector says it reads (decision 8), else `observed`."""
        return "reconstructed" if self.reconstructs else "observed"

    @property
    def reconstructs(self) -> bool:
        """Whether tool truth has somewhere to come from: `tool_truth` names a store and the
        Connector handed over declares it (`ConnectorDescription.evidence`)."""
        truth = self.tool_truth
        if truth is None or self.connector is None:
            return False
        try:
            return truth.evidence in self.connector.describe().evidence
        except ConnectorError:
            return False

    @property
    def transport_timeout_s(self) -> float:
        """How long the socket waits: past the Turn timeout, so the runner's Turn timeout
        decides."""
        return self.section.turn_timeout_s(self.environment) + TRANSPORT_MARGIN_S

    def describe(self) -> AdapterDescription:
        """What this Adapter is, recorded verbatim in `run.json` (D5, decision 18): it
        observes nothing of the deployed set, and the Connector covers it (ADR-0011 §3)."""
        block = self.section.environments.get(self.environment)
        return AdapterDescription(
            kind=self.kind,
            fidelity=self.fidelity,
            side_effects=self.side_effects,
            environment=self.environment,
            observes=[],
            config=dict(block) if isinstance(block, Mapping) else {},
            live_acknowledged=self.allow_live,
        )

    # --- preflight ---

    def check(self) -> None:
        """Preflight: the block valid, the credentials set, the Dialect installed, before
        any Run directory; a `live` environment refused (ADR-0001 point 5)."""
        self._refuse_live()
        environment_block(self.section, self.environment)
        mine = (f"adapter.environments.{self.environment}", "adapter.tool_truth")
        own = [
            f"{where}: {message}"
            for where, message in http_problems(self.section)
            if where == mine[0] or where.startswith((f"{mine[0]}.", mine[1]))
        ]
        if own:
            raise HttpEnvironmentError("; ".join(own))
        self._resolved()
        load_dialect(environment_block(self.section, self.environment).dialect)

    def check_fixtures(self, fixtures: Sequence[Fixture]) -> str | None:
        """Can `open` apply these (decision 6)? Answered from the identity block and the
        Dialect's modes, reading no credential and opening nothing."""
        if not fixtures:
            return None
        block = environment_block(self.section, self.environment)
        return fixture_problem(
            fixtures,
            environment=self.environment,
            identity=block.identity,
            extra_modes=dialect_identity_modes(load_dialect(block.dialect)),
        )

    # --- session ---

    def open(self, trace: TraceWriter, *, fixtures: Sequence[Fixture] = ()) -> Session:
        """Apply the identity Fixture and start a conversation with no id (decision 3)."""
        self._refuse_live()
        env = self._resolved()
        dialect = load_dialect(env.dialect)
        modes = dialect_identity_modes(dialect)
        refusal = fixture_problem(
            fixtures, environment=env.name, identity=env.identity, extra_modes=modes
        )
        if refusal is not None:
            raise FixtureNotApplicable(refusal)
        applied = IdentityApplied()
        for fixture in fixtures:
            mode = identity_mode(env.identity or {}, modes)
            if env.identity is None or mode is None:  # `fixture_problem` refused both already
                raise FixtureNotApplicable(f"Fixture {fixture.name!r} has no identity mode")
            one = mode.apply(env.identity, fixture.data)
            applied = IdentityApplied(
                headers={**applied.headers, **one.headers},
                body={**(applied.body or {}), **one.body} if one.body is not None else applied.body,
                first_message_prefix=one.first_message_prefix or applied.first_message_prefix,
            )
            # The field names, never their values: an identity is customer data.
            trace.event(
                "fixture/applied",
                actor="adapter",
                fixture=fixture.name,
                mechanism=mode.mechanism,
                detail={"kind": fixture.kind, "fields": sorted(fixture.data)},
            )
        recorder = TurnRecorder(trace, tool_kinds=self.tool_kinds, dialect=dialect.name)
        return HttpSession(self, env, dialect, recorder, applied)

    def _resolved(self) -> ResolvedHttpEnvironment:
        """The environment with its credentials read; every error scrubbed of their values."""
        try:
            return resolve_http_environment(self.section, self.environment, self.environ)
        except (HttpEnvironmentError, ConnectorError) as exc:
            scrubbed = scrub_environment(str(exc), self.section, self.environment, self.environ)
            raise type(exc)(scrubbed) from None

    def _refuse_live(self) -> None:
        if self.side_effects == "live" and not self.allow_live:
            raise LiveSideEffectsRefused(LIVE_REFUSAL.format(environment=self.environment))


def _monotonic_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def _head(chunks: Iterator[bytes], limit: int) -> str:
    """The first `limit` bytes of a body as text, reading no further than they need."""
    held = b""
    for chunk in chunks:
        held += chunk
        if len(held) >= limit:
            break
    return held[:limit].decode("utf-8", "replace").strip()


__all__ = [
    "RESPONSE_NOT_OBSERVED",
    "FixtureNotApplicable",
    "HttpAdapter",
    "HttpSession",
    "HttpTransport",
    "TargetResponseError",
    "TransportResponse",
    "TurnRecorder",
    "UrllibResponse",
    "UrllibTransport",
]
