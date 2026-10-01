"""Seam 1: the HTTP Adapter core over the fake HTTP Target (ticket 17, phase-8 decisions
1-10 and 17-19).

What the Manifest's environment block may say and what `validate` names when it says it
wrong; credentials failing closed and the token travelling as a header into no file; the
three identity modes and their `fixture/applied` Events, which carry field names and never
values; the conversation id round trip, and a Target that reports none; the `response`
Span's attributes; a non-2xx status, an error frame and bytes no Dialect reads, each a
`target_error`; a reply slower than `turn_timeout` recorded `timeout` by a real Run; tool
frames with and without arguments, and the `retrieval` kind; `--live`; the Adapter drawn by
`show`; and the package offline at import.

Every request goes to a fake on 127.0.0.1 (`tests/fakes/http_target.py`); nothing else is
reached, and no model is called: the Suites here declare no judged Eval.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentdiag.adapter.base import AdapterDescription, Fixture, LiveSideEffectsRefused
from agentdiag.adapter.http import HttpAdapter
from agentdiag.adapter.http.config import http_problems
from agentdiag.adapter.http.session import FixtureNotApplicable, TargetResponseError
from agentdiag.adapter.http.sse import SseEvent, read_sse
from agentdiag.cli import app
from agentdiag.connector.base import CredentialMissing
from agentdiag.connector.plugins import ADAPTER_GROUP, registered
from agentdiag.run.manifest import AdapterSection, load_manifest
from agentdiag.run.preflight import build_adapter
from agentdiag.trace import Event, TraceWriter, event_fields, project_spans, read_trace
from agentdiag.trace.attributes import (
    CONVERSATION_NEW,
    DIALECT,
    FRAMES,
    NOT_OBSERVED,
    REQUEST_MESSAGE,
    TIME_TO_FIRST_FRAME_MS,
)
from agentdiag.workspace import Workspace
from tests.fakes.http_target import (
    IDENTITY_HEADER,
    SLUG,
    TOKEN_VARIABLE,
    DialectName,
    FakeHttpTarget,
    Reply,
    Tool,
    http_workspace,
    serving_target,
    unreachable_url,
)

REPO = Path(__file__).resolve().parents[1]
DIALECTS: tuple[DialectName, ...] = ("json", "sse-json")
TOKEN = "tok-3f9a-not-a-real-secret"
PHONE = "+1 555 0100"

runner = CliRunner()


# --- building an Adapter over the fake ---


def config(
    base_url: str,
    dialect: str,
    *,
    side_effects: str = "none",
    **block: Any,
) -> dict[str, Any]:
    return {
        "kind": "http",
        "side_effects": side_effects,
        "environments": {
            "default": "dev",
            "dev": {"base_url": base_url, "dialect": dialect, **block},
        },
    }


def adapter_over(
    fake: FakeHttpTarget,
    *,
    environ: Mapping[str, str] | None = None,
    tool_kinds: Mapping[str, Any] | None = None,
    **block: Any,
) -> HttpAdapter:
    return HttpAdapter(
        config(fake.base_url, fake.dialect, **block),
        environment="dev",
        tool_kinds=tool_kinds,
        environ=environ or {},
    )


def converse(
    adapter: HttpAdapter,
    tmp_path: Path,
    messages: Sequence[str] = ("Hello",),
    *,
    fixtures: Sequence[Fixture] = (),
) -> tuple[list[str], list[Event]]:
    """Open, deliver each message inside its own `turn` Span, close; the replies and the
    Trace."""
    trace = TraceWriter(tmp_path / f"trace-{len(list(tmp_path.glob('trace-*')))}.jsonl")
    trace.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
    session = adapter.open(trace, fixtures=fixtures)
    replies: list[str] = []
    for number, message in enumerate(messages, start=1):
        with trace.span("turn", actor="agentdiag", name=f"turn {number}", fidelity="instrumented"):
            replies.append(session.deliver(message))
    session.close()
    trace.end("completed")
    trace.close()
    return replies, read_trace(trace.path)


def responses(events: Sequence[Event]) -> list[Any]:
    return [span for span in project_spans(events) if span.kind == "response"]


def run_cli(root: Path, *arguments: str) -> Any:
    return runner.invoke(app, ["run", "--root", str(root), "--target", SLUG, *arguments])


def only_run(root: Path) -> Path:
    runs = sorted((root / ".agentdiag" / "targets" / SLUG / "runs").iterdir())
    assert len(runs) == 1, runs
    return runs[0]


def trace_of(run_dir: Path, scenario: str = "greet") -> list[Event]:
    return read_trace(run_dir / "trials" / scenario / "1" / "trace.jsonl")


# --- the environment block (decision 2) ---


PROBLEMS: dict[str, tuple[dict[str, Any], str, str]] = {
    "base_url with a path": (
        {"base_url": "https://api.example.test/v1"},
        "adapter.environments.dev.base_url",
        "is a scheme and a host only",
    ),
    "base_url with no scheme": (
        {"base_url": "api.example.test"},
        "adapter.environments.dev.base_url",
        "needs a scheme and a host",
    ),
    "an unknown Dialect": (
        {"dialect": "nowhere"},
        "adapter.environments.dev.dialect",
        "no Dialect of kind 'nowhere' is installed",
    ),
    "a header interpolating a credential": (
        {"headers": {"x-key": "{token}"}},
        "adapter.environments.dev.headers.x-key",
        "interpolates {token}, which is not an identifier",
    ),
    "a path interpolating an unknown name": (
        {"path": "/agents/{agent}/chat"},
        "adapter.environments.dev.path",
        "interpolates {agent}",
    ),
    "a path not starting with a slash": (
        {"path": "chat"},
        "adapter.environments.dev.path",
        "does not start with /",
    ),
    "a credential naming no variable": (
        {"credentials": {"token": ""}},
        "adapter.environments.dev.credentials.token",
        "names no environment variable",
    ),
    "an unknown identity mode": (
        {"identity": {"mode": "carrier-pigeon"}},
        "adapter.environments.dev.identity.mode",
        "is not an identity mode; core's are header, body, first_message",
    ),
    "a header mode with no header": (
        {"identity": {"mode": "header", "from": "phone"}},
        "adapter.environments.dev.identity",
        "the header mode needs `header`",
    ),
    "a credential in a static header": (
        {"headers": {"Authorization": "Bearer abc"}},
        "adapter.environments.dev.headers.Authorization",
        "carries a credential; name its variable under `credentials:`",
    ),
    "a side-effect class outside the closed set": (
        {"side_effects": "bogus"},
        "adapter.environments.dev.side_effects",
        "Input should be 'none', 'sandboxed' or 'live'",
    ),
    "a first_message mode with no template": (
        {"identity": {"mode": "first_message"}},
        "adapter.environments.dev.identity",
        "the first_message mode needs `template`",
    ),
}


@pytest.mark.parametrize("case", sorted(PROBLEMS))
def test_validate_names_each_problem_of_an_http_environment_where_it_is(case: str) -> None:
    change, where, fragment = PROBLEMS[case]
    block: dict[str, Any] = {"base_url": "https://api.example.test", "dialect": "json", **change}
    section = AdapterSection.model_validate(
        {"kind": "http", "environments": {"default": "dev", "dev": block}}
    )

    problems = http_problems(section)

    assert any(at == where and fragment in message for at, message in problems), problems


def test_a_malformed_tool_truth_block_is_named() -> None:
    section = AdapterSection.model_validate(
        {
            "kind": "http",
            "tool_truth": {"evidence": "voice"},
            "environments": {
                "default": "dev",
                "dev": {"base_url": "https://api.example.test", "dialect": "json"},
            },
        }
    )

    assert [where for where, _ in http_problems(section)] == ["adapter.tool_truth"]


def test_validate_passes_the_reference_manifest_and_names_a_broken_one(tmp_path: Path) -> None:
    good = http_workspace(tmp_path / "good", "http://127.0.0.1:9")
    bad = http_workspace(tmp_path / "bad", "http://127.0.0.1:9/api", environment={"dialect": "x"})

    passed = runner.invoke(app, ["validate", "--root", str(good)])
    failed = runner.invoke(app, ["validate", "--root", str(bad)])

    assert passed.exit_code == 0, passed.stdout
    assert "error:" not in passed.stdout
    assert failed.exit_code == 3
    errors = [line for line in failed.stdout.splitlines() if line.startswith("error:")]
    assert any("adapter.environments.dev.base_url" in line for line in errors), errors
    assert any("adapter.environments.dev.dialect" in line for line in errors), errors


# --- credentials fail closed, and never reach a file (decision 2) ---


def test_a_missing_credential_fails_closed_naming_its_variable(tmp_path: Path) -> None:
    with serving_target("json") as fake:
        adapter = adapter_over(
            fake,
            environ={"OTHER_TOKEN": "someone-elses"},
            credentials={"token": "DEV_TOKEN"},
        )
        with pytest.raises(CredentialMissing, match=r"\$DEV_TOKEN") as missing:
            adapter.check()
        assert "someone-elses" not in str(missing.value)
        trace = TraceWriter(tmp_path / "trace.jsonl")
        trace.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
        with pytest.raises(CredentialMissing):
            adapter.open(trace)
        trace.close()
    assert fake.requests == []


def test_a_run_with_the_credential_unset_is_refused_at_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(TOKEN_VARIABLE, raising=False)
    root = http_workspace(tmp_path, "http://127.0.0.1:9")

    result = run_cli(root)

    assert result.exit_code == 3, result.stdout
    assert f"${TOKEN_VARIABLE}" in result.stdout
    assert not (root / ".agentdiag" / "targets" / SLUG / "runs").exists()


def test_the_token_travels_as_a_header_and_is_written_to_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    with serving_target(
        "sse-json", require_token=TOKEN, echo_headers=[IDENTITY_HEADER, "x-organization-id"]
    ) as fake:
        root = http_workspace(tmp_path, fake.base_url)
        result = run_cli(root)

    assert result.exit_code == 0, result.stdout
    (seen,) = fake.requests
    assert seen.authorized is True
    assert "authorization" in seen.header_names
    assert seen.path == "/api/agents/echo-1/chat"
    assert seen.echoed["x-organization-id"] == "org-7"
    written = [path for path in root.rglob("*") if path.is_file()]
    assert written
    for path in written:
        assert TOKEN.encode() not in path.read_bytes(), path


# --- identity modes (decision 6) ---


def test_the_header_mode_sends_the_identity_and_the_trace_names_only_its_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    with serving_target("sse-json", echo_headers=[IDENTITY_HEADER]) as fake:
        root = http_workspace(tmp_path, fake.base_url)
        result = run_cli(root)

    assert result.exit_code == 0, result.stdout
    assert fake.requests[0].echoed == {IDENTITY_HEADER: PHONE}
    run_dir = only_run(root)
    events = trace_of(run_dir)
    (applied,) = [event for event in events if event.type == "fixture/applied"]
    fields = event_fields(applied)
    assert applied.actor == "adapter"
    assert fields["fixture"] == "a-caller"
    assert fields["mechanism"] == "identity_header"
    assert fields["detail"] == {"kind": "identity", "fields": ["phone"]}
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert PHONE.encode() not in path.read_bytes(), path


@pytest.mark.parametrize("dialect", DIALECTS)
def test_the_body_mode_puts_the_identity_beside_the_message(
    dialect: DialectName, tmp_path: Path
) -> None:
    with serving_target(dialect) as fake:
        adapter = adapter_over(fake, identity={"mode": "body"})
        fixture = Fixture(name="a-caller", kind="identity", data={"phone": PHONE, "tier": "gold"})
        _, events = converse(adapter, tmp_path, fixtures=[fixture])

    body = fake.requests[0].body
    assert body["message"] == "Hello"
    assert body["identity"] == {"phone": PHONE, "tier": "gold"}
    (applied,) = [event for event in events if event.type == "fixture/applied"]
    assert event_fields(applied)["mechanism"] == "identity_body"
    assert event_fields(applied)["detail"] == {"kind": "identity", "fields": ["phone", "tier"]}


def test_the_first_message_mode_prefixes_the_first_turn_only_and_records_what_was_sent(
    tmp_path: Path,
) -> None:
    with serving_target("json") as fake:
        adapter = adapter_over(
            fake, identity={"mode": "first_message", "template": "[caller {phone}]"}
        )
        fixture = Fixture(name="a-caller", kind="identity", data={"phone": PHONE})
        _, events = converse(adapter, tmp_path, ["Hello", "Thanks"], fixtures=[fixture])

    sent = [request.body["message"] for request in fake.requests]
    assert sent == [f"[caller {PHONE}]\nHello", "Thanks"]
    first, second = responses(events)
    assert first.attributes[REQUEST_MESSAGE] == sent[0]
    assert REQUEST_MESSAGE not in second.attributes
    (applied,) = [event for event in events if event.type == "fixture/applied"]
    assert event_fields(applied)["mechanism"] == "identity_first_message"


FIXTURE_REFUSALS: dict[str, tuple[dict[str, Any], Fixture, str]] = {
    "no identity block": (
        {},
        Fixture(name="an-account", kind="identity", data={"id": "acct-1"}),
        "Fixture 'an-account' (kind identity) needs adapter.environments.dev.identity; "
        "the http Adapter applies Fixtures only through an identity mode",
    ),
    "a kind other than identity": (
        {"identity": {"mode": "body"}},
        Fixture(name="orders", kind="data", data={"id": "NB-1"}),
        "Fixture 'orders' (kind data) cannot be applied",
    ),
    "a field the mode reads missing": (
        {"identity": {"mode": "header", "header": IDENTITY_HEADER, "from": "phone"}},
        Fixture(name="an-account", kind="identity", data={"id": "acct-1"}),
        "Fixture 'an-account' (kind identity) has no field 'phone', which "
        "adapter.environments.dev.identity reads",
    ),
}


@pytest.mark.parametrize("case", sorted(FIXTURE_REFUSALS))
def test_a_fixture_the_identity_mode_cannot_apply_is_refused_the_same_way_twice(
    case: str, tmp_path: Path
) -> None:
    block, fixture, fragment = FIXTURE_REFUSALS[case]
    with serving_target("json") as fake:
        adapter = adapter_over(fake, **block)
        reason = adapter.check_fixtures([fixture])
        trace = TraceWriter(tmp_path / "trace.jsonl")
        trace.start(trace_id="r/s/1", scenario="s", run="r", trial=1)
        with pytest.raises(FixtureNotApplicable) as refused:
            adapter.open(trace, fixtures=[fixture])
        trace.close()

    assert reason is not None and fragment in reason
    assert str(refused.value) == reason
    assert fake.requests == []


# --- the conversation (decision 3) ---


@pytest.mark.parametrize("dialect", DIALECTS)
def test_the_conversation_id_the_reply_names_is_sent_back_on_the_next_turn(
    dialect: DialectName, tmp_path: Path
) -> None:
    script = (Reply(text="Hi.", conversation="conv-42"), Reply(text="Again."))
    with serving_target(dialect, script=script) as fake:
        replies, events = converse(adapter_over(fake), tmp_path, ["Hello", "Still there?"])

    assert replies == ["Hi.", "Again."]
    assert [request.body["conversation_id"] for request in fake.requests] == [None, "conv-42"]
    first, second = responses(events)
    assert "gen_ai.conversation.id" not in _start_attributes(events, first.span_id)
    assert first.attributes["gen_ai.conversation.id"] == "conv-42"
    assert first.attributes[CONVERSATION_NEW] is True
    assert _start_attributes(events, second.span_id)["gen_ai.conversation.id"] == "conv-42"
    assert second.attributes[CONVERSATION_NEW] is False


def test_a_target_that_names_no_conversation_makes_every_turn_a_new_chat(tmp_path: Path) -> None:
    with serving_target("sse-json", script=(Reply(text="Hi."),)) as fake:
        _, events = converse(adapter_over(fake), tmp_path, ["Hello", "Still there?"])

    assert [request.body["conversation_id"] for request in fake.requests] == [None, None]
    assert [span.attributes[CONVERSATION_NEW] for span in responses(events)] == [True, True]
    assert all("gen_ai.conversation.id" not in span.attributes for span in responses(events))


# --- what one Turn records (decision 7) ---


@pytest.mark.parametrize("dialect", DIALECTS)
def test_the_response_span_says_what_the_surface_showed_and_what_it_did_not(
    dialect: DialectName, tmp_path: Path
) -> None:
    with serving_target(dialect, script=(Reply(text="Hello there.", conversation="c-1"),)) as fake:
        replies, events = converse(adapter_over(fake, path="/v1/chat"), tmp_path)

    assert replies == ["Hello there."]
    (span,) = responses(events)
    (turn,) = [span for span in project_spans(events) if span.kind == "turn"]
    assert span.parent_span_id == turn.span_id
    assert span.name == f"response {dialect}"
    assert span.actor == "target"
    assert span.fidelity == "observed"
    assert span.status == "ok"
    start = _start_attributes(events, span.span_id)
    assert start == {"http.request.method": "POST", "url.path": "/v1/chat", DIALECT: dialect}
    assert span.attributes["http.response.status_code"] == 200
    assert isinstance(span.attributes[TIME_TO_FIRST_FRAME_MS], int)
    assert span.attributes[NOT_OBSERVED] == ["llm_calls", "usage"]
    frames = span.attributes[FRAMES]
    assert frames["conversation"] == 1 and frames["text"] >= 1


@pytest.mark.parametrize("dialect", DIALECTS)
def test_tool_frames_become_observed_tool_spans_with_unknown_arguments_when_unshown(
    dialect: DialectName, tmp_path: Path
) -> None:
    tools = (
        Tool("search_articles"),
        Tool("open_ticket", arguments={"topic": "billing"}, result={"id": "T-9"}, call_id="c1"),
    )
    with serving_target(dialect, script=(Reply(text="Done.", tools=tools),)) as fake:
        _, events = converse(
            adapter_over(fake, tool_kinds={"search_articles": "retrieval"}), tmp_path
        )

    (response,) = responses(events)
    search, ticket = [
        span for span in project_spans(events) if span.parent_span_id == response.span_id
    ]
    assert (search.kind, ticket.kind) == ("retrieval", "tool_call")
    assert {search.fidelity, ticket.fidelity} == {"observed"}
    assert search.attributes[NOT_OBSERVED] == ["start_time", "end_time", "arguments", "result"]
    assert ticket.attributes[NOT_OBSERVED] == ["start_time", "end_time"]
    assert ticket.attributes["gen_ai.tool.call.id"] == "c1"
    assert "gen_ai.tool.call.id" not in search.attributes
    calls = {event_fields(e)["tool"]: event_fields(e) for e in events if e.type == "tool/call"}
    assert calls["search_articles"]["arguments"] is None
    assert calls["search_articles"]["not_observed"] == ["arguments", "gen_ai.tool.call.id"]
    assert calls["open_ticket"]["arguments"] == {"topic": "billing"}
    assert calls["open_ticket"]["not_observed"] == []
    results = [event_fields(e) for e in events if e.type == "tool/result"]
    assert [(r["tool"], r["result"]) for r in results] == [("open_ticket", {"id": "T-9"})]


def test_a_non_2xx_status_ends_the_turn_in_error(tmp_path: Path) -> None:
    with (
        serving_target("json", script=(Reply(status=503),)) as fake,
        pytest.raises(TargetResponseError, match="503"),
    ):
        converse(adapter_over(fake), tmp_path)

    events = read_trace(next(tmp_path.glob("trace-*.jsonl")))
    (span,) = responses(events)
    assert span.status == "error"
    assert span.attributes["error.type"] == "503"
    assert span.attributes["http.response.status_code"] == 503
    errors = [event for event in events if event.type == "error" and event.span_id == span.span_id]
    assert len(errors) == 1 and event_fields(errors[0])["error_type"] == "503"


@pytest.mark.parametrize("dialect", DIALECTS)
def test_bytes_no_dialect_can_read_end_the_turn_in_error(
    dialect: DialectName, tmp_path: Path
) -> None:
    with (
        serving_target(dialect, script=(Reply(garbage=b"\xff\xfe not a frame \x00"),)) as fake,
        pytest.raises(TargetResponseError, match="DialectError"),
    ):
        converse(adapter_over(fake), tmp_path)

    (span,) = responses(read_trace(next(tmp_path.glob("trace-*.jsonl"))))
    assert span.status == "error"
    assert span.attributes["error.type"] == "DialectError"


def test_an_error_frame_ends_the_turn_in_error_with_its_type(tmp_path: Path) -> None:
    script = (Reply(text="Partial", error=("rate_limited", "slow down")),)
    with (
        serving_target("sse-json", script=script) as fake,
        pytest.raises(TargetResponseError, match="rate_limited: slow down"),
    ):
        converse(adapter_over(fake), tmp_path)

    (span,) = responses(read_trace(next(tmp_path.glob("trace-*.jsonl"))))
    assert span.attributes["error.type"] == "rate_limited"


def test_a_target_that_cannot_be_reached_is_a_target_error(tmp_path: Path) -> None:
    adapter = HttpAdapter(config(unreachable_url(), "json"), environment="dev", environ={})

    with pytest.raises(TargetResponseError, match="could not be reached"):
        converse(adapter, tmp_path)


def test_a_reply_slower_than_turn_timeout_is_recorded_timeout_by_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    with serving_target("sse-json", script=(Reply(delay_s=1.5),)) as fake:
        root = http_workspace(tmp_path, fake.base_url, environment={"turn_timeout": 0.3})
        result = run_cli(root)

    # `incomplete / timeout` on every Score: undecided, not failed (D32).
    assert result.exit_code == 2, result.output
    events = trace_of(only_run(root))
    assert event_fields(events[-1])["termination"] == "timeout"
    record = json.loads((only_run(root) / "run.json").read_text(encoding="utf-8"))
    assert record["adapter"]["kind"] == "http"
    assert record["adapter"]["fidelity"] == "observed"
    assert record["adapter"]["turn_timeout_s"] == 0.3
    assert "live_acknowledged" not in record["adapter"]


def test_a_run_over_the_fake_scores_the_observed_first_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    with serving_target("sse-json") as fake:
        root = http_workspace(tmp_path, fake.base_url)
        result = run_cli(root)

    assert result.exit_code == 0, result.stdout
    trial = only_run(root) / "trials" / "greet" / "1" / "scores.json"
    scores = json.loads(trial.read_text(encoding="utf-8"))
    by_eval = {score["eval"]: score for score in scores["scores"]}
    latency = by_eval["first_token_latency"]
    assert latency["verdict"] == "pass"
    assert latency["fidelity"] == "observed"
    assert any(cited.startswith("response-") for cited in latency["evidence"])
    # The reply text is what the surface showed: no Score over it claims more (decision 7).
    assert (by_eval["must_not_say"]["verdict"], by_eval["must_not_say"]["fidelity"]) == (
        "pass",
        "observed",
    )
    turns = [span for span in project_spans(trace_of(only_run(root))) if span.kind == "turn"]
    assert {span.fidelity for span in turns} == {"observed"}


# --- `live` needs --live (decision 9) ---


def test_a_live_environment_is_refused_without_the_flag_and_opens_with_it(
    tmp_path: Path,
) -> None:
    with serving_target("json") as fake:
        refused = adapter_over(fake, side_effects="live")
        with pytest.raises(LiveSideEffectsRefused, match="pass --live to acknowledge it"):
            refused.check()
        allowed = HttpAdapter(
            config(fake.base_url, "json", side_effects="live"),
            environment="dev",
            allow_live=True,
            environ={},
        )
        allowed.check()
        replies, _ = converse(allowed, tmp_path)

    assert replies == ["Hello from the fake Target."]
    description = allowed.describe()
    assert description.live_acknowledged is True
    assert description.side_effects == "live"
    assert refused.describe().live_acknowledged is False


def test_run_live_records_the_flag_and_run_without_it_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    with serving_target("sse-json") as fake:
        root = http_workspace(tmp_path, fake.base_url, environment={"side_effects": "live"})
        refused = run_cli(root)
        assert not (root / ".agentdiag" / "targets" / SLUG / "runs").exists()
        live = run_cli(root, "--live")

    assert refused.exit_code == 3
    assert "pass --live to acknowledge it" in refused.stdout
    assert live.exit_code == 0, live.stdout
    record = json.loads((only_run(root) / "run.json").read_text(encoding="utf-8"))
    assert record["adapter"]["live_acknowledged"] is True
    assert record["adapter"]["side_effects"] == "live"


def test_live_acknowledged_is_written_only_when_true() -> None:
    base = {"kind": "http", "fidelity": "observed", "side_effects": "none", "environment": "dev"}

    assert "live_acknowledged" not in AdapterDescription(**base).model_dump()
    assert AdapterDescription(**base, live_acknowledged=True).model_dump()["live_acknowledged"]
    assert AdapterDescription.model_validate({**base}).live_acknowledged is False


def test_the_ui_refuses_a_live_launch(tmp_path: Path) -> None:
    from tests.serving import call, serving

    root = http_workspace(tmp_path, "http://127.0.0.1:9")
    with serving(Workspace.find(root)) as server:
        answer = call(server, "POST", "/api/runs", {"target": SLUG, "live": True})

    assert answer.status == 400
    assert "a live Run is launched from the terminal in this version" in answer.body.decode()


# --- any registered kind is built (decision 1) ---


class _PluginAdapter(HttpAdapter):
    """A plugin's Adapter kind: the constructor every kind takes, nothing in-process."""

    kind = "plugin-http"


