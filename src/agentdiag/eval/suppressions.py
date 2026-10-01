"""Suppressions: the known false fails a Target's authors tell the Judge to disregard.

A Suppression (ADR-0003 §8, phase-6 decision 16) is Manifest data with an id, the Eval it
applies to (`*` for every judged Eval), an effective-date window and a pattern, with a
line saying why. It is not a Calibration Note: a note is standing guidance for every Trial,
a Suppression is one known false fail for the Trials inside a window, so an expired one
stops applying without anyone editing prose.

**Applied against the Trace's start, by the Judge, and on the record.** The ones in force
for a Trial are those whose Eval matches and whose `[from, until]` window holds the UTC
date of the Trace's `trace/start`: the Trace's own time, so a rescore a month later judges
a Trace under the Suppressions of the day it was recorded. Those reach the Judge as text,
in the prompt head's Suppressions section, and nowhere else: agentdiag never rewrites a
Verdict because of one. With none in force the section is absent and the prompt is
byte-for-byte what it was before Suppressions existed. The instances go into `run.json`'s
Judge configuration and every Score records the ids offered to its Judge
(`ScoreSource.suppressions_in_force`); the ones in force enter that Score's Judge
Fingerprint, so a Suppression added or expired is a visible change in a comparison.

Mechanical Evals ignore Suppressions in v1: they have no rationale to name one in, and a
Suppression naming one is a `validate` warning.

Offline: dates and strings.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, Field

from agentdiag.trace.events import Event

EVERY_EVAL = "*"
"""The `eval` of a Suppression that applies to every judged Eval."""


class Suppression(BaseModel):
    """One known false fail, as the Manifest's `suppressions` list names it."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str = Field(min_length=1)
    eval: str = Field(min_length=1)
    """The Eval it applies to, by name, or `*` for every judged Eval."""

    from_: date = Field(alias="from")
    until: date
    """The window, both ends included, in UTC dates."""

    pattern: str = Field(min_length=1)
    """What the known false fail looks like, as the Judge is told it."""

    why: str = Field(min_length=1)

    def applies_to(self, eval_name: str) -> bool:
        return self.eval in (EVERY_EVAL, eval_name)

    def in_window(self, day: date) -> bool:
        return self.from_ <= day <= self.until


def trace_start(events: Iterable[Event]) -> date | None:
    """The UTC date of the Trace's `trace/start`; None for a Trace without one."""
    for event in events:
        if event.type == "trace/start":
            return datetime.fromtimestamp(event.ts / 1000, tz=UTC).date()
    return None


def in_force(
    suppressions: Sequence[Suppression], eval_name: str, events: Iterable[Event]
) -> list[Suppression]:
    """The Suppressions in force for one Eval over one Trace, in the Manifest's order."""
    day = trace_start(events)
    if day is None:
        return []
    return [s for s in suppressions if s.applies_to(eval_name) and s.in_window(day)]


__all__ = ["EVERY_EVAL", "Suppression", "in_force", "trace_start"]
