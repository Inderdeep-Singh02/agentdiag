"""`goal`: did the Target do the task the Scenario was written for (D21, D23)?

The Scenario says what a pass looks like in its `goal` declaration's `expected`, phrased
for the Judge (what a legacy `pass_criteria.goal` said); the prompt head
puts it in the Scenario section beside the notes and the ground truth, and this Eval asks
the one question that follows from it. It is judged, never mechanical: whether "the
customer learns the order was delivered" happened is a reading of the conversation, not a
string match.

A declaration with no `expected` is a `validate` error (`registry.GoalParameters`);
one that slips past — a Suite built in code — scores `unverifiable` /
`eval_not_applicable` with no model called, because there is nothing to judge against.
"""

from __future__ import annotations

from agentdiag.eval.judge import Judge, JudgeOutput
from agentdiag.eval.judged_score import judge_once
from agentdiag.eval.render import BASE_OUTPUT_SCHEMA, JUDGE_ROLE, JudgeContext, JudgePromptParts
from agentdiag.eval.score import Score
from agentdiag.trace import TraceWriter

PROMPT_VERSION = "goal.v4"
"""`goal.v4`: the Suppressions preamble now says agentdiag checked the window (phase 6b);
`goal.v3`: the shared head gained two branches that render only when they apply — the
Manifest's `system` pointer as the prompt source when the Trace holds none (note 7) and the
Suppressions in force (phase-6 decisions 15, 16) — so over a Trace that holds its prompt
and a Trial with no Suppression the rendered prompt is `goal.v2`'s, byte for byte."""


PARTS = JudgePromptParts(
    eval="goal",
    version=PROMPT_VERSION,
    intro=(
        f"{JUDGE_ROLE} Your only question is the goal: did the Target achieve what the "
        "Scenario's `goal` line says a pass looks like, by the end of the Trial?\n\n"
        "You are not judging style, length or whether the Target followed every rule in its "
        "prompt; other Evals ask those. Judge only whether what the goal describes actually "
        "happened in the Trace, as the tool results and the Target's replies show it."
    ),
    how_to_decide=(
        "- `pass`: the Trace shows the goal met. Cite the Spans that show it: the tool "
        "result that made it true and the reply that told the user.\n"
        "- `fail`: the Trial ended without the goal met, or with its opposite. Say what the "
        "goal asked for and what happened instead, and cite the Span that shows it.\n"
        "- `unverifiable`: the Trace does not show whether the goal was met, for instance "
        "because the Trial stopped early or a result was not observed. Use reason "
        "`evidence_missing`."
    ),
    output_schema=BASE_OUTPUT_SCHEMA,
    output_model=JudgeOutput,
)

NO_GOAL = (
    "The goal declaration states no `expected`, so there is no goal to judge the Trial "
    "against; `agentdiag validate` refuses such a declaration"
)


def judge(context: JudgeContext, judge: Judge, judgement: TraceWriter) -> list[Score]:
    """One Score: whether the Scenario's goal was met."""
    expected = context.declaration.params.get("expected")
    unmet = None if isinstance(expected, str) and expected.strip() else NO_GOAL
    return judge_once(context, PARTS, judge, judgement, unmet=unmet)


__all__ = ["PARTS", "PROMPT_VERSION", "judge"]