def test_build_adapter_builds_any_registered_kind_with_the_common_arguments(
    tmp_path: Path,
) -> None:
    root = http_workspace(tmp_path, "http://127.0.0.1:9", adapter={"kind": "plugin-http"})
    manifest = load_manifest(Workspace.find(root).resolve(SLUG))
    with registered("plugin-http", _PluginAdapter, group=ADAPTER_GROUP):
        adapter, description, problems = build_adapter(manifest, None, allow_live=True)

    # The credential is unset in this process, so the check refuses — after building it.
    assert adapter is None and description is None
    assert problems and f"${TOKEN_VARIABLE}" in problems[0]


def test_build_adapter_refuses_a_registered_kind_that_is_not_an_adapter(tmp_path: Path) -> None:
    class NotAnAdapter:
        def __init__(self, config: Mapping[str, Any], **_: Any) -> None:
            self.config = config

    root = http_workspace(tmp_path, "http://127.0.0.1:9", adapter={"kind": "not-one"})
    manifest = load_manifest(Workspace.find(root).resolve(SLUG))
    with registered("not-one", NotAnAdapter, group=ADAPTER_GROUP):
        adapter, _, problems = build_adapter(manifest, None)

    assert adapter is None
    assert problems == [
        "Adapter: the not-one Adapter is installed, and what it builds is not an Adapter "
        "(describe, check_fixtures, open and check)"
    ]


