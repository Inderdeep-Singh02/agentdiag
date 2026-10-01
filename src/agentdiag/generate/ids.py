"""Where a generated Scenario comes from, the id that follows from it, and which existing
Scenario a draft is (phase-6 decisions 29 and 30, ADR-0002 §8).

**Provenance has four forms**, one per source a Scenario is drafted from: a prompt section
(`prompt:<name>` or `prompt:<name>#<heading slug>`, the section ids of decision 10 with
`:` for the prefix's `.`), a tool (`tool:<name>`), a Trace (`trace:<run id>/<scenario>/<n>`)
and a Change record (`change:<id>`). The last two are ticket 34's sources; the grammar and
the anchor are fixed here so a Suite generated today and one generated from Traces later
share one id scheme.

**An id is `<anchor>-<slug(title)>`**, the anchor's words dropped from the title's slug when
it opens with them, at most `ID_MAX` characters and cut at a word boundary of the title,
with `-2`, `-3` on a clash within the Suite. The anchor is what the Scenario answers to: the
heading slug, the prompt's name when the prompt has no heading, the tool's name slugged, the
Trace's Scenario id, the Change record's id. The suffix fits inside the limit, so a long
title and a clash still give an id of at most `ID_MAX`.

**Matching keeps ids stable across regeneration.** A draft that names an existing id is that
Scenario; otherwise the first existing Scenario with the same provenance and the same title;
otherwise the one existing Scenario with its provenance, when that provenance is unique among
all the drafts and all the existing Scenarios — so rewording a rule's title keeps its id where
the section alone says which it is. A provenance several Scenarios or several drafts share
(the rules of a prompt with no headings, a section whose rule was replaced by another)
matches on the title only: guessing would hand one rule's Runs to another. Offline and pure:
the command decides what to write from what this returns.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Literal

from agentdiag.run.init import slug

ProvenanceKind = Literal["prompt", "tool", "trace", "change"]

ID_MAX = 60
"""The longest id `generate` derives (decision 29)."""

PROVENANCE_FORMS = (
    "prompt:<name>[#<section>]",
    "tool:<name>",
    "trace:<run id>/<scenario>/<n>",
    "change:<change record id>",
)
"""What an error names when a provenance is none of the four."""


class ProvenanceError(ValueError):
    """A provenance in none of the four forms."""


ManifestBlock = Literal["prompts", "tools"]


@dataclass(frozen=True)
class Provenance:
    """One parsed provenance: its form, the name it cites, the anchor an id starts with, and
    what of the Manifest and the Fingerprint it points into, each fixed by its form."""

    kind: ProvenanceKind
    name: str
    """The prompt's or tool's name, the Run id, or the Change record id."""

    anchor: str
    """What a derived id starts with (decision 29)."""

    section_id: str | None = None
    """The Fingerprint section id a prompt or tool provenance names (decision 10)."""

    listed_in: ManifestBlock | None = None
    """The Manifest block that must name `name`: `prompts`, `tools`, or none for a Trace or
    a Change record, which ticket 34 resolves."""


def _prompt(match: re.Match[str]) -> Provenance:
    name, section = match["name"], match["section"]
    return Provenance(
        "prompt",
        name,
        anchor=section or slug(name),
        section_id=f"prompt.{name}" + (f"#{section}" if section else ""),
        listed_in="prompts",
    )


def _tool(match: re.Match[str]) -> Provenance:
    name = match["name"]
    return Provenance("tool", name, anchor=slug(name), section_id=f"tool.{name}", listed_in="tools")


def _trace(match: re.Match[str]) -> Provenance:
    return Provenance("trace", match["run"], anchor=slug(match["scenario"]))


def _change(match: re.Match[str]) -> Provenance:
    return Provenance("change", match["id"], anchor=slug(match["id"]))


_FORMS: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], Provenance]], ...] = (
    (re.compile(r"^prompt:(?P<name>[^#\s]+)(?:#(?P<section>[^#\s]+))?$"), _prompt),
    (re.compile(r"^tool:(?P<name>\S+)$"), _tool),
    (
        re.compile(r"^trace:(?P<run>[^/\s]+)/(?P<scenario>[^/\s]+)/(?P<trial>[1-9][0-9]*)$"),
        _trace,
    ),
    (re.compile(r"^change:(?P<id>\S+)$"), _change),
)
"""The four forms, in `PROVENANCE_FORMS`' order: each pattern with what it parses to."""


