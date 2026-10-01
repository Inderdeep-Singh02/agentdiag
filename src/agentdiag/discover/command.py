"""`agentdiag discover`: the scan, the Connector's read, the draft, and what is written.

The two paths of phase-6 decision 27 write one draft shape:

- **`--scan <dir>`** (the default, over the Workspace root) proposes the Adapter, the
  prompts, the tools, the data sources and the Connector from the repository
  (`discover.scan`), every value under a REVIEW line.
- **`--from-connector --env <name>`** builds the Connector (`plugins.build_connector`)
  from the `connector` block of the Target's `manifest.yaml`, reads the deployed set, and
  saves it as local files under the Target directory — `prompts/<name>.md`,
  `tools/<name>.json` — which the draft points at (`local_only: false`), with the model
  from the read. Only a reviewed block is built: the scan's `deployed` guess names code
  nobody has vetted, and building a Connector from it would import and run that code, so
  it is accepted into `manifest.yaml` first. Both flags at once suit the Target whose
  repository holds the Adapter and whose platform holds the prompt.

**What the existing Manifest says is kept.** A draft replaces `manifest.yaml` once
reviewed, so every entry it holds — environments, prompts, tools, data sources, the
Connector, Suites, Suppressions, and each field of each — is copied verbatim without a
REVIEW line (`draft.merged`); the scan only adds the entries and fields that are absent.
The Connector's read is the exception it exists for: it replaces the prompt and tool
pointers it saved, and the model. Without a Manifest, `suites` and `judge_notes` are listed
from the Target directory when present.

**Nothing is written over work, and nothing outside the Workspace.** The draft goes to
`manifest.draft.yaml` (or an `--out` under the Workspace root, ADR-0013 §2), never over
`manifest.yaml`. A file this command would change — the draft, a saved prompt or tool
schema — is written only when git vouches that it is committed and unchanged, unless
`--force` (ADR-0011 §5's rule for `pull`, borrowed and closed: outside git, or gitignored,
nothing vouches, so the file is kept). A file whose bytes would not change is no overwrite.
Every refusal comes before the first write.

Exit 0 on a draft written, 3 otherwise (decision 43). Offline at import time: the
Connector is built only when `--from-connector` asks, and the in-process one imports no SDK.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agentdiag.connector.base import ConnectorError, DeployedSet
from agentdiag.connector.plugins import UnknownKind, build_connector
from agentdiag.discover.draft import (
    PROPOSED,
    SECTION_COMMENTS,
    Draft,
    DraftLine,
    annotate,
    copied,
    flow,
    merged,
    render_draft,
)
from agentdiag.discover.scan import ScanFindings, scan_repository, tool_kind
from agentdiag.exits import USAGE_EXIT
from agentdiag.overwrite import held_back
from agentdiag.run.init import slug as slug_of
from agentdiag.run.manifest import DEFAULT_ENVIRONMENT_KEY, OBSERVED, Manifest
from agentdiag.run.templates import (
    EVAL_PARAMETERS_COMMENT,
    FAMILY_COMMENT,
    PLACEHOLDER_DESCRIPTION,
    REDACTION_STARTER,
)
from agentdiag.sync.observe import connector_failure
from agentdiag.sync.pointed import render_schema
from agentdiag.workspace import (
    DEFAULT_SLUG,
    JUDGE_NOTES_NAME,
    SUITES_DIRNAME,
    TargetPaths,
    Workspace,
    WorkspaceError,
)

DRAFT_NAME = "manifest.draft.yaml"
PROMPTS_DIRNAME = "prompts"
TOOLS_DIRNAME = "tools"
LOCAL_ENVIRONMENT = "local"
"""The Adapter environment a scanned draft names: the one a developer runs on their own
machine, as `init`'s scaffold names it."""

DRAFTED_KEYS = frozenset(
    {"schema_version", "target", "family", "channel", "adapter", "prompts", "connector"}
    | {"tools", "data_sources", "suites", "judge_notes"}
)
"""The top-level keys the draft writes itself; every other key of an existing Manifest is
copied after them as written."""


