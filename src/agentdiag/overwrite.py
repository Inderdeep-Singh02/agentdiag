"""When a command may overwrite a file a person may have edited (phase-6 decision 27,
amended after the ticket 11 reviews; shared with `generate`, ticket 12).

A file that exists is overwritten without `--force` only when its bytes would not change or
git vouches for it: tracked, and `git status --porcelain` empty for it, so what an overwrite
replaces is in a commit. Outside a git work tree, for an untracked or gitignored file, or
with no git at all, nothing records what would be lost, so the file is held back. Offline,
and `git` is asked with a timeout so a hung repository never hangs a command.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path

GIT_TIMEOUT_SECONDS = 10


def committed_unchanged(path: Path) -> bool:
    """Whether git vouches for `path`: tracked, and no change to it that a commit does not
    hold. False outside a git work tree, for an untracked or gitignored file, and with no
    git: then nothing records what an overwrite would lose."""
    tracked = _git(path, "ls-files", "--error-unmatch", "--", path.name)
    status = _git(path, "status", "--porcelain", "--", path.name)
    return tracked is not None and status is not None and not status.strip()


def held_back(files: Mapping[Path, str | bytes], *, force: bool) -> list[Path]:
    """The files of `files` (path to the content that would be written) that exist, would
    change, and git does not vouch for; none with `force`."""
    if force:
        return []
    held: list[Path] = []
    for path, content in files.items():
        if not path.is_file():
            continue
        wanted = content.encode("utf-8") if isinstance(content, str) else content
        if path.read_bytes() != wanted and not committed_unchanged(path):
            held.append(path)
    return held


def _git(path: Path, *arguments: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path.parent), *arguments],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


__all__ = ["GIT_TIMEOUT_SECONDS", "committed_unchanged", "held_back"]
