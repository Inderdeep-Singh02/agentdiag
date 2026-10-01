"""The repository scan behind `agentdiag discover --scan` (phase-6 decision 27).

A coding agent dropped into an unfamiliar repository needs a first Manifest to argue with,
not a blank one. The scan reads the files and proposes one: prompt files and prompt string
literals, tool schemas, the model SDK and the model a call names, the factory and the tools
callable, the deployed-set convention the in-process Connector reads, and data-source
identities. **Every finding is a guess** and carries the reason it was guessed, which the
draft prints as a `# REVIEW:` line above it; the skill has the agent accept or rewrite each.

**The scanned code is read, never run.** Python is parsed with `ast`: importing a module to
find its prompt would run whatever it does at import, in agentdiag's process, for a
repository nobody has vetted yet. A module that does not parse is listed as unreadable and
skipped.

**Heuristics that would hold on any repository.** Nothing here knows the toy Target: a
factory is a module-level `make_*`/`build_*`/`create_*` whose return is a callable, a
tools callable is one whose return is a mapping of callables, and a name defined in one
module and re-exported by its package is referenced by the package, as the package's own
users import it. Directories that hold no Target (`.git`, `.venv`, `node_modules`,
`__pycache__`, `tests/`, `.agentdiag/`) are skipped.

Offline: the standard library only, and `agentdiag.types`.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args
from urllib.parse import urlsplit, urlunsplit

from agentdiag.types import EvidenceKind, ToolKind

SKIPPED_DIRECTORIES = frozenset(
    {".git", ".venv", "venv", "env", "node_modules", "__pycache__", "tests", ".agentdiag"}
    | {"build", "dist", ".tox", "site-packages", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
)
"""Directories the scan never enters: tooling, dependencies, build output, tests, and
agentdiag's own. A directory holding `pyvenv.cfg` is a virtual environment, whatever its
name, and is skipped too."""

NOT_PROMPTS = ("readme", "changelog", "license", "contributing")
"""Markdown a repository holds for its people, never a prompt, whoever references it; and
`AGENTS.md` / `CLAUDE.md`, which are a coding agent's, not the Target's."""

AGENT_FILES = frozenset({"agents.md", "claude.md"})

PACKAGING_FILES = frozenset({"setup.py"})
"""Python files whose string constants name files for packaging, not for the Target."""

PROMPT_DIRECTORY_WORDS = ("prompt", "instruction", "persona")
"""A directory whose name holds one of these holds prompt files."""

PROMPT_SUFFIXES = frozenset({".md", ".txt", ".prompt"})

PROMPT_LITERAL_WORDS = ("PROMPT", "SYSTEM", "INSTRUCTION")
"""A module-level string whose name holds one of these, and is long enough, is a prompt."""

PROMPT_LITERAL_MIN_CHARS = 200

FACTORY_PREFIXES = ("make_", "build_", "create_")

RETRIEVAL_PREFIXES = ("get", "lookup", "search", "find", "list", "read", "fetch")
"""A tool whose name starts with one of these is guessed a `retrieval`; any other an
`action`, the Manifest's default."""

MODEL_SDKS = ("anthropic", "openai")

DEPLOYED_SET_NAMES = frozenset({"deployed_set", "DEPLOYED", "DEPLOYED_SET"})
"""The in-process Connector's deployed-set convention: a module-level callable or mapping
by one of these names."""

ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete", "route", "api_route"})
"""`@app.post("/chat")`, `@router.get(...)`, `@bp.route(...)`: FastAPI's and Flask's."""

DATA_SOURCE_SUFFIXES = ("_URL", "_DSN", "_DB")
"""An environment variable whose name ends in one of these names a data source."""

URL = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://\S+$")

DATABASE_SCHEMES = frozenset(
    {"postgres", "postgresql", "mysql", "mariadb", "sqlite", "mongodb", "redis", "mssql"}
)


Public = Callable[[str, str], str]
"""`(module, attribute)` to the `module:attr` the package's users import it by."""


@dataclass(frozen=True)
class Finding:
    """One guess: the value the draft writes, and why it is only a guess."""

    value: Any
    review: str


@dataclass(frozen=True)
class ToolFinding:
    """One tool the scan found: its guessed kind, a schema file when one holds it alone."""

    kind: ToolKind
    schema: Path | None
    review: str