class DiscoverRefused(Exception):
    """Nothing was written: one message, exit 3."""


@dataclass
class DiscoverOptions:
    target: TargetPaths
    scan: Path | None = None
    """The directory to scan; None with `from_connector` False: the Workspace root."""

    from_connector: bool = False
    environment: str | None = None
    out: Path | None = None
    force: bool = False
    several_targets: bool = False
    """Whether the Workspace holds other Targets, so the printed next commands name this one."""


@dataclass
class DiscoverExit:
    code: int
    message: str
    draft: Path | None = None
    written: list[Path] = field(default_factory=list)


def discover(options: DiscoverOptions) -> DiscoverExit:
    """Write the draft, or refuse and say why; exit 0 or 3."""
    try:
        return _discover(options)
    except DiscoverRefused as refused:
        return DiscoverExit(code=USAGE_EXIT, message=f"error: {refused}")


def _discover(options: DiscoverOptions) -> DiscoverExit:
    target = options.target
    if options.from_connector and not options.environment:
        raise DiscoverRefused("--from-connector reads one environment; name it with --env")
    if options.environment and not options.from_connector:
        raise DiscoverRefused("--env names the environment --from-connector reads; add it")
    out = _out(options)

    existing = _existing(target)
    scan_directory = options.scan
    if scan_directory is None and not options.from_connector:
        scan_directory = target.root
    findings: ScanFindings | None = None
    if scan_directory is not None:
        if not scan_directory.is_dir():
            raise DiscoverRefused(f"--scan {scan_directory} is not a directory")
        findings = scan_repository(scan_directory)

    deployed: DeployedFiles | None = None
    if options.from_connector:
        assert options.environment is not None
        deployed = DeployedFiles.of(_read(target, existing, options.environment))
    draft = compose(target, existing, findings, deployed, families=_sibling_families(target))
    if existing is None:
        text, reviews = render_draft(draft), draft.reviews
    else:
        text, reviews = annotate(
            target.manifest.read_text(encoding="utf-8"),
            existing,
            draft,
            notes=_identity_notes(target, existing, findings),
        )
    _check(text, out)

    files = deployed.files(target) if deployed is not None else {}
    files[out] = text
    _refuse_overwrites(files, options.force)
    # A Target this draft creates gets the local redaction starter `init` writes (ADR-0015
    # §4), so it is as complete as a scaffolded one; an author's file is never touched.
    starter = existing is None and not target.redaction.exists()
    if starter:
        files[target.redaction] = REDACTION_STARTER
    written = _write(files)
    return DiscoverExit(
        code=0,
        message=_summary(
            options, findings, deployed, out, reviews, written, scan_directory, starter=starter
        ),
        draft=out,
        written=written,
    )


def _out(options: DiscoverOptions) -> Path:
    """Where the draft goes: beside the Manifest, or an `--out` under the Workspace root."""
    target = options.target
    out = options.out if options.out is not None else target.directory / DRAFT_NAME
    if out.resolve() == target.manifest.resolve():
        raise DiscoverRefused(
            f"{out} is the Manifest itself; a draft is written beside it and moved into "
            "place once reviewed"
        )
    if not out.resolve().is_relative_to(target.root.resolve()):
        raise DiscoverRefused(
            f"--out {out} is outside the Workspace root {target.root}; agentdiag writes only "
            "under its Workspace, so name a path inside it"
        )
    return out


# --- what exists ---


def _existing(target: TargetPaths) -> dict[str, Any] | None:
    """The existing Manifest as written, or None; one that does not load is refused, since
    a draft that silently dropped what it holds would lose an author's work."""
    if not target.manifest.is_file():
        return None
    try:
        raw = yaml.safe_load(target.manifest.read_text(encoding="utf-8"))
        Manifest.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        raise DiscoverRefused(
            f"{target.manifest} does not load as a Manifest, so nothing could be kept from "
            f"it: {exc}; fix it or move it aside, then discover again"
        ) from exc
    assert isinstance(raw, dict)
    return raw


# --- the Connector's read ---


@dataclass(frozen=True)
class SavedFile:
    """One section of the deployed set as a file: its path relative to the Target
    directory, and its text."""

    path: str
    text: str


