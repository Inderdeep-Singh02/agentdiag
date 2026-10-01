"""The help desk's three tools: one retrieval over a seeded article table, two actions.

`search_articles` is a keyword search (a `retrieval` Span once the Manifest marks it so,
D37); `open_ticket` and `escalate` are actions, and `escalate` hands work to a person, which
is why its Manifest entry is `side_effects: sandboxed` (phase-6 decision 38).

The shape differs from the first toy's on purpose, so discovery and the Adapter are shown a
second way a Target ships its tools: the toy's `make_tools()` is a closure over a dict of
orders; here one `HelpDesk` object holds the session's tickets, its bound methods are the
tools, and every tool argument is keyword-only. `make_tools(*, articles=…)` takes its seed
by keyword and still builds with no arguments, which is how the Adapter calls it: once per
session, so no ticket one Trial opened is seen by the next (ADR-0001 point 2).

Deterministic: ticket and escalation ids count from fixed starts and nothing reads a clock,
so a replayed Trial's Trace reproduces by the byte. Imports nothing of agentdiag.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Article:
    """One help article: what `search_articles` returns is its id, title and summary."""

    id: str
    title: str
    summary: str
    keywords: tuple[str, ...] = ()

    def as_result(self) -> dict[str, str]:
        return {"id": self.id, "title": self.title, "summary": self.summary}

    def words(self) -> set[str]:
        return _words(" ".join((self.title, self.summary, *self.keywords)))


SEED_ARTICLES: tuple[Article, ...] = (
    Article(
        "KB-101",
        "Reset your password",
        "Sign in > Forgot password, then follow the link we email you within 10 minutes.",
        ("password", "login", "sign"),
    ),
    Article(
        "KB-104",
        "Change your billing address",
        "Settings > Billing > Address, then Save.",
        ("billing", "address"),
    ),
    Article(
        "KB-117",
        "Download invoices",
        "Billing > Invoices, then Download beside each one.",
        ("invoice", "receipt"),
    ),
    Article(
        "KB-122",
        "Update the card you pay with",
        "Settings > Billing > Payment method, then Replace card.",
        ("card", "payment"),
    ),
    Article(
        "KB-130",
        "Pair the Northwind app with your bike",
        "In the app, Garage > Add bike, then hold the bike's power button for 5 seconds.",
        ("app", "pair", "bluetooth"),
    ),
)
"""The article table as it ships. Read-only; a search reads it and nothing writes it."""

FIRST_TICKET = 5001
FIRST_ESCALATION = 301

STOPWORD_TEXT = (
    "a an and are can do does for how i in is it me my of on or the to too what when where "
    "why with you your"
)
STOPWORDS = frozenset(STOPWORD_TEXT.split())
"""Words a search ignores: they match every article and say nothing about any."""


class TicketNotFound(LookupError):
    """No ticket of this session carries that id."""


@dataclass
class HelpDesk:
    """One session's help desk: the articles it searches and the tickets it has opened."""

    articles: Sequence[Article] = SEED_ARTICLES
    tickets: dict[str, dict[str, Any]] = field(default_factory=dict)
    escalations: list[dict[str, Any]] = field(default_factory=list)

    def search_articles(self, *, query: str) -> list[dict[str, str]]:
        """The articles that share at least half of the query's words, best first, at most
        three; an empty list when none does."""
        wanted = _words(query)
        if not wanted:
            return []
        scored = [(len(wanted & article.words()), article) for article in self.articles]
        needed = (len(wanted) + 1) // 2
        kept = sorted(
            (pair for pair in scored if pair[0] >= needed), key=lambda pair: (-pair[0], pair[1].id)
        )
        return [article.as_result() for _, article in kept[:3]]

    def open_ticket(self, *, subject: str, details: str) -> dict[str, Any]:
        """Open a ticket and return it with its id."""
        ticket_id = f"HD-{FIRST_TICKET + len(self.tickets)}"
        ticket = {"ticket_id": ticket_id, "status": "open", "subject": subject, "details": details}
        self.tickets[ticket_id] = ticket
        return dict(ticket)

    def escalate(self, *, ticket_id: str, reason: str) -> dict[str, Any]:
        """Hand an open ticket to a person; a ticket this session never opened is refused."""
        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            raise TicketNotFound(f"No ticket {ticket_id}; open one with open_ticket first")
        ticket["status"] = "escalated"
        escalation = {
            "escalation_id": f"ESC-{FIRST_ESCALATION + len(self.escalations)}",
            "ticket_id": ticket_id,
            "reason": reason,
            "status": "queued for a person",
        }
        self.escalations.append(escalation)
        return dict(escalation)

    def tools(self) -> dict[str, Callable[..., Any]]:
        """The three tools, by the names the platform's tool records give them."""
        return {
            "search_articles": self.search_articles,
            "open_ticket": self.open_ticket,
            "escalate": self.escalate,
        }


def make_tools(*, articles: Sequence[Article] = SEED_ARTICLES) -> dict[str, Callable[..., Any]]:
    """One session's tools, over a fresh `HelpDesk`: what the Manifest's `tools` names."""
    return HelpDesk(articles=tuple(articles)).tools()


def _words(text: str) -> set[str]:
    """The lowercase words of `text` that carry meaning, a trailing plural `s` dropped."""
    found = {word.lower() for word in re.findall(r"[A-Za-z0-9]+", text)}
    stemmed = {word[:-1] if len(word) > 3 and word.endswith("s") else word for word in found}
    return stemmed - STOPWORDS


__all__ = [
    "FIRST_ESCALATION",
    "FIRST_TICKET",
    "SEED_ARTICLES",
    "Article",
    "HelpDesk",
    "TicketNotFound",
    "make_tools",
]
