"""Seam 1: the Change record as a file (ticket 25, ADR-0012, phase-7 decisions 1, 5, 6).

A record is one markdown file under the Target's `changes/` with its head between `---`
lines; it round-trips through `write_record` and `load_record`, and customer identifiers
(an e-mail address, a phone number, a name the Manifest's redaction list gives) never reach
the file. `validate` checks every record's shape and its status against its fields.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agentdiag.change.record import (
    ChangeRecord,
    ChangeRecordInvalid,
    Trigger,
    load_record,
    load_records,
    new_record_id,
    record_body,
    redactable_fields,
    write_record,
)
from agentdiag.change.redact import redact, redaction_misses
from agentdiag.cli import app
from agentdiag.workspace import Workspace


def test_redact_replaces_an_email_a_phone_and_a_listed_name() -> None:
    text = "Maria Lopez (maria.lopez@example.com, +1 (555) 010-4477) says her order vanished."

    assert redact(text, ["maria lopez"]) == "[name] ([email], [phone]) says her order vanished."


def test_redact_leaves_dates_times_ids_and_short_numbers_alone() -> None:
    text = (
        "On 2026-09-15 10:00 order NB-1042 in Run 20260923T100000Z-base was 1,534 chars "
        "behind record 20260929-refund-window (fingerprint 3f9c1234567e); Marian stays."
    )

    assert redact(text, ["Maria"]) == text


def test_a_phone_number_beside_a_date_is_redacted_and_the_date_kept() -> None:
    assert redact("Called on 2026-09-15 555 123 4567 about it") == (
        "Called on 2026-09-15 [phone] about it"
    )
    assert redact("20260915 5551234") == "20260915 [phone]"


def test_seven_digits_are_a_phone_number_the_accepted_false_positive() -> None:
    """The contract's rule: seven digits with separators are redacted, whatever they were."""
    assert redact("order 123-4567 shipped") == "order [phone] shipped"
    assert redact("order 123-456 shipped") == "order 123-456 shipped"
    assert redaction_misses("order 123-4567, then 2026-09-15") == ["123-4567"]


# --- the file ---

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "toy"
SLUG = "toy-order-desk"

runner = CliRunner()


def toy(tmp_path: Path, *, names: list[str] | None = None) -> Path:
    root = tmp_path / "toy"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs", "index.sqlite"))
    if names is not None:
        manifest = root / ".agentdiag" / "targets" / SLUG / "manifest.yaml"
        quoted = ", ".join(f'"{name}"' for name in names)
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + f"\nredaction:\n  names: [{quoted}]\n",
            encoding="utf-8",
        )
    return root


def a_record(**fields: Any) -> ChangeRecord:
    base: dict[str, Any] = {
        "id": "20260929-refund-window",
        "target": SLUG,
        "status": "open",
        "opened_at": "2026-09-29T10:00:00Z",
        "opened_by": "tester",
        "title": "Refund window invented on cancel",
        "trigger": Trigger(kind="complaint", summary="Refund window invented on cancel"),
        "layer": "rules",
    }
    return ChangeRecord.model_validate({**base, **fields})


def test_a_status_or_a_layer_outside_the_closed_sets_is_refused() -> None:
    with pytest.raises(ValidationError):
        a_record(status="fixed-unverified")
    with pytest.raises(ValidationError):
        a_record(layer="prompt")


def test_a_record_round_trips_through_its_file_head_and_body(tmp_path: Path) -> None:
    target = Workspace.find(toy(tmp_path)).resolve(None)
    record = a_record()

    path = write_record(target, record, "## Trigger\n\nThe customer was told 5 days.\n")

    assert path == target.directory / "changes" / "20260929-refund-window.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\nschema_version: 1\nid: 20260929-refund-window\n")
    loaded, body = load_record(path), record_body(path)
    assert loaded == record
    assert body == "## Trigger\n\nThe customer was told 5 days."
    assert load_records(target) == [(path, record)]


