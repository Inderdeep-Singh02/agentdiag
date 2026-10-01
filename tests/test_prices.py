"""The price table a model call is costed under (ADR-0006 §4, phase-5 decisions 4 and 5)."""

from __future__ import annotations

import pytest

from agentdiag.model.prices import (
    PRICE_TABLE_VERSION,
    PRICES,
    cost_attributes,
    cost_check,
    cost_usd,
    price_for,
)


def test_the_price_table_is_versioned_by_the_date_it_last_changed() -> None:
    """2026-06-24 was the reference the rates were taken from; 2026-09-23 added the 1-hour
    cache-write column after the live cost check (ticket 19). A Run costed under either
    says which in `run.json.price_table.version`, and `compare` diffs it."""
    assert PRICE_TABLE_VERSION == "2026-09-23"


@pytest.mark.parametrize("model", sorted(PRICES))
def test_every_model_in_the_table_is_priced_by_its_own_name(model: str) -> None:
    assert price_for(model) is PRICES[model]


CACHE_WRITE_CELLS: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (12.50, 20.00),
    "claude-fable-5": (12.50, 20.00),
    "claude-opus-5-5": (5.00, 8.00),
    "claude-opus-5": (6.25, 10.00),
    "claude-opus-4-8": (6.25, 10.00),
    "claude-opus-4-7": (6.25, 10.00),
    "claude-opus-4-6": (6.25, 10.00),
    "claude-sonnet-5": (2.50, 4.00),
    "claude-sonnet-4-6": (3.75, 6.00),
    "claude-haiku-4-5": (1.25, 2.00),
}
"""Decision 5's `cache_write` and `cache_write_1h` columns, USD per million tokens, spelled
as the contract's table spells them: the standard 1.25 x input for the five-minute lifetime
and 2 x input for the one-hour lifetime. `prices.py` derives them; a slip there fails here."""


def test_the_cache_write_cells_are_pinned_for_every_model_in_the_table() -> None:
    assert sorted(CACHE_WRITE_CELLS) == sorted(PRICES)


@pytest.mark.parametrize(("model", "cells"), sorted(CACHE_WRITE_CELLS.items()))
def test_a_rows_cache_write_cells_are_decision_fives(
    model: str, cells: tuple[float, float]
) -> None:
    assert (PRICES[model].cache_write, PRICES[model].cache_write_1h) == cells


def test_a_dated_alias_is_priced_as_the_model_it_is_a_release_of() -> None:
    assert price_for("claude-opus-5-20260401") is PRICES["claude-opus-5"]
    assert price_for("claude-sonnet-5-20260815") is PRICES["claude-sonnet-5"]


def test_a_dated_alias_does_not_fall_back_to_a_shorter_family_name() -> None:
    """`claude-opus-5-5-20260601` is Opus 5.5, never Opus 5: nothing else is guessed."""
    assert price_for("claude-opus-5-5-20260601") is PRICES["claude-opus-5-5"]


@pytest.mark.parametrize("model", ["claude-mystery-1", "gpt-9", "claude-opus", "", None])
def test_a_model_the_table_does_not_hold_is_unpriced_not_free(model: str | None) -> None:
    assert price_for(model) is None
    assert cost_usd(model, {"input_tokens": 1000, "output_tokens": 1000}) is None


def test_a_call_costs_each_token_kind_at_its_own_rate_to_six_decimals() -> None:
    usage = {
        "input_tokens": 742,
        "output_tokens": 61,
        "cache_read_input_tokens": 1000,
        "cache_creation_input_tokens": 2000,
    }
    # claude-sonnet-5: $2 in, $10 out, $0.20 cache read, $2.50 cache write, per million.
    expected = (742 * 2.00 + 61 * 10.00 + 1000 * 0.20 + 2000 * 2.50) / 1_000_000

    assert cost_usd("claude-sonnet-5", usage) == round(expected, 6)


def test_a_token_kind_the_usage_does_not_report_costs_nothing() -> None:
    assert cost_usd("claude-haiku-4-5", {"input_tokens": 1_000_000}) == 1.0


def test_the_cost_attributes_split_the_total_into_its_openinference_parts() -> None:
    usage = {
        "input_tokens": 100_000,
        "output_tokens": 10_000,
        "cache_read_input_tokens": 200_000,
        "cache_creation_input_tokens": 40_000,
    }

    # claude-opus-5: $5 in, $25 out, $0.50 cache read, $6.25 cache write, per million.
    assert cost_attributes("claude-opus-5", usage) == {
        "llm.cost.total": 1.1,
        "llm.cost.prompt": 0.85,
        "llm.cost.completion": 0.25,
        "llm.cost.prompt_details.input": 0.5,
        "llm.cost.prompt_details.cache_read": 0.1,
        "llm.cost.prompt_details.cache_write": 0.25,
    }


def test_a_prompt_part_the_usage_does_not_report_is_not_written() -> None:
    written = cost_attributes("claude-sonnet-5", {"input_tokens": 10, "output_tokens": 1})

    assert "llm.cost.prompt_details.cache_read" not in written
    assert "llm.cost.prompt_details.cache_write" not in written


# --- a cache write is priced by its lifetime when the usage says which (ticket 19, live) ---


