"""The Trace as the Judge reads it: one deterministic text form (D23).

Two properties matter more than prettiness.

**Every line a Judge may cite carries the Span id it belongs to.** A `pass` or `fail`
cites Span ids and agentdiag checks them against the Trace (ADR-0003 §4); a Judge cannot
cite what it was never shown, so the ids are in the text, in square brackets, on every
line.

**The rendering is a pure function of the Events.** No timestamps, no run id, nothing that
differs between two Runs of the same replayed Trial — otherwise the Judge's request body
would differ between Runs and the recording that replays it would stop matching. A test
asserts that two Runs produce identical Judge request bodies, and this is the file that
has to hold for it to pass.

What is deliberately not here: the system prompt (it is in the prompt's own sections, and
repeating it would let a Judge cite the wrong copy) and request bodies (summarised to the
model and the stop reason — a dumped body is the prompt again, in a worse place).

**One prompt head for every judged Eval** (D23, phase-5 interfaces, ticket 05). Every Judge
prompt is `render_judge_prompt(parts, context)`, laid out in one order and no other: the
Eval's intro; the Scenario (its goal, notes and ground truth); the Target's prompt
sections; the calibration notes; the Trace; any section the Eval adds; the Eval's rule for
deciding; the reverse-hallucination checklist. Two Evals over one Trial therefore differ
only in the parts they name, and one Eval's question can never drift from the evidence
rules the others follow. The head is a pure function of the Events, the Scenario, the notes and
the parts, which is what keeps a replayed Trial's request body — the recording's key —
identical between Runs.

The head is written once, as a template (`agentdiag.eval.template`): every fixed word the
Judge reads — headings, the notes' preamble, "(none)", the `Rule N` labels, the checklist —
is in `JudgePromptParts.template()`, and the Trial supplies only data (the Scenario's
fields, the notes text, the prompt's sections, the Trace, and what an Eval adds). That
template is what `run.json` records under `judge.prompts` and what the prompt's Fingerprint
hashes, so rewording anything a Judge reads is a visible change in a comparison (ADR-0003
§8, ADR-0005 §3).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agentdiag.eval.notes import JudgeNotes
from agentdiag.eval.spec import EvalContext
from agentdiag.eval.suppressions import Suppression, in_force
from agentdiag.eval.template import Value, fill
from agentdiag.run.manifest import SYSTEM_PROMPT_POINTER
from agentdiag.scenario.models import EvalDeclaration, Scenario
from agentdiag.trace import Event, Span, project_spans, resolve_blobs
from agentdiag.trace.requests import system_text
from agentdiag.trace.spans import is_own_span
from agentdiag.types import Fidelity, ToolKind

NOT_OBSERVED = "<not observed>"
"""What a field the Adapter could not see renders as — never an empty value (D6)."""

FULL_FIDELITY: Fidelity = "instrumented"
"""The Fidelity that needs no announcing: everything below it is said out loud."""

STOP_WHEN = "stop_when"
"""The `about` of the `note` a stop check writes (phase-5 decision 47): agentdiag asking
itself whether a simulated conversation should end, which no Judge is shown."""


def judged_events(events: Sequence[Event]) -> list[Event]:
    """The Events a Judge is shown of a Trial: every one, less those inside a Span
    `is_own_span` names (at any depth) and the stop check's own `note`s outside a Span.

    The user's messages stay: a simulated Turn's `message` is inside its `turn` Span and
    renders as `user: …` exactly as a literal Turn's. A Trace with none of these Spans —
    every Trace recorded before ticket 06 — comes back unchanged, so no rendered prompt and
    no recording key moves (decisions 57, 59).
    """
    spans = {span.span_id: span for span in project_spans(events)}
    hidden: set[str] = set()
    for span in spans.values():
        current: Span | None = span
        while current is not None:
            if is_own_span(current):
                hidden.add(span.span_id)
                break
            current = spans.get(current.parent_span_id or "")
    return [
        event
        for event in events
        if (event.span_id is None or event.span_id not in hidden)
        and not (
            event.type == "note"
            and event.span_id is None
            and (event.model_extra or {}).get("about") == STOP_WHEN
        )
    ]


def render_trace_for_judge(events: Sequence[Event], *, max_chars: int | None = None) -> str:
    """Everything that happened in one Trial, in order, with Span ids to cite.

    Blobs are resolved first: a Judge that read `{"blob": "sha256:..."}` would be reading
    agentdiag's storage format rather than what the Target said. The Simulated User's own
    model calls and agentdiag's stop checks are left out (`judged_events`, decision 59).

    `max_chars` caps one rendered field. The default is None — nothing is ever cut — and
    that is the case the shipped example takes. When a cap is set and a field exceeds it,
    the line says `[truncated N chars]` rather than ending quietly, because checklist
    item 2 asks the Judge whether it is resting a Verdict on a truncated field, and a
    silent cut would make that question unanswerable.
    """
    resolved = judged_events(resolve_blobs(events))
    spans = {span.span_id: span for span in project_spans(resolved)}

    lines: list[str] = []
    turn_seen: int | None = None
    announced: set[str] = set()
    for event in resolved:
        turn = event.turn
        if turn is not None and turn != turn_seen:
            if lines:
                lines.append("")
            lines.append(f"Turn {turn}")
            turn_seen = turn
        rendered = _render_event(event, spans, max_chars)
        if rendered is None:
            continue
        lines.append(_with_fidelity(rendered, event, spans, announced))
    return "\n".join(lines)


def _with_fidelity(rendered: str, event: Event, spans: dict[str, Span], announced: set[str]) -> str:
    """Announce a Span's Fidelity once, on its first line, when it is below `instrumented`.

    ADR-0001: a Score may claim no more than the Fidelity that grounded it, and the Judge
    is the one being asked to make the claim. A `reconstructed` Span is a reading of what
    probably happened, and a Judge that cannot tell it from an observed one would cite it
    with the same confidence. Nothing changes for the toy Target, whose every Span is
    `instrumented` — this is what makes the rendering honest for Phase 8's Adapters.
    """
    span_id = event.span_id
    if span_id is None or span_id in announced:
        return rendered
    span = spans.get(span_id)
    if span is None or span.fidelity == FULL_FIDELITY:
        return rendered
    announced.add(span_id)
    head, _, tail = rendered.partition("\n")
    marked = f"{head} (fidelity {span.fidelity})"
    return f"{marked}\n{tail}" if tail else marked


def _render_event(event: Event, spans: dict[str, Span], max_chars: int | None) -> str | None:
    """One line for one Event, or None for the Events that carry no new fact."""
    extra: dict[str, Any] = event.model_extra or {}
    span_id = event.span_id or "-"
    prefix = f"[{span_id}]"

    if event.type == "message":
        role = str(extra.get("role", "?"))
        return f"{prefix} {role}: {_cut(str(extra.get('content', '')), max_chars)}"

    if event.type == "request":
        span = spans.get(event.span_id or "")
        model = span.attributes.get("gen_ai.request.model", "?") if span else "?"
        return f"{prefix} request to model {model}"

    if event.type == "response":
        return "\n".join(_render_response(prefix, extra, max_chars))

    if event.type == "tool/call":
        not_observed = list(extra.get("not_observed") or [])
        arguments = (
            NOT_OBSERVED
            if "arguments" in not_observed
            else _compact(extra.get("arguments"), max_chars)
        )
        return f"{prefix} tool/call {extra.get('tool', '?')} {arguments}"

    if event.type == "tool/result":
        marker = " (error)" if extra.get("is_error") else ""
        return f"{prefix} tool/result{marker} {_compact(extra.get('result'), max_chars)}"

    if event.type == "error":
        return f"{prefix} error {extra.get('error_type', '?')}: {extra.get('message', '')}"

    if event.type == "note":
        return f"{prefix} note: {extra.get('text', '')}"

    if event.type == "trace/end":
        return f"trace ended: {extra.get('termination', '?')}"

    return None


def _render_response(prefix: str, extra: dict[str, Any], max_chars: int | None) -> list[str]:
    """A model response: its blocks in full, then how it stopped.

    Assistant text is shown whole rather than summarised. The Judge's question is whether
    the Target followed its prompt, and what it actually said is most of the answer.
    """
    body = extra.get("body") or {}
    lines: list[str] = []
    for block in body.get("content", []) or []:
        kind = block.get("type")
        if kind == "text":
            lines.append(f"{prefix} assistant text: {_cut(str(block.get('text', '')), max_chars)}")
        elif kind == "tool_use":
            name = block.get("name", "?")
            arguments = _compact(block.get("input"), max_chars)
            lines.append(f"{prefix} assistant tool_use {name} {arguments}")
        elif kind == "thinking":
            thinking = _cut(str(block.get("thinking", "")), max_chars)
            lines.append(f"{prefix} assistant thinking: {thinking}")
        else:
            lines.append(f"{prefix} assistant {kind}")
    lines.append(f"{prefix} stop_reason {body.get('stop_reason', '?')}")
    return lines


def _compact(value: Any, max_chars: int | None = None) -> str:
    """A JSON value on one line, with its keys in a fixed order.

    Sorted keys, because a dict whose iteration order changed between two Runs would
    change the Judge's request body and break the replay that reproduces it.
    """
    if value is None:
        return NOT_OBSERVED
    return _cut(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")), max_chars
    )


def _cut(text: str, max_chars: int | None) -> str:
    """One field, capped, saying how much was dropped if anything was.

    Never a silent ellipsis: the Judge's checklist asks whether a quote came from a
    truncated field, and only a count makes that answerable.
    """
    if max_chars is None or len(text) <= max_chars:
        return text
    return f"{text[:max_chars]} [truncated {len(text) - max_chars} chars]"


# --- the Target's own prompt, split into the sections a Score can cite (D21, D23) ---

NUMBERED_SECTION = re.compile(r"^\s*(\d+)\s*[.)]\s*(.*)$")
"""A line that opens a numbered rule: `1.` or `1)`, with the rule text after it."""

PREAMBLE = "preamble"
"""What section 0 is called: everything before the first numbered rule."""


def prompt_sections(system_prompt: str) -> list[tuple[int, str]]:
    """The Target's prompt split into the numbered rules a Score can cite (D23).

    Text before the first numbered line is section 0, the preamble: it usually holds the
    Target's role and its tools, which a rule may depend on, so dropping it would leave a
    Judge unable to tell an exercised rule from an inapplicable one.
    """
    sections: list[tuple[int, list[str]]] = []
    preamble: list[str] = []
    for line in system_prompt.splitlines():
        match = NUMBERED_SECTION.match(line)
        if match:
            sections.append((int(match.group(1)), [match.group(2)]))
        elif sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)

    rendered: list[tuple[int, str]] = []
    preamble_text = "\n".join(preamble).strip()
    if preamble_text:
        rendered.append((0, preamble_text))
    rendered.extend((number, "\n".join(body).strip()) for number, body in sections)
    return rendered


def render_sections(system_prompt: str) -> str:
    """The prompt sections as the head shows them, numbered and labelled — through the one
    template fragment that spells the labels, so they are on record with the rest."""
    return fill(SECTIONS, {"prompt_sections": section_values(system_prompt)})


def section_values(system_prompt: str) -> list[dict[str, Value]]:
    """Each section as the template's `{*prompt_sections}` block reads it."""
    return [
        {"preamble": PREAMBLE if number == 0 else None, "number": str(number), "text": text}
        for number, text in prompt_sections(system_prompt)
    ]


