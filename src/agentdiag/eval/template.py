"""The Judge's prompt as a template: every fixed word in it, and slots for the Trial's data.

ADR-0003 §8 and ADR-0005 §3 ask that anything able to move a Verdict without the Target
moving be on record, so a comparison can say "the judgement changed". A Judge prompt is
fixed wording around one Trial's data, and the fixed wording is exactly that kind of thing:
rewording "This Target has no calibration notes." changes every request a Judge is sent.
So the wording lives in one template string per Eval — the text `run.json` records under
`judge.prompts` and the prompt Fingerprint hashes — and a prompt is that template filled
with the Trial's values, never assembled from strings the template does not hold (ticket 05
Standards review; phase-5 interfaces, "the prompt recorded in `run.json`").

The template language is the least that lets optional and repeated parts keep their
wording inside the template:

- `{name}` — a value, inserted as it is;
- `{?name}...{/name}` — kept when `name` has a value (not None, not empty);
- `{!name}...{/name}` — kept when it has none, which is where "(none)" and "has no
  calibration notes" live;
- `{*name}...{/name}` — repeated once per item of the list `name`, each item's own values
  in scope, the repetitions joined and trailing newlines trimmed.

Values are inserted in one pass over the parsed template and never re-read, so a Trace or a
prompt full of braces cannot be mistaken for a slot. A slot the template names and the
values do not supply is a `KeyError`: a prompt with a silently empty hole would be a
different question from the one on record.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

Value = str | Sequence[Mapping[str, "Value"]] | None
"""One slot's value: text, a list of items for a `{*name}` block, or None for nothing."""

Values = Mapping[str, Value]

TOKEN = re.compile(r"\{([?!*/]?)([a-z_]+)\}")
"""`{name}`, `{?name}`, `{!name}`, `{*name}` or `{/name}`: only lower-case identifiers, so a
JSON object or a `{id, rule}` in prose is never read as a slot."""


@dataclass
class _Node:
    kind: str
    """`text`, `slot`, `?`, `!` or `*`."""

    name: str = ""
    text: str = ""
    children: list[_Node] = field(default_factory=list)


class TemplateError(ValueError):
    """A template whose blocks do not close, or close out of order."""


def fill(template: str, values: Values) -> str:
    """The template with every slot filled from `values`, in one pass."""
    return _render(_parse(template), [values])


def slots(template: str) -> set[str]:
    """Every name the template reads, at any depth."""
    return {match.group(2) for match in TOKEN.finditer(template) if match.group(1) != "/"}


def _parse(template: str) -> list[_Node]:
    root = _Node("root")
    stack = [root]
    position = 0
    for match in TOKEN.finditer(template):
        if match.start() > position:
            stack[-1].children.append(_Node("text", text=template[position : match.start()]))
        position = match.end()
        marker, name = match.groups()
        if marker == "":
            stack[-1].children.append(_Node("slot", name=name))
        elif marker == "/":
            if len(stack) == 1 or stack[-1].name != name:
                raise TemplateError(f"{{/{name}}} closes no open block of that name")
            stack.pop()
        else:
            block = _Node(marker, name=name)
            stack[-1].children.append(block)
            stack.append(block)
    if len(stack) != 1:
        raise TemplateError(f"the block {stack[-1].name!r} is never closed")
    if position < len(template):
        root.children.append(_Node("text", text=template[position:]))
    return root.children


def _lookup(scopes: list[Values], name: str) -> Value:
    for scope in reversed(scopes):
        if name in scope:
            return scope[name]
    raise KeyError(f"the prompt template reads {{{name}}} and no value was given for it")


def _present(value: Value) -> bool:
    return value is not None and len(value) > 0


def _render(nodes: list[_Node], scopes: list[Values]) -> str:
    out: list[str] = []
    for node in nodes:
        if node.kind == "text":
            out.append(node.text)
        elif node.kind == "slot":
            value = _lookup(scopes, node.name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{{{node.name}}} is a list; a list is read by {{*{node.name}}}")
            out.append(value or "")
        elif node.kind == "?":
            if _present(_lookup(scopes, node.name)):
                out.append(_render(node.children, scopes))
        elif node.kind == "!":
            if not _present(_lookup(scopes, node.name)):
                out.append(_render(node.children, scopes))
        else:
            items = _lookup(scopes, node.name)
            if isinstance(items, str):
                raise TypeError(f"{{*{node.name}}} repeats over a list; it was given text")
            repeated = "".join(_render(node.children, [*scopes, item]) for item in items or [])
            out.append(repeated.rstrip("\n"))
    return "".join(out)


__all__ = ["TOKEN", "TemplateError", "Value", "Values", "fill", "slots"]
