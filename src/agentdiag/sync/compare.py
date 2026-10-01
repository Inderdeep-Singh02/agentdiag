"""Sync: three hashes per section, and which of the three is ahead (ADR-0011 §3, §4).

Per section id there are up to three hashes: **L**, the local file a path pointer names;
**D**, the deployed side as the Connector read it or, with no Connector read, as the
Adapter's probe observed it; and **R**, the recorded hash in
the last Fingerprint. `compare_sections` is decision 12's table and nothing else:

| known          | rule                 | direction          |
|----------------|----------------------|--------------------|
| neither        |                      | `not_covered`      |
| L and D        | L = D = R            | `identical`        |
| L and D        | L ≠ R, D = R         | `local_ahead`      |
| L and D        | D ≠ R, L = R         | `deployed_ahead`   |
| L and D        | L ≠ R, D ≠ R         | `diverged`         |
| L only         | L = R / L ≠ R        | `identical` / `local_ahead`    |
| D only         | D = R / D ≠ R        | `identical` / `deployed_ahead` |

A `diverged` section whose L and D agree is noted "local and deployed agree; the record is
stale": nothing to push or pull, and the next Run re-syncs. A section R lacks is `added`,
ahead on the side that has it (deployed when D is known); one R has and neither side has
now is `removed`, ahead on the side R took it from. A section the Manifest names that
nothing could observe this time stays `not_covered` with its reason, even when R has it:
an Adapter that could not observe is not evidence that the section went away.

The Run-level status (`sync_status`) is `held` when every section is `identical` or
`not_covered`, `broken` when any is ahead or diverged; `not_checked` carries its reason
from the closed set: `adapter_cannot_observe` when nothing was observed at all, else
`no_fingerprint` when there is no record to compare against (`no_manifest` is the caller's,
which never got this far).

The terminal table (`render_sync`) is byte-stable: plain padded text, one line per section,
the same at any width, so `sync --check` output can be diffed and quoted in the README.

Offline: this module compares hashes and formats text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

from agentdiag.sync.fingerprint import NONE_SHOWN, Fingerprint, NotCovered, short
from agentdiag.sync.sections import Hashed, belongs_to
from agentdiag.types import (
    BROKEN_DIRECTIONS,
    DEPLOYED_SOURCES,
    Change,
    CoveredBy,
    Direction,
    NotCheckedReason,
    SectionKind,
    SyncStatus,
)

RECORD_IS_STALE = "local and deployed agree; the record is stale"
"""The note on a `diverged` section whose two live sides agree (decision 12)."""


class SectionState(BaseModel):
    """One section's three hashes and what they say."""

    id: str
    kind: SectionKind
    direction: Direction
    change: Change = None
    local: str | None = None
    deployed: str | None = None
    recorded: str | None = None
    """The three sha256s: L, D and R; None where that side does not know the section."""

    deployed_by: CoveredBy | None = None
    """`adapter` or `connector`, when D is known."""

    note: str | None = None
    """Why a section is `not_covered`, or that a `diverged` one's record is stale."""

    @property
    def covered(self) -> str:
        """What the table's `covered` column prints: the deployed source when D is known,
        else `local` when L is, else nothing."""
        if self.deployed_by is not None:
            return self.deployed_by
        return "local" if self.local is not None else NONE_SHOWN


class SyncSection(BaseModel):
    """The Sync a Run checked before its first Trial (ADR-0007, ADR-0008, D30).

    `fingerprint` is the id of the Fingerprint compared against; `resynced_from` is set when
    the Run found Sync broken and rebuilt the Fingerprint (the default), so `show`, the
    Scorecard's summary and the Report can say which sections moved before anything else
    (ADR-0008's "never silent"); `sections` are the broken ones.
    """

    status: SyncStatus
    reason: NotCheckedReason | None = None
    environment: str | None = None
    fingerprint: str | None = None
    resynced_from: str | None = None
    sections: list[SectionState] = Field(default_factory=list)
    covered_by: CoveredBy | None = None
    """Which source read the deployed side for this Run's Sync (phase-6 decision 25): the
    Connector's read, or the Adapter's probe; None when neither observed anything, and for a
    Run recorded before ticket 23."""

    connector_failed: str | None = None
    """Why the Connector's read failed when the Run fell back to the probe: a Run warns and
    proceeds rather than refuse, and the record keeps what it warned of."""