# --- `show` draws the response Span (decision 7) ---


def test_show_draws_the_response_span_with_its_first_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    with serving_target("sse-json", script=(Reply(tools=(Tool("search_articles"),)),)) as fake:
        root = http_workspace(tmp_path, fake.base_url)
        assert run_cli(root).exit_code == 0
    run_dir = only_run(root)

    shown = runner.invoke(app, ["show", str(run_dir), "greet"])

    assert shown.exit_code == 0, shown.stdout
    line = next(line for line in shown.stdout.splitlines() if "response sse-json" in line)
    assert "first frame" in line
    assert "not observed: llm_calls, usage" in line
    assert "unpriced" not in line
    assert "→ search_articles <not observed>" in shown.stdout


# --- server-sent events (decision 5) ---


def test_read_sse_reads_the_line_rules_across_chunk_boundaries() -> None:
    chunks = [
        b": a comment\r",
        b"\nevent: note\r\ndata: one\r\ndata: two\r",
        b"\n\r\nid: 7\ndata:three\n\n",
        "data: café".encode()[:-1],
        "data: café".encode()[-1:] + b"\n",
    ]

    events = list(read_sse(chunks))

    assert events == [
        SseEvent(data="one\ntwo", event="note"),
        SseEvent(data="three", id="7"),
        SseEvent(data="café", id="7"),
    ]


