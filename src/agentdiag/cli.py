"""The `agentdiag` command: `init`, `validate`, `sync`, `pull`, `push`, `discover`,
`generate`, `import`, `run`, `rescore`, `compare`, `list`, `index rebuild`, `show`, `export`,
`registry`, `dashboard`, `target show`, `change` (`open`, `propose`, `expect`, `close`,
`show`, `list`) and `serve`.

Command functions are thin on purpose (D3): they build an options object, call the module
that does the work, print what it returned and exit with the code it decided. Nothing here
knows what a Verdict is, so the UI (Phase 7) can call the same functions in-process and get
the same answer as the terminal.

Execution is imported inside `run` and `rescore`, not here: it brings the Judge, the model
client and the Anthropic SDK, and `validate` is an offline command that must
start without them (ticket 03). A test runs `validate` in a fresh process and asserts the
SDK never loaded. `sync` imports its module inside the command too: a probe, when no
Connector reads the deployed set, builds the Adapter, and with it the SDK. `compare` is
imported inside its command for the same reason: it reads the Scorecard model, which
reaches the Judge's configuration through `run.json`'s. `list`
and `index` import the index inside their commands too: they are offline, and `sqlite3` is
only theirs to pay for. `serve` imports its server inside the command, and the server
imports the executor only when the page launches a Run.

**Credentials come from one file, read once per command** (phase-8 decision 4): the `main`
callback loads `~/.agentdiag/env` (or `$AGENTDIAG_ENV_FILE`) into the process environment
before any command runs, never overriding a variable already set, and prints its one
warning to stderr; a Manifest names each credential by that variable, never by value.

**One Workspace, resolved once per command** (ADR-0013, phase-6 decision 3). `--root`
defaults to None, meaning the nearest `.agentdiag/` at or above the current directory, and
`_workspace` is the one place it is resolved; `_target` resolves `--target`, which needs
naming only when the Workspace holds several. `init`, which creates the root, resolves it
through `run.init.init_root` instead: the same walk, falling back to the current directory
when it finds no Workspace (ADR-0016 §8). A `WorkspaceError` is `error: …`, exit 3, from
every command alike. A command given a Run *path* needs no Workspace; a Run *id* is looked
for under every Target's `runs/`, or the named one's.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from agentdiag import __version__
from agentdiag.credentials_file import load_credentials_file
from agentdiag.eval.registry import (
    DEFAULT_JUDGE_MODEL,
    DEFAULT_REVIEWER_MODEL,
    DEFAULT_SIMULATED_USER_MODEL,
)
from agentdiag.exits import USAGE_EXIT
from agentdiag.run.init import (
    INIT_EXIT,
    InitOptions,
    InitRefused,
    init_root,
    render_created,
    scaffold_target,
)
from agentdiag.run.locate import NOT_FOUND_EXIT, TrialNotFound, locate_run
from agentdiag.run.templates import DEFAULT_MODEL
from agentdiag.scenario.validate import VALIDATE_EXIT
from agentdiag.trace.export import (
    FORMATS,
    UnknownFormat,
    default_out,
    render_export,
    write_text,
)
from agentdiag.trace.show import (
    FOLLOW_WAIT_SECONDS,
    follow_story,
    raw_events,
    render_story,
    trial_story,
)
from agentdiag.workspace import (
    TargetPaths,
    Workspace,
    WorkspaceError,
    WorkspaceNotFound,
)

if TYPE_CHECKING:
    from agentdiag.change.command import ChangeExit
    from agentdiag.run.manifest import Manifest

app = typer.Typer(
    name="agentdiag",
    help="Profile and evaluate an agentic system.",
    no_args_is_help=True,
)

index_app = typer.Typer(
    name="index",
    help="The derived index of Runs: deleted and rebuilt from the Run directories at will.",
    no_args_is_help=True,
)

target_app = typer.Typer(
    name="target",
    help="One Target of the Workspace, as its Manifest and its files describe it.",
    no_args_is_help=True,
)

change_app = typer.Typer(
    name="change",
    help=(
        "Change records: one recorded fix each, opened from a Diagnosis or a complaint and "
        "closed verified or refuted only through compare."
    ),
    no_args_is_help=True,
)


def _root_option() -> Path | None:
    """`--root` on every command but `init`: None walks up to the nearest Workspace."""
    return typer.Option(  # type: ignore[no-any-return]
        None,
        "--root",
        help=(
            "The Workspace root, holding .agentdiag/ (default: the nearest one at or above "
            "the current directory)."
        ),
        show_default=False,
    )


def _target_option(what: str = "The Target to act on") -> str | None:
    """`--target`: omitted, the Workspace's one Target; needed only when it holds several."""
    return typer.Option(  # type: ignore[no-any-return]
        None,
        "--target",
        help=f"{what}, by slug (default: the Workspace's one Target).",
        show_default=False,
    )


def _workspace(root: Path | None) -> Workspace:
    """The Workspace this command runs in: `--root`'s, or the nearest one above the current
    directory. The one place it is resolved; a `WorkspaceError` is exit 3."""
    try:
        return Workspace.find(root)
    except WorkspaceError as problem:
        typer.echo(f"error: {problem}", err=True)
        raise typer.Exit(USAGE_EXIT) from problem


def _target(workspace: Workspace, slug: str | None, *, new: bool = False) -> TargetPaths:
    """The Target `--target` names, or the one Target; ambiguity and absence are exit 3,
    naming the slugs there are. `new`: a slug the Workspace does not hold names a Target
    to create (`Workspace.resolve_or_new`), for `discover`, which writes its first file."""
    try:
        return workspace.resolve_or_new(slug) if new else workspace.resolve(slug)
    except WorkspaceError as problem:
        typer.echo(f"error: {problem}", err=True)
        raise typer.Exit(USAGE_EXIT) from problem