def system_prompt_from(events: Sequence[Event]) -> str | None:
    """The Target's own prompt, from the first `request.body.system` in the Trace (D21).

    From the Trace, not from the Manifest: what the Target was actually sent is the only
    thing a Score can honestly judge it against. Absent — an Adapter that could not see
    the prompt — the head says so, and `prompt_adherence` alone scores that
    `unverifiable`, because it is the one Eval whose question is the prompt itself. The
    Simulated User's own request is never read as the Target's (decision 59).
    """
    for event in judged_events(resolve_blobs(events)):
        if event.type != "request":
            continue
        body = (event.model_extra or {}).get("body") or {}
        text = system_text(body.get("system"))
        if text is not None:
            return text
    return None


# --- the head every judged Eval's prompt shares (D23) ---

JUDGE_ROLE = (
    "You are the Judge in agentdiag, a profiler for agentic systems. You are judging one "
    "Trial of one Scenario against a Target."
)
"""Who the Judge is. Every Eval's `intro` opens with it and then asks its one question."""

NO_REASON = "none"
"""The output schema's spelling of "this Verdict carries no reason"."""

REASON_SCHEMA: dict[str, Any] = {
    "type": "string",
    "enum": [NO_REASON, "evidence_missing", "evidence_truncated", "refusal", "malformed_output"],
}
"""A plain string enum carrying `"none"` rather than a nullable type: the documented
json_schema subset lists `enum`, `const` and `anyOf` but not a `"type": ["string", "null"]`
array, and a schema the API might reject at request time would turn every judged Score into
`invalid` for a reason that has nothing to do with the Target."""