@dataclass(frozen=True)
class DataSourceFinding:
    identity: str
    kind: str | None
    review: str


@dataclass
class ScanFindings:
    """Everything the scan proposes, each with its reason."""

    directory: Path
    prompts: dict[str, Finding] = field(default_factory=dict)
    """Name to a prompt file's absolute path, or `observed` for a string literal."""

    tools: dict[str, ToolFinding] = field(default_factory=dict)
    factory: Finding | None = None
    """`module:attr` of the Target's factory."""

    tools_reference: Finding | None = None
    """`module:attr` of the callable returning the tools."""

    model: Finding | None = None
    deployed: Finding | None = None
    """`module:attr` following the deployed-set convention."""

    deployed_module: str | None = None
    """The module that defines the deployed set, where its Evidence stores are looked for."""

    evidence: dict[str, Finding] = field(default_factory=dict)
    """Evidence store kind to the `module:attr` of its rows: a mapping of stores by kind in
    the deployed set's module (walkthrough friction 9)."""

    description: Finding | None = None
    sdks: dict[str, list[str]] = field(default_factory=dict)
    """Module to the model SDKs it imports."""

    routes: list[str] = field(default_factory=list)
    """HTTP routes, as `module:function (METHOD /path)`."""

    data_sources: dict[str, DataSourceFinding] = field(default_factory=dict)
    unreadable: list[str] = field(default_factory=list)
    """Python files that did not parse, relative to the directory scanned."""


@dataclass
class _Module:
    """One parsed Python file."""

    name: str
    path: Path
    tree: ast.Module
    is_package: bool
    strings: dict[str, str] = field(default_factory=dict)
    """Module-level string constants, by name."""

    imported: dict[str, tuple[str, str]] = field(default_factory=dict)
    """Local name to (module, attribute) for every `from … import …`."""

    code: list[str] = field(default_factory=list)
    """Every string constant that is code rather than a docstring."""


def scan_repository(directory: Path) -> ScanFindings:
    """Read `directory` and propose a Manifest's contents, every one a guess."""
    directory = Path(directory)
    findings = ScanFindings(directory=directory)
    files = sorted(_files(directory))
    modules = [module for path in files if (module := _parse(path, directory, findings))]
    exports = _exports(modules)

    def public(module: str, attribute: str) -> str:
        return _public_reference(module, attribute, exports)

    _prompt_files(files, directory, modules, findings)
    for module in modules:
        _prompt_literals(module, public, findings)
        _tool_lists(module, public, findings)
        _entry_points(module, public, findings)
        _sdk_usage(module, findings)
        _data_sources(module, findings)
    _tool_files(files, directory, findings)
    _evidence(modules, public, findings)
    _model(modules, public, findings)
    _description(directory, modules, findings)
    return findings


# --- walking and parsing ---


def _files(directory: Path) -> Iterator[Path]:
    for path in directory.iterdir():
        if path.is_dir():
            skipped = path.name in SKIPPED_DIRECTORIES or (path / "pyvenv.cfg").is_file()
            if not skipped and not path.is_symlink():
                yield from _files(path)
        elif path.is_file():
            yield path


def _module_name(path: Path) -> tuple[str, bool]:
    """The dotted name a file is imported by: its directory's packages, walked up while
    each holds an `__init__.py`."""
    is_package = path.name == "__init__.py"
    parts = [] if is_package else [path.stem]
    parent = path.parent
    while (parent / "__init__.py").is_file():
        parts.insert(0, parent.name)
        parent = parent.parent
    return ".".join(parts) or path.stem, is_package


def _parse(path: Path, directory: Path, findings: ScanFindings) -> _Module | None:
    if path.suffix != ".py":
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, ValueError):
        findings.unreadable.append(path.relative_to(directory).as_posix())
        return None
    name, is_package = _module_name(path)
    module = _Module(
        name=name, path=path, tree=tree, is_package=is_package, code=list(_code_strings(tree))
    )
    for statement in tree.body:
        for target, value in _assignments(statement):
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                module.strings[target] = value.value
        if isinstance(statement, ast.ImportFrom):
            source = _absolute(module, statement)
            for alias in statement.names:
                module.imported[alias.asname or alias.name] = (source, alias.name)
    return module


