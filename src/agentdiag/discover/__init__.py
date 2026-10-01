"""`agentdiag discover`: draft a Manifest for a Target agentdiag has never seen (ticket 11).

Two sources, one draft shape (phase-6 decision 27): `scan` reads a repository without
running it, and `command` reads a platform's deployed set through the Connector the
Manifest names and saves it as local files. `draft` renders either into
`manifest.draft.yaml` with a `# REVIEW:` line above every guess, keeping what the existing
Manifest wrote, and `command.discover` is what the CLI calls. Offline at import
time, as `sync` and `connector` are: nothing here reaches the model SDK.
"""