def _run_dir(root: Path | None, slug: str | None, run: str) -> Path:
    """The Run directory `run` names, as `locate_run` finds it. A path needs no Workspace,
    so no Workspace found is not an error here; any other `WorkspaceError` is exit 3.
    `TrialNotFound` names where it looked."""
    try:
        workspace: Workspace | None = Workspace.find(root)
    except WorkspaceNotFound:
        workspace = None
    except WorkspaceError as problem:
        typer.echo(f"error: {problem}", err=True)
        raise typer.Exit(USAGE_EXIT) from problem
    within = [_target(workspace, slug)] if workspace is not None and slug is not None else None
    return locate_run(workspace, run, within=within)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"agentdiag {__version__}")
        raise typer.Exit(0)


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the agentdiag version and exit.",
        callback=_version,
        is_eager=True,
    ),
) -> None:
    """Profile and evaluate an agentic system."""
    loaded = load_credentials_file()
    if loaded.warning is not None:
        typer.echo(f"warning: {loaded.warning}", err=True)


@app.command()
def init(
    root: Path | None = typer.Option(
        None,
        "--root",
        help=(
            "The Workspace root, where .agentdiag/ is or will be (default: the nearest one at "
            "or above the current directory, else the current directory)."
        ),
        show_default=False,
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        help=(
            "The slug of the Target to create (default: default); adds it to an existing Workspace."
        ),
        show_default=False,
    ),
    adapter: str | None = typer.Option(
        None,
        "--adapter",
        help=(
            "What drives the Target: toy (the shipped toy Target) or python:<module:attr> (its "
            "factory). Omitted: the toy for the first init with no --target; with --target, "
            "an Adapter of kind pending."
        ),
    ),
    tools: str | None = typer.Option(
        None,
        "--tools",
        help="The Target's tools, as <module:attr>. Omitted: the Manifest declares none.",
    ),
    model: str = typer.Option(
        DEFAULT_MODEL,
        "--model",
        help="The model the Target runs on, passed to its factory as an option.",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        help="The Target's name, as a Report prints it (default: the slug, for a pending Adapter).",
        show_default=False,
    ),
    description: str | None = typer.Option(
        None, "--description", help="One sentence: what the Target does.", show_default=False
    ),
    family: str | None = typer.Option(
        None,
        "--family",
        help="The persona this Target is one channel of.",
        show_default=False,
    ),
    channel: str | None = typer.Option(
        None, "--channel", help="Which channel of the Family: chat, voice, …", show_default=False
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Rewrite an existing Target's Manifest and sample Suite; never a Run, never the "
            "Judge notes. With --skills: replace edited skills."
        ),
    ),
    skills: bool = typer.Option(
        False,
        "--skills",
        help=(
            "Install agentdiag's skills under the Workspace root's .agents/skills/ (the "
            "nearest Workspace, or --root's), linked from .claude/skills/, instead of "
            "scaffolding a Target; an edited one is kept unless --force."
        ),
    ),
) -> None:
    """Scaffold a Target in the Workspace: a Manifest, a sample Suite, Judge notes, a gitignore.

    With --skills, install the packaged skills (/agentdiag-discover, ...) for a coding agent
    instead, and scaffold nothing.
    """
    if skills:
        from agentdiag.orientation import write_orientation
        from agentdiag.run.skills import SkillsRefused, install_skills, render_installed

        workspace = _workspace(root)
        try:
            installed = install_skills(workspace.root, force=force)
        except SkillsRefused as refused:
            typer.echo(f"error: {refused}", err=True)
            raise typer.Exit(INIT_EXIT) from refused
        # Skills without the routing page is the gap ADR-0016 §6 closes.
        orientation = write_orientation(workspace)
        typer.echo(render_installed(installed, orientation.summary()))
        for notice in orientation.notices:
            typer.echo(notice, err=True)
        raise typer.Exit(0)
    resolved = root if root is not None else init_root(Path.cwd())
    try:
        result = scaffold_target(
            InitOptions(
                root=resolved,
                target=target,
                adapter=adapter,
                tools=tools,
                model=model,
                force=force,
                name=name,
                description=description,
                family=family,
                channel=channel,
                joined=root is None and resolved != Path("."),
            )
        )
    except InitRefused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(INIT_EXIT) from refused
    typer.echo(render_created(result))
    for notice in result.notices:
        typer.echo(notice, err=True)
    raise typer.Exit(0)


@app.command()
def validate(
    paths: list[Path] | None = typer.Argument(
        None,
        help="Suite files to check. None: every Suite the Manifest names.",
        show_default=False,
    ),
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
    manifest_path: Path | None = typer.Option(
        None,
        "--manifest",
        help=(
            "Check this file as the Target's Manifest instead of manifest.yaml, such as the "
            "draft `agentdiag discover` wrote; its paths stay relative to the Target directory."
        ),
        show_default=False,
    ),
    every: bool = typer.Option(
        False,
        "--all",
        help=(
            "Check every Target of the Workspace in slug order, each ending on one "
            "`<slug>: validated …` line, with one exit code: 3 when any Target has an error."
        ),
    ),
) -> None:
    """Check the Manifest, then its Suites and Change records, offline: errors exit 3,
    warnings do not.

    With no paths: the Manifest first (every pointer, every Suppression), then each
    runnable and draft Suite it names (a retired Suite is named as skipped, never read),
    then every Change record under the Target's changes/.
    `--manifest` checks another file as the Manifest, then the Suites it names (or the paths).
    `--all` checks every Target so, after the Workspace's own warnings, printed once.
    """
    from agentdiag.orientation import orientation_warnings
    from agentdiag.validation import validate_target, validate_workspace

    if every:
        clashes = [
            flag
            for flag, given in (
                ("--target", target is not None),
                ("a Suite path", bool(paths)),
                ("--manifest", manifest_path is not None),
            )
            if given
        ]
        if clashes:
            typer.echo(
                f"error: --all validates every Target of the Workspace; drop {', '.join(clashes)}",
                err=True,
            )
            raise typer.Exit(USAGE_EXIT)
        lines, ok = validate_workspace(_workspace(root))
        for line in lines:
            typer.echo(line)
        raise typer.Exit(0 if ok else VALIDATE_EXIT)

    files = list(paths or [])
    if files and manifest_path is None:
        checked = validate_target(None, paths=files)
    else:
        # A whole Target: no paths and no draft Manifest, so the Workspace's warnings and
        # the Change records belong to this check too.
        whole = not files and manifest_path is None
        workspace = _workspace(root)
        checked = validate_target(
            _target(workspace, target),
            paths=files,
            manifest_path=manifest_path,
            whole=whole,
            orientation_lines=orientation_warnings(workspace) if whole else (),
        )
    for line in checked.lines:
        typer.echo(line)
    typer.echo(checked.summary)
    raise typer.Exit(0 if checked.ok else VALIDATE_EXIT)