def _assignments(statement: ast.stmt) -> Iterator[tuple[str, ast.expr]]:
    """`(name, value)` for a module-level `NAME = …` or `NAME: T = …`."""
    if isinstance(statement, ast.Assign):
        for target in statement.targets:
            if isinstance(target, ast.Name):
                yield target.id, statement.value
    elif (
        isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
        and statement.value is not None
    ):
        yield statement.target.id, statement.value


def _absolute(module: _Module, statement: ast.ImportFrom) -> str:
    """The module a `from … import` reads, relative imports resolved."""
    if not statement.level:
        return statement.module or ""
    package = module.name if module.is_package else module.name.rpartition(".")[0]
    parts = package.split(".") if package else []
    base = parts[: len(parts) - (statement.level - 1)] if statement.level > 1 else parts
    return ".".join([*base, *([statement.module] if statement.module else [])])


def _exports(modules: list[_Module]) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """(module, attribute) to the packages that re-export it, as (package, name)."""
    exports: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for module in modules:
        if not module.is_package:
            continue
        for local, source in module.imported.items():
            exports.setdefault(source, []).append((module.name, local))
    return exports


def _public_reference(
    module: str, attribute: str, exports: dict[tuple[str, str], list[tuple[str, str]]]
) -> str:
    """`module:attr` as the shortest package path that re-exports it, else where it is
    defined: how the package's own users import it."""
    best = (module, attribute)
    seen = {best}
    pending = [best]
    while pending:
        current = pending.pop()
        for exporter in exports.get(current, []):
            if exporter in seen:
                continue
            seen.add(exporter)
            pending.append(exporter)
            if exporter[0].count(".") < best[0].count("."):
                best = exporter
    return f"{best[0]}:{best[1]}"


def _code_strings(tree: ast.Module) -> Iterator[str]:
    """Every string constant that is code rather than a docstring."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node.value


# --- prompts ---


def _name(text: str) -> str:
    """A Manifest key: lowercase words joined by `_`."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "_".join(words) or "unnamed"


def _unique(name: str, taken: dict[str, Any]) -> str:
    candidate, counter = name, 2
    while candidate in taken:
        candidate, counter = f"{name}_{counter}", counter + 1
    return candidate


def _prompt_files(
    files: list[Path],
    directory: Path,
    modules: list[_Module],
    findings: ScanFindings,
) -> None:
    for path in files:
        if path.suffix not in PROMPT_SUFFIXES or _for_people(path):
            continue
        relative = path.relative_to(directory)
        folders = [part.lower() for part in relative.parts[:-1]]
        holder = next(
            (part for part in folders if any(word in part for word in PROMPT_DIRECTORY_WORDS)),
            None,
        )
        if holder is not None:
            review = (
                f"{relative.as_posix()} is under a {holder}/ directory; confirm it is a prompt "
                "the Target sends, and that the file is the truth (else `observed`)"
            )
        elif path.suffix == ".md" and _first_line(path).startswith("#"):
            referrer = next(
                (
                    module
                    for module in modules
                    if module.path.name not in PACKAGING_FILES
                    and any(path.name in text for text in module.code)
                ),
                None,
            )
            if referrer is None:
                continue
            review = (
                f"{relative.as_posix()} opens with a heading and "
                f"{referrer.path.relative_to(directory).as_posix()} names it; confirm the "
                "Target sends it as a prompt"
            )
        else:
            continue
        name = _unique(_name(path.stem), findings.prompts)
        findings.prompts[name] = Finding(value=path, review=review)


def _for_people(path: Path) -> bool:
    """A README, CHANGELOG, LICENSE, CONTRIBUTING, AGENTS.md or CLAUDE.md."""
    name = path.name.lower()
    return name.startswith(NOT_PROMPTS) or name in AGENT_FILES


def _first_line(path: Path) -> str:
    try:
        with path.open(encoding="utf-8") as handle:
            return handle.readline().strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _prompt_literals(module: _Module, public: Public, findings: ScanFindings) -> None:
    for attribute, text in module.strings.items():
        upper = attribute.upper()
        if len(text) < PROMPT_LITERAL_MIN_CHARS or not any(
            word in upper for word in PROMPT_LITERAL_WORDS
        ):
            continue
        name = "system" if "SYSTEM" in upper else _literal_name(upper)
        name = _unique(name, findings.prompts)
        findings.prompts[name] = Finding(
            value="observed",
            review=(
                f"{public(module.name, attribute)} is a {len(text):,}-character string in "
                "code, so `observed`: the Connector or the Adapter reads it; a path instead "
                "if a file is the truth"
            ),
        )


