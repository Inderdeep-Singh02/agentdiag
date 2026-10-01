"""Seam 1: a Manifest whose Adapter or Connector kind nothing registers is refused by every
Run, with the message `agentdiag validate` gives (ADR-0015 §3).

`validate` and preflight share one rule (`manifest_checks.kind_problems`), so `run`,
`run --dry-run` and `rescore` exit 3 with `validate`'s line, and a Connector that can never
be built is not silently stood in for by the Adapter's probe. A rescore reads no Connector,
so only its `adapter.kind` counts; and the kind's problem arrives in the same pass as every
other problem preflight collects.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.adapter import InProcessAdapter
from agentdiag.cli import app
from agentdiag.connector.inprocess import InProcessConnector
from agentdiag.connector.plugins import ADAPTER_GROUP, CONNECTOR_GROUP, registered
from agentdiag.run.preflight import PREFLIGHT_EXIT
from agentdiag.scenario.validate import VALIDATE_EXIT

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
FIXTURES = REPO / "tests" / "fixtures"
TARGET = Path(".agentdiag") / "targets" / "toy-order-desk"
# The rescore source and the recording its Judge replays, as tests/test_rescore_cli.py uses.
BASELINE = "20260923T100000Z-base"
RECORDING = FIXTURES / "recordings" / "toy-orders.jsonl"
MECHANICAL = "where-is-shipped-order"

runner = CliRunner()


def toy(tmp_path: Path, section: str, kind: str, *, source: str | None = None) -> Path:
    """The example Workspace with `<section>.kind` set to `kind`, and the Run fixture
    `source` under its runs/ when one is named."""
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    if source is not None:
        shutil.copytree(FIXTURES / "runs" / source, root / TARGET / "runs" / source)
    path = root / TARGET / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest[section]["kind"] = kind
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return root


def invoke(*arguments: str) -> Any:
    return runner.invoke(app, list(arguments))


def rescore(root: Path, *arguments: str) -> Any:
    return invoke("rescore", BASELINE, "--root", str(root), *arguments, "--replay", str(RECORDING))


def validate_line(root: Path, where: str) -> str:
    """The `<where>: <message>` part of `validate`'s error line for `where`."""
    result = invoke("validate", "--root", str(root))
    assert result.exit_code == VALIDATE_EXIT, result.output
    [line] = [line for line in result.output.splitlines() if f": {where}: " in line]
    return line[line.index(f"{where}: ") :]


@pytest.mark.parametrize("arguments", [("--dry-run",), ()], ids=["dry-run", "run"])
def test_an_unknown_connector_kind_is_refused_with_validates_message(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    root = toy(tmp_path, "connector", "nope")
    expected = validate_line(root, "connector.kind")

    result = invoke("run", "--root", str(root), *arguments)

    assert result.exit_code == PREFLIGHT_EXIT, result.output
    assert expected in result.output.splitlines()
    assert "install the plugin distribution that registers one under agentdiag.connectors" in (
        expected
    )
    assert "probe stands in" not in result.output
    assert "sync not_checked" not in result.output


def test_an_unknown_adapter_kind_is_refused_once_with_validates_message(tmp_path: Path) -> None:
    root = toy(tmp_path, "adapter", "nope")
    expected = validate_line(root, "adapter.kind")

    result = invoke("run", "--root", str(root), "--dry-run")

    assert result.exit_code == PREFLIGHT_EXIT, result.output
    assert expected in result.output.splitlines()
    assert "agentdiag.adapters" in expected
    assert "Adapter: " not in result.output
    assert result.output.count("'nope'") == 1


def test_an_unknown_kind_and_a_suite_problem_are_reported_in_one_pass(tmp_path: Path) -> None:
    root = toy(tmp_path, "connector", "nope")
    expected = validate_line(root, "connector.kind")
    (root / TARGET / "suites" / "orders.yaml").write_text("scenarios: 5\n", encoding="utf-8")

    result = invoke("run", "--root", str(root), "--dry-run")

    assert result.exit_code == PREFLIGHT_EXIT, result.output
    lines = result.output.splitlines()
    assert expected in lines
    assert any("orders.yaml" in line for line in lines), result.output


def test_a_connector_kind_the_test_seam_registers_still_dry_runs(tmp_path: Path) -> None:
    root = toy(tmp_path, "connector", "custom")

    with registered("custom", InProcessConnector, group=CONNECTOR_GROUP):
        result = invoke("run", "--root", str(root), "--dry-run")

    assert result.exit_code == 0, result.output


def test_an_adapter_kind_the_test_seam_registers_still_dry_runs(tmp_path: Path) -> None:
    root = toy(tmp_path, "adapter", "custom")

    with registered("custom", InProcessAdapter, group=ADAPTER_GROUP):
        result = invoke("run", "--root", str(root), "--dry-run")

    assert result.exit_code == 0, result.output


def test_a_rescore_refuses_an_unknown_adapter_kind_with_validates_message(
    tmp_path: Path,
) -> None:
    root = toy(tmp_path, "adapter", "nope", source=BASELINE)
    expected = validate_line(root, "adapter.kind")

    result = rescore(root, "--scenario", MECHANICAL)

    assert result.exit_code == PREFLIGHT_EXIT, result.output
    assert expected in result.output.splitlines()
    assert "Adapter: " not in result.output


def test_a_rescore_does_not_need_the_connector_kind(tmp_path: Path) -> None:
    """A rescore drives nothing and checks no Sync, so it never builds the Connector."""
    root = toy(tmp_path, "connector", "nope", source=BASELINE)

    result = rescore(root, "--scenario", MECHANICAL)

    assert result.exit_code == 0, result.output
