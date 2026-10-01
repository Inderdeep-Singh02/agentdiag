"""The scripted Trace fixtures are what `agentdiag run` writes when it replays them.

`scripts/record_fixtures.py --scripted` ran the toy Target through the real Adapter
against a scripted model, wrote one recording scoped per Scenario, and then kept the
Trace `run` wrote replaying it with a one-millisecond-per-Event clock. This regenerates
each fixture the same way, so a fixture that stops being the pipeline's own output — an
Event gained or lost, an attribute renamed — fails here and is regenerated deliberately.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

from agentdiag.run.execute import RunOptions, run
from agentdiag.scenario.load import load_suite
from tests.fakes.workspace import the_target

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
TRACES = REPO / "tests" / "fixtures" / "traces"
SUITE_RECORDING = REPO / "tests" / "fixtures" / "recordings" / "toy-orders.jsonl"


def _record_fixtures() -> ModuleType:
    """`scripts/record_fixtures.py`, whose run id, clock and scripts these tests reuse."""
    spec = importlib.util.spec_from_file_location(
        "record_fixtures", REPO / "scripts" / "record_fixtures.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("scripts/record_fixtures.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    # Registered, as an import would, so the script's dataclasses can resolve their module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RECORD_FIXTURES = _record_fixtures()
SCRIPTED = list(RECORD_FIXTURES.SCRIPTS)


@pytest.mark.parametrize("scenario", SCRIPTED)
def test_the_scripted_trace_fixture_is_what_a_replayed_run_writes_byte_for_byte(
    scenario: str, tmp_path: Path
) -> None:
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs"))

    exit = run(
        RunOptions(target=the_target(root), scenario=[scenario], replay=SUITE_RECORDING),
        clock=RECORD_FIXTURES.scripted_clock(),
        run_id=RECORD_FIXTURES.SCRIPTED_RUN,
    )

    assert exit.run_dir is not None, exit.message
    written = exit.run_dir / "trials" / scenario / "1" / "trace.jsonl"
    expected = TRACES / f"{scenario}.trace.jsonl"
    assert written.read_text(encoding="utf-8") == expected.read_text(encoding="utf-8")


def test_every_line_of_the_examples_recording_is_scoped_to_a_scenario_of_its_suites() -> None:
    """Decision 19: `--scripted` writes the scope on every line it records."""
    ids = {
        scenario.id
        for name in ("orders", "guardrails")
        for scenario in load_suite(
            EXAMPLE / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / f"{name}.yaml"
        )[0].scenarios
    }
    lines = SUITE_RECORDING.read_text(encoding="utf-8").splitlines()
    scopes = {json.loads(line).get("scenario") for line in lines if line.strip()}

    assert scopes == ids