def _literal_name(upper: str) -> str:
    """`GREETING_PROMPT` → `greeting`: the name without the words that made it a prompt."""
    marks = {"PROMPT", "PROMPTS", "INSTRUCTION", "INSTRUCTIONS"}
    words = [word for word in upper.split("_") if word and word not in marks]
    return _name("_".join(words)) if words else "prompt"


# --- tools ---


def tool_kind(name: str) -> tuple[ToolKind, str]:
    """The guessed kind of a tool by its name, and the reason."""
    prefix = next((prefix for prefix in RETRIEVAL_PREFIXES if name.startswith(prefix)), None)
    if prefix is not None:
        return "retrieval", f'kind retrieval because the name starts with "{prefix}"'
    return "action", "kind action because the name starts with no lookup verb"


def _add_tool(findings: ScanFindings, name: str, schema: Path | None, where: str) -> None:
    if name in findings.tools:
        return
    kind, why = tool_kind(name)
    findings.tools[name] = ToolFinding(kind=kind, schema=schema, review=f"{why}; found in {where}")


def _tool_lists(module: _Module, public: Public, findings: ScanFindings) -> None:
    for statement in module.tree.body:
        for attribute, value in _assignments(statement):
            if isinstance(value, ast.Dict):
                _tool_dict(value, public(module.name, attribute), findings)
                continue
            if not isinstance(value, ast.List | ast.Tuple) or not value.elts:
                continue
            names = [_tool_entry_name(element) for element in value.elts]
            if not all(names):
                continue
            where = public(module.name, attribute)
            for name in names:
                assert name is not None
                _add_tool(findings, name, None, f"{where} (schema observed)")


def _tool_dict(value: ast.Dict, where: str, findings: ScanFindings) -> None:
    """A module-level mapping of tool name to schema (walkthrough friction 4): string keys,
    every value a dict literal holding `input_schema` or `parameters`."""
    names = _string_keys(value)
    if names is None or not all(_is_tool_entry(item) for item in value.values):
        return
    for name in names:
        _add_tool(findings, name, None, f"{where} (schema observed)")


def _string_keys(value: ast.Dict) -> list[str] | None:
    """The keys of a dict literal when every one is a string literal, else None."""
    keys = [
        key.value
        for key in value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    ]
    return keys if keys and len(keys) == len(value.keys) else None


def _is_tool_entry(node: ast.expr) -> bool:
    if not isinstance(node, ast.Dict):
        return False
    keys = {key.value for key in node.keys if isinstance(key, ast.Constant)}
    return "input_schema" in keys or "parameters" in keys


