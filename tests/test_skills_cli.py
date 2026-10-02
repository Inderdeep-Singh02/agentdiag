"""Seam 1: `agentdiag init --skills` and the packaged skills (tickets 11, 12, 48; ADR-0014 §3,
ADR-0016 §6).

The skills ship as package data under `agentdiag/skills/<name>/`; `init --skills` writes
each to the tracked copy `<root>/.agents/skills/agentdiag-<name>/`, which Codex and Gemini
CLI read, and links `<root>/.claude/skills/agentdiag-<name>` to it for Claude Code, or copies
it where the filesystem refuses the link. It leaves an unchanged skill alone, migrates a
0.1.1 install, and refuses to overwrite one an author edited unless `--force`; `validate`
warns when the two layouts disagree (0.1.2-interfaces decisions 19-21).
"""

from __future__ import annotations

import os
import re
import shutil
from importlib.resources import files
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.run.skills import on_disk, packaged_contents, packaged_skills

runner = CliRunner()


def frontmatter(text: str) -> dict[str, object]:
    assert text.startswith("---\n")
    data = yaml.safe_load(text.split("---\n", 2)[1])
    assert isinstance(data, dict)
    return data


def test_the_discovery_skill_is_package_data_and_user_invoked() -> None:
    skill = files("agentdiag").joinpath("skills", "discover", "SKILL.md")

    assert skill.is_file()
    head = frontmatter(skill.read_text(encoding="utf-8"))
    assert head["name"] == "agentdiag-discover"
    assert head["disable-model-invocation"] is True
    body = skill.read_text(encoding="utf-8")
    for mark in ("**code**", "**judgement**", "**human**", "## Done when", "--from-connector"):
        assert mark in body


def test_the_generation_skill_is_package_data_and_user_invoked() -> None:
    skill = files("agentdiag").joinpath("skills", "generate", "SKILL.md")

    assert skill.is_file()
    head = frontmatter(skill.read_text(encoding="utf-8"))
    assert head["name"] == "agentdiag-generate"
    assert head["disable-model-invocation"] is True
    body = skill.read_text(encoding="utf-8")
    for mark in (
        "**code**",
        "**judgement**",
        "## Done when",
        "agentdiag target show",
        "agentdiag generate",
        "--check",
        "trace:<run id>/<scenario>/<n>",
        "change:<change record id>",
        "hints",
        "stop_when",
    ):
        assert mark in body
    for name in (
        "must_not_say",
        "forbidden_phrases",
        "expect_tools_order",
        "forbid_tools",
        "expect_tools",
        "prompt_adherence",
        "goal",
    ):
        assert name in body


def test_the_fix_cycle_skill_is_package_data_and_user_invoked() -> None:
    """Phase-7 decision 18: trigger → evidence → Diagnosis → Change record → fix → push →
    Run → compare → close, the budgets as text, a protected push handed to a person."""
    skill = files("agentdiag").joinpath("skills", "fix-cycle", "SKILL.md")

    head = frontmatter(skill.read_text(encoding="utf-8"))
    assert head["name"] == "agentdiag-fix-cycle"
    assert head["disable-model-invocation"] is True
    body = skill.read_text(encoding="utf-8")
    for mark in (
        "**code**",
        "**judgement**",
        "**human**",
        "## Done when",
        "## Budgets",
        "Two evidence commands",
        "One read bundle",
        "Diff first",
        "agentdiag show",
        "agentdiag change open",
        "agentdiag change propose",
        "agentdiag change expect",
        "agentdiag push --env <env>",
        "--push --change <id>",
        "agentdiag pull",
        "agentdiag compare",
        "--expect fingerprint",
        "agentdiag change close",
        "typed by a person",
    ):
        assert mark in body, mark


def test_the_fix_cycle_verifies_on_the_environment_it_pushed_to() -> None:
    """Ticket 38: the pre-change and verifying Runs open the pushed environment with `run
    --env`; the workaround of pushing to the default environment is gone."""
    body = files("agentdiag").joinpath("skills", "fix-cycle", "SKILL.md").read_text("utf-8")
    steps = {
        line.split(".", 1)[0]: line for line in body.splitlines() if line.split(".", 1)[0].isdigit()
    }

    assert "agentdiag run --env <env> --scenario" in steps["6"]
    assert "agentdiag run --env <env> --scenario" in steps["11"]
    assert "agentdiag push --env <env>" in steps["9"]
    assert "converses with" not in body
    assert "adapter.environments.default" not in body


