"""The Manifest: the Target's identity document, read from its Target directory (D37).

Pointers, not copies (ADR-0001). One schema version covers every block, and
every key but `target` and `adapter` is optional, so a Manifest written for Phase 4 still
loads unchanged; unknown keys are carried rather than refused.

- **Who and how**: `target`, `family` and `channel` (ADR-0013 §3), and `adapter` with its
  environments, each with a `side_effects` class, `protected`, and `turn_timeout`
  (ADR-0011 §2, §6d). `connector` names the management side (ticket 23 reads it).
- **The deployed set, by pointer** (phase-6 decision 8): `prompts` (a path, or `observed`
  for what the Adapter or the Connector observes; the pointer named `system` is the system
  prompt, note 7), `tools` (each with its `kind`, a schema pointer and a side-effect
  class), `data_sources` (an identity string). `agentdiag.sync` fingerprints them.
- **What agentdiag reads for its own work**: `suites` (a path, or a path with a `status`),
  `records` (the Change records directory), `judge_notes` (ADR-0003 §8),
  `forbidden_phrases` (phase-5 decision 6), `eval_parameters` (the latency defaults and the
  tool argument types), `suppressions` (ADR-0003 §8, decision 16) and `redaction` (the names
  a Change record never carries, phase-7 decision 6).

Where the file is belongs to `agentdiag.workspace` (ADR-0013): `load_manifest` takes the
`TargetPaths` a Workspace resolved, never a bare root. What is wrong with a Manifest beyond
its shape — a pointer that is absolute or leaves the Workspace root (ADR-0015 §2,
`pointer_problems`), a pointer to a missing file, a Suppression whose window ends before it
starts — is `manifest_report`'s, which `validate` prints before it checks any Suite.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SerializerFunctionWrapHandler,
    ValidationError,
    field_validator,
    model_serializer,
)

from agentdiag.eval.latency import MANIFEST_LATENCY_KEYS
from agentdiag.eval.registry import REGISTRY
from agentdiag.eval.suppressions import EVERY_EVAL, Suppression
from agentdiag.types import (
    PROTECTED_BY_DEFAULT,
    SIDE_EFFECT_ORDER,
    JsonType,
    SideEffectClass,
    SuiteStatus,
    ToolKind,
)
from agentdiag.workspace import CHANGES_DIRNAME, TargetPaths

DEFAULT_TURN_TIMEOUT_S = 600.0
"""How long one Turn may take when the environment writes no `turn_timeout` (phase-5
decision 56): a default for 2 to 8 minute Turns, which a 120 s timeout turns into silent
timeouts."""


DEFAULT_ENVIRONMENT_KEY = "default"
"""The key of `adapter.environments` that names an environment rather than being one."""

OBSERVED: Literal["observed"] = "observed"
"""The pointer that says "the Adapter or the Connector observes this": a prompt or a tool
schema with no local file (D37)."""

ObservedPointer = Literal["observed"]

SYSTEM_PROMPT_POINTER = "system"
"""The `prompts` pointer that is the Target's system prompt (note 7): the one the Adapter's
probe observes, and the one the Judge falls back to when a Trace holds none."""


class ManifestNotFound(FileNotFoundError):
    """No Manifest under the root. A preflight error, not a crash."""


class ManifestError(ValueError):
    """A Manifest that exists and cannot be read as one."""


class TargetSection(BaseModel):
    """Who the Target is. A name a Report can print and a line saying what it does."""

    model_config = ConfigDict(extra="allow")

    name: str
    description: str | None = None


class AdapterSection(BaseModel):
    """How to drive the Target: the Adapter kind, its side effects, its environments.

    `environments` is open because each Adapter kind reads its own block; `default` names
    the one a Run uses when nothing else does. The side-effect class is declared here and
    nowhere else, so refusing a live Run is a decision about the Manifest, not about code;
    an environment may raise it for itself (`side_effects`) and never lower it.
    """

    model_config = ConfigDict(extra="allow")

    kind: str
    side_effects: str = "none"
    """The Adapter-level class. Read as text and checked by the Adapter, whose refusal names
    the classes there are (ADR-0001 point 5)."""

    environments: dict[str, Any] = Field(default_factory=dict)

    @property
    def default_environment(self) -> str:
        """The environment a Run opens unless told otherwise."""
        default = self.environments.get(DEFAULT_ENVIRONMENT_KEY)
        if not isinstance(default, str):
            raise ManifestError(
                "The Manifest's adapter.environments needs a 'default' naming one environment"
            )
        return default

    @property
    def environment_names(self) -> list[str]:
        """Every environment the block declares, in the order written."""
        return [name for name in self.environments if name != DEFAULT_ENVIRONMENT_KEY]

    def block(self, environment: str) -> dict[str, Any]:
        """One environment's block as written; empty when it names none."""
        block = self.environments.get(environment)
        return dict(block) if isinstance(block, dict) else {}

    def turn_timeout_s(self, environment: str) -> float:
        """How long one Turn in `environment` may take, in seconds (decision 56): its block's
        `turn_timeout`, a positive number, or `DEFAULT_TURN_TIMEOUT_S` when it writes none.
        Per environment, because a Target's staging may be slower than its local copy."""
        value = self.block(environment).get("turn_timeout")
        if value is None:
            return DEFAULT_TURN_TIMEOUT_S
        if isinstance(value, bool) or not isinstance(value, int | float) or not value > 0:
            raise ManifestError(
                f"The Manifest's adapter.environments.{environment}.turn_timeout is "
                f"{value!r}; it is a positive number of seconds"
            )
        return float(value)

    def as_adapter_config(self) -> dict[str, Any]:
        """The block the Adapter constructor takes (`InProcessAdapter(config, ...)`)."""
        return self.model_dump(mode="json")


