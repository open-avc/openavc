"""The generated AI tool declarations match their table, the hello sends them,
and they stay inside what the cloud's reader of them takes.

``python -m openavc.cloud.contract_gen`` renders ``cloud/tool_declarations.py``
into ``openavc/cloud/ai_tool_declarations.json``, which the cloud copies. A
release carries its declarations for as long as it runs, so the limits the
cloud's reader holds a declaration to are held here before one ships: a
declaration the reader refused would leave every system on that release
without the assistant.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

from openavc.cloud import contract_gen, protocol
from openavc.cloud.tool_declarations import KEYWORDS, TOOLS, declarations

REPO_ROOT = Path(__file__).resolve().parents[1]

# What the cloud's reader refuses a declaration for.
MAX_TOOLS = 200
MAX_BYTES = 64 * 1024
TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_NESTING = 5  # objects and arrays inside one another, the input object counted
MAX_ENUM = 100
MAX_STRING = 200
WAIT_RANGE = range(1, 601)


def test_committed_declarations_match_the_table():
    path = contract_gen.ai_tools_path(REPO_ROOT)
    assert path.is_file(), "openavc/cloud/ai_tool_declarations.json is missing: run the generator"
    committed = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == contract_gen.render_ai_tools(), (
        "openavc/cloud/ai_tool_declarations.json does not match "
        "openavc/cloud/tool_declarations.py: regenerate with "
        "'python -m openavc.cloud.contract_gen' (never edit it by hand)"
    )


def test_the_hello_sends_the_committed_declarations():
    hello = protocol.build_hello(
        system_id="s", version="0.41.0", capabilities=[], os_info="Linux", hardware="x86_64",
        install={"deployment_mode": "linux_package", "updates_itself": True,
                 "artifact_kind": "package"},
    )
    committed = json.loads(contract_gen.ai_tools_path(REPO_ROOT).read_text(encoding="utf-8"))
    assert hello["payload"]["ai_tools"] == committed


def test_the_tools_come_in_table_order_with_their_inputs():
    tools = declarations()["tools"]
    assert [tool["name"] for tool in tools] == list(TOOLS)
    assert all(tool["input"]["type"] == "object" for tool in tools)


# --- What the cloud's reader takes ---

def _schemas(schema: dict[str, Any], above: int = 0) -> Iterator[tuple[dict[str, Any], int]]:
    """Every schema inside an input, with how many objects and arrays it sits
    in, itself counted when it is one."""
    depth = above + (1 if _types(schema) & {"object", "array"} else 0)
    yield schema, depth
    for child in (schema.get("properties") or {}).values():
        yield from _schemas(child, depth)
    for key in ("items", "additionalProperties"):
        if isinstance(schema.get(key), dict):
            yield from _schemas(schema[key], depth)


def _types(schema: dict[str, Any]) -> set[str]:
    kind = schema.get("type")
    return set(kind) if isinstance(kind, list) else ({kind} if kind else set())


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def test_the_shape_and_size_the_reader_takes():
    sent = declarations()
    assert sent["format"] == 1
    assert len(sent["tools"]) <= MAX_TOOLS
    assert len(json.dumps(sent, separators=(",", ":"))) <= MAX_BYTES
    names = [tool["name"] for tool in sent["tools"]]
    assert len(names) == len(set(names))
    assert all(TOOL_NAME.match(name) for name in names)


def test_each_input_uses_only_the_closed_subset():
    for tool in declarations()["tools"]:
        assert set(tool) <= {"name", "changes_project", "answer_within_seconds", "input"}
        for schema, depth in _schemas(tool["input"]):
            assert set(schema) <= KEYWORDS, (tool["name"], set(schema) - KEYWORDS)
            assert depth <= MAX_NESTING, tool["name"]
            assert len(schema.get("enum", ())) <= MAX_ENUM, tool["name"]


def test_no_description_and_no_long_string_anywhere():
    for tool in declarations()["tools"]:
        assert all("description" not in schema for schema, _ in _schemas(tool["input"])), tool["name"]
        assert all(len(text) <= MAX_STRING for text in _strings(tool)), tool["name"]


def test_a_wait_is_a_whole_number_of_seconds_in_range():
    for tool in declarations()["tools"]:
        if "answer_within_seconds" in tool:
            wait = tool["answer_within_seconds"]
            assert isinstance(wait, int) and not isinstance(wait, bool), tool["name"]
            assert wait in WAIT_RANGE, tool["name"]
