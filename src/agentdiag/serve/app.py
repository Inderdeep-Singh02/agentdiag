"""The routes of `agentdiag serve`, as functions over one Workspace (phase-7 decision 22).

Every route calls the function the CLI command calls and dumps what it returns with
`model_dump(mode="json")`; nothing here computes a Sync, a Score or a comparison of its own.
JSON in, JSON out, and an error is `{"error": <sentence>}` with the status that fits: 400
for a request that cannot be done as asked, 404 for a Target, Run, record or route that is
not there, 409 for a state that moved underneath the request (a deployed set changed since
its preview, a preview too old, a Run still being written). A traceback never reaches a
body.

**Reads**: the Dashboard, the Registry, one Target, its Sync screen (`sync --check`'s
result beside a push preview's section diffs, its open Sync breaks and Push records), its
Suites, the Runs from the Index, one Run as the Report's `RunView`, a Run followed as it is
written (the Events `show --follow` reads, as server-sent events), a comparison, the Change
records and one record's story, one Flow's definition.

**Writes, the only ones** (ADR-0011 §6-§7, ticket 16's amendment): launching a Run (the
`run` function in-process, in a thread, returning its id at once), cancelling it, `pull`,
a push preview (kept in memory with the moment it was taken) and the push that confirms
one. A push to a protected environment needs the environment's name typed in the page's
confirm step, recorded as `ui_confirm`; an unprotected one needs the explicit push box,
which stands for `--push`. No other request writes.

**No read writes.** The Sync screen compares as `sync --check` does but records no Sync
break (`sync_target(record_breaks=False)`); it lists the breaks already recorded.

**Ids are checked before a path is built**: a Run id against the Run id grammar, a Change
record id against the record id grammar (`change.record.is_record_id`), so no request names a
file outside the Workspace's own.

Offline at import: the executor, which brings the Judge and the model client, is imported
only when a Run is launched, and every other command module inside the route that needs it,
as `cli.py` does.
"""

from __future__ import annotations

import json
import queue
import re
import secrets
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentdiag.registry import ChangeRecordSummary, OpenSyncBreak, PushSummary
from agentdiag.sync.compare import SyncResult
from agentdiag.sync.push import PushExit, PushPreview
from agentdiag.trace.show import FOLLOW_WAIT_SECONDS
from agentdiag.workspace import TargetPaths, Workspace, WorkspaceError

if TYPE_CHECKING:
    from agentdiag.run.execute import RunExit
    from agentdiag.run.manifest import Manifest

JSON_TYPE = "application/json"
EVENT_STREAM = "text/event-stream"
HTML_TYPE = "text/html; charset=utf-8"

FOLLOW_POLL_MS = 100
"""How often a followed Run's files are looked at, as `show --follow` looks."""

PREVIEW_ID_BYTES = 8
TOKEN_BYTES = 24

KEEPALIVE_S = 5.0
"""How often a follow with nothing to say sends a comment, so a page that went away is
noticed and the follow's thread ends."""

LIVE_FROM_THE_TERMINAL = "a live Run is launched from the terminal in this version"
"""Why the UI refuses `live: true` (phase-8 decision 9): its confirmation step for a `live`
launch is not built, and `agentdiag run --live` is."""


class RouteError(Exception):
    """A request the route refuses: its status and the sentence the body carries."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Reply:
    """What a route answers: JSON (`body`), the page (`text`), or server-sent `events`."""

    status: int = 200
    body: Any = None
    content_type: str = JSON_TYPE
    text: str | None = None
    events: Iterator[str] | None = None

    def encoded(self) -> bytes:
        """The body's bytes, for every reply but a stream."""
        if self.text is not None:
            return self.text.encode("utf-8")
        return json.dumps(self.body, ensure_ascii=False).encode("utf-8")


def error(status: int, message: str) -> Reply:
    return Reply(status=status, body={"error": message})


@dataclass
class Request:
    method: str
    path: str
    params: dict[str, str]
    query: dict[str, list[str]]
    body: bytes | None

    def one(self, name: str) -> str | None:
        """A query parameter given once (the last, when repeated); None when absent or empty."""
        values = self.query.get(name) or []
        return (values[-1] or None) if values else None


# --- the view models the routes add (the rest are the commands' own) ---


class SuiteScenarioView(BaseModel):
    """One Scenario as the Tests screen's picker lists it."""

    id: str
    title: str
    kinds: list[str]
    """Derived (`one_shot`, `conversation`, `simulated`, `tool`), which `--tag` also matches."""

    tags: list[str]
    evals: list[str]
    continues: str | None = None
    not_run: str | None = None
    """The Suite's reason for skipping it, when it lists it under `not_run`: `--tag` and
    `--suite` honour it, `--scenario` does not (D31)."""


class SuiteView(BaseModel):
    """One Suite the Manifest names, by the name `--suite` and `run.json` use."""

    name: str
    reference: str
    status: str
    scenarios: list[SuiteScenarioView] = Field(default_factory=list)
    problem: str | None = None
    """Why the file did not load; `validate` says the same."""


