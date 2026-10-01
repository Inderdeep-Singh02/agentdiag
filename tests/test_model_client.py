"""Seam 2: the ModelClient — the request body, the replay, and where credentials come from.

The request body is asserted as a literal rather than as a round trip, because it is the
key a recording matches on: a change to it is a change to every committed fixture, and a
test that derived the expectation from the code would not notice (D12).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentdiag.model import credentials
from agentdiag.model.claude_code import (
    Backend,
    ClaudeCodeCli,
    ClaudeCodeClient,
    ClaudeCodeError,
    backend_for,
    live_client,
)
from agentdiag.model.client import (
    LiveAnthropicClient,
    ModelRequest,
    ModelResponse,
    RecordingModelClient,
    ReplayModelClient,
)
from agentdiag.model.credentials import ENV_API_KEY, ENV_AUTH_TOKEN, CredentialSource, resolve
from agentdiag.model.replay import (
    Recording,
    RecordingNotConsumed,
    ReplayCursor,
    ReplayMismatch,
)

SCHEMA: dict[str, Any] = {"type": "object", "properties": {"verdict": {"type": "string"}}}


def a_request(**overrides: Any) -> ModelRequest:
    fields: dict[str, Any] = {
        "model": "claude-opus-5",
        "max_tokens": 16000,
        "messages": [{"role": "user", "content": "judge this"}],
    }
    fields.update(overrides)
    return ModelRequest(**fields)


def a_response(model: str = "claude-opus-5", text: str = "{}") -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def write_recording(path: Path, exchanges: list[dict[str, Any]]) -> Path:
    path.write_text(
        "\n".join(json.dumps(exchange) for exchange in exchanges) + "\n", encoding="utf-8"
    )
    return path


def cursor_over(path: Path) -> ReplayCursor:
    return ReplayCursor(Recording.load(path))


# --- the request body is the exact API body, and so the recording key ---


def test_a_request_without_a_schema_or_an_effort_carries_no_output_config() -> None:
    assert a_request().body() == {
        "model": "claude-opus-5",
        "max_tokens": 16000,
        "messages": [{"role": "user", "content": "judge this"}],
    }


def test_a_request_with_an_output_schema_carries_a_json_schema_output_config() -> None:
    body = a_request(output_schema=SCHEMA).body()

    assert body["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}


def test_a_request_with_an_effort_carries_it_inside_the_same_output_config() -> None:
    body = a_request(output_schema=SCHEMA, effort="high").body()

    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema"


def test_a_request_never_carries_a_sampling_parameter_that_was_not_asked_for() -> None:
    """The Claude 5 models reject `temperature`; v1 sends none, so none appears (D12)."""
    body = a_request().body()

    assert "temperature" not in body
    assert "top_p" not in body
    assert "top_k" not in body


def test_a_request_with_a_system_prompt_places_it_beside_the_messages() -> None:
    body = a_request(system="you are the Judge").body()

    assert body["system"] == "you are the Judge"


# --- a response records what the API resolved, not what was asked for ---


def test_the_judge_fingerprint_rides_on_the_request_and_never_in_its_body() -> None:
    """Ticket 21, decision 40: the body is the recording key, so a Fingerprint in it would
    move every key with the Backend; beside it, a client that records can still say which
    Judge made the request."""
    plain = a_request(output_schema=SCHEMA, effort="high")
    fingerprinted = a_request(output_schema=SCHEMA, effort="high", fingerprint="f" * 64)

    assert fingerprinted.fingerprint == "f" * 64
    assert json.dumps(fingerprinted.body(), sort_keys=True) == json.dumps(
        plain.body(), sort_keys=True
    )
    assert "fingerprint" not in json.dumps(fingerprinted.body())


def test_a_response_separates_the_requested_model_from_the_one_the_api_resolved() -> None:
    response = ModelResponse.from_body(a_request(), a_response(model="claude-opus-5-20260401"))

    assert response.requested_model == "claude-opus-5"
    assert response.resolved_model == "claude-opus-5-20260401"


def test_a_response_keeps_the_wire_body_so_the_event_says_what_the_api_said() -> None:
    body = a_response()
    response = ModelResponse.from_body(a_request(), body)

    assert response.body == body


def test_a_response_concatenates_its_text_blocks_for_the_structured_output() -> None:
    response = ModelResponse.from_body(a_request(), a_response(text='{"verdict": "pass"}'))

    assert response.text() == '{"verdict": "pass"}'


def test_a_response_records_no_sampling_support_when_no_sampling_was_sent() -> None:
    response = ModelResponse.from_body(a_request(), a_response())

    assert response.sampling_accepted == {}


def test_a_response_records_every_sampling_parameter_the_api_accepted() -> None:
    response = ModelResponse.from_body(a_request(sampling={"temperature": 0.0}), a_response())

    assert response.sampling_accepted == {"temperature": "accepted"}


# --- replay fails loud, both ways (D12) ---


def test_the_replay_client_serves_the_exchange_whose_request_body_matches(
    tmp_path: Path,
) -> None:
    request = a_request()
    path = write_recording(
        tmp_path / "one.jsonl", [{"request": request.body(), "response": a_response()}]
    )

    response = ReplayModelClient(cursor_over(path)).complete(request)

    assert response.resolved_model == "claude-opus-5"


def test_a_request_no_recorded_exchange_matches_raises_replay_mismatch(tmp_path: Path) -> None:
    path = write_recording(
        tmp_path / "one.jsonl", [{"request": a_request().body(), "response": a_response()}]
    )
    client = ReplayModelClient(cursor_over(path))

    with pytest.raises(ReplayMismatch) as raised:
        client.complete(a_request(messages=[{"role": "user", "content": "something else"}]))

    assert "first difference" in str(raised.value)


def test_a_recording_with_an_exchange_nobody_requested_raises_when_the_trial_ends(
    tmp_path: Path,
) -> None:
    path = write_recording(
        tmp_path / "two.jsonl",
        [
            {"request": a_request().body(), "response": a_response()},
            {"request": a_request(max_tokens=99).body(), "response": a_response()},
        ],
    )
    client = ReplayModelClient(cursor_over(path))
    client.complete(a_request())

    with pytest.raises(RecordingNotConsumed) as raised:
        client.assert_consumed()

    assert "1 of 2" in str(raised.value)


# --- recording produces what the replay reads back ---


def test_the_recording_client_appends_exchanges_the_recording_loader_reads_back(
    tmp_path: Path,
) -> None:
    """How a maintainer with credentials makes a fixture: run once, replay forever."""

    class Stub:
        def complete(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse.from_body(request, a_response())

    path = tmp_path / "recorded.jsonl"
    client = RecordingModelClient(Stub(), path)
    client.complete(a_request())
    client.complete(a_request(max_tokens=99))

    recording = Recording.load(path)
    assert [exchange.request["max_tokens"] for exchange in recording.exchanges] == [16000, 99]


def test_a_recorded_exchange_replays_against_the_request_that_produced_it(
    tmp_path: Path,
) -> None:
    class Stub:
        def complete(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse.from_body(request, a_response(text="recorded"))

    path = tmp_path / "recorded.jsonl"
    RecordingModelClient(Stub(), path).complete(a_request(output_schema=SCHEMA, effort="high"))

    replayed = ReplayModelClient(cursor_over(path)).complete(
        a_request(output_schema=SCHEMA, effort="high")
    )
    assert replayed.text() == "recorded"


# --- credentials are named, never read out (D15) ---


def clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (ENV_API_KEY, ENV_AUTH_TOKEN, "ANTHROPIC_PROFILE", "ANTHROPIC_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)


def test_an_api_key_in_the_environment_resolves_as_the_api_key_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_environment(monkeypatch)
    monkeypatch.setenv(ENV_API_KEY, "sk-ant-secret")

    source = resolve()

    assert source is not None
    assert source.kind == "api_key"
    # The name of the variable, never what is in it: a CredentialSource is safe to print.
    assert source.detail == ENV_API_KEY
    assert "secret" not in source.model_dump_json()


def test_an_auth_token_resolves_when_no_api_key_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_environment(monkeypatch)
    monkeypatch.setenv(ENV_AUTH_TOKEN, "token-value")

    source = resolve()

    assert source is not None
    assert source.kind == "auth_token"
    assert "token-value" not in source.model_dump_json()


def test_a_login_profile_resolves_when_no_environment_variable_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_environment(monkeypatch)

    class Profile:
        profile = "work"

    class Result:
        provider = Profile()

    monkeypatch.setattr("anthropic.default_credentials", lambda **_: Result())

    source = resolve()

    assert source is not None
    assert source.kind == "profile"
    assert source.detail == "work"


def test_a_workload_identity_resolves_as_its_own_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_environment(monkeypatch)

    class WorkloadIdentityCredentials:
        pass

    class Result:
        provider = WorkloadIdentityCredentials()

    monkeypatch.setattr("anthropic.default_credentials", lambda **_: Result())

    source = resolve()

    assert source is not None
    assert source.kind == "workload_identity"


def test_nothing_configured_resolves_to_nothing_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preflight's whole question. A raise here would become a traceback, not a message."""
    clear_environment(monkeypatch)
    monkeypatch.setattr("anthropic.default_credentials", lambda **_: None)

    assert resolve() is None


