"""Plugin registration: Adapter and Connector kinds found by entry point (decision 21).

Core registers its own `inprocess` kinds in `pyproject.toml`, so they are found the way a
plugin's are; an unknown kind names the package that would provide it; a kind two
distributions register is refused naming both; `preflight.build_adapter` resolves
`adapter.kind` through the same lookup; and the in-process override the fake Connector uses
is consulted first and undone afterwards (ADR-0014 §1, §2).
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agentdiag.adapter import InProcessAdapter
from agentdiag.cli import app
from agentdiag.connector import plugins
from agentdiag.connector.inprocess import InProcessConnector
from agentdiag.connector.plugins import (
    ADAPTER_GROUP,
    CONNECTOR_GROUP,
    AmbiguousKind,
    UnknownKind,
    adapter_class,
    connector_class,
    registered,
)

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"

runner = CliRunner()


@pytest.fixture
def fresh_lookup() -> Iterator[None]:
    """The lookup is cached per process; a test that fakes the entry points clears it."""
    plugins.installed.cache_clear()
    yield
    plugins.installed.cache_clear()


def test_core_registers_its_in_process_kinds_by_entry_point() -> None:
    assert plugins.installed(ADAPTER_GROUP)["inprocess"].value == (
        "agentdiag.adapter.inprocess:InProcessAdapter"
    )
    assert adapter_class("inprocess") is InProcessAdapter
    assert connector_class("inprocess") is InProcessConnector


def test_an_unknown_kind_with_no_known_distribution_says_to_install_a_plugin() -> None:
    with pytest.raises(UnknownKind) as refused:
        connector_class("acme")

    assert str(refused.value).startswith("no Connector of kind 'acme' is installed (installed: ")
    assert str(refused.value).endswith(
        "install the plugin distribution that registers one under agentdiag.connectors"
    )


def test_an_unknown_kind_nobody_knows_names_the_installed_kinds() -> None:
    # `http` was the unknown name until ticket 17 registered it; `grpc` is nobody's.
    with pytest.raises(
        UnknownKind, match=r"no Adapter of kind 'grpc' is installed \(installed: http, inprocess\)"
    ):
        adapter_class("grpc")


def test_a_kind_two_distributions_register_is_refused_naming_both(
    monkeypatch: pytest.MonkeyPatch, fresh_lookup: None
) -> None:
    def point(distribution: str) -> Any:
        return SimpleNamespace(
            name="acme", value=f"{distribution}:C", dist=SimpleNamespace(name=distribution)
        )

    monkeypatch.setattr(
        plugins, "entry_points", lambda group: [point("agentdiag-acme"), point("acme-fork")]
    )

    with pytest.raises(AmbiguousKind, match="registered by both agentdiag-acme and acme-fork"):
        connector_class("acme")


def test_a_core_kind_missing_says_the_checkout_needs_uv_sync(
    monkeypatch: pytest.MonkeyPatch, fresh_lookup: None
) -> None:
    monkeypatch.setattr(plugins, "entry_points", lambda group: [])

    with pytest.raises(UnknownKind, match="a fresh checkout needs `uv sync`"):
        adapter_class("inprocess")


def test_a_kind_served_by_the_test_seam_is_never_listed_as_installed(fresh_lookup: None) -> None:
    with registered("seam-only", InProcessConnector), pytest.raises(UnknownKind) as refused:
        connector_class("elsewhere")

    assert "seam-only" not in str(refused.value)


def test_the_override_is_consulted_first_and_undone_after(fresh_lookup: None) -> None:
    class Stand(InProcessConnector):
        pass

    with registered("inprocess", Stand):
        assert connector_class("inprocess") is Stand
    assert connector_class("inprocess") is InProcessConnector
    with registered("custom", Stand, group=CONNECTOR_GROUP):
        assert connector_class("custom") is Stand
    with pytest.raises(UnknownKind):
        connector_class("custom")


def toy(tmp_path: Path, **adapter: Any) -> Path:
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["adapter"].update(adapter)
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return root


def test_a_run_resolves_the_adapter_kind_through_the_entry_points(tmp_path: Path) -> None:
    built: list[str] = []

    class Counting(InProcessAdapter):
        def check(self) -> None:
            built.append(self.environment)
            super().check()

    with registered("counting", Counting, group=ADAPTER_GROUP):
        result = runner.invoke(
            app, ["run", "--root", str(toy(tmp_path, kind="counting")), "--dry-run"]
        )

    assert result.exit_code == 0, result.output
    assert built == ["local"]


def test_core_registers_the_http_adapter_and_its_two_dialects() -> None:
    """Phase-8 decision 1: `http` among the Adapters, `json` and `sse-json` among the
    Dialects, each found by entry point as a plugin's would be, with the same test seam."""
    from agentdiag.adapter.http import HttpAdapter
    from agentdiag.adapter.http.dialects import JsonDialect, SseJsonDialect
    from agentdiag.connector.plugins import (
        CORE_KINDS,
        DIALECT_GROUP,
        dialect_class,
        registered,
    )

    assert "http" in CORE_KINDS
    assert adapter_class("http") is HttpAdapter
    assert dialect_class("json") is JsonDialect
    assert dialect_class("sse-json") is SseJsonDialect
    with pytest.raises(UnknownKind, match=r"no Dialect of kind 'smoke-signals' is installed"):
        dialect_class("smoke-signals")
    with registered("smoke-signals", JsonDialect, group=DIALECT_GROUP):
        assert dialect_class("smoke-signals") is JsonDialect


def test_a_core_kind_of_one_group_is_not_claimed_for_another() -> None:
    """`http` is core's Adapter kind, not a Connector kind: no Connector message says
    agentdiag registers it (review T7)."""
    from agentdiag.connector.plugins import CONNECTOR_GROUP, check_registered

    with pytest.raises(UnknownKind) as unknown:
        check_registered(CONNECTOR_GROUP, "http")

    assert "agentdiag registers it itself" not in str(unknown.value)
    assert "(installed: inprocess)" in str(unknown.value)
