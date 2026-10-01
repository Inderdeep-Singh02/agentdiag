"""The help desk's "platform": the deployed records its prompt, tools and model live in.

The second toy Target stands for an agent whose prompt and tools are records on a hosted
platform rather than files in a repository (phase-6 decision 38). This module is that
platform, in miniature: `DEPLOYED` is the deployed set in the in-process Connector's
convention (decision 22: `prompts`, `tools`, `model`, `provider`), and the Target reads it
at every request (`target.py`), so assigning `DEPLOYED["prompts"]["system"]` is an edit on
the deployed side, live at once, exactly as saving a prompt on a platform is. The Manifest
names `agentdiag.examples.helpdesk.platform:DEPLOYED` for the Connector.

`EVIDENCE` is the platform's Evidence stores, by kind (decision 40): `EVIDENCE["proxy"]` is
one conversation's four proxy rows, the same rows as
`tests/fixtures/evidence/helpdesk-proxy-rows.json` (a test asserts it), invented, with no
customer content. They are an older conversation, logged under an earlier, shorter prompt:
Evidence is what happened, not what is deployed now.

The prompt is markdown with headings, so `agentdiag sync` fingerprints it as the sections
`prompt.system#persona`, `#rules` and `#tone` (decision 10), where the first toy's is one
paragraph of numbered rules.

`DEPLOYED["flows"]` holds one Flow, `open_ticket_flow`: what the platform runs when the
`open_ticket` tool is called, as steps and edges with a `state` and a `version` (phase-7
decision 21). The Connector reads it as the section `flow.open_ticket_flow`, and the Flow
view draws it; the Target itself never runs it, since the help desk's `open_ticket` only
appends to a list. Nothing here imports anything but `copy`, for `STAGING`.
"""

from __future__ import annotations

import copy
from typing import Any

SYSTEM_PROMPT = """\
# Persona

You are the help desk for Northwind Bicycles' customer accounts. Customers write in about
signing in, billing, invoices, their saved addresses and the Northwind app. You are not the
order desk: a question about one order's status belongs to the order desk, and you say so.

# Rules

1. Search before you answer. For any how-to question, call search_articles first and answer
   from the article it returns, naming the article's id and title.

2. Never invent article content. State only the steps, menu names, times and policies that
   search_articles returned. If no article matches, say so and offer to open a ticket; never
   describe steps from memory.

3. Open a ticket when no article answers the question or the customer reports that
   something is broken. Call open_ticket with a one-line subject and the customer's own
   words as the details, and give the customer the ticket id.

4. Escalate to a person when the customer asks for one, or reports a safety problem with a
   bike or its battery. Open a ticket first, then call escalate with that ticket id and the
   reason, and tell the customer a person will reply. Never escalate a question an article
   answers.

5. Never ask for a password, a full card number or a security code, and never repeat one a
   customer sends.

# Tone

Plain and warm. At most four sentences per reply. No marketing language and no emoji.
"""
"""The system prompt as the platform holds it: three headed sections, five numbered rules."""

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_articles": {
        "name": "search_articles",
        "description": "Search the help articles by keyword.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    "open_ticket": {
        "name": "open_ticket",
        "description": "Open a support ticket for a person on the help desk team to follow up.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "One line naming the problem."},
                "details": {"type": "string", "description": "The customer's own words."},
            },
            "required": ["subject", "details"],
        },
    },
    "escalate": {
        "name": "escalate",
        "description": "Hand an open ticket to a person, who replies to the customer directly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "The ticket to escalate."},
                "reason": {"type": "string", "description": "Why a person must take it."},
            },
            "required": ["ticket_id", "reason"],
        },
    },
}
"""The platform's tool records, by name, in the `input_schema` shape the Anthropic API
takes. `search_articles`' record is the one the proxy rows below were logged with."""

DEFAULT_MODEL = "claude-sonnet-5"
"""What the platform runs the help desk on. Not the Judge's model (D23)."""

OPEN_TICKET_FLOW: dict[str, Any] = {
    "tool": "open_ticket",
    "state": "on",
    "version": 3,
    "steps": [
        {"id": "receive", "name": "Receive the ticket", "kind": "trigger", "calls": "open_ticket"},
        {"id": "file", "name": "File it in the queue", "kind": "action", "calls": "queue.add"},
        {
            "id": "safety",
            "name": "Check for a safety report",
            "kind": "condition",
            "calls": "rules.safety",
        },
        {"id": "page", "name": "Page the duty lead", "kind": "action", "calls": "pager.notify"},
        {
            "id": "acknowledge",
            "name": "Acknowledge the customer",
            "kind": "respond",
            "calls": "reply.send",
        },
    ],
    "edges": [
        ["receive", "file"],
        ["file", "safety"],
        ["safety", "page"],
        ["safety", "acknowledge"],
        ["page", "acknowledge"],
    ],
}
"""The Flow the platform runs on each `open_ticket` call: file the ticket, page the duty lead
when it reports a safety problem, acknowledge the customer. Invented, in the `steps`/`edges`
shape; `kind` and `calls` are the platform's own words, which agentdiag passes through."""

DEPLOYED: dict[str, Any] = {
    "prompts": {"system": SYSTEM_PROMPT},
    "tools": TOOL_SCHEMAS,
    "model": DEFAULT_MODEL,
    "provider": "anthropic",
    "flows": {"open_ticket_flow": OPEN_TICKET_FLOW},
}
"""The deployed set, in the deployed-set convention (decision 22). Mutable on purpose: an
edit here is an edit on the platform, which the Target and the Connector both see at once."""

