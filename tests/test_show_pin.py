"""`show`'s text is pinned by the byte over every committed Run fixture and Trace (ticket 15).

Ticket 15 extended the view model `show` renders from (`SpanView` gained its offsets and
depth, `TrialStory` its messages) so the HTML Report could draw a flame graph from the same
`TrialStory` (D40, phase-7 decision 19). The terminal text must not move with it: this test
renders every Trial of every Run under `tests/fixtures/runs/`, and every Trace under
`tests/fixtures/traces/` told by the Trace alone, at a fixed width, and compares the result
with the snapshot under `tests/fixtures/show/`, which was written from `show` before the
view model changed. A change to `show`'s output is then a deliberate snapshot update, never
a side effect of a Report field.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentdiag.trace.reader import read_trace, resolve_blobs
from agentdiag.trace.show import render_story, story_from_events, trial_story

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RUNS = FIXTURES / "runs"
TRACES = FIXTURES / "traces"
SNAPSHOTS = FIXTURES / "show"
WIDTH = 100
RULE = "\n\n" + "=" * 20 + "\n\n"


def rendered_run(run_dir: Path) -> str:
    """Every Trial of one Run fixture, in its Scorecard's order, as `show` prints it."""
    scorecard = json.loads((run_dir / "scorecard.json").read_text(encoding="utf-8"))
    return RULE.join(
        render_story(trial_story(run_dir, line["id"], line["trial"]), width=WIDTH)
        for line in scorecard["scenarios"]
    )


def rendered_traces() -> str:
    """Every committed Trace, told by its Events alone (the story `--follow` builds)."""
    return RULE.join(
        render_story(story_from_events(resolve_blobs(read_trace(path))), width=WIDTH)
        for path in sorted(TRACES.rglob("*.trace.jsonl"))
    )


@pytest.mark.parametrize("run_dir", sorted(RUNS.iterdir()), ids=lambda path: path.name)
def test_show_prints_each_run_fixture_exactly_as_before_the_report(run_dir: Path) -> None:
    snapshot = SNAPSHOTS / f"{run_dir.name}.txt"
    assert rendered_run(run_dir) + "\n" == snapshot.read_text(encoding="utf-8")


def test_show_prints_each_committed_trace_exactly_as_before_the_report() -> None:
    snapshot = SNAPSHOTS / "traces.txt"
    assert rendered_traces() + "\n" == snapshot.read_text(encoding="utf-8")
