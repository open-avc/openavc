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
  simulator's own merge (``_merge_inline_protocol`` in
  ``simulator/yaml_auto.py``) derives from the device's rules only.
* **A device that authors none of the four shares the class definition**,
  untouched. Otherwise the instance gets its own ``_definition`` and its own
  ``DRIVER_INFO`` shadow, because the class attribute is shared by every
  device of the driver: the device page, the macro command picker and the
  state seeding read the live instance, so a per-device protocol must never
  be written to the class. The state variables are seeded again so the
  device's own appear on the device card before the first reply.
* **The device's commands are framed once, here**, with the prefix and the
  suffix, else the line ending (``send_framing``'s ``_command_prefix`` and
  ``_inline_suffix``, so ``_init_send_framing`` runs first), and their names
  are kept (``_inline_command_names``, which ``send_framing`` reads) so a
  send does not frame them again. Their ``{placeholder}`` tokens become
  declared params so the Send Command card prompts for them; a token naming
  a config field is left to the config.
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

from openavc.drivers.inline_protocol import (
    declare_command_params,
    derive_state_vars_from_responses,
    normalize_config_commands,
    normalize_config_responses,
    normalize_config_state_vars,
    normalize_ir_codes,
)
from openavc.drivers.yaml_driver_info import build_commands_meta


def _inline_poll_queries(commands: dict[str, Any]) -> list[str]:
    """The poll lines a device's own commands add: those the editor flagged
    "poll" are sent on the device's poll_interval to keep status values live
    (most AV gear doesn't push). A byte-stream command's query is its send
    string (line ending included); an HTTP or OSC command has no send string,
    so its name is used (poll() looks the name up)."""
    return [
        cmd["send"] if isinstance(cmd.get("send"), str) else name
        for name, cmd in commands.items()
        if cmd.get("poll")
    ]


class InlineMerge:
    """The inline-protocol merge, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver / ConfigurableDriver and its other mixins;
    # declared here for the methods.
    config: dict[str, Any]
    DRIVER_INFO: dict[str, Any]
    _definition: dict[str, Any]
    _command_prefix: str
    _inline_suffix: Callable[[], str]
    _init_state_variables: Callable[[], None]

    def _merge_config_protocol(self) -> None:
        """Merge the device-authored ``commands`` / ``responses`` /
        ``state_variables`` / ``ir_codes`` over the file definition, into a
        per-instance ``_definition`` and ``DRIVER_INFO``; a no-op when the
        device authors none of them."""
        norm_commands = normalize_config_commands(
            self.config.get("commands"),
            self._inline_suffix(),
            prefix=self._command_prefix,
        )
        # File commands (not in this set) are the ones send_command frames.
        self._inline_command_names: set[str] = set(norm_commands)
        norm_responses = normalize_config_responses(self.config.get("responses"))
        norm_state_vars = normalize_config_state_vars(
            self.config.get("state_variables")
        )
        ir_commands = normalize_ir_codes(self.config.get("ir_codes"))

        if not any((norm_commands, norm_responses, norm_state_vars, ir_commands)):
            return

        # config_keys are excluded: a {host} token resolves from config, not
        # a prompt.
        declare_command_params(norm_commands, set(self.config.keys()))

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
