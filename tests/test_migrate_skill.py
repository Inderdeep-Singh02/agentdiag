"""Seam 1: the `agentdiag-migrate` skill's code steps, run over an invented source repository
(ticket 49, ADR-0016 §7, 0.1.2-interfaces decisions 23-24 as amended after the ticket 49
reviews).

`tests/fixtures/migrate/source/` is a maintenance repository of one Target kept somewhere
else: a public library's chat assistant, invented, with a prompt of headings, two tool
schemas, tests in a format of its own, maintainers' notes and a fix history. The skill asks
an agent for judgement at its steps 1, 4, 5, 8 and 9; that judgement is written down in
`tests/migrate_fixtures.py` (the identity, the Manifest edits) and under
`tests/fixtures/migrate/judgement/` (the drafts and the two notes files), and this module
runs every code step through the CLI into a temporary Workspace, in the skill's order. What
it proves is what the skill's "Done when" claims: `validate` exits 0 with only the pending
Adapter and `2 lines marked REVIEW` left, each of the source's tests has a Scenario under
`prompt:` provenance, the Orientation page lists the Target, `run --dry-run` refuses it by
name, and nothing of the source's history reached `changes/` (a Change record closes only
through `compare`, ADR-0012 §4, §6), nor any credential the Target directory.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from tests.fakes.workspace import PENDING_LINE, target_dir, warnings_of
from tests.migrate_fixtures import (
    IDENTITY,
    JUDGEMENT,
    SLUG,
    SOURCE,
    SUITE,
    retire_sample,
    settle_manifest,
)

NAME = dict(IDENTITY)["--name"]
FAMILY = dict(IDENTITY)["--family"]
CHANNEL = dict(IDENTITY)["--channel"]
TWO_REVIEW_LINES = "2 lines marked REVIEW; settle each (accept or rewrite it) before a Run"

CREDENTIAL = re.compile(
    r"sk-[A-Za-z0-9_-]{8,}"
    r"|(?i:api[_-]?key|token|secret|password|passwd)\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+]{8,}"
    r"|(?i:bearer)\s+[A-Za-z0-9_\-.]{12,}"
    r"|://[^/\s:@]+:[^/\s@]+@"
)
"""What a credential value looks like in a file: a key, a `name: value` pair for a secret, a
bearer token, a URL carrying a password."""

runner = CliRunner()


def invoke(*arguments: str | Path) -> Any:
    return runner.invoke(app, [str(argument) for argument in arguments])


@dataclass
class Migrated:
    """The Workspace after the skill's code steps, with what each command printed."""

    root: Path
    printed: dict[str, Any] = field(default_factory=dict)

    @property
    def target(self) -> Path:
        return target_dir(self.root, SLUG)


@pytest.fixture(scope="module")
def migrated(tmp_path_factory: pytest.TempPathFactory) -> Migrated:
    """Steps 2, 3, 5, 7, 10 and 11 through the CLI, the judgement steps from
    `tests/migrate_fixtures.py` and the judgement files, in the skill's order; the commit
    (step 12) is the person's."""
    done = Migrated(tmp_path_factory.mktemp("migrate") / "workspace")
    root, target = done.root, done.target
    at = ("--root", root, "--target", SLUG)

    done.printed["init"] = invoke("init", *at, *[word for pair in IDENTITY for word in pair])
    assert done.printed["init"].exit_code == 0, done.printed["init"].output

    for kind in ("prompts", "tools"):
        shutil.copytree(SOURCE / kind, target / kind)
    settle_manifest(target / "manifest.yaml")

    drafts = target / "drafts" / f"{SUITE}.yaml"
    drafts.parent.mkdir()
    shutil.copy(JUDGEMENT / f"{SUITE}.yaml", drafts)
    retire_sample(target / "manifest.yaml")
    done.printed["check"] = invoke("generate", *at, "--from", drafts, "--check")
    done.printed["generate"] = invoke("generate", *at, "--from", drafts)

    for notes in ("judge_notes.md", "maintainer_notes.md"):
        shutil.copy(JUDGEMENT / notes, target / notes)

    done.printed["validate"] = invoke("validate", *at)
    done.printed["registry"] = invoke("registry", "--root", root, "--write")
    done.printed["dry-run"] = invoke("run", *at, "--dry-run")
    return done


def test_init_writes_the_identity_and_four_review_lines(migrated: Migrated) -> None:
    printed = migrated.printed["init"].stdout

    assert f"Target {SLUG} ({NAME}), family {FAMILY}, channel {CHANNEL}" in printed
    assert "settle the 4 REVIEW lines" in printed


def test_the_prompts_and_tools_are_copied_and_pointed_at(migrated: Migrated) -> None:
    """Step 3: the copies are the source's bytes, the Manifest points at them, and the
    REVIEW lines left are the Adapter kind's and the Connector's."""
    for source in [*SOURCE.joinpath("prompts").rglob("*"), *SOURCE.joinpath("tools").rglob("*")]:
        copied = migrated.target / source.relative_to(SOURCE)
        assert copied.read_bytes() == source.read_bytes(), copied
    text = (migrated.target / "manifest.yaml").read_text(encoding="utf-8")
    manifest = yaml.safe_load(text)
    assert manifest["prompts"] == {"system": "prompts/system.md"}
    assert manifest["tools"] == {
        "find_book": {"kind": "retrieval", "schema": "tools/find_book.json"},
        "renew_loan": {"kind": "action", "schema": "tools/renew_loan.json"},
    }
    assert manifest["adapter"]["environments"]["prod"] == {"protected": True}
    review = [line.strip() for line in text.splitlines() if "REVIEW:" in line]
    assert len(review) == 2, review
    assert review[0].startswith("# REVIEW: nothing drives this Target yet")
    assert review[1].startswith("# REVIEW: no Connector yet")


