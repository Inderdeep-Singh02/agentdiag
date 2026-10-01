"""`agentdiag import`: one conversation's evidence becomes a Run of `source: imported`
(phase-6 decision 36, ADR-0013 §5).

The evidence comes from a file (`--rows`, a JSON array or JSONL of proxy rows, no Connector
needed) or from the Target's Connector: `--chat <id>` reads the `proxy` store for that
conversation (with the `flows` store when the Connector offers one, so Flow runs can join
their tool calls), `--conversation <id>` the `conversation` store and `--voice <id>` the
`voice` store. The Importer of that kind turns it into one Trace, and the Run holds:

- `run.json` with `source: imported`, the `importer` section (kind, Fidelity, the query,
  when the evidence was read, what it lacked), the Manifest and Fingerprint as at import,
  `selection.expression: "import <kind> <id>"`, one Scenario `imported-<slug>` with no
  Evals, no Simulated User, no Judge, and an Adapter description of kind `import`;
- `trials/<scenario>/1/trace.jsonl`, the Importer's Events stamped with this Run;
- an empty `scores.json` and a Scorecard of zero Scores with the Sync status of the moment.

**Sync is checked as a Run checks it** (decision 14): the Connector's read, else the
Adapter's probe, against the last Fingerprint. An import drives nothing, so it never
re-syncs; a check that cannot be made (a probe that fails, a Fingerprint that cannot be
read) is recorded `not_checked` with a warning rather than refusing the import, since the
evidence was read either way. The Run is then indexed, so `list` shows it at once, and
`rescore <run> --eval <name>` judges it.

Exit 0 with the Run written, 3 otherwise (decision 43).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from agentdiag.adapter import AdapterDescription
from agentdiag.connector.base import (
    ConnectorError,
    EvidenceQuery,
    EvidenceRows,
)
from agentdiag.connector.plugins import UnknownKind, build_connector
from agentdiag.eval.score import ScoresFile
from agentdiag.evidence import ImporterSection
from agentdiag.exits import USAGE_EXIT
from agentdiag.importer import importer_for
from agentdiag.importer.base import Imported, ImportRowsError, stamped
from agentdiag.importer.rows import read_rows_file
from agentdiag.report.html import write_report
from agentdiag.run import index as run_index
from agentdiag.run.directory import RunDirectory, new_run_id
from agentdiag.run.locate import TRACE_FILE
from agentdiag.run.manifest import Manifest, ManifestError, ManifestNotFound, load_manifest
from agentdiag.run.record import (
    RunStamp,
    ScenarioSummary,
    SelectionSection,
    SyncSection,
    build_run_record,
)
from agentdiag.run.scorecard import aggregate
from agentdiag.sync.check import check_target
from agentdiag.sync.fingerprint import Fingerprint, FingerprintError
from agentdiag.sync.observe import ObservationFailed, deployed_side
from agentdiag.timestamps import now_utc
from agentdiag.trace.attributes import NOT_OBSERVED
from agentdiag.trace.spans import project_spans
from agentdiag.trace.writer import write_events
from agentdiag.types import EvidenceKind, Fidelity
from agentdiag.workspace import TargetPaths

IMPORT_ADAPTER_KIND = "import"
"""What `run.json.adapter.kind` names for an imported Run: nothing conversed."""

NOT_OBSERVED_SHOWN = 6
"""How many not-observed facts the summary names before it counts the rest."""


@dataclass
class ImportOptions:
    """What the caller asked `import` for; exactly one source is set."""

    target: TargetPaths
    rows: Path | None = None
    chat: str | None = None
    conversation: str | None = None
    voice: str | None = None
    environment: str | None = None
    json_output: bool = False


@dataclass
class ImportExit:
    """What `agentdiag import` prints and exits with."""

    code: int
    message: str
    run_dir: Path | None = None
    warnings: list[str] = field(default_factory=list)


class ImportRefused(ValueError):
    """An import that cannot be made: the message says why and what to do."""


def import_evidence(
    options: ImportOptions, *, run_id: str | None = None, stamp: RunStamp | None = None
) -> ImportExit:
    """Read the evidence, import it, write the Run; `run_id` and `stamp` exist so a test
    can reproduce a Run byte for byte."""
    warnings: list[str] = []
    try:
        manifest = load_manifest(options.target)
        environment = options.environment or manifest.adapter.default_environment
        kind, identifier, evidence = _read(options, manifest, environment)
        importer = importer_for(kind, tool_kinds=manifest.tool_kinds)
        imported = importer.import_rows(evidence)
        sync, fingerprint = _sync(options.target, manifest, environment, warnings)
    except (ManifestNotFound, ManifestError, ImportRefused, ImportRowsError) as exc:
        return ImportExit(code=USAGE_EXIT, message=f"error: {exc}")
    identifier = (
        identifier
        or imported.conversation_id
        or (options.rows.stem if options.rows else "conversation")
    )

    stamp = stamp or RunStamp.now(options.target.directory)
    new_id = run_id or new_run_id(stamp.created_at)
    directory = RunDirectory.create(options.target, new_id)
    directory.write_run_record(
        build_run_record(
            run_id=new_id,
            stamp=stamp,
            target=options.target,
            manifest=manifest,
            selection=SelectionSection(expression=f"import {kind} {identifier}"),
            scenarios=[
                ScenarioSummary(
                    id=imported.scenario_id,
                    title=f"Imported {kind} {identifier}",
                    provenance=f"evidence:{kind}:{identifier}",
                )
            ],
            not_run=[],
            adapter=_adapter(importer.fidelity, environment),
            sync=sync,
            fingerprint=fingerprint,
            importer=ImporterSection(
                kind=kind,
                fidelity=importer.fidelity,
                query=evidence.query,
                evidence_read_at=evidence.read_at,
                lacked=imported.lacked,
            ),
        )
    )
    _write_trial(directory, imported, new_id, sync)
    write_report(directory.path)
    run_index.record(options.target, directory.path)
    summary = _summary(kind, identifier, new_id, options.target, imported, sync)
    message = (
        json.dumps(
            {
                "run_id": new_id,
                "run_dir": str(directory.path),
                "scenario": imported.scenario_id,
                "importer": {
                    "kind": kind,
                    "fidelity": importer.fidelity,
                    "lacked": [entry.model_dump() for entry in imported.lacked],
                },
            },
            indent=2,
        )
        if options.json_output
        else summary
    )
    return ImportExit(code=0, message=message, run_dir=directory.path, warnings=warnings)


# --- reading the evidence ---


def _source(options: ImportOptions) -> tuple[EvidenceKind, str | None]:
    given: list[tuple[EvidenceKind, str | None]] = []
    if options.rows is not None:
        given.append(("proxy", None))
    for kind, value in (
        ("proxy", options.chat),
        ("conversation", options.conversation),
        ("voice", options.voice),
    ):
        if value is not None:
            given.append((kind, value))  # type: ignore[arg-type]
    if len(given) != 1:
        raise ImportRefused(
            "name exactly one source: --rows <file>, --chat <id>, --conversation <id> or "
            "--voice <id>"
        )
    return given[0]


def _rows_reference(path: Path, root: Path) -> str:
    """How `run.json` names a `--rows` file: relative to the Workspace root when under it,
    so a committed Workspace reads the same on every machine; else as an absolute path."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _read(
    options: ImportOptions, manifest: Manifest, environment: str
) -> tuple[EvidenceKind, str | None, EvidenceRows]:
    kind, identifier = _source(options)
    if options.rows is not None:
        try:
            rows = read_rows_file(options.rows)
        except OSError as exc:
            raise ImportRefused(f"cannot read {options.rows}: {exc}") from exc
        except ValueError as exc:
            raise ImportRefused(str(exc)) from exc
        return (
            kind,
            None,
            EvidenceRows(
                kind="proxy",
                query=EvidenceQuery(
                    extra={"rows": _rows_reference(options.rows, options.target.root)}
                ),
                rows=rows,
                read_at=now_utc(),
            ),
        )
    assert identifier is not None
    try:
        connector = build_connector(manifest)
        if connector is None:
            raise ImportRefused(
                f"Target {options.target.slug}'s Manifest names no Connector, so there is no "
                f"{kind} Evidence store to read; import a file of proxy rows with --rows"
            )
        query = EvidenceQuery(conversation_id=identifier)
        evidence = connector.read_evidence(environment, kind, query)
        if kind == "proxy" and "flows" in connector.describe().evidence:
            flows = connector.read_evidence(environment, "flows", query)
            evidence = evidence.model_copy(update={"rows": [*evidence.rows, *flows.rows]})
    except (ConnectorError, UnknownKind) as exc:
        raise ImportRefused(f"the Connector could not read the {kind} store: {exc}") from exc
    if not evidence.rows:
        raise ImportRefused(
            f"the {kind} store holds nothing for conversation {identifier!r} in environment "
            f"{environment!r}"
        )
    return kind, identifier, evidence


