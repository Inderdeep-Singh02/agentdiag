"""`module:attr` references: how a Manifest names Python it does not hold.

The Adapter's `factory` and `tools`, and the in-process Connector's `deployed` and Evidence
`rows`, are all written as `module:attr` and resolved by this one function. It lives here,
offline, rather than in the in-process Adapter's module, because that module imports the
Anthropic SDK and the Connector must not: `sync` reads a deployed set without a model
client in the process (phase-6 decisions 18 and 22). `agentdiag.adapter.import_attribute`
is this function, re-exported.

`reference_problem` is the offline half: what `validate` asks of a reference without
importing it (walkthrough friction 5).
"""

from __future__ import annotations

import ast
import importlib
import importlib.machinery
import sys
from collections.abc import Sequence
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any


def import_attribute(reference: str) -> Any:
    """Resolve a `module:attr` reference from the Manifest."""
    if ":" not in reference:
        raise ValueError(f"{reference!r} is not a 'module:attr' reference")
    module_name, _, attribute = reference.partition(":")
    module = importlib.import_module(module_name)
    try:
        return getattr(module, attribute)
    except AttributeError as exc:
        raise ImportError(f"{module_name} has no attribute {attribute!r}") from exc


# --- finding a reference without importing it (ticket 13's validate, friction 5) ---

UNCHECKED = ModuleSpec("<unchecked>", None)
"""A module a finder vouches for whose submodules cannot be looked for on disk: accepted."""


def reference_problem(reference: Any) -> str | None:
    """Why `reference` names nothing, or None: not `module:attr`; a module no import finder
    can find; or a `.py` module that binds no such name at its top level. Nothing is imported
    and no code runs: finders are asked by name, and a source file is parsed with `ast`."""
    if not isinstance(reference, str) or ":" not in reference:
        return f"{reference!r} is not a `module:attr` reference"
    module, _, attribute = reference.partition(":")
    if not module or not attribute:
        return f"{reference!r} is not a `module:attr` reference"
    spec = find_module_spec(module)
    if spec is None:
        return f"no module {module} can be found for {reference}"
    origin = spec.origin
    if spec is UNCHECKED or not origin or not origin.endswith(".py"):
        return None
    names = top_level_names(Path(origin))
    if names is None or attribute in names or _is_submodule(spec, attribute):
        return None
    return f"{module} binds no {attribute!r} at its top level, so {reference} names nothing"


def find_module_spec(module: str) -> ModuleSpec | None:
    """The spec the import system would use for `module`, found without importing it: the
    top-level name asked of every `sys.meta_path` finder (an editable install's own finder
    included), each submodule looked for in its package's search locations. None when no
    finder can find it; `UNCHECKED` when a package has no search locations to look in."""
    parts = module.split(".")
    spec = _loaded_spec(parts[0]) or _meta_path_spec(parts[0])
    for end in range(2, len(parts) + 1):
        if spec is None or spec is UNCHECKED:
            return spec
        name = ".".join(parts[:end])
        loaded = _loaded_spec(name)
        if loaded is not None:
            spec = loaded
            continue
        locations = list(spec.submodule_search_locations or [])
        if not locations:
            return UNCHECKED
        spec = importlib.machinery.PathFinder.find_spec(name, locations)
    return spec


def _loaded_spec(name: str) -> ModuleSpec | None:
    loaded = sys.modules.get(name)
    if loaded is None:
        return None
    spec = getattr(loaded, "__spec__", None)
    return spec if isinstance(spec, ModuleSpec) else UNCHECKED


def _meta_path_spec(name: str) -> ModuleSpec | None:
    for finder in sys.meta_path:
        find_spec = getattr(finder, "find_spec", None)
        if find_spec is None:
            continue
        try:
            spec = find_spec(name, None)
        except (ImportError, ValueError, AttributeError):
            continue
        if spec is not None:
            return spec if isinstance(spec, ModuleSpec) else UNCHECKED
    return None


def top_level_names(path: Path) -> set[str] | None:
    """Every name a Python source file binds at its top level (a def, a class, an
    assignment, an import, an `__all__` entry), inside top-level `if`/`try`/`with` blocks
    too; None when that cannot be told statically (it does not parse, it star-imports, or it
    defines a module `__getattr__`)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return None
    names: set[str] = set()
    pending: list[ast.stmt] = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if node.name == "__getattr__":
                return None
            names.add(node.name)
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                names |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
            if isinstance(node, ast.Assign | ast.AnnAssign) and _is_all(targets):
                names |= _listed(node.value)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    return None
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.If | ast.Try | ast.With):
            for field in ("body", "orelse", "finalbody", "handlers"):
                for child in getattr(node, field, []):
                    pending.extend(child.body if isinstance(child, ast.ExceptHandler) else [child])
    return names


def _is_all(targets: Sequence[ast.expr]) -> bool:
    return any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets)


def _listed(value: ast.expr | None) -> set[str]:
    if not isinstance(value, ast.List | ast.Tuple):
        return set()
    return {e.value for e in value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}


def _is_submodule(spec: ModuleSpec, attribute: str) -> bool:
    """`package:submodule`: a name a package's directory holds as a module."""
    for location in spec.submodule_search_locations or []:
        folder = Path(location)
        if (folder / f"{attribute}.py").is_file() or (folder / attribute).is_dir():
            return True
    return False


__all__ = [
    "UNCHECKED",
    "find_module_spec",
    "import_attribute",
    "reference_problem",
    "top_level_names",
]