def _tool_entry_name(node: ast.expr) -> str | None:
    """The `name` of a dict literal holding `name` and `input_schema` (or `parameters`)."""
    if not isinstance(node, ast.Dict):
        return None
    keys = {
        key.value: value
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    name = keys.get("name")
    if not ("input_schema" in keys or "parameters" in keys):
        return None
    if isinstance(name, ast.Constant) and isinstance(name.value, str):
        return name.value
    return None


def _tool_files(files: list[Path], directory: Path, findings: ScanFindings) -> None:
    """Tools in JSON files: a tool object, a list of them, or `{"tools": [...]}`. Anything
    else a JSON file holds (a scalar, null, a list of numbers) is not a tool file."""
    for path in files:
        if path.suffix != ".json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            listed = data.get("tools")
            candidates = listed if isinstance(listed, list) else [data]
            alone = not isinstance(listed, list)
        elif isinstance(data, list):
            candidates, alone = data, False
        else:
            continue
        tools = [name for entry in candidates if (name := _tool_json_name(entry, path))]
        relative = path.relative_to(directory).as_posix()
        for name in tools:
            _add_tool(findings, name, path if alone and len(tools) == 1 else None, relative)


def _tool_json_name(entry: Any, path: Path) -> str | None:
    if not isinstance(entry, dict):
        return None
    function = entry.get("function")
    inner: dict[str, Any] = function if isinstance(function, dict) else entry
    parameters = inner.get("parameters")
    is_tool = "input_schema" in inner or (
        isinstance(parameters, dict) and parameters.get("type") == "object"
    )
    if not is_tool:
        return None
    name = inner.get("name")
    return name if isinstance(name, str) and name else path.stem


# --- entry points ---


def _entry_points(module: _Module, public: Public, findings: ScanFindings) -> None:
    for statement in module.tree.body:
        if isinstance(statement, ast.ClassDef):
            for method in statement.body:
                if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef) and _tools_named(
                    method.name
                ):
                    where = f"{public(module.name, statement.name)}.{method.name}()"
                    for name in _literal_keys(method):
                        _add_tool(findings, name, None, f"{where} (schema observed)")
            continue
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            _route(module, statement, findings)
            if statement.name in DEPLOYED_SET_NAMES and findings.deployed is None:
                findings.deployed = _deployed(public(module.name, statement.name))
                findings.deployed_module = module.name
            if not statement.name.startswith(FACTORY_PREFIXES):
                continue
            returns = _returns(statement)
            reference = public(module.name, statement.name)
            if returns == "callable" and findings.factory is None:
                findings.factory = Finding(
                    value=reference,
                    review=(
                        f"{reference} is a module-level {statement.name.split('_')[0]}_* "
                        "returning a callable; confirm it takes (client, tools, **options)"
                    ),
                )
            elif returns == "tools" and findings.tools_reference is None:
                findings.tools_reference = Finding(
                    value=reference,
                    review=(
                        f"{reference} returns a mapping of callables; confirm they are the tools"
                    ),
                )
                for name in _returned_keys(statement) or _literal_keys(statement):
                    _add_tool(findings, name, None, f"{reference} (schema observed)")
            if _tools_named(statement.name) and returns != "tools":
                for name in _literal_keys(statement):
                    _add_tool(findings, name, None, f"{reference} (schema observed)")
        else:
            for attribute, _ in _assignments(statement):
                if attribute in DEPLOYED_SET_NAMES and findings.deployed is None:
                    # A record is named in the module that defines it, its home, whatever
                    # package re-exports it (second walk friction 4); a callable keeps the
                    # package path its users import it by.
                    findings.deployed = _deployed(f"{module.name}:{attribute}")
                    findings.deployed_module = module.name


EVIDENCE_KINDS: tuple[str, ...] = get_args(EvidenceKind)
"""The Evidence store kinds a mapping's keys must all be to be one of stores."""


def _evidence(modules: list[_Module], public: Public, findings: ScanFindings) -> None:
    """A module-level mapping whose name holds `EVIDENCE` and whose keys are all Evidence
    store kinds, in the module defining the deployed set: each key a store whose rows it
    holds, as the in-process Connector reads them (`rows: module:attr`)."""
    for module in modules:
        if module.name != findings.deployed_module:
            continue
        for statement in module.tree.body:
            for attribute, value in _assignments(statement):
                if "EVIDENCE" not in attribute.upper() or not isinstance(value, ast.Dict):
                    continue
                kinds = _string_keys(value)
                if not kinds or not all(kind in EVIDENCE_KINDS for kind in kinds):
                    continue
                reference = f"{module.name}:{attribute}"  # a record: named at its home
                for kind in kinds:
                    findings.evidence.setdefault(
                        kind,
                        Finding(
                            reference,
                            f"{reference} holds {kind} rows beside the deployed set; the "
                            "in-process Connector reads them for `agentdiag import`",
                        ),
                    )


def _deployed(reference: str) -> Finding:
    return Finding(
        value=reference,
        review=(
            f"{reference} follows the in-process Connector's deployed-set convention; "
            "confirm it returns the prompts, tools and model the Target runs"
        ),
    )


