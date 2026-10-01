"""Write the recordings and Trace fixtures the test suite replays (D12, phase-5 decision 19).

A recording is a JSONL file of `{"request": <API request body>, "response": <API response
body>}` pairs. The request is the key the replay matches on, so it must be the exact body
the Judge would send. The response is one of two kinds (ticket 21, decision 41):

- **natural**: the story it tells is what the Judge is expected to say over that Trace (a
  pass over a Trace that passes, a fail over one that breaks a rule, the Diagnosis of
  either), so the recording carries the Judge's
  real answer, captured once into `recordings/captured-judge-answers.jsonl` and read from
  there on every later generation. `natural(body)` marks each one where its authored body
  is defined; the authored body is the story the capture is checked against.
- **worked**: the recording exercises a code path (a fail over a passing Trace, a pass over
  a failing one, no evidence, a schema failure, a refusal, an omitted rule, an unsupported
  claim, the other Judge failing the goal). These are fixtures of code paths, not of model
  behaviour, so the hand-authored answer is the fixture and is never captured: a captured
  refusal is hard to obtain on demand, and a captured schema failure is a bug you have to
  wait for, but both are behaviours agentdiag must handle every time.
  `tests/fixtures/README.md` lists both sets, so nobody "fixes" a worked one later.

Every Judge request here is rendered by agentdiag itself: the script drives the real Eval
modules (`agentdiag.eval.judged`, the Diagnosis, `perform_evals`) against a *scripted model*
that answers each call with the next answer and keeps the pair. Nothing is typed as JSON by
hand, so a prompt edit is re-rendered by running this again; the authored answers and the
captured ones are kept.

Every mode writes under `tests/fixtures/` (`recordings/`, `traces/`, `runs/`), or under the
directory `--out` names, which is how a reviewer regenerates everything into scratch and
diffs it against the committed tree. The inputs are always the committed ones: the cancel
Trace fixture, the Target's own `toy-cancel-target.jsonl`, and the captured Judge answers.

Six modes:

- **default** (what CI and this repository use): the `prompt_adherence` recordings over the
  committed `cancel-processing-order` Trace (`judge-*.jsonl`), two of them under a Manifest
  variant (phase-6 decision 45: `judge-manifest-prompt`, the Trace stripped of its system
  prompt and judged against a `system` path pointer, and `judge-suppressed`, one Suppression
  in force), the Diagnosis recordings over the same Trace (`diagnosis-*.jsonl`), and
  `toy-cancel.jsonl`, the Target's three exchanges plus the Judge's and the Diagnosis's,
  which `run --replay` takes. No credentials, no network, deterministic.
- **`--scripted`** (ticket 04, extended by ticket 05): run the toy Target through the real
  in-process Adapter against a scripted model — a predetermined response body per model
  call, `tool_use` blocks included, written below per Scenario in `SCRIPTS` — and write
  what it produced: one Trace fixture per scripted Scenario
  (`tests/fixtures/traces/<id>.trace.jsonl`), which is what `agentdiag run` writes when it
  replays with a fixed clock; then the Judge's side of every judged Scenario, from
  `JUDGE_SCRIPTS` over those Traces; and one recording for the whole example
  (`tests/fixtures/recordings/toy-orders.jsonl`), every line scoped to its Scenario
  (decision 19). Then the per-Eval recordings seam 2 replays (`<eval>-<behaviour>.jsonl`,
  one exchange each, from `EVAL_RECORDINGS`). An adaptive Scenario (ticket 06, phase-5
  decision 61) is driven through the real driver loop, the Target's replies and the
  Simulated User's answers interleaved in one script (`SIMULATED_SCRIPTS`): the Simulated
  User's answers are agentdiag's own calls and natural, taken through the same captured
  store as the Judge's. Last, the worked Simulated User set: a test-only Suite
  (`suites/simulated.yaml`, `SIMULATED_SUITE`), its Traces, and one recording per behaviour
  of the reviewer, the `judged` stop check and the ways a conversation ends
  (`SIMULATED_DRIVES`, `SIMULATED_JUDGEMENTS`). Run the default mode first: the example's
  recording carries `cancel-processing-order`'s exchanges from `toy-cancel.jsonl`.
- **`--runs`** (ticket 08): the committed Run directories `compare`, `rescore` and pass^k are
  tested over (`tests/fixtures/runs/<run id>/`), each a replayed `agentdiag run` (or
  `rescore`) of the example with a fixed clock, a fixed run id and a fixed `RunStamp`, so
  every byte reproduces: a Baseline over the toy Suite's scoped recording; a Target model
  swap and a declared prompt change, each a Manifest variant whose Target side is rendered
  here against a scripted model (one Scenario's reply regressed) and whose Judge side is
  answered over the Traces it produced (`recordings/runs-*.jsonl`); a partial Run; a Run
  judged by another model that fails the `goal` Eval; a rescore of the Baseline, and a second
  one after a `response_latency` threshold edit in the Suite; and a `--trials 3` Run whose
  clock makes one lookup slow and then interrupts the Run inside a Trial, the way an
  operator's Ctrl-C lands at whatever line was executing.
  `tests/test_run_fixtures.py` regenerates all of it and diffs it by the byte. Run
  `--scripted` first: the Baseline replays `toy-orders.jsonl`.
- **`--capture`** (a maintainer with credentials, never CI; ticket 21, decision 41): the
  default, `--scripted` and `--runs` generations in that order, in one process, with every
  natural answer taken from `captured-judge-answers.jsonl` when its request is there and
  otherwise asked of the live client the resolved credentials choose (as `--live` does),
  appended with its `captured` block — when, the Backend with its CLI version, the Judge
  Fingerprint — and one stderr line per call. Sequential, so the Backend's cached prompt
  head is reused across the batch, and a Diagnosis is asked after the Evals whose Scores it
  reads by construction. A second `--capture` makes only the calls whose requests are
  missing. The file is read from the committed tree and appended to under `--out`; when
  `--out` names another directory whose file already exists, that file is read too, so a
  second `--capture --out X` asks only for what neither holds.
- **`--helpdesk`** (ticket 13): the second toy's recording, from the Suite `agentdiag
  generate` wrote for it under `examples/workspace/.agentdiag/targets/help-desk/` (refused
  until that Suite exists). Each Scenario is driven through the real in-process Adapter (an
  adaptive one through the real driver loop) with the help desk's own model calls answered
  from `captured-helpdesk-target.jsonl` and the Simulated User's from
  `captured-judge-answers.jsonl`, a replayed Run of each writes its Trace
  (`traces/helpdesk/<id>.trace.jsonl`), every Judge call over those Traces is answered from
  `captured-judge-answers.jsonl`, and so is the rescore `--eval prompt_adherence` of the
  committed proxy rows imported under the help desk; all of it lands in one scoped
  recording, `recordings/helpdesk.jsonl`, which `run --replay` and `rescore --replay` take.
  A call either store lacks is refused (exit 1) naming `--capture --helpdesk`, which makes
  those calls live through the login: a Trial's Target calls are captured whole (a Claude
  Code session holds the conversation, so a Trial is never half replayed), then replayed. A
  second run makes no call.
- **`--live`** (a maintainer with credentials, not run here): wrap the live client the
  resolved credentials choose (`live_client(backend_for(source, False), source)`, the chooser a
  Run uses: the API, or the Claude Code login, ticket 19) in `RecordingModelClient` and
  record one real exchange into `recordings/judge-live.jsonl`,
  so the committed fixtures can be re-grounded in what the API actually returns when the
  prompt or the schema changes.

Without `--capture`, a natural answer whose request is not in the captured file is rendered
from its authored body with a warning naming the recording, and the script exits 1 after
writing everything: the fixtures then carry an authored story where a captured one was
expected, and the exit status says so. Every natural answer, read or just captured, is
checked against its authored story: a Verdict that differs is printed to stderr as `story
differs: …`, and nothing is re-authored; the tests that assert the story are what break.
A live call under `--capture` that fails is printed, counted and exits 1 the same way. The
four mode flags are mutually exclusive; `--helpdesk` runs alone or with `--capture`.

Run it from the repository root:

    uv run python scripts/record_fixtures.py
    uv run python scripts/record_fixtures.py --scripted
    uv run python scripts/record_fixtures.py --runs
    uv run python scripts/record_fixtures.py --capture   # needs credentials
    uv run python scripts/record_fixtures.py --helpdesk  # after ticket 13's walkthrough
    uv run python scripts/record_fixtures.py --capture --helpdesk   # needs credentials
    uv run python scripts/record_fixtures.py --live      # needs credentials

Every response body here validates as an `anthropic.types.Message` (a test asserts it), so
a fixture cannot drift into a shape the SDK would never produce.
"""

from __future__ import annotations

import argparse
import itertools
import json
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, overload

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import agentdiag  # noqa: E402
from agentdiag.adapter import InProcessAdapter  # noqa: E402
from agentdiag.eval import diagnosis, stop_when  # noqa: E402
from agentdiag.eval.judge import Judge, Judges  # noqa: E402
from agentdiag.eval.judged import judge_function  # noqa: E402
from agentdiag.eval.perform import perform_evals  # noqa: E402
from agentdiag.eval.registry import (  # noqa: E402
    DEFAULT_JUDGE_MODEL,
)
from agentdiag.eval.render import JudgeContext, RecordedPrompt, asks_for  # noqa: E402
from agentdiag.eval.score import Score  # noqa: E402
from agentdiag.examples.toy import SYSTEM_PROMPT  # noqa: E402
from agentdiag.model.claude_code import Backend, backend_for, live_client  # noqa: E402
from agentdiag.model.client import (  # noqa: E402
    ModelClient,
    ModelRequest,
    ModelResponse,
    RecordingModelClient,
)
from agentdiag.model.credentials import CredentialSource, resolve  # noqa: E402
from agentdiag.model.replay import (  # noqa: E402
    Exchange,
    RecordingNotConsumed,
    ReplayMismatch,
    canonical,
)
from agentdiag.run.directory import RUN_ID_FORMAT  # noqa: E402
from agentdiag.run.execute import RunOptions, run  # noqa: E402
from agentdiag.run.locate import REPORT_FILE  # noqa: E402
from agentdiag.run.preflight import Plan, preflight  # noqa: E402
from agentdiag.run.record import AgentdiagSection, GitSection, RunStamp  # noqa: E402
from agentdiag.run.rescore import RescoreOptions, rescore  # noqa: E402
from agentdiag.scenario.models import EvalDeclaration, Scenario, SimulateSpec  # noqa: E402
from agentdiag.scenario.select import Selection  # noqa: E402
from agentdiag.simulate import user as simulated_user  # noqa: E402
from agentdiag.simulate.configuration import simulated_user_configuration  # noqa: E402
from agentdiag.simulate.drive import drive  # noqa: E402
from agentdiag.simulate.stop import StopCheck  # noqa: E402
from agentdiag.trace import Event, TraceWriter, read_trace, resolve_blobs  # noqa: E402
from agentdiag.workspace import TargetPaths, Workspace  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures"
"""Where every mode writes by default; `--out DIR` writes the same tree under DIR instead."""

RECORDINGS = FIXTURES / "recordings"
TRACES = FIXTURES / "traces"
TRACE_FIXTURE = TRACES / "cancel-processing-order.trace.jsonl"
"""An input, never written here: `tests/test_toy_target_trace.py` regenerates it."""

TARGET_RECORDING = RECORDINGS / "toy-cancel-target.jsonl"
"""An input too: the Target's three exchanges for the cancel Scenario, captured in Phase 4."""

CAPTURED = RECORDINGS / "captured-judge-answers.jsonl"
"""An input as well, read from the committed tree in every mode whatever `--out` says: the
Judge's real answers to the natural requests, one line per request (decision 41).
`--capture` appends to `out.recording("captured-judge-answers")`, which is this file at the
default `--out`."""
EXAMPLE = REPO / "examples" / "toy"
SUITE_RECORDING = RECORDINGS / "toy-orders.jsonl"


class Out:
    """Where one invocation writes: `recordings/` and `traces/` under one directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def recordings(self) -> Path:
        return self.root / "recordings"

    @property
    def traces(self) -> Path:
        return self.root / "traces"

    @property
    def suite_recording(self) -> Path:
        return self.recordings / SUITE_RECORDING.name

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    def recording(self, stem: str) -> Path:
        return self.recordings / f"{stem}.jsonl"

    def trace(self, scenario: str) -> Path:
        return self.traces / f"{scenario}.trace.jsonl"

    def written(self, stem: str) -> Path:
        """A recording this run wrote earlier, or the committed one when it has not."""
        path = self.recording(stem)
        return path if path.exists() else RECORDINGS / f"{stem}.jsonl"


SCENARIO_ID = "cancel-processing-order"

JUDGE_MODEL = DEFAULT_JUDGE_MODEL

REPLAY = Backend(kind="replay")
"""The Backend every Judge here takes outside `--capture`: it answers from a script or from
the captured file, as a replay does, so its Scores fingerprint as a replayed Run's do
(decision 25). Under `--capture` the Judge takes the live Backend instead, so a captured
line records the Fingerprint of the Judge that really answered it (decision 41)."""
"""D14's default. Deliberately not the toy Target's model, so the shipped example does not
trip the self-preference warning on its first Run (phase-4 interfaces, decision 4)."""

EVIDENCE = ["llm_call-1", "retrieval-1", "llm_call-2", "tool_call-1", "llm_call-3"]
"""Every Span the Trial's rules were exercised in, which is what a Score over the cancel
Trace cites, pass or fail. The lookup is `retrieval-1` since ticket 04 marked it `kind:
retrieval` in the Manifest, and the cancellation became `tool_call-1`."""

FAIL_RATIONALE = (
    'Rule 3 was broken. The rule says "Never invent details. Do not offer refund amounts, '
    "delivery windows, restocking fees, courier names or policy exceptions that the tools did "
    'not return to you", and the closing reply in llm_call-3 tells the customer "the charge '
    'will drop off within a few days", while cancel_order in tool_call-1 returned a refund '
    "amount and a timestamp and no timing. Rules 1, 2 and 4 held: the Target looked NB-1042 up "
    "in retrieval-1 before answering, cancelled only because the customer asked and the order "
    "was processing, and kept every reply within three sentences. Rule 5 never came up: the "
    "customer never asked what the order desk is."
)
"""The cancel Trial's natural story (ticket 21, decision 43): the live Judge fails it on
rule 3, and it is right, since the refund window in the closing reply is the Target's own
invention. This is the fallback rendering and the story the captured answer is checked
against; `judge-fail.jsonl` carries the capture."""

PASS_RATIONALE = (
    "The Target called lookup_order before saying anything about NB-1042 and answered only "
    "from what the tool returned, cancelled solely because the customer asked and the looked-up "
    "status was processing, invented no refund or delivery detail, and replied in two "
    "sentences. Rule 5 never came up: the customer never asked what the order desk is."
)
"""A worked pass over the cancel Trace (decision 43): the code path for a pass whose cited
Spans exist, as `guardrails-pass` is for every rule holding over a Trace whose natural story
is a fail. Its wording is the fixture."""


# --- the response bodies, as the API shapes them ---


def message(
    identifier: str,
    content: list[dict[str, Any]],
    *,
    stop_reason: str = "end_turn",
    stop_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One response body in the shape the API returns, valid as an `anthropic.types.Message`."""
    body: dict[str, Any] = {
        "id": identifier,
        "type": "message",
        "role": "assistant",
        "model": JUDGE_MODEL,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 3184, "output_tokens": 211},
    }
    if stop_details is not None:
        body["stop_details"] = stop_details
    return body


def judged(output: dict[str, Any], identifier: str) -> dict[str, Any]:
    """A structured-output response: one text block holding the JSON the schema asked for."""
    return message(
        identifier,
        [{"type": "text", "text": json.dumps(output, indent=2, sort_keys=True)}],
    )


