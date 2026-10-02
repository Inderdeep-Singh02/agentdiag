"""`agentdiag init --skills`: install the packaged skills (ADR-0014 §3, ADR-0016 §6).

The skills are agentdiag's operating procedure for a coding agent — discovery (ticket 11),
generation (ticket 12) — and they ship inside the package, under `agentdiag/skills/<name>/`,
so the version that installs them is the version whose commands they call. Each is named
`agentdiag-<name>` so it never shadows one of the repository's own.

**One Tracked copy every Harness reads** (ADR-0016 §6, 0.1.2-interfaces decision 19).
`init --skills` writes each skill to `<root>/.agents/skills/agentdiag-<name>/`, which Codex
and Gemini CLI read directly, and makes `<root>/.claude/skills/agentdiag-<name>` a relative
symlink to it for Claude Code, one link per skill so a repository's own skills beside them
are untouched. An edit has one home; three copies would each go their own way and `validate`
would be warning about agentdiag's own output. Where the filesystem refuses the symlink, a
copy is written instead and `layout_warnings` says when it differs from the Tracked copy
(decision 21). A real directory under `.claude/skills/` equal to the package's is replaced by
the link.

**One classification.** Each Claude Code entry is read once into an `EntryState` (absent,
the link, a link elsewhere, a copy equal to the package's, an edited copy, a plain file),
and the installer, the warnings and the renderer all work from it (decision 19 as amended).

**An installed skill is the author's once it is edited.** A skill whose files already match
the package's is left alone (the command is idempotent); one that differs, in the Tracked
copy or in a Claude Code directory, is refused by name before anything is written unless
`--force`, because a team that tuned a procedure to its repository would lose that tuning
to a reinstall nothing recorded. Every packaged skill changed since 0.1.1, so a 0.1.1 install
is refused the same way and the refusal says it may be an earlier version's. A plain file
where the link belongs (a Windows checkout without `core.symlinks` turns a committed symlink
into one) and a link to anywhere else are refused too. The package's skill directories are
read through `importlib.resources`, so an installed wheel serves them exactly as a checkout
does.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from enum import Enum
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

SKILLS_PACKAGE = "agentdiag"
SKILLS_DIRNAME = "skills"
INSTALLED_PREFIX = "agentdiag-"
TRACKED_SKILLS = Path(".agents") / "skills"
"""The Tracked copy, under the Workspace root: Codex and Gemini CLI read it directly."""
CLAUDE_SKILLS = Path(".claude") / "skills"
"""Where Claude Code reads a project's skills, under the Workspace root: one link per skill
into the Tracked copy."""

LAYOUTS = {"agents": TRACKED_SKILLS, "claude": CLAUDE_SKILLS}
"""Each skills layout a Harness reads, by the name `installed_layouts` reports it under
(0.1.2-interfaces decision 19): `.agents/skills/` for Codex and Gemini CLI, `.claude/skills/`
for Claude Code."""

COPY_REASON = (
    "this filesystem refused a symlink; agentdiag validate warns when the copy differs from "
    "the tracked copy"
)
"""What `init --skills` says beside a Claude Code entry it had to copy (decision 20)."""


class SkillsRefused(Exception):
    """An installed skill differs from the package's, or something else holds a Claude Code
    entry, and `--force` was not given; nothing was written."""


class EntryState(Enum):
    """What one `.claude/skills/agentdiag-<name>` entry is, read once."""

    ABSENT = "absent"
    LINKED = "a link to the Tracked copy"
    LINK_ELSEWHERE = "a link to anywhere else"
    EQUAL_COPY = "a real directory equal to the reference"
    EDITED_COPY = "a real directory that differs from the reference"
    FILE = "a plain file"


class EntryOutcome(Enum):
    """What `install_skills` left at one Claude Code entry."""

    LINKED = "linked"
    RELINKED = "linked, replacing what was there"
    COPIED = "copied, the filesystem refusing the link"
    KEPT_LINK = "the link, already there"
    KEPT_COPY = "an equal copy, the filesystem still refusing the link"


@dataclass
class SkillsResult:
    """What `install_skills` did. `written` and `unchanged` are the Tracked copy's files;
    `entries` is each Claude Code entry's outcome, every path absolute under `root`."""

    root: Path
    written: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)
    entries: dict[Path, EntryOutcome] = field(default_factory=dict)


