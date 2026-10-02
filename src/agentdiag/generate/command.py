"""`agentdiag generate`: a coding agent's drafts, checked, keyed and written as a Suite.

The generation skill (`skills/generate/SKILL.md`) has an agent read a Target's prompt
sections and tool schemas and draft one Scenario per rule and per tool; this command is the
mechanical half (phase-6 decisions 29 and 30, ADR-0002 §7-§8):

- **Every draft answers to something.** A draft names its `provenance` in one of the four
  forms of `generate.ids`, and a prompt or tool it names must be one the Manifest points
  at; a draft with no Eval is refused by name, because a Scenario nothing judges proves
  nothing. The Suite's `tags` gain every tool a tool Eval names, so `--tag cancel_order`
  selects every Scenario about that tool.
- **Nothing is written that `validate` would reject.** The Suite is assembled as data,
  validated with the Manifest's latency defaults, rendered by the one canonical dump
  (`scenario.render.render_commented_suite`) with a `# from <provenance>` line above each
  Scenario, and validated again as the text that would be written. A problem is exit 3 with
  `validate`'s own lines, located in the drafts file, and no file changes.
- **Ids outlive the drafts that made them.** On a Suite that exists, a draft matched to a
  Scenario keeps its id (`generate.ids.match_drafts`); a Scenario no draft matches is kept
  and listed under `not_run` as retired, since a Run may hold Trials under its id; and an id
  a draft would re-key — it names an id other than its match's — is refused while any Run
  in the Index holds a Trial under the old one (`run.index.scenario_ids`), naming the Runs.
- **A Suite someone edited is not lost.** The Suite file is overwritten only when its bytes
  would not change or git vouches for it (`agentdiag.overwrite`, the rule `discover` keeps,
  decision 27 amended), unless `--force`. Its header names the drafts relative to the
  Workspace root, or by file name, so the same drafts give the same bytes anywhere.
- **The Manifest is edited, not rewritten.** When its `suites` does not name the Suite, one
  list line is inserted after the last entry (or the key appended, or a one-line flow list
  extended), in the file's own line endings, so every comment the Manifest holds
  survives; the edit is parsed back and checked before it is written.

Offline: this module imports the Scenario models, the Manifest, the Fingerprint and the
Index, none of which reaches the SDK.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agentdiag.eval.registry import REGISTRY
from agentdiag.exits import USAGE_EXIT
from agentdiag.generate.ids import (
    Drafted,
    Existing,
    Provenance,
    ProvenanceError,
    derive_id,
    match_drafts,
    parse_provenance,
)
from agentdiag.overwrite import held_back
from agentdiag.run.index import IndexCorrupt, scenario_ids
from agentdiag.run.manifest import Manifest, ManifestError, ManifestNotFound, load_manifest
from agentdiag.run.templates import scalar
from agentdiag.scenario.load import path_spelling
from agentdiag.scenario.models import Suite, expand_declaration
from agentdiag.scenario.render import render_commented_suite
from agentdiag.scenario.validate import (
    DefaultThresholds,
    ManifestFacts,
    Problem,
    ValidationReport,
    report_lines,
    validate_document,
    validate_rendered,
)
from agentdiag.sync.fingerprint import load_fingerprint
from agentdiag.workspace import SLUG, SUITES_DIRNAME, TargetPaths

DEFAULT_SUITE = "generated"
"""The Suite `generate` writes when neither the drafts nor `--suite` name one."""

DRAFTS_KEYS = frozenset({"target", "suite", "description", "evals", "fixtures", "scenarios"})
"""A drafts file's keys: decision 29's four, and the Suite-level `evals` and `fixtures` a
drafted Scenario may inherit or name, which the Suite written carries as given."""

RETIRED_PREFIX = "retired by generate on "
"""How `not_run` marks a Scenario no draft matched (decision 30); a draft that matches it
again removes the entry, and no other `not_run` reason is touched."""

TOOL_PARAMETERS = ("tools", "tool")
"""The parameters a tool Eval names tools in: `expect_tools*`, `forbid_tools` and
`tool_choice` take `tools`; `expect_tool_args` names one `tool`."""


class GenerateRefused(Exception):
    """Nothing was written: the lines to print, exit 3."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        super().__init__("\n".join(lines))


