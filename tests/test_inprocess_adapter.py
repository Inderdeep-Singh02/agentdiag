"""What the in-process Adapter records, seen only through the Events it emits (Seam 3).

Adapter-specific capture — SDK wrapping, tool wrapping, the replay transport — is tested
only through the Trace, never by reaching inside the Adapter (spec, Testing Decisions).
So every assertion here reads a Trace file back.
"""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from agentdiag.adapter import InProcessAdapter
from agentdiag.model.claude_code import Backend, ClaudeCodeCli
from agentdiag.model.credentials import CredentialSource
from agentdiag.model.replay import ReplayMismatch
from agentdiag.trace import Event, TraceWriter, event_fields, project_spans, read_trace

RECORDINGS = Path(__file__).parent / "fixtures" / "recordings"


def scripted_clock(start: int = 1_000_000_000_000, step: int = 1) -> Any:
    """A clock that advances one millisecond per Event, so `ts` is predictable."""
    state = {"now": start - step}

    def clock() -> int:
        state["now"] += step
        return state["now"]

    return clock


def config(**overrides: Any) -> dict[str, Any]:
    environment: dict[str, Any] = {
        "factory": "agentdiag.examples.toy:make_target",
        "tools": "agentdiag.examples.toy:make_tools",
        "model": "claude-sonnet-5",
    }
    environment.update(overrides)
    return {
        "kind": "inprocess",
        "side_effects": "none",
        "environments": {"default": "local", "local": environment},
    }


def deliver_once(
    tmp_path: Path, recording: Path, message: str = "Hi, I'd like to cancel order NB-1042."
) -> list[Event]:
    """Run one Turn through the Adapter in replay and return the Trace it wrote."""
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path, clock=scripted_clock())
    adapter = InProcessAdapter(config(), environment="local", replay=recording)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    session = adapter.open(writer)
    with writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented") as turn:
        turn.event("message", actor="agentdiag", role="user", content=message)
        reply = session.deliver(message)
        turn.event("message", actor="target", role="assistant", content=reply)
    session.close()
    writer.end("completed")
    writer.close()
    return read_trace(path)


def spans_of(events: Sequence[Event], kind: str) -> list[Any]:
    return [span for span in project_spans(events) if span.kind == kind]


def events_of(events: Sequence[Event], type: str) -> list[Event]:
    return [event for event in events if event.type == type]


def test_a_tool_span_nests_under_the_llm_call_that_requested_it(tmp_path: Path) -> None:
    events = deliver_once(tmp_path, RECORDINGS / "toy-cancel-target.jsonl")

    tool_spans = spans_of(events, "tool_call")
    llm_spans = spans_of(events, "llm_call")
    assert [span.parent_span_id for span in tool_spans] == ["llm_call-1", "llm_call-2"]
    assert [span.name for span in tool_spans] == [
        "execute_tool lookup_order",
        "execute_tool cancel_order",
    ]
    # And its interval lies inside its parent's, which is what a flame graph needs.
    by_id = {span.span_id: span for span in llm_spans}
    for tool in tool_spans:
        parent = by_id[str(tool.parent_span_id)]
        assert parent.start_ms <= tool.start_ms
        assert tool.end_ms is not None and parent.end_ms is not None
        assert tool.end_ms <= parent.end_ms


def test_a_tool_span_carries_the_call_id_of_the_block_that_asked_for_it(tmp_path: Path) -> None:
    events = deliver_once(tmp_path, RECORDINGS / "toy-cancel-target.jsonl")

    calls = events_of(events, "tool/call")
    assert [event_fields(call)["call_id"] for call in calls] == [
        "toolu_01LookupNB1042",
        "toolu_02CancelNB1042",
    ]
    assert [span.attributes["gen_ai.tool.call.id"] for span in spans_of(events, "tool_call")] == [
        "toolu_01LookupNB1042",
        "toolu_02CancelNB1042",
    ]
    assert all(event_fields(call)["not_observed"] == [] for call in calls)


