"""
Device action resolution and validation.

A driver can promote a subset of its capabilities to prominent "Quick Action"
buttons at the top of the device view — instead of leaving everything buried in
the flat "Send Command" list. Two declaration styles, both folded into one
resolved list the IDE renders:

    quick_actions: ["power_on", "discover_add_all"]   # sugar: promote commands

    actions:                                           # full form
      - { id: power_on, kind: command, label: "Power On", icon: power,
          confirm: false, visible_when: {...} }
      - { id: provision, kind: setup, availability: offline, ... }

The platform stays generic: it parses, validates, resolves, renders, and
invokes actions. It never knows what a specific action *does* — that lives in
the driver (the command it sends, or, for ``kind: setup``, the
``run_setup_action`` handler body). ``kind: "setup"`` is reserved for the
provisioning-wizard mechanism; ``kind: "command"`` is the invocable runtime
action; ``kind: "link"`` opens a URL (the device's own web interface) in a new
tab, entirely client-side — nothing is sent to the device.

**Open Web UI.** Any device with a browser-reachable web interface gets an
"Open" button with no per-driver action needed. ``web_ui`` is a three-way
switch at the top level of DRIVER_INFO:

* unset (the default) — **auto-detect**. The platform works out a reachable web
  URL for the device (HTTP-transport devices from their own config, others from
  a light port probe / discovery scan — see ``core/web_ui_probe.py``) and adds
  the button when one is found. The resolved URL arrives here as
  ``detected_web_ui_url``.
* ``true`` (default URL ``https://{host}``) or ``"http://{host}:8080"`` — force
  the button on with that URL, skipping detection.
* ``false`` — force the button off (the device has no web UI worth surfacing).

A driver that wants a custom label/icon can instead declare an explicit
``{kind: link, url: ...}`` action; the auto-add then stands down.
``{host}`` / ``{port}`` / ``{config_key}`` in a link URL are substituted from
the device's connection config when the device is served.

Resolved action shape (one dict per entry, consumed by the REST API + IDE):

    {
      "id": str,                     # unique within the device
      "kind": "command" | "setup" | "link",
      "label": str,
      "icon": str | None,            # lucide icon name
      "confirm": bool | str | None,  # str = custom confirmation message
      "visible_when": dict | None,   # shared condition shape (key/operator/value
                                     # or {any|all:[...]}); evaluated by the IDE
      "availability": "online" | "offline" | "always",
      "params": dict,                # param schema for the input dialog
      "command": str,                # kind=="command" only: command id to send
      "url": str,                    # kind=="link" only: URL to open (host-substituted)
    }
"""

from __future__ import annotations

from typing import Any

from server.core.web_ui_probe import web_ui_url_for_http_config
from server.drivers.avcdriver_semantic import validate_actions as validate_actions
from server.drivers.spec import ACTION_KINDS, AVAILABILITIES
from server.utils.logger import get_logger

log = get_logger(__name__)


# Default URL for an auto-added / url-less Open Web UI link action.
_DEFAULT_WEB_UI_URL = "https://{host}"
_WEB_UI_ACTION_ID = "open_web_ui"

_DEFAULT_AVAILABILITY = "online"



def resolve_device_actions(
    driver_info: dict[str, Any],
    config: dict[str, Any] | None = None,
    detected_web_ui_url: str | None = None,
) -> list[dict[str, Any]]:
    """Fold a driver's ``actions`` + ``quick_actions`` into one resolved list.

    Explicit ``actions`` entries are authoritative and come first (in declared
    order); ``quick_actions`` sugar then fills in any command ids not already
    covered by an explicit action. Malformed or dangling entries are skipped
    defensively (a Python driver isn't load-validated), so this never raises.

    When the driver declares ``web_ui`` and no explicit link action exists, an
    ``open_web_ui`` link action is appended. When ``config`` is provided (the
    device's connection config), ``{host}``/``{port}``/``{key}`` placeholders in
    every link action's URL are substituted so the served action carries a
    ready-to-open URL.
    """
    commands = driver_info.get("commands")
    if not isinstance(commands, dict):
        commands = {}

    resolved: list[dict[str, Any]] = []
    seen: set[str] = set()

    raw_actions = driver_info.get("actions")
    if isinstance(raw_actions, list):
        for entry in raw_actions:
            action = _resolve_action_entry(entry, commands)
            if action is None or action["id"] in seen:
                continue
            resolved.append(action)
            seen.add(action["id"])

    # Auto-add Open Web UI when the driver didn't declare its own link action —
    # so every web-capable device gets the button for free. `web_ui` is the
    # three-way switch (unset = auto-detect, truthy = force on, False = off).
    url = _resolve_web_ui_url(
        driver_info.get("web_ui"), driver_info, config, detected_web_ui_url
    )
    if url and not any(a["kind"] == "link" for a in resolved) and _WEB_UI_ACTION_ID not in seen:
        resolved.append({
            "id": _WEB_UI_ACTION_ID,
            "kind": "link",
            "label": "Open Web UI",
            "icon": "external-link",
            "confirm": None,
            "visible_when": None,
            "availability": "always",
            "params": {},
            "url": url,
        })
        seen.add(_WEB_UI_ACTION_ID)

    quick = driver_info.get("quick_actions")
    if isinstance(quick, list):
        for cmd_id in quick:
            if not isinstance(cmd_id, str) or not cmd_id or cmd_id in seen:
                continue
            cmd = commands.get(cmd_id)
            if not isinstance(cmd, dict):
                # References a command this driver doesn't declare — skip rather
                # than render a button that would 404 on click.
                continue
            params = cmd.get("params")
            resolved.append({
                "id": cmd_id,
                "kind": "command",
                "command": cmd_id,
                "label": cmd.get("label") or cmd_id,
                "icon": None,
                "confirm": None,
                "visible_when": None,
                "availability": _DEFAULT_AVAILABILITY,
                "params": params if isinstance(params, dict) else {},
            })
            seen.add(cmd_id)

    if config:
        for action in resolved:
            if action["kind"] == "link" and action.get("url"):
                action["url"] = _substitute_url(action["url"], config)

    return resolved


