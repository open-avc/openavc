"""What a device says about its own routing, read back as a matrix proposal.

A matrix element is two lists (``matrix_model``), and every field in them is
already sitting in the driver: the destination child type is the one carrying a
routed-source property, that property IS the route key, the route command
names which parameter takes which end. Typing it all again by hand is how an
8x8 shipped as a dead 4x4.

So this reads a driver's declaration and proposes the lists. It does not apply
them (matrix plan **D3**): a proposal is a guess with a sentence attached, the
author ticks and renames and presses Apply, and what lands in the project is an
explicit list (**D5**). Two things make that the only honest arrangement:

* **The corpus does not agree with itself.** The routed-source property is
  ``input`` on six drivers and ``source`` on two. Destinations are called
  output, decoder, rx, window, zone -- and, on the Atlona DSPs, ``input``, so a
  rule keyed on the name gets those exactly backwards. Route parameters are
  ``child_id``-typed on four drivers and plain integers on four others.
* **A structural test has false positives.** ``turtle_bt_wallplate.eq_copy(source,
  dest)`` passes every test below and copies EQ settings between channels.

Read from the LIVE driver, never from the file. ``atlona_ome_ms``,
``biamp_tesira_ttp`` and ``symetrix_composer`` build their ``DRIVER_INFO``
programmatically, so a file-reading tool sees an empty driver; a running
instance also knows which ports actually registered, which is the difference
between "a 16x16 frame" and "the eight outputs this unit has".

One proposal per routing PLANE, because a plane is a property rather than a
device: a Chazy decoder carries six (video, audio, IR, RS-232, USB, CEC) and an
AVPro AC-MX carries two (video, and the extracted-audio matrix). Each is a
separate matrix element, and the picker asks which one you meant.

Pure stdlib and free of platform imports, so it can be handed a plain
``DRIVER_INFO`` dict in a test without a running engine behind it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

#: A state variable on a child that holds "what is routed here".
#:
#: Three shapes, and the third is what makes multi-plane gear work: ``source``
#: and ``input`` name the only plane a one-plane device has, ``source_video`` /
#: ``source_usb`` name one plane of several, and ``audio_input`` is the same
#: idea spelled the other way round (the AC-MX's extracted-audio matrix).
_ROUTED_PROPERTY = re.compile(
    r"^(routed_)?(input|source|src)$"
    r"|^(source|src|input)_[a-z0-9_]+$"
    r"|^[a-z0-9_]+_(source|src|input)$",
    re.I,
)

#: Property names that match the pattern above and are never a routed source.
#: Each is in the shipped corpus; each would otherwise propose a matrix whose
#: crosspoints mean nothing.
_NOT_ROUTED = frozenset({
    # A count of inputs, not one of them.
    "num_inputs", "input_count", "n_inputs",
    # Presence/format read-outs that happen to end in _input.
    "signal_input", "sync_input", "active_input_format",
    # The source's own name, not the source.
    "source_name", "input_name", "source_label", "input_label",
})

#: A command parameter that names the DESTINATION of a route. The trailing
#: ``_id`` is not decoration: the Chazy and Darwin drivers spell both ends
#: ``decoder_id`` / ``encoder_id``, and without it their route command reads as
#: taking neither end of a route.
_DESTINATION_PARAM = re.compile(
    r"^(out|output|outputs|dest|destination|dst|zone|bus|mix|channel|window|"
    r"decoder|rx|display|sink)(_id|_no|_num|_index)?$",
    re.I,
)

#: A command parameter that names the SOURCE of a route. The optional prefix is
#: what reaches ``ip_input`` on the Atlona OmniStream and ``audio_source``
#: elsewhere; without it those commands look like they take no source at all.
_SOURCE_PARAM = re.compile(
    r"^([a-z0-9]+_)?(in|input|inputs|source|sources|src|encoder|tx)(_id|_no|_num|_index)?$",
    re.I,
)

#: Words that name a routing PLANE rather than a device. A command carrying one
#: the property does not is almost always the neighbouring plane's command --
#: an AC-MX has ``route`` and ``audio_route``, and picking the wrong one sends
#: video where the author asked for extracted audio.
_PLANE_WORDS = frozenset({
    "audio", "video", "usb", "ir", "infrared", "serial", "rs232", "cec",
    "analog", "hdmi", "hdbt", "deembed", "mic", "aux", "stereo", "sub",
    "edid", "eq", "preset", "mirror", "arc",
    # Deliberately NOT "st": it abbreviates "stereo" on one shipped driver, and
    # two letters collide with too much to stand for a plane on their own.
})

#: Command-name words that say "this one switches something". Weak on their own
#: -- ``copy_output_edid`` takes two child ids and copies an EDID -- which is
#: why they are one term in a score rather than a test.
_ROUTING_WORDS = frozenset({
    "route", "switch", "select", "assign", "patch", "tie", "xpt", "crosspoint",
    "source", "input",
})

#: Property-name words that say nothing about which plane it is.
_PLANE_NOISE = frozenset({"source", "src", "input", "routed", "active", "connected"})

#: A child type whose members are plausible sources when nothing else says so.
_SOURCE_CHILD_TYPE = re.compile(r"^(in|input|inputs|source|sources|encoder|tx)$", re.I)

#: The state variable a driver uses for a port's device-reported name. The
#: platform injects ``label`` on every child (that is the PROJECT's name for it,
#: which the author is about to edit here), so only a driver-declared name is
#: worth binding a live label to.
_NAME_PROPERTIES = ("name", "port_name", "display_name")

#: How a plane's property name reads in a sentence. Anything not listed keeps
#: the driver's own label, which is the point of D4: no driver is told what to
#: call anything.
_PLANE_WORD = re.compile(r"^(source|src|input)_(.+)$|^(.+)_(source|src|input)$", re.I)


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _enum_values(param: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A command parameter's declared options, as ``{value, label}`` pairs.

    An entry is a bare wire value or a ``{value, label}`` pair (driver spec), so
    both spellings land here the same way.
    """
    raw = param.get("values")
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, Mapping):
            if "value" not in entry:
                continue
            out.append({
                "value": entry["value"],
                "label": str(entry.get("label") or entry["value"]),
            })
        elif isinstance(entry, str | int | float) and not isinstance(entry, bool):
            out.append({"value": entry, "label": str(entry)})
    return out


