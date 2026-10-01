"""Seam 1: `agentdiag show` over a Run directory, as a reader sees it.

Every assertion is on what the command printed or the code it exited with, never on how
`trial_story` arranged the view model underneath (spec, Testing Decisions).

Two kinds of Run appear here, because `show` must read both. The first is produced by
`agentdiag run` with the toy Target in replay, so its durations come from the real clock
and only its structure can be asserted. The second is a directory holding nothing but the
committed Trace fixture, whose timestamps are literals — so the durations, and the way the
header degrades when `run.json` is absent, are asserted there.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.eval import prompt_adherence
from agentdiag.eval.judge import Judge
from agentdiag.eval.render import JudgeContext
from agentdiag.model.claude_code import Backend
from agentdiag.model.client import ModelRequest, ModelResponse
from agentdiag.scenario.load import load_suite
from agentdiag.trace import Event, TraceWriter, read_trace
from agentdiag.trace.show import render_story, trial_story
from tests.stories import CANCEL_EXIT, CANCEL_VERDICT

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
RECORDING = REPO / "tests" / "fixtures" / "recordings" / "toy-cancel.jsonl"
SCHEMA_FAILURE = REPO / "tests" / "fixtures" / "recordings" / "judge-schema-failure.jsonl"
TARGET_ONLY = REPO / "tests" / "fixtures" / "recordings" / "toy-cancel-target.jsonl"
TRACE_FIXTURE = REPO / "tests" / "fixtures" / "traces" / "cancel-processing-order.trace.jsonl"

SCENARIO = "cancel-processing-order"
FIXTURE_RUN_ID = "20260922T101500Z-k7pq"
"""The run id inside the committed Trace fixture's `trace/start` Event."""

runner = CliRunner()


# --- helpers: the two kinds of Run this file reads ---


def target_root(tmp_path: Path) -> Path:
    """A copy of the shipped example, so a Run never writes into the repository."""
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs"))
    return root


