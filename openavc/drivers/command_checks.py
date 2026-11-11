"""A command's load-time checks: one entry of a driver definition's ``commands:``.

A YAML command is checked in this order: how it sends (exactly one of
``send``, ``path``/``method``, ``address``, ``udp``, ``ir``), an OSC command's
``args``, a ``udp:`` block, its parameters' form aids (``param_aid_checks``),
what ``sets:`` and ``query_for:`` name, then the four checks a Python
driver's command gets too: ``confirm``, ``sleeps_device``, ``read_back`` and
the file rules. ``validate_driver_definition`` asks ``CommandSection`` about
each command and tags what comes back with the command's place;
``python_info`` asks ``SharedCommandChecks`` about each command of a
``DRIVER_INFO``. Those four checks are listed once, in
``SharedCommandChecks.errors``, so a field both kinds of driver may declare is
one function and one line there.

The runtime's twins: ``configurable.py`` sends each form, ``side_sends.py``
the ``udp:`` and ``ir:`` ones; ``rereads.py`` sends a ``read_back``;
``core/device_sleep.py`` waits out a ``sleeps_device``; ``command_files.py``
is how a Python driver takes a file and hands one back. The simulator answers
``sets:`` and ``query_for:`` (``simulator/yaml_auto.py``). Two checks
of a command run after every command: the references its parameters make into
``child_entity_types`` (``child_type_checks.child_param_reference_errors``) and
the placeholders in its templates (``substitution_checks``).

A value ``python_info`` could not read from the source (a constant, a call)
is not judged. Two rules say so, and they are not the same: ``_unevaluated``
matches that reader's placeholder by its class name (this module cannot import
the reader, which imports it), where the file rules' ``_judgeable`` judges only
None and a plain scalar, list, tuple or mapping, so there a date YAML reads is
skipped too.

Purity contract, as for ``avcdriver_semantic``: standard library and the
sibling contract modules only. The module is vendored with the rest of the
driver contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

import re
from typing import Any, Callable

from openavc.drivers.declared_names import (
    UNEVALUATED_KEY,
    DeclaredSections,
    child_writable_props,
    did_you_mean,
    substitution_names,
)
from openavc.drivers.param_aid_checks import param_aid_errors
from openavc.drivers.spec import (
    FILE_PARAM_EXCLUDED_KEYS,
    FILE_PARAM_MAX_BYTES,
    OSC_ARG_TYPES,
    PLACEHOLDER,
)

# Each way a command can send, and how its declaration says so. An empty
# ``send`` is the Driver Builder's seed for a new command, not a form.
_FORMS: tuple[tuple[str, Callable[[dict[str, Any]], Any]], ...] = (
    ("send", lambda cmd: cmd.get("send")),
    ("path/method", lambda cmd: cmd.get("path") or cmd.get("method")),
    ("address", lambda cmd: cmd.get("address") is not None),
    ("udp", lambda cmd: cmd.get("udp") is not None),
    ("ir", lambda cmd: cmd.get("ir") is not None),
)

# The forms sent beside the device's own connection (a datagram, a bridge's
# emitter), so no reply to them comes back over it.
_SIDE_FORMS = ("udp", "ir")

_ONE_FORM = (
    "'send' (TCP/serial), 'path'/'method' (HTTP), 'address' (OSC), or 'udp' "
    "(a datagram beside the main transport)"
)


def _forms(cmd_def: dict[str, Any]) -> list[str]:
    """The ways a command declares it sends, in ``_FORMS`` order."""
    return [name for name, declares in _FORMS if declares(cmd_def)]


def _side_form(cmd_def: dict[str, Any]) -> str | None:
    """``udp`` or ``ir`` when the command is sent beside the connection."""
    forms = _forms(cmd_def)
    return next((form for form in _SIDE_FORMS if form in forms), None)


class CommandSection:
    """A YAML driver's commands, and what each command's checks read from the
    rest of the definition: the commands themselves, the device's state
    variables and its child types (each as ``declared`` read it), its
    transport and the names a template may substitute."""

    def __init__(self, driver_def: dict[str, Any], declared: DeclaredSections) -> None:
        self.declared_vars = declared.state_variables
        self.child_types = declared.child_types
        self.config_names = substitution_names(driver_def)
        self.shared = SharedCommandChecks(
            declared.commands, driver_def.get("transport"), python=False
        )

    def command_errors(self, cmd_name: Any, cmd_def: Any) -> list[str]:
        """Everything one command's declaration says wrong, in order."""
        where = f"Command '{cmd_name}'"
        if not isinstance(cmd_def, dict):
            return [f"{where}: must be a dict"]
        forms = _forms(cmd_def)
        errors = _form_errors(where, forms)
        if "address" in forms:
            errors.extend(osc_args_errors(where, cmd_def.get("args")))
        if "udp" in forms:
            errors.extend(udp_block_errors(where, cmd_def.get("udp"), self.config_names))
        errors.extend(param_aid_errors(where, cmd_def.get("params")))
        errors.extend(self._state_name_errors(where, cmd_def))
        errors.extend(self.shared.errors(cmd_name, cmd_def))
        return errors

    def _state_name_errors(self, where: str, cmd_def: dict[str, Any]) -> list[str]:
        """What ``sets:`` and ``query_for:`` name.

        Both drive the generated simulator, so a name that resolves to nothing
        would silently declare nothing. A command addressed to exactly one
        child (a single ``child_id`` parameter) may name that child type's
        variables too: the simulator routes the effect to the addressed
        child's own state.
        """
        params = cmd_def.get("params")
        params = params if isinstance(params, dict) else {}
        names = _StateNames(self.declared_vars, self._addressed_child_vars(params))
        return names.sets_errors(where, cmd_def.get("sets"), params) + (
            names.query_for_errors(where, cmd_def.get("query_for"))
        )

    def _addressed_child_vars(self, params: dict[str, Any]) -> set[str]:
        """The writable properties of the one child type this command is
        addressed to, or none when it is not addressed to exactly one."""
        child_param_types = [
            p.get("child_type")
            for p in params.values()
            if isinstance(p, dict) and p.get("type") == "child_id"
        ]
        if len(child_param_types) != 1:
            return set()
        if child_param_types[0] not in self.child_types:
            return set()
        return child_writable_props(self.child_types.get(child_param_types[0]))