class SyncResult(BaseModel):
    """One comparison of a Target against its last Fingerprint (decision 12)."""

    status: SyncStatus
    reason: NotCheckedReason | None = None
    environment: str | None = None
    fingerprint: str | None = None
    """The `id` of the Fingerprint compared against; None when there is none."""

    built_at: str | None = None
    """When that Fingerprint was built, for the table's header."""

    sections: list[SectionState] = Field(default_factory=list)
    """Every section, sorted by id."""

    covered_by: CoveredBy | None = None
    """Which source read the deployed side: `connector` when the Connector's read covered
    any section, else `adapter` when the probe did, else None (decision 25)."""

    @property
    def broken(self) -> list[SectionState]:
        """The sections that are ahead on one side or diverged."""
        return [state for state in self.sections if state.direction in BROKEN_DIRECTIONS]


def compare_sections(
    local: Mapping[str, Hashed],
    deployed: Mapping[str, Hashed],
    recorded: Fingerprint | None,
    *,
    deployed_by: Mapping[str, CoveredBy] | None = None,
    not_covered: Sequence[NotCovered] = (),
) -> list[SectionState]:
    """Decision 12's table over every section id any source or the record knows, sorted.

    `deployed_by` names the source of each D (the Adapter's probe by default);
    `not_covered` is what the Manifest names and nothing observed, whose ids (a prompt's
    `prompt.<name>` covers its heading sections) stay `not_covered` whatever R holds.
    """
    sources = dict(deployed_by or {})
    record = recorded.sections if recorded is not None else {}
    ids = sorted(set(local) | set(deployed) | set(record) | {entry.id for entry in not_covered})
    states: list[SectionState] = []
    for identifier in ids:
        ours, theirs = local.get(identifier), deployed.get(identifier)
        kept = record.get(identifier)
        uncovered = _uncovered(identifier, not_covered)
        if ours is None and theirs is None:
            if uncovered is not None:
                states.append(
                    SectionState(
                        id=identifier,
                        kind=uncovered.kind,
                        direction="not_covered",
                        recorded=kept.sha256 if kept is not None else None,
                        note=uncovered.reason,
                    )
                )
            elif kept is not None:
                states.append(
                    SectionState(
                        id=identifier,
                        kind=kept.kind,
                        direction="local_ahead" if kept.covered_by == "local" else "deployed_ahead",
                        change="removed",
                        recorded=kept.sha256,
                    )
                )
            continue
        known = theirs or ours
        assert known is not None
        l_hash = ours.sha256 if ours is not None else None
        d_hash = theirs.sha256 if theirs is not None else None
        r_hash = kept.sha256 if kept is not None else None
        change: Change
        if r_hash is None:
            # Absent from the record: ahead on the side that has it, the deployed side
            # when both do; never `diverged`, since there is no record to be stale.
            direction: Direction = "deployed_ahead" if d_hash is not None else "local_ahead"
            note, change = None, "added"
        else:
            direction, note = _direction(l_hash, d_hash, r_hash)
            change = None if direction == "identical" else "changed"
        states.append(
            SectionState(
                id=identifier,
                kind=known.kind,
                direction=direction,
                change=change,
                local=l_hash,
                deployed=d_hash,
                recorded=r_hash,
                deployed_by=sources.get(identifier, "adapter") if theirs is not None else None,
                note=note,
            )
        )
    return states


def _uncovered(identifier: str, not_covered: Sequence[NotCovered]) -> NotCovered | None:
    return next((entry for entry in not_covered if belongs_to(identifier, entry.id)), None)


def _direction(
    local: str | None, deployed: str | None, recorded: str
) -> tuple[Direction, str | None]:
    """The table's row for the hashes known against a recorded one."""
    if local is not None and deployed is not None:
        if local == recorded and deployed == recorded:
            return "identical", None
        if deployed == recorded:
            return "local_ahead", None
        if local == recorded:
            return "deployed_ahead", None
        return "diverged", RECORD_IS_STALE if local == deployed else None
    if local is not None:
        return ("identical" if local == recorded else "local_ahead"), None
    if deployed is not None:
        return ("identical" if deployed == recorded else "deployed_ahead"), None
    return "not_covered", None


def sync_status(
    sections: Sequence[SectionState], recorded: Fingerprint | None
) -> tuple[SyncStatus, NotCheckedReason | None]:
    """The Run-level Sync from the sections (ADR-0011 §4)."""
    if all(state.direction == "not_covered" for state in sections):
        return "not_checked", "adapter_cannot_observe"
    if recorded is None:
        return "not_checked", "no_fingerprint"
    if any(state.direction in BROKEN_DIRECTIONS for state in sections):
        return "broken", None
    return "held", None


def sync_result(
    sections: Sequence[SectionState], recorded: Fingerprint | None, environment: str | None
) -> SyncResult:
    """The comparison as one value: its status and reason, the record it was against."""
    status, reason = sync_status(sections, recorded)
    sources = {state.deployed_by for state in sections}
    return SyncResult(
        status=status,
        reason=reason,
        environment=environment,
        fingerprint=recorded.id if recorded is not None else None,
        built_at=recorded.built_at if recorded is not None else None,
        sections=list(sections),
        covered_by=next((source for source in DEPLOYED_SOURCES if source in sources), None),
    )


