"""`agentdiag validate`: every problem with a Suite at once, where it was found (D19, D39).

`validate_suite` never raises on content. It runs the models that generate the published
schema (phase-5 decision 15), then the rules that relate one object to another, and
returns every error and warning with the path it was found at, because an author fixing a
Suite wants the whole list from one command, not one problem per attempt. `load_suite`
reads a Suite through the same function and refuses one with any error, so a Run never
starts on a Suite that `validate` would reject.

Two kinds of trouble, kept apart on purpose (D19):

- An **unknown Eval name** is a warning. The catalogue is open, so a Suite that names an
  Eval this agentdiag does not have still runs, and the Score says `eval_not_applicable`.
  So is a guardrail rule with no text (decision 16): its id is kept, and nothing judges it.
- A **legacy spelling**, a Scenario id another field cannot find, a `simulate`
  Turn with no way to end or with a Turn after it (phase-5 decision 55), a mechanical
  Eval's parameters that do not fit its typed `params_model` (an unknown operator, a
  negative cap, an empty tool list, a latency Eval with no `max_ms` and no default from the
  Manifest's `eval_parameters.latency`) — each is an error.
  Running such a Suite would score a Scenario the author did not write.

Nothing here touches the network: this module, and everything it imports, is what
`agentdiag validate` loads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from agentdiag.eval.registry import REGISTRY
from agentdiag.exits import USAGE_EXIT
from agentdiag.scenario.models import (
    AUTHORED_OBJECTS,
    CONVERT_HINT,
    LEGACY_EVAL_NAMES,
    TURN_TAGS,
    EvalDeclaration,
    Scenario,
    Suite,
    applicable_rule_ids,
    bind_short_form,
    effective_evals,
    field_keys,
    parse_guardrail_rules,
    picked_rule_ids,
)
from agentdiag.scenario.source import UnreadableSuite, document_shape, parse_document

VALIDATE_EXIT = USAGE_EXIT
"""Any error in any Suite: the configuration exit code (D32)."""

LONG_FORM_KEYS = frozenset({"eval", "id", "params", "threshold", "judge"})
"""The keys an authored long-form Eval declaration may carry (D19)."""


class Problem(BaseModel):
    """One thing wrong with a Suite, where it is."""

    path: str
    """Where in the document, as authored: `scenarios[2].turns[1].simulate`; empty for
    the file as a whole."""

    message: str

    def located(self) -> str:
        """The message with its path, for a line that carries no file."""
        return f"{self.message} at {self.path}" if self.path else self.message


class ValidationReport(BaseModel):
    """Everything `validate` found in one file."""

    file: Path
    errors: list[Problem] = Field(default_factory=list)
    warnings: list[Problem] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """No errors. Warnings do not stop a Run (D19)."""
        return not self.errors


DefaultThresholds = Mapping[str, Mapping[str, Any]]
"""A Metric Eval's default threshold by Eval name: the Manifest's `eval_parameters.latency`
(phase-6 decision 17). A declaration that writes no threshold is held to its Eval's."""


@dataclass(frozen=True)
class ManifestFacts:
    """What a Suite's validation reads from its Target's Manifest: each Metric Eval's
    default threshold (decision 17), and whether the Manifest lists `forbidden_phrases` at
    all (None: not known, as when a Suite file is checked on its own; walkthrough friction
    23). The default is a Suite checked with no Manifest."""

    default_thresholds: DefaultThresholds = field(default_factory=dict)
    forbidden_listed: bool | None = None
    target_name: str | None = None
    """The Manifest's `target.name`, which a Suite's `target` names (second walk friction 5)."""

    @classmethod
    def of(cls, manifest: Any) -> ManifestFacts:
        """The facts of a loaded `Manifest`."""
        return cls(
            manifest.latency_thresholds,
            manifest.forbidden_phrases is not None,
            manifest.target.name,
        )


NO_MANIFEST = ManifestFacts()


def with_default_threshold(
    declaration: EvalDeclaration, defaults: DefaultThresholds
) -> EvalDeclaration | None:
    """The declaration held to its Eval's default threshold, when it is a Metric that writes
    none and the Manifest has a default for it (phase-6 decision 17); None otherwise. The one
    statement of the rule: `validate` checks the declaration so, and preflight runs it so."""
    spec = REGISTRY.get(declaration.eval)
    default = defaults.get(declaration.eval)
    if spec is None or not spec.metric or declaration.threshold is not None or default is None:
        return None
    return declaration.model_copy(update={"threshold": dict(default)})


