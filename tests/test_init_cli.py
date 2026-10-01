"""Seam 1: `agentdiag init` scaffolds a Workspace with one Target, and the first Run needs no
editing.

`init` with no `--target` writes the Target `default` under `.agentdiag/targets/default/`
(ADR-0013); `tests/test_workspace_cli.py` covers `--target` and the Workspace of several.

Every assertion is on what a developer can observe after typing one command — a file on
disk, a line of output, an exit code — and then on whether `run` and `show` accept what
`init` wrote. The checked-in `examples/toy/.agentdiag/` is the truth of what `init` writes
with no `--adapter`, so the two can never drift apart (ticket 02).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.eval.notes import JUDGE_NOTES_MAX_WORDS
from agentdiag.run.manifest import Manifest, load_manifest
from agentdiag.run.templates import (
    EXAMPLE_ONLY,
    EXAMPLE_SCAFFOLD,
    render_judge_notes,
    render_manifest,
    render_suite,
)
from agentdiag.scenario.load import load_suite
from agentdiag.workspace import INDEX_FILE, RUNS_DIRNAME, Workspace
from tests.stories import CANCEL_COUNTS, CANCEL_EXIT

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy" / ".agentdiag" / "targets" / "toy-order-desk"
RECORDINGS = REPO / "tests" / "fixtures" / "recordings"
TOY_RECORDING = RECORDINGS / "toy-cancel.jsonl"

DERIVED = (RUNS_DIRNAME, INDEX_FILE)
"""What running the example writes beside what `init` wrote: output, never source."""

GITIGNORED = [
    ".agentdiag/targets/*/runs/",
    ".agentdiag/targets/*/restore-points/",
    ".agentdiag/targets/*/platform/",
    ".agentdiag/index.sqlite",
]

TOY_SCENARIO = "cancel-processing-order"
SAMPLE_SCENARIO = "first-turn"
"""The sample Scenario `init --adapter` writes for a Target agentdiag knows nothing about."""

runner = CliRunner()


def init(root: Path, *arguments: str) -> object:
    return runner.invoke(app, ["init", "--root", str(root), *arguments])


def target_dir(root: Path) -> Path:
    """Where `init` with no `--target` writes: the Target `default` (ADR-0013)."""
    return root / ".agentdiag" / "targets" / "default"


def manifest_in(root: Path) -> Manifest:
    """The Manifest of the one Target of the Workspace at `root`, as `run` loads it."""
    return load_manifest(Workspace.find(root).resolve(None))


def manifest_of(root: Path) -> dict:
    return yaml.safe_load((target_dir(root) / "manifest.yaml").read_text(encoding="utf-8"))


def suite_of(root: Path) -> dict:
    return yaml.safe_load((target_dir(root) / "suites" / "sample.yaml").read_text(encoding="utf-8"))


def only_run(root: Path) -> Path:
    runs = sorted((target_dir(root) / "runs").iterdir())
    assert len(runs) == 1, f"expected one Run directory, found {[run.name for run in runs]}"
    return runs[0]


# --- what one command leaves on disk ---


def test_init_writes_a_manifest_a_sample_suite_and_a_gitignore_entry(tmp_path: Path) -> None:
    """The three artefacts of D39, from one command and no arguments."""
    result = init(tmp_path)

    assert result.exit_code == 0, result.stdout
    assert (target_dir(tmp_path) / "manifest.yaml").is_file()
    assert (target_dir(tmp_path) / "suites" / "sample.yaml").is_file()
    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert all(line in lines for line in GITIGNORED)


def test_the_scaffolded_manifest_holds_target_adapter_tools_and_suites(tmp_path: Path) -> None:
    """D37: `init` writes the sections a Run reads, and the one prompt pointer the Adapter
    observes, so `agentdiag sync` has something to fingerprint (phase-6 decision 10)."""
    init(tmp_path)

    document = manifest_of(tmp_path)

    assert set(document) == {
        "schema_version",
        "target",
        "family",
        "channel",
        "adapter",
        "prompts",
        "connector",
        "tools",
        "judge_notes",
        "suites",
    }
    assert document["prompts"] == {"system": "observed"}
    assert (document["family"], document["channel"]) == ("northwind", "chat")
    assert document["connector"] == {
        "kind": "inprocess",
        "environments": {"local": {"deployed": "agentdiag.examples.toy:deployed_set"}},
    }
    assert document["judge_notes"] == "judge_notes.md"
    assert document["schema_version"] == 1
    assert document["suites"] == ["suites/sample.yaml"]
    assert document["adapter"]["kind"] == "inprocess"
    assert document["adapter"]["side_effects"] == "none"
    assert document["adapter"]["environments"]["default"] == "local"


def test_the_toy_scaffold_marks_the_lookup_tool_retrieval_and_the_action_tool_action(
    tmp_path: Path,
) -> None:
    """Ticket 04: the kind the Adapter records the toy's tool Spans as (D37, ADR-0006 §2)."""
    init(tmp_path)

    assert manifest_of(tmp_path)["tools"] == {
        "lookup_order": {"kind": "retrieval"},
        "cancel_order": {"kind": "action"},
    }


