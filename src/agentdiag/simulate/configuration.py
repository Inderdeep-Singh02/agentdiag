"""What `run.json` records about the Simulated User and the reviewer, and their Fingerprints
(D14, D27, ADR-0005 §3, ADR-0007 §3; phase-5 decision 52).

The Simulated User is Run configuration: which implementation, which model at which effort,
over which Backend, with which prompt, sampling as declared. All of it goes into one
Fingerprint, so `compare` can say the user changed rather than imply the Target did. Its
`persona` is not here: it is per Scenario, a slot of the prompt, not configuration. Neither
is a seed: the Messages API takes none, so none is sent, and a field that could only ever
hold `null` would be the constant D12 forbids.

The reviewer (D28, decision 57) is recorded under `run.json.judge.reviewer`, beside the
Judge whose kind of call it is: model, effort and its prompt.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field

from agentdiag.eval.registry import DEFAULT_REVIEWER_MODEL, DEFAULT_SIMULATED_USER_MODEL
from agentdiag.eval.render import RecordedPrompt
from agentdiag.model.claude_code import Backend


class SimulatedUserConfiguration(BaseModel):
    """`run.json.simulated_user`: the Simulated User one Run drove its adaptive Trials with."""

    implementation: Literal["model"]
    """`model` in v1: the scripted implementation is the driver-loop tests' and is not
    reachable from a Suite."""

    model: str = DEFAULT_SIMULATED_USER_MODEL
    effort: str | None = None
    backend: Backend | None = None
    """The Run's one Backend for agentdiag's own calls (decision 49); None only in a dry
    run, which calls nothing."""

    prompt: RecordedPrompt
    sampling: dict[str, float | int] = Field(default_factory=dict)
    """The sampling parameters as declared (`--simulated-user-temperature`, decision 53);
    `{}` in every v1 default Run. Whether the API accepted each is per call, on the Span."""

    fingerprint: str


class ReviewerConfiguration(BaseModel):
    """`run.json.judge.reviewer`: the Simulated User reviewer's Judge (D28, decision 57)."""

    model: str = DEFAULT_REVIEWER_MODEL
    effort: str | None = None
    prompt: RecordedPrompt


def simulated_user_fingerprint(configuration: SimulatedUserConfiguration) -> str:
    """sha256 over every field of the configuration but the Fingerprint itself (decision 52):
    the implementation, the model, the effort or `""`, the prompt's version and text, the
    sampling in canonical JSON, and the Backend's kind and CLI version or `""` — everything
    that could change what the Simulated User says without the Target changing."""
    backend = configuration.backend
    joined = "\0".join(
        [
            configuration.implementation,
            configuration.model,
            configuration.effort or "",
            configuration.prompt.version,
            configuration.prompt.text,
            json.dumps(configuration.sampling, sort_keys=True, separators=(",", ":")),
            backend.kind if backend else "",
            (backend.cli_version or "") if backend else "",
        ]
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def simulated_user_configuration(
    prompt: RecordedPrompt,
    *,
    model: str = DEFAULT_SIMULATED_USER_MODEL,
    effort: str | None = None,
    temperature: float | None = None,
    backend: Backend | None = None,
) -> SimulatedUserConfiguration:
    """The configuration a Run records, its Fingerprint computed over the rest of it."""
    unsigned = SimulatedUserConfiguration(
        implementation="model",
        model=model,
        effort=effort,
        backend=backend,
        prompt=prompt,
        sampling={} if temperature is None else {"temperature": temperature},
        fingerprint="",
    )
    return unsigned.model_copy(update={"fingerprint": simulated_user_fingerprint(unsigned)})


__all__ = [
    "DEFAULT_REVIEWER_MODEL",
    "DEFAULT_SIMULATED_USER_MODEL",
    "RecordedPrompt",
    "ReviewerConfiguration",
    "SimulatedUserConfiguration",
    "simulated_user_configuration",
    "simulated_user_fingerprint",
]
