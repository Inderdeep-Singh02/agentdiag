"""The judged Evals' prompt modules, by name, and what `run.json` records about the Judge.

One module per judged Eval (ticket 05): each exports `PROMPT_VERSION`, `PARTS` and
`judge(context, judge, judgement) -> list[Score]`, and Diagnosis the same shape with the
Trial's Scores as a keyword. This is the one place that maps a name to its module, so
`perform` dispatches by name and `run.json` records exactly the prompts that will run.

It imports the Judge, and so the model client and the SDK. That is why the catalogue
(`agentdiag.eval.registry`) names the judged Evals in `JUDGED_WITH_A_PROMPT` as strings
and never imports this: `agentdiag validate` loads the catalogue and must stay offline.
A test asserts the two lists agree.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import ModuleType
from typing import Any, cast

from pydantic import BaseModel, Field

from agentdiag.eval import (
    data_grounding,
    data_query,
    diagnosis,
    goal,
    guardrails,
    prompt_adherence,
    simulated_user_review,
    stop_when,
    tool_choice,
)
from agentdiag.eval.judge import Judge, Judges
from agentdiag.eval.notes import JudgeNotes
from agentdiag.eval.render import JudgeContext, JudgePromptParts, RecordedPrompt, asks_for
from agentdiag.eval.score import Score
from agentdiag.eval.suppressions import Suppression
from agentdiag.model.claude_code import Backend
from agentdiag.model.client import ModelClient
from agentdiag.scenario.models import JudgeOverride
from agentdiag.simulate.configuration import ReviewerConfiguration
from agentdiag.trace import TraceWriter


class JudgeConfiguration(BaseModel):
    """The `judge` block of `run.json`: which model, how hard, which prompts, which notes.

    The full text, not a pointer: a Run is meant to be interpretable with no access to the
    agentdiag that produced it, and a prompt that changed between two Runs must be visible
    in a comparison rather than inferred from a version string. `overrides` names every
    declaration that asked for a Judge of its own (D14, D19), by its id or its Eval's name.
    """

    model: str
    effort: str | None = None
    backend: Backend | None = None
    """The path the Judge's calls take, decided once in preflight from the credentials
    (ticket 19, decision 21): execution and `rescore` build the client from this and never
    ask the environment again. None only in a dry run, which calls nothing."""

    prompts: dict[str, RecordedPrompt] = Field(default_factory=dict)
    """Every judged Eval the selection declared, and `diagnosis`."""

    notes: JudgeNotes | None = None
    """The calibration notes the Judge read (ADR-0003 §8); None when the Manifest names none."""

    overrides: dict[str, JudgeOverride] = Field(default_factory=dict)
    """Declaration id or Eval name to the model and effort that declaration's Judge ran on."""

    reviewer: ReviewerConfiguration | None = None
    """The Simulated User reviewer's Judge (D28, phase-5 decision 57): its model, effort and
    prompt; None when no selected Scenario has a `simulate` Turn. Current on a rescore,
    because the reviewer judges again."""

    suppressions: list[Suppression] = Field(default_factory=list)
    """Every Suppression the Manifest records (ADR-0003 §8, phase-6 decision 16): the
    instances, which `compare` diffs under `judge.suppressions.<id>`; which were in force
    for a Score is on its `source`."""


def judges(
    client: ModelClient,
    configuration: JudgeConfiguration,
    *,
    manifest_prompts: Mapping[str, str] | None = None,
) -> Judges:
    """The Judges a `run.json` configuration describes, over one client, each built up
    front: the default and one per distinct override (D14, D19), each knowing the Backend
    its Fingerprint covers. `manifest_prompts` is the text of the Manifest's path prompt
    pointers, which preflight read (note 7)."""
    return Judges(
        client,
        configuration.model,
        configuration.effort,
        notes=configuration.notes,
        overrides=configuration.overrides.values(),
        backend=configuration.backend,
        reviewer=configuration.reviewer,
        manifest_prompts=manifest_prompts,
        suppressions=configuration.suppressions,
    )


