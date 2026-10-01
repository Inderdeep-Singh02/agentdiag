"""Seam 1: `agentdiag run` over a Run directory, with the toy Target in replay.

Every assertion here is on what a user can observe at the seam — a file in the Run
directory, a line of the summary, an exit code — never on how the modules underneath
arranged it (spec, Testing Decisions).
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.eval import prompt_adherence
from agentdiag.eval.prompt_adherence import PROMPT_VERSION
from agentdiag.eval.render import RecordedPrompt
from agentdiag.model import credentials
from agentdiag.model.claude_code import Backend, ClaudeCodeCli
from agentdiag.model.client import ReplayModelClient
from agentdiag.model.credentials import CredentialSource
from agentdiag.model.replay import Recording, ReplayCursor
from agentdiag.run.preflight import preflight
from agentdiag.scenario.models import Scenario
from agentdiag.scenario.select import Selection
from agentdiag.simulate import user
from agentdiag.simulate.configuration import simulated_user_configuration
from agentdiag.simulate.user import ModelSimulatedUser
from agentdiag.trace import TraceWriter, read_trace
from tests.fakes.workspace import the_target
from tests.stories import CANCEL_BROKEN_AT, CANCEL_COUNTS, CANCEL_EXIT, CANCEL_VERDICT

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
RECORDING = REPO / "tests" / "fixtures" / "recordings" / "toy-cancel.jsonl"
TARGET_ONLY_RECORDING = REPO / "tests" / "fixtures" / "recordings" / "toy-cancel-target.jsonl"
"""The Target's three exchanges without the Judge's: what a Run with no judged Eval wants."""

MISMATCHED_RECORDING = REPO / "tests" / "fixtures" / "recordings" / "fake-idle.jsonl"
TRACE_FIXTURE = REPO / "tests" / "fixtures" / "traces" / "cancel-processing-order.trace.jsonl"

SCENARIO = "cancel-processing-order"
RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z-[a-z2-7]{4}$")
"""A creation timestamp and four lowercase base32 characters (ADR-0005 section 2)."""

runner = CliRunner()

EXPECTED_COUNTS = {"pass": 0, "fail": 1, "incomplete": 0, "unverifiable": 0, "invalid": 0}
"""The five Verdicts the example Run produces, written out rather than derived: the cancel
Trial's one Score is the Judge's fail (`tests/stories.py`)."""


def target_root(tmp_path: Path) -> Path:
    """A copy of the shipped example, so a Run never writes into the repository.

    `runs/` is left behind: the example ships none, and a stray one from a developer's own
    `agentdiag run` must not make "how many Runs did this produce" unanswerable.
    """
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs"))
    return root


def invoke(root: Path, *arguments: str, replay: Path | None = RECORDING) -> object:
    command = ["run", "--root", str(root), *arguments]
    if replay is not None:
        command += ["--replay", str(replay)]
    return runner.invoke(app, command)


def without_evals(root: Path) -> Path:
    """The example with no Evals on its first Scenario, `cancel-processing-order`."""
    suite = root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "orders.yaml"
    document = yaml.safe_load(suite.read_text(encoding="utf-8"))
    document["scenarios"][0].pop("evals")
    suite.write_text(yaml.safe_dump(document), encoding="utf-8")
    return root


def only_run(root: Path) -> Path:
    runs = sorted((root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir())
    assert len(runs) == 1, f"expected one Run directory, found {[r.name for r in runs]}"
    return runs[0]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def trace_shape(path: Path) -> list[tuple[str, str | None]]:
    """A Trace as (type, span_id) per line: the run id and the timestamps differ."""
    return [
        (event["type"], event["span_id"])
        for event in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    ]


# --- the shipped example, end to end ---


def test_the_cli_lists_run(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "run" in result.stdout


def test_a_run_of_the_example_scenario_fails_its_judged_eval_and_exits_1(
    tmp_path: Path,
) -> None:
    """The whole tracer bullet in one assertion: one Scenario, judged, and its Score the
    Judge's fail on rule 3 (`tests/stories.py`). D32: any `fail` is exit 1, and it is
    louder than every abnormal Verdict."""
    root = target_root(tmp_path)

    result = invoke(root, "--scenario", SCENARIO)

    assert result.exit_code == CANCEL_EXIT, result.stdout
    counts_line = next(line for line in result.stdout.splitlines() if "pass rate" in line)
    assert counts_line.startswith(CANCEL_COUNTS)


def test_a_run_creates_one_directory_named_by_creation_time_and_a_random_suffix(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)

    invoke(root, "--scenario", SCENARIO)

    assert RUN_ID_PATTERN.match(only_run(root).name)


def test_the_run_record_freezes_the_configuration_the_run_executed_under(tmp_path: Path) -> None:
    root = target_root(tmp_path)

    invoke(root, "--scenario", SCENARIO)
    record = read_json(only_run(root) / "run.json")

    assert (record["sync"]["status"], record["sync"]["reason"]) == ("not_checked", "no_fingerprint")
    assert record["fingerprint"] is None
    assert record["trials"] == 1
    assert record["adapter"]["kind"] == "inprocess"
    assert record["adapter"]["side_effects"] == "none"
    # The Target's calls took the recording's path too (ticket 20, decision 28).
    assert record["adapter"]["backend"] == {"kind": "replay", "cli_version": None}
    assert record["selection"]["expression"] == f"scenario={SCENARIO}"
    assert record["scenarios"][0]["evals"] == [
        {
            "eval": "prompt_adherence",
            "id": None,
            "inherited": False,
            "params": {},
            "threshold": None,
        }
    ]


def test_the_run_record_freezes_the_judge_configuration_with_its_prompt(
    tmp_path: Path,
) -> None:
    """ADR-0005 section 3: the full prompt text, so a Score is never separated from it."""
    root = target_root(tmp_path)

    invoke(root, "--scenario", SCENARIO)
    judge = read_json(only_run(root) / "run.json")["judge"]

    assert judge["model"] == "claude-opus-5"
    assert judge["effort"] is None
    # A replayed Run's Judge took the recording's path, and says so (ticket 19).
    assert judge["backend"] == {"kind": "replay", "cli_version": None}
    prompt = judge["prompts"]["prompt_adherence"]
    assert prompt["version"] == PROMPT_VERSION
    assert prompt["fingerprint"] == prompt_adherence.PARTS.fingerprint()
    assert prompt["text"].startswith("You are the Judge in agentdiag")


def test_a_run_with_no_judged_eval_records_no_judge_configuration(tmp_path: Path) -> None:
    root = without_evals(target_root(tmp_path))

    invoke(root, "--scenario", SCENARIO, replay=TARGET_ONLY_RECORDING)

    assert read_json(only_run(root) / "run.json")["judge"] is None


def test_the_run_record_names_the_repository_each_git_state_came_from(tmp_path: Path) -> None:
    """A Target copied outside any repository has no git state; agentdiag always has one."""
    root = target_root(tmp_path)

    invoke(root, "--scenario", SCENARIO)
    git = read_json(only_run(root) / "run.json")["git"]

    assert git["agentdiag"]["repository"] == str(REPO)
    assert set(git["agentdiag"]) == {"repository", "commit", "dirty"}
    # tmp_path is outside any checkout, so the Target's state is honestly absent rather
    # than borrowed from whichever repository happened to be above it.
    assert git["target"] is None


def test_the_trial_trace_holds_the_same_sequence_as_the_committed_fixture(tmp_path: Path) -> None:
    """The Run's Trial loop is the one that regenerates the fixture (slice order, item 2)."""
    root = target_root(tmp_path)

    invoke(root, "--scenario", SCENARIO)
    trace = only_run(root) / "trials" / SCENARIO / "1" / "trace.jsonl"

    assert trace_shape(trace) == trace_shape(TRACE_FIXTURE)


