"""A Workspace of two Targets, built the way a developer builds one: two `init`s.

Phase-6 decision 7: the seam-1 tests of ticket 24 run every command against a Workspace
under `tmp_path` holding Target `a` (the toy, `init --target a`) and Target `b` (a Target
agentdiag has only been pointed at, `init --target b --adapter …`), so "which Target did
this command resolve" is asked of files `init` really wrote rather than of a hand-made tree.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.workspace import TargetPaths, Workspace

REPO = Path(__file__).resolve().parents[2]
RECORDINGS = REPO / "tests" / "fixtures" / "recordings"

TOY_SLUG = "a"
TOY_SCENARIO = "cancel-processing-order"
TOY_RECORDING = RECORDINGS / "toy-cancel.jsonl"

CUSTOM_SLUG = "b"
CUSTOM_ADAPTER = "python:tests.fakes.idle_target:make_target"
CUSTOM_SCENARIO = "first-turn"
GREETING = "Hello! What can you help me with today?"
"""The Turn of the sample Scenario `init --adapter` writes."""


def two_target_workspace(root: Path) -> Path:
    """`init --target a`, then `init --target b --adapter …`, under `root`; the root back."""
    runner = CliRunner()
    for arguments in (
        ["--target", TOY_SLUG],
        ["--target", CUSTOM_SLUG, "--adapter", CUSTOM_ADAPTER],
    ):
        result = runner.invoke(app, ["init", "--root", str(root), *arguments])
        assert result.exit_code == 0, result.output
    return root


def the_target(root: Path) -> TargetPaths:
    """The one Target of the Workspace at `root`: what a command with no `--target` runs."""
    return Workspace.find(root).resolve(None)


def greeting_recording(directory: Path) -> Path:
    """The idle fake's recorded exchange, re-keyed to the sample Scenario's Turn: replay
    matches the exact request body, and the committed recording was made for "Hello"."""
    exchange = json.loads((RECORDINGS / "fake-idle.jsonl").read_text(encoding="utf-8"))
    exchange["request"]["messages"][0]["content"] = GREETING
    path = directory / "greeting.jsonl"
    path.write_text(json.dumps(exchange) + "\n", encoding="utf-8")
    return path


__all__ = [
    "CUSTOM_ADAPTER",
    "CUSTOM_SCENARIO",
    "CUSTOM_SLUG",
    "TOY_RECORDING",
    "TOY_SCENARIO",
    "TOY_SLUG",
    "greeting_recording",
    "the_target",
    "two_target_workspace",
]
