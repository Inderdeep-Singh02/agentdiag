"""What `validate` checks in a Target's Change records (phase-7 decision 5).

Every file under `changes/` loads as a `ChangeRecord` (an error names the file and the
field); its id is its file name and its Target the one it sits under; the status agrees
with the fields (`pushed` has a push event, `verified` and `refuted` a verification of that
result, `wontfix` a reason, `superseded` a pointer to a record that exists, an open record
no verification); a layer is named unless the record was imported; an expectation was
stated before the verification's comparison and before the verifying Run started; a closed
record cites Run directories that exist, `unknown` only on an imported one; and no field
`write_record` redacts (`redactable_fields`) holds an e-mail address or a phone number (a
redaction miss). Neither gate's `compare` is re-run here: `validate` checks the file, not
the Runs' Scores.

An imported entry whose superseding entry was not in the same file keeps the FB id
it named, which is a warning, not an error: the import had nothing to map it to. So is a
`verified` or `refuted` record with a Scenario whose Observations hold no `pass` or `fail`
(phase-8 decision 15): the close was made on Scores that decide nothing, which the gate now
refuses, and the file says so without failing `validate`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agentdiag.change.record import (
    UNKNOWN,
    ChangeRecord,
    Expectation,
    load_records,
    record_path,
    redactable_fields,
    unreadable_records,
)
from agentdiag.change.redact import redaction_misses
from agentdiag.eval.score import render_counts
from agentdiag.run.directory import is_run_id
from agentdiag.run.locate import RUN_RECORD_FILE
from agentdiag.types import VERDICTS, Verdict
from agentdiag.workspace import TargetPaths


@dataclass
class ChangeRecordReport:
    """Every problem `validate` found in a Target's Change records."""

    checked: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)
    warnings: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def lines(self) -> list[str]:
        """`error: <file>: <field>: <message>`, then the warnings, as a Suite's report reads."""
        return [
            f"{severity}: {path}: {message}"
            for severity, problems in (("error", self.errors), ("warning", self.warnings))
            for path, message in problems
        ]


def check_records(target: TargetPaths) -> ChangeRecordReport:
    """Every record of the Target checked; none is not a problem."""
    records, unreadable = load_records(target), unreadable_records(target)
    report = ChangeRecordReport(checked=len(records) + len(unreadable))
    for invalid in unreadable:
        for where, message in invalid.problems:
            report.errors.append((invalid.path, f"{where}: {message}"))
    for path, record in records:
        errors, warnings = record_problems(target, path, record)
        report.errors += [(path, message) for message in errors]
        report.warnings += [(path, message) for message in warnings]
    return report


def record_problems(
    target: TargetPaths, path: Path, record: ChangeRecord
) -> tuple[list[str], list[str]]:
    """(errors, warnings) for one record that loaded."""
    errors: list[str] = []
    warnings: list[str] = []
    imported = record.imported_from is not None
    if path.stem != record.id:
        errors.append(f"id: {record.id!r} is not the file's name {path.stem!r}")
    if record.target != target.slug:
        errors.append(f"target: {record.target!r}, and the file is under Target {target.slug}")
    if record.layer is None and not imported:
        errors.append("layer: none named; only an imported entry may leave it out")

    status = record.status
    if status == "pushed" and not record.pushes:
        errors.append("status: status pushed and no push event")
    if status in ("verified", "refuted"):
        if record.verification is None:
            errors.append(f"status: status {status} and no verification")
        elif record.verification.result != status:
            errors.append(
                f"status: status {status} and a verification whose result is "
                f"{record.verification.result}"
            )
    elif record.verification is not None:
        errors.append(f"verification: a verification on a record that is {status}")
    if status == "wontfix" and not (record.why or "").strip():
        errors.append("why: status wontfix and no reason")
    if status == "superseded":
        pointer = record.superseded_by
        if pointer is None or not record_path(target, pointer).is_file():
            message = (
                f"superseded_by: status superseded and {pointer!r} names no Change record "
                f"under Target {target.slug}"
            )
            (warnings if imported else errors).append(message)
    if record.is_closed and record.closed_at is None:
        errors.append(f"closed_at: status {status} and no closing time")

    expected, verification = record.expected, record.verification
    if (
        expected is not None
        and verification is not None
        and not expected.stated_at < verification.compared_at
    ):
        errors.append(
            f"expected.stated_at: {expected.stated_at} is stated after the verification's "
            f"comparison at {verification.compared_at}"
        )
    if verification is not None:
        for which, run in (("baseline", verification.baseline), ("run", verification.run)):
            if run == UNKNOWN and imported:
                continue
            cited = target.runs / run / RUN_RECORD_FILE
            if run == UNKNOWN or not is_run_id(run) or not cited.is_file():
                errors.append(
                    f"verification.{which}: {run!r} names no Run directory under "
                    f"{target.runs}" + ("" if run != UNKNOWN else "; only an imported record")
                )
            elif which == "run" and expected is not None:
                started = _created_at(cited)
                if started is not None and not expected.stated_at < started:
                    errors.append(
                        f"expected.stated_at: {expected.stated_at} is not before the verifying "
                        f"Run {run} started at {started}"
                    )

    if status in ("verified", "refuted") and expected is not None:
        warnings += _decides_nothing(record, status, expected)

    for where, text in redactable_fields(record):
        found = redaction_misses(text)
        if found:
            errors.append(
                f"{where}: a redaction miss: {len(found)} e-mail address or phone number left "
                "in a committed file"
            )
    return errors, warnings


def _decides_nothing(record: ChangeRecord, status: str, expected: Expectation) -> list[str]:
    """One warning per Scenario the expectation names whose Observations hold no `pass` or
    `fail` (phase-8 decision 15, amended), all five Verdicts counted, zeros included. The
    gate cites Observations over `should_move` only, so a `should_move` Scenario with none
    is warned of, and a `must_not_move` one only when the record holds Observations of it
    (a hand edit, or a gate that cites them)."""
    counts: dict[str, dict[Verdict, int]] = {
        scenario: dict.fromkeys(VERDICTS, 0) for scenario in expected.should_move
    }
    for observation in record.observed:
        if observation.scenario in expected.must_not_move:
            counts.setdefault(observation.scenario, dict.fromkeys(VERDICTS, 0))
        if observation.scenario in counts:
            counts[observation.scenario][observation.verdict] += 1
    return [
        f"observed: closed {status} on Scores that decide nothing for {scenario} "
        f"({render_counts(found)})"
        for scenario, found in counts.items()
        if found["pass"] == 0 and found["fail"] == 0
    ]


def _created_at(run_json: Path) -> str | None:
    try:
        loaded = json.loads(run_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    created = loaded.get("created_at") if isinstance(loaded, dict) else None
    return created if isinstance(created, str) else None


__all__ = ["ChangeRecordReport", "check_records", "record_problems"]
