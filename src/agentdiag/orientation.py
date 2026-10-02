"""The files a coding agent reads at a Workspace root: the Orientation page, its two import
files and the vocabulary copy (ADR-0016 §1-§3, 0.1.2-interfaces decisions 1-9 as amended
after the ticket 45 reviews).

ADR-0014 §3 installed the skills and no page routing to them; a Workspace cloned into a
Harness then held procedures nothing pointed to. `AGENTS.md` is that page: what the
repository is, the read set, the request-to-skill routing, the Targets table, the standing
rules and the commands. `CLAUDE.md` and `GEMINI.md` are each one import line in their
Harness's own spelling, so whichever Harness opens the clone reads the same page, and
`.agentdiag/CONTEXT.md` carries the words the page and the skills use into a clone with no
network and no agentdiag checkout.

**One reading, two renderings.** `_inspect` reads the root once (the Registry, the skills
layouts, the page with its markers, the import files, the vocabulary), and both
`write_orientation` (what `init`, `init --skills` and `registry --write` call) and
`orientation_warnings` (what `validate` prints) render that state, so the rules below are
written once:

- **A file the user owns keeps every byte** (ADR-0016 §3). The page is read as bytes, never
  through universal newlines, and the marked block is written with the file's own line
  ending. A page without both markers is the user's: the block is appended at its end, with
  a notice. Otherwise only the block is replaced: the closing marker is the last one, and the
  opening marker the last one before it, so a stray marker never pairs with the appended
  block across the user's text. `CLAUDE.md` and `GEMINI.md` are written only when absent.
- **The vocabulary is agentdiag's** and is rewritten freely: it sits under `.agentdiag/`
  because the root may be a repository with a `CONTEXT.md` of its own.
- **The table is a rendering of the Registry**, never authored. A command that writes under
  `.agentdiag/targets/` (`init`, `generate`, `discover`) refreshes it through
  `refresh_targets_table` when the page holds the markers, and never writes an absent page;
  `registry --write` is for every other change. Only a stale table is warned of everywhere;
  every other warning waits until the skills are installed, because a Workspace in a corner
  of a repository with instruction files of its own has nothing to route until then.

The read set the page names is budgeted at `READ_SET_BUDGET_TOKENS`, and a test holds it
(decision 9). Offline: it reads Manifests through the Registry and the package's own files.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from agentdiag.registry import RegistryEntry, registry, suites_shown
from agentdiag.run.skills import INSTALLED_PREFIX, installed_layouts, packaged_skills
from agentdiag.sync.fingerprint import NONE_SHOWN
from agentdiag.workspace import AGENTDIAG_DIR, VOCABULARY_FILE, Workspace

ORIENTATION_NAME = "AGENTS.md"
"""The Orientation page, at the Workspace root: Codex reads it there itself."""

CLAUDE_IMPORT_NAME = "CLAUDE.md"
"""The file Claude Code reads at the root."""

CLAUDE_IMPORT_LINE = "@AGENTS.md"
"""Claude Code's import spelling: it reads `AGENTS.md` only through this line."""

GEMINI_IMPORT_NAME = "GEMINI.md"
"""The file Gemini CLI reads at the root."""

GEMINI_IMPORT_LINE = "@./AGENTS.md"
"""Gemini CLI's import spelling: a relative import starts with `./`."""

IMPORTS = (
    (CLAUDE_IMPORT_NAME, CLAUDE_IMPORT_LINE, "Claude Code"),
    (GEMINI_IMPORT_NAME, GEMINI_IMPORT_LINE, "Gemini CLI"),
)
"""Each import file, its one line, and the Harness that reads it."""

TARGETS_OPEN = "<!-- agentdiag:targets -->"
"""The opening marker of the generated Targets table."""

TARGETS_CLOSE = "<!-- /agentdiag:targets -->"
"""The closing marker: the block up to it is the only text of a page agentdiag rewrites
once the page exists."""

TARGETS_GENERATED = (
    "<!-- generated from the Manifests by agentdiag registry --write; "
    "a hand edit here is replaced -->"
)
"""The block's first line, so a block appended to a user's page says what it is."""