MODULES: dict[str, ModuleType] = {
    module.PARTS.eval: module
    for module in (prompt_adherence, guardrails, goal, data_grounding, data_query, tool_choice)
}
"""Every judged Eval agentdiag can perform, by name."""

DIAGNOSIS = diagnosis.ABOUT

JudgeFunction = Callable[[JudgeContext, Judge, TraceWriter], list[Score]]


def judge_function(name: str) -> JudgeFunction:
    """The `judge` of the module that performs this Eval."""
    return cast(JudgeFunction, MODULES[name].judge)


OWN_PROMPTS: dict[str, ModuleType] = {
    DIAGNOSIS: diagnosis,
    stop_when.PARTS.eval: stop_when,
    simulated_user_review.ABOUT: simulated_user_review,
}
"""The Judge's prompts that are not a judged Eval's: the Diagnosis, the stop check and the
Simulated User reviewer (phase-5 decisions 51, 57)."""


def parts_of(name: str) -> JudgePromptParts:
    """The prompt parts of a judged Eval, of the Diagnosis, the stop check or the reviewer."""
    module = OWN_PROMPTS.get(name) or MODULES[name]
    return cast(JudgePromptParts, module.PARTS)


def is_judge_request(request: Mapping[str, Any]) -> bool:
    """Whether a recorded request body is the Judge's: its structured-output schema is one
    of the judged Evals', the Diagnosis's, the stop check's or the reviewer's (decision 51).
    """
    return any(asks_for(request, parts_of(name).output_schema) for name in (*MODULES, *OWN_PROMPTS))


def is_rescored_request(request: Mapping[str, Any]) -> bool:
    """Whether a recorded request is one a `rescore` makes again: a Judge request that is
    not a stop check's. A rescore judges finished Traces and drives nothing, so the Target's
    exchanges, the Simulated User's and the stop checks' in a whole-Suite recording are not
    its to use, and leaving them unused is no finding (decisions 19, 51; ticket 08)."""
    return is_judge_request(request) and not stop_when.is_stop_when_request(request)


def configuration(
    names: Sequence[str],
    model: str,
    effort: str | None,
    *,
    notes: JudgeNotes | None = None,
    overrides: Mapping[str, JudgeOverride] | None = None,
    backend: Backend | None = None,
    stop_check: bool = False,
    reviewer_model: str | None = None,
    suppressions: Sequence[Suppression] = (),
) -> JudgeConfiguration:
    """What `run.json` records under `judge` for the judged Evals a Run selected (D14).

    Every selected judged Eval's prompt, then Diagnosis's, which every judged or reviewed
    Trial runs;
    the stop check's when a selected `simulate` Turn declares `stop_when: {judged}`
    (decision 51); the reviewer when any selected Scenario is simulated (`reviewer_model`
    given, decision 57); and the Backend preflight resolved (ticket 19).
    """
    prompts = {name: RecordedPrompt.of(parts_of(name)) for name in dict.fromkeys(names)}
    # Recorded whenever a Judge configuration exists: a Trial whose review applied gets a
    # Diagnosis even with no judged Eval (amended decision 57).
    prompts[DIAGNOSIS] = RecordedPrompt.of(diagnosis.PARTS)
    if stop_check:
        prompts[stop_when.PARTS.eval] = RecordedPrompt.of(stop_when.PARTS)
    return JudgeConfiguration(
        model=model,
        effort=effort,
        backend=backend,
        prompts=prompts,
        notes=notes,
        overrides=dict(overrides or {}),
        suppressions=list(suppressions),
        reviewer=(
            ReviewerConfiguration(
                model=reviewer_model, prompt=RecordedPrompt.of(simulated_user_review.PARTS)
            )
            if reviewer_model is not None
            else None
        ),
    )


__all__ = [
    "DIAGNOSIS",
    "MODULES",
    "OWN_PROMPTS",
    "JudgeConfiguration",
    "RecordedPrompt",
    "configuration",
    "is_judge_request",
    "is_rescored_request",
    "judge_function",
    "judges",
    "parts_of",
]
