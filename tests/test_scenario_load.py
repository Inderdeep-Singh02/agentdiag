"""The Suite loader: one Suite from either format, the full schema, inheritance (D17-D19).

Error and warning *reporting* is tested at seam 1 (`test_validate_cli.py`) over committed
fixtures; this file holds what the loaded model must say, which only the model can show.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agentdiag.scenario import SimulateTurn, SuiteLoadError, load_suite

SUITE = {
    "schema_version": 1,
    "target": "toy-order-desk",
    "description": "What the order desk must do when a customer asks to cancel.",
    "scenarios": [
        {
            "id": "cancel-processing-order",
            "title": "Cancel an order that is still processing",
            "tags": ["orders", "cancel"],
            "turns": ["Hi, I'd like to cancel order NB-1042."],
            "evals": [{"eval": "prompt_adherence"}],
        }
    ],
}


def write_yaml(path: Path, document: dict) -> Path:
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def write_json(path: Path, document: dict) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_yaml_and_json_load_into_the_same_suite(tmp_path: Path) -> None:
    """The format is the author's business; the loaded Suite is not."""
    from_yaml, _ = load_suite(write_yaml(tmp_path / "orders.yaml", SUITE))
    from_json, _ = load_suite(write_json(tmp_path / "orders.json", SUITE))

    assert from_yaml == from_json


def test_a_suite_loads_its_scenario_with_its_id_title_tags_and_literal_turns(
    tmp_path: Path,
) -> None:
    suite, _ = load_suite(write_yaml(tmp_path / "orders.yaml", SUITE))

    scenario = suite.scenarios[0]
    assert suite.target == "toy-order-desk"
    assert scenario.id == "cancel-processing-order"
    assert scenario.title == "Cancel an order that is still processing"
    assert scenario.tags == ["orders", "cancel"]
    assert scenario.turns == ["Hi, I'd like to cancel order NB-1042."]


def test_an_eval_declaration_loads_from_the_long_form_and_the_bare_name(tmp_path: Path) -> None:
    """`- eval: x` and `- x` are the same declaration written two ways (D19)."""
    document = json.loads(json.dumps(SUITE))
    document["scenarios"][0]["evals"] = ["prompt_adherence"]

    short, _ = load_suite(write_yaml(tmp_path / "short.yaml", document))
    long, _ = load_suite(write_yaml(tmp_path / "long.yaml", SUITE))

    assert short.scenarios[0].evals == long.scenarios[0].evals
    assert short.scenarios[0].evals[0].eval == "prompt_adherence"


def test_an_unknown_eval_name_is_one_warning_naming_the_scenario_and_the_eval(
    tmp_path: Path,
) -> None:
    """The catalogue is open, so a name it does not hold is a warning, never an error."""
    document = json.loads(json.dumps(SUITE))
    document["scenarios"][0]["evals"] = [{"eval": "tone_of_voice"}]

    _, warnings = load_suite(write_yaml(tmp_path / "orders.yaml", document))

    assert len(warnings) == 1
    assert "tone_of_voice" in warnings[0]
    assert "cancel-processing-order" in warnings[0]


def test_a_registered_eval_name_produces_no_warning(tmp_path: Path) -> None:
    """`prompt_adherence` is in the catalogue, so the shipped example loads silently."""
    _, warnings = load_suite(write_yaml(tmp_path / "orders.yaml", SUITE))

    assert warnings == []


def test_a_file_that_is_neither_yaml_nor_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "orders.txt"
    path.write_text("scenarios: []", encoding="utf-8")

    with pytest.raises(SuiteLoadError):
        load_suite(path)


def load(tmp_path: Path, document: dict) -> object:
    suite, _ = load_suite(write_yaml(tmp_path / "suite.yaml", document))
    return suite


def with_scenarios(*scenarios: dict, **suite_fields: object) -> dict:
    return {
        "schema_version": 1,
        "target": "toy-order-desk",
        **suite_fields,
        "scenarios": list(scenarios),
    }


def scenario(identifier: str = "s", **fields: object) -> dict:
    return {"id": identifier, "title": identifier, "turns": ["hi"], **fields}


# --- Eval declarations: three spellings, one declaration (decision 1) ---


def test_the_bare_name_the_short_form_and_the_long_form_load_to_the_same_declaration(
    tmp_path: Path,
) -> None:
    short = load(tmp_path, with_scenarios(scenario(evals=[{"expect_tools": ["cancel_order"]}])))
    long = load(
        tmp_path,
        with_scenarios(
            scenario(evals=[{"eval": "expect_tools", "params": {"tools": ["cancel_order"]}}])
        ),
    )

    assert short == long
    assert short.scenarios[0].evals[0].params == {"tools": ["cancel_order"]}