def _plane_label(prop: str, var_def: Mapping[str, Any]) -> str:
    """What to call this routing plane, in the driver's own words where it has any."""
    declared = str(var_def.get("label") or "").strip()
    if declared:
        return declared
    word = _plane_token(prop)
    if word:
        return word.replace("_", " ").strip().title()
    return prop.replace("_", " ").title()


def _plane_token(prop: str) -> str:
    """The one word that says WHICH plane, or "" when the property names the only one.

    ``source_video`` -> ``video``, ``audio_input`` -> ``audio``, ``input`` -> ``""``.
    """
    match = _PLANE_WORD.match(prop)
    if not match:
        return ""
    return (match.group(2) or match.group(3) or "").lower()


def _plane_params(
    params: Mapping[str, Any], taken: Sequence[str], plane: str,
) -> tuple[dict[str, Any], list[str]]:
    """The route command's REMAINING parameters, filled in where the plane says so.

    A Chazy decoder's six planes are one command with a ``signal`` parameter
    (ALL/VIDEO/AUDIO/IR/RS232/USB/CEC), and an AVPro MXNet's five are one
    command with a ``stream`` parameter. Without this, every plane of those
    devices would produce the same matrix and route video six times.

    Returns the parameters to add to the action, and the names of any that are
    required and could not be filled -- which the author has to settle, so the
    proposal says so rather than shipping a command that will be refused.
    """
    filled: dict[str, Any] = {}
    unfilled: list[str] = []
    for name, raw in params.items():
        if name in taken:
            continue
        spec = _as_mapping(raw)
        match = next(
            (
                option for option in _enum_values(spec)
                if plane and plane in (str(option["value"]).lower(), option["label"].lower())
            ),
            None,
        )
        if match is not None:
            filled[name] = match["value"]
        elif spec.get("required") and "default" not in spec:
            unfilled.append(name)
    return filled, unfilled


#: Which plane is offered first when a device routes several.
#:
#: A property with no plane word in it is the device's MAIN route -- a frame
#: says ``input`` for video and ``audio_input`` for its extracted-audio matrix,
#: and offering the ex-audio one first (which alphabetical order does) puts the
#: rarely-wanted plane in front of the one everybody came for.
_PLANE_ORDER = ("", "video", "audio")


