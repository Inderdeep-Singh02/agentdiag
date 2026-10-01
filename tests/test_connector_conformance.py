"""Seam 3: the Connector conformance suite, over the in-process Connector and the fake.

The in-process Connector runs against the toy Target's deployed set
(`agentdiag.examples.toy:deployed_set`) and a proxy-row Evidence store served from this
module; the fake (`tests.fakes.fake_connector`) runs from fixtures through the plugin
registry's test seam, as every seam-1 Sync test uses it (phase-6 decisions 23, 24). The
second toy, the help desk, joins with its platform's deployed set and its proxy rows
(ticket 13, decision 38), once per environment: `local` (`platform.DEPLOYED`) and the
protected `staging` (`platform.STAGING`, phase-7 decision 16).

Writes (phase-7 decision 15): the first toy's deployed set is a callable, so the in-process
Connector refuses every write to it; the help desk's are module-level mappings, written in
place, so each help-desk case restores the platform after itself; the fake writes into its
own mapping with the same compare-and-swap.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

from agentdiag.connector.base import Connector
from agentdiag.connector.conformance import ConnectorConformance
from agentdiag.connector.environment import ResolvedEnvironment
from agentdiag.connector.inprocess import InProcessConnector
from agentdiag.connector.plugins import registered
from agentdiag.examples.helpdesk import platform
from agentdiag.examples.toy import deployed_set
from agentdiag.run.manifest import ConnectorSection, Manifest
from tests.fakes.fake_connector import FAKE_KIND, FakeConnector

PROXY_ROWS: list[dict[str, Any]] = [
    {"request_id": "req-1", "created_at": "2026-09-27T10:00:00Z", "conversation_id": "c-1"},
    {"request_id": "req-2", "created_at": "2026-09-27T10:00:05Z", "conversation_id": "c-2"},
]
"""A two-row proxy store, invented, in the generic row shape (decision 33)."""


def manifest_with(connector: dict[str, Any]) -> Manifest:
    return Manifest.model_validate(
        {
            "target": {"name": "conformance"},
            "adapter": {"kind": "inprocess", "environments": {"default": "local", "local": {}}},
            "connector": connector,
        }
    )


def needing(
    kind: str, variable: str, local: dict[str, Any], environment: str = "local"
) -> Manifest:
    return manifest_with(
        {
            "kind": kind,
            "environments": {
                "dev": {**local, "credentials": {"token": "AGENTDIAG_OTHER_TOKEN"}},
                environment: {**local, "credentials": {"token": variable}},
            },
        }
    )


class TestInProcessConnectorConformance(ConnectorConformance):
    """The in-process Connector over the first toy (decision 22)."""

    BLOCK: ClassVar[dict[str, Any]] = {"deployed": "agentdiag.examples.toy:deployed_set"}

    def make_connector(self, tmp_path: Path) -> Connector:
        section = ConnectorSection(
            kind="inprocess",
            environments={"local": dict(self.BLOCK)},
            evidence={"proxy": {"rows": "tests.test_connector_conformance:PROXY_ROWS"}},
        )
        local = ResolvedEnvironment(
            name="local", identifiers=dict(self.BLOCK), protected=False, side_effects="none"
        )
        return InProcessConnector(section, environments={"local": local})

    def a_manifest_needing(self, variable: str) -> Manifest:
        return needing("inprocess", variable, dict(self.BLOCK))

    def writes(self) -> bool:
        return False

    def test_a_write_to_a_callable_deployed_set_is_refused_naming_it(self, tmp_path: Path) -> None:
        from agentdiag.connector.base import WriteRefused

        with pytest.raises(WriteRefused, match=r"agentdiag\.examples\.toy:deployed_set"):
            self.make_connector(tmp_path).write_deployed_set(
                "local", {"prompt.system": "x"}, expected_fingerprint="0" * 64, change_record=None
            )

    def test_the_toy_read_is_the_running_modules_prompt_tools_and_model(
        self, tmp_path: Path
    ) -> None:
        read = self.make_connector(tmp_path).read_deployed_set("local")

        expected = deployed_set()
        assert read.prompts == expected["prompts"]
        assert read.tools == expected["tools"]
        assert read.model == "claude-sonnet-5"
        assert read.provider == "anthropic"
        assert read.flows == {} and read.tier is None

    def test_evidence_rows_are_filtered_by_the_query(self, tmp_path: Path) -> None:
        from agentdiag.connector.base import EvidenceQuery

        connector = self.make_connector(tmp_path)

        one = connector.read_evidence("local", "proxy", EvidenceQuery(conversation_id="c-2"))
        cut = connector.read_evidence("local", "proxy", EvidenceQuery(limit=1))

        assert [row["request_id"] for row in one.rows] == ["req-2"]
        assert [row["request_id"] for row in cut.rows] == ["req-1"] and cut.truncated


@pytest.fixture
def platform_restored() -> Iterator[None]:
    """The help desk's platform as it was before the test: a write through the in-process
    Connector edits `DEPLOYED` or `STAGING` in place, and the next test must not see it."""
    kept = {name: copy.deepcopy(getattr(platform, name)) for name in ("DEPLOYED", "STAGING")}
    yield
    for name, value in kept.items():
        held = getattr(platform, name)
        held.clear()
        held.update(value)


class TestHelpDeskConnectorConformance(ConnectorConformance):
    """The in-process Connector over the second toy: its deployed set is a module-level
    mapping, not a callable, and its Evidence stores are one mapping by kind (decision 38)."""

    BLOCK: ClassVar[dict[str, Any]] = {"deployed": "agentdiag.examples.helpdesk.platform:DEPLOYED"}
    EVIDENCE: ClassVar[dict[str, Any]] = {
        "proxy": {"rows": "agentdiag.examples.helpdesk.platform:EVIDENCE"}
    }
    ENVIRONMENT: ClassVar[str] = "local"

    @pytest.fixture(autouse=True)
    def _restored(self, platform_restored: None) -> None:
        return None

    def an_environment(self) -> str:
        return self.ENVIRONMENT

    def make_connector(self, tmp_path: Path) -> Connector:
        name = self.ENVIRONMENT
        section = ConnectorSection(
            kind="inprocess", environments={name: dict(self.BLOCK)}, evidence=self.EVIDENCE
        )
        resolved = ResolvedEnvironment(
            name=name, identifiers=dict(self.BLOCK), protected=False, side_effects="none"
        )
        return InProcessConnector(section, environments={name: resolved})

    def a_manifest_needing(self, variable: str) -> Manifest:
        return needing("inprocess", variable, dict(self.BLOCK), self.ENVIRONMENT)

    def a_prompt_section_to_write(self) -> tuple[str, str]:
        return "prompt.system#tone", "Terse, and never more than two sentences.\n"

    def test_a_heading_write_keeps_every_other_section_and_the_headings(
        self, tmp_path: Path
    ) -> None:
        from agentdiag.sync.observe import deployed_fingerprint

        connector = self.make_connector(tmp_path)
        before = connector.read_deployed_set(self.an_environment())

        connector.write_deployed_set(
            self.an_environment(),
            {"prompt.system#tone": "\nTerse.\n"},
            expected_fingerprint=deployed_fingerprint(before),
            change_record=None,
        )

        text = connector.read_deployed_set(self.an_environment()).prompts["system"]
        assert text == before.prompts["system"].split("# Tone")[0] + "# Tone\n\nTerse.\n"

    def test_the_read_is_the_platforms_prompt_tools_and_model(self, tmp_path: Path) -> None:
        read = self.make_connector(tmp_path).read_deployed_set(self.an_environment())

        assert read.prompts == {"system": platform.SYSTEM_PROMPT}
        assert read.tools == platform.TOOL_SCHEMAS
        assert (read.model, read.provider) == ("claude-sonnet-5", "anthropic")

    def test_an_edit_on_the_platform_is_in_the_next_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connector = self.make_connector(tmp_path)
        held = getattr(platform, "STAGING" if self.ENVIRONMENT == "staging" else "DEPLOYED")
        monkeypatch.setitem(held["prompts"], "system", "# Rules\n\n1. Be brief.\n")

        read = connector.read_deployed_set(self.an_environment())
        assert read.prompts["system"] == "# Rules\n\n1. Be brief.\n"

    def test_the_proxy_store_is_the_kinds_entry_of_the_platforms_evidence(
        self, tmp_path: Path
    ) -> None:
        from agentdiag.connector.base import EvidenceQuery

        rows = self.make_connector(tmp_path).read_evidence(
            self.an_environment(), "proxy", EvidenceQuery(conversation_id="hd-0001")
        )

        assert rows.rows == platform.EVIDENCE["proxy"]

    def test_a_mapping_without_the_kinds_entry_is_refused_naming_it(self, tmp_path: Path) -> None:
        from agentdiag.connector.base import ConnectorError, EvidenceQuery

        section = ConnectorSection(
            kind="inprocess",
            environments={"local": dict(self.BLOCK)},
            evidence={"conversation": {"rows": "agentdiag.examples.helpdesk.platform:EVIDENCE"}},
        )
        local = ResolvedEnvironment(
            name="local", identifiers=dict(self.BLOCK), protected=False, side_effects="none"
        )
        connector = InProcessConnector(section, environments={"local": local})

        with pytest.raises(ConnectorError, match="conversation"):
            connector.read_evidence("local", "conversation", EvidenceQuery())


class TestHelpDeskStagingConnectorConformance(TestHelpDeskConnectorConformance):
    """The help desk's protected `staging` (phase-7 decision 16): a deep copy of the
    platform made at import, so a write to one environment never reaches the other."""

    BLOCK: ClassVar[dict[str, Any]] = {"deployed": "agentdiag.examples.helpdesk.platform:STAGING"}
    ENVIRONMENT: ClassVar[str] = "staging"

    def test_a_write_to_staging_never_reaches_local(self, tmp_path: Path) -> None:
        from agentdiag.sync.observe import deployed_fingerprint

        connector = self.make_connector(tmp_path)
        local = copy.deepcopy(platform.DEPLOYED)

        connector.write_deployed_set(
            "staging",
            {"prompt.system#tone": "Terse.\n", "tool.escalate": {"name": "escalate"}},
            expected_fingerprint=deployed_fingerprint(connector.read_deployed_set("staging")),
            change_record=None,
        )

        assert local == platform.DEPLOYED
        assert platform.STAGING["tools"]["escalate"] == {"name": "escalate"}


class TestFakeConnectorConformance(ConnectorConformance):
    """The fake every seam-1 Sync test uses (decision 23), writing into its own mapping."""

    @pytest.fixture(autouse=True)
    def _served(self) -> Iterator[None]:
        with registered(FAKE_KIND, FakeConnector({}).as_kind()):
            yield

    def make_connector(self, tmp_path: Path) -> Connector:
        return FakeConnector(
            {"local": deployed_set()},
            {("local", "proxy"): [dict(row) for row in PROXY_ROWS]},
            refuse_writes=False,
        )

    def a_manifest_needing(self, variable: str) -> Manifest:
        return needing(FAKE_KIND, variable, {})

    def test_every_call_is_recorded(self, tmp_path: Path) -> None:
        connector = self.make_connector(tmp_path)
        assert isinstance(connector, FakeConnector)

        connector.read_deployed_set("local")

        assert connector.calls == [("read_deployed_set", "local", None)]

    def test_every_write_is_recorded_with_what_it_expected(self, tmp_path: Path) -> None:
        from agentdiag.sync.observe import deployed_fingerprint

        connector = self.make_connector(tmp_path)
        assert isinstance(connector, FakeConnector)
        expected = deployed_fingerprint(connector.read_deployed_set("local"))

        connector.write_deployed_set(
            "local", {"prompt.system": "x\n"}, expected_fingerprint=expected, change_record=None
        )

        assert connector.writes == [("local", {"prompt.system": "x\n"}, expected)]


class TestRefusingFakeConnectorConformance(TestFakeConnectorConformance):
    """The fake as every Sync test before ticket 27 built it: refusing every write."""

    def make_connector(self, tmp_path: Path) -> Connector:
        return FakeConnector(
            {"local": deployed_set()}, {("local", "proxy"): [dict(row) for row in PROXY_ROWS]}
        )

    def writes(self) -> bool:
        return False

    def test_every_write_is_recorded_with_what_it_expected(self, tmp_path: Path) -> None:
        pytest.skip("a refusing fake records no write")