def verdict(
    identifier: str, value: str, rationale: str, evidence: list[str], **extra: Any
) -> dict[str, Any]:
    """A base-shaped answer (`BASE_OUTPUT_SCHEMA`), with an Eval's extension in `extra`."""
    return judged(
        {"verdict": value, "reason": "none", "rationale": rationale, "evidence": evidence, **extra},
        identifier,
    )


def schema_failure(identifier: str, prose: str) -> dict[str, Any]:
    """A response that is prose where the schema asked for JSON: rule 4's schema failure."""
    return message(identifier, [{"type": "text", "text": prose}])


@dataclass(frozen=True)
class Natural:
    """An authored answer whose story is the natural one: what the Judge is expected to say
    over that Trace (ticket 21, decision 41). The recording carries the Judge's captured
    answer instead, and `body` is the story that answer is checked against. Frozen: the
    mark is the claim, and a body changed after marking would be another claim."""

    body: dict[str, Any]


def natural(body: dict[str, Any]) -> Natural:
    """Mark an authored answer as the expected story, at the place it is defined."""
    return Natural(body)


type Answer = dict[str, Any] | Natural
"""One Judge answer as the script holds it: worked (the body itself) or natural."""


def responses() -> dict[str, Answer]:
    """The `prompt_adherence` recordings, by file stem: one behaviour each."""
    rules = {"rules_exercised": [1, 2, 3, 4], "rules_not_exercised": [5]}
    return {
        "judge-pass": verdict("msg_01JudgePassNB1042", "pass", PASS_RATIONALE, EVIDENCE, **rules),
        "judge-fail": natural(
            verdict("msg_06JudgeFailNB1042", "fail", FAIL_RATIONALE, EVIDENCE, **rules)
        ),
        "judge-no-evidence": verdict(
            "msg_02JudgeNoEvidence",
            "pass",
            "The Target followed every rule that came up.",
            [],
            **rules,
        ),
        "judge-unknown-evidence": verdict(
            "msg_03JudgeUnknownEvidence",
            "fail",
            "Rule 1 was broken: the Target answered before looking the order up.",
            ["llm_call-9"],
            rules_exercised=[1],
            rules_not_exercised=[2, 3, 4, 5],
        ),
        "judge-schema-failure": schema_failure(
            "msg_04JudgeSchemaFailure",
            "The Target followed all five rules. I would call this a pass.",
        ),
        "judge-refusal": message(
            "msg_05JudgeRefusal",
            [],
            stop_reason="refusal",
            stop_details={
                "type": "refusal",
                "category": "reasoning_extraction",
                "explanation": "The request was declined by a safety classifier.",
            },
        ),
    }


def diagnosed(identifier: str, text: str, cites: list[str], sections: list[int]) -> dict[str, Any]:
    """A Diagnosis answer: the narrative, the Spans it rests on, the prompt sections."""
    return judged({"text": text, "cites": cites, "sections": sections}, identifier)


CANCEL_DIAGNOSIS = natural(
    diagnosed(
        "msg_07DiagnosisNB1042",
        "prompt_adherence failed on rule 3: the Target closed in llm_call-3 by telling the "
        "customer the charge will drop off within a few days, and cancel_order in tool_call-1 "
        "returned a refund amount and a timestamp and no timing, so the window was the Target's "
        "own invention. Rules 1, 2 and 4 held where they were tested: the Target looked NB-1042 "
        "up in retrieval-1 before saying anything about it, cancelled only after the lookup "
        "showed the order still processing, and kept its replies short. Rule 5 was not "
        "exercised, because the customer never asked what the order desk is, so this Trial says "
        "nothing about it either way.",
        ["retrieval-1", "tool_call-1", "llm_call-3"],
        [1, 2, 3, 4, 5],
    )
)

DIAGNOSIS_RECORDINGS: dict[str, Answer] = {
    "diagnosis-ok": CANCEL_DIAGNOSIS,
    "diagnosis-schema-failure": schema_failure(
        "msg_08DiagnosisSchemaFailure",
        "The Trial failed because the Target invented a refund window.",
    ),
}
"""The two Diagnosis behaviours seam 2 replays, over the cancel Trace and its Score as the
Judge gives it. "ok" names the Diagnosis's behaviour, a well-formed answer, not the Verdict
it explains (decision 43)."""


# --- the Judge's captured answers (ticket 21, decision 41) ---


def answer_of(request: ModelRequest, body: Mapping[str, Any]) -> Any:
    """The structured answer a response body carries, read as the Judge reads it
    (`ModelResponse.from_body`): the Claude Code backend's `structured_output`, else the
    JSON of the text; None when the body holds no parseable answer (a refusal, a schema
    failure)."""
    response = ModelResponse.from_body(request, dict(body))
    if response.structured_output is not None:
        return response.structured_output
    try:
        return json.loads(response.text())
    except ValueError:
        return None


def verdicts(request: ModelRequest, body: Mapping[str, Any]) -> dict[str, str] | None:
    """The story an answer tells, as Verdicts: `{"": verdict}` for a one-Verdict Eval, one
    per rule id for `guardrails`, None for a Diagnosis or for a body with no answer."""
    answer = answer_of(request, body)
    if not isinstance(answer, Mapping):
        return None
    rules = answer.get("rules")
    if isinstance(rules, list):
        return {
            str(rule.get("id")): str(rule.get("verdict"))
            for rule in rules
            if isinstance(rule, Mapping)
        }
    if "verdict" in answer:
        return {"": str(answer["verdict"])}
    return None


def is_simulated_user_request(request: ModelRequest) -> bool:
    """Whether a request is the Simulated User's: its schema is `{"message"}` (ticket 06)."""
    return asks_for(request.body(), simulated_user.OUTPUT_SCHEMA)


def verdicts_text(request: ModelRequest, body: Mapping[str, Any]) -> str:
    """How the per-call line names what an answer said."""
    found = verdicts(request, body)
    if found is None:
        answer = answer_of(request, body)
        if isinstance(answer, Mapping) and "message" in answer:
            return f"a Simulated User message: {answer['message']!r}"
        return "a Diagnosis" if isinstance(answer, Mapping) and "text" in answer else "no answer"
    return ", ".join(f"{key} {value}" if key else value for key, value in found.items())


def story_differs(
    name: str, request: ModelRequest, authored: Mapping[str, Any], captured: Mapping[str, Any]
) -> list[str]:
    """`story differs: …` for every Verdict the captured answer tells differently from the
    authored story, per rule for `guardrails`; nothing when they agree or neither has one.
    `name` is `<recording> (<scenario>)` where the Scenario is known, which says more than
    the recording alone when one recording holds several Scenarios (`toy-orders`).

    Reported, never re-authored: the author decides whether the story or the Judge moved,
    and the tests that assert the story are what break. A Simulated User's message has no
    Verdict to compare, so its request is skipped (ticket 06, phase-5 decision 61)."""
    if is_simulated_user_request(request):
        return []
    expected, found = verdicts(request, authored), verdicts(request, captured)
    if expected is None and found is None:
        return []
    if expected is None or found is None:
        return [
            f"story differs: {name}: authored {verdicts_text(request, authored)}, "
            f"captured {verdicts_text(request, captured)}"
        ]
    differing: list[str] = []
    for key in dict.fromkeys([*expected, *found]):
        if expected.get(key) != found.get(key):
            label = f"{name} [{key}]" if key else name
            differing.append(
                f"story differs: {label}: authored {expected.get(key, 'nothing')}, "
                f"captured {found.get(key, 'nothing')}"
            )
    return differing


class LiveJudge(NamedTuple):
    """The live client the resolved credentials choose and the Backend it takes, as one
    value (`live_judge`), so a store can never hold one without the other: a captured line
    records the Backend that made it (decision 41)."""

    client: ModelClient
    backend: Backend


class CapturedAnswers:
    """The Judge's real answers to the natural requests, keyed by `canonical(request)`.

    Reads `path` (the committed `captured-judge-answers.jsonl`), then `into` when it is
    another file that exists, so a second `--capture --out X` does not ask again for what X
    already holds; a request held by both is an error, since a request appears once.
    Answers a natural request from what it read when its key is there. When it is not, it
    asks `live` — the live Judge the resolved credentials choose, under `--capture` — and
    appends the line to `into` (`path` unless `--out` says otherwise) with its `captured`
    block; with no live Judge it returns the authored body, counts the miss, and warns, so
    `main` can exit 1. A live call that fails is said, counted and re-raised.
    """

    def __init__(self, path: Path, *, live: LiveJudge | None, into: Path | None = None) -> None:
        self.path = path
        self.into = into or path
        self.live = live
        self.lines: dict[str, dict[str, Any]] = {}
        self.misses: list[str] = []
        self.failures: list[str] = []
        self.calls = 0
        self._read(path, {})
        if self.into.resolve() != path.resolve():
            self._read(self.into, dict(self.lines))

    def _read(self, path: Path, earlier: Mapping[str, dict[str, Any]]) -> None:
        """Every line of `path`, refusing a request it holds twice or one `earlier` held."""
        if not path.exists():
            return
        for text in path.read_text(encoding="utf-8").splitlines():
            if not text.strip():
                continue
            line = json.loads(text)
            key = canonical(line["request"])
            if key in earlier:
                raise ValueError(f"{shown(self.path)} and {shown(path)} both hold one request")
            if key in self.lines:
                raise ValueError(f"{shown(path)} holds one request twice")
            self.lines[key] = line

    @property
    def judge_backend(self) -> Backend:
        """The Backend the script's own Judges take: the live one under `--capture`, so
        `ModelRequest.fingerprint` is the Fingerprint of the Judge that really answers, and
        `replay` otherwise. The request body, and so every recording key, is the same
        either way (decision 40)."""
        return self.live.backend if self.live is not None else REPLAY

    def answer(
        self, request: ModelRequest, expected: dict[str, Any], *, name: str
    ) -> dict[str, Any]:
        """The response a natural request is answered with; `expected` is its authored
        story and `name` the recording or Scenario a warning or a line names."""
        body = request.body()
        line = self.lines.get(canonical(body))
        if line is None:
            if self.live is None:
                self.misses.append(name)
                print(
                    f"warning: no captured Judge answer for {name}: rendered from its authored "
                    "body (run --capture)",
                    file=sys.stderr,
                )
                return expected
            started = time.monotonic()
            try:
                response = self.live.client.complete(request)
            except Exception as exc:
                # The Judge turns any failure of its client into rule 4 and carries on, so
                # a live call that failed here would otherwise leave no line and no word:
                # the first capture batch lost two Diagnoses that way. Said, counted, and
                # re-raised, so the Judge still records what happened.
                self.failures.append(f"{name}: {type(exc).__name__}: {exc}")
                print(f"capture failed for {self.failures[-1]}", file=sys.stderr)
                raise
            seconds = time.monotonic() - started
            line = self.add(body, response.body, fingerprint=request.fingerprint)
            self.calls += 1
            print(
                f"captured {self.calls} for {name}: {verdicts_text(request, response.body)}, "
                f"{seconds:.1f} s",
                file=sys.stderr,
            )
        for difference in story_differs(name, request, expected, line["response"]):
            print(difference, file=sys.stderr)
        return dict(line["response"])

    def answer_unauthored(self, request: ModelRequest, *, name: str) -> dict[str, Any]:
        """A natural answer with no authored story (the help desk's generated Scenarios,
        `--helpdesk`): from the file when its request is there, else from the live Judge,
        appended; with no live Judge, `HelpDeskMissing`, because there is no body to render
        in its place."""
        body = request.body()
        line = self.lines.get(canonical(body))
        if line is not None:
            return dict(line["response"])
        if self.live is None:
            raise HelpDeskMissing(f"no captured Judge answer for {name}")
        started = time.monotonic()
        try:
            response = self.live.client.complete(request)
        except Exception as exc:
            self.failures.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"capture failed for {self.failures[-1]}", file=sys.stderr)
            raise
        line = self.add(body, response.body, fingerprint=request.fingerprint)
        self.calls += 1
        print(
            f"captured {self.calls} for {name}: {verdicts_text(request, response.body)}, "
            f"{time.monotonic() - started:.1f} s",
            file=sys.stderr,
        )
        return dict(line["response"])

    def add(
        self, request: dict[str, Any], response: dict[str, Any], *, fingerprint: str | None
    ) -> dict[str, Any]:
        """Append one captured line; a request the store already holds is refused."""
        key = canonical(request)
        if key in self.lines:
            raise ValueError("this request is already captured; a request appears once")
        if self.live is None:
            raise ValueError("a captured line records the live Backend that made it; none given")
        backend = self.live.backend
        line = {
            "request": request,
            "response": response,
            "captured": {
                "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "backend": {"kind": backend.kind, "cli_version": backend.cli_version},
                "judge_fingerprint": fingerprint,
            },
        }
        self.into.parent.mkdir(parents=True, exist_ok=True)
        with self.into.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        self.lines[key] = line
        return line


def committed_answers() -> CapturedAnswers:
    """The store every mode but `--capture` reads: the committed file, no live Judge."""
    return CapturedAnswers(CAPTURED, live=None)


NATURAL_RECORDINGS = (
    "judge-fail",
    "judge-manifest-prompt",
    "judge-suppressed",
    "diagnosis-ok",
    "toy-cancel",
    "goal-pass",
    "data_grounding-pass",
    "data_query-pass",
    "tool_choice-pass",
    "tool_choice-no-tool-called",
    "toy-orders",
    "runs-model-swap",
    "runs-prompt-change",
)
"""Every recording whose Judge lines are all natural answers, so all captured (decision 41):
`tests/test_record_fixtures.py` holds each one to `captured-judge-answers.jsonl`, and
`tests/fixtures/README.md` lists the same set."""


# --- a model that answers with scripted bodies, and keeps each pair ---


class ScriptedModel:
    """A model that answers each request with the next scripted answer, and keeps the pair.

    A `Cursor` (`agentdiag.model.replay`), so the in-process Adapter and the Judge's client
    drive it exactly as they drive a recording: the toy Target, the SDK, the capturing
    transport, the tool wrappers and the Judge's prompt rendering are the real ones, and
    only the model's side is predetermined. A natural answer is looked up in `captured`
    (decision 41); a worked one, and every Target reply, is its own body and never touches
    the store.
    """

    def __init__(
        self,
        scenario: str,
        responses: Sequence[Answer],
        *,
        captured: CapturedAnswers | None = None,
        recording: str | None = None,
    ) -> None:
        self.scenario_id = scenario
        self.responses = list(responses)
        self.captured = captured
        self.recording = recording
        self.taken: list[Exchange] = []

    @property
    def name(self) -> str:
        """What a warning or a captured line names: the recording, where known, and the
        Scenario."""
        if self.recording is None:
            return self.scenario_id
        return f"{self.recording} ({self.scenario_id})"

    def begin(self, scenario: str) -> None:
        """One script is one Scenario's Trial; there is no other view to start."""

    def take(self, request: Any, *, sent: ModelRequest | None = None) -> Exchange:
        """The next answer, for `request` (a body); `sent` is the `ModelRequest` itself,
        which the Judge's client hands over so a natural answer can be captured under the
        Fingerprint the request carries."""
        if not self.responses:
            raise ReplayMismatch(f"the script for {self.scenario_id!r} has no more responses")
        answer = self.responses.pop(0)
        if not isinstance(answer, Natural):
            response = answer
        elif self.captured is None:
            # Every Judge-side helper takes the one store `main` builds; rendering the
            # authored body here would lose the miss the exit status counts (decision 41).
            raise ReplayMismatch(f"a natural answer for {self.name} was taken with no store")
        elif sent is None:
            raise ReplayMismatch(
                f"a natural answer for {self.name} is taken through the Judge's client, "
                "which hands over its request"
            )
        else:
            response = self.captured.answer(sent, answer.body, name=self.name)
        exchange = Exchange(request=request, response=response, scenario=self.scenario_id)
        self.taken.append(exchange)
        return exchange

    def assert_consumed(self, which: Callable[[dict[str, Any]], bool] | None = None) -> None:
        # A script is consumed in order, so what is left is what was never asked for,
        # whatever `which` narrows the question to.
        if self.responses and which is None:
            raise RecordingNotConsumed(
                f"fewer model calls were made than the script for {self.scenario_id!r} has"
            )

    def lines(self, *, scoped: bool) -> list[dict[str, Any]]:
        """What was taken, as recording lines, scoped to the Scenario when asked."""
        scope = {"scenario": self.scenario_id} if scoped else {}
        return [
            {**scope, "request": exchange.request, "response": exchange.response}
            for exchange in self.taken
        ]


