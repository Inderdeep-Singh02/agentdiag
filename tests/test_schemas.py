"""The committed JSON Schemas are the ones the models generate (D2, schema gate)."""

from __future__ import annotations

from pathlib import Path

from agentdiag.schemas import SCHEMAS, write_schemas

COMMITTED = Path(__file__).resolve().parents[1] / "schemas"


def test_every_persisted_model_has_a_committed_schema() -> None:
    for name in SCHEMAS:
        assert (COMMITTED / f"{name}.schema.json").exists(), "run `python -m agentdiag.schemas`"


def test_the_committed_schemas_match_the_models_that_generate_them(tmp_path: Path) -> None:
    for path in write_schemas(tmp_path):
        committed = COMMITTED / path.name
        assert committed.read_bytes() == path.read_bytes(), (
            f"{committed.name} is stale; run `python -m agentdiag.schemas`"
        )


def test_the_committed_schemas_are_the_only_ones_in_the_directory() -> None:
    on_disk = {path.name for path in COMMITTED.glob("*.schema.json")}
    assert on_disk == {f"{name}.schema.json" for name in SCHEMAS}
