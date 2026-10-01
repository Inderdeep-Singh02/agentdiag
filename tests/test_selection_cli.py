"""Seam 1: which Scenarios a Run executes, and how every other one is named (ticket 07).

Two committed Suites, placed in a Target root the tests build: `selection.yaml` (tags, two
`not_run` entries, every kind of Scenario, one with a Fixture) at `suites/selection.yaml`,
and `selection-refunds.yaml` at `suites/billing/refunds.yaml`, so a Suite is addressed by a
stem and by a path that are not the same. The Target is a fake that calls no model, so
nothing here needs a recording or credentials. Selection is read through `run --dry-run`
where the question is only what was chosen, and through a real Run where the question is
what `run.json`, the Scorecard and the summary say (D31, ADR-0005 §3, §8, phase-5
decisions 11 and 12).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.cli import app

REPO = Path(__file__).resolve().parents[1]
SUITES = REPO / "tests" / "fixtures" / "suites"

SELECTION_TEXT = "Refunds are frozen while the desk migrates its ledger."
SIMULATED_TEXT = "Adaptive, so it runs only when named with --scenario."

GREET = "greet-the-desk"
CARMEN = "ask-as-carmen"
CANCEL = "cancel-without-a-tool"
TWO_TURNS = "refund-in-two-turns"
ADAPTIVE = "cancel-adaptively"
LATE = "refund-a-late-order"
DISPUTE = "dispute-a-charge"

PREFLIGHT_EXIT = 3
"""Usage, configuration and preflight errors: no Run directory (D32)."""

UNDECIDED_EXIT = 2
"""What a Run including `cancel-without-a-tool` exits with: its `forbid_tools` is
`unverifiable` against a fake Target that calls no model, so nothing fails and not
everything is decided (D32)."""

runner = CliRunner()


def selection_root(tmp_path: Path, factory: str = "tests.fakes.seeded_target") -> Path:
    """A Target root with the two Suites and a Manifest pointing at a fake Target."""
    root = tmp_path / "desk"
    suites = root / ".agentdiag" / "targets" / "toy-order-desk" / "suites"
    (suites / "billing").mkdir(parents=True)
    shutil.copy(SUITES / "selection.yaml", suites / "selection.yaml")
    shutil.copy(SUITES / "selection-refunds.yaml", suites / "billing" / "refunds.yaml")
    manifest = {
        "schema_version": 1,
        "target": {"name": "selection-desk"},
        "adapter": {
            "kind": "inprocess",
            "side_effects": "none",
            "environments": {
                "default": "local",
                "local": {"factory": f"{factory}:make_target", "tools": f"{factory}:TOOLS"},
            },
        },
        "suites": ["suites/selection.yaml", "suites/billing/refunds.yaml"],
    }
    (root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml").write_text(
        yaml.safe_dump(manifest), encoding="utf-8"
    )
    return root


def run(root: Path, *arguments: str) -> Any:
    return runner.invoke(app, ["run", "--root", str(root), *arguments])


def dry_run(root: Path, *arguments: str) -> Any:
    return run(root, "--dry-run", *arguments)


def chosen(result: Any) -> list[str]:
    """The selected Scenario ids a dry run printed, in its order."""
    assert result.exit_code == 0, result.stdout
    return [
        line.split("  ")[0]
        for line in result.stdout.splitlines()
        if "  kinds " in line and "  not run  " not in line
    ]


def left_out(result: Any) -> dict[str, str]:
    """Every not-run Scenario a dry run or a summary printed, with its reason."""
    return {
        line.split("  ")[0]: line.split("  not run  ")[1].split("  ")[0]
        for line in result.stdout.splitlines()
        if "  not run  " in line
    }


def only_run(root: Path) -> Path:
    (run_dir,) = (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir()
    return run_dir


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# --- no flag, and a Suite's own skips (decision 11) ---


def test_no_selection_flag_selects_every_scenario_except_the_suites_not_run_entries(
    tmp_path: Path,
) -> None:
    result = dry_run(selection_root(tmp_path))

    assert chosen(result) == [GREET, CARMEN, CANCEL, LATE, DISPUTE]
    assert left_out(result) == {TWO_TURNS: "suite_not_run", ADAPTIVE: "suite_not_run"}


def test_a_tag_selection_honours_the_suites_not_run_and_names_it_with_the_suites_text(
    tmp_path: Path,
) -> None:
    result = dry_run(selection_root(tmp_path), "--tag", "refund")

    assert chosen(result) == [LATE]
    assert (
        f"{TWO_TURNS}  kinds conversation  suite selection  not run  suite_not_run  "
        f"{SELECTION_TEXT}"
    ) in result.stdout.splitlines()


def test_a_suite_selection_honours_the_suites_not_run(tmp_path: Path) -> None:
    result = dry_run(selection_root(tmp_path), "--suite", "selection")

    assert chosen(result) == [GREET, CARMEN, CANCEL]
    assert left_out(result)[TWO_TURNS] == "suite_not_run"
    assert left_out(result)[LATE] == "not_selected"


def test_a_scenario_named_with_scenario_runs_even_when_its_suite_lists_it_under_not_run(
    tmp_path: Path,
) -> None:
    result = dry_run(selection_root(tmp_path), "--scenario", TWO_TURNS)

    assert chosen(result) == [TWO_TURNS]
    assert left_out(result)[GREET] == "not_selected"


def test_a_scenario_its_suite_skips_is_suite_not_run_even_when_the_selection_missed_it(
    tmp_path: Path,
) -> None:
    """The Suite's text is the more useful reason: a skip is named as a skip whatever the
    flags, and `not_selected` is only for Scenarios the Suite does not skip."""
    result = dry_run(selection_root(tmp_path), "--scenario", GREET)

    assert left_out(result)[ADAPTIVE] == "suite_not_run"
    assert left_out(result)[TWO_TURNS] == "suite_not_run"
    assert left_out(result)[CARMEN] == "not_selected"
    assert SIMULATED_TEXT in result.stdout


def test_the_help_says_scenario_overrides_a_suites_not_run() -> None:
    result = runner.invoke(app, ["run", "--help"], terminal_width=200)

    assert "not_run" in result.stdout
    assert "--tag" in result.stdout
    assert "--suite" in result.stdout
    assert "--dry-run" in result.stdout


# --- how flags combine (D31) ---


def test_values_of_one_flag_combine_as_or(tmp_path: Path) -> None:
    root = selection_root(tmp_path)

    assert chosen(dry_run(root, "--tag", "greeting", "--tag", "billing")) == [GREET, DISPUTE]
    assert chosen(dry_run(root, "--scenario", GREET, "--scenario", LATE)) == [GREET, LATE]
    assert chosen(dry_run(root, "--suite", "selection", "--suite", "refunds")) == [
        GREET,
        CARMEN,
        CANCEL,
        LATE,
        DISPUTE,
    ]


def test_different_flags_combine_as_and(tmp_path: Path) -> None:
    root = selection_root(tmp_path)

    assert chosen(dry_run(root, "--tag", "refund", "--suite", "refunds")) == [LATE]
    assert chosen(dry_run(root, "--tag", "orders", "--tag", "greeting", "--scenario", GREET)) == [
        GREET
    ]


def test_the_selection_is_in_suite_order_whatever_order_the_flags_were_typed_in(
    tmp_path: Path,
) -> None:
    result = dry_run(selection_root(tmp_path), "--scenario", DISPUTE, "--scenario", GREET)

    assert chosen(result) == [GREET, DISPUTE]


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("one_shot", [GREET, CARMEN, CANCEL, LATE]),
        ("conversation", [DISPUTE]),
        ("tool", [CANCEL]),
    ],
)
def test_a_derived_kind_is_selectable_as_a_tag(
    tmp_path: Path, kind: str, expected: list[str]
) -> None:
    """Decision 2: `kind` is never authored, and still selects."""
    assert chosen(dry_run(selection_root(tmp_path), "--tag", kind)) == expected


def test_a_simulated_scenario_its_suite_skips_is_listed_with_its_kind_and_not_refused(
    tmp_path: Path,
) -> None:
    """Decision 14: an unselected `simulate` Scenario loads and is listed as `simulated`."""
    result = dry_run(selection_root(tmp_path))

    assert (
        f"{ADAPTIVE}  kinds simulated  suite selection  not run  suite_not_run  {SIMULATED_TEXT}"
    ) in result.stdout.splitlines()


def test_selecting_only_skipped_scenarios_by_tag_is_empty_and_names_the_suites_text(
    tmp_path: Path,
) -> None:
    result = dry_run(selection_root(tmp_path), "--tag", "simulated")

    assert result.exit_code == PREFLIGHT_EXIT, result.stdout
    assert f"{ADAPTIVE}: {SIMULATED_TEXT}" in result.stdout


@pytest.mark.parametrize(
    "address",
    [
        "refunds",
        "suites/billing/refunds.yaml",
        "suites/billing/refunds",
        "./suites/billing/refunds",
    ],
)
def test_a_suite_is_addressed_by_its_stem_or_by_its_path_and_recorded_by_its_name(
    tmp_path: Path, address: str
) -> None:
    """However the Suite is spelt, the selection records one expression for it."""
    result = dry_run(selection_root(tmp_path), "--suite", address)

    assert chosen(result) == [LATE, DISPUTE]
    assert result.stdout.splitlines()[-2] == "selection suite=refunds"


def test_two_suites_sharing_a_stem_are_named_by_their_paths(tmp_path: Path) -> None:
    root = selection_root(tmp_path)
    other = root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "other"
    other.mkdir()
    document = yaml.safe_load((SUITES / "selection-refunds.yaml").read_text(encoding="utf-8"))
    for scenario in document["scenarios"]:
        scenario["id"] = f"other-{scenario['id']}"
    (other / "refunds.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    manifest_path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["suites"].append("suites/other/refunds.yaml")
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    result = dry_run(root, "--suite", "refunds")

    assert chosen(result) == [LATE, DISPUTE, "other-refund-a-late-order", "other-dispute-a-charge"]
    assert f"{LATE}  kinds one_shot  tags refund  suite suites/billing/refunds" in result.stdout
    # The last line is the Sync the Run would find (phase-6 decision 14).
    assert result.stdout.splitlines()[-2] == (
        "selection suite=suites/billing/refunds,suites/other/refunds"
    )


# --- a selection that matches nothing is an error, never an empty green Run (D31, D32) ---


def test_a_selection_matching_no_scenario_exits_3_naming_the_flags_used(tmp_path: Path) -> None:
    root = selection_root(tmp_path)

    result = run(root, "--tag", "greeting", "--suite", "refunds")

    assert result.exit_code == PREFLIGHT_EXIT, result.stdout
    assert "--tag greeting" in result.stdout
    assert "--suite refunds" in result.stdout
    assert "matched no Scenario" in result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()


@pytest.mark.parametrize(
    ("flag", "value", "named"),
    [
        ("--tag", "no-such-tag", "tag"),
        ("--suite", "no-such-suite", "Suite"),
        ("--scenario", "no-such-scenario", "Scenario"),
    ],
)
def test_a_value_that_matches_nothing_at_all_is_named(
    tmp_path: Path, flag: str, value: str, named: str
) -> None:
    root = selection_root(tmp_path)

    result = run(root, flag, value)

    assert result.exit_code == PREFLIGHT_EXIT, result.stdout
    assert f"{flag} {value}" in result.stdout
    assert named in result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()


def test_a_selection_whose_every_match_its_suite_skips_is_empty_and_says_so(
    tmp_path: Path,
) -> None:
    result = run(selection_root(tmp_path), "--tag", "refund", "--suite", "selection")

    assert result.exit_code == PREFLIGHT_EXIT, result.stdout
    assert "--tag refund --suite selection" in result.stdout
    assert "not_run" in result.stdout
    assert "--scenario" in result.stdout


def test_a_scenario_id_two_selected_suites_both_declare_is_a_preflight_problem(
    tmp_path: Path,
) -> None:
    """Ticket 03's rule: two Trials of one id would share one directory."""
    root = selection_root(tmp_path)
    refunds = (
        root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "billing" / "refunds.yaml"
    )
    document = yaml.safe_load(refunds.read_text(encoding="utf-8"))
    document["scenarios"][0]["id"] = GREET
    refunds.write_text(yaml.safe_dump(document), encoding="utf-8")

    result = run(root)

    assert result.exit_code == PREFLIGHT_EXIT, result.stdout
    assert f"{GREET!r} is declared in more than one Suite (selection, refunds)" in result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()
    assert chosen(dry_run(root, "--suite", "selection", "--scenario", GREET)) == [GREET]


