"""`agentdiag init --skills`: install the packaged skills into a Workspace (ADR-0014 §3).

The skills are agentdiag's operating procedure for a coding agent — discovery (ticket 11),
generation (ticket 12) — and they ship inside the package, under `agentdiag/skills/<name>/`,
so the version that installs them is the version whose commands they call. `init --skills`
copies each to `<root>/.claude/skills/agentdiag-<name>/`, where Claude Code finds a
project's skills, and prefixes the name so an agentdiag skill never shadows one of the
repository's own.

**An installed skill is the author's once it is edited.** A skill whose files already match
the package's is left alone (the command is idempotent); one that differs is refused by
name unless `--force`, because a team that tuned a procedure to its repository would lose
that tuning to a reinstall nothing recorded. The package's skill directories are read
through `importlib.resources`, so an installed wheel serves them exactly as a checkout does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

SKILLS_PACKAGE = "agentdiag"
SKILLS_DIRNAME = "skills"
INSTALLED_PREFIX = "agentdiag-"
CLAUDE_SKILLS = Path(".claude") / "skills"
"""Where a project's skills live, under the Workspace root."""


class SkillsRefused(Exception):
    """An installed skill was edited and `--force` was not given; nothing was written."""


@dataclass
class SkillsResult:
    root: Path
    written: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)


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


def install_skills(root: Path, *, force: bool = False) -> SkillsResult:
    """Copy every packaged skill to `<root>/.claude/skills/agentdiag-<name>/`; refuse, before
    writing anything, when an installed file differs from the package's and not `force`."""
    root = Path(root)
    plan: dict[Path, bytes] = {}
    for name, skill in packaged_skills().items():
        directory = root / CLAUDE_SKILLS / f"{INSTALLED_PREFIX}{name}"
        plan.update({directory / path: data for path, data in _contents(skill).items()})
    edited = [path for path, data in plan.items() if path.is_file() and path.read_bytes() != data]
    if edited and not force:
        raise SkillsRefused(
            "these installed skill files were edited: "
            + ", ".join(str(path) for path in edited)
            + "; `agentdiag init --skills --force` replaces them with the packaged ones"
        )
    result = SkillsResult(root=root)
    for path, data in plan.items():
        if path.is_file() and path.read_bytes() == data:
            result.unchanged.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        result.written.append(path)
    return result


def render_installed(result: SkillsResult) -> str:
    """What `init --skills` prints: each file, written or unchanged, and how to invoke."""
    lines = [f"Installed the agentdiag skills under {result.root / CLAUDE_SKILLS}:"]
    for path in sorted([*result.written, *result.unchanged]):
        state = "wrote" if path in result.written else "unchanged"
        lines.append(f"  {state} {path.relative_to(result.root).as_posix()}")
    names = sorted(
        {
            path.relative_to(result.root / CLAUDE_SKILLS).parts[0]
            for path in [*result.written, *result.unchanged]
        }
    )
    lines += ["", "Invoke one in Claude Code by name: " + ", ".join(f"/{name}" for name in names)]
    return "\n".join(lines)


__all__ = [
    "CLAUDE_SKILLS",
    "INSTALLED_PREFIX",
    "SkillsRefused",
    "SkillsResult",
    "install_skills",
    "packaged_skills",
    "render_installed",
]
