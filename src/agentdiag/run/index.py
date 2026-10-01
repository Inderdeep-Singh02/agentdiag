"""The derived index of Runs: one SQLite file, rebuilt from the files at any time (D35).

`<root>/.agentdiag/index.sqlite` holds four tables — `runs`, `trials`, `spans`, `scores` —
keyed so a Trial's Spans and Scores join to their Run (phase-5 decision 62), and a fifth,
`changes`, of the Change records (phase-7 decision 8). Every row is read from a Run
directory or a Change record file and nothing else is stored, so the whole file can be deleted at
any time and `agentdiag index rebuild` makes it again (ADR-0005 §5). The files are the
truth; this is what `list`, and later the UI, read instead of every Trace.

**Written when a Run completes, never before** (decision 63). `execute.finish` calls
`record` after `scorecard.json` is written, and a failure to index is a warning, never a
changed exit code: the Run is on disk and complete. `list` first *reconciles* — it indexes
the Run directories the file does not hold and forgets the rows whose directory is gone — so
a Run copied in, or made by an agentdiag older than the index, is listed, and a deleted one
is not. A directory without `scorecard.json` is not indexed: it is a Run still being
written, or one that never finished, and indexing it would be indexing before completion.

**A corrupt index is a fact for a human.** A file that cannot be opened or created (any
`sqlite3.Error`, a read-only `.agentdiag/` among them), whose `user_version` is not
`INDEX_SCHEMA_VERSION`, or whose query raises `sqlite3.DatabaseError` raises `IndexCorrupt`,
naming the rebuild command once; nothing rebuilds it on its own, because a silent rebuild
would hide whatever wrote it.

Nothing here keys on the root: `run_id` is the key and `target` a column, so a Workspace of
several Targets (ADR-0013) is more rows, not a migration. One Index per Workspace, and
`locate.run_directories` walks every Target's `runs/` for it. `target` is `run.json`'s slug,
or the slug of the Target whose `runs/` holds the directory for a Run recorded before
`run.json` named one (phase-6 decision 6, amended: the directory is the truth). `source` is
`run.json`'s, `run` when absent, and exists so an imported Trial is a row too;
`change_record` is derived from the Change record files (ADR-0012, phase-7 decision 8): the
Run a record's verification names carries its id, and the `changes` table holds one row per
record, both made again from the files whenever the index reconciles. A `trials` row is one
Trace: a Trial the Run never started has no directory and no row, and `runs.not_run` already
counts it (amended decision 62).

Imports nothing from `agentdiag.model`, and reads `run.json` and `scorecard.json` as plain
JSON rather than as `RunRecord` and `Scorecard`: `list` is an offline command, run in the
process `validate` shares, and those models reach the Judge's configuration.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentdiag.change.record import (
    CLOSED,
    ChangeRecord,
    ChangeRecordInvalid,
    load_record,
    load_records,
    unreadable_records,
)
from agentdiag.eval.score import render_counts
from agentdiag.run.locate import (
    RUN_RECORD_FILE,
    SCORECARD_FILE,
    SCORES_FILE,
    TRIALS_DIRNAME,
    index_path,
    run_directories,
    trace_path,
    trial_dir,
)
from agentdiag.sync.fingerprint import NONE_SHOWN, short
from agentdiag.table import render_table
from agentdiag.trace.events import Event
from agentdiag.trace.otel_genai import REQUEST_MODEL, RESPONSE_MODEL, TOOL_NAME
from agentdiag.trace.reader import read_trace
from agentdiag.trace.spans import Span, project_spans, span_cost, trace_totals
from agentdiag.types import (
    DEFAULT_RUN_SOURCE,
    VERDICTS,
    NotCheckedReason,
    RunSource,
    SyncStatus,
    Verdict,
)
from agentdiag.workspace import TargetPaths, Workspace

INDEX_SCHEMA_VERSION = 2
"""`PRAGMA user_version` of an index this agentdiag wrote; any other value is corrupt. 2 since
the `changes` table (ticket 25): an index written before it is rebuilt, never migrated."""

REBUILD_COMMAND = "agentdiag index rebuild"

TARGET_ACTOR = "target"

SCHEMA = """
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY, target TEXT NOT NULL, created_at TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('run', 'imported')),
    change_record TEXT,
    traces_from TEXT, fingerprint TEXT, sync_status TEXT NOT NULL, sync_reason TEXT,
    selection TEXT NOT NULL, trials INTEGER NOT NULL,
    adapter_kind TEXT, environment TEXT, target_backend TEXT, judge_model TEXT,
    judge_backend TEXT,
    agentdiag_version TEXT, scenarios INTEGER NOT NULL, not_run INTEGER NOT NULL,
    pass INTEGER, fail INTEGER, incomplete INTEGER, unverifiable INTEGER, invalid INTEGER,
    pass_rate REAL,
    disk_bytes INTEGER NOT NULL, indexed_at TEXT NOT NULL
);
CREATE TABLE trials (
    run_id TEXT NOT NULL REFERENCES runs ON DELETE CASCADE, scenario TEXT NOT NULL,
    trial INTEGER NOT NULL,
    suite TEXT, termination TEXT, duration_ms INTEGER, events INTEGER NOT NULL,
    spans INTEGER NOT NULL,
    target_cost_usd REAL, PRIMARY KEY (run_id, scenario, trial)
);
CREATE TABLE spans (
    run_id TEXT NOT NULL, scenario TEXT NOT NULL, trial INTEGER NOT NULL,
    span_id TEXT NOT NULL, parent_span_id TEXT, kind TEXT NOT NULL, name TEXT NOT NULL,
    actor TEXT NOT NULL, fidelity TEXT NOT NULL, turn INTEGER, start_ms INTEGER NOT NULL,
    end_ms INTEGER,
    status TEXT NOT NULL, duration_ms INTEGER, cost_usd REAL, tool_name TEXT, model TEXT,
    PRIMARY KEY (run_id, scenario, trial, span_id),
    FOREIGN KEY (run_id, scenario, trial) REFERENCES trials ON DELETE CASCADE
);
CREATE TABLE scores (
    run_id TEXT NOT NULL, scenario TEXT NOT NULL, trial INTEGER NOT NULL,
    position INTEGER NOT NULL, eval TEXT NOT NULL, eval_id TEXT, verdict TEXT NOT NULL,
    reason TEXT, fault_source TEXT, fault_direction TEXT, value REAL,
    source_kind TEXT NOT NULL, judge_fingerprint TEXT,
    PRIMARY KEY (run_id, scenario, trial, position),
    FOREIGN KEY (run_id, scenario, trial) REFERENCES trials ON DELETE CASCADE
);
CREATE TABLE changes (
    id TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL, opened_at TEXT NOT NULL,
    closed_at TEXT, layer TEXT, trigger_kind TEXT NOT NULL, trigger_run TEXT,
    verification_run TEXT, pushes INTEGER NOT NULL, title TEXT NOT NULL, path TEXT NOT NULL,
    PRIMARY KEY (target, id)
);
"""
"""Decision 62's four tables, as written, and phase-7 decision 8's `changes`; the leading key
columns of `spans` and `scores` carry the types and NOT NULL of the `trials` columns they
reference. A Change record's id is scoped to its Target (ADR-0012 §1), so `changes` is keyed
by both."""


BUSY_TIMEOUT_S = 5.0
"""How long a connection waits for another process's write (a Run being indexed, a Change
record recorded) before SQLite says the database is locked."""


DERIVE_CHANGE_RECORD = (
    "UPDATE runs SET change_record = (SELECT changes.id FROM changes "
    "WHERE changes.verification_run = runs.run_id AND changes.target = runs.target "
    "ORDER BY changes.id LIMIT 1)"
)
"""`runs.change_record` from the `changes` table: the Run a record's verification names
carries the record's id (phase-7 decision 8)."""


