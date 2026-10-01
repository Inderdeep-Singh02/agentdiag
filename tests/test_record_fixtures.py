"""`scripts/record_fixtures.py`'s captured Judge answers (ticket 21, decision 41).

A recording whose story is the natural one — what the Judge is expected to say over that
Trace — carries the Judge's real answer, captured once into
`tests/fixtures/recordings/captured-judge-answers.jsonl`; one that exercises a code path is
worked, and its authored answer is the fixture. These tests hold the script's store to that
split without a live call: the "live" client here is a fake that answers from a body the
test chose. The last test holds the committed fixtures to the captured file, and it is red
until a maintainer has run `--capture` once.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from agentdiag.model.claude_code import Backend
from agentdiag.model.client import ModelRequest, ModelResponse
from agentdiag.model.replay import canonical

REPO = Path(__file__).resolve().parents[1]
RECORDINGS = REPO / "tests" / "fixtures" / "recordings"
CAPTURED = RECORDINGS / "captured-judge-answers.jsonl"

CLAUDE_CODE = Backend(kind="claude_code", cli_version="2.1.280")
FINGERPRINT = "f" * 64


def _record_fixtures() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "record_fixtures_captured", REPO / "scripts" / "record_fixtures.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("scripts/record_fixtures.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    # Registered, as an import would, so the script's dataclasses can resolve their module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SCRIPT = _record_fixtures()

SIMULATED_USER_SCHEMA = {
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
    "additionalProperties": False,
}


def a_request(content: str = "judge this") -> ModelRequest:
    return ModelRequest(
        model="claude-opus-5",
        max_tokens=16000,
        messages=[{"role": "user", "content": content}],
        output_schema={"type": "object"},
        fingerprint=FINGERPRINT,
    )


def answered(output: dict[str, Any], identifier: str = "msg_authored") -> dict[str, Any]:
    body: dict[str, Any] = SCRIPT.judged(output, identifier)
    return body


def a_verdict(value: str, identifier: str = "msg_authored") -> dict[str, Any]:
    return answered(
        {"verdict": value, "reason": "none", "rationale": "because", "evidence": ["llm_call-1"]},
        identifier,
    )


def the_rules(**verdicts: str) -> dict[str, Any]:
    return answered(
        {
            "rules": [
                {
                    "id": rule.replace("_", "-"),
                    "verdict": value,
                    "reason": "none",
                    "rationale": "because",
                    "evidence": ["llm_call-3"],
                }
                for rule, value in verdicts.items()
            ]
        }
    )


class FakeLive:
    """The live client's seat, answered by a body the test chose, counting its calls."""

    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse.from_body(request, self.body)


class Unreachable:
    """A live client no test may reach."""

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("the store asked the live client")


def captured_line(request: ModelRequest, response: dict[str, Any]) -> dict[str, Any]:
    return {
        "request": request.body(),
        "response": response,
        "captured": {
            "at": "2026-09-24T12:00:00Z",
            "backend": {"kind": "claude_code", "cli_version": "2.1.280"},
            "judge_fingerprint": FINGERPRINT,
        },
    }


