"""One HTTP Target environment, validated and resolved: where it is, how it is framed, who
the user is, and the credentials it needs (phase-8 decisions 2 and 6, ADR-0011 §2).

`adapter.environments.<name>` for `kind: http` names the Target's shipped chat endpoint by
identifiers — `base_url` (scheme and host), an optional `path`, any other key an identifier
the Dialect may interpolate (`agent_id`, `org_id`) — and names its credentials by the
environment variable that holds each, never by value. Three rules follow ADR-0011 §2:

- **Credentials fail closed.** `resolve_http_environment` reads them through
  `connector.environment.read_credentials`, the function the Connector reads its own
  through: a variable unset or empty is `CredentialMissing` naming it, never another
  environment's, never a default. The values sit in a field excluded from every dump and
  every `repr`, and `ResolvedHttpEnvironment.scrub` takes them out of any text an error
  carries.
- **Only identifiers interpolate.** `headers` and `path` may say `{org_id}`; a name that is
  not one of the block's identifiers — a credential role, say — is a `validate` error, so a
  token can only reach a request the way the Dialect puts it there.
- **A Fixture reaches an HTTP Target only through an identity mode** (ADR-0001 §6,
  decision 6). The environment's `identity` block names the mode; the Scenario declares
  only the Fixture. Core has three (`header`, `body`, `first_message`); a Dialect may offer
  more through its optional `identity_modes`, consulted after these (a plugin's identity
  modes).

`http_problems` is what `validate` checks of the block (`HttpAdapter.validate_section`,
called by `run.manifest_checks` without loading the Adapter's module): `base_url` with a
scheme and a host and no path or query, a `dialect` a distribution registers, `headers` and
`path` interpolating only identifiers, `credentials` mapping roles to names, an `identity`
mode core knows (for a core Dialect) with the fields it needs, `path` starting with `/`.

Offline: pydantic, the standard library and the Manifest model.
"""

from __future__ import annotations

import os
import string
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentdiag.connector.environment import CREDENTIALS_KEY, read_credentials, scrub_values
from agentdiag.connector.plugins import (
    CORE_DIALECTS,
    DIALECT_GROUP,
    UnknownKind,
    check_registered,
)
from agentdiag.run.manifest import (
    DEFAULT_ENVIRONMENT_KEY,
    AdapterSection,
    higher_side_effects,
    protected_by_name,
)
from agentdiag.scenario.models import Fixture
from agentdiag.types import SideEffectClass

IDENTITY_KEY = "identity"
"""The key of an environment block naming how a Fixture of kind `identity` is applied."""

IDENTITY_FIXTURE_KIND = "identity"
"""The one Fixture kind an HTTP Target takes (decision 6)."""

CREDENTIAL_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization", "x-api-key"})
"""Header names that carry a credential: a static `headers` entry may not name one, since a
value written there would be a secret in a committed Manifest (decision 2)."""

Problem = tuple[str, str]
"""(where, message), as `ManifestProblem` takes them."""


class HttpEnvironmentError(ValueError):
    """An HTTP environment block the Adapter cannot use: not declared, or malformed."""


TOOL_TRUTH_KEY = "tool_truth"
"""The Adapter-level key naming where tool truth comes from (decision 8)."""


class ToolTruth(BaseModel):
    """`adapter.tool_truth`: after each Turn, read this Evidence store through the Target's
    Connector and reconstruct the Turn's tool Spans from it (decision 8, ADR-0013 §5)."""

    model_config = ConfigDict(extra="forbid")

    evidence: Literal["proxy"]
    """The one store core reconstructs from in a live Trial: proxy rows."""

    wait_s: float = Field(default=0, ge=0)
    """How long to keep reading for a row of the Turn; 0 reads once."""

    poll_s: float = Field(default=2, gt=0)
    """How long to wait between reads while `wait_s` lasts."""