class _StateNames:
    """The state variables a command may set or ask for.

    ``child_vars`` is empty when the command is not addressed to a single
    child type; once it is, the reserved properties are always in it, which is
    what the message wording follows.
    """

    def __init__(self, declared_vars: dict[str, Any], child_vars: set[str]) -> None:
        self.declared_vars = declared_vars
        self.child_vars = child_vars

    def _undeclared(self, name: Any) -> bool:
        return name not in self.declared_vars and name not in self.child_vars

    def _not_declared(self) -> str:
        if self.child_vars:
            return (
                "a declared state variable (device-level or of the addressed "
                "child type)"
            )
        return "a declared state variable"

    def sets_errors(self, where: str, sets_def: Any, params: dict[str, Any]) -> list[str]:
        """``sets:`` maps a state variable to a literal or a bare ``{param}``."""
        if sets_def is None:
            return []
        if not isinstance(sets_def, dict):
            return [f"{where}: 'sets' must be a mapping"]
        errors: list[str] = []
        for var_name, set_value in sets_def.items():
            if self._undeclared(var_name):
                errors.append(
                    f"{where}: sets '{var_name}' which is not {self._not_declared()}"
                )
            if not _literal_or_param(set_value, params):
                errors.append(
                    f"{where}: sets.{var_name} value {set_value!r} must be a "
                    f"literal or a bare {{param}} reference to a declared parameter"
                )
        return errors

    def query_for_errors(self, where: str, query_for: Any) -> list[str]:
        """``query_for:`` names the one state variable this command reads."""
        if query_for is None:
            return []
        if not isinstance(query_for, str) or not query_for:
            return [f"{where}: 'query_for' must name a state variable"]
        if self._undeclared(query_for):
            return [f"{where}: query_for '{query_for}' is not {self._not_declared()}"]
        return []


_BARE_PARAM = re.compile(r"\{([^{}]+)\}")


def _literal_or_param(value: Any, params: dict[str, Any]) -> bool:
    """A ``sets:`` value with no brace in it, or exactly one ``{param}`` of
    this command's."""
    if not isinstance(value, str):
        return True
    if "{" not in value and "}" not in value:
        return True
    ref = _BARE_PARAM.fullmatch(value)
    return bool(ref) and ref.group(1) in params


def _form_errors(where: str, forms: list[str]) -> list[str]:
    """A command sends exactly one way (``forms``: the ways it declares)."""
    if not forms:
        return [f"{where}: must have {_ONE_FORM}"]
    if len(forms) > 1:
        return [
            f"{where}: declares {' and '.join(forms)}, but a command sends one "
            f"way. Keep one of {_ONE_FORM}"
        ]
    return []


