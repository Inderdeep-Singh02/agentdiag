"""The Fingerprint: the hashes of a Target's deployed set taken at one moment (D38, ADR-0007).

`fingerprint.json` lives in the Target directory, is committed, and is written by `agentdiag
sync` and by a Run that re-syncs, and by nothing else (phase-6 decision 9). It records, per
section, the sha256 of the canonical text (`agentdiag.sync.sections`) and which source it was
taken from — the local files, the Adapter's probe, or (ticket 23) the Connector's read — and
lists every section the Manifest names that nothing could observe under `not_covered`, with
the reason, because a section silently omitted from coverage would read as one that held.

**Its identity is its sections.** `id` hashes the sorted `(section id, sha256)` pairs and
nothing else, so rebuilding a Fingerprint of an unchanged Target yields the same id at a new
`built_at`, and `compare` and `resynced_from` name a configuration rather than a moment. A
prior Run keeps the Fingerprint it ran under in its own `run.json`, and nothing rewrites
that (ADR-0007 §2).

Offline: it reads and writes JSON.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    computed_field,
    field_validator,
    model_serializer,
)

from agentdiag.types import CoveredBy, SectionKind
from agentdiag.workspace import TargetPaths

FINGERPRINT_SCHEMA_VERSION = 1

FINGERPRINT_SHOWN = 8
"""How many characters of a Fingerprint (or any hash) the terminal shows: `list`'s, the
Registry's, `sync`'s and the Scorecard summary's, spelled once by `short`."""

NONE_SHOWN = "-"
"""A table cell with nothing in it: never blank, so a column cannot be misread as the next."""


def short(hash_: str | None) -> str:
    """A Fingerprint id, or any hash, as the terminal shows it; `-` for none."""
    return hash_[:FINGERPRINT_SHOWN] if hash_ else NONE_SHOWN


class FingerprintError(ValueError):
    """A `fingerprint.json` that exists and cannot be read as one."""


class Section(BaseModel):
    """One section of the deployed set as the Fingerprint recorded it."""

    id: str
    """`prompt.system#rule-3`, `tool.cancel_order`, `model`: decision 10's grammar."""

    kind: SectionKind
    sha256: str
    """Of the section's canonical text (`sections.canonical_text`, `canonical_json`)."""

    covered_by: CoveredBy
    """The source this hash was taken from: the deployed side when it was known, else the
    local file (decision 13)."""

    summary: str | None = None
    """What a human reads it by: the heading title, the tool name, the model id."""


class NotCovered(BaseModel):
    """A section the Manifest names that nothing observed, and why (ADR-0007 §1)."""

    id: str
    kind: SectionKind
    reason: str


class Fingerprint(BaseModel):
    """The whole of `fingerprint.json`, and what `run.json.fingerprint` records."""

    schema_version: int = FINGERPRINT_SCHEMA_VERSION
    built_at: str
    """ISO-8601 in UTC, `run.record.iso_utc`'s spelling."""

    environment: str
    """The Adapter environment observed."""

    manifest_sha256: str
    """Of the Manifest file's bytes when this was built: which Manifest it was built from."""

    sections: dict[str, Section] = Field(default_factory=dict)
    not_covered: list[NotCovered] = Field(default_factory=list)
    resynced_from: str | None = None
    """The previous Fingerprint's `id`, when a Run rebuilt this one on a broken Sync
    (ADR-0008); None when `sync` built it."""

    pushed_from: str | None = None
    """The previous Fingerprint's `id`, when a push rebuilt this one from the Connector's
    read after its write (ADR-0011 §6g, phase-7 decision 13); None otherwise, and then left
    out of the file, so every Fingerprint and `run.json` written before pushes existed
    reads and writes as it did."""

    @model_serializer(mode="wrap")
    def _without_absent_push(self, handler: SerializerFunctionWrapHandler) -> Any:
        dumped = handler(self)
        if isinstance(dumped, dict) and dumped.get("pushed_from", ...) is None:
            dumped.pop("pushed_from")
        return dumped

    @field_validator("sections")
    @classmethod
    def _sorted(cls, sections: dict[str, Section]) -> dict[str, Section]:
        return dict(sorted(sections.items()))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        """sha256 over one `<id>\\t<sha256>` line per section, sorted by id: the identity
        `compare`, the Index and `resynced_from` use."""
        return fingerprint_id({key: section.sha256 for key, section in self.sections.items()})


def fingerprint_id(hashes: Mapping[str, str]) -> str:
    """sha256 over one `<section id>\\t<sha256>` line per section, sorted by id: a
    Fingerprint's identity, and the deployed Fingerprint of a Connector's read (phase-7
    decision 10), spelled once."""
    joined = "\n".join(f"{key}\t{hashes[key]}" for key in sorted(hashes))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def load_fingerprint(target: TargetPaths) -> Fingerprint | None:
    """The Target's Fingerprint, or None when `fingerprint.json` does not exist."""
    path = target.fingerprint
    if not path.is_file():
        return None
    try:
        return Fingerprint.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise FingerprintError(f"{path} is not a Fingerprint: {exc}") from exc


def render_fingerprint(fingerprint: Fingerprint) -> str:
    """The one spelling of `fingerprint.json`: indented, keys sorted, newline-terminated,
    so a committed Fingerprint diffs by the section."""
    return json.dumps(fingerprint.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def write_fingerprint(target: TargetPaths, fingerprint: Fingerprint) -> Path:
    """Write the Target's `fingerprint.json`, replacing the previous one: it is the record
    of the current configuration, and every Run that ran under an earlier one keeps its
    own copy in `run.json`."""
    path = target.fingerprint
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_fingerprint(fingerprint), encoding="utf-8")
    return path


__all__ = [
    "FINGERPRINT_SCHEMA_VERSION",
    "FINGERPRINT_SHOWN",
    "NONE_SHOWN",
    "Fingerprint",
    "FingerprintError",
    "NotCovered",
    "Section",
    "fingerprint_id",
    "load_fingerprint",
    "render_fingerprint",
    "short",
    "write_fingerprint",
]