SPAN_ID = r"[A-Za-z][A-Za-z0-9_]*-[0-9]+"
"""The grammar of a Span id as `TraceWriter` mints them, `f"{kind}-{n}"` (ticket 21, decision
38). One spelling for the structured-output schema below and for the fallback that reads a
decorated cite (`judged_score.CITED_SPAN`, decision 37), so the shape the schema guarantees
and the shape the fallback reads cannot drift apart."""

SPAN_ID_PATTERN = f"^{SPAN_ID}$"
"""One evidence entry, whole: anchors and character classes only, which is the part of the
documented json_schema subset `pattern` may use (read 2026-09-24). An order id such as
`NB-0688` has the same shape; the schema can only guarantee a shape, and rule 2 still drops
a cite the Trace has no Span for."""

SPAN_ID_ITEM: dict[str, Any] = {"type": "string", "pattern": SPAN_ID_PATTERN}
"""What each `evidence` entry, and each Diagnosis `cites` entry, is at the boundary."""

BASE_PROPERTIES: dict[str, Any] = {
    "verdict": {"type": "string", "enum": ["pass", "fail", "unverifiable"]},
    "reason": REASON_SCHEMA,
    "rationale": {"type": "string"},
    "evidence": {"type": "array", "items": SPAN_ID_ITEM},
}
"""The schema is the guarantee and the prompt only an instruction (decision 38): on the API
the pattern is enforced by constrained decoding, and on the Claude Code path the SDK
validates the answer against it and re-prompts. No `minItems` or `maxItems`: the documented
subset rejects the second, and an empty `evidence` is rule 2's to map, not the schema's."""

