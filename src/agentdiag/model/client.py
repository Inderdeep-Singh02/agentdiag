"""agentdiag's own model boundary: one request shape, one response shape, three clients (D12).

A fourth, `ClaudeCodeClient`, lives in `agentdiag.model.claude_code` (ticket 19): the same
seam, reached through the Claude Code CLI rather than the Messages API.

The Judge, and later the Simulated User and the reviewer, never touch the Anthropic SDK.
They build a `ModelRequest` and hand it to a `ModelClient`. Three things follow:

- **The request body is the recording key.** `ModelRequest.body()` is the exact JSON that
  goes on the wire, so a recorded exchange matches by construction (phase-4 interfaces,
  decision 5) — the same property the Target-side replay transport gets by sitting under
  the SDK.
- **A response records what the API resolved, not what was asked for.** `requested_model`
  and `resolved_model` are separate fields because an alias makes them differ, and a
  Verdict from an alias is not reproducible (D12).
- **Nothing is assumed about a parameter's support.** Only the Simulated User sends
  sampling parameters, and only as declared (`--simulated-user-temperature`); a 400 naming
  one maps it to `not_supported` and re-raises `SamplingNotSupported`. The client never
  retries: the Simulated User resends once without the refused key, in a Span of its own
  (`agentdiag.simulate.user`, phase-5 decision 53).

`ModelResponse.body` is the wire body, not a re-serialised SDK object, so a `response`
Event in `judgement.jsonl` says exactly what the API said.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Protocol, get_args

import anthropic
from pydantic import BaseModel, Field

from agentdiag.model.replay import Cursor
from agentdiag.types import SamplingSupport

Effort = Literal["low", "medium", "high", "xhigh", "max"]
"""The effort levels `output_config.effort` takes; part of the Judge configuration (D14)."""

EFFORT_LEVELS = frozenset(get_args(Effort))
"""The same set as data, so a `--judge-effort` typo is a message rather than a 400."""

SAMPLING_KEYS = ("temperature", "top_p", "top_k")
"""The parameters whose support is recorded per call. None is sent in v1 (D12)."""


class ModelRequest(BaseModel):
    """One call agentdiag is about to make, in the shape the API takes."""

    model: str
    max_tokens: int
    system: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] | None = None
    output_schema: dict[str, Any] | None = None
    """A JSON Schema for structured output (D13); becomes `output_config.format`."""

    effort: Effort | None = None
    sampling: dict[str, Any] = Field(default_factory=dict)
    """Sampling parameters to send. Empty in v1: the Claude 5 models reject them."""

    fingerprint: str | None = None
    """The Fingerprint of the configuration making the call (ADR-0003 §8, ticket 21,
    decision 40): the Judge's today, set by `Judge`, and the Simulated User's when it has
    one. Never part of `body()`: the body is the wire request and the recording key, and a
    key that moved with the Backend would stop every replay matching. It is how a client
    that records a request can record which configuration made it."""

    def body(self) -> dict[str, Any]:
        """The exact API request body — and so the key a recorded exchange matches on.

        Keys are only present when they carry something, because an absent key and a
        null one are different requests to the API and would be different recording keys.
        """
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
        }
        if self.system is not None:
            body["system"] = self.system
        body["messages"] = [dict(message) for message in self.messages]
        if self.tools is not None:
            body["tools"] = [dict(tool) for tool in self.tools]
        output_config: dict[str, Any] = {}
        if self.output_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": self.output_schema}
        if self.effort is not None:
            output_config["effort"] = self.effort
        if output_config:
            body["output_config"] = output_config
        body.update(self.sampling)
        return body


class ModelResponse(BaseModel):
    """What one call returned, with the facts a Score's `source` needs (D12, D13)."""

    requested_model: str
    resolved_model: str
    content: list[dict[str, Any]] = Field(default_factory=list)
    stop_reason: str
    stop_details: dict[str, Any] | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    sampling_accepted: dict[str, SamplingSupport] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)
    """The exact API response body, as the `response` Event carries it (ADR-0004 §1). The
    Claude Code backend's is reassembled in the same layout (ticket 19, decision 23)."""

    structured_output: Any | None = None
    """The answer to a structured-output schema as the backend parsed it, when it reports
    one (the Claude Code CLI does, from `body.structured_output`); the Messages API puts
    the answer in a text block instead, and this stays None (decision 23)."""

    reported_cost_usd: float | None = None
    """What the backend said this call cost (`body.claude_code.total_cost_usd`); None for
    the Messages API, which bills later. Checked against `prices.py` on the Judge's Span,
    never used in its place (decision 27)."""

    def text(self) -> str:
        """Every text block, concatenated: what a structured output is parsed from."""
        return "".join(
            str(block.get("text", "")) for block in self.content if block.get("type") == "text"
        )

    @classmethod
    def from_body(cls, request: ModelRequest, body: dict[str, Any]) -> ModelResponse:
        """Read a response body — the wire's, or the Claude Code backend's reassembled one
        — into the shape agentdiag records."""
        reported = (body.get("claude_code") or {}).get("total_cost_usd")
        return cls(
            requested_model=request.model,
            resolved_model=str(body.get("model", request.model)),
            content=[dict(block) for block in body.get("content", [])],
            stop_reason=str(body.get("stop_reason") or "end_turn"),
            stop_details=body.get("stop_details"),
            usage=dict(body.get("usage") or {}),
            sampling_accepted=dict.fromkeys(request.sampling, "accepted"),
            body=body,
            structured_output=body.get("structured_output"),
            reported_cost_usd=float(reported) if isinstance(reported, int | float) else None,
        )


