"""What every Connector must do, whatever platform it manages (phase-6 decision 24).

A Connector for a platform's management API and the in-process one for a toy read wildly
different things, but `sync` has to mean the same thing behind either. These are the
behaviours that make that true: every operation declared with its access, class and
observations (ADR-0011 §2); a read of a named environment that says which environment it is
and is stable when nothing moved; an unknown environment and an undeclared Evidence store
refused by name; a missing credential refused naming its variable and never borrowed from
another environment; a write refused or receipted, never silently taken; a write carrying a
stale expected Fingerprint refused as `ExpectedFingerprintMoved` (the compare-and-swap of
ADR-0011 §6a), a receipted write visible on the next read with the receipt's
`fingerprint_after` the deployed Fingerprint of that read (phase-7 decisions 10 and 15), a
read-only section (the model, the provider, the tier) never written; and nothing in any of it
that opens a session or writes a file.

A test module subclasses `ConnectorConformance`, overrides the hooks, and inherits every
test, as the Adapter's suite is used (`adapter.conformance`). A platform plugin
subclasses the same class. Each method is named as the specification sentence it enforces.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import get_args

import pytest

from agentdiag.connector.base import (
    READ_DEPLOYED_SET,
    WRITE_DEPLOYED_SET,
    Connector,
    ConnectorError,
    CredentialMissing,
    DeployedSet,
    EvidenceQuery,
    ExpectedFingerprintMoved,
    WriteReceipt,
    WriteRefused,
    evidence_operation,
)
from agentdiag.connector.plugins import build_connector
from agentdiag.run.manifest import Manifest
from agentdiag.sync.observe import deployed_fingerprint, deployed_from
from agentdiag.sync.sections import canonical_text, prompt_sections, sha256
from agentdiag.types import Access, EvidenceKind, SideEffectClass

STALE = "0" * 64
"""An expected Fingerprint no deployed set has: the compare-and-swap must refuse it."""


class ConnectorConformance:
    """The suite. Subclass it, override the hooks, inherit the tests."""

    # --- what a subclass supplies ---

    def make_connector(self, tmp_path: Path) -> Connector:
        """A Connector ready to read, serving `an_environment()`."""
        raise NotImplementedError

    def an_environment(self) -> str:
        """An environment the Connector from `make_connector` names."""
        return "local"

    def a_manifest_needing(self, variable: str) -> Manifest:
        """A Manifest naming this Connector's kind, one of whose environments needs the
        credential `variable` (and another environment needing a different one)."""
        raise NotImplementedError

    def an_undeclared_evidence_kind(self) -> EvidenceKind:
        """An Evidence store the Target behind `make_connector` does not declare."""
        return "voice"

    def writes(self) -> bool:
        """Whether the Connector from `make_connector` receipts a write; False for one that
        refuses every write (`WriteRefused`), such as a deployed set held by a callable."""
        return True

    def a_prompt_section_to_write(self) -> tuple[str, str]:
        """A prompt section id of the deployed set and the text to write into it: the body
        of a heading section (`prompt.<name>#<slug>`) or a whole prompt (`prompt.<name>`)."""
        return "prompt.system", "Written by the Connector conformance suite.\n"

    # --- the behaviours ---

    def test_describe_names_every_operation_with_its_access_class_and_observations(
        self, tmp_path: Path
    ) -> None:
        description = self.make_connector(tmp_path).describe()

        assert description.kind
        names = [operation.name for operation in description.operations]
        assert READ_DEPLOYED_SET in names
        assert WRITE_DEPLOYED_SET in names
        for kind in description.evidence:
            assert evidence_operation(kind) in names
        for operation in description.operations:
            assert operation.access in get_args(Access)
            assert operation.side_effects in get_args(SideEffectClass)
        by_name = {operation.name: operation for operation in description.operations}
        assert by_name[READ_DEPLOYED_SET].access == "read"
        assert by_name[READ_DEPLOYED_SET].observes, "a deployed-set read observes something"
        assert by_name[WRITE_DEPLOYED_SET].access == "write"
        assert by_name[WRITE_DEPLOYED_SET].observes == []
        assert self.an_environment() in description.environments

    def test_a_read_of_a_named_environment_says_which_it_is(self, tmp_path: Path) -> None:
        read = self.make_connector(tmp_path).read_deployed_set(self.an_environment())

        assert isinstance(read, DeployedSet)
        assert read.environment == self.an_environment()
        assert read.read_at

    def test_two_reads_with_no_edit_between_are_the_same_deployed_set(self, tmp_path: Path) -> None:
        connector = self.make_connector(tmp_path)

        first = connector.read_deployed_set(self.an_environment())
        second = connector.read_deployed_set(self.an_environment())

        assert first.model_dump(exclude={"read_at"}) == second.model_dump(exclude={"read_at"})

    def test_an_unknown_environment_is_refused_naming_the_known_ones(self, tmp_path: Path) -> None:
        connector = self.make_connector(tmp_path)

        with pytest.raises(ConnectorError, match=self.an_environment()):
            connector.read_deployed_set("no-such-environment")

    def test_a_missing_credential_is_refused_naming_its_variable_and_reading_no_other(
        self,
    ) -> None:
        """ADR-0011 §2: fail closed, per environment; never another environment's variable,
        never a default. Building reads nothing; the read resolves its own environment."""
        variable = "AGENTDIAG_CONFORMANCE_TOKEN"
        manifest = self.a_manifest_needing(variable)
        read: list[str] = []

        class Recording(dict[str, str]):
            def get(self, key: str, default: object = None) -> object:  # type: ignore[override]
                read.append(key)
                return super().get(key, default)

            def __getitem__(self, key: str) -> str:
                read.append(key)
                return super().__getitem__(key)

        connector = build_connector(manifest, Recording({"AGENTDIAG_OTHER_TOKEN": "t"}))
        assert connector is not None
        assert read == [], "building a Connector reads no credential"

        with pytest.raises(CredentialMissing, match=rf"\${variable}"):
            connector.read_deployed_set(self.an_environment())
        assert read == [variable]

    def test_no_operation_opens_a_session(self, tmp_path: Path) -> None:
        """The Connector manages; conversing is the Adapter's (ADR-0011 §1): it has no
        session to open and no Turn to deliver."""
        connector = self.make_connector(tmp_path)

        for conversing in ("open", "deliver", "rebind", "close"):
            assert not hasattr(connector, conversing), conversing

    def test_an_undeclared_evidence_store_is_refused(self, tmp_path: Path) -> None:
        connector = self.make_connector(tmp_path)

        with pytest.raises(ConnectorError):
            connector.read_evidence(
                self.an_environment(), self.an_undeclared_evidence_kind(), EvidenceQuery()
            )

    def test_a_write_is_refused_or_receipted_never_silent(self, tmp_path: Path) -> None:
        connector = self.make_connector(tmp_path)
        section, text = self.a_prompt_section_to_write()
        expected = deployed_fingerprint(connector.read_deployed_set(self.an_environment()))

        try:
            receipt = connector.write_deployed_set(
                self.an_environment(),
                {section: text},
                expected_fingerprint=expected,
                change_record=None,
            )
        except WriteRefused:
            assert not self.writes(), "this Connector says it writes, and refused"
            return
        assert self.writes(), "this Connector says it refuses writes, and took one"
        assert isinstance(receipt, WriteReceipt)
        assert receipt.environment == self.an_environment()
        assert receipt.sections == [section]

    def test_a_write_with_a_stale_expected_fingerprint_is_refused_as_moved(
        self, tmp_path: Path
    ) -> None:
        """ADR-0011 §6a: the deployed set moved between the preview and the write, so the
        write is refused and nothing is written."""
        connector = self.make_connector(tmp_path)
        section, text = self.a_prompt_section_to_write()
        before = connector.read_deployed_set(self.an_environment())

        refusal = ExpectedFingerprintMoved if self.writes() else WriteRefused
        with pytest.raises(refusal):
            connector.write_deployed_set(
                self.an_environment(),
                {section: text},
                expected_fingerprint=STALE,
                change_record=None,
            )

        after = connector.read_deployed_set(self.an_environment())
        assert after.model_dump(exclude={"read_at"}) == before.model_dump(exclude={"read_at"})

    def test_a_receipted_write_is_on_the_next_read_and_its_fingerprint_is_that_reads(
        self, tmp_path: Path
    ) -> None:
        if not self.writes():
            pytest.skip("this Connector refuses every write")
        connector = self.make_connector(tmp_path)
        section, text = self.a_prompt_section_to_write()
        expected = deployed_fingerprint(connector.read_deployed_set(self.an_environment()))

        receipt = connector.write_deployed_set(
            self.an_environment(),
            {section: text},
            expected_fingerprint=expected,
            change_record=None,
        )

        after = connector.read_deployed_set(self.an_environment())
        assert receipt.fingerprint_before == expected
        assert receipt.fingerprint_after == deployed_fingerprint(after) != expected
        hashed = deployed_from(after).sections[section].sha256
        if "#" in section:
            assert hashed == sha256(canonical_text(text))
        else:
            name = section.removeprefix("prompt.")
            assert hashed == prompt_sections(name, text)[section].sha256

    def test_a_read_only_section_is_never_written(self, tmp_path: Path) -> None:
        """ADR-0011 §1: the model, the provider and the tier are set on the platform."""
        connector = self.make_connector(tmp_path)
        before = connector.read_deployed_set(self.an_environment())

        for section in ("model", "provider", "tier"):
            with pytest.raises(WriteRefused):
                connector.write_deployed_set(
                    self.an_environment(),
                    {section: "changed"},
                    expected_fingerprint=deployed_fingerprint(before),
                    change_record=None,
                )

        after = connector.read_deployed_set(self.an_environment())
        assert after.model_dump(exclude={"read_at"}) == before.model_dump(exclude={"read_at"})

    def test_nothing_in_the_suite_writes_a_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Connector reads the live side; a file it wrote would be a second copy of it."""
        workdir = tmp_path / "cwd"
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        connector = self.make_connector(tmp_path)
        before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

        connector.describe()
        connector.read_deployed_set(self.an_environment())
        for kind in connector.describe().evidence:
            connector.read_evidence(self.an_environment(), kind, EvidenceQuery())
        with contextlib.suppress(WriteRefused, ExpectedFingerprintMoved):
            connector.write_deployed_set(
                self.an_environment(), {}, expected_fingerprint=STALE, change_record=None
            )

        assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


__all__ = ["ConnectorConformance"]