def _plane_rank(prop: str) -> int:
    plane = _plane_token(prop)
    return _PLANE_ORDER.index(plane) if plane in _PLANE_ORDER else len(_PLANE_ORDER)


def _child_name_property(schema: Mapping[str, Any]) -> str | None:
    """The child property holding the device's own name for this port, if any."""
    for candidate in _NAME_PROPERTIES:
        if candidate in schema:
            return candidate
    return None


def _generated_ids(id_format: Mapping[str, Any]) -> list[int]:
    """The ids a child type COULD have, for a driver whose ports never registered.

    Only integer children can be guessed at: a string-keyed child type is named
    by the device, and inventing names would be worse than an empty list the
    author fills in.
    """
    if str(id_format.get("type") or "integer") != "integer":
        return []
    try:
        low = int(id_format.get("min", 1))
        high = int(id_format.get("max", 0))
    except (TypeError, ValueError):
        return []
    if high < low or high - low > 255:
        return []
    return list(range(low, high + 1))


class _Children:
    """The registered children of a device, as the picker needs to read them.

    A live driver knows which ports exist; a driver that is not running does
    not. Both cases arrive here, and the difference is visible to the caller
    (``registered``) so a proposal can say which one it read.
    """

    def __init__(self, rows: Mapping[str, Sequence[Mapping[str, Any]]] | None) -> None:
        self._rows = {
            str(ctype): [dict(r) for r in entries if isinstance(r, Mapping)]
            for ctype, entries in _as_mapping(rows).items()
            if isinstance(entries, Sequence) and not isinstance(entries, str | bytes)
        }

    def registered(self, ctype: str) -> bool:
        return bool(self._rows.get(ctype))

    def entries(
        self, ctype: str, type_schema: Mapping[str, Any],
    ) -> list[tuple[Any, str, str]]:
        """``(local_id, padded_id, label)`` for each child of this type.

        Falls back to the declared id range when nothing has registered, so a
        matrix can be built against a device that is powered off -- which is
        most of them, at the point somebody is drawing the panel.
        """
        rows = self._rows.get(ctype)
        if rows:
            out = []
            for row in rows:
                local = row.get("local_id")
                padded = str(row.get("local_id_padded") or local)
                out.append((local, padded, str(row.get("label") or "")))
            return out
        pad = _as_mapping(type_schema.get("id_format")).get("pad_width")
        width = int(pad) if isinstance(pad, int) and pad > 0 else 0
        return [
            (i, str(i).zfill(width) if width else str(i), "")
            for i in _generated_ids(_as_mapping(type_schema.get("id_format")))
        ]


def _entry_label(explicit: str, type_schema: Mapping[str, Any], ctype: str, n: int) -> str:
    """One port's caption: what the project calls it, else what its type is called."""
    if explicit:
        return explicit
    word = str(type_schema.get("label") or ctype).strip() or ctype
    return f"{word} {n}"


def _words(text: str) -> set[str]:
    """A name split into the words a score can compare."""
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if w}


class _Candidate:
    """One command that could be this plane's route, and how well it fits."""

    __slots__ = ("name", "dest_param", "source_param", "source_def", "score", "typed")

    def __init__(
        self, name: str, dest_param: str, source_param: str,
        source_def: dict[str, Any], score: int, typed: bool,
    ) -> None:
        self.name = name
        self.dest_param = dest_param
        self.source_param = source_param
        self.source_def = source_def
        self.score = score
        self.typed = typed


def _plane_context(
    route_property: str, dest_ctype: str, schema: Mapping[str, Any],
) -> set[str]:
    """Every word that legitimately names THIS plane.

    The property is not always the one carrying it: a frame's extracted-audio
    matrix says ``audio_input`` on an ``output``, but an audio processor's
    analog outs say plain ``source`` on a child type called "Analog Audio Outs".
    Both are the audio plane, and a command called ``set_audio_source`` belongs
    to both, so the child type's own names count too.
    """
    return (
        _words(route_property)
        | _words(dest_ctype)
        | _words(str(schema.get("label") or ""))
        | _words(str(schema.get("label_plural") or ""))
    ) - _PLANE_NOISE