@dataclass
class GenerateOptions:
    target: TargetPaths
    drafts: Path
    suite: str | None = None
    check: bool = False
    force: bool = False
    """Overwrite a Suite file git does not vouch for (the ticket 11 overwrite rule)."""

    several_targets: bool = False
    """Whether the Workspace holds other Targets, so the printed next commands name this one."""


@dataclass
class GenerateExit:
    code: int
    message: str


def generate(options: GenerateOptions) -> GenerateExit:
    """Write the Suite, or refuse and say why; exit 0 or 3."""
    try:
        return _generate(options)
    except GenerateRefused as refused:
        return GenerateExit(code=USAGE_EXIT, message="\n".join(refused.lines))


@dataclass
class _Placed:
    """One Scenario of the Suite to be written, and how it got its id."""

    state: str
    """`new`, `kept`, `re-keyed` or `retired`."""

    document: dict[str, Any]
    provenance: str | None

    @property
    def id(self) -> str:
        return str(self.document["id"])


def _generate(options: GenerateOptions) -> GenerateExit:
    target = options.target
    manifest = _manifest(target)
    drafts_file = options.drafts
    raw = _read_drafts(drafts_file)
    drafts = raw["scenarios"]
    name = options.suite or raw.get("suite") or DEFAULT_SUITE
    if not isinstance(name, str) or not SLUG.match(name):
        raise GenerateRefused(
            [f"error: the Suite name {name!r} is not lowercase letters, digits and hyphens"]
        )
    relative = f"{SUITES_DIRNAME}/{name}.yaml"
    out = target.relative(relative)
    defaults = manifest.latency_thresholds

    problems = _draft_problems(raw, manifest)
    if problems:
        raise GenerateRefused(_lines(drafts_file, problems))
    warnings = _section_warnings(target, drafts)

    existing_raw = _existing(out, defaults)
    placed, not_run = _place(target, drafts, existing_raw)
    document = _suite_document(manifest, raw, existing_raw, placed, not_run)

    report = validate_document(document, drafts_file, facts=ManifestFacts.of(manifest))
    if not report.ok:
        raise GenerateRefused(report_lines(report))
    text = _render(document, placed, _drafts_named(drafts_file, target.root))
    rendered = validate_rendered(text, out, facts=ManifestFacts(default_thresholds=defaults))
    if not rendered.ok:  # pragma: no cover - the canonical dump disagreeing with itself
        raise GenerateRefused(report_lines(rendered))
    warnings += [line for line in report_lines(report) if line.startswith("warning:")]

    adds = not any(path_spelling(entry.path) == relative for entry in manifest.suites)
    manifest_bytes = _with_suite(target.manifest, relative) if adds else None
    held = held_back({out: text}, force=options.force)
    if held:
        raise GenerateRefused(
            [
                f"error: {held[0]} would change and git does not hold what it says now; "
                "commit or move it, or pass --force"
            ]
        )
    refreshed: Path | None = None
    if not options.check:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        if manifest_bytes is not None:
            target.manifest.write_bytes(manifest_bytes)
            refreshed = _refresh_table(target)
    return GenerateExit(
        code=0,
        message=_summary(options, out, relative, name, placed, adds, warnings, refreshed),
    )


def _refresh_table(target: TargetPaths) -> Path | None:
    """The Suite added to the Manifest is a cell of the Orientation page's Targets table:
    a command that writes a Manifest refreshes the table it changed (0.1.2-interfaces,
    amended decision 4), and never writes an absent page."""
    from agentdiag.orientation import refresh_targets_table
    from agentdiag.workspace import Workspace

    return refresh_targets_table(Workspace.at(target.root))


# --- reading ---