def test_the_live_client_is_constructible_without_a_request_being_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK defers the credential failure to the first call, so building one is safe."""
    monkeypatch.setenv(ENV_API_KEY, "sk-ant-not-used")

    assert LiveAnthropicClient().client is not None


# --- the Claude Code login is tried last, and decides the Backend (ticket 19, decision 21) ---

CLI = ClaudeCodeCli(path="/opt/claude/bin/claude", version="2.1.280")


def test_a_claude_code_login_resolves_when_nothing_on_the_api_side_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_environment(monkeypatch)
    monkeypatch.setattr("anthropic.default_credentials", lambda **_: None)

    monkeypatch.setattr(credentials, "claude_code_probe", lambda: CLI)

    source = resolve()

    assert source == CredentialSource(
        kind="claude_code", detail="Claude Code login (2.1.280)", cli=CLI
    )


def test_no_claude_code_login_either_resolves_to_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_environment(monkeypatch)
    monkeypatch.setattr("anthropic.default_credentials", lambda **_: None)

    monkeypatch.setattr(credentials, "claude_code_probe", lambda: None)

    assert resolve() is None


def test_an_api_key_wins_and_the_claude_code_cli_is_never_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_environment(monkeypatch)
    monkeypatch.setenv(ENV_API_KEY, "sk-ant-secret")
    asked: list[str] = []

    def probe() -> ClaudeCodeCli:
        asked.append("probe")
        return CLI

    monkeypatch.setattr(credentials, "claude_code_probe", probe)

    source = resolve()

    assert source is not None and source.kind == "api_key"
    assert asked == []


def test_a_login_profile_also_wins_over_claude_code(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_environment(monkeypatch)

    class Profile:
        profile = "work"

    class Result:
        provider = Profile()

    monkeypatch.setattr("anthropic.default_credentials", lambda **_: Result())
    monkeypatch.setattr(credentials, "claude_code_probe", lambda: CLI)

    source = resolve()

    assert source is not None and source.kind == "profile"


def test_the_suite_never_finds_a_developers_claude_code_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`tests/conftest.py` switches the default probe off for every test not marked live."""
    clear_environment(monkeypatch)
    monkeypatch.setattr("anthropic.default_credentials", lambda **_: None)

    assert resolve() is None