@app.command()
def run(
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
    scenario: list[str] | None = typer.Option(
        None,
        "--scenario",
        help=(
            "A Scenario id to run, even one its Suite lists under not_run. Repeatable; "
            "ids OR together."
        ),
        show_default=False,
    ),
    tag: list[str] | None = typer.Option(
        None,
        "--tag",
        help=(
            "Run Scenarios carrying this tag or kind (one_shot, conversation, simulated, "
            "tool). Repeatable; tags OR together. Honours a Suite's not_run."
        ),
        show_default=False,
    ),
    suite: list[str] | None = typer.Option(
        None,
        "--suite",
        help=(
            "Run the Scenarios of this Suite, by stem or by path under the Target directory. "
            "Repeatable; Suites OR together. Honours a Suite's not_run."
        ),
        show_default=False,
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help=(
            "Print what would run and what would not, and the Sync a Run would find, then "
            "stop: no Run directory. The Sync check runs the Target's factory in-process; "
            "no model is called."
        ),
    ),
    trials: int = typer.Option(
        1,
        "--trials",
        help="Trials per selected Scenario; the Scorecard reports pass^k for every k up to it.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print the Scorecard as JSON instead of the terminal summary.",
    ),
    judge_model: str = typer.Option(
        DEFAULT_JUDGE_MODEL,
        "--judge-model",
        help="The model the Judge uses for judged Evals.",
    ),
    judge_effort: str | None = typer.Option(
        None,
        "--judge-effort",
        help="The Judge's reasoning effort, when the model takes one.",
    ),
    simulated_user_model: str = typer.Option(
        DEFAULT_SIMULATED_USER_MODEL,
        "--simulated-user-model",
        help="The model the Simulated User plays the user of a simulate Turn with.",
    ),
    simulated_user_effort: str | None = typer.Option(
        None,
        "--simulated-user-effort",
        help="The Simulated User's reasoning effort, when the model takes one.",
    ),
    simulated_user_temperature: float | None = typer.Option(
        None,
        "--simulated-user-temperature",
        help=(
            "Send the Simulated User this temperature; whether the model accepted it is "
            "recorded per call and printed beside pass^k."
        ),
    ),
    reviewer_model: str = typer.Option(
        DEFAULT_REVIEWER_MODEL,
        "--reviewer-model",
        help=(
            "The model that reviews every fail of a simulated Trial for the Simulated "
            "User's faults; stronger than the Simulated User's."
        ),
    ),
    no_resync: bool = typer.Option(
        False,
        "--no-resync",
        help=(
            "On a broken Sync, run against the old Fingerprint, labelled broken, instead of "
            "re-syncing first."
        ),
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="On a broken Sync, refuse to run (exit 3) and print which sections moved.",
    ),
    env: str | None = typer.Option(
        None,
        "--env",
        help=(
            "The Adapter environment the Run opens: the Sync it checks and the environment "
            "run.json records (default: the Adapter's default); a Connector-only environment "
            "is not runnable."
        ),
        show_default=False,
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help=(
            "Acknowledge an Adapter environment whose side effects are live, which is "
            "refused without it; run.json records that it was given."
        ),
    ),
    replay: Path | None = typer.Option(
        None,
        "--replay",
        hidden=True,
        help="Replay a recorded model exchange file instead of calling the API.",
    ),
) -> None:
    """Run the selected Scenarios against the Target and write one Run directory.

    Different flags AND together. No flag runs every Scenario except those a Suite lists
    under not_run; a selection that matches nothing exits 3. Sync is checked first: held
    runs, broken re-syncs the Fingerprint and runs, unless --no-resync or --strict.
    """
    from agentdiag.run.execute import RunOptions
    from agentdiag.run.execute import run as execute_run

    _require_trials(trials)
    if no_resync and strict:
        typer.echo("error: --no-resync and --strict ask opposite things; give one", err=True)
        raise typer.Exit(USAGE_EXIT)
    exit_state = execute_run(
        RunOptions(
            target=_target(_workspace(root), target),
            scenario=list(scenario or []),
            tag=list(tag or []),
            suite=list(suite or []),
            dry_run=dry_run,
            trials=trials,
            json_output=json_output,
            judge_model=judge_model,
            judge_effort=judge_effort,
            simulated_user_model=simulated_user_model,
            simulated_user_effort=simulated_user_effort,
            simulated_user_temperature=simulated_user_temperature,
            reviewer_model=reviewer_model,
            replay=replay,
            no_resync=no_resync,
            strict=strict,
            live=live,
            environment=env,
        )
    )
    if exit_state.message:
        typer.echo(exit_state.message)
    raise typer.Exit(exit_state.code)


@app.command()
def sync(
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
    env: str | None = typer.Option(
        None,
        "--env",
        help=(
            "The environment to read: an Adapter environment, or one only the Connector "
            "names (default: the Adapter's default)."
        ),
        show_default=False,
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="Compare only and write nothing: exit 0 held, 2 broken, 3 not checked.",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the Sync result as JSON instead of the table."
    ),
) -> None:
    """Fingerprint the Target section by section and compare it with the last Fingerprint.

    The deployed side is the Connector's read when the Manifest names one, else the
    Adapter's probe. Without --check, write fingerprint.json from it and the local files.
    Both forms record a Sync break under the Target when Sync is broken. Nothing here
    converses with the Target: the Connector reads, and the probe never reaches a model.
    """
    from agentdiag.sync.check import sync_target

    exit_state = sync_target(
        _target(_workspace(root), target),
        environment=env,
        check_only=check,
        json_output=json_output,
    )
    typer.echo(exit_state.message, err=exit_state.message.startswith("error:"))
    if exit_state.notice is not None:
        typer.echo(exit_state.notice, err=True)
    raise typer.Exit(exit_state.code)


