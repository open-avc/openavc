"""What the Simulator UI is told about a generated device: the control each of
its state variables gets, and the children section of its card.

The rules worth knowing before changing anything here:

* **The server picks a variable's control; the UI picks a child's.** A flat
  variable arrives as a finished control (slider, toggle, select, power,
  indicator). A child property arrives as its declared shape (``prop_defs``)
  and ``ChildEntitiesPanel.tsx`` in ``web/simulator`` decides how to draw it,
  with its own step for a fader.
* **An invented slider step stays under 500 stops** (``slider_step``); a step
  the driver declares is used as written.
* **A child's name**: its project label, else the type's ``instances.label``
  with ``{id}`` filled in, else the type's label and the id. A project label is
  keyed by the padded id the platform uses; a run of digits is read as a
  number to find the roster's unpadded one.
"""

from __future__ import annotations

from typing import Any, Callable

#: A slider never gets more stops than this from a step the simulator invents.
MAX_STOPS = 500


def variable_control(key: str, var_def: dict, var_type: str, label: str) -> dict:
    """The control a state variable of ``var_type`` gets on the device card."""
    if var_type in ("integer", "number"):
        return _slider_or_indicator(key, var_def, label, integral=var_type == "integer")
    if var_type == "boolean":
        return {"type": "toggle", "key": key, "label": label}
    if var_type == "enum":
        return _enum_control(key, var_def, label)
    return _indicator(key, label)


def _indicator(key: str, label: str) -> dict:
    return {"type": "indicator", "key": key, "label": label}


def _slider_or_indicator(key: str, var_def: dict, label: str, *, integral: bool) -> dict:
    """A slider over a declared range; without both bounds, an indicator."""
    v_min = var_def.get("min")
    v_max = var_def.get("max")
    if v_min is None or v_max is None:
        return _indicator(key, label)
    ctl = {"type": "slider", "key": key, "label": label,
           "min": v_min, "max": v_max,
           "step": slider_step(v_min, v_max, var_def.get("step"), integral=integral)}
    if var_def.get("unit"):
        ctl["unit"] = var_def["unit"]
    return ctl


def _enum_control(key: str, var_def: dict, label: str) -> dict:
    """``power`` is the card's power switch; any other enum with values a
    select, and one with none an indicator."""
    values = var_def.get("values", [])
    if key == "power":
        return {"type": "power", "key": key}
    if values:
        return {"type": "select", "key": key, "label": label, "options": values}
    return _indicator(key, label)


def slider_step(v_min: float, v_max: float, declared, integral: bool):
    """The step for an auto-generated slider, when the driver names none.

    A declared step is the author's call and is returned untouched. What is
    left is the step WE invent, and inventing 0.1 for every number was
    wrong in a way that only shows up on a real driver: an amplifier
    reporting AC line voltage over 0-300 V got 3000 stops, and its power
    draw over 0-5000 W got 50,000. A range input fires a change per pixel
    of travel, so those became a control that could not be placed and a
    flood of writes on the way.

    So pick the finest step off a 1/2/5 ladder that keeps the slider under
    MAX_STOPS. 0-300 V lands on 1 V, 0-5000 W on 10 W, -80-0 dBFS on 0.5 dB
    -- each the resolution a person would have chosen. Integers never go
    below 1. Exactness is not lost: the readout beside the slider is
    typeable, which is where an exact 440 Hz comes from.
    """
    if isinstance(declared, (int, float)) and not isinstance(declared, bool):
        return declared
    span = float(v_max) - float(v_min)
    if span <= 0:
        return 1 if integral else 0.1
    ladder = [1, 2, 5]
    exp = -2 if not integral else 0
    while exp <= 9:
        for mult in ladder:
            step = mult * (10 ** exp)
            if step < 1 and integral:
                continue
            if span / step <= MAX_STOPS:
                # Keep integer ranges on integer steps, and trim binary
                # float dust (3 * 0.1 is not 0.3) so the value that reaches
                # the UI is the one written here.
                return int(step) if integral or step >= 1 else round(step, 10)
        exp += 1
    return int(span) or 1


def children_info(
    roster: dict[str, list[str]],
    child_types: dict[str, dict],
    child_vars: Callable[[str], dict[str, dict]],
    project_children: dict[str, dict[str, dict]],
) -> dict[str, Any]:
    """The card's children section: each child type in the roster with its
    label, its children by name, and its properties' declared shapes."""
    children: dict[str, Any] = {}
    for ctype, ids in roster.items():
        tdef = child_types.get(ctype) or {}
        cvars = child_vars(ctype)
        inst = tdef.get("instances") if isinstance(tdef.get("instances"), dict) else {}
        label_template = inst.get("label") if isinstance(inst.get("label"), str) else None
        type_label = tdef.get("label") or ctype.replace("_", " ").title()
        project_labels = _project_labels(project_children.get(ctype) or {})
        entries = []
        for cid in ids:
            label = project_labels.get(cid)
            if not label and label_template:
                label = label_template.replace("{id}", cid)
            entries.append({"id": cid, "label": label or f"{type_label} {cid}"})
        children[ctype] = {
            "label": type_label,
            "entries": entries,
            "props": list(cvars.keys()),
            # The declared shape of each property, so the UI can draw a
            # mute as a switch instead of a box you type "true" into.
            # `props` stays as it is -- it is the key list the device panel
            # uses to tell child state from device state.
            "prop_defs": {name: prop_def(cdef) for name, cdef in cvars.items()},
        }
    return children


def _project_labels(entries: dict[str, Any]) -> dict[str, str]:
    """The project's labels for one child type, keyed by the roster's id."""
    labels: dict[str, str] = {}
    for pid, meta in entries.items():
        label = meta.get("label") if isinstance(meta, dict) else None
        if not label:
            continue
        pid_norm = str(pid).strip()
        if pid_norm.isdigit():
            pid_norm = str(int(pid_norm))
        labels[pid_norm] = str(label)
    return labels


def prop_def(cdef: dict | None) -> dict:
    """A child property's declared shape, the fields it leaves out omitted."""
    return {
        k: v for k, v in {
            "type": (cdef or {}).get("type", "string"),
            "label": (cdef or {}).get("label"),
            "min": (cdef or {}).get("min"),
            "max": (cdef or {}).get("max"),
            "step": (cdef or {}).get("step"),
            "unit": (cdef or {}).get("unit"),
            "values": (cdef or {}).get("values"),
        }.items() if v is not None
    }