def test_an_llm_call_that_requested_tools_ends_when_its_last_tool_returns(
    tmp_path: Path,
) -> None:
    """Decision 1: the Span encloses the tools it asked for, so the nesting is honest."""
    events = deliver_once(tmp_path, RECORDINGS / "toy-cancel-target.jsonl")
    by_seq = {event.seq: event for event in events}

    first_llm_end = next(
        event for event in events if event.type == "span/end" and event.span_id == "llm_call-1"
    )
    first_tool_end = next(
        event for event in events if event.type == "span/end" and event.span_id == "tool_call-1"
    )
    assert first_tool_end.seq < first_llm_end.seq
    assert by_seq[first_llm_end.seq - 1].span_id == "tool_call-1"


def test_an_llm_call_that_ended_the_turn_ends_at_its_response(tmp_path: Path) -> None:
    events = deliver_once(tmp_path, RECORDINGS / "toy-cancel-target.jsonl")

    last = spans_of(events, "llm_call")[-1]
    assert last.attributes["gen_ai.response.finish_reasons"] == ["end_turn"]
    response = next(
        event for event in events if event.type == "response" and event.span_id == last.span_id
    )
    end = next(
        event for event in events if event.type == "span/end" and event.span_id == last.span_id
    )
    assert end.seq == response.seq + 1  # nothing came between them


def test_every_llm_call_records_the_requested_and_the_resolved_model(tmp_path: Path) -> None:
    events = deliver_once(tmp_path, RECORDINGS / "toy-cancel-target.jsonl")

    for span in spans_of(events, "llm_call"):
        assert span.attributes["gen_ai.request.model"] == "claude-sonnet-5"
        assert span.attributes["gen_ai.response.model"] == "claude-sonnet-5-20260815"
        assert span.attributes["gen_ai.provider.name"] == "anthropic"
        assert span.attributes["gen_ai.usage.input_tokens"] > 0
        assert span.attributes["gen_ai.usage.output_tokens"] > 0


def test_a_response_event_carries_the_body_and_no_latency(tmp_path: Path) -> None:
    """Model latency is the projection `response.ts - request.ts`, never a stored field (D11)."""
    events = deliver_once(tmp_path, RECORDINGS / "toy-cancel-target.jsonl")

    for response in events_of(events, "response"):
        fields = event_fields(response)
        assert set(fields) == {"body"}
        assert fields["body"]["type"] == "message"


def test_the_toy_target_imports_nothing_from_agentdiag_beyond_its_own_package() -> None:
    """The Target does not import agentdiag: the Adapter instruments it from outside (D6).

    Walked as an AST rather than matched as text, so a renamed import, a conditional one
    or one inside a function cannot slip past the guard.
    """
    import agentdiag.examples.toy as toy

    allowed = {"anthropic", *sys.stdlib_module_names}
    root = Path(toy.__file__).parent
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
                root_name = name.split(".")[0]
                assert name.startswith("agentdiag.examples.toy") or root_name in allowed, (
                    f"{module.name} imports {name!r}"
                )


def test_describe_declares_kind_fidelity_side_effects_and_the_environment() -> None:
    adapter = InProcessAdapter(config(), environment="local")

    description = adapter.describe()

    assert description.kind == "inprocess"
    assert description.fidelity == "instrumented"
    assert description.side_effects == "none"
    assert description.environment == "local"
    assert description.observes == ["prompts", "tools", "model", "provider"]
    assert description.config["factory"] == "agentdiag.examples.toy:make_target"


def test_describe_names_the_backend_the_targets_calls_take() -> None:
    """Ticket 20, decision 28: replay first, then the Claude Code login, else the API."""
    login = CredentialSource(
        kind="claude_code",
        detail="Claude Code login (2.1.280)",
        cli=ClaudeCodeCli(path="/opt/claude/bin/claude", version="2.1.280"),
    )
    key = CredentialSource(kind="api_key", detail="ANTHROPIC_API_KEY")
    replay = RECORDINGS / "toy-cancel-target.jsonl"

    backends = {
        "nothing": InProcessAdapter(config(), environment="local").describe().backend,
        "key": InProcessAdapter(config(), environment="local", credentials=key).describe().backend,
        "login": InProcessAdapter(config(), environment="local", credentials=login)
        .describe()
        .backend,
        "login and replay": InProcessAdapter(
            config(), environment="local", credentials=login, replay=replay
        )
        .describe()
        .backend,
    }

    assert backends == {
        "nothing": Backend(kind="anthropic_api"),
        "key": Backend(kind="anthropic_api"),
        "login": Backend(kind="claude_code", cli_version="2.1.280"),
        "login and replay": Backend(kind="replay"),
    }


