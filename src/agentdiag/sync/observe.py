"""Gathering the three sides of Sync: the local files, the deployed set, the record.

**L** is what the Manifest's path pointers hold on disk, read here. **D** is the deployed
set, from whoever can observe it (decision 25): **the Connector first**, when the Manifest
names one and its block names the environment (`connector_read`, which turns its
`DeployedSet` into a `Deployed`); **else the Adapter's probe** (`InProcessAdapter.observe`,
decision 11), which builds the Target exactly as a session would and records the first
request it makes, answered by a canned `end_turn` with no model and no network; else
nothing. **R** is the last Fingerprint, `sync.fingerprint`.

**The probe is D for `observed` pointers only.** A path pointer is the local file's, L,
until a Connector reads its deployed twin: the probe sees the prompt as the Target
assembles it and the tool as its request carries it, which is not what a file holds, so
hashing both sides of one path pointer from the probe would never settle. The Connector
reads the deployed record itself, so with one a path pointer has both L and D, and
`diverged` is reachable. The Connector also covers `flow.<id>` and `tier`, which no probe
sees; a Connector read that fails is the caller's to decide on (`sync` refuses, a Run falls
back to the probe and warns).

What the Manifest names and nothing observed is a `NotCovered` with its reason, never an
omission (ADR-0007 §1): an `observed` pointer with no probe, a path pointer whose file is
missing, a tool whose schema no request carried, the model and provider when no probe ran.

This module is offline at import time: the Adapter, and with it the SDK, is imported only
when `adapter_probe` builds one, so `sync --check`'s module graph and `validate`'s stay
free of the model client until a probe is actually made.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import yaml
from pydantic import BaseModel, Field

from agentdiag.connector.base import Connector, ConnectorError, CredentialMissing, DeployedSet
from agentdiag.connector.environment import scrub_credentials
from agentdiag.connector.plugins import UnknownKind, build_connector
from agentdiag.run.manifest import (
    OBSERVED,
    SYSTEM_PROMPT_POINTER,
    Manifest,
    ManifestError,
    PromptPointer,
)
from agentdiag.sync.fingerprint import Fingerprint, NotCovered, Section, fingerprint_id
from agentdiag.sync.sections import (
    MODEL_SECTION,
    PROVIDER_SECTION,
    TIER_SECTION,
    TOOL_PREFIX,
    Hashed,
    belongs_to,
    data_source_section,
    flow_section,
    model_section,
    prompt_id,
    prompt_sections,
    provider_section,
    tier_section,
    tool_section,
)
from agentdiag.types import CoveredBy, SectionKind
from agentdiag.workspace import TargetPaths

if TYPE_CHECKING:
    from agentdiag.adapter.inprocess import Observation

IN_PROCESS_PROVIDER = "anthropic"
"""The provider every in-process Target's calls reach: its only client is the Anthropic
SDK the Adapter hands it (decision 10)."""


class ObservationFailed(RuntimeError):
    """The probe could not observe the Target: its factory raised, or it made no model call.
    `sync` records every section the probe would have covered `not_covered` with this
    reason, and still hashes the path pointers."""


Probe = Callable[[], "Observation"]
"""An Adapter's probe: one Observation (what the request carried, `adapter.inprocess`), or
`ObservationFailed`."""


class Observes(Protocol):
    """An Adapter with a probe: the in-process one (decision 11). Not on the `Adapter`
    protocol, because observing is what a probe-capable Adapter adds, and the conformance
    suite and every fake Adapter would otherwise have to grow one."""

    environment: str

    def observe(self, *, turn_timeout_s: float = ...) -> Observation: ...


class Deployed(BaseModel):
    """The deployed side as one source saw it: its sections and which source that is.

    `deployed_from` makes one of a Connector's read; `gather` takes it in a `DeployedSide`,
    and its sections win over the probe's where both cover a section (decision 25)."""

    sections: dict[str, Hashed] = Field(default_factory=dict)
    covered_by: CoveredBy