def execute_run(root: Path, *, replay: Path = RECORDING, exits: int = CANCEL_EXIT) -> Path:
    """One Run of the example Scenario in replay; returns its Run directory. Over the shipped
    recording the Run exits with the cancel Trial's fail (`tests/stories.py`)."""
    result = runner.invoke(
        app,
        ["run", "--root", str(root), "--scenario", SCENARIO, "--replay", str(replay)],
    )
    assert result.exit_code == exits, result.output
    runs = sorted((root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir())
    assert len(runs) == 1, [directory.name for directory in runs]
    return runs[0]


def trace_only_run(tmp_path: Path, *, trace: Path = TRACE_FIXTURE) -> Path:
    """A Run directory holding one Trace and nothing else: a Trial still in progress.

    No `run.json`, no `judgement.jsonl`, no `scores.json` — the state `show` sees while a
    Trial is running, and the one a Trace fixture alone can reconstruct.
    """
    run_dir = tmp_path / "runs" / FIXTURE_RUN_ID
    trial = run_dir / "trials" / SCENARIO / "1"
    trial.mkdir(parents=True)
    shutil.copyfile(trace, trial / "trace.jsonl")
    return run_dir


def show(*arguments: str) -> object:
    return runner.invoke(app, ["show", *arguments])


def judge_usage() -> dict[str, int]:
    """The usage the cancel recording's first Judge answer reports."""
    for line in RECORDING.read_text(encoding="utf-8").splitlines():
        exchange = json.loads(line)
        if "output_config" in exchange["request"]:
            return dict(exchange["response"]["usage"])
    raise AssertionError("the cancel recording holds no Judge exchange")


def line_starting(output: str, prefix: str) -> str:
    """The one printed line whose stripped text starts with `prefix`."""
    matches = [line for line in output.splitlines() if line.strip().startswith(prefix)]
    assert len(matches) == 1, f"expected one line starting {prefix!r}, got {matches}"
    return matches[0]


def indent_of(output: str, prefix: str) -> int:
    line = line_starting(output, prefix)
    return len(line) - len(line.lstrip())


# --- (g) the command exists ---


def test_the_cli_lists_run_and_show() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "run" in result.stdout
    assert "show" in result.stdout


# --- (a) a Run the CLI produced, read back as one story ---


def test_show_names_the_run_the_scenario_and_the_trial_in_its_header(tmp_path: Path) -> None:
    root = target_root(tmp_path)
    run_dir = execute_run(root)

    result = show(run_dir.name, SCENARIO, "--root", str(root))

    assert result.exit_code == 0
    header = result.stdout.splitlines()[0]
    assert f"Run {run_dir.name}" in header
    assert f"Scenario {SCENARIO}" in header
    assert "Trial 1" in header


def test_show_names_the_target_the_adapter_and_the_sync_status(tmp_path: Path) -> None:
    root = target_root(tmp_path)
    run_dir = execute_run(root)

    result = show(run_dir.name, SCENARIO, "--root", str(root))

    assert "toy-order-desk" in result.stdout
    assert "claude-sonnet-5" in result.stdout
    assert "inprocess" in result.stdout
    assert "instrumented" in result.stdout
    assert "side effects none" in result.stdout
    assert "Sync not_checked (no_fingerprint)" in result.stdout
    assert "Termination completed" in result.stdout


CLAUDE_CODE_BACKEND = {"kind": "claude_code", "cli_version": "2.1.280"}


@pytest.mark.parametrize(
    ("adapter_backend", "judge_backend", "said"),
    [
        (
            CLAUDE_CODE_BACKEND,
            CLAUDE_CODE_BACKEND,
            "Backend target claude_code 2.1.280 · judge claude_code 2.1.280"
            " (structured output checked after the fact, one retry)",
        ),
        (
            {"kind": "replay", "cli_version": None},
            {"kind": "anthropic_api", "cli_version": None},
            "Backend target replay · judge anthropic_api"
            " (structured output constrained at decoding)",
        ),
        (CLAUDE_CODE_BACKEND, None, "Backend target claude_code 2.1.280"),
        (
            None,
            CLAUDE_CODE_BACKEND,
            "Backend judge claude_code 2.1.280"
            " (structured output checked after the fact, one retry)",
        ),
        (None, None, None),
    ],
)
def test_show_names_the_backends_the_run_record_holds_after_the_target_line(
    tmp_path: Path,
    adapter_backend: dict[str, Any] | None,
    judge_backend: dict[str, Any] | None,
    said: str | None,
) -> None:
    """Decision 44: the header says which path each side's model calls took, and for the
    Judge how its structured answer was held to the schema; a Run from before ticket 19
    records no Backend and prints no line."""
    root = target_root(tmp_path)
    run_dir = execute_run(root)
    record_path = run_dir / "run.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    for section, backend in (("adapter", adapter_backend), ("judge", judge_backend)):
        record[section].pop("backend")
        if backend is not None:
            record[section]["backend"] = backend
    record_path.write_text(json.dumps(record), encoding="utf-8")

    result = show(run_dir.name, SCENARIO, "--root", str(root))

    assert result.exit_code == 0, result.output
    header = result.stdout.splitlines()
    if said is None:
        assert not any(line.startswith("Backend") for line in header)
    else:
        assert header[1].startswith("Target ")
        assert header[2] == said


def test_a_replayed_run_says_both_backends_replayed_as_recorded(tmp_path: Path) -> None:
    root = target_root(tmp_path)
    run_dir = execute_run(root)

    result = show(run_dir.name, SCENARIO, "--root", str(root))

    assert line_starting(result.stdout, "Backend") == (
        "Backend target replay · judge replay (structured output as recorded)"
    )


def test_show_prints_the_users_message_and_the_targets_final_message_in_the_turn(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)
    run_dir = execute_run(root)

    result = show(run_dir.name, SCENARIO, "--root", str(root))

    assert "Turn 1" in result.stdout
    assert "user" in line_starting(result.stdout, "user")
    assert "Hi, I'd like to cancel order NB-1042." in result.stdout
    assert "I found order NB-1042" in line_starting(result.stdout, "target")


def test_show_prints_each_llm_call_with_its_model_tokens_and_stop_reason(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)
    run_dir = execute_run(root)

    result = show(run_dir.name, SCENARIO, "--root", str(root))
    # The Turns only: the rationale and the Diagnosis below them cite Span ids in prose.
    turns = result.stdout.split("\nJudgement", 1)[0]

    first = line_starting(turns, "llm_call-1")
    assert "chat claude-sonnet-5" in first
    assert "in 742 out 61" in first
    assert "tool_use" in first
    last = line_starting(turns, "llm_call-3")
    assert "end_turn" in last


def test_show_prints_each_tool_call_with_its_arguments_and_its_result(tmp_path: Path) -> None:
    root = target_root(tmp_path)
    run_dir = execute_run(root)

    result = show(run_dir.name, SCENARIO, "--root", str(root))

    assert '→ lookup_order {"order_id": "NB-1042"}' in result.stdout
    assert '→ cancel_order {"order_id": "NB-1042"}' in result.stdout
    results = [line.strip() for line in result.stdout.splitlines() if line.strip().startswith("←")]
    assert len(results) == 2, results
    assert '"status": "processing"' in results[0]
    assert '"status": "cancelled"' in results[1]


def test_a_tool_call_span_is_nested_under_the_llm_call_that_requested_it(
    tmp_path: Path,
) -> None:
    """ADR-0006 §3 nests the Span; the indentation is how a reader sees that nesting."""
    root = target_root(tmp_path)
    run_dir = execute_run(root)

    result = show(run_dir.name, SCENARIO, "--root", str(root))
    # The Turns only: the Diagnosis below them cites Span ids in prose too.
    turns = result.stdout.split("\nJudgement", 1)[0]

    assert indent_of(turns, "retrieval-1") > indent_of(turns, "llm_call-1")
    assert indent_of(turns, "tool_call-1") > indent_of(turns, "llm_call-2")


def test_show_prints_the_judgement_section_with_the_judge_span_and_its_eval(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)
    run_dir = execute_run(root)

    result = show(run_dir.name, SCENARIO, "--root", str(root))

    assert "Judgement" in result.stdout
    judge = line_starting(result.stdout, "judge-1")
    assert "prompt_adherence" in judge
    assert "claude-opus-5" in judge
    # The token counts are the captured answer's (ticket 21, decision 41), read from it.
    usage = judge_usage()
    assert f"in {usage['input_tokens']} out {usage['output_tokens']}" in judge


def test_show_prints_the_cost_a_backend_reported_and_its_check_beside_the_judges_cost(
    tmp_path: Path,
) -> None:
    """Ticket 19: the Claude Code backend reports each call's cost; `show` puts it and the
    check against the price table after the cost the table gives."""
    root = target_root(tmp_path)
    reported = tmp_path / "reported.jsonl"
    lines = []
    for line in RECORDING.read_text(encoding="utf-8").splitlines():
        exchange = json.loads(line)
        if "output_config" in exchange["request"]:
            # The usage is set here too, so the check is over figures this test authored,
            # whatever the captured answer's own usage is (decision 41).
            exchange["response"]["usage"] = {"input_tokens": 3184, "output_tokens": 211}
            exchange["response"]["claude_code"] = {"total_cost_usd": 0.0212}
        lines.append(json.dumps(exchange))
    reported.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run_dir = execute_run(root, replay=reported)

    result = show(run_dir.name, SCENARIO, "--root", str(root))

    # claude-opus-5 at $5 in and $25 out per million: 3184 in and 211 out is $0.021195.
    assert "$0.021195 reported $0.021200 (agrees)" in line_starting(result.stdout, "judge-1")


class Answering:
    """A Judge client answering with one body, as `ClaudeCodeClient` reassembles one."""

    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse.from_body(request, self.body)


def retried_body(attempts: int) -> dict[str, Any]:
    """A pass as the CLI reports a call it re-prompted `attempts - 1` times (decision 42)."""
    answer = {
        "verdict": "pass",
        "reason": "none",
        "rationale": "The Target looked the order up before cancelling it.",
        "evidence": ["retrieval-1", "tool_call-1"],
        "rules_exercised": [1, 2],
        "rules_not_exercised": [3, 4, 5],
    }
    rejected = [
        {"id": f"msg_try_{n}", "content": [], "rejected": "Output does not match"}
        for n in range(attempts - 1)
    ]
    return {
        "id": "msg_cc_01",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": [
            {"type": "tool_use", "id": "toolu_01", "name": "StructuredOutput", "input": answer}
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 3184, "output_tokens": 211},
        "structured_output": answer,
        "claude_code": {"cli_version": "2.1.280", **({"attempts": rejected} if rejected else {})},
    }