def test_a_new_id_is_the_opening_day_and_the_title_slug_and_a_collision_counts_up(
    tmp_path: Path,
) -> None:
    target = Workspace.find(toy(tmp_path)).resolve(None)
    opened = "2026-09-29T23:59:59Z"

    first = new_record_id(target, "Refund window: invented!", opened)
    write_record(target, a_record(id=first))
    second = new_record_id(target, "Refund window: invented!", opened)
    write_record(target, a_record(id=second))

    assert first == "20260929-refund-window-invented"
    assert second == "20260929-refund-window-invented-2"
    assert new_record_id(target, "Refund window: invented!", opened).endswith("-3")


def test_a_complaint_carrying_an_email_a_phone_and_a_listed_name_never_lands(
    tmp_path: Path,
) -> None:
    target = Workspace.find(toy(tmp_path, names=["Dana Whitfield"])).resolve(None)
    complaint = "Dana Whitfield (dana.w@example.org, 0161 496 0018) was promised a refund."
    record = a_record(
        title="Dana Whitfield was promised a refund",
        trigger=Trigger(kind="complaint", summary=complaint),
    )

    path = write_record(target, record, f"## Trigger\n\n{complaint}\n")

    text = path.read_text(encoding="utf-8")
    for identifier in ("Dana", "Whitfield", "dana.w@example.org", "0161 496 0018", "4960018"):
        assert identifier not in text
    assert "[name] ([email], [phone]) was promised a refund." in text
    assert load_record(path).title == "[name] was promised a refund"


def test_the_id_and_the_file_name_are_slugged_from_the_redacted_title(tmp_path: Path) -> None:
    target = Workspace.find(toy(tmp_path, names=["Dana Whitfield"])).resolve(None)
    title = "Refund for jane@acme.com 555 123 4567 Dana Whitfield"

    record_id = new_record_id(target, title, "2026-09-29T10:00:00Z")
    path = write_record(target, a_record(id=record_id, title=title))

    for identifier in ("jane", "acme", "555", "4567", "dana", "whitfield"):
        assert identifier not in record_id
        assert identifier not in path.name.lower()
    assert record_id == "20260929-refund-for-email-phone-name"


def test_every_string_but_an_identifier_is_redacted_on_write(tmp_path: Path) -> None:
    """One field list decides both: what `write_record` redacts is what `validate` checks."""
    target = Workspace.find(toy(tmp_path, names=["Dana Whitfield"])).resolve(None)
    record = a_record(
        opened_by="Dana Whitfield",
        change={"files": ["notes/from dana@example.org"], "flow_ids": ["call-5551234567"]},
        pushes=[{"kind": "local", "environment": "Dana Whitfield", "at": "2026-09-29T11:00:00Z"}],
        status="pushed",
    )

    loaded = load_record(write_record(target, record))

    assert loaded.opened_by == "[name]"
    assert loaded.change is not None
    assert loaded.change.files == ["notes/from [email]"]
    assert loaded.change.flow_ids == ["call-[phone]"]
    assert loaded.pushes[0].environment == "[name]"
    assert loaded.id == record.id and loaded.opened_at == record.opened_at
    checked = {where for where, _ in redactable_fields(record)}
    assert {"opened_by", "change.files[0]", "change.flow_ids[0]", "pushes[0].environment"} <= (
        checked
    )
    assert not {"id", "target", "opened_at", "pushes[0].at"} & checked


def test_a_file_that_is_not_a_record_names_the_file_and_the_field(tmp_path: Path) -> None:
    path = tmp_path / "20260929-x.md"
    path.write_text("---\nid: 20260929-x\nstatus: done\n---\n", encoding="utf-8")

    with pytest.raises(ChangeRecordInvalid) as refused:
        load_record(path)

    assert str(path) in str(refused.value)
    assert "status" in str(refused.value)


# --- validate (decision 5) ---


def validate(root: Path) -> Any:
    return runner.invoke(app, ["validate", "--root", str(root)])


def test_validate_counts_the_change_records_it_checked(tmp_path: Path) -> None:
    root = toy(tmp_path)
    target = Workspace.find(root).resolve(None)
    write_record(target, a_record())

    result = validate(root)

    assert result.exit_code == 0, result.output
    assert "validated the Manifest, 2 Suites and 1 Change record: 0 errors, 0 warnings" in (
        result.stdout
    )