def packaged_skills() -> dict[str, Traversable]:
    """Every skill the package holds, by name: a directory under `agentdiag/skills/` with a
    `SKILL.md`."""
    base = files(SKILLS_PACKAGE).joinpath(SKILLS_DIRNAME)
    return {
        entry.name: entry
        for entry in sorted(base.iterdir(), key=lambda entry: entry.name)
        if entry.is_dir() and entry.joinpath("SKILL.md").is_file()
    }


def _contents(skill: Traversable, prefix: Path = Path()) -> dict[Path, bytes]:
    found: dict[Path, bytes] = {}
    for entry in skill.iterdir():
        if entry.name == "__pycache__":
            continue
        relative = prefix / entry.name
        if entry.is_dir():
            found.update(_contents(entry, relative))
        else:
            found[relative] = entry.read_bytes()
    return found


def packaged_contents(name: str) -> dict[Path, bytes]:
    """One packaged skill's files by their path inside it, `name` with or without the
    `agentdiag-` prefix."""
    return _contents(packaged_skills()[name.removeprefix(INSTALLED_PREFIX)])


def on_disk(directory: Path) -> dict[Path, bytes]:
    """An installed skill's files by their path inside `directory`, as `packaged_contents`
    gives the package's."""
    return {
        path.relative_to(directory): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.relative_to(directory).parts
    }


def link_target(installed: str) -> str:
    """The text of `.claude/skills/<installed>`'s symlink: relative, so it holds in every
    clone."""
    return (Path("..") / ".." / TRACKED_SKILLS / installed).as_posix()


def _exists(path: Path) -> bool:
    """Whether anything is at `path`, a dangling symlink included."""
    return path.is_symlink() or path.exists()


def _entry_state(entry: Path, reference: dict[Path, bytes]) -> EntryState:
    """Classify one Claude Code entry; a real directory is compared with `reference`, the
    files it should hold."""
    if entry.is_symlink():
        linked = Path(os.readlink(entry)).as_posix() == link_target(entry.name)
        return EntryState.LINKED if linked else EntryState.LINK_ELSEWHERE
    if entry.is_dir():
        equal = on_disk(entry) == reference
        return EntryState.EQUAL_COPY if equal else EntryState.EDITED_COPY
    return EntryState.FILE if entry.exists() else EntryState.ABSENT


def _refused(entry: Path, state: EntryState, reference: dict[Path, bytes]) -> list[str]:
    """How a refusal names one entry, from its state: an edited copy by the files that make
    it differ (or itself when files are only missing)."""
    if state is EntryState.LINK_ELSEWHERE:
        return [f"{entry} (a link to {os.readlink(entry)})"]
    if state is EntryState.FILE:
        return [f"{entry} (a file where the link to the tracked copy belongs)"]
    if state is EntryState.EDITED_COPY:
        held = on_disk(entry)
        named = [str(entry / path) for path in sorted(held) if reference.get(path) != held[path]]
        return named or [str(entry)]
    return []


def install_skills(root: Path, *, force: bool = False) -> SkillsResult:
    """Write every packaged skill to the Tracked copy and link each Claude Code entry to it
    (decision 19). Everything is planned first, the files to write and each entry's state;
    a refusal comes before any write, unless `force`."""
    root = Path(root)
    skills = {
        f"{INSTALLED_PREFIX}{name}": _contents(skill) for name, skill in packaged_skills().items()
    }
    result = SkillsResult(root=root)
    writes: dict[Path, bytes] = {}
    refused: list[str] = []
    for installed, contents in skills.items():
        for relative, data in contents.items():
            path = root / TRACKED_SKILLS / installed / relative
            if path.is_file() and path.read_bytes() == data:
                result.unchanged.append(path)
                continue
            writes[path] = data
            if path.is_file():
                refused.append(str(path))
    states = {
        installed: _entry_state(root / CLAUDE_SKILLS / installed, contents)
        for installed, contents in skills.items()
    }
    for installed, state in states.items():
        refused += _refused(root / CLAUDE_SKILLS / installed, state, skills[installed])
    if refused and not force:
        raise SkillsRefused(
            "these differ from this agentdiag's packaged skills (edited, or installed by an "
            f"earlier version): {', '.join(refused)}; agentdiag init --skills --force "
            "replaces them"
        )

    for path, data in writes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        result.written.append(path)
    for installed, contents in skills.items():
        entry = root / CLAUDE_SKILLS / installed
        result.entries[entry] = _place(root, installed, contents, states[installed])
    return result


