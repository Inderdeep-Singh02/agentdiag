"""The Eval catalogue: what each Eval is, and what a Run does with a name it cannot place.

The catalogue is an open registry (D21). An Eval declared in a Scenario but absent here is
not an error: it is a warning at load and, at run time, a Score `unverifiable` with reason
`eval_not_applicable` (D19). That is deliberate — a Suite written for a newer agentdiag
still runs, and the Scorecard says plainly which judgements were not made rather than
quietly counting them as passes.

Ticket 03 registered every row of D21's catalogue an author declares, because the Scenario
loader needs two facts about each before any of them can be performed: its *primary*
parameter, which the short form `- name: <value>` binds to (phase-5 decision 1), and
whether it is in the tool family, which decides the derived `tool` kind (decision 2).

Ticket 05 implements the judged rows, each in its own prompt module (`goal`, `guardrails`,
`data_grounding`, `data_query`, `tool_choice`, beside Phase 4's `prompt_adherence`). Those
modules import the Judge and so the model client, so this catalogue names them only as
strings (`JUDGED_WITH_A_PROMPT`) and `agentdiag.eval.judged` maps the names to the modules;
the parameters `validate` checks for `goal` and `guardrails` live here, where it can read
them offline.

Ticket 04 implements the mechanical rows. Each lives with its implementation — the tool
Evals in `agentdiag.eval.tools`, the text Evals in `agentdiag.eval.text`, the latency
Metrics in `agentdiag.eval.latency` — and is registered here by importing that module, so a
row's `EvalSpec` and the code its Scores cite (`source.code`) cannot be edited apart. A
judged row with no prompt module is still performed exactly as an unknown name is —
`unverifiable` / `eval_not_applicable` — until ticket 05 lands it, asking no Judge for what
no Judge can yet do and needing no credentials for it.

This module imports nothing that reaches the network: `agentdiag validate` reads it, and
that command must stay offline. The three Eval modules read only the Trace and the Score.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentdiag.eval import latency, text, tools
from agentdiag.eval.spec import EvalContext, EvalSpec

DEFAULT_JUDGE_MODEL = "claude-opus-5"
"""The model a judged Eval runs on unless the Run or its declaration names another (D14).
Here, in the offline catalogue, because the CLI's `--judge-model` default reads it."""

DEFAULT_SIMULATED_USER_MODEL = "claude-sonnet-5"
"""The model the Simulated User plays the user on unless the Run names another (D14: a more
capable model is not a more faithful simulator). Here, offline, beside the Judge's, because
the CLI's `--simulated-user-model` default reads it (phase-5 decision 52)."""

DEFAULT_REVIEWER_MODEL = "claude-opus-5"
"""The Simulated User reviewer's model unless `--reviewer-model` names another (D14: the
reviewer must be stronger than the simulator, D28)."""

UNKNOWN_EVAL_CODE = "agentdiag.eval.registry"
"""What a Score records as its `source.code` when no Eval of that name is registered, or
when the one registered has no implementation yet."""


class GoalParameters(BaseModel):
    """`- goal: <what a pass looks like>`: `expected`, the primary parameter (decision 1), is
    required, so a `goal` with nothing to judge against is a `validate` error."""

    model_config = ConfigDict(extra="forbid")

    expected: str = Field(min_length=1)


class GuardrailParameters(BaseModel):
    """`rules`: Suite-level rule objects, or the ids a Scenario picks. Their shape is checked
    by `validate`'s own guardrail rules (decision 16); this refuses a key that is not one."""

    model_config = ConfigDict(extra="forbid")

    rules: Any = None


PROMPT_ADHERENCE = EvalSpec(
    name="prompt_adherence",
    kind="judged",
    min_fidelity="observed",
    answers="stick to the prompt",
)
"""Did the Target stick to its own prompt (D21)? Judged, and `observed` is enough: the
Target's words and its prompt are what the question is about, so an Adapter that saw only
the conversation can still ground it."""


CATALOGUE: tuple[EvalSpec, ...] = (
    *tools.SPECS,
    *text.SPECS,
    *latency.SPECS,
    PROMPT_ADHERENCE,
    EvalSpec(
        name="guardrails",
        kind="judged",
        min_fidelity="observed",
        primary="rules",
        answers="stick to the prompt",
        params_model=GuardrailParameters,
    ),
    EvalSpec(
        name="goal",
        kind="judged",
        min_fidelity="observed",
        primary="expected",
        answers="did it do the task",
        params_model=GoalParameters,
    ),
    EvalSpec(
        name="data_grounding",
        kind="judged",
        min_fidelity="reconstructed",
        answers="stick to the data it was given",
    ),
    EvalSpec(
        name="data_query",
        kind="judged",
        min_fidelity="reconstructed",
        tool_family=True,
        answers="query the correct data",
    ),
    EvalSpec(
        name="tool_choice",
        kind="judged",
        min_fidelity="reconstructed",
        tool_family=True,
        answers="use the correct tool",
    ),
)
"""D21's rows an author declares, in the catalogue's order. `simulated_user_review` is
not here: it is run by agentdiag on the `fail` of an adaptive Scenario (ticket 06), never
declared."""

REGISTRY: dict[str, EvalSpec] = {spec.name: spec for spec in CATALOGUE}
"""Every Eval agentdiag knows, by name."""

JUDGED_WITH_A_PROMPT: frozenset[str] = frozenset(
    {"prompt_adherence", "guardrails", "goal", "data_grounding", "data_query", "tool_choice"}
)
"""The judged Evals whose prompt module exists (`agentdiag.eval.judged.MODULES`, which a
test holds to this list). A judged row not named here is scored `eval_not_applicable`
without asking any Judge — the open-registry behaviour for a row a later ticket lands."""


def is_implemented(name: str) -> bool:
    """Whether this agentdiag can perform the Eval, rather than merely name it."""
    spec = REGISTRY.get(name)
    if spec is None:
        return False
    if spec.kind == "judged":
        return name in JUDGED_WITH_A_PROMPT
    return spec.perform is not None


def is_judged(name: str) -> bool:
    """Whether performing this Eval calls a Judge — and so needs credentials (D16).

    An unregistered name is not judged: it cannot call a model, because nothing knows how.
    Neither is a judged row with no prompt yet: it scores `eval_not_applicable` without a
    model call, so demanding credentials for it would refuse a Run for nothing.
    """
    spec = REGISTRY.get(name)
    return spec is not None and spec.kind == "judged" and is_implemented(name)


__all__ = [
    "CATALOGUE",
    "DEFAULT_JUDGE_MODEL",
    "DEFAULT_REVIEWER_MODEL",
    "DEFAULT_SIMULATED_USER_MODEL",
    "JUDGED_WITH_A_PROMPT",
    "PROMPT_ADHERENCE",
    "REGISTRY",
    "UNKNOWN_EVAL_CODE",
    "EvalContext",
    "EvalSpec",
    "GoalParameters",
    "GuardrailParameters",
    "is_implemented",
    "is_judged",
]