def osc_args_errors(where: str, arg_defs: Any) -> list[str]:
    """An OSC message's ``args``: each a mapping whose ``type`` tag the
    encoder knows, so a typo fails at load rather than being dropped when the
    message is built. A device setting's OSC ``write:`` asks this too."""
    if arg_defs is None:
        return []
    if not isinstance(arg_defs, list):
        return [f"{where}: args must be a list"]
    errors: list[str] = []
    for j, arg in enumerate(arg_defs):
        if not isinstance(arg, dict):
            errors.append(f"{where}: args[{j}] must be a mapping")
            continue
        arg_type = arg.get("type", "f")
        if arg_type not in OSC_ARG_TYPES:
            errors.append(
                f"{where}: args[{j}] unknown OSC type '{arg_type}' "
                f"(expected one of f/i/s/h/d/T/F/N)"
            )
    return errors


def udp_block_errors(where: str, udp_def: Any, config_names: set[str]) -> list[str]:
    """A command's ``udp:`` block, a datagram sent beside the transport.

    Exactly one of payload / magic_packet; a payload needs a port (a number in
    range, or a {config_field} placeholder); a magic_packet names a config
    field that can hold the MAC. A block that passes here is one the runtime
    can always turn into a datagram, so the only thing left to fail at send
    time is a MAC the device has not reported yet. ``config_names`` is what a
    placeholder may name (``declared_names.substitution_names``).
    """
    if not isinstance(udp_def, dict):
        return [f"{where}: 'udp' must be a mapping (host, port, payload or magic_packet)"]
    payload = udp_def.get("payload")
    mac_field = udp_def.get("magic_packet")
    errors = _datagram_errors(where, payload, mac_field)
    host = udp_def.get("host")
    if host is not None and not isinstance(host, str):
        errors.append(f"{where}: 'udp.host' must be a string")
    needs_port = bool(payload) and not mac_field
    errors.extend(_udp_port_errors(where, udp_def.get("port"), needs_port, config_names))
    errors.extend(_magic_packet_errors(where, mac_field, config_names))
    if "broadcast" in udp_def and not isinstance(udp_def.get("broadcast"), bool):
        errors.append(f"{where}: 'udp.broadcast' must be true or false")
    return errors


def _datagram_errors(where: str, payload: Any, mac_field: Any) -> list[str]:
    """A datagram is a payload or a wake packet, never both, never neither."""
    if payload and mac_field:
        return [
            f"{where}: 'udp' declares both 'payload' and 'magic_packet'; a "
            f"datagram is one or the other"
        ]
    if not payload and not mac_field:
        return [
            f"{where}: 'udp' needs a 'payload' (the datagram) or a "
            f"'magic_packet' (the field holding the MAC to wake)"
        ]
    return []


def _udp_port_errors(
    where: str, port: Any, needs_port: bool, config_names: set[str]
) -> list[str]:
    """The port a payload goes to: a number in range or a config placeholder.
    A wake packet goes to the Wake-on-LAN port when none is given."""
    if port is None or port == "":
        if not needs_port:
            return []
        return [
            f"{where}: 'udp.port' is required with a payload (a number, or a "
            f"{{config_field}} placeholder)"
        ]
    if isinstance(port, bool) or not isinstance(port, (int, str)):
        return [f"{where}: 'udp.port' must be a number or a {{config_field}} placeholder"]
    if isinstance(port, int):
        return _port_range_errors(where, port, port)
    return _port_text_errors(where, port, config_names)


def _bare_placeholder(text: str) -> str | None:
    """The name in a value that is one placeholder with no format spec, else
    None: a port is a number, so a spec has nothing to format."""
    match = PLACEHOLDER.fullmatch(text)
    if match is None or match.group(2) is not None:
        return None
    return match.group(1)


def _port_text_errors(where: str, port: str, config_names: set[str]) -> list[str]:
    """A port written as text: a ``{config_field}`` the driver declares, or
    digits."""
    field = _bare_placeholder(port.strip())
    if field is not None:
        if field in config_names:
            return []
        return [
            f"{where}: 'udp.port' names config field '{field}', "
            f"which the driver does not declare"
        ]
    if not port.strip().isdigit():
        return [
            f"{where}: 'udp.port' {port!r} is neither a number nor a "
            f"{{config_field}} placeholder"
        ]
    return _port_range_errors(where, int(port.strip()), port)


