"""Which Scenarios a Run executes, and why every other one does not (D31, ticket 07).

A caller names what they mean with three repeatable flags: `--scenario <id>`, `--tag <tag>`
and `--suite <suite>`. Values of one flag kind combine as OR and different kinds as AND,
so `--tag cancel --tag refund --suite orders` is "tagged cancel or refund, in orders". A
Scenario's derived kinds (`one_shot`, `conversation`, `simulated`, `tool`) are selectable
as tags without ever being authored (phase-5 decision 2).

No flag at all means every Scenario in every Suite the Manifest names, minus the Suites'
own `not_run` entries (decision 11). A Suite's skip is honoured by `--tag`, `--suite` and
the empty selection, and overridden only by `--scenario`: naming a Scenario by id is the
one way of saying "this one, deliberately".

What is not selected is never simply absent. Every Scenario in the loaded Suites that will
not run comes back as a `NotRun` naming its Suite and its reason — `suite_not_run` with the
Suite's own text whenever the Suite skips it and `--scenario` did not name it,
`not_selected` for the rest, or `fixture_unavailable` with the Adapter's reason — because
"nothing failed" and "nothing ran" look identical in a pass rate (ADR-0005 §3, §8). For
the same reason a selection that matches nothing is a problem, never an empty Run, and so
is a flag value that matches nothing at all: a typo in `--tag` must not quietly narrow a
Run. A Scenario named by `--scenario` is a demand, so one whose Fixtures the Adapter
cannot apply is a problem too, where under `--tag`, `--suite` or `all` it is a listed skip.

Selection is in Suite order, then Suite file order, whatever order the flags were typed
in: a continued Scenario must run before its continuer (decision 13), and a Run's Trials
should not depend on how a command line was spelt.

Offline like the rest of `agentdiag.scenario`: the Adapter's answer about Fixtures arrives
as a plain callable, so nothing here imports the Adapter, the model client or the SDK.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import PurePosixPath

from pydantic import BaseModel, Field, field_validator

from agentdiag.scenario.load import LoadedSuite, path_spelling
from agentdiag.scenario.models import Fixture, Scenario
from agentdiag.types import NotRunReason

FixtureCheck = Callable[[Sequence[Fixture]], str | None]
"""`Adapter.check_fixtures`: why these Fixtures cannot be applied, or None (decision 12)."""


class Selection(BaseModel):
    """What the caller asked to run: each flag's values sorted and de-duplicated.

    `select` returns it again with every `suite` value replaced by the name of the Suite it
    matched (`LoadedSuite.name`), so two command lines that mean the same selection —
    `--suite orders` and `--suite ./suites/orders.yaml` — record the same `run.json`
    section and the same expression.
    """

    scenario: list[str] = Field(default_factory=list)
    tag: list[str] = Field(default_factory=list)
    suite: list[str] = Field(default_factory=list)

    @field_validator("scenario", "tag")
    @classmethod
    def _normalised(cls, values: list[str]) -> list[str]:
        return sorted(set(values))

    @field_validator("suite")
    @classmethod
    def _normalised_paths(cls, values: list[str]) -> list[str]:
        return sorted({path_spelling(value) for value in values})

    def _given(self) -> list[tuple[str, list[str]]]:
        kinds = (("scenario", self.scenario), ("tag", self.tag), ("suite", self.suite))
        return [(kind, values) for kind, values in kinds if values]

    def expression(self) -> str:
        """`all`, or `scenario=a,b tag=x,y suite=orders` with only the kinds given."""
        given = self._given()
        if not given:
            return "all"
        return " ".join(f"{kind}={','.join(values)}" for kind, values in given)

    def flags(self) -> str:
        """The selection as the flags that say it, for a message naming them."""
        given = self._given()
        if not given:
            return "no selection flag (all)"
        return " ".join(f"--{kind} {value}" for kind, values in given for value in values)


class NotRun(BaseModel):
    """A Scenario that a Suite declared and this Run did not execute (D31, ADR-0005 §3).

    Enumerated rather than omitted: "nothing failed" and "nothing ran" look identical in a
    pass rate, and ADR-0005 §8 makes the Scorecard say which it was.
    """

    scenario: str
    suite: str | None = None
    """The name of the Suite that declares it: ids are unique within a Suite, not across
    Suites. None only in a Run recorded before ticket 07, which still reads."""

    reason: NotRunReason
    detail: str | None = None
    """Free text where the reason has more to say, such as a Suite's own `not_run` note."""

    trial: int | None = None
    """The Trial that did not run, numbered whenever the Run has more than one Trial per
    Scenario, so a cancellation names each unstarted Trial (ticket 08); None in a
    single-Trial Run, and for every reason but `cancelled`, where the Scenario itself is
    what did not run."""

    def line(self, *, with_suite: bool = False, after_id: str = "") -> str:
        """`<id>[after_id][  suite <name>][  trial <n>]  not run  <reason>[  <detail>]`: the
        one spelling of a not-run line, which the summary and a dry run both print."""
        suite = f"  suite {self.suite}" if with_suite and self.suite else ""
        trial = f"  trial {self.trial}" if self.trial is not None else ""
        detail = f"  {self.detail}" if self.detail else ""
        return f"{self.scenario}{after_id}{suite}{trial}  not run  {self.reason}{detail}"


