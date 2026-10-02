"""The README's walkthroughs are what the commands print (phase-6 decision 44).

"Point it at a Workspace of Targets" pastes the real output of `registry` and `target show`;
this test types every `uv run agentdiag …` line of that section into a temporary root, in
order, and compares what the two commands print with the blocks the README shows, so the
section cannot drift from the CLI. The one substitution is the documented root, because a
test must not write into whatever `/tmp/agentdiag-shop` currently holds. "Keep the Target in
Sync" is typed the same way, over a copy of the example (ticket 10).
"""

from __future__ import annotations

import re
import shlex
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentdiag.cli import app

REPO = Path(__file__).resolve().parents[1]
GUIDE = REPO / "docs" / "guide.md"
SECTION = "## Point it at a Workspace of Targets"
DOCUMENTED_ROOT = "/tmp/agentdiag-shop"
BLOCK = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)

runner = CliRunner()


def section() -> str:
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(SECTION)
    return text[start : text.index("\n## ", start + 1)]


def blocks() -> list[tuple[str, str]]:
    """Every fenced block of the section, as (language, body), in order."""
    return [(match.group(1), match.group(2)) for match in BLOCK.finditer(section())]


def test_the_workspace_walkthrough_prints_what_the_readme_shows(tmp_path: Path) -> None:
    root = tmp_path / "agentdiag-shop"
    printed: dict[str, str] = {}
    expected: dict[str, str] = {}
    last: str | None = None

    for language, body in blocks():
        if language == "bash":
            for line in body.splitlines():
                words = [root.as_posix() if w == DOCUMENTED_ROOT else w for w in shlex.split(line)]
                assert words[:3] == ["uv", "run", "agentdiag"], line
                result = runner.invoke(app, words[3:])
                assert result.exit_code == 0, f"`{line}` exited {result.exit_code}: {result.output}"
                last = words[3]
                printed[last] = result.stdout
        else:
            assert last is not None, "an output block before any command"
            expected[last] = body

    assert set(expected) == {"registry", "target"}
    assert printed["registry"] == expected["registry"]
    assert printed["target"] == expected["target"]


