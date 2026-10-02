"""The gate for `examples/workspace/`, the two-Target example Workspace (ticket 13, phase-6
decision 47).

`examples/workspace/` is what a developer gets from `agentdiag init --target order-desk
--adapter toy`, `agentdiag init --target help-desk --adapter
python:agentdiag.examples.helpdesk:make_helpdesk --tools agentdiag.examples.helpdesk:make_tools
--model claude-sonnet-5` and `agentdiag init --skills`, then the help desk's Manifest and
Suite as a coding agent produced them from the two skills. As `examples/toy/` is, it is
asserted byte for byte against what the templates render, so a template change the example
does not follow fails here rather than drifting.

At the root it carries the Orientation page, its two import files and the vocabulary copy,
as `init` and `init --skills` write them (ticket 45, ADR-0016 §1-§2), gated against what
`orientation` renders over the example's own Registry.

The help desk is gated by the commands that write it: `discover --from-connector` drafts its
Manifest (with nothing to add) and saves its prompt and tool schemas, `generate --from
drafts/generated.yaml` writes its Suite, and `sync` its Fingerprint, each byte for byte.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.orientation import (
    CLAUDE_IMPORT_LINE,
    GEMINI_IMPORT_LINE,
    render_page,
    vocabulary_text,
)
from agentdiag.registry import registry
from agentdiag.run.init import GITIGNORE_LINES, ignore_outputs
from agentdiag.run.skills import CLAUDE_SKILLS, INSTALLED_PREFIX, _contents, packaged_skills
from agentdiag.run.templates import (
    JUDGE_NOTES_STARTER,
    TOY_SCAFFOLD,
    render_judge_notes,
    render_manifest,
    render_suite,
)
from agentdiag.workspace import Workspace

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = REPO / "examples" / "workspace"
ORDER_DESK = WORKSPACE / ".agentdiag" / "targets" / "order-desk"
HELP_DESK = WORKSPACE / ".agentdiag" / "targets" / "help-desk"
OUTPUT = ("runs", "restore-points", "platform", "index.sqlite")
"""What using the example writes that is output, gitignored and never gated."""

LOCAL = ("redaction.yaml",)
"""What `init` writes that the Workspace's gitignore keeps out of every clone (ADR-0015 §4):
a reader's own names to redact, never gated. `examples/toy/`, which has no gitignore of its
own, carries the starter and gates it."""

USED = (*OUTPUT, *LOCAL, "sync-breaks", "fingerprint.json")
"""Beside the output, what a `sync` writes: committed in a Workspace of one's own. The order
desk ships without them, so a `sync` a reader ran there is set aside; the help desk ships its
`fingerprint.json`, gated below, and no Sync break."""

ORDER_DESK_SCAFFOLD = replace(TOY_SCAFFOLD, slug="order-desk")
"""What `init --target order-desk --adapter toy` renders: the toy, under another slug."""


def test_the_workspace_holds_the_order_desk_and_the_help_desk_and_nothing_else() -> None:
    assert [target.slug for target in Workspace.find(WORKSPACE).targets()] == [
        "help-desk",
        "order-desk",
    ]
    held = sorted(p.name for p in (WORKSPACE / ".agentdiag").iterdir() if p.name not in OUTPUT)
    assert held == ["CONTEXT.md", "targets"]
    assert sorted(p.name for p in WORKSPACE.iterdir()) == [
        ".agentdiag",
        ".claude",
        ".gitignore",
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
    ]


def test_the_orientation_page_and_its_imports_are_what_init_writes_byte_for_byte() -> None:
    """The page over the example's two Targets, the one-line imports and the vocabulary: a
    Manifest edited here without `registry --write`, or a page text changed in the package
    and not regenerated here, fails."""
    page = render_page(registry(Workspace.find(WORKSPACE)))

    assert (WORKSPACE / "AGENTS.md").read_bytes() == page.encode("utf-8")
    assert (WORKSPACE / "CLAUDE.md").read_bytes() == f"{CLAUDE_IMPORT_LINE}\n".encode()
    assert (WORKSPACE / "GEMINI.md").read_bytes() == f"{GEMINI_IMPORT_LINE}\n".encode()
    assert (WORKSPACE / ".agentdiag" / "CONTEXT.md").read_bytes() == vocabulary_text().encode(
        "utf-8"
    )


def test_validate_finds_the_example_page_current(tmp_path: Path) -> None:
    """The example has skills installed, so an absent or stale page would be warned of."""
    root = help_desk_copy(tmp_path)

    for slug in ("help-desk", "order-desk"):
        result = CliRunner().invoke(app, ["validate", "--root", str(root), "--target", slug])
        assert result.exit_code == 0, result.output
        assert "AGENTS.md" not in result.stdout
        assert "CONTEXT.md" not in result.stdout


def test_the_order_desk_is_what_init_target_order_desk_renders_byte_for_byte() -> None:
    assert (ORDER_DESK / "manifest.yaml").read_text(encoding="utf-8") == render_manifest(
        ORDER_DESK_SCAFFOLD
    )
    assert (ORDER_DESK / "suites" / "sample.yaml").read_text(encoding="utf-8") == render_suite(
        ORDER_DESK_SCAFFOLD
    )
    assert (ORDER_DESK / "judge_notes.md").read_text(encoding="utf-8") == render_judge_notes(
        ORDER_DESK_SCAFFOLD
    )
    written = sorted(
        str(p.relative_to(ORDER_DESK))
        for p in ORDER_DESK.rglob("*")
        if p.is_file() and not set(p.relative_to(ORDER_DESK).parts) & set(USED)
    )
    assert written == ["judge_notes.md", "manifest.yaml", "suites/sample.yaml"]


def test_the_order_desk_is_the_chat_channel_of_the_northwind_family() -> None:
    manifest = (ORDER_DESK / "manifest.yaml").read_text(encoding="utf-8")

    assert "\nfamily: northwind\nchannel: chat\n" in manifest


def test_the_gitignore_is_what_init_writes(tmp_path: Path) -> None:
    fresh = tmp_path / ".gitignore"
    ignore_outputs(fresh, GITIGNORE_LINES)

    assert (WORKSPACE / ".gitignore").read_bytes() == fresh.read_bytes()


def test_the_installed_skills_are_the_packaged_ones_byte_for_byte() -> None:
    """What `init --skills` copied; the walkthrough agent reads these, so a skill fixed in
    the package and not re-installed here fails."""
    installed = WORKSPACE / CLAUDE_SKILLS
    packaged = packaged_skills()

    assert sorted(p.name for p in installed.iterdir()) == sorted(
        f"{INSTALLED_PREFIX}{name}" for name in packaged
    )
    for name, skill in packaged.items():
        here = installed / f"{INSTALLED_PREFIX}{name}"
        on_disk = {p.relative_to(here): p.read_bytes() for p in here.rglob("*") if p.is_file()}
        assert on_disk == _contents(skill), name


def help_desk_copy(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    shutil.copytree(
        WORKSPACE,
        root,
        ignore=shutil.ignore_patterns(
            "runs", "restore-points", "platform", "sync-breaks", "index.sqlite"
        ),
    )
    return root


def test_the_help_desk_holds_what_the_walkthrough_left_and_nothing_else() -> None:
    written = sorted(
        p.relative_to(HELP_DESK).as_posix()
        for p in HELP_DESK.rglob("*")
        if p.is_file() and not set(p.relative_to(HELP_DESK).parts) & {*OUTPUT, *LOCAL}
    )
    assert written == [
        "drafts/generated.yaml",
        "fingerprint.json",
        "judge_notes.md",
        "manifest.yaml",
        "prompts/system.md",
        "suites/generated.yaml",
        "tools/escalate.json",
        "tools/open_ticket.json",
        "tools/search_articles.json",
    ]
    assert (HELP_DESK / "judge_notes.md").read_text(encoding="utf-8") == JUDGE_NOTES_STARTER


def test_the_help_desk_manifest_prompts_and_tools_are_what_discover_writes_byte_for_byte(
    tmp_path: Path,
) -> None:
    """`discover --from-connector` over the checked-in Manifest saves the prompt and the tool
    schemas as they are committed, and drafts the Manifest itself byte for byte. Over an
    existing Manifest a draft is that file line for line, with only REVIEW-marked lines
    inserted (walkthrough friction 2); over this one there is nothing to insert, no line
    absent and no value the Connector's read would change, so the draft carries no REVIEW
    line at all and is compared whole. The onboarding from `init` that reaches this Manifest
    is `tests/test_drop_on_unknown_target.py`'s."""
    root = help_desk_copy(tmp_path)
    target = root / ".agentdiag" / "targets" / "help-desk"
    for saved in ("prompts", "tools"):
        shutil.rmtree(target / saved)

    result = CliRunner().invoke(
        app,
        [
            "discover",
            "--root",
            str(root),
            "--target",
            "help-desk",
            "--from-connector",
            "--env",
            "local",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "0 lines marked REVIEW" in result.output
    assert (target / "manifest.draft.yaml").read_bytes() == (
        HELP_DESK / "manifest.yaml"
    ).read_bytes()
    for folder in ("prompts", "tools"):
        for path in (HELP_DESK / folder).iterdir():
            assert (target / folder / path.name).read_bytes() == path.read_bytes(), path.name


def test_the_help_desk_suite_is_what_generate_writes_from_the_drafts_byte_for_byte(
    tmp_path: Path,
) -> None:
    root = help_desk_copy(tmp_path)
    target = root / ".agentdiag" / "targets" / "help-desk"
    (target / "suites" / "generated.yaml").unlink()

    result = CliRunner().invoke(
        app,
        [
            "generate",
            "--root",
            str(root),
            "--target",
            "help-desk",
            "--from",
            str(target / "drafts" / "generated.yaml"),
        ],
    )

    assert result.exit_code == 0, result.output
    suite = HELP_DESK / "suites" / "generated.yaml"
    assert (target / "suites" / "generated.yaml").read_bytes() == suite.read_bytes()
    assert (target / "manifest.yaml").read_bytes() == (HELP_DESK / "manifest.yaml").read_bytes()


def test_the_help_desk_fingerprint_is_what_sync_writes_byte_for_byte(tmp_path: Path) -> None:
    from agentdiag.sync.check import sync_target
    from agentdiag.sync.fingerprint import load_fingerprint

    root = help_desk_copy(tmp_path)
    committed = load_fingerprint(Workspace.find(WORKSPACE).resolve("help-desk"))
    assert committed is not None
    target = Workspace.find(root).resolve("help-desk")
    target.fingerprint.unlink()

    synced = sync_target(target, built_at=committed.built_at)

    assert synced.code == 0, synced.message
    assert target.fingerprint.read_bytes() == (HELP_DESK / "fingerprint.json").read_bytes()
    assert not committed.not_covered