def test_a_metric_short_form_value_binds_to_its_threshold(tmp_path: Path) -> None:
    suite = load(tmp_path, with_scenarios(scenario(evals=[{"response_latency": {"max_ms": 900}}])))

    declaration = suite.scenarios[0].evals[0]
    assert declaration.threshold == {"max_ms": 900}
    assert declaration.params == {}


def test_a_short_form_mapping_that_names_the_primary_parameter_is_the_parameters(
    tmp_path: Path,
) -> None:
    """How a restricted text Eval is written short (decision 7)."""
    suite = load(
        tmp_path,
        with_scenarios(scenario(evals=[{"must_say_any": {"phrases": ["cancelled"], "turn": 1}}])),
    )

    assert suite.scenarios[0].evals[0].params == {"phrases": ["cancelled"], "turn": 1}


def test_an_unregistered_short_form_keeps_its_value_under_params_value(tmp_path: Path) -> None:
    suite, warnings = load_suite(
        write_yaml(
            tmp_path / "suite.yaml", with_scenarios(scenario(evals=[{"tone_of_voice": "warm"}]))
        )
    )

    assert suite.scenarios[0].evals[0].params == {"value": "warm"}
    assert len(warnings) == 2  # the unknown name, and the value nothing can bind


def test_a_judge_override_and_an_id_ride_on_the_long_form(tmp_path: Path) -> None:
    declaration = {
        "eval": "goal",
        "id": "cancelled",
        "params": {"expected": "The order is cancelled."},
        "judge": {"model": "claude-opus-5-5", "effort": "high"},
    }
    suite = load(tmp_path, with_scenarios(scenario(evals=[declaration])))

    loaded = suite.scenarios[0].evals[0]
    assert loaded.id == "cancelled"
    assert loaded.judge is not None and loaded.judge.model == "claude-opus-5-5"


# --- inheritance and replacement (decision 17) ---


def test_every_scenario_inherits_the_suite_level_evals_marked_as_inherited(tmp_path: Path) -> None:
    suite = load(
        tmp_path,
        with_scenarios(
            scenario("a", evals=[{"must_not_say": ["refund"]}]),
            scenario("b"),
            evals=["prompt_adherence"],
        ),
    )

    assert [(d.eval, d.inherited) for d in suite.scenarios[0].evals] == [
        ("must_not_say", False),
        ("prompt_adherence", True),
    ]
    assert [(d.eval, d.inherited) for d in suite.scenarios[1].evals] == [("prompt_adherence", True)]


def test_a_scenario_that_opts_out_inherits_nothing(tmp_path: Path) -> None:
    suite = load(
        tmp_path,
        with_scenarios(scenario(inherit_suite_evals=False), evals=["prompt_adherence"]),
    )

    assert suite.scenarios[0].evals == []


def test_a_scenario_declaration_with_the_same_eval_and_id_replaces_the_inherited_one(
    tmp_path: Path,
) -> None:
    suite = load(
        tmp_path,
        with_scenarios(
            scenario(
                evals=[{"eval": "response_latency", "id": "fast", "threshold": {"max_ms": 1}}]
            ),
            evals=[
                {"eval": "response_latency", "id": "fast", "threshold": {"max_ms": 5000}},
                {"eval": "response_latency", "id": "first-token", "threshold": {"max_ms": 500}},
            ],
        ),
    )

    evals = suite.scenarios[0].evals
    assert [(d.id, d.threshold, d.inherited) for d in evals] == [
        ("fast", {"max_ms": 1}, False),
        ("first-token", {"max_ms": 500}, True),
    ]


def test_a_scenario_level_guardrails_picks_rules_and_replaces_the_suite_level_one(
    tmp_path: Path,
) -> None:
    """Decision 16: Suite-level rules plus a Scenario's guardrails declaration."""
    rules = [{"id": "G1", "rule": "one"}, {"id": "G2", "rule": "two"}]
    suite = load(
        tmp_path,
        with_scenarios(
            scenario("picks", evals=[{"guardrails": ["G2"]}]),
            scenario("inherits"),
            evals=[{"guardrails": {"rules": rules}}],
        ),
    )

    picks, inherits = suite.scenarios
    assert [(d.eval, d.params, d.inherited) for d in picks.evals] == [
        ("guardrails", {"rules": ["G2"]}, False)
    ]
    assert [(d.eval, d.inherited) for d in inherits.evals] == [("guardrails", True)]
    assert sorted(suite.guardrail_rules()) == ["G1", "G2"]