# --- Sync, as a Run checks it ---


def _sync(
    target: TargetPaths, manifest: Manifest, environment: str, warnings: list[str]
) -> tuple[SyncSection, Fingerprint | None]:
    """The Sync status of the moment (decision 14's check, never a re-sync). An observation
    or a Connector read that fails is `not_checked` with a warning: the evidence stands
    either way. A Fingerprint that cannot be read refuses the import, as it refuses `sync`:
    a Sync that guessed would be worse than none. Anything else is a bug, and raises."""
    try:
        side = deployed_side(manifest, environment)
        if side.connector_failed is not None:
            warnings.append(f"Sync: the Adapter's probe stands in, because {side.connector_failed}")
        check = check_target(target, manifest, environment, side)
    except (ObservationFailed, ConnectorError, UnknownKind) as exc:
        warnings.append(f"Sync not checked: {type(exc).__name__}: {exc}")
        return (
            SyncSection(
                status="not_checked", reason="adapter_cannot_observe", environment=environment
            ),
            None,
        )
    except FingerprintError as exc:
        raise ImportRefused(f"Sync cannot be checked: {exc}") from exc
    result = check.result
    return (
        SyncSection(
            status=result.status,
            reason=result.reason,
            environment=environment,
            fingerprint=result.fingerprint,
            covered_by=result.covered_by,
            connector_failed=side.connector_failed,
            sections=result.broken if result.status == "broken" else [],
        ),
        check.recorded,
    )


