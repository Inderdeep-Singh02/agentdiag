"""Eval parameters: the Manifest's data for Evals, never prose for the Judge (ticket 10,
phase-6 decision 17).

Two consumers. `eval_parameters.latency` is the default threshold of the three latency
Evals when a declaration writes none, recorded in `run.json` with `from_manifest: true`
so a moved default is a visible difference; `eval_parameters.tool_argument_types` is a
Target-level screen every Scenario inherits, as `forbidden_phrases` is, scored by the
mechanical `tool_argument_types` over the committed Trace fixtures here.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml
from typer.testing import CliRunner

from agentdiag.cli import app
from agentdiag.eval.registry import REGISTRY
from agentdiag.eval.score import Score
from agentdiag.eval.spec import EvalContext
from agentdiag.eval.tools import TOOL_ARGUMENT_TYPES, json_type
from agentdiag.scenario.load import load_suite
from agentdiag.scenario.models import EvalDeclaration, Scenario
from agentdiag.trace import project_spans, read_trace, resolve_blobs

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
TRACES = REPO / "tests" / "fixtures" / "traces"
RECORDING = REPO / "tests" / "fixtures" / "recordings" / "toy-orders.jsonl"
SUITE = EXAMPLE / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "orders.yaml"

runner = CliRunner()


def scored(trace: str, types: dict[str, dict[str, str]], **fields: Any) -> Score:
    suite, _ = load_suite(SUITE)
    scenario: Scenario = suite.scenarios[0]
    events = resolve_blobs(read_trace(TRACES / f"{trace}.trace.jsonl"))
    context = EvalContext(
        scenario=scenario,
        declaration=EvalDeclaration(eval="tool_argument_types", params={"types": types}),
        events=events,
        spans=project_spans(events),
        fidelity=fields.get("fidelity", "instrumented"),
        forbidden_phrases=None,
        tool_kinds={},
    )
    assert TOOL_ARGUMENT_TYPES.perform is not None
    return TOOL_ARGUMENT_TYPES.perform(context)


def toy_root(tmp_path: Path, **blocks: object) -> Path:
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    path = root / ".agentdiag" / "targets" / "toy-order-desk" / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest.update(blocks)
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return root


# --- tool_argument_types ---


def test_calls_whose_typed_arguments_have_their_types_pass_citing_the_calls() -> None:
    score = scored("lookup-then-cancel", {"lookup_order": {"order_id": "string"}})

    assert score.verdict == "pass", score.rationale
    assert score.evidence and all(span.startswith("retrieval-") for span in score.evidence)


def test_a_call_carrying_the_wrong_type_fails_naming_the_argument_and_the_type_seen() -> None:
    score = scored("lookup-then-cancel", {"cancel_order": {"order_id": "integer"}})

    assert score.verdict == "fail"
    assert "cancel_order.order_id is string, declared integer" in score.rationale
    assert score.evidence and all(span.startswith("tool_call-") for span in score.evidence)


def test_arguments_the_adapter_did_not_observe_are_unverifiable_never_a_pass() -> None:
    score = scored("lookup-with-unseen-arguments", {"lookup_order": {"order_id": "string"}})

    assert (score.verdict, score.reason) == ("unverifiable", "evidence_missing")
    assert score.evidence


def test_a_typed_tool_never_called_is_not_a_fail() -> None:
    score = scored("greeting-calls-no-tool", {"cancel_order": {"order_id": "string"}})

    assert score.verdict == "pass"
    assert "made no call to a typed tool" in score.rationale


def test_below_reconstructed_the_screen_cannot_ground_a_verdict() -> None:
    assert REGISTRY["tool_argument_types"].min_fidelity == "reconstructed"
    assert not REGISTRY["tool_argument_types"].tool_family


def test_json_types_are_json_schemas_names_and_a_bool_is_never_an_integer() -> None:
    assert [json_type(v) for v in ("a", 1, 1.5, True, None, [], {})] == [
        "string",
        "integer",
        "number",
        "boolean",
        "null",
        "array",
        "object",
    ]


def test_every_scenario_inherits_the_screen_when_the_manifest_declares_types(
    tmp_path: Path,
) -> None:
    root = toy_root(
        tmp_path, eval_parameters={"tool_argument_types": {"lookup_order": {"order_id": "string"}}}
    )

    result = runner.invoke(
        app,
        [
            "run",
            "--root",
            str(root),
            "--scenario",
            "where-is-shipped-order",
            "--replay",
            str(RECORDING),
        ],
    )

    assert "tool_argument_types  pass" in result.stdout, result.output
    (run_dir,) = (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir()
    record = json.loads((run_dir / "run.json").read_text())
    (summary,) = record["scenarios"]
    inherited = [e for e in summary["evals"] if e["eval"] == "tool_argument_types"]
    assert inherited == [
        {
            "eval": "tool_argument_types",
            "id": None,
            "inherited": True,
            "params": {"types": {"lookup_order": {"order_id": "string"}}},
            "threshold": None,
        }
    ]


# --- eval_parameters.latency ---


def test_a_latency_declaration_without_a_threshold_is_held_to_the_manifests_default(
    tmp_path: Path,
) -> None:
    root = toy_root(tmp_path, eval_parameters={"latency": {"response_max_ms": 30000}})
    suite_path = root / ".agentdiag" / "targets" / "toy-order-desk" / "suites" / "orders.yaml"
    suite = yaml.safe_load(suite_path.read_text(encoding="utf-8"))
    shipped = next(s for s in suite["scenarios"] if s["id"] == "where-is-shipped-order")
    shipped["evals"] = [
        "response_latency"
        if isinstance(declaration, dict) and "response_latency" in declaration
        else declaration
        for declaration in shipped["evals"]
    ]
    suite_path.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "run",
            "--root",
            str(root),
            "--scenario",
            "where-is-shipped-order",
            "--replay",
            str(RECORDING),
        ],
    )

    assert result.exit_code in {0, 1}, result.output
    (run_dir,) = (root / ".agentdiag" / "targets" / "toy-order-desk" / "runs").iterdir()
    record = json.loads((run_dir / "run.json").read_text())
    (latency,) = [e for e in record["scenarios"][0]["evals"] if e["eval"] == "response_latency"]
    assert latency["threshold"] == {"max_ms": 30000, "from_manifest": True}
    scores = json.loads(
        (run_dir / "trials" / "where-is-shipped-order" / "1" / "scores.json").read_text()
    )["scores"]
    (score,) = [s for s in scores if s["eval"] == "response_latency"]
    assert score["threshold"] == {"max_ms": 30000}
    assert score["verdict"] == "pass"
