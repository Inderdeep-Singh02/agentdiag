"""Where agentdiag's own model calls would get their credentials from (D15, D16).

Preflight needs one question answered before a Run starts: would a model call work at
all? It never needs the answer's value. So everything here returns the *name* of a
source and never the secret it holds — a `CredentialSource` is safe to print, to put in
an error message, and to hand to the Scorecard.

Resolution order is the SDK's own, read from `anthropic` 1.7.0 rather than guessed:
`ANTHROPIC_API_KEY`, then `ANTHROPIC_AUTH_TOKEN`, then `anthropic.default_credentials()`
for an `ant auth login` profile or a workload identity. That last call is deliberate: the
profile layout and the pointer file that selects one are the SDK's business, and a copy of
its rules here would drift the first time the SDK changed them.

Ticket 19 adds a fifth source, tried last: the developer's Claude Code login, asked of the
CLI (`claude_code.cli()`, which runs `<cli> auth status`) rather than read from its files,
for the same reason. Every API-side kind means the Judge takes the `anthropic_api` Backend
and only this one means `claude_code` (decision 21), so with no key set and a Claude Code
login, Claude Code is the default. The probe is `claude_code_probe`, a module-level name,
so the test suite can switch it off for every test that is not `live`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Literal

import anthropic
from pydantic import BaseModel

from agentdiag.model import claude_code
from agentdiag.model.claude_code import ClaudeCodeCli

ENV_API_KEY = "ANTHROPIC_API_KEY"
ENV_AUTH_TOKEN = "ANTHROPIC_AUTH_TOKEN"

CredentialKind = Literal["api_key", "auth_token", "profile", "workload_identity", "claude_code"]
"""The four ways credentials reach the SDK, and the Claude Code login, named so a message
can say which one."""

claude_code_probe: Callable[[], ClaudeCodeCli | None] = claude_code.cli
"""What `resolve()` asks last: a logged-in Claude Code CLI, or None. Patched to return None
by `tests/conftest.py` for every test not marked `live`."""


class CredentialSource(BaseModel):
    """Which source would answer, and enough detail to act on it — never the secret.

    `detail` names the environment variable or the profile, so "credentials resolve" can
    be checked by a reader against what they think they configured.
    """

    kind: CredentialKind
    detail: str
    cli: ClaudeCodeCli | None = None
    """For `claude_code` only: the binary and version the probe found, which `run.json`
    records as the Judge's Backend. A path and a version, never an account."""


def resolve() -> CredentialSource | None:
    """The source a live model call would use, or None when nothing is configured.

    Constructing a client with nothing configured does not raise in the SDK; the failure
    only arrives at the first request. Preflight exists to turn that into a message
    before a Run directory exists (D16), so this asks the question up front. The SDK's
    chain first, in its order; the Claude Code login last (`claude_code_probe`, the one
    seam a test replaces), so an exported key always wins.
    """
    if os.environ.get(ENV_API_KEY):
        return CredentialSource(kind="api_key", detail=ENV_API_KEY)
    if os.environ.get(ENV_AUTH_TOKEN):
        return CredentialSource(kind="auth_token", detail=ENV_AUTH_TOKEN)

    try:
        result = anthropic.default_credentials()
    except Exception:
        # An explicitly selected but broken profile raises inside the SDK. That is still
        # "no credentials resolve" for preflight's purposes, and the one D16 message is a
        # better thing for a reader to receive than a traceback out of a dependency.
        result = None
    if result is not None:
        return _from_provider(result.provider)

    found = claude_code_probe()
    if found is None:
        return None
    return CredentialSource(
        kind="claude_code", detail=f"Claude Code login ({found.version})", cli=found
    )


def _from_provider(provider: object) -> CredentialSource:
    """Name the provider the SDK chose, by its class rather than by re-deriving why."""
    name = type(provider).__name__
    if name == "StaticToken":
        return CredentialSource(kind="auth_token", detail=ENV_AUTH_TOKEN)
    if name == "WorkloadIdentityCredentials":
        return CredentialSource(kind="workload_identity", detail="workload identity")
    profile = getattr(provider, "profile", None)
    return CredentialSource(kind="profile", detail=str(profile) if profile else "active profile")


__all__ = [
    "ENV_API_KEY",
    "ENV_AUTH_TOKEN",
    "CredentialKind",
    "CredentialSource",
    "claude_code_probe",
    "resolve",
]