# --- Fixtures the Adapter cannot apply (decision 12) ---


def test_a_scenario_whose_fixtures_the_adapter_cannot_apply_is_not_run_and_the_run_proceeds(
    tmp_path: Path,
) -> None:
    root = selection_root(tmp_path, factory="tests.fakes.fixtureless_target")

    result = run(root, "--tag", "orders")

    assert result.exit_code == UNDECIDED_EXIT, result.stdout
    run_dir = only_run(root)
    record = read_json(run_dir / "run.json")
    assert [scenario["id"] for scenario in record["scenarios"]] == [CANCEL]
    (unavailable,) = [entry for entry in record["not_run"] if entry["scenario"] == CARMEN]
    assert unavailable["reason"] == "fixture_unavailable"
    assert "does not accept fixtures" in unavailable["detail"]
    assert not (run_dir / "trials" / CARMEN).exists()
    assert f"{CARMEN}  suite selection  not run  fixture_unavailable  " in result.stdout


def test_a_scenario_continuing_one_whose_fixtures_are_unavailable_is_not_run_either(
    tmp_path: Path,
) -> None:
    """Decision 13: the session it would resume never opens, so it goes with the first."""
    root = selection_root(tmp_path, factory="tests.fakes.fixtureless_target")
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "selection.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["scenarios"].append(
        {
            "id": "then-cancel-it",
            "title": "Then cancel it in the same conversation",
            "tags": ["orders"],
            "continues": CARMEN,
            "turns": ["Cancel it, please."],
        }
    )
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    result = dry_run(root, "--tag", "orders")

    assert chosen(result) == [CANCEL]
    assert left_out(result)["then-cancel-it"] == "fixture_unavailable"
    assert f"it continues {CARMEN!r}, whose Fixtures the Adapter cannot apply" in result.stdout


