"""The scan heuristics of `agentdiag discover --scan` (ticket 11, phase-6 decision 27).

Each heuristic is asked of a small synthetic repository under `tmp_path`, written for the
one thing it finds, so a heuristic that drifts fails its own test rather than the toy's
equality. The scan reads files and parses Python with `ast`; nothing here imports the code
it scans, which a module that raises on import proves.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentdiag.discover.scan import scan_repository

LONG = "You are the help desk. " * 12
"""A prompt string literal: 200+ characters."""


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def package(root: Path, name: str) -> Path:
    """A package directory with an empty `__init__.py`."""
    write(root, f"{name}/__init__.py", "")
    return root / name


def test_a_file_under_a_prompts_directory_is_a_prompt(tmp_path: Path) -> None:
    write(tmp_path, "agent/prompts/system.md", "# Rules\n\n1. Be kind.\n")
    write(tmp_path, "agent/persona/voice.txt", "Warm and brief.\n")
    write(tmp_path, "agent/instructions/refunds.prompt", "Refund within policy.\n")
    write(tmp_path, "agent/docs/notes.md", "# Notes\n")

    found = scan_repository(tmp_path)

    assert {name: found.prompts[name].value for name in found.prompts} == {
        "system": tmp_path / "agent/prompts/system.md",
        "voice": tmp_path / "agent/persona/voice.txt",
        "refunds": tmp_path / "agent/instructions/refunds.prompt",
    }
    assert "prompts" in found.prompts["system"].review


def test_a_markdown_file_a_python_file_names_is_a_prompt(tmp_path: Path) -> None:
    write(tmp_path, "agent/greeting.md", "# Greeting\n\nSay hello.\n")
    write(tmp_path, "agent/unused.md", "# Unused\n")
    write(tmp_path, "agent/plain.md", "No heading here.\n")
    write(
        tmp_path,
        "agent/run.py",
        '"""Mentions unused.md in a docstring only."""\n'
        'GREETING = open("greeting.md").read()\nPLAIN = "plain.md"\n',
    )

    found = scan_repository(tmp_path)

    assert list(found.prompts) == ["greeting"]
    assert "agent/run.py" in found.prompts["greeting"].review


def test_a_long_module_level_prompt_literal_is_observed_naming_module_and_attribute(
    tmp_path: Path,
) -> None:
    root = package(tmp_path, "desk")
    write(root, "prompt.py", f"SYSTEM_PROMPT = {LONG!r}\nGREETING_PROMPT = {LONG!r}\n")
    write(root, "short.py", 'SYSTEM_PROMPT = "too short"\nBANNER = ' + repr(LONG) + "\n")

    found = scan_repository(tmp_path)

    assert {name: found.prompts[name].value for name in found.prompts} == {
        "system": "observed",
        "greeting": "observed",
    }
    assert "desk.prompt:SYSTEM_PROMPT" in found.prompts["system"].review


def test_a_prompt_re_exported_by_its_package_is_named_by_the_package(tmp_path: Path) -> None:
    root = tmp_path / "desk"
    write(root, "__init__.py", "from desk.prompt import SYSTEM_PROMPT\n")
    write(root, "prompt.py", f"SYSTEM_PROMPT = {LONG!r}\n")

    found = scan_repository(tmp_path)

    assert "desk:SYSTEM_PROMPT" in found.prompts["system"].review


def test_tool_schemas_come_from_json_files_and_module_level_lists(tmp_path: Path) -> None:
    schema = {
        "name": "search_kb",
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
    }
    write(tmp_path, "tools/search_kb.json", json.dumps(schema))
    openai_style = {
        "type": "function",
        "function": {"name": "refund", "parameters": {"type": "object", "properties": {}}},
    }
    write(tmp_path, "tools/refund.json", json.dumps(openai_style))
    write(tmp_path, "package.json", json.dumps({"name": "not-a-tool", "version": "1"}))
    root = package(tmp_path, "desk")
    write(
        root,
        "tools.py",
        "TOOLS: list = [\n"
        '    {"name": "get_order", "input_schema": {"type": "object"}},\n'
        '    {"name": "cancel_order", "input_schema": {"type": "object"}},\n'
        "]\n",
    )

    found = scan_repository(tmp_path)

    assert {name: (tool.kind, tool.schema) for name, tool in found.tools.items()} == {
        "search_kb": ("retrieval", tmp_path / "tools/search_kb.json"),
        "refund": ("action", tmp_path / "tools/refund.json"),
        "get_order": ("retrieval", None),
        "cancel_order": ("action", None),
    }
    assert "desk.tools:TOOLS" in found.tools["get_order"].review
    assert 'starts with "get"' in found.tools["get_order"].review


def test_every_retrieval_prefix_guesses_retrieval_and_anything_else_action(
    tmp_path: Path,
) -> None:
    names = ["get_a", "lookup_b", "search_c", "find_d", "list_e", "read_f", "fetch_g", "send_h"]
    entries = ", ".join(f'{{"name": "{name}", "input_schema": {{}}}}' for name in names)
    write(tmp_path, "desk.py", f"TOOLS = [{entries}]\n")

    found = scan_repository(tmp_path)

    assert {name: tool.kind for name, tool in found.tools.items()} == {
        **dict.fromkeys(names[:-1], "retrieval"),
        "send_h": "action",
    }


def test_model_sdk_usage_and_a_model_keyword_are_found(tmp_path: Path) -> None:
    root = package(tmp_path, "desk")
    write(
        root,
        "agent.py",
        "import anthropic\n"
        "def answer(client):\n"
        '    return client.messages.create(model="claude-haiku-5", messages=[])\n',
    )
    write(root, "other.py", "from openai import OpenAI\n")

    found = scan_repository(tmp_path)

    assert found.model is not None
    assert found.model.value == "claude-haiku-5"
    assert found.sdks == {"desk.agent": ["anthropic"], "desk.other": ["openai"]}


def test_a_model_default_bound_to_a_module_constant_is_resolved(tmp_path: Path) -> None:
    root = package(tmp_path, "desk")
    write(root, "settings.py", 'MODEL = "claude-sonnet-5"\n')
    write(
        root,
        "agent.py",
        "from desk.settings import MODEL\n"
        "def make_agent(client, tools, *, model: str = MODEL):\n"
        "    def respond(message):\n"
        "        return client.messages.create(model=model)\n"
        "    return respond\n",
    )

    found = scan_repository(tmp_path)

    assert found.model is not None
    assert found.model.value == "claude-sonnet-5"
    assert "desk.agent:make_agent" in found.model.review


def test_a_make_function_returning_a_callable_is_the_factory_and_one_returning_callables_the_tools(
    tmp_path: Path,
) -> None:
    root = package(tmp_path, "desk")
    write(
        root,
        "entry.py",
        "def build_agent(client, tools):\n"
        "    def respond(message):\n"
        "        return message\n"
        "    return respond\n"
        "def create_tools():\n"
        "    def lookup(order_id):\n"
        "        return {}\n"
        '    return {"lookup": lookup}\n'
        "def make_config():\n"
        "    return 3\n",
    )

    found = scan_repository(tmp_path)

    assert found.factory is not None
    assert found.factory.value == "desk.entry:build_agent"
    assert found.tools_reference is not None
    assert found.tools_reference.value == "desk.entry:create_tools"
    assert set(found.tools) == {"lookup"}


def test_an_http_route_is_named_and_left_for_the_http_adapter(tmp_path: Path) -> None:
    write(
        tmp_path,
        "service.py",
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        '@app.post("/chat")\n'
        "def chat(body):\n"
        "    return body\n",
    )

    found = scan_repository(tmp_path)

    assert found.factory is None
    assert found.routes == ["service:chat (POST /chat)"]


def test_data_source_identities_come_from_urls_and_environment_variable_names(
    tmp_path: Path,
) -> None:
    write(
        tmp_path,
        "db.py",
        "import os\n"
        'ORDERS_DSN = "postgresql://desk:s3cret@db.internal:5432/orders"\n'
        'KB = "https://kb.example/v1"\n'
        'CACHE = os.environ["CACHE_URL"]\n'
        'REPORTS = os.getenv("REPORTS_DB")\n'
        'TOKEN = os.getenv("API_TOKEN")\n',
    )

    found = scan_repository(tmp_path)

    assert {
        name: (source.identity, source.kind) for name, source in found.data_sources.items()
    } == {
        "orders": ("postgresql://db.internal:5432/orders", "database"),
        "kb": ("https://kb.example/v1", "http"),
        "cache": ("env:CACHE_URL", None),
        "reports": ("env:REPORTS_DB", "database"),
    }
    assert "password" in found.data_sources["orders"].review
    assert "embedded token" in found.data_sources["kb"].review


def test_a_url_identity_keeps_no_query_string_and_no_user(tmp_path: Path) -> None:
    write(tmp_path, "kb.py", 'KB_URL = "https://ada@kb.example/v1/search?api_key=sk-123&x=1"\n')

    found = scan_repository(tmp_path)

    assert found.data_sources["kb"].identity == "https://kb.example/v1/search"
    assert "sk-123" not in found.data_sources["kb"].review


def test_the_deployed_set_convention_is_found(tmp_path: Path) -> None:
    root = tmp_path / "desk"
    write(root, "__init__.py", "from desk.target import deployed_set\n")
    write(root, "target.py", 'def deployed_set():\n    return {"prompts": {}}\n')

    found = scan_repository(tmp_path)

    assert found.deployed is not None
    assert found.deployed.value == "desk:deployed_set"


def test_a_json_file_that_holds_no_tool_object_is_skipped(tmp_path: Path) -> None:
    for name, text in {
        "null.json": "null",
        "number.json": "3",
        "text.json": '"a string"',
        "numbers.json": "[1, 2, 3]",
        "tools.json": '{"tools": 5}',
    }.items():
        write(tmp_path, f"data/{name}", text)

    found = scan_repository(tmp_path)

    assert found.tools == {}


def test_virtual_environments_and_build_output_are_skipped(tmp_path: Path) -> None:
    for skipped in ("venv", "env", "build", "dist", ".tox", "site-packages", ".mypy_cache"):
        write(tmp_path, f"{skipped}/agent.py", f"SYSTEM_PROMPT = {LONG!r}\n")
    write(tmp_path, "my-env/pyvenv.cfg", "home = /usr/bin\n")
    write(tmp_path, "my-env/lib/agent.py", f"SYSTEM_PROMPT = {LONG!r}\n")

    found = scan_repository(tmp_path)

    assert found.prompts == {}


def test_a_readme_or_agent_file_is_never_a_prompt_and_packaging_references_do_not_count(
    tmp_path: Path,
) -> None:
    for name in ("README.md", "CHANGELOG.md", "LICENSE.md", "CONTRIBUTING.md", "AGENTS.md"):
        write(tmp_path, name, "# Heading\n")
    write(tmp_path, "prompts/README.md", "# About these prompts\n")
    write(tmp_path, "CLAUDE.md", "# Heading\n")
    write(tmp_path, "guide.md", "# Guide\n")
    names = ["README.md", "AGENTS.md", "CLAUDE.md", "guide.md"]
    write(tmp_path, "agent.py", f"FILES = {names[:3]!r}\n")
    write(tmp_path, "setup.py", f"FILES = {names!r}\n")

    found = scan_repository(tmp_path)

    assert found.prompts == {}


def test_skipped_directories_are_never_read_and_nothing_scanned_is_imported(
    tmp_path: Path,
) -> None:
    for skipped in (".git", ".venv", "node_modules", "__pycache__", "tests", ".agentdiag"):
        write(tmp_path, f"{skipped}/prompts/system.md", "# Hidden\n")
        write(tmp_path, f"{skipped}/agent.py", f"SYSTEM_PROMPT = {LONG!r}\n")
    write(
        tmp_path,
        "boom.py",
        'raise SystemExit("imported")\ndef make_agent():\n    return lambda m: m\n',
    )
    write(tmp_path, "broken.py", "def (:\n")

    found = scan_repository(tmp_path)

    assert found.prompts == {}
    assert found.factory is not None
    assert found.factory.value == "boom:make_agent"
    assert found.unreadable == ["broken.py"]


def test_friction_4_a_mapping_of_schemas_and_a_tools_method_name_the_tools(
    tmp_path: Path,
) -> None:
    """Walkthrough friction 4: `--scan` found 0 tools in a package whose schemas are a dict of
    name to schema and whose tools are a class's `tools()` returning bound methods."""
    root = package(tmp_path, "desk")
    write(
        root,
        "records.py",
        'SCHEMAS = {"search_kb": {"name": "search_kb", "input_schema": {"type": "object"}},\n'
        '           "file_report": {"name": "file_report", "input_schema": {}}}\n'
        'SETTINGS = {"retries": {"count": 2}}\n',
    )
    write(
        root,
        "desk.py",
        "class Desk:\n"
        "    def tools(self):\n"
        '        return {"search_kb": self.search, "page_someone": self.page}\n',
    )

    found = scan_repository(tmp_path)

    assert sorted(found.tools) == ["file_report", "page_someone", "search_kb"]
    assert found.tools["search_kb"].kind == "retrieval"
    assert "desk.desk:Desk.tools()" in found.tools["page_someone"].review
    assert "retries" not in found.tools