class IndexCorrupt(RuntimeError):
    """The index file cannot be opened, created or read as this agentdiag's index
    (decision 63). The message names the rebuild command, once."""

    def __init__(self, path: Path, words: str) -> None:
        super().__init__(self.sentence(path, words))
        self.path = path

    @staticmethod
    def sentence(path: Path, words: str) -> str:
        return f"the Index at {path} could not be read ({words}); run {REBUILD_COMMAND}"


class IndexBusy(IndexCorrupt):
    """Another process held the index past `BUSY_TIMEOUT_S`: nothing is wrong with the
    file, so the message says to try again and names no rebuild. A subclass, so every
    caller that reports a corrupt index reports this one too, in its own words."""

    @staticmethod
    def sentence(path: Path, words: str) -> str:
        return f"the Index at {path} is busy ({words}): another process is writing it; try again"


def _failure(path: Path, exc: BaseException) -> IndexCorrupt:
    """What a `sqlite3` failure means: `IndexBusy` for a lock that outlasted the busy
    timeout, `IndexCorrupt` for anything else."""
    words = str(exc)
    if isinstance(exc, sqlite3.OperationalError) and ("locked" in words or "busy" in words):
        return IndexBusy(path, words)
    return IndexCorrupt(path, words)


SkipLevel = Literal["warning", "notice"]
"""How a skipped Run directory is said: `warning` for files that cannot be read, `notice`
for a Run with no Scorecard yet, since one still being written is a normal thing to find."""


class RunUnreadable(ValueError):
    """A Run directory whose files cannot be indexed; skipped, and said so naming it."""

    def __init__(self, run_dir: Path, why: str, *, level: SkipLevel = "warning") -> None:
        super().__init__(f"skipped {run_dir}: {why}")
        self.level: SkipLevel = level

    @property
    def line(self) -> str:
        return f"{self.level}: {self}"


class ListedSync(BaseModel):
    """The Sync result a Run recorded (ADR-0007), as the listing shows it."""

    status: SyncStatus
    reason: NotCheckedReason | None = None