def tool_truth_of(section: AdapterSection) -> ToolTruth | None:
    """The Adapter block's `tool_truth`, read; None when it names none. `HttpEnvironmentError`
    when it is malformed."""
    raw = (section.model_extra or {}).get(TOOL_TRUTH_KEY)
    if raw is None:
        return None
    try:
        return ToolTruth.model_validate(raw)
    except ValidationError as exc:
        wrong = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'tool_truth'}: {error['msg']}"
            for error in exc.errors()
        )
        raise HttpEnvironmentError(f"adapter.tool_truth: {wrong}") from None


class HttpEnvironment(BaseModel):
    """One `adapter.environments.<name>` block for `kind: http`, as written (decision 2).

    Every key not named here is an identifier (`agent_id`, `org_id`), kept in
    `model_extra`, which `headers` and `path` may interpolate and a Dialect may read."""

    model_config = ConfigDict(extra="allow")

    base_url: str
    """Scheme and host only: `https://api-dev.example.test`. An identifier, not a secret."""

    dialect: str
    """A registered Dialect's name (`json`, `sse-json`, or a plugin's)."""

    path: str | None = None
    """The path each Turn is posted to, identifiers interpolated; the Dialect's default when
    absent."""

    headers: dict[str, str] = Field(default_factory=dict)
    """Static request headers; `{name}` interpolates identifiers, never credentials."""

    credentials: dict[str, str] = Field(default_factory=dict)
    """Role to the NAME of an environment variable (`{token: ACME_AUTH_TOKEN}`)."""

    identity: dict[str, Any] | None = None
    """How a Fixture of kind `identity` is applied (decision 6); None: no Fixture can be."""

    turn_timeout: float | None = None
    """Declared here, though the Manifest reads it (`AdapterSection.turn_timeout_s`), so it is
    never taken for an identifier a Dialect may interpolate."""

    side_effects: SideEffectClass | None = None
    """The environment's own class, one of the closed set; declared so `validate` names a
    bad one and it is never taken for an identifier."""

    protected: bool | None = None
    """Read by the Manifest (`is_protected`); declared so it is never taken for an
    identifier."""

    @property
    def identifiers(self) -> dict[str, Any]:
        """Every key of the block that is not one of the Adapter's own."""
        return dict(self.model_extra or {})


class ResolvedHttpEnvironment(BaseModel):
    """One HTTP environment ready to converse with: its identifiers interpolated, its
    credentials read from the process, its classes decided. Never dumped with a value."""

    name: str
    base_url: str
    dialect: str
    path: str | None = None
    """As written, `{identifier}`s not yet interpolated; `interpolate` does it."""

    headers: dict[str, str] = Field(default_factory=dict)
    """The static headers, identifiers interpolated."""

    identifiers: dict[str, Any] = Field(default_factory=dict)
    identity: dict[str, Any] | None = None

    credentials: dict[str, str] = Field(default_factory=dict, exclude=True, repr=False)
    """Role to value, read from the process environment; never dumped, never printed."""

    credential_names: dict[str, str] = Field(default_factory=dict)
    """Role to the variable's name, as the Manifest wrote it: what `scrub` replaces with."""

    side_effects: SideEffectClass = "none"
    protected: bool = False

    def interpolate(self, template: str) -> str:
        """`template` with every `{identifier}` replaced by the identifier's value."""
        return interpolated(template, self.identifiers)

    def scrub(self, text: str) -> str:
        """`text` with every credential value replaced by `$<VAR>`: what an error says may
        reach a Trace, a file or a terminal, and a value never may."""
        for role, value in self.credentials.items():
            if value:
                text = text.replace(value, f"${self.credential_names.get(role, role)}")
        return text


def template_names(template: str) -> list[str]:
    """The `{name}` fields of a format template, in order; `ValueError` when it is not one."""
    return [
        field.split(".")[0].split("[")[0]
        for _, field, _, _ in string.Formatter().parse(template)
        if field is not None and field != ""
    ]


def interpolated(template: str, values: Mapping[str, Any]) -> str:
    """`template` with each `{name}` replaced by `str(values[name])`; `KeyError` naming a
    field `values` has not."""
    return template.format_map({name: str(value) for name, value in values.items()})


