"""The text `agentdiag init` writes: a Manifest skeleton and a sample Suite (D37, D39).

Rendered from one template rather than assembled by a YAML dumper, because the comments
are the greater part of what is written. A Manifest is edited by hand from the day it is
created, and a dumped mapping teaches nobody what `side_effects` means or why `tools` is a
pointer; the comments here are what make the skeleton something a developer can extend
instead of a shape they have to look up.

`init` writes three files into a Target directory, `.agentdiag/targets/<slug>/` (ADR-0013):
the Manifest, the sample Suite, and the Judge's calibration-notes starter (ticket 05). The
checked-in `examples/toy/.agentdiag/targets/toy-order-desk/` is what this module renders from
`EXAMPLE_SCAFFOLD`, byte for byte, and `examples/workspace/`'s `order-desk` is what `init
--target order-desk` renders (phase-6 decision 47); `tests/test_init_cli.py` asserts both — so
the example directories stay the truth of what `init` writes, and a change here that they do
not follow fails the suite rather than drifting quietly.

**Every interpolated value goes through `scalar()`.** A Manifest is YAML, and a `--model`
or `--tools` a caller typed can hold a colon, a `#` or a quote. Pasting one in raw writes a
file that either fails to parse or — worse — parses into something the caller did not say,
which is a Target driven by a configuration nobody wrote. `scalar()` quotes whatever needs
it and leaves plain words alone, so the common case still reads as prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import yaml

from agentdiag.eval.notes import JUDGE_NOTES_MAX_WORDS
from agentdiag.types import ToolKind
from agentdiag.workspace import DEFAULT_SLUG, JUDGE_NOTES_NAME

TOY_FACTORY = "agentdiag.examples.toy:make_target"
TOY_TOOLS = "agentdiag.examples.toy:make_tools"
TOY_DEPLOYED = "agentdiag.examples.toy:deployed_set"
"""The toy's deployed set, which the in-process Connector reads (phase-6 decision 22)."""
DEFAULT_MODEL = "claude-sonnet-5"
"""What the toy Target runs on, and what a Target with no `--model` is assumed to run on.
Deliberately not the Judge's default: a Target and a Judge on one model would trip the
self-preference warning (D23)."""

SAMPLE_SUITE_NAME = "suites/sample.yaml"
"""Where `init` puts the Suite, relative to the Target directory. The example's is `orders.yaml`,
because a Suite is named for the Scenarios it holds once someone has written them."""


@dataclass(frozen=True)
class Scaffold:
    """Everything the two scaffolded files differ by, and nothing else.

    A Target agentdiag knows (the toy) and a Target it has only been pointed at differ in
    four places: who they are, what drives them, what tools they declare, and what the one
    sample Scenario can honestly say. Holding those four here keeps one template for both,
    so the toy scaffold cannot acquire an explanation the custom one lacks.
    """

    target_name: str
    target_description: str
    factory: str
    tools: str | None
    model: str
    suite_description: str
    scenario_id: str
    scenario_title: str
    scenario_tags: list[str] = field(default_factory=list)
    turn: str = ""
    scenario_note: str | None = None
    """A comment above the Scenario, for a sample nobody should keep as written."""

    suite_path: str = SAMPLE_SUITE_NAME
    """The Suite file this Manifest names, relative to the Target directory."""

    root_hint: str = "."
    """What the `--root` in the Manifest's own comment says, so the example's copy cites
    the path a reader of this repository would actually type."""

    tool_kinds: dict[str, ToolKind] = field(default_factory=dict)
    """The Manifest's `tools` section, `{name: kind}`: known for the toy, whose lookup is a
    `retrieval` (D37); a Target `init` has never run gets a comment instead of a guess."""

    more_suites: tuple[str, ...] = ()
    """Suites the Manifest names after the sample one. `init` writes none; the example
    names the one that holds its Suite-level guardrail rules (ticket 05), which a Suite
    shared with `init`'s sample Scenario could not carry without that Scenario inheriting
    them."""

    deployed: str | None = None
    """The `module:attr` the in-process Connector reads the deployed set from: known for the
    toy; a Target `init` has never run gets the `connector` block as a comment instead of a
    guess (decision 22)."""

    slug: str = DEFAULT_SLUG
    """The Target's slug: the name of its Target directory under `.agentdiag/targets/`, and
    what `--target` selects it by (ADR-0013 §4). Not `target_name`, which is what a Report
    prints and may be anything."""

    family: str | None = None
    channel: str | None = None
    """The persona this Target is one channel of, and which channel (ADR-0013 §3): known for
    the toy, the chat channel of Northwind Bicycles; a Target `init` has never run gets both
    as a comment instead of a guess."""

    @property
    def directory_words(self) -> str:
        """What the Manifest's comments call the directory its paths are relative to."""
        return "this Target directory"