VOCABULARY_NAME = f"{AGENTDIAG_DIR}/{VOCABULARY_FILE}"
"""The vocabulary copy, relative to the root (`Workspace.vocabulary` is the path)."""

READ_SET_BUDGET_TOKENS = 10_000
"""The read set the page names (the page, the vocabulary, one skill), measured as
`len(text) // 4` (ADR-0016 §1)."""

TABLE_HEADER = (
    "| Target | Name | Family / channel | Default env | Protected | Suites | Connector "
    "| Maintainer notes |"
)
"""The Targets table's header row (decision 6)."""

TABLE_RULE = "|---|---|---|---|---|---|---|---|"
"""The Markdown rule under the header, one cell per column."""

PAGE = """\
# Orientation: an agentdiag Workspace

This repository is an agentdiag **Workspace**: one `.agentdiag/targets/<slug>/` directory per
**Target**, an agentic system under test, holding its Manifest (pointers to its prompts, tools
and environments, never copies), its Suites of Scenarios, its Calibration Notes for the Judge,
its Maintainer notes for you, and its Change records. `agentdiag` drives a Target through
Scenarios, records each Trial as a Trace, judges the Trace with Evals, keeps the Target's local
files and its deployed set in Sync, and records every fix as a Change record that closes only
through `compare`.

**Read before the first command of a session**: this page; `.agentdiag/targets/<slug>/maintainer_notes.md`
for the Target the request names; the one `/agentdiag-*` skill the request routes to. Reference,
on demand: `agentdiag <command> --help` for every flag, and `{vocabulary}` for every word
used here (Target, Manifest, Suite, Scenario, Trace, Span, Eval, Verdict, Sync, Change record).
That read set is budgeted at 10k tokens.

## Routing

Match the request to one skill and invoke it by name. Each skill marks its steps **code** (run
it), **judgement** (decide it and say why) or **human** (hand it to the person and wait), and
ends on a "Done when" list.

| The request | Skill |
|---|---|
| A Target this Workspace has never described, whose prompts and tools live in a repository or on a platform | `/agentdiag-discover` |
| A Target whose prompts, tests, judging rules and notes already live in another maintenance repository, with no Connector yet | `/agentdiag-migrate` |
| "Write tests", "build a Suite", "cover rule N": Scenarios from a Target's prompt rules and tools | `/agentdiag-generate` |
| A failure of the Target: a failing Trial, a Diagnosis, a complaint about a real conversation, taken to a verified or refuted Change record | `/agentdiag-fix-cycle` |
| A Score that is wrong when the Target was right: the Judge or an Eval misjudged, and the judging configuration must change | `/agentdiag-correction` |

A failure of the Target is a fix cycle; a failure of the judgement is a correction. Read a
Target through its commands before its files: `agentdiag target show <slug>` prints the
Manifest as loaded, the notes, the Fingerprint and the open records; `agentdiag show <run>
<scenario>` prints a Trial as a story; `agentdiag list` the Runs.

## Targets

{table}

Generated from the Manifests by `agentdiag registry --write`, and `agentdiag validate` warns
when it is stale. Every command takes `--target <slug>` when the Workspace holds more than
one Target, and `--root <path>` from outside it.

## Rules

1. **Diff before push.** `agentdiag push --env <env>` previews; `--push` writes only after
   the person has read that preview's diff.
2. **A protected environment confirms by name.** Its name is typed by a person at the
   terminal, or in the UI's confirm step; no flag stands in for it, so a session without a
   terminal hands the person the exact command and waits.
3. **Verdicts cite Spans.** A Score names the Spans it judged, and a cause written into a
   Change record quotes a Span `agentdiag show` printed, never a summary of the conversation.
4. **No credential value in any file.** A Manifest names the environment variable that holds
   a credential; the value lives in `~/.agentdiag/env` or the shell, and in no file under this
   repository, no Trace and no record.
5. **Runs are output.** `runs/`, Restore points, the Index and each Target's `redaction.yaml`
   are gitignored; Manifests, Suites, notes, Change records, Push records and
   `fingerprint.json` are committed, a fix with the record and Fingerprint it belongs to.

## Commands

| To | Run |
|---|---|
| List the Targets; print one in full | `agentdiag registry`; `agentdiag target show <slug>` |
| Check a Target offline | `agentdiag validate --target <slug>` (every Target: `--all`) |
| See what a Run would do, then run it | `agentdiag run --target <slug> --dry-run`; drop `--dry-run`, add `--suite <name>` or `--scenario <id>` |
| Read a Trial | `agentdiag show <run> <scenario>` |
| Compare two Runs | `agentdiag compare <baseline> <run>` |
| Check or record the deployed set | `agentdiag sync --target <slug>`, `--check` to check only |
| Preview or make a push | `agentdiag push --target <slug> --env <env>`, then `--push --change <id>` |
| Carry a fix | `agentdiag change open`, `expect`, `propose`, `close` |
| Refresh this page's table | `agentdiag registry --write` |

Skills this agentdiag ships, installed by `agentdiag init --skills`: {skills}.
"""  # noqa: E501
"""The page (decision 6 as amended), written with the writing-for-agents skill and kept
verbatim: only the three fields vary, `{table}` (the marked block), `{vocabulary}` and
`{skills}`. The routing and the commands tables are wider than the code's 100 columns
because a Markdown table row is one line."""


