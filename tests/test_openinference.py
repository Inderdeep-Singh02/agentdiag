"""The vendored OpenInference cost names stay pinned to one upstream commit (ADR-0006 §4)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from agentdiag.trace.openinference import ATTRIBUTES, COST_TOTAL, PINNED_COMMIT, SPEC_URL

SNAPSHOT = Path(__file__).parent / "fixtures" / "openinference" / "cost-attributes-7feb0c4.txt"
SNAPSHOT_SHA256 = "d851144759fdd74972e3479b5b96240357cc4ef2dda06f06ca3b5b303a43d5c3"


def derive_cost_names(markdown: str) -> frozenset[str]:
    """Every distinct `llm.cost.*` name the conventions page names."""
    return frozenset(name.rstrip(".") for name in re.findall(r"llm\.cost\.[a-z0-9_.]+", markdown))


def read_snapshot() -> frozenset[str]:
    return frozenset(SNAPSHOT.read_text(encoding="utf-8").split())


def test_every_vendored_cost_name_is_in_the_pinned_snapshot() -> None:
    assert ATTRIBUTES.issubset(read_snapshot())


def test_the_snapshot_is_the_one_taken_at_the_pinned_commit() -> None:
    assert hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest() == SNAPSHOT_SHA256


def test_every_cost_name_agentdiag_writes_is_a_vendored_name() -> None:
    from agentdiag.model.prices import cost_attributes

    usage = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 2,
    }
    written = cost_attributes("claude-sonnet-5", usage)

    assert COST_TOTAL in written
    assert set(written).issubset(ATTRIBUTES)


def test_the_spec_url_names_the_pinned_commit() -> None:
    assert PINNED_COMMIT in SPEC_URL
    assert SPEC_URL.startswith("https://raw.githubusercontent.com/")


@pytest.mark.network
def test_refetching_the_pinned_commit_derives_the_same_cost_names() -> None:
    import urllib.request

    with urllib.request.urlopen(SPEC_URL, timeout=30) as response:
        markdown = response.read().decode("utf-8")
    assert derive_cost_names(markdown) == read_snapshot()
