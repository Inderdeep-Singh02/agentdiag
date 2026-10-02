"""`validate`'s pass over a Target and over a Workspace: the lines it prints, the summary
line and whether it passed, so the command only prints and exits (ADR-0016 §8,
0.1.2-interfaces decision 25 as amended after the ticket 50 reviews).

**One Target, three modes, one renderer.** `validate_target` checks Suite files alone (no
Target), a Target through a draft Manifest (`--manifest`), or a whole Target: the Manifest
first (every pointer, every Suppression), then each runnable and draft Suite it names (a
retired one is named as skipped, never read), then its Change records (phase-7
decision 5). Lines printed before the Target's own, the Workspace-level warnings of a
whole-Target `validate`, are passed in and counted in its summary.

**A Workspace is every Target so checked** (`validate --all`). The Workspace-level warnings
are printed once, before the first Target, and counted in no Target's summary: they are no
one Target's. Each Target's lines then end on its summary line under its slug, in slug
order, and the pass fails when any Target has an error; a Workspace of no Target fails with
the Registry's `no Targets` line, since there was nothing to check.

Offline, as `validate` is: nothing here imports a model client or the SDK.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agentdiag.change.checks import check_records
from agentdiag.orientation import orientation_warnings
from agentdiag.registry import NO_TARGETS
from agentdiag.run.manifest import manifest_report
from agentdiag.scenario.validate import (
    NO_MANIFEST,
    ManifestFacts,
    report_lines,
    summary_line,
    validate_suite,
)
from agentdiag.workspace import TargetPaths, Workspace


@dataclass(frozen=True)
class TargetValidation:
    """One `validate` of a Target or of Suite files: the lines before the summary, the
    summary line, and whether nothing erred."""

    lines: list[str]
    summary: str
    ok: bool


def validate_target(
    target: TargetPaths | None,
    *,
    paths: Sequence[Path] = (),
    manifest_path: Path | None = None,
    whole: bool = False,
    orientation_lines: Sequence[str] = (),
) -> TargetValidation:
    """Check `paths` alone when `target` is None; else the Target's Manifest (or the draft
    at `manifest_path`) and the Suites it names, or `paths` in their place, and with `whole`
    its Change records too. `orientation_lines` come first and count as warnings."""
    lines = list(orientation_lines)
    files = list(paths)
    manifest_ok = True
    counted = (0, 0)
    facts = NO_MANIFEST
    if target is not None:
        manifest_check, manifest = manifest_report(target, manifest_path)
        lines += manifest_check.lines()
        counted = (
            len(manifest_check.errors),
            len(manifest_check.warnings) + len(orientation_lines),
        )
        if manifest is None:
            return TargetValidation(lines, summary_line([], manifest=counted), False)
        manifest_ok = manifest_check.ok
        facts = ManifestFacts.of(manifest)
        if not files:
            lines += [
                f"skipped: {target.relative(entry.path)}: Suite status: retired"
                for entry in manifest.retired_suites
            ]
            files = [
                target.relative(entry.path)
                for index, entry in enumerate(manifest.suites)
                if entry in manifest.read_suites
                and f"suites[{index}]" not in manifest_check.refused
            ]

    reports = [validate_suite(path, facts=facts) for path in files]
    for report in reports:
        lines += report_lines(report)
    changes: tuple[int, int, int] | None = None
    changes_ok = True
    if target is not None and whole:
        records = check_records(target)
        lines += records.lines()
        changes = (records.checked, len(records.errors), len(records.warnings))
        changes_ok = records.ok
    summary = summary_line(
        reports, manifest=counted if target is not None else None, changes=changes
    )
    ok = manifest_ok and changes_ok and all(report.ok for report in reports)
    return TargetValidation(lines, summary, ok)


def validate_workspace(workspace: Workspace) -> tuple[list[str], bool]:
    """Every Target of the Workspace checked whole, in slug order, after the Workspace's
    warnings, each Target's summary under its slug; and whether every Target passed."""
    targets = workspace.targets()
    if not targets:
        return [NO_TARGETS], False
    lines = orientation_warnings(workspace)
    ok = True
    for target in targets:
        checked = validate_target(target, whole=True)
        lines += [*checked.lines, f"{target.slug}: {checked.summary}"]
        ok = ok and checked.ok
    return lines, ok


__all__ = ["TargetValidation", "validate_target", "validate_workspace"]
