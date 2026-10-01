"""`record_fixtures.py --helpdesk`, offline (ticket 13 stage A, phase-6 decisions 38, 46).

The mode records the help desk's generated Suite: the Target's own model calls from
`captured-helpdesk-target.jsonl`, the Judge's answers from `captured-judge-answers.jsonl`,
both taken live through the login only under `--capture --helpdesk` (the main session's, in
stage C). These tests drive the plumbing over a stand-in Suite in a copy of
`examples/workspace/`, with the Target's calls scripted into the store the way a capture
fills it, and never make a call: without the Suite the mode refuses; with an empty store it
refuses naming `--capture`; with a full one it writes a scoped recording that `run
--replay` takes, and writes it the same twice.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.model.credentials import CredentialSource
from tests.fakes.scripted_model import ScriptedCursor, text_reply, tool_reply

REPO = Path(__file__).resolve().parents[1]

STAND_IN_SUITE: dict[str, Any] = {
    "schema_version": 1,
    "target": "helpdesk",
    "description": "A stand-in for the Suite the walkthrough generates.",
    "scenarios": [
        {
            "id": "rule-1-search-before-answering",
            "title": "A how-to question is answered from an article",
            "provenance": "prompt:system#rules",
            "turns": ["How do I change the billing address on my account?"],
            "evals": [{"expect_tools": ["search_articles"]}, {"forbid_tools": ["escalate"]}],
        },
        {
            "id": "escalate-names-the-ticket",
            "title": "The escalation names the ticket just opened",
            "provenance": "tool:escalate",
            "turns": [
                "I need to speak to a person, not a bot.",
                {
                    "simulate": {
                        "goal": "Get the problem handed to a person.",
                        "known_facts": {"problem": "Two bikes I never added."},
                        "stop_when": {"tool_called": "escalate"},
                    }
                },
            ],
            "max_turns": 3,
            "evals": [{"expect_tools_order": ["open_ticket", "escalate"]}],
        },
    ],
}
SCRIPT_REPLIES = {
    "rule-1-search-before-answering": [
        tool_reply("hd1", "search_articles", {"query": "change billing address"}),
        text_reply("hd2", "Settings > Billing > Address, then Save (KB-104)."),
    ],
    "escalate-names-the-ticket": [
        text_reply("hd3", "Of course. What is the problem?"),
        tool_reply("hd4", "open_ticket", {"subject": "Unknown bikes", "details": "Two bikes."}),
        tool_reply("hd5", "escalate", {"ticket_id": "HD-5001", "reason": "asked for a person"}),
        text_reply("hd6", "A person will reply on ticket HD-5001."),
    ],
}


def _record_fixtures() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "record_fixtures_helpdesk", REPO / "scripts" / "record_fixtures.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("scripts/record_fixtures.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SCRIPT = _record_fixtures()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the example Workspace whose help desk holds the stand-in Suite."""
    root = tmp_path / "workspace"
    shutil.copytree(REPO / "examples" / "workspace", root, ignore=shutil.ignore_patterns("runs"))
    target = root / ".agentdiag" / "targets" / "help-desk"
    (target / "suites" / "generated.yaml").write_text(yaml.safe_dump(STAND_IN_SUITE))
    manifest = yaml.safe_load((target / "manifest.yaml").read_text(encoding="utf-8"))
    manifest["suites"] = ["suites/generated.yaml"]
    manifest["tools"] = {"search_articles": {"kind": "retrieval"}}
    (target / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    monkeypatch.setattr(SCRIPT, "HELPDESK_WORKSPACE", root)
    return root


def filled_store(tmp_path: Path, workspace: Path) -> Any:
    """The Target store as a `--capture` would leave it: each stand-in Scenario's Trial
    driven through the real Adapter against the scripted model (the adaptive one through the
    real driver loop, its Simulated User answered by the canned Judge), read back off its
    Trace."""
    root = SCRIPT.helpdesk_root(tmp_path / "filling")
    login = CredentialSource(kind="api_key", detail="ANTHROPIC_API_KEY")
    calls = SCRIPT.CapturedTargetCalls(tmp_path / "target-calls.jsonl", live=login)
    for identifier, replies in SCRIPT_REPLIES.items():
        plan, scenario = SCRIPT.planned(identifier, root)
        adapter = SCRIPT.helpdesk_adapter(plan, replay=ScriptedCursor(replies))
        store = judge_store(tmp_path)
        trace = SCRIPT.drive_helpdesk_trial(
            scenario,
            plan,
            adapter,
            tmp_path / f"{identifier}.scripted.jsonl",
            SCRIPT.UnauthoredJudgeModel(identifier, store),
            store,
        )
        calls.capture(scenario.id, SCRIPT.exchanges_of(trace.path))
    return SCRIPT.CapturedTargetCalls(tmp_path / "target-calls.jsonl", live=None)


class CannedJudge:
    """A stand-in for the live Judge under `--capture`: every request answered with one pass,
    so the plumbing's capture path runs with no model."""

    def complete(self, request: Any) -> Any:
        from agentdiag.model.client import ModelResponse

        asked = json.dumps(request.body().get("output_config") or {})
        answer: dict[str, Any] = (
            {"message": "My account shows two bikes I never added."}
            if '"message"' in asked
            else {"verdict": "pass", "reason": "none", "rationale": "Held.", "evidence": []}
        )
        body = {
            "id": "msg_canned",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [{"type": "text", "text": json.dumps(answer)}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        return ModelResponse.from_body(request, body)


def judge_store(tmp_path: Path) -> Any:
    """The Judge's store as a `--capture` fills it: the imported Trace's rescore asks it."""
    from agentdiag.model.claude_code import Backend

    live = SCRIPT.LiveJudge(CannedJudge(), Backend(kind="anthropic_api"))
    return SCRIPT.CapturedAnswers(tmp_path / "judge.jsonl", live=live)


def test_without_the_generated_suite_the_mode_refuses_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "bare"
    shutil.copytree(REPO / "examples" / "workspace", root, ignore=shutil.ignore_patterns("runs"))
    (root / ".agentdiag/targets/help-desk/suites/generated.yaml").unlink()
    monkeypatch.setattr(SCRIPT, "HELPDESK_WORKSPACE", root)

    with pytest.raises(SystemExit, match=r"generated\.yaml"):
        SCRIPT.record_helpdesk(
            SCRIPT.Out(tmp_path / "out"),
            judge_store(tmp_path),
            SCRIPT.CapturedTargetCalls(tmp_path / "none.jsonl", live=None),
        )
    assert not (tmp_path / "out").exists()


def test_an_empty_target_store_outside_capture_is_refused_naming_the_scenario(
    tmp_path: Path, workspace: Path
) -> None:
    empty = SCRIPT.CapturedTargetCalls(tmp_path / "none.jsonl", live=None)

    with pytest.raises(SCRIPT.HelpDeskMissing, match="rule-1-search-before-answering"):
        SCRIPT.record_helpdesk(SCRIPT.Out(tmp_path / "out"), judge_store(tmp_path), empty)


def test_a_full_store_writes_a_scoped_recording_run_replay_takes(
    tmp_path: Path, workspace: Path
) -> None:
    out = SCRIPT.Out(tmp_path / "out")

    SCRIPT.record_helpdesk(out, judge_store(tmp_path), filled_store(tmp_path, workspace))

    recording = out.recording("helpdesk")
    lines = [yaml.safe_load(line) for line in recording.read_text().splitlines()]
    scopes = [line["scenario"] for line in lines]
    assert scopes[:2] == ["rule-1-search-before-answering"] * 2
    adaptive = [line for line in lines if line["scenario"] == "escalate-names-the-ticket"]
    assert len(adaptive) == 5, "four Target calls and the Simulated User's one message"
    assert scopes[-1] == "imported-hd-0001", "the imported Trace's rescore rides along"
    for identifier in SCRIPT_REPLIES:
        assert (out.traces / "helpdesk" / f"{identifier}.trace.jsonl").is_file()
    result = CliRunner().invoke(
        app,
        ["run", "--root", str(workspace), "--target", "help-desk", "--replay", str(recording)],
    )
    assert result.exit_code == 0, result.output


def test_a_second_generation_is_byte_for_byte_the_first(tmp_path: Path, workspace: Path) -> None:
    calls = filled_store(tmp_path, workspace)
    first, second = SCRIPT.Out(tmp_path / "one"), SCRIPT.Out(tmp_path / "two")

    SCRIPT.record_helpdesk(first, judge_store(tmp_path), calls)
    SCRIPT.record_helpdesk(second, judge_store(tmp_path), calls)

    for path in sorted(first.root.rglob("*")):
        if path.is_file():
            assert path.read_bytes() == (second.root / path.relative_to(first.root)).read_bytes()


def test_the_mode_runs_alone_or_with_capture(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        SCRIPT.main(["--helpdesk", "--runs"])
    assert "--helpdesk runs alone, or with --capture" in capsys.readouterr().err