class VerdictCounts(BaseModel):
    """Every Verdict counted, zeros included (ADR-0005 §8): five required fields, so the
    published schema cannot let a listing leave one out."""

    model_config = ConfigDict(populate_by_name=True)

    # In VERDICTS' order, so a dump prints the five as the Scorecard does. `pass` is a
    # Python keyword, hence the alias; every dump and the schema use the Verdict's name.
    pass_: int = Field(alias="pass")
    fail: int
    incomplete: int
    unverifiable: int
    invalid: int

    def as_mapping(self) -> dict[Verdict, int]:
        dumped = self.model_dump(by_alias=True)
        return {verdict: dumped[verdict] for verdict in VERDICTS}


class RunListing(BaseModel):
    """One Run as `agentdiag list --json` emits it (decision 64), read from the index."""

    run_id: str
    target: str
    created_at: str
    source: RunSource
    change_record: str | None = None
    traces_from: str | None = None
    """A rescore's source (D34): the Run whose Traces this one's Scores read."""

    fingerprint: str | None = None
    sync: ListedSync
    selection: str
    """The selection expression (D31)."""

    trials: int
    counts: VerdictCounts
    pass_rate: float | None = None
    not_run: int
    """How many Scenarios, or Trials, the Scorecard names as not run, with a reason."""

    disk_bytes: int
    """The sum of the Run directory's file sizes (D36); a rescore's is small, and says so."""


