"""Seam 1: `agentdiag generate` over a temporary Workspace built from `init` (ticket 12).

A coding agent writes drafts, one Scenario per prompt rule and per tool, each with the
`provenance` it answers to; `generate` checks them, derives their ids, and writes the Suite
with a `# from <provenance>` line above each Scenario (phase-6 decision 29). On a Suite that
exists it keeps every id it can match, retires what no draft matches, and refuses to re-key
an id a Run holds (decision 30, ADR-0002 §8). Every test reads what the command wrote or
printed; the unit tests at the end pin the id derivation and the matching rule.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.generate.ids import (
    Drafted,
    Existing,
    derive_id,
    match_drafts,
    parse_provenance,
)
from agentdiag.scenario.models import Suite

REPO = Path(__file__).resolve().parents[1]
TOY_DRAFTS = REPO / "tests" / "fixtures" / "drafts" / "toy-order-desk.yaml"
BASE_RUN = REPO / "tests" / "fixtures" / "runs" / "20260923T100000Z-base"

runner = CliRunner()


def invoke(*arguments: str | Path) -> Any:
    return runner.invoke(app, [str(argument) for argument in arguments])


def workspace(tmp_path: Path) -> Path:
    """A Workspace from `init`: the Target `default`, the toy's scaffold."""
    root = tmp_path / "shop"
    result = invoke("init", "--root", root)
    assert result.exit_code == 0, result.output
    return root


def target_dir(root: Path) -> Path:
    return root / ".agentdiag" / "targets" / "default"


def suite_file(root: Path, name: str = "generated") -> Path:
    return target_dir(root) / "suites" / f"{name}.yaml"


def write_drafts(directory: Path, scenarios: list[dict[str, Any]], **top: Any) -> Path:
    path = directory / "drafts.yaml"
    path.write_text(
        yaml.safe_dump({"target": "toy-order-desk", **top, "scenarios": scenarios}),
        encoding="utf-8",
    )
    return path


def draft(provenance: str, title: str, *evals: Any, **extra: Any) -> dict[str, Any]:
    return {
        "provenance": provenance,
        "title": title,
        "turns": ["Where is order NB-1042?"],
        "evals": list(evals) or [{"forbid_tools": ["cancel_order"]}],
        **extra,
    }


def written(root: Path, name: str = "generated") -> Suite:
    return Suite.model_validate(yaml.safe_load(suite_file(root, name).read_text("utf-8")))


def ids_of(suite: Suite) -> list[str]:
    return [scenario.id for scenario in suite.scenarios]


# --- a first generation ---


