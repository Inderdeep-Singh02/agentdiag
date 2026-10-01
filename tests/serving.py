"""`agentdiag serve` on a real socket, for the tests that go through HTTP (ticket 16).

`serving` starts the server on a free port in a thread and stops it after; `call` makes one
request the way the page's script does (a JSON POST carries `Content-Type:
application/json`, `X-Agentdiag-Request: 1` and the page token as `X-Agentdiag-Token`), and
`events` reads a server-sent event stream to its end, the token in its query as the page's
EventSource sends it. Nothing here fakes the server: the handler, the refusals and the routes
are the ones `agentdiag serve` runs.
"""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from agentdiag.serve.server import AgentdiagServer, make_server
from agentdiag.workspace import Workspace

PAGE_HEADERS = {"Content-Type": "application/json", "X-Agentdiag-Request": "1"}


@dataclass
class Answer:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


@contextmanager
def serving(workspace: Workspace) -> Iterator[AgentdiagServer]:
    server = make_server(workspace, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.app.closing.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def call(
    server: AgentdiagServer,
    method: str,
    path: str,
    body: Any = None,
    *,
    headers: dict[str, str] | None = None,
    page: bool = True,
) -> Answer:
    """One request; a POST carries the page's two headers unless `page` is False."""
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=120)
    own = {**PAGE_HEADERS, "X-Agentdiag-Token": server.app.token}
    sent = {**(own if method == "POST" and page else {}), **(headers or {})}
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    try:
        connection.request(method, path, body=payload, headers=sent)
        response = connection.getresponse()
        return Answer(
            response.status,
            {name.lower(): value for name, value in response.getheaders()},
            response.read(),
        )
    finally:
        connection.close()


def events(
    server: AgentdiagServer, path: str, *, comments: list[str] | None = None
) -> list[tuple[str, Any]]:
    """Every server-sent event of the stream at `path`, as (name, data), until it closes;
    the page token is added to the query. Comment lines go to `comments` when given."""
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=120)
    joined = "&" if "?" in path else "?"
    try:
        connection.request("GET", f"{path}{joined}token={server.app.token}")
        response = connection.getresponse()
        assert response.status == 200, response.read()
        assert response.getheader("Content-Type") == "text/event-stream"
        found: list[tuple[str, Any]] = []
        name, data = None, None
        for raw in response:
            line = raw.decode("utf-8").rstrip("\n")
            if line.startswith(":") and comments is not None:
                comments.append(line)
            elif line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
            elif line == "" and name is not None:
                found.append((name, data))
                name, data = None, None
        return found
    finally:
        connection.close()


__all__ = ["PAGE_HEADERS", "Answer", "call", "events", "serving"]