SYNC_SECTION = "## Keep the Target in Sync"
SYNC_ROOT = "/tmp/agentdiag-sync"
EDIT_MARKER = "<!-- edit: prompt.py rule 4 -->"
PROMPT_READ_AT = "agentdiag.examples.toy.target.SYSTEM_PROMPT"
BUILT = re.compile(r"built \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
BREAK_FILE = re.compile(r"sync-breaks/\d{8}T\d{6}Z(-\d+)?\.json")


def moment_free(text: str) -> str:
    """`text` with the two moments no two runs share: when a Fingerprint was built, and the
    Sync break file named for when it was opened."""
    return BREAK_FILE.sub("sync-breaks/<time>.json", BUILT.sub("built <time>", text))


def test_the_sync_walkthrough_prints_what_the_readme_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every `uv run agentdiag …` line of "Keep the Target in Sync", typed into a copy of
    the example in order, prints its block and exits as its `# exits N` says. The prompt
    edit the prose asks for is made through `monkeypatch` where the toy reads it; the one
    other substitutions are the moments no two runs share (`moment_free`)."""
    from agentdiag.examples.toy import SYSTEM_PROMPT

    root = tmp_path / "agentdiag-sync"
    shutil.copytree(REPO / "examples" / "toy", root, ignore=shutil.ignore_patterns("runs"))
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(SYNC_SECTION)
    body = text[start : text.index("\n## ", start + 1)]
    matches = list(BLOCK.finditer(body))
    assert matches
    pending: tuple[str, str] | None = None
    compared = 0
    previous_end = 0
    for match in matches:
        language, block = match.group(1), match.group(2)
        if EDIT_MARKER in body[previous_end : match.start()]:
            monkeypatch.setattr(
                PROMPT_READ_AT,
                SYSTEM_PROMPT.replace("at most three sentences", "at most two sentences"),
            )
        previous_end = match.end()
        if language == "bash":
            (line,) = block.strip().splitlines()
            command, _, comment = line.partition("#")
            exits = int(comment.split()[-1]) if comment.strip() else 0
            words = [str(root) if w == SYNC_ROOT else w for w in shlex.split(command)]
            assert words[:3] == ["uv", "run", "agentdiag"], line
            result = runner.invoke(app, words[3:])
            assert result.exit_code == exits, f"`{line}` exited {result.exit_code}: {result.output}"
            pending = (line, result.stdout.replace(str(root), SYNC_ROOT))
        else:
            assert pending is not None, "an output block before any command"
            assert moment_free(pending[1]) == moment_free(block), pending[0]
            compared += 1
            pending = None
    assert compared == 5


DISCOVER_SECTION = "## Discover an unknown Target"
DISCOVER_ROOT = "/tmp/agentdiag-discover"


def test_the_discovery_walkthrough_prints_what_the_readme_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every `uv run agentdiag …` line of "Discover an unknown Target", typed into a
    temporary root from the repository root (the scan names `src/agentdiag/examples/toy`),
    prints its block; the `yaml` block is the draft's own text, line for line."""
    monkeypatch.chdir(REPO)
    root = tmp_path / "agentdiag-discover"
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(DISCOVER_SECTION)
    body = text[start : text.index("\n## ", start + 1)]
    pending: str | None = None
    compared = 0
    for match in BLOCK.finditer(body):
        language, block = match.group(1), match.group(2)
        if language == "bash":
            for line in block.strip().splitlines():
                words = [w.replace(DISCOVER_ROOT, root.as_posix()) for w in shlex.split(line)]
                assert words[:3] == ["uv", "run", "agentdiag"], line
                result = runner.invoke(app, words[3:])
                assert result.exit_code == 0, f"`{line}` exited {result.exit_code}: {result.output}"
                pending = result.stdout.replace(root.as_posix(), DISCOVER_ROOT)
        elif language == "yaml":
            draft = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.draft.yaml"
            assert block in draft.read_text(encoding="utf-8")
            compared += 1
        else:
            assert pending is not None, "an output block before any command"
            assert pending == block
            compared += 1
            pending = None
    assert compared == 5


GENERATE_SECTION = "## Generate a Suite"
GENERATE_ROOT = "/tmp/agentdiag-generate"


def test_the_generation_walkthrough_prints_what_the_readme_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every `uv run agentdiag …` line of "Generate a Suite", typed into a temporary root
    from the repository root (the drafts are `tests/fixtures/drafts/toy-order-desk.yaml`),
    prints its block; the `yaml` block is the written Suite's own text (ticket 12)."""
    monkeypatch.chdir(REPO)
    root = tmp_path / "agentdiag-generate"
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(GENERATE_SECTION)
    body = text[start : text.index("\n## ", start + 1)]
    pending: str | None = None
    compared = 0
    for match in BLOCK.finditer(body):
        language, block = match.group(1), match.group(2)
        if language == "bash":
            for line in block.strip().splitlines():
                words = [w.replace(GENERATE_ROOT, root.as_posix()) for w in shlex.split(line)]
                assert words[:3] == ["uv", "run", "agentdiag"], line
                result = runner.invoke(app, words[3:])
                assert result.exit_code == 0, f"`{line}` exited {result.exit_code}: {result.output}"
                pending = result.stdout.replace(root.as_posix(), GENERATE_ROOT)
        elif language == "yaml":
            suite = root / ".agentdiag" / "targets" / "default" / "suites" / "generated.yaml"
            assert block in suite.read_text(encoding="utf-8")
            compared += 1
        else:
            assert pending is not None, "an output block before any command"
            assert pending == block
            compared += 1
            pending = None
    assert compared == 4


IMPORT_SECTION = "## Import a conversation"
IMPORT_ROOT = "/tmp/agentdiag-import"
RUN_ID = re.compile(r"\d{8}T\d{6}Z-[a-z2-7]{4}")
CREATED = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
SIZE = re.compile(r"\d+ KB$", re.MULTILINE)
"""The listing's size column: an imported Run records the absolute path of its source
rows, so the bytes on disk differ from one machine to another."""


def run_free(text: str) -> str:
    """`text` with what no two imports share: the Run id and when the Run was made, each
    padded as the listing pads it, and the size on disk."""
    sized = SIZE.sub("<size>", text)
    return CREATED.sub("<created>           ", RUN_ID.sub("<run id>             ", sized))


def test_the_import_walkthrough_prints_what_the_readme_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every `uv run agentdiag …` line of "Import a conversation", typed into a temporary
    root from the repository root (the rows are `tests/fixtures/evidence/`), prints its block
    and exits as its `# exits N` says; `<run>` is the id `import` printed, and the Run ids
    and creation times are set aside (ticket 26)."""
    monkeypatch.chdir(REPO)
    root = tmp_path / "agentdiag-import"
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(IMPORT_SECTION)
    body = text[start : text.index("\n## ", start + 1)]
    pending: str | None = None
    imported: str | None = None
    compared = 0
    for match in BLOCK.finditer(body):
        language, block = match.group(1), match.group(2)
        if language == "bash":
            for line in block.strip().splitlines():
                command, _, comment = line.partition("  #")
                exits = int(comment.split()[-1]) if comment.strip() else 0
                words = [
                    imported
                    if w == "<run>" and imported
                    else w.replace(IMPORT_ROOT, root.as_posix())
                    for w in shlex.split(command)
                ]
                assert words[:3] == ["uv", "run", "agentdiag"], line
                result = runner.invoke(app, [str(w) for w in words[3:]])
                assert result.exit_code == exits, (
                    f"`{line}` exited {result.exit_code}: {result.output}"
                )
                pending = result.stdout.replace(root.as_posix(), IMPORT_ROOT)
                if words[3] == "import":
                    found = RUN_ID.search(result.stdout)
                    assert found is not None
                    imported = found.group(0)
        else:
            assert pending is not None, "an output block before any command"
            assert run_free(pending) == run_free(block)
            compared += 1
            pending = None
    assert compared == 4


CHANGE_SECTION = "## Record a fix as a Change record"
CHANGE_ROOT = "/tmp/agentdiag-change"
CHANGE_RUNS = ("20260923T100000Z-base", "20260923T100200Z-prmt")
MOMENT = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
OPENED_ID = re.compile(r"\b\d{8}-refund-promised-on-a-shipped-order\b")


def change_free(text: str) -> str:
    """`text` with what no two walks share: the moment each command ran, and the day in the
    id of the record opened today; both keep their width, so padded columns still align."""
    return OPENED_ID.sub(
        "<day>-refund-promised-on-a-shipped-order", MOMENT.sub("<moment>          ", text)
    )


def change_walkthrough(root: Path) -> list[tuple[str, str]]:
    """Every `uv run agentdiag …` line of "Record a fix as a Change record", typed into a copy
    of the example holding the two Run fixtures, in order: (the bash block, what its
    commands printed, stdout and stderr), with `<id>` the id `open` printed. The `cp` and
    `mkdir` lines are the setup, done here."""
    shutil.copytree(REPO / "examples" / "toy", root, ignore=shutil.ignore_patterns("runs"))
    runs = root / ".agentdiag" / "targets" / "toy-order-desk" / "runs"
    for run in CHANGE_RUNS:
        shutil.copytree(REPO / "tests" / "fixtures" / "runs" / run, runs / run)
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(CHANGE_SECTION)
    body = text[start : text.index("\n## ", start + 1)]
    opened: str | None = None
    walked: list[tuple[str, str]] = []
    for match in BLOCK.finditer(body):
        language, block = match.group(1), match.group(2)
        if language != "bash":
            continue
        printed = ""
        for line in block.strip().splitlines():
            if not line.startswith("uv run agentdiag"):
                continue
            command, _, comment = line.partition("  #")
            exits = int(comment.split()[-1]) if comment.strip() else 0
            words = [
                opened if w == "<id>" and opened else w.replace(CHANGE_ROOT, root.as_posix())
                for w in shlex.split(command)
            ]
            result = runner.invoke(app, [str(w) for w in words[3:]])
            assert result.exit_code == exits, f"`{line}` exited {result.exit_code}: {result.output}"
            printed += result.output.replace(root.as_posix(), CHANGE_ROOT)
            if words[3:5] == ["change", "open"]:
                opened = result.output.split()[1]
        walked.append((block, printed))
    return walked


def test_the_change_record_walkthrough_prints_what_the_readme_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every `uv run agentdiag …` line of "Record a fix as a Change record" prints the block
    after it and exits as its `# exits N` says (ticket 25); the moments and today's day in
    the opened record's id are set aside (`change_free`)."""
    monkeypatch.chdir(REPO)
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(CHANGE_SECTION)
    body = text[start : text.index("\n## ", start + 1)]
    shown = [match.group(2) for match in BLOCK.finditer(body) if match.group(1) == ""]
    walked = change_walkthrough(tmp_path / "agentdiag-change")

    assert len(walked) == len(shown) == 5
    for (block, printed), expected in zip(walked, shown, strict=True):
        assert change_free(printed) == change_free(expected), block


PUSH_SECTION = "## Pull and push"
PUSH_ROOT = "/tmp/agentdiag-push"
COMPACT_MOMENT = re.compile(r"\d{8}T\d{6}Z")
GIT_INDEX = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+ \d+$", re.MULTILINE)


def push_free(text: str) -> str:
    """`text` with what no two walks share: each moment (a read, a build, a file named for
    when it was written) and the blobs and file mode git prints on an index line."""
    text = COMPACT_MOMENT.sub("<compact>", MOMENT.sub("<moment>", text))
    return GIT_INDEX.sub("index <blobs>", text)


def test_the_pull_and_push_walkthrough_prints_what_the_readme_shows(tmp_path: Path) -> None:
    """Every `uv run agentdiag …` line of "Pull and push" prints the block after it and exits
    as its `# exits N` says (ticket 27). The `cp` line is done here, leaving out what using
    the example writes; the `git` and `sed` lines run in a shell, as written. Before each
    command the help desk's platform module is put back as it is defined, because each
    `uv run` is a fresh process: what persists between them is the platform's `store`."""
    import copy
    import os
    import subprocess

    from agentdiag.examples.helpdesk import platform

    pristine = {name: copy.deepcopy(getattr(platform, name)) for name in ("DEPLOYED", "STAGING")}

    def fresh_process() -> None:
        for name, value in pristine.items():
            held = getattr(platform, name)
            held.clear()
            held.update(copy.deepcopy(value))

    root = tmp_path / "agentdiag-push"
    shutil.copytree(
        REPO / "examples" / "workspace",
        root,
        ignore=shutil.ignore_patterns(
            "runs", "restore-points", "platform", "sync-breaks", "index.sqlite"
        ),
    )
    shell_environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(PUSH_SECTION)
    body = text[start : text.index("\n## ", start + 1)]
    shown = [match.group(2) for match in BLOCK.finditer(body) if match.group(1) == ""]
    walked: list[str] = []
    try:
        for match in BLOCK.finditer(body):
            if match.group(1) != "bash":
                continue
            printed = ""
            for line in match.group(2).strip().splitlines():
                if line.startswith("cp -r examples/workspace"):
                    continue
                if not line.startswith("uv run agentdiag"):
                    subprocess.run(
                        ["bash", "-c", line.replace(PUSH_ROOT, root.as_posix())],
                        check=True,
                        capture_output=True,
                        env=shell_environment,
                    )
                    continue
                command, _, comment = line.partition("  #")
                exits = int(comment.split()[-1]) if comment.strip() else 0
                words = [w.replace(PUSH_ROOT, root.as_posix()) for w in shlex.split(command)]
                fresh_process()
                result = runner.invoke(app, words[3:])
                assert result.exit_code == exits, (
                    f"`{line}` exited {result.exit_code}: {result.output}"
                )
                printed += result.output.replace(root.as_posix(), PUSH_ROOT)
            if printed:
                walked.append(printed)
    finally:
        fresh_process()

    assert len(walked) == len(shown) == 3
    for printed, expected in zip(walked, shown, strict=True):
        assert push_free(printed) == push_free(expected)


SERVE_SECTION = "## Serve the UI"
SERVE_ROOT = "/tmp/agentdiag-serve"


class _Interrupted:
    """A bound server whose `serve_forever` returns at once, as if Ctrl-C came first."""

    def __init__(self, server: Any) -> None:
        self.server = server

    def __getattr__(self, name: str) -> Any:
        return getattr(self.server, name)

    def serve_forever(self) -> None:
        raise KeyboardInterrupt


def test_the_serve_section_shows_what_serve_prints_and_what_its_registry_route_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Serve the UI": the line `serve` prints is the one the README shows (on a free port
    here, shown as the default one, and the root as documented), and the `json` block is
    the first entry `GET /api/registry` answers over a copy of the example Workspace."""
    import json

    from agentdiag.serve import server as serving
    from agentdiag.serve.app import App
    from agentdiag.workspace import Workspace

    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(SERVE_SECTION)
    body = text[start : text.index("\n## ", start + 1)]
    command, printed, answered = BLOCK.findall(body)
    assert command == ("bash", f"uv run agentdiag serve --root {SERVE_ROOT}\n")
    root = tmp_path / "agentdiag-serve"
    shutil.copytree(
        REPO / "examples" / "workspace",
        root,
        ignore=shutil.ignore_patterns("runs", "platform", "restore-points", "index.sqlite"),
    )
    workspace = Workspace.find(root)
    bind = serving.make_server
    monkeypatch.setattr(serving, "make_server", lambda w, port: _Interrupted(bind(w, 0)))
    lines: list[str] = []

    assert serving.serve(workspace, echo=lines.append) == 0

    (line,) = lines
    shown = re.sub(r"127\.0\.0\.1:\d+", f"127.0.0.1:{serving.DEFAULT_PORT}", line)
    assert printed == ("", shown.replace(str(root), SERVE_ROOT) + "\n")
    assert answered[0] == "json"
    assert json.loads(answered[1]) == App(workspace).handle("GET", "/api/registry").body[0]


DASHBOARD_SECTION = "## The Dashboard"
DASHBOARD_ROOT = "/tmp/agentdiag-dashboard"


def test_the_dashboard_section_prints_what_the_readme_shows(tmp_path: Path) -> None:
    """ "The Dashboard": the `cp` line is a copy of the example Workspace (without the
    gitignored Index), and `dashboard` over it prints the block the README shows."""
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(DASHBOARD_SECTION)
    body = text[start : text.index("\n## ", start + 1)]
    (command, printed) = BLOCK.findall(body)
    copy_line, dashboard_line = command[1].strip().splitlines()
    assert command[0] == "bash"
    assert copy_line == f"cp -r examples/workspace {DASHBOARD_ROOT}"
    root = tmp_path / "agentdiag-dashboard"
    shutil.copytree(
        REPO / "examples" / "workspace",
        root,
        ignore=shutil.ignore_patterns("runs", "platform", "restore-points", "index.sqlite"),
    )
    words = [str(root) if w == DASHBOARD_ROOT else w for w in shlex.split(dashboard_line)]
    assert words[:3] == ["uv", "run", "agentdiag"], dashboard_line

    result = runner.invoke(app, words[3:])

    assert result.exit_code == 0, result.output
    assert printed == ("", result.stdout.replace(str(root), DASHBOARD_ROOT))


HTTP_SECTION = "## Drive a Target over HTTP"


def test_the_http_section_manifest_validates_and_drives_the_fake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The section "Drive a Target over HTTP" (phase-8 decision 11): its Manifest block
    parses, is the reference fixture's in substance, `validate` passes it, and a Run of the
    reference Suite over the fake endpoint at its `base_url` completes, the token sent and
    written nowhere."""
    import yaml

    from tests.fakes.http_target import (
        IDENTITY_HEADER,
        REFERENCE_MANIFEST,
        REFERENCE_SUITE,
        TOKEN_VARIABLE,
        serving_target,
    )

    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(HTTP_SECTION)
    body = text[start : text.index("\n## ", start + 1)]
    (block,) = [match.group(2) for match in BLOCK.finditer(body) if match.group(1) == "yaml"]
    documented = yaml.safe_load(block)
    reference = yaml.safe_load(REFERENCE_MANIFEST.read_text(encoding="utf-8"))
    assert documented["adapter"] == reference["adapter"]
    assert documented["suites"] == reference["suites"]

    monkeypatch.setenv(TOKEN_VARIABLE, "tok-readme-not-a-secret")
    root = tmp_path / "http"
    directory = root / ".agentdiag" / "targets" / "http-echo"
    (directory / "suites").mkdir(parents=True)
    shutil.copy(REFERENCE_SUITE, directory / "suites" / "http-echo.yaml")
    with serving_target(
        "sse-json", require_token="tok-readme-not-a-secret", echo_headers=[IDENTITY_HEADER]
    ) as fake:
        documented["adapter"]["environments"]["dev"]["base_url"] = fake.base_url
        (directory / "manifest.yaml").write_text(yaml.safe_dump(documented), encoding="utf-8")
        validated = runner.invoke(app, ["validate", "--root", str(root)])
        ran = runner.invoke(app, ["run", "--root", str(root)])

    assert validated.exit_code == 0, validated.output
    assert ran.exit_code == 0, ran.output
    assert [request.authorized for request in fake.requests] == [True]
    assert fake.requests[0].echoed == {IDENTITY_HEADER: "+1 555 0100"}
    for path in root.rglob("*"):
        if path.is_file():
            assert b"tok-readme-not-a-secret" not in path.read_bytes(), path


