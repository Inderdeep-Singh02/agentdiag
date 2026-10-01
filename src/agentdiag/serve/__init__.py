"""`agentdiag serve`: the local web UI over one Workspace (ticket 16, phase-7 decisions 22-23).

`app` holds the routes as functions over a `Workspace`, calling the same command functions
the CLI calls; `server` is the standard library's HTTP server around them, bound to
127.0.0.1. Nothing here imports a model client: a Run launched from the page imports the
executor when it starts, as `agentdiag run` does.
"""