def validate_suite(path: Path, *, facts: ManifestFacts = NO_MANIFEST) -> ValidationReport:
    """Every error and warning in one Suite file. Never raises on the file's content."""
    report, _ = read_suite(Path(path), facts=facts)
    return report


def read_suite(
    path: Path, *, facts: ManifestFacts = NO_MANIFEST
) -> tuple[ValidationReport, Suite | None]:
    """The report, and the Suite as authored when there were no errors."""
    report = ValidationReport(file=path)
    try:
        raw = parse_document(path)
    except UnreadableSuite as exc:
        report.errors.append(Problem(path="", message=str(exc)))
        return report, None
    return report, _check(raw, report, facts)


def validate_rendered(
    text: str, file: Path, *, facts: ManifestFacts = NO_MANIFEST
) -> ValidationReport:
    """Validate YAML text as if it were `file`: what `generate` checks its own
    output with, under the Manifest's latency defaults (phase-6 decision 17)."""
    report = ValidationReport(file=file)
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        report.errors.append(Problem(path="", message=f"not valid yaml: {exc}"))
        return report
    _check(raw, report, facts)
    return report


def validate_document(
    raw: Any, file: Path, *, facts: ManifestFacts = NO_MANIFEST
) -> ValidationReport:
    """Validate an already parsed document as if it were `file`: what `generate` checks the
    Suite it assembled, and the Suite it read, with, parsing neither twice."""
    report = ValidationReport(file=file)
    _check(raw, report, facts)
    return report


def report_lines(report: ValidationReport) -> list[str]:
    """`error: <file>: <path>: <message>` then the warnings, one line each."""
    return [
        f"{severity}: {report.file}: "
        + (f"{problem.path}: " if problem.path else "")
        + problem.message
        for severity, problems in (("error", report.errors), ("warning", report.warnings))
        for problem in problems
    ]


def summary_line(
    reports: list[ValidationReport],
    *,
    manifest: tuple[int, int] | None = None,
    changes: tuple[int, int, int] | None = None,
) -> str:
    """One line over every file: how many were read and what was found. `manifest` is the
    Manifest's (errors, warnings) when it was checked first (phase-6 decision 8); `changes`
    is the Change records' (checked, errors, warnings), named only when there were any
    (phase-7 decision 5)."""
    errors = sum(len(report.errors) for report in reports)
    warnings = sum(len(report.warnings) for report in reports)
    read = [_count(len(reports), "Suite")]
    if manifest is not None:
        errors, warnings = errors + manifest[0], warnings + manifest[1]
        read.insert(0, "the Manifest")
    if changes is not None and changes[0]:
        errors, warnings = errors + changes[1], warnings + changes[2]
        read.append(_count(changes[0], "Change record"))
    listed = read[0] if len(read) == 1 else f"{', '.join(read[:-1])} and {read[-1]}"
    return f"validated {listed}: {_count(errors, 'error')}, {_count(warnings, 'warning')}"


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" + ("" if number == 1 else "s")


def _check(raw: Any, report: ValidationReport, facts: ManifestFacts) -> Suite | None:
    """Validate a parsed document into `report`; the authored Suite when it is valid."""
    if not isinstance(raw, dict):
        report.errors.append(Problem(path="", message="the file does not hold a Suite mapping"))
        return None

    shape = document_shape(raw)
    if shape != "standard":
        report.errors.append(
            Problem(path="", message=f"this is a legacy {shape} Suite shape; {CONVERT_HINT}")
        )
        return None

    report.warnings.extend(_authored_warnings(raw))
    try:
        suite = Suite.model_validate(raw)
    except ValidationError as exc:
        report.errors.extend(model_problems(exc, raw))
        return None

    report.errors.extend(_cross_field_problems(suite))
    report.errors.extend(_parameter_problems(suite, facts.default_thresholds))
    report.warnings.extend(_suite_warnings(suite))
    if facts.forbidden_listed is False:
        report.warnings.extend(_vacuous_forbidden_phrases(suite))
    if facts.target_name is not None and suite.target != facts.target_name:
        report.warnings.append(
            Problem(
                path="target",
                message=(
                    f"the Suite names the Target {suite.target!r} and the Manifest names "
                    f"{facts.target_name!r}; a Report and a comparison print the Manifest's"
                ),
            )
        )
    return None if report.errors else suite


