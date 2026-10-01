"""What an imported Run records about its evidence, in an offline home (ticket 26).

`run.json`'s `importer` section and the Trace's `import/source` Event both list what the
evidence lacked (ADR-0013 §6). The run layer writes the one and the Importers the other, so
the shapes live here, beside neither: `agentdiag.run.record` must not pull the Importers in,
and the Importers must not pull the run layer (and with it the SDK) in. Like `timestamps.py`
and `reference.py`, this module imports nothing that reaches a model.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentdiag.connector.base import EvidenceQuery
from agentdiag.types import EvidenceKind, Fidelity


class Lacked(BaseModel):
    """One fact the evidence could not give: which, where in the Trace, and why."""

    field: str
    """A `NotObservedFact` (`end_time_exact`, `result`, `timestamps`, ...), or what the
    import set aside: `row` (a dropped duplicate), `flow_run` (a Flow run no tool call's
    window holds)."""

    where: str
    """A Span id (`llm_call-3`), `trace`, a row (`request req-7`) or a Flow id."""

    reason: str


class ImporterSection(BaseModel):
    """What an imported Run was imported from (ADR-0013 §5, phase-6 decision 36): the
    Evidence store, the Fidelity it supports, the query that read it, when, and every fact
    the evidence lacked, as the Trace's `import/source` Event names them."""

    kind: EvidenceKind
    fidelity: Fidelity
    query: EvidenceQuery
    evidence_read_at: str
    lacked: list[Lacked] = Field(default_factory=list)


__all__ = ["ImporterSection", "Lacked"]