BASE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": BASE_PROPERTIES,
    "required": list(BASE_PROPERTIES),
    "additionalProperties": False,
}
"""What one Verdict looks like at the boundary (D13): Phase 4's schema without
`prompt_adherence`'s two rule lists, which are that Eval's extension."""


def extended_schema(**properties: Any) -> dict[str, Any]:
    """`BASE_OUTPUT_SCHEMA` with more properties, every one of them required."""
    merged = {**BASE_PROPERTIES, **properties}
    return {
        "type": "object",
        "properties": merged,
        "required": list(merged),
        "additionalProperties": False,
    }


PROMPT_SECTIONS_NOTE = (
    "These are the instructions the Target was given, split into numbered sections exactly "
    "as they appeared in its system prompt."
)

NO_SYSTEM_PROMPT = (
    "No system prompt was captured in this Trace, so the Target's own instructions are not known."
)

MANIFEST_PROMPT_NOTE = (
    "No system prompt was captured in this Trace; these are the instructions the Manifest's "
    "`system` prompt pointer holds, which the Target is believed to have been given."
)
"""What the head says when the sections came from the Manifest, not the Trace (note 7,
phase-6 decision 15): the Judge is told whose word the prompt is on."""

SUPPRESSIONS_NOTE = (
    "The Target's authors have recorded these known false fails. agentdiag has already "
    "checked each one's effective-date window against this Trace's start: every Suppression "
    "listed here is in force for this Trace, whatever dates its messages or tool results "
    "carry. A Trace that matches a listed pattern is not a fail on that account. Name the "
    "Suppression's id in your rationale whenever it changed your Verdict."
)
"""The Suppressions section's preamble (phase-6 decision 16). The section is present only
when one is in force, so a Trial with none renders the prompt it rendered before."""