ORIENTATION_SECTION = "## Operate it with a coding agent"
FENCE = re.compile(r"^```", re.MULTILINE)


def fenced_section(heading: str) -> str:
    """The section under `heading`, to the next `## ` heading outside a fenced block: this
    section shows the Orientation page, whose own `## ` headings sit inside a fence."""
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(heading)
    inside = False
    at = start + len(heading)
    for line in text[at:].splitlines(keepends=True):
        if FENCE.match(line):
            inside = not inside
        elif line.startswith("## ") and not inside:
            return text[start:at]
        at += len(line)
    return text[start:]


def test_the_orientation_walkthrough_prints_what_the_guide_shows(tmp_path: Path) -> None:
    """ "Operate it with a coding agent" continues the Workspace walkthrough above it: those
    `uv run agentdiag …` lines are typed first, as the setup, then this section's lines in
    order (ticket 45). A `cat` line reads its files here, a `sed` line runs in a shell as
    written, and each output block is what the bash block before it printed."""
    import subprocess

    root = tmp_path / "agentdiag-shop"

    def typed(line: str) -> str:
        words = [w.replace(DOCUMENTED_ROOT, root.as_posix()) for w in shlex.split(line)]
        if words[0] == "cat":
            return "".join(Path(w).read_text(encoding="utf-8") for w in words[1:])
        if words[:3] != ["uv", "run", "agentdiag"]:
            subprocess.run(
                ["bash", "-c", line.replace(DOCUMENTED_ROOT, root.as_posix())],
                check=True,
                capture_output=True,
            )
            return ""
        result = runner.invoke(app, words[3:])
        assert result.exit_code == 0, f"`{line}` exited {result.exit_code}: {result.output}"
        return result.stdout.replace(root.as_posix(), DOCUMENTED_ROOT)

    for language, body in blocks():
        if language == "bash":
            for line in body.splitlines():
                typed(line)

    pending: str | None = None
    compared = 0
    for match in BLOCK.finditer(fenced_section(ORIENTATION_SECTION)):
        language, body = match.group(1), match.group(2)
        if language == "bash":
            pending = "".join(typed(line) for line in body.strip().splitlines())
        else:
            assert pending is not None, "an output block before any command"
            assert pending == body
            compared += 1
            pending = None
    assert compared == 5
    assert "Harness" in fenced_section(ORIENTATION_SECTION)
    for harness, reads in (("Codex", "AGENTS.md"), ("Claude Code", "CLAUDE.md")):
        assert f"| {harness} | `{reads}` |" in fenced_section(ORIENTATION_SECTION)
    assert "| Gemini CLI | `GEMINI.md` |" in fenced_section(ORIENTATION_SECTION)

    section = fenced_section(ORIENTATION_SECTION)
    for layout in ("`.agents/skills/agentdiag-<name>/`", "`core.symlinks`", "`--force`"):
        assert layout in section, layout