@dataclass
class DeployedFiles:
    """The deployed set as the files the draft points at."""

    environment: str
    prompts: dict[str, SavedFile]
    tools: dict[str, SavedFile]
    model: str | None

    @classmethod
    def of(cls, read: DeployedSet) -> DeployedFiles:
        """Each section a file; two names with one file name get `-2`, `-3`, as
        `sync.sections` numbers a repeated heading, so neither is lost."""
        taken: set[str] = set()
        prompts = {
            name: SavedFile(_unique_path(PROMPTS_DIRNAME, name, ".md", taken), _ending(text))
            for name, text in read.prompts.items()
        }
        tools = {
            name: SavedFile(
                _unique_path(TOOLS_DIRNAME, name, ".json", taken),
                render_schema(schema),
            )
            for name, schema in read.tools.items()
        }
        return cls(environment=read.environment, prompts=prompts, tools=tools, model=read.model)

    def files(self, target: TargetPaths) -> dict[Path, str]:
        return {
            target.relative(saved.path): saved.text
            for saved in (*self.prompts.values(), *self.tools.values())
        }


def _unique_path(folder: str, name: str, suffix: str, taken: set[str]) -> str:
    stem = _file_name(name)
    candidate, counter = f"{folder}/{stem}{suffix}", 2
    while candidate.lower() in taken:
        candidate, counter = f"{folder}/{stem}-{counter}{suffix}", counter + 1
    taken.add(candidate.lower())
    return candidate


def _file_name(name: str) -> str:
    """A section name as a file name: kept when it is one already, else its slug."""
    safe = all(character.isalnum() or character in "-_." for character in name)
    return name if safe and not name.startswith(".") else slug_of(name)