TOY_SCAFFOLD = Scaffold(
    target_name="toy-order-desk",
    target_description=(
        "An order desk with five numbered rules, one lookup tool and one action tool."
    ),
    factory=TOY_FACTORY,
    tools=TOY_TOOLS,
    model=DEFAULT_MODEL,
    suite_description="What the order desk must do when a customer asks to cancel.",
    scenario_id="cancel-processing-order",
    scenario_title="Cancel an order that is still processing",
    scenario_tags=["orders", "cancel"],
    turn="Hi, I'd like to cancel order NB-1042.",
    tool_kinds={"lookup_order": "retrieval", "cancel_order": "action"},
    deployed=TOY_DEPLOYED,
    family="northwind",
    channel="chat",
)
"""The shipped toy Target: what `init` writes when no `--adapter` names another one, so a
first `agentdiag run` works with nothing edited and no Adapter written."""

EXAMPLE_SCAFFOLD = replace(
    TOY_SCAFFOLD,
    suite_path="suites/orders.yaml",
    root_hint="examples/toy",
    more_suites=("suites/guardrails.yaml",),
    slug="toy-order-desk",
)
"""What `examples/toy/.agentdiag/targets/toy-order-desk/` holds. It differs from what `init`
writes in the four places a reader of this repository can see: the Suite is named for the
Scenarios in it, the Manifest's own comment cites the `--root` that reaches it from the
repository root, the Manifest names a second Suite, whose Suite-level guardrail rules
exercise `guardrails` without every Scenario of the first inheriting them, and its slug is
the Target's name (ticket 13 moved it there from the Phase 4 spelling).

`tests/test_init_cli.py` asserts that rendering this produces the Manifest byte for byte,
and the Suite's opening byte for byte up to `EXAMPLE_ONLY`. That test is the gate: the
example is regenerated by rendering it, and a template change that nobody carried into the
example fails rather than drifting quietly."""

EXAMPLE_ONLY = (
    "  # ---------------------------------------------------------------------------------\n"
    "  # Everything below is the example's own: `agentdiag init` writes only the Scenario\n"
    "  # above. These exercise every mechanical and judged Eval against the toy Target\n"
    "  # (guardrails, whose rules are Suite-level, in suites/guardrails.yaml), and replay\n"
    "  # from tests/fixtures/recordings/toy-orders.jsonl.\n"
)
"""Where the example's Suite stops being what `init` writes (ticket 04).

The sample Suite `init` scaffolds holds one Scenario on purpose — it is the first thing a
developer edits — while the example also carries the Scenarios that exercise every
mechanical Eval. They are authored below this marker, so the template stays one Scenario
and the gate still pins every byte above it."""


class UnwritableValue(ValueError):
    """A value that cannot go into a scaffolded file as one scalar."""


def scalar(value: str, *, quoted: bool = False) -> str:
    """One YAML scalar: the value as written when that is safe, quoted when it is not.

    A caller's `--model`, `--tools` or `--adapter` is arbitrary text, and YAML reads
    `a: b` as a mapping, `gpt # real` as a value with a comment, and a newline as the end
    of the line. Any of those pasted in raw would write a Manifest that fails to load or,
    worse, loads as something nobody typed. So every value is round-tripped: if reading the
    plain form back gives the same string, it is written plain and the file still reads as
    prose; otherwise it is written as a double-quoted, escaped scalar.

    A newline is refused rather than quoted. `init` writes files a human then edits, and a
    value that spans lines has no honest one-line form to hand back.

    `quoted=True` keeps the quotes even when the plain form would do. A Turn is literal
    user text, and an author editing one should see where the message begins and ends
    rather than discover it by deleting a word.
    """
    if "\n" in value or "\r" in value:
        raise UnwritableValue(f"{value!r} spans more than one line; a scaffolded value is one line")
    try:
        if not quoted and yaml.safe_load(value) == value:
            return value
    except yaml.YAMLError:
        pass
    return (
        yaml.safe_dump(value, default_style='"', default_flow_style=True, width=10**6)
        .strip()
        .rstrip("\n")
    )


