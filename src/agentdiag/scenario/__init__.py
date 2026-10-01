"""Scenarios and Suites: the schema, loading and validating them.

Offline by construction: nothing here imports a model client, so `agentdiag validate`
runs with no network and no credentials (ticket 03). A test holds
the package to that.
"""

from agentdiag.scenario.load import LoadedSuite, SuiteLoadError, load_suite
from agentdiag.scenario.models import (
    EvalDeclaration,
    FixtureSpec,
    GuardrailRule,
    JudgeOverride,
    Scenario,
    SimulateSpec,
    SimulateTurn,
    StopWhen,
    Suite,
    Turn,
)
from agentdiag.scenario.validate import Problem, ValidationReport, validate_suite

__all__ = [
    "EvalDeclaration",
    "FixtureSpec",
    "GuardrailRule",
    "JudgeOverride",
    "LoadedSuite",
    "Problem",
    "Scenario",
    "SimulateSpec",
    "SimulateTurn",
    "StopWhen",
    "Suite",
    "SuiteLoadError",
    "Turn",
    "ValidationReport",
    "load_suite",
    "validate_suite",
]