def test_a_custom_target_gets_the_connector_block_as_a_comment_not_a_guess(
    tmp_path: Path,
) -> None:
    """Decision 22: `init` has never run a custom Target, so it cannot know its deployed
    set; the block is shown, commented, and the probe stays the deployed side."""
    result = init(tmp_path, "--adapter", "python:tests.fakes.idle_target:make_target")
    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]

    text = (target_dir(tmp_path) / "manifest.yaml").read_text(encoding="utf-8")

    assert "connector" not in manifest_of(tmp_path)
    assert "# connector:\n#   kind: inprocess\n" in text


def test_a_custom_target_gets_no_tools_section_only_a_comment_showing_one(
    tmp_path: Path,
) -> None:
    """`init` has never run a custom Target, so it does not guess which tools look things up."""
    result = init(tmp_path, "--adapter", "python:tests.fakes.idle_target:make_target")

    assert result.exit_code == 0, result.output
    assert "tools" not in manifest_of(tmp_path)
    assert "# tools:" in (target_dir(tmp_path) / "manifest.yaml").read_text(encoding="utf-8")


def test_the_scaffolded_files_carry_the_comments_that_explain_them(tmp_path: Path) -> None:
    """A skeleton nobody can read is a skeleton nobody edits: the comments are the point."""
    init(tmp_path)

    manifest = (target_dir(tmp_path) / "manifest.yaml").read_text(encoding="utf-8")
    suite = (target_dir(tmp_path) / "suites" / "sample.yaml").read_text(encoding="utf-8")

    assert "Pointers, not copies" in manifest
    assert "the only thing that touches a Target" in manifest
    assert "One Suite" in suite


def test_the_checked_in_example_is_what_the_template_renders_byte_for_byte() -> None:
    """`examples/toy/.agentdiag/` is regenerated from `EXAMPLE_SCAFFOLD`, and this is the gate.

    Bytes, not parsed content: the comments are most of what `init` writes, and a parsed
    comparison would let them rot while the keys still matched. The example differs from
    what `init` writes only in the two places `EXAMPLE_SCAFFOLD` names — the Suite's
    filename and the `--root` its comment cites — and in the Scenarios it adds after
    `EXAMPLE_ONLY`, which `init`'s one-Scenario sample does not carry (ticket 04).
    """
    assert render_manifest(EXAMPLE_SCAFFOLD) == (EXAMPLE / "manifest.yaml").read_text(
        encoding="utf-8"
    )
    example = (EXAMPLE / "suites" / "orders.yaml").read_text(encoding="utf-8")
    rendered = render_suite(EXAMPLE_SCAFFOLD)
    assert example[: len(rendered)] == rendered
    assert example[len(rendered) :].startswith("\n" + EXAMPLE_ONLY)


def test_the_example_directory_holds_only_the_files_init_writes() -> None:
    """One gitignore convention: the Runs line goes in the Target's own `.gitignore`.

    The Runs directory and the index are left out of the listing: running the README example
    writes Runs under `examples/toy/.agentdiag/targets/toy-order-desk/runs/` and indexes them
    in `examples/toy/.agentdiag/index.sqlite` (both gitignored), and doing what the README
    says must never break the suite. The Workspace holds the one Target and nothing else
    (ticket 13 moved it under `targets/`).
    """
    workspace = EXAMPLE.parents[1]
    assert sorted(path.name for path in workspace.iterdir() if path.name not in DERIVED) == [
        "targets"
    ]
    assert [path.name for path in (workspace / "targets").iterdir()] == ["toy-order-desk"]
    assert sorted(path.name for path in EXAMPLE.iterdir() if path.name not in DERIVED) == [
        "judge_notes.md",
        "manifest.yaml",
        "suites",
    ]


