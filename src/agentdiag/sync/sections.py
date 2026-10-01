"""Section ids and canonical text: what one Fingerprint section is, and what it hashes.

A Fingerprint names what changed by section (ADR-0007 §1), so the one grammar of a section
id and the one canonical text each section's sha256 is taken over live here, and nowhere
else (phase-6 decision 10):

- `prompt.<name>` for a prompt with no markdown heading; `prompt.<name>#<heading-slug>` per
  heading section, any `#` level starting one (the heading tree flattened), and
  `prompt.<name>#_preamble` for the text before the first heading. A repeated slug gets
  `-2`, `-3`, so two headings with one title are still two sections.
- `tool.<name>`: the tool's schema as canonical JSON, so a whitespace edit or a reordered
  key is not a change. The schema is the whole entry of the request's `tools` list —
  `name`, `description`, `input_schema` — as the probe observes it; a tool schema file a
  Manifest points at holds that same entry shape, so a Connector's read of the deployed
  tool (ticket 23) hashes the same way the file does.
- `data_source.<name>`: the identity string, stripped.
- `model` and `provider`: the resolved model id, and the provider the client reaches.
- `tier`: the resolved tier, stripped; `flow.<id>`: the Flow's definition, its `state`
  included, as canonical JSON. Only a Connector reads either (ticket 23).

The canonical text of a prose section is its body with trailing whitespace stripped per
line, CRLF normalised to LF, and exactly one trailing newline: a Windows checkout, an
editor that trims lines and one that does not all hash the same prompt the same.

Offline: `sync --check`, `validate` and `show` read this, and none of them reaches a model.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel

from agentdiag.run.init import slug
from agentdiag.types import SectionKind

PROMPT_PREFIX = "prompt."
TOOL_PREFIX = "tool."
DATA_SOURCE_PREFIX = "data_source."
FLOW_PREFIX = "flow."
MODEL_SECTION = "model"
PROVIDER_SECTION = "provider"
TIER_SECTION = "tier"
PREAMBLE_SLUG = "_preamble"
"""The heading slug of the text before a prompt's first heading. The underscore keeps it
apart from any slug a heading can produce, which is letters, digits and hyphens."""

HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
"""An ATX markdown heading line at any level: `# Title`, `### Rule 3 ###`."""


class Hashed(BaseModel):
    """One section as a source knows it: its kind, the sha256 of its canonical text, and
    what a human reads it by (the heading title, the tool name, the model id)."""

    kind: SectionKind
    sha256: str
    summary: str | None = None


def sha256(text: str) -> str:
    """The hash every section records, over UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_text(text: str) -> str:
    """A prose section as it is hashed: LF line ends, no trailing whitespace on any line,
    and one trailing newline (an empty body is the empty string)."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return "\n".join(lines) + "\n" if lines else ""


def canonical_json(value: Any) -> str:
    """A tool schema as it is hashed: keys sorted, no whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _headings(lines: list[str]) -> list[tuple[int, str, str]]:
    """`(line index, heading slug, title)` of every heading line, a repeated slug given
    `-2`, `-3` in the order the headings appear: the one derivation both `split_sections`
    and `replace_section` use, so a section id names the same lines in both."""
    found: list[tuple[int, str, str]] = []
    seen: dict[str, int] = {}
    for index, line in enumerate(lines):
        match = HEADING.match(line)
        if not match:
            continue
        title = match.group(2)
        base = slug(title) if title.strip() else "section"
        seen[base] = seen.get(base, 0) + 1
        found.append((index, base if seen[base] == 1 else f"{base}-{seen[base]}", title))
    return found


def split_sections(text: str) -> list[tuple[str | None, str | None, str]]:
    """The prompt split on its markdown headings: `(heading slug, title, body)` per section,
    in order. A prompt with no heading is one section whose slug and title are None; text
    before the first heading, when there is any, is the `_preamble` section. A repeated slug
    gets `-2`, `-3`, in the order the headings appear. A body is the lines between its
    heading and the next, as written, so `replace_section` puts back exactly what this took."""
    lines = _lines(text)
    headings = _headings(lines)
    if not headings:
        return [(None, None, "\n".join(lines))]
    found: list[tuple[str | None, str | None, str]] = []
    preamble = "\n".join(lines[: headings[0][0]])
    if canonical_text(preamble):
        found.append((PREAMBLE_SLUG, None, preamble))
    ends = [index for index, _, _ in headings[1:]] + [len(lines)]
    for (index, heading_slug, title), end in zip(headings, ends, strict=True):
        found.append((heading_slug, title, "\n".join(lines[index + 1 : end])))
    return found


class SectionMissing(ValueError):
    """A heading section a write names and the text does not hold, given with no heading
    line of its own to add it by."""