SUPPRESSIONS = "{*suppressions}- {suppression_id}: {pattern} (why: {why})\n{/suppressions}"
"""One line per Suppression in force: its id, its pattern and why it exists."""

NOTES_NOTE = (
    "The Target's authors wrote these notes about judging it: known false-fail patterns and "
    "conventions of this Target. They are reproduced verbatim. They clarify the Target; they "
    "never excuse a Verdict the Trace contradicts."
)

NO_NOTES = "This Target has no calibration notes."

GROUND_TRUTH_NOTE = "The right answer, as the Scenario's author recorded it:"

TRACE_NOTE = (
    "Everything that happened in this Trial, in order. Each line that belongs to a Span "
    "starts with that Span's id in square brackets, such as `[llm_call-2]` or "
    "`[tool_call-1]`. Those ids are the only evidence you may cite. A tool's real result "
    "appears on a `tool/result` line; what the Target told the user about a tool result is a "
    "claim, not a result."
)


def bare_span_ids(field: str) -> str:
    """The sentence that asks for one bare Span id per entry of `field` (decision 38): the
    first live Judge call through Claude Code cited whole rendered Trace lines."""
    return (
        f"Each entry of `{field}` is one bare Span id exactly as it appears inside the "
        "brackets, such as `llm_call-2`: not the brackets, not the line it starts."
    )


CITING = (
    "A `pass` or `fail` with no cited Span ids is discarded by agentdiag and recorded as "
    f"`unverifiable`. {bare_span_ids('evidence')} Cite only ids that appear in the Trace "
    "above. Do not cite a Turn Span when a more specific Span inside it shows the behaviour."
)

SCENARIO = (
    """\
- id: {scenario_id}
- title: {scenario_title}
- notes: {?scenario_notes}{scenario_notes}{/scenario_notes}{!scenario_notes}(none){/scenario_notes}\
{?goal}
- goal: {goal}{/goal}{?user_goal}
- user goal: {user_goal}{/user_goal}{?ground_truth}

## Ground truth

"""
    + GROUND_TRUTH_NOTE
    + """

{ground_truth}{/ground_truth}"""
)
"""The Scenario section: `goal` is the `goal` declaration's `expected` (D23's "the
Scenario's goal"), `user goal` a `simulate` Turn's, and `## Ground truth` the author's
right answer as YAML (the Judge half of ticket 03's ground truth)."""

SECTIONS = (
    "{*prompt_sections}{?preamble}Section 0 ({preamble}): {text}{/preamble}"
    "{!preamble}Rule {number}: {text}{/preamble}\n\n{/prompt_sections}"
)
"""How each of the Target's prompt sections is labelled: the preamble, then `Rule N`."""