class SuitesView(BaseModel):
    """What `/api/targets/<slug>/suites` returns: the picker's tree and the environments."""

    target: str
    default_environment: str | None = None
    environments: list[str] = Field(default_factory=list)
    """The Adapter's environments and then the Connector's, as the Sync screen lists them."""

    adapter_environments: list[str] = Field(default_factory=list)
    """The Adapter's environments alone: the ones a Run can open (`run --env`, phase-8
    decision 12), which the Tests screen's picker lists."""

    suites: list[SuiteView] = Field(default_factory=list)


class SyncScreenView(BaseModel):
    """The Sync screen of one Target and environment (phase-7 decision 22)."""

    target: str
    environment: str
    environments: list[str]
    protected: bool
    code: int
    """`sync --check`'s exit code: 0 held, 2 broken, 3 not checked or refused."""

    error: str | None = None
    """Why no comparison was made, when none was: the Connector's read failed, say."""

    result: SyncResult | None = None
    preview: PushPreview | None = None
    """A push preview over the same environment: the section diffs, deployed -> local, and
    every reason a push would be refused. Its read and payload are left out of the JSON."""

    sync_breaks: list[OpenSyncBreak] = Field(default_factory=list)
    """The open Sync breaks already recorded; reading this screen records none."""

    pushes: list[PushSummary] = Field(default_factory=list)
    change_records: list[ChangeRecordSummary] = Field(default_factory=list)


# --- the request bodies of the writes ---


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunRequest(_Body):
    """`POST /api/runs`: what `agentdiag run` takes, as the page sends it."""

    target: str | None = None
    scenario: list[str] = Field(default_factory=list)
    tag: list[str] = Field(default_factory=list)
    suite: list[str] = Field(default_factory=list)
    env: str | None = None
    """`--env`: the Adapter environment the Run opens, the default when None (phase-8
    decision 12); one the Adapter block does not declare is a 400."""

    trials: int = Field(default=1, ge=1)
    resync: Literal["resync", "no_resync", "strict"] = "resync"
    """The broken-Sync policy: re-sync (the default), `--no-resync` or `--strict`."""

    dry_run: bool = False
    replay: str | None = None
    """Hidden, as `--replay` is: a recorded model exchange file, so a Run reproduces
    offline. A relative path is taken from the Workspace root."""

    live: bool = False
    """`--live` (phase-8 decision 9): refused here with 400, since the UI's confirmation of a
    `live` launch is not built yet; a live Run is launched from the terminal."""


class PullRequest(_Body):
    env: str
    sections: list[str] = Field(default_factory=list)
    overwrite_local: bool = False


class PreviewRequest(_Body):
    env: str
    sections: list[str] = Field(default_factory=list)
    restore: str | None = None


class Confirm(_Body):
    """The confirm step: the push box (`--push`), or the environment's name typed."""

    push: bool = False
    typed_name: str | None = None


class PushRequest(_Body):
    preview_id: str
    change: str | None = None
    confirm: Confirm = Field(default_factory=Confirm)


# --- what the server keeps between requests ---


@dataclass
class KeptPreview:
    """A push preview the page was shown, kept until a push writes it or it is too old."""

    slug: str
    environment: str
    sections: list[str] | None
    restore: str | None
    preview: PushPreview
    taken: float


@dataclass
class RunLaunch:
    """A Run this server launched: its thread, its cancel flag, and how it ended."""

    run_id: str
    target: TargetPaths
    selection: str
    cancel: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    exit: RunExit | None = None
    error: str | None = None