README_OPERATE = "## Operate it with a coding agent"
README_ROOT = "/tmp/agentdiag-demo"


def test_the_readme_install_block_is_what_init_skills_prints(tmp_path: Path) -> None:
    """The README's "Operate it with a coding agent" (ticket 48): its `init --skills` line,
    typed into a Workspace the first Run's `init` made, prints the block it shows; the
    section names the three Harnesses with the file each reads, and the skills layout."""
    text = (REPO / "README.md").read_text(encoding="utf-8")
    start = text.index(README_OPERATE)
    section = text[start : text.index("\n## ", start + 1)]
    root = tmp_path / "agentdiag-demo"
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0
    (_, command), (_, shown) = [(m.group(1), m.group(2)) for m in BLOCK.finditer(section)]
    words = [w.replace(README_ROOT, root.as_posix()) for w in shlex.split(command.strip())]
    assert words[:3] == ["uv", "run", "agentdiag"]

    result = runner.invoke(app, words[3:])

    assert result.exit_code == 0, result.output
    assert result.stdout.replace(root.as_posix(), README_ROOT) == shown
    for harness, reads in (("Codex", "AGENTS.md"), ("Claude Code", "CLAUDE.md")):
        assert f"`{reads}`" in section and harness in section
    assert "`GEMINI.md`" in section and "Gemini CLI" in section
    assert "`.agents/skills/`" in section and "`.claude/skills/`" in section