def test_a_trial_with_a_judged_eval_leaves_a_judgement_file_beside_its_trace(
    tmp_path: Path,
) -> None:
    """ADR-0004 section 3: the Judge writes its own file and never touches the Trace."""
    root = target_root(tmp_path)

    invoke(root, "--scenario", SCENARIO)
    trial = only_run(root) / "trials" / SCENARIO / "1"
    judgement = [
        json.loads(line)
        for line in (trial / "judgement.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    # Two Judge Spans: the Eval's, then the Trial's Diagnosis (D24).
    spans = [event for event in judgement if event["type"] == "span/start"]
    assert [span["name"] for span in spans] == ["judge prompt_adherence", "judge diagnosis"]
    assert [event["type"] for event in judgement if event["type"].startswith("span")] == [
        "span/start",
        "span/end",
        "span/start",
        "span/end",
    ]
    assert {event["actor"] for event in judgement} == {"agentdiag", "judge"}
    assert not any(
        event["actor"] == "judge"
        for event in map(
            json.loads, (trial / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        )
    )


def test_a_trial_with_no_judged_eval_leaves_no_judgement_file(tmp_path: Path) -> None:
    """An empty one would render in `show` as a judgement that happened (ADR-0005 §2)."""
    root = without_evals(target_root(tmp_path))

    invoke(root, "--scenario", SCENARIO, replay=TARGET_ONLY_RECORDING)

    assert not (only_run(root) / "trials" / SCENARIO / "1" / "judgement.jsonl").exists()


def test_the_scorecard_counts_every_verdict_and_withholds_a_rate_it_cannot_compute(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)

    invoke(root, "--scenario", SCENARIO)
    scorecard = read_json(only_run(root) / "scorecard.json")

    assert scorecard["counts"] == EXPECTED_COUNTS
    assert scorecard["pass_rate"] == 0.0


def test_run_json_prints_the_scorecard_file_byte_for_byte(tmp_path: Path) -> None:
    root = target_root(tmp_path)

    result = invoke(root, "--scenario", SCENARIO, "--json")

    assert result.stdout == (only_run(root) / "scorecard.json").read_text(encoding="utf-8")
    # Byte-equality alone would hold if both were empty or both were wrong, so what the
    # JSON actually says is asserted against the literal, not against the other output.
    assert json.loads(result.stdout)["counts"] == EXPECTED_COUNTS


def test_the_summary_shows_the_counts_and_the_sync_status(tmp_path: Path) -> None:
    root = target_root(tmp_path)

    result = invoke(root, "--scenario", SCENARIO)

    counts_line = next(line for line in result.stdout.splitlines() if "pass rate" in line)
    assert counts_line.startswith(CANCEL_COUNTS)
    assert "unverifiable 0" in counts_line
    assert "sync not_checked (no_fingerprint)" in counts_line


def test_two_runs_produce_two_directories_and_neither_overwrites_the_other(
    tmp_path: Path,
) -> None:
    """A Run is immutable (ADR-0005 section 2): a second one never lands on the first."""
    root = target_root(tmp_path)

    invoke(root, "--scenario", SCENARIO)
    invoke(root, "--scenario", SCENARIO)

    runs = sorted((root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir())
    assert len(runs) == 2
    assert runs[0].name != runs[1].name
    assert all((run / "scorecard.json").is_file() for run in runs)


# --- preflight: one message, exit 3, and no Run directory (D32) ---


def test_an_unknown_scenario_id_is_named_and_nothing_is_written(tmp_path: Path) -> None:
    root = target_root(tmp_path)

    result = invoke(root, "--scenario", "no-such-scenario")

    assert result.exit_code == 3
    assert "no-such-scenario" in result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()


def test_a_missing_manifest_is_a_preflight_error(tmp_path: Path) -> None:
    root = target_root(tmp_path)
    (root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml").unlink()

    result = invoke(root, "--scenario", SCENARIO)

    assert result.exit_code == 3
    assert "manifest.yaml" in result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()


def test_an_adapter_that_causes_live_side_effects_is_refused_before_anything_runs(
    tmp_path: Path,
) -> None:
    """ADR-0001 point 5: agentdiag never touches a live system by accident."""
    root = target_root(tmp_path)
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["adapter"]["side_effects"] = "live"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    result = invoke(root, "--scenario", SCENARIO)

    assert result.exit_code == 3
    assert "live side effects" in result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()


# --- a Scenario that declares no Eval, and Trials that do not finish ---


def test_a_scenario_with_no_evals_asks_no_question_and_exits_0(tmp_path: Path) -> None:
    root = without_evals(target_root(tmp_path))

    result = invoke(root, "--scenario", SCENARIO, replay=TARGET_ONLY_RECORDING)

    assert result.exit_code == 0, result.stdout
    assert "pass 0  fail 0  incomplete 0  unverifiable 0  invalid 0" in result.stdout


def test_a_target_that_cannot_be_built_ends_the_trial_target_error_and_scores_incomplete(
    tmp_path: Path,
) -> None:
    """D22: a termination reason is a fact about the Trial, and this one is the Target's."""
    root = target_root(tmp_path)
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["adapter"]["environments"]["local"] = {
        "factory": "tests.fakes.broken_target:make_target",
        "tools": "tests.fakes.broken_target:make_tools",
    }
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    result = invoke(root, "--scenario", SCENARIO)

    assert result.exit_code == 2, result.stdout
    events = [
        json.loads(line)
        for line in (only_run(root) / "trials" / SCENARIO / "1" / "trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["type"] == "trace/end"
    assert events[-1]["termination"] == "target_error"
    assert any(event["type"] == "error" for event in events)

    score = read_json(only_run(root) / "trials" / SCENARIO / "1" / "scores.json")["scores"][0]
    assert score["verdict"] == "incomplete"
    assert score["reason"] == "target_error"


def test_a_recording_that_does_not_match_is_agentdiags_own_fault(tmp_path: Path) -> None:
    """A replay agentdiag cannot satisfy broke the test, so the Score is `invalid`."""
    root = target_root(tmp_path)

    result = invoke(root, "--scenario", SCENARIO, replay=MISMATCHED_RECORDING)

    assert result.exit_code == 2, result.stdout
    events = [
        json.loads(line)
        for line in (only_run(root) / "trials" / SCENARIO / "1" / "trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["termination"] == "agentdiag_error"

    score = read_json(only_run(root) / "trials" / SCENARIO / "1" / "scores.json")["scores"][0]
    assert score["verdict"] == "invalid"
    assert score["fault_source"] == "agentdiag"
    assert score["fault_direction"] == "none"


# --- what did not run is named as plainly as what did (D31, ADR-0005 sections 3 and 8) ---


def add_second_scenario(root: Path) -> str:
    """A second Scenario in the example Suite, so one can be selected and one left out."""
    suite = root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "orders.yaml"
    document = yaml.safe_load(suite.read_text(encoding="utf-8"))
    second = json.loads(json.dumps(document["scenarios"][0]))
    second["id"] = "refund-delivered-order"
    second["title"] = "Refund an order that already arrived"
    document["scenarios"].append(second)
    suite.write_text(yaml.safe_dump(document), encoding="utf-8")
    return second["id"]


def other_scenarios(root: Path) -> list[tuple[str, str]]:
    """Every (Suite, Scenario id) the root's Manifest loads except the one these tests
    select, in order: the orders Suite, then the guardrails Suite (ticket 05)."""
    left_out: list[tuple[str, str]] = []
    for name in ("orders", "guardrails"):
        suite = root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / f"{name}.yaml"
        document = yaml.safe_load(suite.read_text(encoding="utf-8"))
        left_out.extend(
            (name, scenario["id"])
            for scenario in document["scenarios"]
            if scenario["id"] != SCENARIO
        )
    return left_out


def test_a_loaded_but_unselected_scenario_is_listed_not_selected_in_the_run_record(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)
    add_second_scenario(root)

    invoke(root, "--scenario", SCENARIO)

    assert read_json(only_run(root) / "run.json")["not_run"] == [
        {
            "scenario": left_out,
            "suite": suite,
            "reason": "not_selected",
            "detail": None,
            "trial": None,
        }
        for suite, left_out in other_scenarios(root)
    ]


def test_a_loaded_but_unselected_scenario_is_listed_not_selected_in_the_scorecard(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)
    add_second_scenario(root)

    invoke(root, "--scenario", SCENARIO)

    assert read_json(only_run(root) / "scorecard.json")["not_run"] == [
        {
            "scenario": left_out,
            "suite": suite,
            "reason": "not_selected",
            "detail": None,
            "trial": None,
        }
        for suite, left_out in other_scenarios(root)
    ]


def test_the_summary_names_every_scenario_that_did_not_run(tmp_path: Path) -> None:
    """A pass rate is never shown without what it left out (ADR-0005 section 8)."""
    root = target_root(tmp_path)
    add_second_scenario(root)
    left_out = other_scenarios(root)

    result = invoke(root, "--scenario", SCENARIO)

    # Two Suites are loaded, so each line names its Suite: ids are unique only within one.
    for suite, scenario in left_out:
        assert f"{scenario}  suite {suite}  not run  not_selected" in result.stdout
    assert f"not run {len(left_out)}" in next(
        line for line in result.stdout.splitlines() if "pass rate" in line
    )


# --- load warnings reach the operator, and never stdout (D19, D29) ---


def test_an_unknown_eval_warns_on_stderr_and_leaves_stdout_machine_readable(
    tmp_path: Path,
) -> None:
    """The warning names an Eval the catalogue really does not hold: `prompt_adherence`
    is registered now, so warning about it would be a lie rather than a courtesy."""
    root = target_root(tmp_path)
    suite = root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "orders.yaml"
    document = yaml.safe_load(suite.read_text(encoding="utf-8"))
    document["scenarios"][0]["evals"] = [{"eval": "tone_of_voice"}]
    suite.write_text(yaml.safe_dump(document), encoding="utf-8")

    # No judged Eval runs, so no Judge exchange is wanted: the Target-only recording is
    # the one that matches, and the combined one would go unconsumed.
    result = invoke(root, "--scenario", SCENARIO, "--json", replay=TARGET_ONLY_RECORDING)

    assert "warning: unknown Eval 'tone_of_voice'" in result.stderr
    assert SCENARIO in result.stderr
    assert "warning" not in result.stdout
    assert json.loads(result.stdout)["counts"]["unverifiable"] == 1


def test_the_shipped_example_loads_with_no_warning_at_all(tmp_path: Path) -> None:
    root = target_root(tmp_path)

    result = invoke(root, "--scenario", SCENARIO, "--json")

    assert "warning" not in result.stderr


# --- an interrupt after the directory exists still leaves a readable Run ---


def test_a_target_that_raises_keyboardinterrupt_still_writes_the_scorecard(
    tmp_path: Path,
) -> None:
    """A Ctrl-C is a fact about the operator (D22): the Run stays readable and exits 2."""
    root = target_root(tmp_path)
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["adapter"]["environments"]["local"] = {
        "factory": "tests.fakes.interrupted_target:make_target",
        "tools": "tests.fakes.interrupted_target:make_tools",
    }
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    left_out = add_second_scenario(root)

    result = invoke(root, "--scenario", SCENARIO, "--scenario", left_out)

    assert result.exit_code == 2, result.stdout
    run_dir = only_run(root)
    assert (run_dir / "scorecard.json").is_file()

    scorecard = read_json(run_dir / "scorecard.json")
    assert scorecard["counts"]["incomplete"] == 1
    unselected = [scenario for _, scenario in other_scenarios(root) if scenario != left_out]
    assert {entry["scenario"]: entry["reason"] for entry in scorecard["not_run"]} == {
        **dict.fromkeys(unselected, "not_selected"),
        left_out: "cancelled",
    }

    events = [
        json.loads(line)
        for line in (run_dir / "trials" / SCENARIO / "1" / "trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["termination"] == "cancelled"


# --- preflight refuses a judged Eval it cannot reach a model for (D16) ---


def no_credentials(monkeypatch: object) -> None:
    """Nothing in the environment, and nothing on disk the SDK would find."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)  # type: ignore[attr-defined]
    monkeypatch.setattr("anthropic.default_credentials", lambda **_: None)  # type: ignore[attr-defined]


def test_a_judged_eval_with_no_credentials_and_no_replay_refuses_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D16: one message, exit 3, and no Run directory to mistake for a Run that measured."""
    root = target_root(tmp_path)
    no_credentials(monkeypatch)

    result = invoke(root, "--scenario", SCENARIO, replay=None)

    assert result.exit_code == 3, result.stdout
    assert "prompt_adherence in cancel-processing-order" in result.stdout
    assert "ANTHROPIC_API_KEY" in result.stdout
    assert "claude auth login" in result.stdout
    assert "ant auth login" in result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()


def test_a_replayed_run_needs_no_credentials_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recorded exchange is a model call that already happened (D12)."""
    root = target_root(tmp_path)
    no_credentials(monkeypatch)

    result = invoke(root, "--scenario", SCENARIO)

    assert result.exit_code == CANCEL_EXIT, result.stdout


def test_a_scenario_with_no_judged_eval_needs_no_credentials_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = without_evals(target_root(tmp_path))
    no_credentials(monkeypatch)

    result = invoke(root, "--scenario", SCENARIO, replay=None)

    # It fails for want of a model to drive the Target, not for want of a Judge: the
    # message must not mention credentials.
    assert "credentials" not in (result.stdout or "")


# --- credentials decide the Target's path too, once, in preflight (ticket 20, decision 29) ---


CLI = ClaudeCodeCli(path="/opt/claude/bin/claude", version="2.1.280")


def counted_resolve(monkeypatch: pytest.MonkeyPatch) -> list[None]:
    """`credentials.resolve` answering the Claude Code login, and a list that grows by one
    each time it is asked."""
    asked: list[None] = []

    def resolve() -> CredentialSource:
        asked.append(None)
        return CredentialSource(kind="claude_code", detail="Claude Code login (2.1.280)", cli=CLI)

    monkeypatch.setattr(credentials, "resolve", resolve)
    return asked


def test_a_live_preflight_resolves_the_credentials_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = counted_resolve(monkeypatch)

    preflight(the_target(target_root(tmp_path)), Selection(scenario=[SCENARIO]), None)

    assert len(asked) == 1


def test_a_live_preflight_with_no_judged_eval_still_resolves_the_credentials_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Target calls a model whatever the Evals are, so its Backend is decided either way."""
    root = without_evals(target_root(tmp_path))
    asked = counted_resolve(monkeypatch)

    plan = preflight(the_target(root), Selection(scenario=[SCENARIO]), None)

    assert len(asked) == 1
    assert plan.credentials is not None and plan.credentials.kind == "claude_code"


def test_a_dry_run_and_a_replay_never_resolve_the_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = target_root(tmp_path)
    asked = counted_resolve(monkeypatch)

    command = ["run", "--root", str(root), "--scenario", SCENARIO]
    dry = runner.invoke(app, [*command, "--dry-run"])
    replayed = runner.invoke(app, [*command, "--replay", str(RECORDING)])

    assert (dry.exit_code, replayed.exit_code) == (0, CANCEL_EXIT), (dry.stdout, replayed.stdout)
    assert asked == []


def test_with_the_claude_code_login_and_no_key_the_adapter_describes_the_claude_code_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_credentials(monkeypatch)
    monkeypatch.setattr(credentials, "claude_code_probe", lambda: CLI)

    plan = preflight(the_target(target_root(tmp_path)), Selection(scenario=[SCENARIO]), None)

    assert plan.description.backend == Backend(kind="claude_code", cli_version="2.1.280")
    assert plan.judge is not None
    assert plan.judge.backend == Backend(kind="claude_code", cli_version="2.1.280")


def test_with_nothing_resolving_the_adapter_describes_the_messages_api_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whether a Target needs Anthropic credentials is the Target's business: its calls
    still take the Messages API Backend, and fail there as they would have (decision 29)."""
    root = without_evals(target_root(tmp_path))
    no_credentials(monkeypatch)

    plan = preflight(the_target(root), Selection(scenario=[SCENARIO]), None)

    assert plan.description.backend == Backend(kind="anthropic_api", cli_version=None)


# --- a recording that answers the Target but not the Judge ---


def test_a_recording_without_the_judges_exchange_makes_the_judge_score_invalid(
    tmp_path: Path,
) -> None:
    """A ReplayMismatch inside the Judge is the Judge's fault source, not the Target's:
    the Target's Trial completed, and it is agentdiag's recording that fell short."""
    root = target_root(tmp_path)
    target_only = REPO / "tests" / "fixtures" / "recordings" / "toy-cancel-target.jsonl"

    result = invoke(root, "--scenario", SCENARIO, replay=target_only)

    assert result.exit_code == 2, result.stdout
    score = read_json(only_run(root) / "trials" / SCENARIO / "1" / "scores.json")["scores"][0]
    assert score["verdict"] == "invalid"
    assert score["fault_source"] == "judge"
    assert score["fault_direction"] == "none"


def recording_with(tmp_path: Path, *extra: dict) -> Path:
    """A copy of the toy recording with more exchanges appended."""
    path = tmp_path / "recording.jsonl"
    lines = RECORDING.read_text(encoding="utf-8").splitlines()
    lines += [json.dumps(exchange) for exchange in extra]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_an_unconsumed_recording_overrules_the_scores_without_losing_the_judgement(
    tmp_path: Path,
) -> None:
    """ADR-0003 section 7: what the Judge said is kept beside the Score, even when
    agentdiag's own recording turns out to have been wrong."""
    root = target_root(tmp_path)
    unwanted = {
        "request": {"model": "claude-sonnet-5", "max_tokens": 1, "messages": []},
        "response": {
            "id": "msg_unwanted",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    }

    result = invoke(root, "--scenario", SCENARIO, replay=recording_with(tmp_path, unwanted))

    assert result.exit_code == 2, result.stdout
    trial = only_run(root) / "trials" / SCENARIO / "1"

    score = read_json(trial / "scores.json")["scores"][0]
    assert score["verdict"] == "invalid"
    assert score["fault_source"] == "agentdiag"
    assert score["fault_direction"] == "none"
    # The Verdict the Judge reached is still readable, and so is where to check it.
    assert f"the judge scored {CANCEL_VERDICT}" in score["rationale"].lower()
    assert "judgement.jsonl" in score["rationale"]
    # Five: the Evals' side of the recording, which is what can overrule a Score; the
    # Diagnosis's exchange is asked about separately and never overrules one (D24).
    assert "1 of 5" in score["rationale"]

    judgement = [
        json.loads(line)
        for line in (trial / "judgement.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    types = [event["type"] for event in judgement]
    assert "request" in types and "response" in types
    assert types.count("trace/start") == 1, "the judgement file was reopened and rewritten"


def test_a_run_whose_judged_eval_passes_exits_0(tmp_path: Path) -> None:
    """The inverse of the example's fail: the worked pass over the same Trace
    (`judge-pass.jsonl`, decision 43) spliced in, every Score a pass, exit 0."""
    root = target_root(tmp_path)
    passing = json.loads(
        (REPO / "tests" / "fixtures" / "recordings" / "judge-pass.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )

    result = invoke(root, "--scenario", SCENARIO, replay=judge_swapped(tmp_path, passing))

    assert result.exit_code == 0, result.stdout
    counts_line = next(line for line in result.stdout.splitlines() if "pass rate" in line)
    assert "pass 1" in counts_line
    assert "fail 0" in counts_line


def test_a_failing_judged_eval_is_written_as_a_fail_that_cites_its_evidence(
    tmp_path: Path,
) -> None:
    """The example's one judged Eval, scored by a Judge, as its story and not its captured
    wording: the fail cites the closing reply it rests on, and nothing the Trace does not
    hold (ADR-0003 §4)."""
    root = target_root(tmp_path)

    invoke(root, "--scenario", SCENARIO)
    trial = only_run(root) / "trials" / SCENARIO / "1"
    scores = read_json(trial / "scores.json")
    assert (scores["scenario"], scores["trial"], len(scores["scores"])) == (SCENARIO, 1, 1)
    score = scores["scores"][0]

    assert score["eval"] == "prompt_adherence"
    assert score["verdict"] == CANCEL_VERDICT
    assert CANCEL_BROKEN_AT in score["evidence"]
    assert set(score["evidence"]) <= {
        event["span_id"]
        for event in map(
            json.loads, (trial / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        )
    }
    assert score["reason"] is None
    assert score["source"]["kind"] == "judge"


def judge_swapped(tmp_path: Path, exchange: dict) -> Path:
    """The Target's three exchanges with one Judge answer after them, in place of the
    shipped recording's Judge lines: the Diagnosis's exchange is left out, and a mismatch
    confined to the Diagnosis leaves the Scores as they are."""
    path = tmp_path / "judge-swapped.jsonl"
    lines = TARGET_ONLY_RECORDING.read_text(encoding="utf-8").splitlines()
    lines.append(json.dumps(exchange))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- the full schema at run time (ticket 03) ---


def remembering_root(tmp_path: Path, suite: dict) -> Path:
    """The example's Manifest pointed at a Target that remembers and calls no model."""
    root = target_root(tmp_path)
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["adapter"]["environments"]["local"] = {
        "factory": "tests.fakes.remembering_target:make_target",
        "tools": "tests.fakes.remembering_target:TOOLS",
    }
    manifest["suites"] = ["suites/orders.yaml"]  # this Suite in place of the example's two
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "orders.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "target": "toy-order-desk", **suite}),
        encoding="utf-8",
    )
    return root


def trace_events(run_dir: Path, scenario: str) -> list[dict]:
    path = run_dir / "trials" / scenario / "1" / "trace.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def replies(run_dir: Path, scenario: str) -> list[str]:
    return [
        event["content"]
        for event in trace_events(run_dir, scenario)
        if event["type"] == "message" and event["actor"] == "target"
    ]


CONTINUED = {
    "fixtures": {"carmen": {"kind": "identity", "name": "Carmen"}},
    "scenarios": [
        {
            "id": "ask-about-order",
            "title": "Ask about an order",
            "fixtures": ["carmen"],
            "turns": ["Where is NB-1042?"],
        },
        {
            "id": "then-cancel-it",
            "title": "Then cancel it in the same conversation",
            "continues": "ask-about-order",
            "turns": ["Cancel it, please."],
        },
    ],
}


def test_the_suites_fixtures_reach_the_target_and_are_recorded_as_applied(
    tmp_path: Path,
) -> None:
    """ADR-0001 §6: a Fixture the Trace does not show cannot be cited when a Score blames it."""
    root = remembering_root(tmp_path, CONTINUED)

    result = invoke(root, "--scenario", "ask-about-order", replay=None)

    assert result.exit_code == 0, result.stdout
    run_dir = only_run(root)
    applied = [
        e for e in trace_events(run_dir, "ask-about-order") if e["type"] == "fixture/applied"
    ]
    assert [(e["fixture"], e["actor"], e["detail"]) for e in applied] == [
        ("carmen", "adapter", {"kind": "identity", "name": "Carmen"})
    ]
    assert "given carmen={'name': 'Carmen'}" in replies(run_dir, "ask-about-order")[0]


def test_a_continuing_scenario_resumes_the_session_of_the_scenario_it_continues(
    tmp_path: Path,
) -> None:
    """Decision 13: one conversation, two Trials, two Trace files."""
    root = remembering_root(tmp_path, CONTINUED)

    # Named in the opposite order: the continued Scenario still runs first.
    result = invoke(
        root, "--scenario", "then-cancel-it", "--scenario", "ask-about-order", replay=None
    )

    assert result.exit_code == 0, result.stdout
    run_dir = only_run(root)
    assert replies(run_dir, "ask-about-order")[0].startswith("heard 1: Where is NB-1042?")
    assert replies(run_dir, "then-cancel-it")[0].startswith(
        "heard 2: Where is NB-1042? | Cancel it, please."
    )
    start = trace_events(run_dir, "then-cancel-it")[0]
    assert start["type"] == "trace/start"
    assert start["continues"] == "ask-about-order"
    assert "continues" not in trace_events(run_dir, "ask-about-order")[0]
    # The Fixtures were applied once, when the continued Scenario opened the session.
    assert not [
        e for e in trace_events(run_dir, "then-cancel-it") if e["type"] == "fixture/applied"
    ]


def test_show_says_which_scenario_a_trial_continues(tmp_path: Path) -> None:
    root = remembering_root(tmp_path, CONTINUED)
    invoke(root, "--scenario", "ask-about-order", "--scenario", "then-cancel-it", replay=None)
    run_dir = only_run(root)

    result = runner.invoke(app, ["show", str(run_dir), "then-cancel-it"])

    assert result.exit_code == 0, result.output
    assert "Continues ask-about-order (the same Adapter session)" in result.stdout


def test_a_continuing_scenario_selected_without_the_one_it_continues_is_refused(
    tmp_path: Path,
) -> None:
    root = remembering_root(tmp_path, CONTINUED)

    result = invoke(root, "--scenario", "then-cancel-it", replay=None)

    assert result.exit_code == 3, result.stdout
    assert "'then-cancel-it' continues 'ask-about-order'" in result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()


SIMULATED = {
    "scenarios": [
        {"id": "ask-about-order", "title": "Ask about an order", "turns": ["Where is NB-1042?"]},
        {
            "id": "cancel-adaptively",
            "title": "Cancel, the customer improvising",
            "turns": [
                "Hi, I want to cancel something.",
                {"simulate": {"goal": "Get NB-1042 cancelled.", "stop_token": "[DONE]"}},
            ],
            "max_turns": 4,
        },
    ]
}


def simulated_user_recording(tmp_path: Path, answer: str) -> Path:
    """The one exchange the Simulated User makes after `cancel-adaptively`'s opener, answered
    `answer`: its request rendered by the Simulated User itself over the Trial so far, as the
    remembering Target will have answered, so the key is the one the Run will send."""
    scenario = next(
        Scenario.model_validate(entry)
        for entry in SIMULATED["scenarios"]
        if entry["id"] == "cancel-adaptively"
    )
    assert scenario.simulate is not None
    writer = TraceWriter(tmp_path / "so-far.jsonl")
    writer.start(trace_id="r/s/1", scenario=scenario.id, run="r", trial=1)
    with writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented") as turn:
        opener = scenario.literal_turns[0]
        turn.event("message", actor="agentdiag", role="user", content=opener)
        heard = f"heard 1: {opener}; given nothing"
        turn.event("message", actor="target", role="assistant", content=heard)
    configuration = simulated_user_configuration(RecordedPrompt.of(user.PARTS))
    request = ModelSimulatedUser(
        ReplayModelClient(ReplayCursor(Recording(path=tmp_path, exchanges=()))),
        configuration,
        scenario.simulate,
        scenario,
    ).request(read_trace(writer.path))
    writer.close()
    response = {
        "id": "msg_su",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5",
        "content": [{"type": "text", "text": json.dumps({"message": answer})}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 900, "output_tokens": 5},
    }
    path = tmp_path / "simulated-user.jsonl"
    path.write_text(json.dumps({"request": request.body(), "response": response}) + "\n")
    return path


def test_a_selected_simulate_scenario_runs_its_simulated_turns_until_the_user_stops(
    tmp_path: Path,
) -> None:
    """Decision 14 is retired (ticket 06): the Scenario validates, loads and now runs."""
    root = remembering_root(tmp_path, SIMULATED)
    recording = simulated_user_recording(tmp_path, "[DONE]")

    result = invoke(root, "--scenario", "cancel-adaptively", replay=recording)

    assert result.exit_code == 0, result.stdout
    events = trace_events(only_run(root), "cancel-adaptively")
    assert (events[-1]["termination"], events[-1]["detail"]) == (
        "stop_token",
        "the Simulated User answered the stop token after Turn 1",
    )
    assert replies(only_run(root), "cancel-adaptively") == [
        "heard 1: Hi, I want to cancel something.; given nothing"
    ]


def test_a_live_run_of_a_simulate_scenario_with_no_credentials_is_refused_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D16, decision 49: the Simulated User needs a model as a judged Eval does."""
    root = remembering_root(tmp_path, SIMULATED)
    no_credentials(monkeypatch)

    result = invoke(root, "--scenario", "cancel-adaptively", replay=None)

    assert result.exit_code == 3, result.stdout
    assert "simulate in cancel-adaptively" in result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()


def test_an_unselected_simulate_scenario_loads_and_the_run_proceeds(tmp_path: Path) -> None:
    root = remembering_root(tmp_path, SIMULATED)

    result = invoke(root, "--scenario", "ask-about-order", replay=None)

    assert result.exit_code == 0, result.stdout
    record = read_json(only_run(root) / "run.json")
    assert record["not_run"] == [
        {
            "scenario": "cancel-adaptively",
            "suite": "orders",
            "reason": "not_selected",
            "detail": None,
            "trial": None,
        }
    ]


def test_run_json_carries_ground_truth_provenance_and_notes_and_show_prints_them(
    tmp_path: Path,
) -> None:
    root = remembering_root(
        tmp_path,
        {
            "scenarios": [
                {
                    "id": "ask-about-order",
                    "title": "Ask about an order",
                    "provenance": "chat 33457e85",
                    "notes": "A pass does not prove the prod bug is fixed.",
                    "ground_truth": {"status": "processing"},
                    "turns": ["Where is NB-1042?"],
                }
            ]
        },
    )
    invoke(root, "--scenario", "ask-about-order", replay=None)
    run_dir = only_run(root)

    summary = read_json(run_dir / "run.json")["scenarios"][0]
    assert summary["provenance"] == "chat 33457e85"
    assert summary["notes"] == "A pass does not prove the prod bug is fixed."
    assert summary["ground_truth"] == {"status": "processing"}

    shown = runner.invoke(app, ["show", str(run_dir), "ask-about-order"]).stdout
    assert "provenance    chat 33457e85" in shown
    assert "notes         A pass does not prove the prod bug is fixed." in shown
    assert 'ground truth  {"status": "processing"}' in shown


def test_every_judged_eval_asks_for_credentials_once_its_prompt_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ticket 05 landed `goal`'s prompt, so D16 now asks credentials for it too, and names
    the declaration a reader has to change."""
    root = remembering_root(
        tmp_path,
        {
            "scenarios": [
                {
                    "id": "ask-about-order",
                    "title": "Ask about an order",
                    "turns": ["Where is NB-1042?"],
                    "evals": [{"goal": "The customer learns the order's status."}],
                }
            ]
        },
    )
    no_credentials(monkeypatch)

    result = invoke(root, "--scenario", "ask-about-order", replay=None)

    assert result.exit_code == 3, result.stdout
    assert "goal in ask-about-order" in result.stdout
    assert not (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").exists()


def test_a_suite_level_eval_is_scored_on_every_scenario_that_inherits_it(tmp_path: Path) -> None:
    root = remembering_root(
        tmp_path,
        {
            "evals": [{"forbid_tools": ["cancel_order"]}],
            "scenarios": [
                {"id": "ask-about-order", "title": "Ask", "turns": ["Where is NB-1042?"]},
                {
                    "id": "opted-out",
                    "title": "Opted out",
                    "turns": ["Hello"],
                    "inherit_suite_evals": False,
                },
            ],
        },
    )

    invoke(root, "--scenario", "ask-about-order", "--scenario", "opted-out", replay=None)

    run_dir = only_run(root)
    inherited = read_json(run_dir / "trials" / "ask-about-order" / "1" / "scores.json")
    opted_out = read_json(run_dir / "trials" / "opted-out" / "1" / "scores.json")
    assert [score["eval"] for score in inherited["scores"]] == ["forbid_tools"]
    assert opted_out["scores"] == []