def _ending(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _read(target: TargetPaths, existing: dict[str, Any] | None, environment: str) -> DeployedSet:
    """The deployed set of `environment`, through the Connector `manifest.yaml` names."""
    if existing is None or not existing.get("connector"):
        where = "there is no Manifest yet" if existing is None else f"{target.manifest} names none"
        raise DiscoverRefused(
            f"--from-connector builds the Connector manifest.yaml names, and {where}; draft "
            "one with --scan, accept its `connector` block into manifest.yaml (or write one), "
            "then read through it"
        )
    manifest = Manifest.model_validate(existing).loaded_from(target.directory)
    section = manifest.connector
    assert section is not None
    if environment not in section.environments:
        names = ", ".join(section.environments) or "none"
        raise DiscoverRefused(
            f"the {section.kind} Connector names no environment {environment!r} (it names {names})"
        )
    try:
        connector = build_connector(manifest)
        assert connector is not None
        return connector.read_deployed_set(environment)
    except (ConnectorError, UnknownKind) as exc:
        raise DiscoverRefused(connector_failure(exc, manifest, environment)) from exc


# --- composing the draft ---


def compose(
    target: TargetPaths,
    existing: dict[str, Any] | None,
    findings: ScanFindings | None,
    deployed: DeployedFiles | None,
    *,
    families: dict[str, str] | None = None,
) -> Draft:
    """The draft: each section what the existing Manifest wrote, merged with what the scan
    proposes (`draft.merged`), then the Connector's read, where the scaffold puts it."""
    kept = existing or {}
    sections = {
        "target": _target(target, findings),
        "adapter": _adapter(kept, findings),
        "prompts": _prompts(target, findings, kept),
        "connector": _connector(findings),
        "tools": _tools(target, findings, kept),
        "data_sources": _data_sources(findings),
    }
    built = {key: _section(key, kept, proposal) for key, proposal in sections.items()}
    if deployed is not None:
        adapter = built["adapter"]
        if adapter is not None and deployed.model is not None:
            _set_model(adapter, kept, deployed)
        built["prompts"] = _replace(built["prompts"], "prompts", _read_prompts(deployed))
        tools = _read_tools(deployed, _mapping(kept.get("tools")))
        built["tools"] = _replace(built["tools"], "tools", tools)
    order = ("target", "family", "channel", "adapter", "prompts", "connector", "tools")
    lines = [
        line
        for key in (*order, "data_sources")
        if (line := built.get(key) or _copied_fact(kept, key)) is not None
    ]
    lines[1:1] = _family_lines(kept, families or {})
    lines += _suites_and_notes(target, kept)
    lines += [copied(key, value) for key, value in kept.items() if key not in DRAFTED_KEYS]
    return Draft(lines=lines)


def _copied_fact(kept: dict[str, Any], key: str) -> DraftLine | None:
    """`family` and `channel`, which the draft never guesses, as the Manifest wrote them."""
    if key in ("family", "channel") and kept.get(key) is not None:
        return copied(key, kept[key])
    return None


def _family_lines(kept: dict[str, Any], families: dict[str, str]) -> list[DraftLine]:
    """`family` and `channel` written commented out under REVIEW when the Manifest names
    neither (walkthrough friction 7): the draft cannot know the persona, but it can say
    which Families the Workspace's other Targets name."""
    if kept.get("family") is not None or kept.get("channel") is not None:
        return []
    others = ", ".join(f"{slug} is {family}" for slug, family in sorted(families.items()))
    sibling = f"the Workspace's other Targets name theirs ({others}); " if others else ""
    return [
        DraftLine(
            "family",
            "your-persona",
            missing=True,
            comment=FAMILY_COMMENT,
            review=(
                f"{sibling}name the persona this Target is one channel of, or delete both "
                "lines when it is the only one"
            ),
        ),
        DraftLine(
            "channel",
            "chat",
            missing=True,
            review="how this Target meets its users: chat, voice, …",
        ),
    ]


def _sibling_families(target: TargetPaths) -> dict[str, str]:
    """Each other Target of the Workspace that names a Family, by slug; read from their
    Manifests' text, never loaded or run."""
    try:
        others = [each for each in Workspace(target.root).targets() if each.slug != target.slug]
    except WorkspaceError:
        return {}
    found: dict[str, str] = {}
    for other in others:
        try:
            raw = yaml.safe_load(other.manifest.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(raw, dict) and isinstance(raw.get("family"), str):
            found[other.slug] = raw["family"]
    return found


def _identity_notes(
    target: TargetPaths, existing: dict[str, Any], findings: ScanFindings | None
) -> list[tuple[tuple[str, ...], str]]:
    """Review comments on the Target's identity as the existing Manifest writes it
    (walkthrough friction 10): `init`'s placeholder description, and a name that is not the
    slug."""
    identity = _mapping(existing.get("target"))
    notes: list[tuple[tuple[str, ...], str]] = []
    name = identity.get("name")
    if isinstance(name, str) and name != target.slug:
        notes.append(
            (
                ("target", "name"),
                f"the name differs from the slug {target.slug}; keep it only when the persona "
                "has a name of its own, since a Report and a comparison print it",
            )
        )
    if identity.get("description") == PLACEHOLDER_DESCRIPTION:
        better = (
            f"{PROPOSED}target.description: {flow(findings.description.value)}"
            if findings is not None and findings.description is not None
            else ""
        )
        notes.append(
            (
                ("target", "description"),
                "this is the placeholder `agentdiag init` writes; say what this Target does"
                + better,
            )
        )
    return notes


def _section(key: str, kept: dict[str, Any], proposal: DraftLine | None) -> DraftLine | None:
    """One top-level section: the existing Manifest's merged with the proposal, under the
    scaffold's comment for it; None when neither has anything."""
    existing = kept.get(key)
    if existing is None and proposal is None:
        return None
    line = merged(key, existing, proposal)
    line.comment = SECTION_COMMENTS.get(key)
    return line


def _replace(section: DraftLine | None, key: str, lines: list[DraftLine]) -> DraftLine:
    """The Connector's read put into a section: each line replaces the one of its key, the
    section's other lines kept."""
    if section is None or section.missing or section.children is None:
        section = DraftLine(key, children=[], comment=SECTION_COMMENTS.get(key))
    assert section.children is not None
    by_key = {line.key: line for line in lines}
    section.children = [by_key.pop(line.key, line) for line in section.children]
    section.children += list(by_key.values())
    return section


def _target(target: TargetPaths, findings: ScanFindings | None) -> DraftLine:
    if target.slug != DEFAULT_SLUG or findings is None:
        name, why = target.slug, f"the Target's slug, {target.slug}"
    else:
        name = slug_of(findings.directory.resolve().name)
        why = f"the name of the directory scanned, {findings.directory.resolve().name}"
    description = (
        DraftLine("description", findings.description.value, review=findings.description.review)
        if findings is not None and findings.description is not None
        else DraftLine(
            "description",
            "Say here what this Target does.",
            review="no docstring said what this Target does; one line that does",
        )
    )
    return DraftLine(
        "target",
        children=[
            DraftLine("name", name, review=f"{why}; the name a Report and a comparison print"),
            description,
        ],
    )


def _adapter(kept: dict[str, Any], findings: ScanFindings | None) -> DraftLine | None:
    """The scan's Adapter; its environment fields land on the existing Manifest's default
    environment when it names one, so a rescan fills that environment rather than adding a
    second."""
    if findings is None:
        return None
    environments = _mapping(_mapping(kept.get("adapter")).get("environments"))
    default = environments.get(DEFAULT_ENVIRONMENT_KEY)
    environment = default if isinstance(default, str) else LOCAL_ENVIRONMENT
    local: list[DraftLine] = []
    if findings.factory is not None:
        local.append(DraftLine("factory", findings.factory.value, review=findings.factory.review))
    else:
        local.append(
            DraftLine(
                "factory",
                "your_package.module:make_target",
                missing=True,
                review=_no_factory(findings),
            )
        )
    if findings.tools_reference is not None:
        local.append(
            DraftLine(
                "tools", findings.tools_reference.value, review=findings.tools_reference.review
            )
        )
    if findings.model is not None:
        local.append(DraftLine("model", findings.model.value, review=findings.model.review))
    else:
        local.append(
            DraftLine(
                "model",
                "<model id>",
                missing=True,
                review="no model= was found; the factory's own default applies unless one is named",
            )
        )
    kind_review = (
        f"{findings.factory.value} is a Python factory, which the in-process Adapter drives"
        if findings.factory is not None
        else "the only Adapter kind agentdiag ships; an HTTP Adapter is ticket 17's"
    )
    sdks = sorted({sdk for used in findings.sdks.values() for sdk in used})
    if sdks:
        kind_review += f"; the code imports {', '.join(sdks)}"
    return DraftLine(
        "adapter",
        children=[
            DraftLine("kind", "inprocess", review=kind_review),
            DraftLine(
                "side_effects",
                "none",
                review=(
                    "a Run is taken to change nothing outside this process; "
                    "`sandboxed` or `live` if a tool writes to a real system"
                ),
            ),
            DraftLine(
                "environments",
                children=[
                    DraftLine(
                        DEFAULT_ENVIRONMENT_KEY,
                        environment,
                        review=(
                            f"`{environment}` is the environment a developer runs on their "
                            "own machine; name the one a Run opens by default"
                        ),
                    ),
                    DraftLine(environment, children=local),
                ],
            ),
        ],
    )


def _no_factory(findings: ScanFindings) -> str:
    if findings.routes:
        return (
            f"no Python factory was found, only HTTP routes ({'; '.join(findings.routes)}); "
            "an HTTP Adapter is ticket 17's, so name a make_* callable here or wait for it"
        )
    return (
        "no module-level make_*/build_*/create_* returning a callable was found; name the factory"
    )


def _read_model(deployed: DeployedFiles) -> DraftLine:
    return DraftLine(
        "model",
        deployed.model,
        review=(
            f"the model the Connector read from {deployed.environment}; confirm the Target runs it"
        ),
    )


def _set_model(adapter: DraftLine, kept: dict[str, Any], deployed: DeployedFiles) -> None:
    """Put the read's model on the Adapter environment of the read's name, else the default."""
    environments = next(
        (line for line in adapter.children or [] if line.key == "environments"), None
    )
    if environments is None or not environments.children:
        return
    names = {line.key: line for line in environments.children}
    default = _mapping(_mapping(kept.get("adapter")).get("environments")).get(
        DEFAULT_ENVIRONMENT_KEY
    )
    chosen = names.get(deployed.environment) or (
        names.get(default) if isinstance(default, str) else None
    )
    if chosen is None:
        return
    if chosen.children is None:
        chosen.value, chosen.children = None, []
    chosen.children = [line for line in chosen.children if line.key != "model"]
    chosen.children.append(_read_model(deployed))


def _prompts(
    target: TargetPaths, findings: ScanFindings | None, kept: dict[str, Any]
) -> DraftLine | None:
    if findings is None:
        return None
    lines = [
        DraftLine(
            name,
            finding.value if finding.value == OBSERVED else _pointer(target, finding.value),
            review=finding.review,
        )
        for name, finding in findings.prompts.items()
    ]
    if lines:
        return DraftLine("prompts", children=lines)
    if kept.get("prompts") is not None:
        return None
    return DraftLine(
        "prompts",
        "{system: observed}",
        missing=True,
        review="no prompt was found; `observed` when the running Target holds it, else a path",
    )


def _read_prompts(deployed: DeployedFiles) -> list[DraftLine]:
    return [
        DraftLine(
            name,
            saved.path,
            review=(
                f"saved from the Connector's read of {deployed.environment}; the file is now "
                "the local side of the Sync"
            ),
        )
        for name, saved in deployed.prompts.items()
    ]


def _pointer(target: TargetPaths, path: Path) -> str:
    """A found file as the Manifest names it: relative to the Target directory."""
    return Path(os.path.relpath(path.resolve(), target.directory.resolve())).as_posix()


def _connector(findings: ScanFindings | None) -> DraftLine | None:
    if findings is None or findings.deployed is None:
        return None
    evidence = [
        DraftLine(kind, {"rows": finding.value}, review=finding.review)
        for kind, finding in findings.evidence.items()
    ]
    return DraftLine(
        "connector",
        children=[
            DraftLine(
                "kind",
                "inprocess",
                review=(
                    "the in-process Connector reads a deployed-set callable in this process; "
                    "a platform's Connector is a plugin with its own kind"
                ),
            ),
            DraftLine(
                "environments",
                children=[
                    DraftLine(
                        LOCAL_ENVIRONMENT,
                        children=[
                            DraftLine(
                                "deployed",
                                findings.deployed.value,
                                review=findings.deployed.review,
                            )
                        ],
                    )
                ],
            ),
            *([DraftLine("evidence", children=evidence)] if evidence else []),
        ],
    )


def _tools(
    target: TargetPaths, findings: ScanFindings | None, kept: dict[str, Any]
) -> DraftLine | None:
    if findings is None:
        return None
    lines: list[DraftLine] = []
    for name, tool in findings.tools.items():
        pointer: dict[str, Any] = {"kind": tool.kind}
        if tool.schema is not None:
            pointer["schema"] = _pointer(target, tool.schema)
        lines.append(DraftLine(name, pointer, review=tool.review))
    if lines:
        return DraftLine("tools", children=lines)
    if kept.get("tools") is not None:
        return None
    return DraftLine(
        "tools",
        "{lookup_order: {kind: retrieval}}",
        missing=True,
        review="no tool schema was found; list the Target's tools by name, if it has any",
    )


def _read_tools(deployed: DeployedFiles, known: dict[str, Any]) -> list[DraftLine]:
    """Each tool the read returned, pointing at its saved schema, every other field the
    Manifest wrote for it kept."""
    lines: list[DraftLine] = []
    for name, saved in deployed.tools.items():
        entry = _mapping(known.get(name))
        if "kind" in entry:
            kind, why = entry["kind"], "kind as the Manifest had it"
        else:
            kind, why = tool_kind(name)
        lines.append(
            DraftLine(
                name,
                {**entry, "kind": kind, "schema": saved.path},
                review=f"{why}; schema saved from the Connector's read of {deployed.environment}",
            )
        )
    return lines


def _data_sources(findings: ScanFindings | None) -> DraftLine | None:
    if findings is None or not findings.data_sources:
        return None
    lines = []
    for name, source in findings.data_sources.items():
        value: dict[str, Any] = {"identity": source.identity}
        if source.kind is not None:
            value["kind"] = source.kind
        lines.append(DraftLine(name, value, review=source.review))
    return DraftLine("data_sources", children=lines)


def _mapping(value: Any) -> dict[str, Any]:
    """`value` when the existing Manifest wrote a mapping there, else an empty one."""
    return value if isinstance(value, dict) else {}


def _suites_and_notes(target: TargetPaths, kept: dict[str, Any]) -> list[DraftLine]:
    lines: list[DraftLine] = []
    suites = kept.get("suites")
    if suites is None:
        folder = target.directory / SUITES_DIRNAME
        found = sorted(folder.glob("*.yaml")) if folder.is_dir() else []
        suites = [f"{SUITES_DIRNAME}/{path.name}" for path in found] or None
    if suites:
        lines.append(DraftLine("suites", suites, comment=SECTION_COMMENTS["suites"]))
    notes = kept.get("judge_notes")
    if notes is None and target.judge_notes.is_file():
        notes = JUDGE_NOTES_NAME
    if notes is not None:
        lines.append(DraftLine("judge_notes", notes))
    if "eval_parameters" not in kept:
        lines.append(DraftLine("", comment=EVAL_PARAMETERS_COMMENT))
    return lines


# --- checking and writing ---


def _check(text: str, out: Path) -> None:
    """The draft parses back to a Manifest: a draft that did not would be a guess nobody
    could validate."""
    try:
        Manifest.model_validate(yaml.safe_load(text))
    except (yaml.YAMLError, ValidationError) as exc:  # pragma: no cover - a bug, said plainly
        raise DiscoverRefused(
            f"the draft for {out} does not validate as a Manifest: {exc}"
        ) from exc


def _refuse_overwrites(files: dict[Path, str], force: bool) -> None:
    held = held_back(files, force=force)
    if held:
        raise DiscoverRefused(
            "these files would change and git does not hold what they say now: "
            + ", ".join(str(path) for path in held)
            + "; commit or move them, or pass --force"
        )


def _write(files: dict[Path, str]) -> list[Path]:
    written: list[Path] = []
    for path, text in files.items():
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def _summary(
    options: DiscoverOptions,
    findings: ScanFindings | None,
    deployed: DeployedFiles | None,
    out: Path,
    reviews: int,
    written: list[Path],
    scan_directory: Path | None,
    *,
    starter: bool = False,
) -> str:
    lines: list[str] = []
    if findings is not None and scan_directory is not None:
        found = [
            _count(len(findings.prompts), "prompt"),
            _count(len(findings.tools), "tool"),
            _count(len(findings.data_sources), "data source"),
        ]
        lines.append(f"Scanned {scan_directory}: {', '.join(found)}.")
        if findings.unreadable:
            lines.append(f"  not parsed: {', '.join(findings.unreadable)}")
    if deployed is not None:
        lines.append(f"Read the deployed set of {deployed.environment} through the Connector:")
        for path in deployed.files(options.target):
            state = "wrote" if path in written else "unchanged"
            lines.append(f"  {state} {path}")
    state = "Wrote" if out in written else "Unchanged:"
    lines.append(f"{state} {out}, {_count(reviews, 'line')} marked REVIEW.")
    if starter:
        lines.append(f"  wrote {options.target.redaction} (local, gitignored)")
    naming = f" --target {options.target.slug}" if options.several_targets else ""
    root = options.target.root
    lines += [
        "",
        "Next: accept or rewrite every REVIEW line, then",
        f"  agentdiag validate --root {root}{naming} --manifest {out}",
        f"  mv {out} {options.target.manifest}",
        f"  agentdiag sync --root {root}{naming}",
    ]
    return "\n".join(lines)


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" + ("" if number == 1 else "s")


__all__ = [
    "DRAFT_NAME",
    "DeployedFiles",
    "DiscoverExit",
    "DiscoverOptions",
    "DiscoverRefused",
    "SavedFile",
    "compose",
    "discover",
]
