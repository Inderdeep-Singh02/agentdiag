"""The committed Run fixtures are what `record_fixtures.py --runs` writes, byte for byte.

`tests/fixtures/runs/` holds whole Run directories that `compare`, `rescore` and pass^k are
tested over. None of them is hand-edited: each is a replayed `agentdiag run` (or
`rescore`) with a fixed clock, id and stamp. This regenerates every one of them, and the
recordings the variant Runs replay, into a scratch directory and diffs the two trees, so a
change to what a Run writes shows up here first and the fixtures are regenerated on
purpose rather than drifting from the code that reads them.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"


def _record_fixtures() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "record_fixtures_runs", REPO / "scripts" / "record_fixtures.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("scripts/record_fixtures.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    # Registered, as an import would, so the script's dataclasses can resolve their module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def files_under(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_every_run_fixture_and_its_recording_regenerates_byte_for_byte(tmp_path: Path) -> None:
    record_fixtures = _record_fixtures()

    record_fixtures.record_runs(record_fixtures.Out(tmp_path), record_fixtures.committed_answers())

    written, committed = files_under(tmp_path / "runs"), files_under(FIXTURES / "runs")
    assert sorted(written) == sorted(committed)
    for name, content in written.items():
        assert content == committed[name], name
    for path in sorted((tmp_path / "recordings").iterdir()):
        assert path.read_bytes() == (FIXTURES / "recordings" / path.name).read_bytes(), path.name


def test_the_default_recordings_regenerate_byte_for_byte(tmp_path: Path) -> None:
    """The default mode is cheap (one Trace, eight Judge calls), so it is diffed too: a
    prompt edit, or a captured answer, that did not regenerate them shows up here."""
    record_fixtures = _record_fixtures()

    record_fixtures.record_cancel(
        record_fixtures.Out(tmp_path), record_fixtures.committed_answers()
    )

    written = sorted((tmp_path / "recordings").iterdir())
    assert written
    for path in written:
        assert path.read_bytes() == (FIXTURES / "recordings" / path.name).read_bytes(), path.name


def test_the_scripted_recordings_traces_and_simulated_suite_regenerate_byte_for_byte(
    tmp_path: Path,
) -> None:
    """The `--scripted` mode too (ticket 06, phase-5 decision 59): the Judge's rendered Trace
    omits the Simulated User's calls, so every judged recording over a literal Trace must come
    out as committed, and the adaptive Scenario's and the test-only Suite's fixtures with them.
    About forty seconds; the proof that no existing rendered prompt moved is worth it."""
    record_fixtures = _record_fixtures()

    record_fixtures.record_scripted(
        record_fixtures.Out(tmp_path), record_fixtures.committed_answers()
    )

    written = files_under(tmp_path)
    assert written
    for name, content in written.items():
        assert content == (FIXTURES / name).read_bytes(), name
