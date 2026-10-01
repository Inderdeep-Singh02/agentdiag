"""The generic row shapes an Evidence store's rows arrive in (phase-6 decision 33).

A plugin's Connector returns its platform's rows in these shapes (ADR-0014 §2), and
`agentdiag import --rows` reads a file of them, so one Importer serves every platform. Each
shape is open to extras (`extra="allow"`): a plugin carries a field through that core does
not read yet, and a later Importer can start reading it without a migration.

The shapes are what the Importers need and no more. A proxy row is one model request and
its response as an LLM proxy logged it; a conversation record is a chat as the platform
stores it; a voice conversation is a call's transcript; a Flow run is one execution of a
Flow a tool started. The fixtures under `tests/fixtures/evidence/` are written in them by
hand, with no customer content.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Open(BaseModel):
    """A row shape that carries a plugin's extra fields through untouched."""

    model_config = ConfigDict(extra="allow")


class ProxyRow(_Open):
    """One model request and its response as an LLM proxy logged it."""

    request_id: str = Field(min_length=1)
    """The proxy's full request id: the one key two logged copies of a call share."""

    created_at: str
    """When the proxy received the request, ISO-8601."""

    latency_ms: float = Field(ge=0)
    """How long the proxy waited for the response: the end is `created_at` plus this, as
    the proxy measured it, which is not the model's own end time."""

    model: str
    provider: str | None = None
    status: int
    """The HTTP status the proxy returned; anything but 200 is an `llm_call` in error."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    """The usage the row reports; None when it reports none, which is not observed, never
    zero (ADR-0006 §4)."""

    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost_usd: float | None = None
    """What the proxy says the call cost, when it says; the price table prices it otherwise."""

    conversation_id: str | None = None
    request_body: dict[str, Any] | None = None
    """`{messages, system?, tools?}`: Anthropic Messages or OpenAI chat shape."""

    response_body: dict[str, Any] | None = None
    """`{content}` (Anthropic) or `{choices}` (OpenAI)."""

    cache_hit: bool = False
    """The proxy answered from its cache: a second copy of an earlier request's row."""

    truncated: list[str] = Field(default_factory=list)
    """The fields the proxy cut (`request_body`, `response_body`, or a path under one)."""


class ConversationMessage(_Open):
    """One message of a conversation record."""

    role: Literal["user", "assistant", "operator", "tool"]
    content: Any = None
    at: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None


class ConversationRecord(_Open):
    """A chat as the platform stores it: the messages in order, staff takeovers included."""

    conversation_id: str = Field(min_length=1)
    started_at: str
    messages: list[ConversationMessage]
    channel: str | None = None


class VoiceToolCall(_Open):
    """A tool call a voice platform's transcript reports."""

    name: str
    arguments: Any = None
    result: Any = None


class VoiceTurn(_Open):
    """One utterance of a voice transcript."""

    role: str
    text: str = ""
    at: str | None = None
    tool_calls: list[VoiceToolCall] = Field(default_factory=list)


class VoiceConversation(_Open):
    """A call's transcript, with the tool calls the platform reports and its length."""

    conversation_id: str = Field(min_length=1)
    started_at: str
    transcript: list[VoiceTurn]
    duration_s: float | None = None


class FlowRun(_Open):
    """One execution of a Flow a tool started, as the platform records it."""

    flow_id: str = Field(min_length=1)
    started_at: str
    ended_at: str | None = None
    status: str
    arguments: dict[str, Any] | None = None
    steps: list[Any] | None = None


FLOW_RUN_KEY = "flow_id"
"""A row holding this key is a `FlowRun` riding among proxy rows (decision 34)."""


def read_rows_file(path: Path) -> list[dict[str, Any]]:
    """The rows a `--rows` file holds: a JSON array, or JSONL with one row per line.

    `ValueError` naming the file and the line when it is neither, or a row is not an
    object; the shapes themselves are the Importer's to check.
    """
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not a JSON array of rows: {exc}") from exc
        rows = loaded if isinstance(loaded, list) else [loaded]
    else:
        rows = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} line {number} is not a JSON row: {exc}") from exc
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{path} row {index} is not a JSON object")
    return rows


__all__ = [
    "FLOW_RUN_KEY",
    "ConversationMessage",
    "ConversationRecord",
    "FlowRun",
    "ProxyRow",
    "VoiceConversation",
    "VoiceToolCall",
    "VoiceTurn",
    "read_rows_file",
]