PLACEHOLDER_DESCRIPTION = "Written by `agentdiag init`; say here what this Target does."
"""The description `init` writes for a Target it has only been pointed at: a placeholder
`discover` marks for review (walkthrough friction 10)."""


def custom_scaffold(*, target_name: str, factory: str, tools: str | None, model: str) -> Scaffold:
    """The scaffold for a Target agentdiag has only been pointed at.

    Its sample Scenario says the least that is still true of any Target: that a first user
    message gets an answer. Anything more would be a guess about a Target this command has
    never run, and a Scenario that guesses wrong scores a Target for the wrong thing.
    """
    return Scaffold(
        target_name=target_name,
        target_description=PLACEHOLDER_DESCRIPTION,
        factory=factory,
        tools=tools,
        model=model,
        suite_description="The first Scenarios written for this Target.",
        scenario_id="first-turn",
        scenario_title="The Target answers a first message",
        turn="Hello! What can you help me with today?",
        scenario_note=(
            "Replace this Scenario with one of your own: the Turns a real user sends, and "
            "what you want judged about the answer."
        ),
    )


JUDGE_NOTES_STARTER = f"""<!--
Calibration notes for the Judge (ADR-0003 section 8). docs/judge-notes.md has the guidance.

What belongs here: known false-fail patterns, each as the Verdict a Judge gets wrong for
this Target and why; and conventions of this Target a Judge cannot learn from its prompt.
What does not: rules for the Target (those are its prompt, or a Suite's guardrails), and
anything that would excuse a failure the Trace shows.

Everything outside this comment reaches the Judge verbatim, in every judged Eval's prompt
and in the Diagnosis, and is part of every judged Score's Judge Fingerprint, so an edit
shows in a comparison. At most {JUDGE_NOTES_MAX_WORDS} words; a Run refuses more.

When the Target and the Judge run on the same model, say so here: a model judging its own
output tends to favour it, and agentdiag warns of that on every Score it touches.
-->
"""
"""What `init` writes as the Target's `judge_notes.md`: the guidance, as a comment the Judge
never reads, and no notes yet (`agentdiag.eval.notes`)."""


def render_judge_notes(scaffold: Scaffold) -> str:
    """The calibration-notes starter: the same for every Target until someone writes one."""
    del scaffold  # one starter for every Target: the guidance does not depend on it
    return JUDGE_NOTES_STARTER


EVAL_PARAMETERS_COMMENT = """\
# Eval parameters, read by the Evals and never shown to the Judge: a default threshold for
# the latency Evals, and the JSON type each tool argument must have. Forbidden phrases stay
# in `forbidden_phrases`, their one home.
# eval_parameters:
#   latency: {response_max_ms: 30000, first_token_max_ms: 5000, tool_max_ms: 1000}
#   tool_argument_types: {lookup_order: {order_id: string}}

"""
"""What the Manifest says of `eval_parameters` (phase-6 decision 17), commented out: a
Target has none until someone decides its thresholds."""

TARGET_COMMENT = "# Who the Target is. The name is what a Report and a comparison print.\n"
ADAPTER_COMMENT = (
    "# How agentdiag drives it. The Adapter is the only thing that touches a Target (ADR-0001).\n"
)
TOOLS_COMMENT = """\
# The Target's tools, by name. `kind: retrieval` marks a lookup, recorded as a
# `retrieval` Span; `action`, the default, marks a tool that changes something.
"""
"""The comments above three Manifest sections, shared by `init`'s scaffold and
`discover`'s draft so the two explain a section in the same words."""


def prompts_comment(directory_words: str) -> str:
    """The comment above `prompts`, naming what its paths are relative to."""
    return f"""# The Target's prompts, by name: a path relative to {directory_words}, or
# `observed` for one the Connector reads or the Adapter observes from the running Target.
# `system` is the system prompt; `agentdiag sync` fingerprints each one section by section.
"""


