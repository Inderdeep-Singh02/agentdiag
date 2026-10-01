"""A chat endpoint on 127.0.0.1 that speaks core's two Dialects (phase-8 decision 10).

The HTTP Adapter's tests need a Target whose every reply they wrote: the text, the tools the
stream reports (with arguments or without, with a result or without), a conversation id or
none, a status, a delay, bytes no Dialect can read. `FakeHttpTarget` is a
`ThreadingHTTPServer` on a free loopback port that answers each POST with the next `Reply` of
its script (the last one again once the script runs out) in the `json` or the `sse-json`
shape, and records every request it saw as a `SeenRequest`: the method, the path, the header
*names*, the values of the headers it was told to echo (an identity header), whether the
token matched, and the parsed body. So a test asserts the identity header was sent, the
conversation id came back, and the token travelled as a header — and then that it appears in
no file agentdiag wrote.

`serving_target(...)` starts it in a thread and stops it after. `http_workspace` writes a
Workspace whose one Target is the reference Manifest (`tests/fixtures/manifests/
http-echo.yaml`) pointed at a serving fake, with whatever a test changes in it. Nothing but
the loopback is ever reached.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

import yaml

from agentdiag.workspace import Workspace

REPO = Path(__file__).resolve().parents[2]
MANIFESTS = REPO / "tests" / "fixtures" / "manifests"
REFERENCE_MANIFEST = MANIFESTS / "http-echo.yaml"
REFERENCE_SUITE = MANIFESTS / "suites" / "http-echo.yaml"
SLUG = "http-echo"
TOKEN_VARIABLE = "HTTP_ECHO_TOKEN"
IDENTITY_HEADER = "x-user-phone"

DialectName = Literal["json", "sse-json"]


@dataclass(frozen=True)
class Tool:
    """A tool the reply reports: its name, and its arguments and result when it shows them."""

    name: str
    arguments: dict[str, Any] | None = None
    result: Any = None
    call_id: str | None = None

    def fields(self) -> dict[str, Any]:
        shown: dict[str, Any] = {"name": self.name}
        for key in ("arguments", "result", "call_id"):
            value = getattr(self, key)
            if value is not None:
                shown[key] = value
        return shown


@dataclass(frozen=True)
class Reply:
    """One Turn's answer, as the script says it."""

    text: str = "Hello from the fake Target."
    tools: Sequence[Tool] = ()
    conversation: str | None = None
    status: int = 200
    delay_s: float = 0.0
    garbage: bytes | None = None
    """Sent as the body instead of any Dialect's shape: what no Dialect can read."""

    error_body: bytes | None = None
    """The body of a non-200 reply, instead of the default `{"error": "status N"}`."""

    error: tuple[str, str] | None = None
    """An `sse-json` error frame (type, message) sent after the text."""


@dataclass
class SeenRequest:
    """What the fake saw of one request: names, never a credential's value."""

    method: str
    path: str
    header_names: list[str]
    echoed: dict[str, str]
    authorized: bool | None
    """Whether `authorization` carried the required token; None when none is required."""

    body: Any = None