def _port_range_errors(where: str, number: int, written: Any) -> list[str]:
    if 1 <= number <= 65535:
        return []
    return [f"{where}: 'udp.port' must be between 1 and 65535, got {written}"]


def _magic_packet_errors(where: str, mac_field: Any, config_names: set[str]) -> list[str]:
    """The field a wake packet's MAC is read from.

    A config field, so a display that has never connected can still be woken
    by typing its MAC in (and so the Builder's dry run can build the packet).
    A state variable of the same name is read first when the device has
    reported one; on its own it is a wake that cannot work until the first
    connect, which is the case the wake exists for.
    """
    if mac_field is None:
        return []
    if not isinstance(mac_field, str) or not mac_field:
        return [f"{where}: 'udp.magic_packet' must name a config field"]
    if mac_field in config_names:
        return []
    return [
        f"{where}: 'udp.magic_packet' names '{mac_field}', which is not a config "
        f"field. Declare it in config_schema so the MAC can be typed in; a state "
        f"variable of the same name is read first once the device reports one"
    ]


class SharedCommandChecks:
    """The checks a command gets whichever kind of driver declares it.

    ``python`` is the surface: a Python driver's ``DRIVER_INFO`` is refused
    what only the YAML runtime acts on (``read_back``) and may declare what
    only Python can do (a file).
    """

    def __init__(self, commands: dict[str, Any], transport: Any, *, python: bool) -> None:
        self.commands = commands
        self.transport = transport
        self.python = python

    def errors(self, cmd_name: Any, cmd_def: dict[str, Any]) -> list[str]:
        """The four checks, in the order they are reported."""
        where = f"Command '{cmd_name}'"
        return [
            *command_confirm_errors(where, cmd_def),
            *command_sleep_errors(cmd_name, cmd_def, self.commands),
            *self.read_back_errors(cmd_name, cmd_def),
            *file_command_errors(where, cmd_def, python=self.python),
        ]

    def read_back_errors(self, cmd_name: Any, cmd_def: dict[str, Any]) -> list[str]:
        """A command's ``read_back`` is something the runtime can send after it.

        ``true`` reads the command's own OSC address or HTTP path; a string or
        a list of them names a command, or is a query sent as a poll query is.
        A Python driver is refused the field outright: it reads a value back
        in its own send_command.
        """
        raw = cmd_def.get("read_back")
        if raw is None or _unevaluated(raw):
            return []
        where = f"Command '{cmd_name}'"
        refused = self._read_back_refusal(where, cmd_def, raw)
        if refused is not None:
            return refused
        listed = isinstance(raw, list)
        own_params = cmd_def.get("params")
        own_params = own_params if isinstance(own_params, dict) else {}
        errors: list[str] = []
        for i, entry in enumerate(raw if listed else [raw]):
            at = f"{where}: read_back" + (f"[{i}]" if listed else "")
            errors.extend(self._read_back_entry_errors(at, cmd_name, entry, own_params))
        return errors

    def _read_back_refusal(
        self, where: str, cmd_def: dict[str, Any], raw: Any
    ) -> list[str] | None:
        """The one message a ``read_back`` gets as a whole, or None when its
        entries are each to be checked."""
        if self.python:
            return [
                f"{where}: 'read_back' is read by YAML drivers; a Python driver "
                f"reads the value back in its own send_command"
            ]
        side = _side_form(cmd_def)
        if side is not None:
            return [
                f"{where}: 'read_back' reads a value back over the device's "
                f"connection, and a '{side}' command is sent beside it, so nothing "
                f"would answer"
            ]
        if raw is True:
            return [] if _reads_its_own_address(cmd_def) else [
                f"{where}: 'read_back: true' reads back from an OSC command's "
                f"own address or an HTTP command's own path. Name the query "
                f"that reads this command's value instead"
            ]
        if not _is_query_list(raw):
            return [f"{where}: 'read_back' must be true, a query, or a list of queries"]
        return None

    def _read_back_entry_errors(
        self, at: str, cmd_name: Any, entry: str, own_params: dict[str, Any]
    ) -> list[str]:
        """One query a ``read_back`` sends: a command, or a raw query."""
        if entry == cmd_name:
            return [f"{at} names this command itself; name the query that reads its value"]
        named = self.commands.get(entry) if isinstance(self.commands, dict) else None
        if isinstance(named, dict):
            return _named_read_back_errors(at, entry, named, own_params)
        if self.transport == "osc" and not entry.startswith("/"):
            return [
                f"{at} '{entry}' is neither a command nor an OSC address "
                f"(an address starts with /)"
            ]
        return []