def test_a_scenario_named_by_scenario_whose_fixtures_cannot_be_applied_is_refused(
    tmp_path: Path,
) -> None:
    """Naming a Scenario is a demand, as naming an id that does not exist is."""
    root = selection_root(tmp_path, factory="tests.fakes.fixtureless_target")

    result = run(root, "--scenario", CARMEN, "--scenario", GREET)

    assert result.exit_code == PREFLIGHT_EXIT, result.stdout
    assert f"--scenario {CARMEN} names a Scenario whose Fixtures the Adapter" in result.stdout
    assert "does not accept fixtures" in result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()


def test_a_tag_selection_whose_every_match_lacks_its_fixtures_is_empty_and_says_why(
    tmp_path: Path,
) -> None:
    root = selection_root(tmp_path, factory="tests.fakes.fixtureless_target")

    result = run(root, "--tag", "lookup")

    assert result.exit_code == PREFLIGHT_EXIT, result.stdout
    assert "the Adapter cannot apply the Fixtures of every match" in result.stdout
    assert "does not accept fixtures" in result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()


# --- what did not run is written down, with its reason (ADR-0005 §3, §8) ---

EXPECTED_NOT_RUN = [
    {
        "scenario": GREET,
        "suite": "selection",
        "reason": "not_selected",
        "detail": None,
        "trial": None,
    },
    {
        "scenario": TWO_TURNS,
        "suite": "selection",
        "reason": "suite_not_run",
        "detail": SELECTION_TEXT,
        "trial": None,
    },
    {
        "scenario": ADAPTIVE,
        "suite": "selection",
        "reason": "suite_not_run",
        "detail": SIMULATED_TEXT,
        "trial": None,
    },
    {"scenario": LATE, "suite": "refunds", "reason": "not_selected", "detail": None, "trial": None},
    {
        "scenario": DISPUTE,
        "suite": "refunds",
        "reason": "not_selected",
        "detail": None,
        "trial": None,
    },
]
"""`--tag orders` over the two Suites, in Suite order then file order."""


