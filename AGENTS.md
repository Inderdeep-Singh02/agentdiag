# AGENTS.md — agentdiag

A profiler and evaluator for agentic systems, and a kit for maintaining them. `README.md`
is the five-minute start, `docs/guide.md` the full guide, `docs/adr/` the design record,
`CONTEXT.md` the vocabulary.

## Rules

- **Vocabulary.** Names of files, functions, tables and CLI commands use the words in
  `CONTEXT.md` (Target, Adapter, Connector, Trace, Span, Scenario, Eval, Run, Score, Sync,
  Change record, Workspace, Registry) and none of the synonyms it lists under _Avoid_.
- **Tests first, at the seams.** Three seams and nothing else: the CLI over a Run directory,
  the ModelClient through committed recordings, and the Adapter through the conformance
  suite. A new behaviour starts as a failing test at one of them.
- **Checks before every commit**, all four green:

  ```bash
  uv run ruff check src tests
  uv run ruff format --check src tests
  uv run mypy
  uv run pytest
  ```

- **No attribution trailer.** No commit carries a `Co-Authored-By` or `Generated with`
  line; `.claude/settings.json` sets `includeCoAuthoredBy` to false.
- **Design decisions** that change a rule go in a new ADR under `docs/adr/`; a new word goes
  in `CONTEXT.md` first.
- Nothing under `src/agentdiag` names a platform: a platform's Connector, Dialect and
  identity modes are a plugin (`docs/plugins.md`).
