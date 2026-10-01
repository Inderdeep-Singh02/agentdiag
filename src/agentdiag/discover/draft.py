"""The Manifest draft `agentdiag discover` writes, and how it is rendered (decision 27).

**A draft is a Manifest with its guesses marked.** Every value the scan or the Connector's
read proposed, rather than one a person already wrote, sits under a `# REVIEW: <why this is
a guess>` line; the skill's "done when" is that none is left. Values copied from the
Target's existing Manifest (its name, its Family, its Connector's environments, its Suites,
its Suppressions) are facts and carry no REVIEW line: the draft is meant to replace that
Manifest, and dropping what an author wrote would lose it.

**Rendered with `init`'s template machinery**, not a YAML dumper: every value goes through
`templates.scalar()` (a mapping as one flow-style line), and each section opens with the
comment `init`'s scaffold gives it (`templates.TARGET_COMMENT` and its neighbours), so a
draft reads like the scaffold a developer already knows. The tree here is small on purpose:
an `DraftLine` is a key with a value or with children, an optional explanatory comment, an
optional review, and `missing` for a key the draft could not fill, written commented out
under its REVIEW so the agent sees the hole.

**A draft over an existing Manifest is that Manifest, line for line** (walkthrough friction
2): its text is kept byte for byte, comments included, and only new lines are inserted — an
absent key under the block it belongs to (a new top-level block at the end), each under its
REVIEW line; where the Connector's read or the scan would change a value the author wrote,
a REVIEW comment above that line gives the proposed value, and the line stays as written
(`annotate`). That is how `generate` inserts its one `suites` line.

**A draft always validates as a Manifest**: `render_draft`'s output is parsed back and
checked by `Manifest.model_validate` before anything is written (`command.discover`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from agentdiag.run.templates import (
    ADAPTER_COMMENT,
    CONNECTOR_COMMENT,
    TARGET_COMMENT,
    TOOLS_COMMENT,
    prompts_comment,
    scalar,
    suites_comment,
)

REVIEW = "# REVIEW:"
"""The marker above every guessed line. `grep -n 'REVIEW:'` finds what is left to decide."""

INDENT = "  "

DRAFT_HEADER = """\
# A Manifest draft, written by `agentdiag discover`: pointers, not copies, as a Manifest is.
#
# Every guessed line sits under a review comment (the word REVIEW and a colon) saying why
# it was guessed. Accept each by deleting its comment, or rewrite the line; then check the
# draft with `agentdiag validate --manifest <this file>` and move it to manifest.yaml.
schema_version: 1

"""
"""The fresh draft's header. It never spells the marker itself (walkthrough friction 8), so
`grep -n 'REVIEW:'` comes back empty once every guess is settled."""


@dataclass
class DraftLine:
    """One line of the draft: a key with a value, or with the lines nested under it, and
    what to say above it."""

    key: str
    """Empty for a comment with no key under it."""

    value: Any = None
    """A scalar, or a mapping or list written as one flow-style line."""

    children: list[DraftLine] | None = None
    review: str | None = None
    """Why this line is a guess; None for a value copied from the existing Manifest."""

    comment: str | None = None
    """An explanatory comment above the key, already `#`-prefixed, ending in a newline."""

    missing: bool = False
    """Nothing was found: the key is written commented out, `value` as its placeholder."""


@dataclass
class Draft:
    """The draft's top-level entries, in the order the scaffold writes its sections."""

    lines: list[DraftLine] = field(default_factory=list)

    @property
    def reviews(self) -> int:
        """How many REVIEW lines the draft holds."""

        def count(entries: list[DraftLine]) -> int:
            return sum(
                (entry.review is not None) + count(entry.children or []) for entry in entries
            )

        return count(self.lines)


def flow(value: Any) -> str:
    """One value on one line: a scalar through `scalar()`, anything else as flow YAML."""
    if isinstance(value, str):
        return scalar(value)
    if isinstance(value, bool) or value is None:
        return "true" if value is True else "false" if value is False else "null"
    if isinstance(value, int | float):
        return str(value)
    return yaml.safe_dump(value, default_flow_style=True, sort_keys=False, width=10**6).strip()


def render_draft(draft: Draft) -> str:
    """The draft as YAML text: the header, then each section with a blank line after it."""
    return DRAFT_HEADER + "".join(_render(line, 0) + "\n" for line in draft.lines)


def _render(entry: DraftLine, depth: int) -> str:
    pad = INDENT * depth
    lines = ""
    if entry.comment:
        lines += "".join(f"{pad}{line}\n" for line in entry.comment.rstrip("\n").splitlines())
    if entry.review is not None:
        lines += f"{pad}{REVIEW} {entry.review}\n"
    if not entry.key:
        return lines  # a comment with no key under it, as the scaffold's Eval parameters
    key = scalar(entry.key)
    if entry.missing:
        return lines + f"{pad}# {key}: {entry.value}\n"
    if entry.children is not None:
        if not entry.children:
            return lines + f"{pad}{key}: {{}}\n"
        return (
            lines
            + f"{pad}{key}:\n"
            + "".join(_render(child, depth + 1) for child in entry.children)
        )
    return lines + f"{pad}{key}: {flow(entry.value)}\n"