CHECKLIST = """\
## Before you answer, check these five things

1. Did I read every line of the Trace above, not a summary of it, and does my rationale \
rest on lines I can point to?
2. Does any quote I rely on come from a field marked truncated or not observed? If so, I \
cannot rest a `pass` or `fail` on it.
3. Did I take tool results from `tool/result` lines, and not from what the Target said \
about them?
4. Did every user Turn in the Scenario actually run, and was the premise the rules assume \
actually present in the Trace? If not, the Verdict is `unverifiable`, not `fail`.
5. Is every rule I cite quoted verbatim from the prompt sections above, with its number? \
If I cannot quote it, it is not a rule.

Answer only in the structured format requested."""
"""The reverse-hallucination checklist adapted to Spans,
verbatim from `prompt_adherence.v1`, and the tail of every Judge prompt (D23)."""


class JudgePromptParts(BaseModel):
    """What one judged Eval adds to the shared head (phase-5 interfaces, ticket 05)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    eval: str
    version: str
    """The prompt's identity, recorded on every Score's `source` (D13)."""

    intro: str
    """Who the Judge is and its one question: `JUDGE_ROLE`, then the Eval's own words."""

    how_to_decide: str
    output_schema: dict[str, Any]
    """`BASE_OUTPUT_SCHEMA` or an extension of it, sent as the structured-output format."""

    output_model: type[BaseModel] = Field(exclude=True)
    """What the Judge's answer is validated as; a response it refuses is `invalid`."""

    extra_sections: list[tuple[str, str]] = Field(default_factory=list)
    """`(heading, body)` pairs placed after the Trace, such as `("## The rules", ...)`. A
    body is template text: its fixed wording and the slots the Eval fills when it asks."""

    requires_system_prompt: bool = False
    """True only for `prompt_adherence`: with no system prompt in the Trace there is
    nothing to judge against, so `render_judge_prompt` returns None."""

    own_template: str | None = None
    """A prompt that is not over the Judge head (phase-5 decision 54): the stop check's,
    the Simulated User reviewer's and the Simulated User's own. When set, `template()` is
    it verbatim — still the text `run.json` records and the Fingerprint hashes — and
    `intro`, `how_to_decide` and `extra_sections` are the module's own words, which its
    template holds. The six judged Evals and the Diagnosis leave it None."""

    def template(self) -> str:
        """Every fixed word this Eval's Judge reads, with slots for the Trial's data: what
        `run.json` records as the prompt's text, and what `fingerprint` hashes."""
        if self.own_template is not None:
            return self.own_template
        blocks = [
            self.intro,
            f"## The Scenario\n\n{SCENARIO}",
            "## The Target's prompt sections\n\n"
            "{?prompt_sections}"
            f"{{?manifest_prompt}}{MANIFEST_PROMPT_NOTE}{{/manifest_prompt}}"
            f"{{!manifest_prompt}}{PROMPT_SECTIONS_NOTE}{{/manifest_prompt}}"
            f"\n\n{SECTIONS}{{/prompt_sections}}"
            f"{{!prompt_sections}}{NO_SYSTEM_PROMPT}{{/prompt_sections}}",
            "## Calibration notes for this Target\n\n"
            f"{{?notes}}{NOTES_NOTE}\n\n{{notes}}{{/notes}}{{!notes}}{NO_NOTES}{{/notes}}"
            "{?suppressions}\n\n## Suppressions in force for this Trace\n\n"
            f"{SUPPRESSIONS_NOTE}\n\n{SUPPRESSIONS}{{/suppressions}}",
            f"## The Trace\n\n{TRACE_NOTE}\n\n{{trace}}",
            *(f"{heading}\n\n{body}" for heading, body in self.extra_sections),
            f"## How to decide\n\n{self.how_to_decide}\n\n{CITING}",
            CHECKLIST,
        ]
        return "\n\n".join(blocks) + "\n"

    def fingerprint(self) -> str:
        """sha256 of `template()`, as `run.json` records it (ADR-0005 §3)."""
        return hashlib.sha256(self.template().encode("utf-8")).hexdigest()