class PromptPointer(BaseModel):
    """A pointer to a local file: a prompt's text, or a tool's schema (D37)."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    """Relative to the Target directory."""

    local_only: bool = False
    """True for a file that lives in one checkout and not in another (a secret-bearing
    variant, a draft): absent, it is a `validate` warning rather than an error."""


Pointer = PromptPointer | ObservedPointer
"""What a `prompts` entry or a tool's `schema` is: a file, or `observed`."""


def _pointer(value: Any) -> Any:
    """A bare string that is not `observed` is a path; everything else is as written."""
    if isinstance(value, str) and value != OBSERVED:
        return {"path": value}
    return value


class ToolEntry(BaseModel):
    """One tool the Target has, as the Manifest points at it (D37)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True, serialize_by_alias=True)

    kind: ToolKind = "action"
    """`retrieval` for a lookup, `action` for a tool that changes something (ADR-0006 §2).
    The in-process Adapter records a `retrieval` tool's calls as `retrieval` Spans."""

    schema_: Pointer | None = Field(default=None, alias="schema")
    """The tool's schema: a file, `observed` (what the Target's requests carry), or absent,
    which the in-process Adapter's probe reads as `observed`."""

    side_effects: SideEffectClass = "none"
    """What calling this tool does to the world (ADR-0011 §6d)."""

    @field_validator("schema_", mode="before")
    @classmethod
    def _bare_path(cls, value: Any) -> Any:
        return _pointer(value)


class DataSource(BaseModel):
    """One data source by its identity (D37): what a change of database or index is."""

    model_config = ConfigDict(extra="allow")

    identity: str = Field(min_length=1)
    kind: str | None = None


class SuiteEntry(BaseModel):
    """One Suite the Manifest names (phase-6 decision 8): its path and its status."""

    model_config = ConfigDict(extra="forbid")

    path: str
    """Relative to the Target directory, as the Manifest names it."""

    status: SuiteStatus = "runnable"
    """`runnable` Suites run; a `draft` one is validated and its Scenarios named `not_run`;
    a `retired` one is neither loaded nor validated."""


class ConnectorSection(BaseModel):
    """The management side (ADR-0011): which Connector, its environments' identifiers, and
    the Evidence stores it reads. Open, because each kind reads its own keys (ticket 23)."""

    model_config = ConfigDict(extra="allow")

    kind: str
    environments: dict[str, dict[str, Any]] = Field(default_factory=dict)
    evidence: dict[str, dict[str, Any]] = Field(default_factory=dict)


class LatencyDefaults(BaseModel):
    """`eval_parameters.latency`: the default `max_ms` of each latency Eval a declaration
    leaves without a threshold (decision 17). Named for what it is to a declaration."""

    model_config = ConfigDict(extra="forbid")

    response_max_ms: float | None = Field(default=None, ge=0)
    first_token_max_ms: float | None = Field(default=None, ge=0)
    tool_max_ms: float | None = Field(default=None, ge=0)

    def thresholds(self) -> dict[str, dict[str, Any]]:
        """`{eval name: {max_ms: n}}` for every default written, by the Eval's own name."""
        written = self.model_dump()
        return {
            eval_name: {"max_ms": _number(written[key])}
            for eval_name, key in MANIFEST_LATENCY_KEYS.items()
            if written[key] is not None
        }


