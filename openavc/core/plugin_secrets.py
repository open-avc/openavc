"""Plugin logins: which of a plugin's settings hold one, and where they sit in
its config.

A plugin declares its logins; the platform moves them out of the project into
this system's login file (``core/device_logins``) and fills them back into the
config it hands the plugin. No plugin calls anything for it, so a plugin that
declares its logins runs unchanged on an older OpenAVC, which ignores the
declaration and keeps the values in the project as it always did.

Two declarations
----------------
- A ``CONFIG_SCHEMA`` field, at the top level or inside a ``group``, holds a
  login by the one rule every reader asks (``drivers/spec.is_secret_setting``):
  ``secret: true``, or the field is named ``password``. A ``mapping_list``
  row's field is never one: its rows have no id to file a login under, so a
  ``secret`` there is ignored, with a warning when the plugin loads.
- ``CONFIG_SECRETS``, a class attribute, for a list of items the plugin keeps
  in its config without a schema (Video Panel's streams, Present's displays)::

      CONFIG_SECRETS = {
          "streams": {"id": "stream_id", "fields": ["password"]},
      }

  Each item's logins are filed under its id. ``"generated": True`` says the
  plugin makes those values itself (a key it hands out): nobody is asked for
  one, and when none is saved on this system the plugin is handed an empty
  value and makes a new one.

Where a login sits
------------------
A login is filed under its plugin and an *item*: ``""`` for a schema field,
whose name is its dotted path (``password``, ``connection.api_key``), or
``streams[stream_id=cam1]`` for a list item, whose name is the field in the
item (``password``). The item says how to find itself in a config without the
plugin's class, so the rollback put-back, which runs before any plugin is
loaded, and a plugin that is not installed still find their logins.

What the plugin is handed
-------------------------
``fill`` puts each saved login back in place of the marker, and blanks every
marker left, so a plugin is never handed the marker as a value: unlike a
device, which refuses to dial a marker (``login_missing``), a plugin has no
refusal, and Present would take the marker itself as a display key anyone
could type.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from openavc.drivers.spec import is_secret_setting
from openavc.utils.logger import get_logger

log = get_logger(__name__)

#: The marker, as ``core/device_logins.SAVED_LOGIN`` (imported there from
#: here would be circular: that module imports this one).
_MARKER = "<saved on this system>"

_ITEM = re.compile(r"^(?P<list>[^\[\]]+)\[(?P<id_key>[^=\[\]]+)=(?P<id>.*)\]$")
_LIST_DECLARATION_KEYS = frozenset({"id", "fields", "generated"})


@dataclass(frozen=True)
class ListSecrets:
    """One ``CONFIG_SECRETS`` entry: the list, the key holding each item's id,
    the item fields that hold a login, and whether the plugin makes them."""

    key: str
    id_key: str
    fields: tuple[str, ...]
    generated: bool = False


@dataclass(frozen=True)
class PluginSecrets:
    """Every place a plugin's config holds a login."""

    #: Schema fields, each as its path through groups (``("connection", "api_key")``).
    schema_fields: tuple[tuple[str, ...], ...] = ()
    lists: tuple[ListSecrets, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.schema_fields or self.lists)


# --- the declarations ---------------------------------------------------------