def _place(
    root: Path, installed: str, contents: dict[Path, bytes], state: EntryState
) -> EntryOutcome:
    """Make one Claude Code entry the link, or the copy where the link is refused. The link
    is made beside the entry first and moved into place, so an entry whose filesystem
    refuses links is never removed for nothing: an equal copy stays."""
    if state is EntryState.LINKED:
        return EntryOutcome.KEPT_LINK
    entry = root / CLAUDE_SKILLS / installed
    entry.parent.mkdir(parents=True, exist_ok=True)
    trial = entry.with_name(f".{installed}.link")
    _remove(trial)
    try:
        os.symlink(link_target(installed), trial, target_is_directory=True)
    except (OSError, NotImplementedError):
        if state is EntryState.EQUAL_COPY:
            return EntryOutcome.KEPT_COPY
        _remove(entry)
        for path, data in contents.items():
            (entry / path).parent.mkdir(parents=True, exist_ok=True)
            (entry / path).write_bytes(data)
        return EntryOutcome.COPIED
    try:
        _remove(entry)
        os.replace(trial, entry)
    finally:
        if trial.is_symlink():
            trial.unlink()
    return EntryOutcome.LINKED if state is EntryState.ABSENT else EntryOutcome.RELINKED


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _entries(directory: Path) -> set[str]:
    if not directory.is_dir():
        return set()
    return {entry.name for entry in directory.iterdir() if entry.name.startswith(INSTALLED_PREFIX)}


def installed_layouts(root: Path) -> set[str]:
    """The layouts under `root` holding any `agentdiag-*` entry (a directory, a link or the
    file a checkout without symlinks leaves): whether agentdiag's skills are installed here,
    which is when `validate` warns of an absent Orientation page (ADR-0016 §3)."""
    return {name for name, directory in LAYOUTS.items() if _entries(Path(root) / directory)}


def layout_warnings(root: Path) -> list[str]:
    """`<file>: <message>` for each skill the two layouts disagree on (decision 21 as
    amended): one present in one layout only, naming the Harness that cannot find it; a link
    to anywhere else or a plain file where the link belongs, as `install_skills` refuses
    them; and a Claude Code copy that differs from the Tracked copy, naming which side was
    edited (each compared with the package) and the remedy that loses nothing. A link to the
    Tracked copy is never compared: it reads the Tracked copy itself."""
    root = Path(root)
    packaged = {f"{INSTALLED_PREFIX}{name}" for name in packaged_skills()}
    warnings: list[str] = []
    for installed in sorted(_entries(root / TRACKED_SKILLS) | _entries(root / CLAUDE_SKILLS)):
        tracked, entry = root / TRACKED_SKILLS / installed, root / CLAUDE_SKILLS / installed
        held = (TRACKED_SKILLS / installed).as_posix()
        name = (CLAUDE_SKILLS / installed).as_posix()
        tracked_files = on_disk(tracked) if tracked.is_dir() else None
        package = packaged_contents(installed) if installed in packaged else None
        state = _entry_state(entry, tracked_files if tracked_files is not None else {})
        if tracked_files is None:
            dangles = ", and Claude Code's link dangles" if state is EntryState.LINKED else ""
            warnings.append(
                f"{held}: absent; Codex and Gemini CLI find no such skill{dangles}; "
                "agentdiag init --skills writes the tracked copy"
            )
        if state is EntryState.ABSENT:
            warnings.append(
                f"{name}: absent; Claude Code finds no such skill; agentdiag init --skills links it"
            )
        elif state is EntryState.LINK_ELSEWHERE:
            warnings.append(
                f"{name}: a link to {os.readlink(entry)}, not to the tracked copy; Claude Code "
                "reads another skill under this name; agentdiag init --skills --force links it"
            )
        elif state is EntryState.FILE:
            warnings.append(
                f"{name}: a file, not a link or a copy (a checkout without core.symlinks); "
                "Claude Code finds no such skill; agentdiag init --skills --force replaces it"
            )
        elif state is EntryState.EDITED_COPY and tracked_files is not None:
            edited = _edited_side(on_disk(entry), tracked_files, held, package)
            warnings.append(f"{name}: differs from {held}{edited}")
    return warnings