def _reads_its_own_address(cmd_def: dict[str, Any]) -> bool:
    """An OSC or HTTP command, which ``read_back: true`` reads back from."""
    forms = _forms(cmd_def)
    return "address" in forms or "path/method" in forms


def _is_query_list(raw: Any) -> bool:
    """A non-empty query, or a non-empty list of them."""
    entries = [raw] if isinstance(raw, str) else raw
    if not isinstance(entries, list) or not entries:
        return False
    return all(isinstance(e, str) and e.strip() for e in entries)


def _named_read_back_errors(
    at: str, entry: str, named: dict[str, Any], own_params: dict[str, Any]
) -> list[str]:
    """A ``read_back`` that names a command: one that only reads, over the
    connection, with every parameter it declares one of this command's (it is
    sent with the parameters the two share by name)."""
    errors: list[str] = []
    if named.get("read_back") is not None:
        errors.append(
            f"{at} names '{entry}', which has a read_back of its own. "
            f"Name a command that only reads"
        )
    if _side_form(named) is not None:
        errors.append(
            f"{at} names '{entry}', which is sent beside the device's "
            f"connection, so nothing would answer it"
        )
    declared = named.get("params")
    if not isinstance(declared, dict) or UNEVALUATED_KEY in declared:
        return errors
    errors.extend(
        f"{at} names '{entry}', whose parameter '{pname}' is not a parameter of "
        f"this command, so it would be sent without it"
        for pname in declared
        if pname not in own_params
    )
    return errors


def _unevaluated(value: Any) -> bool:
    """True for ``python_info``'s placeholder for a value read from a constant
    or built at runtime (it is defined there, and that module imports this
    one), which no rule can judge from the source."""
    return type(value).__name__ == "_Unevaluated"


def command_confirm_errors(where: str, cmd_def: dict[str, Any]) -> list[str]:
    """A command's ``confirm`` is a flag or the sentence to ask.

    Anything else is dropped by every door that asks, and the command is sent
    unasked.
    """
    confirm = cmd_def.get("confirm")
    if _unevaluated(confirm) or isinstance(confirm, (bool, str, type(None))):
        return []
    return [f"{where}: 'confirm' must be a boolean or a message string"]


def command_sleep_errors(
    cmd_name: Any, cmd_def: dict[str, Any], commands: dict[str, Any]
) -> list[str]:
    """A command's ``sleeps_device`` names a command that can wake the device.

    The wake command has to work with the device off the network
    (``available_offline``) and say how long the device takes to come back
    (``restarts_device_for``), because the wait after it is sent is the
    driver's measured number and the device that has not returned by then
    reads as the real fault.
    """
    wake = cmd_def.get("sleeps_device")
    if wake is None or _unevaluated(wake):
        return []
    where = f"Command '{cmd_name}'"
    refused = _wake_name_errors(where, cmd_name, wake, commands)
    if refused is not None:
        return refused
    wake_def = commands.get(wake)
    if not isinstance(wake_def, dict):
        return []
    return _wake_command_errors(where, wake, wake_def)


def _wake_name_errors(
    where: str, cmd_name: Any, wake: Any, commands: dict[str, Any]
) -> list[str] | None:
    """What is wrong with the name itself, or None when it names a declared
    command. A name that is not among the commands the reader could see is
    not judged when the command map was built at runtime."""
    if not isinstance(wake, str) or not wake.strip():
        return [f"{where}: 'sleeps_device' must name the command that wakes the device"]
    if wake == cmd_name:
        return [
            f"{where}: 'sleeps_device' names this command itself; name the "
            f"command that wakes the device"
        ]
    names = {n for n in commands if isinstance(n, str) and n != UNEVALUATED_KEY}
    if wake in names:
        return None
    if UNEVALUATED_KEY in commands:
        return []
    return [
        f"{where}: 'sleeps_device' names '{wake}', which is not a declared "
        f"command{did_you_mean(wake, names)}"
    ]


def _wake_command_errors(where: str, wake: str, wake_def: dict[str, Any]) -> list[str]:
    """The wake command can be sent to a device off the network, and says how
    long the device takes to come back."""
    errors: list[str] = []
    offline = wake_def.get("available_offline")
    if not _unevaluated(offline) and offline is not True:
        errors.append(
            f"{where}: 'sleeps_device' names '{wake}', which does not set "
            f"available_offline: true, so it cannot wake a device that is off "
            f"the network"
        )
    restarts = wake_def.get("restarts_device_for")
    if not _unevaluated(restarts) and not restarts:
        errors.append(
            f"{where}: 'sleeps_device' names '{wake}', which declares no "
            f"restarts_device_for: give it the measured seconds the device takes "
            f"to come back after '{wake}'"
        )
    return errors