def _vacuous_forbidden_phrases(suite: Suite) -> list[Problem]:
    """A `forbidden_phrases` declaration with no phrase of its own, in a Target whose
    Manifest lists none: it would pass on nothing (walkthrough friction 23)."""
    return [
        Problem(
            path=f"scenarios[{index}].evals[{number}]",
            message=(
                f"Scenario {scenario.id!r} declares forbidden_phrases with no phrase of its own "
                "and the Manifest lists no `forbidden_phrases`, so it passes on nothing; list "
                "the phrases in the Manifest's `forbidden_phrases` or use must_not_say"
            ),
        )
        for index, scenario in enumerate(suite.scenarios)
        for number, declaration in enumerate(scenario.evals)
        if declaration.eval == "forbidden_phrases" and not declaration.params.get("phrases")
    ]


# --- turning Pydantic's errors into Problems ---


def format_path(location: tuple[int | str, ...] | list[int | str]) -> str:
    """`('scenarios', 2, 'turns', 1)` as `scenarios[2].turns[1]`."""
    text = ""
    for part in location:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += f".{part}" if text else str(part)
    return text


def model_problems(exc: ValidationError, raw: Any) -> list[Problem]:
    """Pydantic's errors as Problems at the authored paths, naming the Scenario."""
    problems: list[Problem] = []
    for error in exc.errors():
        location = [part for part in error["loc"] if part not in TURN_TAGS]
        message = str(error["msg"]).removeprefix("Value error, ")
        if error["type"] == "extra_forbidden":
            if location[-1] == "kind" and len(location) == 3 and location[0] == "scenarios":
                message = (
                    "`kind` is derived from the Turns and the Evals and is never written "
                    "(decision 2)"
                )
            else:
                message = (
                    f"unknown key {location[-1]!r}; the schema has no slot for it, so carry "
                    "it under `extras` if it must be kept"
                ) + _allowed_keys(location)
        problems.append(
            Problem(path=format_path(location), message=message + _in_scenario(location, raw))
        )
    return problems


def _allowed_keys(location: list[int | str]) -> str:
    """`; the keys here are …` for an unknown key, naming the object's own fields
    (walkthrough friction 16: each allowed name took a probe to find)."""
    names = [part for part in location[:-1] if isinstance(part, str)]
    model = AUTHORED_OBJECTS.get(names[-1] if names else "")
    if model is None:
        return ""
    return f"; the keys here are {', '.join(field_keys(model))}"


def _in_scenario(location: list[int | str], raw: Any) -> str:
    """` (Scenario 'x')` when the problem is inside one, so a message names it."""
    if (
        isinstance(raw, dict)
        and len(location) >= 2
        and location[0] == "scenarios"
        and isinstance(location[1], int)
    ):
        scenarios = raw.get("scenarios") or []
        if location[1] < len(scenarios) and isinstance(scenarios[location[1]], dict):
            identifier = scenarios[location[1]].get("id")
            if identifier is not None:
                return f" (Scenario {identifier!r})"
    return ""


# --- the rules no single model can see ---