def _manifest(target: TargetPaths) -> Manifest:
    try:
        return load_manifest(target)
    except (ManifestNotFound, ManifestError) as exc:
        raise GenerateRefused([f"error: {exc}"]) from exc


def _read_drafts(path: Path) -> dict[str, Any]:
    """The drafts file as a mapping with a non-empty `scenarios` list, or refused."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GenerateRefused([f"error: cannot read the drafts {path}: {exc}"]) from exc
    except yaml.YAMLError as exc:
        raise GenerateRefused([f"error: {path}: not valid yaml: {exc}"]) from exc
    if not isinstance(raw, dict):
        raise GenerateRefused([f"error: {path}: a drafts file is a mapping with `scenarios`"])
    unknown = sorted(str(key) for key in raw if key not in DRAFTS_KEYS)
    if unknown:
        raise GenerateRefused(
            [
                f"error: {path}: unknown key {', '.join(unknown)}; a drafts file holds "
                + ", ".join(sorted(DRAFTS_KEYS))
            ]
        )
    scenarios = raw.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise GenerateRefused([f"error: {path}: scenarios: a drafts file holds one draft or more"])
    return raw


def _draft_problems(raw: dict[str, Any], manifest: Manifest) -> list[Problem]:
    """What decision 29 asks of the drafts before any id is derived."""
    problems: list[Problem] = []
    named = raw.get("target")
    if named is not None and named != manifest.target.name:
        problems.append(
            Problem(
                path="target",
                message=(
                    f"the drafts are for Target {named!r} and the Manifest is "
                    f"{manifest.target.name!r}"
                ),
            )
        )
    for position, entry in enumerate(raw["scenarios"]):
        where = f"scenarios[{position}]"
        if not isinstance(entry, dict):
            problems.append(Problem(path=where, message="a draft is a Scenario mapping"))
            continue
        title = entry.get("title")
        called = f"draft {title!r}" if isinstance(title, str) else "this draft"
        if isinstance(title, str) and not any(character.isalnum() for character in title):
            problems.append(
                Problem(
                    path=f"{where}.title",
                    message=f"{called} has a title with no letter or digit, and an id is "
                    "derived from its words",
                )
            )
        provenance = entry.get("provenance")
        if not isinstance(provenance, str) or not provenance:
            problems.append(
                Problem(
                    path=f"{where}.provenance",
                    message=f"{called} names no provenance: the prompt section, tool, Trace "
                    "or Change record it answers to",
                )
            )
        else:
            problems.extend(_provenance_problems(f"{where}.provenance", provenance, manifest))
        evals = entry.get("evals")
        if not isinstance(evals, list) or not evals:
            problems.append(
                Problem(
                    path=f"{where}.evals",
                    message=f"{called} declares no Eval; a generated Scenario is judged by at "
                    "least one Eval",
                )
            )
    return problems


def _provenance_problems(where: str, text: str, manifest: Manifest) -> list[Problem]:
    try:
        provenance = parse_provenance(text)
    except ProvenanceError as exc:
        return [Problem(path=where, message=str(exc))]
    if provenance.listed_in is None:
        return []
    listed = sorted(getattr(manifest, provenance.listed_in))
    if provenance.name in listed:
        return []
    noun = provenance.kind
    known = ", ".join(listed) or "none"
    return [
        Problem(
            path=where,
            message=(
                f"provenance {text!r} names {noun} {provenance.name!r}, which the Manifest "
                f"does not point at (its {noun}s: {known})"
            ),
        )
    ]


def _section_warnings(target: TargetPaths, drafts: list[dict[str, Any]]) -> list[str]:
    """A prompt section the Fingerprint in force does not hold: a heading renamed since the
    drafts, or a slug typed by hand. Warned, not refused: `sync` may simply not have run."""
    fingerprint = load_fingerprint(target)
    if fingerprint is None:
        return []
    uncovered = {entry.id for entry in fingerprint.not_covered}
    warnings: list[str] = []
    for position, entry in enumerate(drafts):
        provenance = _parsed(entry.get("provenance"))
        if provenance is None or provenance.kind != "prompt":
            continue
        section = provenance.section_id
        whole = f"prompt.{provenance.name}"
        if section in fingerprint.sections or whole in uncovered:
            continue
        warnings.append(
            f"warning: scenarios[{position}].provenance: {section} is no section of the "
            "Fingerprint in force; `agentdiag target show` lists them"
        )
    return warnings


def _parsed(text: Any) -> Provenance | None:
    try:
        return parse_provenance(text) if isinstance(text, str) else None
    except ProvenanceError:
        return None


def _existing(out: Path, defaults: DefaultThresholds) -> dict[str, Any] | None:
    """The Suite already at `out`, as authored, or None; one `validate` rejects is refused."""
    if not out.is_file():
        return None
    try:
        raw = yaml.safe_load(out.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raw = None
        report = ValidationReport(
            file=out, errors=[Problem(path="", message=f"not valid yaml: {exc}")]
        )
    else:
        report = validate_document(raw, out, facts=ManifestFacts(default_thresholds=defaults))
    if not report.ok or not isinstance(raw, dict):
        raise GenerateRefused(
            [
                *report_lines(report),
                f"error: {out} exists and does not validate; fix it or move it before "
                "regenerating, since its ids are what regeneration keeps",
            ]
        )
    return raw


# --- ids (decision 30) ---


def _place(
    target: TargetPaths, drafts: list[dict[str, Any]], existing_raw: dict[str, Any] | None
) -> tuple[list[_Placed], dict[str, str]]:
    """Every Scenario the Suite will hold, drafts first then retired ones, and its `not_run`."""
    old: list[dict[str, Any]] = list(existing_raw.get("scenarios") or []) if existing_raw else []
    not_run: dict[str, str] = dict(existing_raw.get("not_run") or {}) if existing_raw else {}
    existing = [
        Existing(id=str(entry["id"]), provenance=entry.get("provenance"), title=entry["title"])
        for entry in old
    ]
    matched = match_drafts(
        [
            Drafted(
                provenance=entry["provenance"],
                title=str(entry.get("title", "")),
                id=None if entry.get("id") is None else str(entry["id"]),
            )
            for entry in drafts
        ],
        existing,
    )
    claimed = set(matched.values())
    taken = {existing[index].id for index in range(len(existing)) if index not in claimed}
    rekeyed: dict[str, str] = {}
    ids: dict[int, str] = {}
    for position, entry in enumerate(drafts):
        if position in matched:
            kept = existing[matched[position]].id
            named = entry.get("id")
            ids[position] = str(named) if named is not None else kept
            if ids[position] != kept:
                rekeyed[kept] = ids[position]
        elif entry.get("id") is not None:
            ids[position] = str(entry["id"])
        else:
            continue
        taken.add(ids[position])
    for position, entry in enumerate(drafts):
        if position not in ids:
            anchor = parse_provenance(entry["provenance"]).anchor
            ids[position] = derive_id(anchor, str(entry.get("title", "")), taken=taken)
            taken.add(ids[position])
    if rekeyed:
        _refuse_held_rekeys(target, rekeyed)

    placed: list[_Placed] = []
    for position, entry in enumerate(drafts):
        document = {"id": ids[position], **{k: v for k, v in entry.items() if k != "id"}}
        document["tags"] = _tags(entry)
        if position in matched:
            kept = existing[matched[position]].id
            state = "kept" if kept == ids[position] else "re-keyed"
            reason = not_run.pop(kept, None)
            if reason is not None and not reason.startswith(RETIRED_PREFIX):
                not_run[ids[position]] = reason
        else:
            state = "new"
        placed.append(_Placed(state, document, entry["provenance"]))
    today = datetime.now(UTC).date().isoformat()
    for index, entry in enumerate(old):
        if index in claimed:
            continue
        not_run.setdefault(str(entry["id"]), f"{RETIRED_PREFIX}{today}: no draft")
        placed.append(_Placed("retired", dict(entry), entry.get("provenance")))
    return placed, not_run


def _refuse_held_rekeys(target: TargetPaths, rekeyed: dict[str, str]) -> None:
    """ADR-0002 §8: an id a Run holds a Trial under is never re-keyed."""
    try:
        held = scenario_ids(target.root, target=target.slug)
    except IndexCorrupt as corrupt:
        raise GenerateRefused([f"error: {corrupt}"]) from corrupt
    lines = [
        f"error: Scenario {old!r} would be re-keyed to {new!r}, and Runs hold Trials under "
        f"{old!r}: {', '.join(held[old])}; keep its id (drop `id: {new}` from the draft)"
        for old, new in sorted(rekeyed.items())
        if old in held
    ]
    if lines:
        raise GenerateRefused(lines)


def _tags(entry: dict[str, Any]) -> list[str]:
    """The draft's own tags, then every tool its tool Evals name (decision 29)."""
    tags: list[Any] = list(entry.get("tags") or [])
    for declaration in entry.get("evals") or []:
        tags.extend(_tools_named(declaration))
    return [str(tag) for tag in dict.fromkeys(tags)]


