"""What a recorded Messages API request says of the Target: its system prompt, read once.

The API takes `system` as a string or as a list of text blocks. The Judge reads the
Target's prompt from a Trace's first `request` (`eval.render.system_prompt_from`) and the
Sync probe reads it from the one request it keeps (`adapter.inprocess.observation_of`); both
read it here, so a prompt sent as blocks is one prompt to both, spelled the same way.

Offline: it reads a mapping.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def system_text(system: Any) -> str | None:
    """A request body's `system` as text: the string as sent, or the blocks' text joined
    by newlines and stripped; None when it holds no text at all."""
    if isinstance(system, str):
        return system if system.strip() else None
    if isinstance(system, list):
        # The API also takes `system` as content blocks; join their text so an Adapter
        # that sent the prompt that way is not treated as having sent none.
        text = "\n".join(
            str(block.get("text", "")) for block in system if isinstance(block, Mapping)
        ).strip()
        return text or None
    return None


__all__ = ["system_text"]
