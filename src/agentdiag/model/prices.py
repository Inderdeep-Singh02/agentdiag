"""What a model call cost, from a versioned price table (ADR-0006 §4, phase-5 decisions 4, 5).

Cost is written on the Span when the call is captured — by the Adapter's emitter for the
Target, by the Judge for itself — under OpenInference's `llm.cost.*` names in
`span/end.attributes` (`cost_attributes`, the one function both call), and the Metrics
projection only reads `llm.cost.total` back. So a later edit to this table cannot change what a
recorded Run cost (ADR-0003 §5's rule that a stored Score stays interpretable, applied to
money), and `run.json` names the table's version so a comparison can say "priced under
different tables".

The table is USD per million tokens. `PRICE_TABLE_VERSION` is the date the table last
changed: the rates were taken from the `claude-api` skill's model table as cached on
2026-06-24, and the 1-hour cache-write column was added on 2026-09-23. Which cells are
published and which are derived, so a cell can be corrected without archaeology:

- `input` and `output`: the published rates, every row.
- `cache_read`: the published rate for `claude-fable-5-1` (0.25) and `claude-opus-5-5`
  (0.20); everywhere else the standard 0.1 times input.
- `cache_write`: the standard 1.25 times input (the five-minute cache) on every row.
- `cache_write_1h`: the standard 2 times input (the one-hour cache) on every row.

A cache write is priced at the five-minute rate unless the response's `usage.cache_creation`
says how many of its tokens had the one-hour lifetime (`ephemeral_1h_input_tokens`); those
are priced at `cache_write_1h`, the rest at `cache_write`, and both go into the one
`llm.cost.prompt_details.cache_write` attribute. The Messages API sends the split whenever a
cache write happens; the Claude Code CLI writes every cache entry with the one-hour lifetime,
which is what the live cost check of 2026-09-23 found (ticket 19).

A model is matched exactly, then by its dated-alias prefix (`claude-opus-5-20260401` →
`claude-opus-5`); nothing else is guessed. A model the table does not price costs `None`,
the Span gets no cost attribute, and `show` says `unpriced` — never zero, which would be a
claim about a bill nobody has seen.

A backend that reports what a call cost (the Claude Code CLI's `total_cost_usd`, ticket 19)
is checked against this table, never used in its place: `cost_check` says `agrees`,
`differs` or `unpriced` under `cost_tolerance`, the Judge writes the verdict on its Span
beside both numbers, and nothing here changes because of a `differs`. That is the evidence
for a human to correct a cell (decision 27); the one-hour column is the first correction
made from it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import BaseModel

from agentdiag.trace.attributes import COST_CHECK, REPORTED_COST_USD
from agentdiag.trace.openinference import (
    COST_COMPLETION,
    COST_PROMPT,
    COST_PROMPT_CACHE_READ,
    COST_PROMPT_CACHE_WRITE,
    COST_PROMPT_INPUT,
    COST_TOTAL,
)
from agentdiag.types import CostCheck

PRICE_TABLE_VERSION = "2026-09-23"
"""The date the table last changed (decision 5): a Run records it, and `compare` says when
two Runs were priced under different tables."""

PER_TOKENS = 1_000_000
"""The table's unit: USD per million tokens."""

DATED_ALIAS = re.compile(r"^(?P<model>.+)-\d{8}$")
"""A resolved model id with a release date suffix, as the API returns it."""


class ModelPrice(BaseModel):
    """One row: USD per million tokens of each kind the API bills."""

    input: float
    output: float
    cache_read: float
    cache_write: float
    """A cache write with the five-minute lifetime, the API's default."""
    cache_write_1h: float
    """A cache write with the one-hour lifetime."""


def _with_standard_cache_writes(input: float, output: float, cache_read: float) -> ModelPrice:
    """A row whose cache writes are the standard multiples of its input rate."""
    return ModelPrice(
        input=input,
        output=output,
        cache_read=cache_read,
        cache_write=1.25 * input,
        cache_write_1h=2 * input,
    )


PRICES: dict[str, ModelPrice] = {
    "claude-fable-5-1": _with_standard_cache_writes(input=10.00, output=50.00, cache_read=0.25),
    "claude-fable-5": _with_standard_cache_writes(input=10.00, output=50.00, cache_read=1.00),
    "claude-opus-5-5": _with_standard_cache_writes(input=4.00, output=20.00, cache_read=0.20),
    "claude-opus-5": _with_standard_cache_writes(input=5.00, output=25.00, cache_read=0.50),
    "claude-opus-4-8": _with_standard_cache_writes(input=5.00, output=25.00, cache_read=0.50),
    "claude-opus-4-7": _with_standard_cache_writes(input=5.00, output=25.00, cache_read=0.50),
    "claude-opus-4-6": _with_standard_cache_writes(input=5.00, output=25.00, cache_read=0.50),
    "claude-sonnet-5": _with_standard_cache_writes(input=2.00, output=10.00, cache_read=0.20),
    "claude-sonnet-4-6": _with_standard_cache_writes(input=3.00, output=15.00, cache_read=0.30),
    "claude-haiku-4-5": _with_standard_cache_writes(input=1.00, output=5.00, cache_read=0.10),
}
"""Decision 5's table, cell for cell: `input`, `output` and `cache_read` spelled as it spells
them, the two cache-write cells the standard multiples it lists (derived here, pinned as
literals in `tests/test_prices.py`). A row with a published cache rate of its own would be a
`ModelPrice(...)` spelled out in full."""

