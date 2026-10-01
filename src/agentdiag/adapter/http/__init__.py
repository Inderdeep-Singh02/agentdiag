"""The HTTP Adapter: a Target driven through the chat endpoint it ships (ticket 17, phase-8
decision 1).

`config` validates and resolves one environment block (identifiers, credentials by name,
identity modes); `dialect` says what a Dialect is — a platform's framing of one Turn — and the
Frames it reads; `dialects` holds core's two, `json` and `sse-json`; `sse` reads server-sent events
off a byte stream for any Dialect over SSE; `session` is the Adapter, its session, the
transport seam and the recorder of what one Turn showed.

Registered as `http` under `agentdiag.adapters` and the two Dialects under
`agentdiag.dialects` in `pyproject.toml`. Offline at import: no SDK, no `anthropic`, so
`validate` may import it.
"""

from agentdiag.adapter.http.config import (
    HttpEnvironment,
    HttpEnvironmentError,
    IdentityMode,
    ResolvedHttpEnvironment,
    resolve_http_environment,
)
from agentdiag.adapter.http.dialect import (
    DIALECT_GROUP,
    Dialect,
    DialectError,
    Frame,
    HttpRequest,
    HttpResponse,
    dialect_class,
)
from agentdiag.adapter.http.session import (
    FixtureNotApplicable,
    HttpAdapter,
    HttpSession,
    HttpTransport,
    TargetResponseError,
    TurnRecorder,
    UrllibTransport,
)

__all__ = [
    "DIALECT_GROUP",
    "Dialect",
    "DialectError",
    "FixtureNotApplicable",
    "Frame",
    "HttpAdapter",
    "HttpEnvironment",
    "HttpEnvironmentError",
    "HttpRequest",
    "HttpResponse",
    "HttpSession",
    "HttpTransport",
    "IdentityMode",
    "ResolvedHttpEnvironment",
    "TargetResponseError",
    "TurnRecorder",
    "UrllibTransport",
    "dialect_class",
    "resolve_http_environment",
]
