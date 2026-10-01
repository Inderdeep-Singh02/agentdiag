"""The in-process Adapter: drives a Python Target and captures at its two boundaries (D6).

The Target is a factory that returns a callable. The Adapter hands that factory an
Anthropic SDK client whose HTTP transport it owns, and tool callables it has wrapped, so
every model exchange and every tool execution becomes an Event — while the Target's own
code runs unchanged and imports nothing of agentdiag (ADR-0001 points 1 and 2).

**Capture is at the HTTP boundary, not at `messages.create`.** Proxying the SDK method
would record the arguments agentdiag passed, not the body the SDK put on the wire, and the
two differ (the SDK sorts keys, fills defaults, serialises models). A transport records
what was actually sent, and the same transport position serves live and replay, so a
recording made live replays against the same code path that produced it.

Fidelity is `instrumented`: everything here is captured inside the Target's process.

**Three transports sit under the capture, one per Backend of the Target's calls**
(ticket 20, decisions 28 and 29): the HTTP transport, the replay transport, and
`ClaudeCodeTransport` when the credentials preflight resolved are the Claude Code login. The
capture above them is the same, so the `request` Event is the body the SDK put on the wire
whichever carries it, and the Target's code does not change (ADR-0001, ADR-0010). The Claude
Code transport holds a CLI session for the length of the Adapter session, so the session's
`close` ends it.

**The probe is the one exception to "no Turn before a Trial"** (phase-6 decision 11):
`observe()` builds the Target exactly as `open` does, over a client whose transport
(`ObservingTransport`) keeps the first request body and answers it with a canned `end_turn`
— no model, no network, no Backend, no Trace — delivers `PROBE_MESSAGE`, and returns what
the request carried: the system prompt, the tool schemas and the model. That is what `sync`
fingerprints an `observed` pointer from. The tools the factory is handed are the real
callables, unwrapped, since there is no Trace to record them into; a Target that calls a
tool before its first model call does so in its own process, as a Run would, which is why
the environment's side-effect class governs the probe as it governs `open`.

Two things are written at capture time because only capture knows them honestly: what a
model call cost, from `agentdiag.model.prices` into the `llm_call`'s `span/end` (phase-5
decision 4), and which tools are lookups — a tool the Manifest marks `kind: retrieval` is
recorded as a `retrieval` Span, every other as a `tool_call` (D6, D37, ADR-0006 §2), with
the same attributes either way.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any

import anthropic
import httpx2
from pydantic import BaseModel, Field

from agentdiag.adapter.base import (
    LIVE_REFUSAL,
    AdapterDescription,
    Fixture,
    LiveSideEffectsRefused,
    Session,
    SessionClosed,
)
from agentdiag.deadline import Overdue, within
from agentdiag.inprocess_rules import adapter_problems
from agentdiag.model.claude_code import Backend, ClaudeCodeCli, backend_for
from agentdiag.model.claude_code_transport import (
    MESSAGES_PATH,
    ClaudeCodeTransport,
    ClaudeCodeTransportError,
    SessionFactory,
)
from agentdiag.model.credentials import CredentialSource
from agentdiag.model.prices import cost_attributes
from agentdiag.model.replay import (
    Cursor,
    Recording,
    ReplayCursor,
    ReplayMismatch,
    ReplayTransport,
    canonical,
)
from agentdiag.reference import import_attribute
from agentdiag.run.manifest import DEFAULT_TURN_TIMEOUT_S, AdapterSection, higher_side_effects
from agentdiag.sync.observe import ObservationFailed
from agentdiag.trace import SpanHandle, TraceWriter
from agentdiag.trace.attributes import BACKEND_STARTUP_MS
from agentdiag.trace.requests import system_text
from agentdiag.types import SideEffectClass, ToolKind

PROBE_API_KEY = "agentdiag-sync-probe"
"""The placeholder key of the probe's client: its transport sends nothing, so no key is
real, and a test fake can tell the probe from a session by it."""

OBSERVES = ("prompts", "tools", "model", "provider")
"""What the probe can observe of the deployed set (decision 11): `describe()` says what it
could, the Fingerprint what it did."""


class PendingToolUse:
    """A `tool_use` block a response asked for, waiting for the Target to execute it.

    Attribution is by content, never by position (ADR-0001 point 3): a wrapped callable
    receives arguments, not the block's id, so the wrapper matches on tool name and
    argument equality and says so in the Trace when it could not.
    """

    def __init__(self, *, call_id: str, name: str, input: Mapping[str, Any]) -> None:
        self.call_id = call_id
        self.name = name
        self.key = canonical(dict(input))


class ModelCall:
    """One open `llm_call` Span and the tools its response is still waiting on."""

    def __init__(self, span: SpanHandle, model: str) -> None:
        self.span = span
        self.span_model = model
        """The requested model, priced when a response does not name the resolved one."""
        self.pending: list[PendingToolUse] = []
        self.ended = False
        self.end_attributes: dict[str, Any] = {}
        """What the response supplied, held until the Span is allowed to close."""


class CapturingTransport(httpx2.BaseTransport):
    """Wraps the transport the SDK would have used, and writes the exchange to the Trace.

    It opens the `llm_call` Span at the request and — decision 1 — keeps it open past the
    response when that response asked for tools, so the tool Spans nest inside it and a
    child's interval lies within its parent's.
    """

    def __init__(self, inner: httpx2.BaseTransport, emitter: TraceEmitter) -> None:
        self.inner = inner
        self.emitter = emitter

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        if not request.url.path.endswith(MESSAGES_PATH):
            return self.inner.handle_request(request)

        body = json.loads(request.read() or b"{}")
        with self.emitter.model_call(body) as call:
            response = self.inner.handle_request(request)
            if response.status_code >= 400:
                self.emitter.model_failed(
                    call,
                    error_type=f"HTTPStatus{response.status_code}",
                    message=_body_text(response),
                )
                return response
            self.emitter.model_responded(call, json.loads(response.read() or b"{}"))
            return response

    def close(self) -> None:
        self.inner.close()


PROBE_MESSAGE = "agentdiag sync probe"
"""The one user message the probe delivers (decision 11). It never reaches a model."""


class Observation(BaseModel):
    """What the probe's one request carried: the Target's deployed set as the in-process
    Adapter can see it (decision 11)."""

    system_prompt: str | None = None
    tool_schemas: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """Each tool as the request's `tools` entry carried it — name, description,
    input_schema — by name."""

    model: str | None = None


def observation_of(body: Mapping[str, Any]) -> Observation:
    """What one Messages API request body says of the deployed set: the probe's reading,
    the system prompt read as the Judge reads it (`trace.requests.system_text`)."""
    tools = {
        str(tool["name"]): dict(tool)
        for tool in body.get("tools") or []
        if isinstance(tool, Mapping) and "name" in tool
    }
    model = body.get("model")
    return Observation(
        system_prompt=system_text(body.get("system")),
        tool_schemas=tools,
        model=str(model) if model else None,
    )


class ObservingTransport(httpx2.BaseTransport):
    """The probe's transport (decision 11): keeps the first Messages request body and
    answers every one with an empty `end_turn` and zero usage, touching no network.

    The canned answer is a complete Messages API response, so the SDK parses it and a
    Target's loop ends on it as it would on any final answer."""

    def __init__(self) -> None:
        self.body: dict[str, Any] | None = None

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.read() or b"{}")
        if request.url.path.endswith(MESSAGES_PATH) and self.body is None:
            self.body = body
        return httpx2.Response(
            200,
            json={
                "id": "msg_agentdiag_sync_probe",
                "type": "message",
                "role": "assistant",
                "model": str(body.get("model", "unknown")),
                "content": [{"type": "text", "text": ""}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
            request=request,
        )


class TraceEmitter:
    """Everything the Adapter writes into one Trace, and the state that ties it together.

    The writer owns the clock. Nothing here reads `time.time()`: a Trace's timestamps come
    from one source, so a scripted clock reproduces a Trial byte for byte.
    """

    def __init__(
        self, trace: TraceWriter, *, tool_kinds: Mapping[str, ToolKind] | None = None
    ) -> None:
        self.trace = trace
        self.calls: list[ModelCall] = []
        self.tool_kinds = dict(tool_kinds or {})
        """The Manifest's `tools.<name>.kind`, by tool name; absent means `action`."""

    def retarget(self, trace: TraceWriter) -> None:
        """Write into `trace` from now on (phase-5 decision 13).

        A model call still open belongs to the Trace it started in, so it is ended there
        first; nothing a later Trial records can then be parented under it.
        """
        self.end_open_calls()
        self.trace = trace

    # --- model exchanges ---

    @contextmanager
    def model_call(self, body: Mapping[str, Any]) -> Iterator[ModelCall]:
        """Open the `llm_call` Span, write the `request`, and fail loudly if the call does."""
        model = str(body.get("model", "unknown"))
        span = self.trace.span(
            "llm_call",
            actor="target",
            name=f"chat {model}",
            fidelity="instrumented",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "anthropic",
                "gen_ai.request.model": model,
                "gen_ai.request.max_tokens": body.get("max_tokens"),
            },
        )
        call = ModelCall(span, model)
        self.calls.append(call)
        span.event("request", actor="target", body=dict(body))
        try:
            yield call
        except BaseException as exc:
            # A transport that raised — a replay mismatch, a connection failure — is
            # recorded and re-raised untouched, so the SDK and the caller see the truth.
            self.model_failed(call, error_type=type(exc).__name__, message=str(exc))
            raise

    def model_responded(self, call: ModelCall, body: Mapping[str, Any]) -> None:
        """Write the `response`, and either end the Span or wait for the tools it asked for."""
        call.span.event("response", actor="target", body=dict(body))
        usage = body.get("usage") or {}
        attributes: dict[str, Any] = {
            "gen_ai.response.model": body.get("model"),
            "gen_ai.response.id": body.get("id"),
            "gen_ai.response.finish_reasons": [body.get("stop_reason")],
            "gen_ai.usage.input_tokens": usage.get("input_tokens"),
            "gen_ai.usage.output_tokens": usage.get("output_tokens"),
        }
        for source, name in (
            ("cache_read_input_tokens", "gen_ai.usage.cache_read.input_tokens"),
            ("cache_creation_input_tokens", "gen_ai.usage.cache_write.input_tokens"),
        ):
            if usage.get(source) is not None:
                attributes[name] = usage[source]
        attributes.update(cost_attributes(body.get("model") or call.span_model, usage))
        # Decision 36: the first call of a Claude Code session encloses the CLI's start-up,
        # so a latency read off this Span is not mistaken for the model's.
        startup = (body.get("claude_code") or {}).get("startup_ms")
        if startup is not None:
            attributes[BACKEND_STARTUP_MS] = startup
        call.end_attributes = attributes

        if body.get("stop_reason") == "tool_use":
            call.pending = [
                PendingToolUse(
                    call_id=str(block.get("id")),
                    name=str(block.get("name")),
                    input=block.get("input") or {},
                )
                for block in body.get("content") or []
                if block.get("type") == "tool_use"
            ]
            if call.pending:
                return  # decision 1: stay open until the last requested tool returns
        self.end_call(call)

    def model_failed(self, call: ModelCall, *, error_type: str, message: str) -> None:
        """A non-2xx response or a transport exception: one `error` Event, Span in error."""
        if call.ended:
            return
        call.span.event("error", actor="target", error_type=error_type, message=message)
        call.ended = True
        call.span.end(
            status="error",
            attributes={"error.type": error_type},
            error=(error_type, message),
        )

    def end_call(self, call: ModelCall) -> None:
        """Close one `llm_call` Span with the attributes its response supplied."""
        if call.ended:
            return
        call.ended = True
        call.span.end(status="ok", attributes=call.end_attributes)

    def end_open_calls(self) -> None:
        """Close any `llm_call` whose tools the Target never executed.

        A Target that asked for a tool and then returned without calling it leaves the Span
        open. It is ended when the Turn is over rather than left dangling, so every
        `span/start` in a finished Trace has its `span/end`.
        """
        for call in self.calls:
            self.end_call(call)

    # --- tool executions ---

    def wrap(self, name: str, tool: Callable[..., Any]) -> Callable[..., Any]:
        """Instrument one tool callable: a tool Span with its call and its result.

        The Span is a `retrieval` when the Manifest marks the tool so, a `tool_call`
        otherwise, and its attributes are the same either way (ADR-0006 §2): the kind says
        what the tool is for, and nothing an Eval reads about the call depends on it.

        Attribution to the `tool_use` block that asked for it, in order (decision 2):

        1. **Exact match** — same tool name and the same arguments, compared as canonical
           JSON. This is the normal case and yields `gen_ai.tool.call.id`.
        2. **Name only** — the arguments could not be bound to names, so only the tool name
           can be compared. Still yields the call id; `arguments` is `null` and
           `not_observed` says `arguments`.
        3. **Unmatched** — no pending block fits. The Span still parents under the most
           recent open `llm_call` (or is a root when none is open), and `not_observed` says
           `gen_ai.tool.call.id`, which is then absent from the attributes.

        Never by position (ADR-0001 point 3): position is what turned four correct
        behaviours into defects in the reference repository.
        """

        @wraps(tool)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            arguments, not_observed = _bind(tool, args, kwargs)
            call, pending = self._attribute(name, arguments)
            attributes: dict[str, Any] = {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": name,
            }
            if pending is not None:
                attributes["gen_ai.tool.call.id"] = pending.call_id
            else:
                not_observed = [*not_observed, "gen_ai.tool.call.id"]

            parent = call.span if call is not None else None
            span = self.trace.span(
                "retrieval" if self.tool_kinds.get(name) == "retrieval" else "tool_call",
                actor="target",
                name=f"execute_tool {name}",
                fidelity="instrumented",
                attributes=attributes,
                parent=parent,
            )
            span.event(
                "tool/call",
                actor="target",
                tool=name,
                call_id=pending.call_id if pending else None,
                arguments=arguments,
                not_observed=not_observed,
            )
            try:
                result = tool(*args, **kwargs)
            except Exception as exc:
                span.event(
                    "tool/result",
                    actor="target",
                    tool=name,
                    call_id=pending.call_id if pending else None,
                    result=str(exc),
                    is_error=True,
                )
                span.end(
                    status="error",
                    attributes={"error.type": type(exc).__name__},
                    error=(type(exc).__name__, str(exc)),
                )
                self._settle(call, pending)
                raise
            span.event(
                "tool/result",
                actor="target",
                tool=name,
                call_id=pending.call_id if pending else None,
                result=_jsonable(result),
                is_error=False,
            )
            span.end(status="ok")
            self._settle(call, pending)
            return result

        return wrapped

    def _attribute(
        self, name: str, arguments: Mapping[str, Any] | None
    ) -> tuple[ModelCall | None, PendingToolUse | None]:
        """Find the `tool_use` block this execution answers (decision 2).

        Most recent open call first: a Target that interleaves calls still gets the block
        it most plausibly just read. Unmatched still parents under the most recent open
        call, and the Trace records that the id was not observed.
        """
        key = canonical(dict(arguments)) if arguments is not None else None
        open_calls = [call for call in self.calls if call.pending and not call.ended]
        for call in reversed(open_calls):
            for pending in call.pending:
                if pending.name == name and (key is None or pending.key == key):
                    return call, pending
        return (open_calls[-1] if open_calls else None), None

    def _settle(self, call: ModelCall | None, pending: PendingToolUse | None) -> None:
        """Retire a matched block; when it was the last one, close its `llm_call`."""
        if call is None:
            return
        if pending is not None and pending in call.pending:
            call.pending.remove(pending)
        if not call.pending:
            self.end_call(call)


class InProcessSession:
    """One conversation with an in-process Target."""

    def __init__(
        self,
        respond: Callable[[str], str],
        emitter: TraceEmitter,
        *,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._respond = respond
        self._emitter = emitter
        self._on_close = on_close
        """What else the session holds and must end: the Claude Code transport's session."""
        self._closed = False

    def deliver(self, message: str) -> str:
        """Deliver one user Turn; the runner's `turn` Span is what this lands inside."""
        if self._closed:
            raise SessionClosed("This session is closed")
        try:
            return self._respond(message)
        except anthropic.APIConnectionError as exc:
            # The SDK reports any transport exception as a connection error. A replay
            # mismatch, or a request the Claude Code Backend could not carry (decision 31), is
            # not a connection problem and must not read as one, so the real cause is
            # raised instead of the SDK's paraphrase of it.
            cause = exc.__cause__
            if isinstance(cause, (ReplayMismatch, ClaudeCodeTransportError)):
                raise cause from None
            raise
        finally:
            # A Target that asked for a tool and never executed it leaves the Span open;
            # the Turn is over, so it is ended now rather than inherited by the next one.
            self._emitter.end_open_calls()

    def rebind(self, trace: TraceWriter) -> None:
        """Point the session's Events at a continuing Scenario's Trace (decision 13).

        The Target's conversation state lives in the callable the factory returned, so
        nothing about the Target changes; the emitter, which the capturing transport and
        every wrapped tool write through, is the only thing that moves.
        """
        if self._closed:
            raise SessionClosed("This session is closed")
        self._emitter.retarget(trace)

    def close(self) -> None:
        """End the session. A second close is an error: a Trial closes exactly once."""
        if self._closed:
            raise SessionClosed("This session is already closed")
        self._emitter.end_open_calls()
        self._closed = True
        if self._on_close is not None:
            self._on_close()


class InProcessAdapter:
    """Drives a Target that ships a factory returning a callable (D6)."""

    kind = "inprocess"
    fidelity = "instrumented"

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        environment: str,
        replay: Path | Cursor | None = None,
        clock: Callable[[], int] | None = None,
        allow_live: bool = False,
        tool_kinds: Mapping[str, ToolKind] | None = None,
        credentials: CredentialSource | None = None,
        claude_code_session: SessionFactory | None = None,
    ) -> None:
        self.config = dict(config)
        self.tool_kinds = dict(tool_kinds or {})
        """`{name: kind}` from the Manifest's `tools` section; preflight passes it."""
        self.environment = environment
        # A `ReplayCursor` rather than a path when the Run drives the Target and the Judge
        # from one recording: `assert_consumed()` must then be asked once across both, or
        # the Judge's own exchange would look like an exchange nobody used (D12).
        self.replay: Cursor | None = (
            ReplayCursor(Recording.load(replay)) if isinstance(replay, Path) else replay
        )
        self.clock = clock
        self.allow_live = allow_live
        self.credentials = credentials
        """What preflight resolved (decision 29): the Claude Code login carries the Target's
        calls through the CLI; anything else, or nothing, leaves them to the HTTP transport
        and the SDK's own credential chain."""
        self.claude_code_session = claude_code_session
        """The session factory the Claude Code transport builds from; a test hands a fake."""
        self._replay_transport: ReplayTransport | None = None

    @staticmethod
    def validate_section(section: AdapterSection) -> list[tuple[str, str]]:
        """What `validate` adds for this kind: each environment's `factory` and `tools`, a
        `module:attr` found without importing it (`agentdiag.inprocess_rules`, offline, which
        `validate` calls directly so it never imports this module and its SDK)."""
        return adapter_problems(section)

    # --- description ---

    @property
    def side_effects(self) -> SideEffectClass:
        """The class the Manifest declares for this environment, validated on read (D2,
        ADR-0001 point 5): the higher of the Adapter block's and the environment's own
        (phase-6 decision 8), so an environment can say it does more, never less."""
        environments = self.config.get("environments") or {}
        block = environments.get(self.environment) if isinstance(environments, Mapping) else None
        own = block.get("side_effects") if isinstance(block, Mapping) else None
        return higher_side_effects(self.config.get("side_effects", "none"), own)

    @property
    def environment_config(self) -> dict[str, Any]:
        """The Manifest block for the named environment, as written."""
        environments = self.config.get("environments") or {}
        if self.environment not in environments:
            raise KeyError(
                f"No environment {self.environment!r} in the Manifest's adapter block; "
                f"it declares {sorted(k for k in environments if k != 'default')}"
            )
        block = environments[self.environment]
        if not isinstance(block, Mapping):
            raise TypeError(f"Environment {self.environment!r} is not a mapping")
        return dict(block)

    def describe(self) -> AdapterDescription:
        """What this Adapter is, recorded verbatim in `run.json` (D5)."""
        return AdapterDescription(
            kind=self.kind,
            fidelity="instrumented",
            side_effects=self.side_effects,
            environment=self.environment,
            observes=list(OBSERVES),
            config=self.environment_config,
            backend=self.backend,
            live_acknowledged=self.allow_live,
        )

    @property
    def backend(self) -> Backend:
        """The Backend this Adapter's Target calls take (decision 28), by the one cascade the
        Judge's takes (`backend_for`): replay first, then the Claude Code login, else the
        Messages API through the SDK's own credential chain, whether or not anything
        resolves there."""
        return backend_for(self.credentials, self.replay is not None) or Backend(
            kind="anthropic_api"
        )

    @property
    def _claude_code_cli(self) -> ClaudeCodeCli | None:
        """The CLI the Claude Code Backend spawns, when that is this Adapter's Backend."""
        if self.backend.kind != "claude_code":
            return None
        if self.credentials is None or self.credentials.cli is None:
            raise ValueError("the Claude Code credentials name no CLI")
        return self.credentials.cli

    def check(self) -> None:
        """Preflight: everything this Adapter needs must resolve, before any Run directory.

        Imports the factory and the tools reference without calling either, and asks for
        the side-effect class and the environment block so a typo in the Manifest is a
        message rather than a half-written Run. A `live` environment is refused here for
        the same reason `open` refuses it (ADR-0001 point 5).
        """
        self._refuse_live()
        block = self.environment_config
        if "factory" not in block:
            raise KeyError(
                f"Environment {self.environment!r} declares no 'factory'; "
                "an in-process Adapter needs one to build the Target"
            )
        # `tools` is optional: a Target that holds its tools behind its own factory, or
        # has none yet, declares none, and is handed an empty mapping (ticket 02).
        for key in ("factory", "tools"):
            if key in block:
                import_attribute(str(block[key]))

    def check_fixtures(self, fixtures: Sequence[Fixture]) -> str | None:
        """Preflight's question (phase-5 decision 12): can `open` hand these to the Target?

        Answered from the factory's signature, which is the only mechanism this Adapter
        has for applying a Fixture: imported, never called, so no session opens and no
        Target is built. The same rule `open` enforces, so the two cannot disagree.
        """
        if not fixtures:
            return None
        reference = str(self.environment_config["factory"])
        return _fixtures_refusal(import_attribute(reference), reference, len(fixtures))

    # --- session ---

    def open(self, trace: TraceWriter, *, fixtures: Sequence[Fixture] = ()) -> Session:
        """Build the instrumented Target and start a session (D5, ADR-0001 point 5)."""
        factory, reference, tools, options = self._target_parts()
        if fixtures:
            # Declared, so they must arrive: a Scenario whose Fixtures were silently
            # dropped would run against the wrong world and score as if it had not.
            refusal = _fixtures_refusal(factory, reference, len(fixtures))
            if refusal is not None:
                raise TypeError(refusal)
            options["fixtures"] = tuple(fixtures)

        emitter = TraceEmitter(trace, tool_kinds=self.tool_kinds)
        client, on_close = self._client(emitter)
        wrapped = {name: emitter.wrap(name, tool) for name, tool in tools.items()}
        respond = factory(client, wrapped, **options)

        # Only now: a refused open leaves no `fixture/applied` claiming a Fixture reached
        # a Target that never took it.
        for fixture in fixtures:
            trace.event(
                "fixture/applied",
                actor="adapter",
                fixture=fixture.name,
                mechanism="factory_option",
                detail={"kind": fixture.kind, **fixture.data},
            )
        return InProcessSession(respond, emitter, on_close=on_close)

    def observe(self, *, turn_timeout_s: float = DEFAULT_TURN_TIMEOUT_S) -> Observation:
        """The probe (decision 11): build the Target as `open` does, deliver
        `PROBE_MESSAGE` over `ObservingTransport`, and return what its first request
        carried. No Trace, no network, no Backend; a `live` environment is refused as `open`
        refuses it. Building and answering run on a daemon thread held to the environment's
        Turn timeout (phase-5 decision 56), so a Target that blocks is `ObservationFailed`,
        never a hung `sync`. `ObservationFailed` too when the factory cannot be built or
        raises, or the Target makes no model call; a Target that raises after its request
        was kept — a streaming one, whose loop cannot read the canned answer — is observed."""
        try:
            factory, _, tools, options = self._target_parts()
        except (ImportError, KeyError, TypeError, ValueError) as exc:
            raise ObservationFailed(f"{type(exc).__name__}: {exc}") from exc
        transport = ObservingTransport()
        client = anthropic.Anthropic(
            api_key=PROBE_API_KEY,
            max_retries=0,
            http_client=anthropic.DefaultHttpxClient(transport=transport),
        )

        def answer() -> None:
            factory(client, tools, **options)(PROBE_MESSAGE)

        try:
            within(answer, turn_timeout_s, name="agentdiag-sync-probe")
        except Overdue:
            raise ObservationFailed(
                f"the Target did not answer the probe; timed out after {turn_timeout_s:g} s"
            ) from None
        except Exception as exc:
            if transport.body is None:
                raise ObservationFailed(
                    f"the Target raised before its first model call: {type(exc).__name__}: {exc}"
                ) from exc
        finally:
            client.close()
        if transport.body is None:
            raise ObservationFailed("the Target answered the probe without calling a model")
        return observation_of(transport.body)

    def _target_parts(
        self,
    ) -> tuple[Callable[..., Any], str, dict[str, Callable[..., Any]], dict[str, Any]]:
        """What `open` and `observe` both build a Target from: the factory and its
        reference, the tools as the Manifest resolves them (fresh per call), and the
        options, the environment's `model` among them. A `live` environment is refused
        first (ADR-0001 point 5)."""
        self._refuse_live()
        block = self.environment_config
        reference = str(block["factory"])
        factory = import_attribute(reference)
        tools = _resolve_tools(import_attribute(str(block["tools"])) if "tools" in block else {})
        options = dict(block.get("options") or {})
        if "model" in block:
            options["model"] = block["model"]
        return factory, reference, tools, options

    def _refuse_live(self) -> None:
        if self.side_effects == "live" and not self.allow_live:
            raise LiveSideEffectsRefused(LIVE_REFUSAL.format(environment=self.environment))

    def _client(
        self, emitter: TraceEmitter
    ) -> tuple[anthropic.Anthropic, Callable[[], None] | None]:
        """The client the Target is handed, a real SDK client on a transport we own, and
        what the session must close when it ends.

        Live, Claude Code and replay differ only in what sits under the capture, so a Trial
        exercises the same code either way. Replay and Claude Code pin `max_retries=0`: a
        replay mismatch must fail on the first attempt rather than hide behind a retry, and
        the CLI already holds a user message a retry would send twice (decision 30).
        """
        if self.replay is not None:
            self._replay_transport = ReplayTransport(self.replay)
            inner: httpx2.BaseTransport = self._replay_transport
            transport = CapturingTransport(inner, emitter)
            client = anthropic.Anthropic(
                api_key="replay",
                max_retries=0,
                http_client=anthropic.DefaultHttpxClient(transport=transport),
            )
            return client, None
        cli = self._claude_code_cli
        if cli is not None:
            claude_code = ClaudeCodeTransport(cli, session=self.claude_code_session)
            transport = CapturingTransport(claude_code, emitter)
            # The key is a placeholder the SDK needs to build a request; the transport sends
            # no HTTP, and the login the CLI holds is what authenticates the call.
            client = anthropic.Anthropic(
                api_key="claude-code",
                max_retries=0,
                http_client=anthropic.DefaultHttpxClient(transport=transport),
            )
            return client, claude_code.close
        transport = CapturingTransport(httpx2.HTTPTransport(), emitter)
        # Live: default credential resolution and the SDK's default retries. A retried
        # request honestly appears as a second `request` Event, because the transport sees
        # each attempt.
        client = anthropic.Anthropic(http_client=anthropic.DefaultHttpxClient(transport=transport))
        return client, None

    def assert_consumed(self) -> None:
        """In replay, raise when the recording still held exchanges (D12)."""
        if self._replay_transport is not None:
            self._replay_transport.assert_consumed()


def _resolve_tools(tools: Any) -> dict[str, Callable[..., Any]]:
    """The tools for one session, from a mapping or from a callable that builds one.

    A Target whose tools hold state ships a zero-argument builder, so each session gets
    its own. One that is stateless may ship the mapping itself. Either way the Adapter
    calls this once per `open()` and nothing outside the Target resets anything.

    A Manifest with no `tools` key reaches here as an empty mapping, and the factory is
    handed one: a Target may keep its tools inside itself, and being asked to name a
    module path for tools agentdiag will never wrap would be a pointer to nothing.
    """
    if isinstance(tools, Mapping):
        return dict(tools)
    if callable(tools):
        built = tools()
        if not isinstance(built, Mapping):
            raise TypeError(
                f"The Manifest's tools callable returned {type(built).__name__}; "
                "it must return a mapping of tool name to callable"
            )
        return dict(built)
    raise TypeError(
        f"The Manifest's tools reference resolved to {type(tools).__name__}; "
        "it must be a mapping of tool name to callable, or a callable returning one"
    )


def _fixtures_refusal(factory: Callable[..., Any], reference: str, count: int) -> str | None:
    """Why a factory cannot receive a Scenario's Fixtures, or None when it can."""
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return None  # a factory whose signature cannot be read is given the benefit of doubt
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return None
        if parameter.name == "fixtures" and parameter.kind is not inspect.Parameter.POSITIONAL_ONLY:
            return None
    return f"Target factory {reference} does not accept fixtures; the Scenario declares {count}"


def _bind(
    tool: Callable[..., Any], args: tuple[Any, ...], kwargs: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, list[str]]:
    """Name a tool's arguments, or say they could not be seen.

    An Adapter that could not observe arguments must record "unknown", never "no
    arguments" (ADR-0001 consequences). So a binding failure gives `arguments: null` and
    `not_observed: ["arguments"]`, which no reader can mistake for an empty call.
    """
    try:
        signature = inspect.signature(tool)
        bound = signature.bind(*args, **kwargs)
    except (TypeError, ValueError):
        return None, ["arguments"]
    if _names_nothing(signature):
        # A tool declared `*args` or `**kwargs` swallows its arguments into a parameter
        # named `args` or `kwargs`, which names nothing a reader could cite. Binding
        # succeeded and the Adapter still did not see the arguments, so it says so.
        return None, ["arguments"]
    bound.apply_defaults()
    return {name: _jsonable(value) for name, value in bound.arguments.items()}, []


def _names_nothing(signature: inspect.Signature) -> bool:
    """Whether a signature has no parameter that names one real argument."""
    variadic = {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
    return all(parameter.kind in variadic for parameter in signature.parameters.values())


def _jsonable(value: Any) -> Any:
    """The JSON form of a value, or its `repr` when it has none."""
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value


def _body_text(response: httpx2.Response) -> str:
    try:
        return response.read().decode("utf-8", "replace")
    except Exception:  # pragma: no cover - a body that cannot be read at all
        return "<unreadable response body>"


__all__ = [
    "MESSAGES_PATH",
    "OBSERVES",
    "PROBE_API_KEY",
    "PROBE_MESSAGE",
    "CapturingTransport",
    "InProcessAdapter",
    "InProcessSession",
    "ModelCall",
    "Observation",
    "ObservationFailed",
    "ObservingTransport",
    "PendingToolUse",
    "SessionClosed",
    "TraceEmitter",
    "import_attribute",
    "observation_of",
]