def test_each_of_the_source_tests_is_a_scenario_under_prompt_provenance(
    migrated: Migrated,
) -> None:
    """Steps 5 and 7: one Scenario per entry of the source's own `tests/cases.json`, with
    its user messages as literal Turns and its id in `notes`; `generate` checks the drafts'
    sections against the prompt file and writes them with no warning, under a
    `# from prompt:system#rules` line, beside the retired sample Suite."""
    for name in ("check", "generate"):
        result = migrated.printed[name]
        assert result.exit_code == 0, result.output
        assert "warning" not in result.output, result.output
    entries = json.loads((SOURCE / "tests" / "cases.json").read_text(encoding="utf-8"))["cases"]
    text = (migrated.target / "suites" / f"{SUITE}.yaml").read_text(encoding="utf-8")
    scenarios = yaml.safe_load(text)["scenarios"]

    assert len(scenarios) == len(entries) == 4
    assert text.count("# from prompt:system#rules") == len(entries)
    for entry in entries:
        (scenario,) = [s for s in scenarios if entry["id"] in s["notes"]]
        assert scenario["provenance"] == "prompt:system#rules", entry["id"]
        assert scenario["turns"] == entry["messages"], entry["id"]
    manifest = yaml.safe_load((migrated.target / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["suites"] == [
        {"path": "suites/sample.yaml", "status": "retired"},
        f"suites/{SUITE}.yaml",
    ]


def test_the_notes_are_filled_under_600_words_and_hold_no_credential(
    migrated: Migrated,
) -> None:
    """Steps 8 and 9: the Judging section of the source's NOTES.md went to `judge_notes.md`,
    with the Judge model checked against this Workspace's; the rest went under the five
    headings of `maintainer_notes.md`; no credential anywhere under the Target directory."""
    judge = (migrated.target / "judge_notes.md").read_text(encoding="utf-8")
    maintainer = (migrated.target / "maintainer_notes.md").read_text(encoding="utf-8")

    for text in (judge, maintainer):
        assert 0 < len(text.split()) < 600
    assert "rule-1 breach" in judge
    assert "Judge defaults to claude-opus-5" in judge
    run_help = runner.invoke(app, ["run", "--help"], terminal_width=200).output
    assert "[default: claude-opus-5]" in run_help, "the notes state the default Judge model"
    for heading in (
        "## What the Target does",
        "## Who it serves",
        "## Environments",
        "## Known traps",
        "## Where evidence lives",
    ):
        assert heading in maintainer, heading
    assert "chat_log" in maintainer and "chat_log" not in judge
    for path in migrated.target.rglob("*"):
        if path.is_file():
            found = CREDENTIAL.search(path.read_text(encoding="utf-8"))
            assert found is None, f"{path}: {found.group(0) if found else ''}"


def test_validate_leaves_the_pending_adapter_and_the_two_review_lines(
    migrated: Migrated,
) -> None:
    """Step 10: 0 errors, and the warnings are the pending Adapter and the REVIEW count,
    nothing else: the table is fresh (the sample was retired before `generate` refreshed
    it), and the generated Suite is runnable, so `no runnable Suite` does not fire."""
    result = migrated.printed["validate"]
    manifest = (migrated.target / "manifest.yaml").as_posix()

    assert result.exit_code == 0, result.output
    assert warnings_of(result) == [
        f"warning: {manifest}: {PENDING_LINE}",
        f"warning: {manifest}: {TWO_REVIEW_LINES}",
    ]
    summary = result.stdout.splitlines()[-1]
    assert summary == "validated the Manifest and 1 Suite: 0 errors, 2 warnings"


def test_the_page_lists_the_target_and_the_dry_run_refuses_it_by_name(
    migrated: Migrated,
) -> None:
    """Step 11: `registry --write` finds the table `generate` refreshed already current, and
    it has the Target's row, its Maintainer notes named; `run --dry-run` exits 3 naming the
    pending Adapter and nothing else."""
    registry = migrated.printed["registry"]
    assert registry.exit_code == 0, registry.output
    assert "unchanged AGENTS.md" in registry.stdout.splitlines()
    page = (migrated.root / "AGENTS.md").read_text(encoding="utf-8")
    assert (
        f"| `{SLUG}` | {NAME} | {FAMILY} / {CHANNEL} | dev | prod | sample (retired), {SUITE} "
        "| - | maintainer_notes.md |"
    ) in page
    refused = migrated.printed["dry-run"]
    assert refused.exit_code == 3, refused.output
    assert refused.output.splitlines() == [PENDING_LINE]


def test_nothing_of_the_source_history_is_imported(migrated: Migrated) -> None:
    """What stays in the source repository: its fix history. No Change record was written,
    and no sentence of HISTORY.md is anywhere in the Workspace (ADR-0012 §4, §6)."""
    history = (SOURCE / "HISTORY.md").read_text(encoding="utf-8")
    sentences = [
        part.strip()
        for line in history.splitlines()
        if line and not line.startswith("#")
        for part in re.split(r"(?<=\.)\s", line)
        if len(part.strip()) > 12
    ]
    assert sentences

    assert not (migrated.target / "changes").exists()
    for path in migrated.root.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            for sentence in sentences:
                assert sentence not in text, f"{path}: {sentence}"
            for record in ("FB-07", "FB-09"):
                assert record not in text, path
