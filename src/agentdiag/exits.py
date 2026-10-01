"""The exit code every command shares for a usage, configuration or preflight error (D32).

D32 gives usage, configuration and preflight errors one code, so each command's own name
for it (`VALIDATE_EXIT`, `PREFLIGHT_EXIT`, `NOT_FOUND_EXIT`, ...) is this constant under
the name its reader looks for, and the code can never differ between two commands. The
Verdict-driven codes 0, 1 and 2 are `agentdiag.run.scorecard.exit_code`'s. Imports nothing,
so the offline commands can read it.
"""

from __future__ import annotations

USAGE_EXIT = 3

__all__ = ["USAGE_EXIT"]