def _tools_named(declaration: Any) -> list[str]:
    """The tools one declaration names, in any authored spelling; none for an unreadable one,
    which `validate` then reports."""
    try:
        expanded = expand_declaration(declaration)
    except ValueError:
        return []
    if not isinstance(expanded, dict):
        return []
    spec = REGISTRY.get(str(expanded.get("eval")))
    params = expanded.get("params")
    if spec is None or not spec.tool_family or not isinstance(params, dict):
        return []
    found: list[str] = []
    for key in TOOL_PARAMETERS:
        value = params.get(key)
        listed = value if isinstance(value, list) else [value]
        found.extend(str(name) for name in listed if isinstance(name, str))
    counts = params.get("counts")
    if isinstance(counts, dict):
        found.extend(str(name) for name in counts)
    return found


# --- the Suite ---


def _suite_document(
    manifest: Manifest,
    raw: dict[str, Any],
    existing_raw: dict[str, Any] | None,
    placed: list[_Placed],
    not_run: dict[str, str],
) -> dict[str, Any]:
    """The Suite as data: the drafts' Suite-level keys where given, else the existing
    Suite's, which also keeps its `extras`."""
    previous = existing_raw or {}
    document: dict[str, Any] = {"schema_version": 1, "target": manifest.target.name}
    for key in ("description", "evals", "fixtures"):
        value = raw[key] if key in raw else previous.get(key)
        if value is not None:
            document[key] = value
    if not_run:
        document["not_run"] = not_run
    document["scenarios"] = [entry.document for entry in placed]
    if previous.get("extras"):
        document["extras"] = previous["extras"]
    return document