@pytest.mark.parametrize(
    ("attempts", "said"),
    [
        (1, None),
        (2, "(2 attempts: Claude Code rejected the first)"),
        (3, "(3 attempts: Claude Code rejected the first 2)"),
    ],
)
def test_show_says_beside_the_judge_span_how_many_attempts_claude_code_made(
    tmp_path: Path, attempts: int, said: str | None
) -> None:
    """Decision 42: the CLI validates a structured answer and re-prompts once; the rejected
    attempt is in the body, the Judge counts it on its Span, and `show` says so beside the
    call — and nothing at one attempt. The Judge is the real one, over the Trace fixture."""
    run_dir = trace_only_run(tmp_path)
    trial = run_dir / "trials" / SCENARIO / "1"
    suite, _ = load_suite(
        EXAMPLE / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "orders.yaml"
    )
    scenario = next(s for s in suite.scenarios if s.id == SCENARIO)
    judge = Judge(
        Answering(retried_body(attempts)),
        "claude-opus-5",
        None,
        backend=Backend(kind="claude_code", cli_version="2.1.280"),
    )
    judgement = TraceWriter(trial / "judgement.jsonl")
    judgement.start(trace_id="test/show/1", scenario=SCENARIO, run=FIXTURE_RUN_ID, trial=1)
    context = JudgeContext.of(
        scenario,
        scenario.evals[0],
        read_trace(TRACE_FIXTURE),
        fidelity="instrumented",
        notes=None,
        tool_kinds={"lookup_order": "retrieval", "cancel_order": "action"},
    )
    prompt_adherence.judge(context, judge, judgement)
    judgement.end("completed")
    judgement.close()

    result = show(str(run_dir), SCENARIO)

    judge_line = line_starting(result.stdout, "judge-1")
    if said is None:
        assert "attempts" not in judge_line
    else:
        assert judge_line.endswith(said)


