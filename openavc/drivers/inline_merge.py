"""A device's own protocol over its YAML driver's file: the commands,
replies, state variables and IR codes the device authored in its project
config, merged into a per-instance definition and ``DRIVER_INFO``.

The rules worth knowing before changing anything here:

* **Config extends and overrides the file.** Commands merge by name, the
  device's winning over the file's and an IR code over a command of the
  same name; replies go after the file's, so a file driver's own rules
  still match first; poll lines the device flagged go after the file's
  queries.
* **State variables layer three ways**: the file's, then those derived from
  the reply rules, then the device's explicit ones. The derived set is taken
  from the file's rules and the device's together, so it replaces a file
  variable a file rule writes, its label and values with it; the
  simulator's own merge (``merge_inline_protocol`` in
  ``simulator/inline_device.py``) derives from the device's rules only.
* **A device whose config holds none of the four shares the class
  definition**, untouched. A driver's shipped ``default_config.ir_codes``
  are layered into every device's config, so each device of such a driver
  gets its own. Otherwise the instance gets its own ``_definition`` and its own
  ``DRIVER_INFO`` shadow, because the class attribute is shared by every
  device of the driver: the device page, the macro command picker and the
  state seeding read the live instance, so a per-device protocol must never
  be written to the class. The state variables are seeded again so the
  device's own appear on the device card before the first reply.
* **The device's commands are framed once, here**, as ``send_framing``
  frames a command the device authored (``framed_commands``), and their
  names are kept (``_inline_command_names``, which ``send_framing`` reads)
  so a send does not frame them again. Their
  ``{placeholder}`` tokens become declared params so the Send Command card
  prompts for them; a token naming a config field is left to the config.
* **What a device declares is worked out by one function** (``own_commands``),
  which the running driver's merge calls and ``declared_commands`` calls for
  a project's device with no driver running, so the save and the send judge
  a command on the same declaration (``drivers/secret_params``).
* **The IR codes** are the driver's shipped ``default_config.ir_codes``
  overlaid with the device's own (already layered into the config); each
  becomes a command sent through the bound bridge (``side_sends``).

``InlineMerge`` is a mixin ``ConfigurableDriver`` inherits, so every name
stays on the driver object; ``__init__`` calls ``_merge_config_protocol``
once, after the send framing is set up and before the derived config and
the reply rules' compile.
The parsers are ``inline_protocol``'s, shared with the simulator.
"""

from __future__ import annotations

from typing import Any, Callable

from openavc.drivers.command_form import command_form
from openavc.drivers.inline_protocol import (
    declare_command_params,
    derive_state_vars_from_responses,
    normalize_config_commands,
    normalize_config_responses,
    normalize_config_state_vars,
    normalize_ir_codes,
)
from openavc.drivers.send_framing import framed_commands
from openavc.drivers.yaml_driver_info import build_commands_meta


