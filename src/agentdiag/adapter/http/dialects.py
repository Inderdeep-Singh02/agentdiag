"""Core's two Dialects, `json` and `sse-json`: agentdiag's own shapes, no platform's
(phase-8 decision 5).

Both send the same request, a JSON body `{"message": …, "conversation_id": …}` posted to the
environment's `path` (`/chat` when it names none), with `identity: {…}` beside `message`
when the body identity mode is in force (decision 6), and `authorization: Bearer <token>`
when the environment names a `token` credential (nothing when it names none). They differ
in the reply:

- **`json`**: one JSON object, `{"reply": str, "conversation_id"?: str, "tools"?: [{"name",
  "arguments"?, "result"?, "call_id"?}]}`, read once the body has arrived.
- **`sse-json`**: `text/event-stream` whose `data:` lines are JSON frames `{"type": "text" |
  "tool" | "conversation" | "error" | "done", …}` with each frame's fields under their own
  names (`text`; `name`, `arguments`, `result`, `call_id`; `id`; `error_type`, `message`;
  `reason`); a `data: [DONE]` line also ends it. Line reading is `sse.read_sse`.

Either raises `DialectError` for a body it cannot read — not JSON, not the shape, not UTF-8,
or a stream that ends holding no frame — and the Adapter ends the Turn `error` with it.

Neither is a real platform's framing, which is the point: a test Target speaks them
(`tests/fakes/http_target.py`), and a platform's own Dialect is its plugin's (ticket 30).
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

from agentdiag.adapter.http.config import ResolvedHttpEnvironment
from agentdiag.adapter.http.dialect import (
    ConversationFrame,
    DialectError,
    EndFrame,
    ErrorFrame,
    Frame,
    HttpRequest,
    HttpResponse,
    TextFrame,
    ToolFrame,
)
from agentdiag.adapter.http.sse import read_sse

DEFAULT_PATH = "/chat"
"""Where both core Dialects post a Turn when the environment names no `path`."""

TOKEN_ROLE = "token"
"""The credential role both core Dialects send as `authorization: Bearer <token>`."""

DONE = "[DONE]"
"""The `data` line that also ends an `sse-json` stream."""


def json_request(
    message: str,
    *,
    conversation: str | None,
    env: ResolvedHttpEnvironment,
    identity: Mapping[str, Any] | None,
    accept: str,
) -> HttpRequest:
    """The one request both core Dialects send."""
    body: dict[str, Any] = {"message": message, "conversation_id": conversation}
    if identity is not None:
        body["identity"] = dict(identity)
    headers = {"content-type": "application/json", "accept": accept}
    token = env.credentials.get(TOKEN_ROLE)
    if token:
        headers["authorization"] = f"Bearer {token}"
    return HttpRequest(
        path=env.interpolate(env.path or DEFAULT_PATH),
        headers=headers,
        body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
    )


def _tool_frame(raw: Any, where: str) -> ToolFrame:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("name"), str):
        raise DialectError(f"{where} is not a tool with a name")
    arguments = raw.get("arguments")
    if arguments is not None and not isinstance(arguments, Mapping):
        raise DialectError(f"{where}'s arguments are not an object")
    call_id = raw.get("call_id")
    return ToolFrame(
        name=str(raw["name"]),
        arguments=dict(arguments) if arguments is not None else None,
        result=raw.get("result"),
        call_id=str(call_id) if call_id is not None else None,
    )


class JsonDialect:
    """One JSON object per reply (decision 5)."""

    name = "json"

    def request(
        self,
        message: str,
        *,
        conversation: str | None,
        env: ResolvedHttpEnvironment,
        identity: Mapping[str, Any] | None = None,
    ) -> HttpRequest:
        return json_request(
            message,
            conversation=conversation,
            env=env,
            identity=identity,
            accept="application/json",
        )

    def frames(self, response: HttpResponse) -> Iterator[Frame]:
        raw = b"".join(response.iter_bytes())
        try:
            reply = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DialectError(f"the json Dialect read a body that is not JSON: {exc}") from None
        if not isinstance(reply, Mapping) or not isinstance(reply.get("reply"), str):
            raise DialectError("the json Dialect read JSON with no string `reply`")
        conversation = reply.get("conversation_id")
        if conversation is not None:
            yield ConversationFrame(id=str(conversation))
        tools = reply.get("tools") or []
        if not isinstance(tools, list):
            raise DialectError("the json Dialect read `tools` that is not a list")
        for index, tool in enumerate(tools):
            yield _tool_frame(tool, f"tools[{index}]")
        yield TextFrame(text=str(reply["reply"]))
        yield EndFrame()


class SseJsonDialect:
    """JSON frames over server-sent events (decision 5)."""

    name = "sse-json"

    def request(
        self,
        message: str,
        *,
        conversation: str | None,
        env: ResolvedHttpEnvironment,
        identity: Mapping[str, Any] | None = None,
    ) -> HttpRequest:
        return json_request(
            message,
            conversation=conversation,
            env=env,
            identity=identity,
            accept="text/event-stream",
        )

    def frames(self, response: HttpResponse) -> Iterator[Frame]:
        read = 0
        try:
            for event in read_sse(response.iter_bytes()):
                if event.data.strip() == DONE:
                    yield EndFrame(reason="done")
                    return
                frame = _sse_frame(event.data)
                read += 1
                yield frame
                if isinstance(frame, EndFrame):
                    return
        except UnicodeDecodeError as exc:
            raise DialectError(
                f"the sse-json Dialect read bytes that are not UTF-8: {exc}"
            ) from None
        if read == 0:
            raise DialectError("the stream ended holding no frame the sse-json Dialect reads")


def _sse_frame(data: str) -> Frame:
    try:
        raw = json.loads(data)
    except json.JSONDecodeError as exc:
        raise DialectError(
            f"the sse-json Dialect read a data line that is not JSON: {exc}"
        ) from None
    if not isinstance(raw, Mapping):
        raise DialectError("the sse-json Dialect read a data line that is not a JSON object")
    kind = raw.get("type")
    if kind == "text" and isinstance(raw.get("text"), str):
        return TextFrame(text=str(raw["text"]))
    if kind == "tool":
        return _tool_frame(raw, "a tool frame")
    if kind == "conversation" and raw.get("id") is not None:
        return ConversationFrame(id=str(raw["id"]))
    if kind == "error":
        return ErrorFrame(
            error_type=str(raw.get("error_type") or "error"), message=str(raw.get("message") or "")
        )
    if kind == "done":
        reason = raw.get("reason")
        return EndFrame(reason=str(reason) if reason is not None else None)
    raise DialectError(f"the sse-json Dialect read a frame it does not know: type {kind!r}")


__all__ = ["DEFAULT_PATH", "DONE", "TOKEN_ROLE", "JsonDialect", "SseJsonDialect", "json_request"]