class Index:
    """An open index file. `open` makes it when absent and refuses it when corrupt."""

    def __init__(self, path: Path, connection: sqlite3.Connection) -> None:
        self.path = path
        self._connection = connection

    @classmethod
    def open(cls, root: Path) -> Index:
        """The index of the Runs under `root`: created with its schema when absent,
        `IndexCorrupt` when it cannot be created, or is there and is not this agentdiag's."""
        path = index_path(root)
        try:
            if not path.exists():
                return cls._ready(path, _create(path))
            # Read-write but never create: a path that vanished between the check and here
            # must not become an empty file that then reads as a corrupt one.
            connection = sqlite3.connect(
                f"{path.resolve().as_uri()}?mode=rw", uri=True, timeout=BUSY_TIMEOUT_S
            )
        except (sqlite3.Error, OSError) as exc:
            raise _failure(path, exc) from exc
        try:
            (version,) = connection.execute("PRAGMA user_version").fetchone()
        except sqlite3.Error as exc:
            connection.close()
            raise _failure(path, exc) from exc
        if version != INDEX_SCHEMA_VERSION:
            connection.close()
            raise IndexCorrupt(
                path, f"schema version {version}, and this agentdiag reads {INDEX_SCHEMA_VERSION}"
            )
        return cls._ready(path, connection)

    @classmethod
    def _ready(cls, path: Path, connection: sqlite3.Connection) -> Index:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return cls(path, connection)

    def record(self, run_dir: Path, holder: str) -> None:
        """One Run's rows in the four tables, replacing any it had, in one transaction.
        `holder` is the slug of the Target whose `runs/` holds the directory: the Run's
        Target when its `run.json` names none, since the directory is the truth.

        `RunUnreadable` when the directory cannot be indexed; the index is then unchanged.
        """
        rows = _read_run(Path(run_dir), holder)
        with self._connection:
            self._connection.execute("DELETE FROM runs WHERE run_id = ?", (rows.run["run_id"],))
            self._insert("runs", [rows.run])
            self._insert("trials", rows.trials)
            self._insert("spans", rows.spans)
            self._insert("scores", rows.scores)

    def reindex_target(self, target: TargetPaths) -> int:
        """One Target's rows dropped and its Run directories indexed again, the other
        Targets' rows untouched (`index rebuild --target`); return how many were indexed."""
        try:
            with self._connection:
                self._connection.execute("DELETE FROM runs WHERE target = ?", (target.slug,))
        except sqlite3.DatabaseError as exc:
            raise _failure(self.path, exc) from exc
        indexed = sum(
            1
            for path in run_directories(target.root, within=target)
            if self._record_or_say(path, target.slug)
        )
        self.reconcile_changes(target.root)
        return indexed

    def forget(self, run_id: str) -> None:
        """A Run's rows, gone: its directory is (the cascade takes its Trials' rows)."""
        try:
            with self._connection:
                self._connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
        except sqlite3.DatabaseError as exc:
            raise _failure(self.path, exc) from exc

    def reconcile(self, root: Path) -> tuple[int, int]:
        """Index the Run directories the file does not hold, forget the rows whose directory
        is gone; return (indexed, forgotten). An unreadable directory is said on stderr.

        `run_id` is the key, so a Run id under two Targets' `runs/` cannot be two rows: both
        are skipped with a warning naming them, and a row it had is forgotten. Run ids carry
        a random suffix, so only a directory copied between Targets does this: a guard."""
        held = self.run_ids()
        present = _run_directories_by_name(root)
        indexed = 0
        single: set[str] = set()
        for name, copies in sorted(present.items()):
            if len(copies) > 1:
                print(f"warning: {_under_two_targets(name, copies)}", file=sys.stderr)
                continue
            single.add(name)
            ((slug, path),) = copies
            if name not in held and self._record_or_say(path, slug):
                indexed += 1
        gone = sorted(held - single)
        for run_id in gone:
            self.forget(run_id)
        self.reconcile_changes(root)
        return indexed, len(gone)

    def reconcile_changes(self, root: Path) -> int:
        """The `changes` table made again from every Target's Change record files, and
        `runs.change_record` derived from them: the Run a record's verification names
        carries the record's id (phase-7 decision 8). A file that does not load is said on
        stderr and skipped; `validate` names what is wrong with it. Returns the rows."""
        found: list[dict[str, Any]] = []
        for target in Workspace.at(root).targets():
            for invalid in unreadable_records(target):
                print(f"warning: skipped Change record {invalid}", file=sys.stderr)
            found += [_change_row(target, path, record) for path, record in load_records(target)]
        try:
            with self._connection:
                self._connection.execute("DELETE FROM changes")
                self._insert("changes", found)
                self._connection.execute(DERIVE_CHANGE_RECORD)
        except sqlite3.DatabaseError as exc:
            raise _failure(self.path, exc) from exc
        return len(found)

    def record_change(self, target: TargetPaths, path: Path, record: ChangeRecord) -> None:
        """One Change record's row, replacing the one it had, and `runs.change_record`
        derived again: what a `change` write calls so the Index never waits for a rebuild
        to count it (the Dashboard only reads)."""
        try:
            with self._connection:
                self._connection.execute(
                    "DELETE FROM changes WHERE target = ? AND id = ?", (record.target, record.id)
                )
                self._insert("changes", [_change_row(target, path, record)])
                self._connection.execute(DERIVE_CHANGE_RECORD)
        except sqlite3.DatabaseError as exc:
            raise _failure(self.path, exc) from exc

    def listing(self, *, limit: int | None = None, target: str | None = None) -> list[RunListing]:
        """The indexed Runs, newest first; the `limit` newest when given; only the Target
        `target` names when given (`list --target`)."""
        sql = "SELECT * FROM runs"
        parameters: list[Any] = []
        if target is not None:
            sql += " WHERE target = ?"
            parameters.append(target)
        sql += " ORDER BY created_at DESC, run_id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        return [
            RunListing(
                run_id=row["run_id"],
                target=row["target"],
                created_at=row["created_at"],
                source=row["source"],
                change_record=row["change_record"],
                traces_from=row["traces_from"],
                fingerprint=row["fingerprint"],
                sync=ListedSync(status=row["sync_status"], reason=row["sync_reason"]),
                selection=row["selection"],
                trials=row["trials"],
                counts=VerdictCounts.model_validate(
                    {verdict: row[verdict] or 0 for verdict in VERDICTS}
                ),
                pass_rate=row["pass_rate"],
                not_run=row["not_run"],
                disk_bytes=row["disk_bytes"],
            )
            for row in self._query(sql, parameters)
        ]

    def run_ids(self) -> set[str]:
        """Every Run id the index holds: what the Dashboard compares with the Run
        directories' names to say the index is behind them, without opening one."""
        return {row["run_id"] for row in self._query("SELECT run_id FROM runs")}

    def open_change_ids_by_target(self) -> dict[str, list[str]]:
        """The ids of every Change record still open (`open`, `proposed`, `pushed`), by
        Target, each Target's by id: what the Dashboard counts (phase-7 decision 24)."""
        closed = sorted(CLOSED)
        sql = (
            "SELECT target, id FROM changes "
            f"WHERE status NOT IN ({', '.join('?' for _ in closed)}) ORDER BY target, id"
        )
        held: dict[str, list[str]] = {}
        for row in self._query(sql, closed):
            held.setdefault(row["target"], []).append(row["id"])
        return held

    def scenario_ids(self, *, target: str | None = None) -> dict[str, list[str]]:
        """Every Scenario id a Trial is indexed under, with the Runs holding one, oldest
        first; only the Target `target` names when given (phase-6 decision 30)."""
        sql = (
            "SELECT DISTINCT trials.scenario, trials.run_id FROM trials "
            "JOIN runs ON runs.run_id = trials.run_id"
        )
        parameters: list[Any] = []
        if target is not None:
            # `runs.target` is NOT NULL: a Run whose `run.json` names none is indexed under
            # the Target whose `runs/` holds it (decision 6, amended), so nothing is skipped.
            sql += " WHERE runs.target = ?"
            parameters.append(target)
        sql += " ORDER BY trials.scenario, runs.created_at, trials.run_id"
        held: dict[str, list[str]] = {}
        for row in self._query(sql, parameters):
            held.setdefault(row["scenario"], []).append(row["run_id"])
        return held

    def close(self) -> None:
        self._connection.close()

    def _record_or_say(self, run_dir: Path, holder: str) -> bool:
        try:
            self.record(run_dir, holder)
        except RunUnreadable as unreadable:
            print(unreadable.line, file=sys.stderr)
            return False
        except sqlite3.IntegrityError as exc:
            # Two rows under one key: the files say something twice (a Trace repeating a
            # Span id), so the Run is skipped rather than half indexed.
            print(f"warning: skipped {run_dir}: {exc}", file=sys.stderr)
            return False
        except sqlite3.DatabaseError as exc:
            raise _failure(self.path, exc) from exc
        return True

    def _query(self, sql: str, parameters: Sequence[Any] = ()) -> list[sqlite3.Row]:
        try:
            return self._connection.execute(sql, parameters).fetchall()
        except sqlite3.DatabaseError as exc:
            raise _failure(self.path, exc) from exc

    def _insert(self, table: str, rows: Sequence[dict[str, Any]]) -> None:
        if not rows:
            return
        columns = list(rows[0])
        self._connection.executemany(
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            [tuple(row[column] for column in columns) for row in rows],
        )