def _own_returns(function: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[ast.expr]:
    """What `function` itself returns: its `return` values, nested functions' excluded."""
    pending: list[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            continue
        if isinstance(node, ast.Return) and node.value is not None:
            yield node.value
        pending.extend(ast.iter_child_nodes(node))


def _nested(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    return {
        node.name
        for node in function.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _callables_mapping(value: ast.expr, nested: set[str]) -> bool:
    return (
        isinstance(value, ast.Dict)
        and bool(value.values)
        and all(isinstance(item, ast.Name) and item.id in nested for item in value.values)
    )


Returned = Literal["callable", "tools"]
"""What a `make_*` returns: a callable (the factory) or a mapping of callables (the tools)."""


def _returns(function: ast.FunctionDef | ast.AsyncFunctionDef) -> Returned | None:
    """`callable`, `tools` (a mapping of callables) or None, from the annotation or the
    returned expressions."""
    annotation = function.returns
    if annotation is not None:
        text = ast.unparse(annotation)
        head = text.split("[", 1)[0].rsplit(".", 1)[-1]
        if head == "Callable":
            return "callable"
        if head in {"dict", "Dict", "Mapping", "MutableMapping"} and "Callable" in text:
            return "tools"
    nested = _nested(function)
    for value in _own_returns(function):
        if isinstance(value, ast.Lambda) or (isinstance(value, ast.Name) and value.id in nested):
            return "callable"
        if _callables_mapping(value, nested):
            return "tools"
    return None


def _returned_keys(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """The tool names of a returned mapping of nested callables, in order."""
    nested = _nested(function)
    keys: list[str] = []
    for value in _own_returns(function):
        if isinstance(value, ast.Dict) and _callables_mapping(value, nested):
            keys += [
                key.value
                for key in value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            ]
    return keys


def _tools_named(name: str) -> bool:
    """A function or method whose name says it returns the tools: `tools`, `make_tools`,
    `build_tools`, `get_tools` (walkthrough friction 4)."""
    return name == "tools" or name.endswith("_tools")


def _literal_keys(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """The string-literal keys of a dict literal the function itself returns, whatever its
    values are (bound methods, callables imported from elsewhere)."""
    keys: list[str] = []
    for value in _own_returns(function):
        if isinstance(value, ast.Dict) and (found := _string_keys(value)) is not None:
            keys += [key for key in found if key not in keys]
    return keys


def _route(
    module: _Module, function: ast.FunctionDef | ast.AsyncFunctionDef, findings: ScanFindings
) -> None:
    for decorator in function.decorator_list:
        if not (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr in ROUTE_METHODS
            and decorator.args
            and isinstance(decorator.args[0], ast.Constant)
            and isinstance(decorator.args[0].value, str)
            and decorator.args[0].value.startswith("/")
        ):
            continue
        method = decorator.func.attr.upper()
        if method in {"ROUTE", "API_ROUTE"}:
            method = "ANY"
        findings.routes.append(
            f"{module.name}:{function.name} ({method} {decorator.args[0].value})"
        )


# --- the model and its SDK ---


def _sdk_usage(module: _Module, findings: ScanFindings) -> None:
    used: list[str] = []
    for node in ast.walk(module.tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names = [node.module]
        for name in names:
            root = name.split(".", 1)[0]
            if root in MODEL_SDKS and root not in used:
                used.append(root)
    if used:
        findings.sdks[module.name] = used


def _model(modules: list[_Module], public: Public, findings: ScanFindings) -> None:
    """The first model a call's `model=` or a parameter named `model` defaults to, as a
    string literal or a module-level string constant; the others named in the review."""
    by_name = {module.name: module for module in modules}
    seen: list[tuple[str, str]] = []
    for module in modules:
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "model":
                        _model_value(
                            module, keyword.value, by_name, f"a model= in {module.name}", seen
                        )
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                arguments = node.args
                positional = [*arguments.posonlyargs, *arguments.args]
                pairs = list(
                    zip(
                        positional[len(positional) - len(arguments.defaults) :],
                        arguments.defaults,
                        strict=True,
                    )
                ) + [
                    (argument, default)
                    for argument, default in zip(
                        arguments.kwonlyargs, arguments.kw_defaults, strict=True
                    )
                    if default is not None
                ]
                for argument, default in pairs:
                    if argument.arg == "model":
                        where = (
                            f"the model= default of {public(module.name, node.name)}"
                            if node in module.tree.body
                            else f"a model= default in {module.name}"
                        )
                        _model_value(module, default, by_name, where, seen)
    if not seen:
        return
    value, where = seen[0]
    others = sorted({other for other, _ in seen[1:] if other != value})
    also = f"; also seen: {', '.join(others)}" if others else ""
    findings.model = Finding(value=value, review=f'"{value}" is {where}{also}')


def _model_value(
    module: _Module,
    node: ast.expr,
    by_name: dict[str, _Module],
    where: str,
    seen: list[tuple[str, str]],
) -> None:
    value: str | None = None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = node.value
    elif isinstance(node, ast.Name):
        value = module.strings.get(node.id)
        if value is None and node.id in module.imported:
            source, attribute = module.imported[node.id]
            other = by_name.get(source)
            value = other.strings.get(attribute) if other is not None else None
    if value and all(value != known for known, _ in seen):
        seen.append((value, where))


# --- data sources ---


def _data_sources(module: _Module, findings: ScanFindings) -> None:
    assigned = {
        id(value): name for statement in module.tree.body for name, value in _assignments(statement)
    }
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and URL.match(node.value):
            _url_source(node.value, assigned.get(id(node)), module, findings)
        variable = _environment_name(node)
        if variable is not None and variable.upper().endswith(DATA_SOURCE_SUFFIXES):
            base = variable.upper()
            for suffix in DATA_SOURCE_SUFFIXES:
                base = base.removesuffix(suffix)
            name = _unique(_name(base), findings.data_sources)
            findings.data_sources[name] = DataSourceFinding(
                identity=f"env:{variable}",
                kind="database" if variable.upper().endswith(("_DSN", "_DB")) else None,
                review=(
                    f"{module.name} reads ${variable}; write the identity it holds (without a "
                    "password), or keep the variable's name"
                ),
            )


def _url_source(url: str, variable: str | None, module: _Module, findings: ScanFindings) -> None:
    parts = urlsplit(url)
    scheme = parts.scheme.lower().split("+", 1)[0]
    kind = (
        "database"
        if scheme in DATABASE_SCHEMES
        else "http"
        if scheme in {"http", "https"}
        else scheme
    )
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port is not None else host
    identity = urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    review = (
        f"{module.name} holds this {kind} URL, written without its user, password and query "
        "(a Manifest holds no secret); check its path for an embedded token too"
    )
    if variable is not None:
        base = variable.upper()
        for suffix in (*DATA_SOURCE_SUFFIXES, "_URI"):
            base = base.removesuffix(suffix)
        name = _name(base)
    elif scheme == "sqlite":
        name = _name(Path(parts.path).stem)
    else:
        name = _name((parts.hostname or scheme).split(".", 1)[0])
    if any(source.identity == identity for source in findings.data_sources.values()):
        return
    findings.data_sources[_unique(name, findings.data_sources)] = DataSourceFinding(
        identity=identity, kind=kind, review=review
    )


def _environment_name(node: ast.AST) -> str | None:
    """The variable `os.environ["X"]`, `os.environ.get("X")`, `os.getenv("X")` reads."""
    if isinstance(node, ast.Subscript) and _is_environ(node.value):
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return key.value
    if isinstance(node, ast.Call) and node.args:
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            return None
        function = node.func
        if (
            isinstance(function, ast.Attribute)
            and function.attr == "get"
            and _is_environ(function.value)
        ):
            return first.value
        if (isinstance(function, ast.Attribute) and function.attr == "getenv") or (
            isinstance(function, ast.Name) and function.id == "getenv"
        ):
            return first.value
    return None


def _is_environ(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ") or (
        isinstance(node, ast.Name) and node.id == "environ"
    )


# --- who the Target is ---


def _description(directory: Path, modules: list[_Module], findings: ScanFindings) -> None:
    """The first line of the scanned package's docstring, else the factory module's."""
    ordered = sorted(
        modules, key=lambda module: module.path.parent != directory or not module.is_package
    )
    for module in ordered:
        docstring = ast.get_docstring(module.tree)
        if docstring:
            line = docstring.strip().splitlines()[0].strip()
            findings.description = Finding(
                value=line,
                review=f"the first line of {module.name}'s docstring; say what this Target does",
            )
            return


__all__ = [
    "RETRIEVAL_PREFIXES",
    "SKIPPED_DIRECTORIES",
    "DataSourceFinding",
    "Finding",
    "ScanFindings",
    "ToolFinding",
    "scan_repository",
    "tool_kind",
]