def suites_comment(directory_words: str) -> str:
    """The comment above `suites`, naming what its paths are relative to."""
    return f"""# Where the Scenarios live, relative to {directory_words}. A bare path runs;
# `{{path: …, status: draft}}` is validated and never run, and `retired` is neither.
"""


CONNECTOR_COMMENT = """\
# The management side (ADR-0011): the Connector reads the deployed set, and the Evidence
# stores where the Target has them, without running a Turn, and `agentdiag sync` takes its
# read first. `inprocess` reads a Python module's deployed set; a platform's Connector is a
# plugin. `credentials`, when an environment needs them, maps a role to the NAME of an
# environment variable, never to a value.
"""
"""What the Manifest says above its `connector` block, written or commented out."""


def render_connector(scaffold: Scaffold) -> str:
    """The `connector` block: the toy's, naming its deployed set; for a Target `init` has
    never run, the same block as a comment, since its deployed set is not known."""
    if scaffold.deployed is None:
        return (
            CONNECTOR_COMMENT
            + """# connector:
#   kind: inprocess
#   environments:
#     local:
#       deployed: your_package.module:deployed_set

"""
        )
    return (
        CONNECTOR_COMMENT
        + f"""connector:
  kind: inprocess
  environments:
    local:
      # `module:attr`: a mapping of `prompts`, `tools` and `model` as the running Target
      # holds them, or a callable returning one, read afresh on every `sync`.
      deployed: {scalar(scaffold.deployed)}

"""
    )


FAMILY_COMMENT = """\
# The persona this Target is one channel of: Targets naming one `family` are one agent's
# chat, voice or other builds, and `agentdiag registry` lists them together (ADR-0013 §3).
"""


def render_family(scaffold: Scaffold) -> str:
    """`family` and `channel`: the toy's, or, for a Target `init` has never run, the same two
    lines as a comment, since which persona it belongs to is its author's to say."""
    if scaffold.family is None and scaffold.channel is None:
        return FAMILY_COMMENT + "# family: your-persona\n# channel: chat\n\n"
    lines = "".join(
        f"{key}: {scalar(value)}\n"
        for key, value in (("family", scaffold.family), ("channel", scaffold.channel))
        if value is not None
    )
    return f"{FAMILY_COMMENT}{lines}\n"


def render_manifest(scaffold: Scaffold) -> str:
    """The Manifest skeleton: the sections a Run reads, each one explained (D37)."""
    tool_kinds = "".join(
        f"  {scalar(name)}: {{kind: {scalar(kind)}}}\n"
        for name, kind in scaffold.tool_kinds.items()
    )
    tools_section = (
        f"""{TOOLS_COMMENT}tools:
{tool_kinds}
"""
        if scaffold.tool_kinds
        else """# The Target's tools can be listed here by name, each with `kind: retrieval`
# (a lookup, recorded as a `retrieval` Span) or `kind: action` (the default):
# tools:
#   lookup_order: {kind: retrieval}

"""
    )
    tools_lines = (
        f"""      # `module:attr` returning the tool callables, resolved once per session so the
      # Target's own state is never shared between Trials.
      tools: {scalar(scaffold.tools)}
"""
        if scaffold.tools is not None
        else """      # No tools were declared. Add `tools: module:attr` — a mapping of tool name to
      # callable, or a callable returning one — and the Adapter wraps every one of them.
"""
    )
    more_suites = "".join(f"  - {scalar(path)}\n" for path in scaffold.more_suites)
    words = JUDGE_NOTES_MAX_WORDS
    notes_section = f"""\
# The Judge's calibration notes, relative to {scaffold.directory_words}: known
# false-fail patterns, at most {words} words, read verbatim by every judged Eval and part of
# each judged Score's Judge Fingerprint (ADR-0003 section 8).
judge_notes: {scalar(JUDGE_NOTES_NAME)}

"""
    middle = render_connector(scaffold) + tools_section + notes_section + EVAL_PARAMETERS_COMMENT
    return f"""# The Target's identity document (D37, ADR-0005 section 1).
#
# Pointers, not copies: nothing here holds a prompt, a tool schema or a secret. `agentdiag
# run --root {scaffold.root_hint}` reads this file and nothing above it.
schema_version: 1

{TARGET_COMMENT}target:
  name: {scalar(scaffold.target_name)}
  description: {scalar(scaffold.target_description)}

{render_family(scaffold)}{ADAPTER_COMMENT}adapter:
  # `inprocess` drives a Python Target in this process and captures at its two boundaries.
  kind: inprocess
  # What a Run against this Target does to the world, one of three classes: `none`, nothing
  # outside this process changes; `sandboxed`, it writes only to a test system; `live`, it
  # reaches real users or data, and a Run is refused without a flag. A tool entry may carry
  # its own `side_effects`, and `agentdiag validate` names the classes when one is wrong.
  side_effects: none
  environments:
    # Which environment a Run opens when nothing else says.
    default: local
    # One block per environment. `protected: true` marks one that is never pushed to
    # without confirmation; prod, staging and eu_prod are protected unless their block says
    # `protected: false`. A block may raise the side-effect class for itself, never lower it.
    local:
      # `module:attr`, called with (client, tools, **options). The Adapter hands in the
      # client, so it can record every exchange without the Target knowing (D6).
      factory: {scalar(scaffold.factory)}
{tools_lines}      # Passed to the factory as an option. Not the Judge's model: a Target and a Judge
      # on the same model would trip the self-preference warning (D23).
      model: {scalar(scaffold.model)}

{prompts_comment(scaffold.directory_words)}prompts: {{system: observed}}

{middle}{suites_comment(scaffold.directory_words)}suites:
  - {scalar(scaffold.suite_path)}
{more_suites}"""