def _resolve_web_ui_url(
    web_ui: Any,
    driver_info: dict[str, Any],
    config: dict[str, Any] | None,
    detected_web_ui_url: str | None,
) -> str | None:
    """Resolve the Open Web UI URL from the three-way ``web_ui`` switch.

    ``False`` forces the button off. A truthy value forces it on (a string is
    the URL template, ``True`` uses ``https://{host}``). Unset means auto-detect:
    prefer a URL the runtime already detected (port probe / discovery), then
    fall back to deriving one straight from an HTTP-transport device's config
    (its control endpoint is already a web server).
    """
    if web_ui is False:
        return None
    if isinstance(web_ui, str) and web_ui:
        return web_ui
    if web_ui:  # True, or any other truthy non-string
        return _DEFAULT_WEB_UI_URL
    # Auto-detect (web_ui unset).
    if detected_web_ui_url:
        return detected_web_ui_url
    if config and driver_info.get("transport") == "http":
        return web_ui_url_for_http_config(config)
    return None


def _substitute_url(template: str, config: dict[str, Any]) -> str:
    """Substitute {host}/{port}/{config_key} in a link URL from device config.
    Unknown or missing placeholders are left intact rather than raising."""
    class _Safe(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    values = _Safe({k: v for k, v in config.items() if v is not None})
    try:
        return template.format_map(values)
    except (ValueError, IndexError):
        return template


def _resolve_action_entry(
    entry: Any, commands: dict[str, Any],
) -> dict[str, Any] | None:
    """Normalize one explicit ``actions`` entry, or None if unusable."""
    if not isinstance(entry, dict):
        return None
    action_id = entry.get("id")
    if not isinstance(action_id, str) or not action_id:
        return None

    kind = entry.get("kind", "command")
    if kind not in ACTION_KINDS:
        return None

    icon = entry.get("icon")
    confirm = entry.get("confirm")
    visible_when = entry.get("visible_when")
    availability = entry.get("availability")
    if availability not in AVAILABILITIES:
        availability = _DEFAULT_AVAILABILITY

    resolved: dict[str, Any] = {
        "id": action_id,
        "kind": kind,
        "label": entry.get("label") or action_id,
        "icon": icon if isinstance(icon, str) and icon else None,
        # confirm: True (default message), a custom string, or None (no confirm).
        "confirm": confirm if isinstance(confirm, (bool, str)) and confirm else None,
        "visible_when": visible_when if isinstance(visible_when, dict) else None,
        "availability": availability,
    }

    if kind == "command":
        command_id = entry.get("command")
        if not isinstance(command_id, str) or not command_id:
            command_id = action_id
        resolved["command"] = command_id
        cmd = commands.get(command_id)
        cmd = cmd if isinstance(cmd, dict) else {}
        # Promoting a command stays terse: inherit the command's label/params
        # when the action doesn't override them.
        if not entry.get("label") and cmd.get("label"):
            resolved["label"] = cmd["label"]
        params = entry.get("params")
        if not isinstance(params, dict):
            params = cmd.get("params")
        resolved["params"] = params if isinstance(params, dict) else {}
    elif kind == "link":
        url = entry.get("url")
        resolved["url"] = url if isinstance(url, str) and url else _DEFAULT_WEB_UI_URL
        resolved["params"] = {}
        # A link opens client-side and doesn't touch the device — default to
        # always-visible unless the driver scoped it otherwise.
        if entry.get("availability") not in AVAILABILITIES:
            resolved["availability"] = "always"
    else:  # setup
        params = entry.get("params")
        resolved["params"] = params if isinstance(params, dict) else {}

    return resolved