def environment_block(section: AdapterSection, name: str) -> HttpEnvironment:
    """`adapter.environments.<name>` read as an HTTP environment; `HttpEnvironmentError`
    when the block does not declare it or it is not one."""
    if name == DEFAULT_ENVIRONMENT_KEY or name not in section.environments:
        present = ", ".join(section.environment_names) or "none"
        raise HttpEnvironmentError(
            f"the Manifest's adapter block names no environment {name!r}; it names {present}"
        )
    block = section.environments[name]
    if not isinstance(block, Mapping):
        raise HttpEnvironmentError(f"adapter.environments.{name} is not a mapping")
    try:
        return HttpEnvironment.model_validate(dict(block))
    except ValidationError as exc:
        wrong = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise HttpEnvironmentError(f"adapter.environments.{name}: {wrong}") from None


def resolve_http_environment(
    section: AdapterSection, name: str, environ: Mapping[str, str] = os.environ
) -> ResolvedHttpEnvironment:
    """Resolve HTTP environment `name`, reading only its own credentials (decision 2).

    `HttpEnvironmentError` when the block is undeclared or malformed, or its headers name
    a field that is not an identifier; `ConnectorError` when `credentials` is not a mapping
    of role to variable; `CredentialMissing` naming the first variable unset or empty.
    """
    block = environment_block(section, name)
    credentials = read_credentials(block.credentials, name, environ, where="adapter")
    identifiers = block.identifiers
    try:
        headers = {key: interpolated(value, identifiers) for key, value in block.headers.items()}
    except (KeyError, ValueError, IndexError) as exc:
        raise HttpEnvironmentError(
            f"adapter.environments.{name}.headers interpolates {exc}, which is not one of "
            "the block's identifiers"
        ) from None
    return ResolvedHttpEnvironment(
        name=name,
        base_url=block.base_url.rstrip("/"),
        dialect=block.dialect,
        path=block.path,
        headers=headers,
        identifiers=identifiers,
        identity=block.identity,
        credentials=credentials,
        credential_names=dict(block.credentials),
        side_effects=higher_side_effects(section.side_effects, block.side_effects),
        protected=protected_by_name(name) or block.protected is True,
    )


def scrub_environment(
    text: str, section: AdapterSection, name: str, environ: Mapping[str, str] = os.environ
) -> str:
    """`text` with every credential value environment `name` declares replaced by `$<VAR>`,
    whether or not it resolved: what an error raised before resolution may carry."""
    block = section.environments.get(name)
    declared = block.get(CREDENTIALS_KEY) if isinstance(block, Mapping) else None
    return scrub_values(text, declared or {}, environ)


# --- identity modes (decision 6) ---


class IdentityApplied(BaseModel):
    """What one identity mode does to a session's requests, from one Fixture's data."""

    headers: dict[str, str] = Field(default_factory=dict, repr=False)
    """Sent on every request of the session."""

    body: dict[str, Any] | None = Field(default=None, repr=False)
    """Handed to the Dialect, which places it in the request body as `identity`."""

    first_message_prefix: str | None = Field(default=None, repr=False)
    """Prefixed to the session's first user message, which the Trace then records as sent."""


@runtime_checkable
class IdentityMode(Protocol):
    """How one `identity.mode` applies a Fixture of kind `identity` (decision 6).

    Core's three are below; a Dialect may offer more as `identity_modes: Mapping[str,
    IdentityMode]`, an optional attribute the Adapter consults after core's."""

    mechanism: str
    """What `fixture/applied.mechanism` records."""

    def problems(self, block: Mapping[str, Any]) -> list[str]:
        """What is wrong with the `identity` block itself for this mode."""
        ...

    def fields(self, block: Mapping[str, Any]) -> list[str]:
        """The Fixture data fields this mode reads, as the block names them."""
        ...

    def apply(self, block: Mapping[str, Any], data: Mapping[str, Any]) -> IdentityApplied:
        """What the session sends, from the Fixture's data (every field present)."""
        ...