# The kinds of value the Python reader hands back as themselves; anything else
# is its placeholder for an expression it could not evaluate.
_PLAIN_VALUE_TYPES = frozenset({str, int, float, bool, list, tuple, dict})


def _judgeable(value: Any) -> bool:
    """A value read from source as itself, not a placeholder for an
    expression the Python reader could not evaluate."""
    return value is None or type(value) in _PLAIN_VALUE_TYPES


_ACCEPT_RE = re.compile(r"\.[A-Za-z0-9][A-Za-z0-9._-]*")


def file_command_errors(where: str, cmd_def: dict[str, Any], *, python: bool) -> list[str]:
    """The rules for a command that takes a file or hands one back.

    A Python driver is the only one that may declare either: a YAML driver's
    runtime has no way to put a file on the wire, so a ``.avcdriver``
    declaring one is refused here (the platform never runs the published JSON
    Schema at load, so its enum alone would not stop it).
    """
    errors: list[str] = []
    params = cmd_def.get("params")
    if isinstance(params, dict):
        for pname, pdef in params.items():
            errors.extend(_file_param_errors(f"{where} param '{pname}'", pdef, python))
    errors.extend(_returns_file_errors(where, cmd_def.get("returns_file"), python))
    return errors


def _file_param_errors(label: str, pdef: Any, python: bool) -> list[str]:
    """One parameter: a file param's own keys, or the file keys on another."""
    if not isinstance(pdef, dict) or not _judgeable(pdef.get("type")):
        return []
    if pdef.get("type") != "file":
        return _stray_file_key_errors(label, pdef)
    if not python:
        return [f"{label}: type 'file' requires a Python driver"]
    return _file_param_key_errors(label, pdef)


def _file_param_key_errors(label: str, pdef: dict[str, Any]) -> list[str]:
    """What a file param may say: which endings it takes and how large a file
    may be, and none of the keys that shape a typed or picked value."""
    errors: list[str] = []
    excluded = [key for key in FILE_PARAM_EXCLUDED_KEYS if key in pdef]
    if excluded:
        errors.append(f"{label}: a file param takes no {', '.join(excluded)}")
    if not _lists_file_endings(pdef.get("accept")):
        errors.append(f"{label}: accept must list file-name endings, e.g. [\".bin\"]")
    if not _is_byte_limit(pdef.get("max_bytes")):
        errors.append(
            f"{label}: max_bytes must be a whole number of bytes from 1 "
            f"to {FILE_PARAM_MAX_BYTES} (1 GiB)"
        )
    return errors


def _stray_file_key_errors(label: str, pdef: dict[str, Any]) -> list[str]:
    stray = [key for key in ("accept", "max_bytes") if key in pdef]
    if not stray:
        return []
    verb = "applies" if len(stray) == 1 else "apply"
    return [f"{label}: {' and '.join(stray)} {verb} only to a file param (type: file)"]


def _lists_file_endings(accept: Any) -> bool:
    """No ``accept``, one the reader could not evaluate, or a non-empty list
    of file-name endings (an item it could not evaluate passes)."""
    if accept is None or not _judgeable(accept):
        return True
    if not isinstance(accept, (list, tuple)) or not accept:
        return False
    return all(_is_file_ending(item) for item in accept)


def _is_file_ending(item: Any) -> bool:
    if not _judgeable(item):
        return True
    return isinstance(item, str) and bool(_ACCEPT_RE.fullmatch(item))


def _is_byte_limit(max_bytes: Any) -> bool:
    """No ``max_bytes``, one the reader could not evaluate, or a whole number
    of bytes up to the platform's limit."""
    if max_bytes is None or not _judgeable(max_bytes):
        return True
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        return False
    return 1 <= max_bytes <= FILE_PARAM_MAX_BYTES


def _returns_file_errors(where: str, returns_file: Any, python: bool) -> list[str]:
    if returns_file is None or not _judgeable(returns_file):
        return []
    if not isinstance(returns_file, bool):
        return [f"{where}: 'returns_file' must be true or false"]
    if returns_file and not python:
        return [f"{where}: 'returns_file' requires a Python driver"]
    return []
