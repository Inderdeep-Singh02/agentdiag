"""A Workspace of two Targets, built the way a developer builds one: two `init`s.

Phase-6 decision 7: the seam-1 tests of ticket 24 run every command against a Workspace
under `tmp_path` holding Target `a` (the toy, `init --target a --adapter toy`) and Target
`b` (a Target agentdiag has only been pointed at, `init --target b --adapter …`), so "which
Target did this command resolve" is asked of files `init` really wrote rather than of a
hand-made tree.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
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

PENDING_LINE = (
    "adapter.kind: pending: nothing drives this Target yet; set the Adapter kind and its "
    "environment block (the REVIEW lines in manifest.yaml name what to fill)"
)
"""The one spelling `validate`, `run` and `sync` share for a pending Adapter (ADR-0016 §4,
0.1.2-interfaces decision 12)."""


def two_target_workspace(root: Path) -> Path:
    """`init --target a --adapter toy`, then `init --target b --adapter …`, under `root`; the
    root back."""
    runner = CliRunner()
    for arguments in (
        ["--target", TOY_SLUG, "--adapter", "toy"],
        ["--target", CUSTOM_SLUG, "--adapter", CUSTOM_ADAPTER],
    ):
        result = runner.invoke(app, ["init", "--root", str(root), *arguments])
        assert result.exit_code == 0, result.output
    return root


def toy_workspace(root: Path, slugs: Sequence[str]) -> Path:
    """`init --target <slug> --adapter toy` under `root` for each slug, in the order given;
    the root back. Each Target validates with nothing to say."""
    runner = CliRunner()
    for slug in slugs:
        result = runner.invoke(
            app, ["init", "--root", str(root), "--target", slug, "--adapter", "toy"]
        )
        assert result.exit_code == 0, result.output
    return root


def warnings_of(result: Any) -> list[str]:
    """The `warning:` lines a command printed to stdout, in order."""
    return [line for line in result.stdout.splitlines() if line.startswith("warning:")]


def target_dir(root: Path, slug: str = "default") -> Path:
    """Where `init` writes the Target `slug`; `default` when `init` named no `--target`."""
    return root / ".agentdiag" / "targets" / slug


def manifest_of(root: Path, slug: str = "default") -> Any:
    """The Target's `manifest.yaml`, as YAML reads it."""
    return yaml.safe_load((target_dir(root, slug) / "manifest.yaml").read_text(encoding="utf-8"))


def edit_manifest(root: Path, slug: str, **keys: Any) -> None:
    """Write `keys` over the Target's `manifest.yaml`, each top-level key replaced, and a key
    given as None removed: a hand edit, as a developer makes one between commands."""
    path = target_dir(root, slug) / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key, value in keys.items():
        if value is None:
            manifest.pop(key, None)
        else:
            manifest[key] = value
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def suite_of(root: Path, slug: str = "default") -> Any:
    """The Target's sample Suite, `suites/sample.yaml`, as YAML reads it."""
    path = target_dir(root, slug) / "suites" / "sample.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


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
    "edit_manifest",
    "greeting_recording",
    "manifest_of",
    "suite_of",
    "target_dir",
    "the_target",
    "two_target_workspace",
]
