"""A Target that sends `stop_sequences`, which the Claude Code path cannot carry.

The toy Target sends none, so this one exists to be refused: through the Claude Code
transport, `stop_sequences` is named and nothing is sent (ticket 20, decision 31), and the
Trial ends `agentdiag_error`, because the path could not carry the Trial and that is not the
Target's fault.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from agentdiag.examples.toy import SYSTEM_PROMPT, TOOL_SCHEMAS


def make_target(
    client: Any,
    tools: Mapping[str, Callable[..., Any]],
    *,
    model: str = "claude-sonnet-5",
) -> Callable[[str], str]:
    def respond(message: str) -> str:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            stop_sequences=["\n\nCustomer:"],
            messages=[{"role": "user", "content": message}],
        )
        return "".join(block.text for block in response.content if block.type == "text")

    return respond


__all__ = ["make_target"]
