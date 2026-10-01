"""Trace Importers: Evidence rows a Connector reads become Traces (ticket 26, ADR-0013 §5).

Three Importers, one per Evidence store kind: proxy rows (`reconstructed`), conversation
records and voice conversations (both `observed`). Each is pure and offline at import time,
so `agentdiag.importer` loads no SDK; the `import` command (`importer.command`, imported only
by the CLI) reads the rows, runs one, and writes the Run.
"""

from __future__ import annotations

from collections.abc import Mapping

from agentdiag.importer.base import Imported, Importer, ImportRowsError, Lacked
from agentdiag.importer.conversation import ConversationRecordImporter
from agentdiag.importer.proxy_rows import ProxyRowImporter
from agentdiag.importer.voice import VoiceConversationImporter
from agentdiag.types import EvidenceKind, ToolKind

IMPORTER_CLASSES: Mapping[
    EvidenceKind,
    type[ProxyRowImporter] | type[ConversationRecordImporter] | type[VoiceConversationImporter],
] = {
    "proxy": ProxyRowImporter,
    "conversation": ConversationRecordImporter,
    "voice": VoiceConversationImporter,
}
"""The Importer of each Evidence store kind (decision 32); `flows` rides with proxy rows."""

IMPORTERS: Mapping[EvidenceKind, Importer] = {
    kind: importer() for kind, importer in IMPORTER_CLASSES.items()
}
"""One Importer per kind, with no Manifest tool kinds."""


def importer_for(kind: EvidenceKind, *, tool_kinds: Mapping[str, ToolKind]) -> Importer:
    """The Importer of `kind` for a Target whose Manifest gives its tools these kinds, so a
    `retrieval` tool's imported calls are `retrieval` Spans, as the Adapter records them.
    `KeyError` for a kind no Importer reads."""
    return IMPORTER_CLASSES[kind](tool_kinds=tool_kinds)


__all__ = [
    "IMPORTERS",
    "IMPORTER_CLASSES",
    "ConversationRecordImporter",
    "ImportRowsError",
    "Imported",
    "Importer",
    "Lacked",
    "ProxyRowImporter",
    "VoiceConversationImporter",
    "importer_for",
]
