"""A help desk with a headed markdown prompt, a retrieval tool and two actions, on a platform.

This is the second toy Target (phase-6 decision 38), shaped unlike the first on purpose, so
discovery, generation, Sync and the Importers are shown a second kind of Target: its prompt
is markdown under `# Persona`, `# Rules` and `# Tone` headings rather than one paragraph;
its prompt and tool records live on a "platform" (`platform.DEPLOYED`) that the Target reads
at every request and the in-process Connector reads for `sync`, so an edit there is live at
once; its tools are the bound methods of one `HelpDesk` with keyword-only arguments; and its
factory returns an object rather than a closure. The Manifest names
`agentdiag.examples.helpdesk:make_helpdesk` and `agentdiag.examples.helpdesk:make_tools` for
the Adapter, `agentdiag.examples.helpdesk.platform:DEPLOYED` for the Connector's deployed set
and `agentdiag.examples.helpdesk.platform:EVIDENCE` for its proxy rows. Its protected
`staging` environment is `make_staging_helpdesk` over `platform.STAGING` (ticket 27).

It is the help desk of the same persona as the first toy, Northwind Bicycles, so the two are
one Family (`family: northwind`, `channel: chat` in both Manifests). Nothing here imports
agentdiag outside this package: the Adapter instruments the Target from the outside (D6).
"""

from agentdiag.examples.helpdesk.platform import (
    DEFAULT_MODEL,
    DEPLOYED,
    EVIDENCE,
    PROXY_ROWS,
    STAGING,
    SYSTEM_PROMPT,
    TOOL_SCHEMAS,
)
from agentdiag.examples.helpdesk.target import (
    DEFAULT_MAX_TOKENS,
    HelpDeskTarget,
    make_helpdesk,
    make_staging_helpdesk,
)
from agentdiag.examples.helpdesk.tools import (
    SEED_ARTICLES,
    Article,
    HelpDesk,
    TicketNotFound,
    make_tools,
)

__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "DEPLOYED",
    "EVIDENCE",
    "PROXY_ROWS",
    "SEED_ARTICLES",
    "STAGING",
    "SYSTEM_PROMPT",
    "TOOL_SCHEMAS",
    "Article",
    "HelpDesk",
    "HelpDeskTarget",
    "TicketNotFound",
    "make_helpdesk",
    "make_staging_helpdesk",
    "make_tools",
]
