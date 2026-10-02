"""Seam 1: `init --target <slug>` writes an Adapter of kind pending, never the toy (ticket
46, ADR-0016 §4).

A Target described before it is connected: who it is from `--name`, `--description`,
`--family` and `--channel`, an Adapter of the core kind `pending`, the Connector and the
prompt pointers as comments, each hole under a `# REVIEW:` line in the shape `discover`
writes, and a draft sample Suite. It validates with warnings and no errors, and every
command that would converse with it refuses by name. The toy stays the first `init`'s
scaffold and `--adapter toy` writes it under any slug; the name flags apply to every
scaffold. Every assertion is on a file `init` wrote, a line a command printed, or an exit
code.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import Result
from typer.testing import CliRunner

from agentdiag.cli import app
from tests.fakes.workspace import manifest_of, suite_of, target_dir

REPO = Path(__file__).resolve().parents[1]
ORDER_DESK = REPO / "examples" / "workspace" / ".agentdiag" / "targets" / "order-desk"
IDLE_ADAPTER = "python:tests.fakes.idle_target:make_target"

PENDING_LINE = (
    "adapter.kind: pending: nothing drives this Target yet; set the Adapter kind and its "
    "environment block (the REVIEW lines in manifest.yaml name what to fill)"
)
"""The one spelling `validate`, `run` and `sync` share (ADR-0016 §4, decision 12)."""

runner = CliRunner()


NO_RUNNABLE_LINE = (
    "suites: no runnable Suite: suites/sample.yaml is draft; settle its Scenarios and drop "
    "status: draft from its entry to run them"
)
"""What `validate` warns and a Run that drives refuses on (the ticket 46 amendment)."""


def invoke(*arguments: str | Path) -> Result:
    return runner.invoke(app, [str(argument) for argument in arguments])


def output(result: Result) -> str:
    return result.stdout + result.stderr


def manifest_text(root: Path, slug: str) -> str:
    return (target_dir(root, slug) / "manifest.yaml").read_text(encoding="utf-8")


def reviewed(text: str) -> list[str]:
    """The line under each `# REVIEW:` line, stripped, after checking the REVIEW line sits
    on its own line at the indentation of the line it is above (as `discover` writes it)."""
    lines = text.splitlines()
    under: list[str] = []
    for at, line in enumerate(lines):
        if "# REVIEW:" not in line:
            continue
        assert line.lstrip().startswith("# REVIEW: "), line
        below = lines[at + 1]
        indent = len(line) - len(line.lstrip())
        assert len(below) - len(below.lstrip()) == indent, (line, below)
        under.append(below.strip())
    return under


def described(root: Path, *flags: str) -> Result:
    """`init --target a --name A --family f --channel chat`, plus `flags`."""
    result = invoke(
        "init",
        "--root",
        root,
        "--target",
        "a",
        "--name",
        "A",
        "--family",
        "f",
        "--channel",
        "chat",
        *flags,
    )
    assert result.exit_code == 0, output(result)
    return result


# --- what the pending scaffold writes ---


def test_init_target_writes_who_the_target_is_a_pending_adapter_and_a_draft_suite(
    tmp_path: Path,
) -> None:
    described(tmp_path)

    document = manifest_of(tmp_path, "a")

    assert document["target"]["name"] == "A"
    assert document["family"] == "f"
    assert document["channel"] == "chat"
    assert document["adapter"] == {
        "kind": "pending",
        "side_effects": "none",
        "environments": {"default": "dev", "dev": {}},
    }
    assert document["suites"] == [{"path": "suites/sample.yaml", "status": "draft"}]
    assert document["judge_notes"] == "judge_notes.md"
    for absent in ("prompts", "tools", "connector"):
        assert absent not in document, absent
    text = manifest_text(tmp_path, "a")
    assert "\n# connector:\n" in text
    assert "\n# prompts:\n" in text
    assert "toy-order-desk" not in text
    assert "agentdiag.examples" not in text


def test_the_review_lines_sit_above_description_kind_dev_prompts_and_connector_only(
    tmp_path: Path,
) -> None:
    described(tmp_path)

    under = reviewed(manifest_text(tmp_path, "a"))

    assert [line.split(":", 1)[0] for line in under] == [
        "description",
        "kind",
        "dev",
        "# prompts",
        "# connector",
    ]
    assert "kind: pending" in under
    assert "dev: {}" in under


def test_the_review_line_above_kind_names_the_kinds_to_choose_from(tmp_path: Path) -> None:
    described(tmp_path)

    text = manifest_text(tmp_path, "a")

    assert (
        "  # REVIEW: nothing drives this Target yet; replace pending with inprocess (a Python "
        "factory in this process), http (a chat endpoint with a Dialect), or a plugin's kind, "
        "and give the environment block that kind reads\n  kind: pending\n"
    ) in text
    assert (
        "    # REVIEW: one block per environment the Target runs in, protected: true on every "
        "one that reaches real users\n    dev: {}\n"
    ) in text


def test_a_given_description_carries_no_review_line_and_no_family_adds_one(
    tmp_path: Path,
) -> None:
    result = invoke(
        "init", "--root", tmp_path, "--target", "desk", "--description", "Takes returns."
    )
    assert result.exit_code == 0, output(result)

    document = manifest_of(tmp_path, "desk")
    under = reviewed(manifest_text(tmp_path, "desk"))

    assert document["target"] == {"name": "desk", "description": "Takes returns."}
    assert "family" not in document and "channel" not in document
    assert [line.split(":", 1)[0] for line in under] == [
        "# family",
        "kind",
        "dev",
        "# prompts",
        "# connector",
    ]


def test_the_sample_suite_is_the_first_turn_under_a_review_line_and_names_the_target(
    tmp_path: Path,
) -> None:
    described(tmp_path)

    suite = suite_of(tmp_path, "a")
    text = (target_dir(tmp_path, "a") / "suites" / "sample.yaml").read_text(encoding="utf-8")

    assert suite["target"] == "A"
    assert [scenario["id"] for scenario in suite["scenarios"]] == ["first-turn"]
    assert reviewed(text) == ["- id: first-turn"]


# --- what it validates as, and what refuses it ---


def test_validate_passes_it_with_the_pending_review_and_no_runnable_suite_warnings_only(
    tmp_path: Path,
) -> None:
    described(tmp_path)
    manifest = target_dir(tmp_path, "a") / "manifest.yaml"

    result = invoke("validate", "--root", tmp_path, "--target", "a")

    assert result.exit_code == 0, output(result)
    assert "error:" not in output(result)
    warnings = [line for line in result.stdout.splitlines() if line.startswith("warning:")]
    assert warnings == [
        f"warning: {manifest}: {PENDING_LINE}",
        f"warning: {manifest}: 5 lines marked REVIEW; settle each (accept or rewrite it) "
        "before a Run",
        f"warning: {manifest}: {NO_RUNNABLE_LINE}",
    ]
    assert "0 errors, 3 warnings" in result.stdout


def test_run_refuses_the_pending_adapter_and_writes_no_run(tmp_path: Path) -> None:
    described(tmp_path)

    result = invoke("run", "--root", tmp_path, "--target", "a")

    assert result.exit_code == 3, output(result)
    assert PENDING_LINE in output(result).splitlines()
    assert NO_RUNNABLE_LINE in output(result).splitlines()
    assert not (target_dir(tmp_path, "a") / "runs").exists()


def test_sync_refuses_the_pending_adapter_with_validates_line_and_writes_nothing(
    tmp_path: Path,
) -> None:
    described(tmp_path)

    for arguments in ((), ("--check",)):
        result = invoke("sync", "--root", tmp_path, "--target", "a", *arguments)

        assert result.exit_code == 3, output(result)
        assert f"error: {PENDING_LINE}" in output(result).splitlines()
    assert not (target_dir(tmp_path, "a") / "fingerprint.json").exists()
    assert not (target_dir(tmp_path, "a") / "sync-breaks").exists()


def test_sync_of_a_pending_adapter_beside_an_inprocess_connector_reads_the_connector(
    tmp_path: Path,
) -> None:
    """`sync` refuses a pending Adapter only when no Connector reads the deployed set (the
    ticket 46 amendment): with the in-process Connector filled in, it syncs through it."""
    described(tmp_path)
    manifest = target_dir(tmp_path, "a") / "manifest.yaml"
    text = manifest.read_text(encoding="utf-8").replace(
        "# connector:\n#   kind: inprocess\n#   environments:\n#     dev:\n"
        "#       deployed: your_package.module:deployed_set\n",
        "connector:\n  kind: inprocess\n  environments:\n    dev:\n"
        "      deployed: agentdiag.examples.toy:deployed_set\n",
    )
    assert "\nconnector:\n" in text
    manifest.write_text(text, encoding="utf-8")

    result = invoke("sync", "--root", tmp_path, "--target", "a")

    assert result.exit_code == 0, output(result)
    assert PENDING_LINE not in output(result)
    assert (target_dir(tmp_path, "a") / "fingerprint.json").is_file()


# --- the toy, and the flags on every scaffold ---


def test_the_first_init_with_no_flags_still_writes_the_toy_as_default(tmp_path: Path) -> None:
    result = invoke("init", "--root", tmp_path)

    assert result.exit_code == 0, output(result)
    document = manifest_of(tmp_path, "default")
    assert document["target"]["name"] == "toy-order-desk"
    assert document["adapter"]["kind"] == "inprocess"
    assert "# REVIEW:" not in manifest_text(tmp_path, "default")


def test_adapter_toy_under_a_slug_renders_the_example_order_desk_byte_for_byte(
    tmp_path: Path,
) -> None:
    result = invoke("init", "--root", tmp_path, "--target", "order-desk", "--adapter", "toy")

    assert result.exit_code == 0, output(result)
    written = target_dir(tmp_path, "order-desk")
    for name in ("manifest.yaml", "suites/sample.yaml", "judge_notes.md"):
        assert (written / name).read_bytes() == (ORDER_DESK / name).read_bytes(), name


def test_the_name_flags_apply_to_the_toy(tmp_path: Path) -> None:
    result = invoke(
        "init",
        "--root",
        tmp_path,
        "--name",
        "Shop desk",
        "--description",
        "The shop's own desk.",
        "--family",
        "shop",
        "--channel",
        "voice",
    )
    assert result.exit_code == 0, output(result)

    document = manifest_of(tmp_path, "default")

    assert document["target"] == {"name": "Shop desk", "description": "The shop's own desk."}
    assert (document["family"], document["channel"]) == ("shop", "voice")
    assert document["adapter"]["kind"] == "inprocess"
    assert suite_of(tmp_path, "default")["target"] == "Shop desk"
    assert invoke("validate", "--root", tmp_path).exit_code == 0


def test_the_name_flags_apply_to_a_custom_target(tmp_path: Path) -> None:
    result = invoke(
        "init",
        "--root",
        tmp_path,
        "--target",
        "b",
        "--adapter",
        IDLE_ADAPTER,
        "--name",
        "Idle",
        "--family",
        "quiet",
    )
    assert result.exit_code == 0, output(result)

    document = manifest_of(tmp_path, "b")

    assert document["target"]["name"] == "Idle"
    assert document["family"] == "quiet"
    assert "channel" not in document
    assert document["adapter"]["environments"]["local"]["factory"] == IDLE_ADAPTER[7:]
    assert suite_of(tmp_path, "b")["target"] == "Idle"


def test_a_name_holding_a_colon_and_a_hash_round_trips(tmp_path: Path) -> None:
    name = "Returns: desk #2"
    result = invoke("init", "--root", tmp_path, "--target", "desk", "--name", name)
    assert result.exit_code == 0, output(result)

    assert manifest_of(tmp_path, "desk")["target"]["name"] == name
    assert suite_of(tmp_path, "desk")["target"] == name
    assert invoke("validate", "--root", tmp_path).exit_code == 0


def test_the_adapter_help_names_toy_beside_python(tmp_path: Path) -> None:
    result = invoke("init", "--help")

    assert result.exit_code == 0
    help_text = " ".join(result.stdout.split())
    assert "toy" in help_text
    assert "python:<module:attr>" in help_text


def test_an_adapter_not_understood_names_both_forms(tmp_path: Path) -> None:
    result = invoke("init", "--root", tmp_path, "--target", "a", "--adapter", "http:x")

    assert result.exit_code == 3
    assert "--adapter toy" in output(result)
    assert "--adapter python:<module:attr>" in output(result)
    assert not (tmp_path / ".agentdiag").exists()


# --- what init says next ---


def test_init_says_no_adapter_yet_and_names_the_review_lines_validate_and_dry_run(
    tmp_path: Path,
) -> None:
    result = described(tmp_path)

    lines = result.stdout.splitlines()

    assert "Target a (A), family f, channel chat; no Adapter yet (adapter.kind: pending)." in lines
    assert "  settle the 5 REVIEW lines in .agentdiag/targets/a/manifest.yaml, then" in lines
    assert "  agentdiag validate" in lines
    assert "  agentdiag run --dry-run" in lines
    assert not any("factory" in line for line in lines)
    assert (
        "A judged Eval needs credentials: a Claude Code login (claude auth login) "
        "or export ANTHROPIC_API_KEY=…."
    ) in lines


def test_init_without_family_or_channel_omits_them_from_the_target_line(tmp_path: Path) -> None:
    result = invoke("init", "--root", tmp_path, "--target", "desk")
    assert result.exit_code == 0, output(result)

    assert "Target desk (desk); no Adapter yet (adapter.kind: pending)." in result.stdout
    assert "  settle the 6 REVIEW lines in .agentdiag/targets/desk/manifest.yaml, then" in (
        result.stdout.splitlines()
    )