def _score_command(
    name: str, dest_param_def: Mapping[str, Any], source_param_def: Mapping[str, Any],
    dest_ctype: str, route_property: str, context: set[str],
) -> tuple[bool, int, bool]:
    """How well one command fits one routing plane: ``(right plane, points, typed)``.

    Compared in that order, and the order is the whole point. A driver with two
    planes has two nearly identical commands (``route`` and ``audio_route``) and
    a driver with one plane has a dozen that take the same two child ids
    (``copy_output_edid``, ``set_ir_code``). Picking the first that structurally
    matches gets both wrong -- and gets them wrong SILENTLY, by routing video
    where the author asked for extracted audio.

    * **Right plane** -- the command carries no plane word this plane does not.
      Judged against the wide context (the child type's names included), so a
      frame's analog audio outs accept ``set_audio_source``. Not a hard filter:
      a driver that spells a plane in a word nothing else uses still gets a
      proposal, it just sorts below anything cleaner.
    * **Points** -- a word the ROUTED PROPERTY also has is the strongest name
      signal there is (``deembed_source`` -> ``set_deembed_source``), a routing
      verb is a weak one, and a ``child_id`` naming this exact child type is a
      declaration rather than a guess. Scored against the property alone rather
      than the wide context, or a child type called "Audio Outputs" hands every
      one of its planes to whichever command happens to say audio.
    """
    typed = (
        dest_param_def.get("type") == "child_id"
        and str(dest_param_def.get("child_type") or "") == dest_ctype
    )
    name_words = _words(name)

    score = 4 if typed else 0
    score += 3 * len((_words(route_property) - _PLANE_NOISE) & name_words)
    score += 2 if name_words & _ROUTING_WORDS else 0
    score += 2 if source_param_def.get("type") == "child_id" else 0
    return not ((name_words & _PLANE_WORDS) - context), score, typed


def _find_route_command(
    commands: Mapping[str, Any], dest_ctype: str, route_property: str, context: set[str],
) -> _Candidate | None:
    """The command that routes this plane, and which parameter is which end.

    A candidate must name both ends: something that reads as the destination
    (a ``child_id`` of this child type, or a parameter called output/zone/...)
    and something that reads as a source. Everything else about the choice is
    the score above.

    Anchoring on the child type carrying the routed-source property, rather than
    on a command's parameter names, is also what keeps
    ``turtle_bt_wallplate.eq_copy(source, dest)`` out: it passes every
    parameter-shaped test and copies EQ settings, but no child on that driver
    reports what is routed to it, so no plane ever asks about its commands.
    """
    best: _Candidate | None = None
    best_rank: tuple[bool, int, bool] | None = None
    for raw_name, raw in commands.items():
        name = str(raw_name)
        params = _as_mapping(_as_mapping(raw).get("params"))
        if len(params) < 2:
            continue

        # A parameter typed to a DIFFERENT child type is that type's port, not
        # this one's, however much its name reads like a destination. Without
        # this, a frame's `route(output, input)` was offered as the route for
        # its analog audio outs -- a different child type, a different command.
        def _other_childs(spec: Any) -> bool:
            child_type = str(_as_mapping(spec).get("child_type") or "")
            return _as_mapping(spec).get("type") == "child_id" and child_type != dest_ctype

        dest_params = [
            p for p, d in params.items()
            if not _other_childs(d)
            and (
                (
                    _as_mapping(d).get("type") == "child_id"
                    and str(_as_mapping(d).get("child_type") or "") == dest_ctype
                )
                or _DESTINATION_PARAM.match(p)
            )
        ]
        if not dest_params:
            continue
        # A child_id naming this type beats a parameter that merely reads like a
        # destination: one is a declaration, the other is a guess.
        dest_params.sort(
            key=lambda p: _as_mapping(params[p]).get("type") != "child_id",
        )
        dest_param = dest_params[0]

        source_param = next(
            (p for p in params if p != dest_param and _SOURCE_PARAM.match(p)), None,
        )
        if source_param is None:
            continue

        rank = _score_command(
            name, _as_mapping(params[dest_param]), _as_mapping(params[source_param]),
            dest_ctype, route_property, context,
        )
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best = _Candidate(
                name, dest_param, source_param,
                _as_mapping(params[source_param]), rank[1], rank[2],
            )
    return best


