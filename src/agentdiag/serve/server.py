"""The HTTP server around `serve.app`: the standard library's, bound to 127.0.0.1 only.

The handler is thin: it refuses what did not come from this server's own page, reads the
body, hands the request line to `App.handle`, and writes the reply (JSON with its length,
the page, or a stream of server-sent events flushed as each is made).

**A page on any origin can make a browser POST to 127.0.0.1**, so a request is refused with
403 when its `Host` is not this server's own address (which also stops DNS rebinding), when
it carries an `Origin` that is not, or when the browser says it is `Sec-Fetch-Site:
cross-site`. A POST needs `Content-Type: application/json` (415 otherwise), which a
cross-origin form cannot send without a preflight, the header `X-Agentdiag-Request: 1`, and
the header `X-Agentdiag-Token` carrying the page token this process embedded in the page it
served; a follow (`/api/runs/<id>/follow`) takes the token as `?token=`, since an
EventSource sends no headers. No CORS header is ever sent, so no other origin can read an
answer, and the page may not be framed.

**What the token proves** (ADR-0011 §6d in the UI): that a request came from the page this
server rendered, so a name typed in its confirm step was typed there by a person. A local
process that reads the page to take its token is deliberately impersonating a person, as
faking a terminal is on the CLI, and is not what the rule defends against.
"""

from __future__ import annotations

import hmac
import re
import sys
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from agentdiag.exits import USAGE_EXIT
from agentdiag.serve.app import JSON_TYPE, App, Reply, error
from agentdiag.workspace import Workspace

HOST = "127.0.0.1"
DEFAULT_PORT = 7331
REQUEST_HEADER = "X-Agentdiag-Request"
TOKEN_HEADER = "X-Agentdiag-Token"
FOLLOW = re.compile(r"/api/runs/[^/]+/follow")
MAX_BODY_BYTES = 1 << 20
"""The largest request body read: every write's body is a few hundred bytes."""


class AgentdiagServer(ThreadingHTTPServer):
    """One `App` served on 127.0.0.1, each request on its own thread."""

    daemon_threads = True

    def __init__(self, app: App, port: int) -> None:
        self.app = app
        super().__init__((HOST, port), Handler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/"

    def own_hosts(self) -> set[str]:
        return {f"{HOST}:{self.port}", f"localhost:{self.port}"}

    def refusal(self, method: str, path: str, headers: dict[str, str]) -> Reply | None:
        """Why this request is not answered, or None: the checks the module docstring names."""
        own = self.own_hosts()
        if headers.get("sec-fetch-site") == "cross-site":
            return error(403, "a cross-site request is not answered: open the page itself")
        host = headers.get("host")
        if host not in own:
            return error(403, f"the request's Host {host!r} is not this server's ({self.url})")
        origin = headers.get("origin")
        if origin is not None and origin not in {f"http://{name}" for name in own}:
            return error(403, f"the request's Origin {origin!r} is not this server's page")
        if method == "POST":
            kind = (headers.get("content-type") or "").split(";")[0].strip().lower()
            if kind != JSON_TYPE:
                return error(415, f"a POST's body must be {JSON_TYPE}, not {kind or 'none'}")
            if headers.get(REQUEST_HEADER.lower()) != "1":
                return error(403, f"a POST must carry the header {REQUEST_HEADER}: 1")
            if not self._token_is(headers.get(TOKEN_HEADER.lower())):
                return error(403, f"a POST must carry this server's page token as {TOKEN_HEADER}")
        split = urlsplit(path)
        if FOLLOW.fullmatch(split.path):
            tokens = parse_qs(split.query).get("token") or []
            if not self._token_is(tokens[-1] if tokens else None):
                return error(403, "a follow must carry this server's page token as ?token=")
        return None

    def _token_is(self, given: str | None) -> bool:
        return given is not None and hmac.compare_digest(given, self.app.token)


class Handler(BaseHTTPRequestHandler):
    """Refuse, read, hand to the app, write: nothing else."""

    server: AgentdiagServer
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:
        self._serve("GET")

    def do_POST(self) -> None:
        self._serve("POST")

    def do_PUT(self) -> None:
        self._write(error(405, "this server answers GET and POST only"))

    do_DELETE = do_PATCH = do_PUT

    def _serve(self, method: str) -> None:
        headers = {name.lower(): value for name, value in self.headers.items()}
        refused = self.server.refusal(method, self.path, headers)
        if refused is not None:
            self._write(refused)
            return
        body = None
        if method == "POST":
            try:
                length = int(headers.get("content-length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY_BYTES:
                self._write(error(413, f"a request body must be 0 to {MAX_BODY_BYTES} bytes"))
                return
            body = self.rfile.read(length)
        self._write(self.server.app.handle(method, self.path, body))

    def _write(self, reply: Reply) -> None:
        try:
            self.send_response(reply.status)
            self.send_header("Content-Type", reply.content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            if reply.events is None:
                payload = reply.encoded()
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            self.end_headers()
            for frame in reply.events:
                self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return  # the page went away; a followed Run goes on without it
        finally:
            if reply.events is not None:
                close = getattr(reply.events, "close", None)
                if close is not None:
                    close()

    def log_message(self, format: str, *args: object) -> None:
        """Quiet: the terminal shows the URL and errors, not every request."""


def _say(text: str) -> None:
    print(text, flush=True)  # before serve_forever, so a piped reader sees the URL at once


def make_server(workspace: Workspace, port: int = DEFAULT_PORT) -> AgentdiagServer:
    """The server for `workspace` on 127.0.0.1:`port` (0 picks a free port), bound, not
    yet serving. `OSError` when the port is taken."""
    return AgentdiagServer(App(workspace), port)


def serve(
    workspace: Workspace,
    *,
    port: int = DEFAULT_PORT,
    open_browser: bool = False,
    echo: Callable[[str], None] | None = None,
) -> int:
    """`agentdiag serve`: bind, print the URL, open it when asked, serve until Ctrl-C.
    The exit code: 0 after Ctrl-C, 3 (`USAGE_EXIT`) when the port cannot be bound."""
    try:
        server = make_server(workspace, port)
    except OSError as exc:
        print(f"error: cannot listen on {HOST}:{port}: {exc}", file=sys.stderr)
        return USAGE_EXIT
    (echo or _say)(f"agentdiag serve: {server.url} (Workspace {workspace.root}); Ctrl-C stops it")
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.app.closing.set()
        server.server_close()
    return 0


__all__ = [
    "DEFAULT_PORT",
    "HOST",
    "REQUEST_HEADER",
    "TOKEN_HEADER",
    "AgentdiagServer",
    "make_server",
    "serve",
]