# --- writing the Run ---


def _adapter(fidelity: Fidelity, environment: str) -> AdapterDescription:
    """`run.json.adapter` for an import: nothing conversed, nothing observed of the
    deployed set, no side effects, at the Importer's Fidelity."""
    return AdapterDescription(
        kind=IMPORT_ADAPTER_KIND,
        fidelity=fidelity,
        side_effects="none",
        environment=environment,
        observes=[],
    )


def _write_trial(
    directory: RunDirectory, imported: Imported, run_id: str, sync: SyncSection
) -> None:
    """The one Trial's Trace, an empty `scores.json`, and a Scorecard of zero Scores."""
    trial = directory.trial_dir(imported.scenario_id, 1)
    write_events(trial / TRACE_FILE, stamped(imported.events, run_id))
    directory.write_scores(
        imported.scenario_id, 1, ScoresFile(scenario=imported.scenario_id, trial=1)
    )
    directory.write_scorecard(aggregate(run_id, sync, 1, [(imported.scenario_id, 1, [])], []))


def _summary(
    kind: str,
    identifier: str,
    run_id: str,
    target: TargetPaths,
    imported: Imported,
    sync: SyncSection,
) -> str:
    spans = project_spans(imported.events)
    turns = sum(1 for span in spans if span.kind == "turn")
    fidelity = spans[0].fidelity if spans else "observed"
    unseen: Counter[str] = Counter(
        str(item) for span in spans for item in span.attributes.get(NOT_OBSERVED) or []
    )
    named = [
        f"{fact} ({count} Span{'s' if count != 1 else ''})" for fact, count in unseen.most_common()
    ]
    dropped = sum(1 for entry in imported.lacked if entry.field == "row")
    reason = f" ({sync.reason})" if sync.reason else ""
    lines = [
        f"Imported {kind} {identifier} as Run {run_id} (Target {target.slug})",
        f"Scenario {imported.scenario_id} · 1 Trial · {turns} Turn{'s' if turns != 1 else ''} "
        f"· {len(spans)} Spans at {fidelity}, every one marked imported",
        f"Sync {sync.status}{reason}",
        "Not observed: " + (", ".join(named[:NOT_OBSERVED_SHOWN]) or "nothing"),
    ]
    if len(named) > NOT_OBSERVED_SHOWN:
        lines[-1] += f", and {len(named) - NOT_OBSERVED_SHOWN} more"
    if dropped:
        lines.append(
            f"Dropped {dropped} duplicate row{'s' if dropped != 1 else ''} (same request id)"
        )
    lines.append(f"Judge it: agentdiag rescore {run_id} --eval <name>")
    return "\n".join(lines)


__all__ = ["IMPORT_ADAPTER_KIND", "ImportExit", "ImportOptions", "import_evidence"]