def asks_for(request: Mapping[str, Any], schema: Mapping[str, Any]) -> bool:
    """Whether a recorded request body asks for structured output in exactly `schema`: the
    one way agentdiag tells its own requests apart in a recording — the Diagnosis's, the stop
    check's, the reviewer's, the Simulated User's (amended decision 51). Here, below every
    prompt module, so each can use it without importing another."""
    output_config = request.get("output_config")
    if not isinstance(output_config, Mapping):
        return False
    output_format = output_config.get("format")
    return isinstance(output_format, Mapping) and output_format.get("schema") == schema


class RecordedPrompt(BaseModel):
    """One prompt as `run.json` records it (ADR-0005 §3, ADR-0003 §7): a judged Eval's, the
    Diagnosis's, the stop check's, the reviewer's or the Simulated User's (phase-5 decision
    52; it was `JudgePrompt`). The full text, not a pointer."""

    version: str
    text: str
    """The template: every fixed word, every Trial-specific body a slot."""

    fingerprint: str

    @classmethod
    def of(cls, parts: JudgePromptParts) -> RecordedPrompt:
        return cls(version=parts.version, text=parts.template(), fingerprint=parts.fingerprint())


class JudgeContext(BaseModel):
    """Everything one judged Eval may read about one finished Trial."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    scenario: Scenario
    declaration: EvalDeclaration
    events: Sequence[Event]
    """The Trace's Events with blobs resolved."""

    spans: Sequence[Span]
    fidelity: Fidelity
    """The Trace's: what a Score records when no Span says less."""

    notes: JudgeNotes | None
    tool_kinds: Mapping[str, str]
    """The Manifest's `tools.<name>.kind`, by tool name."""

    manifest_prompts: Mapping[str, str] = Field(default_factory=dict)
    """The text of every path pointer in the Manifest's `prompts`, by name, read once at
    preflight (note 7, phase-6 decision 15); `system`'s is the fallback when the Trace holds
    no system prompt. Empty for `observed` pointers, whose text only a Trace can hold."""

    suppressions: Sequence[Suppression] = ()
    """Every Suppression the Manifest records (decision 16); which are in force for this
    Eval over this Trace is `suppressions_in_force`."""

    @property
    def suppressions_in_force(self) -> list[Suppression]:
        """The Suppressions whose Eval is this declaration's (or `*`) and whose window holds
        the Trace's start: what the Judge is shown, and what its Fingerprint covers."""
        return in_force(self.suppressions, self.declaration.eval, self.events)

    @classmethod
    def of(
        cls,
        scenario: Scenario,
        declaration: EvalDeclaration,
        events: Sequence[Event],
        *,
        fidelity: Fidelity,
        notes: JudgeNotes | None,
        tool_kinds: Mapping[str, str],
        manifest_prompts: Mapping[str, str] | None = None,
        suppressions: Sequence[Suppression] = (),
    ) -> JudgeContext:
        """The context for a Trace as read from disk: blobs resolved, Spans projected."""
        resolved = resolve_blobs(events)
        return cls(
            scenario=scenario,
            declaration=declaration,
            events=resolved,
            spans=project_spans(resolved),
            fidelity=fidelity,
            notes=notes,
            tool_kinds=dict(tool_kinds),
            manifest_prompts=dict(manifest_prompts or {}),
            suppressions=list(suppressions),
        )

    def eval_context(self) -> EvalContext:
        """What the mechanical Evals read, for a judged Eval with a mechanical form."""
        return EvalContext(
            scenario=self.scenario,
            declaration=self.declaration,
            events=self.events,
            spans=self.spans,
            fidelity=self.fidelity,
            forbidden_phrases=None,
            tool_kinds=cast(Mapping[str, ToolKind], self.tool_kinds),
        )


