"""Reading a Trace back: whole, tailed while it is still being written, blobs resolved."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from agentdiag.trace.events import Event, from_line

BLOB_PREFIX = "sha256:"


def read_trace(path: Path) -> list[Event]:
    """Every Event in a Trace file, in the order it was written.

    Tolerant of unknown Event types and unknown fields: `Event` carries them through
    (ADR-0004 section 1). Blank lines are skipped; a partially written last line is not,
    so a torn file fails loudly rather than reading as a shorter Trace.
    """
    events: list[Event] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                events.append(from_line(line))
    return events


def follow(
    path: Path,
    *,
    poll_ms: int = 200,
    stop: Callable[[], bool] | None = None,
) -> Iterator[Event]:
    """Yield Events as they are appended, starting from the first.

    Stops after `trace/end`, or as soon as `stop()` returns True. Waits for the file to
    appear, so a follower may start before the Trial does.
    """
    path = Path(path)
    seconds = poll_ms / 1000
    offset = 0
    buffer = ""
    while True:
        if stop is not None and stop():
            return
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                handle.seek(offset)
                buffer += handle.read()
                offset = handle.tell()
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if not line.strip():
                    continue
                event = from_line(line)
                yield event
                if event.type == "trace/end":
                    return
        if stop is not None and stop():
            return
        time.sleep(seconds)


def resolve_blobs(events: Sequence[Event]) -> list[Event]:
    """Every `{"blob": "sha256:..."}` reference replaced by the content it names.

    A reference whose `blob` Event is not in `events` is left as it stands, so a
    truncated Trace still reads rather than raising.
    """
    contents = {
        f"{BLOB_PREFIX}{event.model_extra['sha256']}": event.model_extra["content"]
        for event in events
        if event.type == "blob" and event.model_extra
    }
    return [_resolve_event(event, contents) for event in events]


def _resolve_event(event: Event, contents: Mapping[str, str]) -> Event:
    extra = event.model_extra or {}
    resolved = {key: _resolve(value, contents) for key, value in extra.items()}
    if resolved == extra:
        return event
    return event.model_copy(update=resolved)


def _resolve(value: Any, contents: Mapping[str, str]) -> Any:
    if isinstance(value, Mapping):
        ref = value.get("blob")
        if len(value) == 1 and isinstance(ref, str) and ref in contents:
            return contents[ref]
        return {key: _resolve(item, contents) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, contents) for item in value]
    return value


__all__ = ["follow", "read_trace", "resolve_blobs"]