class HeaderMode:
    """`<header>: <data[from]>` on every request (`also: {other-header: field}` optional)."""

    mechanism = "identity_header"

    def problems(self, block: Mapping[str, Any]) -> list[str]:
        missing = [key for key in ("header", "from") if not isinstance(block.get(key), str)]
        also = block.get("also", {})
        wrong = [] if isinstance(also, Mapping) else ["also must map a header to a field"]
        return [f"the header mode needs `{key}`" for key in missing] + wrong

    def fields(self, block: Mapping[str, Any]) -> list[str]:
        also = block.get("also") or {}
        return [str(block.get("from")), *(str(field) for field in also.values())]

    def apply(self, block: Mapping[str, Any], data: Mapping[str, Any]) -> IdentityApplied:
        headers = {str(block["header"]): str(data[str(block["from"])])}
        for header, field in (block.get("also") or {}).items():
            headers[str(header)] = str(data[str(field)])
        return IdentityApplied(headers=headers)


class BodyMode:
    """The request body gains `identity: {…data}`, placed by the Dialect."""

    mechanism = "identity_body"

    def problems(self, block: Mapping[str, Any]) -> list[str]:
        return []

    def fields(self, block: Mapping[str, Any]) -> list[str]:
        return []

    def apply(self, block: Mapping[str, Any], data: Mapping[str, Any]) -> IdentityApplied:
        return IdentityApplied(body=dict(data))


class FirstMessageMode:
    """The first user message is prefixed with `template.format(**data)`: a template of
    field names, never instructions."""

    mechanism = "identity_first_message"

    def problems(self, block: Mapping[str, Any]) -> list[str]:
        template = block.get("template")
        if not isinstance(template, str) or not template:
            return ["the first_message mode needs `template`"]
        try:
            template_names(template)
        except ValueError as exc:
            return [f"the first_message template is not a format template: {exc}"]
        return []

    def fields(self, block: Mapping[str, Any]) -> list[str]:
        return template_names(str(block.get("template") or ""))

    def apply(self, block: Mapping[str, Any], data: Mapping[str, Any]) -> IdentityApplied:
        return IdentityApplied(first_message_prefix=interpolated(str(block["template"]), data))


CORE_IDENTITY_MODES: Mapping[str, IdentityMode] = {
    "header": HeaderMode(),
    "body": BodyMode(),
    "first_message": FirstMessageMode(),
}
"""Core's three identity modes, by `identity.mode` (decision 6)."""


def identity_mode(
    block: Mapping[str, Any], extra: Mapping[str, IdentityMode] | None = None
) -> IdentityMode | None:
    """The mode an `identity` block names: core's first, then a Dialect's; None when neither
    has it."""
    name = block.get("mode")
    if not isinstance(name, str):
        return None
    return CORE_IDENTITY_MODES.get(name) or (extra or {}).get(name)


# --- what `validate` checks (decision 2) ---


def http_problems(section: AdapterSection) -> list[Problem]:
    """Every rule an HTTP Adapter block breaks: its `tool_truth`, then each environment's,
    in the order written."""
    problems: list[Problem] = []
    try:
        tool_truth_of(section)
    except HttpEnvironmentError as wrong:
        problems.append(
            (f"adapter.{TOOL_TRUTH_KEY}", str(wrong).removeprefix("adapter.tool_truth: "))
        )
    for name, raw in section.environments.items():
        if name == DEFAULT_ENVIRONMENT_KEY or not isinstance(raw, Mapping):
            continue
        where = f"adapter.environments.{name}"
        try:
            block = HttpEnvironment.model_validate(dict(raw))
        except ValidationError as exc:
            for error in exc.errors():
                loc = ".".join(str(part) for part in error["loc"])
                problems.append((f"{where}.{loc}", str(error["msg"])))
            continue
        problems += _base_url_problems(f"{where}.base_url", block.base_url)
        try:
            check_registered(DIALECT_GROUP, block.dialect)
        except UnknownKind as unknown:
            problems.append((f"{where}.dialect", str(unknown)))
        identifiers = set(block.identifiers)
        if block.path is not None:
            if not block.path.startswith("/"):
                problems.append((f"{where}.path", f"{block.path!r} does not start with /"))
            problems += _template_problems(f"{where}.path", block.path, identifiers)
        for header, value in block.headers.items():
            if header.lower() in CREDENTIAL_HEADERS:
                problems.append(
                    (
                        f"{where}.headers.{header}",
                        f"{header} carries a credential; name its variable under "
                        "`credentials:` instead, and the Dialect sends it",
                    )
                )
            problems += _template_problems(f"{where}.headers.{header}", value, identifiers)
        for role, variable in block.credentials.items():
            if not variable:
                problems.append((f"{where}.credentials.{role}", "names no environment variable"))
        if block.identity is not None:
            problems += _identity_problems(f"{where}.identity", block.identity, block.dialect)
    return problems