class PlannedScenario(BaseModel):
    """One selected Scenario, with the Suite it came from and the Fixtures it applies.

    Keyed by `(suite.name, scenario.id)`: ids are unique within a Suite, not across Suites,
    and a Scenario's Fixtures and the Scenario it continues are its own Suite's.
    """

    suite: LoadedSuite
    scenario: Scenario
    fixtures: list[Fixture] = Field(default_factory=list)
    """Resolved from the Suite's `fixtures`, as `Adapter.open` takes them (ADR-0001 §6)."""

    @property
    def key(self) -> tuple[str, str]:
        return (self.suite.name, self.scenario.id)


class Selected(BaseModel):
    """What a selection chose, what it left out and why, and what is wrong with it."""

    selection: Selection
    """The selection as recorded: normalised, each Suite by its name."""

    suites: list[LoadedSuite] = Field(default_factory=list)
    """Every Suite the selection chose from, as loaded."""

    scenarios: list[PlannedScenario] = Field(default_factory=list)
    """In Suite order, then Suite file order."""

    not_run: list[NotRun] = Field(default_factory=list)
    """Every other Scenario in the loaded Suites, in the same order."""

    problems: list[str] = Field(default_factory=list)

    @property
    def several_suites(self) -> bool:
        """Whether a line naming a Scenario must also name its Suite to be unambiguous."""
        return len(self.suites) > 1


def planned_scenario(loaded: LoadedSuite, scenario: Scenario) -> PlannedScenario:
    """A Scenario with its Fixtures resolved from its own Suite."""
    return PlannedScenario(
        suite=loaded,
        scenario=scenario,
        fixtures=[
            Fixture(
                name=name,
                kind=loaded.suite.fixtures[name].kind,
                data=dict(loaded.suite.fixtures[name].data),
            )
            for name in scenario.fixtures
        ],
    )


def selectable_tags(scenario: Scenario) -> frozenset[str]:
    """The authored tags and the derived kinds: what `--tag` matches (decision 2)."""
    return frozenset(scenario.tags) | scenario.kinds


def addresses(loaded: LoadedSuite) -> frozenset[str]:
    """Every spelling `--suite` accepts for this Suite: its name, its stem, and its path
    relative to the Target directory with and without the extension (decision 11)."""
    spellings = {loaded.name, loaded.path.stem}
    if loaded.reference is not None:
        path = path_spelling(loaded.reference)
        spellings |= {path, str(PurePosixPath(path).with_suffix(""))}
    return frozenset(spellings)