# --- offline (decision 1, 4) ---


def test_the_http_adapter_and_the_credentials_file_import_no_sdk() -> None:
    probe = (
        "import sys, agentdiag.adapter.http, agentdiag.adapter.http.session, "
        "agentdiag.adapter.http.dialects, agentdiag.credentials_file; "
        "leaked = sorted(m for m in sys.modules if m == 'anthropic' "
        "or m.startswith(('anthropic.', 'claude_agent_sdk', 'agentdiag.model', "
        "'agentdiag.adapter.inprocess'))); "
        "print(','.join(leaked))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=REPO
    )

    assert completed.stdout.strip() == ""


# --- helpers ---


def _start_attributes(events: Sequence[Event], span_id: str) -> dict[str, Any]:
    (start,) = [e for e in events if e.type == "span/start" and e.span_id == span_id]
    return dict(event_fields(start)["attributes"])


def test_live_on_an_environment_that_is_not_live_is_recorded_as_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    with serving_target("sse-json") as fake:
        root = http_workspace(tmp_path, fake.base_url)
        result = run_cli(root, "--live")

    assert result.exit_code == 0, result.output
    record = json.loads((only_run(root) / "run.json").read_text(encoding="utf-8"))
    assert (record["adapter"]["live_acknowledged"], record["adapter"]["side_effects"]) == (
        True,
        "none",
    )


