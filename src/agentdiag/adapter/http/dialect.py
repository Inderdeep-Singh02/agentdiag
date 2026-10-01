"""What a Dialect is: how a platform frames one Turn's request and its streamed reply
(phase-8 decision 5, ADR-0014 §2).

An HTTP Target's endpoint is one POST per Turn, but every platform frames it its own way:
the body's shape, where the conversation id travels, how a streamed reply marks text, a
tool, an error, the end. A **Dialect** is that framing and nothing else. It builds the
request from the message, the conversation it continues and the resolved environment, and it
reads the response into **Frames** — `TextFrame`, `ToolFrame`, `ConversationFrame`,
`ErrorFrame`, `EndFrame` — as they arrive.

A Dialect is a pure function of its inputs: no I/O, no clock. `frames` is a generator over
the response's `iter_bytes`, so the Adapter, not the Dialect, timestamps each Frame as it
arrives (the observed first-frame latency, decision 7) and decides what a Frame becomes in
the Trace. Platform facts (a platform's line prefixes, its conversation header, a bare token
in `authorization`) never appear in core: they are a plugin's Dialect (ticket 30). Core ships
two, `json` and `sse-json` (`agentdiag.adapter.http.dialects`), registered by entry point
under `agentdiag.dialects` and found by `dialect_class`, as an Adapter kind is.

**Deviation, recorded for the contract:** `request` takes `identity`, the body-mode identity
data (decision 6), as a keyword the decision's signature does not show: the body mode says
"the Dialect places it", and the Dialect has no other way to be handed it.

`HttpRequest` may carry a credential value in a header — the Dialect builds it from
`env.credentials[role]` — so it is never dumped, printed or written: the Trace records the
path sent and header *names* only.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast, runtime_checkable

from pydantic import BaseModel, Field

from agentdiag.connector.plugins import DIALECT_GROUP, dialect_class

if TYPE_CHECKING:
    from agentdiag.adapter.http.config import IdentityMode, ResolvedHttpEnvironment


class HttpRequest(BaseModel):
    """One Turn's request as a Dialect framed it. Never dumped, printed or written."""

    method: Literal["POST", "GET"] = "POST"
    path: str
    """The path sent, identifiers interpolated, starting with `/`; no host, no query."""

    headers: dict[str, str] = Field(default_factory=dict, repr=False)
    body: bytes | None = Field(default=None, repr=False)


@runtime_checkable
class HttpResponse(Protocol):
    """What a transport hands back: the status, the headers (names lower-cased), and the
    body as it arrives."""

    status: int
    headers: Mapping[str, str]

    def iter_bytes(self) -> Iterator[bytes]:
        """The body in the chunks the connection delivers them."""
        ...


class TextFrame(BaseModel):
    """Some of the reply's text; the reply is every `TextFrame` joined."""

    text: str


class ToolFrame(BaseModel):
    """The stream reports a tool ran: its name, and whatever else it reported."""

    name: str
    arguments: dict[str, Any] | None = None
    """None: the stream did not show them — unknown, never empty (ADR-0001)."""

    result: Any = None
    call_id: str | None = None


class ConversationFrame(BaseModel):
    """The conversation id the Target holds this chat under."""

    id: str


class ErrorFrame(BaseModel):
    """The Target reported an error inside the stream."""

    error_type: str
    message: str


class EndFrame(BaseModel):
    """The reply is complete."""

    reason: str | None = None


Frame = TextFrame | ToolFrame | ConversationFrame | ErrorFrame | EndFrame

FRAME_TYPES: Mapping[type[BaseModel], str] = {
    TextFrame: "text",
    ToolFrame: "tool",
    ConversationFrame: "conversation",
    ErrorFrame: "error",
    EndFrame: "end",
}
"""Each Frame's name in `agentdiag.frames`, the count by type a `response` Span records."""


class DialectError(RuntimeError):
    """A response the Dialect cannot read; the Turn ends `error` with it."""


@runtime_checkable
class Dialect(Protocol):
    """A platform's framing of one Turn: the request, and the reply read as Frames."""

    name: str

    def request(
        self,
        message: str,
        *,
        conversation: str | None,
        env: ResolvedHttpEnvironment,
        identity: Mapping[str, Any] | None = None,
    ) -> HttpRequest:
        """The request that delivers `message`, continuing `conversation` when given.
        `identity` is the body-mode identity data, which the Dialect places (decision 6)."""
        ...

    def frames(self, response: HttpResponse) -> Iterator[Frame]:
        """The reply read as Frames, lazily, as `response.iter_bytes()` yields; raises
        `DialectError` on what it cannot read."""
        ...


def load_dialect(name: str) -> Dialect:
    """The Dialect registered under `name`, constructed; `UnknownKind` when none is, and
    `TypeError` when what is registered is not a Dialect."""
    dialect = dialect_class(name)()
    if not isinstance(dialect, Dialect):
        raise TypeError(f"the Dialect registered as {name!r} is not a Dialect")
    return dialect


def dialect_identity_modes(dialect: Dialect) -> Mapping[str, IdentityMode]:
    """The identity modes a Dialect offers beyond core's (decision 6), or none."""
    modes = getattr(dialect, "identity_modes", None)
    return cast("Mapping[str, IdentityMode]", modes) if isinstance(modes, Mapping) else {}


__all__ = [
    "DIALECT_GROUP",
    "FRAME_TYPES",
    "ConversationFrame",
    "Dialect",
    "DialectError",
    "EndFrame",
    "ErrorFrame",
    "Frame",
    "HttpRequest",
    "HttpResponse",
    "TextFrame",
    "ToolFrame",
    "dialect_class",
    "dialect_identity_modes",
    "load_dialect",
]
