"""Loading a Suite for a Run: the effective Suite, or every reason there is none.

A Run executes the *effective* Suite: every Scenario's `evals` is its own plus the
Suite's, unless it opts out, with a Scenario-level declaration of the same Eval and id
replacing the inherited one (phase-5 decision 17). Loading is validating first — through
`scenario.validate`, the same function `agentdiag validate` runs — so a Run never starts
on a Suite that command would reject, and the refusal lists every error at once.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from pydantic import BaseModel

from agentdiag.scenario.models import Suite, effective_suite
from agentdiag.scenario.validate import DefaultThresholds, ManifestFacts, read_suite

if TYPE_CHECKING:
    from agentdiag.run.manifest import Manifest
    from agentdiag.workspace import TargetPaths


class SuiteLoadError(ValueError):
    """A Suite file could not be read as a Suite. The message lists every error."""


class LoadedSuite(BaseModel):
    """A Suite with the file it came from, which is how `--suite` addresses it."""

    path: Path
    name: str
    """The file's stem (`orders` for `suites/orders.yaml`), or its `reference` without the
    extension when another loaded Suite has the same stem (`with_distinct_names`): the one
    spelling `run.json`, the Scorecard and a dry run name the Suite by."""

    reference: str | None = None
    """The path as the Manifest names it, relative to the Target directory (`suites/orders.yaml`):
    the other spelling `--suite` accepts (phase-5 decision 11). None for a Suite loaded
    from a path no Manifest named, which is then addressed by its stem only."""

    suite: Suite


def load_suite(
    path: Path, *, default_thresholds: DefaultThresholds | None = None
) -> tuple[Suite, list[str]]:
    """Read one Suite; return the effective Suite with any warnings about it.

    YAML (`.yaml`, `.yml`) or JSON (`.json`), chosen by extension: the format is the
    author's business and the loaded Suite is identical either way. Raises
    `SuiteLoadError` listing every error `validate` would print. `default_thresholds` are
    the Manifest's latency defaults, which make a Metric declaration without a threshold
    valid (phase-6 decision 17); the declaration itself is left as authored.
    """
    path = Path(path)
    report, suite = read_suite(
        path, facts=ManifestFacts(default_thresholds=default_thresholds or {})
    )
    if suite is None:
        raise SuiteLoadError(
            f"{path}: " + "; ".join(problem.located() for problem in report.errors)
        )
    return effective_suite(suite), [problem.located() for problem in report.warnings]


def path_spelling(value: str) -> str:
    """One spelling for a Suite path: forward slashes, no leading `./`."""
    return str(PurePosixPath(value.replace("\\", "/")))


def with_distinct_names(suites: Sequence[LoadedSuite]) -> list[LoadedSuite]:
    """The Suites, each named by its stem unless another shares it (ticket 07).

    Two Suites with one stem (`suites/a/orders.yaml`, `suites/b/orders.yaml`) are named by
    their references without extension instead, so one name always means one Suite and a
    selection records one spelling per Suite however the caller typed it.
    """
    stems = Counter(loaded.name for loaded in suites)
    return [
        loaded.model_copy(
            update={"name": str(PurePosixPath(path_spelling(loaded.reference)).with_suffix(""))}
        )
        if stems[loaded.name] > 1 and loaded.reference is not None
        else loaded
        for loaded in suites
    ]


class SuiteFiles(BaseModel):
    """What loading every Suite a Manifest reads gave: the Suites, their warnings, and the
    load problem of each file that did not load, by its reference."""

    suites: list[LoadedSuite]
    warnings: list[str]
    problems: dict[str, str]


def load_suite_files(target: TargetPaths, manifest: Manifest) -> SuiteFiles:
    """Every `runnable` and `draft` Suite the Manifest names, effective and distinctly
    named, each path relative to the Target directory. A `retired` Suite is not loaded
    (decision 8); a draft one is, so its Scenarios can be named `not_run`. What a Run
    selects from (`run.preflight.load_suites`) and what the UI's picker lists."""
    suites: list[LoadedSuite] = []
    warnings: list[str] = []
    problems: dict[str, str] = {}
    defaults = manifest.latency_thresholds
    for entry in manifest.read_suites:
        reference = entry.path
        path = target.relative(reference)
        try:
            suite, suite_warnings = load_suite(path, default_thresholds=defaults)
        except (SuiteLoadError, OSError) as exc:
            problems[reference] = str(exc)
            continue
        suites.append(LoadedSuite(path=path, name=path.stem, reference=reference, suite=suite))
        warnings.extend(suite_warnings)
    return SuiteFiles(suites=with_distinct_names(suites), warnings=warnings, problems=problems)


__all__ = [
    "LoadedSuite",
    "SuiteFiles",
    "SuiteLoadError",
    "load_suite",
    "load_suite_files",
    "path_spelling",
    "with_distinct_names",
]
