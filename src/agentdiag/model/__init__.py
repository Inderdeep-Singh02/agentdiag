"""agentdiag's own model boundary: the ModelClient, credentials, and replay (D12 to D16).

Slice 2 landed `replay` here because the Target-side replay transport and slice 4's
`ReplayModelClient` consume one recording format (phase-4 interfaces, decision 5). Slice 4
adds the client and the credential check that preflight asks; ticket 19 the Claude Code
backend beside the API one (`claude_code`).
"""

from agentdiag.model.claude_code import (
    Backend,
    BackendKind,
    ClaudeCodeCli,
    ClaudeCodeClient,
    ClaudeCodeError,
)
from agentdiag.model.client import (
    SAMPLING_KEYS,
    Effort,
    LiveAnthropicClient,
    ModelClient,
    ModelRequest,
    ModelResponse,
    RecordingModelClient,
    ReplayModelClient,
    SamplingNotSupported,
    SamplingSupport,
)
from agentdiag.model.credentials import CredentialKind, CredentialSource
from agentdiag.model.replay import (
    Exchange,
    Recording,
    RecordingNotConsumed,
    ReplayCursor,
    ReplayMismatch,
    ReplayTransport,
    canonical,
)

__all__ = [
    "SAMPLING_KEYS",
    "Backend",
    "BackendKind",
    "ClaudeCodeCli",
    "ClaudeCodeClient",
    "ClaudeCodeError",
    "CredentialKind",
    "CredentialSource",
    "Effort",
    "Exchange",
    "LiveAnthropicClient",
    "ModelClient",
    "ModelRequest",
    "ModelResponse",
    "Recording",
    "RecordingModelClient",
    "RecordingNotConsumed",
    "ReplayCursor",
    "ReplayMismatch",
    "ReplayModelClient",
    "ReplayTransport",
    "SamplingNotSupported",
    "SamplingSupport",
    "canonical",
]