class App:
    """The routes over one Workspace, and the state they share: kept previews, launched
    Runs, and the page token (`token`), which the served page carries and every write and
    every follow must present (`serve.server` checks it). `clock` is monotonic seconds, the
    age a kept preview is measured in; `follow_idle_s` and `keepalive_s` time a follow
    (test seams)."""

    def __init__(
        self,
        workspace: Workspace,
        *,
        clock: Callable[[], float] = time.monotonic,
        follow_idle_s: float = FOLLOW_WAIT_SECONDS,
        keepalive_s: float = KEEPALIVE_S,
    ) -> None:
        self.workspace = workspace
        self.clock = clock
        self.follow_idle_s = follow_idle_s
        self.keepalive_s = keepalive_s
        self.token = secrets.token_urlsafe(TOKEN_BYTES)
        self.closing = threading.Event()
        self._lock = threading.Lock()
        self._previews: dict[str, KeptPreview] = {}
        self._launches: dict[str, RunLaunch] = {}
        self._routes: list[tuple[str, re.Pattern[str], Callable[[Request], Reply]]] = [
            ("GET", _pattern("/"), self.page),
            ("GET", _pattern("/api/dashboard"), self.dashboard),
            ("GET", _pattern("/api/registry"), self.registry),
            ("GET", _pattern("/api/targets/<slug>"), self.target),
            ("GET", _pattern("/api/targets/<slug>/sync"), self.sync),
            ("GET", _pattern("/api/targets/<slug>/suites"), self.suites),
            ("POST", _pattern("/api/targets/<slug>/pull"), self.pull),
            ("POST", _pattern("/api/targets/<slug>/push/preview"), self.push_preview),
            ("POST", _pattern("/api/targets/<slug>/push"), self.push),
            ("GET", _pattern("/api/runs"), self.runs),
            ("POST", _pattern("/api/runs"), self.launch),
            ("GET", _pattern("/api/runs/<run_id>"), self.run),
            ("GET", _pattern("/api/runs/<run_id>/follow"), self.follow),
            ("POST", _pattern("/api/runs/<run_id>/cancel"), self.cancel),
            ("GET", _pattern("/api/compare"), self.compare),
            ("GET", _pattern("/api/changes"), self.changes),
            ("GET", _pattern("/api/changes/<record_id>"), self.change),
            ("GET", _pattern("/api/flows/<slug>/<env>/<flow_id>"), self.flow),
        ]

    # --- dispatch ---

    def handle(self, method: str, target: str, body: bytes | None = None) -> Reply:
        """Answer one request: `target` is the request line's path and query. Never raises;
        an unexpected exception is a 500 naming its type, without a traceback."""
        split = urlsplit(target)
        query = parse_qs(split.query, keep_blank_values=True)
        allowed: list[str] = []
        for verb, pattern, route in self._routes:
            matched = pattern.fullmatch(split.path)
            if matched is None:
                continue
            if verb != method:
                allowed.append(verb)
                continue
            params = {name: unquote(value) for name, value in matched.groupdict().items()}
            try:
                return route(Request(method, split.path, params, query, body))
            except RouteError as refused:
                return error(refused.status, refused.message)
            except Exception as exc:  # a route never answers with a traceback
                return error(500, f"the server failed: {type(exc).__name__}: {exc}")
        if allowed:
            return error(405, f"{split.path} answers {', '.join(sorted(set(allowed)))} only")
        return error(404, f"no route {method} {split.path}")

    # --- the page ---

    def page(self, request: Request) -> Reply:
        from agentdiag.report.html import render_page

        return Reply(content_type=HTML_TYPE, text=render_page(token=self.token))

    def dashboard(self, request: Request) -> Reply:
        """`agentdiag dashboard --json`: every Target's row, `?trend=` Runs deep."""
        from agentdiag.dashboard import DEFAULT_TREND, dashboard

        given = request.one("trend")
        trend = DEFAULT_TREND
        if given is not None:
            if not given.isdigit() or int(given) < 1:
                raise RouteError(400, f"trend must be a whole number of at least 1; got {given!r}")
            trend = int(given)
        view = dashboard(self.workspace, trend=trend)
        return Reply(body=view.model_dump(mode="json", by_alias=True))

    # --- reads ---

    def registry(self, request: Request) -> Reply:
        from agentdiag.registry import registry

        return Reply(body=[entry.model_dump(mode="json") for entry in registry(self.workspace)])

    def target(self, request: Request) -> Reply:
        from agentdiag.registry import target_view

        slug = request.params["slug"]
        self._target(slug)
        return Reply(body=target_view(self.workspace, slug).model_dump(mode="json"))

    def sync(self, request: Request) -> Reply:
        """`sync --check --env <env>` beside a push preview of the same environment."""
        from agentdiag.registry import target_view
        from agentdiag.sync.check import sync_target
        from agentdiag.sync.push import preview

        target = self._target(request.params["slug"])
        manifest = self._manifest(target)
        environments = _environments(manifest)
        environment = request.one("env") or _default_environment(manifest)
        if environment not in environments:
            raise RouteError(
                404,
                f"Target {target.slug} names no environment {environment!r}; "
                f"it names {', '.join(environments) or 'none'}",
            )
        checked = sync_target(
            target, environment=environment, check_only=True, json_output=True, record_breaks=False
        )
        shown = preview(target, manifest, environment) if manifest.connector is not None else None
        view = target_view(self.workspace, target.slug)
        screen = SyncScreenView(
            target=target.slug,
            environment=environment,
            environments=environments,
            protected=manifest.is_protected(environment),
            code=checked.code,
            error=_sentence(checked.message) if checked.result is None else None,
            result=checked.result,
            preview=shown,
            sync_breaks=view.sync_breaks,
            pushes=view.pushes,
            change_records=view.change_records,
        )
        return Reply(body=screen.model_dump(mode="json", exclude={"preview": {"read", "payload"}}))

    def suites(self, request: Request) -> Reply:
        """Every Suite the Manifest loads (runnable and draft, as a Run loads them), with
        each Scenario's id, title, kinds, tags and Evals: the picker's tree, named as
        `--suite` names them."""
        from agentdiag.scenario.load import load_suite_files

        target = self._target(request.params["slug"])
        manifest = self._manifest(target)
        loaded = load_suite_files(target, manifest)
        named = {suite.reference: suite for suite in loaded.suites}
        views: list[SuiteView] = []
        for entry in manifest.read_suites:
            one = named.get(entry.path)
            if one is None:
                views.append(
                    SuiteView(
                        name=Path(entry.path).stem,
                        reference=entry.path,
                        status=entry.status,
                        problem=loaded.problems.get(entry.path),
                    )
                )
                continue
            views.append(
                SuiteView(
                    name=one.name,
                    reference=entry.path,
                    status=entry.status,
                    scenarios=[
                        SuiteScenarioView(
                            id=scenario.id,
                            title=scenario.title,
                            kinds=sorted(scenario.kinds),
                            tags=list(scenario.tags),
                            evals=[declaration.eval for declaration in scenario.evals],
                            continues=scenario.continues,
                            not_run=one.suite.not_run.get(scenario.id),
                        )
                        for scenario in one.suite.scenarios
                    ],
                )
            )
        body = SuitesView(
            target=target.slug,
            default_environment=_default_or_none(manifest),
            environments=_environments(manifest),
            adapter_environments=manifest.adapter.environment_names,
            suites=views,
        )
        return Reply(body=body.model_dump(mode="json"))

    def runs(self, request: Request) -> Reply:
        """`agentdiag list --json`, of one Target with `?target=`."""
        from agentdiag.run.index import IndexCorrupt, list_runs

        slug = request.one("target")
        if slug is not None:
            self._target(slug)
        try:
            rows, _ = list_runs(self.workspace.root, target=slug)
        except IndexCorrupt as corrupt:
            raise RouteError(409, str(corrupt)) from corrupt
        return Reply(body=[row.model_dump(mode="json", by_alias=True) for row in rows])

    def run(self, request: Request) -> Reply:
        """One Run as the Report's `RunView`, rendered from its files now (so the story of
        a Change record it verified since is in it, decision 21's amendment)."""
        from agentdiag.report.view import run_view
        from agentdiag.run.locate import SCORECARD_FILE

        run_id = request.params["run_id"]
        run_dir = self._run_dir(run_id)
        if not (run_dir / SCORECARD_FILE).exists():
            raise RouteError(
                409,
                f"Run {run_id} is still being written (no {SCORECARD_FILE} yet); follow it "
                f"at /api/runs/{run_id}/follow",
            )
        return Reply(body=run_view(run_dir).model_dump(mode="json"))

    def follow(self, request: Request) -> Reply:
        """The Run as it is written, as server-sent events (see `_follow`)."""
        run_id = request.params["run_id"]
        with self._lock:
            launch = self._launches.get(run_id)
        run_dir = launch.target.runs / run_id if launch is not None else self._run_dir(run_id)
        return Reply(content_type=EVENT_STREAM, events=self._follow(run_dir, launch))

    def compare(self, request: Request) -> Reply:
        """`agentdiag compare <baseline> <run> [--expect …]`: `expect` repeated or a,b."""
        from agentdiag.run.compare import CompareUsageError, compare

        baseline, run = request.one("baseline"), request.one("run")
        if baseline is None or run is None:
            raise RouteError(400, "compare needs ?baseline=<run id>&run=<run id>")
        expect = [
            part.strip()
            for value in request.query.get("expect", [])
            for part in value.split(",")
            if part.strip()
        ]
        try:
            comparison = compare(self._run_dir(baseline), self._run_dir(run), expect)
        except CompareUsageError as usage:
            raise RouteError(400, str(usage)) from usage
        return Reply(body=comparison.model_dump(mode="json"))

    def changes(self, request: Request) -> Reply:
        """Every Change record's head, by Target then id; one Target's with `?target=`."""
        from agentdiag.change.record import load_records

        slug = request.one("target")
        targets = [self._target(slug)] if slug is not None else self.workspace.targets()
        return Reply(
            body=[
                record.model_dump(mode="json")
                for target in targets
                for _, record in load_records(target)
            ]
        )

    def change(self, request: Request) -> Reply:
        """One Change record as its story (`change show`), looked for under `?target=` or
        under every Target; an id two Targets hold asks for `?target=`."""
        from agentdiag.change.record import ChangeRecordNotFound, find_record
        from agentdiag.report.story import change_record_story

        record_id = request.params["record_id"]
        _checked_change(record_id)
        slug = request.one("target")
        targets = [self._target(slug)] if slug is not None else self.workspace.targets()
        found = []
        for target in targets:
            try:
                _, record, _ = find_record(target, record_id)
            except ChangeRecordNotFound:
                continue
            found.append((target, record))
        if not found:
            where = f"Target {slug}" if slug is not None else "any Target of the Workspace"
            raise RouteError(404, f"no Change record {record_id!r} under {where}")
        if len(found) > 1:
            names = ", ".join(target.slug for target, _ in found)
            raise RouteError(409, f"Change record {record_id!r} is under {names}: add ?target=")
        target, record = found[0]
        return Reply(body=change_record_story(record, target).model_dump(mode="json"))

    def flow(self, request: Request) -> Reply:
        """One Flow's definition, from the Connector's read of `env` now."""
        from agentdiag.connector.base import ConnectorError
        from agentdiag.connector.plugins import UnknownKind, build_connector
        from agentdiag.report.flow import FlowDefinitionUnreadable, flow_definition_view

        target = self._target(request.params["slug"])
        manifest = self._manifest(target)
        environment, flow_id = request.params["env"], request.params["flow_id"]
        try:
            connector = build_connector(manifest)
            if connector is None:
                raise RouteError(404, f"Target {target.slug} names no Connector to read Flows")
            read = connector.read_deployed_set(environment)
        except (ConnectorError, UnknownKind) as exc:
            raise RouteError(400, f"the Connector could not read {environment}: {exc}") from exc
        definition = read.flows.get(flow_id)
        if definition is None:
            held = ", ".join(sorted(read.flows)) or "none"
            raise RouteError(404, f"{environment} holds no Flow {flow_id!r}; it holds {held}")
        try:
            view = flow_definition_view(flow_id, definition)
        except FlowDefinitionUnreadable as unreadable:
            raise RouteError(400, str(unreadable)) from unreadable
        return Reply(body=view.model_dump(mode="json"))

    # --- writes: a Run ---

    def launch(self, request: Request) -> Reply:
        """`agentdiag run` in-process: a dry run answers here; a Run starts in a thread
        and its id comes back at once, for the page to follow."""
        from agentdiag.run.directory import new_run_id
        from agentdiag.run.execute import RunOptions
        from agentdiag.run.execute import run as execute_run
        from agentdiag.run.preflight import unknown_environment
        from agentdiag.run.record import RunStamp
        from agentdiag.scenario.select import Selection

        spec = _parsed(request, RunRequest)
        if spec.live:
            raise RouteError(400, LIVE_FROM_THE_TERMINAL)
        target = self._target(spec.target)
        manifest = self._manifest(target)
        # Checked here rather than left to preflight: a non-dry launch answers 202 and runs
        # preflight in its thread, so a 400 must be answered before that thread exists.
        _ = _default_environment(manifest)  # a Manifest with no default is a 400
        declared = manifest.adapter.environment_names
        if spec.env is not None and spec.env not in declared:
            # A Connector-only environment too: nothing converses with it (decision 12).
            raise RouteError(400, unknown_environment(spec.env, declared))
        options = RunOptions(
            target=target,
            scenario=spec.scenario,
            tag=spec.tag,
            suite=spec.suite,
            dry_run=spec.dry_run,
            trials=spec.trials,
            json_output=spec.dry_run,
            replay=self._replay(spec.replay),
            no_resync=spec.resync == "no_resync",
            strict=spec.resync == "strict",
            environment=spec.env,
        )
        if spec.dry_run:
            dry = execute_run(options)
            if dry.code != 0:
                raise RouteError(400, _sentence(dry.message or "the dry run was refused"))
            return Reply(body={"dry_run": json.loads(dry.message or "{}")})

        with self._lock:
            running = [
                launch.run_id
                for launch in self._launches.values()
                if launch.target.slug == target.slug and not launch.finished.is_set()
            ]
            if running:
                raise RouteError(409, f"Run {running[0]} of Target {target.slug} is still running")
            stamp = RunStamp.now(target.directory)
            launch = RunLaunch(
                run_id=new_run_id(stamp.created_at),
                target=target,
                selection=Selection(
                    scenario=spec.scenario, tag=spec.tag, suite=spec.suite
                ).expression(),
            )
            self._launches[launch.run_id] = launch

        def work() -> None:
            try:
                launch.exit = execute_run(
                    options, run_id=launch.run_id, stamp=stamp, cancelled=launch.cancel.is_set
                )
            except BaseException as exc:  # a Run's thread never dies unreported
                launch.error = f"the Run failed: {type(exc).__name__}: {exc}"
            finally:
                launch.finished.set()

        threading.Thread(target=work, name=f"agentdiag-run-{launch.run_id}", daemon=True).start()
        return Reply(
            status=202,
            body={
                "run_id": launch.run_id,
                "target": target.slug,
                "selection": launch.selection,
                "follow": f"/api/runs/{launch.run_id}/follow",
                "cancel": f"/api/runs/{launch.run_id}/cancel",
            },
        )

    def cancel(self, request: Request) -> Reply:
        """Ask a launched Run to start no further Trial: the Trial in progress finishes,
        and every Trial not started is recorded `not_run: cancelled`."""
        run_id = request.params["run_id"]
        with self._lock:
            launch = self._launches.get(run_id)
        if launch is None:
            raise RouteError(404, f"no Run {run_id} was launched by this server")
        if launch.finished.is_set():
            raise RouteError(409, f"Run {run_id} has already finished")
        launch.cancel.set()
        return Reply(
            status=202,
            body={
                "run_id": run_id,
                "cancelling": True,
                "detail": (
                    "the Trial in progress finishes; every Trial not started is recorded "
                    "not_run with reason cancelled"
                ),
            },
        )

    # --- writes: pull and push ---

    def pull(self, request: Request) -> Reply:
        """`agentdiag pull --env <env> [--section …] [--overwrite-local]`."""
        from agentdiag.sync.pull import pull_target

        target = self._target(request.params["slug"])
        spec = _parsed(request, PullRequest)
        done = pull_target(
            target,
            self._manifest(target),
            spec.env,
            spec.sections or None,
            overwrite_local=spec.overwrite_local,
        )
        if done.code != 0 or done.result is None:
            raise RouteError(400, _sentence(done.message))
        return Reply(body={"result": done.result.model_dump(mode="json"), "message": done.message})

    def push_preview(self, request: Request) -> Reply:
        """`agentdiag push --env <env>` without `--push`: the preview, kept under an id when
        nothing refuses it, for the push that confirms it."""
        from agentdiag.sync.push import PREVIEW_MAX_AGE_S, preview

        target = self._target(request.params["slug"])
        spec = _parsed(request, PreviewRequest)
        shown = preview(
            target, self._manifest(target), spec.env, spec.sections or None, restore=spec.restore
        )
        preview_id = None
        if not shown.refusals:
            preview_id = secrets.token_hex(PREVIEW_ID_BYTES)
            with self._lock:
                self._forget_stale()
                self._previews[preview_id] = KeptPreview(
                    slug=target.slug,
                    environment=spec.env,
                    sections=spec.sections or None,
                    restore=spec.restore,
                    preview=shown,
                    taken=self.clock(),
                )
        return Reply(
            body={
                "preview_id": preview_id,
                "max_age_s": PREVIEW_MAX_AGE_S,
                "preview": shown.model_dump(mode="json", exclude={"read"}),
            }
        )

    def push(self, request: Request) -> Reply:
        """The push that confirms a kept preview (decision 22 → decision 13), mapping the
        confirm step onto `push_target`: a typed name is `typed` recorded as `ui_confirm`;
        the push box is `write` alone, which a protected environment refuses (the box never
        stands in for the name) and an unprotected one takes as `--push`. A kept preview is
        used once: taken under the lock, and kept again only when nothing was written."""
        from agentdiag.sync.push import MOVED_EXIT, PREVIEW_MAX_AGE_S, push_target

        target = self._target(request.params["slug"])
        spec = _parsed(request, PushRequest)
        _checked_change(spec.change)
        with self._lock:
            kept = self._previews.pop(spec.preview_id, None)
        if kept is None:
            raise RouteError(404, f"no preview {spec.preview_id!r} is kept: preview again")
        age = self.clock() - kept.taken
        if age > PREVIEW_MAX_AGE_S:
            raise RouteError(
                409,
                f"the preview was taken {age:.0f} s ago, not within the last "
                f"{PREVIEW_MAX_AGE_S} s: preview again",
            )

        def keep_and_refuse(status: int, sentence: str) -> RouteError:
            with self._lock:
                self._previews[spec.preview_id] = kept
            return RouteError(status, sentence)

        if kept.slug != target.slug:
            raise keep_and_refuse(400, f"preview {spec.preview_id} is of Target {kept.slug}")
        environment = kept.environment
        typed = spec.confirm.typed_name
        if not kept.preview.protected:
            if typed is not None:
                raise keep_and_refuse(
                    400,
                    f"{environment} is not protected: its push is confirmed by the push box "
                    "(--push), not a typed name",
                )
            if not spec.confirm.push:
                raise keep_and_refuse(
                    400,
                    f"{environment} is written only on an explicit push: tick the push box "
                    "(--push) to confirm it",
                )
        done = push_target(
            target,
            self._manifest(target),
            environment,
            kept.sections,
            write=True,
            change=spec.change,
            restore=kept.restore,
            terminal=True,
            typed=typed,
            shown=kept.preview,
            typed_as="ui_confirm",
        )
        if done.ask is not None:
            raise keep_and_refuse(
                400,
                f"{environment} is protected: the push box does not stand in for the "
                "environment's name typed by a person",
            )
        if done.untouched:
            raise keep_and_refuse(400, _sentence(done.error or "the push was refused"))
        if done.code == MOVED_EXIT:
            raise RouteError(409, _sentence(done.error or "the deployed set moved: preview again"))
        if done.outcome is None:
            raise RouteError(400, _sentence(done.error or "the push was refused"))
        written = _pushed(target, done)
        if done.code != 0:
            return Reply(status=500, body={"error": _sentence(done.error or ""), **written})
        return Reply(body=written)

    # --- helpers ---

    def _target(self, slug: str | None) -> TargetPaths:
        try:
            return self.workspace.resolve(slug)
        except WorkspaceError as missing:
            raise RouteError(404 if slug is not None else 400, str(missing)) from missing

    def _manifest(self, target: TargetPaths) -> Manifest:
        from agentdiag.run.manifest import ManifestError, ManifestNotFound, load_manifest

        try:
            return load_manifest(target)
        except (ManifestNotFound, ManifestError) as problem:
            raise RouteError(400, str(problem)) from problem

    def _run_dir(self, run_id: str) -> Path:
        """The Run directory of `run_id` under the Workspace's Targets; only an id in the
        Run id grammar is looked for, so no request names a path."""
        from agentdiag.run.directory import is_run_id

        if not is_run_id(run_id):
            raise RouteError(404, f"{run_id!r} is not a Run id")
        found = [t.runs / run_id for t in self.workspace.targets() if (t.runs / run_id).is_dir()]
        if not found:
            raise RouteError(404, f"no Run {run_id} under any Target of the Workspace")
        if len(found) > 1:
            raise RouteError(409, f"Run {run_id} is under several Targets: {found}")
        return found[0]

    def _replay(self, replay: str | None) -> Path | None:
        if replay is None:
            return None
        path = Path(replay)
        path = path if path.is_absolute() else self.workspace.root / path
        if not path.is_file():
            raise RouteError(400, f"the replay file {path} does not exist")
        return path

    def _forget_stale(self) -> None:
        from agentdiag.sync.push import PREVIEW_MAX_AGE_S

        now = self.clock()
        for preview_id in [
            key for key, kept in self._previews.items() if now - kept.taken > PREVIEW_MAX_AGE_S
        ]:
            del self._previews[preview_id]

    def _follow(self, run_dir: Path, launch: RunLaunch | None) -> Iterator[str]:
        """The Run's Events as server-sent events (`_frames`), with a `: keepalive` comment
        whenever `keepalive_s` passes with nothing to send.

        The frames are made on a thread of their own and handed over a queue, so a follow
        that is only waiting still writes, and a page that went away is noticed at the next
        write: the handler then closes this generator, which tells the thread to stop. An
        exception while following is an `error` event and a `finished` one, never a
        traceback."""
        frames: queue.Queue[str | None] = queue.Queue()
        departed = threading.Event()

        def produce() -> None:
            try:
                for frame in self._frames(run_dir, launch, departed):
                    frames.put(frame)
            except Exception as exc:  # a follow ends in words, never in a traceback
                frames.put(_event("error", {"error": f"{type(exc).__name__}: {exc}"}))
                frames.put(_event("finished", self._finished(run_dir, launch, "error")))
            finally:
                frames.put(None)

        threading.Thread(
            target=produce, name=f"agentdiag-follow-{run_dir.name}", daemon=True
        ).start()
        try:
            while True:
                try:
                    frame = frames.get(timeout=self.keepalive_s)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                if frame is None:
                    return
                yield frame
        finally:
            departed.set()

    def _frames(
        self, run_dir: Path, launch: RunLaunch | None, departed: threading.Event
    ) -> Iterator[str]:
        """`run` once `run.json` reads whole (the Trials in the order a Run writes them:
        sweep by sweep, Suite order within one); per Trial `trial`, then one `event` per
        line `show --follow --json` reads (the Trace's Events, then the judgement's), then
        `scores`; last, `finished`, with the exit code when this server launched the Run.
        A Trial the Run never started (a cancel) is announced and passed with no Events. A
        Run this server did not launch is followed until nothing has changed for
        `follow_idle_s` after the last Event, and then `finished` says so (`idle`). A file
        read while it is being written is read again at the next poll."""
        from agentdiag.run.locate import (
            RUN_RECORD_FILE,
            SCORECARD_FILE,
            SCORES_FILE,
            trace_path,
            trial_dir,
        )
        from agentdiag.trace.show import follow_story

        record_file = run_dir / RUN_RECORD_FILE
        scorecard = run_dir / SCORECARD_FILE
        last_change = self.clock()

        def idle() -> bool:
            return launch is None and self.clock() - last_change > self.follow_idle_s

        def ended() -> bool:
            return (
                self.closing.is_set()
                or departed.is_set()
                or scorecard.exists()
                or (launch is not None and launch.finished.is_set())
                or idle()
            )

        record = _whole_json(record_file)
        while record is None:
            if ended() or (launch is None and not record_file.exists()):
                yield _event("finished", self._finished(run_dir, launch, _why(idle())))
                return
            time.sleep(FOLLOW_POLL_MS / 1000)
            record = _whole_json(record_file)
        scenarios = [entry["id"] for entry in record.get("scenarios") or []]
        order = [
            (number, scenario)
            for number in range(1, int(record.get("trials") or 1) + 1)
            for scenario in scenarios
        ]
        yield _event(
            "run",
            {
                "run_id": record.get("run_id"),
                "target": record.get("target"),
                "selection": (record.get("selection") or {}).get("expression"),
                "trials": [{"scenario": s, "trial": n} for n, s in order],
            },
        )
        for index, (number, scenario) in enumerate(order, start=1):
            where = {"scenario": scenario, "trial": number}
            yield _event("trial", {**where, "index": index, "of": len(order)})
            stop = _stopper(ended, trace_path(run_dir, scenario, number))
            for line in follow_story(
                run_dir, scenario, number, raw=True, poll_ms=FOLLOW_POLL_MS, stop=stop
            ):
                last_change = self.clock()
                yield _event("event", {**where, "event": json.loads(line)})
            scores = trial_dir(run_dir, scenario, number) / SCORES_FILE
            written = _whole_json(scores)
            while written is None and scores.exists() and not idle() and not departed.is_set():
                time.sleep(FOLLOW_POLL_MS / 1000)
                written = _whole_json(scores)
            yield _event("scores", {**where, "scores": (written or {}).get("scores")})
        while not ended():
            time.sleep(FOLLOW_POLL_MS / 1000)
        if launch is not None:
            launch.finished.wait()
        yield _event("finished", self._finished(run_dir, launch, _why(idle())))

    def _finished(
        self, run_dir: Path, launch: RunLaunch | None, reason: str | None = None
    ) -> dict[str, Any]:
        from agentdiag.run.locate import SCORECARD_FILE

        exit_state = launch.exit if launch is not None else None
        return {
            "run_id": run_dir.name,
            "scorecard": (run_dir / SCORECARD_FILE).exists(),
            "code": exit_state.code if exit_state is not None else None,
            "message": (
                exit_state.message if exit_state is not None else launch.error if launch else None
            ),
            "reason": reason,
            "report": f"#run/{run_dir.name}",
        }