def _drafts_named(drafts: Path, root: Path) -> str:
    """The drafts file as the Suite's header names it: relative to the Workspace root when
    it is under it, else its name alone, so a Suite generated from the same drafts is the
    same bytes wherever the command ran (decision 47)."""
    try:
        return drafts.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return drafts.name


def _render(document: dict[str, Any], placed: list[_Placed], drafts: str) -> str:
    suite = Suite.model_validate(document)
    header = (
        "# Written by `agentdiag generate` from the drafts in\n"
        f"#   {drafts}\n"
        "# Regenerate from the drafts rather than editing here: ids are kept, and a Scenario\n"
        "# no draft matches stays, listed under not_run as retired.\n"
    )
    above = [f"from {entry.provenance}" if entry.provenance else None for entry in placed]
    return render_commented_suite(suite, header=header, above=above)


# --- the Manifest's `suites` ---

_SUITES_KEY = re.compile(r"^suites:[ \t]*(#.*)?$")
_SUITES_FLOW = re.compile(r"^suites:[ \t]*\[(?P<items>[^\]]*)\][ \t]*(?P<comment>#.*)?$")
_ITEM = re.compile(r"^(?P<indent>[ \t]*)- ")


def _with_suite(path: Path, relative: str) -> bytes:
    """The Manifest's bytes with `relative` added to `suites`, every other line as it was,
    its line endings included: a CRLF Manifest gains a CRLF line."""
    text = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += newline
    item = scalar(relative)
    start = next((i for i, line in enumerate(lines) if line.startswith("suites:")), None)
    if start is None:
        lines += [newline, f"suites:{newline}", f"  - {item}{newline}"]
    elif flow := _SUITES_FLOW.match(lines[start].rstrip("\r\n")):
        items = flow["items"].strip()
        comment = f"  {flow['comment']}" if flow["comment"] else ""
        ending = lines[start][len(lines[start].rstrip("\r\n")) :]
        lines[start] = f"suites: [{items + ', ' if items else ''}{item}]{comment}{ending}"
    elif _SUITES_KEY.match(lines[start].rstrip("\r\n")):
        last, indent = start, None
        for position in range(start + 1, len(lines)):
            line = lines[position]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if not (line[0] in " \t" or line.startswith("- ")):
                break
            last = position
            if indent is None and (found := _ITEM.match(line)):
                indent = found["indent"]
        lines.insert(last + 1, f"{indent if indent is not None else '  '}- {item}{newline}")
    else:
        raise GenerateRefused(
            [
                f"error: {path}: `suites` is written in a form this command does not edit; "
                f"add `{relative}` to it and rerun"
            ]
        )
    edited = "".join(lines)
    try:
        suites = Manifest.model_validate(yaml.safe_load(edited)).suites
    except (yaml.YAMLError, ValidationError) as exc:  # pragma: no cover - said plainly
        raise GenerateRefused([f"error: adding {relative} to {path} broke it: {exc}"]) from exc
    if not any(path_spelling(entry.path) == relative for entry in suites):  # pragma: no cover
        raise GenerateRefused([f"error: {path}: could not add {relative} to `suites`"])
    return edited.encode("utf-8")