STAGING: dict[str, Any] = copy.deepcopy(DEPLOYED)
"""The platform's protected `staging` environment (phase-7 decision 16): a deep copy of
`DEPLOYED` made at import, so a push to one never reaches the other. The Manifest marks it
`protected`, so a push to it needs the environment's name typed by a person."""

PROXY_ROWS: list[dict[str, Any]] = [
    {
        "request_id": "req-hd-0001-1",
        "created_at": "2026-09-20T09:00:00.000Z",
        "latency_ms": 900,
        "model": "claude-sonnet-5",
        "provider": "anthropic",
        "status": 200,
        "prompt_tokens": 640,
        "completion_tokens": 48,
        "conversation_id": "hd-0001",
        "request_body": {
            "system": "You answer for the Northwind help desk. Search the help articles "
            "before you answer a how-to question, and quote the article you "
            "used.",
            "tools": [
                {
                    "name": "search_articles",
                    "description": "Search the help articles by keyword.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }
            ],
            "messages": [
                {"role": "user", "content": "How do I change the billing address on my account?"}
            ],
        },
        "response_body": {
            "id": "msg_hd0001_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_hd0001_search",
                    "name": "search_articles",
                    "input": {"query": "change billing address"},
                }
            ],
            "stop_reason": "tool_use",
        },
    },
    {
        "request_id": "req-hd-0001-2",
        "created_at": "2026-09-20T09:00:01.300Z",
        "latency_ms": 1100,
        "model": "claude-sonnet-5",
        "provider": "anthropic",
        "status": 200,
        "prompt_tokens": 790,
        "completion_tokens": 72,
        "cost_usd": 0.0023,
        "conversation_id": "hd-0001",
        "request_body": {
            "messages": [
                {"role": "user", "content": "How do I change the billing address on my account?"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_hd0001_search",
                            "name": "search_articles",
                            "input": {"query": "change billing address"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_hd0001_search",
                            "content": '[{"id": "KB-104", "title": "Change '
                            'your billing address", "summary": '
                            '"Settings > Billing > Address, then '
                            'Save."}]',
                        }
                    ],
                },
            ]
        },
        "response_body": {
            "id": "msg_hd0001_2",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [
                {
                    "type": "text",
                    "text": "Open Settings, then Billing, then Address, and press "
                    "Save (article KB-104, Change your billing "
                    "address).",
                }
            ],
            "stop_reason": "end_turn",
        },
    },
    {
        "request_id": "req-hd-0001-3",
        "created_at": "2026-09-20T09:01:10.000Z",
        "latency_ms": 850,
        "model": "claude-sonnet-5",
        "provider": "anthropic",
        "status": 200,
        "prompt_tokens": 880,
        "completion_tokens": 40,
        "conversation_id": "hd-0001",
        "request_body": {
            "messages": [
                {"role": "user", "content": "How do I change the billing address on my account?"},
                {
                    "role": "assistant",
                    "content": "Open Settings, then Billing, then Address, and "
                    "press Save (article KB-104, Change your billing "
                    "address).",
                },
                {"role": "user", "content": "Thanks. Can I download my past invoices too?"},
            ]
        },
        "response_body": {
            "id": "msg_hd0001_3",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_hd0001_invoices",
                    "name": "search_articles",
                    "input": {"query": "download invoices"},
                }
            ],
            "stop_reason": "tool_use",
        },
    },
    {
        "request_id": "req-hd-0001-4",
        "created_at": "2026-09-20T09:01:11.200Z",
        "latency_ms": 1000,
        "model": "claude-sonnet-5",
        "provider": "anthropic",
        "status": 200,
        "prompt_tokens": 1010,
        "completion_tokens": 66,
        "conversation_id": "hd-0001",
        "request_body": {
            "messages": [
                {"role": "user", "content": "How do I change the billing address on my account?"},
                {
                    "role": "assistant",
                    "content": "Open Settings, then Billing, then Address, and "
                    "press Save (article KB-104, Change your billing "
                    "address).",
                },
                {"role": "user", "content": "Thanks. Can I download my past invoices too?"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_hd0001_invoices",
                            "name": "search_articles",
                            "input": {"query": "download invoices"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_hd0001_invoices",
                            "content": '[{"id": "KB-117", "title": "Download '
                            'invoices", "summary": "Billing > '
                            "Invoices, then Download beside each "
                            'one."}]',
                        }
                    ],
                },
            ]
        },
        "response_body": {
            "id": "msg_hd0001_4",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [
                {
                    "type": "text",
                    "text": "Yes: go to Billing, then Invoices, and press "
                    "Download beside each one (article KB-117, Download "
                    "invoices).",
                }
            ],
            "stop_reason": "end_turn",
        },
    },
]
"""One conversation, `hd-0001`, as the platform's proxy logged it: two Turns, each a
question, a `search_articles` call, the row that carries its result and the answer."""

EVIDENCE: dict[str, list[dict[str, Any]]] = {"proxy": PROXY_ROWS}
"""The platform's Evidence stores, by kind. The Manifest names this mapping as the `proxy`
store's rows, and the in-process Connector takes the entry of the kind it reads."""

__all__ = [
    "DEFAULT_MODEL",
    "DEPLOYED",
    "EVIDENCE",
    "OPEN_TICKET_FLOW",
    "PROXY_ROWS",
    "SYSTEM_PROMPT",
    "TOOL_SCHEMAS",
]
