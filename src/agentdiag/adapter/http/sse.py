"""Server-sent events read from a byte stream: the line rules, and nothing of any platform
(phase-8 decision 5).

`read_sse` turns the chunks a connection delivers into `SseEvent`s by the WHATWG
event-stream rules: lines end in `\\r\\n`, `\\n` or `\\r` (a `\\r` at a chunk's end waits for
the next chunk, in case a `\\n` follows); a line starting with `:` is a comment; `field:
value` drops one space after the colon; `data` lines join with `\\n`; `event` names the
event (`message` when none does); `id` sets the last event id; a blank line dispatches the
event when it holds data. At the end of the stream an event still holding data is
dispatched, since a server that closes without a trailing blank line meant it.

Transport-level: the `sse-json` Dialect reads its JSON frames from the `data` of these
events, and any Dialect over SSE (ticket 30's) reuses this. Bytes that are not UTF-8 raise
`UnicodeDecodeError`, which a Dialect turns into its `DialectError`.
"""

from __future__ import annotations

import codecs
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

DEFAULT_EVENT = "message"


@dataclass(frozen=True)
class SseEvent:
    """One dispatched event."""

    data: str
    event: str = DEFAULT_EVENT
    id: str | None = None


class _Reader:
    """The event being built, and the last event id."""

    def __init__(self) -> None:
        self.data: list[str] = []
        self.event = ""
        self.last_id: str | None = None

    def line(self, line: str) -> SseEvent | None:
        if line == "":
            return self.dispatch()
        if line.startswith(":"):
            return None
        field, colon, value = line.partition(":")
        if colon and value.startswith(" "):
            value = value[1:]
        if field == "data":
            self.data.append(value)
        elif field == "event":
            self.event = value
        elif field == "id" and "\0" not in value:
            self.last_id = value
        return None

    def dispatch(self) -> SseEvent | None:
        if not self.data:
            self.event = ""
            return None
        event = SseEvent(
            data="\n".join(self.data), event=self.event or DEFAULT_EVENT, id=self.last_id
        )
        self.data, self.event = [], ""
        return event


def read_sse(chunks: Iterable[bytes]) -> Iterator[SseEvent]:
    """Every event the byte stream carries, as each is dispatched."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    reader = _Reader()
    pending = ""
    for chunk in chunks:
        pending += decoder.decode(chunk)
        lines, pending = _complete_lines(pending)
        for line in lines:
            event = reader.line(line)
            if event is not None:
                yield event
    pending += decoder.decode(b"", final=True)
    for line in [*_complete_lines(pending + "\n")[0]]:
        event = reader.line(line)
        if event is not None:
            yield event
    event = reader.dispatch()
    if event is not None:
        yield event


def _complete_lines(text: str) -> tuple[list[str], str]:
    """The complete lines of `text`, and what is left after the last line end. A `\\r` at
    the very end is left pending: the `\\n` of a `\\r\\n` may be in the next chunk."""
    lines: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\n":
            lines.append(text[start:index])
            start = index + 1
        elif char == "\r":
            if index + 1 == len(text):
                break
            lines.append(text[start:index])
            if text[index + 1] == "\n":
                index += 1
            start = index + 1
        index += 1
    return lines, text[start:]


__all__ = ["DEFAULT_EVENT", "SseEvent", "read_sse"]