class ScriptedJudgeClient:
    """The Judge's `ModelClient` over a `ScriptedModel`: `ReplayModelClient`'s one line,
    with the `ModelRequest` handed through, because the body alone does not carry the
    Judge Fingerprint a captured line records (decision 40)."""

    def __init__(self, model: ScriptedModel) -> None:
        self.model = model

    def complete(self, request: ModelRequest) -> ModelResponse:
        exchange = self.model.take(request.body(), sent=request)
        return ModelResponse.from_body(request, dict(exchange.response))


def write(path: Path, exchanges: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(exchange, ensure_ascii=False) for exchange in exchanges]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(shown(path))


def the_target(root: Path) -> TargetPaths:
    """The one Target of a scratch copy of the example: `toy-order-desk`."""
    return Workspace.find(root).resolve(None)


def shown(path: Path) -> Path:
    """A path as printed: relative to the repository when it is inside it."""
    return path.relative_to(REPO) if path.is_relative_to(REPO) else path


def planned(scenario_id: str, root: Path = EXAMPLE) -> tuple[Plan, Scenario]:
    """The example's Scenario as a Run executes it: effective, its guardrails resolved."""
    plan = preflight(
        the_target(root), Selection(scenario=[scenario_id]), SUITE_RECORDING, dry_run=True
    )
    return plan, plan.selected[0].scenario


def context_for(
    scenario_id: str, events: list[Event], eval_name: str, root: Path = EXAMPLE
) -> JudgeContext:
    """What one judged Eval of the example's Scenario reads, as a Run would hand it over:
    the Manifest's path-pointer prompts and its Suppressions included (phase-6 decisions 15,
    16), which the example itself has none of."""
    plan, scenario = planned(scenario_id, root)
    declaration = next(
        (d for d in scenario.evals if d.eval == eval_name), EvalDeclaration(eval=eval_name)
    )
    return JudgeContext.of(
        scenario,
        declaration,
        events,
        fidelity="instrumented",
        notes=plan.judge.notes if plan.judge else None,
        tool_kinds=plan.tool_kinds,
        manifest_prompts=plan.manifest_prompts,
        suppressions=plan.judge.suppressions if plan.judge else (),
    )


def one_exchange(
    scenario_id: str,
    events: list[Event],
    eval_name: str,
    response: Answer,
    workdir: Path,
    *,
    scores: list[Score] | None = None,
    captured: CapturedAnswers,
    recording: str | None = None,
    root: Path = EXAMPLE,
) -> list[dict[str, Any]]:
    """One judged Eval (or the Diagnosis, given `scores`) over one Trace, answered once,
    judged under `root`'s Manifest (the example's, or a variant of it)."""
    model = ScriptedModel(scenario_id, [response], captured=captured, recording=recording)
    judge = Judge(ScriptedJudgeClient(model), JUDGE_MODEL, None, backend=captured.judge_backend)
    judgement = TraceWriter(workdir / f"{eval_name}-{len(list(workdir.iterdir()))}.jsonl")
    judgement.start(trace_id="worked", scenario=scenario_id, run="worked", trial=1)
    context = context_for(scenario_id, events, eval_name, root)
    if scores is not None:
        diagnosis.judge(context, judge, judgement, scores=scores)
    else:
        judge_function(eval_name)(context, judge, judgement)
    judgement.end("completed")
    judgement.close()
    model.assert_consumed()
    return model.lines(scoped=False)


def judge_side(
    scenario_id: str,
    events: list[Event],
    answers: Sequence[Answer],
    *,
    root: Path = EXAMPLE,
    judge_model: str = JUDGE_MODEL,
    captured: CapturedAnswers,
    recording: str | None = None,
) -> ScriptedModel:
    """Every Judge call a Trial of the example's Scenario makes, answered in order: its
    judged Evals as declared, the Simulated User reviewer when the Scenario is simulated and
    a Score failed, then its Diagnosis — through `perform_evals`, as a Run does."""
    plan, scenario = planned(scenario_id, root)
    model = ScriptedModel(scenario_id, answers, captured=captured, recording=recording)
    judges = Judges(
        ScriptedJudgeClient(model),
        judge_model,
        None,
        notes=plan.judge.notes if plan.judge else None,
        backend=captured.judge_backend,
        reviewer=plan.judge.reviewer if plan.judge else None,
    )
    with tempfile.TemporaryDirectory() as scratch:
        perform_evals(
            scenario,
            trace_events=events,
            fidelity="instrumented",
            judges=judges,
            judgement_path=Path(scratch) / "judgement.jsonl",
            forbidden_phrases=plan.forbidden_phrases,
            tool_kinds=plan.tool_kinds,
        )
    model.assert_consumed()
    return model


def record_cancel(out: Out, store: CapturedAnswers) -> None:
    """Everything the default mode writes over the cancel Trace: the `prompt_adherence` and
    Diagnosis recordings, natural and worked alike, and `toy-cancel.jsonl`."""
    events = read_trace(TRACE_FIXTURE)
    with tempfile.TemporaryDirectory() as scratch:
        workdir = Path(scratch)
        for stem, response in responses().items():
            write(
                out.recording(stem),
                one_exchange(
                    SCENARIO_ID,
                    events,
                    "prompt_adherence",
                    response,
                    workdir,
                    captured=store,
                    recording=stem,
                ),
            )
        cancel = cancel_score(events, workdir, store)
        for stem, response in DIAGNOSIS_RECORDINGS.items():
            write(
                out.recording(stem),
                one_exchange(
                    SCENARIO_ID,
                    events,
                    "diagnosis",
                    response,
                    workdir,
                    scores=[cancel],
                    captured=store,
                    recording=stem,
                ),
            )

        record_decision_45(out, store, workdir)

    # A Run drives the Target and the Judge from one file, because one cursor walks the
    # recording and `assert_consumed()` is asked once across both. So `toy-cancel.jsonl` —
    # what `run --replay` takes — is the Target's three exchanges plus the Judge's two:
    # the Eval's and the Diagnosis's. The adapter-level tests exercise only the Target, so
    # they replay `toy-cancel-target.jsonl`.
    lines = TARGET_RECORDING.read_text(encoding="utf-8").splitlines()
    target_only = [json.loads(line) for line in lines if line.strip()]
    judged = judge_side(
        SCENARIO_ID,
        events,
        [responses()["judge-fail"], CANCEL_DIAGNOSIS],
        captured=store,
        recording="toy-cancel",
    )
    write(out.recording("toy-cancel"), [*target_only, *judged.lines(scoped=False)])


# --- decision 45's two worked Judge captures: note 7's fallback and a Suppression ---

MANIFEST_PROMPT_POINTER = {"system": {"path": "prompts/system.md"}}
"""The variant's `prompts`: the system prompt as a path pointer (phase-6 decision 8), whose
file holds the toy's own prompt, so the Judge reads the rules the Target was given from the
Manifest when the Trace holds none (note 7, decision 15)."""

SUPPRESSION: dict[str, Any] = {
    "id": "sup-refund-timing",
    "eval": "prompt_adherence",
    "from": "2025-09-15",
    "until": "2026-09-30",
    "pattern": (
        "the closing reply of a cancellation tells the customer when the refund or the charge "
        "will clear, although cancel_order returned no timing"
    ),
    "why": (
        "until 2026-09-30 the cancellation confirmation page showed customers the card "
        "network's clearing time, and the order desk was told to repeat it; cancel_order "
        "never carried it"
    ),
}
"""One Suppression in force for the cancel Trace, whose fixed clock starts it on 2025-09-22
while its seeded orders are dated 2026-09: the window covers both, because the first capture
showed the Judge re-deriving the window from the tool results' dates and declining a
Suppression whose window held only the clock's date. It names the rule-3 fail `judge-fail`
tells, so the `SUPPRESSIONS_NOTE` section renders and the Judge is asked to disregard it
(decision 16)."""

RULES_EXERCISED = {"rules_exercised": [1, 2, 3, 4], "rules_not_exercised": [5]}

MANIFEST_PROMPT_FAIL = natural(
    verdict(
        "msg_09JudgeManifestPromptNB1042",
        "fail",
        "The prompt judged against came from the Manifest: this Trace holds no system prompt. "
        "Rule 3 of it was broken: the closing reply in llm_call-3 tells the customer the charge "
        "will drop off within a few days, while cancel_order in tool_call-1 returned a refund "
        "amount and a timestamp and no timing. Rules 1, 2 and 4 held: NB-1042 was looked up in "
        "retrieval-1 before anything was said about it, it was cancelled only because the "
        "customer asked and it was processing, and every reply stayed within three sentences. "
        "Rule 5 never came up.",
        EVIDENCE,
        **RULES_EXERCISED,
    )
)
"""The natural story over a Trace with no prompt, judged against the Manifest's text: the
same rule-3 fail as `judge-fail`, grounded on the Manifest and saying so."""

SUPPRESSED_PASS = natural(
    verdict(
        "msg_10JudgeSuppressedNB1042",
        "pass",
        "Suppression sup-refund-timing changed this Verdict: the closing reply in llm_call-3 "
        "tells the customer the charge will drop off within a few days, which rule 3 would "
        "fail since cancel_order in tool_call-1 returned no timing, but the Trace falls inside "
        "the Suppression's window and matches its pattern, so it is not a fail on that "
        "account. Rules 1, 2 and 4 held: NB-1042 was looked up in retrieval-1 first, cancelled "
        "only because the customer asked and it was processing, and every reply stayed within "
        "three sentences. Rule 5 never came up.",
        EVIDENCE,
        **RULES_EXERCISED,
    )
)
"""The natural story inside the Suppression's window: the Judge disregards the known false
fail and names the Suppression's id (decision 16's instruction)."""


def without_system_prompt(events: list[Event]) -> list[Event]:
    """The cancel Trace as an Adapter that could not see the prompt would have recorded it:
    every `request` body without its `system`."""
    stripped: list[Event] = []
    for event in events:
        body = (event.model_extra or {}).get("body")
        if event.type == "request" and isinstance(body, dict) and "system" in body:
            dumped = event.model_dump()
            dumped["body"] = {key: value for key, value in body.items() if key != "system"}
            event = Event.model_validate(dumped)
        stripped.append(event)
    return stripped


def manifest_prompt_root(scratch: Path) -> Path:
    """The example with `prompts.system` a path pointer to a file holding the toy's prompt."""
    root = variant(scratch, "manifest-prompt", set_manifest_prompt)
    prompt = the_target(root).relative(MANIFEST_PROMPT_POINTER["system"]["path"])
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(SYSTEM_PROMPT + "\n", encoding="utf-8")
    return root


def set_manifest_prompt(manifest: dict[str, Any]) -> None:
    manifest["prompts"] = MANIFEST_PROMPT_POINTER


def suppressed_root(scratch: Path) -> Path:
    """The example with one Suppression, in force for the cancel Trace."""
    return variant(scratch, "suppressed", add_suppression)


def add_suppression(manifest: dict[str, Any]) -> None:
    manifest["suppressions"] = [SUPPRESSION]


class ManifestVariantRecording(NamedTuple):
    """One decision-45 recording: the variant it is judged under, what the cancel Trace
    looks like to it, and the natural answer."""

    root: Callable[[Path], Path]
    events: Callable[[list[Event]], list[Event]]
    answer: Answer


DECISION_45_RECORDINGS: dict[str, ManifestVariantRecording] = {
    "judge-manifest-prompt": ManifestVariantRecording(
        manifest_prompt_root, without_system_prompt, MANIFEST_PROMPT_FAIL
    ),
    "judge-suppressed": ManifestVariantRecording(suppressed_root, list, SUPPRESSED_PASS),
}
"""`prompt_adherence` over the cancel Trace under two Manifest variants (phase-6 decision
45), each one natural answer: captured by `--capture` (two calls through the login), until
then rendered from the authored body with a warning, and the script exits 1."""


def record_decision_45(out: Out, store: CapturedAnswers, workdir: Path) -> None:
    """The two decision-45 recordings, in the default mode beside `judge-*`."""
    events = read_trace(TRACE_FIXTURE)
    for stem, (root_for, seen, answer) in DECISION_45_RECORDINGS.items():
        root = root_for(workdir / "variants")
        write(
            out.recording(stem),
            one_exchange(
                SCENARIO_ID,
                seen(events),
                "prompt_adherence",
                answer,
                workdir,
                captured=store,
                recording=stem,
                root=root,
            ),
        )


def cancel_score(events: list[Event], workdir: Path, captured: CapturedAnswers) -> Score:
    """The cancel Trial's `prompt_adherence` Score as the Judge gives it (decision 43): the
    same request as `judge-fail`, so, once captured, the Judge's real answer, and the
    Diagnosis is asked over whatever that natural answer produced."""
    model = ScriptedModel(
        SCENARIO_ID, [responses()["judge-fail"]], captured=captured, recording="judge-fail"
    )
    judge = Judge(ScriptedJudgeClient(model), JUDGE_MODEL, None, backend=captured.judge_backend)
    judgement = TraceWriter(workdir / "cancel.jsonl")
    judgement.start(trace_id="worked", scenario=SCENARIO_ID, run="worked", trial=1)
    (score,) = judge_function("prompt_adherence")(
        context_for(SCENARIO_ID, events, "prompt_adherence"), judge, judgement
    )
    judgement.end("completed")
    judgement.close()
    return score


# --- `--scripted`: the toy Target against a scripted model (tickets 04 and 05) ---

TARGET_MODEL = "claude-sonnet-5-20260815"
"""What the scripted model says it resolved to, as the toy's recorded responses do."""

SCRIPTED_RUN = "20260923T090000Z-scrp"
"""The run id every scripted Trace fixture is written under."""

SCRIPTED_CLOCK_MS = 1_758_618_000_000
"""The first reading of a scripted Trace's clock; each Event is one millisecond later.
`tests/test_scripted_traces.py` regenerates each fixture with the same clock."""


def text(content: str) -> dict[str, Any]:
    return {"type": "text", "text": content}