# --- the calibration notes starter (ticket 05, ADR-0003 §8) ---


def test_init_writes_a_calibration_notes_starter_the_manifest_names(tmp_path: Path) -> None:
    init(tmp_path)

    notes = target_dir(tmp_path) / "judge_notes.md"
    assert notes.is_file()
    assert manifest_of(tmp_path)["judge_notes"] == "judge_notes.md"


def test_the_notes_starter_carries_the_guidance_as_a_comment_the_judge_never_reads(
    tmp_path: Path,
) -> None:
    """The heading comment is the guidance summary: what belongs, the budget, and the
    self-preference note D23 asks authors to make."""
    init(tmp_path)

    text = (target_dir(tmp_path) / "judge_notes.md").read_text(encoding="utf-8")
    assert text.startswith("<!--")
    assert text.rstrip().endswith("-->")
    assert "false-fail patterns" in text
    assert f"At most {JUDGE_NOTES_MAX_WORDS} words" in text
    assert "same model" in text
    assert "docs/judge-notes.md" in text


def test_the_example_notes_are_what_init_writes_byte_for_byte() -> None:
    assert render_judge_notes(EXAMPLE_SCAFFOLD) == (EXAMPLE / "judge_notes.md").read_text(
        encoding="utf-8"
    )


def test_force_keeps_the_calibration_notes_an_author_wrote(tmp_path: Path) -> None:
    """Notes are what an author learned about judging the Target: never a scaffold to redo."""
    init(tmp_path)
    notes = target_dir(tmp_path) / "judge_notes.md"
    notes.write_text("A refund amount the cancel tool returned is data, not an invention.\n")

    result = init(tmp_path, "--force")

    assert result.exit_code == 0, result.stdout
    assert (
        notes.read_text() == "A refund amount the cancel tool returned is data, not an invention.\n"
    )


def test_the_scaffold_and_the_checked_in_example_say_the_same_thing(tmp_path: Path) -> None:
    """What `init` writes into a fresh directory loads as the example's Target and Suite."""
    init(tmp_path)

    scaffolded, _ = load_suite(target_dir(tmp_path) / "suites" / "sample.yaml")
    example, _ = load_suite(EXAMPLE / "suites" / "orders.yaml")

    example_manifest = manifest_in(EXAMPLE.parents[2])
    assert manifest_in(tmp_path).adapter == example_manifest.adapter
    assert manifest_in(tmp_path).target == example_manifest.target
    assert manifest_in(tmp_path).tools == example_manifest.tools
    assert (manifest_in(tmp_path).family, manifest_in(tmp_path).channel) == ("northwind", "chat")
    assert scaffolded.target == example.target
    assert scaffolded.scenarios[0] == example.scenarios[0]


def test_the_default_scaffold_points_at_the_toy_target_so_a_run_needs_no_editing(
    tmp_path: Path,
) -> None:
    init(tmp_path)

    local = manifest_of(tmp_path)["adapter"]["environments"]["local"]

    assert local["factory"] == "agentdiag.examples.toy:make_target"
    assert local["tools"] == "agentdiag.examples.toy:make_tools"
    assert local["model"] == "claude-sonnet-5"
    assert [scenario["id"] for scenario in suite_of(tmp_path)["scenarios"]] == [TOY_SCENARIO]


# --- a value a caller typed never changes what the Manifest says (code review, fix 1) ---


def local_environment(root: Path) -> dict:
    return manifest_of(root)["adapter"]["environments"]["local"]


def test_a_model_holding_a_colon_round_trips_instead_of_becoming_a_mapping(
    tmp_path: Path,
) -> None:
    """`model: a: b` is not YAML; quoted, it is the string the caller typed."""
    result = init(
        tmp_path, "--adapter", "python:tests.fakes.idle_target:make_target", "--model", "a: b"
    )

    assert result.exit_code == 0, result.stdout
    assert manifest_in(tmp_path).adapter.environments["local"]["model"] == "a: b"