def _base_url_problems(where: str, value: str) -> list[Problem]:
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return [(where, f"{value!r} needs a scheme and a host, such as https://api.example.test")]
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return [(where, f"{value!r} is a scheme and a host only; put the path under `path`")]
    return []


def _template_problems(where: str, template: str, identifiers: set[str]) -> list[Problem]:
    try:
        names = template_names(template)
    except ValueError as exc:
        return [(where, f"{template!r} is not a format template: {exc}")]
    unknown = [name for name in names if name not in identifiers]
    return [
        (
            where,
            f"interpolates {{{name}}}, which is not an identifier of this environment; only "
            "identifiers interpolate, never credentials",
        )
        for name in unknown
    ]


def _identity_problems(where: str, block: Mapping[str, Any], dialect: str) -> list[Problem]:
    mode = identity_mode(block)
    if mode is None:
        if dialect not in CORE_DIALECTS:
            return []  # a plugin Dialect's own modes are its to check, when it is loaded
        return [
            (
                f"{where}.mode",
                f"{block.get('mode')!r} is not an identity mode; core's are "
                f"{', '.join(CORE_IDENTITY_MODES)}",
            )
        ]
    return [(where, problem) for problem in mode.problems(block)]


def fixture_problem(
    fixtures: Sequence[Fixture],
    *,
    environment: str,
    identity: Mapping[str, Any] | None,
    extra_modes: Mapping[str, IdentityMode] | None = None,
) -> str | None:
    """Why these Fixtures cannot be applied through environment `environment`'s `identity`
    block, or None (decision 6): one rule for `check_fixtures` and `open`, so the two
    cannot disagree."""
    where = f"adapter.environments.{environment}.identity"
    for fixture in fixtures:
        if fixture.kind != IDENTITY_FIXTURE_KIND:
            return (
                f"Fixture {fixture.name!r} (kind {fixture.kind}) cannot be applied: the http "
                "Adapter applies only Fixtures of kind identity, through an identity mode"
            )
        if identity is None:
            return (
                f"Fixture {fixture.name!r} (kind identity) needs {where}; the http Adapter "
                "applies Fixtures only through an identity mode"
            )
        mode = identity_mode(identity, extra_modes)
        if mode is None:
            return (
                f"Fixture {fixture.name!r} (kind identity): {where}.mode "
                f"{identity.get('mode')!r} is not an identity mode this Adapter has"
            )
        wrong = mode.problems(identity)
        if wrong:
            return f"Fixture {fixture.name!r} (kind identity): {where}: {'; '.join(wrong)}"
        for field in mode.fields(identity):
            if field not in fixture.data:
                return (
                    f"Fixture {fixture.name!r} (kind identity) has no field {field!r}, which "
                    f"{where} reads"
                )
    return None


__all__ = [
    "CORE_IDENTITY_MODES",
    "CREDENTIAL_HEADERS",
    "IDENTITY_FIXTURE_KIND",
    "IDENTITY_KEY",
    "TOOL_TRUTH_KEY",
    "BodyMode",
    "FirstMessageMode",
    "HeaderMode",
    "HttpEnvironment",
    "HttpEnvironmentError",
    "IdentityApplied",
    "IdentityMode",
    "ResolvedHttpEnvironment",
    "ToolTruth",
    "environment_block",
    "fixture_problem",
    "http_problems",
    "identity_mode",
    "interpolated",
    "resolve_http_environment",
    "scrub_environment",
    "template_names",
    "tool_truth_of",
]
