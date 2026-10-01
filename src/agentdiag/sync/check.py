"""`agentdiag sync` and `sync --check`, and the Sync a Run takes before its first Trial.

Two callers ask one question — how does this Target compare with its last Fingerprint —
and do different things with the answer, so the question is asked here once:

- **`sync`** builds a Fingerprint from what it gathered (D where it is known, else L),
  prints the section table against the previous one (every section `added` when there is
  none) and writes `fingerprint.json`, exit 0 (phase-6 decision 13) — unless no section was
  covered, when it writes nothing and exits 3 (walkthrough friction 5).
- **`sync --check`** compares and writes nothing: exit 0 on `held`, 2 on `broken` (the
  Verdict-shaped "the Target moved" signal, D32), 3 on `not_checked`, with the reason and
  what to run.
- **A Run** (decision 14, D30, ADR-0008) asks `check_target` with its own Adapter's probe,
  before any Run directory exists, and decides from the result: proceed on `held`, rebuild
  on `broken` unless `--no-resync` or `--strict` says otherwise.

**D comes from the Connector first** (decision 25): when the Manifest names one and its
block names the environment, its read is the deployed side and no probe is made; else the
Adapter's probe. A Connector read that fails — an unknown kind, a missing credential, a
platform that refused — is `sync`'s exit 3 naming it, because a Sync that guessed would be
worse than none; a Run instead falls back to the probe and warns (`run.preflight`), so a
missing credential never blocks a local Run.

**Both forms record a Sync break on `broken`** (decision 26, `sync.breaks`), unless a break
already open names the same sections and directions, which is said on stderr instead. The
break `sync` records is closed by the Fingerprint it writes unless re-recording could not
settle it (a rebuild to the same id). A Run never records one.

Everything this writes is under the Target directory, and nothing it does converses with
the Target (decision 18): the Connector reads, and the probe is answered in-process.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from agentdiag.exits import USAGE_EXIT
from agentdiag.run.manifest import Manifest, ManifestError, ManifestNotFound, load_manifest
from agentdiag.sync.breaks import record_break
from agentdiag.sync.compare import (
    SyncResult,
    compare_sections,
    render_sync,
    sync_result,
)
from agentdiag.sync.fingerprint import (
    Fingerprint,
    FingerprintError,
    load_fingerprint,
    short,
    write_fingerprint,
)
from agentdiag.sync.observe import (
    DeployedSide,
    Gathered,
    build_fingerprint,
    deployed_side,
    gather,
    withhold_connector_sections,
)
from agentdiag.timestamps import now_utc
from agentdiag.workspace import TargetPaths

BROKEN_EXIT = 2
"""`sync --check` on `broken`: the Target moved, a re-run signal (D32, decision 43)."""


class SyncCheck(BaseModel):
    """One comparison, with what it was built from: the result, the record it compared
    against, and what was gathered (from which a Fingerprint can be built)."""

    model_config = {"arbitrary_types_allowed": True}

    result: SyncResult
    recorded: Fingerprint | None = None
    gathered: Gathered


class SyncExit(BaseModel):
    """What `agentdiag sync` prints and exits with."""

    code: int
    message: str
    result: SyncResult | None = None
    sync_break: Path | None = None
    """The Sync break this comparison recorded, when it was `broken` and no open break
    already named the same sections and directions."""

    notice: str | None = None
    """What goes to stderr beside the message: the open break a comparison repeated."""


def check_target(
    target: TargetPaths,
    manifest: Manifest,
    environment: str,
    side: DeployedSide,
) -> SyncCheck:
    """Gather L and D, read R, and compare (decision 12). `FingerprintError` when the
    Target's `fingerprint.json` cannot be read: a Sync that guessed would be worse than none.
    After a failed Connector read, what only the Connector covered is withheld as
    `not_covered` rather than compared (`withhold_connector_sections`)."""
    gathered = gather(target, manifest, environment, side=side)
    recorded = load_fingerprint(target)
    if side.connector_failed is not None:
        gathered = withhold_connector_sections(gathered, recorded, side.connector_failed)
    sections = compare_sections(
        gathered.local,
        gathered.deployed,
        recorded,
        deployed_by=gathered.deployed_by,
        not_covered=gathered.not_covered,
    )
    return SyncCheck(
        result=sync_result(sections, recorded, environment), recorded=recorded, gathered=gathered
    )


def rebuilt(check: SyncCheck, *, built_at: str, resynced: bool) -> Fingerprint:
    """The Fingerprint of what `check` gathered; `resynced` names the previous one."""
    previous = check.recorded.id if check.recorded is not None and resynced else None
    return build_fingerprint(check.gathered, built_at=built_at, resynced_from=previous)


def sync_target(
    target: TargetPaths,
    *,
    environment: str | None = None,
    check_only: bool = False,
    json_output: bool = False,
    built_at: str | None = None,
    record_breaks: bool = True,
) -> SyncExit:
    """`agentdiag sync [--env] [--check] [--json]` for one Target (decision 13).

    `record_breaks=False` compares and writes nothing at all, not even a Sync break: the
    UI's Sync screen reads with it, since a page that is only looked at must not write
    (ticket 16's fix round). It applies with `check_only` only; `sync --check` records."""
    if not check_only:
        record_breaks = True
    try:
        manifest = load_manifest(target)
    except ManifestNotFound as exc:
        result = SyncResult(status="not_checked", reason="no_manifest")
        return SyncExit(
            code=USAGE_EXIT, message=f"error: {exc}; `agentdiag init` writes one", result=result
        )
    except ManifestError as exc:
        return SyncExit(code=USAGE_EXIT, message=f"error: {exc}")
    try:
        chosen = environment or manifest.adapter.default_environment
    except ManifestError as exc:
        return SyncExit(code=USAGE_EXIT, message=f"error: {exc}")
    known = [*manifest.adapter.environment_names]
    if manifest.connector is not None:
        known += [name for name in manifest.connector.environments if name not in known]
    if chosen not in known:
        return SyncExit(
            code=USAGE_EXIT,
            message=(
                f"error: the Manifest names no Adapter or Connector environment {chosen!r}; "
                f"it names {', '.join(known) or 'none'}"
            ),
        )
    side = deployed_side(manifest, chosen)
    if side.connector_failed is not None:
        return SyncExit(code=USAGE_EXIT, message=f"error: {side.connector_failed}")
    try:
        check = check_target(target, manifest, chosen, side)
    except (FingerprintError, ManifestError) as exc:
        return SyncExit(code=USAGE_EXIT, message=f"error: {exc}")
    result = check.result
    stamp = built_at or now_utc()
    recorded = record_break(target, result, opened_at=stamp) if record_breaks else None
    # record_break writes only on `broken`; a comparison that covered nothing is `not_checked`.
    opened = recorded.path if recorded is not None and not recorded.already_open else None
    notice = (
        f"notice: this break is already open as {recorded.path}"
        if recorded is not None and recorded.already_open
        else None
    )

    if check_only:
        code = (
            0
            if result.status == "held"
            else BROKEN_EXIT
            if result.status == "broken"
            else USAGE_EXIT
        )
        return SyncExit(
            code=code,
            message=_shown(result, json_output, opened=opened),
            result=result,
            sync_break=opened,
            notice=notice,
        )

    fingerprint = rebuilt(check, built_at=stamp, resynced=False)
    if not fingerprint.sections:
        # A Fingerprint of nothing would make the next `sync` a Sync break of everything
        # (walkthrough friction 5): nothing is written, and the reasons are the table's.
        return SyncExit(
            code=USAGE_EXIT,
            message=_shown(result, json_output)
            + "\nno section was covered, so no Fingerprint was written; fix what the table "
            "names, then `agentdiag sync` again",
            result=result,
        )
    path = write_fingerprint(target, fingerprint)
    wrote = f"{path} (fingerprint {short(fingerprint.id)}, {len(fingerprint.sections)} sections)"
    return SyncExit(
        code=0,
        message=_shown(result, json_output, wrote=wrote, opened=opened),
        result=result,
        sync_break=opened,
        notice=notice,
    )


def _shown(
    result: SyncResult,
    json_output: bool,
    *,
    wrote: str | None = None,
    opened: Path | None = None,
) -> str:
    if json_output:
        return json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True)
    shown = render_sync(result, wrote=wrote)
    return shown + (f"\nrecorded Sync break {opened}" if opened is not None else "")


__all__ = [
    "BROKEN_EXIT",
    "SyncCheck",
    "SyncExit",
    "check_target",
    "rebuilt",
    "sync_target",
]
