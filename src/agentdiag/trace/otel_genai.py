"""The OTel GenAI attribute names agentdiag vendors (ADR-0006 section 1).

Pinned to one upstream commit so drift is visible: `tests/test_otel_genai.py` fails when
`ATTRIBUTES` leaves the committed snapshot, and a `network`-marked test refetches the
registry page at `PINNED_COMMIT` and re-derives the same set.
"""

from __future__ import annotations

PINNED_COMMIT = "8ffdf568e1b4391a99adb081db16e8102e36918e"

REGISTRY_URL = (
    "https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/"
    f"{PINNED_COMMIT}/docs/registry/attributes/gen-ai.md"
)

TOOL_NAME = "gen_ai.tool.name"
"""The tool a tool Span executed."""

REQUEST_MODEL = "gen_ai.request.model"
"""The model a model Span asked for."""

RESPONSE_MODEL = "gen_ai.response.model"
"""The model the API answered as, which can differ from the one asked for."""

TOOL_CALL_ID = "gen_ai.tool.call.id"
"""The `tool_use` block a tool Span answers, when the Adapter could attribute it."""

ATTRIBUTES: frozenset[str] = frozenset(
    {
        "error.type",
        "gen_ai.conversation.id",
        "gen_ai.operation.name",
        "gen_ai.provider.name",
        "gen_ai.request.max_tokens",
        REQUEST_MODEL,
        "gen_ai.response.finish_reasons",
        "gen_ai.response.id",
        RESPONSE_MODEL,
        TOOL_CALL_ID,
        TOOL_NAME,
        "gen_ai.usage.cache_read.input_tokens",
        "gen_ai.usage.cache_write.input_tokens",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.reasoning.output_tokens",
    }
)

__all__ = [
    "ATTRIBUTES",
    "PINNED_COMMIT",
    "REGISTRY_URL",
    "REQUEST_MODEL",
    "RESPONSE_MODEL",
    "TOOL_CALL_ID",
    "TOOL_NAME",
]