def _manifest_of(target: TargetPaths) -> Manifest:
    """The Target's Manifest, or `error: …` and exit 3 when it does not load."""
    from agentdiag.run.manifest import ManifestError, ManifestNotFound, load_manifest

    try:
        return load_manifest(target)
    except (ManifestNotFound, ManifestError) as problem:
        typer.echo(f"error: {problem}", err=True)
        raise typer.Exit(USAGE_EXIT) from problem


@app.command()
def pull(
    env: str = typer.Option(
        ..., "--env", help="The Connector environment whose deployed set to pull."
    ),
    section: list[str] | None = typer.Option(
        None,
        "--section",
        help="Pull only this section, by Fingerprint section id. Repeatable.",
        show_default=False,
    ),
    overwrite_local: bool = typer.Option(
        False,
        "--overwrite-local",
        help="Write a pointed file even when it holds changes no commit does.",
    ),
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
) -> None:
    """Write the deployed sections the Connector reads ahead into the files the Manifest
    points at, and print the git diff.

    Never commits and never writes fingerprint.json. A file with uncommitted changes is
    skipped unless --overwrite-local; a pointer outside the Workspace root is skipped and
    named.
    """
    from agentdiag.sync.pull import pull_target

    resolved = _target(_workspace(root), target)
    exit_state = pull_target(
        resolved, _manifest_of(resolved), env, section, overwrite_local=overwrite_local
    )
    typer.echo(exit_state.message, err=exit_state.code != 0)
    raise typer.Exit(exit_state.code)


def _stdin_is_a_terminal() -> bool:
    """Whether a person can answer a prompt here: the one question a protected push asks
    before it writes (the test seam that fakes a terminal)."""
    import sys

    return sys.stdin.isatty()


@app.command()
def push(
    env: str = typer.Option(..., "--env", help="The Connector environment to push to."),
    section: list[str] | None = typer.Option(
        None,
        "--section",
        help="Push only this section, by Fingerprint section id. Repeatable.",
        show_default=False,
    ),
    do_push: bool = typer.Option(
        False, "--push", help="Write, after the preview; without it `push` only previews."
    ),
    change: str | None = typer.Option(
        None,
        "--change",
        help=(
            "The Change record this push is (required on a protected environment), or "
            "`none` (default elsewhere)."
        ),
        show_default=False,
    ),
    restore: str | None = typer.Option(
        None,
        "--restore",
        help="Push a Restore point back instead of the local files: its name or path.",
        show_default=False,
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the preview as JSON instead of the table and diffs."
    ),
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
) -> None:
    """Preview, and with --push write, the local files to the deployed set.

    The preview reads the deployed set through the Connector now and shows exactly the
    bytes that would change. With --push an unprotected environment is written; a
    protected one asks for its name typed at the terminal, and there is no flag that
    stands in for it. Before the write the deployed set is saved as a Restore point; after
    it the Fingerprint is rebuilt with pushed_from and a Push record is written. Exit 0 on
    a preview or a write, 2 when the deployed set moved since the preview, 3 on a refusal.
    """
    from agentdiag.sync.push import push_target

    resolved = _target(_workspace(root), target)
    manifest = _manifest_of(resolved)
    exit_state = push_target(
        resolved,
        manifest,
        env,
        section,
        write=do_push,
        change=change,
        restore=restore,
        json_output=json_output,
        terminal=_stdin_is_a_terminal(),
    )
    if exit_state.ask is not None:
        typer.echo(exit_state.message)
        exit_state = push_target(
            resolved,
            manifest,
            env,
            section,
            write=do_push,
            change=change,
            typed=typer.prompt(exit_state.ask),
            shown=exit_state.preview,
        )
    if exit_state.message:
        typer.echo(exit_state.message)
    if exit_state.error:
        typer.echo(exit_state.error, err=True)
    raise typer.Exit(exit_state.code)


@app.command()
def discover(
    root: Path | None = _root_option(),
    target: str | None = _target_option(
        "The Target to draft a Manifest for; a new slug creates its Target directory"
    ),
    scan: Path | None = typer.Option(
        None,
        "--scan",
        help=(
            "The repository to read, never run (default: the Workspace root, unless "
            "--from-connector alone is given)."
        ),
        show_default=False,
    ),
    from_connector: bool = typer.Option(
        False,
        "--from-connector",
        help=(
            "Read the deployed set through the Connector and save its prompts and tool "
            "schemas as files under the Target directory; needs --env."
        ),
    ),
    env: str | None = typer.Option(
        None, "--env", help="The Connector environment --from-connector reads.", show_default=False
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Where to write the draft (default: manifest.draft.yaml in the Target directory).",
        show_default=False,
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite files with uncommitted changes: the draft, a saved prompt or tool schema.",
    ),
) -> None:
    """Draft a Manifest for a Target from its repository or its Connector, offline.

    Every guess sits under a `# REVIEW:` line saying why it was guessed. The draft is
    written beside the Manifest, never over it: review it, `validate --manifest` it, move it
    into place, then `sync`. Exit 0 with a draft written, 3 otherwise.
    """
    from agentdiag.discover.command import DiscoverOptions
    from agentdiag.discover.command import discover as discover_target

    workspace = _workspace(root)
    chosen = _target(workspace, target, new=True)
    exit_state = discover_target(
        DiscoverOptions(
            target=chosen,
            scan=scan,
            from_connector=from_connector,
            environment=env,
            out=out,
            force=force,
            several_targets=any(t.slug != chosen.slug for t in workspace.targets()),
        )
    )
    typer.echo(exit_state.message, err=exit_state.code != 0)
    raise typer.Exit(exit_state.code)