def parse_provenance(text: str) -> Provenance:
    """`text` as one of the four forms, or `ProvenanceError` naming them."""
    for pattern, parsed in _FORMS:
        if match := pattern.match(text):
            return parsed(match)
    raise ProvenanceError(f"provenance {text!r} is none of {', '.join(PROVENANCE_FORMS)}")


def derive_id(anchor: str, title: str, *, taken: Collection[str]) -> str:
    """`<anchor>-<slug(title)>` within `ID_MAX`, suffixed `-2`, `-3` past every id `taken`."""
    head = slug(anchor)
    rest = slug(title)
    if rest == head or rest.startswith(f"{head}-"):
        # A title that opens with its anchor's words (a tool Scenario naming its tool) would
        # repeat them: walkthrough friction 20.
        rest = rest[len(head) + 1 :]
    whole = f"{head}-{rest}" if rest else head
    base = _clipped(whole, ID_MAX, keep=len(head))
    if base not in taken:
        return base
    number = 2
    while True:
        suffix = f"-{number}"
        candidate = _clipped(base, ID_MAX - len(suffix), keep=len(head)) + suffix
        if candidate not in taken:
            return candidate
        number += 1


def _clipped(text: str, limit: int, *, keep: int) -> str:
    """`text` within `limit`, cut at the last whole word after the first `keep` characters
    (the anchor) when there is one, so an id never ends mid-word."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if text[limit] != "-" and "-" in cut[keep + 1 :]:
        cut = cut.rsplit("-", 1)[0]
    return cut.rstrip("-")


@dataclass(frozen=True)
class Existing:
    """One Scenario of the Suite as it is on disk, as matching reads it."""

    id: str
    provenance: str | None
    title: str


@dataclass(frozen=True)
class Drafted:
    """One draft as matching reads it."""

    provenance: str
    title: str
    id: str | None = None
    """The id the draft names, when it names one."""


def match_drafts(drafts: Sequence[Drafted], existing: Sequence[Existing]) -> dict[int, int]:
    """Which existing Scenario each draft is, by position: draft index to existing index.

    Three passes: the id a draft names; provenance and title; and provenance alone where it
    is unique on both sides, over every draft and every existing Scenario (decision 30), so a
    section whose rule was replaced by another never hands the old rule's id to the new one."""
    matched: dict[int, int] = {}
    taken: set[int] = set()

    def claim(draft: int, scenario: int) -> None:
        matched[draft] = scenario
        taken.add(scenario)

    by_id = {scenario.id: position for position, scenario in enumerate(existing)}
    for position, entry in enumerate(drafts):
        found = by_id.get(entry.id) if entry.id is not None else None
        if found is not None and found not in taken:
            claim(position, found)

    for position, entry in enumerate(drafts):
        if position in matched:
            continue
        found = next(
            (
                index
                for index, scenario in enumerate(existing)
                if index not in taken
                and scenario.provenance == entry.provenance
                and scenario.title == entry.title
            ),
            None,
        )
        if found is not None:
            claim(position, found)

    drafted = Counter(entry.provenance for entry in drafts)
    held = Counter(scenario.provenance for scenario in existing)
    for position, entry in enumerate(drafts):
        if position in matched or drafted[entry.provenance] != 1 or held[entry.provenance] != 1:
            continue
        (found,) = (i for i, old in enumerate(existing) if old.provenance == entry.provenance)
        if found not in taken:
            claim(position, found)
    return matched


__all__ = [
    "ID_MAX",
    "PROVENANCE_FORMS",
    "Drafted",
    "Existing",
    "ManifestBlock",
    "Provenance",
    "ProvenanceError",
    "ProvenanceKind",
    "derive_id",
    "match_drafts",
    "parse_provenance",
]