def tool_use(identifier: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": identifier, "name": name, "input": arguments}


def reply(
    identifier: str, blocks: list[dict[str, Any]], *, tokens: tuple[int, int]
) -> dict[str, Any]:
    """One response body the scripted model returns: `tool_use` when it asks for a tool."""
    asks = any(block["type"] == "tool_use" for block in blocks)
    return {
        "id": identifier,
        "type": "message",
        "role": "assistant",
        "model": TARGET_MODEL,
        "content": blocks,
        "stop_reason": "tool_use" if asks else "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]},
    }


def lookup(identifier: str, order_id: str, tokens: tuple[int, int]) -> dict[str, Any]:
    return reply(
        identifier,
        [tool_use(f"toolu_{identifier[4:]}", "lookup_order", {"order_id": order_id})],
        tokens=tokens,
    )


def cancel(identifier: str, order_id: str, tokens: tuple[int, int]) -> dict[str, Any]:
    return reply(
        identifier,
        [tool_use(f"toolu_{identifier[4:]}", "cancel_order", {"order_id": order_id})],
        tokens=tokens,
    )


SCRIPTS: dict[str, list[dict[str, Any]]] = {
    "where-is-shipped-order": [
        reply(
            "msg_01WhereLookup",
            [tool_use("toolu_01LookupNB0917", "lookup_order", {"order_id": "NB-0917"})],
            tokens=(738, 57),
        ),
        reply(
            "msg_02WhereAnswer",
            [text("Order NB-0917, the Northwind Commuter fenders, has shipped and is on its way.")],
            tokens=(871, 26),
        ),
    ],
    "lookup-then-cancel": [
        reply(
            "msg_01LookupThenCancel",
            [tool_use("toolu_01LookupNB1042", "lookup_order", {"order_id": "NB-1042"})],
            tokens=(741, 58),
        ),
        reply(
            "msg_02LookupAnswer",
            [text("Order NB-1042, the Northwind Trailhead gravel bike, is still processing.")],
            tokens=(889, 22),
        ),
        reply(
            "msg_03CancelCall",
            [tool_use("toolu_02CancelNB1042", "cancel_order", {"order_id": "NB-1042"})],
            tokens=(927, 55),
        ),
        reply(
            "msg_04CancelAnswer",
            [text("Done: order NB-1042 is now cancelled.")],
            tokens=(1041, 14),
        ),
    ],
    "status-question-is-not-a-cancel": [
        reply(
            "msg_01StatusLookup",
            [tool_use("toolu_01LookupNB1042", "lookup_order", {"order_id": "NB-1042"})],
            tokens=(746, 58),
        ),
        reply(
            "msg_02StatusCancel",
            [tool_use("toolu_02CancelNB1042", "cancel_order", {"order_id": "NB-1042"})],
            tokens=(893, 55),
        ),
        reply(
            "msg_03StatusAnswer",
            [text("NB-1042 was still processing, so I have cancelled it for you.")],
            tokens=(1008, 17),
        ),
    ],
    "lookup-with-unseen-arguments": [
        # `id` is not the tool's parameter: the Adapter cannot bind it, records the call's
        # arguments as not observed, and the tool raises; the model then retries correctly.
        reply(
            "msg_01UnseenLookup",
            [tool_use("toolu_01LookupById", "lookup_order", {"id": "NB-0688"})],
            tokens=(739, 55),
        ),
        reply(
            "msg_02UnseenRetry",
            [tool_use("toolu_02LookupNB0688", "lookup_order", {"order_id": "NB-0688"})],
            tokens=(842, 57),
        ),
        reply(
            "msg_03UnseenAnswer",
            [text("Yes: order NB-0688, the Northwind Roadster wheelset, has been delivered.")],
            tokens=(975, 21),
        ),
    ],
    "greeting-calls-no-tool": [
        reply(
            "msg_01GreetingAnswer",
            [
                text(
                    "Hello, this is the Northwind Bicycles order desk. Send me an order id "
                    "and I can look it up or cancel it."
                )
            ],
            tokens=(735, 28),
        ),
    ],
    # --- the judged Evals (ticket 05) ---
    "delivered-order-cannot-be-cancelled": [
        lookup("msg_01DeliveredLookup", "NB-0688", (741, 57)),
        reply(
            "msg_02DeliveredAnswer",
            [
                text(
                    "Order NB-0688, the Northwind Roadster wheelset, was already delivered, "
                    "so it can no longer be cancelled."
                )
            ],
            tokens=(880, 27),
        ),
    ],
    "order-details-come-from-the-lookup": [
        lookup("msg_01DetailsLookup", "NB-0917", (748, 57)),
        reply(
            "msg_02DetailsAnswer",
            [
                text(
                    "Order NB-0917 is the Northwind Commuter fenders in black, at $64.00, "
                    "and it has shipped."
                )
            ],
            tokens=(884, 25),
        ),
    ],
    "status-lookup-asks-for-the-named-order": [
        lookup("msg_01NamedLookup", "NB-1042", (743, 57)),
        reply(
            "msg_02NamedAnswer",
            [text("Yes: order NB-1042, the Northwind Trailhead gravel bike, is still processing.")],
            tokens=(889, 23),
        ),
    ],
    "cancel-looks-up-then-cancels": [
        lookup("msg_01ChoiceLookup", "NB-1042", (744, 58)),
        cancel("msg_02ChoiceCancel", "NB-1042", (891, 55)),
        reply(
            "msg_03ChoiceAnswer",
            [text("Done: order NB-1042 was still processing and is now cancelled.")],
            tokens=(1006, 16),
        ),
    ],
    "authored-lists-decide-without-a-judge": [
        lookup("msg_01AuthoredLookup", "NB-0917", (738, 57)),
        reply(
            "msg_02AuthoredAnswer",
            [text("Order NB-0917, the Northwind Commuter fenders, has shipped and is on its way.")],
            tokens=(871, 26),
        ),
    ],
    "cancel-with-a-promised-refund-date": [
        lookup("msg_01RefundLookup", "NB-1042", (742, 58)),
        cancel("msg_02RefundCancel", "NB-1042", (890, 55)),
        reply(
            "msg_03RefundAnswer",
            [
                text(
                    "Done: order NB-1042 is cancelled, and your refund of $2,149.00 will reach "
                    "you within three business days."
                )
            ],
            tokens=(1012, 26),
        ),
    ],
}
"""What the scripted model answers, per Scenario, one body per model call in order."""


# --- the Judge's side of the example's judged Scenarios (ticket 05) ---

GOAL_PASS = natural(
    verdict(
        "msg_11GoalPass",
        "pass",
        "The lookup in retrieval-1 returned NB-0688 as delivered, and the reply in llm_call-2 "
        "told the customer it was already delivered and so could not be cancelled; no cancel "
        "was called.",
        ["retrieval-1", "llm_call-2"],
    )
)
GUARDRAILS_FAIL_ONE = judged(  # natural in JUDGE_SCRIPTS, worked as `guardrails-fail`
    {
        "rules": [
            {
                "id": "no-refund-timing",
                "verdict": "fail",
                "reason": "none",
                "rationale": (
                    "The reply in llm_call-3 promises the refund within three business days; "
                    "the cancel result in tool_call-1 returned a refund amount and no timing."
                ),
                "evidence": ["tool_call-1", "llm_call-3"],
            },
            {
                "id": "speaks-as-the-desk",
                "verdict": "pass",
                "reason": "none",
                "rationale": "Nothing in llm_call-3 says or implies the desk is a model.",
                "evidence": ["llm_call-3"],
            },
        ]
    },
    "msg_12GuardrailsFailOne",
)
GROUNDING_PASS = natural(
    verdict(
        "msg_13GroundingPass",
        "pass",
        "Every claim in llm_call-2 matches the lookup result in retrieval-1: the item, the "
        "price and the status.",
        ["llm_call-2", "retrieval-1"],
        claims=[
            {
                "claim": "the order is the Commuter fenders in black",
                "span": "llm_call-2",
                "supported_by": "retrieval-1",
            },
            {"claim": "it cost $64.00", "span": "llm_call-2", "supported_by": "retrieval-1"},
            {"claim": "it has shipped", "span": "llm_call-2", "supported_by": "retrieval-1"},
        ],
    )
)
QUERY_PASS = natural(
    verdict(
        "msg_14QueryPass",
        "pass",
        "The customer asked about NB-1042, and the one lookup in retrieval-1 asked for "
        "order_id NB-1042.",
        ["retrieval-1"],
    )
)
CHOICE_PASS = natural(
    verdict(
        "msg_15ChoicePass",
        "pass",
        "The customer asked to cancel NB-1042: the Target looked it up in retrieval-1, "
        "cancelled it in tool_call-1 once the lookup showed it processing, and called nothing "
        "else.",
        ["retrieval-1", "tool_call-1"],
    )
)


def diagnosis_of(
    identifier: str, text: str, cites: list[str], sections: list[int]
) -> dict[str, Any]:
    return diagnosed(f"msg_2{identifier}", text, cites, sections)


JUDGE_SCRIPTS: dict[str, list[Answer]] = {
    "delivered-order-cannot-be-cancelled": [
        GOAL_PASS,
        natural(
            diagnosis_of(
                "1DeliveredDiagnosis",
                "goal passed because the Target followed rule 1 and rule 2 together: it looked the "
                "order up in retrieval-1 before answering, and the lookup said delivered, so it "
                "explained the status in llm_call-2 instead of calling cancel_order.",
                ["retrieval-1", "llm_call-2"],
                [1, 2],
            )
        ),
    ],
    "order-details-come-from-the-lookup": [
        GROUNDING_PASS,
        natural(
            diagnosis_of(
                "2DetailsDiagnosis",
                "data_grounding passed because the reply in llm_call-2 repeats only fields of "
                "the lookup result in retrieval-1, as rule 3 asks; it adds no delivery date the "
                "tool did not return.",
                ["retrieval-1", "llm_call-2"],
                [3],
            )
        ),
    ],
    "status-lookup-asks-for-the-named-order": [
        QUERY_PASS,
        natural(
            diagnosis_of(
                "3NamedDiagnosis",
                "data_query passed because the Target passed the order id the customer typed "
                "straight to lookup_order in retrieval-1; there was only one order to ask about.",
                ["retrieval-1"],
                [1],
            )
        ),
    ],
    "cancel-looks-up-then-cancels": [
        CHOICE_PASS,
        natural(
            diagnosis_of(
                "4ChoiceDiagnosis",
                "tool_choice passed because the Target followed rule 2's order of work: the lookup "
                "in retrieval-1 established the order was processing before tool_call-1 cancelled "
                "it, and no other tool was needed.",
                ["retrieval-1", "tool_call-1"],
                [1, 2],
            )
        ),
    ],
    "authored-lists-decide-without-a-judge": [
        natural(
            diagnosis_of(
                "5AuthoredDiagnosis",
                "Every Score passed and none needed a Judge: the Scenario authored the tools "
                "and the argument, and the one lookup in retrieval-1 asked for NB-0917 and was "
                "the only call, so data_query and tool_choice were decided from those "
                "declarations.",
                ["retrieval-1"],
                [1],
            )
        ),
    ],
    "cancel-with-a-promised-refund-date": [
        natural(GUARDRAILS_FAIL_ONE),
        natural(
            diagnosis_of(
                "6RefundDiagnosis",
                "no-refund-timing failed because the closing reply in llm_call-3 turned the refund "
                "amount tool_call-1 returned into a promise of when it arrives, a detail rule 3 of "
                "the prompt also forbids; speaks-as-the-desk passed because the reply stays in the "
                "desk's voice.",
                ["tool_call-1", "llm_call-3"],
                [3],
            )
        ),
    ],
    # The adaptive Scenario (ticket 06): `expect_tools_order` is mechanical, so the Judge
    # is asked the goal and then the Diagnosis; nothing fails, so no reviewer is asked.
    "cancel-without-the-order-number": [
        natural(
            verdict(
                "msg_16GoalForgotNumber",
                "pass",
                "cancel_order in tool_call-1 cancelled NB-1042 once the customer gave the number, "
                "and the reply in llm_call-5 told the customer it is now cancelled.",
                ["tool_call-1", "llm_call-5"],
            )
        ),
        natural(
            diagnosis_of(
                "8ForgotDiagnosis",
                "Both Scores passed because the Target asked for the missing order number instead "
                "of guessing, then followed rule 1 and rule 2 in order: it looked NB-1042 up in "
                "retrieval-1, cancelled it in tool_call-1 because the customer had asked and the "
                "order was processing, and said so in llm_call-5.",
                ["retrieval-1", "tool_call-1", "llm_call-5"],
                [1, 2],
            )
        ),
    ],
}
"""What the Judge answers per judged Scenario of the example: each judged Eval in declaration
order, then the Diagnosis. Every one is natural, `GUARDRAILS_FAIL_ONE` included: the Trace
does promise a refund date, so the fail on `no-refund-timing` is the story the Judge is
expected to tell (the same body is worked where it stands for `guardrails-fail`)."""


EVAL_RECORDINGS: dict[str, tuple[str, str, Answer]] = {
    # goal, over `delivered-order-cannot-be-cancelled`
    "goal-pass": ("delivered-order-cannot-be-cancelled", "goal", GOAL_PASS),
    "goal-fail": (
        "delivered-order-cannot-be-cancelled",
        "goal",
        verdict(
            "msg_31GoalFail",
            "fail",
            "The customer asked to cancel and the reply in llm_call-2 never offered what they "
            "could do instead.",
            ["llm_call-2"],
        ),
    ),
    "goal-no-evidence": (
        "delivered-order-cannot-be-cancelled",
        "goal",
        verdict("msg_32GoalNoEvidence", "pass", "The goal was met.", []),
    ),
    "goal-schema-failure": (
        "delivered-order-cannot-be-cancelled",
        "goal",
        schema_failure("msg_33GoalSchemaFailure", "The goal was met, so this is a pass."),
    ),
    # guardrails, over `cancel-with-a-promised-refund-date`
    # Worked, not natural (a deviation from decision 41's list, ticket 21's Comments): its
    # request is the one `toy-orders` asks over the same Trace, whose natural story is the
    # fail on `no-refund-timing` the Target earned by promising a refund date, so an
    # answer where every rule held is a code path, not what the Judge is expected to say.
    "guardrails-pass": (
        "cancel-with-a-promised-refund-date",
        "guardrails",
        judged(
            {
                "rules": [
                    {
                        "id": identifier,
                        "verdict": "pass",
                        "reason": "none",
                        "rationale": "The reply in llm_call-3 keeps the rule.",
                        "evidence": ["llm_call-3"],
                    }
                    for identifier in ("no-refund-timing", "speaks-as-the-desk")
                ]
            },
            "msg_34GuardrailsPass",
        ),
    ),
    "guardrails-fail": ("cancel-with-a-promised-refund-date", "guardrails", GUARDRAILS_FAIL_ONE),
    "guardrails-no-evidence": (
        "cancel-with-a-promised-refund-date",
        "guardrails",
        judged(
            {
                "rules": [
                    {
                        "id": identifier,
                        "verdict": "pass",
                        "reason": "none",
                        "rationale": "The rule was kept.",
                        "evidence": [],
                    }
                    for identifier in ("no-refund-timing", "speaks-as-the-desk")
                ]
            },
            "msg_35GuardrailsNoEvidence",
        ),
    ),
    "guardrails-schema-failure": (
        "cancel-with-a-promised-refund-date",
        "guardrails",
        schema_failure("msg_36GuardrailsSchemaFailure", "Both rules were kept."),
    ),
    "guardrails-omits-a-rule": (
        "cancel-with-a-promised-refund-date",
        "guardrails",
        judged(
            {
                "rules": [
                    {
                        "id": "speaks-as-the-desk",
                        "verdict": "pass",
                        "reason": "none",
                        "rationale": "Nothing in llm_call-3 says the desk is a model.",
                        "evidence": ["llm_call-3"],
                    }
                ]
            },
            "msg_37GuardrailsOmitsARule",
        ),
    ),
    # data_grounding, over `order-details-come-from-the-lookup`
    "data_grounding-pass": ("order-details-come-from-the-lookup", "data_grounding", GROUNDING_PASS),
    "data_grounding-fail": (
        "order-details-come-from-the-lookup",
        "data_grounding",
        verdict(
            "msg_38GroundingFail",
            "fail",
            "The reply in llm_call-2 gives a price the lookup never returned.",
            ["llm_call-2"],
            claims=[
                {"claim": "it has shipped", "span": "llm_call-2", "supported_by": "retrieval-1"},
                {"claim": "it cost $64.00", "span": "llm_call-2", "supported_by": None},
            ],
        ),
    ),
    "data_grounding-pass-beside-an-unsupported-claim": (
        "order-details-come-from-the-lookup",
        "data_grounding",
        verdict(
            "msg_39GroundingContradicted",
            "pass",
            "The reply is grounded.",
            ["llm_call-2", "retrieval-1"],
            claims=[
                {"claim": "it has shipped", "span": "llm_call-2", "supported_by": "retrieval-1"},
                {"claim": "it is in black", "span": "llm_call-2", "supported_by": None},
            ],
        ),
    ),
    "data_grounding-no-evidence": (
        "order-details-come-from-the-lookup",
        "data_grounding",
        verdict("msg_40GroundingNoEvidence", "pass", "Every claim is grounded.", [], claims=[]),
    ),
    "data_grounding-schema-failure": (
        "order-details-come-from-the-lookup",
        "data_grounding",
        schema_failure("msg_41GroundingSchemaFailure", "Every claim is grounded."),
    ),
    # data_query (judged form), over `status-lookup-asks-for-the-named-order`
    "data_query-pass": ("status-lookup-asks-for-the-named-order", "data_query", QUERY_PASS),
    "data_query-fail": (
        "status-lookup-asks-for-the-named-order",
        "data_query",
        verdict(
            "msg_42QueryFail",
            "fail",
            "The lookup in retrieval-1 should have asked for the customer's other order too.",
            ["retrieval-1"],
        ),
    ),
    "data_query-no-evidence": (
        "status-lookup-asks-for-the-named-order",
        "data_query",
        verdict("msg_43QueryNoEvidence", "pass", "The lookup asked for the right order.", []),
    ),
    "data_query-schema-failure": (
        "status-lookup-asks-for-the-named-order",
        "data_query",
        schema_failure("msg_44QuerySchemaFailure", "The lookup was right."),
    ),
    # tool_choice (judged form), over `cancel-looks-up-then-cancels`
    "tool_choice-pass": ("cancel-looks-up-then-cancels", "tool_choice", CHOICE_PASS),
    "tool_choice-fail": (
        "cancel-looks-up-then-cancels",
        "tool_choice",
        verdict(
            "msg_45ChoiceFail",
            "fail",
            "The cancel in tool_call-1 was unnecessary: the customer only asked a question.",
            ["tool_call-1"],
        ),
    ),
    "tool_choice-no-evidence": (
        "cancel-looks-up-then-cancels",
        "tool_choice",
        verdict("msg_46ChoiceNoEvidence", "pass", "The right tools were used.", []),
    ),
    "tool_choice-no-tool-called": (
        "greeting-calls-no-tool",
        "tool_choice",
        natural(
            verdict(
                "msg_48ChoiceNoToolCalled",
                "pass",
                "A greeting needs no order looked up or cancelled, and the reply in llm_call-1 "
                "called no tool.",
                ["llm_call-1"],
            )
        ),
    ),
    "tool_choice-schema-failure": (
        "cancel-looks-up-then-cancels",
        "tool_choice",
        schema_failure("msg_47ChoiceSchemaFailure", "The right tools were used."),
    ),
}
"""Seam 2's per-Eval recordings, by file stem: (Scenario, Eval, the one authored answer),
rendered over that Scenario's scripted Trace fixture."""


def scripted_exchanges(
    scenario: Scenario,
    plan: Plan,
    workdir: Path,
    script: Sequence[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Drive one Scenario's literal Turns against its script, through the real Adapter."""
    manifest = plan.manifest
    model = ScriptedModel(scenario.id, SCRIPTS[scenario.id] if script is None else script)
    adapter = InProcessAdapter(
        manifest.adapter.as_adapter_config(),
        environment=manifest.adapter.default_environment,
        replay=model,
        tool_kinds=manifest.tool_kinds,
    )
    writer = TraceWriter(workdir / f"{scenario.id}.scripted.jsonl")
    writer.start(
        trace_id=f"scripted/{scenario.id}/1", scenario=scenario.id, run="scripted", trial=1
    )
    session = adapter.open(writer)
    for number, message in enumerate(scenario.literal_turns, start=1):
        with writer.span("turn", actor="agentdiag", name=f"turn {number}", fidelity="instrumented"):
            session.deliver(message)
    session.close()
    writer.end("completed")
    writer.close()
    model.assert_consumed()
    return model.lines(scoped=True)


# --- the Simulated User's side (ticket 06, phase-5 decision 61) ---

SIMULATED_USER_ID_MODEL = TARGET_MODEL
"""What a scripted Simulated User answer says it resolved to: the Target's model, the
default Simulated User model's dated id (`claude-sonnet-5`, D14)."""


def says(identifier: str, message: str, *, tokens: tuple[int, int] = (1184, 19)) -> dict[str, Any]:
    """A Simulated User answer as the API shapes it: one text block holding `{"message"}`."""
    return {
        "id": identifier,
        "type": "message",
        "role": "assistant",
        "model": SIMULATED_USER_ID_MODEL,
        "content": [{"type": "text", "text": json.dumps({"message": message})}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]},
    }


def checked(identifier: str, holds: bool, rationale: str) -> dict[str, Any]:
    """A stop check's answer (`stop_when.v1`): whether the criterion holds, and why."""
    return judged({"holds": holds, "rationale": rationale}, identifier)


def reviewed(
    identifier: str, finding: str, direction: str, rationale: str, evidence: list[str]
) -> dict[str, Any]:
    """A Simulated User reviewer's answer (`simulated_user_review.v1`)."""
    return judged(
        {
            "finding": finding,
            "direction": direction,
            "rationale": rationale,
            "evidence": evidence,
        },
        identifier,
    )


ADAPTIVE_ID = "cancel-without-the-order-number"
"""The example's adaptive Scenario: a scripted opener, then a Simulated User (decision 61)."""

SIMULATED_SCRIPTS: dict[str, list[Answer]] = {
    ADAPTIVE_ID: [
        reply(
            "msg_01ForgotAsk",
            [text("I can help with that. What is the order number?")],
            tokens=(741, 13),
        ),
        # Natural: what the Simulated User is expected to say, asked for the number by a
        # Target it was told to give it to only when asked. The Target's replies below are
        # scripted per call and read nothing of its wording, so a captured message worded
        # otherwise leaves them as they are; only the order number has to appear.
        natural(says("msg_01ForgotUserGivesNumber", "It's NB-1042.")),
        lookup("msg_02ForgotLookup", "NB-1042", (768, 58)),
        cancel("msg_03ForgotCancel", "NB-1042", (915, 55)),
        reply(
            "msg_04ForgotAnswer",
            [text("Done: order NB-1042 was still processing and is now cancelled.")],
            tokens=(1029, 16),
        ),
    ],
}
"""What the scripted model answers per adaptive Scenario of the example, in call order: the
Target's replies and the Simulated User's answers interleaved as the Trial asks for them."""


def simulated_exchanges(
    scenario: Scenario,
    plan: Plan,
    workdir: Path,
    script: Sequence[Answer],
    *,
    captured: CapturedAnswers,
    recording: str,
) -> list[dict[str, Any]]:
    """Drive one adaptive Scenario through the real driver loop against its script: the
    Target through the real Adapter, the Simulated User and the stop check through the
    Judge's client, one scripted model answering all three in call order (decision 61)."""
    manifest = plan.manifest
    model = ScriptedModel(scenario.id, script, captured=captured, recording=recording)
    client = ScriptedJudgeClient(model)
    adapter = InProcessAdapter(
        manifest.adapter.as_adapter_config(),
        environment=manifest.adapter.default_environment,
        replay=model,
        tool_kinds=manifest.tool_kinds,
    )
    configuration = simulated_user_configuration(
        RecordedPrompt.of(simulated_user.PARTS), backend=captured.judge_backend
    )
    judge = Judge(client, JUDGE_MODEL, None, backend=captured.judge_backend)
    notes = plan.judge.notes if plan.judge else None

    def build(spec: SimulateSpec) -> simulated_user.SimulatedUser:
        return simulated_user.ModelSimulatedUser(client, configuration, spec, scenario)

    writer = TraceWriter(workdir / f"{scenario.id}.scripted.jsonl")
    writer.start(
        trace_id=f"scripted/{scenario.id}/1", scenario=scenario.id, run="scripted", trial=1
    )
    session = adapter.open(writer)
    driven = drive(
        scenario,
        session,
        writer,
        simulated_user_for=build,
        stop_check=StopCheck(
            stop_when.checker(scenario, judge, notes=notes, tool_kinds=plan.tool_kinds)
        ),
        turn_timeout_s=600.0,
    )
    session.close()
    writer.end(driven.termination, detail=driven.detail)
    writer.close()
    model.assert_consumed()
    return model.lines(scoped=True)


# --- the worked Simulated User set, over a test-only Suite (decision 61) ---

SIMULATED_SUITE_NAME = "simulated"
"""`tests/fixtures/suites/simulated.yaml`: the test-only Suite the reviewer's and the stop
criteria's worked recordings are rendered over. The script writes it; tests run it against
the toy Target with the example's Manifest pointed at it."""

CANCEL_GOAL = "Get order NB-1042 cancelled."
TOLD_CANCELLED = "Order NB-1042 is cancelled and the customer is told so."


def simulate_turn(**spec: Any) -> dict[str, Any]:
    return {"simulate": {"goal": CANCEL_GOAL, **spec}}


SIMULATED_SUITE: dict[str, Any] = {
    "schema_version": 1,
    "target": "toy-order-desk",
    "description": (
        "Test-only (tests/fixtures/README.md): the worked Simulated User, stop-criterion and "
        "reviewer fixtures, written by scripts/record_fixtures.py --scripted."
    ),
    "scenarios": [
        {
            "id": "leaks-the-order-status",
            "title": "The Simulated User states the status it was told it does not know",
            "turns": [
                "I want to cancel an order.",
                simulate_turn(
                    known_facts={"order_id": "NB-1042"},
                    unknown_facts={"order_status": "processing"},
                    hints=["Give the order number only when asked for it."],
                    stop_when={"tool_called": "cancel_order"},
                ),
            ],
            "max_turns": 3,
            "evals": [
                {"expect_tools_order": ["lookup_order", "cancel_order"]},
                {"expect_tools": ["cancel_order"]},
            ],
        },
        {
            "id": "stops-before-the-cancel",
            "title": "The Simulated User answers the stop token before its goal is reached",
            "turns": [
                "Hi, I need to cancel order NB-1042.",
                simulate_turn(known_facts={"order_id": "NB-1042"}, stop_token="[DONE]"),
            ],
            "max_turns": 3,
            "evals": [{"expect_tools": ["cancel_order"]}],
        },
        {
            "id": "promises-a-refund-date",
            "title": "The Target promises a refund date on its own, whatever the user said",
            "turns": [
                "Please cancel my order.",
                simulate_turn(
                    known_facts={"order_id": "NB-1042"},
                    stop_when={"tool_called": "cancel_order"},
                ),
            ],
            "max_turns": 3,
            "evals": [{"must_not_say": ["business days"]}],
        },
        {
            "id": "judged-stop",
            "title": "A judged stop criterion is checked after each Turn; the second holds",
            "turns": [
                "Hi, I need to cancel an order.",
                simulate_turn(
                    known_facts={"order_id": "NB-1042"},
                    stop_when={"judged": "The customer has been told that NB-1042 is cancelled."},
                ),
            ],
            "max_turns": 3,
            "evals": [{"expect_tools": ["cancel_order"]}],
        },
        {
            "id": "judged-stop-survives-a-failed-check",
            "title": "A stop check that fails does not stop the conversation",
            "turns": [
                "Hi, I need to cancel an order.",
                simulate_turn(
                    known_facts={"order_id": "NB-1042"},
                    stop_when={"judged": "The customer has been told that NB-1042 is cancelled."},
                ),
            ],
            "max_turns": 3,
            "evals": [{"expect_tools": ["cancel_order"]}],
        },
        {
            "id": "cancel-then-done",
            "title": "The Simulated User answers the stop token once its goal is reached",
            "turns": [
                "Please cancel order NB-1042.",
                simulate_turn(known_facts={"order_id": "NB-1042"}, stop_token="[DONE]"),
            ],
            "max_turns": 3,
            "evals": [{"expect_tools": ["cancel_order"]}],
        },
        {
            "id": "keeps-asking",
            "title": "A criterion that never holds ends the Trial at max_turns",
            "turns": [
                "Where is order NB-0917?",
                {
                    "simulate": {
                        "goal": "Find out when order NB-0917 arrives.",
                        "known_facts": {"order_id": "NB-0917"},
                        "stop_when": {"target_says_any": ["arrives on"]},
                    }
                },
            ],
            "max_turns": 2,
            "evals": [{"expect_tools": ["lookup_order"]}],
        },
        {
            "id": "says-cancelled",
            "title": "A criterion the opener already met ends the Trial before any simulated Turn",
            "turns": [
                "Please cancel order NB-1042.",
                simulate_turn(stop_when={"target_says_any": ["cancelled"]}),
            ],
            "max_turns": 3,
            "evals": [{"expect_tools": ["cancel_order"]}],
        },
        {
            "id": "refuses-to-play",
            "title": "A Simulated User that refuses ends the Trial simulated_user_error",
            "turns": [
                "Hi, I need to cancel an order.",
                simulate_turn(
                    known_facts={"order_id": "NB-1042"},
                    stop_when={"tool_called": "cancel_order"},
                ),
            ],
            "max_turns": 3,
            "evals": [{"expect_tools": ["cancel_order"]}],
        },
    ],
}

ASKS_WHICH = reply("msg_61AsksWhich", [text("Of course. Which order is it?")], tokens=(733, 9))
CANCELLED = reply(
    "msg_64Cancelled", [text("Done: order NB-1042 is now cancelled.")], tokens=(1018, 12)
)
REFUSAL = message(
    "msg_71SimulatedUserRefusal",
    [],
    stop_reason="refusal",
    stop_details={
        "type": "refusal",
        "category": "reasoning_extraction",
        "explanation": "The request was declined by a safety classifier.",
    },
)

SIMULATED_DRIVES: dict[str, list[Answer]] = {
    "leaks-the-order-status": [
        ASKS_WHICH,
        # The leak: the status the Simulated User was told it does not know.
        says("msg_62LeakUser", "NB-1042. It's still processing, so you can cancel it right away."),
        # The Target, told the status by the customer, cancels on its word: no lookup, which
        # its rule 1 requires, so `expect_tools_order` fails because of the leak.
        cancel("msg_63LeakCancel", "NB-1042", (902, 55)),
        reply(
            "msg_64LeakCancelled",
            [text("Since it is still processing, order NB-1042 is now cancelled.")],
            tokens=(1018, 14),
        ),
    ],
    "stops-before-the-cancel": [
        lookup("msg_65StopsLookup", "NB-1042", (744, 58)),
        reply(
            "msg_66StopsAsks",
            [text("Order NB-1042 is still processing. Shall I cancel it for you?")],
            tokens=(889, 17),
        ),
        says("msg_67StopsUser", "[DONE]"),
    ],
    "promises-a-refund-date": [
        ASKS_WHICH,
        says("msg_68RefundUser", "NB-1042."),
        lookup("msg_69RefundLookup", "NB-1042", (781, 58)),
        cancel("msg_70RefundCancel", "NB-1042", (927, 55)),
        reply(
            "msg_72RefundAnswer",
            [
                text(
                    "Done: order NB-1042 is cancelled, and your refund of $2,149.00 will reach "
                    "you within three business days."
                )
            ],
            tokens=(1049, 26),
        ),
    ],
    "judged-stop": [
        ASKS_WHICH,
        checked(
            "msg_73CheckNotYet",
            False,
            "Nothing is cancelled yet: in llm_call-1 the Target only asked which order it is.",
        ),
        says("msg_74JudgedUser", "NB-1042."),
        lookup("msg_75JudgedLookup", "NB-1042", (781, 58)),
        cancel("msg_76JudgedCancel", "NB-1042", (927, 55)),
        CANCELLED,
        checked(
            "msg_77CheckHolds",
            True,
            "cancel_order in tool_call-1 cancelled NB-1042 and llm_call-6 told the customer so.",
        ),
    ],
    "judged-stop-survives-a-failed-check": [
        ASKS_WHICH,
        schema_failure("msg_78CheckSchemaFailure", "Not yet, I would say."),
        says("msg_74JudgedUser", "NB-1042."),
        lookup("msg_75JudgedLookup", "NB-1042", (781, 58)),
        cancel("msg_76JudgedCancel", "NB-1042", (927, 55)),
        CANCELLED,
        checked(
            "msg_77CheckHolds",
            True,
            "cancel_order in tool_call-1 cancelled NB-1042 and llm_call-6 told the customer so.",
        ),
    ],
    "cancel-then-done": [
        lookup("msg_79DoneLookup", "NB-1042", (744, 58)),
        cancel("msg_80DoneCancel", "NB-1042", (891, 55)),
        CANCELLED,
        says("msg_81DoneUser", "[DONE]"),
    ],
    "keeps-asking": [
        lookup("msg_82AskingLookup", "NB-0917", (738, 57)),
        reply(
            "msg_83AskingShipped",
            [text("Order NB-0917, the Northwind Commuter fenders, has shipped.")],
            tokens=(871, 15),
        ),
        says("msg_84AskingUser", "When will it arrive?"),
        reply(
            "msg_85AskingNoDate",
            [text("I will need to check that: the order record shows no delivery date.")],
            tokens=(902, 16),
        ),
    ],
    "says-cancelled": [
        lookup("msg_86SaysLookup", "NB-1042", (744, 58)),
        cancel("msg_87SaysCancel", "NB-1042", (891, 55)),
        CANCELLED,
    ],
    "refuses-to-play": [ASKS_WHICH, {**REFUSAL, "model": SIMULATED_USER_ID_MODEL}],
}
"""The worked Trials' model calls, in call order: the Target's replies, the Simulated User's
authored answers and the stop checks'. Worked, never captured: each is a code path — a leak,
a premature stop, a refusal, a check that fails — that a live model cannot be asked for."""

LEAK_REVIEW = reviewed(
    "msg_91ReviewLeak",
    "leaked_hidden_fact",
    "helped",
    "In turn-2 the Simulated User stated that NB-1042 is still processing, a status it was told "
    "it does not know; that handed the Target what it should have fetched, and it cancelled in "
    "turn-2 without the lookup. The missing lookup follows from the leak, not from the Target "
    "alone.",
    ["turn-2"],
)

LEAK_DIAGNOSIS = diagnosed(
    "msg_92DiagnosisLeak",
    "expect_tools_order was rewritten invalid: the Simulated User told the Target the order was "
    "still processing in turn-2, and the Target, taking the customer's word for it, cancelled in "
    "tool_call-1 without the lookup rule 1 asks for. expect_tools passed on that cancellation.",
    ["tool_call-1", "llm_call-4"],
    [1, 2],
)


SIMULATED_JUDGEMENTS: dict[str, tuple[str, list[Answer]]] = {
    "simulated_user_review-leak": ("leaks-the-order-status", [LEAK_REVIEW, LEAK_DIAGNOSIS]),
    "simulated_user_review-direction-none": (
        "leaks-the-order-status",
        [
            reviewed(
                "msg_97ReviewNoDirection",
                "leaked_hidden_fact",
                "none",
                "In turn-2 the Simulated User stated a status it was told it does not know; which "
                "way that pushed the Target cannot be told from the Trace.",
                ["turn-2"],
            ),
            diagnosed(
                "msg_98DiagnosisNoDirection",
                "expect_tools_order was rewritten invalid: the Simulated User stated the hidden "
                "status in turn-2, and the Target cancelled in tool_call-1 without a lookup.",
                ["tool_call-1"],
                [1],
            ),
        ],
    ),
    "simulated_user_review-premature-stop": (
        "stops-before-the-cancel",
        [
            reviewed(
                "msg_93ReviewStoppedEarly",
                "stopped_early",
                "hindered",
                "After turn-1 the Target offered to cancel NB-1042 and the Simulated User "
                "answered the stop token instead of saying yes, before its goal was reached.",
                ["turn-1"],
            ),
            diagnosed(
                "msg_99DiagnosisStoppedEarly",
                "expect_tools was rewritten invalid: the Target looked NB-1042 up in retrieval-1 "
                "and offered to cancel it in llm_call-2, and the conversation ended there because "
                "the Simulated User answered its stop token instead of saying yes.",
                ["retrieval-1", "llm_call-2"],
                [1, 2],
            ),
        ],
    ),
    "simulated_user_review-none": (
        "promises-a-refund-date",
        [
            reviewed(
                "msg_94ReviewNone",
                "none",
                "none",
                "The Simulated User gave the order number when asked and nothing else; the "
                "refund date in the Target's reply is its own.",
                [],
            ),
        ],
    ),
    "simulated_user_review-cites-nothing": (
        "promises-a-refund-date",
        [
            reviewed(
                "msg_95ReviewCitesNothing",
                "leaked_hidden_fact",
                "helped",
                "The Simulated User said more than it knew.",
                ["turn-9"],
            ),
        ],
    ),
    "simulated_user_review-schema-failure": (
        "promises-a-refund-date",
        [schema_failure("msg_96ReviewSchemaFailure", "The Simulated User did nothing wrong.")],
    ),
    "stop_when-judged": ("judged-stop", []),
    "stop_when-judged-failure": ("judged-stop-survives-a-failed-check", []),
    "simulate-stop-token": ("cancel-then-done", []),
    "simulate-max-turns": ("keeps-asking", []),
    "simulate-target-says-any": ("says-cancelled", []),
    "simulate-refusal": ("refuses-to-play", []),
}
"""Each worked recording: the Scenario it replays, and the Judge's answers after the Trial —
its judged Evals, the reviewer, the Diagnosis, in the order `perform_evals` asks. Every one
worked (decision 41): a code path, never captured."""


def simulated_root(scratch: Path, suite: Path) -> Path:
    """The example with its Manifest pointed at the test-only Suite alone."""
    root = scratch / SIMULATED_SUITE_NAME
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs"))
    path = the_target(root).manifest
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["suites"] = [f"suites/{SIMULATED_SUITE_NAME}.yaml"]
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    shutil.copyfile(suite, the_target(root).relative(f"suites/{SIMULATED_SUITE_NAME}.yaml"))
    return root


def record_simulated(out: Out, store: CapturedAnswers, workdir: Path) -> None:
    """The test-only Suite, its Traces, and every worked Simulated User recording."""
    suite = out.root / "suites" / f"{SIMULATED_SUITE_NAME}.yaml"
    suite.parent.mkdir(parents=True, exist_ok=True)
    suite.write_text(
        "# Written by scripts/record_fixtures.py --scripted; never edited by hand.\n"
        + yaml.safe_dump(SIMULATED_SUITE, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    print(shown(suite))
    root = simulated_root(workdir, suite)
    driven: dict[str, list[dict[str, Any]]] = {}
    for identifier, script in SIMULATED_DRIVES.items():
        plan, scenario = planned(identifier, root)
        driven[identifier] = simulated_exchanges(
            scenario, plan, workdir, script, captured=store, recording=identifier
        )
        path = workdir / f"{identifier}.drive.jsonl"
        write_quietly(path, driven[identifier])
        exit = run(
            RunOptions(target=the_target(root), scenario=[identifier], replay=path),
            clock=scripted_clock(),
            run_id=SCRIPTED_RUN,
        )
        if exit.run_dir is None:
            raise SystemExit(f"{identifier}: the replayed Run did not start: {exit.message}")
        trace = exit.run_dir / "trials" / identifier / "1" / "trace.jsonl"
        target = out.trace(identifier)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(trace, target)
        shutil.rmtree(exit.run_dir)
        print(shown(target))
    for stem, (identifier, answers) in SIMULATED_JUDGEMENTS.items():
        lines = list(driven[identifier])
        if answers:
            model = judge_side(
                identifier,
                read_trace(out.trace(identifier)),
                answers,
                root=root,
                captured=store,
                recording=stem,
            )
            lines.extend(model.lines(scoped=True))
        write(out.recording(stem), lines)


def write_quietly(path: Path, exchanges: list[dict[str, Any]]) -> None:
    """A scratch recording, written without the line `write` prints for a fixture."""
    path.write_text(
        "\n".join(json.dumps(exchange, ensure_ascii=False) for exchange in exchanges) + "\n",
        encoding="utf-8",
    )


def scripted_clock() -> Callable[[], int]:
    """One millisecond per Event, from `SCRIPTED_CLOCK_MS`: a Trace reproduced by the byte."""
    readings = itertools.count(SCRIPTED_CLOCK_MS)
    return lambda: next(readings)


def record_scripted(out: Out, store: CapturedAnswers) -> None:
    """Each scripted Scenario's Trace as `run` writes it, then the Judge's side of every
    judged one, then the example's scoped recording and the per-Eval recordings."""
    # `cancel-processing-order` replays from `toy-cancel.jsonl`, scoped to itself, so
    # this one file replays every Scenario the example holds.
    lines = [
        {"scenario": SCENARIO_ID, **json.loads(line)}
        for line in out.written("toy-cancel").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    with tempfile.TemporaryDirectory() as scratch:
        workdir = Path(scratch)
        for identifier in SCRIPTS:
            plan, scenario = planned(identifier)
            lines.extend(scripted_exchanges(scenario, plan, workdir))
        # An adaptive Scenario's Target and Simulated User exchanges, driven together
        # through the one driver loop (ticket 06, decision 61).
        for identifier, script in SIMULATED_SCRIPTS.items():
            plan, scenario = planned(identifier)
            lines.extend(
                simulated_exchanges(
                    scenario, plan, workdir, script, captured=store, recording=SUITE_RECORDING.stem
                )
            )
        # The Target's side first: the Traces are what the Judge reads, and a replayed Run
        # writes its Trace whatever the Judge's exchanges say.
        write(out.suite_recording, lines)

        for identifier in [*SCRIPTS, *SIMULATED_SCRIPTS]:
            root = workdir / identifier
            shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs"))
            exit = run(
                RunOptions(
                    target=the_target(root), scenario=[identifier], replay=out.suite_recording
                ),
                clock=scripted_clock(),
                run_id=SCRIPTED_RUN,
            )
            if exit.run_dir is None:
                raise SystemExit(f"{identifier}: the replayed Run did not start: {exit.message}")
            trace = exit.run_dir / "trials" / identifier / "1" / "trace.jsonl"
            target = out.trace(identifier)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(trace, target)
            print(shown(target))

        for identifier, answers in JUDGE_SCRIPTS.items():
            events = read_trace(out.trace(identifier))
            model = judge_side(
                identifier, events, answers, captured=store, recording=SUITE_RECORDING.stem
            )
            lines.extend(model.lines(scoped=True))
        write(out.suite_recording, lines)

        for stem, (identifier, eval_name, response) in EVAL_RECORDINGS.items():
            events = read_trace(out.trace(identifier))
            write(
                out.recording(stem),
                one_exchange(
                    identifier,
                    events,
                    eval_name,
                    response,
                    workdir,
                    captured=store,
                    recording=stem,
                ),
            )

        record_simulated(out, store, workdir)


# --- `--runs`: the Run fixtures of `compare`, `rescore` and pass^k (ticket 08) ---

RUN_SELECTION = [
    "where-is-shipped-order",
    "status-question-is-not-a-cancel",
    "greeting-calls-no-tool",
    "delivered-order-cannot-be-cancelled",
]
"""Every compared Run's Scenarios: three mechanical ones (a pass with two Metrics, the
example's fail, a pass with no tool), and one judged by `goal`, which the Judge swap moves."""

BASELINE_RUN = "20260923T100000Z-base"
MODEL_SWAP_RUN = "20260923T100100Z-swap"
PROMPT_CHANGE_RUN = "20260923T100200Z-prmt"
PARTIAL_RUN = "20260923T100300Z-part"
JUDGE_SWAP_RUN = "20260923T100400Z-jdge"
RESCORE_RUN = "20260923T100500Z-resc"
TRIALS_RUN = "20260923T100600Z-tri3"
THRESHOLD_RUN = "20260923T100700Z-thrs"
"""Fixed run ids: the time orders them as they were made, the suffix says what each is."""

SWAPPED_TARGET_MODEL = "claude-sonnet-4-6"
SWAPPED_JUDGE_MODEL = "claude-opus-5-5"

PROMPT_POINTER = {
    "system": "observed",
    "rules": {"path": "prompts/rules-v2.md", "local_only": True},
}
"""The declared prompt change: `manifest.prompts` gains a second pointer, a local-only
revision of the rules that this checkout does not hold, so nothing the Target is sent
moves and the change is the Manifest's alone (phase-6 decision 8's pointer shape)."""

REGRESSED_SHIPPED_REPLY = reply(
    "msg_02WhereAnswerRefund",
    [text("Order NB-0917 has shipped, so no refund is due; it is on its way to you.")],
    tokens=(871, 31),
)
"""What the swapped and the re-prompted Target say about a shipped order: `refund`, which
`must_not_say` forbids, so that Scenario's Score moves between the Baseline and the Run."""

REGRESSED_SCRIPTS: dict[str, list[dict[str, Any]]] = {
    "where-is-shipped-order": [SCRIPTS["where-is-shipped-order"][0], REGRESSED_SHIPPED_REPLY],
}

GOAL_FAIL = verdict(
    "msg_51GoalFailOtherJudge",
    "fail",
    "The reply in llm_call-2 says the order cannot be cancelled but never says it was "
    "delivered, so the customer does not learn why.",
    ["llm_call-2"],
)

SWAPPED_JUDGE_ANSWERS: dict[str, list[Answer]] = {
    "delivered-order-cannot-be-cancelled": [
        GOAL_FAIL,
        diagnosis_of(
            "7OtherJudgeDiagnosis",
            "goal failed because the reply in llm_call-2 refuses the cancellation without "
            "naming the delivered status retrieval-1 returned, which rule 2 asks it to explain.",
            ["retrieval-1", "llm_call-2"],
            [2],
        ),
    ],
}
"""The other Judge's answers: it fails the goal the default Judge passed. Worked, never
captured: the pair exists to exercise `compare`'s Judge statement, a code path, and no real
`claude-opus-5-5` answer is expected to tell this story."""

SLOW_TOOL_MS = 5_000
"""How much longer the second Trial's lookup takes in the Trials Run: over `tool_latency`'s
1000 ms threshold, under `response_latency`'s 30000, so exactly one Metric fails."""

TRIALS_SELECTION = ["where-is-shipped-order", "lookup-then-cancel", "greeting-calls-no-tool"]


def fixed_stamp(run_id: str) -> RunStamp:
    """The stamp of a fixture Run: its id's time, this agentdiag, and no git or packages,
    so a commit or a dependency upgrade does not regenerate the fixtures."""
    return RunStamp(
        created_at=datetime.strptime(run_id.split("-")[0], RUN_ID_FORMAT).replace(tzinfo=UTC),
        agentdiag=AgentdiagSection(version=agentdiag.__version__, packages={}),
        git=GitSection(),
    )


@overload
def as_model(body: dict[str, Any], model: str) -> dict[str, Any]: ...
@overload
def as_model(body: Answer, model: str) -> Answer: ...
def as_model(body: Answer, model: str) -> Answer:
    """A response body that says it came from `model`, as that model's response would. A
    natural answer stays natural, its authored body edited inside the mark: a captured
    answer is the Judge's own and already says which model answered."""
    if isinstance(body, Natural):
        return natural({**body.body, "model": model})
    return {**body, "model": model}


def variant(scratch: Path, name: str, edit: Callable[[dict[str, Any]], None] | None) -> Path:
    """A copy of the example with its Manifest edited: one Target configuration per Run."""
    root = scratch / name
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("runs"))
    if edit is not None:
        path = the_target(root).manifest
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        edit(manifest)
        path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return root


def swap_target_model(manifest: dict[str, Any]) -> None:
    manifest["adapter"]["environments"]["local"]["model"] = SWAPPED_TARGET_MODEL


def change_prompt(manifest: dict[str, Any]) -> None:
    manifest["prompts"] = PROMPT_POINTER


TIGHT_RESPONSE_LATENCY = {"max_ms": 1}
"""The threshold edit the second rescore judges under: where-is-shipped-order's Turn took
16 ms on the scripted clock, so `response_latency` flips from pass to fail on the same
Trace, and only the Eval declaration in `run.json` says why."""


def tighten_response_latency(root: Path) -> None:
    """Edit the copy's orders Suite: `response_latency: {max_ms: 1}` on the shipped order."""
    path = the_target(root).relative("suites/orders.yaml")
    suite = yaml.safe_load(path.read_text(encoding="utf-8"))
    shipped = next(s for s in suite["scenarios"] if s["id"] == "where-is-shipped-order")
    shipped["evals"] = [
        {"response_latency": TIGHT_RESPONSE_LATENCY}
        if isinstance(declaration, dict) and "response_latency" in declaration
        else declaration
        for declaration in shipped["evals"]
    ]
    path.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")


def fixture_rescore(root: Path, source: str, run_id: str) -> Path:
    """One replayed rescore of `source` under `root`'s current Suites, a fixture by the byte."""
    rescored = rescore(
        RescoreOptions(target=the_target(root), run=source, replay=SUITE_RECORDING),
        clock=scripted_clock(),
        run_id=run_id,
        stamp=fixed_stamp(run_id),
    )
    if rescored.run_dir is None:
        raise SystemExit(f"{run_id}: the rescore did not start: {rescored.message}")
    return rescored.run_dir


def variant_recording(
    root: Path,
    path: Path,
    *,
    scripts: Mapping[str, list[dict[str, Any]]],
    target_model: str | None,
    judge_answers: Mapping[str, list[Answer]],
    judge_model: str,
    captured: CapturedAnswers,
) -> Path:
    """One scoped recording for a Manifest variant, rendered as `--scripted` renders the
    example's: the Target's side through the real Adapter against the scripted model, then
    a replayed Run of that alone for its Traces, then the Judge's side worked over them."""
    lines: list[dict[str, Any]] = []
    for identifier in RUN_SELECTION:
        if identifier not in SCRIPTS:
            continue
        plan, scenario = planned(identifier, root)
        script = list(scripts.get(identifier, SCRIPTS[identifier]))
        if target_model is not None:
            script = [as_model(body, target_model) for body in script]
        with tempfile.TemporaryDirectory() as scratch:
            lines.extend(scripted_exchanges(scenario, plan, Path(scratch), script=script))
    write(path, lines)

    with tempfile.TemporaryDirectory() as scratch:
        probe = Path(scratch) / "probe"
        shutil.copytree(root, probe, ignore=shutil.ignore_patterns("runs"))
        exit = run(
            RunOptions(
                target=the_target(probe),
                scenario=RUN_SELECTION,
                replay=path,
                judge_model=judge_model,
            ),
            clock=scripted_clock(),
            run_id="probe",
        )
        if exit.run_dir is None:
            raise SystemExit(f"{root.name}: the probe Run did not start: {exit.message}")
        for identifier, answers in judge_answers.items():
            events = read_trace(exit.run_dir / "trials" / identifier / "1" / "trace.jsonl")
            answered = [as_model(body, judge_model) for body in answers]
            model = judge_side(
                identifier,
                events,
                answered,
                root=root,
                judge_model=judge_model,
                captured=captured,
                recording=path.stem,
            )
            lines.extend(model.lines(scoped=True))
    write(path, lines)
    return path


def keep(run_dir: Path, out: Out) -> None:
    """Copy one generated Run directory into `runs/`, replacing an earlier generation.

    The Run's `report.html` (ticket 15) is left behind: it is a rendering of the other
    files, never read back, and the committed fixtures predate it (D40), so they stay
    byte-identical and `test_run_fixtures.py` diffs only what a reader of a Run reads.
    """
    target = out.runs / run_dir.name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(run_dir, target, ignore=shutil.ignore_patterns(REPORT_FILE))
    print(shown(target))


def fixture_run(
    root: Path,
    run_id: str,
    *,
    selection: Sequence[str] = RUN_SELECTION,
    replay: Path = SUITE_RECORDING,
    judge_model: str = JUDGE_MODEL,
    trials: int = 1,
    clock: Callable[[], int] | None = None,
) -> Path:
    """One replayed Run of `root` under a fixed id, clock and stamp: a fixture by the byte."""
    exit = run(
        RunOptions(
            target=the_target(root),
            scenario=list(selection),
            replay=replay,
            judge_model=judge_model,
            trials=trials,
        ),
        clock=clock or scripted_clock(),
        run_id=run_id,
        stamp=fixed_stamp(run_id),
    )
    if exit.run_dir is None:
        raise SystemExit(f"{run_id}: the Run did not start: {exit.message}")
    return exit.run_dir


class TrialClock:
    """The Trials Run's clock: one millisecond per reading, like `scripted_clock`, except
    that from reading `slow_at` on it reads `SLOW_TOOL_MS` later, and reading
    `interrupt_at` raises `KeyboardInterrupt` once, as an operator's Ctrl-C would there."""

    def __init__(self, slow_at: int, interrupt_at: int) -> None:
        self.slow_at = slow_at
        self.interrupt_at = interrupt_at
        self.readings = 0

    def __call__(self) -> int:
        reading = self.readings
        self.readings += 1
        if reading == self.interrupt_at:
            raise KeyboardInterrupt
        return SCRIPTED_CLOCK_MS + reading + (SLOW_TOOL_MS if reading >= self.slow_at else 0)


def trials_clock() -> TrialClock:
    """Where the slow lookup and the interrupt land, counted from the scripted Traces.

    Each Trial of a mechanical Scenario reads the clock once per Event of its Trace, so a
    sweep over the three Scenarios is the sum of their Traces' lengths. The lookup of
    where-is-shipped-order's second Trial ends slow; the interrupt lands on the second
    reading of lookup-then-cancel's third Trial, as its first Turn opens.
    """
    lengths = {
        identifier: len(TRACES.joinpath(f"{identifier}.trace.jsonl").read_text().splitlines())
        for identifier in TRIALS_SELECTION
    }
    sweep_length = sum(lengths.values())
    shipped = read_trace(TRACES / "where-is-shipped-order.trace.jsonl")
    lookup_end = next(
        index
        for index, event in enumerate(shipped)
        if event.type == "span/end" and event.span_id == "retrieval-1"
    )
    return TrialClock(
        slow_at=sweep_length + lookup_end,
        interrupt_at=2 * sweep_length + lengths["where-is-shipped-order"] + 1,
    )


def record_runs(out: Out, store: CapturedAnswers) -> None:
    """Every Run fixture, in the order of their ids."""
    with tempfile.TemporaryDirectory() as scratch_name:
        scratch = Path(scratch_name)

        base_root = variant(scratch, "base", None)
        keep(fixture_run(base_root, BASELINE_RUN), out)

        swap_root = variant(scratch, "swap", swap_target_model)
        swap_recording = variant_recording(
            swap_root,
            out.recording("runs-model-swap"),
            scripts=REGRESSED_SCRIPTS,
            target_model=SWAPPED_TARGET_MODEL,
            judge_answers={k: v for k, v in JUDGE_SCRIPTS.items() if k in RUN_SELECTION},
            judge_model=JUDGE_MODEL,
            captured=store,
        )
        keep(fixture_run(swap_root, MODEL_SWAP_RUN, replay=swap_recording), out)

        prompt_root = variant(scratch, "prompt", change_prompt)
        prompt_recording = variant_recording(
            prompt_root,
            out.recording("runs-prompt-change"),
            scripts=REGRESSED_SCRIPTS,
            target_model=None,
            judge_answers={k: v for k, v in JUDGE_SCRIPTS.items() if k in RUN_SELECTION},
            judge_model=JUDGE_MODEL,
            captured=store,
        )
        keep(fixture_run(prompt_root, PROMPT_CHANGE_RUN, replay=prompt_recording), out)

        partial_root = variant(scratch, "partial", None)
        partial = [i for i in RUN_SELECTION if i != "greeting-calls-no-tool"]
        keep(fixture_run(partial_root, PARTIAL_RUN, selection=partial), out)

        judge_root = variant(scratch, "judge", None)
        judge_recording = variant_recording(
            judge_root,
            out.recording("runs-judge-swap"),
            scripts={},
            target_model=None,
            judge_answers=SWAPPED_JUDGE_ANSWERS,
            judge_model=SWAPPED_JUDGE_MODEL,
            captured=store,
        )
        keep(
            fixture_run(
                judge_root, JUDGE_SWAP_RUN, replay=judge_recording, judge_model=SWAPPED_JUDGE_MODEL
            ),
            out,
        )

        keep(fixture_rescore(base_root, BASELINE_RUN, RESCORE_RUN), out)

        trials_root = variant(scratch, "trials", None)
        keep(
            fixture_run(
                trials_root,
                TRIALS_RUN,
                selection=TRIALS_SELECTION,
                trials=3,
                clock=trials_clock(),
            ),
            out,
        )

        # The same Baseline rescored again after a threshold edit in the Suite: compared
        # with the first rescore, the one difference is the Eval declaration.
        threshold_root = variant(scratch, "threshold", None)
        tighten_response_latency(threshold_root)
        shutil.copytree(
            the_target(base_root).runs / BASELINE_RUN,
            the_target(threshold_root).runs / BASELINE_RUN,
        )
        keep(fixture_rescore(threshold_root, BASELINE_RUN, THRESHOLD_RUN), out)


# --- `--helpdesk`: the second toy's recording (ticket 13, phase-6 decisions 38, 46) ---

HELPDESK_WORKSPACE = REPO / "examples" / "workspace"
HELPDESK_SLUG = "help-desk"
HELPDESK_SUITE = "suites/generated.yaml"
"""The Suite `agentdiag generate` writes for the help desk in the walkthrough (decision 39);
absent until stage C of ticket 13, and `--helpdesk` refuses without it."""

HELPDESK_RECORDING = "helpdesk"
"""`recordings/helpdesk.jsonl`: every help desk Scenario's Target and Judge exchanges, each
line scoped to its Scenario, which `run --replay` takes."""

HELPDESK_TARGET_CALLS = RECORDINGS / "captured-helpdesk-target.jsonl"
"""The help desk's own model calls, captured once through the login under `--capture
--helpdesk` and read on every later generation: `captured-judge-answers.jsonl`'s shape, one
line per request, but keyed by Scenario and request, because two Scenarios opening on the
same message would otherwise share a first answer and a re-capture of one would break the
other's chain."""

HELPDESK_RUN = "20260928T090000Z-hdsk"
"""The run id every help desk Trace fixture is written under."""


class HelpDeskMissing(RuntimeError):
    """A help desk model call or Judge answer that no store holds, outside `--capture`."""


class TargetCallMissing(HelpDeskMissing):
    """The Target sent a request the Target store does not hold for its Scenario."""


class CapturedTargetCalls:
    """The help desk's captured model calls, by `(scenario, canonical(request))`.

    A Trial is replayed from the store whole or captured whole: the Claude Code session a
    live Target call rides on holds the conversation, so a Trial cannot be half replayed and
    half live. `capture` writes one Trial's exchanges, replacing any line an earlier capture
    of that Scenario left under the same request, and rewrites `into`.
    """

    def __init__(
        self, path: Path, *, live: CredentialSource | None, into: Path | None = None
    ) -> None:
        self.path = path
        self.into = into or path
        self.live = live
        self.lines: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls = 0
        for source in dict.fromkeys([path, self.into]):
            if source.exists():
                for text in source.read_text(encoding="utf-8").splitlines():
                    if text.strip():
                        line = json.loads(text)
                        self.lines[(line["scenario"], canonical(line["request"]))] = line

    def cursor(self, scenario: str) -> TargetCallCursor:
        return TargetCallCursor(self, scenario)

    def capture(
        self, scenario: str, exchanges: Sequence[tuple[dict[str, Any], dict[str, Any]]]
    ) -> None:
        """One live Trial's exchanges, in call order, into the store and onto `into`."""
        if self.live is None:
            raise ValueError("a captured Target call records the live Backend that made it")
        cli = self.live.cli
        for request, response in exchanges:
            self.lines[(scenario, canonical(request))] = {
                "scenario": scenario,
                "request": request,
                "response": response,
                "captured": {
                    "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "backend": {
                        "kind": self.live.kind,
                        "cli_version": cli.version if cli is not None else None,
                    },
                },
            }
            self.calls += 1
        self.into.parent.mkdir(parents=True, exist_ok=True)
        self.into.write_text(
            "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in self.lines.values()),
            encoding="utf-8",
        )


class TargetCallCursor:
    """A replay `Cursor` over one Scenario's captured Target calls; a miss is marked and
    raised, so the caller can capture the Trial live (under `--capture`) or refuse."""

    def __init__(self, store: CapturedTargetCalls, scenario: str) -> None:
        self.store = store
        self.scenario_id = scenario
        self.taken: list[Exchange] = []
        self.missed = False

    def begin(self, scenario: str) -> None:
        """One cursor is one Trial."""

    def take(self, request: Any) -> Exchange:
        line = self.store.lines.get((self.scenario_id, canonical(request)))
        if line is None:
            self.missed = True
            raise TargetCallMissing(f"no captured help desk call for {self.scenario_id}")
        exchange = Exchange(request=request, response=line["response"], scenario=self.scenario_id)
        self.taken.append(exchange)
        return exchange

    def assert_consumed(self, which: Callable[[dict[str, Any]], bool] | None = None) -> None:
        """Answered by lookup, never by position: nothing is left over to name."""

    def lines(self) -> list[dict[str, Any]]:
        return [
            {"scenario": self.scenario_id, "request": e.request, "response": e.response}
            for e in self.taken
        ]


def helpdesk_root(scratch: Path) -> Path:
    """A copy of the example Workspace holding the help desk alone, so every helper that
    resolves the one Target of a root (`the_target`, `planned`) resolves it."""
    suite = HELPDESK_WORKSPACE / ".agentdiag" / "targets" / HELPDESK_SLUG / HELPDESK_SUITE
    if not suite.is_file():
        raise SystemExit(
            f"--helpdesk needs {shown(suite)}, the Suite `agentdiag generate` writes for the "
            "help desk in ticket 13's walkthrough (stage C); it is not there yet, so nothing "
            "was written"
        )
    root = scratch / "helpdesk"
    shutil.copytree(HELPDESK_WORKSPACE, root, ignore=shutil.ignore_patterns("runs", ".claude"))
    for target in Workspace.find(root).targets():
        if target.slug != HELPDESK_SLUG:
            shutil.rmtree(target.directory)
    return root


def deliver_literal_turns(scenario: Scenario, adapter: InProcessAdapter, path: Path) -> TraceWriter:
    """One Trial of a literal-Turn Scenario through `adapter`, into a Trace at `path`."""
    writer = TraceWriter(path)
    writer.start(trace_id=f"helpdesk/{scenario.id}/1", scenario=scenario.id, run="hd", trial=1)
    session = adapter.open(writer)
    try:
        for number, message in enumerate(scenario.literal_turns, start=1):
            with writer.span(
                "turn", actor="agentdiag", name=f"turn {number}", fidelity="instrumented"
            ):
                session.deliver(message)
    finally:
        session.close()
        writer.end("completed")
        writer.close()
    return writer


def helpdesk_adapter(plan: Plan, **options: Any) -> InProcessAdapter:
    manifest = plan.manifest
    return InProcessAdapter(
        manifest.adapter.as_adapter_config(),
        environment=manifest.adapter.default_environment,
        tool_kinds=manifest.tool_kinds,
        **options,
    )


def exchanges_of(trace: Path) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Every model call a Trace records, as (request body, response body), in call order."""
    requests: dict[str, dict[str, Any]] = {}
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for event in resolve_blobs(read_trace(trace)):
        body = (event.model_extra or {}).get("body")
        if event.actor != "target" or not isinstance(body, dict) or event.span_id is None:
            continue
        if event.type == "request":
            requests[event.span_id] = body
        elif event.type == "response" and event.span_id in requests:
            pairs.append((requests.pop(event.span_id), body))
    return pairs


def drive_helpdesk_trial(
    scenario: Scenario,
    plan: Plan,
    adapter: InProcessAdapter,
    path: Path,
    simulated: UnauthoredJudgeModel,
    captured: CapturedAnswers,
) -> TraceWriter:
    """One Trial through `adapter`: its literal Turns, or, for an adaptive Scenario, the real
    driver loop with the Simulated User's messages answered through the captured store (they
    are agentdiag's own calls, natural like the Judge's; decision 61)."""
    if scenario.simulate is None:
        return deliver_literal_turns(scenario, adapter, path)
    client = ScriptedJudgeClient(simulated)
    configuration = simulated_user_configuration(
        RecordedPrompt.of(simulated_user.PARTS), backend=captured.judge_backend
    )
    judge = Judge(client, JUDGE_MODEL, None, backend=captured.judge_backend)
    notes = plan.judge.notes if plan.judge else None

    def build(spec: SimulateSpec) -> simulated_user.SimulatedUser:
        return simulated_user.ModelSimulatedUser(client, configuration, spec, scenario)

    writer = TraceWriter(path)
    writer.start(trace_id=f"helpdesk/{scenario.id}/1", scenario=scenario.id, run="hd", trial=1)
    session = adapter.open(writer)
    try:
        driven = drive(
            scenario,
            session,
            writer,
            simulated_user_for=build,
            stop_check=StopCheck(
                stop_when.checker(scenario, judge, notes=notes, tool_kinds=plan.tool_kinds)
            ),
            turn_timeout_s=600.0,
        )
    finally:
        session.close()
    writer.end(driven.termination, detail=driven.detail)
    writer.close()
    return writer


def helpdesk_target_side(
    scenario: Scenario,
    plan: Plan,
    workdir: Path,
    calls: CapturedTargetCalls,
    captured: CapturedAnswers,
) -> list[dict[str, Any]]:
    """One Scenario's Target exchanges (and, for an adaptive one, the Simulated User's),
    scoped: replayed from the stores, or, on a Target miss under `--capture`, the Trial run
    live through the login, its Target calls captured whole, then replayed."""
    for attempt in ("replay", "after capture"):
        cursor = calls.cursor(scenario.id)
        simulated = UnauthoredJudgeModel(scenario.id, captured)
        try:
            drive_helpdesk_trial(
                scenario,
                plan,
                helpdesk_adapter(plan, replay=cursor),
                workdir / f"{scenario.id}.{attempt}.jsonl",
                simulated,
                captured,
            )
        except Exception:
            if not cursor.missed:
                raise
        if not cursor.missed:
            return [*cursor.lines(), *simulated.lines(scoped=True)]
        if calls.live is None:
            raise HelpDeskMissing(f"no captured help desk calls for {scenario.id}")
        if attempt == "after capture":
            raise HelpDeskMissing(f"{scenario.id}: a captured Trial did not replay")
        started = time.monotonic()
        live = drive_helpdesk_trial(
            scenario,
            plan,
            helpdesk_adapter(plan, credentials=calls.live),
            workdir / f"{scenario.id}.live.jsonl",
            UnauthoredJudgeModel(scenario.id, captured),
            captured,
        )
        exchanges = exchanges_of(live.path)
        calls.capture(scenario.id, exchanges)
        print(
            f"captured {len(exchanges)} help desk calls for {scenario.id}, "
            f"{time.monotonic() - started:.1f} s",
            file=sys.stderr,
        )
    raise AssertionError("unreachable")


class UnauthoredJudgeModel(ScriptedModel):
    """The Judge's side of one help desk Trial: every call answered through the captured
    store with no authored story (`CapturedAnswers.answer_unauthored`), however many judged
    Evals the generated Scenario declares."""

    def __init__(self, scenario: str, captured: CapturedAnswers) -> None:
        super().__init__(scenario, [], captured=captured, recording=HELPDESK_RECORDING)
        self.store = captured

    def take(self, request: Any, *, sent: ModelRequest | None = None) -> Exchange:
        if sent is None:
            raise ReplayMismatch("a help desk Judge answer is taken through the Judge's client")
        response = self.store.answer_unauthored(sent, name=self.name)
        exchange = Exchange(request=request, response=response, scenario=self.scenario_id)
        self.taken.append(exchange)
        return exchange


def helpdesk_judge_side(
    scenario_id: str, events: list[Event], root: Path, captured: CapturedAnswers
) -> list[dict[str, Any]]:
    """Every Judge call a Trial of the help desk's Scenario makes, as a Run makes them."""
    plan, scenario = planned(scenario_id, root)
    model = UnauthoredJudgeModel(scenario_id, captured)
    judges = Judges(
        ScriptedJudgeClient(model),
        JUDGE_MODEL,
        None,
        notes=plan.judge.notes if plan.judge else None,
        backend=captured.judge_backend,
        reviewer=plan.judge.reviewer if plan.judge else None,
        manifest_prompts=plan.manifest_prompts,
        suppressions=plan.judge.suppressions if plan.judge else (),
    )
    with tempfile.TemporaryDirectory() as scratch:
        perform_evals(
            scenario,
            trace_events=events,
            fidelity="instrumented",
            judges=judges,
            judgement_path=Path(scratch) / "judgement.jsonl",
            forbidden_phrases=plan.forbidden_phrases,
            tool_kinds=plan.tool_kinds,
        )
    return model.lines(scoped=True)


def record_helpdesk(out: Out, store: CapturedAnswers, calls: CapturedTargetCalls) -> None:
    """The help desk's scoped recording and Trace fixtures, from its generated Suite: the
    Target's side of every Scenario, a replayed Run of each for its Trace, then the Judge's
    side over those Traces. Deterministic once both stores hold every call."""
    with tempfile.TemporaryDirectory() as scratch_name:
        scratch = Path(scratch_name)
        root = helpdesk_root(scratch)
        plan = preflight(the_target(root), Selection(), None, dry_run=True)
        scenarios = [planned_scenario.scenario for planned_scenario in plan.selected]
        refused = [s.id for s in scenarios if s.continues is not None]
        if refused:
            raise SystemExit(
                f"--helpdesk records Scenarios that open their own session; {', '.join(refused)} "
                "continue another, which this mode does not drive"
            )
        path = out.recording(HELPDESK_RECORDING)
        lines: list[dict[str, Any]] = []
        for scenario in scenarios:
            lines.extend(helpdesk_target_side(scenario, plan, scratch, calls, store))
        write(path, lines)

        for scenario in scenarios:
            exit = run(
                RunOptions(target=the_target(root), scenario=[scenario.id], replay=path),
                clock=scripted_clock(),
                run_id=HELPDESK_RUN,
            )
            if exit.run_dir is None:
                raise SystemExit(f"{scenario.id}: the replayed Run did not start: {exit.message}")
            trace = exit.run_dir / "trials" / scenario.id / "1" / "trace.jsonl"
            kept = out.traces / HELPDESK_RECORDING / f"{scenario.id}.trace.jsonl"
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(trace, kept)
            shutil.rmtree(exit.run_dir)
            print(shown(kept))

        for scenario in scenarios:
            events = read_trace(out.traces / HELPDESK_RECORDING / f"{scenario.id}.trace.jsonl")
            lines.extend(helpdesk_judge_side(scenario.id, events, root, store))
        lines.extend(helpdesk_imported_judge_side(root, store))
        write(path, lines)


HELPDESK_PROXY_ROWS = FIXTURES / "evidence" / "helpdesk-proxy-rows.json"
"""The committed proxy rows `import --rows` reads (decision 40): the input, whatever `--out`."""

HELPDESK_IMPORT_RUN = "20260928T090100Z-impt"
HELPDESK_RESCORE_RUN = "20260928T090200Z-resc"
HELPDESK_IMPORTED_EVALS = ["prompt_adherence"]
"""What `rescore <imported> --eval prompt_adherence --replay recordings/helpdesk.jsonl`
applies (decision 39): its Judge lines are this recording's too, so one capture serves the
Suite and the imported Trace."""


def helpdesk_imported_judge_side(root: Path, captured: CapturedAnswers) -> list[dict[str, Any]]:
    """The Judge's side of the imported proxy Trace, as `rescore --eval prompt_adherence`
    asks it: the rows imported under the help desk, then the real `rescore` with its Judge's
    client the one answered through the captured store (`rescore(client=…)`)."""
    from agentdiag.importer.command import ImportOptions, import_evidence

    imported = import_evidence(
        ImportOptions(target=the_target(root), rows=HELPDESK_PROXY_ROWS),
        run_id=HELPDESK_IMPORT_RUN,
        stamp=fixed_stamp(HELPDESK_IMPORT_RUN),
    )
    if imported.run_dir is None:
        raise SystemExit(f"the help desk's proxy rows did not import: {imported.message}")
    record = json.loads((imported.run_dir / "run.json").read_text(encoding="utf-8"))
    (scenario,) = [summary["id"] for summary in record["scenarios"]]
    model = UnauthoredJudgeModel(scenario, captured)
    exit = rescore(
        RescoreOptions(
            target=the_target(root), run=HELPDESK_IMPORT_RUN, evals=HELPDESK_IMPORTED_EVALS
        ),
        clock=scripted_clock(),
        run_id=HELPDESK_RESCORE_RUN,
        stamp=fixed_stamp(HELPDESK_RESCORE_RUN),
        client=ScriptedJudgeClient(model),
        client_backend=captured.judge_backend,
    )
    if exit.run_dir is None:
        raise SystemExit(f"the imported Trace's rescore did not start: {exit.message}")
    return model.lines(scoped=True)


def live_judge(mode: str) -> LiveJudge:
    """The live client the resolved credentials choose, and its Backend, as a Run's
    preflight chooses them (decision 21); `mode` names the flag that needs them."""
    source = resolve()
    backend = backend_for(source, replay=False)
    if backend is None:
        raise SystemExit(
            f"{mode} needs credentials: set ANTHROPIC_API_KEY, log in to Claude Code "
            "(`claude auth login`), or run `ant auth login`"
        )
    return LiveJudge(live_client(backend, source), backend)


def record_live(out: Out) -> None:
    """One real exchange, for a maintainer with credentials. Never run in CI."""
    live, backend = live_judge("--live")
    events = read_trace(TRACE_FIXTURE)
    path = out.recording("judge-live")
    client = RecordingModelClient(live, path)
    judge = Judge(client, JUDGE_MODEL, None, backend=backend)
    print(f"judging through the {backend.kind} backend", file=sys.stderr)
    judgement = _throwaway_judgement(out)
    scores = judge_function("prompt_adherence")(
        context_for(SCENARIO_ID, events, "prompt_adherence"), judge, judgement
    )
    for score in scores:
        print(score.model_dump_json(indent=2))
    print(f"recorded to {path}")


def _throwaway_judgement(out: Out) -> TraceWriter:
    """A `judgement.jsonl` for the live call, beside the recording rather than in a Run."""
    path = out.recording("live-judgement")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    writer = TraceWriter(path)
    writer.start(trace_id="live/record-fixtures/1", scenario=SCENARIO_ID, run="record", trial=1)
    return writer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # One mode per invocation: `--capture` already runs the other three generations in
    # order, and any pair would have run one silently and ignored the other.
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--live",
        action="store_true",
        help=(
            "Call the real model through RecordingModelClient, by the API or the Claude Code "
            "login (needs credentials)."
        ),
    )
    modes.add_argument(
        "--capture",
        action="store_true",
        help=(
            "Run the default, --scripted and --runs generations with every natural Judge "
            "answer captured through the live client once (needs credentials)."
        ),
    )
    modes.add_argument(
        "--scripted",
        action="store_true",
        help="Run the toy Target against the scripted model and write the example's fixtures.",
    )
    modes.add_argument(
        "--runs",
        action="store_true",
        help="Write the Run fixtures compare, rescore and pass^k are tested over.",
    )
    parser.add_argument(
        "--helpdesk",
        action="store_true",
        help=(
            "Write the help desk's recording and Traces from its generated Suite (ticket 13), "
            "from the captured stores; with --capture, the calls they lack are made live."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=FIXTURES,
        help=(
            "The directory every mode writes `recordings/` and `traces/` under "
            "(default: tests/fixtures)."
        ),
    )
    arguments = parser.parse_args(argv)
    out = Out(arguments.out.resolve())
    if arguments.helpdesk:
        if arguments.live or arguments.scripted or arguments.runs:
            parser.error("--helpdesk runs alone, or with --capture")
        return helpdesk_mode(out, capture=arguments.capture)
    if arguments.live:
        record_live(out)
        return 0
    if arguments.capture:
        live = live_judge("--capture")
        print(f"capturing through the {live.backend.kind} backend", file=sys.stderr)
        store = CapturedAnswers(CAPTURED, live=live, into=out.recording(CAPTURED.stem))
        record_cancel(out, store)
        record_scripted(out, store)
        record_runs(out, store)
        print(f"{store.calls} Judge answers captured into {shown(store.into)}", file=sys.stderr)
        return exit_status(store)
    store = committed_answers()
    if arguments.scripted:
        record_scripted(out, store)
    elif arguments.runs:
        record_runs(out, store)
    else:
        record_cancel(out, store)
    return exit_status(store)


def helpdesk_mode(out: Out, *, capture: bool) -> int:
    """`--helpdesk`, and `--capture --helpdesk`: the help desk alone, with the Judge's and
    the Target's calls taken live only under `--capture` (decision 46's budget)."""
    live = live_judge("--capture --helpdesk") if capture else None
    source = resolve() if capture else None
    store = CapturedAnswers(CAPTURED, live=live, into=out.recording(CAPTURED.stem))
    calls = CapturedTargetCalls(
        HELPDESK_TARGET_CALLS, live=source, into=out.recording(HELPDESK_TARGET_CALLS.stem)
    )
    try:
        record_helpdesk(out, store, calls)
    except HelpDeskMissing as missing:
        print(
            f"{missing}: run `{shown(Path(__file__))} --capture --helpdesk` (live, through "
            "the login)",
            file=sys.stderr,
        )
        return 1
    if capture:
        print(
            f"{calls.calls} help desk calls and {store.calls} Judge answers captured",
            file=sys.stderr,
        )
    return exit_status(store)


def exit_status(store: CapturedAnswers) -> int:
    """1 when any natural answer is not a captured one, after every file is written: it was
    rendered from its authored body (no live Judge), or asked of the live Judge and the call
    failed. The fixtures then tell an authored story, or none, where a captured one was
    expected, and no silent fallback is the point (decision 41). The one exit rule every
    mode that renders Judge answers shares."""
    if store.misses:
        print(
            f"{len(store.misses)} natural Judge answers were rendered from their authored "
            f"bodies, not captured ones: run `{shown(Path(__file__))} --capture`",
            file=sys.stderr,
        )
    if store.failures:
        calls = "call" if len(store.failures) == 1 else "calls"
        print(
            f"{len(store.failures)} live Judge {calls} failed, so those natural answers are not "
            "captured and their recordings carry no line for them: run --capture again",
            file=sys.stderr,
        )
    return 1 if store.misses or store.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