# --- what is printed ---


def _lines(file: Path, problems: list[Problem]) -> list[str]:
    return report_lines(ValidationReport(file=file, errors=problems))


def _summary(
    options: GenerateOptions,
    out: Path,
    relative: str,
    name: str,
    placed: list[_Placed],
    adds: bool,
    warnings: list[str],
    refreshed: Path | None = None,
) -> str:
    counts = dict.fromkeys(("new", "kept", "re-keyed", "retired"), 0)
    for entry in placed:
        counts[entry.state] += 1
    tally = ", ".join(f"{number} {state}" for state, number in counts.items())
    verb = "Would write" if options.check else "Wrote"
    lines = [*warnings, f"{verb} {out}: {_count(len(placed), 'Scenario')}, {tally}."]
    width = max(len(entry.id) for entry in placed)
    lines += [
        f"  {entry.state:<9}{entry.id.ljust(width)}  from {entry.provenance or 'no provenance'}"
        for entry in placed
    ]
    if adds:
        lines.append(
            f"{'Would add' if options.check else 'Added'} {relative} to the Manifest's suites."
        )
    if refreshed is not None:
        lines.append(f"Refreshed the Targets table in {refreshed}.")
    if options.check:
        lines.append("Nothing written (--check).")
        return "\n".join(lines)
    naming = f" --target {options.target.slug}" if options.several_targets else ""
    root = options.target.root
    lines += [
        "",
        "Next:",
        f"  agentdiag validate --root {root}{naming}",
        f"  agentdiag run --root {root}{naming} --suite {name} --dry-run",
    ]
    return "\n".join(lines)


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" + ("" if number == 1 else "s")


__all__ = [
    "DEFAULT_SUITE",
    "DRAFTS_KEYS",
    "RETIRED_PREFIX",
    "GenerateExit",
    "GenerateOptions",
    "GenerateRefused",
    "generate",
]