def protected_by_name(environment: str) -> bool:
    """Whether `environment`'s name is one `init` protects by default, in any case
    (ADR-0011 §6d): `Prod` and `STAGING` reach real users as surely as `prod` does."""
    return environment.lower() in PROTECTED_BY_DEFAULT


def higher_side_effects(adapter_level: Any, environment_level: Any) -> SideEffectClass:
    """The higher of two declared side-effect classes; the environment's may be absent.
    A value outside the closed set is a `ManifestError` naming the classes there are."""
    classes: list[SideEffectClass] = []
    for declared in (adapter_level, environment_level):
        if declared is None:
            continue
        if declared not in SIDE_EFFECT_ORDER:
            raise ManifestError(
                f"The Manifest declares side_effects {declared!r}; it must be one of "
                f"{', '.join(SIDE_EFFECT_ORDER)}"
            )
        classes.append(cast(SideEffectClass, declared))
    return max(classes or ["none"], key=SIDE_EFFECT_ORDER.index)


def _number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


class EvalParameters(BaseModel):
    """Per-Target data an Eval reads from the Manifest (CONTEXT.md **Eval parameters**).
    `forbidden_phrases` predates the block and stays top-level: a second home would be
    duplication."""

    model_config = ConfigDict(extra="forbid")

    latency: LatencyDefaults | None = None
    tool_argument_types: dict[str, dict[str, JsonType]] = Field(default_factory=dict)
    """`{tool: {argument: JSON type}}`: when present, every Scenario is screened by the
    `tool_argument_types` Eval (decision 17)."""


class Redaction(BaseModel):
    """What never reaches a committed Change record beyond e-mail addresses and phone
    numbers (phase-7 decision 6): the names `agentdiag.change.redact` replaces, its one
    reader."""

    model_config = ConfigDict(extra="forbid")

    names: list[str] = Field(default_factory=list)


