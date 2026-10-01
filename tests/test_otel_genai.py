"""The vendored OTel GenAI attribute names stay pinned to one upstream commit."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from agentdiag.trace.otel_genai import ATTRIBUTES, PINNED_COMMIT, REGISTRY_URL

SNAPSHOT = Path(__file__).parent / "fixtures" / "otel_genai" / "attributes-8ffdf56.txt"
SNAPSHOT_SHA256 = "d8d16849586da46943ee0f68ff4f2c80ad0ec56717721e4e5aa357db1a7889cf"


def derive_attributes(markdown: str) -> frozenset[str]:
    """Every distinct attribute name the registry page names, plus `error.type`."""
    names = {name.rstrip(".") for name in re.findall(r"gen_ai\.[a-z0-9_.]+", markdown)}
    names.add("error.type")
    return frozenset(names)


def read_snapshot() -> frozenset[str]:
    return frozenset(SNAPSHOT.read_text(encoding="utf-8").split())


def test_every_vendored_attribute_name_is_in_the_pinned_snapshot() -> None:
    assert ATTRIBUTES.issubset(read_snapshot())


def test_the_snapshot_is_the_one_taken_at_the_pinned_commit() -> None:
    digest = hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest()
    assert digest == SNAPSHOT_SHA256


def test_every_token_attribute_the_projection_reads_is_a_vendored_name() -> None:
    """`Metrics` may only read usage attributes agentdiag has vendored."""
    from agentdiag.trace.spans import TOKEN_ATTRIBUTES

    assert set(TOKEN_ATTRIBUTES).issubset(ATTRIBUTES)


def test_the_registry_url_names_the_pinned_commit() -> None:
    assert PINNED_COMMIT in REGISTRY_URL
    assert REGISTRY_URL.startswith("https://raw.githubusercontent.com/")


@pytest.mark.network
def test_refetching_the_pinned_commit_derives_the_same_attribute_names() -> None:
    import urllib.request

    with urllib.request.urlopen(REGISTRY_URL, timeout=30) as response:
        markdown = response.read().decode("utf-8")
    assert derive_attributes(markdown) == read_snapshot()