def test_show_prints_each_score_with_its_verdict_evidence_and_rationale(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)
    run_dir = execute_run(root)

    result = show(run_dir.name, SCENARIO, "--root", str(root))

    assert "Scores" in result.stdout
    assert f"prompt_adherence  {CANCEL_VERDICT}" in result.stdout
    # What `scores.json` holds is what is printed; the answer is captured (decision 41),
    # so its evidence and rationale are read from the file, not written here.
    story = trial_story(run_dir, SCENARIO, 1)
    assert story.scores is not None
    (score,) = story.scores
    evidence = line_starting(result.stdout, "evidence")
    assert score.evidence
    trace = read_trace(run_dir / "trials" / SCENARIO / "1" / "trace.jsonl")
    assert set(score.evidence) <= {event.span_id for event in trace if event.type == "span/start"}
    for span_id in score.evidence:
        assert span_id in evidence
    # The story: a rationale that is there, printed whole (wrapped lines rejoined).
    assert score.rationale.strip()
    assert " ".join(score.rationale.split()) in " ".join(result.stdout.split())


# --- (b) a Trial with only a Trace: the header degrades, the durations are literals ---


def test_show_reads_a_trial_that_has_only_a_trace(tmp_path: Path) -> None:
    run_dir = trace_only_run(tmp_path)

    result = show(str(run_dir), SCENARIO)

    assert result.exit_code == 0
    assert f"Run {FIXTURE_RUN_ID}" in result.stdout
    assert "Turn 1" in result.stdout


def test_a_trial_without_a_run_record_still_names_what_the_trace_itself_says(
    tmp_path: Path,
) -> None:
    """No `run.json`: the Target and the Adapter are unknown, and `show` says so plainly."""
    run_dir = trace_only_run(tmp_path)

    result = show(str(run_dir), SCENARIO)

    assert "Target unknown" in result.stdout
    assert "Sync unknown" in result.stdout
    assert "Termination completed" in result.stdout


def test_durations_come_from_the_traces_own_timestamps(tmp_path: Path) -> None:
    """`llm_call-1` spans 1758536100006 → 1758536100951, its `request` → `response` 903 ms."""
    run_dir = trace_only_run(tmp_path)

    result = show(str(run_dir), SCENARIO)

    assert "945 ms (model 903 ms)" in line_starting(result.stdout, "llm_call-1")
    assert "30 ms" in line_starting(result.stdout, "retrieval-1")
    assert "2.40 s" in result.stdout


def with_startup(tmp_path: Path, span_id: str, startup_ms: int) -> Path:
    """The committed Trace fixture with the Claude Code start-up on one Span's end."""
    lines = []
    for line in TRACE_FIXTURE.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event["type"] == "span/end" and event["span_id"] == span_id:
            event["attributes"]["agentdiag.backend.startup_ms"] = startup_ms
        lines.append(json.dumps(event))
    path = tmp_path / "startup.trace.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_the_first_llm_call_of_a_claude_code_session_shows_the_start_up_beside_its_duration(
    tmp_path: Path,
) -> None:
    """Ticket 20, decision 36: that Span's duration encloses the CLI's start-up too."""
    run_dir = trace_only_run(tmp_path, trace=with_startup(tmp_path, "llm_call-1", 8930))

    result = show(str(run_dir), SCENARIO)

    assert "945 ms (model 903 ms) (Claude Code start-up 8.93 s)" in line_starting(
        result.stdout, "llm_call-1"
    )
    assert "start-up" not in line_starting(result.stdout, "llm_call-2")