def test_each_credential_kind_selects_its_backend() -> None:
    def source(kind: str) -> CredentialSource:
        return CredentialSource(kind=kind, detail="x", cli=CLI if kind == "claude_code" else None)  # type: ignore[arg-type]

    assert {
        kind: backend_for(source(kind), replay=False)
        for kind in ("api_key", "auth_token", "profile", "workload_identity", "claude_code")
    } == {
        "api_key": Backend(kind="anthropic_api"),
        "auth_token": Backend(kind="anthropic_api"),
        "profile": Backend(kind="anthropic_api"),
        "workload_identity": Backend(kind="anthropic_api"),
        "claude_code": Backend(kind="claude_code", cli_version="2.1.280"),
    }


def test_a_replay_is_the_replay_backend_whatever_resolved() -> None:
    claude = CredentialSource(kind="claude_code", detail="x", cli=CLI)

    assert backend_for(claude, replay=True) == Backend(kind="replay", cli_version=None)
    assert backend_for(None, replay=True) == Backend(kind="replay")
    assert backend_for(None, replay=False) is None


def test_the_live_client_is_the_one_the_backend_names_built_from_what_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_API_KEY, "sk-ant-not-used")
    login = CredentialSource(kind="claude_code", detail="x", cli=CLI)

    api = live_client(Backend(kind="anthropic_api"), None)
    claude = live_client(Backend(kind="claude_code", cli_version="2.1.280"), login)

    assert isinstance(api, LiveAnthropicClient)
    assert isinstance(claude, ClaudeCodeClient) and claude.cli == CLI
    with pytest.raises(ValueError, match="no live client"):
        live_client(Backend(kind="replay"), login)


def test_a_claude_code_backend_without_a_resolved_cli_is_refused_not_probed_for() -> None:
    backend = Backend(kind="claude_code", cli_version="2.1.280")
    api_key = CredentialSource(kind="api_key", detail=ENV_API_KEY)

    for source in (None, api_key):
        with pytest.raises(ClaudeCodeError, match="resolved no Claude Code CLI"):
            live_client(backend, source)


def test_a_cli_other_than_the_version_the_backend_records_is_refused() -> None:
    newer = CredentialSource(
        kind="claude_code",
        detail="x",
        cli=ClaudeCodeCli(path="/opt/claude/bin/claude", version="2.1.300"),
    )

    with pytest.raises(ClaudeCodeError, match=r"records Claude Code 2\.1\.280 .* is 2\.1\.300"):
        live_client(Backend(kind="claude_code", cli_version="2.1.280"), newer)


WIZARD = Path(__file__).resolve().parents[1] / "scripts" / "credentials-wizard.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
def test_the_credentials_wizard_parses_and_offers_the_claude_code_login_first() -> None:
    """Never executed here (it is for a human); parsed, and its first stage checked."""
    subprocess.run(["bash", "-n", str(WIZARD)], check=True)
    stages = [
        line
        for line in WIZARD.read_text(encoding="utf-8").splitlines()
        if line.startswith("stage ")
    ]

    assert stages == [
        'stage "Claude Code login"',
        'stage "Alternative: an API key"',
        'stage "Alternative: ant auth login"',
        'stage "Verify"',
    ]
    assert "TOTAL_STAGES=4" in WIZARD.read_text(encoding="utf-8")