def test_focus_on_an_eval_the_scenario_opted_out_of_is_an_error(tmp_path: Path) -> None:
    document = with_scenarios(
        scenario(inherit_suite_evals=False, focus="kept"),
        evals=[{"eval": "prompt_adherence", "id": "kept"}],
    )

    with pytest.raises(SuiteLoadError) as raised:
        load(tmp_path, document)

    assert "focuses on 'kept'" in str(raised.value)


# --- the derived kinds (decision 2) ---


@pytest.mark.parametrize(
    ("fields", "kinds"),
    [
        ({"turns": ["hi"]}, {"one_shot"}),
        ({"turns": ["hi", "thanks"]}, {"conversation"}),
        (
            {
                "turns": ["hi", {"simulate": {"goal": "cancel", "stop_token": "[DONE]"}}],
                "max_turns": 4,
            },
            {"simulated"},
        ),
        ({"turns": ["hi"], "evals": [{"forbid_tools": ["cancel_order"]}]}, {"one_shot", "tool"}),
        ({"turns": ["hi"], "evals": ["prompt_adherence"]}, {"one_shot"}),
    ],
)
def test_the_kinds_are_derived_from_the_turns_and_the_evals(
    tmp_path: Path, fields: dict, kinds: set
) -> None:
    suite = load(tmp_path, with_scenarios({"id": "s", "title": "s", **fields}))

    assert suite.scenarios[0].kinds == kinds


def test_an_inherited_tool_eval_makes_every_scenario_a_tool_scenario(tmp_path: Path) -> None:
    suite = load(tmp_path, with_scenarios(scenario(), evals=[{"forbid_tools": ["refund"]}]))

    assert "tool" in suite.scenarios[0].kinds


# --- the rest of D18 loads as written ---


def test_a_simulate_turn_loads_with_everything_the_simulated_user_is_told(tmp_path: Path) -> None:
    simulate = {
        "goal": "Get NB-1042 cancelled.",
        "persona": "Terse.",
        "known_facts": {"order_id": "NB-1042"},
        "unknown_facts": {"status": "processing"},
        "hints": ["Give the order number when asked."],
        "stop_when": {"tool_called": "cancel_order"},
    }
    suite = load(
        tmp_path,
        with_scenarios(scenario(turns=["hi", {"simulate": simulate}], max_turns=4)),
    )

    turn = suite.scenarios[0].turns[1]
    assert isinstance(turn, SimulateTurn)
    assert turn.simulate.known_facts == {"order_id": "NB-1042"}
    assert turn.simulate.stop_when is not None
    assert turn.simulate.stop_when.tool_called == "cancel_order"


def test_ground_truth_provenance_notes_and_extras_are_carried_as_written(tmp_path: Path) -> None:
    suite = load(
        tmp_path,
        with_scenarios(
            scenario(
                provenance="chat 33457e85",
                notes="A pass does not prove the prod bug is fixed.",
                ground_truth={"status": "cancelled", "refund": False},
                extras={"inject_identity": "discreet_header"},
            ),
            extras={"channel": "whatsapp"},
        ),
    )

    loaded = suite.scenarios[0]
    assert loaded.ground_truth == {"status": "cancelled", "refund": False}
    assert loaded.provenance == "chat 33457e85"
    assert loaded.extras == {"inject_identity": "discreet_header"}
    assert suite.extras == {"channel": "whatsapp"}


def test_a_fixture_is_authored_flat_and_loads_as_its_kind_and_data(tmp_path: Path) -> None:
    suite = load(
        tmp_path,
        with_scenarios(
            scenario(fixtures=["carmen", "orders"]),
            fixtures={
                "carmen": {"kind": "identity", "name": "Carmen"},
                "orders": {"rows": [1, 2]},
            },
        ),
    )

    assert suite.fixtures["carmen"].kind == "identity"
    assert suite.fixtures["carmen"].data == {"name": "Carmen"}
    assert suite.fixtures["orders"].kind == "data"


def test_a_load_error_lists_every_problem_not_just_the_first(tmp_path: Path) -> None:
    document = with_scenarios(
        scenario("a", fixtures=["missing"]), scenario("a"), not_run={"nowhere": "x"}
    )

    with pytest.raises(SuiteLoadError) as raised:
        load(tmp_path, document)

    message = str(raised.value)
    assert "duplicate Scenario id 'a'" in message
    assert "names Fixture 'missing'" in message
    assert "not_run names 'nowhere'" in message