def vocabulary_text() -> str:
    """agentdiag's vocabulary as the installed package holds it, read through
    `importlib.resources` so a wheel serves it exactly as a checkout does (decision 8)."""
    return files("agentdiag").joinpath(VOCABULARY_FILE).read_bytes().decode("utf-8")


def render_page(entries: Sequence[RegistryEntry]) -> str:
    """The whole Orientation page over these Registry entries, naming every skill the
    installed version ships."""
    skills = ", ".join(f"/{INSTALLED_PREFIX}{name}" for name in packaged_skills())
    return PAGE.format(
        table=render_targets_table(entries), vocabulary=VOCABULARY_NAME, skills=skills
    )


def render_targets_table(entries: Sequence[RegistryEntry]) -> str:
    """The block between the markers, markers included: the generated-by comment, then one
    row per entry in slug order, `-` for an absent value, and a Manifest that did not load a
    row of `-` with its problem in the Name cell."""
    lines = [TARGETS_OPEN, TARGETS_GENERATED, TABLE_HEADER, TABLE_RULE]
    lines += [_row(entry) for entry in sorted(entries, key=lambda entry: entry.slug)]
    lines.append(TARGETS_CLOSE)
    return "\n".join(lines)


def _row(entry: RegistryEntry) -> str:
    if entry.name is None:
        problem = "; ".join(entry.problems) or "the Manifest did not load"
        cells = [f"`{entry.slug}`", f"(problem: {problem})", *([NONE_SHOWN] * 6)]
    else:
        cells = [
            f"`{entry.slug}`",
            entry.name,
            _family_channel(entry),
            entry.environments[0] if entry.environments else NONE_SHOWN,
            ", ".join(entry.protected) or NONE_SHOWN,
            suites_shown(entry),
            entry.connector or NONE_SHOWN,
            entry.maintainer_notes or NONE_SHOWN,
        ]
    return "| " + " | ".join(_cell(cell) for cell in cells) + " |"


def _family_channel(entry: RegistryEntry) -> str:
    if entry.family and entry.channel:
        return f"{entry.family} / {entry.channel}"
    if entry.channel:
        return f"{NONE_SHOWN} / {entry.channel}"
    return entry.family or NONE_SHOWN


def _cell(text: str) -> str:
    """One cell on one line: a pipe would end the cell, a newline the row."""
    return " ".join(text.split()).replace("|", "\\|")


# --- reading the root once ---


