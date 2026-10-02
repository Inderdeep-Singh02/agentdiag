"""The text `agentdiag init` writes: a Manifest skeleton and a sample Suite (D37, D39).

Rendered from one template rather than assembled by a YAML dumper, because the comments
are the greater part of what is written. A Manifest is edited by hand from the day it is
created, and a dumped mapping teaches nobody what `side_effects` means or why `tools` is a
pointer; the comments here are what make the skeleton something a developer can extend
instead of a shape they have to look up.

`init` writes its files into a Target directory, `.agentdiag/targets/<slug>/` (ADR-0013):
the Manifest, the sample Suite, the Calibration Notes starter (ticket 05), the
Maintainer notes starter every skill reads first and the Judge never does (ADR-0016 §5),
and the local redaction starter (ADR-0015 §4). The
checked-in `examples/toy/.agentdiag/targets/toy-order-desk/` is what this module renders from
`EXAMPLE_SCAFFOLD`, byte for byte, and `examples/workspace/`'s `order-desk` is what `init
--target order-desk --adapter toy` renders (phase-6 decision 47); `tests/test_init_cli.py`
asserts both — so the example directories stay the truth of what `init` writes, and a change
here that they do not follow fails the suite rather than drifting quietly.

**Three scaffolds, one template** (ADR-0016 §4): the toy, a Target of one's own
(`custom_scaffold`, driven by a factory), and a Target described before anything drives it
(`pending_scaffold`), whose Adapter is `pending` and whose holes (the description, the
persona, the kind, the environments, the prompts, the Connector) each sit under a
`# REVIEW:` line in the shape `discover` writes. The Adapter block is rendered per kind, one
renderer each (`ADAPTER_RENDERERS`), and the other holes only for a pending scaffold, so the
toy and custom outputs stay what the examples gate.

**Every interpolated value goes through `scalar()`.** A Manifest is YAML, and a `--model`
or `--tools` a caller typed can hold a colon, a `#` or a quote. Pasting one in raw writes a
file that either fails to parse or — worse — parses into something the caller did not say,
which is a Target driven by a configuration nobody wrote. `scalar()` quotes whatever needs
it and leaves plain words alone, so the common case still reads as prose.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import NamedTuple, cast

import yaml

from agentdiag.eval.notes import JUDGE_NOTES_MAX_WORDS
from agentdiag.run.manifest_checks import PENDING_KIND, REVIEW
from agentdiag.types import ToolKind
from agentdiag.workspace import (
    DEFAULT_SLUG,
    JUDGE_NOTES_NAME,
    MAINTAINER_NOTES_NAME,
    MANIFEST_NAME,
    REDACTION_NAME,
    TargetPaths,
)

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

    adapter_kind: str = "inprocess"
    """The Adapter kind the Manifest names: `inprocess` for the toy and a custom Target, or
    `pending` for a Target nothing drives yet (ADR-0016 §4). It picks the Adapter block's
    renderer (`ADAPTER_RENDERERS`); a pending Manifest also carries the REVIEW lines."""

    environment: str = "local"
    """The one environment the Adapter block (and the commented Connector block) names."""

    suite_status: str | None = None
    """The sample Suite's status in the Manifest: None, a bare path that runs; `draft` for a
    pending Target, whose sample Scenario no one has written yet."""

    scenario_review: str | None = None
    """A REVIEW line above the sample Scenario, for a Suite that is a draft until settled."""

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
    def pending(self) -> bool:
        """Whether nothing drives this Target yet (ADR-0016 §4): its holes are written under
        REVIEW lines."""
        return self.adapter_kind == PENDING_KIND

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
"""The shipped toy Target: what the first `init` writes, naming no Target and no Adapter, and
what `--adapter toy` writes under any slug, so a first `agentdiag run` works with nothing
edited and no Adapter written."""

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


FIRST_TURN_ID = "first-turn"
FIRST_TURN_TITLE = "The Target answers a first message"
FIRST_TURN = "Hello! What can you help me with today?"
"""The sample Scenario of a Target agentdiag has never run: its id, title and one Turn."""


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
        suite_description=PENDING_SUITE_DESCRIPTION,
        scenario_id=FIRST_TURN_ID,
        scenario_title=FIRST_TURN_TITLE,
        turn=FIRST_TURN,
        scenario_note=(
            "Replace this Scenario with one of your own: the Turns a real user sends, and "
            "what you want judged about the answer."
        ),
    )


REVIEW_DESCRIPTION = (
    "say in a sentence what this Target does and who it serves "
    "(agentdiag init --description writes it)"
)
"""Above `description` when `--description` was not given."""

REVIEW_FAMILY = (
    "name the persona this Target is one channel of and which channel it is (chat, voice), "
    "or delete both lines when it stands alone"
)
"""Above the commented `family` and `channel` when neither flag was given (one line, both
keys)."""

REVIEW_KIND = (
    "nothing drives this Target yet; replace pending with inprocess (a Python factory in this "
    "process), http (a chat endpoint with a Dialect), or a plugin's kind, and give the "
    "environment block that kind reads"
)
"""Above `kind: pending`: the kinds to choose from (decision 11)."""

REVIEW_ENVIRONMENT = (
    "one block per environment the Target runs in, protected: true on every one that reaches "
    "real users"
)
"""Above the empty environment block (decision 11)."""

REVIEW_PROMPTS = (
    "name each prompt as a path under this Target directory (prompts/system.md) once the file "
    "is here, or observed when only the running Target shows it"
)
"""Above the commented `prompts` section (decision 11)."""

REVIEW_CONNECTOR = (
    "no Connector yet: nothing reads or pushes the deployed set; a platform's Connector is a "
    "plugin kind, inprocess reads a Python module"
)
"""Above the commented `connector` block (decision 11)."""

REVIEW_SCENARIO = (
    "replace this Scenario with one of your own (the Turns a real user sends, and what to "
    f"judge of the answer), then drop status: draft from this Suite's entry in {MANIFEST_NAME}"
)
"""Above the sample Scenario in a pending Target's draft Suite (decision 11)."""