def _schema_fields(schema: Any, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    fields: list[tuple[str, ...]] = []
    if not isinstance(schema, dict):
        return fields
    for name, definition in schema.items():
        if not isinstance(definition, dict):
            continue
        if definition.get("type") == "group":
            fields.extend(_schema_fields(definition.get("fields"), (*prefix, str(name))))
        elif definition.get("type") == "mapping_list":
            continue
        elif is_secret_setting(str(name), definition):
            fields.append((*prefix, str(name)))
    return fields


def unsupported_row_secrets(schema: Any, prefix: str = "") -> list[str]:
    """The ``mapping_list`` row fields a schema marks as logins, which are not
    supported (a row has no id), as ``list.field``. For the loader's warning."""
    found: list[str] = []
    if not isinstance(schema, dict):
        return found
    for name, definition in schema.items():
        if not isinstance(definition, dict):
            continue
        where = f"{prefix}{name}"
        if definition.get("type") == "group":
            found.extend(unsupported_row_secrets(definition.get("fields"), f"{where}."))
        elif definition.get("type") == "mapping_list":
            rows = definition.get("item_schema")
            if isinstance(rows, dict):
                found.extend(
                    f"{where}.{field}" for field, d in rows.items()
                    if isinstance(d, dict) and is_secret_setting(str(field), d)
                )
    return found


def config_secrets_problem(value: Any, schema: Any = None) -> str | None:
    """What is wrong with a ``CONFIG_SECRETS`` declaration, or None."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return "CONFIG_SECRETS must be a dict"
    for key, entry in value.items():
        if not isinstance(key, str) or not key or "[" in key or "]" in key:
            return f"CONFIG_SECRETS key {key!r} must be a config key"
        if isinstance(schema, dict) and key in schema:
            return f"CONFIG_SECRETS['{key}'] is also a CONFIG_SCHEMA field; declare its logins in one place"
        if not isinstance(entry, dict):
            return f"CONFIG_SECRETS['{key}'] must be a dict"
        unknown = set(entry) - _LIST_DECLARATION_KEYS
        if unknown:
            return f"CONFIG_SECRETS['{key}'] has unknown keys: {sorted(unknown)}"
        id_key = entry.get("id")
        if not isinstance(id_key, str) or not id_key or "=" in id_key or "[" in id_key or "]" in id_key:
            return f"CONFIG_SECRETS['{key}']['id'] must name the key holding each item's id"
        fields = entry.get("fields")
        if (
            not isinstance(fields, list) or not fields
            or not all(isinstance(f, str) and f for f in fields)
            or len(set(fields)) != len(fields)
        ):
            return f"CONFIG_SECRETS['{key}']['fields'] must be a list of distinct field names"
        if id_key in fields:
            return f"CONFIG_SECRETS['{key}']: the id key '{id_key}' cannot also be a login"
        if "generated" in entry and not isinstance(entry["generated"], bool):
            return f"CONFIG_SECRETS['{key}']['generated'] must be true or false"
    return None


def declared(plugin_class: Any) -> PluginSecrets:
    """Every place ``plugin_class`` declares a login. A malformed
    ``CONFIG_SECRETS`` declares nothing (the loader refuses to start it)."""
    if plugin_class is None:
        return PluginSecrets()
    schema = getattr(plugin_class, "CONFIG_SCHEMA", None)
    raw = getattr(plugin_class, "CONFIG_SECRETS", None)
    lists: list[ListSecrets] = []
    if raw is not None and config_secrets_problem(raw, schema) is None:
        for key, entry in raw.items():
            lists.append(ListSecrets(
                key=key, id_key=entry["id"], fields=tuple(entry["fields"]),
                generated=bool(entry.get("generated", False)),
            ))
    return PluginSecrets(schema_fields=tuple(_schema_fields(schema)), lists=tuple(lists))


def declared_for(plugin_id: str) -> PluginSecrets:
    """The logins the installed plugin ``plugin_id`` declares; none when it is
    not installed (its logins move once it is)."""
    from openavc.core.plugin_loader import _PLUGIN_CLASS_REGISTRY

    return declared(_PLUGIN_CLASS_REGISTRY.get(plugin_id))


# --- where a login sits ---------------------------------------------------------


def item_name(list_key: str, id_key: str, item_id: Any) -> str:
    """The item a list entry's logins are filed under."""
    return f"{list_key}[{id_key}={item_id}]"


def holder_of(config: Any, item: str) -> dict[str, Any] | None:
    """The dict in ``config`` that holds ``item``'s logins: the config itself
    for ``""``, the list entry with that id otherwise; None when it is not
    there (or two entries share the id)."""
    if not isinstance(config, dict):
        return None
    if item == "":
        return config
    match = _ITEM.match(item)
    if match is None:
        return None
    entries = config.get(match["list"])
    if not isinstance(entries, list):
        return None
    found = [
        e for e in entries
        if isinstance(e, dict) and _id_text(e.get(match["id_key"])) == match["id"]
    ]
    return found[0] if len(found) == 1 else None


def _id_text(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip() if isinstance(value, str) else str(value)
    return text or None


def get_field(holder: Any, field: str) -> Any:
    """The value at ``field`` (dotted through groups) in ``holder``."""
    node = holder
    for part in field.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def set_field(holder: dict[str, Any], field: str, value: Any) -> None:
    """Set ``field`` (dotted through groups) in ``holder``; the groups exist."""
    parts = field.split(".")
    node = holder
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def places(config: Any, secrets: PluginSecrets) -> list[tuple[str, str, bool]]:
    """Every place in ``config`` that ``secrets`` declares a login, as
    (item, field, generated), whether or not it holds a value. A list entry
    without an id, or whose id another entry shares, has no place: nothing
    can say which entry a login filed under that id belongs to."""
    found: list[tuple[str, str, bool]] = []
    if not isinstance(config, dict):
        return found
    for path in secrets.schema_fields:
        found.append(("", ".".join(path), False))
    for decl in secrets.lists:
        entries = config.get(decl.key)
        if not isinstance(entries, list):
            continue
        ids = [_id_text(e.get(decl.id_key)) if isinstance(e, dict) else None for e in entries]
        shared = sorted({i for i in ids if i is not None and ids.count(i) > 1})
        for item_id in shared:
            log.warning(
                "Two entries of '%s' share the id '%s', so their logins stay where they are",
                decl.key, item_id,
            )
        for item_id in ids:
            if item_id is None or item_id in shared:
                continue
            item = item_name(decl.key, decl.id_key, item_id)
            found.extend((item, field, decl.generated) for field in decl.fields)
    return found


def _usable(value: Any) -> bool:
    return isinstance(value, str) and value != "" and value != _MARKER


def find(config: Any, secrets: PluginSecrets) -> dict[str, dict[str, str]]:
    """The logins ``config`` holds in the clear, by item: field to value."""
    found: dict[str, dict[str, str]] = {}
    for item, field, _ in places(config, secrets):
        value = get_field(holder_of(config, item), field)
        if _usable(value):
            found.setdefault(item, {})[field] = value
    return found


def marked(config: dict[str, Any], found: dict[str, dict[str, str]]) -> dict[str, Any]:
    """A copy of ``config`` with the marker in each place ``found`` names."""
    out = copy.deepcopy(config)
    for item, fields in found.items():
        holder = holder_of(out, item)
        if holder is None:
            continue
        for field in fields:
            if _usable(get_field(holder, field)):
                set_field(holder, field, _MARKER)
    return out


def marked_places(config: Any, items: Iterable[tuple[str, Iterable[str]]]) -> list[tuple[str, str]]:
    """Of ``items`` ((item, fields) pairs, from the login file), each place
    that holds the marker in ``config``."""
    found: list[tuple[str, str]] = []
    for item, fields in items:
        holder = holder_of(config, item)
        if holder is None:
            continue
        found.extend((item, field) for field in fields if get_field(holder, field) == _MARKER)
    return found


def fill(
    config: Any,
    saved: Iterable[tuple[str, dict[str, str]]],
) -> Any:
    """``config`` with each marker replaced by its saved login from ``saved``
    ((item, {field: login}) pairs), and every marker left blanked to ``""``.
    Returned as it is when it holds no marker."""
    if not _holds_marker(config):
        return config
    out = copy.deepcopy(config)
    for item, fields in saved:
        holder = holder_of(out, item)
        if holder is None:
            continue
        for field, value in fields.items():
            if get_field(holder, field) == _MARKER and _usable(value):
                set_field(holder, field, value)
    return _blanked(out)


def _holds_marker(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_holds_marker(v) for v in value.values())
    if isinstance(value, list):
        return any(_holds_marker(v) for v in value)
    return value == _MARKER


def _blanked(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _blanked(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_blanked(v) for v in value]
    return "" if value == _MARKER else value


def waiting(
    config: Any, secrets: PluginSecrets, saved: Callable[[str, str], str | None],
) -> list[tuple[str, str]]:
    """The declared places in ``config`` that hold the marker with nothing
    behind it (``saved(item, field)`` is None), the generated ones aside: a
    login somebody has to enter here."""
    return [
        (item, field) for item, field, generated in places(config, secrets)
        if not generated
        and get_field(holder_of(config, item), field) == _MARKER
        and saved(item, field) is None
    ]


def display_path(item: str, field: str) -> str:
    """A place as the Programmer names it: ``password``,
    ``connection.api_key``, ``streams[cam1].password``."""
    match = _ITEM.match(item)
    if match is None:
        return field
    return f"{match['list']}[{match['id']}].{field}"