@app.command()
def generate(
    drafts: Path = typer.Option(
        ...,
        "--from",
        help="The drafts file: {target, suite, description, scenarios}, each with a provenance.",
    ),
    root: Path | None = _root_option(),
    target: str | None = _target_option("The Target to write the Suite for"),
    suite: str | None = typer.Option(
        None,
        "--suite",
        help=(
            "The Suite's name, written to suites/<name>.yaml (default: the drafts' name, "
            "else generated)."
        ),
        show_default=False,
    ),
    check: bool = typer.Option(
        False, "--check", help="Check the drafts and print the ids; write nothing."
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite a Suite file with uncommitted changes (or one outside git).",
    ),
) -> None:
    """Write a Suite from a coding agent's drafts, offline, keeping ids across regeneration.

    Each draft names its provenance (prompt:<name>[#<section>], tool:<name>,
    trace:<run>/<scenario>/<n> or change:<id>) and at least one Eval. The Suite is validated
    before it is written, with a `# from <provenance>` line above each Scenario, and the
    Manifest's suites gain it. Exit 0 with the Suite written (or checked), 3 otherwise.
    """
    from agentdiag.generate.command import GenerateOptions
    from agentdiag.generate.command import generate as generate_suite

    workspace = _workspace(root)
    chosen = _target(workspace, target)
    exit_state = generate_suite(
        GenerateOptions(
            target=chosen,
            drafts=drafts,
            suite=suite,
            check=check,
            force=force,
            several_targets=any(t.slug != chosen.slug for t in workspace.targets()),
        )
    )
    typer.echo(exit_state.message, err=exit_state.code != 0)
    raise typer.Exit(exit_state.code)


@app.command("import")
def import_evidence(
    root: Path | None = _root_option(),
    target: str | None = _target_option("The Target the conversation belongs to"),
    rows: Path | None = typer.Option(
        None,
        "--rows",
        help="A JSON or JSONL file of proxy rows to import; needs no Connector.",
        show_default=False,
    ),
    chat: str | None = typer.Option(
        None,
        "--chat",
        help="A conversation id: read its proxy rows through the Connector.",
        show_default=False,
    ),
    conversation: str | None = typer.Option(
        None,
        "--conversation",
        help="A conversation id: read its conversation record through the Connector.",
        show_default=False,
    ),
    voice: str | None = typer.Option(
        None,
        "--voice",
        help="A conversation id: read its voice conversation through the Connector.",
        show_default=False,
    ),
    env: str | None = typer.Option(
        None,
        "--env",
        help="The environment to read from (default: the Adapter's default).",
        show_default=False,
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the new Run's id, directory and importer as JSON."
    ),
) -> None:
    """Import one conversation's evidence as a Run of source imported, with one Trial.

    Every Span is marked imported at the Fidelity the evidence supports, and the Trace's
    import/source Event names what the evidence lacks. Judge it with rescore --eval.
    Exit 0 with the Run written, 3 otherwise.
    """
    from agentdiag.importer.command import ImportOptions
    from agentdiag.importer.command import import_evidence as execute_import

    exit_state = execute_import(
        ImportOptions(
            target=_target(_workspace(root), target),
            rows=rows,
            chat=chat,
            conversation=conversation,
            voice=voice,
            environment=env,
            json_output=json_output,
        )
    )
    for warning in exit_state.warnings:
        typer.echo(f"warning: {warning}", err=True)
    typer.echo(exit_state.message, err=exit_state.code != 0)
    raise typer.Exit(exit_state.code)


@app.command()
def rescore(
    run: str = typer.Argument(..., help="The Run to judge again: an id, or a path to it."),
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
    scenario: list[str] | None = typer.Option(
        None,
        "--scenario",
        help="Rescore only this Scenario of the source Run. Repeatable; ids OR together.",
        show_default=False,
    ),
    tag: list[str] | None = typer.Option(
        None,
        "--tag",
        help="Rescore only the Scenarios carrying this tag or kind. Repeatable.",
        show_default=False,
    ),
    suite: list[str] | None = typer.Option(
        None,
        "--suite",
        help="Rescore only the Scenarios of this Suite. Repeatable.",
        show_default=False,
    ),
    evals: list[str] | None = typer.Option(
        None,
        "--eval",
        help=(
            "An Eval to apply to every Trial whose Scenario no Suite declares, such as an "
            "imported one: a name, or name=<value> for its primary parameter "
            "(expect_tools=[search_articles]). Repeatable."
        ),
        show_default=False,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print the new Run's Scorecard as JSON instead of the terminal summary.",
    ),
    judge_model: str = typer.Option(
        DEFAULT_JUDGE_MODEL,
        "--judge-model",
        help="The model the Judge uses for judged Evals.",
    ),
    judge_effort: str | None = typer.Option(
        None,
        "--judge-effort",
        help="The Judge's reasoning effort, when the model takes one.",
    ),
    reviewer_model: str = typer.Option(
        DEFAULT_REVIEWER_MODEL,
        "--reviewer-model",
        help="The model that reviews every fail of a simulated Trial again.",
    ),
    replay: Path | None = typer.Option(
        None,
        "--replay",
        hidden=True,
        help="Replay a recorded model exchange file instead of calling the API.",
    ),
) -> None:
    """Judge a stored Run's Traces again with the current Suites and Judge, as a new Run.

    The Target is never touched and the source Run is never written: the new Run's
    run.json names it under traces_from, and its Trials hold only the new judgement.
    """
    from agentdiag.run.rescore import RescoreOptions, locate_source
    from agentdiag.run.rescore import rescore as execute_rescore

    workspace = _workspace(root)
    named = _target(workspace, target) if target is not None else None
    try:
        chosen, source_dir = locate_source(workspace, run, named)
    except (TrialNotFound, WorkspaceError) as problem:
        typer.echo(f"error: {problem}", err=True)
        raise typer.Exit(USAGE_EXIT) from problem
    exit_state = execute_rescore(
        RescoreOptions(
            target=chosen,
            run=str(source_dir),
            scenario=list(scenario or []),
            tag=list(tag or []),
            suite=list(suite or []),
            evals=list(evals or []),
            json_output=json_output,
            judge_model=judge_model,
            judge_effort=judge_effort,
            reviewer_model=reviewer_model,
            replay=replay,
        )
    )
    if exit_state.message:
        typer.echo(exit_state.message)
    raise typer.Exit(exit_state.code)