NOT_IN_READ = "not in the Connector's read"
"""Why a section the Manifest names is uncovered when the Connector's read omitted it."""


def deployed_from(read: DeployedSet) -> Deployed:
    """A Connector's `DeployedSet` as the sections it covers: each prompt split on its
    headings, each tool, each Flow with its state, and the model, tier and provider it
    names (decision 25). What the read leaves out is not a section of the deployed set."""
    sections: dict[str, Hashed] = {}
    for name, text in read.prompts.items():
        sections.update(prompt_sections(name, text))
    for name, schema in read.tools.items():
        identifier, hashed = tool_section(name, schema)
        sections[identifier] = hashed
    for flow, definition in read.flows.items():
        identifier, hashed = flow_section(flow, definition)
        sections[identifier] = hashed
    for value, section in (
        (read.model, model_section),
        (read.tier, tier_section),
        (read.provider, provider_section),
    ):
        if value:
            identifier, hashed = section(value)
            sections[identifier] = hashed
    return Deployed(sections=sections, covered_by="connector")


def deployed_fingerprint(read: DeployedSet) -> str:
    """The deployed Fingerprint of a Connector's read alone (phase-7 decision 10): the
    `Fingerprint.id` grammar over the sections `deployed_from` yields. It is what a push
    expects and what a Connector's write compares against (ADR-0011 §6a), so a Connector
    computing it from its own read agrees with agentdiag without a network."""
    sections = deployed_from(read).sections
    return fingerprint_id({key: hashed.sha256 for key, hashed in sections.items()})


def connector_refusal(manifest: Manifest, environment: str) -> str | None:
    """Why no Connector can read `environment` of this Manifest (it names no Connector, or
    its block names no such environment), or None: what `pull` and a push's preview refuse
    on before any read, in one spelling."""
    section = manifest.connector
    if section is None:
        return "the Manifest names no Connector: nothing reads or writes the deployed set"
    if environment not in section.environments:
        names = ", ".join(section.environments) or "none"
        return (
            f"the {section.kind} Connector names no environment {environment!r} (it names {names})"
        )
    return None