def test_every_scenario_that_did_not_run_is_in_run_json_with_its_reason(tmp_path: Path) -> None:
    root = selection_root(tmp_path)

    result = run(root, "--tag", "orders")

    assert result.exit_code == UNDECIDED_EXIT, result.stdout
    record = read_json(only_run(root) / "run.json")
    assert [scenario["id"] for scenario in record["scenarios"]] == [CARMEN, CANCEL]
    assert record["not_run"] == EXPECTED_NOT_RUN


def test_every_scenario_that_did_not_run_is_in_the_scorecard_with_its_reason(
    tmp_path: Path,
) -> None:
    root = selection_root(tmp_path)

    run(root, "--tag", "orders")

    assert read_json(only_run(root) / "scorecard.json")["not_run"] == EXPECTED_NOT_RUN


def test_the_summary_prints_every_not_run_scenario_with_its_detail_before_the_counts(
    tmp_path: Path,
) -> None:
    result = run(selection_root(tmp_path), "--tag", "orders")

    lines = result.stdout.strip().splitlines()
    assert f"{TWO_TURNS}  suite selection  not run  suite_not_run  {SELECTION_TEXT}" in lines
    assert f"{GREET}  suite selection  not run  not_selected" in lines
    not_run_lines = [number for number, line in enumerate(lines) if "  not run  " in line]
    assert len(not_run_lines) == len(EXPECTED_NOT_RUN)
    counts = next(number for number, line in enumerate(lines) if "pass rate" in line)
    assert max(not_run_lines) < counts
    assert f"not run {len(EXPECTED_NOT_RUN)}" in lines[counts]