def select(
    suites: Sequence[LoadedSuite],
    selection: Selection,
    *,
    check_fixtures: FixtureCheck | None = None,
) -> Selected:
    """Choose the Scenarios `selection` names, and name every one left behind (D31).

    `check_fixtures` is the Adapter's answer (decision 12), asked once per matched
    Scenario that declares Fixtures: a reason makes it `fixture_unavailable` and the rest
    of the selection stands, unless `--scenario` named it. A Scenario continuing one whose
    Fixtures are unavailable goes with it, because the session it would resume never opens
    (decision 13).
    """
    pairs = [(loaded, scenario) for loaded in suites for scenario in loaded.suite.scenarios]
    problems = _unmatched_values(suites, pairs, selection)
    named = set(selection.scenario)
    in_suites = [loaded for loaded in suites if addresses(loaded) & set(selection.suite)]

    def matches(loaded: LoadedSuite, scenario: Scenario) -> bool:
        return (
            (not selection.scenario or scenario.id in named)
            and (not selection.tag or bool(selectable_tags(scenario) & set(selection.tag)))
            and (not selection.suite or loaded in in_suites)
        )

    chosen: list[PlannedScenario] = []
    not_run: list[NotRun] = []
    skipped: list[NotRun] = []
    unavailable: dict[tuple[str, str], str] = {}
    demanded = False
    for loaded, scenario in pairs:
        skip = loaded.suite.not_run.get(scenario.id)
        if skip is not None and scenario.id not in named:
            entry = NotRun(
                scenario=scenario.id, suite=loaded.name, reason="suite_not_run", detail=skip or None
            )
            not_run.append(entry)
            if matches(loaded, scenario):
                skipped.append(entry)
            continue
        if not matches(loaded, scenario):
            not_run.append(NotRun(scenario=scenario.id, suite=loaded.name, reason="not_selected"))
            continue
        planned = planned_scenario(loaded, scenario)
        reason = _fixtures_unavailable(planned, unavailable, check_fixtures)
        if reason is None:
            chosen.append(planned)
            continue
        unavailable[planned.key] = reason
        if scenario.id in named:
            demanded = True
            problems.append(
                f"--scenario {scenario.id} names a Scenario whose Fixtures the Adapter "
                f"cannot apply: {reason}"
            )
        not_run.append(
            NotRun(
                scenario=scenario.id,
                suite=loaded.name,
                reason="fixture_unavailable",
                detail=reason,
            )
        )

    problems.extend(_declared_twice(chosen))
    if not chosen and not demanded:
        problems.append(_nothing_matched(selection, skipped, [*unavailable.values()]))
    return Selected(
        selection=selection.model_copy(
            update={"suite": sorted({loaded.name for loaded in in_suites})}
        ),
        suites=list(suites),
        scenarios=chosen,
        not_run=not_run,
        problems=problems,
    )


def _fixtures_unavailable(
    planned: PlannedScenario,
    unavailable: dict[tuple[str, str], str],
    check_fixtures: FixtureCheck | None,
) -> str | None:
    continues = planned.scenario.continues
    if continues is not None and (planned.suite.name, continues) in unavailable:
        return (
            f"it continues {continues!r}, whose Fixtures the Adapter cannot apply: "
            f"{unavailable[(planned.suite.name, continues)]}"
        )
    if check_fixtures is None or not planned.fixtures:
        return None
    return check_fixtures(planned.fixtures)


def _unmatched_values(
    suites: Sequence[LoadedSuite],
    pairs: Sequence[tuple[LoadedSuite, Scenario]],
    selection: Selection,
) -> list[str]:
    """Every flag value that matches nothing at all, each named with its flag."""
    ids = {scenario.id for _, scenario in pairs}
    tags = frozenset().union(*(selectable_tags(scenario) for _, scenario in pairs))
    spellings = frozenset().union(*(addresses(loaded) for loaded in suites))
    problems = [
        f"--scenario {value} names no Scenario in any Suite the Manifest names"
        for value in selection.scenario
        if value not in ids
    ]
    problems.extend(
        f"--tag {value} matches no Scenario's tag or kind in any Suite the Manifest names"
        for value in selection.tag
        if value not in tags
    )
    problems.extend(
        f"--suite {value} names no Suite the Manifest names; it names "
        f"{', '.join(loaded.name for loaded in suites) or 'none'}"
        for value in selection.suite
        if value not in spellings
    )
    return problems