def _edited_side(
    copy: dict[Path, bytes],
    tracked: dict[Path, bytes],
    held: str,
    package: dict[Path, bytes] | None,
) -> str:
    """The rest of a `differs from` warning: which side differs from the package, and the
    remedy that loses neither side's edit. A skill the package does not ship has no third
    side to tell them apart, so both count as edited."""
    copy_edited = package is None or copy != package
    tracked_edited = package is None or tracked != package
    if tracked_edited and not copy_edited:
        return (
            f", whose edit this copy lacks (this filesystem refuses symlinks); copy {held} over it"
        )
    if copy_edited and not tracked_edited:
        return (
            f"; the copy was edited; move the edit into {held}, or agentdiag init --skills "
            "--force restores it"
        )
    return f"; both the copy and {held} were edited; reconcile them by hand into {held}"


def render_installed(result: SkillsResult, orientation: str | None = None) -> str:
    """What `init --skills` prints (decision 20 as amended): each file of the Tracked copy,
    written or unchanged; where each Harness finds the skills, one line per Claude Code
    entry from its outcome; the Orientation page's one line when `orientation` gives it;
    and how to invoke."""
    root = result.root
    lines = [f"Installed the agentdiag skills under {root / TRACKED_SKILLS} (the tracked copy):"]
    for path in sorted([*result.written, *result.unchanged]):
        state = "wrote" if path in result.written else "unchanged"
        lines.append(f"  {state} {path.relative_to(root).as_posix()}")
    lines += [
        "Where each Harness finds them:",
        f"  Codex and Gemini CLI read {TRACKED_SKILLS.as_posix()}/ directly.",
        f"  Claude Code reads {CLAUDE_SKILLS.as_posix()}/:",
    ]
    said = {
        EntryOutcome.LINKED: "linked {name} -> {target}",
        EntryOutcome.RELINKED: "linked {name} -> {target} (replaced what was there)",
        EntryOutcome.COPIED: f"copied {{name}} ({COPY_REASON})",
        EntryOutcome.KEPT_LINK: "unchanged {name} -> {target}",
        EntryOutcome.KEPT_COPY: f"unchanged {{name}} (a copy; {COPY_REASON})",
    }
    entries = sorted(result.entries)
    for entry in entries:
        text = said[result.entries[entry]]
        name = entry.relative_to(root).as_posix()
        lines.append("    " + text.format(name=name, target=link_target(entry.name)))
    if orientation is not None:
        lines.append(orientation)
    names = ", ".join(f"/{entry.name}" for entry in entries)
    lines += ["", f"Invoke one by name (in Claude Code: {names})"]
    return "\n".join(lines)


__all__ = [
    "CLAUDE_SKILLS",
    "COPY_REASON",
    "INSTALLED_PREFIX",
    "LAYOUTS",
    "TRACKED_SKILLS",
    "EntryOutcome",
    "EntryState",
    "SkillsRefused",
    "SkillsResult",
    "install_skills",
    "installed_layouts",
    "layout_warnings",
    "link_target",
    "on_disk",
    "packaged_contents",
    "packaged_skills",
    "render_installed",
]