PENDING_ENVIRONMENT = "dev"
"""The one environment a pending Target's Adapter block names, empty under its REVIEW line:
the environment a Target is first described in, before anyone says which reach users."""

PENDING_SUITE_DESCRIPTION = "The first Scenarios written for this Target."
"""The sample Suite's description, as a Target of one's own gets it."""


def pending_scaffold(slug: str) -> Scaffold:
    """The scaffold for a Target described before anything drives it (ADR-0016 §4).

    `init --target <slug>` with no `--adapter` once wrote the toy under every slug, and a
    Workspace of ten Targets came out as ten toy order desks. This writes who the Target is
    (named after its slug until `--name` says otherwise), an Adapter of the `pending` kind
    that validates with a warning and refuses to drive, and the first-turn sample Scenario a
    Target of one's own gets, in a draft Suite: nothing here is a guess about a Target
    agentdiag cannot reach.
    """
    return Scaffold(
        target_name=slug,
        target_description=PLACEHOLDER_DESCRIPTION,
        factory="",
        tools=None,
        model=DEFAULT_MODEL,
        suite_description=PENDING_SUITE_DESCRIPTION,
        scenario_id=FIRST_TURN_ID,
        scenario_title=FIRST_TURN_TITLE,
        turn=FIRST_TURN,
        scenario_review=REVIEW_SCENARIO,
        adapter_kind=PENDING_KIND,
        environment=PENDING_ENVIRONMENT,
        suite_status="draft",
        slug=slug,
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


REDACTION_STARTER = """\
# The names a Change record of this Target never carries (ADR-0015 section 4): each is
# replaced by [name], beside the e-mail addresses and phone numbers every record loses.
#
# This file is local and gitignored by `agentdiag init`, so the names it lists are
# never committed and a clone starts without them. List a customer's name here when it
# must not reach a record; `agentdiag validate` warns when the file is absent.
names: []
"""
"""What `init` writes as the Target's `redaction.yaml`: the one key, and no names yet
(`agentdiag.change.redact`)."""


def render_redaction(scaffold: Scaffold) -> str:
    """The redaction starter: the same for every Target until an author lists a name."""
    del scaffold  # one starter for every Target: the names are the author's to add
    return REDACTION_STARTER


MAINTAINER_NOTES_STARTER = """<!--
Maintainer notes (ADR-0016 section 5): what whoever maintains this Target needs before
touching it. Every agentdiag skill reads this file before its first step; the Judge never
does.

What belongs here: the facts a maintenance cycle needs and nothing else records — what the
Target does and for whom, which environment is the default and which reach real users, the
traps a previous cycle fell into, where its evidence (conversations, logs, Flow runs) is
kept and how it is read. What does not: rules for judging it (judge_notes.md, which the
Judge reads), anything the Manifest already says (point at it instead), and any credential
value (a Manifest names the variable; the value lives in ~/.agentdiag/env or the shell).

At most 600 words: this file is part of a read set budgeted at 10k tokens.
-->

## What the Target does

_The job, in its owners' words._

## Who it serves

_The users and the business; the language, hours and channel they expect._

## Environments

_The default environment and what it reaches; each protected one and why; the one a fix is
verified on._

## Known traps

_What misled a previous cycle: a stale snapshot, a tool that answers differently per
environment, a name that changed._

## Where evidence lives

_Each Evidence store by kind and the command that reads it; what a store lacks (a truncated
body, no end time)._
"""
"""What `init` (every scaffold) and `discover` (for a Target it creates) write as the Target's
`maintainer_notes.md` (ADR-0016 §5, decision 15): the guidance as an HTML comment, then the
five headings a maintenance cycle needs, each with one italic placeholder. Read by every
skill before its first step, never by the Judge, so nothing under `agentdiag.eval` names it;
`target show` says `not written yet` while the text outside the comment is still this."""


def render_maintainer_notes(scaffold: Scaffold) -> str:
    """The Maintainer notes starter: the same for every Target until a maintainer fills it."""
    del scaffold  # one starter for every Target: the facts are the maintainer's to write
    return MAINTAINER_NOTES_STARTER


MAINTAINER_NOTES_COMMENT = """\
# The Maintainer notes, relative to this Target directory: read in full by every agentdiag
# skill before its first step, and never by the Judge (ADR-0016 section 5).
"""
"""The two lines above the Manifest's `maintainer_notes` key: who reads the file and who never
does. `discover` writes the same comment above the key in a draft for a Target it creates."""


class Starter(NamedTuple):
    """One file of a Target written when absent and kept on `--force`, because what an author
    writes there is work nothing else records (ADR-0003 §8, ADR-0015 §4, ADR-0016 §5)."""

    attribute: str
    """The `TargetPaths` property naming where the file goes."""

    text: str
    """The starter: the same for every Target."""

    note: str
    """What `discover`'s summary says beside the path when it writes the file."""

    def path(self, target: TargetPaths) -> Path:
        """Where the file goes in `target`."""
        return cast(Path, getattr(target, self.attribute))


STARTERS = (
    Starter("judge_notes", JUDGE_NOTES_STARTER, "the Calibration Notes starter"),
    Starter("maintainer_notes", MAINTAINER_NOTES_STARTER, "the Maintainer notes starter"),
    Starter("redaction", REDACTION_STARTER, "local, gitignored"),
)
"""The starters, in the order `init` lists them; `discover` writes the subset it names."""


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
    never run, the same block as a comment, since its deployed set is not known (under a
    REVIEW line for a pending Target)."""
    if scaffold.deployed is None:
        review = f"{REVIEW} {REVIEW_CONNECTOR}\n" if scaffold.pending else ""
        return (
            CONNECTOR_COMMENT
            + review
            + f"""# connector:
#   kind: inprocess
#   environments:
#     {scaffold.environment}:
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
        review = f"{REVIEW} {REVIEW_FAMILY}\n" if scaffold.pending else ""
        return FAMILY_COMMENT + review + "# family: your-persona\n# channel: chat\n\n"
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
    more_suites = "".join(f"  - {scalar(path)}\n" for path in scaffold.more_suites)
    first_suite = (
        scalar(scaffold.suite_path)
        if scaffold.suite_status is None
        else yaml.safe_dump(
            {"path": scaffold.suite_path, "status": scaffold.suite_status},
            default_flow_style=True,
            sort_keys=False,
            width=10**6,
        ).strip()
    )
    description_review = (
        f"  {REVIEW} {REVIEW_DESCRIPTION}\n"
        if scaffold.pending and scaffold.target_description == PLACEHOLDER_DESCRIPTION
        else ""
    )
    words = JUDGE_NOTES_MAX_WORDS
    notes_section = f"""\
# The Judge's calibration notes, relative to {scaffold.directory_words}: known
# false-fail patterns, at most {words} words, read verbatim by every judged Eval and part of
# each judged Score's Judge Fingerprint (ADR-0003 section 8).
judge_notes: {scalar(JUDGE_NOTES_NAME)}
{MAINTAINER_NOTES_COMMENT}maintainer_notes: {scalar(MAINTAINER_NOTES_NAME)}
# The names a Change record never carries are in {REDACTION_NAME} beside this file: local and
# gitignored, so a key here is needed only to point elsewhere (redaction: <path>).

"""
    middle = render_connector(scaffold) + tools_section + notes_section + EVAL_PARAMETERS_COMMENT
    return f"""# The Target's identity document (D37, ADR-0005 section 1).
#
# Pointers, not copies: nothing here holds a prompt, a tool schema or a secret. `agentdiag
# run --root {scaffold.root_hint}` reads this file and nothing above it.
schema_version: 1

{TARGET_COMMENT}target:
  name: {scalar(scaffold.target_name)}
{description_review}  description: {scalar(scaffold.target_description)}

{render_family(scaffold)}{ADAPTER_COMMENT}{ADAPTER_RENDERERS[scaffold.adapter_kind](scaffold)}
{prompts_comment(scaffold.directory_words)}{_render_prompts(scaffold)}

{middle}{suites_comment(scaffold.directory_words)}suites:
  - {first_suite}
{more_suites}"""


SIDE_EFFECTS_COMMENT = (
    "  # What a Run against this Target does to the world, one of three classes: `none`, "
    "nothing\n"
    "  # outside this process changes; `sandboxed`, it writes only to a test system; `live`, "
    "it\n"
    "  # reaches real users or data, and a Run is refused without a flag. A tool entry may "
    "carry\n"
    "  # its own `side_effects`, and `agentdiag validate` names the classes when one is "
    "wrong.\n"
)
"""The comment above `side_effects`, the same for every kind."""

ENVIRONMENTS_COMMENT = """\
    # One block per environment. `protected: true` marks one that is never pushed to
    # without confirmation; prod, staging and eu_prod are protected unless their block says
    # `protected: false`. A block may raise the side-effect class for itself, never lower it.
"""
"""The comment above the environment blocks, the same for every kind."""

INPROCESS_KIND_COMMENT = (
    "  # `inprocess` drives a Python Target in this process and captures at its two boundaries.\n"
)
"""What the in-process kind does, above `kind: inprocess`."""

PENDING_KIND_COMMENT = (
    "  # `pending` drives nothing: `run`, `run --dry-run` and `sync` refuse this Target "
    "until\n  # the kind is set (ADR-0016 section 4).\n"
)
"""What the pending kind does, above its REVIEW line and `kind: pending`."""


def _adapter_head(kind_lines: str, scaffold: Scaffold) -> str:
    """`adapter:` through the default environment's line: the kind's own lines, then what
    every kind shares."""
    return f"""adapter:
{kind_lines}{SIDE_EFFECTS_COMMENT}  side_effects: none
  environments:
    # Which environment a Run opens when nothing else says.
    default: {scalar(scaffold.environment)}
{ENVIRONMENTS_COMMENT}"""


def render_inprocess_adapter(scaffold: Scaffold) -> str:
    """The `inprocess` Adapter block: the factory, its tools and its model, each explained."""
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
    return _adapter_head(f"{INPROCESS_KIND_COMMENT}  kind: inprocess\n", scaffold) + (
        f"""    {scalar(scaffold.environment)}:
      # `module:attr`, called with (client, tools, **options). The Adapter hands in the
      # client, so it can record every exchange without the Target knowing (D6).
      factory: {scalar(scaffold.factory)}
{tools_lines}      # Passed to the factory as an option. Not the Judge's model: a Target and a Judge
      # on the same model would trip the self-preference warning (D23).
      model: {scalar(scaffold.model)}
"""
    )


def render_pending_adapter(scaffold: Scaffold) -> str:
    """The `pending` Adapter block (decision 11): the kind and the one empty environment,
    each under its REVIEW line, since nothing about what drives the Target is known."""
    kind_lines = f"{PENDING_KIND_COMMENT}  {REVIEW} {REVIEW_KIND}\n  kind: {PENDING_KIND}\n"
    return _adapter_head(kind_lines, scaffold) + (
        f"    {REVIEW} {REVIEW_ENVIRONMENT}\n    {scalar(scaffold.environment)}: {{}}\n"
    )


ADAPTER_RENDERERS: Mapping[str, Callable[[Scaffold], str]] = {
    "inprocess": render_inprocess_adapter,
    PENDING_KIND: render_pending_adapter,
}
"""The Adapter block of each kind `init` scaffolds, by `Scaffold.adapter_kind`: a kind `init`
learns to write is one renderer more here, and the rest of the Manifest is shared."""


def _render_prompts(scaffold: Scaffold) -> str:
    """`prompts`: the one pointer the in-process Adapter observes, or, for a pending Target,
    the same section as a comment under REVIEW, since no prompt of it is known yet."""
    if scaffold.pending:
        return f"""{REVIEW} {REVIEW_PROMPTS}
# prompts:
#   system: prompts/system.md"""
    return "prompts: {system: observed}"


def render_suite(scaffold: Scaffold) -> str:
    """The sample Suite: one Scenario, one Turn, one Eval, and why each line is there."""
    note = (
        f"  # {_wrapped_comment(scaffold.scenario_note)}\n  #\n" if scaffold.scenario_note else ""
    )
    review = f"  {REVIEW} {scaffold.scenario_review}\n" if scaffold.scenario_review else ""
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
{review}  - id: {scalar(scaffold.scenario_id)}
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
    "ADAPTER_RENDERERS",
    "CONNECTOR_COMMENT",
    "DEFAULT_MODEL",
    "EVAL_PARAMETERS_COMMENT",
    "EXAMPLE_ONLY",
    "EXAMPLE_SCAFFOLD",
    "FAMILY_COMMENT",
    "JUDGE_NOTES_STARTER",
    "MAINTAINER_NOTES_COMMENT",
    "MAINTAINER_NOTES_STARTER",
    "PENDING_ENVIRONMENT",
    "PLACEHOLDER_DESCRIPTION",
    "REDACTION_STARTER",
    "REVIEW_CONNECTOR",
    "REVIEW_DESCRIPTION",
    "REVIEW_ENVIRONMENT",
    "REVIEW_FAMILY",
    "REVIEW_KIND",
    "REVIEW_PROMPTS",
    "REVIEW_SCENARIO",
    "SAMPLE_SUITE_NAME",
    "STARTERS",
    "TARGET_COMMENT",
    "TOOLS_COMMENT",
    "TOY_DEPLOYED",
    "TOY_SCAFFOLD",
    "Scaffold",
    "Starter",
    "UnwritableValue",
    "custom_scaffold",
    "pending_scaffold",
    "prompts_comment",
    "render_connector",
    "render_family",
    "render_inprocess_adapter",
    "render_judge_notes",
    "render_maintainer_notes",
    "render_manifest",
    "render_pending_adapter",
    "render_redaction",
    "render_suite",
    "scalar",
    "suites_comment",
]