# --- module helpers ---


def _pattern(template: str) -> re.Pattern[str]:
    """`/api/runs/<run_id>` as a pattern: each `<name>` one non-empty path segment."""
    return re.compile(re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", template))


def _parsed[M: BaseModel](request: Request, model: type[M]) -> M:
    try:
        return model.model_validate_json(request.body or b"{}")
    except ValidationError as invalid:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in problem['loc']) or 'body'}: {problem['msg']}"
            for problem in invalid.errors()
        )
        raise RouteError(400, f"the request body is not a {model.__name__}: {problems}") from None


def _checked_change(record_id: str | None) -> None:
    """A Change record id in the record id grammar, or `none`/absent; else 404 before any
    path is built, naming no path."""
    from agentdiag.change.record import is_record_id
    from agentdiag.sync.push import NO_CHANGE

    if record_id not in (None, NO_CHANGE) and not is_record_id(record_id):
        raise RouteError(404, f"{record_id!r} is not a Change record id")


def _sentence(message: str) -> str:
    """A command's message as the error body's sentence: its first line, without `error: `."""
    first = message.strip().splitlines()[0] if message.strip() else message
    return first.removeprefix("error: ")


def _environments(manifest: Manifest) -> list[str]:
    """Every environment the Adapter or the Connector names, the Adapter's first."""
    known = [*manifest.adapter.environment_names]
    if manifest.connector is not None:
        known += [name for name in manifest.connector.environments if name not in known]
    return known