@dataclass
class FakeHttpTarget:
    """The server, its script and what it saw."""

    dialect: DialectName
    script: Sequence[Reply] = (Reply(),)
    require_token: str | None = None
    echo_headers: Sequence[str] = ()
    requests: list[SeenRequest] = field(default_factory=list)
    served: int = 0
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def base_url(self) -> str:
        assert self._server is not None, "the fake is not serving"
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    def record(self, seen: SeenRequest) -> None:
        with self._lock:
            self.requests.append(seen)

    def next_reply(self) -> Reply:
        with self._lock:
            reply = self.script[min(self.served, len(self.script) - 1)]
            self.served += 1
            return reply

    def handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                return None

            def do_POST(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    body = raw.decode("utf-8", "replace")
                authorized = (
                    None
                    if fake.require_token is None
                    else self.headers.get("authorization") == f"Bearer {fake.require_token}"
                )
                fake.record(
                    SeenRequest(
                        method="POST",
                        path=self.path,
                        header_names=sorted(name.lower() for name in self.headers),
                        echoed={
                            name: self.headers[name]
                            for name in fake.echo_headers
                            if self.headers.get(name) is not None
                        },
                        authorized=authorized,
                        body=body,
                    )
                )
                if authorized is False:
                    self._send(401, "application/json", b'{"error": "unauthorized"}')
                    return
                reply = fake.next_reply()
                if reply.delay_s:
                    time.sleep(reply.delay_s)
                if reply.status != 200:
                    payload = (
                        reply.error_body or json.dumps({"error": f"status {reply.status}"}).encode()
                    )
                    self._send(reply.status, "application/json", payload)
                elif fake.dialect == "json":
                    self._json(reply)
                else:
                    self._sse(reply)

            def _send(self, status: int, content_type: str, payload: bytes) -> None:
                self.send_response(status)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _json(self, reply: Reply) -> None:
                if reply.garbage is not None:
                    self._send(200, "application/json", reply.garbage)
                    return
                answer: dict[str, Any] = {"reply": reply.text}
                if reply.conversation is not None:
                    answer["conversation_id"] = reply.conversation
                if reply.tools:
                    answer["tools"] = [tool.fields() for tool in reply.tools]
                self._send(200, "application/json", json.dumps(answer).encode())

            def _sse(self, reply: Reply) -> None:
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("connection", "close")
                self.end_headers()
                if reply.garbage is not None:
                    self.wfile.write(reply.garbage)
                    self.wfile.flush()
                    self.close_connection = True
                    return
                frames: list[dict[str, Any]] = []
                if reply.conversation is not None:
                    frames.append({"type": "conversation", "id": reply.conversation})
                frames += [{"type": "tool", **tool.fields()} for tool in reply.tools]
                half = len(reply.text) // 2
                for part in (reply.text[:half], reply.text[half:]):
                    if part:
                        frames.append({"type": "text", "text": part})
                if reply.error is not None:
                    frames.append(
                        {"type": "error", "error_type": reply.error[0], "message": reply.error[1]}
                    )
                self.wfile.write(b": a comment line the reader skips\r\n\r\n")
                for frame in frames:
                    self.wfile.write(f"data: {json.dumps(frame)}\r\n\r\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True

        return Handler

    def start(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


@contextmanager
def serving_target(
    dialect: DialectName,
    *,
    script: Sequence[Reply] = (Reply(),),
    require_token: str | None = None,
    echo_headers: Sequence[str] = (),
) -> Iterator[FakeHttpTarget]:
    """A `FakeHttpTarget` serving for the length of a `with` block; its `base_url` is where."""
    fake = FakeHttpTarget(
        dialect, script=script, require_token=require_token, echo_headers=echo_headers
    )
    fake.start()
    try:
        yield fake
    finally:
        fake.stop()


def http_workspace(
    root: Path,
    base_url: str,
    *,
    adapter: dict[str, Any] | None = None,
    environment: dict[str, Any] | None = None,
    drop: Sequence[str] = (),
    manifest: dict[str, Any] | None = None,
    suite: dict[str, Any] | None = None,
) -> Path:
    """A Workspace at `root` whose one Target is the reference HTTP Manifest at `base_url`:
    `adapter` updates the Adapter block, `environment` the `dev` block (`drop` removes keys
    from it), `manifest` the top level; `suite` replaces the reference Suite. The root back."""
    loaded = yaml.safe_load(REFERENCE_MANIFEST.read_text(encoding="utf-8"))
    block = loaded["adapter"]["environments"]["dev"]
    block["base_url"] = base_url
    block.update(environment or {})
    for key in drop:
        block.pop(key, None)
    loaded["adapter"].update(adapter or {})
    loaded.update(manifest or {})
    directory = root / ".agentdiag" / "targets" / SLUG
    (directory / "suites").mkdir(parents=True)
    (directory / "manifest.yaml").write_text(yaml.safe_dump(loaded), encoding="utf-8")
    (directory / "suites" / "http-echo.yaml").write_text(
        yaml.safe_dump(suite) if suite is not None else REFERENCE_SUITE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    Workspace.find(root).resolve(SLUG)
    return root


def unreachable_url() -> str:
    """A loopback URL nothing listens on: a port bound, then released."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


__all__ = [
    "IDENTITY_HEADER",
    "REFERENCE_MANIFEST",
    "REFERENCE_SUITE",
    "SLUG",
    "TOKEN_VARIABLE",
    "DialectName",
    "FakeHttpTarget",
    "Reply",
    "SeenRequest",
    "Tool",
    "http_workspace",
    "serving_target",
    "unreachable_url",
]
