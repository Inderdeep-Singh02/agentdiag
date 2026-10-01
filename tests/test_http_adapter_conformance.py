"""Seam 3: the conformance suite against the HTTP Adapter, over the fake HTTP Target in both
core Dialects (ticket 17, phase-8 decision 10).

`AdapterConformance` is unchanged: the HTTP Adapter must exhibit everything the in-process
one does (D9). The fake reports `search_articles` on every Turn with no arguments, which is
how the unknown-arguments behaviour is exercised here, as the in-process suite exercises it
with a tool declared `*args`: the stream said a tool ran and did not say with what. A
Fixture reaches it through the `header` identity mode; the Adapter that cannot apply one is
the same environment with no `identity` block.

Nothing but the loopback is reached: each class serves its own fake for its tests.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, ClassVar

import pytest

from agentdiag.adapter.base import Adapter
from agentdiag.adapter.conformance import AdapterConformance
from agentdiag.adapter.http import HttpAdapter
from agentdiag.trace import TraceWriter
from agentdiag.types import ToolKind
from tests.fakes.http_target import DialectName, FakeHttpTarget, Reply, Tool, serving_target

TOOL = "search_articles"
SCRIPT = (Reply(text="Here is what I found.", tools=(Tool(TOOL),), conversation="conv-1"),)

_traces = itertools.count(1)


def http_config(
    base_url: str,
    dialect: str,
    *,
    side_effects: str = "none",
    identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    block: dict[str, Any] = {"base_url": base_url, "dialect": dialect}
    if identity is not None:
        block["identity"] = dict(identity)
    return {
        "kind": "http",
        "side_effects": side_effects,
        "environments": {"default": "dev", "dev": block},
    }


class HttpConformance(AdapterConformance):
    """The suite over one Dialect; a subclass names which."""

    dialect: ClassVar[DialectName]
    fake: ClassVar[FakeHttpTarget]

    @pytest.fixture(autouse=True, scope="class")
    @classmethod
    def _serving(cls) -> Iterator[None]:
        with serving_target(cls.dialect, script=SCRIPT) as fake:
            cls.fake = fake
            yield

    def make_adapter(
        self,
        tmp_path: Path,
        *,
        side_effects: str = "none",
        tool_kinds: Mapping[str, ToolKind] | None = None,
    ) -> Adapter:
        config = http_config(
            self.fake.base_url,
            self.dialect,
            side_effects=side_effects,
            identity={"mode": "header", "header": "x-account-id", "from": "id"},
        )
        return HttpAdapter(config, environment="dev", tool_kinds=tool_kinds)

    def make_adapter_that_cannot_apply_fixtures(self, tmp_path: Path) -> Adapter:
        return HttpAdapter(http_config(self.fake.base_url, self.dialect), environment="dev")

    def make_trace(self, tmp_path: Path) -> TraceWriter:
        return TraceWriter(tmp_path / f"trace-{next(_traces)}.jsonl")

    def a_tool(self) -> str:
        return TOOL


class TestHttpAdapterConformanceJson(HttpConformance):
    dialect = "json"


class TestHttpAdapterConformanceSseJson(HttpConformance):
    dialect = "sse-json"
