"""Runs: the Manifest, the immutable Run directory, execution and the Scorecard (ADR-0005).

`execute.run` is the whole of `agentdiag run`; `init.scaffold_target` is the whole of
`agentdiag init`;
`scorecard.aggregate` is the whole of how a Run's Scores become a number (D25);
`compare.compare` and `rescore.rescore` are `agentdiag compare` and `agentdiag rescore`. The
CLI does nothing but build options and print.

The names below resolve on first use, not on import. Execution imports the Judge and the
model client, and with them the Anthropic SDK; `agentdiag validate` reads the Manifest from
`agentdiag.run.manifest` and must not pay for, or depend on, any of that (ticket 03).
"""

from __future__ import annotations

import importlib
from typing import Any

_HOMES = {
    "Comparison": "compare",
    "INIT_EXIT": "init",
    "InitOptions": "init",
    "InitRefused": "init",
    "InitResult": "init",
    "Manifest": "manifest",
    "ManifestError": "manifest",
    "ManifestNotFound": "manifest",
    "NOT_FOUND_EXIT": "locate",
    "NotRun": "record",
    "Plan": "preflight",
    "PreflightFailed": "preflight",
    "RescoreOptions": "rescore",
    "RunDirectory": "directory",
    "RunDirectoryExists": "directory",
    "RunExit": "execute",
    "RunOptions": "execute",
    "RunRecord": "record",
    "RunStamp": "record",
    "Scorecard": "scorecard",
    "TrialNotFound": "locate",
    "aggregate": "scorecard",
    "exit_code": "scorecard",
    "load_manifest": "manifest",
    "locate_run": "locate",
    "negate": "scorecard",
    "new_run_id": "directory",
    "preflight": "preflight",
    "render_summary": "scorecard",
    "require_trace": "locate",
    "run": "execute",
    "scaffold_target": "init",
    "trace_path": "locate",
    "trial_dir": "locate",
}
"""Which submodule each public name lives in: the one list `__all__` and `__getattr__` read."""


def __getattr__(name: str) -> Any:
    home = _HOMES.get(name)
    if home is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f"{__name__}.{home}"), name)


__all__ = sorted(_HOMES)
