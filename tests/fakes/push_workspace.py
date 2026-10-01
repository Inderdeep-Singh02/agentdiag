"""A Workspace for the pull and push tests (ticket 27): the first toy, its system prompt and
its lookup tool's schema as path pointers, its Connector the fake, in a git repository.

The fake serves two environments, `local` (unprotected) and `prod` (protected by its name,
phase-6 decision 8), each holding `PROMPT` as the system prompt and the toy's tool schemas,
and writes with the compare-and-swap (`refuse_writes=False`). The Workspace is committed, so
git vouches for every pointed file until a test edits one; `sync --env <env>` records the
Fingerprint a test starts from.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.connector.plugins import registered
from agentdiag.examples.toy import TOOL_SCHEMAS, deployed_set
from agentdiag.sync.fingerprint import Fingerprint
from tests.fakes.fake_connector import FAKE_KIND, FakeConnector

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "examples" / "toy"
SLUG = "toy-order-desk"

PROMPT = """You answer for the order desk.

# Persona

A patient order clerk.

# Rules

1. Look up before you answer.
2. Cancel only a processing order.

# Tone

Plain and brief.
"""
"""A prompt with a preamble and three headings."""

LOCAL_RULES = PROMPT.replace("Cancel only a processing order.", "Never cancel a shipped order.")
"""The fix a developer makes in the local file: rule 2 rewritten."""

DEPLOYED_TONE = PROMPT.replace("Plain and brief.", "Warm, and at most three sentences.")
"""An edit made on the platform: the tone rewritten."""

LOOKUP = next(schema for schema in TOOL_SCHEMAS if schema["name"] == "lookup_order")

runner = CliRunner()


def served() -> dict[str, Any]:
    read = deployed_set()
    read["prompts"] = {"system": PROMPT}
    return read


@pytest.fixture
def fake() -> Iterator[FakeConnector]:
    connector = FakeConnector({"local": served(), "prod": served()}, refuse_writes=False)
    with registered(FAKE_KIND, connector.as_kind()):
        yield connector


def target_dir(root: Path) -> Path:
    return root / ".agentdiag" / "targets" / SLUG


def local_prompt(root: Path) -> Path:
    return target_dir(root) / "prompts" / "system.md"


def local_tool(root: Path) -> Path:
    return target_dir(root) / "tools" / "lookup_order.json"


def git(root: Path, *arguments: str) -> str:
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
    )
    return completed.stdout


def commit_all(root: Path, message: str = "state") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message)


def workspace(tmp_path: Path, *, in_git: bool = True, **manifest: Any) -> Path:
    """The toy with path pointers and the fake as its Connector; `manifest` overrides keys."""
    root = tmp_path / "shop"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    directory = target_dir(root)
    (directory / "prompts").mkdir()
    local_prompt(root).write_text(PROMPT, encoding="utf-8")
    (directory / "tools").mkdir()
    from agentdiag.sync.pointed import render_schema

    local_tool(root).write_text(render_schema(LOOKUP), encoding="utf-8")
    path = directory / "manifest.yaml"
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    loaded["connector"] = {"kind": FAKE_KIND, "environments": {"local": {}, "prod": {}}}
    loaded["prompts"] = {"system": "prompts/system.md"}
    loaded["tools"]["lookup_order"]["schema"] = "tools/lookup_order.json"
    loaded.update(manifest)
    path.write_text(yaml.safe_dump(loaded, sort_keys=False), encoding="utf-8")
    if in_git:
        git(root, "init", "-q")
        commit_all(root, "the Workspace")
    return root


def invoke(*arguments: str | Path, input: str | None = None) -> Any:
    """The CLI in-process; `input` is what a person types at a prompt."""
    return runner.invoke(app, [str(argument) for argument in arguments], input=input)


def fingerprint(root: Path) -> Fingerprint:
    return Fingerprint.model_validate_json(
        (target_dir(root) / "fingerprint.json").read_text(encoding="utf-8")
    )


__all__ = [
    "DEPLOYED_TONE",
    "LOCAL_RULES",
    "LOOKUP",
    "PROMPT",
    "SLUG",
    "commit_all",
    "fake",
    "fingerprint",
    "git",
    "invoke",
    "local_prompt",
    "local_tool",
    "served",
    "target_dir",
    "workspace",
]