@pytest.mark.parametrize(
    ("usage", "reported"),
    [
        (
            {
                "input_tokens": 2,
                "cache_creation_input_tokens": 3552,
                "cache_read_input_tokens": 0,
                "output_tokens": 969,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 0,
                    "ephemeral_1h_input_tokens": 3552,
                },
                "service_tier": "standard",
            },
            0.059755,
        ),
        (
            {
                "input_tokens": 2,
                "cache_creation_input_tokens": 3835,
                "cache_read_input_tokens": 0,
                "output_tokens": 1930,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 0,
                    "ephemeral_1h_input_tokens": 3835,
                },
                "service_tier": "standard",
            },
            0.086610,
        ),
    ],
    ids=["goal", "diagnosis"],
)
def test_a_one_hour_cache_write_costs_what_the_cli_reported_for_it(
    usage: dict[str, object], reported: float
) -> None:
    """The two live Judge calls of 2026-09-23 through Claude Code (`claude-opus-5`): the CLI
    writes its prompt cache with the 1-hour lifetime and reports `total_cost_usd`; priced
    at the 5-minute rate the table said `differs` by exactly the tokens x ($10 - $6.25)/M.
    Read from the `cache_creation` split, it agrees to six decimals."""
    assert cost_usd("claude-opus-5", usage) == reported
    assert cost_check(cost_usd("claude-opus-5", usage), reported) == "agrees"


def test_each_lifetime_of_a_split_cache_write_is_priced_at_its_own_rate() -> None:
    usage = {
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_creation_input_tokens": 2000,
        "cache_creation": {"ephemeral_5m_input_tokens": 1000, "ephemeral_1h_input_tokens": 1000},
    }

    # claude-sonnet-5: $2 in, $10 out, $2.50 a 5-minute write, $4.00 a 1-hour write.
    assert cost_attributes("claude-sonnet-5", usage) == {
        "llm.cost.total": 0.0068,
        "llm.cost.prompt": 0.0067,
        "llm.cost.completion": 0.0001,
        "llm.cost.prompt_details.input": 0.0002,
        "llm.cost.prompt_details.cache_write": 0.0065,
    }


def test_a_split_that_is_all_five_minute_costs_the_same_as_no_split() -> None:
    """The split refines the total; it never adds a part or changes the attribute names."""
    unsplit = {"input_tokens": 10, "output_tokens": 1, "cache_creation_input_tokens": 2000}
    split = unsplit | {
        "cache_creation": {"ephemeral_5m_input_tokens": 2000, "ephemeral_1h_input_tokens": 0}
    }

    assert cost_attributes("claude-sonnet-5", split) == cost_attributes("claude-sonnet-5", unsplit)


def test_a_cache_write_without_a_lifetime_split_is_priced_at_the_five_minute_rate() -> None:
    """The Messages API's default lifetime, and what every recording before 2026-09-23 has."""
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 1_000_000}

    assert cost_usd("claude-haiku-4-5", usage) == 1.25


def test_a_one_hour_count_above_the_total_is_capped_at_the_total() -> None:
    """Not something the API sends; the two counts contradict each other. The total is what
    the API billed, so the one-hour count is capped at it and the five-minute part is never
    negative."""
    usage = {
        "cache_creation_input_tokens": 1000,
        "cache_creation": {"ephemeral_1h_input_tokens": 3000},
    }

    # claude-sonnet-5: 1000 one-hour tokens at $4.00, no five-minute tokens.
    written = cost_attributes("claude-sonnet-5", usage)

    assert written["llm.cost.prompt_details.cache_write"] == 0.004


def test_a_split_with_no_total_is_still_a_reported_cache_write() -> None:
    """Not something the API sends either; but the split is reported usage, so it is billed
    as it stands rather than dropped."""
    usage = {
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_creation": {"ephemeral_5m_input_tokens": 1000, "ephemeral_1h_input_tokens": 1000},
    }

    written = cost_attributes("claude-sonnet-5", usage)

    assert written["llm.cost.prompt_details.cache_write"] == 0.0065


def test_a_split_claiming_more_one_hour_tokens_than_the_total_is_capped_at_the_total() -> None:
    """The API never sends this; if it did, the five-minute part must not go negative."""
    usage = {
        "cache_creation_input_tokens": 1000,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 3000},
    }

    # claude-haiku-4-5: $2.00 a one-hour write per million; 1000 tokens, every one of them.
    assert cost_usd("claude-haiku-4-5", usage) == 0.002


def test_a_split_without_a_total_is_the_report_of_its_own_two_parts() -> None:
    """The API always sends the total; a split alone still reports a cache write, so it is
    priced rather than dropped."""
    usage = {
        "cache_creation": {"ephemeral_5m_input_tokens": 1000, "ephemeral_1h_input_tokens": 1000}
    }

    # claude-haiku-4-5: $1.25 a five-minute write, $2.00 a one-hour write, per million.
    assert cost_usd("claude-haiku-4-5", usage) == 0.00325


def test_an_unpriced_model_gets_no_cost_attribute_at_all() -> None:
    assert cost_attributes("claude-mystery-1", {"input_tokens": 10}) == {}


# --- a reported cost is checked against the table, never used in its place (decision 27) ---


def test_a_reported_cost_within_half_a_percent_agrees() -> None:
    assert cost_check(0.021195, 0.0212) == "agrees"
    assert cost_check(1.0, 1.005) == "agrees"


def test_a_reported_cost_beyond_half_a_percent_differs() -> None:
    assert cost_check(1.0, 1.006) == "differs"
    assert cost_check(0.021195, 0.03) == "differs"


def test_tiny_costs_agree_within_two_millionths_of_a_dollar() -> None:
    """Six-decimal rounding on both sides: half a percent of a tiny cost is below it."""
    assert cost_check(0.000010, 0.0000115) == "agrees"
    assert cost_check(0.000010, 0.000013) == "differs"


def test_a_model_the_table_does_not_price_is_unpriced_whatever_was_reported() -> None:
    assert cost_check(None, 0.5) == "unpriced"