def test_a_replay_mismatch_surfaces_and_is_recorded_as_an_errored_llm_call(
    tmp_path: Path,
) -> None:
    """Fail loud: no retry hides it, no handler swallows it, and the Trace says so."""
    recording = tmp_path / "wrong.jsonl"
    recording.write_text(
        json.dumps(
            {
                "request": {"model": "nothing-like-it", "max_tokens": 1, "messages": []},
                "response": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path, clock=scripted_clock())
    adapter = InProcessAdapter(config(), environment="local", replay=recording)
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    session = adapter.open(writer)

    with (
        pytest.raises(ReplayMismatch),
        writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented"),
    ):
        session.deliver("Hi")
    writer.end("target_error", error="replay mismatch")
    writer.close()

    events = read_trace(path)
    errors = events_of(events, "error")
    assert len(errors) == 1
    assert event_fields(errors[0])["error_type"] == "ReplayMismatch"
    llm = spans_of(events, "llm_call")[0]
    assert llm.status == "error"


def test_an_unconsumed_recording_is_an_error(tmp_path: Path) -> None:
    from agentdiag.model.replay import RecordingNotConsumed

    events_path = tmp_path / "trace.jsonl"
    writer = TraceWriter(events_path, clock=scripted_clock())
    adapter = InProcessAdapter(
        config(), environment="local", replay=RECORDINGS / "toy-cancel-target.jsonl"
    )
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    adapter.open(writer)
    writer.end("completed")
    writer.close()

    with pytest.raises(RecordingNotConsumed) as raised:
        adapter.assert_consumed()
    assert "3 of 3" in str(raised.value)


def test_a_tool_that_raises_is_recorded_as_an_errored_span_and_an_error_result(
    tmp_path: Path,
) -> None:
    """A tool failure is what happened, so the Trace carries it as fact, not as a gap."""
    events = deliver_once(
        tmp_path, RECORDINGS / "toy-cancel-shipped.jsonl", "Please cancel NB-0917."
    )

    result = events_of(events, "tool/result")[0]
    assert event_fields(result)["is_error"] is True
    assert "shipped" in str(event_fields(result)["result"])

    span = spans_of(events, "tool_call")[0]
    assert span.status == "error"
    assert span.attributes["error.type"] == "ValueError"
    end = next(
        event for event in events if event.type == "span/end" and event.span_id == span.span_id
    )
    assert event_fields(end)["error"]["type"] == "ValueError"


def test_a_tool_that_raises_still_closes_the_llm_call_that_requested_it(
    tmp_path: Path,
) -> None:
    events = deliver_once(
        tmp_path, RECORDINGS / "toy-cancel-shipped.jsonl", "Please cancel NB-0917."
    )

    first = spans_of(events, "llm_call")[0]
    assert first.status == "ok"  # the model call itself succeeded; the tool is what failed
    assert first.end_ms is not None


def test_a_requested_tool_the_target_never_ran_leaves_the_llm_call_open_until_deliver_returns(
    tmp_path: Path,
) -> None:
    """Every `span/start` in a finished Trace has its `span/end`, even when a Target gave up."""
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path, clock=scripted_clock())
    adapter = InProcessAdapter(
        {
            "kind": "inprocess",
            "side_effects": "none",
            "environments": {
                "default": "local",
                "local": {
                    "factory": "tests.fakes.idle_target:make_target",
                    "tools": "tests.fakes.idle_target:TOOLS",
                    "model": "claude-sonnet-5",
                },
            },
        },
        environment="local",
        replay=RECORDINGS / "fake-idle.jsonl",
    )
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    session = adapter.open(writer)
    with writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented"):
        session.deliver("Hello")
    session.close()
    writer.end("completed")
    writer.close()

    events = read_trace(path)
    llm = spans_of(events, "llm_call")[0]
    assert not spans_of(events, "tool_call")  # the tool was requested and never executed
    assert llm.status == "ok"
    assert llm.end_ms is not None
    # Its response asked for a tool, so it was not ended at the response: it was ended
    # when `deliver` returned, which is the last thing to happen inside the Turn.
    end = next(
        event for event in events if event.type == "span/end" and event.span_id == llm.span_id
    )
    turn_end = next(
        event for event in events if event.type == "span/end" and event.span_id == "turn-1"
    )
    assert end.seq == turn_end.seq - 1
    assert event_fields(end)["attributes"]["gen_ai.response.finish_reasons"] == ["tool_use"]


def test_a_manifest_that_declares_no_tools_hands_the_factory_an_empty_mapping(
    tmp_path: Path,
) -> None:
    """`agentdiag init --adapter` with no `--tools` omits the key, and the Target still runs.

    A Target that keeps its tools inside its own factory has no module path to name, and
    being made to invent one would put a pointer to nothing in the Manifest (ticket 02).
    """
    writer = TraceWriter(tmp_path / "trace.jsonl", clock=scripted_clock())
    adapter = InProcessAdapter(
        {
            "kind": "inprocess",
            "side_effects": "none",
            "environments": {
                "default": "local",
                "local": {"factory": "tests.fakes.idle_target:make_target"},
            },
        },
        environment="local",
        replay=RECORDINGS / "fake-idle.jsonl",
    )

    adapter.check()  # the missing key is not a Manifest error
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    session = adapter.open(writer)
    with writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented"):
        session.deliver("Hello")
    session.close()
    writer.end("completed")
    writer.close()

    events = read_trace(writer.path)
    assert spans_of(events, "llm_call"), "the Target was built and driven"
    assert not spans_of(events, "tool_call"), "no tool was declared, so none was wrapped"


def test_a_manifest_that_declares_no_factory_is_refused_by_name(tmp_path: Path) -> None:
    """The factory is what builds the Target; without it there is nothing to drive."""
    adapter = InProcessAdapter(
        {
            "kind": "inprocess",
            "side_effects": "none",
            "environments": {
                "default": "local",
                "local": {"tools": "tests.fakes.idle_target:TOOLS"},
            },
        },
        environment="local",
    )

    with pytest.raises(KeyError, match="factory"):
        adapter.check()


def test_a_factory_that_asks_for_fixtures_is_handed_them(tmp_path: Path) -> None:
    """A Target that can seed itself gets the Fixtures; one that cannot is not broken by them."""
    from agentdiag.scenario.models import Fixture

    writer = TraceWriter(tmp_path / "trace.jsonl", clock=scripted_clock())
    adapter = InProcessAdapter(
        {
            "kind": "inprocess",
            "side_effects": "none",
            "environments": {
                "default": "local",
                "local": {
                    "factory": "tests.fakes.seeded_target:make_target",
                    "tools": "tests.fakes.seeded_target:TOOLS",
                },
            },
        },
        environment="local",
    )
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    session = adapter.open(writer, fixtures=[Fixture(name="an-account", kind="identity")])
    reply = session.deliver("Who am I?")
    session.close()
    writer.end("completed")
    writer.close()

    assert reply == "an-account"
    applied = events_of(read_trace(writer.path), "fixture/applied")
    assert event_fields(applied[0])["detail"]["kind"] == "identity"


def test_two_sessions_from_one_adapter_each_start_from_the_seed_orders(
    tmp_path: Path,
) -> None:
    """Target state is per-session: one Trial's cancellation cannot leak into the next.

    The Adapter builds the tools once per `open()`, so nothing outside the Target has to
    reach in and reset it — which would be the hidden entry point ADR-0001 point 2 forbids.
    """
    statuses = []
    for number in (1, 2):
        adapter = InProcessAdapter(
            config(), environment="local", replay=RECORDINGS / "toy-cancel-target.jsonl"
        )
        writer = TraceWriter(tmp_path / f"trace-{number}.jsonl", clock=scripted_clock())
        writer.start(trace_id=f"r/s/{number}", scenario="s", run="r", trial=number)
        session = adapter.open(writer)
        with writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented"):
            session.deliver("Hi, I'd like to cancel order NB-1042.")
        session.close()
        writer.end("completed")
        writer.close()

        lookup = next(
            event
            for event in read_trace(writer.path)
            if event.type == "tool/result" and event_fields(event)["tool"] == "lookup_order"
        )
        statuses.append(event_fields(lookup)["result"]["status"])

    assert statuses == ["processing", "processing"]


def _adapter_for(factory: str, tools: str, recording: str, **options: Any) -> InProcessAdapter:
    environment: dict[str, Any] = {"factory": factory, "tools": tools}
    if options:
        environment["options"] = options
    return InProcessAdapter(
        {
            "kind": "inprocess",
            "side_effects": "none",
            "environments": {"default": "local", "local": environment},
        },
        environment="local",
        replay=RECORDINGS / recording,
    )


def test_a_factory_that_cannot_take_fixtures_refuses_to_open_and_writes_no_event(
    tmp_path: Path,
) -> None:
    """A Scenario's Fixtures either reach the Target or the Run stops: never silently lost."""
    from agentdiag.scenario.models import Fixture

    writer = TraceWriter(tmp_path / "trace.jsonl", clock=scripted_clock())
    adapter = _adapter_for(
        "tests.fakes.idle_target:make_target",
        "tests.fakes.idle_target:TOOLS",
        "fake-idle.jsonl",
    )
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)

    with pytest.raises(TypeError) as raised:
        adapter.open(writer, fixtures=[Fixture(name="an-account", kind="identity")])
    writer.close()

    assert "does not accept fixtures" in str(raised.value)
    assert "tests.fakes.idle_target:make_target" in str(raised.value)
    assert "declares 1" in str(raised.value)
    # And the refused open left no Event claiming the Fixture was applied.
    assert not events_of(read_trace(writer.path), "fixture/applied")