def copied(key: str, value: Any, comment: str | None = None) -> DraftLine:
    """An entry copied from the existing Manifest: a mapping kept as nested entries, so a
    later edit touches one line, and anything else as one value."""
    if isinstance(value, dict) and value:
        return DraftLine(
            key=key,
            children=[copied(str(name), child) for name, child in value.items()],
            comment=comment,
        )
    return DraftLine(key=key, value=value, comment=comment)


def merged(key: str, existing: Any, proposal: DraftLine | None) -> DraftLine:
    """What the existing Manifest wrote at `key`, kept verbatim, with only what the
    proposal adds that is absent from it: a key missing from a mapping, a field missing from
    a one-line mapping, each under the proposal's REVIEW line. With nothing written, the
    proposal; with no proposal, the existing value. An existing value never gives way to a
    guess: a rescan must not undo an author's `protected: true` or `side_effects: live`."""
    if proposal is None:
        return copied(key, existing)
    if existing is None:
        return proposal
    if proposal.children is not None and isinstance(existing, dict):
        proposed = {line.key: line for line in proposal.children}
        lines = [
            merged(str(name), value, proposed.get(str(name))) for name, value in existing.items()
        ]
        lines += [line for line in proposal.children if line.key not in existing]
        return DraftLine(key=key, children=lines, comment=proposal.comment)
    if isinstance(proposal.value, dict) and isinstance(existing, dict) and not proposal.missing:
        added = {name: value for name, value in proposal.value.items() if name not in existing}
        if added:
            return DraftLine(
                key=key,
                value={**existing, **added},
                review=f"{', '.join(added)} added: {proposal.review}",
            )
    kept = copied(key, existing)
    kept.comment = proposal.comment
    return kept


# --- a draft over an existing Manifest: the text kept, the new lines inserted ---

KEY_LINE = re.compile(r"""^(?P<indent> *)(?P<key>[^\s#'"{}\[\]-][^:#]*|'[^']*'|"[^"]*"):(\s|$)""")
"""A block-mapping key line: indentation, the key, a colon."""