class SamplingNotSupported(RuntimeError):
    """The API refused a sampling parameter that was sent (D12).

    Carries which one, so the refusal is recorded as a fact about that parameter rather
    than as an opaque 400. The Claude Code client raises it too, for any sampling at all,
    before a call is made (decision 53). The Judge sends none; the Simulated User resends
    once without the refused keys, and a second refusal is `simulated_user_error`.
    """

    def __init__(self, sampling_accepted: dict[str, SamplingSupport], cause: Exception) -> None:
        self.sampling_accepted = sampling_accepted
        super().__init__(str(cause))


class ModelClient(Protocol):
    """The only way agentdiag calls a model (D12)."""

    def complete(self, request: ModelRequest) -> ModelResponse: ...


class LiveAnthropicClient:
    """The real API, through the installed SDK.

    `with_raw_response` rather than `create`: `.text()` gives the exact JSON the API
    returned, so `ModelResponse.body` is the wire body and a recording made here replays
    against the same bytes that produced it — the same property the Target-side transport
    gets by sitting under the SDK.
    """

    def __init__(self, anthropic_client: anthropic.Anthropic | None = None) -> None:
        self.client = anthropic_client if anthropic_client is not None else anthropic.Anthropic()

    def complete(self, request: ModelRequest) -> ModelResponse:
        body = request.body()
        try:
            raw = self.client.messages.with_raw_response.create(**body)
        except anthropic.BadRequestError as exc:
            rejected = _rejected_sampling_key(exc, request)
            if rejected is None:
                raise
            # Recorded on the way past, never retried here: the Simulated User owns the
            # resend, in a Span of its own, and a silent retry would hide which parameter
            # the API refused (D12, decision 53).
            raise SamplingNotSupported({rejected: "not_supported"}, exc) from exc
        # `.text` is a method on 1.7.0's APIResponse, and it is the exact JSON the API
        # returned — so `ModelResponse.body` is the wire body, as the recording needs.
        return ModelResponse.from_body(request, json.loads(raw.text()))


class ReplayModelClient:
    """Serves recorded exchanges, and fails loud on anything else (D12).

    Takes a `ReplayCursor`, not a path: `run --replay` drives the Target's transport and
    the Judge from one cursor over one recording, so `assert_consumed()` is asked once per
    Trial across both.
    """

    def __init__(self, cursor: Cursor) -> None:
        self.cursor = cursor

    def complete(self, request: ModelRequest) -> ModelResponse:
        body = request.body()
        exchange = self.cursor.take(body)
        return ModelResponse.from_body(request, dict(exchange.response))

    def assert_consumed(self) -> None:
        """Raise `RecordingNotConsumed` when any exchange went unused."""
        self.cursor.assert_consumed()


class RecordingModelClient:
    """Wraps a live client and writes what passed through, so fixtures come from reality.

    This is how a maintainer with credentials produces the recordings the suite replays:
    one real Run through here leaves a file the next thousand Runs reproduce without a
    key (`scripts/record_fixtures.py --live`).
    """

    def __init__(self, inner: ModelClient, path: Path) -> None:
        self.inner = inner
        self.path = Path(path)

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = self.inner.complete(request)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {"request": request.body(), "response": response.body}, ensure_ascii=False
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return response


def _rejected_sampling_key(exc: Exception, request: ModelRequest) -> str | None:
    """Which sampling parameter a 400 named, if it named one it was sent."""
    message = str(exc)
    for key in SAMPLING_KEYS:
        if key in request.sampling and key in message:
            return key
    return None


__all__ = [
    "EFFORT_LEVELS",
    "SAMPLING_KEYS",
    "Effort",
    "LiveAnthropicClient",
    "ModelClient",
    "ModelRequest",
    "ModelResponse",
    "RecordingModelClient",
    "ReplayModelClient",
    "SamplingNotSupported",
    "SamplingSupport",
]