def test_asking_whether_fixtures_apply_reads_the_factorys_signature_and_builds_nothing(
    tmp_path: Path,
) -> None:
    """Decision 12: preflight asks without opening a session, so the Target is not built."""
    from agentdiag.scenario.models import Fixture
    from tests.fakes import fixtureless_target

    adapter = _adapter_for(
        "tests.fakes.fixtureless_target:make_target",
        "tests.fakes.fixtureless_target:TOOLS",
        "fake-idle.jsonl",
    )
    built = len(fixtureless_target.BUILT)

    reason = adapter.check_fixtures([Fixture(name="an-account", kind="identity")])

    assert reason is not None
    assert "tests.fakes.fixtureless_target:make_target does not accept fixtures" in reason
    assert len(fixtureless_target.BUILT) == built


def test_a_tool_the_model_never_requested_records_an_unobserved_call_id(
    tmp_path: Path,
) -> None:
    """Unmatched still parents under the open `llm_call`; the id is not invented."""
    writer = TraceWriter(tmp_path / "trace.jsonl", clock=scripted_clock())
    adapter = _adapter_for(
        "tests.fakes.unrequested_tool_target:make_target",
        "tests.fakes.unrequested_tool_target:TOOLS",
        "fake-unrequested-after.jsonl",
        when="after",
    )
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    session = adapter.open(writer)
    with writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented"):
        session.deliver("Hello")
    session.close()
    writer.end("completed")
    writer.close()

    events = read_trace(writer.path)
    call = events_of(events, "tool/call")[0]
    assert event_fields(call)["not_observed"] == ["gen_ai.tool.call.id"]
    assert event_fields(call)["call_id"] is None
    assert event_fields(call)["arguments"] == {"query": "unrequested"}

    span = spans_of(events, "tool_call")[0]
    assert "gen_ai.tool.call.id" not in span.attributes
    assert span.parent_span_id == "llm_call-1"