def test_a_model_holding_a_hash_is_not_silently_truncated(tmp_path: Path) -> None:
    """Unquoted, everything from the `#` would be read as a comment and lost."""
    init(
        tmp_path, "--adapter", "python:tests.fakes.idle_target:make_target", "--model", "gpt # real"
    )

    assert local_environment(tmp_path)["model"] == "gpt # real"


def test_a_tools_reference_holding_two_colons_round_trips(tmp_path: Path) -> None:
    init(
        tmp_path,
        "--adapter",
        "python:tests.fakes.idle_target:make_target",
        "--tools",
        "a:b:  c",
    )

    assert local_environment(tmp_path)["tools"] == "a:b:  c"


def test_a_value_spanning_two_lines_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    """A newline would inject a key of its own; there is no honest one-line form."""
    result = init(
        tmp_path,
        "--adapter",
        "python:tests.fakes.idle_target:make_target",
        "--model",
        "a\nname: mine",
    )

    assert result.exit_code == 3
    assert not (tmp_path / ".agentdiag").exists(), "a refused value leaves no half-scaffold"


def test_a_plain_value_is_still_written_plainly(tmp_path: Path) -> None:
    """Quoting only where it is needed: the common Manifest still reads as prose."""
    init(tmp_path)

    assert "  model: claude-sonnet-5\n" in (
        (target_dir(tmp_path) / "manifest.yaml").read_text(encoding="utf-8")
    )


# --- a typo in --root is a message, not a tree of empty directories (code review, fix 5) ---


def test_init_creates_the_root_it_was_given(tmp_path: Path) -> None:
    root = tmp_path / "my-target"

    result = init(root)

    assert result.exit_code == 0, result.stdout
    assert (target_dir(root) / "manifest.yaml").is_file()


def test_a_root_whose_parent_does_not_exist_is_refused_by_name(tmp_path: Path) -> None:
    missing = tmp_path / "typo" / "my-target"

    result = init(missing)

    assert result.exit_code == 3
    assert str(missing.parent) in result.stdout + result.stderr
    assert not missing.exists()
    assert not missing.parent.exists(), "nothing was created on the way to the refusal"


# --- the gitignore line, written once ---


def test_init_creates_a_gitignore_when_the_directory_has_none(tmp_path: Path) -> None:
    init(tmp_path)

    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()[
        -len(GITIGNORED) :
    ] == GITIGNORED