@dataclass(frozen=True)
class _Page:
    """A page on disk as its bytes say: the text undecoded by universal newlines, its own
    line ending, and where the marked block is (None: a page without markers)."""

    text: str
    newline: str
    marked: tuple[int, int] | None

    @classmethod
    def read(cls, path: Path) -> _Page:
        text = path.read_bytes().decode("utf-8")
        return cls(text, "\r\n" if "\r\n" in text else "\n", _marked(text))

    def block(self) -> str | None:
        """The marked block with LF line endings, as `render_targets_table` renders it."""
        if self.marked is None:
            return None
        start, end = self.marked
        return self.text[start:end].replace("\r\n", "\n")

    def with_table(self, table: str) -> str:
        """The page with `table` in its marked block, or appended when it has none, in the
        page's own line ending; nothing else of the text changes."""
        table = table.replace("\n", self.newline)
        if self.marked is None:
            ending = "" if not self.text or self.text.endswith("\n") else self.newline
            return f"{self.text}{ending}{self.newline}{table}{self.newline}"
        start, end = self.marked
        return self.text[:start] + table + self.text[end:]


def _marked(text: str) -> tuple[int, int] | None:
    """The marked block's span, markers included: the last closing marker and the last
    opening marker before it; None when the page lacks either (decision 1, bytes)."""
    end = text.rfind(TARGETS_CLOSE)
    if end < 0:
        return None
    start = text.rfind(TARGETS_OPEN, 0, end)
    if start < 0:
        return None
    return start, end + len(TARGETS_CLOSE)


@dataclass(frozen=True)
class _State:
    """What `_inspect` read at a root: the Registry, whether skills are installed, and each
    orientation file (None when absent)."""

    workspace: Workspace
    entries: list[RegistryEntry]
    skills_installed: bool
    page: _Page | None
    imports: dict[str, str | None]

    @property
    def table(self) -> str:
        return render_targets_table(self.entries)


def _inspect(workspace: Workspace) -> _State:
    root = workspace.root
    page = root / ORIENTATION_NAME
    return _State(
        workspace=workspace,
        entries=[_rooted(entry, root) for entry in registry(workspace)],
        skills_installed=bool(installed_layouts(root)),
        page=_Page.read(page) if page.is_file() else None,
        imports={
            name: (root / name).read_bytes().decode("utf-8") if (root / name).is_file() else None
            for name, _, _ in IMPORTS
        },
    )


def _rooted(entry: RegistryEntry, root: Path) -> RegistryEntry:
    """The entry with its problems naming paths relative to the root: the page is committed
    and read in every clone, so no cell may carry one machine's absolute path."""
    prefixes = {f"{Path(root).resolve().as_posix()}/", f"{Path(root).as_posix()}/"}
    problems = list(entry.problems)
    for prefix in sorted(prefixes, key=len, reverse=True):
        problems = [problem.replace(prefix, "") for problem in problems]
    return entry.model_copy(update={"problems": problems})


# --- writing ---


@dataclass
class OrientationResult:
    """What `write_orientation` did, each path relative to the root, in the order the
    files are named: the page, the two imports, the vocabulary."""

    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    """`notice: …` lines: a page the table was appended to, an import file left without
    its line."""

    def lines(self) -> list[str]:
        """`wrote <file>` or `unchanged <file>`, one per file, in the order written."""
        return [
            f"{'wrote' if name in self.written else 'unchanged'} {name}"
            for name in _ORDER
            if name in self.written or name in self.unchanged
        ]

    def summary(self) -> str:
        """One line: `Orientation page: wrote …; unchanged …`, as `init --skills` prints."""
        said = [
            f"{verb} {', '.join(names)}"
            for verb, names in (("wrote", self.written), ("unchanged", self.unchanged))
            if names
        ]
        return "Orientation page: " + "; ".join(said)


_ORDER = (ORIENTATION_NAME, CLAUDE_IMPORT_NAME, GEMINI_IMPORT_NAME, VOCABULARY_NAME)

APPENDED_NOTICE = (
    f"notice: {ORIENTATION_NAME} exists and had no Targets table; the marked section was "
    "appended at its end (regenerate it with agentdiag registry --write)"
)
"""What `init` and `registry --write` say on stderr after appending to a user's page."""


def _import_notice(name: str, line: str, harness: str) -> str:
    return (
        f"notice: {name} exists and does not import {ORIENTATION_NAME}; add the line {line} "
        f"so {harness} reads the orientation page"
    )