def replace_section(text: str, heading_slug: str, body: str) -> str:
    """`text` with the section `heading_slug` written, its heading line kept (phase-7
    decisions 11 and 15): the lines between that heading and the next become `body`, and
    `_preamble` is the lines before the first heading.

    A section the text does not hold is added at the end when `body` opens with its own
    heading line, one whose slug is `heading_slug`: that is how a section added on one side
    reaches the other, since a body alone carries no title. Otherwise `SectionMissing`.
    Line ends are LF; a body followed by another heading is ended with a newline, so that
    heading stays on its own line."""
    lines = _lines(text)
    headings = _headings(lines)
    replacement = _lines(body)
    if heading_slug == PREAMBLE_SLUG:
        first = headings[0][0] if headings else len(lines)
        if headings and replacement[-1] != "":
            replacement.append("")
        return "\n".join([*replacement, *lines[first:]])
    for position, (index, found, _) in enumerate(headings):
        if found != heading_slug:
            continue
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        if end < len(lines) and replacement[-1] != "":
            replacement.append("")
        return "\n".join([*lines[: index + 1], *replacement, *lines[end:]])
    added = _headings(replacement)
    if not added or added[0][0] != 0 or added[0][1] != heading_slug:
        raise SectionMissing(
            f"the text holds no heading section {heading_slug!r}, and the value written to it "
            "does not open with that heading"
        )
    kept = "\n".join(lines).rstrip("\n")
    joined = "\n".join(replacement)
    return (f"{kept}\n\n" if kept else "") + joined + ("" if joined.endswith("\n") else "\n")


def section_text(text: str, heading_slug: str) -> str | None:
    """The body of section `heading_slug` of `text`, as `split_sections` gives it; None when
    the text holds no such section."""
    for found, _, body in split_sections(text):
        if found == heading_slug:
            return body
    return None


def heading_line(text: str, heading_slug: str) -> str | None:
    """The heading line of section `heading_slug` as `text` writes it; None when absent."""
    lines = _lines(text)
    for index, found, _ in _headings(lines):
        if found == heading_slug:
            return lines[index]
    return None


def prompt_sections(name: str, text: str) -> dict[str, Hashed]:
    """Every section of one prompt, by section id."""
    sections: dict[str, Hashed] = {}
    for heading_slug, title, body in split_sections(text):
        identifier = f"{PROMPT_PREFIX}{name}" + (f"#{heading_slug}" if heading_slug else "")
        summary = title if title is not None else ("preamble" if heading_slug else name)
        sections[identifier] = Hashed(
            kind="prompt", sha256=sha256(canonical_text(body)), summary=summary
        )
    return sections


def prompt_id(name: str) -> str:
    """The id of a prompt pointer as a whole: what a `not_covered` entry names, and the
    prefix every one of its sections carries."""
    return f"{PROMPT_PREFIX}{name}"


def belongs_to(section_id: str, covered_id: str) -> bool:
    """Whether `section_id` is `covered_id` or one of its heading sections."""
    return section_id == covered_id or section_id.startswith(f"{covered_id}#")


def tool_section(name: str, schema: Any) -> tuple[str, Hashed]:
    return f"{TOOL_PREFIX}{name}", Hashed(
        kind="tool", sha256=sha256(canonical_json(schema)), summary=name
    )


def data_source_section(name: str, identity: str) -> tuple[str, Hashed]:
    return f"{DATA_SOURCE_PREFIX}{name}", Hashed(
        kind="data_source", sha256=sha256(identity.strip()), summary=identity.strip()
    )


def model_section(model: str) -> tuple[str, Hashed]:
    return MODEL_SECTION, Hashed(kind="model", sha256=sha256(model.strip()), summary=model)


def tier_section(tier: str) -> tuple[str, Hashed]:
    return TIER_SECTION, Hashed(kind="tier", sha256=sha256(tier.strip()), summary=tier)


def flow_section(identifier: str, definition: Any) -> tuple[str, Hashed]:
    """A Flow as its definition with its state: switching a Flow off moves the section."""
    return f"{FLOW_PREFIX}{identifier}", Hashed(
        kind="flow", sha256=sha256(canonical_json(definition)), summary=identifier
    )


def provider_section(provider: str) -> tuple[str, Hashed]:
    return PROVIDER_SECTION, Hashed(
        kind="provider", sha256=sha256(provider.strip()), summary=provider
    )


__all__ = [
    "DATA_SOURCE_PREFIX",
    "FLOW_PREFIX",
    "HEADING",
    "MODEL_SECTION",
    "PREAMBLE_SLUG",
    "PROMPT_PREFIX",
    "PROVIDER_SECTION",
    "TIER_SECTION",
    "TOOL_PREFIX",
    "Hashed",
    "SectionMissing",
    "belongs_to",
    "canonical_json",
    "canonical_text",
    "data_source_section",
    "flow_section",
    "heading_line",
    "model_section",
    "prompt_id",
    "prompt_sections",
    "provider_section",
    "replace_section",
    "section_text",
    "sha256",
    "split_sections",
    "tier_section",
    "tool_section",
]