def _sources_from(
    device_id: str,
    source_param_def: Mapping[str, Any],
    source_ctype: str | None,
    child_types: Mapping[str, Any],
    children: _Children,
    route_var: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    """The list of things this matrix can route FROM, and where it was read.

    Four places, most specific first. The route command's own parameter is the
    most trustworthy -- it is literally the set of tokens the device accepts --
    and the declared range of the routed-source property is the loosest, because
    a range says how many there could be and nothing about which ones are patched.
    """
    enum = _enum_values(source_param_def)
    if enum:
        return (
            [{"value": e["value"], "label": e["label"]} for e in enum],
            f"the values the route command's '{source_param_def.get('label') or 'source'}' "
            f"parameter accepts",
        )

    if source_ctype and source_ctype in child_types:
        schema = _as_mapping(child_types[source_ctype])
        name_prop = _child_name_property(_as_mapping(schema.get("state_variables")))
        entries = []
        for position, (local, padded, label) in enumerate(children.entries(source_ctype, schema)):
            entry: dict[str, Any] = {
                "value": local,
                "label": _entry_label(label, schema, source_ctype, position + 1),
            }
            if name_prop:
                entry["label_key"] = f"device.{device_id}.{source_ctype}.{padded}.{name_prop}"
            entries.append(entry)
        if entries:
            word = "registered" if children.registered(source_ctype) else "declared"
            return entries, f"the {word} '{source_ctype}' children"

    for declared, where in (
        (source_param_def, "the route command's source parameter"),
        (route_var, "the routed-source property"),
    ):
        try:
            low = int(declared["min"])
            high = int(declared["max"])
        except (KeyError, TypeError, ValueError):
            continue
        if low <= high and high - low <= 255:
            return (
                [{"value": i, "label": f"Input {i}"} for i in range(low, high + 1)],
                f"the {low}..{high} range {where} declares",
            )

    return [], "nothing -- the driver does not say what this can be routed from"


def _proposal_warnings(
    sources: Sequence[Mapping[str, Any]],
    destinations: Sequence[Mapping[str, Any]],
    command: str | None,
    dest_ctype: str,
    route_var: Mapping[str, Any],
    unfilled_params: Sequence[str],
    from_roster: bool,
) -> list[str]:
    """What this proposal cannot know, said before it is applied rather than after.

    Every one of these is a real state a shipped driver reaches. They are
    warnings and not refusals because the author is the one who can settle them,
    and the picker is where they are standing.
    """
    warnings: list[str] = []
    if not destinations:
        warnings.append(
            "This device has not told the system which ports it has, so there is "
            "nothing to route to yet. Connect it and press Re-read device, or add "
            "the destinations by hand.",
        )
    elif not from_roster:
        # The difference between "a 16x16 frame" and "the four outputs this unit
        # has". A driver that is not connected registers no children, so the
        # list here is the widest thing the driver can be -- which draws a
        # perfectly convincing 16x16 for a 4x4 that is sitting on the bench.
        warnings.append(
            f"These are the {len(destinations)} ports this driver can have, not the "
            f"ones this device reports -- it has not registered any. Connect it and "
            f"press Re-read device, or untick the ones that are not there.",
        )
    if not sources:
        warnings.append(
            "The driver does not say what this can be routed from, so the sources "
            "list is empty. Add the values the device accepts.",
        )
    if command is None:
        warnings.append(
            "No routing command was found, so tapping a crosspoint will not send "
            "anything until you add a Video route action in the Bindings tab.",
        )
    zero = [str(s.get("label") or s.get("value")) for s in sources if str(s.get("value")) == "0"]
    if zero:
        warnings.append(
            f"{zero[0]} has the value 0, which a panel reads as 'nothing is routed', "
            f"so its crosspoint can never light. Give it the value the device "
            f"reports when it is selected.",
        )
    reported = route_var.get("values")
    if (
        sources
        and isinstance(reported, Sequence)
        and not isinstance(reported, str | bytes)
        and reported
        and {str(s.get("value")) for s in sources} != {str(v) for v in reported}
    ):
        warnings.append(
            f"The device reports this as one of {', '.join(str(v) for v in reported)}, "
            f"which is not the same vocabulary the route command takes -- so the "
            f"crosspoints may not light for a route that worked. Check the values "
            f"against the driver before relying on the feedback.",
        )
    if unfilled_params:
        warnings.append(
            f"The routing command also needs "
            f"{', '.join(repr(p) for p in unfilled_params)}, and nothing here says "
            f"what to send. Fill that in on the Video route action in the Bindings "
            f"tab, or the command will be refused.",
        )
    if _SOURCE_CHILD_TYPE.match(dest_ctype):
        warnings.append(
            f"The destinations here are children called '{dest_ctype}', which usually "
            f"names a source. That is correct on some audio processors and wrong on a "
            f"video switcher -- check this is the end you meant to route TO.",
        )
    return warnings


def propose_matrices(
    device_id: str,
    driver_info: Mapping[str, Any] | None,
    children: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Every routing plane this device declares, as a matrix somebody could apply.

    Ordered most-confident first. An empty list means the driver declares nothing
    that looks like routing, which is a real answer and not a failure -- most
    drivers are not switchers.
    """
    info = _as_mapping(driver_info)
    child_types = _as_mapping(info.get("child_entity_types"))
    commands = _as_mapping(info.get("commands"))
    roster = _Children(children)

    proposals: list[dict[str, Any]] = []
    for dest_ctype, raw_schema in child_types.items():
        schema = _as_mapping(raw_schema)
        state_vars = _as_mapping(schema.get("state_variables"))
        name_prop = _child_name_property(state_vars)
        ports = roster.entries(dest_ctype, schema)

        for prop, raw_var in state_vars.items():
            if prop.lower() in _NOT_ROUTED or not _ROUTED_PROPERTY.match(prop):
                continue
            route_var = _as_mapping(raw_var)

            source_ctype = next(
                (c for c in child_types if c != dest_ctype and _SOURCE_CHILD_TYPE.match(c)),
                None,
            )
            context = _plane_context(prop, dest_ctype, schema)
            found = _find_route_command(commands, dest_ctype, prop, context)
            command = found.name if found else None
            dest_param = found.dest_param if found else ""
            source_param = found.source_param if found else ""
            source_param_def = found.source_def if found else {}
            if source_param_def.get("type") == "child_id":
                source_ctype = str(source_param_def.get("child_type") or "") or source_ctype

            sources, source_origin = _sources_from(
                device_id, source_param_def, source_ctype, child_types, roster, route_var,
            )

            destinations = []
            for position, (local, padded, label) in enumerate(ports):
                entry: dict[str, Any] = {
                    "value": local,
                    "label": _entry_label(label, schema, dest_ctype, position + 1),
                    "route_key": f"device.{device_id}.{dest_ctype}.{padded}.{prop}",
                }
                if name_prop:
                    entry["label_key"] = (
                        f"device.{device_id}.{dest_ctype}.{padded}.{name_prop}"
                    )
                destinations.append(entry)

            plane_params: dict[str, Any] = {}
            unfilled: list[str] = []
            if command:
                plane_params, unfilled = _plane_params(
                    _as_mapping(_as_mapping(commands.get(command)).get("params")),
                    (dest_param, source_param),
                    _plane_token(prop),
                )
            route = (
                [{
                    "action": "device.command",
                    "device": device_id,
                    "command": command,
                    "params": {
                        dest_param: "$output",
                        source_param: "$input",
                        **plane_params,
                    },
                }]
                if command
                else None
            )

            plural = str(schema.get("label_plural") or schema.get("label") or dest_ctype)
            plane = _plane_label(prop, route_var)
            typed = bool(found) and found.typed
            proposals.append({
                "id": f"{dest_ctype}.{prop}",
                "device_id": device_id,
                "label": f"{plural} -- {plane}",
                "destination_child_type": dest_ctype,
                "route_property": prop,
                "source_child_type": source_ctype,
                "command": command,
                "confidence": "high" if typed else "medium" if command else "low",
                "why": (
                    f"Each '{dest_ctype}' reports its routed source in '{prop}', and "
                    + (
                        f"'{command}' takes the destination as '{dest_param}' and the "
                        f"source as '{source_param}'."
                        if command
                        else "no command on this driver routes one to another."
                    )
                    + f" Sources come from {source_origin}."
                ),
                "from_roster": roster.registered(dest_ctype),
                "warnings": _proposal_warnings(
                    sources, destinations, command, dest_ctype, route_var, unfilled,
                    roster.registered(dest_ctype),
                ),
                "sources": sources,
                "destinations": destinations,
                "route": route,
            })

    order = {"high": 0, "medium": 1, "low": 2}
    proposals.sort(
        key=lambda p: (order[p["confidence"]], _plane_rank(p["route_property"]), p["id"]),
    )
    return proposals