@app.command()
def compare(
    baseline: str = typer.Argument(..., help="The Baseline: a Run id, or a path to a Run."),
    run: str = typer.Argument(..., help="The Run compared with it: an id, or a path."),
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
    expect: list[str] | None = typer.Option(
        None,
        "--expect",
        help=(
            "A run.json path or prefix you meant to vary, such as judge.model or "
            "manifest.adapter.environments.local.model. Repeatable."
        ),
        show_default=False,
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the comparison as JSON instead of text."
    ),
) -> None:
    """Compare a Baseline and a Run: the configuration diff first, then the Scores.

    No Score change is called a regression while any undeclared configuration difference
    exists, nor any judged one across a Judge change.
    """
    from agentdiag.run.compare import (
        COMPARE_EXIT_USAGE,
        CompareUsageError,
        render_comparison,
    )
    from agentdiag.run.compare import compare as execute_compare

    try:
        comparison = execute_compare(
            _run_dir(root, target, baseline), _run_dir(root, target, run), list(expect or [])
        )
    except (CompareUsageError, TrialNotFound) as usage:
        typer.echo(f"error: {usage}", err=True)
        raise typer.Exit(COMPARE_EXIT_USAGE) from usage
    if json_output:
        typer.echo(comparison.model_dump_json(indent=2))
    else:
        typer.echo(render_comparison(comparison))
    raise typer.Exit(0)


@app.command("list")
def list_runs(
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
    limit: int | None = typer.Option(
        None, "--limit", help="Show only the N newest Runs.", show_default=False
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the Runs as JSON rows instead of a table."
    ),
) -> None:
    """List every Run, newest first, with every Verdict counted and what did not run.

    Served from the derived index, which is brought in step with the Run directories first:
    a missing index is built, a corrupt one is reported and left for index rebuild.
    """
    from agentdiag.run.index import IndexCorrupt, list_runs, listed_under, render_listing
    from agentdiag.run.locate import index_path

    if limit is not None and limit < 1:
        typer.echo(f"error: --limit must be at least 1; got {limit}", err=True)
        raise typer.Exit(USAGE_EXIT)
    workspace = _workspace(root)
    named = _target(workspace, target) if target is not None else None
    try:
        rows, built = list_runs(workspace.root, limit=limit, target=named.slug if named else None)
    except IndexCorrupt as corrupt:
        typer.echo(f"error: {corrupt}", err=True)
        raise typer.Exit(USAGE_EXIT) from corrupt
    if built:
        typer.echo(f"notice: built the index at {index_path(workspace.root)}", err=True)
    if json_output:
        typer.echo(
            json.dumps([row.model_dump(mode="json", by_alias=True) for row in rows], indent=2)
        )
    else:
        typer.echo(render_listing(rows, where=listed_under(workspace, named)))
    raise typer.Exit(0)


@index_app.command("rebuild")
def index_rebuild(
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
) -> None:
    """Delete the index and build it again from the Run directories, which are the truth."""
    from agentdiag.run.index import IndexCorrupt, rebuild
    from agentdiag.run.locate import index_path

    workspace = _workspace(root)
    named = _target(workspace, target) if target is not None else None
    index = index_path(workspace.root)
    try:
        indexed = rebuild(workspace.root, named)
    except (IndexCorrupt, OSError) as exc:
        typer.echo(f"error: could not rebuild the index at {index}: {exc}", err=True)
        raise typer.Exit(USAGE_EXIT) from exc
    of = f" of Target {named.slug}" if named is not None else ""
    typer.echo(f"indexed {indexed} Runs{of} into {index}")
    raise typer.Exit(0)


app.add_typer(index_app)
app.add_typer(target_app)
app.add_typer(change_app)


def _require_trials(trials: int) -> None:
    """`--trials` below 1 is a usage error, and usage errors exit 3 (D32), not click's 2."""
    if trials < 1:
        typer.echo(f"error: --trials must be at least 1; got {trials}", err=True)
        raise typer.Exit(USAGE_EXIT)


@app.command()
def show(
    run: str = typer.Argument(..., help="A Run id, or a path to a Run directory."),
    scenario: str = typer.Argument(..., help="The Scenario whose Trial to render."),
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
    trial: int = typer.Option(1, "--trial", help="Which Trial of that Scenario to render."),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the Trace's and the judgement's raw Events instead of the story.",
    ),
    follow: bool = typer.Option(
        False,
        "--follow",
        help="Stream the story as the Trial is written, instead of reading a finished one.",
    ),
    wait: float = typer.Option(
        FOLLOW_WAIT_SECONDS,
        "--wait",
        help="With --follow: seconds to wait for the Judge after the Trace ends.",
    ),
) -> None:
    """Render one Trial of a Run as one story: the Trace, the Judge's file and the Scores."""
    try:
        run_dir = _run_dir(root, target, run)
        if follow:
            for line in follow_story(run_dir, scenario, trial, wait_seconds=wait, raw=json_output):
                typer.echo(line)
        elif json_output:
            for line in raw_events(run_dir, scenario, trial):
                typer.echo(line)
        else:
            typer.echo(render_story(trial_story(run_dir, scenario, trial)))
    except TrialNotFound as missing:
        typer.echo(str(missing), err=True)
        raise typer.Exit(NOT_FOUND_EXIT) from missing
    raise typer.Exit(0)


