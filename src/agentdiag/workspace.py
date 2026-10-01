"""The Workspace: one root holding many Targets, and the one module that knows its layout.

ADR-0013 §1 widens ADR-0005 §1's one `.agentdiag/` per Target into a Workspace: a root whose
`.agentdiag/targets/<slug>/` holds each Target's Manifest, Fingerprint, Suites, Calibration
Notes, Change records and Runs (phase-6 decision 1). Every other module asks this one where
a Target's files are, through `TargetPaths`, so the layout is written down once and moving a
directory is a change to one file.

**One spelling.** The Phase 4 layout — a Manifest directly under `.agentdiag/` — was read
as the Target `default` until ticket 13 moved the example under `targets/` and dropped that
reader (phase-6 decision 1's expand-contract, contracted). A root still spelled that way is
refused by name, with the move that fixes it, rather than read as a Workspace holding no
Target: an author whose Manifest seems to vanish would otherwise be told to `init` over it.

**The root is the user's** (ADR-0013 §2): given by `--root`, or found by walking up from the
current directory to the nearest `.agentdiag/`. agentdiag writes only under it.

Imports nothing beyond the standard library: `show`, `validate` and `list` resolve a
Workspace, and they are offline commands.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

AGENTDIAG_DIR = ".agentdiag"
"""The directory under a Workspace root that holds everything agentdiag reads and writes."""

TARGETS_DIRNAME = "targets"
"""Under `.agentdiag/`, one Target directory per Target, named by its slug."""

DEFAULT_SLUG = "default"
"""The Target `init` creates when no `--target` names one."""

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
"""What a Target's slug may be: what `init.slug()` produces from any text. A directory name
a shell, a URL and a `--target` all carry unchanged."""

MANIFEST_NAME = "manifest.yaml"
FINGERPRINT_NAME = "fingerprint.json"
JUDGE_NOTES_NAME = "judge_notes.md"
RUNS_DIRNAME = "runs"
SYNC_BREAKS_DIRNAME = "sync-breaks"
CHANGES_DIRNAME = "changes"
PUSHES_DIRNAME = "pushes"
RESTORE_POINTS_DIRNAME = "restore-points"
"""What a Target directory holds (decision 1), by name. `runs/` and `restore-points/` are
output and gitignored; everything else is committed (ADR-0013 §1)."""

SUITES_DIRNAME = "suites"

INDEX_FILE = "index.sqlite"
"""The derived Index, one per Workspace (ADR-0005 §5): never inside a Target, never the
truth."""


class WorkspaceError(ValueError):
    """A root that is not one Workspace: none found, or one in the Phase 4 spelling."""


class WorkspaceNotFound(WorkspaceError):
    """No `.agentdiag/` at the root named, or at or above the current directory. The one
    Workspace error a command given a Run *path* can do without."""


class TargetNotFound(WorkspaceError):
    """No Target by that slug, or none at all; the message names the slugs that exist."""


class NotASlug(WorkspaceError):
    """A `--target` that is not a slug: named, with what a slug is."""


def require_slug(slug: str) -> str:
    """`slug`, when it is one; `NotASlug` otherwise."""
    if not SLUG.match(slug):
        raise NotASlug(
            f"--target {slug!r} is not a slug: lowercase letters, digits and hyphens, "
            "starting with a letter or a digit, as in --target order-desk"
        )
    return slug


class TargetAmbiguous(WorkspaceError):
    """Several Targets and no `--target`; the message names them, so the fix is typed."""


@dataclass(frozen=True)
class TargetPaths:
    """Where one Target's files live: its Target directory and what is under it. Every
    relative path a Manifest names (`suites`, `judge_notes`, ...) is relative to
    `directory`."""

    slug: str
    directory: Path

    @property
    def root(self) -> Path:
        """The Workspace root this Target belongs to: where the Index and the gitignore
        live. Derived here, from the layout this module alone knows."""
        return self.directory.parent.parent.parent

    @property
    def manifest(self) -> Path:
        return self.directory / MANIFEST_NAME

    @property
    def fingerprint(self) -> Path:
        return self.directory / FINGERPRINT_NAME

    @property
    def judge_notes(self) -> Path:
        """Where `init` puts the Calibration Notes; the Manifest's `judge_notes` names them."""
        return self.directory / JUDGE_NOTES_NAME

    @property
    def runs(self) -> Path:
        return self.directory / RUNS_DIRNAME

    @property
    def sync_breaks(self) -> Path:
        return self.directory / SYNC_BREAKS_DIRNAME

    @property
    def changes(self) -> Path:
        return self.directory / CHANGES_DIRNAME

    @property
    def pushes(self) -> Path:
        return self.directory / PUSHES_DIRNAME

    @property
    def restore_points(self) -> Path:
        return self.directory / RESTORE_POINTS_DIRNAME

    def relative(self, reference: str) -> Path:
        """A path the Manifest names, resolved against the Target directory."""
        return self.directory / reference

    def relative_of(self, path: Path) -> str:
        """`path` as a reader of the Workspace is shown it: relative to the Workspace root in
        forward slashes, or as given when it lies outside the root."""
        for candidate, base in ((path, self.root), (path.resolve(), self.root.resolve())):
            try:
                return candidate.relative_to(base).as_posix()
            except ValueError:
                continue
        return path.as_posix()


