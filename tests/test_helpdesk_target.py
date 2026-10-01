"""The second toy Target, the help desk, through the in-process Adapter (ticket 13, phase-6
decision 38).

Seam 1 with the model scripted (`tests.fakes.scripted_model`): the real Adapter builds the
real help desk from the factory and the tools the Manifest would name, hands it a client
whose transport the Adapter owns, and every request the Target sends is kept, so what is
asserted is what the help desk put on the wire. The platform edit is the behaviour decision
39's end-to-end check leans on: an assignment to `platform.DEPLOYED` is the prompt the very
next request carries.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from agentdiag.adapter import InProcessAdapter
from agentdiag.examples import helpdesk
from agentdiag.examples.helpdesk import platform
from agentdiag.sync.sections import split_sections
from agentdiag.trace import TraceWriter, event_fields, project_spans, read_trace
from tests.fakes.scripted_model import ScriptedCursor, text_reply, tool_reply

REPO = Path(__file__).resolve().parents[1]
PROXY_ROWS = REPO / "tests" / "fixtures" / "evidence" / "helpdesk-proxy-rows.json"

ANSWER = "Open Settings, then Billing, then Address, and press Save (KB-104)."


def adapter(cursor: ScriptedCursor) -> InProcessAdapter:
    config: dict[str, Any] = {
        "kind": "inprocess",
        "side_effects": "none",
        "environments": {
            "default": "local",
            "local": {
                "factory": "agentdiag.examples.helpdesk:make_helpdesk",
                "tools": "agentdiag.examples.helpdesk:make_tools",
                "model": "claude-sonnet-5",
            },
        },
    }
    return InProcessAdapter(
        config, environment="local", replay=cursor, tool_kinds={"search_articles": "retrieval"}
    )


def turns(tmp_path: Path, cursor: ScriptedCursor, *messages: str, between: Any = None) -> Path:
    """One session, one Turn per message; `between` runs after the first Turn."""
    writer = TraceWriter(tmp_path / "trace.jsonl")
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    session = adapter(cursor).open(writer)
    for number, message in enumerate(messages, start=1):
        with writer.span("turn", actor="agentdiag", name=f"turn {number}", fidelity="instrumented"):
            session.deliver(message)
        if number == 1 and between is not None:
            between()
    session.close()
    writer.end("completed")
    writer.close()
    return writer.path


def test_a_how_to_turn_searches_the_articles_as_a_retrieval_and_answers(tmp_path: Path) -> None:
    cursor = ScriptedCursor(
        [
            tool_reply("1", "search_articles", {"query": "change billing address"}),
            text_reply("2", ANSWER),
        ]
    )

    trace = turns(tmp_path, cursor, "How do I change my billing address?")

    first, second = cursor.requests
    assert first["system"] == platform.SYSTEM_PROMPT
    assert [tool["name"] for tool in first["tools"]] == [
        "search_articles",
        "open_ticket",
        "escalate",
    ]
    assert first["model"] == "claude-sonnet-5"
    (result,) = second["messages"][-1]["content"]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    assert json.loads(result["content"]) == [
        {
            "id": "KB-104",
            "title": "Change your billing address",
            "summary": "Settings > Billing > Address, then Save.",
        }
    ]
    events = read_trace(trace)
    kinds = [span.kind for span in project_spans(events)]
    assert kinds.count("retrieval") == 1 and "tool_call" not in kinds
    (call,) = [event for event in events if event.type == "tool/call"]
    assert event_fields(call)["arguments"] == {"query": "change billing address"}
    assert event_fields(call)["not_observed"] == []


def test_a_platform_edit_is_the_prompt_the_very_next_request_carries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edited = platform.SYSTEM_PROMPT.replace("At most four sentences", "At most two sentences")
    cursor = ScriptedCursor([text_reply("1", "Hello.")])

    def edit_on_the_platform() -> None:
        monkeypatch.setitem(platform.DEPLOYED["prompts"], "system", edited)

    turns(tmp_path, cursor, "Hi", "Hi again", between=edit_on_the_platform)

    assert [request["system"] for request in cursor.requests] == [platform.SYSTEM_PROMPT, edited]


def test_an_escalation_opens_a_ticket_first_and_the_escalation_names_it(tmp_path: Path) -> None:
    cursor = ScriptedCursor(
        [
            tool_reply(
                "1", "open_ticket", {"subject": "Brakes fail", "details": "My brakes fail."}
            ),
            tool_reply("2", "escalate", {"ticket_id": "HD-5001", "reason": "safety"}),
            text_reply("3", "A person will reply on ticket HD-5001."),
        ]
    )

    trace = turns(tmp_path, cursor, "My brakes fail on hills, I want a person.")

    escalated = json.loads(cursor.requests[2]["messages"][-1]["content"][0]["content"])
    assert escalated == {
        "escalation_id": "ESC-301",
        "ticket_id": "HD-5001",
        "reason": "safety",
        "status": "queued for a person",
    }
    names = [event_fields(e)["tool"] for e in read_trace(trace) if e.type == "tool/call"]
    assert names == ["open_ticket", "escalate"]


def test_a_tool_that_raises_goes_back_to_the_model_as_an_error_result(tmp_path: Path) -> None:
    cursor = ScriptedCursor(
        [
            tool_reply("1", "escalate", {"ticket_id": "HD-9999", "reason": "asked for a person"}),
            text_reply("2", "Let me open a ticket first."),
        ]
    )

    turns(tmp_path, cursor, "Get me a person.")

    (result,) = cursor.requests[1]["messages"][-1]["content"]
    assert result["is_error"] is True
    assert result["content"].startswith("TicketNotFound: No ticket HD-9999")


def test_the_probe_observes_the_platforms_prompt_tools_and_model() -> None:
    observation = adapter(ScriptedCursor([text_reply("1", "")])).observe()

    assert observation.system_prompt == platform.SYSTEM_PROMPT
    assert observation.tool_schemas == platform.TOOL_SCHEMAS
    assert observation.model == "claude-sonnet-5"


def test_the_prompt_splits_into_persona_rules_and_tone_sections() -> None:
    sections = split_sections(platform.SYSTEM_PROMPT)

    assert [slug for slug, _, _ in sections] == ["persona", "rules", "tone"]
    rules = sections[1][2]
    assert [line[:3] for line in rules.splitlines() if line[:1].isdigit()] == [
        "1. ",
        "2. ",
        "3. ",
        "4. ",
        "5. ",
    ]


def test_the_deployed_set_follows_the_connector_convention() -> None:
    assert set(platform.DEPLOYED) == {"prompts", "tools", "model", "provider", "flows"}
    assert set(platform.DEPLOYED["flows"]) == {"open_ticket_flow"}  # ticket 28
    assert platform.DEPLOYED["provider"] == "anthropic"
    assert all(name == schema["name"] for name, schema in platform.DEPLOYED["tools"].items())


def test_the_platforms_proxy_rows_are_the_committed_evidence_fixture() -> None:
    """Decision 40, as amended after ticket 26: one set of rows, in two homes."""
    assert json.loads(PROXY_ROWS.read_text(encoding="utf-8")) == platform.EVIDENCE["proxy"]


# --- the tools ---


def test_a_search_returns_the_articles_sharing_half_the_querys_words_best_first() -> None:
    search = helpdesk.make_tools()["search_articles"]

    assert [a["id"] for a in search(query="change billing address")] == ["KB-104"]
    assert [a["id"] for a in search(query="How do I download my invoices?")] == ["KB-117"]
    assert [a["id"] for a in search(query="change the card")] == ["KB-104", "KB-122"]
    assert search(query="refund policy") == []
    assert search(query="how do I") == []


def test_every_tool_argument_is_keyword_only() -> None:
    tools = helpdesk.make_tools()

    with pytest.raises(TypeError):
        tools["search_articles"]("billing")
    with pytest.raises(TypeError):
        tools["open_ticket"]("subject", "details")


def test_each_session_gets_its_own_tickets() -> None:
    first, second = helpdesk.make_tools(), helpdesk.make_tools()

    assert first["open_ticket"](subject="a", details="b")["ticket_id"] == "HD-5001"
    assert first["open_ticket"](subject="c", details="d")["ticket_id"] == "HD-5002"
    assert second["open_ticket"](subject="e", details="f")["ticket_id"] == "HD-5001"
    with pytest.raises(helpdesk.TicketNotFound):
        second["escalate"](ticket_id="HD-5002", reason="not this session's")


def test_the_help_desk_imports_nothing_from_agentdiag_beyond_its_own_package() -> None:
    """As the toy: the Adapter instruments it from outside (D6), so it imports only the
    standard library and itself."""
    root = Path(helpdesk.__file__).parent
    for module in sorted(root.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                continue
            for name in imported:
                assert name.startswith("agentdiag.examples.helpdesk") or (
                    name.split(".")[0] in sys.stdlib_module_names
                ), f"{module.name} imports {name!r}"
