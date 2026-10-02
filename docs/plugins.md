# Plugins

agentdiag core knows no platform. A platform's Connector, Dialect and identity modes live in
a separate distribution, a **plugin**, and core finds them by name through Python entry
points when a Manifest names them ([ADR-0014](adr/0014-distribution-core-plugins-skills.md)). Core registers its own kinds the same way in
its `pyproject.toml`, so a plugin is never a special case.

## Entry-point groups

| Group | What it registers | Named by |
|-------|-------------------|----------|
| `agentdiag.adapters` | an Adapter class (converses with the Target) | `adapter.kind` in the Manifest |
| `agentdiag.connectors` | a Connector class (manages the deployed set and Evidence stores) | `connector.kind` in the Manifest |
| `agentdiag.dialects` | a Dialect class (how an HTTP endpoint frames one Turn) | `dialect` in an `http` Adapter environment |

Core registers the `inprocess`, `http` and `pending` Adapters (`pending` is the placeholder
`init --target` writes, which refuses to drive), the `inprocess` Connector, and the `json` and
`sse-json` Dialects. A plugin's `pyproject.toml` adds its own:

```toml
[project.entry-points."agentdiag.connectors"]
acme = "agentdiag_acme.connector:AcmeConnector"

[project.entry-points."agentdiag.dialects"]
acme = "agentdiag_acme.dialect:AcmeDialect"
```

A kind two distributions register is refused by name, and a kind nothing registers is
refused naming the kinds that are installed; both reach the user as `validate` and
`run` errors (`agentdiag.connector.UnknownKind`, and its subclass `AmbiguousKind` in
`agentdiag.connector.plugins`).

## The public API a plugin may import

| Import | Holds |
|--------|-------|
| `agentdiag.adapter` | `Adapter`, `Session`, `AdapterDescription`, `Fixture`, `LiveSideEffectsRefused`, `SessionClosed` |
| `agentdiag.adapter.conformance` | `AdapterConformance`: subclass it in the plugin's tests and inherit every test an Adapter must pass (needs pytest in the plugin's test environment) |
| `agentdiag.adapter.http` | `Dialect`, `Frame`, `HttpRequest`, `HttpResponse`, `DialectError`, `IdentityMode`, `ResolvedHttpEnvironment`, `HttpAdapter`, `HttpTransport` |
| `agentdiag.adapter.http.dialect` | the Frame classes a Dialect yields: `TextFrame`, `ToolFrame`, `ConversationFrame`, `ErrorFrame`, `EndFrame`, and `dialect_identity_modes` |
| `agentdiag.connector` | `Connector`, `ConnectorDescription`, `DeployedSet`, `EvidenceQuery`, `EvidenceRows`, `Operation`, `WriteReceipt`, `WriteRefused`, `CredentialMissing`, `ExpectedFingerprintMoved`, `ResolvedEnvironment`, `resolve_environment` |
| `agentdiag.connector.conformance` | `ConnectorConformance`: the suite every Connector must pass |
| `agentdiag.importer` | `ProxyRowImporter`, `ConversationRecordImporter`, `VoiceConversationImporter` and the generic row shapes they read; a Connector returns its platform's rows in those shapes |
| `agentdiag.trace` | `TraceWriter`, `read_trace`, `project_spans`, `event_fields` |
| `agentdiag.types` | the closed vocabularies: `Fidelity`, `ToolKind`, `SideEffectClass`, `EvidenceKind`, `Verdict` |

Everything else under `agentdiag` is internal and may change between minor versions.

## What a plugin does, and does not

- A **Connector** implements `describe`, `read_deployed_set`, `read_evidence` and
  `write_deployed_set`. It resolves one environment's identifiers and credentials through
  `resolve_environment` and fails closed: a credential another environment holds is never
  used instead. It writes a Target only through `push`.
- A **Dialect** is a pure function of its inputs: it builds one Turn's `HttpRequest` from the
  message, the conversation it continues and the resolved environment, and reads the
  response into Frames as they arrive. No I/O, no clock; the Adapter timestamps each Frame.
  A Dialect may declare `identity_modes` the platform supports beyond core's `header`,
  `body` and `first_message`.
- Platform facts (endpoint shapes, header names, a stream's line prefixes) stay in the
  plugin. Nothing of a platform is named in core, in a Trace schema or in a Score.
- Credential values travel only inside the request a Dialect builds and appear in no file
  agentdiag writes; a Trace records header names, never values.

## Checking a plugin

Install the plugin beside agentdiag, point a Manifest at its kinds, then:

```bash
agentdiag validate            # the Manifest loads; unknown kinds are named
agentdiag run --dry-run       # the plugin's classes are built, nothing is driven
agentdiag sync                # the Connector's read becomes the Target's Fingerprint
```

`validate` imports nothing of the plugin; `run --dry-run` is the first command that does.