def test_a_trace_without_a_start_up_shows_none(tmp_path: Path) -> None:
    run_dir = trace_only_run(tmp_path)

    result = show(str(run_dir), SCENARIO)

    assert "start-up" not in result.stdout


def test_a_trial_with_no_judgement_file_has_no_judgement_section(tmp_path: Path) -> None:
    """No judged Eval ran, which is normal and not an error (show_format, Rules)."""
    run_dir = trace_only_run(tmp_path)

    result = show(str(run_dir), SCENARIO)

    assert result.exit_code == 0
    assert "Judgement" not in result.stdout


def test_a_trial_whose_scores_are_not_written_yet_says_so(tmp_path: Path) -> None:
    run_dir = trace_only_run(tmp_path)

    result = show(str(run_dir), SCENARIO)

    assert "Scores  (not yet written)" in result.stdout


# --- (c) --json is the raw Events, byte for byte ---


def test_json_emits_the_trace_then_the_judgement_exactly_as_stored(tmp_path: Path) -> None:
    root = target_root(tmp_path)
    run_dir = execute_run(root)
    trial = run_dir / "trials" / SCENARIO / "1"

    result = show(run_dir.name, SCENARIO, "--root", str(root), "--json")

    assert result.exit_code == 0
    expected = (trial / "trace.jsonl").read_text(encoding="utf-8").splitlines() + (
        trial / "judgement.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert result.stdout.splitlines() == expected


def test_json_of_a_trial_with_only_a_trace_is_that_trace(tmp_path: Path) -> None:
    run_dir = trace_only_run(tmp_path)

    result = show(str(run_dir), SCENARIO, "--json")

    assert result.stdout.splitlines() == TRACE_FIXTURE.read_text(encoding="utf-8").splitlines()


# --- (d) what cannot be found is named, with exit 3 ---


def test_a_run_that_does_not_exist_exits_3_and_names_the_path_looked_at(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)

    result = show("20260922T000000Z-zzzz", SCENARIO, "--root", str(root))

    assert result.exit_code == 3
    assert "20260922T000000Z-zzzz" in result.output
    assert str(root / ".agentdiag" / "targets" / "toy-order-desk" / "runs") in result.output


def test_a_trial_that_does_not_exist_exits_3_and_names_the_trace_it_looked_for(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)
    run_dir = execute_run(root)

    result = show(run_dir.name, "no-such-scenario", "--root", str(root))

    assert result.exit_code == 3
    assert "no-such-scenario" in result.output
    assert "trace.jsonl" in result.output


def test_a_trial_number_that_was_never_run_exits_3(tmp_path: Path) -> None:
    root = target_root(tmp_path)
    run_dir = execute_run(root)

    result = show(run_dir.name, SCENARIO, "--root", str(root), "--trial", "7")

    assert result.exit_code == 3
    assert "trials" in result.output


# --- (e) --follow streams the story while the Trial is still being written ---


def test_follow_prints_the_story_as_the_trace_is_written(tmp_path: Path) -> None:
    """A writer appends with small sleeps; `show --follow` prints as the lines land.

    Bounded twice so a bug cannot hang the suite: the writer thread is joined with a
    timeout, and the follower stops at `trace/end`, which the fixture ends with.
    """
    run_dir = tmp_path / "runs" / FIXTURE_RUN_ID
    trial = run_dir / "trials" / SCENARIO / "1"
    trial.mkdir(parents=True)
    path = trial / "trace.jsonl"
    lines = TRACE_FIXTURE.read_text(encoding="utf-8").splitlines(keepends=True)

    def write_slowly() -> None:
        with path.open("w", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line)
                handle.flush()
                time.sleep(0.005)

    writer = threading.Thread(target=write_slowly)
    writer.start()
    try:
        result = show(str(run_dir), SCENARIO, "--follow", "--wait", "0")
    finally:
        writer.join(timeout=10)
    assert not writer.is_alive()

    assert result.exit_code == 0
    assert f"Run {FIXTURE_RUN_ID}" in result.stdout
    assert "Hi, I'd like to cancel order NB-1042." in result.stdout
    assert "llm_call-1" in result.stdout
    assert "Termination completed" in result.stdout


def test_follow_with_json_streams_the_raw_events(tmp_path: Path) -> None:
    run_dir = trace_only_run(tmp_path)

    result = show(str(run_dir), SCENARIO, "--follow", "--json", "--wait", "0")

    assert result.exit_code == 0
    assert result.stdout.splitlines() == TRACE_FIXTURE.read_text(encoding="utf-8").splitlines()


def test_following_a_finished_trial_as_json_emits_what_json_alone_emits(
    tmp_path: Path,
) -> None:
    """`--follow` chooses when the Events arrive, never which of them a reader gets.

    A judged Trial's `--json` is the Trace's lines then the judgement's; following the
    same Trial must end at the same bytes, or a reader piping the live view into `jq`
    silently loses everything the Judge said.
    """
    root = target_root(tmp_path)
    run_dir = execute_run(root)

    finished = show(run_dir.name, SCENARIO, "--root", str(root), "--json")
    followed = show(
        run_dir.name, SCENARIO, "--root", str(root), "--follow", "--json", "--wait", "0"
    )

    assert followed.exit_code == 0
    assert followed.stdout == finished.stdout


# --- (f) a Judge that broke is shown, and its Score is invalid ---


@pytest.fixture
def judge_failed_run(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """A Run whose Target replayed cleanly and whose Judge returned unparseable output.

    Built by splicing the Target's own recording together with the schema-failure Judge
    exchange, so nothing is hand-written: the Judge's request must still match what the
    Judge actually sends.
    """
    root = target_root(tmp_path)
    recording = tmp_path / "toy-cancel-judge-schema-failure.jsonl"
    judge_exchange = _judge_exchange(SCHEMA_FAILURE, RECORDING)
    recording.write_text(
        TARGET_ONLY.read_text(encoding="utf-8").rstrip("\n") + "\n" + judge_exchange,
        encoding="utf-8",
    )
    yield root, execute_run(root, replay=recording, exits=2)


def _judge_exchange(failure: Path, judged: Path) -> str:
    """The Judge's recorded request from `judged`, answered by `failure`'s response.

    The Judge's exchange is the one after the Target's; the Diagnosis's follows it."""
    target = len(TARGET_ONLY.read_text(encoding="utf-8").splitlines())
    request = json.loads(judged.read_text(encoding="utf-8").splitlines()[target])["request"]
    response = json.loads(failure.read_text(encoding="utf-8").splitlines()[0])["response"]
    return json.dumps({"request": request, "response": response}, ensure_ascii=False) + "\n"


def test_a_judge_that_errored_shows_its_error_under_its_span(
    judge_failed_run: tuple[Path, Path],
) -> None:
    root, run_dir = judge_failed_run

    result = show(run_dir.name, SCENARIO, "--root", str(root))

    assert result.exit_code == 0
    assert "Judgement" in result.stdout
    assert "judge-1" in result.stdout
    assert "error" in result.stdout


def test_a_score_the_judge_invalidated_is_shown_with_its_fault(
    judge_failed_run: tuple[Path, Path],
) -> None:
    root, run_dir = judge_failed_run

    result = show(run_dir.name, SCENARIO, "--root", str(root))

    assert "prompt_adherence  invalid" in result.stdout
    assert "judge" in line_starting(result.stdout, "fault")


# --- the README cannot drift from the CLI ---


README = REPO / "README.md"
GUIDE = REPO / "docs" / "guide.md"
DOCUMENTS = (README, GUIDE)
"""The two documents a reader types from: the five-minute README and the full guide."""
BASH_BLOCK = re.compile(r"^```bash\n(.*?)^```", re.MULTILINE | re.DOTALL)


def readme_agentdiag_commands() -> list[str]:
    """Every `uv run agentdiag <command>` the README and the guide tell a reader to type."""
    commands: list[str] = []
    for document in DOCUMENTS:
        for block in BASH_BLOCK.findall(document.read_text(encoding="utf-8")):
            for line in block.splitlines():
                words = line.strip().split()
                if words[:3] == ["uv", "run", "agentdiag"] and len(words) > 3:
                    commands.append(words[3])
    return commands


DOCUMENTED_ROOT = "/tmp/agentdiag-demo"
"""The `--root` the README's five-minute walkthrough types. A checkout runs `uv run` from
the repository, so every documented command names a root rather than relying on the cwd —
and the walkthrough test substitutes a temporary directory for this one."""

FIVE_MINUTES = "## Five minutes"
FIRST_RUN = "## The first Run"


def readme_section(heading: str, document: Path = GUIDE) -> str:
    """One `##` section of a document, from its heading to the next one."""
    text = document.read_text(encoding="utf-8")
    start = text.index(heading)
    following = text.find("\n## ", start + len(heading))
    return text[start:] if following == -1 else text[start:following]


def readme_agentdiag_lines(section: str) -> list[list[str]]:
    """Every `uv run agentdiag …` line of a README section, split into words and in order."""
    return [
        words
        for block in BASH_BLOCK.findall(section)
        for line in block.splitlines()
        if (words := line.strip().split())[:3] == ["uv", "run", "agentdiag"]
    ]


def test_every_agentdiag_command_the_readme_types_is_one_the_cli_registers() -> None:
    registered = {command.name or command.callback.__name__ for command in app.registered_commands}
    registered |= {
        str(group.typer_instance.info.name)
        for group in app.registered_groups
        if group.typer_instance is not None
    }
    typed = readme_agentdiag_commands()

    assert typed, "the README shows no agentdiag command"
    for command in typed:
        assert command.startswith("--") or command in registered, (
            f"README types `agentdiag {command}`, which the CLI does not register: "
            f"{sorted(registered)}"
        )


# --- the README's five-minute walkthrough is the one a reader can type (ticket 02) ---


def test_the_five_minute_walkthrough_runs_as_it_is_written(tmp_path: Path) -> None:
    """Every documented command, in order, against a temporary root, exiting as the README
    says: `run` exits 1, because the toy Target breaks its rule 3 and the Judge catches it
    (`tests/stories.py`), and every other command exits 0.

    Three substitutions, and no others, so what runs here is what a reader types:

    - the documented `--root` becomes `tmp_path`, because a test must not write into
      whatever `/tmp/agentdiag-demo` currently holds;
    - `run` gains `--replay`, because nothing here has credentials;
    - `<run>` becomes the id the `run` above produced, which is what the README's angle
      brackets stand for.

    So the documented `run` line is **checked by proxy, never live**: this test proves that
    the command as written is accepted and drives the Trial to a Run directory, and the
    recording stands in for the API. That the same line works against the real API is what
    the `live` marker's tests are for, and they are deselected by default.
    """
    root = tmp_path / "agentdiag-demo"
    run_id: str | None = None

    section = readme_section(FIVE_MINUTES)
    assert f"This first Run exits {CANCEL_EXIT}, by design." in section
    typed = readme_agentdiag_lines(section)
    assert [words[3] for words in typed] == ["--help", "init", "run", "show", "show", "show"], (
        "the five-minute walkthrough is init, run and show"
    )

    for words in typed:
        if "<run>" in words:
            assert run_id is not None, "the README shows <run> before any Run was made"
        substitutions = {DOCUMENTED_ROOT: str(root), "<run>": run_id or "<run>"}
        arguments = [substitutions.get(word, word) for word in words[3:]]
        if words[3] == "run":
            arguments += ["--replay", str(RECORDING)]

        result = runner.invoke(app, arguments)

        expected = CANCEL_EXIT if words[3] == "run" else 0
        assert result.exit_code == expected, f"`{' '.join(words)}` exited {result.exit_code}"
        if words[3] == "run":
            run_id = sorted((root / ".agentdiag" / "targets" / "default" / "runs").iterdir())[
                -1
            ].name


def test_the_readmes_first_run_runs_as_it_is_written(tmp_path: Path) -> None:
    """The README's "The first Run" is `init`, `run`, the same `run` with `--replay`, and
    `show`, typed in order against a temporary root with the five-minute test's
    substitutions: the documented root, `--replay` on a `run` that has none, and `<run>`.
    Both `run` lines exit 1, by design; the others 0."""
    root = tmp_path / "agentdiag-demo"
    run_id: str | None = None

    section = readme_section(FIRST_RUN, README)
    assert f"this first Run exits {CANCEL_EXIT}, by design" in section
    typed = readme_agentdiag_lines(section)
    assert [words[3] for words in typed] == ["init", "run", "run", "show"], (
        "the first Run is init, run, run --replay and show"
    )

    for words in typed:
        substitutions = {DOCUMENTED_ROOT: str(root), "<run>": run_id or "<run>"}
        arguments = [substitutions.get(word, word) for word in words[3:]]
        if words[3] == "run" and "--replay" not in arguments:
            arguments += ["--replay", str(RECORDING)]
        elif "--replay" in arguments:
            arguments[arguments.index("--replay") + 1] = str(RECORDING)

        result = runner.invoke(app, arguments)

        expected = CANCEL_EXIT if words[3] == "run" else 0
        assert result.exit_code == expected, f"`{' '.join(words)}` exited {result.exit_code}"
        if words[3] == "run":
            run_id = sorted((root / ".agentdiag" / "targets" / "default" / "runs").iterdir())[
                -1
            ].name


# --- what `show` prints never hides what the Trace holds ---


def test_every_span_in_the_trace_is_named_in_the_story(tmp_path: Path) -> None:
    """ADR-0004 §6: `show` shows what a `cat` shows, or points at it. Nothing is cut."""
    run_dir = trace_only_run(tmp_path)
    result = show(str(run_dir), SCENARIO)

    events: list[Event] = read_trace(run_dir / "trials" / SCENARIO / "1" / "trace.jsonl")
    span_ids = {event.span_id for event in events if event.type == "span/start"}

    for span_id in span_ids:
        assert span_id is not None
        assert span_id in result.stdout or span_id.startswith("turn-")


def test_long_text_wraps_at_the_width_and_loses_nothing(tmp_path: Path) -> None:
    """A narrow terminal folds the free text onto more lines; it never truncates it.

    The Trace is the source of truth, so the rendered rationale, read back with its
    continuation lines rejoined, must equal the rationale `scores.json` holds.
    """
    root = target_root(tmp_path)
    run_dir = execute_run(root)
    story = trial_story(run_dir, SCENARIO, 1)
    assert story.scores is not None
    rationale = story.scores[0].rationale

    narrow = render_story(story, width=60)

    assert rationale not in narrow, "a long rationale cannot sit on one 60-column line"
    rendered = [line for line in narrow.splitlines() if line.startswith(" " * 4)]
    # A word longer than the room (a compact JSON blob the Judge quoted) overflows its own
    # line rather than losing characters; every line that could fold did.
    folded = [line for line in rendered if " " in line.strip()]
    assert all(len(line) <= 60 for line in folded), max(folded, key=len)
    assert " ".join(rationale.split()) in " ".join(narrow.split())


def test_a_wide_terminal_leaves_the_text_on_one_line(tmp_path: Path) -> None:
    """Wide enough for the text and its label column: nothing folds, nothing is lost."""
    root = target_root(tmp_path)
    run_dir = execute_run(root)
    story = trial_story(run_dir, SCENARIO, 1)
    assert story.scores is not None
    rationale = story.scores[0].rationale

    wide = render_story(story, width=len(rationale) + 40)

    # A captured rationale may hold paragraphs; each is one unfolded line.
    for paragraph in filter(None, rationale.splitlines()):
        assert any(line.endswith(paragraph) for line in wide.splitlines()), paragraph
    assert story.turns[0].user is not None
    assert line_starting(wide, "user").strip().endswith(story.turns[0].user)


# --- how often decision 37's fallback fired, under each Score and the Diagnosis (ticket 21) ---


def with_read_cites(run_dir: Path) -> None:
    """The Run's Score and Diagnosis as if the Judge had cited rendered Trace lines."""
    trial = run_dir / "trials" / SCENARIO / "1"
    scores = json.loads((trial / "scores.json").read_text(encoding="utf-8"))
    scores["scores"][0]["cites_read"] = ["[llm_call-1]", "llm_call-3 stop_reason end_turn"]
    (trial / "scores.json").write_text(json.dumps(scores), encoding="utf-8")
    lines = []
    for line in (trial / "judgement.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event["type"] == "note" and event.get("about") == "diagnosis":
            event["cites_read"] = ["[retrieval-1]"]
        lines.append(json.dumps(event))
    (trial / "judgement.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_show_prints_the_cites_read_as_span_ids_under_the_score_and_the_diagnosis(
    tmp_path: Path,
) -> None:
    root = target_root(tmp_path)
    run_dir = execute_run(root)
    with_read_cites(run_dir)

    rendered = render_story(trial_story(run_dir, SCENARIO, 1), width=200)

    assert "Scores  (2 cites read as Span ids)" in rendered.splitlines()
    read = [
        line.strip() for line in rendered.splitlines() if line.strip().startswith("cites read ")
    ]
    assert read == [
        'cites read 2 read as the Span ids they begin with: "[llm_call-1]", '
        '"llm_call-3 stop_reason end_turn"',
        'cites read 1 read as the Span id it begins with: "[retrieval-1]"',
    ]


def test_show_prints_no_cites_read_line_when_every_cite_was_a_bare_id(tmp_path: Path) -> None:
    root = target_root(tmp_path)
    run_dir = execute_run(root)

    result = show(run_dir.name, SCENARIO, "--root", str(root))

    assert "Scores" in result.stdout.splitlines()
    assert "cites read" not in result.stdout
