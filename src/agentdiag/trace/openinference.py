"""The OpenInference cost attribute names agentdiag vendors (ADR-0006 §4, phase-5 decision 4).

OTel GenAI names no cost attribute, so a Span's cost uses OpenInference's `llm.cost.*`
names, pinned to one upstream commit exactly as `trace/otel_genai.py` pins the GenAI names:
`tests/test_openinference.py` fails when a name here leaves the committed snapshot of every
`llm.cost.*` name at `PINNED_COMMIT`, and a `network`-marked test refetches the page and
re-derives it.

agentdiag writes six of them, at capture, into a model Span's `span/end.attributes`:
`COST_TOTAL` (prompt + completion), `COST_PROMPT` (every input token: uncached, cache read
and cache write), `COST_COMPLETION` (output tokens), and the three prompt parts, each only
when the response reported that usage.
"""

from __future__ import annotations

PINNED_COMMIT = "7feb0c4ba2fd77cb76036712e21d06ff15a2be22"

SPEC_URL = (
    "https://raw.githubusercontent.com/Arize-ai/openinference/"
    f"{PINNED_COMMIT}/spec/semantic_conventions.md"
)

COST_TOTAL = "llm.cost.total"
COST_PROMPT = "llm.cost.prompt"
COST_COMPLETION = "llm.cost.completion"
COST_PROMPT_INPUT = "llm.cost.prompt_details.input"
COST_PROMPT_CACHE_READ = "llm.cost.prompt_details.cache_read"
COST_PROMPT_CACHE_WRITE = "llm.cost.prompt_details.cache_write"

ATTRIBUTES: frozenset[str] = frozenset(
    {
        COST_TOTAL,
        COST_PROMPT,
        COST_COMPLETION,
        COST_PROMPT_INPUT,
        COST_PROMPT_CACHE_READ,
        COST_PROMPT_CACHE_WRITE,
    }
)
"""The names agentdiag writes; every one is in the pinned snapshot."""

__all__ = [
    "ATTRIBUTES",
    "COST_COMPLETION",
    "COST_PROMPT",
    "COST_PROMPT_CACHE_READ",
    "COST_PROMPT_CACHE_WRITE",
    "COST_PROMPT_INPUT",
    "COST_TOTAL",
    "PINNED_COMMIT",
    "SPEC_URL",
]