@dataclass(frozen=True)
class Workspace:
    """One root and the Targets under its `.agentdiag/`."""

    root: Path

    @classmethod
    def find(cls, start: Path | None) -> Workspace:
        """The Workspace at `start`, which must hold `.agentdiag/`; with None, the nearest
        one found walking up from the current directory. `WorkspaceError` when there is
        none, or when the one found is in the Phase 4 spelling."""
        if start is not None:
            root = Path(start)
            if not (root / AGENTDIAG_DIR).is_dir():
                raise WorkspaceNotFound(
                    f"no Workspace at {root}: it holds no {AGENTDIAG_DIR}/; "
                    f"`agentdiag init --root {root}` makes one"
                )
            return cls._checked(root)
        here = Path.cwd()
        for candidate in (here, *here.parents):
            if (candidate / AGENTDIAG_DIR).is_dir():
                return cls._checked(candidate)
        raise WorkspaceNotFound(
            f"no Workspace found: no {AGENTDIAG_DIR}/ in {here} or any directory above it; "
            "name one with --root, or `agentdiag init` makes one here"
        )

    @classmethod
    def holding(cls, path: Path) -> Workspace | None:
        """The nearest Workspace at or above `path`, unchecked; None when no directory on
        the way up holds `.agentdiag/` (a Run directory copied out of every Workspace)."""
        start = Path(path).resolve()
        for candidate in (start, *start.parents):
            if (candidate / AGENTDIAG_DIR).is_dir():
                return cls(candidate)
        return None

    @classmethod
    def at(cls, root: Path) -> Workspace:
        """The Workspace `root` is or will be, unchecked: `init`'s, which creates it."""
        return cls(Path(root))

    @classmethod
    def _checked(cls, root: Path) -> Workspace:
        workspace = cls(root)
        workspace.targets()  # a root in the Phase 4 spelling is refused once, here
        return workspace

    @property
    def agentdiag_dir(self) -> Path:
        return self.root / AGENTDIAG_DIR

    @property
    def targets_dir(self) -> Path:
        return self.agentdiag_dir / TARGETS_DIRNAME

    @property
    def index(self) -> Path:
        return self.agentdiag_dir / INDEX_FILE

    def targets(self) -> list[TargetPaths]:
        """Every Target, by slug.

        A Manifest directly under `.agentdiag/` (the Phase 4 spelling), or a directory under
        `targets/` that is not a slug, is a `WorkspaceError` naming the path and the fix:
        nothing here guesses what was meant.
        """
        stray = self.agentdiag_dir / MANIFEST_NAME
        if stray.is_file():
            raise WorkspaceError(
                f"{stray} is a Manifest in the Phase 4 spelling, which agentdiag no longer "
                f"reads; move it, and the suites/, judge_notes.md and runs/ beside it, into "
                f"{self.targets_dir / DEFAULT_SLUG} (or a Target directory of another slug)"
            )
        if not self.targets_dir.is_dir():
            return []
        found: list[TargetPaths] = []
        for child in sorted(self.targets_dir.iterdir()):
            if not child.is_dir():
                continue
            if not SLUG.match(child.name):
                raise WorkspaceError(
                    f"{child} is not a Target directory: a slug is lowercase letters, digits "
                    "and hyphens, starting with a letter or a digit; rename it or move it out "
                    f"of {self.targets_dir}"
                )
            found.append(TargetPaths(child.name, child))
        return found

    def resolve(self, slug: str | None) -> TargetPaths:
        """The Target `slug` names; with None, the one Target, or `TargetAmbiguous`."""
        targets = self.targets()
        slugs = ", ".join(target.slug for target in targets)
        if slug is None:
            if len(targets) == 1:
                return targets[0]
            if not targets:
                raise TargetNotFound(
                    f"the Workspace at {self.root} holds no Target yet; "
                    f"`agentdiag init --root {self.root}` adds one"
                )
            raise TargetAmbiguous(
                f"the Workspace at {self.root} holds {len(targets)} Targets ({slugs}); "
                "name one with --target"
            )
        for target in targets:
            if target.slug == slug:
                return target
        raise TargetNotFound(
            f"no Target {slug!r} in the Workspace at {self.root}; "
            + (f"it holds {slugs}" if targets else "it holds none yet")
        )

    def new_target(self, slug: str) -> TargetPaths:
        """Where a Target of this slug, not yet in the Workspace, is created: `init --target`
        and `discover --target` both ask here. `NotASlug` for a slug that is not one."""
        require_slug(slug)
        return self.paths_for(slug)

    def resolve_or_new(self, slug: str | None) -> TargetPaths:
        """The Target `slug` names, or where a new one of that slug goes (`new_target`);
        with None, the one Target, `TargetAmbiguous` among several, or a new `default` in
        a Workspace that holds none. For the commands that make a Target's first files."""
        targets = self.targets()
        if slug is None:
            return self.resolve(None) if targets else self.new_target(DEFAULT_SLUG)
        known = next((target for target in targets if target.slug == slug), None)
        return known if known is not None else self.new_target(slug)

    def paths_for(self, slug: str) -> TargetPaths:
        """Where a Target of this slug lives, whether or not it exists yet: what `init
        --target` writes into."""
        return TargetPaths(slug, self.targets_dir / slug)


__all__ = [
    "AGENTDIAG_DIR",
    "CHANGES_DIRNAME",
    "DEFAULT_SLUG",
    "FINGERPRINT_NAME",
    "INDEX_FILE",
    "JUDGE_NOTES_NAME",
    "MANIFEST_NAME",
    "PUSHES_DIRNAME",
    "RESTORE_POINTS_DIRNAME",
    "RUNS_DIRNAME",
    "SLUG",
    "SUITES_DIRNAME",
    "SYNC_BREAKS_DIRNAME",
    "TARGETS_DIRNAME",
    "NotASlug",
    "TargetAmbiguous",
    "TargetNotFound",
    "TargetPaths",
    "Workspace",
    "WorkspaceError",
    "WorkspaceNotFound",
    "require_slug",
]