def test_the_fix_cycle_close_step_says_a_replayed_run_cannot_verify_a_prompt_change() -> None:
    """Ticket 40 (phase-8 decision 16): step 13 says why a replay decides nothing after a
    prompt change, which Run verifies instead, and that the gate refuses both results."""
    body = files("agentdiag").joinpath("skills", "fix-cycle", "SKILL.md").read_text("utf-8")
    step = next(line for line in body.splitlines() if line.startswith("13."))

    assert "A replayed Run cannot verify a prompt change" in step
    assert "the recording matches on the request body" in step
    assert "every judged Score is `invalid`" in step
    assert "live (through the login) or freshly recorded against the pushed prompt" in step
    assert "refuses a comparison that decides nothing" in step
    assert "decides nothing for a Scenario the expectation names" in step
    assert "for `--verified` and `--refuted` alike" in step
    assert "never close `--refuted` on one that does not" in step


def test_the_correction_skill_is_package_data_and_user_invoked() -> None:
    """Phase-7 decision 18: the wrong Score, the note, `sync`, a rescore of the same Run,
    `compare --expect judge`, a Suppression when the fail is date-bound."""
    skill = files("agentdiag").joinpath("skills", "correction", "SKILL.md")

    head = frontmatter(skill.read_text(encoding="utf-8"))
    assert head["name"] == "agentdiag-correction"
    assert head["disable-model-invocation"] is True
    body = skill.read_text(encoding="utf-8")
    for mark in (
        "**code**",
        "**judgement**",
        "**human**",
        "## Done when",
        "## Budgets",
        "Two evidence commands",
        "One read bundle",
        "Diff first",
        "judge_notes.md",
        "eval_parameters",
        "Suppression",
        "agentdiag sync",
        "agentdiag rescore <run>",
        "--expect judge",
    ):
        assert mark in body, mark


def test_every_eval_the_generation_skill_names_is_in_the_catalogue() -> None:
    """The skill's Eval table names real Evals, and leaves their parameters to the reference
    beside it, whose declarations `tests/test_generate_reference.py` validates (walkthrough
    friction 15: the table's own spellings were rejected)."""
    import re

    from agentdiag.eval.registry import REGISTRY

    body = files("agentdiag").joinpath("skills", "generate", "SKILL.md").read_text("utf-8")
    table = body[body.index("## Which Eval for which rule") : body.index("## Simulated users")]
    rows = [line for line in table.splitlines() if line.startswith("| ") and "---" not in line]
    named = {name for row in rows[1:] for name in re.findall(r"`([a-z_]+)`", row.split("|")[2])}
    named -= {"forbidden_phrases", "focus", "id"}  # a Manifest key and two Scenario keys

    assert named <= set(REGISTRY), sorted(named - set(REGISTRY))
    assert {"must_not_say", "must_say_any", "expect_tools_order", "prompt_adherence"} <= named
    assert "scenario-reference.md" in table
    assert "equals" not in table, "the operators are the reference's to list"


MAINTAINER_NOTES_FIRST = (
    "Before step 1, read the Target's Maintainer notes, `maintainer_notes.md` beside the "
    "Manifest, in full: what it does, who it serves, its environments, its traps, where its "
    "evidence lives; with none yet, write them as you learn these."
)


@pytest.mark.parametrize("name", sorted(packaged_skills()))
def test_every_skill_reads_the_maintainer_notes_before_its_step_1(name: str) -> None:
    """ADR-0016 §5, 0.1.2-interfaces decision 18: one sentence before step 1, in the Budgets
    block where there is one, and the step numbers unchanged."""
    body = files("agentdiag").joinpath("skills", name, "SKILL.md").read_text("utf-8")
    first_step = body.index("\n1. **")

    assert MAINTAINER_NOTES_FIRST in body[:first_step]
    assert body.index("maintainer_notes.md") < first_step
    if "## Budgets" in body:
        budgets = body[body.index("## Budgets") : body.index("## Steps")]
        assert MAINTAINER_NOTES_FIRST in budgets
    steps = body[body.index("## Steps") :]
    steps = steps[: steps.find("\n## ", 1)] if "\n## " in steps[1:] else steps
    numbers = [int(match) for match in re.findall(r"^(\d+)\. \*\*", steps, re.MULTILINE)]
    assert numbers and numbers == list(range(1, len(numbers) + 1)), numbers


def test_discover_writes_the_notes_first_headings_and_generate_names_the_pending_refusal() -> None:
    discover = files("agentdiag").joinpath("skills", "discover", "SKILL.md").read_text("utf-8")
    identity = next(line for line in discover.splitlines() if "**the Target's identity**" in line)
    assert "`## What the Target does`, `## Who it serves`" in identity
    generate = files("agentdiag").joinpath("skills", "generate", "SKILL.md").read_text("utf-8")
    step_9 = next(line for line in generate.splitlines() if line.startswith("9. "))
    assert "still `pending` the dry run refuses by name" in step_9