def own_commands(
    definition: dict[str, Any], config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The commands a device's ``config`` authors over its driver: its own
    commands, framed once (``send_framing.framed_commands``) and given a
    param for each ``{placeholder}`` that names no config field, and its IR
    codes. Both empty for a device that authors none."""
    commands = framed_commands(
        definition, config, normalize_config_commands(config.get("commands")),
    )
    # config_keys are excluded: a {host} token resolves from config, not
    # a prompt.
    declare_command_params(commands, set(config.keys()))
    return commands, normalize_ir_codes(config.get("ir_codes"))


def declared_commands(driver_class: Any, config: dict[str, Any]) -> dict[str, Any]:
    """The ``commands`` of ``DRIVER_INFO`` a device of ``driver_class`` with
    ``config`` declares, worked out with no driver running: the class's, with
    a YAML driver's device commands and IR codes merged over them by name, as
    ``_merge_config_protocol`` merges them into the running driver. ``config``
    is the device's settings as its driver is handed them (the driver's
    defaults, the device's config, its connection row), since a placeholder
    naming any of them is not a param. A Python driver's are its class's: it
    builds any others itself once it runs."""
    info = getattr(driver_class, "DRIVER_INFO", None) or {}
    merged = _own_over_file(driver_class, config) if _merges_own_protocol(driver_class) else None
    return merged if merged is not None else (info.get("commands") or {})


def _merges_own_protocol(driver_class: Any) -> bool:
    """Whether a device of ``driver_class`` merges a protocol of its own in:
    a YAML driver's does."""
    return isinstance(driver_class, type) and issubclass(driver_class, InlineMerge)


def _own_over_file(driver_class: Any, config: dict[str, Any]) -> dict[str, Any] | None:
    """The file's commands with the device's own over them, as the running
    driver's ``DRIVER_INFO`` holds them; None when the device authors none."""
    definition = getattr(driver_class, "_definition", None) or {}
    own, ir = own_commands(definition, config)
    if not own and not ir:
        return None
    return build_commands_meta({**(definition.get("commands") or {}), **own, **ir})


def _inline_poll_queries(commands: dict[str, Any]) -> list[str]:
    """The poll lines a device's own commands add: those the editor flagged
    "poll" are sent on the device's poll_interval to keep status values live
    (most AV gear doesn't push). A command sent as a line (``command_form``)
    is polled as its send string (line ending included); one in another form
    is polled by its name, which poll() looks up and runs; one with nothing
    to send (its Send left blank) adds no line."""
    polled = ((name, cmd, command_form(cmd)) for name, cmd in commands.items() if cmd.get("poll"))
    return [
        cmd["send"] if form == "raw" and isinstance(cmd["send"], str) else name
        for name, cmd, form in polled
        if form is not None
    ]


class InlineMerge:
    """The inline-protocol merge, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver / ConfigurableDriver and its other mixins;
    # declared here for the methods.
    config: dict[str, Any]
    DRIVER_INFO: dict[str, Any]
    _definition: dict[str, Any]
    _init_state_variables: Callable[[], None]

    def _merge_config_protocol(self) -> None:
        """Merge the device-authored ``commands`` / ``responses`` /
        ``state_variables`` / ``ir_codes`` over the file definition, into a
        per-instance ``_definition`` and ``DRIVER_INFO``; a no-op when the
        device authors none of them."""
        norm_commands, ir_commands = own_commands(self._definition, self.config)
        # File commands (not in this set) are the ones send_command frames.
        self._inline_command_names: set[str] = set(norm_commands)
        norm_responses = normalize_config_responses(self.config.get("responses"))
        norm_state_vars = normalize_config_state_vars(
            self.config.get("state_variables")
        )

        if not any((norm_commands, norm_responses, norm_state_vars, ir_commands)):
            return

        self._definition = self._merged_definition(
            {**norm_commands, **ir_commands}, norm_responses, norm_state_vars,
            _inline_poll_queries(norm_commands),
        )
        info = dict(self.DRIVER_INFO)
        info["commands"] = build_commands_meta(self._definition["commands"])
        info["state_variables"] = self._definition["state_variables"]
        self.DRIVER_INFO = info

        # super().__init__() already seeded the file vars; seeding again
        # before the first poll is harmless (same defaults).
        self._init_state_variables()

    def _merged_definition(
        self,
        commands: dict[str, Any],
        responses: list[dict[str, Any]],
        state_vars: dict[str, Any],
        poll_queries: list[str],
    ) -> dict[str, Any]:
        """The file definition with the device's own protocol over it: a new
        mapping, the class's left as it was."""
        merged = dict(self._definition)
        merged_responses = list(merged.get("responses") or []) + responses
        if poll_queries:
            file_polling = merged.get("polling") or {}
            merged["polling"] = {
                **file_polling,
                "queries": list(file_polling.get("queries", [])) + poll_queries,
            }
        merged["commands"] = {**(merged.get("commands") or {}), **commands}
        merged["responses"] = merged_responses
        merged["state_variables"] = {
            **(merged.get("state_variables") or {}),
            **derive_state_vars_from_responses(merged_responses),
            **state_vars,
        }
        return merged
