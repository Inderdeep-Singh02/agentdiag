"""What every test shares: no test reaches a model through a developer's Claude Code login.

`credentials.resolve()` asks the Claude Code CLI last (ticket 19, decision 21), and on a
developer's machine that CLI is usually logged in. A test that cleared the API key to see
preflight refuse would then find the login instead, and a test that ran a judged Eval
without `--replay` would make a real, billed call. So the probe answers None for every test
not marked `live`; the `live` tests keep the real one, because reaching the model is what
they are for.

And no test reads the developer's credentials file (phase-8 decision 4): the CLI's `main`
callback loads `~/.agentdiag/env` at every invocation, so `$AGENTDIAG_ENV_FILE` names a file
that does not exist for every test, and a test of the loader passes its own `path=`.

And no test depends on the shell's colour settings: `FORCE_COLOR` is removed for every test,
so the CLI's help renders as plain words for the tests that read it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentdiag.credentials_file import ENV_PATH_VARIABLE
from agentdiag.model import credentials

NO_CREDENTIALS_FILE = Path(__file__).parent / "fixtures" / "no-such-credentials-file"
"""What `$AGENTDIAG_ENV_FILE` names in every test: a path nothing creates."""


@pytest.fixture(autouse=True)
def no_claude_code_login(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("live") is None:
        monkeypatch.setattr(credentials, "claude_code_probe", lambda: None)


@pytest.fixture(autouse=True)
def no_credentials_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PATH_VARIABLE, str(NO_CREDENTIALS_FILE))


@pytest.fixture(autouse=True)
def plain_help_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rich bolds and colours the CLI's help when the shell exports `FORCE_COLOR`, and the
    escape codes split `--tag` in two; the tests that read `--help` read the words, not the
    colours (seen 2026-09-23 under a shell exporting `FORCE_COLOR=3`)."""
    monkeypatch.delenv("FORCE_COLOR", raising=False)