def test_init_appends_to_an_existing_gitignore_without_disturbing_it(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")

    init(tmp_path)

    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "__pycache__/"
    assert all(line in lines for line in GITIGNORED)


def test_a_second_init_does_not_write_the_gitignore_lines_twice(tmp_path: Path) -> None:
    """Runs and their index are output; saying so twice is noise to clean up by hand."""
    init(tmp_path)

    init(tmp_path, "--force")

    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert [lines.count(line) for line in GITIGNORED] == [1] * len(GITIGNORED)


def test_init_adds_the_workspace_lines_to_a_phase_4_stanza_and_keeps_its_runs_line(
    tmp_path: Path,
) -> None:
    """A gitignore an older `init` wrote holds the Phase 4 Runs line alone; the Workspace's
    lines join its stanza once, and the old line stays (idempotent per line)."""
    older = "__pycache__/\n\n# Runs are output, not source: regenerated by `agentdiag run`.\n"
    (tmp_path / ".gitignore").write_text(older + ".agentdiag/runs/\n", encoding="utf-8")

    init(tmp_path)
    init(tmp_path, "--force")

    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert [lines.count(line) for line in GITIGNORED] == [1] * len(GITIGNORED)
    assert lines[-len(GITIGNORED) - 1 :] == [".agentdiag/runs/", *GITIGNORED], (
        "they join the Runs line's stanza"
    )
    assert [line for line in lines if line.startswith("#")] == [
        "# Runs are output, not source: regenerated by `agentdiag run`."
    ], "one stanza, one comment"


def test_a_fresh_gitignore_says_why_its_lines_are_there(tmp_path: Path) -> None:
    init(tmp_path)

    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == (
        "# Runs, Restore points, in-process platform stores and the index are output, not "
        "source: written by `agentdiag run`, a push and `agentdiag index rebuild`.\n"
        + "".join(f"{line}\n" for line in GITIGNORED)
    )


# --- refusing to overwrite (ticket 02) ---


def test_init_refuses_an_existing_target_directory_and_names_it(tmp_path: Path) -> None:
    init(tmp_path)
    (target_dir(tmp_path) / "manifest.yaml").write_text("mine: yes\n", encoding="utf-8")

    result = init(tmp_path)

    assert result.exit_code == 3
    assert str(target_dir(tmp_path)) in result.stdout + result.stderr
    assert manifest_of(tmp_path) == {"mine": True}


def test_force_rewrites_the_scaffold_and_leaves_existing_runs_untouched(tmp_path: Path) -> None:
    """A Run is immutable (ADR-0005 §2): re-scaffolding is not permission to delete one."""
    init(tmp_path)
    (target_dir(tmp_path) / "manifest.yaml").write_text("mine: yes\n", encoding="utf-8")
    run_dir = target_dir(tmp_path) / "runs" / "20260922T101500Z-k7pq"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text("{}", encoding="utf-8")

    result = init(tmp_path, "--force")

    assert result.exit_code == 0, result.stdout
    assert manifest_of(tmp_path)["target"]["name"] == "toy-order-desk"
    assert (run_dir / "run.json").read_text(encoding="utf-8") == "{}"


# --- a Target of the developer's own ---


def test_adapter_points_the_manifest_at_the_named_factory(tmp_path: Path) -> None:
    result = init(tmp_path, "--adapter", "python:tests.fakes.idle_target:make_target")

    assert result.exit_code == 0, result.stdout
    local = manifest_of(tmp_path)["adapter"]["environments"]["local"]
    assert local["factory"] == "tests.fakes.idle_target:make_target"
    assert "tools" not in local, "no --tools was given, so the Manifest claims no tools"
    assert local["model"] == "claude-sonnet-5"


def test_a_custom_target_is_named_after_the_factorys_module(tmp_path: Path) -> None:
    init(tmp_path, "--adapter", "python:tests.fakes.idle_target:make_target")

    assert manifest_of(tmp_path)["target"]["name"] == "idle-target"


def test_a_target_whose_factory_module_is_the_package_is_named_after_its_directory(
    tmp_path: Path,
) -> None:
    """`package:make_target` names no module of its own, so the directory is the readable name."""
    root = tmp_path / "My Order Desk"
    root.mkdir()

    init(root, "--adapter", "python:tests:make_target")

    assert manifest_of(root)["target"]["name"] == "my-order-desk"


def test_tools_and_model_are_written_when_they_are_given(tmp_path: Path) -> None:
    init(
        tmp_path,
        "--adapter",
        "python:tests.fakes.idle_target:make_target",
        "--tools",
        "tests.fakes.idle_target:TOOLS",
        "--model",
        "claude-opus-5",
    )

    local = manifest_of(tmp_path)["adapter"]["environments"]["local"]
    assert local["tools"] == "tests.fakes.idle_target:TOOLS"
    assert local["model"] == "claude-opus-5"


def test_the_sample_scenario_for_a_custom_target_is_one_turn_to_replace(tmp_path: Path) -> None:
    init(tmp_path, "--adapter", "python:tests.fakes.idle_target:make_target")

    document = suite_of(tmp_path)
    scenario = document["scenarios"][0]

    assert scenario["id"] == SAMPLE_SCENARIO
    assert scenario["title"] == "The Target answers a first message"
    assert scenario["turns"] == ["Hello! What can you help me with today?"]
    assert scenario["evals"] == [{"eval": "prompt_adherence"}]
    assert "Replace this Scenario with one of your own" in (
        (target_dir(tmp_path) / "suites" / "sample.yaml").read_text(encoding="utf-8")
    )


def greeting_recording(tmp_path: Path) -> Path:
    """The idle fake's recorded exchange, re-keyed to the sample Scenario's Turn.

    The committed fake recordings were made for a bare "Hello"; the sample Scenario says
    more than that, and replay matches on the exact request body, so the one field that
    differs is substituted here rather than a near-duplicate fixture being committed.
    """
    exchange = json.loads(RECORDINGS.joinpath("fake-idle.jsonl").read_text(encoding="utf-8"))
    exchange["request"]["messages"][0]["content"] = "Hello! What can you help me with today?"
    path = tmp_path / "greeting.jsonl"
    path.write_text(json.dumps(exchange) + "\n", encoding="utf-8")
    return path


def test_a_scaffolded_custom_target_is_driven_by_run(tmp_path: Path) -> None:
    """The point of `--adapter`: what `init` wrote is a Manifest `run` can already use."""
    root = tmp_path / "target"
    root.mkdir()
    init(root, "--adapter", "python:tests.fakes.idle_target:make_target")

    result = runner.invoke(
        app,
        [
            "run",
            "--root",
            str(root),
            "--scenario",
            SAMPLE_SCENARIO,
            "--replay",
            str(greeting_recording(tmp_path)),
        ],
    )

    # The fake never answers, so its Score is not a `pass`; what is under test is that the
    # Trial ran at all — the Target was built, driven, and its Trace written.
    assert (only_run(root) / "trials" / SAMPLE_SCENARIO / "1" / "trace.jsonl").is_file()
    assert result.exit_code in (0, 1, 2), result.stdout


# --- init, run, show: the five-minute path (ticket 02) ---


def test_init_then_run_then_show_all_work_in_a_fresh_directory(tmp_path: Path) -> None:
    """`run` works by exiting 1: the scaffolded toy's cancel Trial is the Judge's fail on
    rule 3 (`tests/stories.py`), and a fail is a Verdict, not a tool error."""
    root = tmp_path / "my-target"
    root.mkdir()

    initialised = init(root)
    ran = runner.invoke(
        app,
        [
            "run",
            "--root",
            str(root),
            "--scenario",
            TOY_SCENARIO,
            "--replay",
            str(TOY_RECORDING),
        ],
    )
    shown = runner.invoke(app, ["show", only_run(root).name, TOY_SCENARIO, "--root", str(root)])

    assert initialised.exit_code == 0, initialised.stdout
    assert ran.exit_code == CANCEL_EXIT, ran.stdout
    counts = next(line for line in ran.stdout.splitlines() if "pass rate" in line)
    assert counts.startswith(CANCEL_COUNTS)
    assert shown.exit_code == 0, shown.stdout


def test_a_run_in_a_scaffolded_directory_records_sync_not_checked(tmp_path: Path) -> None:
    """ADR-0005 §9: no Fingerprint yet, and the Run says so rather than implying one."""
    init(tmp_path)
    runner.invoke(
        app,
        [
            "run",
            "--root",
            str(tmp_path),
            "--scenario",
            TOY_SCENARIO,
            "--replay",
            str(TOY_RECORDING),
        ],
    )

    record = json.loads((only_run(tmp_path) / "run.json").read_text(encoding="utf-8"))

    assert record["sync"] == {
        "status": "not_checked",
        "reason": "no_fingerprint",
        "environment": "local",
        "fingerprint": None,
        "resynced_from": None,
        "sections": [],
        "covered_by": "connector",
        "connector_failed": None,
    }


# --- what init tells the reader to do next ---


def test_init_prints_the_paths_it_created_and_the_two_commands_that_follow(
    tmp_path: Path,
) -> None:
    result = init(tmp_path)

    assert ".agentdiag/targets/default/manifest.yaml" in result.stdout
    assert ".agentdiag/targets/default/suites/sample.yaml" in result.stdout
    assert ".gitignore" in result.stdout
    assert f"agentdiag run --scenario {TOY_SCENARIO}" in result.stdout
    assert f"agentdiag show <run> {TOY_SCENARIO}" in result.stdout


def test_init_points_at_the_ways_to_supply_credentials(tmp_path: Path) -> None:
    result = init(tmp_path)

    assert (
        "A judged Eval needs credentials: a Claude Code login (claude auth login) "
        "or export ANTHROPIC_API_KEY=…."
    ) in result.stdout.splitlines()


def test_the_cli_lists_every_command_this_phase_ships(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("init", "run", "show", "export", "list", "index"):
        assert command in result.stdout