def _cross_field_problems(suite: Suite) -> list[Problem]:
    """Every rule that relates one object to another, each reported where it broke."""
    guardrails = parse_guardrail_rules(suite)
    problems = [Problem(path=path, message=message) for path, message in guardrails.problems]

    positions: dict[str, int] = {}
    for index, scenario in enumerate(suite.scenarios):
        where = f"scenarios[{index}]"
        named = f"Scenario {scenario.id!r}"
        if scenario.id in positions:
            problems.append(
                Problem(
                    path=f"{where}.id",
                    message=(
                        f"duplicate Scenario id {scenario.id!r}; the first is at "
                        f"scenarios[{positions[scenario.id]}]"
                    ),
                )
            )
        else:
            positions[scenario.id] = index

        for number, fixture in enumerate(scenario.fixtures):
            if fixture not in suite.fixtures:
                problems.append(
                    Problem(
                        path=f"{where}.fixtures[{number}]",
                        message=(
                            f"{named} names Fixture {fixture!r}, which the Suite's `fixtures` "
                            "do not declare"
                        ),
                    )
                )

        problems.extend(_continues_problems(suite, index, scenario))
        problems.extend(_simulate_problems(index, scenario))

        for number, declaration in enumerate(scenario.evals):
            if declaration.eval != "guardrails":
                continue
            picked, wrong = picked_rule_ids(declaration)
            if wrong is not None:
                problems.append(
                    Problem(path=f"{where}.evals[{number}]", message=f"{named}: {wrong}")
                )
            problems.extend(
                Problem(
                    path=f"{where}.evals[{number}]",
                    message=(
                        f"{named} picks guardrail rule {rule!r}, which no Suite-level "
                        "guardrails declaration authors"
                    ),
                )
                for rule in picked
                if rule not in guardrails.rules
            )

        if scenario.focus is not None:
            effective = scenario.model_copy(update={"evals": effective_evals(scenario, suite)})
            citable = {d.id for d in effective.evals if d.id is not None}
            citable |= {d.eval for d in effective.evals}
            citable |= set(applicable_rule_ids(effective, suite))
            if scenario.focus not in citable:
                problems.append(
                    Problem(
                        path=f"{where}.focus",
                        message=(
                            f"{named} focuses on {scenario.focus!r}, which names no Eval it "
                            "declares or inherits and no guardrail rule that judges it"
                        ),
                    )
                )

    problems.extend(
        Problem(
            path=f"not_run.{identifier}",
            message=f"not_run names {identifier!r}, which is no Scenario in this Suite",
        )
        for identifier in suite.not_run
        if identifier not in positions
    )
    return problems


def _parameter_problems(suite: Suite, defaults: DefaultThresholds) -> list[Problem]:
    """Every declaration whose parameters its Eval's `params_model` refuses (ticket 04).

    Read against the registry rather than the published schema: which parameters are
    valid depends on the Eval's name, which JSON Schema cannot follow into the catalogue.
    A Metric declaration with no threshold is read with its Eval's default from the
    Manifest when there is one (decision 17): missing with no default is the error.
    """
    declared = [(f"evals[{number}]", "the Suite", d) for number, d in enumerate(suite.evals)]
    declared.extend(
        (f"scenarios[{index}].evals[{number}]", f"Scenario {scenario.id!r}", declaration)
        for index, scenario in enumerate(suite.scenarios)
        for number, declaration in enumerate(scenario.evals)
    )
    problems: list[Problem] = []
    for where, named, declaration in declared:
        spec = REGISTRY.get(declaration.eval)
        if spec is None:
            continue
        declaration = with_default_threshold(declaration, defaults) or declaration
        try:
            spec.parameters(declaration)
        except ValidationError as exc:
            problems.extend(
                Problem(
                    path=f"{where}.{format_path(error['loc'])}" if error["loc"] else where,
                    message=(
                        f"{declaration.eval} in {named}: "
                        + str(error["msg"]).removeprefix("Value error, ")
                        + _takes(spec.params_model)
                    ),
                )
                for error in exc.errors()
            )
    return problems


def _takes(model: type[BaseModel] | None) -> str:
    """`; it takes …`: the parameters an Eval reads (walkthrough friction 16)."""
    if model is None:
        return ""
    names = field_keys(model)
    return f"; it takes {', '.join(names)}" if names else "; it takes no parameters"


ONE_SIMULATE_TURN_LAST = (
    "a Scenario has one simulate Turn and it is the last: the stop criterion ends the conversation"
)
"""Phase-5 decision 55: what a literal Turn after a stop would mean is not defined in v1,
and two goals in one Trial is not a Scenario anyone has written."""


def _simulate_problems(index: int, scenario: Scenario) -> list[Problem]:
    """A second `simulate` Turn, or any Turn after the first one, at the Turn it breaks."""
    markers = [number for number, turn in enumerate(scenario.turns) if not isinstance(turn, str)]
    if not markers:
        return []
    return [
        Problem(
            path=f"scenarios[{index}].turns[{number}]",
            message=f"{ONE_SIMULATE_TURN_LAST} (Scenario {scenario.id!r})",
        )
        for number in range(markers[0] + 1, len(scenario.turns))
    ]


