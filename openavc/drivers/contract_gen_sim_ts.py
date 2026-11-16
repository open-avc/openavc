"""Render the driver contract's simulator controls into the Simulator UI's types.

Produces ``openavc/web/simulator/src/store/controls.gen.ts``: one interface per
control type the Simulator UI draws and the ``ControlDef`` union of them, read
off ``spec.SIMULATOR_CONTROLS``, the one place the types are declared. Each
interface is named for its type (``meters`` -> ``MetersControlDef``); a field
the type cannot be drawn without is required, every other one optional (a
label included: the server may send a control without one). ``DynamicControls``
switches on ``type`` and has no case to spare, so a type added to the contract
fails the Simulator UI's build until it is drawn.

Part of ``python -m openavc.drivers.contract_gen``; pure stdlib.
"""

from __future__ import annotations

from typing import Any

from openavc.drivers import spec
from openavc.drivers.contract_gen_ts import BANNER, _doc_block, _mechanical_ts

#: What a ``controls`` list of a control's own is called in TypeScript.
_UNION = "ControlDef"

_HEADER = """\
// The controls a simulated device's page can declare (a simulator: section's
// controls, a Python simulator's SIMULATOR_INFO["controls"]), one interface per
// type. DynamicControls switches on `type` with a case for each.

"""


def interface_name(control_type: str) -> str:
    """The TypeScript interface for one control type."""
    return "".join(part.title() for part in control_type.split("_")) + "ControlDef"


def _member_ts(node: dict[str, Any]) -> str:
    """A field's TypeScript type; a list of controls is a list of the union."""
    if node.get("items") == {"ref": "simulatorControl"}:
        return f"{_UNION}[]"
    return _mechanical_ts(node)


def _interface(control_type: str, declared: dict[str, Any]) -> str:
    required = set(declared["required"])
    lines = [_doc_block(declared["doc"], ""), f"export interface {interface_name(control_type)} {{\n"]
    lines.append(f'  type: "{control_type}";\n')
    for name, node in declared["fields"].items():
        if node.get("doc"):
            lines.append(_doc_block(node["doc"], "  "))
        optional = "" if name in required else "?"
        lines.append(f"  {name}{optional}: {_member_ts(node)};\n")
    lines.append("}\n")
    return "".join(lines)


def render_controls_ts() -> str:
    """The whole ``controls.gen.ts``."""
    interfaces = [_interface(name, declared) for name, declared in spec.SIMULATOR_CONTROLS.items()]
    members = "".join(f"\n  | {interface_name(name)}" for name in spec.SIMULATOR_CONTROLS)
    union = f"export type {_UNION} ={members};\n"
    return BANNER + _HEADER + "\n".join(interfaces) + "\n" + union