def annotate(
    text: str,
    existing: dict[str, Any],
    draft: Draft,
    notes: list[tuple[tuple[str, ...], str]] | None = None,
) -> tuple[str, int]:
    """`text` (the existing Manifest) with the draft's new lines inserted and a REVIEW
    comment above every value the draft would change; `notes` are review comments on kept
    lines, by key path. Returns the text and how many REVIEW lines it holds."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    index = _KeyIndex(lines)
    inserts: list[tuple[int, int, str]] = []

    def add(position: int, block: str) -> None:
        inserts.append((position, len(inserts), block))

    def walk(entry: DraftLine, path: tuple[str, ...], held: Any) -> None:
        if not entry.key or not isinstance(held, dict):
            return
        here = (*path, entry.key)
        if entry.key not in held:
            pad = INDENT * len(path)
            placeholder = _commented_line(lines, pad, entry.key)
            if entry.missing and placeholder is not None:
                return  # already written commented out: proposing it again is noise
            block = _render(entry, len(path))
            if entry.review is None and not block.count(REVIEW) and not entry.missing:
                return  # nothing to review: a comment-only or copied line
            if placeholder is not None:
                note = (
                    f"{pad}{REVIEW} this block replaces the commented-out `{entry.key}` block "
                    "above; delete that one once this is accepted\n"
                )
                head = f"{pad}{entry.key}:"
                at = next(i for i, line in enumerate(block.splitlines()) if line.startswith(head))
                rows = block.splitlines(keepends=True)
                block = "".join([*rows[:at], note, *rows[at:]])
            where = index.end(path)
            if where is not None:
                add(where, block)
            elif not path:
                add(len(lines), "\n" + block)
            elif (parent := index.line(path)) is not None:
                # The parent is written in flow style: nothing nests under its line, so the
                # addition is proposed above it.
                pad = " " * index.indent(parent)
                add(parent, f"{pad}{proposal(entry, here)}\n")
            return
        value = held[entry.key]
        if (
            entry.review is not None
            and entry.children is None
            and isinstance(entry.value, dict)
            and isinstance(value, dict)
            and index.end(here) is not None
        ):
            # A one-line proposal over a block the author wrote: each key of it on its own,
            # so an absent one is inserted and only a changed one is a proposal.
            for key, item in entry.value.items():
                walk(DraftLine(key, item, review=entry.review), here, value)
            return
        if entry.review is not None and _normal(proposed(entry)) != _normal(value):
            line, _ = index.nearest(here)
            if line is None:
                return
            add(line, f"{' ' * index.indent(line)}{proposal(entry, here)}\n")
            return
        if entry.children is not None:
            for child in entry.children:
                walk(child, here, value)

    for entry in draft.lines:
        walk(entry, (), existing)
    for path, note in notes or []:
        line = index.line(path)
        if line is not None:
            add(line, f"{' ' * index.indent(line)}{REVIEW} {note}\n")
    for position, _, block in sorted(inserts, reverse=True):
        lines[position:position] = [block]
    result = "".join(lines)
    return result, sum(line.lstrip().startswith(REVIEW) for line in result.splitlines())


PROPOSED = "; proposed: "
"""What separates a REVIEW comment's reason from the value it proposes: `<reason>; proposed:
<dotted.key.path>: <flow value>`, the full key path from the top, so the proposal is one a
person, or a script, can apply without reading the lines around it."""


def proposal(entry: DraftLine, path: tuple[str, ...]) -> str:
    """The REVIEW comment proposing `entry`'s value at `path`."""
    reason = entry.review or "absent here"
    return f"{REVIEW} {reason}{PROPOSED}{'.'.join(path)}: {flow(proposed(entry))}"


def _commented_line(lines: list[str], pad: str, key: str) -> int | None:
    """The line where `key` is written commented out at this indentation (`# key: …`), as
    `init`'s scaffold writes the keys a Target it has never run cannot fill; None when none
    is (second walk friction 2)."""
    pattern = re.compile(rf"^{re.escape(pad)}# ?{re.escape(key)}:(\s|$)")
    return next((number for number, line in enumerate(lines) if pattern.match(line)), None)


def proposed(entry: DraftLine) -> Any:
    """What a draft line proposes, as a value: its children as a mapping."""
    if entry.children is not None:
        return {child.key: proposed(child) for child in entry.children if not child.missing}
    return entry.value


def _normal(value: Any) -> Any:
    """A value compared as the Manifest reads it: a pointer `{path: p}` (with the default
    `local_only: false`) is the bare path `p`."""
    if isinstance(value, dict):
        kept = {
            key: _normal(item)
            for key, item in value.items()
            if (key, item) != ("local_only", False)
        }
        if set(kept) == {"path"}:
            return kept["path"]
        return kept
    if isinstance(value, list):
        return [_normal(item) for item in value]
    return value


class _KeyIndex:
    """Where each block-mapping key of a YAML text is, by key path, and where its block ends."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.paths: dict[tuple[str, ...], int] = {}
        stack: list[tuple[int, str]] = []
        for number, line in enumerate(lines):
            match = KEY_LINE.match(line)
            if match is None:
                continue
            indent = len(match["indent"])
            while stack and stack[-1][0] >= indent:
                stack.pop()
            key = match["key"].strip().strip("'\"")
            stack.append((indent, key))
            self.paths.setdefault(tuple(name for _, name in stack), number)

    def line(self, path: tuple[str, ...]) -> int | None:
        return self.paths.get(path)

    def nearest(self, path: tuple[str, ...]) -> tuple[int | None, int]:
        """The line of `path`, or of its nearest ancestor that has one (a key inside a flow
        mapping has no line of its own), and how many keys deep that ancestor is, less one."""
        for depth in range(len(path), 0, -1):
            found = self.paths.get(path[:depth])
            if found is not None:
                return found, depth - 1
        return None, 0

    def indent(self, number: int) -> int:
        line = self.lines[number]
        return len(line) - len(line.lstrip(" "))

    def end(self, path: tuple[str, ...]) -> int | None:
        """The line after the last one of `path`'s block; None for the top level (the end of
        the file) or a key written in flow style, whose children have no lines."""
        if not path:
            return None
        start = self.line(path)
        if start is None:
            return None
        head = self.lines[start].split(":", 1)[1].split("#", 1)[0].strip()
        if head:
            return None  # `key: {…}` or `key: value`: nothing nests under it
        depth = self.indent(start)
        last = start
        for number in range(start + 1, len(self.lines)):
            stripped = self.lines[number].strip()
            if not stripped:
                continue
            if self.indent(number) <= depth:
                break
            last = number
        return last + 1


DATA_SOURCES_COMMENT = """\
# The Target's data sources, by name: each one's identity (a URL or DSN without its
# password, or the variable that holds it), so a change of database is a Sync section.
"""

SECTION_COMMENTS = {
    "target": TARGET_COMMENT,
    "adapter": ADAPTER_COMMENT,
    "prompts": prompts_comment("this Target directory"),
    "connector": CONNECTOR_COMMENT,
    "tools": TOOLS_COMMENT,
    "suites": suites_comment("this Target directory"),
    "data_sources": DATA_SOURCES_COMMENT,
}
"""The scaffold's comment above each section the draft shares with it."""


__all__ = [
    "DATA_SOURCES_COMMENT",
    "DRAFT_HEADER",
    "PROPOSED",
    "REVIEW",
    "SECTION_COMMENTS",
    "Draft",
    "DraftLine",
    "annotate",
    "copied",
    "flow",
    "merged",
    "proposal",
    "proposed",
    "render_draft",
]