def write_orientation(workspace: Workspace) -> OrientationResult:
    """Write the page, the two imports and the vocabulary under the Workspace root, by
    ADR-0016 §3's rules: nothing outside the marked block of an existing page is touched, an
    existing import file is never edited, and the vocabulary is always agentdiag's."""
    state = _inspect(workspace)
    root = workspace.root
    result = OrientationResult()

    page = root / ORIENTATION_NAME
    if state.page is None:
        _write(page, render_page(state.entries), root, result)
    else:
        _write(page, state.page.with_table(state.table), root, result)
        if state.page.marked is None:
            result.notices.append(APPENDED_NOTICE)

    for name, line, harness in IMPORTS:
        held = state.imports[name]
        if held is None:
            _write(root / name, f"{line}\n", root, result)
            continue
        result.unchanged.append(name)
        if line not in held:
            result.notices.append(_import_notice(name, line, harness))

    _write(workspace.vocabulary, vocabulary_text(), root, result)
    return result


def refresh_targets_table(workspace: Workspace) -> Path | None:
    """After a command wrote under `.agentdiag/targets/`: regenerate the marked block of a
    page that holds the markers, and say where when it changed. An absent page, or one
    without the markers, is left as it is: only `init` and `registry --write` write those."""
    state = _inspect(workspace)
    if state.page is None or state.page.marked is None:
        return None
    result = OrientationResult()
    page = workspace.root / ORIENTATION_NAME
    _write(page, state.page.with_table(state.table), workspace.root, result)
    return page if result.written else None


def _write(path: Path, text: str, root: Path, result: OrientationResult) -> None:
    """Write `text` unless the file already holds exactly it; record which, by the path
    relative to the root."""
    name = path.relative_to(root).as_posix()
    data = text.encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        result.unchanged.append(name)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    result.written.append(name)


# --- what validate says ---


def orientation_warnings(workspace: Workspace) -> list[str]:
    """The Workspace-level `warning: <file>: <message>` lines `validate` prints before a
    Target's (decision 3 as amended). A stale table is warned of everywhere: the block is
    agentdiag's and it is wrong. Everything else (a page without markers, an import without
    its line, an absent file) only where skills are installed."""
    state = _inspect(workspace)
    warnings: list[str] = []
    block = state.page.block() if state.page is not None else None
    if block is not None and block != state.table:
        warnings.append(
            f"{ORIENTATION_NAME}: the Targets table is stale; agentdiag registry --write "
            "regenerates it"
        )
    if not state.skills_installed:
        return [f"warning: {warning}" for warning in warnings]
    if state.page is None:
        warnings.append(_absent(ORIENTATION_NAME, "a coding agent finds no orientation page"))
    elif block is None:
        warnings.append(
            f"{ORIENTATION_NAME}: no Targets table between {TARGETS_OPEN} markers; "
            "agentdiag registry --write adds one"
        )
    for name, line, harness in IMPORTS:
        held = state.imports[name]
        if held is None:
            warnings.append(_absent(name, f"{harness} finds no orientation page"))
        elif line not in held:
            warnings.append(f"{name}: does not import {ORIENTATION_NAME} (add the line {line})")
    if not workspace.vocabulary.is_file():
        warnings.append(_absent(VOCABULARY_NAME, "a coding agent finds no vocabulary"))
    return [f"warning: {warning}" for warning in warnings]


def _absent(name: str, consequence: str) -> str:
    return f"{name}: absent; {consequence}; agentdiag registry --write writes one"


__all__ = [
    "CLAUDE_IMPORT_LINE",
    "CLAUDE_IMPORT_NAME",
    "GEMINI_IMPORT_LINE",
    "GEMINI_IMPORT_NAME",
    "ORIENTATION_NAME",
    "READ_SET_BUDGET_TOKENS",
    "TARGETS_CLOSE",
    "TARGETS_GENERATED",
    "TARGETS_OPEN",
    "VOCABULARY_NAME",
    "OrientationResult",
    "orientation_warnings",
    "refresh_targets_table",
    "render_page",
    "render_targets_table",
    "vocabulary_text",
    "write_orientation",
]