def test_a_tool_called_before_any_model_call_has_no_parent_and_no_call_id(
    tmp_path: Path,
) -> None:
    """With no `llm_call` open there is nothing to parent under, and the Trace says so."""
    writer = TraceWriter(tmp_path / "trace.jsonl", clock=scripted_clock())
    adapter = _adapter_for(
        "tests.fakes.unrequested_tool_target:make_target",
        "tests.fakes.unrequested_tool_target:TOOLS",
        "fake-unrequested-before.jsonl",
        when="before",
    )
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    session = adapter.open(writer)
    with writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented"):
        session.deliver("Hello")
    session.close()
    writer.end("completed")
    writer.close()

    events = read_trace(writer.path)
    call = events_of(events, "tool/call")[0]
    assert event_fields(call)["not_observed"] == ["gen_ai.tool.call.id"]
    assert event_fields(call)["call_id"] is None

    span = spans_of(events, "tool_call")[0]
    assert "gen_ai.tool.call.id" not in span.attributes
    assert span.parent_span_id == "turn-1"  # the enclosing Turn, no `llm_call` to nest in


# --- cost is written on the llm_call Span when it is captured (phase-5 decision 4) ---


def with_response_model(tmp_path: Path, recording: Path, model: str) -> Path:
    """The same recording with every response resolved to `model`; requests untouched."""
    lines = []
    for line in recording.read_text(encoding="utf-8").splitlines():
        exchange = json.loads(line)
        exchange["response"]["model"] = model
        lines.append(json.dumps(exchange))
    path = tmp_path / f"{model}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_each_llm_call_span_records_its_cost_in_usd_from_the_price_table(tmp_path: Path) -> None:
    """claude-sonnet-5 at $2 in and $10 out per million: 742 in, 61 out is $0.002094."""
    events = deliver_once(tmp_path, RECORDINGS / "toy-cancel-target.jsonl")

    costs = [span.attributes["llm.cost.total"] for span in spans_of(events, "llm_call")]
    assert costs == [0.002094, 0.002358, 0.002448]


