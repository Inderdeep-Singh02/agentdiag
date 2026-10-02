"""What the migrate skill's proof and the guide's walkthrough of it share (ticket 49,
ADR-0016 §7, 0.1.2-interfaces decision 24 as amended after the ticket 49 reviews).

`tests/fixtures/migrate/source/` is the invented source repository; the skill's judgement
steps over it are written down here (the identity, the Manifest edits) and in
`tests/fixtures/migrate/judgement/` (the drafts and the two notes files), so
`tests/test_migrate_skill.py` and `tests/test_readme.py` make the same edits, as
`tests/change_fixtures.py` serves the Change record tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "tests" / "fixtures" / "migrate" / "source"
JUDGEMENT = REPO / "tests" / "fixtures" / "migrate" / "judgement"

SLUG = "library-desk"
SUITE = "riverbend"

IDENTITY = (
    ("--name", "Ada"),
    (
        "--description",
        "The Riverbend Public Library's chat assistant: looks titles up in the catalogue and "
        "renews loans for members.",
    ),
    ("--family", "riverbend"),
    ("--channel", "chat"),
)
"""Step 1's identity, from the source: the name from the prompt's persona, the description
from NOTES.md's Business section and the tools, the Family from the library's name (a voice
build of Ada would join it), the channel from "chat on the website only"."""

MANIFEST_EDITS = (
    (
        "    # REVIEW: one block per environment the Target runs in, protected: true on every "
        "one that reaches real users\n    dev: {}\n",
        "    dev: {}\n"
        "    # The live website (the source's NOTES.md): never pushed to without the head\n"
        "    # librarian's go-ahead.\n"
        "    prod: {protected: true}\n",
    ),
    (
        "# REVIEW: name each prompt as a path under this Target directory (prompts/system.md) "
        "once the file is here, or observed when only the running Target shows it\n"
        "# prompts:\n#   system: prompts/system.md\n",
        "prompts:\n  system: prompts/system.md\n",
    ),
    (
        "# tools:\n#   lookup_order: {kind: retrieval}\n",
        "tools:\n"
        "  # A catalogue lookup: it changes nothing.\n"
        "  find_book: {kind: retrieval, schema: tools/find_book.json}\n"
        "  # Extends a member's loan: it changes state, whatever else it does.\n"
        "  renew_loan: {kind: action, schema: tools/renew_loan.json}\n",
    ),
)
"""Steps 3 and 4 in the Manifest `init` wrote: the environments REVIEW line settled from
NOTES.md (`prod` reaches real users, so it is protected), the prompt's REVIEW line settled by
its pointer, and the commented tools block replaced by the two tools, each kind with its
clause. The Adapter kind's and the Connector's REVIEW lines stay: this skill does not settle
them."""

RETIRE_SAMPLE = (
    "{path: suites/sample.yaml, status: draft}",
    "{path: suites/sample.yaml, status: retired}",
)
"""Step 7's first edit, made before `generate` so its one write refreshes the Targets table
with the Suites as they stay; the guide's walkthrough makes it with `sed`."""


def replace_once(path: Path, edits: Sequence[tuple[str, str]]) -> None:
    """Make each `(before, after)` edit in `path`, each `before` found exactly once. The
    Manifest is edited as text, never loaded and dumped as YAML, because what a person reads
    in it must survive the edit: the REVIEW lines still to settle and every comment `init`
    wrote."""
    text = path.read_text(encoding="utf-8")
    for before, after in edits:
        assert text.count(before) == 1, f"{path.name} no longer holds {before!r} once"
        text = text.replace(before, after)
    path.write_text(text, encoding="utf-8")


def settle_manifest(manifest: Path) -> None:
    """Steps 3 and 4: every edit of `MANIFEST_EDITS`."""
    replace_once(manifest, MANIFEST_EDITS)


def retire_sample(manifest: Path) -> None:
    """Step 7's first edit: `init`'s sample Suite marked retired."""
    replace_once(manifest, (RETIRE_SAMPLE,))