def connector_read(
    manifest: Manifest,
    environment: str,
    connector: Connector | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[Deployed | None, str | None]:
    """The Connector's read of `environment` as a `Deployed`, or None and why there is none.

    None with no reason when the Manifest names no Connector; None with a reason when its
    block names no such environment (a Connector need not serve every Adapter environment,
    and the probe then covers). `connector` is one already built; without one it is built
    here (`plugins.build_connector`); the environment's credentials resolve when it is read.
    `ConnectorError` (a `CredentialMissing` among them) and `UnknownKind` propagate: `sync`
    refuses on either, a Run falls back to the probe (decision 25).
    """
    if manifest.connector is None:
        return None, None
    refusal = connector_refusal(manifest, environment)
    if refusal is not None:
        return None, refusal
    built = connector
    if built is None:
        built = build_connector(manifest) if environ is None else build_connector(manifest, environ)
    assert built is not None  # the Manifest names a Connector
    return deployed_from(built.read_deployed_set(environment)), None


def connector_failure(
    exc: Exception,
    manifest: Manifest,
    environment: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    """What a Connector that could not read says, in the one spelling `sync`'s error, a
    Run's warning and `run.json.sync.connector_failed` share, with every credential value of
    the environment replaced by `$<VAR>`: a platform's error that echoes a token must not
    carry it into a file or a terminal (ADR-0011 §2)."""
    if isinstance(exc, UnknownKind):
        text = f"no Connector could be built: {exc}"
    elif isinstance(exc, CredentialMissing):
        text = f"the Connector's credentials for {environment!r} did not resolve: {exc}"
    else:
        text = f"the Connector's read failed: {exc}"
    return scrub_credentials(
        text, manifest, environment, os.environ if environ is None else environ
    )


@dataclass(frozen=True)
class DeployedSide:
    """Where D comes from for one environment (decision 25): the Connector's read, or the
    probe and why there is no read. `connector_failed` is set when the Manifest names a
    Connector that could not read: `sync` refuses on it, a Run proceeds on the probe and
    withholds what only the Connector covered (`withhold_connector_sections`)."""

    deployed: Deployed | None = None
    no_connector: str | None = None
    probe: Probe | None = None
    no_probe: str | None = None
    connector_failed: str | None = None


def deployed_side(
    manifest: Manifest,
    environment: str,
    *,
    adapter: Observes | None = None,
    environ: Mapping[str, str] | None = None,
) -> DeployedSide:
    """The Connector's read of `environment` when there is one, else the Adapter's probe;
    a Connector that fails (`ConnectorError`, `UnknownKind`) leaves the probe standing in and
    says why in `connector_failed`, the one place that text is made."""
    try:
        deployed, no_connector = connector_read(manifest, environment, environ=environ)
    except (ConnectorError, UnknownKind) as exc:
        failed = connector_failure(exc, manifest, environment, environ)
        return probe_side(
            manifest, environment, no_connector=failed, connector_failed=failed, adapter=adapter
        )
    if deployed is not None:
        return DeployedSide(deployed=deployed)
    return probe_side(manifest, environment, no_connector=no_connector, adapter=adapter)


def probe_side(
    manifest: Manifest,
    environment: str,
    *,
    no_connector: str | None = None,
    connector_failed: str | None = None,
    adapter: Observes | None = None,
) -> DeployedSide:
    """The Adapter's probe of `environment`, or why there is none: an environment the
    Adapter block does not name (a Connector-only one) has no probe."""
    if environment not in manifest.adapter.environment_names:
        return DeployedSide(
            no_connector=no_connector,
            no_probe=f"adapter_cannot_observe: the Adapter names no environment {environment!r}",
            connector_failed=connector_failed,
        )
    probe, no_probe = adapter_probe(manifest, environment, adapter)
    return DeployedSide(
        no_connector=no_connector,
        probe=probe,
        no_probe=no_probe,
        connector_failed=connector_failed,
    )


def withhold_connector_sections(
    gathered: Gathered, recorded: Fingerprint | None, reason: str
) -> Gathered:
    """After a failed Connector read, every section the record took from the Connector and
    the probe did not observe now (a Flow, the tier, the deployed twin of a path pointer) is
    `not_covered` with `reason`: a failed read is not evidence that the section moved or went
    away, so it must never read as `removed`, `deployed_ahead` or `local_ahead`."""
    if recorded is None:
        return gathered
    withheld = [
        section
        for identifier, section in recorded.sections.items()
        if section.covered_by == "connector" and identifier not in gathered.deployed
    ]
    if not withheld:
        return gathered
    ids = {section.id for section in withheld}
    return gathered.model_copy(
        update={
            "local": {key: value for key, value in gathered.local.items() if key not in ids},
            "not_covered": [
                *(entry for entry in gathered.not_covered if entry.id not in ids),
                *(NotCovered(id=s.id, kind=s.kind, reason=reason) for s in withheld),
            ],
        }
    )


class Gathered(BaseModel):
    """L and D for one Target and one environment, and what neither covered."""

    environment: str
    manifest_sha256: str
    local: dict[str, Hashed] = Field(default_factory=dict)
    deployed: dict[str, Hashed] = Field(default_factory=dict)
    deployed_by: dict[str, CoveredBy] = Field(default_factory=dict)
    not_covered: list[NotCovered] = Field(default_factory=list)


def manifest_sha256(target: TargetPaths) -> str:
    """Of the Manifest file's bytes: which Manifest a Fingerprint was built from."""
    return hashlib.sha256(target.manifest.read_bytes()).hexdigest()


def adapter_probe(
    manifest: Manifest, environment: str, adapter: Observes | None = None
) -> tuple[Probe | None, str | None]:
    """The Adapter's probe for `environment`, or None and why there is none.

    Only the in-process Adapter observes (decision 18); any other kind is
    `adapter_cannot_observe` for every `observed` section, and the path pointers are still
    hashed. `adapter` is one already built (a Run's); without one it is built here, which
    imports it and the SDK with it, and nothing else in this module does. A `live`
    environment's refusal becomes the probe's failure, so `sync` still hashes what it can.
    """
    if manifest.adapter.kind != "inprocess":
        return None, (
            f"adapter_cannot_observe: the {manifest.adapter.kind} Adapter observes nothing of "
            "the deployed set"
        )
    from agentdiag.adapter import InProcessAdapter, LiveSideEffectsRefused

    built: Observes = adapter or InProcessAdapter(
        manifest.adapter.as_adapter_config(),
        environment=environment,
        tool_kinds=manifest.tool_kinds,
    )
    # The environment's Turn timeout bounds the probe too (decision 56): a Target that
    # blocks before its first model call must not hang `sync` or a Run's preflight.
    timeout_s = manifest.adapter.turn_timeout_s(environment)

    def probe() -> Observation:
        try:
            return built.observe(turn_timeout_s=timeout_s)
        except LiveSideEffectsRefused as refused:
            raise ObservationFailed(str(refused)) from refused

    return probe, None


def gather(
    target: TargetPaths,
    manifest: Manifest,
    environment: str,
    *,
    side: DeployedSide,
) -> Gathered:
    """Every section the Manifest names, from each side that knows it.

    `side.probe` is the Adapter's; `side.no_probe` says why there is none, and becomes the
    reason of every section only a probe could have covered. `side.deployed` is the
    Connector's read, which wins where it covers; `side.no_connector` says why a Manifest
    that names a Connector has no read (its block names no such environment, or the read
    failed), the reason the Connector-only `tier` is `not_covered`.
    """
    probe, no_probe = side.probe, side.no_probe
    deployed, no_connector = side.deployed, side.no_connector
    gathered = Gathered(environment=environment, manifest_sha256=manifest_sha256(target))
    observation: Observation | None = None
    unobserved = no_probe or (
        NOT_IN_READ
        if deployed is not None and deployed.covered_by == "connector"
        else "no Adapter observation and no Connector"
    )
    if probe is not None:
        try:
            observation = probe()
        except ObservationFailed as failed:
            unobserved = f"the Adapter's probe failed: {failed}"
    covered = deployed.sections if deployed is not None else {}

    def seen(identifier: str, hashed: Hashed, source: CoveredBy) -> None:
        gathered.deployed[identifier] = hashed
        gathered.deployed_by[identifier] = source

    def known(identifier: str) -> bool:
        return any(belongs_to(key, identifier) for key in (*gathered.local, *gathered.deployed))

    def missing(identifier: str, kind: SectionKind, reason: str) -> None:
        gathered.not_covered.append(NotCovered(id=identifier, kind=kind, reason=reason))

    for name, pointer in manifest.prompts.items():
        identifier = prompt_id(name)
        if isinstance(pointer, PromptPointer):
            text = _read(target.relative(pointer.path))
            if text is not None:
                gathered.local.update(prompt_sections(name, text))
        read = {key: value for key, value in covered.items() if belongs_to(key, identifier)}
        if read and deployed is not None:
            for key, value in read.items():
                seen(key, value, deployed.covered_by)
        elif (
            pointer == OBSERVED
            and name == SYSTEM_PROMPT_POINTER
            and observation is not None
            and observation.system_prompt is not None
        ):
            for key, value in prompt_sections(name, observation.system_prompt).items():
                seen(key, value, "adapter")
        if not known(identifier):
            missing(identifier, "prompt", _prompt_reason(name, pointer, observation, unobserved))

    for name, entry in manifest.tools.items():
        identifier = f"{TOOL_PREFIX}{name}"
        if isinstance(entry.schema_, PromptPointer):
            schema = _read_schema(target.relative(entry.schema_.path))
            if schema is not None:
                gathered.local[identifier] = tool_section(name, schema)[1]
        if identifier in covered and deployed is not None:
            seen(identifier, covered[identifier], deployed.covered_by)
        elif (
            not isinstance(entry.schema_, PromptPointer)
            and observation is not None
            and name in observation.tool_schemas
        ):
            seen(identifier, tool_section(name, observation.tool_schemas[name])[1], "adapter")
        if known(identifier):
            continue
        if isinstance(entry.schema_, PromptPointer):
            missing(identifier, "tool", f"the local file {entry.schema_.path} is missing")
        elif observation is not None:
            missing(identifier, "tool", f"the Adapter's probe request carried no schema for {name}")
        else:
            missing(identifier, "tool", f"{unobserved} for the schema of tool {name}")

    for name, source in manifest.data_sources.items():
        identifier, hashed = data_source_section(name, source.identity)
        gathered.local[identifier] = hashed

    probed: tuple[tuple[str, SectionKind, Hashed | None], ...] = (
        (
            MODEL_SECTION,
            "model",
            model_section(observation.model)[1]
            if observation is not None and observation.model
            else None,
        ),
        (
            PROVIDER_SECTION,
            "provider",
            provider_section(IN_PROCESS_PROVIDER)[1] if observation is not None else None,
        ),
    )
    for identifier, kind, observed in probed:
        if identifier in covered and deployed is not None:
            seen(identifier, covered[identifier], deployed.covered_by)
        elif observed is not None:
            seen(identifier, observed, "adapter")
        else:
            missing(identifier, kind, unobserved)

    if deployed is not None:
        for identifier, hashed in covered.items():
            if identifier not in gathered.deployed:
                seen(identifier, hashed, deployed.covered_by)
    elif manifest.connector is not None:
        missing(TIER_SECTION, "tier", f"connector_only: {no_connector or 'no Connector read'}")
    return gathered


def _prompt_reason(
    name: str, pointer: PromptPointer | str, observation: Observation | None, unobserved: str
) -> str:
    """Why nothing covered prompt `name`: the one sentence its `not_covered` entry carries."""
    if isinstance(pointer, PromptPointer):
        return f"the local file {pointer.path} is missing"
    if observation is None:
        return f"{unobserved} for observed pointer prompts.{name}"
    if name == SYSTEM_PROMPT_POINTER:
        return "the Adapter's probe request carried no system prompt"
    return (
        f"the Adapter's probe observes only the `{SYSTEM_PROMPT_POINTER}` prompt, and no "
        f"Connector reads prompts.{name}"
    )


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _read_schema(path: Path) -> Any:
    """A tool schema file parsed (JSON is YAML), so a whitespace edit is not a change."""
    text = _read(path)
    if text is None:
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path} is not a JSON or YAML tool schema: {exc}") from exc


def build_fingerprint(
    gathered: Gathered, *, built_at: str, resynced_from: str | None = None
) -> Fingerprint:
    """The Fingerprint of what was gathered: D where it is known, else L (decision 13),
    `covered_by` saying which, and every uncovered section with its reason."""
    sections: dict[str, Section] = {}
    for identifier in sorted(set(gathered.local) | set(gathered.deployed)):
        hashed = gathered.deployed.get(identifier)
        source: CoveredBy = gathered.deployed_by.get(identifier, "adapter")
        if hashed is None:
            hashed, source = gathered.local[identifier], "local"
        sections[identifier] = Section(
            id=identifier,
            kind=hashed.kind,
            sha256=hashed.sha256,
            covered_by=source,
            summary=hashed.summary,
        )
    return Fingerprint(
        built_at=built_at,
        environment=gathered.environment,
        manifest_sha256=gathered.manifest_sha256,
        sections=sections,
        not_covered=list(gathered.not_covered),
        resynced_from=resynced_from,
    )


__all__ = [
    "IN_PROCESS_PROVIDER",
    "NOT_IN_READ",
    "Deployed",
    "DeployedSide",
    "Gathered",
    "ObservationFailed",
    "Observes",
    "Probe",
    "adapter_probe",
    "build_fingerprint",
    "connector_failure",
    "connector_read",
    "connector_refusal",
    "deployed_fingerprint",
    "deployed_from",
    "deployed_side",
    "gather",
    "manifest_sha256",
    "probe_side",
    "withhold_connector_sections",
]