def test_an_llm_call_span_records_its_prompt_and_completion_costs_under_openinference_names(
    tmp_path: Path,
) -> None:
    """ADR-0006 §4: 742 input tokens at $2 and 61 output at $10 per million."""
    events = deliver_once(tmp_path, RECORDINGS / "toy-cancel-target.jsonl")

    first = spans_of(events, "llm_call")[0].attributes
    costs = {name: value for name, value in first.items() if name.startswith("llm.cost.")}
    assert costs == {
        "llm.cost.total": 0.002094,
        "llm.cost.prompt": 0.001484,
        "llm.cost.completion": 0.00061,
        "llm.cost.prompt_details.input": 0.001484,
    }


def test_a_resolved_model_the_price_table_does_not_price_records_no_cost_at_all(
    tmp_path: Path,
) -> None:
    """Unpriced, never `0.0`: a zero would claim a bill nobody has seen."""
    recording = with_response_model(
        tmp_path, RECORDINGS / "toy-cancel-target.jsonl", "claude-mystery-1"
    )

    events = deliver_once(tmp_path, recording)

    for span in spans_of(events, "llm_call"):
        assert not [name for name in span.attributes if name.startswith("llm.cost.")]


def test_a_tool_marked_retrieval_is_a_retrieval_span_and_the_action_a_tool_call(
    tmp_path: Path,
) -> None:
    """The toy's two tools as its Manifest marks them (D37, ADR-0006 §2)."""
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path, clock=scripted_clock())
    adapter = InProcessAdapter(
        config(),
        environment="local",
        replay=RECORDINGS / "toy-cancel-target.jsonl",
        tool_kinds={"lookup_order": "retrieval", "cancel_order": "action"},
    )
    writer.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    session = adapter.open(writer)
    with writer.span("turn", actor="agentdiag", name="turn 1", fidelity="instrumented"):
        session.deliver("Hi, I'd like to cancel order NB-1042.")
    session.close()
    writer.end("completed")
    writer.close()

    tools = [
        (span.span_id, span.attributes["gen_ai.tool.name"])
        for span in project_spans(read_trace(path))
        if span.kind in {"retrieval", "tool_call"}
    ]
    assert tools == [("retrieval-1", "lookup_order"), ("tool_call-1", "cancel_order")]
