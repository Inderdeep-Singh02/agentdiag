"""The JSON Schema of every persisted shape, generated from the Pydantic models (D2).

`python -m agentdiag.schemas` writes one file per model into `schemas/`. The committed
output is a gate: `tests/test_schemas.py` regenerates into a temporary directory and
fails on any byte of difference, so a model change that nobody meant to publish cannot
land quietly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from agentdiag.change.record import ChangeRecord
from agentdiag.eval.score import Score, ScoresFile
from agentdiag.registry import RegistryEntry, TargetView
from agentdiag.run.compare import Comparison
from agentdiag.run.index import RunListing
from agentdiag.run.manifest import Manifest
from agentdiag.run.record import RunRecord
from agentdiag.run.scorecard import Scorecard
from agentdiag.scenario.models import Suite
from agentdiag.sync.breaks import SyncBreak
from agentdiag.sync.pushes import PushRecord, RestorePoint
from agentdiag.trace.events import Event
from agentdiag.trace.spans import Span

SCHEMAS: dict[str, type[BaseModel]] = {
    "change-record": ChangeRecord,
    "comparison": Comparison,
    "event": Event,
    "listing": RunListing,
    "manifest": Manifest,
    "push-record": PushRecord,
    "restore-point": RestorePoint,
    "registry": RegistryEntry,
    "run": RunRecord,
    "score": Score,
    "scorecard": Scorecard,
    "scores": ScoresFile,
    "span": Span,
    "suite": Suite,
    "sync-break": SyncBreak,
    "target": TargetView,
}
"""Every persisted shape, by the name of the file it generates. Later slices extend it."""

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


def render(model: type[BaseModel]) -> str:
    """One model's JSON Schema, rendered deterministically."""
    schema = model.model_json_schema()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_schemas(directory: Path) -> list[Path]:
    """Write every registered schema into `directory`; return the paths written."""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in sorted(SCHEMAS.items()):
        path = directory / f"{name}.schema.json"
        path.write_text(render(model), encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    directory = Path(arguments[0]) if arguments else SCHEMA_DIR
    for path in write_schemas(directory):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["SCHEMAS", "SCHEMA_DIR", "main", "render", "write_schemas"]