def _declared_twice(chosen: Sequence[PlannedScenario]) -> list[str]:
    """Ticket 03's rule: two selected Trials of one id would share one Run directory."""
    homes: dict[str, list[str]] = {}
    for planned in chosen:
        homes.setdefault(planned.scenario.id, []).append(planned.suite.name)
    return [
        f"Scenario {identifier!r} is declared in more than one Suite ({', '.join(names)}); "
        "a Run cannot tell their Trials apart: narrow the selection with --suite"
        for identifier, names in homes.items()
        if len(names) > 1
    ]


def _nothing_matched(
    selection: Selection, skipped: Sequence[NotRun], unavailable: Sequence[str]
) -> str:
    """Why an empty selection is empty: nothing matched, or every match was left out."""
    reasons: list[str] = []
    if skipped:
        reasons.append(
            "its Suites list "
            + ("every match" if not unavailable else "some matches")
            + " under not_run ("
            + "; ".join(
                f"{entry.scenario}: {entry.detail or 'no reason given'}" for entry in skipped
            )
            + "), and naming one with --scenario runs it anyway"
        )
    if unavailable:
        reasons.append(
            "the Adapter cannot apply the Fixtures of "
            + ("every match" if not skipped else "the other matches")
            + " ("
            + "; ".join(unavailable)
            + ")"
        )
    return "; ".join([f"The selection {selection.flags()} matched no Scenario to run", *reasons])


class DryRunScenario(BaseModel):
    """One selected Scenario as `run --dry-run` lists it."""

    id: str
    suite: str
    kinds: list[str]
    tags: list[str]


class DryRunNotRun(NotRun):
    """One Scenario that would not run, with its kinds (decision 14)."""

    kinds: list[str]


class DryRun(BaseModel):
    """What `run --dry-run` prints, as text or with `--json` as this model (ticket 07)."""

    selected: list[DryRunScenario]
    not_run: list[DryRunNotRun]
    expression: str

    @classmethod
    def of(cls, selected: Selected) -> DryRun:
        """The listing for a selection. Kinds are sorted, being a set nobody authored; tags
        keep the order their author wrote them in."""
        kinds = {
            (loaded.name, scenario.id): sorted(scenario.kinds)
            for loaded in selected.suites
            for scenario in loaded.suite.scenarios
        }
        return cls(
            selected=[
                DryRunScenario(
                    id=planned.scenario.id,
                    suite=planned.suite.name,
                    kinds=kinds[planned.key],
                    tags=list(planned.scenario.tags),
                )
                for planned in selected.scenarios
            ],
            not_run=[
                DryRunNotRun(
                    **entry.model_dump(), kinds=kinds.get((entry.suite or "", entry.scenario), [])
                )
                for entry in selected.not_run
            ],
            expression=selected.selection.expression(),
        )


def render_dry_run(selected: Selected, *, json_output: bool = False) -> str:
    """What `run --dry-run` prints: each selected Scenario, what will not run, the expression.

    A selected Scenario reads `id  kinds …  tags …  suite …`; a not-run one reads
    `id  kinds …  [suite …  ]not run  <reason>  <detail>`, its kinds included so an
    unselected `simulated` Scenario says what it is (decision 14), and its Suite named
    when more than one Suite was loaded, since ids are unique only within a Suite. Kinds
    are printed sorted, being a set; tags in the order their author wrote them.
    `json_output` prints the same content as the `DryRun` model instead.
    """
    listing = DryRun.of(selected)
    if json_output:
        return listing.model_dump_json(indent=2)
    lines = [
        f"{entry.id}  kinds {_joined(entry.kinds)}  tags {_joined(entry.tags)}  suite {entry.suite}"
        for entry in listing.selected
    ]
    lines.extend(
        entry.line(with_suite=selected.several_suites, after_id=f"  kinds {_joined(entry.kinds)}")
        for entry in listing.not_run
    )
    lines.append(f"selection {listing.expression}")
    return "\n".join(lines)


def _joined(values: Sequence[str]) -> str:
    return ",".join(values) or "-"


__all__ = [
    "DryRun",
    "DryRunNotRun",
    "DryRunScenario",
    "FixtureCheck",
    "NotRun",
    "PlannedScenario",
    "Selected",
    "Selection",
    "addresses",
    "planned_scenario",
    "render_dry_run",
    "select",
    "selectable_tags",
]
