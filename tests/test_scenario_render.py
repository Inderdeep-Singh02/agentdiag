"""`render_suite` is the one canonical spelling of a Suite: a rendered Suite loads back to
the Suite it was rendered from, and rendering that again changes nothing. `generate` writes
through the same document builder (`render_commented_suite`), so this is what keeps a
generated Suite reading back as the Suite the generator built."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentdiag.scenario.load import load_suite
from agentdiag.scenario.render import render_commented_suite, render_suite

SUITES = Path(__file__).resolve().parent / "fixtures" / "suites"
ACCEPTED = sorted(SUITES.glob("*.pass.yaml")) + sorted(SUITES.glob("*.pass.json"))


@pytest.mark.parametrize("path", ACCEPTED, ids=lambda path: path.name)
def test_a_rendered_suite_loads_back_to_itself_and_renders_the_same(
    path: Path, tmp_path: Path
) -> None:
    suite, _ = load_suite(path)
    rendered = render_suite(suite)

    written = tmp_path / "rendered.yaml"
    written.write_text(rendered, encoding="utf-8")
    reloaded, _ = load_suite(written)

    assert reloaded.model_dump() == suite.model_dump()
    assert render_suite(reloaded) == rendered


def test_a_commented_suite_loads_back_to_the_same_suite(tmp_path: Path) -> None:
    suite, _ = load_suite(SUITES / "authored-kind.pass.yaml")
    above = [f"from scenario {n}" for n in range(len(suite.scenarios))]
    rendered = render_commented_suite(suite, header="# generated\n", above=above)

    written = tmp_path / "commented.yaml"
    written.write_text(rendered, encoding="utf-8")
    reloaded, _ = load_suite(written)

    assert reloaded.model_dump() == suite.model_dump()
    assert "  # from scenario 0\n" in rendered
