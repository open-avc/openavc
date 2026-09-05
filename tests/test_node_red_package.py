"""The Node-RED package is pinned to the WebSocket protocol it speaks.

`integrations/node-red/` lives in this repo so that a change to the socket's
message types lands beside the node that sends them. This is the cross-language
pin: every message type the package sends is one `api/ws.py` dispatches, every
reply type it switches on is one the server produces, and every node the
package declares is a file that exists. It reads the JavaScript as text, so it
needs no Node toolchain and runs on every CI leg.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "integrations" / "node-red"
WS_HANDLER = ROOT / "openavc" / "api" / "ws.py"


@pytest.fixture(scope="module")
def package_json() -> dict:
    return json.loads((PACKAGE / "package.json").read_text(encoding="utf-8"))


def _js_sources() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8")
        for folder in ("lib", "nodes")
        for p in sorted((PACKAGE / folder).glob("*.js"))
    )


def test_every_declared_node_has_its_runtime_and_editor_file(package_json):
    nodes = package_json["node-red"]["nodes"]
    assert nodes, "package.json declares no nodes"
    for name, rel in nodes.items():
        js = PACKAGE / rel
        assert js.is_file(), f"{name}: {rel} is missing"
        assert js.with_suffix(".html").is_file(), f"{name}: no editor file beside {rel}"
        assert f'registerType("{name}"' in js.read_text(encoding="utf-8"), (
            f"{name}: {rel} does not register that type"
        )


def test_every_message_type_the_package_sends_is_one_the_server_dispatches():
    # Every frame the package sends is built in lib/connection.js; the nodes
    # never touch the socket themselves.
    connection = (PACKAGE / "lib" / "connection.js").read_text(encoding="utf-8")
    sent = set(re.findall(r'type:\s*"([a-z_.]+)"', connection))
    handled = set(re.findall(r'msg_type == "([a-z_.]+)"', WS_HANDLER.read_text(encoding="utf-8")))
    assert sent, "found no outbound message types in the package"
    unknown = sent - handled
    assert not unknown, f"the package sends message types the server does not dispatch: {sorted(unknown)}"


def test_every_reply_type_the_package_reads_is_one_the_server_produces():
    connection = (PACKAGE / "lib" / "connection.js").read_text(encoding="utf-8")
    read = set(re.findall(r'case "([a-z_.]+)":', connection))
    server = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (ROOT / "openavc").rglob("*.py")
    )
    produced = set(re.findall(r'"type":\s*"([a-z_.]+)"', server))
    # Macro lifecycle frames are relayed from bus events by name: the engine
    # subscribes to `macro.<status>.*` and broadcasts `{"type": "macro.<status>"}`.
    engine = (ROOT / "openavc" / "core" / "engine.py").read_text(encoding="utf-8")
    produced |= set(re.findall(r'events\.on\("(macro\.[a-z_]+)\.\*"', engine))
    assert read, "the connection switches on no reply types"
    unknown = read - produced
    assert not unknown, f"the package reads reply types the server never sends: {sorted(unknown)}"


# Node-RED's own core node types an example may use. Anything else an example
# names has to be one of ours, or the import fails with "unknown node type".
_CORE_NODE_TYPES = {"tab", "comment", "inject", "debug", "function", "switch", "catch"}


def test_every_example_is_a_flow_that_imports_clean(package_json):
    """Examples appear under Import > Examples in the editor. One that names
    a node type nobody has, or a server that is not in the file, imports as a
    broken flow -- and it is the first thing a new user touches."""
    ours = set(package_json["node-red"]["nodes"])
    examples = sorted((PACKAGE / "examples").glob("*.json"))
    assert examples, "no examples shipped"
    for path in examples:
        flow = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(flow, list) and flow, f"{path.name}: not a flow export"
        tabs = {n["id"] for n in flow if n.get("type") == "tab"}
        servers = {n["id"] for n in flow if n.get("type") == "openavc-server"}
        assert tabs, f"{path.name}: no tab"
        assert servers, f"{path.name}: no openavc-server config node"
        for node in flow:
            kind = node.get("type")
            assert kind in ours or kind in _CORE_NODE_TYPES, f"{path.name}: unknown node type {kind!r}"
            if kind in ("tab", "openavc-server"):
                continue
            assert node.get("z") in tabs, f"{path.name}: {node['id']} is on no tab"
            if kind in ours:
                assert node.get("server") in servers, f"{path.name}: {node['id']} points at no server in the file"
        info = next(n for n in flow if n.get("type") == "tab").get("info", "")
        assert "openavc-server" in info, f"{path.name}: the tab's description does not say to point the server node at a system"


def test_the_readme_s_screenshot_is_a_file_in_this_repo():
    """npm and the flow library render the README from the registry, where a
    relative image path is a broken image. The README points at the raw
    GitHub copy of a file that lives beside the docs, so one file serves the
    README, the docs site and GitHub."""
    readme = (PACKAGE / "README.md").read_text(encoding="utf-8")
    urls = re.findall(r"!\[[^\]]*\]\((https://raw\.githubusercontent\.com/open-avc/openavc/main/[^)]+)\)", readme)
    assert urls, "the README has no screenshot"
    for url in urls:
        rel = url.split("/open-avc/openavc/main/", 1)[1]
        assert (ROOT / rel).is_file(), f"README image {rel} is not in the repo"


def test_the_shared_editor_script_is_served_under_the_package_s_own_name(package_json):
    """Node-RED serves a package's resources/ folder at resources/<package
    name>/, so the <script src> in the server node's editor file has to spell
    the package name exactly. A rename that misses it leaves every node dialog
    without its lookups, and nothing but a blank dropdown says so."""
    html = (PACKAGE / "nodes" / "openavc-server.html").read_text(encoding="utf-8")
    expected = f'src="resources/{package_json["name"]}/editor.js"'
    assert expected in html, f"openavc-server.html does not load {expected}"
    assert (PACKAGE / "resources" / "editor.js").is_file()


def test_the_package_pins_the_node_red_it_needs(package_json):
    # The core websocket client gained headers in 4.0, and `resources/` (the
    # shared editor script) has been served since 1.3 -- 4.0 is the floor.
    assert package_json["node-red"]["version"] == ">=4.0.0"
    assert package_json["engines"]["node"] == ">=18"
    assert "resources" in package_json["files"]
