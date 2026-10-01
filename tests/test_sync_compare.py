"""Sync's three hashes per section, and the grammar of a section (ticket 10, decisions 10, 12).

Every row of decision 12's table has a fixture Fingerprint under `tests/fixtures/
fingerprints/`, the record R that row compares against; L and D are given here as the
local file and the deployed side would hash. The section grammar and the canonical text are
asserted on prose, since a Fingerprint that moved on a trailing space would break Sync for
nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentdiag.sync.compare import (
    RECORD_IS_STALE,
    SectionState,
    compare_sections,
    counts_line,
    render_sync,
    resynced_line,
    status_text,
    sync_result,
    sync_status,
)
from agentdiag.sync.fingerprint import Fingerprint, NotCovered
from agentdiag.sync.sections import (
    Hashed,
    canonical_json,
    canonical_text,
    prompt_sections,
    sha256,
    split_sections,
)
from agentdiag.types import SectionKind

FINGERPRINTS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "fingerprints"

ONE, TWO, THREE = (sha256(f"{word}\n") for word in ("one", "two", "three"))


def recorded(name: str) -> Fingerprint:
    return Fingerprint.model_validate_json((FINGERPRINTS / f"{name}.json").read_text())


def hashed(value: str, kind: SectionKind = "prompt") -> dict[str, Hashed]:
    return {"x": Hashed(kind=kind, sha256=value)}


def one_state(
    name: str,
    section: str,
    kind: SectionKind,
    *,
    local: str | None = None,
    deployed: str | None = None,
    not_covered: list[NotCovered] | None = None,
) -> SectionState:
    states = compare_sections(
        {section: Hashed(kind=kind, sha256=local)} if local else {},
        {section: Hashed(kind=kind, sha256=deployed)} if deployed else {},
        recorded(name),
        not_covered=not_covered or [],
    )
    (state,) = states
    return state


# --- decision 12's table, one fixture Fingerprint per row ---


ROWS = [
    # name, section, kind, L, D, direction, change
    ("identical", "prompt.system", "prompt", ONE, ONE, "identical", None),
    ("local-ahead", "prompt.system", "prompt", TWO, ONE, "local_ahead", "changed"),
    ("deployed-ahead", "prompt.system", "prompt", ONE, TWO, "deployed_ahead", "changed"),
    ("diverged", "prompt.system", "prompt", TWO, THREE, "diverged", "changed"),
    ("local-only-identical", "data_source.orders", "data_source", ONE, None, "identical", None),
    ("local-only-ahead", "data_source.orders", "data_source", TWO, None, "local_ahead", "changed"),
    ("deployed-only-identical", "model", "model", None, ONE, "identical", None),
    ("deployed-only-ahead", "model", "model", None, TWO, "deployed_ahead", "changed"),
]


@pytest.mark.parametrize(
    ("name", "section", "kind", "local", "deployed", "direction", "change"), ROWS
)
def test_every_row_of_the_table_gives_its_direction_and_change(
    name: str,
    section: str,
    kind: SectionKind,
    local: str | None,
    deployed: str | None,
    direction: str,
    change: str | None,
) -> None:
    state = one_state(name, section, kind, local=local, deployed=deployed)

    assert (state.direction, state.change) == (direction, change)
    assert (state.local, state.deployed, state.recorded) == (local, deployed, ONE)
    assert state.deployed_by == ("adapter" if deployed else None)


def test_a_diverged_section_whose_two_sides_agree_says_the_record_is_stale() -> None:
    state = one_state("diverged-record-stale", "prompt.system", "prompt", local=TWO, deployed=TWO)

    assert (state.direction, state.note) == ("diverged", RECORD_IS_STALE)
    assert one_state("diverged", "prompt.system", "prompt", local=TWO, deployed=THREE).note is None


def test_a_section_neither_side_knows_is_not_covered_with_its_reason_even_when_recorded() -> None:
    reason = "no Adapter observation and no Connector for observed pointer prompts.system"
    state = one_state(
        "not-covered",
        "prompt.system",
        "prompt",
        not_covered=[NotCovered(id="prompt.system", kind="prompt", reason=reason)],
    )

    assert (state.direction, state.change, state.note) == ("not_covered", None, reason)
    assert state.recorded == ONE


def test_a_section_the_record_lacks_is_added_on_the_side_that_has_it() -> None:
    deployed = one_state("added", "tool.cancel_order", "tool", deployed=ONE)
    local = one_state("added", "prompt.rules", "prompt", local=ONE)
    both = one_state("added", "prompt.rules", "prompt", local=ONE, deployed=ONE)

    assert (deployed.direction, deployed.change) == ("deployed_ahead", "added")
    assert (local.direction, local.change) == ("local_ahead", "added")
    assert (both.direction, both.change, both.note) == ("deployed_ahead", "added", None)


def test_a_recorded_section_neither_side_has_now_is_removed_on_the_side_it_came_from() -> None:
    local = one_state("removed-local", "prompt.rules#rule-3", "prompt")
    deployed = one_state("removed-deployed", "tool.cancel_order", "tool")

    assert (local.direction, local.change) == ("local_ahead", "removed")
    assert (deployed.direction, deployed.change) == ("deployed_ahead", "removed")


def test_a_not_covered_prompt_covers_every_recorded_heading_section_of_it() -> None:
    """An Adapter that could not observe this time is not evidence the section went away."""
    reason = "the Adapter's probe failed: boom"
    states = compare_sections(
        {},
        {},
        recorded("removed-local"),
        not_covered=[NotCovered(id="prompt.rules", kind="prompt", reason=reason)],
    )

    assert [(state.id, state.direction, state.note) for state in states] == [
        ("prompt.rules", "not_covered", reason),
        ("prompt.rules#rule-3", "not_covered", reason),
    ]
    assert states[1].recorded == ONE


# --- the Run-level status ---


def test_held_when_every_section_is_identical_or_not_covered() -> None:
    record = recorded("identical")
    sections = [
        *compare_sections(hashed(ONE), {}, None),
        SectionState(id="y", kind="tool", direction="not_covered"),
    ]
    identical = [state.model_copy(update={"direction": "identical"}) for state in sections]

    assert sync_status(identical, record) == ("held", None)


def test_broken_when_any_section_is_ahead_or_diverged() -> None:
    for direction in ("local_ahead", "deployed_ahead", "diverged"):
        sections = [SectionState(id="x", kind="prompt", direction=direction)]  # type: ignore[arg-type]
        assert sync_status(sections, recorded("identical")) == ("broken", None)


def test_not_checked_reasons_are_no_fingerprint_and_adapter_cannot_observe() -> None:
    covered = [SectionState(id="x", kind="prompt", direction="deployed_ahead", change="added")]
    nothing = [SectionState(id="x", kind="prompt", direction="not_covered")]

    assert sync_status(covered, None) == ("not_checked", "no_fingerprint")
    assert sync_status(nothing, recorded("identical")) == ("not_checked", "adapter_cannot_observe")
    assert sync_status([], None) == ("not_checked", "adapter_cannot_observe")


# --- the terminal ---


def test_the_table_is_one_padded_line_per_section_under_its_header() -> None:
    record = recorded("deployed-ahead")
    sections = compare_sections(
        {"data_source.orders": Hashed(kind="data_source", sha256=ONE)},
        {
            "prompt.system": Hashed(kind="prompt", sha256=TWO),
            "model": Hashed(kind="model", sha256=ONE),
        },
        record,
        not_covered=[
            NotCovered(id="tool.cancel_order", kind="tool", reason="no Adapter observation")
        ],
    )
    result = sync_result(sections, record, "local")

    assert render_sync(result).splitlines() == [
        f"Sync broken against fingerprint {record.id[:8]} (built 2026-09-27T10:00:00Z, local)",
        "section             direction       change   covered",
        "data_source.orders  local_ahead     added    local",
        "model               deployed_ahead  added    adapter",
        "prompt.system       deployed_ahead  changed  adapter",
        "tool.cancel_order   not_covered     -        -        no Adapter observation",
        "1 section local_ahead, 2 deployed_ahead, 1 not covered",
    ]


def test_counts_name_the_first_direction_with_its_noun() -> None:
    sections = [
        SectionState(id=f"s{n}", kind="prompt", direction="identical") for n in range(2)
    ] + [SectionState(id="t", kind="tool", direction="deployed_ahead")]

    assert counts_line(sections) == "2 sections identical, 1 deployed_ahead"


def test_the_summary_and_show_lines_name_a_resync_and_its_sections() -> None:
    sync = {
        "status": "broken",
        "resynced_from": "3f9c0e1a" + "0" * 56,
        "sections": [{"id": "prompt.system", "direction": "deployed_ahead", "change": "changed"}],
    }

    assert status_text(sync) == "broken, re-synced from 3f9c0e1a: prompt.system deployed_ahead"
    assert resynced_line(sync) == (
        f"Re-synced from {'3f9c0e1a' + '0' * 56}: prompt.system deployed_ahead (changed)"
    )
    assert status_text({"status": "held"}) == "held"
    assert status_text({"status": "not_checked", "reason": "no_fingerprint"}) == (
        "not_checked (no_fingerprint)"
    )
    assert resynced_line({"status": "held"}) is None


# --- the grammar and the canonical text (decision 10) ---


def test_a_prompt_without_a_heading_is_one_section_named_for_the_pointer() -> None:
    assert list(prompt_sections("system", "You are the order desk.\n\n1. Look up first.")) == [
        "prompt.system"
    ]


def test_every_heading_level_starts_a_section_and_text_before_the_first_is_the_preamble() -> None:
    text = "Role line.\n# Rules\nbody\n## Rule 3: never invent\nthree\n### Rules\nagain\n"

    assert [(slug, title) for slug, title, _ in split_sections(text)] == [
        ("_preamble", None),
        ("rules", "Rules"),
        ("rule-3-never-invent", "Rule 3: never invent"),
        ("rules-2", "Rules"),
    ]
    assert list(prompt_sections("system", text)) == [
        "prompt.system#_preamble",
        "prompt.system#rules",
        "prompt.system#rule-3-never-invent",
        "prompt.system#rules-2",
    ]


def test_trailing_whitespace_and_crlf_do_not_change_a_sections_hash() -> None:
    unix = prompt_sections("rules", "# One\nfirst line\nsecond\n")
    windows = prompt_sections("rules", "# One\r\nfirst line   \r\nsecond\r\n\r\n")

    assert unix == windows
    assert canonical_text("a  \nb\n\n\n") == "a\nb\n"


def test_a_tool_schema_hashes_its_canonical_json_so_key_order_is_no_change() -> None:
    assert canonical_json({"b": 1, "a": {"d": 2, "c": 3}}) == '{"a":{"c":3,"d":2},"b":1}'


def test_a_fingerprints_id_is_its_sections_and_nothing_else() -> None:
    record = recorded("identical")
    later = record.model_copy(update={"built_at": "2026-10-01T00:00:00Z", "environment": "x"})

    assert later.id == record.id
    assert recorded("deployed-ahead").id == record.id
    assert recorded("added").id != record.id
