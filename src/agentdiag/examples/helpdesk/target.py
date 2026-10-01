"""The help desk Target: an object whose `__call__` answers one message (phase-6 decision 38).

`make_helpdesk(client, tools, *, model)` is the factory the Manifest names. Like the first
toy's it takes the client and the tools from its caller, so the in-process Adapter can hand
in a client whose transport it owns and tool callables it wraps (D6, ADR-0001); unlike the
toy's it returns an instance of `HelpDeskTarget` rather than a closure, and it takes its
model as a required keyword rather than a default.

**The platform is read at every request.** Each model call sends
`platform.DEPLOYED["prompts"]["system"]` and the platform's tool records as they are at that
moment, never a copy taken when the Target was built, so an edit made on the platform is the
prompt the very next request carries — the behaviour a hosted agent has, and the one the
drop-on-an-unknown-Target check edits (decision 39). `make_staging_helpdesk` is the same
Target on the platform's protected `staging` environment, reading `platform.STAGING`
(phase-7 decision 16), so a Run on `staging` talks to what a push to `staging` wrote.

A tool's result goes back to the model as one JSON string, the shape the platform's proxy
rows record (`platform.PROXY_ROWS`); a tool that raises goes back as an error result the
model can recover from. Imports `json` and this package, nothing of agentdiag.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from agentdiag.examples.helpdesk import platform

DEFAULT_MAX_TOKENS = 1024


class HelpDeskTarget:
    """One help desk conversation: calling it twice continues the same thread."""

    def __init__(
        self,
        client: Any,
        tools: Mapping[str, Callable[..., Any]],
        *,
        model: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        deployed: Mapping[str, Any] | None = None,
    ) -> None:
        self.client = client
        self.deployed = deployed
        """The platform environment's deployed set; None reads `platform.DEPLOYED`."""
        self.tools = tools
        self.model = model
        self.max_tokens = max_tokens
        self.messages: list[dict[str, Any]] = []

    def __call__(self, message: str) -> str:
        """Answer one user message, running every tool the model asks for until it stops."""
        self.messages.append({"role": "user", "content": message})
        while True:
            deployed = self.deployed if self.deployed is not None else platform.DEPLOYED
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=deployed["prompts"]["system"],
                tools=list(deployed["tools"].values()),
                messages=self.messages,
            )
            content = [block.model_dump(exclude_none=True) for block in response.content]
            self.messages.append({"role": "assistant", "content": content})
            if response.stop_reason != "tool_use":
                return "".join(block["text"] for block in content if block.get("type") == "text")
            self.messages.append(
                {
                    "role": "user",
                    "content": [
                        self._run(block) for block in content if block.get("type") == "tool_use"
                    ],
                }
            )

    def _run(self, block: Mapping[str, Any]) -> dict[str, Any]:
        """One `tool_use` block run, as the `tool_result` block that answers it."""
        result: dict[str, Any] = {"type": "tool_result", "tool_use_id": block["id"]}
        try:
            value = self.tools[str(block["name"])](**dict(block.get("input") or {}))
        except Exception as exc:  # the model sees a tool failure and recovers from it
            return {**result, "content": f"{type(exc).__name__}: {exc}", "is_error": True}
        return {**result, "content": json.dumps(value)}


def make_helpdesk(
    client: Any, tools: Mapping[str, Callable[..., Any]], *, model: str
) -> HelpDeskTarget:
    """Build one help desk conversation: the factory the Manifest's `factory` names."""
    return HelpDeskTarget(client, tools, model=model)


def make_staging_helpdesk(
    client: Any, tools: Mapping[str, Callable[..., Any]], *, model: str
) -> HelpDeskTarget:
    """The same conversation on the platform's `staging` environment (`platform.STAGING`)."""
    return HelpDeskTarget(client, tools, model=model, deployed=platform.STAGING)


__all__ = ["DEFAULT_MAX_TOKENS", "HelpDeskTarget", "make_helpdesk", "make_staging_helpdesk"]