def lines_of(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- the mark ---


def test_natural_marks_an_authored_answer_and_keeps_its_body() -> None:
    body = a_verdict("pass")

    marked = SCRIPT.natural(body)

    assert isinstance(marked, SCRIPT.Natural)
    assert marked.body is body
    with pytest.raises(AttributeError):
        marked.body = {}


def test_as_model_keeps_the_mark_and_edits_the_body_inside_it() -> None:
    body = a_verdict("pass")

    renamed = SCRIPT.as_model(SCRIPT.natural(body), "claude-opus-5-5")
    plain = SCRIPT.as_model(body, "claude-opus-5-5")

    assert isinstance(renamed, SCRIPT.Natural)
    assert renamed.body["model"] == "claude-opus-5-5"
    assert body["model"] == "claude-opus-5", "the authored body itself is not edited"
    assert not isinstance(plain, SCRIPT.Natural)
    assert plain["model"] == "claude-opus-5-5"


def test_the_natural_answers_are_exactly_the_ones_the_contract_lists() -> None:
    """Decision 41's set, and only it: a worked answer marked natural would be re-captured
    and lose the code path it exists for. `guardrails-pass` is worked (ticket 21's
    Comments): its request is `toy-orders`'s, whose natural story is a fail; `judge-pass` is
    worked for the same reason since decision 43, and `judge-fail` is the cancel Trace's
    natural story."""
    by_stem = {
        **SCRIPT.responses(),
        **SCRIPT.DIAGNOSIS_RECORDINGS,
        **{stem: recording.answer for stem, recording in SCRIPT.DECISION_45_RECORDINGS.items()},
        **{stem: answer for stem, (_, _, answer) in SCRIPT.EVAL_RECORDINGS.items()},
    }

    marked = {stem for stem, answer in by_stem.items() if isinstance(answer, SCRIPT.Natural)}

    assert marked == {
        "judge-fail",
        "judge-manifest-prompt",
        "judge-suppressed",
        "diagnosis-ok",
        "goal-pass",
        "data_grounding-pass",
        "data_query-pass",
        "tool_choice-pass",
        "tool_choice-no-tool-called",
    }
    assert isinstance(SCRIPT.CANCEL_DIAGNOSIS, SCRIPT.Natural)
    assert not isinstance(SCRIPT.responses()["judge-pass"], SCRIPT.Natural)
    assert all(
        isinstance(answer, SCRIPT.Natural)
        for answers in SCRIPT.JUDGE_SCRIPTS.values()
        for answer in answers
    )
    assert not any(
        isinstance(answer, SCRIPT.Natural)
        for answers in SCRIPT.SWAPPED_JUDGE_ANSWERS.values()
        for answer in answers
    )
    assert set(SCRIPT.NATURAL_RECORDINGS) == marked | {
        "toy-cancel",
        "toy-orders",
        "runs-model-swap",
        "runs-prompt-change",
    }


# --- the store ---


def test_a_natural_request_in_the_file_is_answered_from_it_without_a_call(
    tmp_path: Path,
) -> None:
    path = tmp_path / "captured.jsonl"
    request = a_request()
    captured = a_verdict("pass", "msg_captured")
    path.write_text(json.dumps(captured_line(request, captured)) + "\n", encoding="utf-8")
    store = SCRIPT.CapturedAnswers(path, live=SCRIPT.LiveJudge(Unreachable(), CLAUDE_CODE))

    response = store.answer(request, a_verdict("pass"), name="goal-pass")

    assert response == captured
    assert (store.calls, store.misses) == (0, [])


def test_a_miss_asks_the_live_client_once_and_appends_the_line_with_its_captured_facts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "captured.jsonl"
    live = FakeLive(a_verdict("pass", "msg_live"))
    store = SCRIPT.CapturedAnswers(path, live=SCRIPT.LiveJudge(live, CLAUDE_CODE))
    request = a_request()

    first = store.answer(request, a_verdict("pass"), name="goal-pass (a-scenario)")
    again = store.answer(request, a_verdict("pass"), name="toy-orders (a-scenario)")

    assert first == again == live.body
    assert len(live.requests) == 1, "a request is captured once"
    (line,) = lines_of(path)
    assert (line["request"], line["response"]) == (request.body(), live.body)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", line["captured"]["at"])
    assert line["captured"]["backend"] == {"kind": "claude_code", "cli_version": "2.1.280"}
    assert line["captured"]["judge_fingerprint"] == request.fingerprint
    (said,) = [text for text in capsys.readouterr().err.splitlines() if text.startswith("captured")]
    assert re.fullmatch(r"captured 1 for goal-pass \(a-scenario\): pass, \d+\.\d s", said)


def test_a_second_capture_makes_only_the_calls_whose_requests_are_missing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "captured.jsonl"
    SCRIPT.CapturedAnswers(
        path, live=SCRIPT.LiveJudge(FakeLive(a_verdict("pass")), CLAUDE_CODE)
    ).answer(a_request("one"), a_verdict("pass"), name="one")
    live = FakeLive(a_verdict("fail"))
    store = SCRIPT.CapturedAnswers(path, live=SCRIPT.LiveJudge(live, CLAUDE_CODE))

    store.answer(a_request("one"), a_verdict("pass"), name="one")
    store.answer(a_request("two"), a_verdict("fail"), name="two")

    assert [request.body()["messages"][0]["content"] for request in live.requests] == ["two"]
    assert len(lines_of(path)) == 2


def test_the_store_refuses_to_append_a_request_it_already_holds(tmp_path: Path) -> None:
    store = SCRIPT.CapturedAnswers(
        tmp_path / "captured.jsonl", live=SCRIPT.LiveJudge(Unreachable(), CLAUDE_CODE)
    )
    request = a_request()
    store.add(request.body(), a_verdict("pass"), fingerprint=FINGERPRINT)

    with pytest.raises(ValueError, match="appears once"):
        store.add(request.body(), a_verdict("fail"), fingerprint=FINGERPRINT)


def test_with_no_live_client_a_miss_is_the_authored_body_counted_and_warned_of(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "captured.jsonl"
    store = SCRIPT.CapturedAnswers(path, live=None)
    expected = a_verdict("pass")

    response = store.answer(a_request(), expected, name="goal-pass (a-scenario)")

    assert response is expected
    assert store.misses == ["goal-pass (a-scenario)"]
    assert not path.exists(), "nothing is appended without a live client"
    assert "goal-pass (a-scenario)" in capsys.readouterr().err
    assert SCRIPT.exit_status(store) == 1
    assert SCRIPT.exit_status(SCRIPT.CapturedAnswers(path, live=None)) == 0


def test_the_scripts_own_judge_takes_the_live_backend_only_under_capture(tmp_path: Path) -> None:
    """So a captured line's Fingerprint is the live Judge's; the request body, the key, is
    the same either way (decision 40)."""
    path = tmp_path / "captured.jsonl"

    capturing = SCRIPT.CapturedAnswers(path, live=SCRIPT.LiveJudge(Unreachable(), CLAUDE_CODE))
    reading = SCRIPT.CapturedAnswers(path, live=None)

    assert capturing.judge_backend == CLAUDE_CODE
    assert reading.judge_backend == Backend(kind="replay")


def test_a_worked_answer_never_touches_the_store(tmp_path: Path) -> None:
    path = tmp_path / "captured.jsonl"
    store = SCRIPT.CapturedAnswers(path, live=SCRIPT.LiveJudge(Unreachable(), CLAUDE_CODE))
    worked = a_verdict("fail")
    model = SCRIPT.ScriptedModel("a-scenario", [worked], captured=store, recording="goal-fail")

    exchange = model.take(a_request().body(), sent=a_request())

    assert exchange.response is worked
    assert (store.calls, store.misses, path.exists()) == (0, [], False)


def test_a_natural_answer_is_taken_through_the_store(tmp_path: Path) -> None:
    live = FakeLive(a_verdict("pass", "msg_live"))
    store = SCRIPT.CapturedAnswers(
        tmp_path / "captured.jsonl", live=SCRIPT.LiveJudge(live, CLAUDE_CODE)
    )
    model = SCRIPT.ScriptedModel(
        "a-scenario", [SCRIPT.natural(a_verdict("pass"))], captured=store, recording="goal-pass"
    )

    response = SCRIPT.ScriptedJudgeClient(model).complete(a_request())

    assert response.body == live.body
    assert model.lines(scoped=False) == [{"request": a_request().body(), "response": live.body}]


class Failing:
    """A live client whose call fails, as the Claude Code CLI's did for two Diagnoses."""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        raise RuntimeError("Reached maximum number of turns (1)")


def test_a_live_call_that_fails_is_said_counted_and_re_raised(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Decision 41's last bullet: the Judge turns the exception into rule 4, so the store is
    the only place the failure can be said; no line is written, and a second run asks again."""
    path = tmp_path / "captured.jsonl"
    store = SCRIPT.CapturedAnswers(path, live=SCRIPT.LiveJudge(Failing(), CLAUDE_CODE))

    with pytest.raises(RuntimeError, match="maximum number of turns"):
        store.answer(a_request(), a_verdict("pass"), name="diagnosis-ok (a-scenario)")

    failure = "diagnosis-ok (a-scenario): RuntimeError: Reached maximum number of turns (1)"
    assert store.failures == [failure]
    assert f"capture failed for {failure}" in capsys.readouterr().err.splitlines()
    assert (store.calls, path.exists()) == (0, False)
    assert SCRIPT.exit_status(store) == 1


def test_capture_exits_1_naming_how_many_live_calls_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main` under `--capture` with the live Judge faked and the generations reduced to
    two natural calls, one of which fails: every generation still runs, and the exit says
    how many failed."""
    live = FakeLive(a_verdict("pass", "msg_live"))
    failing = Failing()
    ran: list[str] = []

    def generation(name: str, request: ModelRequest) -> Any:
        def generate(out: Any, captured: Any) -> None:
            ran.append(name)
            # The Judge records rule 4 and carries on.
            with contextlib.suppress(RuntimeError):
                captured.answer(request, a_verdict("pass"), name=name)

        return generate

    monkeypatch.setattr(
        SCRIPT, "live_judge", lambda mode: SCRIPT.LiveJudge(Switch(live, failing), CLAUDE_CODE)
    )
    monkeypatch.setattr(SCRIPT, "CAPTURED", tmp_path / "committed.jsonl")
    monkeypatch.setattr(SCRIPT, "record_cancel", generation("cancel", a_request("one")))
    monkeypatch.setattr(SCRIPT, "record_scripted", generation("scripted", a_request("fails")))
    monkeypatch.setattr(SCRIPT, "record_runs", generation("runs", a_request("one")))

    status = SCRIPT.main(["--capture", "--out", str(tmp_path / "out")])

    assert status == 1
    assert ran == ["cancel", "scripted", "runs"]
    assert "1 live Judge call failed" in capsys.readouterr().err


class Switch:
    """A live client that fails on the request whose content is "fails"."""

    def __init__(self, answering: FakeLive, failing: Failing) -> None:
        self.answering, self.failing = answering, failing

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.body()["messages"][0]["content"] == "fails":
            return self.failing.complete(request)
        return self.answering.complete(request)


def test_the_mode_flags_are_mutually_exclusive(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as refused:
        SCRIPT.main(["--capture", "--runs"])

    assert refused.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_under_out_the_store_reads_the_committed_file_then_the_one_it_appends_to(
    tmp_path: Path,
) -> None:
    """A second `--capture --out X` asks only for what neither the committed file nor X
    holds: the store reads both."""
    committed, into = tmp_path / "committed.jsonl", tmp_path / "out" / "captured.jsonl"
    into.parent.mkdir()
    committed.write_text(
        json.dumps(captured_line(a_request("one"), a_verdict("pass", "msg_one"))) + "\n",
        encoding="utf-8",
    )
    into.write_text(
        json.dumps(captured_line(a_request("two"), a_verdict("pass", "msg_two"))) + "\n",
        encoding="utf-8",
    )
    store = SCRIPT.CapturedAnswers(
        committed, live=SCRIPT.LiveJudge(Unreachable(), CLAUDE_CODE), into=into
    )

    assert store.answer(a_request("one"), a_verdict("pass"), name="one")["id"] == "msg_one"
    assert store.answer(a_request("two"), a_verdict("pass"), name="two")["id"] == "msg_two"
    assert store.calls == 0


def test_a_request_held_by_both_the_committed_file_and_the_out_file_is_refused(
    tmp_path: Path,
) -> None:
    committed, into = tmp_path / "committed.jsonl", tmp_path / "into.jsonl"
    line = json.dumps(captured_line(a_request("one"), a_verdict("pass"))) + "\n"
    committed.write_text(line, encoding="utf-8")
    into.write_text(line, encoding="utf-8")

    with pytest.raises(ValueError, match="both"):
        SCRIPT.CapturedAnswers(committed, live=None, into=into)


def test_a_natural_answer_with_no_store_is_refused_rather_than_rendered_silently() -> None:
    """Every Judge-side helper takes the one store `main` builds; a `ScriptedModel` handed a
    natural answer without one would lose the miss the exit status counts."""
    model = SCRIPT.ScriptedModel("a-scenario", [SCRIPT.natural(a_verdict("pass"))])

    with pytest.raises(SCRIPT.ReplayMismatch, match="no store"):
        model.take(a_request().body(), sent=a_request())


# --- the story check: reported, never re-authored ---


def test_an_authored_pass_against_a_captured_fail_is_a_story_that_differs() -> None:
    assert SCRIPT.story_differs("goal-pass", a_request(), a_verdict("pass"), a_verdict("fail")) == [
        "story differs: goal-pass: authored pass, captured fail"
    ]


def test_equal_verdicts_are_the_same_story_whatever_the_wording() -> None:
    captured = answered(
        {"verdict": "pass", "reason": "none", "rationale": "other words", "evidence": ["x-1"]}
    )

    assert SCRIPT.story_differs("goal-pass", a_request(), a_verdict("pass"), captured) == []


def test_a_guardrails_story_is_checked_per_rule() -> None:
    authored = the_rules(no_refund_timing="fail", speaks_as_the_desk="pass")
    captured = the_rules(no_refund_timing="pass", speaks_as_the_desk="pass")

    assert SCRIPT.story_differs("toy-orders", a_request(), authored, captured) == [
        "story differs: toy-orders [no-refund-timing]: authored fail, captured pass"
    ]


def test_a_captured_answer_in_the_structured_output_is_read_for_its_story() -> None:
    """The Claude Code backend's body carries the answer in `structured_output`."""
    captured = {
        "content": [{"type": "tool_use", "name": "StructuredOutput", "input": {}}],
        "structured_output": {"verdict": "fail", "rationale": "", "evidence": []},
    }

    assert SCRIPT.story_differs("goal-pass", a_request(), a_verdict("pass"), captured) == [
        "story differs: goal-pass: authored pass, captured fail"
    ]


def test_a_diagnosis_has_no_verdict_to_compare() -> None:
    authored = SCRIPT.diagnosed("msg_a", "one account", ["llm_call-1"], [1])
    captured = SCRIPT.diagnosed("msg_b", "another account", ["retrieval-1"], [2])

    assert SCRIPT.story_differs("diagnosis-ok", a_request(), authored, captured) == []


# --- the Simulated User's answers (ticket 06, phase-5 decision 61) ---


def a_simulated_user_request() -> ModelRequest:
    return ModelRequest(
        model="claude-sonnet-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": "Write your next message."}],
        output_schema=SIMULATED_USER_SCHEMA,
        fingerprint=FINGERPRINT,
    )


def test_a_simulated_users_message_has_no_verdict_so_its_story_is_never_compared() -> None:
    authored = SCRIPT.says("msg_a", "It's NB-1042.")
    captured = SCRIPT.says("msg_b", "NB-1042, sorry, I'm in a rush.")

    request = a_simulated_user_request()
    assert SCRIPT.story_differs("toy-orders (x)", request, authored, captured) == []
    assert SCRIPT.answer_of(request, captured) == {"message": "NB-1042, sorry, I'm in a rush."}
    assert SCRIPT.verdicts_text(request, captured) == (
        'a Simulated User message: "NB-1042, sorry, I\'m in a rush."'
    )


def test_the_adaptive_example_is_natural_and_the_worked_simulated_set_is_not() -> None:
    """The toy Scenario's Simulated User message, goal and Diagnosis are what the models are
    expected to say (captured by the main session); every answer over the test-only Suite is
    a code path (decision 41)."""
    (adaptive,) = SCRIPT.SIMULATED_SCRIPTS.values()
    assert [type(answer).__name__ for answer in adaptive].count("Natural") == 1
    assert all(
        isinstance(answer, SCRIPT.Natural)
        for answer in SCRIPT.JUDGE_SCRIPTS["cancel-without-the-order-number"]
    )
    assert not any(
        isinstance(answer, SCRIPT.Natural)
        for answers in SCRIPT.SIMULATED_DRIVES.values()
        for answer in answers
    )
    assert not any(
        isinstance(answer, SCRIPT.Natural)
        for _, answers in SCRIPT.SIMULATED_JUDGEMENTS.values()
        for answer in answers
    )
    assert not set(SCRIPT.SIMULATED_JUDGEMENTS) & set(SCRIPT.NATURAL_RECORDINGS)


# --- the committed fixtures carry the captured answers ---


def judge_lines(stem: str) -> list[dict[str, Any]]:
    """A recording's Judge lines: the requests that ask for structured output."""
    return [
        line
        for line in lines_of(RECORDINGS / f"{stem}.jsonl")
        if "output_config" in line["request"]
    ]


def test_every_natural_recording_carries_the_captured_judge_answers() -> None:
    """Red until a maintainer has run `record_fixtures.py --capture` once: before that, the
    natural recordings carry their authored stories and the script exits 1 saying so. One
    test over every natural recording, so the state reads as one red line. Each captured
    line also says when, through which Backend and under which Judge."""
    assert CAPTURED.exists(), "run `uv run python scripts/record_fixtures.py --capture`"
    captured = {canonical(line["request"]): line for line in lines_of(CAPTURED)}

    problems: list[str] = []
    for stem in SCRIPT.NATURAL_RECORDINGS:
        lines = judge_lines(stem)
        if not lines:
            problems.append(f"{stem}: no Judge line")
        for line in lines:
            found = captured.get(canonical(line["request"]))
            if found is None and stem in SCRIPT.DECISION_45_RECORDINGS:
                # Not captured yet (phase-6 decision 45): their own tests in
                # test_manifest_prompt_and_suppressions.py skip, naming `--capture`.
                continue
            if found is None:
                problems.append(f"{stem}: a natural request with no captured answer")
            elif line["response"] != found["response"]:
                problems.append(f"{stem}: a response that is not the captured answer")

    assert problems == []
    for line in captured.values():
        facts = line["captured"]
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", facts["at"])
        assert facts["backend"]["kind"] in {"claude_code", "anthropic_api"}
        assert re.fullmatch(r"[0-9a-f]{64}", facts["judge_fingerprint"])


def test_a_worked_answer_over_a_natural_request_keeps_its_authored_body() -> None:
    """`judge-pass` asks what `judge-fail` asks, so its request is in the captured file, and
    still it carries the authored pass (decision 43): a worked answer never consults the
    store, whatever the store holds for its request."""
    captured = {canonical(line["request"]) for line in lines_of(CAPTURED)}
    (line,) = judge_lines("judge-pass")

    assert canonical(line["request"]) in captured
    assert line["response"]["id"] == "msg_01JudgePassNB1042"