USAGE_KEYS: dict[str, tuple[str, str]] = {
    "input_tokens": ("input", COST_PROMPT_INPUT),
    "cache_read_input_tokens": ("cache_read", COST_PROMPT_CACHE_READ),
}
"""The API's input `usage` fields billed at one rate each: the price column, and the
OpenInference prompt part it is recorded under. Cache writes are split by lifetime instead
(`_cache_write_cost`)."""

CACHE_WRITE_TOKENS = "cache_creation_input_tokens"
"""Every cache-write token of the call, both lifetimes."""

CACHE_WRITE_LIFETIMES = "cache_creation"
"""The API's split of `cache_creation_input_tokens` by lifetime, when it sends one."""

CACHE_WRITE_5M_TOKENS = "ephemeral_5m_input_tokens"
"""Within the split, the tokens written with the five-minute lifetime."""

CACHE_WRITE_1H_TOKENS = "ephemeral_1h_input_tokens"
"""Within the split, the tokens written with the one-hour lifetime."""

OUTPUT_TOKENS = "output_tokens"

COST_TOLERANCE_ABS = 2e-6
"""The floor of the tolerance, USD: the table's costs are rounded to six decimals, so two
roundings apart is still agreement."""

COST_TOLERANCE_REL = 0.005
"""The tolerance as a share of the reported cost: half a percent."""


def price_for(model: str | None) -> ModelPrice | None:
    """The row for `model`: an exact match, else its dated-alias prefix, else None."""
    if not model:
        return None
    if model in PRICES:
        return PRICES[model]
    dated = DATED_ALIAS.match(model)
    if dated is not None:
        return PRICES.get(dated.group("model"))
    return None


def cost_attributes(model: str | None, usage: Mapping[str, object]) -> dict[str, float]:
    """What one call cost, as the `llm.cost.*` attributes a model Span's end carries.

    Empty when `model` is unpriced, so the Span records no cost rather than a zero.
    `usage` is the API response's `usage` object. `llm.cost.prompt` is every input token
    (uncached, cache read, cache write), `llm.cost.completion` the output tokens, and
    `llm.cost.total` their sum; each prompt part is written only when the response
    reported that usage, because the API bills only what it counts. USD, six decimals.
    """
    price = price_for(model)
    if price is None:
        return {}
    parts: dict[str, float] = {}
    for field, (column, name) in USAGE_KEYS.items():
        tokens = _tokens(usage.get(field))
        if tokens is not None:
            parts[name] = tokens * getattr(price, column) / PER_TOKENS
    cache_write = _cache_write_cost(price, usage)
    if cache_write is not None:
        parts[COST_PROMPT_CACHE_WRITE] = cache_write
    prompt = sum(parts.values())
    completion = (_tokens(usage.get(OUTPUT_TOKENS)) or 0) * price.output / PER_TOKENS
    attributes = {
        COST_TOTAL: prompt + completion,
        COST_PROMPT: prompt,
        COST_COMPLETION: completion,
        **parts,
    }
    return {name: round(value, 6) for name, value in attributes.items()}


def cost_usd(model: str | None, usage: Mapping[str, object]) -> float | None:
    """What one call cost in USD, six decimals, or None when `model` is unpriced."""
    return cost_attributes(model, usage).get(COST_TOTAL)


def cost_tolerance(reported: float) -> float:
    """How far a computed cost may sit from a reported one and still agree:
    `max(COST_TOLERANCE_ABS, COST_TOLERANCE_REL * reported)`."""
    return max(COST_TOLERANCE_ABS, COST_TOLERANCE_REL * abs(reported))


def cost_check(computed: float | None, reported: float) -> CostCheck:
    """A backend's reported cost against the table's: `unpriced` when the table has no row
    (`computed` is None), `agrees` within `cost_tolerance`, `differs` otherwise."""
    if computed is None:
        return "unpriced"
    return "agrees" if abs(computed - reported) <= cost_tolerance(reported) else "differs"


def _cache_write_cost(price: ModelPrice, usage: Mapping[str, object]) -> float | None:
    """What the call's cache writes cost, each lifetime at its own rate; None when the
    usage reports no cache write.

    `cache_creation_input_tokens` is the total the API billed. When `cache_creation` says
    how many of them had the one-hour lifetime, those are billed at `cache_write_1h` and the
    rest at `cache_write`; without the split, every token is a five-minute write, the API's
    default. Two shapes the API does not send are priced rather than dropped: a split without
    a total is the report of its own two parts, and a one-hour count above the total is
    capped at it, so the five-minute part is never negative.
    """
    lifetimes = usage.get(CACHE_WRITE_LIFETIMES)
    split = (
        {key: _tokens(lifetimes.get(key)) for key in (CACHE_WRITE_5M_TOKENS, CACHE_WRITE_1H_TOKENS)}
        if isinstance(lifetimes, Mapping)
        else {}
    )
    total = _tokens(usage.get(CACHE_WRITE_TOKENS))
    if total is None:
        if all(count is None for count in split.values()):
            return None
        total = sum(count or 0 for count in split.values())
    one_hour = min(split.get(CACHE_WRITE_1H_TOKENS) or 0, total)
    five_minute = total - one_hour
    return (five_minute * price.cache_write + one_hour * price.cache_write_1h) / PER_TOKENS


def _tokens(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = [
    "COST_CHECK",
    "COST_TOLERANCE_ABS",
    "COST_TOLERANCE_REL",
    "PRICES",
    "PRICE_TABLE_VERSION",
    "REPORTED_COST_USD",
    "CostCheck",
    "ModelPrice",
    "cost_attributes",
    "cost_check",
    "cost_tolerance",
    "cost_usd",
    "price_for",
]
