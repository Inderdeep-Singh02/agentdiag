"""Where a section lives on the local side, and the value one side writes into the other.

`pull` writes deployed sections into the files the Manifest points at, and `push` writes
the pointed files' sections to the deployed set (ADR-0011 §5, §6). Both ask the same two
questions of a section id, answered here once:

- **which file holds it** (`pointed_file`): a `prompt.<name>` or `prompt.<name>#<slug>`
  section lives in the file `prompts.<name>` points at, a `tool.<name>` in the file
  `tools.<name>.schema` points at; every other section (the model, the provider, the tier,
  a Flow, a data source's identity) and every `observed` pointer has no local file, and
  the reason says so. A pointer that resolves outside the Workspace root is named as such
  (ADR-0013 §2): compared, never written;
- **what value carries it across** (`section_value`): a heading section's body, as
  `sections.split_sections` gives it, or — when the side written to does not hold that
  heading — its heading line and body, so `sections.replace_section` can add it; a
  prompt with no headings as its whole text; a tool as its schema.

Offline: it reads files and the Manifest.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agentdiag.run.manifest import Manifest, PromptPointer
from agentdiag.sync.sections import (
    PREAMBLE_SLUG,
    PROMPT_PREFIX,
    TOOL_PREFIX,
    heading_line,
    section_text,
)
from agentdiag.workspace import TargetPaths

OUTSIDE_ROOT = "outside the Workspace root"
"""Why a section whose pointer resolves outside the root is compared and never written."""

PointedKind = Literal["prompt", "tool"]


@dataclass(frozen=True)
class PointedFile:
    """The local file one section lives in."""

    section: str
    kind: PointedKind
    name: str
    """The prompt's or the tool's name in the Manifest."""

    path: Path
    relative: str
    """The pointer as the Manifest writes it, relative to the Target directory."""


def pointed_file(target: TargetPaths, manifest: Manifest, section: str) -> PointedFile | str:
    """The file section `section` lives in, or why no local file holds it."""
    if section.startswith(PROMPT_PREFIX):
        name = section.removeprefix(PROMPT_PREFIX).partition("#")[0]
        pointer = manifest.prompts.get(name)
        if pointer is None:
            return f"the Manifest names no prompt {name!r}"
        if not isinstance(pointer, PromptPointer):
            return f"prompts.{name} is {pointer}: no local file holds it"
        return _pointed(target, section, "prompt", name, pointer.path)
    if section.startswith(TOOL_PREFIX):
        name = section.removeprefix(TOOL_PREFIX)
        entry = manifest.tools.get(name)
        if entry is None:
            return f"the Manifest names no tool {name!r}"
        if not isinstance(entry.schema_, PromptPointer):
            return f"tools.{name}.schema is not a path: no local file holds it"
        return _pointed(target, section, "tool", name, entry.schema_.path)
    return "no local file holds it: it is read from the deployed set only"


def _pointed(
    target: TargetPaths, section: str, kind: PointedKind, name: str, relative: str
) -> PointedFile | str:
    path = target.relative(relative)
    if outside_root(target, path):
        return f"{OUTSIDE_ROOT}: {relative} resolves to {path.resolve().as_posix()}"
    return PointedFile(section=section, kind=kind, name=name, path=path, relative=relative)


def outside_root(target: TargetPaths, path: Path) -> bool:
    """Whether `path` resolves outside the Workspace root (ADR-0013 §2)."""
    try:
        path.resolve().relative_to(target.root.resolve())
    except ValueError:
        return True
    return False


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def section_value(section: str, text: str, other: str) -> str:
    """The value of prompt section `section` of `text`, for writing into `other` (the
    other side's text of the same prompt): a heading's body, or its heading line and body
    when `other` does not hold that heading; the whole text for a prompt with none."""
    _, _, heading = section.removeprefix(PROMPT_PREFIX).partition("#")
    if not heading:
        return text
    body = section_text(text, heading) or ""
    line = heading_line(text, heading)
    if heading == PREAMBLE_SLUG or line is None or heading_line(other, heading) is not None:
        return body
    return f"{line}\n{body}"


def narrow[T](
    candidates: Mapping[str, T], sections: Sequence[str] | None
) -> tuple[dict[str, T], str | None]:
    """`candidates` narrowed to the `--section`s named, and the refusal when one names no
    candidate: what `pull` and `push` both do with `--section`, in one spelling."""
    if not sections:
        return dict(candidates), None
    missing = [section for section in sections if section not in candidates]
    if missing:
        known = ", ".join(sorted(candidates)) or "none"
        return {}, (
            f"not a section this command would write: {', '.join(missing)} "
            f"(the candidates are {known})"
        )
    return {key: value for key, value in candidates.items() if key in set(sections)}, None


def render_schema(schema: Any) -> str:
    """A tool schema as a local file holds it: `discover --from-connector`'s spelling
    (indented, keys in the platform's order), so a pull after a discovery moves no byte."""
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


__all__ = [
    "OUTSIDE_ROOT",
    "PointedFile",
    "narrow",
    "outside_root",
    "pointed_file",
    "read_text",
    "render_schema",
    "section_value",
]