@pytest.mark.parametrize(
    ("fields", "says"),
    [
        ({"status": "pushed"}, "status pushed and no push event"),
        ({"status": "verified", "closed_at": "2026-09-30T00:00:00Z"}, "no verification"),
        ({"status": "superseded", "superseded_by": "20260101-gone"}, "20260101-gone"),
        ({"layer": None}, "layer"),
        ({"why": "call 0161 496 0018 back"}, "redaction"),
        (
            {
                "status": "verified",
                "closed_at": "2026-09-30T00:00:00Z",
                "expected": {
                    "stated_at": "2026-09-30T12:00:00Z",
                    "should_move": ["where-is-shipped-order"],
                    "must_not_move": [],
                },
                "pushes": [{"kind": "local", "environment": "local", "at": "2026-09-29T11:00:00Z"}],
                "verification": {
                    "baseline": "20260923T100200Z-prmt",
                    "run": "20260923T100000Z-base",
                    "compared_at": "2026-09-30T00:00:00Z",
                    "expect": [],
                    "result": "verified",
                    "summary": "improvement 1",
                },
            },
            "stated after",
        ),
        (
            {
                "status": "refuted",
                "closed_at": "2026-09-30T00:00:00Z",
                "pushes": [{"kind": "local", "environment": "local", "at": "2026-09-29T11:00:00Z"}],
                "verification": {
                    "baseline": "20260923T100200Z-prmt",
                    "run": "20260923T100000Z-base",
                    "compared_at": "2026-09-30T00:00:00Z",
                    "expect": [],
                    "result": "refuted",
                    "summary": "",
                },
            },
            "no Run directory",
        ),
    ],
    ids=[
        "pushed-no-event",
        "verified-no-verification",
        "superseded-dangling",
        "no-layer",
        "redaction-miss",
        "expectation-after-verification",
        "run-missing",
    ],
)
def test_validate_refuses_a_record_whose_status_disagrees_with_its_fields(
    tmp_path: Path, fields: dict[str, Any], says: str
) -> None:
    root = toy(tmp_path)
    target = Workspace.find(root).resolve(None)
    record = a_record(**fields)
    path = target.directory / "changes" / f"{record.id}.md"
    path.parent.mkdir(parents=True)
    # Written by hand, bypassing `write_record`'s redaction, as a hand edit would be.
    from agentdiag.change.record import render_record

    path.write_text(render_record(record, ""), encoding="utf-8")

    result = validate(root)

    assert result.exit_code == 3, result.output
    errors = [line for line in result.stdout.splitlines() if line.startswith("error:")]
    assert errors
    assert all(str(path) in line for line in errors)
    assert any(says in line for line in errors), errors


def test_validate_refuses_an_expectation_stated_after_the_cited_run_started(
    tmp_path: Path,
) -> None:
    root = toy(tmp_path)
    target = Workspace.find(root).resolve(None)
    runs = REPO / "tests" / "fixtures" / "runs"
    for run in ("20260923T100000Z-base", "20260923T100200Z-prmt"):
        shutil.copytree(runs / run, target.runs / run)
    record = a_record(
        status="verified",
        closed_at="2026-09-30T00:00:00Z",
        expected={
            "stated_at": "2026-09-23T10:01:00Z",
            "should_move": ["where-is-shipped-order"],
            "must_not_move": [],
        },
        pushes=[{"kind": "local", "environment": "local", "at": "2026-09-23T09:00:00Z"}],
        verification={
            "baseline": "20260923T100200Z-prmt",
            "run": "20260923T100000Z-base",
            "compared_at": "2026-09-30T00:00:00Z",
            "expect": [],
            "result": "verified",
            "summary": "improvement 1",
        },
    )
    write_record(target, record)

    result = validate(root)

    assert result.exit_code == 3, result.output
    assert (
        "expected.stated_at: 2026-09-23T10:01:00Z is not before the verifying Run "
        "20260923T100000Z-base started at 2026-09-23T10:00:00Z"
    ) in result.stdout


def test_validate_names_a_change_file_that_does_not_load(tmp_path: Path) -> None:
    root = toy(tmp_path)
    changes = root / ".agentdiag" / "targets" / SLUG / "changes"
    changes.mkdir()
    (changes / "20260929-broken.md").write_text("no head here\n", encoding="utf-8")

    result = validate(root)

    assert result.exit_code == 3
    assert f"error: {changes / '20260929-broken.md'}: head:" in result.stdout
