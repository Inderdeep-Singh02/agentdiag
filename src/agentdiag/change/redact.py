"""Redaction on write (ADR-0012 §4, phase-7 decision 6): customer identifiers never enter a
committed Change record.

`redact` replaces e-mail addresses with `[email]`, phone numbers (seven or more digits with
an optional `+`, spaces, dashes, dots and parentheses) with `[phone]`, and each name of the
Target's redaction list with `[name]`, case-insensitively and as whole words.

A phone-shaped run is split at every date it holds (`2026-09-15`, `20260915`): the date
stays, and each piece between dates is a phone number when it has seven digits or more, so
`Called on 2026-09-15 555 123 4567` keeps its date and loses its number. A run inside a word
or a number (a hash, a Run id, `$1.095.000`) is not a candidate at all. The seven-digit rule
is the contract's, and its false positive is accepted: any other number of seven digits or
more written with separators, an order number among them, is redacted too.

The redaction list is the Manifest's `redaction.names`; `redaction_names` is the one reader
of it (decision 6: nothing else reads the list).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from agentdiag.run.manifest import manifest_if_it_loads
from agentdiag.workspace import TargetPaths

EMAIL_SHOWN = "[email]"
PHONE_SHOWN = "[phone]"
NAME_SHOWN = "[name]"

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
PHONE = re.compile(r"(?<![\w.,$€£+])\+?\(?\d[\d \t().-]{5,}\d(?![\w])")
"""A phone-shaped candidate: digits and separators, not inside a word or a number."""

PHONE_DIGITS = 7
"""How many digits a piece needs to be a phone number (decision 6)."""

DATE = re.compile(r"(?<!\d)(?:\d{4}-[01]\d-[0-3]\d|(?:19|20)\d{2}[01]\d[0-3]\d)(?!\d)")
"""An ISO date or its compact form: kept as it is wherever a candidate holds one."""

LEADING = " \t.-)"
TRAILING = " \t.-(+"
"""What a piece between dates may begin or end with that belongs to neither phone nor date."""


def whole_word(text: str) -> re.Pattern[str]:
    """`text` as a case-insensitive whole-word pattern: a name to redact, or an
    environment name to find in a record."""
    return re.compile(r"(?<!\w)" + re.escape(text) + r"(?!\w)", re.IGNORECASE)


def redact(text: str, names: Sequence[str] = ()) -> str:
    """`text` with every e-mail address, phone number and listed name replaced."""
    text = EMAIL.sub(EMAIL_SHOWN, text)
    text = PHONE.sub(_phone, text)
    for name in sorted({name.strip() for name in names if name.strip()}, key=len, reverse=True):
        text = whole_word(name).sub(NAME_SHOWN, text)
    return text


def redaction_misses(text: str) -> list[str]:
    """The e-mail addresses and phone numbers still in `text`: what `validate` calls a
    redaction miss (decision 5), by the rules `redact` applies. Names are not checked: the
    list is the Target's to keep."""
    found = [match.group(0) for match in EMAIL.finditer(text)]
    found += [match.group(0) for match in PHONE.finditer(text) if _phone(match) != match.group(0)]
    return found


def redaction_names(target: TargetPaths) -> list[str]:
    """The names the Target's Manifest lists under `redaction.names`; none when the
    Manifest does not load or lists none."""
    manifest = manifest_if_it_loads(target)
    if manifest is None or manifest.redaction is None:
        return []
    return list(manifest.redaction.names)


def _phone(match: re.Match[str]) -> str:
    """The candidate with its dates kept and each piece between them judged alone."""
    candidate = match.group(0)
    pieces: list[str] = []
    last = 0
    for date in DATE.finditer(candidate):
        pieces += [_piece(candidate[last : date.start()]), date.group(0)]
        last = date.end()
    pieces.append(_piece(candidate[last:]))
    return "".join(pieces)


def _piece(piece: str) -> str:
    core = piece.lstrip(LEADING).rstrip(TRAILING)
    if sum(character.isdigit() for character in core) < PHONE_DIGITS:
        return piece
    start = piece.index(core)
    return piece[:start] + PHONE_SHOWN + piece[start + len(core) :]


__all__ = [
    "EMAIL_SHOWN",
    "NAME_SHOWN",
    "PHONE_SHOWN",
    "redact",
    "redaction_misses",
    "redaction_names",
    "whole_word",
]
