"""Seam 1: the Orientation page, its import files and the vocabulary copy (ticket 45, ADR-0016
§1-§3, 0.1.2-interfaces decisions 1-9 as amended after the ticket 45 reviews).

`init`, `init --skills` and `registry --write` write `AGENTS.md`, `CLAUDE.md` and `GEMINI.md`
at the Workspace root and `.agentdiag/CONTEXT.md` through one writer; `generate` and
`discover` refresh the marked table of a page that has one; a file the user owns keeps every
byte outside the marked block, whatever its line endings; `validate` warns everywhere when
the table is stale, and of anything else only where skills are installed. Every assertion is
on what a reader observes after a command: a file, a line printed, an exit code.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.orientation import (
    READ_SET_BUDGET_TOKENS,
    TARGETS_CLOSE,
    TARGETS_GENERATED,
    TARGETS_OPEN,
    vocabulary_text,
)
from agentdiag.run.skills import packaged_skills

REPO = Path(__file__).resolve().parents[1]
DRAFTS = REPO / "tests" / "fixtures" / "drafts" / "toy-order-desk.yaml"
runner = CliRunner()

STALE = "warning: AGENTS.md: the Targets table is stale; agentdiag registry --write regenerates it"
APPENDED = (
    "notice: AGENTS.md exists and had no Targets table; the marked section was appended at its "
    "end (regenerate it with agentdiag registry --write)"
)
ORIENTATION_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md", ".agentdiag/CONTEXT.md")


def invoke(*arguments: str) -> object:
    return runner.invoke(app, list(arguments))


def init(root: Path, *arguments: str) -> object:
    result = runner.invoke(app, ["init", "--root", str(root), *arguments])
    assert result.exit_code == 0, result.output
    return result


def three_targets(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    init(root)
    init(root, "--target", "a")
    init(root, "--target", "b")
    return root


def between_markers(text: str) -> str:
    start = text.index(TARGETS_OPEN)
    end = text.index(TARGETS_CLOSE) + len(TARGETS_CLOSE)
    return text[start:end]


def outside_markers(text: str) -> str:
    return text.replace(between_markers(text), "")


def rows(table: str) -> list[str]:
    """The slug cell of each row of the marked table."""
    body = [line for line in table.splitlines() if line.startswith("| `")]
    return [line.split("|")[1].strip().strip("`") for line in body]


def rename(root: Path, slug: str, name: str) -> None:
    path = root / ".agentdiag" / "targets" / slug / "manifest.yaml"
    text = path.read_text(encoding="utf-8")
    current = yaml.safe_load(text)["target"]["name"]
    path.write_text(text.replace(f"name: {current}\n", f"name: {name}\n", 1), encoding="utf-8")


def created(output: str) -> list[str]:
    """The paths `init` lists, between its first line and the first blank one."""
    lines = output.splitlines()
    return [line.strip() for line in lines[1 : lines.index("")]]


def warnings_counted(output: str) -> int:
    summary = output.strip().splitlines()[-1]
    return int(summary.rsplit(", ", 1)[1].split()[0])


# --- what init writes at the root ---


def test_init_writes_the_page_the_two_imports_and_the_vocabulary_at_the_root(
    tmp_path: Path,
) -> None:
    root = three_targets(tmp_path)

    assert sorted(path.name for path in root.iterdir()) == [
        ".agentdiag",
        ".gitignore",
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
    ]
    page = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert rows(between_markers(page)) == ["a", "b", "default"]
    outside = outside_markers(page).lower()
    for name in ("toy", "northwind", "order-desk", "order desk"):
        assert name not in outside, name
    assert (root / "CLAUDE.md").read_bytes() == b"@AGENTS.md\n"
    assert (root / "GEMINI.md").read_bytes() == b"@./AGENTS.md\n"
    assert (root / ".agentdiag" / "CONTEXT.md").read_bytes() == (REPO / "CONTEXT.md").read_bytes()


def test_the_page_names_the_vocabulary_and_every_packaged_skill(tmp_path: Path) -> None:
    root = tmp_path / "shop"
    init(root)

    page = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert page.startswith("# Orientation: an agentdiag Workspace\n")
    assert "and `.agentdiag/CONTEXT.md` for every word" in page
    named = ", ".join(f"/agentdiag-{name}" for name in packaged_skills())
    assert page.endswith(
        f"Skills this agentdiag ships, installed by `agentdiag init --skills`: {named}.\n"
    )
    assert between_markers(page).splitlines()[1] == TARGETS_GENERATED


def test_init_lists_the_orientation_files_after_the_targets_and_before_the_gitignore(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shop"

    first = init(root)

    listed = created(first.stdout)
    assert listed[-5:] == [*ORIENTATION_FILES, ".gitignore"]
    assert listed[0] == ".agentdiag/targets/default/manifest.yaml"

    second = init(root, "--target", "a")
    listed = created(second.stdout)
    assert "AGENTS.md" in listed, "the table gained a row"
    for unchanged in ORIENTATION_FILES[1:]:
        assert unchanged not in listed

    again = init(root, "--target", "a", "--force")
    listed = created(again.stdout)
    assert "AGENTS.md" not in listed, "a forced rewrite with the same Manifest leaves the table"


# --- a file the user owns ---


def test_a_page_the_user_wrote_keeps_every_byte_and_gains_the_marked_section(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shop"
    root.mkdir()
    own = "# Our repository\n\nOur own rules, in our own words.\n"
    (root / "AGENTS.md").write_text(own, encoding="utf-8")

    first = init(root)

    page = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert page.startswith(own)
    assert rows(between_markers(page)) == ["default"]
    assert page.endswith(TARGETS_CLOSE + "\n")
    assert APPENDED in first.stderr
    assert "AGENTS.md" in first.stdout

    second = init(root, "--target", "a")

    again = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert outside_markers(again) == outside_markers(page)
    assert rows(between_markers(again)) == ["a", "default"]
    assert "notice:" not in second.stderr


def test_import_files_the_user_owns_are_kept_and_named_in_a_notice(tmp_path: Path) -> None:
    root = tmp_path / "shop"
    root.mkdir()
    (root / "CLAUDE.md").write_text("# Our own instructions\n", encoding="utf-8")
    (root / "GEMINI.md").write_text("Read @./AGENTS.md first.\n", encoding="utf-8")

    result = init(root)

    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == "# Our own instructions\n"
    assert (root / "GEMINI.md").read_text(encoding="utf-8") == "Read @./AGENTS.md first.\n"
    assert (
        "notice: CLAUDE.md exists and does not import AGENTS.md; add the line @AGENTS.md so "
        "Claude Code reads the orientation page"
    ) in result.stderr
    assert "GEMINI.md" not in result.stderr, "it imports the page already"

    unoperated = invoke("validate", "--root", str(root))
    assert "CLAUDE.md" not in unoperated.stdout, "no skills: the user's files are theirs"
    assert invoke("init", "--root", str(root), "--skills").exit_code == 0
    validated = invoke("validate", "--root", str(root))
    assert "warning: CLAUDE.md: does not import AGENTS.md (add the line @AGENTS.md)" in (
        validated.stdout
    )
    assert "GEMINI.md" not in validated.stdout


# --- registry --write ---


def test_registry_write_regenerates_the_table_only_and_quiets_validate(tmp_path: Path) -> None:
    root = three_targets(tmp_path)
    before = (root / "AGENTS.md").read_text(encoding="utf-8")
    rename(root, "a", "renamed-desk")

    stale = invoke("validate", "--root", str(root), "--target", "a")
    assert STALE in stale.stdout.splitlines()
    assert stale.stdout.splitlines().index(STALE) == 0, "before the Target's lines"

    written = invoke("registry", "--root", str(root), "--write")

    assert written.exit_code == 0, written.output
    assert written.stdout.splitlines() == [
        "wrote AGENTS.md",
        "unchanged CLAUDE.md",
        "unchanged GEMINI.md",
        "unchanged .agentdiag/CONTEXT.md",
    ]
    after = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert outside_markers(after) == outside_markers(before)
    assert "| `a` | renamed-desk |" in between_markers(after)

    quiet = invoke("validate", "--root", str(root), "--target", "a")
    assert "AGENTS.md" not in quiet.stdout
    assert warnings_counted(quiet.stdout) == warnings_counted(stale.stdout) - 1


def test_registry_write_with_json_is_a_usage_error(tmp_path: Path) -> None:
    root = three_targets(tmp_path)
    page = (root / "AGENTS.md").read_bytes()
    rename(root, "a", "renamed-desk")

    result = invoke("registry", "--root", str(root), "--write", "--json")

    assert result.exit_code == 3
    assert "--json" in result.stderr and "--write" in result.stderr
    assert (root / "AGENTS.md").read_bytes() == page


def test_registry_write_puts_back_an_absent_page_and_vocabulary(tmp_path: Path) -> None:
    root = tmp_path / "shop"
    init(root)
    for name in ORIENTATION_FILES:
        (root / name).unlink()

    result = invoke("registry", "--root", str(root), "--write")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [f"wrote {name}" for name in ORIENTATION_FILES]
    for name in ORIENTATION_FILES:
        assert (root / name).is_file(), name


# --- what validate says ---


def test_validate_says_nothing_of_a_page_in_a_workspace_with_no_skills(tmp_path: Path) -> None:
    """A hand-built Workspace: no page, no import files, no vocabulary, no skills."""
    root = tmp_path / "shop"
    init(root)
    for name in ORIENTATION_FILES:
        (root / name).unlink()

    result = invoke("validate", "--root", str(root))

    assert result.exit_code == 0, result.output
    for name in ORIENTATION_FILES:
        assert name not in result.stdout, name


def test_with_skills_installed_validate_warns_of_a_deleted_page_naming_registry_write(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shop"
    init(root)
    skills = invoke("init", "--root", str(root), "--skills")
    assert skills.exit_code == 0, skills.output
    quiet = invoke("validate", "--root", str(root))
    assert "AGENTS.md" not in quiet.stdout
    (root / "AGENTS.md").unlink()
    (root / ".agentdiag" / "CONTEXT.md").unlink()

    result = invoke("validate", "--root", str(root))

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[:2] == [
        "warning: AGENTS.md: absent; a coding agent finds no orientation page; "
        "agentdiag registry --write writes one",
        "warning: .agentdiag/CONTEXT.md: absent; a coding agent finds no vocabulary; "
        "agentdiag registry --write writes one",
    ]
    assert warnings_counted(result.stdout) == warnings_counted(quiet.stdout) + 2


def test_init_skills_writes_the_orientation_files_that_are_absent(tmp_path: Path) -> None:
    root = tmp_path / "shop"
    init(root)
    for name in ORIENTATION_FILES:
        (root / name).unlink()

    result = invoke("init", "--root", str(root), "--skills")

    assert result.exit_code == 0, result.output
    assert (
        "Orientation page: wrote AGENTS.md, CLAUDE.md, GEMINI.md, .agentdiag/CONTEXT.md"
        in result.stdout.splitlines()
    )
    for name in ORIENTATION_FILES:
        assert (root / name).is_file(), name


def test_a_page_without_markers_is_a_validate_warning_once_skills_are_installed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shop"
    init(root)
    (root / "AGENTS.md").write_text("# Our own page\n", encoding="utf-8")
    assert "AGENTS.md" not in invoke("validate", "--root", str(root)).stdout
    skills = invoke("init", "--root", str(root), "--skills")
    assert skills.exit_code == 0, skills.output
    (root / "AGENTS.md").write_text("# Our own page\n", encoding="utf-8")

    result = invoke("validate", "--root", str(root))

    assert (
        "warning: AGENTS.md: no Targets table between <!-- agentdiag:targets --> markers; "
        "agentdiag registry --write adds one"
    ) in result.stdout.splitlines()


def test_validate_of_a_suite_named_by_path_or_a_draft_manifest_skips_the_workspace(
    tmp_path: Path,
) -> None:
    root = three_targets(tmp_path)
    rename(root, "a", "renamed-desk")
    target = root / ".agentdiag" / "targets" / "a"

    by_path = invoke("validate", "--root", str(root), str(target / "suites" / "sample.yaml"))
    draft = invoke(
        "validate",
        "--root",
        str(root),
        "--target",
        "a",
        "--manifest",
        str(target / "manifest.yaml"),
    )

    assert "AGENTS.md" not in by_path.stdout
    assert "AGENTS.md" not in draft.stdout


# --- the vocabulary and the read-set budget ---


def test_the_packaged_vocabulary_is_the_repositorys_byte_for_byte() -> None:
    assert (REPO / "src" / "agentdiag" / "CONTEXT.md").read_bytes() == (
        REPO / "CONTEXT.md"
    ).read_bytes()
    assert vocabulary_text() == (REPO / "CONTEXT.md").read_text(encoding="utf-8")


def test_the_read_set_fits_the_budget(tmp_path: Path) -> None:
    """The page as `registry --write` writes it for ten Targets, the vocabulary copy, the
    longest installed skill and one Target's Maintainer notes at their 600-word ceiling, since
    the page names the notes in the read set (ADR-0016 §1, amended decision 9)."""
    root = tmp_path / "shop"
    init(root, "--target", "customer-support-desk-00")
    for number in range(1, 10):
        init(root, "--target", f"customer-support-desk-{number:02d}")
    assert invoke("init", "--root", str(root), "--skills").exit_code == 0
    assert invoke("registry", "--root", str(root), "--write").exit_code == 0
    page = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert len(rows(between_markers(page))) == 10
    longest = max(
        (path.read_text(encoding="utf-8") for path in (root / ".claude").rglob("SKILL.md")),
        key=len,
    )
    vocabulary = (root / ".agentdiag" / "CONTEXT.md").read_text(encoding="utf-8")
    notes = root / ".agentdiag" / "targets" / "customer-support-desk-00" / "maintainer_notes.md"
    notes.write_text(
        notes.read_text(encoding="utf-8") + " ".join(["environment"] * 600) + "\n",
        encoding="utf-8",
    )
    maintainer = notes.read_text(encoding="utf-8")

    read_set = page + vocabulary + longest + maintainer
    assert len(read_set) // 4 <= READ_SET_BUDGET_TOKENS, len(read_set) // 4


# --- what the reviews found (amended contract, 2026-10-02) ---


def test_a_crlf_page_the_user_wrote_keeps_every_byte_outside_the_markers(tmp_path: Path) -> None:
    root = tmp_path / "shop"
    root.mkdir()
    own = b"# Our repository\r\n\r\nOur own rules.\r\n"
    (root / "AGENTS.md").write_bytes(own)

    init(root)
    first = (root / "AGENTS.md").read_bytes()
    init(root, "--target", "a")
    second = (root / "AGENTS.md").read_bytes()

    assert first.startswith(own) and second.startswith(own)
    assert b"\n" not in second.replace(b"\r\n", b""), "every line ends as the user's do"
    text = second.decode("utf-8")
    assert text.endswith(TARGETS_CLOSE + "\r\n")
    assert rows(between_markers(text)) == ["a", "default"]
    quiet = invoke("validate", "--root", str(root), "--target", "a")
    assert "AGENTS.md" not in quiet.stdout, "a CRLF table is not stale"


def test_a_lone_opening_marker_never_swallows_the_users_text(tmp_path: Path) -> None:
    root = tmp_path / "shop"
    root.mkdir()
    own = f"# Ours\n\n{TARGETS_OPEN}\nText we wrote after a stray marker.\n"
    (root / "AGENTS.md").write_text(own, encoding="utf-8")

    init(root)
    init(root, "--target", "a")

    page = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert page.startswith(own)
    assert rows(between_markers(page)) == ["a", "default"]


def test_registry_write_keeps_the_text_after_the_closing_marker(tmp_path: Path) -> None:
    root = three_targets(tmp_path)
    page = root / "AGENTS.md"
    page.write_text(page.read_text(encoding="utf-8") + "\n## Ours\n\nKept.\n", encoding="utf-8")
    rename(root, "a", "renamed-desk")

    after = page.read_text(encoding="utf-8").split(TARGETS_CLOSE)[1]

    assert invoke("registry", "--root", str(root), "--write").exit_code == 0

    written = page.read_text(encoding="utf-8")
    assert written.split(TARGETS_CLOSE)[1] == after
    assert after.endswith("\n## Ours\n\nKept.\n")
    assert "renamed-desk" in between_markers(written)


def test_a_second_registry_write_leaves_every_file_unchanged(tmp_path: Path) -> None:
    root = three_targets(tmp_path)
    rename(root, "a", "renamed-desk")
    assert invoke("registry", "--root", str(root), "--write").exit_code == 0

    again = invoke("registry", "--root", str(root), "--write")

    assert again.stdout.splitlines() == [f"unchanged {name}" for name in ORIENTATION_FILES]


def test_generate_refreshes_the_table_it_changed(tmp_path: Path) -> None:
    root = tmp_path / "shop"
    init(root)

    result = invoke("generate", "--root", str(root), "--from", str(DRAFTS))

    assert result.exit_code == 0, result.output
    assert f"Refreshed the Targets table in {root / 'AGENTS.md'}." in result.stdout
    page = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "sample, generated" in between_markers(page)
    assert "AGENTS.md" not in invoke("validate", "--root", str(root)).stdout


def test_generate_and_discover_never_write_an_absent_page(tmp_path: Path) -> None:
    root = tmp_path / "shop"
    init(root)
    (root / "AGENTS.md").unlink()
    scanned = tmp_path / "empty"
    scanned.mkdir()

    generated = invoke("generate", "--root", str(root), "--from", str(DRAFTS))
    discovered = invoke(
        "discover", "--root", str(root), "--target", "fresh", "--scan", str(scanned)
    )

    assert generated.exit_code == 0, generated.output
    assert discovered.exit_code == 0, discovered.output
    assert not (root / "AGENTS.md").exists()
    assert "Targets table" not in generated.stdout + discovered.stdout


def test_discover_of_a_new_target_refreshes_the_table(tmp_path: Path) -> None:
    root = tmp_path / "shop"
    init(root)
    scanned = tmp_path / "empty"
    scanned.mkdir()

    result = invoke("discover", "--root", str(root), "--target", "fresh", "--scan", str(scanned))

    assert result.exit_code == 0, result.output
    assert f"  wrote {root / 'AGENTS.md'} (the Targets table)" in result.stdout.splitlines()
    page = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert rows(between_markers(page)) == ["default", "fresh"]
    assert "| `fresh` | (problem: No Manifest at .agentdiag/targets/fresh/manifest.yaml) |" in (
        between_markers(page)
    )