@app.command()
def export(
    run: str = typer.Argument(..., help="A Run id, or a path to a Run directory."),
    scenario: str = typer.Argument(..., help="The Scenario whose Trial to export."),
    format: str = typer.Option(
        ...,
        "--format",
        help=f"The view to write: {' or '.join(FORMATS)}.",
    ),
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
    trial: int = typer.Option(1, "--trial", help="Which Trial of that Scenario to export."),
    out: str | None = typer.Option(
        None,
        "--out",
        help="Where to write it; `-` writes to stdout (default: ./<run>.<scenario>.<trial>.*).",
    ),
) -> None:
    """Write one Trial as a Chrome trace file for Perfetto, or as a speedscope profile.

    An export is a Report: a rendering of the Trace, never the source of truth, and never
    read back. It is written where the caller asked, never inside the immutable Run.
    """
    try:
        run_dir = _run_dir(root, target, run)
        text, run_id = render_export(run_dir, scenario, trial, format)
    except TrialNotFound as missing:
        typer.echo(str(missing), err=True)
        raise typer.Exit(NOT_FOUND_EXIT) from missing
    except UnknownFormat as unknown:
        typer.echo(str(unknown), err=True)
        raise typer.Exit(NOT_FOUND_EXIT) from unknown

    if out == "-":
        typer.echo(text, nl=False)
    else:
        path = Path(out) if out else default_out(run_id, scenario, trial, format)
        typer.echo(f"wrote {write_text(text, path)}")
    raise typer.Exit(0)


@app.command()
def registry(
    root: Path | None = _root_option(),
    target: str | None = typer.Option(
        None,
        "--target",
        help="Show only this Target's entry, by slug (default: every Target).",
        show_default=False,
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the Registry as JSON entries instead of a table."
    ),
    write: bool = typer.Option(
        False,
        "--write",
        help=(
            "Instead of printing the Registry, regenerate the Targets table and the skills "
            "line between their markers in AGENTS.md at the Workspace root; write the page "
            "and its import files where absent, and the vocabulary copy always."
        ),
    ),
) -> None:
    """List the Workspace's Targets, derived from their Manifests: the Registry is never a
    file of its own.

    One line per Target: its slug, name, Family, channel, environments, Connector, Suites
    and Sync state. A Manifest that does not load is still a line, with its problem after
    the table. With --write, regenerate the Orientation page's Targets table and skills line
    instead: the text between their markers is regenerated and nothing else of the page; the page,
    CLAUDE.md and GEMINI.md are written where absent, and .agentdiag/CONTEXT.md always.
    """
    from agentdiag.registry import registry as workspace_registry
    from agentdiag.registry import render_registry

    if write and (json_output or target is not None):
        clash = "--json" if json_output else "--target"
        typer.echo(
            f"error: --write writes every Target's table and prints no Registry; drop {clash}",
            err=True,
        )
        raise typer.Exit(USAGE_EXIT)
    workspace = _workspace(root)
    if write:
        from agentdiag.orientation import write_orientation

        orientation = write_orientation(workspace)
        for line in orientation.lines():
            typer.echo(line)
        for notice in orientation.notices:
            typer.echo(notice, err=True)
        raise typer.Exit(0)
    named = _target(workspace, target) if target is not None else None
    entries = workspace_registry(workspace, named)
    if json_output:
        typer.echo(json.dumps([entry.model_dump(mode="json") for entry in entries], indent=2))
    else:
        typer.echo(render_registry(entries))
    raise typer.Exit(0)


@app.command()
def dashboard(
    root: Path | None = _root_option(),
    trend: int = typer.Option(
        10, "--trend", help="How many of each Target's newest driven Runs the trend covers."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the Dashboard as JSON instead of a table."
    ),
) -> None:
    """Show every Target's Sync state, last Run, pass-rate trend and open Change records.

    Read from the Registry and the Index, never a source of truth: a Target with no Run or
    no Fingerprint says so, imported and rescored Runs are listed but never trended, and a
    missing or stale Index is a problem line naming index rebuild.
    """
    from agentdiag.dashboard import dashboard as workspace_dashboard
    from agentdiag.dashboard import render_dashboard

    if trend < 1:
        typer.echo(f"error: --trend must be at least 1; got {trend}", err=True)
        raise typer.Exit(USAGE_EXIT)
    view = workspace_dashboard(_workspace(root), trend=trend)
    if json_output:
        typer.echo(json.dumps(view.model_dump(mode="json", by_alias=True), indent=2))
    else:
        typer.echo(render_dashboard(view))
    raise typer.Exit(0)


@target_app.command("show")
def target_show(
    slug: str | None = typer.Argument(
        None,
        help="The Target's slug, as `agentdiag registry` lists it (default: the one Target).",
        show_default=False,
    ),
    root: Path | None = _root_option(),
    target: str | None = _target_option("The Target to show, as the argument does"),
    json_output: bool = typer.Option(
        False, "--json", help="Print the Target as JSON instead of text."
    ),
) -> None:
    """Print one Target: its Manifest, Calibration Notes, Fingerprint, open Sync breaks and
    open Change records."""
    from agentdiag.registry import render_target, target_view

    if slug is not None and target is not None and slug != target:
        typer.echo(f"error: the argument names Target {slug} and --target names {target}", err=True)
        raise typer.Exit(USAGE_EXIT)
    workspace = _workspace(root)
    try:
        view = target_view(workspace, slug or target)
    except WorkspaceError as problem:
        typer.echo(f"error: {problem}", err=True)
        raise typer.Exit(USAGE_EXIT) from problem
    typer.echo(view.model_dump_json(indent=2) if json_output else render_target(view))
    raise typer.Exit(0)


@app.command()
def serve(
    root: Path | None = _root_option(),
    port: int = typer.Option(
        7331, "--port", help="The port on 127.0.0.1 to listen on (0 picks a free one)."
    ),
    open_browser: bool = typer.Option(
        False, "--open", help="Open the page in the default browser once it listens."
    ),
) -> None:
    """Serve the local web UI for the Workspace on 127.0.0.1 and print its URL.

    Every screen reads the files and the Index through the functions the commands call.
    The page writes only by launching a Run, or by a pull or a push under ADR-0011's rules:
    a protected environment's push needs its name typed in the confirm step.
    """
    from agentdiag.serve.server import serve as serve_workspace

    raise typer.Exit(serve_workspace(_workspace(root), port=port, open_browser=open_browser))