# --- the terminal ---

COLUMNS = ("section", "direction", "change", "covered")

NOT_CHECKED_HINTS: Mapping[str, str] = {
    "no_manifest": "`agentdiag init` writes a Manifest",
    "no_fingerprint": "`agentdiag sync` records the first Fingerprint",
    "adapter_cannot_observe": (
        "nothing observes this Target's deployed set: give the Manifest path pointers, an "
        "Adapter that observes, or a Connector"
    ),
}
"""What to run next, by `not_checked` reason: `sync --check` says it beside the reason."""


def status_text(sync: Mapping[str, Any]) -> str:
    """One Sync result in words, from its `run.json` or Scorecard form: `held`,
    `not_checked (no_fingerprint)`, or `broken, re-synced from 3f9c0e1a: prompt.system
    deployed_ahead` — the line `render_summary` prints, so a re-sync is never silent
    (ADR-0008). Read from a mapping so `show` can call it without the Run's models."""
    status = str(sync.get("status"))
    reason = sync.get("reason")
    text = status + (f" ({reason})" if reason else "")
    resynced = sync.get("resynced_from")
    sections = [section for section in sync.get("sections") or [] if isinstance(section, dict)]
    if resynced:
        text += f", re-synced from {short(str(resynced))}"
    if sections:
        text += ": " + ", ".join(f"{s.get('id')} {s.get('direction')}" for s in sections)
    return text


def resynced_line(sync: Mapping[str, Any]) -> str | None:
    """`Re-synced from <id>: <section> <direction> (<change>), …`: what `show` prints under
    its header when the Run rebuilt its Fingerprint (decision 14); None when it did not."""
    resynced = sync.get("resynced_from")
    if not resynced:
        return None
    sections = [section for section in sync.get("sections") or [] if isinstance(section, dict)]
    changed = ", ".join(
        f"{section.get('id')} {section.get('direction')}"
        + (f" ({section['change']})" if section.get("change") else "")
        for section in sections
    )
    return f"Re-synced from {resynced}: {changed or 'no section named'}"


def render_sync(result: SyncResult, *, wrote: str | None = None) -> str:
    """The section table under a header line, then the counts (decision 13)."""
    if result.fingerprint is None:
        header = f"no previous Fingerprint (environment {result.environment or NONE_SHOWN})"
        if result.reason == "adapter_cannot_observe":
            header = f"Sync not_checked (adapter_cannot_observe): {header}"
    else:
        text = result.status + (f" ({result.reason})" if result.reason else "")
        header = (
            f"Sync {text} against fingerprint {short(result.fingerprint)} "
            f"(built {result.built_at}, {result.environment or NONE_SHOWN})"
        )
    table = [list(COLUMNS)] + [
        [state.id, state.direction, state.change or NONE_SHOWN, state.covered, state.note or ""]
        for state in result.sections
    ]
    widths = [max(len(row[column]) for row in table) for column in range(len(COLUMNS))]
    lines = [header]
    for row in table:
        cells = [cell.ljust(width) for cell, width in zip(row, widths, strict=False)]
        lines.append("  ".join([*cells, *row[len(COLUMNS) :]]).rstrip())
    lines.append(counts_line(result.sections))
    if result.status == "not_checked" and result.reason is not None and wrote is None:
        lines.append(f"not checked: {result.reason}; {NOT_CHECKED_HINTS[result.reason]}")
    if wrote is not None:
        lines.append(f"wrote {wrote}")
    return "\n".join(lines)


COUNTED: tuple[Direction, ...] = (
    "identical",
    "local_ahead",
    "deployed_ahead",
    "diverged",
    "not_covered",
)


def counts_line(sections: Sequence[SectionState]) -> str:
    """`2 sections identical, 1 deployed_ahead, 1 not covered`: each direction with a
    non-zero count, in the table's order, the first with its noun."""
    parts: list[str] = []
    for direction in COUNTED:
        count = sum(1 for state in sections if state.direction == direction)
        if not count:
            continue
        word = "not covered" if direction == "not_covered" else direction
        noun = "" if parts else (" section" if count == 1 else " sections")
        parts.append(f"{count}{noun} {word}")
    return ", ".join(parts) or "no sections"


__all__ = [
    "COLUMNS",
    "COUNTED",
    "NOT_CHECKED_HINTS",
    "RECORD_IS_STALE",
    "SectionState",
    "SyncResult",
    "SyncSection",
    "compare_sections",
    "counts_line",
    "render_sync",
    "resynced_line",
    "status_text",
    "sync_result",
    "sync_status",
]
