"""What an Eval is, and what a mechanical one is handed (D19, D21, phase-5 interfaces).

Two shapes, in their own module because both the catalogue (`agentdiag.eval.registry`)
and the modules that implement its rows (`tools`, `text`, `latency`) need them, and the
catalogue imports those modules to register their rows: a shared home is what keeps that
from being a cycle.

`EvalContext` is plain data, not the Manifest. `agentdiag.eval` must not import
`agentdiag.run` — `agentdiag validate` loads the registry and must stay offline, and
`run` reaches the SDK — so preflight extracts the two Manifest fields an Eval reads
(`forbidden_phrases`, the tools' kinds) and `perform_evals` passes them in. It is a frozen
dataclass rather than a Pydantic model for the same reason one level down: its field types
(`Scenario`, `EvalDeclaration`) live in `agentdiag.scenario`, which imports the registry
to bind short forms, so they are named for the type checker only and never resolved at
import time.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from agentdiag.eval.score import Score
from agentdiag.trace.events import Event
from agentdiag.trace.spans import Span
from agentdiag.types import Fidelity, ToolKind

if TYPE_CHECKING:
    from agentdiag.scenario.models import EvalDeclaration, Scenario


@dataclass(frozen=True)
class EvalContext:
    """Everything one mechanical Eval may read about one finished Trial."""

    scenario: Scenario
    declaration: EvalDeclaration
    events: Sequence[Event]
    """The Trace's Events with blobs resolved, so a message is its text."""

    spans: Sequence[Span]
    """`project_spans(events)`, in `dotted_order`."""

    fidelity: Fidelity
    """The Trace's: what a Score records when it read no Span at all."""

    forbidden_phrases: list[str] | None
    """The Manifest's list; None when the key is absent, which differs from `[]` (decision 6).
    No default here or on `perform_evals`: a caller that forgot the Manifest must fail
    loudly, not silently drop the inherited Score."""

    tool_kinds: Mapping[str, ToolKind]
    """The Manifest's `tools.<name>.kind`, by tool name."""


class EvalSpec(BaseModel):
    """One row of the catalogue (D21)."""

    name: str
    kind: Literal["judged", "mechanical"]
    min_fidelity: Fidelity
    """The least Fidelity this Eval can be grounded in; below it the Score is
    `unverifiable` / `fidelity_too_low`."""

    direction: Literal["minimize", "maximize"] | None = None
    """Only a Metric Eval has a direction of better; the rest carry none."""

    primary: str | None = None
    """The parameter `- name: <value>` binds to (decision 1): `threshold` for a Metric,
    None for an Eval that takes no parameter worth writing short."""

    metric: bool = False
    tool_family: bool = False
    """Counts toward the derived `tool` kind (D18, decision 2)."""

    answers: str
    """The line of the brief this Eval answers, for `--help` and the docs (D21)."""

    perform: Callable[[EvalContext], Score] | None = Field(default=None, exclude=True)
    """The mechanical implementation. None for a judged row, which a Judge performs."""

    params_model: type[BaseModel] | None = Field(default=None, exclude=True)
    """The Eval's typed parameters — and, for a Metric, its `threshold` — so `validate`
    refuses a malformed declaration with a path (decision 15) and the Eval reads typed
    values. None for a row whose parameters are the Judge's business."""

    def parameters(self, declaration: EvalDeclaration) -> BaseModel | None:
        """The declaration's parameters, typed; raises `ValidationError` when they do not
        fit. A threshold is read only by a Metric, so on any other Eval it is an unknown
        key and refused."""
        if self.params_model is None:
            return None
        authored = dict(declaration.params)
        if declaration.threshold is not None:
            authored["threshold"] = declaration.threshold
        return self.params_model.model_validate(authored)

    @property
    def code(self) -> str | None:
        """The dotted reference a Score cites as its `source.code`, such as
        `agentdiag.eval.tools:expect_tools`."""
        if self.perform is None:
            return None
        return f"{self.perform.__module__}:{self.perform.__name__}"


__all__ = ["EvalContext", "EvalSpec"]
