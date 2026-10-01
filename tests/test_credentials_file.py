"""Seam 1: the one credentials file, read once at start-up (ticket 17, phase-8 decision 4).

Every test hands `load_credentials_file` its own `path=` and `environ=`, never the real
`~/.agentdiag/env`; the one test through the CLI points `$AGENTDIAG_ENV_FILE` at a file
under `tmp_path` (conftest points it at nothing for every other test).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.credentials_file import (
    DEFAULT_PATH,
    ENV_PATH_VARIABLE,
    credentials_path,
    load_credentials_file,
)

runner = CliRunner()


def written(tmp_path: Path, text: str, mode: int = 0o600) -> Path:
    path = tmp_path / "env"
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def test_no_file_loads_nothing(tmp_path: Path) -> None:
    environ: dict[str, str] = {}

    loaded = load_credentials_file(environ, tmp_path / "absent")

    assert loaded.path is None
    assert (loaded.loaded, loaded.skipped, loaded.warning) == ([], [], None)
    assert environ == {}


def test_key_value_lines_comments_export_and_quotes(tmp_path: Path) -> None:
    path = written(
        tmp_path,
        "# written by the wizard\n"
        "\n"
        "ACME_AUTH_TOKEN=tok-plain\n"
        "export HELP_DESK_AUTH_TOKEN='tok single'\n"
        'LITELLM_MASTER_KEY="tok=double"\n'
        "  ANALYTICS_DB_URL = postgres://reader@db.example.test/analytics  \n",
    )
    environ: dict[str, str] = {}

    loaded = load_credentials_file(environ, path)

    assert loaded.path == path
    assert loaded.warning is None
    assert loaded.loaded == [
        "ACME_AUTH_TOKEN",
        "HELP_DESK_AUTH_TOKEN",
        "LITELLM_MASTER_KEY",
        "ANALYTICS_DB_URL",
    ]
    assert environ == {
        "ACME_AUTH_TOKEN": "tok-plain",
        "HELP_DESK_AUTH_TOKEN": "tok single",
        "LITELLM_MASTER_KEY": "tok=double",
        "ANALYTICS_DB_URL": "postgres://reader@db.example.test/analytics",
    }


def test_a_variable_the_process_has_is_never_overridden(tmp_path: Path) -> None:
    path = written(tmp_path, "ACME_AUTH_TOKEN=from-the-file\nOTHER=from-the-file\n")
    environ = {"ACME_AUTH_TOKEN": "from-the-shell"}

    loaded = load_credentials_file(environ, path)

    assert environ["ACME_AUTH_TOKEN"] == "from-the-shell"
    assert (loaded.loaded, loaded.skipped) == (["OTHER"], ["ACME_AUTH_TOKEN"])


def test_a_file_others_can_read_is_loaded_with_a_warning_naming_its_mode(tmp_path: Path) -> None:
    path = written(tmp_path, "ACME_AUTH_TOKEN=tok\n", mode=0o644)
    environ: dict[str, str] = {}

    loaded = load_credentials_file(environ, path)

    assert environ == {"ACME_AUTH_TOKEN": "tok"}
    assert loaded.warning is not None
    assert "mode 0644" in loaded.warning and "chmod 600" in loaded.warning


def test_a_malformed_line_is_named_by_number_and_never_quoted(tmp_path: Path) -> None:
    path = written(tmp_path, "GOOD=1\nthis-holds-tok-secret-9 no equals\n2BAD=x\nALSO_GOOD=2\n")
    environ: dict[str, str] = {}

    loaded = load_credentials_file(environ, path)

    assert loaded.loaded == ["GOOD", "ALSO_GOOD"]
    assert loaded.warning is not None
    assert "line 2, 3 is not KEY=value" in loaded.warning
    assert "tok-secret" not in loaded.warning and "2BAD" not in loaded.warning


def test_the_path_is_the_override_or_the_default(tmp_path: Path) -> None:
    assert credentials_path({}) == DEFAULT_PATH
    assert Path.home() / ".agentdiag" / "env" == DEFAULT_PATH
    assert credentials_path({ENV_PATH_VARIABLE: str(tmp_path / "mine")}) == tmp_path / "mine"


def test_the_cli_loads_the_file_once_before_any_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    variable = "AGENTDIAG_TEST_CREDENTIAL"
    # Recorded as absent, so the teardown removes what the loader sets.
    monkeypatch.setenv(variable, "placeholder")
    monkeypatch.delenv(variable)
    path = written(tmp_path, f"{variable}=from-the-file\n", mode=0o640)
    monkeypatch.setenv(ENV_PATH_VARIABLE, str(path))

    result = runner.invoke(app, ["validate", "--root", str(tmp_path / "nowhere")])

    assert os.environ[variable] == "from-the-file"
    assert result.stderr.count("mode 0640") == 1
    assert "from-the-file" not in result.output


def test_a_variable_set_but_empty_is_unset_and_the_file_says_so(tmp_path: Path) -> None:
    path = written(tmp_path, "ACME_AUTH_TOKEN=from-the-file\n")
    environ = {"ACME_AUTH_TOKEN": ""}

    loaded = load_credentials_file(environ, path)

    assert environ["ACME_AUTH_TOKEN"] == "from-the-file"
    assert (loaded.loaded, loaded.skipped) == (["ACME_AUTH_TOKEN"], [])
    assert loaded.warning is not None
    assert "ACME_AUTH_TOKEN set but empty in the process" in loaded.warning
