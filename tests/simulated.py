"""What the Simulated User tests share: the test-only Suite, the toy Target in replay.

`tests/fixtures/suites/simulated.yaml` and the recordings the tests replay over it are
written by `scripts/record_fixtures.py --scripted` (ticket 06, phase-5 decision 61); nothing
here writes a fixture. A root is the shipped example with its Manifest pointed at that Suite
alone, so the toy Target, its tools and its calibration notes are the example's own. The
example copy, the one-Run lookup and the clock are the older tests' own, imported.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from tests.test_run_cli import only_run
from tests.test_run_cli import target_root as example_root
from tests.test_trace_writer import ticking

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
FIXTURES = REPO / "tests" / "fixtures"
RECORDINGS = FIXTURES / "recordings"
TRACES = FIXTURES / "traces"
SIMULATED_SUITE = FIXTURES / "suites" / "simulated.yaml"


def simulated_root(tmp_path: Path) -> Path:
    """A copy of the example whose Manifest names the test-only Suite and nothing else."""
    root = tmp_path / "simulated"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs"))
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["suites"] = ["suites/simulated.yaml"]
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    shutil.copyfile(
        SIMULATED_SUITE,
        root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "simulated.yaml",
    )
    return root


__all__ = [
    "RECORDINGS",
    "SIMULATED_SUITE",
    "TRACES",
    "example_root",
    "only_run",
    "simulated_root",
    "ticking",
]
