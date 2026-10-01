"""What every Adapter must do, whatever it drives (D9).

An Adapter for an HTTP endpoint and an Adapter for a Python callable capture wildly
different things, but a Score has to mean the same thing behind either one. These are the
behaviours that make that true: the session lifecycle a Trial depends on, a session a
continuing Scenario resumes into its own Trace (phase-5 decision 13), Fixtures visible in
the Trace, an answer before opening on whether a Scenario's Fixtures can be applied
(phase-5 decision 12), a Fidelity on every Span so an Eval can refuse to judge below its
minimum, unknown tool arguments that cannot be read as empty ones, a tool the Manifest
marks `kind: retrieval` recorded as a `retrieval` Span and every other as a `tool_call`
(D37, ADR-0006 §2), and a `live` Adapter that will not open by accident.

A test module subclasses `AdapterConformance`, overrides the three hooks, and inherits
every test. Phase 8's HTTP Adapter subclasses the same class. Each method is named as the
specification sentence it enforces.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from agentdiag.adapter.base import Adapter, Fixture, LiveSideEffectsRefused
from agentdiag.trace import TraceWriter, event_fields, project_spans, read_trace
from agentdiag.types import SideEffectClass, ToolKind


class AdapterConformance:
    """The suite. Subclass it, override `make_adapter` and `make_trace`, inherit the tests."""

    # --- what a subclass supplies ---

    def make_adapter(
        self,
        tmp_path: Path,
        *,
        side_effects: SideEffectClass = "none",
        tool_kinds: Mapping[str, ToolKind] | None = None,
    ) -> Adapter:
        """An Adapter ready to open, declaring `side_effects`, with the Manifest's
        `tools.<name>.kind` as `tool_kinds`."""
        raise NotImplementedError

    def make_trace(self, tmp_path: Path) -> TraceWriter:
        """A TraceWriter for one Trial, with whatever clock the subclass wants."""
        raise NotImplementedError

    def make_adapter_that_cannot_apply_fixtures(self, tmp_path: Path) -> Adapter:
        """An Adapter whose Target has no way to receive Fixtures (decision 12)."""
        raise NotImplementedError

    def a_message(self) -> str:
        """A user Turn the fake Target behind this Adapter can answer."""
        return "Hello"

    def a_tool(self) -> str:
        """The name of a tool the fake Target executes when it answers `a_message`."""
        raise NotImplementedError

    # --- the behaviours ---

    def test_a_session_opens_delivers_and_closes(self, tmp_path: Path) -> None:
        adapter = self.make_adapter(tmp_path)
        trace = self.make_trace(tmp_path)
        trace.start(trace_id="r/s/1", scenario="s", run="r", trial=1)

        session = adapter.open(trace)
        with trace.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented"):
            reply = session.deliver(self.a_message())
        session.close()
        trace.end("completed")
        trace.close()

        assert isinstance(reply, str)
        events = read_trace(trace.path)
        assert events[0].type == "trace/start"
        assert events[-1].type == "trace/end"

    def test_closing_a_session_twice_is_an_error(self, tmp_path: Path) -> None:
        """A Trial closes its session exactly once; a second close is a bug, not a no-op."""
        adapter = self.make_adapter(tmp_path)
        trace = self.make_trace(tmp_path)
        trace.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
        session = adapter.open(trace)
        session.close()

        # The class is the Adapter's to choose; what conformance fixes is that closing
        # twice raises rather than passing silently.
        with pytest.raises(RuntimeError):
            session.close()
        trace.close()

    def test_an_applied_fixture_is_recorded_as_an_event(self, tmp_path: Path) -> None:
        """A Fixture that is not in the Trace cannot be cited when a Score blames it."""
        adapter = self.make_adapter(tmp_path)
        trace = self.make_trace(tmp_path)
        trace.start(trace_id="r/s/1", scenario="s", run="r", trial=1)

        fixture = Fixture(name="an-account", kind="identity", data={"id": "acct-1"})
        session = adapter.open(trace, fixtures=[fixture])
        session.close()
        trace.end("completed")
        trace.close()

        applied = [event for event in read_trace(trace.path) if event.type == "fixture/applied"]
        assert len(applied) == 1
        assert applied[0].actor == "adapter"
        assert event_fields(applied[0])["fixture"] == "an-account"
        assert event_fields(applied[0])["mechanism"]

    def test_no_fixtures_means_no_fixture_events(self, tmp_path: Path) -> None:
        adapter = self.make_adapter(tmp_path)
        trace = self.make_trace(tmp_path)
        trace.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
        adapter.open(trace).close()
        trace.end("completed")
        trace.close()

        assert not [event for event in read_trace(trace.path) if event.type == "fixture/applied"]

    def test_an_adapter_that_can_apply_a_fixture_gives_no_reason_not_to(
        self, tmp_path: Path
    ) -> None:
        """Decision 12: `check_fixtures` answers at preflight; None: `open` will apply them."""
        fixture = Fixture(name="an-account", kind="identity", data={"id": "acct-1"})

        assert self.make_adapter(tmp_path).check_fixtures([fixture]) is None

    def test_no_fixtures_are_always_applicable(self, tmp_path: Path) -> None:
        assert self.make_adapter(tmp_path).check_fixtures([]) is None
        assert self.make_adapter_that_cannot_apply_fixtures(tmp_path).check_fixtures([]) is None

    def test_an_adapter_that_cannot_apply_a_fixture_says_why_and_open_agrees(
        self, tmp_path: Path
    ) -> None:
        """The reason is what `run.json` records as `fixture_unavailable` detail; `open` with
        the same Fixtures refuses, so a preflight answer can never be contradicted later."""
        adapter = self.make_adapter_that_cannot_apply_fixtures(tmp_path)
        fixture = Fixture(name="an-account", kind="identity", data={"id": "acct-1"})

        reason = adapter.check_fixtures([fixture])

        assert isinstance(reason, str) and reason.strip()
        trace = self.make_trace(tmp_path)
        trace.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
        with pytest.raises(Exception):  # noqa: B017 - the class is the Adapter's to choose
            adapter.open(trace, fixtures=[fixture])
        trace.close()
        assert not [event for event in read_trace(trace.path) if event.type == "fixture/applied"]

    def test_every_span_declares_its_fidelity(self, tmp_path: Path) -> None:
        """An Eval declares the minimum Fidelity it needs; a Span without one cannot be judged."""
        events = self._one_turn(tmp_path)

        starts = [event for event in events if event.type == "span/start"]
        assert starts
        for start in starts:
            assert event_fields(start)["fidelity"] in {
                "instrumented",
                "reconstructed",
                "observed",
            }
        for span in project_spans(events):
            assert span.fidelity

    def test_tool_arguments_the_adapter_could_not_see_are_unknown_and_not_empty(
        self, tmp_path: Path
    ) -> None:
        """The lesson: "no arguments" and "we could not look" differ."""
        events = self._one_turn(tmp_path)

        calls = [event for event in events if event.type == "tool/call"]
        assert calls, "the fake Target behind this Adapter must execute at least one tool"
        unknown = [call for call in calls if "arguments" in event_fields(call)["not_observed"]]
        assert unknown, "the fake Target must include one call whose arguments cannot be bound"
        for call in unknown:
            assert event_fields(call)["arguments"] is None

    def test_a_tool_marked_retrieval_is_recorded_as_a_retrieval_span(self, tmp_path: Path) -> None:
        """D37, ADR-0006 §2: the Manifest says which tools are lookups; the Trace says so too."""
        events = self._one_turn(tmp_path, tool_kinds={self.a_tool(): "retrieval"})

        spans = [span for span in project_spans(events) if _tool_of(span) == self.a_tool()]
        assert spans, "the fake Target must execute the tool `a_tool` names"
        assert {span.kind for span in spans} == {"retrieval"}

    def test_a_tool_not_marked_retrieval_is_recorded_as_a_tool_call_span(
        self, tmp_path: Path
    ) -> None:
        unmarked: tuple[Mapping[str, ToolKind] | None, ...] = (None, {self.a_tool(): "action"})
        for kinds in unmarked:
            events = self._one_turn(tmp_path, tool_kinds=kinds)

            spans = [span for span in project_spans(events) if _tool_of(span) == self.a_tool()]
            assert spans
            assert {span.kind for span in spans} == {"tool_call"}

    def test_a_retrieval_span_carries_the_same_attributes_a_tool_call_span_does(
        self, tmp_path: Path
    ) -> None:
        """The kind says what the tool is for; nothing an Eval reads about the call moves."""
        marked = self._one_turn(tmp_path, tool_kinds={self.a_tool(): "retrieval"})
        unmarked = self._one_turn(tmp_path)

        def starts(events: list[Any]) -> list[dict[str, Any]]:
            return [
                event_fields(event)["attributes"]
                for event in events
                if event.type == "span/start"
                and event_fields(event)["kind"] != "turn"
                and event_fields(event)["kind"] != "llm_call"
            ]

        assert starts(marked) == starts(unmarked)

    def test_a_rebound_session_writes_into_the_new_trace_and_not_the_old_one(
        self, tmp_path: Path
    ) -> None:
        """Decision 13: a continuing Scenario resumes the session and records its own Trace."""
        adapter = self.make_adapter(tmp_path)
        first = self.make_trace(tmp_path)
        first.start(trace_id="r/a/1", scenario="a", run="r", trial=1)
        session = adapter.open(first)
        first.end("completed")
        first.close()

        second = self.make_trace(tmp_path)
        second.start(trace_id="r/b/1", scenario="b", run="r", trial=1)
        session.rebind(second)
        with second.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented"):
            session.deliver(self.a_message())
        session.close()
        second.end("completed")
        second.close()

        assert not [span for span in project_spans(read_trace(first.path)) if span.kind != "turn"]
        emitted = {span.kind for span in project_spans(read_trace(second.path))}
        assert emitted - {"turn"}, "the Adapter's Spans must land in the Trace it was rebound to"

    def test_rebinding_a_closed_session_is_an_error(self, tmp_path: Path) -> None:
        adapter = self.make_adapter(tmp_path)
        trace = self.make_trace(tmp_path)
        trace.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
        session = adapter.open(trace)
        session.close()

        with pytest.raises(RuntimeError):
            session.rebind(self.make_trace(tmp_path))
        trace.close()

    def test_the_adapter_declares_its_side_effect_class(self, tmp_path: Path) -> None:
        description = self.make_adapter(tmp_path).describe()

        assert description.side_effects in {"none", "sandboxed", "live"}
        assert description.fidelity in {"instrumented", "reconstructed", "observed"}
        assert description.kind
        assert description.environment

    def test_a_live_adapter_refuses_to_open_without_the_flag(self, tmp_path: Path) -> None:
        """ADR-0001 point 5: nothing touches a live system by accident."""
        adapter = self.make_adapter(tmp_path, side_effects="live")
        trace = self.make_trace(tmp_path)
        trace.start(trace_id="r/s/1", scenario="s", run="r", trial=1)

        with pytest.raises(LiveSideEffectsRefused):
            adapter.open(trace)
        trace.close()

    # --- shared ---

    def _one_turn(
        self, tmp_path: Path, *, tool_kinds: Mapping[str, ToolKind] | None = None
    ) -> list[Any]:
        adapter = self.make_adapter(tmp_path, tool_kinds=tool_kinds)
        trace = self.make_trace(tmp_path)
        trace.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
        session = adapter.open(trace)
        with trace.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented"):
            session.deliver(self.a_message())
        session.close()
        trace.end("completed")
        trace.close()
        return read_trace(trace.path)


def _tool_of(span: Any) -> Any:
    return span.attributes.get("gen_ai.tool.name")


__all__ = ["AdapterConformance"]