def test_generate_writes_the_suite_with_provenance_comments_tags_and_ids(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    drafts = write_drafts(
        tmp_path,
        [
            draft(
                "tool:cancel_order",
                "Refuse to cancel a delivered order",
                {"forbid_tools": ["cancel_order"]},
                {"expect_tools": {"tools": ["lookup_order"], "turn": 1}},
                tags=["orders"],
            ),
            draft("prompt:system", "A status question is not a cancellation"),
        ],
    )

    result = invoke("generate", "--root", root, "--from", drafts)

    assert result.exit_code == 0, result.output
    suite = written(root)
    assert ids_of(suite) == [
        "cancel-order-refuse-to-cancel-a-delivered-order",
        "system-a-status-question-is-not-a-cancellation",
    ]
    first, second = suite.scenarios
    assert first.provenance == "tool:cancel_order"
    assert first.tags == ["orders", "cancel_order", "lookup_order"]
    assert second.tags == ["cancel_order"]
    text = suite_file(root).read_text(encoding="utf-8")
    lines = text.splitlines()
    for scenario in suite.scenarios:
        opening = lines.index(f"  - id: {scenario.id}")
        assert lines[opening - 1] == f"  # from {scenario.provenance}"
    assert "new" in result.stdout
    assert "cancel-order-refuse-to-cancel-a-delivered-order" in result.stdout


def test_generate_adds_the_suite_to_the_manifest_keeping_every_comment(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    manifest = target_dir(root) / "manifest.yaml"
    before = manifest.read_text(encoding="utf-8").splitlines()
    drafts = write_drafts(tmp_path, [draft("prompt:system", "Look it up first")])

    result = invoke("generate", "--root", root, "--from", drafts)

    assert result.exit_code == 0, result.output
    after = manifest.read_text(encoding="utf-8").splitlines()
    assert [line for line in after if line not in before] == ["  - suites/generated.yaml"]
    assert [line for line in before if line not in after] == []
    loaded = yaml.safe_load("\n".join(after))
    assert "suites/generated.yaml" in loaded["suites"]
    assert "Added suites/generated.yaml to the Manifest" in result.stdout

    again = invoke("generate", "--root", root, "--from", drafts)
    assert again.exit_code == 0, again.output
    assert manifest.read_text(encoding="utf-8").splitlines() == after


def test_generate_writes_the_suite_the_drafts_or_the_flag_name(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    drafts = write_drafts(tmp_path, [draft("prompt:system", "Look it up first")], suite="rules")

    assert invoke("generate", "--root", root, "--from", drafts).exit_code == 0
    assert suite_file(root, "rules").is_file()
    assert invoke("generate", "--root", root, "--from", drafts, "--suite", "other").exit_code == 0
    assert suite_file(root, "other").is_file()
    assert not suite_file(root).exists()


def test_the_generated_suite_validates(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    drafts = write_drafts(tmp_path, [draft("prompt:system", "Look it up first")])
    assert invoke("generate", "--root", root, "--from", drafts).exit_code == 0

    result = invoke("validate", "--root", root)

    assert result.exit_code == 0, result.output
    assert "0 errors" in result.stdout


def test_generate_check_prints_the_ids_and_writes_nothing(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    manifest = target_dir(root) / "manifest.yaml"
    before = manifest.read_bytes()
    drafts = write_drafts(tmp_path, [draft("prompt:system", "Look it up first")])

    result = invoke("generate", "--root", root, "--from", drafts, "--check")

    assert result.exit_code == 0, result.output
    assert "system-look-it-up-first" in result.stdout
    assert not suite_file(root).exists()
    assert manifest.read_bytes() == before


# --- what is refused, with nothing written ---


def test_a_draft_with_no_eval_is_refused_naming_it(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    bare = draft("prompt:system", "Nothing judges this")
    bare["evals"] = []
    drafts = write_drafts(tmp_path, [draft("prompt:system", "Look it up first"), bare])

    result = invoke("generate", "--root", root, "--from", drafts)

    assert result.exit_code == 3
    assert "scenarios[1]" in result.output
    assert "Nothing judges this" in result.output
    assert "at least one Eval" in result.output
    assert not suite_file(root).exists()


@pytest.mark.parametrize(
    ("provenance", "said"),
    [
        (None, "provenance"),
        ("a real chat", "provenance"),
        ("prompt:greeting", "greeting"),
        ("tool:refund_order", "refund_order"),
        ("trace:20260923T100000Z-base/greeting", "trace:<run id>/<scenario>/<n>"),
    ],
)
def test_a_draft_whose_provenance_is_missing_or_unknown_is_refused(
    tmp_path: Path, provenance: str | None, said: str
) -> None:
    root = workspace(tmp_path)
    bad = draft("prompt:system", "Look it up first")
    if provenance is None:
        del bad["provenance"]
    else:
        bad["provenance"] = provenance
    drafts = write_drafts(tmp_path, [bad])

    result = invoke("generate", "--root", root, "--from", drafts)

    assert result.exit_code == 3
    assert said in result.output
    assert "scenarios[0]" in result.output
    assert not suite_file(root).exists()


def test_a_schema_error_prints_the_validate_lines_and_writes_nothing(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    broken = draft("prompt:system", "Look it up first", max_turns=0)
    drafts = write_drafts(tmp_path, [broken])
    manifest = target_dir(root) / "manifest.yaml"
    before = manifest.read_bytes()

    result = invoke("generate", "--root", root, "--from", drafts)

    assert result.exit_code == 3
    assert f"error: {drafts}: scenarios[0].max_turns:" in result.output
    assert not suite_file(root).exists()
    assert manifest.read_bytes() == before


def test_drafts_for_another_target_are_refused(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    drafts = write_drafts(
        tmp_path, [draft("prompt:system", "Look it up first")], target="help-desk"
    )

    result = invoke("generate", "--root", root, "--from", drafts)

    assert result.exit_code == 3
    assert "help-desk" in result.output
    assert "toy-order-desk" in result.output


# --- regeneration keeps ids (decision 30) ---

RULES = """\
# Rules

## Look up first

Call lookup_order before you answer.

## Cancel only when asked

Cancel only on an explicit request.

## Never invent

Say only what the tools returned.
"""

EDITED_RULES = """\
# Rules

## Look up first

Call lookup_order before you answer, every time.

## Cancel only when asked

Cancel only on an explicit request.

## Keep it short

At most three sentences.
"""


def headed_workspace(tmp_path: Path) -> Path:
    """A Workspace whose Target's system prompt is a file with a heading per rule."""
    root = workspace(tmp_path)
    prompts = target_dir(root) / "prompts"
    prompts.mkdir()
    (prompts / "system.md").write_text(RULES, encoding="utf-8")
    manifest = target_dir(root) / "manifest.yaml"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        text.replace("prompts: {system: observed}", "prompts: {system: prompts/system.md}"),
        encoding="utf-8",
    )
    return root


def rule_drafts(sections: list[tuple[str, str]]) -> list[dict[str, Any]]:
    return [draft(f"prompt:system#{section}", title) for section, title in sections]


def test_regenerating_after_a_prompt_edit_keeps_surviving_ids_adds_one_and_retires_one(
    tmp_path: Path,
) -> None:
    root = headed_workspace(tmp_path)
    first = write_drafts(
        tmp_path,
        rule_drafts(
            [
                ("look-up-first", "A status question is answered from the lookup"),
                ("cancel-only-when-asked", "A status question cancels nothing"),
                ("never-invent", "No refund amount the tools did not return"),
            ]
        ),
    )
    assert invoke("generate", "--root", root, "--from", first).exit_code == 0
    before = ids_of(written(root))
    assert before == [
        "look-up-first-a-status-question-is-answered-from-the-lookup",
        "cancel-only-when-asked-a-status-question-cancels-nothing",
        "never-invent-no-refund-amount-the-tools-did-not-return",
    ]

    (target_dir(root) / "prompts" / "system.md").write_text(EDITED_RULES, encoding="utf-8")
    second = write_drafts(
        tmp_path,
        rule_drafts(
            [
                ("look-up-first", "Every status answer comes from a fresh lookup"),
                ("cancel-only-when-asked", "A status question cancels nothing"),
                ("keep-it-short", "A reply stays within three sentences"),
            ]
        ),
    )
    result = invoke("generate", "--force", "--root", root, "--from", second)

    assert result.exit_code == 0, result.output
    after = written(root)
    today = datetime.now(UTC).date().isoformat()
    assert ids_of(after) == [
        before[0],
        before[1],
        "keep-it-short-a-reply-stays-within-three-sentences",
        before[2],
    ]
    assert after.scenarios[0].title == "Every status answer comes from a fresh lookup"
    assert after.not_run == {before[2]: f"retired by generate on {today}: no draft"}
    assert "retired" in result.stdout
    assert invoke("validate", "--root", root).exit_code == 0


def test_a_retired_scenario_a_draft_matches_again_is_run_again(tmp_path: Path) -> None:
    root = headed_workspace(tmp_path)
    one = draft("prompt:system#look-up-first", "Look it up first")
    two = draft("prompt:system#never-invent", "Invent nothing")
    assert (
        invoke("generate", "--root", root, "--from", write_drafts(tmp_path, [one, two])).exit_code
        == 0
    )
    assert (
        invoke(
            "generate", "--force", "--root", root, "--from", write_drafts(tmp_path, [one])
        ).exit_code
        == 0
    )
    assert list(written(root).not_run) == ["never-invent-invent-nothing"]

    result = invoke(
        "generate", "--force", "--root", root, "--from", write_drafts(tmp_path, [one, two])
    )

    assert result.exit_code == 0, result.output
    assert written(root).not_run == {}
    assert ids_of(written(root)) == [
        "look-up-first-look-it-up-first",
        "never-invent-invent-nothing",
    ]


def test_a_rekey_of_an_id_a_run_holds_is_refused_naming_the_run(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    copied = target_dir(root) / "runs" / BASE_RUN.name
    shutil.copytree(BASE_RUN, copied)
    record = copied / "run.json"  # the fixture was recorded under the example's slug
    record.write_text(
        record.read_text(encoding="utf-8").replace(
            '"target": "toy-order-desk"', '"target": "default"'
        ),
        encoding="utf-8",
    )
    kept = draft("prompt:system", "A greeting is answered", id="greeting-calls-no-tool")
    assert (
        invoke("generate", "--root", root, "--from", write_drafts(tmp_path, [kept])).exit_code == 0
    )
    suite = suite_file(root)
    before = suite.read_bytes()

    renamed = {**kept, "id": "greeting-is-answered"}
    result = invoke("generate", "--root", root, "--from", write_drafts(tmp_path, [renamed]))

    assert result.exit_code == 3
    assert "greeting-calls-no-tool" in result.output
    assert BASE_RUN.name in result.output
    assert suite.read_bytes() == before


def test_a_rekey_no_run_holds_is_written(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    kept = draft("prompt:system", "A greeting is answered", id="greeting-calls-no-tool")
    assert (
        invoke("generate", "--root", root, "--from", write_drafts(tmp_path, [kept])).exit_code == 0
    )

    renamed = {**kept, "id": "greeting-is-answered"}
    result = invoke(
        "generate", "--force", "--root", root, "--from", write_drafts(tmp_path, [renamed])
    )

    assert result.exit_code == 0, result.output
    assert ids_of(written(root)) == ["greeting-is-answered"]
    assert written(root).not_run == {}


def test_deleting_one_rule_and_adding_one_in_the_same_section_retires_the_old_and_adds_a_fresh_id(
    tmp_path: Path,
) -> None:
    """The toy's rules share one section, `prompt:system`: replacing the rule-5 draft with a
    new rule must not hand the new rule rule 5's id."""
    root = workspace(tmp_path)
    assert invoke("generate", "--root", root, "--from", TOY_DRAFTS).exit_code == 0
    before = ids_of(written(root))
    drafts = yaml.safe_load(TOY_DRAFTS.read_text(encoding="utf-8"))
    rule_5 = next(i for i, d in enumerate(drafts["scenarios"]) if d.get("tags") == ["rule-5"])
    drafts["scenarios"][rule_5] = draft(
        "prompt:system",
        "The desk refuses to discuss competitors",
        {"must_not_say": ["Contoso Cycles"]},
    )
    edited = tmp_path / "edited.yaml"
    edited.write_text(yaml.safe_dump(drafts), encoding="utf-8")

    result = invoke("generate", "--force", "--root", root, "--from", edited)

    assert result.exit_code == 0, result.output
    assert "1 new, 7 kept, 0 re-keyed, 1 retired" in result.stdout
    after = written(root)
    fresh = "system-the-desk-refuses-to-discuss-competitors"
    assert ids_of(after)[rule_5] == fresh
    assert fresh not in before
    assert list(after.not_run) == [before[rule_5]]


# --- what is on disk already ---


def test_an_edited_suite_outside_git_is_refused_without_force(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    sample = suite_file(root, "sample")
    before = sample.read_bytes()
    drafts = write_drafts(tmp_path, [draft("prompt:system", "Look it up first")])

    refused = invoke("generate", "--root", root, "--from", drafts, "--suite", "sample")

    assert refused.exit_code == 3
    assert str(sample) in refused.output
    assert "--force" in refused.output
    assert sample.read_bytes() == before

    forced = invoke("generate", "--root", root, "--from", drafts, "--suite", "sample", "--force")
    assert forced.exit_code == 0, forced.output
    assert sample.read_bytes() != before


def git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *arguments],
        check=True,
        capture_output=True,
    )


def test_a_committed_clean_suite_is_rewritten(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    first = write_drafts(tmp_path, [draft("prompt:system", "Look it up first")])
    assert invoke("generate", "--root", root, "--from", first).exit_code == 0
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "generated")

    second = write_drafts(
        tmp_path,
        [draft("prompt:system", "Look it up first"), draft("prompt:system", "Invent nothing")],
    )
    result = invoke("generate", "--root", root, "--from", second)

    assert result.exit_code == 0, result.output
    assert ids_of(written(root)) == ["system-look-it-up-first", "system-invent-nothing"]


def test_a_title_with_no_word_is_refused_naming_it(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    drafts = write_drafts(tmp_path, [draft("prompt:system", "!!!")])

    result = invoke("generate", "--root", root, "--from", drafts)

    assert result.exit_code == 3
    assert "scenarios[0].title" in result.output
    assert "'!!!'" in result.output
    assert not suite_file(root).exists()


def test_a_crlf_manifest_keeps_its_line_endings(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    manifest = target_dir(root) / "manifest.yaml"
    lf = manifest.read_bytes()
    manifest.write_bytes(lf.replace(b"\n", b"\r\n"))
    drafts = write_drafts(tmp_path, [draft("prompt:system", "Look it up first")])

    assert invoke("generate", "--root", root, "--from", drafts).exit_code == 0

    after = manifest.read_bytes()
    assert after.count(b"\n") == after.count(b"\r\n")
    assert after.replace(b"\r\n", b"\n").splitlines() == [
        *lf.splitlines(),
        b"  - suites/generated.yaml",
    ]


def test_the_suite_header_names_the_drafts_relative_to_the_workspace_or_by_name(
    tmp_path: Path,
) -> None:
    root = workspace(tmp_path)
    outside = write_drafts(tmp_path, [draft("prompt:system", "Look it up first")])
    assert invoke("generate", "--root", root, "--from", outside).exit_code == 0
    assert "#   drafts.yaml\n" in suite_file(root).read_text(encoding="utf-8")

    inside = root / "drafts" / "desk.yaml"
    inside.parent.mkdir()
    inside.write_bytes(outside.read_bytes())
    assert invoke("generate", "--root", root, "--from", inside, "--force").exit_code == 0
    assert "#   drafts/desk.yaml\n" in suite_file(root).read_text(encoding="utf-8")
    assert str(tmp_path) not in suite_file(root).read_text(encoding="utf-8")


# --- the toy order desk, end to end offline ---


def test_the_toy_order_desk_drafts_generate_a_suite_every_scenario_of_which_can_run(
    tmp_path: Path,
) -> None:
    """The ticket's last criterion, offline: the drafts under tests/fixtures/drafts cover
    the toy's five rules and two tools; the Suite `generate` writes from them validates,
    `run --dry-run` lists every Scenario, and every declaration preflight hands a Trial
    reads as its Eval's parameters, which is the one path to an `invalid` Score whose
    `fault_source` is `scenario` (`eval.mechanical.perform_mechanical`). A live Run needs a
    recording these Scenarios do not have yet."""
    from agentdiag.eval.registry import REGISTRY
    from agentdiag.run.preflight import preflight
    from agentdiag.scenario.select import Selection
    from agentdiag.workspace import Workspace

    root = workspace(tmp_path)
    result = invoke("generate", "--root", root, "--from", TOY_DRAFTS)
    assert result.exit_code == 0, result.output
    suite = written(root)
    assert len(suite.scenarios) == 8
    assert {scenario.provenance for scenario in suite.scenarios} == {
        "prompt:system",
        "tool:lookup_order",
        "tool:cancel_order",
    }

    validated = invoke("validate", "--root", root)
    assert validated.exit_code == 0, validated.output
    assert "0 errors, 0 warnings" in validated.stdout

    dry = invoke("run", "--root", root, "--suite", "generated", "--dry-run")
    assert dry.exit_code == 0, dry.output
    for scenario in suite.scenarios:
        assert scenario.id in dry.stdout

    target = Workspace.find(root).resolve(None)
    plan = preflight(target, Selection(suite=["generated"]), None, dry_run=True)
    assert sorted(planned.scenario.id for planned in plan.selected) == sorted(ids_of(suite))
    for planned in plan.selected:
        assert planned.scenario.evals, planned.scenario.id
        for declaration in planned.scenario.evals:
            spec = REGISTRY[declaration.eval]
            spec.parameters(declaration)


def test_the_generate_package_imports_no_sdk() -> None:
    probe = (
        "import sys, agentdiag.generate, agentdiag.generate.command; "
        "leaked = sorted(m for m in sys.modules if m == 'anthropic' "
        "or m.startswith(('anthropic.', 'agentdiag.model', 'agentdiag.adapter'))); "
        "print(','.join(leaked))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=REPO
    )

    assert completed.stdout.strip() == ""


# --- target show names the sections a provenance cites ---


def test_target_show_lists_the_fingerprint_sections_by_id_and_summary(tmp_path: Path) -> None:
    root = headed_workspace(tmp_path)
    assert invoke("sync", "--root", root).exit_code == 0

    result = invoke("target", "show", "--root", root)

    assert result.exit_code == 0, result.output
    assert "prompt.system#look-up-first" in result.stdout
    assert "Look up first" in result.stdout
    assert "tool.cancel_order" in result.stdout


def pending_target(tmp_path: Path) -> tuple[Path, Path]:
    """The migrate skill's Target before its drafts: `init --target library-desk` with an
    Adapter of kind pending, the source's prompt and tools copied and pointed at, and no
    Fingerprint, since nothing can `sync` it yet (ADR-0016 §7)."""
    from tests.migrate_fixtures import IDENTITY, SLUG, SOURCE, settle_manifest

    root = tmp_path / "migrated"
    words = [word for pair in IDENTITY for word in pair]
    assert invoke("init", "--root", root, "--target", SLUG, *words).exit_code == 0
    target = root / ".agentdiag" / "targets" / SLUG
    for kind in ("prompts", "tools"):
        shutil.copytree(SOURCE / kind, target / kind)
    settle_manifest(target / "manifest.yaml")
    return root, target


def test_with_no_fingerprint_a_draft_naming_no_heading_of_the_prompt_file_is_warned(
    tmp_path: Path,
) -> None:
    """0.1.2-interfaces, amended after the ticket 49 reviews: a pending Target has no
    Fingerprint to check a section against, so the prompt file the Manifest points at, split
    as `sync` splits it, stands in; `generate --check` still exits 0, since `sync` may yet
    disagree with nothing."""
    root, _ = pending_target(tmp_path)
    drafts = write_drafts(
        tmp_path,
        [draft("prompt:system#rule", "Rule 1: a holding check goes through the catalogue")],
        target="Ada",
    )

    result = invoke("generate", "--root", root, "--from", drafts, "--check")

    assert result.exit_code == 0, result.output
    assert [line for line in result.stdout.splitlines() if line.startswith("warning:")] == [
        "warning: scenarios[0].provenance: prompt.system#rule is no section of "
        "prompts/system.md (its sections: prompt.system#persona, prompt.system#rules, "
        "prompt.system#tools); agentdiag target show lists them once sync has run"
    ]


def test_with_no_fingerprint_a_draft_naming_a_real_heading_is_not_warned(
    tmp_path: Path,
) -> None:
    root, _ = pending_target(tmp_path)
    drafts = write_drafts(
        tmp_path,
        [draft("prompt:system#rules", "Rule 1: a holding check goes through the catalogue")],
        target="Ada",
    )

    result = invoke("generate", "--root", root, "--from", drafts, "--check")

    assert result.exit_code == 0, result.output
    assert "warning" not in result.output


def test_with_a_fingerprint_the_sections_are_the_fingerprints_as_before(tmp_path: Path) -> None:
    """Once `sync` has written a Fingerprint it is the one checked, and its warning is the
    one it was; the prompt file is not read for it."""
    root = headed_workspace(tmp_path)
    assert invoke("sync", "--root", root).exit_code == 0
    drafts = write_drafts(tmp_path, [draft("prompt:system#no-such-rule", "A status question")])

    result = invoke("generate", "--root", root, "--from", drafts, "--check")

    assert result.exit_code == 0, result.output
    assert [line for line in result.stdout.splitlines() if line.startswith("warning:")] == [
        "warning: scenarios[0].provenance: prompt.system#no-such-rule is no section of the "
        "Fingerprint in force; `agentdiag target show` lists them"
    ]


# --- units: the id derivation and the matching rule ---


@pytest.mark.parametrize(
    ("provenance", "anchor"),
    [
        ("prompt:system#rule-3", "rule-3"),
        ("prompt:system", "system"),
        ("tool:cancel_order", "cancel-order"),
        ("trace:20260923T100000Z-base/where-is-shipped-order/2", "where-is-shipped-order"),
        ("change:chg-0007", "chg-0007"),
    ],
)
def test_each_provenance_form_names_its_anchor(provenance: str, anchor: str) -> None:
    assert parse_provenance(provenance).anchor == anchor


def test_an_id_is_the_anchor_and_the_title_truncated_to_sixty() -> None:
    long_title = "A customer who writes a very long complaint about a late order is still helped"

    identifier = derive_id("rule-3", long_title, taken=set())

    assert identifier.startswith("rule-3-a-customer-who-writes")
    assert len(identifier) <= 60
    assert not identifier.endswith("-")


def test_friction_20_a_title_opening_with_the_anchor_does_not_repeat_it() -> None:
    """Walkthrough friction 20: `search-articles-search-articles-is-called-with-a-query`."""
    assert (
        derive_id("search_articles", "search_articles is called with a query", taken=set())
        == "search-articles-is-called-with-a-query"
    )
    assert derive_id("tone", "Tone is plain and warm", taken=set()) == "tone-is-plain-and-warm"
    assert derive_id("rules", "Rule 1 searches first", taken=set()) == (
        "rules-rule-1-searches-first"
    ), "a title that only shares a prefix of the anchor's word keeps the anchor"
    assert derive_id("tone", "Tone", taken=set()) == "tone"


def test_a_clash_within_the_suite_gets_a_numbered_suffix() -> None:
    taken = {"rule-3-invent-nothing"}

    assert derive_id("rule-3", "Invent nothing", taken=taken) == "rule-3-invent-nothing-2"
    taken.add("rule-3-invent-nothing-2")
    assert derive_id("rule-3", "Invent nothing", taken=taken) == "rule-3-invent-nothing-3"
    long_taken = {derive_id("rule-3", "x" * 80, taken=set())}
    suffixed = derive_id("rule-3", "x" * 80, taken=long_taken)
    assert suffixed.endswith("-2")
    assert len(suffixed) <= 60


def test_matching_prefers_provenance_and_title_then_a_unique_provenance() -> None:
    existing = [
        Existing(id="a", provenance="prompt:system#one", title="First"),
        Existing(id="b", provenance="prompt:system#two", title="Second"),
        Existing(id="c", provenance="prompt:system", title="Same section one"),
        Existing(id="d", provenance="prompt:system", title="Same section two"),
    ]
    drafts = [
        Drafted("prompt:system#two", "Second"),
        Drafted("prompt:system#one", "First, reworded"),
        Drafted("prompt:system", "Same section, reworded"),
    ]

    matched = match_drafts(drafts, existing)

    assert matched == {0: 1, 1: 0}


def test_a_provenance_two_drafts_share_matches_on_the_title_only() -> None:
    """Uniqueness is over every draft and every existing Scenario (decision 30): a section
    whose one rule was replaced by another, with a draft for each, hands neither the old id."""
    existing = [Existing(id="old", provenance="prompt:system#rules", title="Old rule")]
    drafts = [
        Drafted("prompt:system#rules", "A new rule"),
        Drafted("prompt:system#rules", "Another new rule"),
    ]

    assert match_drafts(drafts, existing) == {}
    assert match_drafts(drafts[:1], existing) == {0: 0}


def test_friction_23_forbidden_phrases_with_nothing_listed_is_a_warning(tmp_path: Path) -> None:
    """Walkthrough friction 23: a draft declaring `- forbidden_phrases` passed `--check`
    with no Manifest list, and so would pass on nothing."""
    root = workspace(tmp_path)
    drafts = write_drafts(
        tmp_path, [draft("prompt:system", "Says nothing banned", "forbidden_phrases")]
    )

    checked = invoke("generate", "--root", root, "--from", drafts, "--check")
    validated = invoke("validate", "--root", root, "--manifest", target_dir(root) / "manifest.yaml")

    assert checked.exit_code == 0, checked.output
    assert "passes on nothing" in checked.output
    assert validated.exit_code == 0