DESCRIBE_SECTION = "## Describe a Target before it is connected"
DESCRIBE_ROOT = "/tmp/agentdiag-describe"
DESCRIBE_EXITS = {"init": 0, "validate": 0, "run": 3}
"""The documented exit of each command the section types: `run --dry-run` refuses a
pending Adapter (ADR-0016 §4)."""


def test_the_describe_walkthrough_prints_what_the_guide_shows(tmp_path: Path) -> None:
    """Every line of "Describe a Target before it is connected", typed into a temporary
    root: the `uv run agentdiag …` lines through the CLI, each exiting as documented, and
    the `grep` line in a shell; each output block is what the bash block before it printed,
    with the documented root in place of the temporary one."""
    import subprocess

    root = tmp_path / "agentdiag-describe"

    def typed(line: str) -> str:
        if not line.startswith("uv run agentdiag "):
            done = subprocess.run(
                ["bash", "-c", line.replace(DESCRIBE_ROOT, root.as_posix())],
                check=True,
                capture_output=True,
                text=True,
            )
            return done.stdout
        words = [w.replace(DESCRIBE_ROOT, root.as_posix()) for w in shlex.split(line)]
        result = runner.invoke(app, words[3:])
        assert result.exit_code == DESCRIBE_EXITS[words[3]], f"`{line}`: {result.output}"
        return result.output.replace(root.as_posix(), DESCRIBE_ROOT)

    pending: str | None = None
    compared = 0
    for match in BLOCK.finditer(fenced_section(DESCRIBE_SECTION)):
        language, body = match.group(1), match.group(2)
        if language == "bash":
            pending = "".join(typed(line) for line in body.strip().splitlines())
        else:
            assert pending is not None, "an output block before any command"
            assert pending == body
            compared += 1
            pending = None
    assert compared == 4