# --- `agentdiag change` (ticket 25, phase-7 decision 9) ---


def _ids(values: list[str] | None) -> list[str]:
    """Scenario ids given as `a,b` or as the option repeated, in order, once each."""
    found = [part.strip() for value in values or [] for part in value.split(",")]
    return list(dict.fromkeys(part for part in found if part))


def _echo_change(exit_state: ChangeExit) -> None:
    if exit_state.message:
        typer.echo(exit_state.message, err=exit_state.code != 0)
    raise typer.Exit(exit_state.code)


@change_app.command("open")
def change_open(
    layer: str = typer.Option(
        ...,
        "--layer",
        help="The layer the fix is in: persona, rules, memory, checklist, flow, runtime or data.",
    ),
    title: str = typer.Option(..., "--title", help="The record's title; its id is made from it."),
    from_trial: str | None = typer.Option(
        None,
        "--from",
        help="The Trial whose Diagnosis triggers the record: <run>/<scenario>/<trial>.",
        show_default=False,
    ),
    complaint: Path | None = typer.Option(
        None,
        "--complaint",
        help="A file holding the complaint; its text is redacted into the record.",
        show_default=False,
    ),
    by: str | None = typer.Option(
        None,
        "--by",
        help="Who opens it (default: $AGENTDIAG_AGENT, else the OS user).",
        show_default=False,
    ),
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
) -> None:
    """Open a Change record from a Trial's Diagnosis or from a complaint."""
    from agentdiag.change.command import open_record

    _echo_change(
        open_record(
            _target(_workspace(root), target),
            layer=layer,
            title=title,
            from_trial=from_trial,
            complaint=complaint,
            by=by,
        )
    )


@change_app.command("propose")
def change_propose(
    record: str = typer.Argument(..., help="The Change record's id."),
    section: list[str] | None = typer.Option(
        None, "--section", help="A Fingerprint section id the change edits. Repeatable."
    ),
    file: list[str] | None = typer.Option(
        None, "--file", help="A file it edits, relative to the Target directory. Repeatable."
    ),
    flow: list[str] | None = typer.Option(None, "--flow", help="A Flow id it edits. Repeatable."),
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
) -> None:
    """Record the change set: open -> proposed, with the Workspace's git HEAD."""
    from agentdiag.change.command import propose_change

    _echo_change(
        propose_change(
            _target(_workspace(root), target),
            record,
            sections=list(section or []),
            files=list(file or []),
            flows=list(flow or []),
        )
    )


@change_app.command("expect")
def change_expect(
    record: str = typer.Argument(..., help="The Change record's id."),
    should_move: list[str] | None = typer.Option(
        None, "--should-move", help="Scenario ids that should improve: a,b or repeated."
    ),
    must_not_move: list[str] | None = typer.Option(
        None, "--must-not-move", help="Scenario ids that must stay unchanged: a,b or repeated."
    ),
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
) -> None:
    """State the expected effect, timestamped, before the verifying Run starts."""
    from agentdiag.change.command import expect_change

    _echo_change(
        expect_change(
            _target(_workspace(root), target),
            record,
            should_move=_ids(should_move),
            must_not_move=_ids(must_not_move),
        )
    )


@change_app.command("close")
def change_close(
    record: str = typer.Argument(..., help="The Change record's id."),
    verified: bool = typer.Option(
        False, "--verified", help="Close it verified: --run's compare shows the expected effect."
    ),
    refuted: bool = typer.Option(
        False, "--refuted", help="Close it refuted: --run's compare shows it did not happen."
    ),
    wontfix: bool = typer.Option(False, "--wontfix", help="Close it unchanged, saying --why."),
    superseded_by: str | None = typer.Option(
        None, "--superseded-by", help="Close it as replaced by this record.", show_default=False
    ),
    why: str | None = typer.Option(
        None, "--why", help="Why it is not changed (--wontfix).", show_default=False
    ),
    run: str | None = typer.Option(
        None, "--run", help="The post-change Run's id (--verified, --refuted).", show_default=False
    ),
    baseline: str | None = typer.Option(
        None,
        "--baseline",
        help="The pre-change Run's id (default: the trigger's Run, when agentdiag drove it).",
        show_default=False,
    ),
    expect: list[str] | None = typer.Option(
        None,
        "--expect",
        help="A run.json path the change was meant to vary, as compare takes it. Repeatable.",
        show_default=False,
    ),
    any_env: bool = typer.Option(
        False,
        "--any-env",
        help=(
            "Accept a --run on another environment than the record's last push; the "
            "verification records environment_mismatch."
        ),
    ),
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
) -> None:
    """Close a Change record: verified or refuted through compare, wontfix, or superseded."""
    from agentdiag.change.command import close_change

    _echo_change(
        close_change(
            _target(_workspace(root), target),
            record,
            verified=verified,
            refuted=refuted,
            wontfix=wontfix,
            superseded_by=superseded_by,
            why=why,
            run=run,
            baseline=baseline,
            expect=list(expect or []),
            any_env=any_env,
        )
    )


@change_app.command("show")
def change_show(
    record: str = typer.Argument(..., help="The Change record's id."),
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
    json_output: bool = typer.Option(False, "--json", help="Print the record's head as JSON."),
) -> None:
    """Print one Change record's head."""
    from agentdiag.change.command import show_change

    _echo_change(show_change(_target(_workspace(root), target), record, json_output=json_output))


@change_app.command("list")
def change_list(
    status: str | None = typer.Option(
        None, "--status", help="Only the records in this status.", show_default=False
    ),
    root: Path | None = _root_option(),
    target: str | None = _target_option(),
) -> None:
    """List the Target's Change records by id."""
    from agentdiag.change.command import list_changes

    _echo_change(list_changes(_target(_workspace(root), target), status=status))
