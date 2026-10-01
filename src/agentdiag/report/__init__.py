"""The Report: one Run rendered for humans, from the Run directory's files (D40, ADR-0005 §11).

`view.run_view` projects a Run directory into a `RunView`, and `html.render_report` embeds
that view with the one JavaScript renderer into a self-contained HTML file. `run` and
`rescore` write it as `report.html` beside `scorecard.json`; the Phase 7 UI serves the same
renderer with the same JSON from its routes. The Flow view's two models live beside it:
`story.ChangeRecordStory` (a Change record in five lanes) and `flow.FlowDefinitionView` (a
Flow's steps and edges), both rendered by the same script. Nothing here imports the SDK, so
a reader of a Run can render it offline.
"""

from agentdiag.report.flow import FlowDefinitionView, flow_definition_view
from agentdiag.report.html import REPORT_FILE, render_report, write_report
from agentdiag.report.story import ChangeRecordStory, change_record_story
from agentdiag.report.view import RunHeaderView, RunView, run_view

__all__ = [
    "REPORT_FILE",
    "ChangeRecordStory",
    "FlowDefinitionView",
    "RunHeaderView",
    "RunView",
    "change_record_story",
    "flow_definition_view",
    "render_report",
    "run_view",
    "write_report",
]
