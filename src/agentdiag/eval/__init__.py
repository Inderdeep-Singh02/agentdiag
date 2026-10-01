"""Evals: the catalogue, the Judge, and the Score one Eval produces (ADR-0003, D21, D23).

Slice 3 landed the Score type and an empty registry; slice 4 added the Judge,
`prompt_adherence`, and the rendering the Judge reads a Trace through; ticket 04 the
mechanical Evals; ticket 05 the prompt head every judged Eval shares, one module per judged
Eval, the calibration notes and the Diagnosis.

The names below are resolved on first use rather than on import. `agentdiag.scenario`
reads the catalogue (`agentdiag.eval.registry`) to bind an Eval declaration's short form,
and `agentdiag validate` must stay offline (ticket 03): importing this package eagerly
would pull in the Judge, and with it the model client and the Anthropic SDK, just to learn
an Eval's primary parameter.
"""

from __future__ import annotations

import importlib
from typing import Any

_HOMES = {
    "Judge": "judge",
    "JudgeAnswer": "judge",
    "JudgeConfiguration": "judged",
    "JudgeFailure": "judge",
    "JudgeOutput": "judge",
    "RecordedPrompt": "render",
    "Judges": "judge",
    "JudgeNotes": "notes",
    "TrialFailure": "perform",
    "perform_evals": "perform",
    "PROMPT_VERSION": "prompt_adherence",
    "REGISTRY": "registry",
    "UNKNOWN_EVAL_CODE": "registry",
    "EvalContext": "spec",
    "EvalSpec": "spec",
    "is_judged": "registry",
    "BASE_OUTPUT_SCHEMA": "render",
    "JudgeContext": "render",
    "JudgePromptParts": "render",
    "prompt_sections": "render",
    "render_judge_prompt": "render",
    "render_trace_for_judge": "render",
    "Score": "score",
    "ScoresFile": "score",
    "ScoreSource": "score",
}
"""Which submodule each public name lives in: the one list `__all__` and `__getattr__` read."""


def __getattr__(name: str) -> Any:
    home = _HOMES.get(name)
    if home is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f"{__name__}.{home}"), name)


__all__ = sorted(_HOMES)