MIGRATE_SECTION = "## Migrate a Target from another maintenance repository"
MIGRATE_ROOT = "/tmp/agentdiag-migrate"
MIGRATE_EXITS = {"init": 0, "generate": 0, "validate": 0, "registry": 0, "run": 3}
"""The documented exit of each command the section types: `run --dry-run` refuses the
pending Adapter the migration leaves (ADR-0016 §4, §7)."""


def test_the_migrate_walkthrough_prints_what_the_guide_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every line of "Migrate a Target from another maintenance repository", typed from the
    repository root into a temporary root (ticket 49): the `uv run agentdiag …` lines
    through the CLI, each exiting as documented, the `cp`, `mkdir` and `sed` lines in a
    shell. The Manifest edits the prose asks for are `tests/migrate_fixtures.py`'s, made at
    the first `yaml` block; each Manifest block is then a part of the Manifest, the drafts
    block a part of the drafts file, and each output block what the bash block before it
    printed, with the documented root in place of the temporary one."""
    import subprocess

    from tests.migrate_fixtures import SLUG, SUITE, settle_manifest

    monkeypatch.chdir(REPO)
    root = tmp_path / "agentdiag-migrate"
    target = root / ".agentdiag" / "targets" / SLUG

    def typed(line: str) -> str:
        """Run one documented line from the repository root, the documented root replaced
        by the temporary one: a shell line through bash (it prints nothing the section
        shows), an agentdiag line through the CLI, exiting as `MIGRATE_EXITS` says; what it
        printed, with the documented root put back."""
        if not line.startswith("uv run agentdiag "):
            subprocess.run(
                ["bash", "-c", line.replace(MIGRATE_ROOT, root.as_posix())],
                check=True,
                capture_output=True,
                cwd=REPO,
            )
            return ""
        words = [w.replace(MIGRATE_ROOT, root.as_posix()) for w in shlex.split(line)]
        result = runner.invoke(app, words[3:])
        assert result.exit_code == MIGRATE_EXITS[words[3]], f"`{line}`: {result.output}"
        return result.output.replace(root.as_posix(), MIGRATE_ROOT)

    pending: str | None = None
    compared = settled = 0
    for match in BLOCK.finditer(fenced_section(MIGRATE_SECTION)):
        language, body = match.group(1), match.group(2)
        if language == "bash":
            pending = "".join(typed(line) for line in body.strip().splitlines())
        elif language == "yaml":
            if not settled:
                settle_manifest(target / "manifest.yaml")
            settled += 1
            drafts = target / "drafts" / f"{SUITE}.yaml"
            held = drafts.read_text(encoding="utf-8") if drafts.is_file() else ""
            manifest = (target / "manifest.yaml").read_text(encoding="utf-8")
            assert body in manifest or body in held, body
        else:
            assert pending, "an output block with no command printing before it"
            assert pending == body
            compared += 1
            pending = None
    assert (settled, compared) == (4, 6)
    section = fenced_section(MIGRATE_SECTION)
    for stays in ("**fix history**", "ADR-0012", "**Adapter and Connector**", "**credentials**"):
        assert stays in section, stays
