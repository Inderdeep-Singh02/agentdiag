"""An order desk with five numbered rules, one lookup tool and one action tool.

This is the toy Target. The Manifest names `agentdiag.examples.toy:make_target` and
`agentdiag.examples.toy:make_tools` for the Adapter, and `agentdiag.examples.toy:deployed_set`
for the in-process Connector, which reads the prompt, the tools and the model the running
module holds. The first line above is the Manifest's description, word for word, so
`agentdiag discover` drafts it from this docstring.
Nothing here imports agentdiag outside this package: the Adapter instruments the Target from
the outside, by handing it a client and tool callables it owns (ADR-0001, D6).

The Phase-4 recording's closing reply invents a refund window, the Judge fails it on rule 3,
and the README's first Run is that fail; the prompt is not tuned to remove it, because the
walkthrough exists to show a catch (phase-5 decision 43).
"""

from agentdiag.examples.toy.prompt import SYSTEM_PROMPT
from agentdiag.examples.toy.target import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    deployed_set,
    make_target,
)
from agentdiag.examples.toy.tools import (
    CANCELLED_AT,
    SEED_ORDERS,
    TOOL_SCHEMAS,
    OrderNotFound,
    make_tools,
)

__all__ = [
    "CANCELLED_AT",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "SEED_ORDERS",
    "SYSTEM_PROMPT",
    "TOOL_SCHEMAS",
    "OrderNotFound",
    "deployed_set",
    "make_target",
    "make_tools",
]