def _continues_problems(suite: Suite, index: int, scenario: Scenario) -> list[Problem]:
    """Decision 13: the continued Scenario exists, runs first, and owns the session."""
    if scenario.continues is None:
        return []
    where, named = f"scenarios[{index}].continues", f"Scenario {scenario.id!r}"
    earlier = {other.id: other for other in suite.scenarios[:index]}
    if scenario.continues == scenario.id:
        return [Problem(path=where, message=f"{named} continues itself")]
    if scenario.continues not in {other.id for other in suite.scenarios}:
        return [
            Problem(
                path=where,
                message=f"{named} continues {scenario.continues!r}, which is no Scenario here",
            )
        ]
    if scenario.continues not in earlier:
        return [
            Problem(
                path=where,
                message=(
                    f"{named} continues {scenario.continues!r}, which comes after it; the "
                    "continued Scenario must come first in the Suite"
                ),
            )
        ]
    continued = earlier[scenario.continues].fixtures
    if scenario.fixtures and scenario.fixtures != continued:
        return [
            Problem(
                path=f"scenarios[{index}].fixtures",
                message=(
                    f"{named} continues {scenario.continues!r}, whose session is already open "
                    f"with Fixtures {continued}; a continuer's fixtures are empty or exactly "
                    "those"
                ),
            )
        ]
    return []


# --- warnings ---


def _suite_warnings(suite: Suite) -> list[Problem]:
    """Every Eval name the catalogue does not hold, and every rule with no text."""
    guardrails = parse_guardrail_rules(suite)
    warnings = [
        Problem(
            path=guardrails.where[rule.id],
            message=(
                f"guardrail rule {rule.id!r} has no text, so no Judge can hold the Target to "
                "it; it will score unverifiable"
            ),
        )
        for rule in guardrails.rules.values()
        if rule.rule is None
    ]
    warnings.extend(
        Problem(
            path=f"evals[{number}]",
            message=f"unknown Eval {declaration.eval!r} in the Suite's evals",
        )
        for number, declaration in enumerate(suite.evals)
        if declaration.eval not in REGISTRY
    )
    warnings.extend(
        Problem(
            path=f"scenarios[{index}].evals[{number}]",
            message=f"unknown Eval {declaration.eval!r} in Scenario {scenario.id!r}",
        )
        for index, scenario in enumerate(suite.scenarios)
        for number, declaration in enumerate(scenario.evals)
        if declaration.eval not in REGISTRY
    )
    return warnings


def _authored_warnings(raw: dict[str, Any]) -> list[Problem]:
    """What only the authored spelling shows: a short-form value nothing can bind, and a
    long-form key the declaration has no slot for (decision 1, ADR-0002 §7)."""
    places: list[tuple[str, str, Any]] = [
        (f"evals[{number}]", "the Suite's evals", declaration)
        for number, declaration in enumerate(_as_list(raw.get("evals")))
    ]
    for index, scenario in enumerate(_as_list(raw.get("scenarios"))):
        if not isinstance(scenario, dict):
            continue
        named = f"Scenario {scenario.get('id', index)!r}"
        places.extend(
            (f"scenarios[{index}].evals[{number}]", named, declaration)
            for number, declaration in enumerate(_as_list(scenario.get("evals")))
        )

    warnings: list[Problem] = []
    for path, named, declaration in places:
        if not isinstance(declaration, dict):
            continue
        if "eval" in declaration:
            warnings.extend(
                Problem(
                    path=path,
                    message=f"unknown key {key!r} in an Eval declaration in {named}; it is ignored",
                )
                for key in declaration
                if key not in LONG_FORM_KEYS and key != "inherited"
            )
            continue
        if len(declaration) != 1:
            continue
        ((name, value),) = declaration.items()
        if name in LEGACY_EVAL_NAMES:
            continue
        unbound = bind_short_form(str(name), value).unbound
        if unbound is not None:
            warnings.append(
                Problem(
                    path=path,
                    message=(
                        f"{name!r} in {named} {unbound}, so its value is carried as "
                        "params.value (decision 1)"
                    ),
                )
            )
    return warnings


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


__all__ = [
    "NO_MANIFEST",
    "VALIDATE_EXIT",
    "DefaultThresholds",
    "ManifestFacts",
    "Problem",
    "ValidationReport",
    "model_problems",
    "read_suite",
    "report_lines",
    "summary_line",
    "validate_document",
    "validate_rendered",
    "validate_suite",
    "with_default_threshold",
]
