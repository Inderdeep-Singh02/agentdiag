"""Seam 1: a Target's Maintainer notes, `maintainer_notes.md` beside the Manifest (ticket 47,
ADR-0016 §5, 0.1.2-interfaces decisions 15-18).

Every scaffold `init` writes carries the starter and the Manifest's `maintainer_notes` key;
`--force` keeps notes an author wrote; `discover` writes them for a Target it creates.
`validate` holds the pointer to ADR-0015 §2's rule and errors on one naming no file, and a
Manifest without the key is checked as before. `target show`, `registry --json` and the
Orientation page's Targets table carry them. The Judge never reads them: a replayed toy Run
with a sentinel sentence in the notes writes it into no file of the Run, where every Judge
prompt is recorded whole. `target show` holds the pointer to the same rule as `validate`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.eval.notes import author_text
from agentdiag.run.templates import MAINTAINER_NOTES_STARTER, TOY_SCAFFOLD, render_maintainer_notes
from tests.fakes.workspace import CUSTOM_ADAPTER, edit_manifest, manifest_of, target_dir
from tests.stories import CANCEL_EXIT

REPO = Path(__file__).resolve().parents[1]
TOY_RECORDING = REPO / "tests" / "fixtures" / "recordings" / "toy-cancel.jsonl"
TOY_SCENARIO = "cancel-processing-order"
TOY_SOURCE = REPO / "src" / "agentdiag" / "examples" / "toy"
EVAL_PACKAGE = REPO / "src" / "agentdiag" / "eval"

HEADINGS = (
    "## What the Target does",
    "## Who it serves",
    "## Environments",
    "## Known traps",
    "## Where evidence lives",
)
SENTINEL = "The quartermaster's ledger is kept in the blue binder behind the third shelf."
"""A sentence nothing else in agentdiag or its recordings holds."""

runner = CliRunner()


def invoke(*arguments: str) -> Any:
    return runner.invoke(app, list(arguments))


def words_of(text: str) -> int:
    """Words outside the HTML comment, as `target show` counts them."""
    return len(author_text(text).split())


SCAFFOLDS = {
    "toy": (),
    "toy-under-a-slug": ("--target", "desk", "--adapter", "toy"),
    "custom": ("--target", "desk", "--adapter", CUSTOM_ADAPTER),
    "pending": ("--target", "desk"),
}


# --- what init writes ---


@pytest.mark.parametrize("scaffold", sorted(SCAFFOLDS))
def test_every_scaffold_writes_the_notes_starter_and_the_manifest_names_it(
    scaffold: str, tmp_path: Path
) -> None:
    arguments = SCAFFOLDS[scaffold]
    slug = "desk" if arguments else "default"

    result = invoke("init", "--root", str(tmp_path), *arguments)

    assert result.exit_code == 0, result.output
    notes = target_dir(tmp_path, slug) / "maintainer_notes.md"
    text = notes.read_text(encoding="utf-8")
    assert text == MAINTAINER_NOTES_STARTER == render_maintainer_notes(TOY_SCAFFOLD)
    assert text.startswith("<!--")
    assert [line for line in text.splitlines() if line.startswith("## ")] == list(HEADINGS)
    assert manifest_of(tmp_path, slug)["maintainer_notes"] == "maintainer_notes.md"
    lines = (target_dir(tmp_path, slug) / "manifest.yaml").read_text("utf-8").splitlines()
    at = lines.index("judge_notes: judge_notes.md")
    assert lines[at + 1].startswith("# ") and lines[at + 2].startswith("# ")
    comment = " ".join(line.removeprefix("# ") for line in lines[at + 1 : at + 3])
    assert "every agentdiag skill before its first step" in comment
    assert "never by the Judge" in comment
    assert lines[at + 3] == "maintainer_notes: maintainer_notes.md"
    assert f"  .agentdiag/targets/{slug}/maintainer_notes.md" in result.stdout.splitlines()


def test_force_keeps_the_maintainer_notes_an_author_wrote(tmp_path: Path) -> None:
    assert invoke("init", "--root", str(tmp_path)).exit_code == 0
    notes = target_dir(tmp_path) / "maintainer_notes.md"
    notes.write_text("## What the Target does\n\nTakes orders for the shop.\n", encoding="utf-8")

    result = invoke("init", "--root", str(tmp_path), "--force")

    assert result.exit_code == 0, result.output
    assert notes.read_text(encoding="utf-8") == (
        "## What the Target does\n\nTakes orders for the shop.\n"
    )
    assert "maintainer_notes.md" not in result.stdout


# --- what discover writes ---


def test_discover_writes_the_notes_for_a_target_it_creates_and_names_them(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shop"
    assert invoke("init", "--root", str(root)).exit_code == 0

    result = invoke("discover", "--root", str(root), "--target", "new", "--scan", str(TOY_SOURCE))

    assert result.exit_code == 0, result.output
    notes = target_dir(root, "new") / "maintainer_notes.md"
    assert notes.read_text(encoding="utf-8") == MAINTAINER_NOTES_STARTER
    assert f"  wrote {notes} (the Maintainer notes starter)" in result.stdout.splitlines()
    draft = yaml.safe_load((target_dir(root, "new") / "manifest.draft.yaml").read_text("utf-8"))
    assert draft["maintainer_notes"] == "maintainer_notes.md"

    notes.write_text("## Known traps\n\nThe staging shop has no stock.\n", encoding="utf-8")
    again = invoke("discover", "--root", str(root), "--target", "new", "--scan", str(TOY_SOURCE))
    assert again.exit_code == 0, again.output
    assert notes.read_text(encoding="utf-8") == "## Known traps\n\nThe staging shop has no stock.\n"
    assert "maintainer_notes.md" not in again.stdout


# --- what validate says ---


def errors_of(result: Any) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.startswith("error:")]


def test_validate_errors_on_a_pointer_naming_no_file(tmp_path: Path) -> None:
    assert invoke("init", "--root", str(tmp_path)).exit_code == 0
    edit_manifest(tmp_path, "default", maintainer_notes="notes/nowhere.md")

    result = invoke("validate", "--root", str(tmp_path))

    assert result.exit_code == 3, result.output
    (line,) = errors_of(result)
    assert line.endswith(
        ": maintainer_notes: the Maintainer notes notes/nowhere.md does not exist under "
        f"{target_dir(tmp_path)}"
    )


def test_validate_errors_on_a_pointer_leaving_the_workspace_root(tmp_path: Path) -> None:
    assert invoke("init", "--root", str(tmp_path)).exit_code == 0
    outside = tmp_path.parent / "maintainer_notes.md"
    edit_manifest(tmp_path, "default", maintainer_notes="../../../../maintainer_notes.md")

    result = invoke("validate", "--root", str(tmp_path))

    assert result.exit_code == 3, result.output
    (line,) = errors_of(result)
    assert line.endswith(
        ": maintainer_notes: the Maintainer notes ../../../../maintainer_notes.md leaves the "
        "Workspace root (it normalises to ../maintainer_notes.md); move the file under the "
        "root and point at it relative to the Target directory"
    )
    assert "does not exist" not in result.stdout
    assert not outside.exists()


def test_a_manifest_without_the_key_validates_with_no_extra_warning(tmp_path: Path) -> None:
    assert invoke("init", "--root", str(tmp_path)).exit_code == 0
    named = invoke("validate", "--root", str(tmp_path))
    edit_manifest(tmp_path, "default", maintainer_notes=None)
    (target_dir(tmp_path) / "maintainer_notes.md").unlink()
    # The page's Targets table lists the pointer, so it is regenerated before the check.
    assert invoke("registry", "--root", str(tmp_path), "--write").exit_code == 0

    unnamed = invoke("validate", "--root", str(tmp_path))

    assert named.exit_code == unnamed.exit_code == 0, unnamed.output
    assert "maintainer" not in unnamed.stdout
    assert named.stdout.splitlines()[-1] == unnamed.stdout.splitlines()[-1]
    assert "0 errors" in unnamed.stdout.splitlines()[-1]


# --- target show, the Registry and the Orientation page ---


def notes_row(root: Path) -> str:
    """The `target show` line directly after the `notes` row."""
    result = invoke("target", "show", "--root", str(root))
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    at = next(index for index, line in enumerate(lines) if line.startswith("notes "))
    return lines[at + 1]


def test_target_show_says_an_unedited_starter_is_not_written_yet(tmp_path: Path) -> None:
    """The starter's headings and placeholders are not notes: counting them read as content
    (amended decision 16)."""
    assert invoke("init", "--root", str(tmp_path)).exit_code == 0

    assert notes_row(tmp_path) == "maintainer notes  maintainer_notes.md, not written yet"


def test_target_show_counts_the_words_once_the_notes_differ_from_the_starter(
    tmp_path: Path,
) -> None:
    assert invoke("init", "--root", str(tmp_path)).exit_code == 0
    notes = target_dir(tmp_path) / "maintainer_notes.md"
    notes.write_text(MAINTAINER_NOTES_STARTER + "\nOne more sentence of five words.\n", "utf-8")

    words = words_of(notes.read_text(encoding="utf-8"))
    assert notes_row(tmp_path) == f"maintainer notes  maintainer_notes.md, {words} words"
    assert words == words_of(MAINTAINER_NOTES_STARTER) + 6


def test_target_show_never_reads_a_pointer_that_leaves_the_root(tmp_path: Path) -> None:
    """ADR-0015 §2 in `target show` as in `validate`: the file outside is there and is never
    read or counted; the row says `none` and a `problem:` line says why."""
    root = tmp_path / "shop"
    assert invoke("init", "--root", str(root)).exit_code == 0
    (tmp_path / "outside.md").write_text("Three words here.\n", encoding="utf-8")
    edit_manifest(root, "default", maintainer_notes="../../../../outside.md")

    result = invoke("target", "show", "--root", str(root))

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert "maintainer notes  none" in lines
    assert (
        "problem: maintainer_notes: the Maintainer notes ../../../../outside.md leaves the "
        "Workspace root (it normalises to ../outside.md); move the file under the root and "
        "point at it relative to the Target directory"
    ) in lines
    assert "words" not in next(line for line in lines if line.startswith("maintainer notes"))


def test_target_show_prints_none_without_the_key(tmp_path: Path) -> None:
    assert invoke("init", "--root", str(tmp_path)).exit_code == 0
    edit_manifest(tmp_path, "default", maintainer_notes=None)

    result = invoke("target", "show", "--root", str(tmp_path))

    assert result.exit_code == 0, result.output
    assert "maintainer notes  none" in result.stdout.splitlines()


def test_target_show_names_a_missing_notes_file_as_a_problem(tmp_path: Path) -> None:
    assert invoke("init", "--root", str(tmp_path)).exit_code == 0
    (target_dir(tmp_path) / "maintainer_notes.md").unlink()

    result = invoke("target", "show", "--root", str(tmp_path))

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert "maintainer notes  none" in lines
    assert (
        "problem: maintainer_notes: the Maintainer notes maintainer_notes.md does not exist "
        f"under {target_dir(tmp_path)}"
    ) in lines


def test_the_registry_entry_carries_the_pointer(tmp_path: Path) -> None:
    assert invoke("init", "--root", str(tmp_path), "--target", "a").exit_code == 0
    assert invoke("init", "--root", str(tmp_path), "--target", "b").exit_code == 0
    edit_manifest(tmp_path, "b", maintainer_notes=None)

    result = invoke("registry", "--root", str(tmp_path), "--json")

    assert result.exit_code == 0, result.output
    entries = {entry["slug"]: entry for entry in json.loads(result.stdout)}
    assert entries["a"]["maintainer_notes"] == "maintainer_notes.md"
    assert entries["b"]["maintainer_notes"] is None
    terminal = invoke("registry", "--root", str(tmp_path))
    assert "maintainer" not in terminal.stdout


def test_the_orientation_page_lists_the_notes_in_the_targets_table(tmp_path: Path) -> None:
    assert invoke("init", "--root", str(tmp_path), "--target", "desk").exit_code == 0

    page = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    (row,) = [line for line in page.splitlines() if line.startswith("| `desk` |")]
    assert row.endswith("| maintainer_notes.md |")


# --- the Judge never reads them (decision 17) ---


def test_the_judge_never_reads_the_maintainer_notes(tmp_path: Path) -> None:
    """A replayed toy Run whose notes hold a sentinel: no file the Run writes holds it. Every
    Judge prompt is recorded whole in its Trial's `judgement.jsonl` (the rendered prompt as a
    blob, then the request), so a sentinel absent from those files reached no Judge."""
    assert invoke("init", "--root", str(tmp_path)).exit_code == 0
    notes = target_dir(tmp_path) / "maintainer_notes.md"
    notes.write_text(f"{MAINTAINER_NOTES_STARTER}\n{SENTINEL}\n", encoding="utf-8")

    ran = invoke(
        "run",
        "--root",
        str(tmp_path),
        "--scenario",
        TOY_SCENARIO,
        "--replay",
        str(TOY_RECORDING),
    )

    assert ran.exit_code == CANCEL_EXIT, ran.output
    (run,) = (target_dir(tmp_path) / "runs").iterdir()
    written = [path for path in run.rglob("*") if path.is_file()]
    assert run / "run.json" in written
    judgements = [path for path in written if path.name == "judgement.jsonl"]
    assert judgements
    held = "".join(path.read_text(encoding="utf-8") for path in judgements)
    assert "You are the Judge in agentdiag" in held, "the prompts are recorded whole"
    for path in written:
        assert SENTINEL.encode("utf-8") not in path.read_bytes(), path


def test_nothing_under_the_eval_package_names_the_key() -> None:
    named = [
        path.relative_to(REPO).as_posix()
        for path in EVAL_PACKAGE.rglob("*.py")
        if "maintainer_notes" in path.read_text(encoding="utf-8")
    ]
    assert named == []
