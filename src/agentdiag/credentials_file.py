"""The one credentials file, outside every repository, read once at start-up (phase-8
decision 4).

A Target's tokens and connection strings are named in its Manifest by environment variable
(ADR-0011 §2) and read from the process environment. They reach the process from
`~/.agentdiag/env` (`$AGENTDIAG_ENV_FILE` names another path): not a Workspace file, because
a Workspace is committed and a credential must never be one `git add -A` from a commit; not
a Target repository's `.env`, which agentdiag never writes. The credentials wizard writes
the file, mode `0600`, and nothing else does.

The rules, which `load_credentials_file` applies:

- **The file is optional.** No file: nothing is loaded, `Loaded(path=None)`.
- **`KEY=value` lines**, `#` comments and blank lines, an optional `export ` prefix, and
  single or double quotes around a value stripped.
- **The process wins.** A variable already set is never overridden; it is listed as
  `skipped`, so a shell's own export always decides. One set but empty is unset, as every
  credential read treats it: the file's value is used, and the warning says so.
- **Said, never shown.** A file readable by group or others is still loaded, with a warning
  naming its mode; a malformed line is a warning naming its number, never its text (it
  may hold a value).

The CLI's `main` callback calls it once, so every command — `serve` among them — sees the
variables; nothing else reads the file. Standard library only, offline.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import MutableMapping
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_PATH = Path.home() / ".agentdiag" / "env"
"""Where the credentials file lives unless `$AGENTDIAG_ENV_FILE` names another path."""

ENV_PATH_VARIABLE = "AGENTDIAG_ENV_FILE"
"""The variable that overrides `DEFAULT_PATH`."""

KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
"""What a variable name may be."""

EXPORT = "export "


class Loaded(BaseModel):
    """What one read of the credentials file did: names only, never a value."""

    path: Path | None = None
    """The file read; None when there was none."""

    loaded: list[str] = Field(default_factory=list)
    """The variables set from the file, in its order."""

    skipped: list[str] = Field(default_factory=list)
    """The variables the file names that the process had set already."""

    warning: str | None = None
    """What a reader should know: a mode others can read, malformed lines, by number."""


def credentials_path(environ: MutableMapping[str, str]) -> Path:
    """The file `load_credentials_file` reads: `$AGENTDIAG_ENV_FILE`, else `DEFAULT_PATH`."""
    override = environ.get(ENV_PATH_VARIABLE)
    return Path(override).expanduser() if override else DEFAULT_PATH


def load_credentials_file(
    environ: MutableMapping[str, str] = os.environ, path: Path | None = None
) -> Loaded:
    """Set every variable the credentials file names that `environ` has not (decision 4)."""
    path = path if path is not None else credentials_path(environ)
    try:
        text = path.read_text(encoding="utf-8")
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return Loaded(path=None)
    except (OSError, UnicodeDecodeError) as exc:
        return Loaded(path=path, warning=f"{path} could not be read: {type(exc).__name__}")
    loaded: list[str] = []
    skipped: list[str] = []
    malformed: list[int] = []
    emptied: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        try:
            entry = _entry(line)
        except ValueError:
            malformed.append(number)
            continue
        if entry is None:
            continue
        key, value = entry
        if environ.get(key):
            skipped.append(key)
            continue
        if key in environ:
            # Set but empty is unset to every credential read (ADR-0011 §2), so the file's
            # value is used, and the reader is told the shell's was empty.
            emptied.append(key)
        environ[key] = value
        loaded.append(key)
    notes: list[str] = []
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        notes.append(f"{path} is mode {mode:04o}, which lets others read it; `chmod 600 {path}`")
    if emptied:
        notes.append(
            f"{', '.join(emptied)} set but empty in the process; the value in {path} was used"
        )
    if malformed:
        lines = ", ".join(str(number) for number in malformed)
        notes.append(f"{path}: line {lines} is not KEY=value and was ignored")
    return Loaded(path=path, loaded=loaded, skipped=skipped, warning="; ".join(notes) or None)


def _entry(line: str) -> tuple[str, str] | None:
    """A line read: (key, value), None for a blank or comment line; `ValueError` when it is
    neither (the message names nothing of the line, which may hold a value)."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith(EXPORT):
        stripped = stripped[len(EXPORT) :].lstrip()
    key, equals, value = stripped.partition("=")
    key = key.strip()
    if not equals or not KEY.match(key):
        raise ValueError("not KEY=value")
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


__all__ = [
    "DEFAULT_PATH",
    "ENV_PATH_VARIABLE",
    "Loaded",
    "credentials_path",
    "load_credentials_file",
]
