"""The padded table every listing prints: `list`, `registry`, `change list` and `dashboard`.

Plain text padded to each column's widest cell, two spaces between columns and nothing
trailing (phase-5 decision 64 as amended), not a Rich table: a Rich table fits itself to the
terminal and wraps a long cell, while these lines are the same bytes at any width, so the
README can paste them and a test can compare them.
"""

from __future__ import annotations

from collections.abc import Sequence


def render_table(rows: Sequence[Sequence[str]]) -> list[str]:
    """The lines of `rows`, the header first, each cell padded to its column's widest."""
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    return [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in rows
    ]


__all__ = ["render_table"]
