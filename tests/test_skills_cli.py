"""Seam 1: `agentdiag init --skills` and the packaged skills (tickets 11, 12; ADR-0014 §3).

The skills ship as package data under `agentdiag/skills/<name>/`; `init --skills` copies
each into `<root>/.claude/skills/agentdiag-<name>/`, leaves an unchanged one alone, and
refuses to overwrite one an author edited unless `--force`.
"""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.run.skills import packaged_skills

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


def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0
    return root


def test_init_skills_installs_every_packaged_skill(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    before = sorted((root / ".agentdiag").rglob("*"))

    result = runner.invoke(app, ["init", "--root", str(root), "--skills"])

    assert result.exit_code == 0, result.output
    for name in ("discover", "generate", "fix-cycle", "correction"):
        installed = root / ".claude" / "skills" / f"agentdiag-{name}" / "SKILL.md"
        packaged = files("agentdiag").joinpath("skills", name, "SKILL.md")
        assert installed.read_bytes() == packaged.read_bytes()
        assert f"/agentdiag-{name}" in result.stdout
    assert sorted((root / ".agentdiag").rglob("*")) == before


def test_init_skills_from_a_subdirectory_installs_under_the_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = workspace(tmp_path)
    inside = root / "src" / "agent"
    inside.mkdir(parents=True)
    monkeypatch.chdir(inside)

    result = runner.invoke(app, ["init", "--skills"])

    assert result.exit_code == 0, result.output
    assert (root / ".claude" / "skills" / "agentdiag-discover" / "SKILL.md").is_file()
    assert not (inside / ".claude").exists()


def test_init_skills_with_no_workspace_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init", "--skills"])

    assert result.exit_code == 3
    assert "agentdiag init" in result.output
    assert not (tmp_path / ".claude").exists()


def test_init_skills_is_idempotent(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    runner.invoke(app, ["init", "--root", str(root), "--skills"])

    again = runner.invoke(app, ["init", "--root", str(root), "--skills"])

    assert again.exit_code == 0, again.output
    assert "unchanged .claude/skills/agentdiag-discover/SKILL.md" in again.stdout
    assert "unchanged .claude/skills/agentdiag-generate/SKILL.md" in again.stdout


def test_init_skills_keeps_an_edited_skill_unless_forced(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    runner.invoke(app, ["init", "--root", str(root), "--skills"])
    installed = root / ".claude" / "skills" / "agentdiag-discover" / "SKILL.md"
    installed.write_text("our own procedure\n", encoding="utf-8")

    refused = runner.invoke(app, ["init", "--root", str(root), "--skills"])
    assert refused.exit_code == 3
    assert str(installed) in refused.output
    assert installed.read_text(encoding="utf-8") == "our own procedure\n"

    forced = runner.invoke(app, ["init", "--root", str(root), "--skills", "--force"])
    assert forced.exit_code == 0, forced.output
    assert installed.read_text(encoding="utf-8") != "our own procedure\n"
