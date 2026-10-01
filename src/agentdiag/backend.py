"""The Backend a model call took, as a record: offline, so an Adapter's description can
name it without importing the model module (ticket 17, phase-8 decision 1).

`Backend` is part of `AdapterDescription` (`run.json.adapter.backend`) and of the Judge's
configuration. It lived in `agentdiag.model.claude_code`, whose module imports the Claude
Agent SDK, so every module that described an Adapter loaded the SDK with it; the HTTP
Adapter must be offline at import (`validate` may import it). The class lives here, as
`reference.py` and `timestamps.py` became offline homes before it, and
`agentdiag.model.claude_code.Backend` is this class, re-exported.
"""

from __future__ import annotations

from pydantic import BaseModel

from agentdiag.types import STRUCTURED_OUTPUT, BackendKind


class Backend(BaseModel):
    """Which path a model call took: the Judge's, as `run.json.judge.backend` records it,
    or the Target's, as `run.json.adapter.backend` does (ticket 20, decision 28).

    Part of the Judge Fingerprint (decision 25): the same model reached through a
    different path, or through another CLI version that wraps the prompt differently, can
    move a Verdict without the Target moving.
    """

    kind: BackendKind
    cli_version: str | None = None
    """The CLI's `--version` for `claude_code`; None for the other two."""

    @property
    def structured_output(self) -> str:
        """How this Backend holds a structured answer to its schema (decision 44), from the
        one table `show` and `compare` also read."""
        return STRUCTURED_OUTPUT[self.kind]


__all__ = ["Backend"]