def test_a_scenario_id_two_suites_declare_is_named_with_its_suite_where_it_did_not_run(
    tmp_path: Path,
) -> None:
    """Ids are unique only within a Suite: the one that ran and the one that did not are
    told apart by their Suites in `run.json`, the summary and a dry run."""
    root = selection_root(tmp_path)
    document = yaml.safe_load((SUITES / "selection.yaml").read_text(encoding="utf-8"))
    document["scenarios"][0]["turns"] = ["Hello", "Anyone there?"]
    (root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "dup.yaml").write_text(
        yaml.safe_dump(document), encoding="utf-8"
    )
    manifest_path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["suites"].append("suites/dup.yaml")
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    planned = dry_run(root, "--tag", "greeting", "--suite", "dup")
    result = run(root, "--tag", "greeting", "--suite", "dup")

    assert f"{GREET}  kinds conversation  tags greeting  suite dup" in planned.stdout
    assert f"{GREET}  kinds one_shot  suite selection  not run  not_selected" in planned.stdout
    assert result.exit_code == 0, result.stdout
    record = read_json(only_run(root) / "run.json")
    assert [scenario["id"] for scenario in record["scenarios"]] == [GREET]
    assert {
        "scenario": GREET,
        "suite": "selection",
        "reason": "not_selected",
        "detail": None,
        "trial": None,
    } in (record["not_run"])
    assert not [
        entry
        for entry in record["not_run"]
        if (entry["suite"], entry["scenario"]) == ("dup", GREET)
    ]
    assert f"{GREET}  suite selection  not run  not_selected" in result.stdout.splitlines()


# --- the normalised selection in run.json (D31) ---


def test_the_normalised_selection_expression_is_written_to_run_json(tmp_path: Path) -> None:
    root = selection_root(tmp_path)

    run(root, "--tag", "orders", "--tag", "lookup", "--tag", "orders", "--suite", "selection")

    assert read_json(only_run(root) / "run.json")["selection"] == {
        "scenario": [],
        "tag": ["lookup", "orders"],
        "suite": ["selection"],
        "expression": "tag=lookup,orders suite=selection",
    }


def test_a_run_with_no_selection_flag_records_the_expression_all(tmp_path: Path) -> None:
    root = selection_root(tmp_path)

    result = run(root)

    assert result.exit_code == UNDECIDED_EXIT, result.stdout
    assert read_json(only_run(root) / "run.json")["selection"] == {
        "scenario": [],
        "tag": [],
        "suite": [],
        "expression": "all",
    }