def render_judge_prompt(
    parts: JudgePromptParts, context: JudgeContext, **extra: Value
) -> str | None:
    """The whole Judge prompt for one Eval over one Trial: the parts' template, filled with
    the Trial's data and whatever the Eval's own sections read (`extra`).

    None only when the parts require the Target's system prompt and the Trace holds none.
    """
    values = trial_values(context)
    if values["prompt_sections"] == [] and parts.requires_system_prompt:
        return None
    return fill(parts.template(), {**values, **extra})


def trial_values(context: JudgeContext) -> dict[str, Value]:
    """Everything the head's slots read from one Trial: data, never wording."""
    scenario = context.scenario
    system = system_prompt(context)
    user_goal = next(
        (turn.simulate.goal for turn in scenario.turns if not isinstance(turn, str)), None
    )
    truth = (
        yaml.safe_dump(
            scenario.ground_truth, sort_keys=False, allow_unicode=True, default_flow_style=False
        ).strip()
        if scenario.ground_truth is not None
        else None
    )
    return {
        "scenario_id": scenario.id,
        "scenario_title": scenario.title,
        "scenario_notes": scenario.notes,
        "goal": scenario_goal(scenario),
        "user_goal": user_goal,
        "ground_truth": truth,
        "prompt_sections": section_values(system.text) if system.text is not None else [],
        "manifest_prompt": FROM_MANIFEST if system.from_manifest else None,
        "notes": context.notes.text if context.notes is not None else None,
        "suppressions": [
            {"suppression_id": item.id, "pattern": item.pattern, "why": item.why}
            for item in context.suppressions_in_force
        ],
        "trace": render_trace_for_judge(context.events),
    }


FROM_MANIFEST = "manifest"
"""The `manifest_prompt` slot's value when the sections came from the Manifest: any text
keeps the `{?manifest_prompt}` blocks, which hold every word the Judge reads about it."""


class SystemPrompt(NamedTuple):
    """The Target's system prompt as the Judge is shown it, and whose word it is."""

    text: str | None
    from_manifest: bool = False


def system_prompt(context: JudgeContext) -> SystemPrompt:
    """The prompt the Judge judges against (note 7, phase-6 decision 15): the Trace's when
    it holds one — what the Target was actually sent — else the text of the Manifest's
    `system` prompt pointer, which the head then says came from the Manifest, else none,
    which the head says too. The one caller of `system_prompt_from`."""
    captured = system_prompt_from(context.events)
    if captured is not None:
        return SystemPrompt(captured)
    pointed = context.manifest_prompts.get(SYSTEM_PROMPT_POINTER)
    if pointed is not None and pointed.strip():
        return SystemPrompt(pointed, from_manifest=True)
    return SystemPrompt(None)


def scenario_goal(scenario: Scenario) -> str | None:
    """The `expected` of the Scenario's `goal` declaration, when it declares one."""
    for declaration in scenario.evals:
        if declaration.eval == "goal":
            expected = declaration.params.get("expected")
            if isinstance(expected, str) and expected.strip():
                return expected
    return None


__all__ = [
    "BASE_OUTPUT_SCHEMA",
    "CHECKLIST",
    "FROM_MANIFEST",
    "FULL_FIDELITY",
    "JUDGE_ROLE",
    "MANIFEST_PROMPT_NOTE",
    "NOT_OBSERVED",
    "NO_REASON",
    "PREAMBLE",
    "SPAN_ID",
    "SPAN_ID_ITEM",
    "SPAN_ID_PATTERN",
    "STOP_WHEN",
    "SUPPRESSIONS",
    "SUPPRESSIONS_NOTE",
    "JudgeContext",
    "JudgePromptParts",
    "RecordedPrompt",
    "SystemPrompt",
    "asks_for",
    "bare_span_ids",
    "extended_schema",
    "judged_events",
    "prompt_sections",
    "render_judge_prompt",
    "render_sections",
    "render_trace_for_judge",
    "scenario_goal",
    "section_values",
    "system_prompt",
    "system_prompt_from",
    "trial_values",
]
