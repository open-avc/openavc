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

**Open Web UI.** Any device whose driver declares a browser-reachable web
interface gets an "Open" button with no per-driver action needed: set
``web_ui: true`` (default URL ``https://{host}``) or ``web_ui: "http://{host}:8080"``
at the top level of DRIVER_INFO and the platform auto-adds an ``open_web_ui``
link action. A driver that wants a custom label/icon can instead declare an
explicit ``{kind: link, url: ...}`` action; the auto-add then stands down.
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

from server.utils.logger import get_logger

log = get_logger(__name__)

# Action kinds the platform understands. "command" promotes an existing command
# (runs online through send_command); "setup" is the offline-capable
# provisioning wizard handled by the driver's run_setup_action(); "link" opens a
# URL (the device's web UI) in a new tab, purely client-side.
ACTION_KINDS: tuple[str, ...] = ("command", "setup", "link")

# Default URL for an auto-added / url-less Open Web UI link action.
_DEFAULT_WEB_UI_URL = "https://{host}"
_WEB_UI_ACTION_ID = "open_web_ui"

# How an action's visibility tracks the device's connection state.
AVAILABILITIES: tuple[str, ...] = ("online", "offline", "always")
_DEFAULT_AVAILABILITY = "online"

# Operators accepted in a visible_when condition. Mirrors the shared condition
# evaluator (server/core/condition_eval.py) and the panel / Stream Deck (§38)
# JS evaluator so an action condition behaves identically everywhere.
_VISIBLE_WHEN_OPERATORS: frozenset[str] = frozenset({
    "eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy",
    "equals", "not_equals", "==", "!=", ">", "<", ">=", "<=",
})


def resolve_device_actions(
    driver_info: dict[str, Any], config: dict[str, Any] | None = None,
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

    # Auto-add Open Web UI from the `web_ui` flag when the driver didn't declare
    # its own link action — so every web-capable device gets the button for free.
    web_ui = driver_info.get("web_ui")
    if web_ui and not any(a["kind"] == "link" for a in resolved) and _WEB_UI_ACTION_ID not in seen:
        url = web_ui if isinstance(web_ui, str) and web_ui else _DEFAULT_WEB_UI_URL
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


def validate_actions(driver_def: dict[str, Any]) -> list[str]:
    """Validate the ``actions`` + ``quick_actions`` blocks of a driver
    definition. Returns a list of error strings (empty when valid).

    Mirrored by the catalog validator in ``openavc-drivers/scripts/
    build_index.py`` (kept stdlib-only there); keep the two in sync.
    """
    errors: list[str] = []
    commands = driver_def.get("commands")
    command_ids = set(commands.keys()) if isinstance(commands, dict) else set()

    quick = driver_def.get("quick_actions")
    if quick is not None:
        if not isinstance(quick, list):
            errors.append("quick_actions: must be a list of command ids")
        else:
            for i, cid in enumerate(quick):
                if not isinstance(cid, str) or not cid:
                    errors.append(
                        f"quick_actions[{i}]: must be a non-empty command id string"
                    )
                elif command_ids and cid not in command_ids:
                    errors.append(
                        f"quick_actions[{i}]: '{cid}' is not a declared command"
                    )

    actions = driver_def.get("actions")
    if actions is not None:
        if not isinstance(actions, list):
            errors.append("actions: must be a list")
        else:
            seen: set[str] = set()
            for i, entry in enumerate(actions):
                errors.extend(
                    _validate_action_entry(i, entry, command_ids, seen)
                )

    return errors


def _validate_action_entry(
    index: int, entry: Any, command_ids: set[str], seen: set[str],
) -> list[str]:
    errors: list[str] = []
    where = f"actions[{index}]"
    if not isinstance(entry, dict):
        return [f"{where}: must be a mapping"]

    action_id = entry.get("id")
    if not isinstance(action_id, str) or not action_id:
        errors.append(f"{where}: missing required 'id' (non-empty string)")
    else:
        if action_id in seen:
            errors.append(f"{where}: duplicate action id '{action_id}'")
        seen.add(action_id)

    kind = entry.get("kind", "command")
    if kind not in ACTION_KINDS:
        errors.append(
            f"{where}: unknown kind '{kind}' (expected one of {list(ACTION_KINDS)})"
        )

    label = entry.get("label")
    if label is not None and not isinstance(label, str):
        errors.append(f"{where}: 'label' must be a string")

    icon = entry.get("icon")
    if icon is not None and not isinstance(icon, str):
        errors.append(f"{where}: 'icon' must be a string (lucide icon name)")

    availability = entry.get("availability")
    if availability is not None and availability not in AVAILABILITIES:
        errors.append(
            f"{where}: 'availability' must be one of {list(AVAILABILITIES)}"
        )

    confirm = entry.get("confirm")
    if confirm is not None and not isinstance(confirm, (bool, str)):
        errors.append(f"{where}: 'confirm' must be a boolean or a message string")

    params = entry.get("params")
    if params is not None and not isinstance(params, dict):
        errors.append(f"{where}: 'params' must be a mapping")

    url = entry.get("url")
    if kind == "link":
        if url is not None and (not isinstance(url, str) or not url):
            errors.append(f"{where}: 'url' must be a non-empty string")
    elif url is not None:
        errors.append(f"{where}: 'url' is only valid on a kind:link action")

    errors.extend(_validate_visible_when(where, entry.get("visible_when")))

    # A kind:"command" action must resolve to a declared command. The command
    # is the explicit `command` field, or the action id itself.
    if kind == "command" and isinstance(action_id, str) and action_id:
        command_id = entry.get("command")
        if command_id is not None and not isinstance(command_id, str):
            errors.append(f"{where}: 'command' must be a string")
        else:
            target = command_id or action_id
            if command_ids and target not in command_ids:
                errors.append(
                    f"{where}: command '{target}' is not a declared command"
                )

    return errors


def _validate_visible_when(where: str, vw: Any) -> list[str]:
    """Validate a visible_when block: a single {key, operator, value} condition,
    or a {any: [...]} / {all: [...]} group of them. Light-touch — unknown extra
    keys are tolerated, only the recognized shapes are checked.
    """
    if vw is None:
        return []
    if not isinstance(vw, dict):
        return [f"{where}: 'visible_when' must be a mapping"]

    errors: list[str] = []
    if "any" in vw or "all" in vw:
        for group_key in ("any", "all"):
            if group_key not in vw:
                continue
            group = vw[group_key]
            if not isinstance(group, list) or not group:
                errors.append(
                    f"{where}: visible_when.{group_key} must be a non-empty list"
                )
                continue
            for j, cond in enumerate(group):
                errors.extend(
                    _validate_condition(f"{where}: visible_when.{group_key}[{j}]", cond)
                )
    else:
        errors.extend(_validate_condition(f"{where}: visible_when", vw))
    return errors


def _validate_condition(where: str, cond: Any) -> list[str]:
    if not isinstance(cond, dict):
        return [f"{where}: condition must be a mapping"]
    errors: list[str] = []
    key = cond.get("key")
    if not isinstance(key, str) or not key:
        errors.append(f"{where}: condition missing 'key' (state key string)")
    op = cond.get("operator", "eq")
    if op not in _VISIBLE_WHEN_OPERATORS:
        errors.append(f"{where}: unknown operator '{op}'")
    return errors
