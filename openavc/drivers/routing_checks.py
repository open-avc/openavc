"""A driver's ``routing:`` block, checked when it loads: where the driver's
routing lives, so a Matrix can be set up from the device. The runtime reads
it in ``ui/matrix_inference._declared_planes``.

The block is a set of names pointing at other parts of the same file: a child
type, a property on it or on the device, a command and that command's
parameters. Every one is a place a typo hides, and it hides well: nothing
here runs when the driver runs, so a wrong ``route_property`` is a Matrix
whose crosspoints never light and a wrong ``command`` a tap that sends
nothing, found in a commissioned space weeks later. Declaring the block
REPLACES the guess, so an unusable declaration is worse than none: it takes
the place of an inference that would have worked.

A plane takes the keys in ``ROUTING_INHERITED`` from the block above it and
its own value wins, None and empty text included, which is how the runtime
merges it too. The plane's fixed ``params`` are its own; the block's are
not judged here. Names are read against what the driver declares
(``_Targets``); a set built at runtime (``UNEVALUATED_KEY``) is not judged.

Each part of a plane is one method of ``_PlaneChecks``, in the order its
messages have always come: the routed property, the destination and where
the property lives, the source, the command, what needs a command to ride
on, the route's two ends, the fixed extras; then whether an earlier plane
already watches the same property and routes it the same way.

Purity contract, as for ``avcdriver_semantic``: standard library and the
sibling ``declared_names``. The module is vendored with the rest of the driver
contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

from typing import Any

from openavc.drivers.declared_names import UNEVALUATED_KEY, did_you_mean

#: The keys a plane may inherit from the block above it.
ROUTING_INHERITED: tuple[str, ...] = (
    "destination_child_type",
    "source_child_type",
    "command",
    "destination_param",
    "source_param",
)

_ENDS: tuple[str, str] = ("destination_param", "source_param")

_NEEDS_PLANES = (
    "routing: needs a non-empty 'planes' list, the independent things "
    "this device routes (usually one; a decoder that routes video, "
    "audio and USB separately declares three)"
)

# What makes two planes the same matrix: the destination type, the property,
# the command as written and the plane's own fixed params.
_PlaneKey = tuple[str, str, str, tuple[tuple[str, str], ...]]


def routing_block_errors(driver_def: dict[str, Any]) -> list[str]:
    """Check the optional ``routing:`` block against what the driver declares."""
    block = driver_def.get("routing")
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["routing: must be a mapping with a 'planes' list"]

    errors: list[str] = []
    planes = block.get("planes")
    if not isinstance(planes, list) or not planes:
        errors.append(_NEEDS_PLANES)
        planes = []
    errors.extend(_inherited_key_errors(block))

    targets = _Targets(driver_def)
    seen: dict[_PlaneKey, int] = {}
    for index, raw in enumerate(planes):
        if not isinstance(raw, dict):
            errors.append(f"routing.planes[{index}]: must be a mapping")
            continue
        checks = _PlaneChecks(index, raw, block, targets)
        errors.extend(checks.errors())
        errors.extend(_duplicate_errors(checks, seen))
    return errors


def _inherited_key_errors(block: dict[str, Any]) -> list[str]:
    """A key the block sets for every plane is a non-empty name."""
    return [
        f"routing.{key}: must be a non-empty string"
        for key in ROUTING_INHERITED
        if block.get(key) is not None and not _is_name(block.get(key))
    ]


def _is_name(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _text_or_none(value: Any) -> str | None:
    """A name as the checks read it: text as written, anything else none."""
    return value if isinstance(value, str) else None


class _Targets:
    """What the block's names resolve against, read once from the driver: its
    child types, its commands and its own state variables, each a mapping or
    empty when the section is missing or malformed."""

    def __init__(self, driver_def: dict[str, Any]) -> None:
        self.child_types = _mapping(driver_def.get("child_entity_types"))
        self.commands = _mapping(driver_def.get("commands"))
        self.device_vars = _mapping(driver_def.get("state_variables"))

    def child_state_vars(self, child_type: str) -> dict[str, Any] | None:
        """A declared child type's state variables, or None when they cannot
        be judged: the type is not a mapping, or a Python driver builds its
        child properties at construction time (``UNEVALUATED_KEY``)."""
        schema = self.child_types.get(child_type)
        if not isinstance(schema, dict):
            return None
        return _readable(schema.get("state_variables"))

    def command_params(self, command: str) -> dict[str, Any] | None:
        """A declared command's parameters, or None when they cannot be
        judged: the command is not a mapping, or its params are computed."""
        entry = self.commands.get(command)
        if not isinstance(entry, dict):
            return None
        return _readable(entry.get("params"))


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _readable(declared: Any) -> dict[str, Any] | None:
    if declared == UNEVALUATED_KEY:
        return None
    return _mapping(declared)


class _PlaneChecks:
    """One plane, read once: its own fields over the block's, and the names
    in it as the checks take them (a name that is not text is no name, and the
    routed property is the plane's own and empty when it is not a name). Each
    check only reports."""

    def __init__(
        self, index: int, raw: dict[str, Any], block: dict[str, Any], targets: _Targets,
    ) -> None:
        self.where = f"routing.planes[{index}]"
        self.index = index
        self.raw = raw
        self.block = block
        self.targets = targets
        self.plane = {**{k: block.get(k) for k in ROUTING_INHERITED}, **raw}
        route_property = raw.get("route_property")
        self.route_property = route_property if _is_name(route_property) else ""
        self.dest_type = _text_or_none(self.plane.get("destination_child_type"))
        self.command = _text_or_none(self.plane.get("command"))
        # The command's parameters, when the command is a declared one.
        self.params: dict[str, Any] | None = (
            targets.command_params(self.command)
            if self.command and self.command in targets.commands else None
        )

    def errors(self) -> list[str]:
        """Every part of the plane, in the order its messages have always come."""
        return [
            *self._route_property_errors(),
            *self._destination_errors(),
            *self._source_errors(),
            *self._command_errors(),
            *self._needs_a_command_errors(),
            *self._end_errors(),
            *self._fixed_param_errors(),
        ]

    def _route_property_errors(self) -> list[str]:
        if self.route_property:
            return []
        return [
            f"{self.where}: missing required 'route_property' (the property "
            f"reporting what is routed here; without it a Matrix can "
            f"switch and can never show what is on)"
        ]

    def _destination_errors(self) -> list[str]:
        """The destination type is declared, and the routed property lives
        where the route lands: on the destination child when there is one, on
        the device itself when the device IS the destination. An undeclared
        type says nothing about the property."""
        dest_type = self.dest_type
        child_types = self.targets.child_types
        if dest_type and dest_type not in child_types:
            return [
                f"{self.where}: destination_child_type '{dest_type}' is not a "
                f"declared child_entity_type"
                + did_you_mean(dest_type, set(child_types))
            ]
        if not self.route_property:
            return []
        if dest_type:
            declared_vars = self.targets.child_state_vars(dest_type)
            origin = f"child type '{dest_type}'"
        else:
            declared_vars = self.targets.device_vars
            origin = "this driver"
        if declared_vars is None or self.route_property in declared_vars:
            return []
        return [
            f"{self.where}: route_property '{self.route_property}' is not a "
            f"state variable of {origin}"
            + did_you_mean(self.route_property, set(declared_vars))
        ]

    def _source_errors(self) -> list[str]:
        source_type = self.plane.get("source_child_type")
        child_types = self.targets.child_types
        if not _is_name(source_type) or source_type in child_types:
            return []
        return [
            f"{self.where}: source_child_type '{source_type}' is not a "
            f"declared child_entity_type"
            + did_you_mean(source_type, set(child_types))
        ]

    def _command_errors(self) -> list[str]:
        command = self.command
        if not command:
            return []
        commands = self.targets.commands
        if command in commands:
            return []
        return [
            f"{self.where}: command '{command}' is not a declared command"
            + did_you_mean(command, set(commands))
        ]

    def _needs_a_command_errors(self) -> list[str]:
        """A parameter name, or a fixed extra, without a command to send it
        on: nothing would carry it, so it reads as configured and does
        nothing. A route parameter counts from the plane or the block; fixed
        extras only from the plane."""
        if self.command:
            return []
        if self.raw.get("params") is not None:
            return [
                f"{self.where}: 'params' needs a 'command' to send them on. "
                f"Declare the routing command here or on the routing block"
            ]
        if any(self.raw.get(k) is not None or self.block.get(k) is not None for k in _ENDS):
            return [
                f"{self.where}: names a routing parameter but no 'command'. "
                f"Declare the routing command here or on the routing block"
            ]
        return []

    def _ends(self) -> list[tuple[str, str]]:
        """The route's two ends the plane names, as (key, parameter)."""
        return [(key, self.plane.get(key)) for key in _ENDS if _is_name(self.plane.get(key))]

    def _end_errors(self) -> list[str]:
        params = self.params
        if params is None:
            return []
        return [
            f"{self.where}: {key} '{value}' is not a parameter of "
            f"command '{self.command}'" + did_you_mean(value, set(params))
            for key, value in self._ends()
            if value not in params
        ]

    def _fixed_param_errors(self) -> list[str]:
        """The plane's fixed extras are a mapping of the command's parameters,
        and none of them is an end of the route, which the panel fills in."""
        extra = self.raw.get("params")
        if extra is None:
            return []
        if not isinstance(extra, dict):
            return [f"{self.where}: 'params' must be a mapping of parameter name to value"]
        taken = [value for _, value in self._ends()]
        errors: list[str] = []
        for name in extra:
            if name in taken:
                end = "destination" if name == self.plane.get("destination_param") else "source"
                errors.append(
                    f"{self.where}: params.{name} is already the route's "
                    f"{end}; a fixed value here would overwrite what the panel routes"
                )
            elif self.params is not None and name not in self.params:
                errors.append(
                    f"{self.where}: params.{name} is not a parameter of "
                    f"command '{self.command}'" + did_you_mean(name, set(self.params))
                )
        return errors

    def key(self) -> _PlaneKey:
        """What makes this plane the same matrix as another: showing one
        property AND routing it the same way. Two that share a property and
        send different fixed params are two different controls: an AVoIP
        decoder that switches every stream with one command and tells them
        apart with ``stream`` / ``signal`` also accepts a combined value on
        that parameter, so "All streams" and "Video" both report
        ``source_video`` and route nothing alike. Keying on the property
        alone refused the combined plane, which is the one an integrator wants
        first: a video-only matrix on that gear leaves audio, USB and serial
        on the previous source with nothing to say so."""
        fixed = self.raw.get("params")
        return (
            self.dest_type or "",
            self.route_property,
            str(self.plane.get("command") or ""),
            tuple(sorted(
                (str(k), str(v)) for k, v in fixed.items()
            )) if isinstance(fixed, dict) else (),
        )


def _duplicate_errors(checks: _PlaneChecks, seen: dict[_PlaneKey, int]) -> list[str]:
    """A plane with a routed property that an earlier plane already shows
    the same way is refused; the first one to claim the key keeps it."""
    if not checks.route_property:
        return []
    key = checks.key()
    if key not in seen:
        seen[key] = checks.index
        return []
    return [
        f"{checks.where}: already declared by routing.planes[{seen[key]}] "
        f"; two planes watching one property and routing it the same "
        f"way are the same matrix twice"
    ]
