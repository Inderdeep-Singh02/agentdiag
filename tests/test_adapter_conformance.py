"""Seam 3: the conformance suite against the in-process Adapter with a fake Target behind it.

The fake, not the toy Target, because the suite tests the Adapter. `tests.fakes.opaque_target`
declares its tool as `*args`, which is the only way to exercise the behaviour that matters
most here: arguments the Adapter could not see are recorded as unknown, never as empty.
`tests.fakes.fixtureless_target` has a factory with no `fixtures` keyword, the Target an
Adapter must say it cannot apply Fixtures to (phase-5 decision 12).

The suite runs twice: with the replay transport under the capture, and with the Claude Code
transport over a fake CLI playing the same recording (ticket 20), because the Adapter must
exhibit the same behaviours whichever path carries the Target's calls.

And once over the second toy, the help desk (ticket 13, decision 38), whose factory returns
an object and whose tools are bound methods with keyword-only arguments: the Adapter must
instrument that shape too. Three behaviours need what only a fake has — a tool whose
arguments cannot be bound, and a factory that takes Fixtures — and are skipped there, named.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from agentdiag.adapter import InProcessAdapter
from agentdiag.adapter.base import Adapter
from agentdiag.adapter.conformance import AdapterConformance
from agentdiag.model.claude_code import ClaudeCodeCli
from agentdiag.model.credentials import CredentialSource
from agentdiag.trace import TraceWriter
from agentdiag.types import ToolKind
from tests.fakes.claude_code_cli import Call, FakeCli, Script
from tests.fakes.scripted_model import ScriptedCursor, text_reply, tool_reply

RECORDING = Path(__file__).parent / "fixtures" / "recordings" / "fake-opaque.jsonl"


class TestInProcessAdapterConformance(AdapterConformance):
    """The in-process Adapter must exhibit everything every Adapter must exhibit (D9)."""

    def make_adapter(
        self,
        tmp_path: Path,
        *,
        side_effects: str = "none",
        tool_kinds: Mapping[str, ToolKind] | None = None,
    ) -> Adapter:
        config: dict[str, Any] = {
            "kind": "inprocess",
            "side_effects": side_effects,
            "environments": {
                "default": "local",
                "local": {
                    "factory": "tests.fakes.opaque_target:make_target",
                    "tools": "tests.fakes.opaque_target:TOOLS",
                    "model": "claude-sonnet-5",
                },
            },
        }
        return InProcessAdapter(
            config, environment="local", replay=RECORDING, tool_kinds=tool_kinds
        )

    def make_adapter_that_cannot_apply_fixtures(self, tmp_path: Path) -> Adapter:
        config: dict[str, Any] = {
            "kind": "inprocess",
            "side_effects": "none",
            "environments": {
                "default": "local",
                "local": {
                    "factory": "tests.fakes.fixtureless_target:make_target",
                    "tools": "tests.fakes.fixtureless_target:TOOLS",
                },
            },
        }
        return InProcessAdapter(config, environment="local")

    def a_tool(self) -> str:
        return "opaque"

    def make_trace(self, tmp_path: Path) -> TraceWriter:
        return TraceWriter(tmp_path / f"trace-{next(_counter)}.jsonl", clock=_scripted())


class TestInProcessAdapterThroughClaudeCodeConformance(TestInProcessAdapterConformance):
    """The same Adapter, its Target's calls carried by the Claude Code transport (ticket 20)."""

    def make_adapter(
        self,
        tmp_path: Path,
        *,
        side_effects: str = "none",
        tool_kinds: Mapping[str, ToolKind] | None = None,
    ) -> Adapter:
        replayed = super().make_adapter(tmp_path, side_effects=side_effects, tool_kinds=tool_kinds)
        assert isinstance(replayed, InProcessAdapter)
        return InProcessAdapter(
            replayed.config,
            environment="local",
            tool_kinds=tool_kinds,
            credentials=LOGIN,
            claude_code_session=FakeCli(_recorded_turn()),
        )


class TestHelpDeskAdapterConformance(TestInProcessAdapterConformance):
    """The in-process Adapter over the help desk, its model scripted to search once."""

    NOT_THE_HELP_DESKS = "the help desk's factory takes no Fixtures; the fakes cover this"

    def make_adapter(
        self,
        tmp_path: Path,
        *,
        side_effects: str = "none",
        tool_kinds: Mapping[str, ToolKind] | None = None,
    ) -> Adapter:
        return InProcessAdapter(
            _help_desk(side_effects),
            environment="local",
            replay=ScriptedCursor(
                [
                    tool_reply("hd1", "search_articles", {"query": "download invoices"}),
                    text_reply("hd2", "Billing, then Invoices (KB-117)."),
                ]
            ),
            tool_kinds=tool_kinds,
        )

    def make_adapter_that_cannot_apply_fixtures(self, tmp_path: Path) -> Adapter:
        return InProcessAdapter(_help_desk("none"), environment="local")

    def a_message(self) -> str:
        return "How do I download my invoices?"

    def a_tool(self) -> str:
        return "search_articles"

    def test_tool_arguments_the_adapter_could_not_see_are_unknown_and_not_empty(
        self, tmp_path: Path
    ) -> None:
        pytest.skip(
            "every help desk tool argument is keyword-only and seen; opaque_target covers this"
        )

    def test_an_applied_fixture_is_recorded_as_an_event(self, tmp_path: Path) -> None:
        pytest.skip(self.NOT_THE_HELP_DESKS)

    def test_an_adapter_that_can_apply_a_fixture_gives_no_reason_not_to(
        self, tmp_path: Path
    ) -> None:
        pytest.skip(self.NOT_THE_HELP_DESKS)


def _help_desk(side_effects: str) -> dict[str, Any]:
    return {
        "kind": "inprocess",
        "side_effects": side_effects,
        "environments": {
            "default": "local",
            "local": {
                "factory": "agentdiag.examples.helpdesk:make_helpdesk",
                "tools": "agentdiag.examples.helpdesk:make_tools",
                "model": "claude-sonnet-5",
            },
        },
    }


LOGIN = CredentialSource(
    kind="claude_code",
    detail="Claude Code login (2.1.280)",
    cli=ClaudeCodeCli(path="/opt/claude/bin/claude", version="2.1.280"),
)


def _recorded_turn() -> Script:
    """The opaque Target's recorded Turn, as the fake CLI streams it."""
    calls = []
    for line in RECORDING.read_text(encoding="utf-8").splitlines():
        response = json.loads(line)["response"]
        calls.append(
            Call(
                response["id"],
                response["content"],
                response["stop_reason"],
                response["usage"],
                response["usage"],
            )
        )
    return Script(calls=calls)


def _counting() -> Any:
    number = 0
    while True:
        number += 1
        yield number


_counter = _counting()


def _scripted() -> Any:
    """One millisecond per Event, so a conformance Trace is reproducible."""
    state = {"now": 1_000_000_000_000}

    def clock() -> int:
        state["now"] += 1
        return state["now"]

    return clock