def _default_environment(manifest: Manifest) -> str:
    """The Adapter's default environment; 400 when the Manifest names none."""
    from agentdiag.run.manifest import ManifestError

    try:
        return manifest.adapter.default_environment
    except ManifestError as problem:
        raise RouteError(400, str(problem)) from problem


def _default_or_none(manifest: Manifest) -> str | None:
    try:
        return _default_environment(manifest)
    except RouteError:
        return None


def _stopper(ended: Callable[[], bool], trace: Path) -> Callable[[], bool]:
    """The `stop` a followed Trial's tail asks: never while the Run is running; at once
    after it when the Trial left no Trace (it never started); else only once the tail has
    read the file again since the Run ended, so no line written before the end is lost
    (`follow` asks `stop` before and after each read, so the third ask follows a read)."""
    asked = 0

    def stop() -> bool:
        nonlocal asked
        if not ended():
            return False
        if not trace.exists():
            return True
        asked += 1
        return asked > 2

    return stop


def _whole_json(path: Path) -> dict[str, Any] | None:
    """A JSON file's object, or None while it is missing or still being written."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _why(idle: bool) -> str | None:
    """Why a follow ended, when not because the Run finished."""
    return "idle" if idle else None


def _event(name: str, data: Mapping[str, Any]) -> str:
    """One server-sent event: its name and its JSON on one `data:` line."""
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _pushed(target: TargetPaths, done: PushExit) -> dict[str, Any]:
    """What the push route answers after a write: the files (relative to the Target
    directory, as the Push record and the Change record's push event name them), both
    Fingerprints, the deployed Fingerprints the receipt carries, and the sections."""
    from agentdiag.sync.push import in_target
    from agentdiag.sync.pushes import load_push_record

    outcome = done.outcome
    assert outcome is not None
    record = load_push_record(outcome.push_record)
    return {
        "push_record": in_target(target, outcome.push_record),
        "restore_point": in_target(target, outcome.restore_point),
        "fingerprint_before": outcome.fingerprint_before,
        "fingerprint_after": outcome.fingerprint_after,
        "deployed_fingerprint_before": outcome.receipt.fingerprint_before,
        "deployed_fingerprint_after": outcome.receipt.fingerprint_after,
        "sections": outcome.sections,
        "confirmed_by": record.confirmed_by,
        "change_record": (
            in_target(target, outcome.change_record) if outcome.change_record else None
        ),
        "message": done.message,
    }


__all__ = [
    "LIVE_FROM_THE_TERMINAL",
    "App",
    "Reply",
    "RouteError",
    "RunLaunch",
    "RunRequest",
    "SuiteView",
    "SuitesView",
    "SyncScreenView",
]