def _create(path: Path) -> sqlite3.Connection:
    """A new index file with decision 62's schema and its version."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S)
    try:
        connection.executescript(SCHEMA)
        connection.execute(f"PRAGMA user_version = {INDEX_SCHEMA_VERSION}")
        connection.commit()
    except sqlite3.Error:
        connection.close()
        raise
    return connection


def list_runs(
    root: Path, *, limit: int | None = None, target: str | None = None
) -> tuple[list[RunListing], bool]:
    """What `agentdiag list` shows (decision 63): the Runs of the Workspace at `root`, or of
    its Target `target`, newest first, and whether the index had to be built for it (the
    command's `notice:`).

    Opens the index or builds it, reconciles it with the Run directories, lists, closes.
    With no index and no Run directory there is nothing to index, and nothing is written —
    not even `.agentdiag/`. `IndexCorrupt` when the file cannot be opened, created or read.
    """
    missing = not index_path(root).exists()
    if missing and not run_directories(root):
        return [], False
    index = Index.open(root)
    try:
        index.reconcile(root)
        return index.listing(limit=limit, target=target), missing
    finally:
        index.close()


def scenario_ids(root: Path, *, target: str | None = None) -> dict[str, list[str]]:
    """Which Runs hold a Trial under each Scenario id, reconciled with the Run directories
    first so a Run copied in counts: what `generate` asks before it re-keys an id (phase-6
    decision 30, ADR-0002 §8). With no index and no Run directory, nothing holds any id, and
    nothing is written. `IndexCorrupt` when the file cannot be opened, created or read."""
    if not index_path(root).exists() and not run_directories(root):
        return {}
    index = Index.open(root)
    try:
        index.reconcile(root)
        return index.scenario_ids(target=target)
    finally:
        index.close()


RECORD_FAILURES = (OSError, ValueError, ValidationError, sqlite3.Error)
"""What indexing one Run's files can raise: reading them, parsing them, writing the rows."""


def record(target: TargetPaths, run_dir: Path) -> None:
    """Index one Run of `target` that just completed: what `execute.finish` calls (decision
    63), into the Index of the Workspace the Target was resolved in.

    Never raises for what a Run's files or the index file can do: the Run is on disk and
    complete whatever happens here, so a failure is a `warning:` on stderr naming the
    rebuild command once, and the Run's exit code is its own.
    """
    try:
        index = Index.open(target.root)
    except IndexCorrupt as corrupt:
        _warn_not_indexed(run_dir, f": {corrupt}")
        return
    try:
        index.record(run_dir, target.slug)
    except IndexCorrupt as corrupt:
        _warn_not_indexed(run_dir, f": {corrupt}")
    except RECORD_FAILURES as exc:
        _warn_not_indexed(run_dir, f" ({type(exc).__name__}: {exc}); run {REBUILD_COMMAND}")
    finally:
        index.close()


def record_change(target: TargetPaths, path: Path) -> None:
    """Index one Change record a `change` command just wrote, as `record` indexes a Run.

    With no index file there is nothing to keep current and nothing is made (the Dashboard
    asks for a rebuild); otherwise never raises for what the file or the index can do: the
    record is on disk whatever happens here, so a failure is a `warning:` on stderr.
    """
    if not index_path(target.root).exists():
        return
    try:
        index = Index.open(target.root)
    except IndexCorrupt as corrupt:
        _warn_change_not_indexed(path, f": {corrupt}")
        return
    try:
        index.record_change(target, path, load_record(path))
    except IndexCorrupt as corrupt:
        _warn_change_not_indexed(path, f": {corrupt}")
    except (ChangeRecordInvalid, *RECORD_FAILURES) as exc:
        _warn_change_not_indexed(path, f" ({type(exc).__name__}: {exc}); run {REBUILD_COMMAND}")
    finally:
        index.close()


def _warn_change_not_indexed(path: Path, why: str) -> None:
    print(f"warning: Change record {path.name} was written but not indexed{why}", file=sys.stderr)


@dataclass
class RunCensus:
    """The Run directories against the Runs an index holds, from names and a light read
    of the two JSON files: what `reconcile` would do, said without doing it."""

    addable: list[Path]
    """Complete directories the index lacks that `reconcile` would index."""

    gone: list[str]
    """Run ids the index holds whose directory is gone."""

    skipped: list[str]
    """Why each directory `reconcile` would skip is skipped, in its warning's words."""


def run_census(root: Path, held: set[str]) -> RunCensus:
    """The Workspace's Run directories against `held`. A directory the index lacks is read
    as far as `run.json` and `scorecard.json` (never a Trace); one still being written is
    neither addable nor skipped, as `reconcile` only notices it."""
    present = _run_directories_by_name(root)
    census = RunCensus(addable=[], gone=[], skipped=[])
    for name, copies in sorted(present.items()):
        if len(copies) > 1:
            census.skipped.append(_under_two_targets(name, copies))
            continue
        ((_, path),) = copies
        if name in held:
            continue
        try:
            _readable_head(path)
        except RunUnreadable as unreadable:
            if unreadable.level == "warning":
                census.skipped.append(str(unreadable))
            continue
        census.addable.append(path)
    single = {name for name, copies in present.items() if len(copies) == 1}
    census.gone = sorted(held - single)
    return census


def _readable_head(run_dir: Path) -> None:
    """`RunUnreadable` as `_read_run` raises it for `run.json` and `scorecard.json`."""
    record = _read_json(run_dir / RUN_RECORD_FILE, RUN_RECORD_FILE)
    if not (run_dir / SCORECARD_FILE).exists():
        raise RunUnreadable(run_dir, "no Scorecard yet", level="notice")
    _read_json(run_dir / SCORECARD_FILE, SCORECARD_FILE)
    run_id = record.get("run_id")
    if run_id != run_dir.name:
        raise RunUnreadable(
            run_dir, f"its {RUN_RECORD_FILE} names Run {run_id!r}, not the directory it is in"
        )


def _run_directories_by_name(root: Path) -> dict[str, list[tuple[str, Path]]]:
    """Every Run directory of the Workspace by name, with the slug of the Target holding it."""
    present: dict[str, list[tuple[str, Path]]] = {}
    for target in Workspace.at(root).targets():
        for path in run_directories(root, within=target):
            present.setdefault(path.name, []).append((target.slug, path))
    return present


def _under_two_targets(name: str, copies: Sequence[tuple[str, Path]]) -> str:
    places = ", ".join(str(path) for _, path in copies)
    return f"skipped Run {name}: it is under more than one Target ({places}); keep it under one"


def rebuild(root: Path, target: TargetPaths | None = None) -> int:
    """Delete the index and index every Run directory of every Target; return how many were
    indexed. With `target`, only that Target's rows are dropped and its Run directories
    indexed again, and the other Targets' rows are left as they are."""
    if target is None:
        index_path(root).unlink(missing_ok=True)
    index = Index.open(root)
    try:
        if target is None:
            indexed, _ = index.reconcile(root)
        else:
            indexed = index.reindex_target(target)
    finally:
        index.close()
    return indexed


def _warn_not_indexed(run_dir: Path, why: str) -> None:
    """`why` ends with the rebuild command, said once: `IndexCorrupt`'s message already
    names it, and every other cause is given it by the caller."""
    print(
        f"warning: Run {Path(run_dir).name} is complete but was not indexed{why}", file=sys.stderr
    )


# --- reading one Run directory into rows ---


@dataclass
class _RunRows:
    """One Run's rows in the four tables, each a mapping of column name to value."""

    run: dict[str, Any]
    trials: list[dict[str, Any]]
    spans: list[dict[str, Any]]
    scores: list[dict[str, Any]]


def _read_run(run_dir: Path, holder: str) -> _RunRows:
    """Every row one Run directory derives, read from its files and nothing else.

    A rescored Trial's Trace is read through `locate.trace_path`, which follows
    `traces_from`, so its `trials` and `spans` rows are its source's Trace and its `scores`
    rows its own (decision 62). Each Trace is projected once, and its Target cost and Span
    count are what `trace_totals` says (amended decision 64).
    """
    record = _read_json(run_dir / RUN_RECORD_FILE, RUN_RECORD_FILE)
    if not (run_dir / SCORECARD_FILE).exists():
        raise RunUnreadable(
            run_dir,
            f"it has no {SCORECARD_FILE}, so it is a Run still being written or one that "
            "never finished",
            level="notice",
        )
    scorecard = _read_json(run_dir / SCORECARD_FILE, SCORECARD_FILE)
    try:
        run_id = record["run_id"]
        if run_id != run_dir.name:
            raise RunUnreadable(
                run_dir, f"its {RUN_RECORD_FILE} names Run {run_id!r}, not the directory it is in"
            )
        suites = {
            aggregate["id"]: aggregate.get("suite")
            for aggregate in scorecard.get("scenario_aggregates") or []
        }
        rows = _RunRows(
            run=_run_row(run_dir, record, scorecard, holder), trials=[], spans=[], scores=[]
        )
        for scenario, number in _trials_of(run_dir):
            key = {"run_id": run_id, "scenario": scenario, "trial": number}
            events = _events(run_dir, scenario, number)
            projected = project_spans(events)
            rows.trials.append(_trial_row(key, suites.get(scenario), events, projected))
            rows.spans.extend(_span_row(key, span) for span in projected)
            rows.scores.extend(_score_rows(key, run_dir, scenario, number))
    except RunUnreadable:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise RunUnreadable(
            run_dir, f"its files could not be read as a Run ({type(exc).__name__}: {exc})"
        ) from exc
    return rows


def _read_json(path: Path, name: str) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RunUnreadable(
            path.parent, f"no readable {name} ({type(exc).__name__}: {exc})"
        ) from exc
    if not isinstance(loaded, dict):
        raise RunUnreadable(path.parent, f"{name} is not a JSON object")
    return loaded


def _run_row(
    run_dir: Path, record: dict[str, Any], scorecard: dict[str, Any], holder: str
) -> dict[str, Any]:
    """The `runs` row. `target` is `run.json`'s slug, or `holder`'s — the Target whose
    `runs/` holds the directory — for a Run recorded before `run.json` named one (phase-6
    decision 6 as amended: the directory is the truth, never `manifest.target.name`)."""
    adapter = record.get("adapter") or {}
    judge = record.get("judge") or {}
    sync = record.get("sync") or {}
    counts = scorecard.get("counts") or {}
    return {
        "run_id": record["run_id"],
        "target": record.get("target") or holder,
        "created_at": record["created_at"],
        "source": record.get("source") or DEFAULT_RUN_SOURCE,
        "change_record": None,
        "traces_from": record.get("traces_from"),
        "fingerprint": _fingerprint_of(record.get("fingerprint")),
        "sync_status": sync["status"],
        "sync_reason": sync.get("reason"),
        "selection": record["selection"]["expression"],
        "trials": record.get("trials", 1),
        "adapter_kind": adapter.get("kind"),
        "environment": adapter.get("environment"),
        "target_backend": (adapter.get("backend") or {}).get("kind"),
        "judge_model": judge.get("model"),
        "judge_backend": (judge.get("backend") or {}).get("kind"),
        "agentdiag_version": (record.get("agentdiag") or {}).get("version"),
        "scenarios": len(record.get("scenarios") or []),
        "not_run": len(scorecard.get("not_run") or []),
        **{verdict: int(counts.get(verdict, 0)) for verdict in VERDICTS},
        "pass_rate": scorecard.get("pass_rate"),
        "disk_bytes": _disk_bytes(run_dir),
        "indexed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _change_row(target: TargetPaths, path: Path, record: ChangeRecord) -> dict[str, Any]:
    """The `changes` row of one record (phase-7 decision 8), its path relative to the root;
    `verification_run` is the Run that verified or refuted it."""
    verification = record.verification
    return {
        "id": record.id,
        "target": record.target,
        "status": record.status,
        "opened_at": record.opened_at,
        "closed_at": record.closed_at,
        "layer": record.layer,
        "trigger_kind": record.trigger.kind,
        "trigger_run": record.trigger.run,
        "verification_run": verification.run if verification is not None else None,
        "pushes": len(record.pushes),
        "title": record.title,
        "path": target.relative_of(path),
    }


def _fingerprint_of(recorded: Any) -> str | None:
    """The id of the Fingerprint a Run recorded (phase-6 decision 9): `run.json.fingerprint.id`,
    which the Fingerprint writes beside its sections; None for a Run that recorded none —
    never a hash the index invents."""
    if isinstance(recorded, dict) and isinstance(recorded.get("id"), str):
        return str(recorded["id"])
    return None


def _disk_bytes(run_dir: Path) -> int:
    """The sum of the sizes of the files in a Run directory (D36)."""
    return sum(path.stat().st_size for path in run_dir.rglob("*") if path.is_file())


def _trials_of(run_dir: Path) -> list[tuple[str, int]]:
    """Every Trial directory this Run holds, by Scenario id then number: a Run's own Trials
    hold a Trace, a rescore's hold only its Scores and judgement."""
    root = run_dir / TRIALS_DIRNAME
    if not root.is_dir():
        return []
    found = {
        (trial.parent.name, int(trial.name))
        for trial in root.glob("*/*")
        if trial.is_dir() and trial.name.isdigit()
    }
    return sorted(found)


def _events(run_dir: Path, scenario: str, number: int) -> list[Event]:
    """The Trial's Trace, its own or its rescore source's; none when neither exists."""
    path = trace_path(run_dir, scenario, number)
    if not path.exists():
        return []
    return read_trace(path)


def _trial_row(
    key: dict[str, Any], suite: str | None, events: Sequence[Event], spans: Sequence[Span]
) -> dict[str, Any]:
    start = next((event for event in events if event.type == "trace/start"), None)
    end = next((event for event in events if event.type == "trace/end"), None)
    totals = trace_totals(spans, events)
    target = totals.actors.get(TARGET_ACTOR)
    # None when the Target made no Span, or only unpriced ones: 0.0 would claim it was free.
    priced = target is not None and not (target.cost_usd == 0 and target.unpriced)
    return {
        **key,
        "suite": suite,
        "termination": (end.model_extra or {}).get("termination") if end else None,
        "duration_ms": end.ts - start.ts if start is not None and end is not None else None,
        "events": totals.events,
        "spans": totals.spans,
        "target_cost_usd": target.cost_usd if target is not None and priced else None,
    }


def _span_row(key: dict[str, Any], span: Span) -> dict[str, Any]:
    tool = span.attributes.get(TOOL_NAME)
    model = span.attributes.get(RESPONSE_MODEL) or span.attributes.get(REQUEST_MODEL)
    return {
        **key,
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "kind": span.kind,
        "name": span.name,
        "actor": span.actor,
        "fidelity": span.fidelity,
        "turn": span.turn,
        "start_ms": span.start_ms,
        "end_ms": span.end_ms,
        "status": span.status,
        "duration_ms": span.end_ms - span.start_ms if span.end_ms is not None else None,
        "cost_usd": span_cost(span),
        "tool_name": tool if isinstance(tool, str) else None,
        "model": model if isinstance(model, str) else None,
    }


def _score_rows(
    key: dict[str, Any], run_dir: Path, scenario: str, number: int
) -> list[dict[str, Any]]:
    path = trial_dir(run_dir, scenario, number) / SCORES_FILE
    if not path.exists():
        return []
    recorded = _read_json(path, f"{TRIALS_DIRNAME}/{scenario}/{number}/{SCORES_FILE}")
    rows: list[dict[str, Any]] = []
    for position, score in enumerate(recorded.get("scores") or []):
        source = score.get("source") or {}
        value = score.get("value")
        rows.append(
            {
                **key,
                "position": position,
                "eval": score["eval"],
                "eval_id": score.get("eval_id"),
                "verdict": score["verdict"],
                "reason": score.get("reason"),
                "fault_source": score.get("fault_source"),
                "fault_direction": score.get("fault_direction"),
                "value": float(value) if isinstance(value, int | float) else None,
                "source_kind": source["kind"],
                "judge_fingerprint": source.get("judge_fingerprint"),
            }
        )
    return rows


# --- the listing (decision 64) ---

COLUMNS = (
    "run",
    "target",
    "source",
    "created",
    "fingerprint",
    "selection",
    "sync",
    "verdicts",
    "not run",
    "traces",
    "change",
    "size",
)


def listed_under(workspace: Workspace, target: TargetPaths | None) -> Path:
    """Where `list` says it looked when it found no Runs: the named Target's `runs/`, the
    one Target's when the Workspace holds one, else the Workspace's `targets/`."""
    if target is not None:
        return target.runs
    targets = workspace.targets()
    return targets[0].runs if len(targets) == 1 else workspace.targets_dir


def render_listing(rows: Sequence[RunListing], *, where: Path) -> str:
    """The Runs as a table under a header line, one line per Run, in the order given; `no
    Runs under <runs root>` when there are none.

    Plain text padded to each column's widest cell, two spaces between columns and nothing
    trailing (amended decision 64), not a Rich table: a Rich table fits itself to the
    terminal and wraps a long selection, while these lines are the same bytes at any width,
    so the README can paste them and a test can compare them.
    """
    if not rows:
        return f"no Runs under {where}"
    return "\n".join(render_table([list(COLUMNS), *(_cells(row) for row in rows)]))


def _cells(row: RunListing) -> list[str]:
    sync = row.sync.status + (f" ({row.sync.reason})" if row.sync.reason else "")
    return [
        row.run_id,
        row.target,
        row.source,
        row.created_at,
        short(row.fingerprint),
        row.selection,
        sync,
        render_counts(row.counts.as_mapping()),
        str(row.not_run),
        f"from {row.traces_from}" if row.traces_from else NONE_SHOWN,
        row.change_record or NONE_SHOWN,
        _render_size(row.disk_bytes),
    ]


def _render_size(size: int) -> str:
    """`812 B`, `136 KB`, `1.2 MB` (amended decision 64): 1024-based, whole bytes and whole
    kilobytes, one decimal from a megabyte up. A Run is tens to hundreds of kilobytes, where
    a decimal is noise; past a megabyte the decimal is what tells two Runs apart. Rounded
    the same way on every machine, so the listing is byte-stable."""
    if size < 1024:
        return f"{size} B"
    if size < 1024**2:
        return f"{round(size / 1024)} KB"
    if size < 1024**3:
        return f"{size / 1024**2:.1f} MB"
    return f"{size / 1024**3:.1f} GB"


__all__ = [
    "BUSY_TIMEOUT_S",
    "INDEX_SCHEMA_VERSION",
    "Index",
    "IndexBusy",
    "IndexCorrupt",
    "RunCensus",
    "RunListing",
    "list_runs",
    "listed_under",
    "rebuild",
    "record",
    "record_change",
    "render_listing",
    "run_census",
    "scenario_ids",
]
