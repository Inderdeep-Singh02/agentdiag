"""The Change record fixtures of `tests/fixtures/changes/` placed where they belong (ticket 28).

The records cite two committed Run fixtures (the trigger's and baseline Run `BASE`, the
verifying Run `PRMT`) and, when pushed, one Push record. `toy_with_records` copies the toy
with those Runs and every record under its Target, and writes that Push record with the
Fingerprints the records' push events carry, so every link a story makes has something to
point at. The Restore point it names is gitignored where a push really runs, so it is written
only when asked.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from agentdiag.workspace import TargetPaths, Workspace

REPO = Path(__file__).resolve().parents[1]
CHANGES = REPO / "tests" / "fixtures" / "changes"
RUNS = REPO / "tests" / "fixtures" / "runs"
EXAMPLE = REPO / "examples" / "toy"
SLUG = "toy-order-desk"
BASE = "20260923T100000Z-base"
PRMT = "20260923T100200Z-prmt"
TRIGGER_TRIAL = "status-question-is-not-a-cancel"
PUSHED_AT = "2026-09-23T10:01:30Z"
PUSH_RECORD = "pushes/20260923T100130Z-local.json"
RESTORE_POINT = "restore-points/20260923T100130Z-local.json"
FINGERPRINT_BEFORE = "1f0e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
FINGERPRINT_AFTER = "2a1b3c4d5e6f708192a3b4c5d6e7f8092a1b3c4d5e6f708192a3b4c5d6e7f809"


def push_record() -> dict[str, Any]:
    """The Push record the pushed fixtures' events name, agreeing with their Fingerprints."""
    return {
        "schema_version": 1,
        "pushed_at": PUSHED_AT,
        "target": SLUG,
        "environment": "local",
        "by": "support",
        "os_user": "support",
        "effective_side_effects": "none",
        "confirmed_by": "--push",
        "sections": ["prompt.system"],
        "fingerprint_before": FINGERPRINT_BEFORE,
        "fingerprint_after": FINGERPRINT_AFTER,
        "restore_point": RESTORE_POINT,
        "diff_sha256": "3" * 64,
        "change_record": "20260923-a-pushed-record",
        "receipt": {
            "environment": "local",
            "written_at": PUSHED_AT,
            "sections": ["prompt.system"],
            "fingerprint_before": FINGERPRINT_BEFORE,
            "fingerprint_after": FINGERPRINT_AFTER,
        },
    }


def toy_with_records(tmp_path: Path, *, restore_point: bool = False) -> TargetPaths:
    """A copy of the toy holding `BASE` and `PRMT`, every fixture record and the Push
    record; the Restore point too when `restore_point`."""
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    target = Workspace.find(root).resolve(None)
    for run in (BASE, PRMT):
        shutil.copytree(RUNS / run, target.runs / run)
    shutil.copytree(CHANGES, target.changes)
    push = target.relative(PUSH_RECORD)
    push.parent.mkdir(parents=True)
    push.write_text(json.dumps(push_record()), encoding="utf-8")
    if restore_point:
        point = target.relative(RESTORE_POINT)
        point.parent.mkdir(parents=True)
        point.write_text("{}", encoding="utf-8")
    return target