# --- no credential leaks, whatever the Target or the Connector says (decision 2) ---


LEAKS: dict[str, Reply] = {
    "a 4xx body echoing the token": Reply(
        status=403, error_body=f'{{"error": "token {TOKEN} is not allowed"}}'.encode()
    ),
    "an sse error frame echoing it in both fields": Reply(
        text="Partial", error=(f"denied-{TOKEN}", f"the token {TOKEN} expired")
    ),
}


@pytest.mark.parametrize("case", sorted(LEAKS))
def test_a_token_the_target_echoes_back_is_scrubbed_everywhere(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    with serving_target("sse-json", script=(LEAKS[case],)) as fake:
        root = http_workspace(tmp_path / "run", fake.base_url)
        result = run_cli(root)
        adapter = adapter_over(
            fake, credentials={"token": TOKEN_VARIABLE}, environ={TOKEN_VARIABLE: TOKEN}
        )
        with pytest.raises(TargetResponseError) as raised:
            converse(adapter, tmp_path)

    assert result.exit_code == 2, result.output
    assert TOKEN not in result.output and TOKEN not in result.stderr
    assert TOKEN not in str(raised.value) and f"${TOKEN_VARIABLE}" in str(raised.value)
    run_dir = only_run(root)
    for path in root.rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes(), path
    trace = (run_dir / "trials" / "greet" / "1" / "trace.jsonl").read_text(encoding="utf-8")
    assert f"${TOKEN_VARIABLE}" in trace
    end = event_fields(trace_of(run_dir)[-1])
    assert end["termination"] == "target_error" and TOKEN not in str(end["error"])


def test_a_token_in_a_connector_error_is_scrubbed_from_the_tool_truth_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentdiag.connector.base import READ_EVIDENCE, ConnectorError
    from tests.fakes.fake_connector import FAKE_KIND, FakeConnector

    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    connector = FakeConnector({"dev": {}}, {("dev", "proxy"): []})
    connector.raise_on(READ_EVIDENCE, ConnectorError(f"the proxy refused {TOKEN}"))
    with serving_target("sse-json") as fake, registered(FAKE_KIND, connector.as_kind()):
        root = http_workspace(
            tmp_path,
            fake.base_url,
            adapter={"tool_truth": {"evidence": "proxy"}},
            manifest={"connector": {"kind": FAKE_KIND, "environments": {"dev": {}}}},
        )
        result = run_cli(root)

    assert result.exit_code == 0, result.output
    assert TOKEN not in result.output and TOKEN not in result.stderr
    for path in root.rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes(), path
    (response,) = responses(trace_of(only_run(root)))
    assert response.attributes["agentdiag.tool_truth"] == {
        "failed": f"the proxy refused ${TOKEN_VARIABLE}"
    }


# --- a Dialect's own fault ends the Turn, never leaves the Span open (review T1) ---


class _BrokenDialect:
    """A plugin Dialect with a bug: it raises what no Dialect should."""

    name = "broken"

    def request(self, message: str, **kwargs: Any) -> Any:
        from agentdiag.adapter.http.dialects import SseJsonDialect

        return SseJsonDialect().request(message, **kwargs)

    def frames(self, response: Any) -> Any:
        raise KeyError("frame_kind")
        yield  # pragma: no cover - a generator that fails on its first read


def test_a_dialect_that_raises_anything_ends_the_turn_as_a_target_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentdiag.connector.plugins import DIALECT_GROUP

    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    with (
        serving_target("sse-json") as fake,
        registered("broken", _BrokenDialect, group=DIALECT_GROUP),
    ):
        root = http_workspace(tmp_path, fake.base_url, environment={"dialect": "broken"})
        result = run_cli(root)

    assert result.exit_code == 2, result.output
    events = trace_of(only_run(root))
    (response,) = responses(events)
    assert response.status == "error"
    assert response.attributes["error.type"] == "KeyError"
    assert event_fields(events[-1])["termination"] == "target_error"


# --- a rescore drives nothing (review S5) ---


def test_a_rescore_needs_neither_the_token_nor_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml

    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    with serving_target("sse-json") as fake:
        root = http_workspace(tmp_path, fake.base_url)
        assert run_cli(root).exit_code == 0
    source = only_run(root)
    monkeypatch.delenv(TOKEN_VARIABLE)
    manifest = root / ".agentdiag" / "targets" / SLUG / "manifest.yaml"
    loaded = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    loaded["adapter"]["environments"]["dev"]["side_effects"] = "live"
    manifest.write_text(yaml.safe_dump(loaded), encoding="utf-8")

    result = runner.invoke(app, ["rescore", "--root", str(root), "--target", SLUG, source.name])

    assert result.exit_code == 0, result.output
    runs = sorted((root / ".agentdiag" / "targets" / SLUG / "runs").iterdir())
    assert len(runs) == 2