def render_suite(scaffold: Scaffold) -> str:
    """The sample Suite: one Scenario, one Turn, one Eval, and why each line is there."""
    note = (
        f"  # {_wrapped_comment(scaffold.scenario_note)}\n  #\n" if scaffold.scenario_note else ""
    )
    tags = (
        f"    # Tags select without naming ids (`--tag {scaffold.scenario_tags[0]}`), and appear "
        f"in the Run record.\n    tags: [{', '.join(scaffold.scenario_tags)}]\n"
        if scaffold.scenario_tags
        else ""
    )
    return f"""# One Suite: an authored collection of Scenarios for one Target (CONTEXT.md).
schema_version: 1

# The Target these Scenarios are written against, by the name in the Manifest.
target: {scalar(scaffold.target_name)}
description: {scalar(scaffold.suite_description)}

scenarios:
{note}  # The id is how `--scenario` selects this Scenario and how a comparison joins it across
  # Runs, so it is stable even when the title is reworded.
  - id: {scalar(scaffold.scenario_id)}
    title: {scalar(scaffold.scenario_title)}
{tags}    # Literal user messages, in order. One Turn is one user message and the Target's
    # complete response to it. A `- simulate: {{goal, stop_when}}` Turn, with `max_turns` on
    # the Scenario, would hand the rest of the conversation to a Simulated User. Every
    # field and every Eval's parameters: .claude/skills/agentdiag-generate/scenario-reference.md,
    # which `agentdiag init --skills` installs.
    turns:
      - {scalar(scaffold.turn, quoted=True)}
    # The judgements applied to this Trace. `- prompt_adherence` is the same declaration
    # written short; the long form takes an `id` and `params` as well. Check the file
    # offline with `agentdiag validate`.
    evals:
      - eval: prompt_adherence
"""


def _wrapped_comment(text: str, width: int = 86) -> str:
    """One comment sentence folded onto continuation lines that are still comments."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n  # ".join(lines)


__all__ = [
    "ADAPTER_COMMENT",
    "CONNECTOR_COMMENT",
    "DEFAULT_MODEL",
    "EVAL_PARAMETERS_COMMENT",
    "EXAMPLE_ONLY",
    "EXAMPLE_SCAFFOLD",
    "FAMILY_COMMENT",
    "JUDGE_NOTES_STARTER",
    "PLACEHOLDER_DESCRIPTION",
    "SAMPLE_SUITE_NAME",
    "TARGET_COMMENT",
    "TOOLS_COMMENT",
    "TOY_DEPLOYED",
    "TOY_SCAFFOLD",
    "Scaffold",
    "UnwritableValue",
    "custom_scaffold",
    "prompts_comment",
    "render_connector",
    "render_family",
    "render_judge_notes",
    "render_manifest",
    "render_suite",
    "scalar",
    "suites_comment",
]