class Manifest(BaseModel):
    """The Target's identity document, as `run.json` snapshots it (ADR-0005 §3)."""

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    target: TargetSection
    family: str | None = None
    """The persona this Target is one channel of (ADR-0013 §3): a chat build and a voice
    build of one deployed agent are two Targets of one Family."""

    channel: str | None = None
    """How this Target meets its users (`chat`, `voice`, ...); free text."""

    adapter: AdapterSection
    connector: ConnectorSection | None = None
    """The management side; absent, the Target has no Connector."""

    prompts: dict[str, Pointer] = Field(default_factory=dict)
    """Name to pointer; `system` is the system prompt (note 7)."""

    tools: dict[str, ToolEntry] = Field(default_factory=dict)
    """The Target's tools by name; a tool not listed here is an `action`."""

    data_sources: dict[str, DataSource] = Field(default_factory=dict)
    """Name to identity; a bare string is the identity."""

    suites: list[SuiteEntry] = Field(default_factory=list)
    """The Suites, relative to the Target directory; a bare path is `runnable`."""

    records: str = CHANGES_DIRNAME
    """The Change records directory, relative to the Target directory (ADR-0012)."""

    forbidden_phrases: list[str] | None = None
    """Phrases the Target must never say, screened in every Scenario (decision 6). An empty
    list and an absent key differ: the first scores `pass` saying none are declared, the
    second adds no Score at all."""

    judge_notes: str | None = None
    """Path to the Judge's calibration notes, relative to the Target directory (ADR-0003
    §8): read verbatim into every judged prompt, budgeted, and part of every judged Score's
    Judge Fingerprint. A path named and missing refuses the Run."""

    eval_parameters: EvalParameters | None = None
    suppressions: list[Suppression] = Field(default_factory=list)
    _directory: Path | None = PrivateAttr(default=None)

    @property
    def directory(self) -> Path | None:
        """The Target directory the Manifest was loaded from, which its relative paths are
        relative to; None for a Manifest built in memory. Never dumped."""
        return self._directory

    def loaded_from(self, directory: Path) -> Manifest:
        """This Manifest, marked as read from the Target directory `directory`."""
        self._directory = directory
        return self

    redaction: Redaction | None = None
    """The names a Change record never carries (decision 6); left out of `run.json`'s
    snapshot when absent, so a Run of a Manifest without it records what it did before."""

    @model_serializer(mode="wrap")
    def _without_absent_redaction(self, handler: SerializerFunctionWrapHandler) -> Any:
        dumped = handler(self)
        if isinstance(dumped, dict) and dumped.get("redaction", ...) is None:
            dumped.pop("redaction")
        return dumped

    @field_validator("prompts", mode="before")
    @classmethod
    def _prompt_paths(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {name: _pointer(pointer) for name, pointer in value.items()}
        return value

    @field_validator("data_sources", mode="before")
    @classmethod
    def _identities(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                name: {"identity": source} if isinstance(source, str) else source
                for name, source in value.items()
            }
        return value

    @field_validator("suites", mode="before")
    @classmethod
    def _suite_paths(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [{"path": entry} if isinstance(entry, str) else entry for entry in value]
        return value

    @property
    def runnable_suites(self) -> list[SuiteEntry]:
        """The Suites a Run selects from."""
        return [entry for entry in self.suites if entry.status == "runnable"]

    @property
    def draft_suites(self) -> list[SuiteEntry]:
        """The Suites validated and named `not_run`, never run (decision 8)."""
        return [entry for entry in self.suites if entry.status == "draft"]

    @property
    def read_suites(self) -> list[SuiteEntry]:
        """Every Suite that is loaded and validated: the runnable and the draft ones, in the
        Manifest's order. A `retired` one is neither, and this is the one place that says so."""
        return [entry for entry in self.suites if entry.status != "retired"]

    @property
    def retired_suites(self) -> list[SuiteEntry]:
        """The Suites neither loaded nor validated, which `validate` names as skipped."""
        return [entry for entry in self.suites if entry.status == "retired"]

    @property
    def suite_paths(self) -> list[str]:
        """The runnable Suites' paths: what a Run selects from."""
        return [entry.path for entry in self.runnable_suites]

    @property
    def connector_kind(self) -> str | None:
        """The `connector.kind` the Manifest names, None when it names no Connector."""
        return self.connector.kind if self.connector is not None else None

    def is_protected(self, environment: str) -> bool:
        """Whether `environment` is protected: always when its name is one of
        `PROTECTED_BY_DEFAULT` (`prod`, `staging`, `eu_prod`, in any case), which no
        `protected: false` unprotects (`validate` names that as an error); else its block's
        `protected` when written."""
        if protected_by_name(environment):
            return True
        written = self.adapter.block(environment).get("protected")
        return written is True

    def side_effects_of(self, environment: str) -> SideEffectClass:
        """The higher of the Adapter-level class and the environment's own (`live` >
        `sandboxed` > `none`): an environment can say it does more, never less."""
        return higher_side_effects(
            self.adapter.side_effects, self.adapter.block(environment).get("side_effects")
        )

    @property
    def tool_kinds(self) -> dict[str, ToolKind]:
        """`{name: kind}`, the plain data the Adapter and the Evals take."""
        return {name: entry.kind for name, entry in self.tools.items()}

    @property
    def latency_thresholds(self) -> dict[str, dict[str, Any]]:
        """The default threshold of each latency Eval, by name; empty when none is written."""
        parameters = self.eval_parameters
        if parameters is None or parameters.latency is None:
            return {}
        return parameters.latency.thresholds()

    @property
    def tool_argument_types(self) -> dict[str, dict[str, JsonType]] | None:
        """The declared argument types, None when the Manifest writes none (no Score)."""
        parameters = self.eval_parameters
        if parameters is None or not parameters.tool_argument_types:
            return None
        return parameters.tool_argument_types


class ManifestInvalid(ManifestError):
    """A Manifest whose shape its model refuses; `problems` names each, where it is."""

    def __init__(self, path: Path, problems: list[tuple[str, str]]) -> None:
        self.problems = problems
        super().__init__(
            f"{path}: " + "; ".join(f"{where}: {message}" for where, message in problems)
        )


def load_manifest(target: TargetPaths, path: Path | None = None) -> Manifest:
    """Read the Target's Manifest, or say which file was missing. `path` reads another file
    as the Target's Manifest — a draft `discover` wrote (decision 27) — its pointers still
    relative to the Target directory."""
    path = target.manifest if path is None else path
    if not path.is_file():
        raise ManifestNotFound(f"No Manifest at {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{path} does not hold a Manifest mapping")
    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestInvalid(path, _problems(exc)) from exc
    return manifest.loaded_from(target.directory)


def manifest_if_it_loads(target: TargetPaths) -> Manifest | None:
    """The Target's Manifest, or None when there is none or it does not load: for a reader
    with a fallback (the Change records' directory and redaction list), never for one that
    must say why; `manifest_report` says why."""
    try:
        return load_manifest(target)
    except (ManifestError, FileNotFoundError):
        return None


def _problems(exc: ValidationError) -> list[tuple[str, str]]:
    """Each refusal as (where, what), the value quoted when there is one to quote."""
    problems: list[tuple[str, str]] = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error["loc"]) or "the Manifest"
        given = error.get("input")
        seen = f" (it is {given!r})" if isinstance(given, str | int | float) else ""
        problems.append((where, f"{error['msg']}{seen}"))
    return problems


# --- what `validate` checks beyond the shape (decision 8) ---


class ManifestProblem(BaseModel):
    """One thing wrong with a Manifest, where it is."""

    path: str
    message: str


class ManifestReport(BaseModel):
    """Everything `validate` found in the Manifest before it read any Suite."""

    file: Path
    errors: list[ManifestProblem] = Field(default_factory=list)
    warnings: list[ManifestProblem] = Field(default_factory=list)
    refused: list[str] = Field(default_factory=list)
    """Where each pointer that is absolute or leaves the Workspace root sits (`suites[0]`):
    `validate` reads no Suite named here."""

    @property
    def ok(self) -> bool:
        return not self.errors

    def lines(self) -> list[str]:
        """`error: <file>: <path>: <message>`, then the warnings, as a Suite's report reads."""
        return [
            f"{severity}: {self.file}: "
            + (f"{problem.path}: " if problem.path else "")
            + problem.message
            for severity, problems in (("error", self.errors), ("warning", self.warnings))
            for problem in problems
        ]


_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def _is_absolute(pointer: str) -> bool:
    """Absolute on any machine: a leading separator once `\\` is `/` (a POSIX root, a UNC
    share), or a drive letter and a separator. `v:2.md` is a relative name."""
    return pointer.replace("\\", "/").startswith("/") or bool(_DRIVE.match(pointer))


class _Pointed(NamedTuple):
    """One path the Manifest names, and whether `validate` checks it exists here."""

    where: str
    what: str
    path: str
    exists_checked: bool
    local_only: bool = False


def _pointers(manifest: Manifest) -> list[_Pointed]:
    """Every path the Manifest names; `observed` names none. A retired Suite's path is a
    path all the same, though its file is never checked. `records` and `judge_notes` get
    only the where-it-points rule. A key that becomes a path (`redaction` as a file, say)
    is one more entry here."""
    pointers: list[_Pointed] = []
    for name, prompt in manifest.prompts.items():
        if isinstance(prompt, PromptPointer):
            pointers.append(
                _Pointed(f"prompts.{name}", "the prompt file", prompt.path, True, prompt.local_only)
            )
    for name, entry in manifest.tools.items():
        schema = entry.schema_
        if isinstance(schema, PromptPointer):
            pointers.append(
                _Pointed(
                    f"tools.{name}.schema",
                    "the tool schema file",
                    schema.path,
                    True,
                    schema.local_only,
                )
            )
    read = manifest.read_suites
    for index, suite in enumerate(manifest.suites):
        pointers.append(_Pointed(f"suites[{index}]", "the Suite", suite.path, suite in read))
    pointers.append(_Pointed("records", "the Change records directory", manifest.records, False))
    if manifest.judge_notes is not None:
        pointers.append(_Pointed("judge_notes", "the Judge notes", manifest.judge_notes, False))
    return pointers


def _pointer_problem(target: TargetPaths, what: str, path: str) -> str | None:
    """Why `path` may not be a Manifest pointer, or None. Lexical, so the answer is the same
    on every machine: no symlink is followed and no file is read."""
    directory = target.directory.relative_to(target.root).as_posix()
    if _is_absolute(path):
        return (
            f"{what} {path} is absolute; a Manifest pointer is relative to the Target "
            f"directory {directory}"
        )
    forward = path.replace("\\", "/")
    normalised = posixpath.normpath(f"{directory}/{forward}")
    if normalised == ".." or normalised.startswith("../"):
        return (
            f"{what} {path} leaves the Workspace root (it normalises to {normalised}); move "
            "the file under the root and point at it relative to the Target directory"
        )
    return None


def pointer_problems(target: TargetPaths, manifest: Manifest) -> list[tuple[str, str]]:
    """Every pointer that is absolute or leaves the Workspace root, as (where, message)
    (ADR-0015 §2): a committed Manifest never depends on one machine's layout. Another
    Target's directory under the same root is inside it. An error whether or not the file
    exists here, `local_only` included: the rule is where it points."""
    problems: list[tuple[str, str]] = []
    for pointed in _pointers(manifest):
        problem = _pointer_problem(target, pointed.what, pointed.path)
        if problem is not None:
            problems.append((pointed.where, problem))
    return problems


def manifest_report(
    target: TargetPaths, path: Path | None = None
) -> tuple[ManifestReport, Manifest | None]:
    """The Manifest checked: its shape, then every pointer and every Suppression.

    A pointer that is absolute or leaves the Workspace root is an error
    (`pointer_problems`), and the existence check skips it; a missing pointer is an error,
    unless it is `local_only`, when it is a warning; an unknown Suite status is an error
    (the shape refuses it, and this names the entry); a Suppression whose `until` precedes
    its `from` is an error, and one naming a mechanical Eval a warning, since mechanical
    Evals ignore Suppressions in v1 (decision 16). `path` checks another file as the
    Target's Manifest (`validate --manifest`).
    """
    report = ManifestReport(file=target.manifest if path is None else path)
    try:
        manifest = load_manifest(target, path)
    except ManifestInvalid as invalid:
        report.errors.extend(
            ManifestProblem(path=where, message=message) for where, message in invalid.problems
        )
        return report, None
    except (ManifestNotFound, ManifestError) as exc:
        report.errors.append(ManifestProblem(path="", message=str(exc)))
        return report, None

    from agentdiag.run.manifest_checks import manifest_problems  # it imports this module

    report.errors.extend(
        ManifestProblem(path=where, message=message)
        for where, message in manifest_problems(manifest)
    )
    for pointed in _pointers(manifest):
        problem = _pointer_problem(target, pointed.what, pointed.path)
        if problem is not None:
            report.errors.append(ManifestProblem(path=pointed.where, message=problem))
            report.refused.append(pointed.where)
        elif pointed.exists_checked and not target.relative(pointed.path).is_file():
            missing = ManifestProblem(
                path=pointed.where,
                message=f"{pointed.what} {pointed.path} does not exist under {target.directory}",
            )
            (report.warnings if pointed.local_only else report.errors).append(missing)
    for index, suppression in enumerate(manifest.suppressions):
        where = f"suppressions[{index}]"
        if suppression.until < suppression.from_:
            report.errors.append(
                ManifestProblem(
                    path=where,
                    message=(
                        f"Suppression {suppression.id!r} ends on {suppression.until} before it "
                        f"starts on {suppression.from_}"
                    ),
                )
            )
        spec = REGISTRY.get(suppression.eval)
        if suppression.eval != EVERY_EVAL and spec is not None and spec.kind == "mechanical":
            report.warnings.append(
                ManifestProblem(
                    path=where,
                    message=(
                        f"Suppression {suppression.id!r} names the mechanical Eval "
                        f"{suppression.eval!r}; mechanical Evals ignore Suppressions, which "
                        "reach only the Judge"
                    ),
                )
            )
    return report, manifest


__all__ = [
    "DEFAULT_ENVIRONMENT_KEY",
    "DEFAULT_TURN_TIMEOUT_S",
    "OBSERVED",
    "SYSTEM_PROMPT_POINTER",
    "AdapterSection",
    "ConnectorSection",
    "DataSource",
    "EvalParameters",
    "LatencyDefaults",
    "Manifest",
    "ManifestError",
    "ManifestInvalid",
    "ManifestNotFound",
    "ManifestProblem",
    "ManifestReport",
    "ObservedPointer",
    "Pointer",
    "PromptPointer",
    "Redaction",
    "SuiteEntry",
    "Suppression",
    "TargetSection",
    "ToolEntry",
    "higher_side_effects",
    "load_manifest",
    "manifest_if_it_loads",
    "manifest_report",
    "pointer_problems",
    "protected_by_name",
]