SKILLS = sorted(f"agentdiag-{name}" for name in packaged_skills())
TRACKED = Path(".agents") / "skills"
CLAUDE = Path(".claude") / "skills"
REFUSED = (
    "this filesystem refused a symlink; agentdiag validate warns when the copy differs from "
    "the tracked copy"
)


def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0
    return root


def install(root: Path, *arguments: str) -> object:
    return runner.invoke(app, ["init", "--root", str(root), "--skills", *arguments])


def link_text(name: str) -> str:
    return f"../../.agents/skills/{name}"


packaged = packaged_contents


def validate(root: Path) -> list[str]:
    result = runner.invoke(app, ["validate", "--root", str(root)])
    assert result.exit_code == 0, result.output
    return result.stdout.splitlines()


def refuse_symlinks(monkeypatch: pytest.MonkeyPatch, error: type[Exception] = OSError) -> None:
    def refused(*_: object, **__: object) -> None:
        raise error("symbolic links are not supported here")

    monkeypatch.setattr(os, "symlink", refused)


def an_0_1_1_install(root: Path) -> None:
    """What 0.1.1's `init --skills` left: a real directory per skill under `.claude/skills/`,
    equal to the package's, and nothing under `.agents/`."""
    for name in SKILLS:
        for path, data in packaged(name).items():
            target = root / CLAUDE / name / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def test_init_skills_writes_the_tracked_copy_and_links_claude_code_to_it(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    before = sorted((root / ".agentdiag").rglob("*"))

    result = install(root)

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == f"Installed the agentdiag skills under {root / TRACKED} (the tracked copy):"
    for name in SKILLS:
        assert on_disk(root / TRACKED / name) == packaged(name), name
        entry = root / CLAUDE / name
        assert entry.is_symlink(), name
        assert Path(os.readlink(entry)).as_posix() == link_text(name)
        assert on_disk(entry) == packaged(name), "Claude Code reads the tracked copy through it"
        assert f"    linked {CLAUDE.as_posix()}/{name} -> {link_text(name)}" in lines
        assert f"/{name}" in lines[-1]
    for path in packaged("agentdiag-generate"):
        assert f"  wrote .agents/skills/agentdiag-generate/{path.as_posix()}" in lines
    assert "Where each Harness finds them:" in lines
    assert "  Codex and Gemini CLI read .agents/skills/ directly." in lines
    assert "  Claude Code reads .claude/skills/:" in lines
    assert (
        lines[-1]
        == "Invoke one by name (in Claude Code: " + ", ".join(f"/{name}" for name in SKILLS) + ")"
    )
    for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        assert (root / name).is_file(), name
    assert sorted((root / ".agentdiag").rglob("*")) == before


def test_a_second_init_skills_prints_every_entry_unchanged(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    assert install(root).exit_code == 0

    again = install(root)

    assert again.exit_code == 0, again.output
    lines = again.stdout.splitlines()
    for name in SKILLS:
        assert f"    unchanged {CLAUDE.as_posix()}/{name} -> {link_text(name)}" in lines
    assert "  unchanged .agents/skills/agentdiag-discover/SKILL.md" in lines
    assert "  unchanged .agents/skills/agentdiag-generate/scenario-reference.md" in lines
    assert not [line for line in lines if line.lstrip().startswith(("wrote", "linked", "copied"))]


def test_init_skills_from_a_subdirectory_installs_under_the_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = workspace(tmp_path)
    inside = root / "src" / "agent"
    inside.mkdir(parents=True)
    monkeypatch.chdir(inside)

    result = runner.invoke(app, ["init", "--skills"])

    assert result.exit_code == 0, result.output
    assert (root / TRACKED / "agentdiag-discover" / "SKILL.md").is_file()
    assert (root / CLAUDE / "agentdiag-discover").is_symlink()
    assert not (inside / ".claude").exists()
    assert not (inside / ".agents").exists()


def test_init_skills_with_no_workspace_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init", "--skills"])

    assert result.exit_code == 3
    assert "agentdiag init" in result.output
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".agents").exists()


def test_init_skills_keeps_an_edited_tracked_skill_unless_forced(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    assert install(root).exit_code == 0
    edited = root / TRACKED / "agentdiag-discover" / "SKILL.md"
    edited.write_text("our own procedure\n", encoding="utf-8")

    refused = install(root)
    assert refused.exit_code == 3
    assert str(edited) in refused.output
    assert edited.read_text(encoding="utf-8") == "our own procedure\n"

    forced = install(root, "--force")
    assert forced.exit_code == 0, forced.output
    assert on_disk(edited.parent) == packaged("agentdiag-discover")


@pytest.mark.parametrize("error", [OSError, NotImplementedError])
def test_where_a_symlink_is_refused_claude_code_gets_a_copy_validate_watches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    root = workspace(tmp_path)
    refuse_symlinks(monkeypatch, error)

    result = install(root)

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    for name in SKILLS:
        entry = root / CLAUDE / name
        assert entry.is_dir() and not entry.is_symlink(), name
        assert on_disk(entry) == on_disk(root / TRACKED / name) == packaged(name)
        assert f"    copied {CLAUDE.as_posix()}/{name} ({REFUSED})" in lines
    assert not [line for line in validate(root) if ".claude/skills" in line]

    again = install(root)
    assert again.exit_code == 0, again.output
    for name in SKILLS:
        assert f"    unchanged {CLAUDE.as_posix()}/{name} (a copy; {REFUSED})" in again.stdout

    (root / CLAUDE / "agentdiag-generate" / "SKILL.md").write_text("tuned\n", encoding="utf-8")
    assert (
        "warning: .claude/skills/agentdiag-generate: differs from "
        ".agents/skills/agentdiag-generate; the copy was edited; move the edit into "
        ".agents/skills/agentdiag-generate, or agentdiag init --skills --force restores it"
    ) in validate(root)


def test_on_the_fallback_an_edit_to_the_tracked_copy_is_carried_over_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Tracked copy is the edit's home; `--force` would reset it to the package's, so it
    is not the remedy `validate` names (0.1.2-interfaces decision 21 as amended)."""
    root = workspace(tmp_path)
    refuse_symlinks(monkeypatch)
    assert install(root).exit_code == 0
    (root / TRACKED / "agentdiag-discover" / "SKILL.md").write_text("ours\n", encoding="utf-8")

    warned = [line for line in validate(root) if "agentdiag-discover" in line]

    assert warned == [
        "warning: .claude/skills/agentdiag-discover: differs from "
        ".agents/skills/agentdiag-discover, whose edit this copy lacks (this filesystem refuses "
        "symlinks); copy .agents/skills/agentdiag-discover over it"
    ]
    assert "--force" not in warned[0]

    (root / CLAUDE / "agentdiag-discover" / "SKILL.md").write_text("theirs\n", encoding="utf-8")
    assert [line for line in validate(root) if "agentdiag-discover" in line] == [
        "warning: .claude/skills/agentdiag-discover: differs from "
        ".agents/skills/agentdiag-discover; both the copy and .agents/skills/agentdiag-discover "
        "were edited; reconcile them by hand into .agents/skills/agentdiag-discover"
    ]
    shutil.copytree(
        root / TRACKED / "agentdiag-discover",
        root / CLAUDE / "agentdiag-discover",
        dirs_exist_ok=True,
    )
    assert not [line for line in validate(root) if "agentdiag-discover" in line]


def test_a_0_1_1_install_becomes_the_link_layout_without_force(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    an_0_1_1_install(root)

    result = install(root)

    assert result.exit_code == 0, result.output
    for name in SKILLS:
        entry = root / CLAUDE / name
        assert entry.is_symlink(), name
        assert Path(os.readlink(entry)).as_posix() == link_text(name)
        assert on_disk(root / TRACKED / name) == packaged(name)
        assert (
            f"    linked {CLAUDE.as_posix()}/{name} -> {link_text(name)} (replaced what was there)"
        ) in result.stdout.splitlines()
    assert not [line for line in validate(root) if "skills/" in line]


def test_an_edited_0_1_1_install_is_refused_by_name_and_replaced_with_force(
    tmp_path: Path,
) -> None:
    root = workspace(tmp_path)
    an_0_1_1_install(root)
    edited = root / CLAUDE / "agentdiag-fix-cycle" / "SKILL.md"
    edited.write_text("our fix cycle\n", encoding="utf-8")

    refused = install(root)

    assert refused.exit_code == 3
    assert str(edited) in refused.output
    assert (
        "differ from this agentdiag's packaged skills (edited, or installed by an earlier version)"
    ) in " ".join(refused.output.split())
    assert "agentdiag init --skills --force replaces them" in " ".join(refused.output.split())
    assert not (root / ".agents").exists(), "nothing is written before the refusal"
    assert edited.read_text(encoding="utf-8") == "our fix cycle\n"

    forced = install(root, "--force")

    assert forced.exit_code == 0, forced.output
    entry = root / CLAUDE / "agentdiag-fix-cycle"
    assert entry.is_symlink()
    assert on_disk(entry) == packaged("agentdiag-fix-cycle")


def test_a_plain_file_where_the_link_belongs_is_refused_unless_forced(tmp_path: Path) -> None:
    """A Windows checkout without `core.symlinks` turns a committed symlink into a text file
    holding the link's target."""
    root = workspace(tmp_path)
    assert install(root).exit_code == 0
    entry = root / CLAUDE / "agentdiag-correction"
    entry.unlink()
    entry.write_text(link_text("agentdiag-correction"), encoding="utf-8")
    assert (
        "warning: .claude/skills/agentdiag-correction: a file, not a link or a copy (a checkout "
        "without core.symlinks); Claude Code finds no such skill; agentdiag init --skills "
        "--force replaces it"
    ) in validate(root)

    refused = install(root)
    assert refused.exit_code == 3
    assert str(entry) in refused.output
    assert entry.is_file() and not entry.is_symlink()

    forced = install(root, "--force")
    assert forced.exit_code == 0, forced.output
    assert entry.is_symlink()
    assert Path(os.readlink(entry)).as_posix() == link_text("agentdiag-correction")


def test_a_link_to_somewhere_else_is_refused_unless_forced(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    assert install(root).exit_code == 0
    elsewhere = tmp_path / "our-skills" / "discover"
    elsewhere.mkdir(parents=True)
    (elsewhere / "SKILL.md").write_text("ours\n", encoding="utf-8")
    entry = root / CLAUDE / "agentdiag-discover"
    entry.unlink()
    os.symlink(elsewhere, entry, target_is_directory=True)

    assert (
        f"warning: .claude/skills/agentdiag-discover: a link to {elsewhere}, not to the tracked "
        "copy; Claude Code reads another skill under this name; agentdiag init --skills "
        "--force links it"
    ) in validate(root)

    refused = install(root)
    assert refused.exit_code == 3
    assert str(entry) in refused.output
    assert entry.resolve() == elsewhere.resolve()

    forced = install(root, "--force")
    assert forced.exit_code == 0, forced.output
    assert Path(os.readlink(entry)).as_posix() == link_text("agentdiag-discover")
    assert (elsewhere / "SKILL.md").read_text(encoding="utf-8") == "ours\n", "only the link went"


def test_validate_warns_of_a_skill_one_layout_holds_and_the_other_lacks(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    assert install(root).exit_code == 0
    quiet = validate(root)
    assert not [line for line in quiet if "skills/" in line]

    (root / CLAUDE / "agentdiag-discover").unlink()
    no_link = validate(root)
    assert (
        "warning: .claude/skills/agentdiag-discover: absent; Claude Code finds no such skill; "
        "agentdiag init --skills links it"
    ) in no_link
    assert no_link[-1] != quiet[-1], "the warning is counted in the summary line"

    assert install(root).exit_code == 0
    shutil.rmtree(root / TRACKED / "agentdiag-generate")
    (root / CLAUDE / "agentdiag-generate").unlink()
    shutil.copytree(
        root / TRACKED / "agentdiag-discover",
        root / CLAUDE / "agentdiag-generate",
    )
    assert (
        "warning: .agents/skills/agentdiag-generate: absent; Codex and Gemini CLI find no such "
        "skill; agentdiag init --skills writes the tracked copy"
    ) in validate(root)


def test_a_dangling_link_is_the_tracked_copy_absent(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    assert install(root).exit_code == 0
    shutil.rmtree(root / TRACKED / "agentdiag-correction")

    assert (
        "warning: .agents/skills/agentdiag-correction: absent; Codex and Gemini CLI find no "
        "such skill, and Claude Code's link dangles; agentdiag init --skills writes the "
        "tracked copy"
    ) in validate(root)
    assert install(root).exit_code == 0
    assert on_disk(root / CLAUDE / "agentdiag-correction") == packaged("agentdiag-correction")


def test_a_workspace_with_skills_of_its_own_keeps_them(tmp_path: Path) -> None:
    """The links are per skill (ADR-0016 §6), so a repository's own skills beside them are
    never touched, and `validate` never speaks of them."""
    root = workspace(tmp_path)
    ours = root / CLAUDE / "release-notes" / "SKILL.md"
    ours.parent.mkdir(parents=True)
    ours.write_text("ours\n", encoding="utf-8")

    assert install(root).exit_code == 0

    assert ours.read_text(encoding="utf-8") == "ours\n"
    assert not [line for line in validate(root) if "release-notes" in line]
