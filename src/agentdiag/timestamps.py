"""The one timestamp spelling agentdiag writes: ISO-8601 in UTC, to the second.

`run.json`, `fingerprint.json`, a Sync break and a Connector's read all say when something
happened in this spelling. It lives here, offline, rather than in `run.record`, because that
module reaches the model client and `sync` and the Connector must not (phase-6 decision 18);
`run.record.iso_utc` is this function, re-exported.
"""

from __future__ import annotations

from datetime import UTC, datetime


def iso_utc(moment: datetime) -> str:
    """The one timestamp spelling `run.json` uses."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> str:
    """This moment, spelled by `iso_utc`."""
    return iso_utc(datetime.now(UTC))


__all__ = ["iso_utc", "now_utc"]
