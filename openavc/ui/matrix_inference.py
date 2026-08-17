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

Pure stdlib apart from the child-id leaf (itself pure), so it can be handed a
plain ``DRIVER_INFO`` dict and a plain config dict in a test without a running
engine behind it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from openavc.drivers.child_ids import coerce_child_local_id, declared_child_ids

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
    # Its kind, how it is being selected, or all of them at once -- each is a
    # fact ABOUT the routing rather than a thing that can be routed. Every one
    # of these is in the shipped corpus: a "Priority Mode" of Override/Backup
    # proposed a matrix of two dozen analog inputs, and a list of the window
    # aliases on an output proposed one with no command at all.
    "source_type", "input_type", "source_mode", "input_mode",
    "source_list", "input_list",
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


#: Where a list of ports came from, weakest last. The whole difference between
#: "the eight outputs this unit has" and "the 128 an SIS frame can have".
REGISTERED, DECLARED, RANGE = "registered", "declared", "range"


class _Children:
    """The children of a device, as the picker needs to read them.

    Three answers, in that order of authority. A live driver knows which ports
    actually registered. A driver that is not running still has a DECLARED
    roster whenever its child type sizes itself from a config field somebody has
    filled in -- an SIS frame with Output Count 4 has four outputs whether it is
    plugged in or not, and that is the answer the platform already gives the
    bound-child-id check. Only when neither says anything does the id_format
    range stand in, and a caller can tell which it got (``origin``) because 128
    ports offered as though they were real is exactly the trap this avoids.
    """

    def __init__(
        self,
        rows: Mapping[str, Sequence[Mapping[str, Any]]] | None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self._rows = {
            str(ctype): [dict(r) for r in entries if isinstance(r, Mapping)]
            for ctype, entries in _as_mapping(rows).items()
            if isinstance(entries, Sequence) and not isinstance(entries, str | bytes)
        }
        self._config = _as_mapping(config)

    def registered(self, ctype: str) -> bool:
        return bool(self._rows.get(ctype))

    def declared(self, type_schema: Mapping[str, Any]) -> list[Any] | None:
        """The ids this type declares for THIS device's config, typed, or None.

        None is the honest answer for a roster the device sizes itself
        (``count_from_state``), a config field left empty, and every Python
        driver that registers its children in code.
        """
        type_def = dict(type_schema)
        raw = declared_child_ids(type_def, self._config)
        if raw is None:
            return None
        typed = [coerce_child_local_id(type_def, value) for value in raw]
        return [value for value in typed if value is not None] or None

    def origin(self, ctype: str, type_schema: Mapping[str, Any]) -> str:
        if self.registered(ctype):
            return REGISTERED
        return DECLARED if self.declared(type_schema) else RANGE

    def entries(
        self, ctype: str, type_schema: Mapping[str, Any],
    ) -> list[tuple[Any, str, str, bool | None]]:
        """``(local_id, padded_id, label, online)`` for each child of this type.

        ``online`` is the platform-reserved child property every registered child
        carries, and None means nobody said -- a roster that came from the
        declared count or the id range has not been asked. It matters here
        because a device can list a port it cannot reach: an MXNet CBOX keeps an
        endpoint in its database after it leaves the rack, and offers it here
        looking exactly like the four that are plugged in.

        Falls back to the declared roster and then to the declared id range when
        nothing has registered, so a matrix can be built against a device that is
        powered off -- which is most of them, at the point somebody is drawing
        the panel.
        """
        rows = self._rows.get(ctype)
        if rows:
            out = []
            for row in rows:
                local = row.get("local_id")
                padded = str(row.get("local_id_padded") or local)
                online = row.get("online")
                out.append((
                    local, padded, str(row.get("label") or ""),
                    None if online is None else bool(online),
                ))
            return out
        id_format = _as_mapping(type_schema.get("id_format"))
        pad = id_format.get("pad_width")
        width = int(pad) if isinstance(pad, int) and pad > 0 else 0
        ids = self.declared(type_schema)
        if ids is None:
            ids = _generated_ids(id_format)
        return [
            (i, str(i).zfill(width) if width and isinstance(i, int) else str(i), "", None)
            for i in ids
        ]


def _entry_label(
    explicit: str, type_schema: Mapping[str, Any], ctype: str, n: int, *, live: bool,
) -> str:
    """One port's caption, or "" to leave it to the device.

    ``explicit`` is what this port is already called -- the project's label, else
    the name the device reports. Where there is neither, a caption is invented
    from the type's own label ("Output 3", "Encoder 1"), which beats the
    resolver's "Out 3" because it is the driver's word for the thing.

    Not when the entry carries a live ``label_key`` (``live``). A caption is a
    stored name and a stored name is what the panel draws first, so inventing one
    here would put "Decoder 1" on a tile for good and the endpoint's real name --
    typed into the rack's own software an hour later -- would never arrive.
    """
    if explicit:
        return explicit
    if live:
        return ""
    word = str(type_schema.get("label") or ctype).strip() or ctype
    return f"{word} {n}"


def _command_label(commands: Mapping[str, Any], name: str | None) -> str:
    """A routing command's declared label, falling back to its id."""
    if not name:
        return ""
    declared = str(_as_mapping(commands.get(name)).get("label") or "").strip()
    return declared or name


def _roster_phrase(origin: str, count: int, plural: str) -> str:
    """Where a list of ports came from, said the way a person would ask it.

    The distinction is the whole reason ``origin`` exists: "the 4 Decoders this
    device reported" is a fact, "the Outputs this driver can have" is a 128-row
    guess for a 4x4 sitting on the bench.
    """
    if origin == REGISTERED:
        return f"the {count} {plural} this device reported"
    if origin == DECLARED:
        return f"the {count} {plural} this device is set up for"
    return f"the {plural} this driver can have"


def _shown_as(plane: _Plane) -> str:
    """What each destination tile reads, in the driver's own words.

    The routed-source property's declared label ("Video Source") rather than its
    key (``source_video``): the key is a wire name, and the author has the same
    property in front of them under its label everywhere else in the IDE.
    """
    declared = str(plane.route_var.get("label") or "").strip()
    return declared or plane.prop


def _is_audio_plane(proposal: Mapping[str, Any]) -> bool:
    """Is this plane the audio one?

    Read off the routed property's own name and the plane's label, which is where
    a plane says which one it is -- ``audio_input`` on a frame's extracted-audio
    matrix, ``source_audio`` on an AVoIP decoder, a declared label of "Audio" on
    both.
    """
    if _plane_token(str(proposal.get("route_property") or "")) == "audio":
        return True
    return "audio" in _words(str(proposal.get("label") or ""))


def _pair_audio_planes(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tell each video plane which plane carries its audio, where one does.

    Every field audio-follow-video needs is already here -- the audio plane's own
    route action and each destination's audio route key -- and without this the
    author is told to hand-author an action list in the Bindings tab for a command
    the picker is holding. Two independently switched planes on one destination is
    the ordinary shape of AVoIP gear and of any frame with an extracted-audio
    matrix, and forgetting the second half is a display showing one source with
    another one's sound.

    Only offered for the plane that is the device's MAIN route (its property
    carries no plane word) or its video one: nothing else follows audio.

    And only when audio routes with a DIFFERENT command, which is the line
    between the two shapes. A frame with a separate ``audio_route`` genuinely
    needs two sends. A device that switches every plane through one command and
    tells them apart with a parameter does not: every one of those in the corpus
    also accepts a combined value on that parameter (MXNet ``stream: all``,
    Chazy and Darwin ``signal: ALL``), so the answer there is the plane that
    sends the combined value, not the same command fired twice.
    """
    audio = [p for p in proposals if _is_audio_plane(p)]
    if not audio:
        return proposals
    for proposal in proposals:
        if _is_audio_plane(proposal):
            continue
        token = _plane_token(str(proposal.get("route_property") or ""))
        if token not in ("", "video"):
            continue
        values = {str(d.get("value")) for d in proposal.get("destinations") or ()}
        match = next(
            (
                p for p in audio
                if p.get("destination_child_type") == proposal.get("destination_child_type")
                and p.get("route")
                and p.get("command") != proposal.get("command")
                and {str(d.get("value")) for d in p.get("destinations") or ()} == values
            ),
            None,
        )
        if match is not None:
            proposal["audio_plane_id"] = match["id"]
    return proposals


def _uniquify_ids(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Make sure no two proposals answer to the same id.

    An id is ``<child type>.<routed property>``, which is unique for every
    driver that routes each plane through its own property. It is NOT unique for
    a device with a combined mode: an MXNet decoder's "All streams" and "Video"
    both watch ``source_video`` and differ only in what they send. The picker
    keys its options by id and finds the chosen one by it, so a collision means
    picking one plane and silently getting the other.
    """
    seen: dict[str, int] = {}
    for proposal in proposals:
        base = str(proposal["id"])
        if base not in seen:
            seen[base] = 1
            continue
        seen[base] += 1
        slug = re.sub(r"[^a-z0-9]+", "_", str(proposal.get("label") or "").lower()).strip("_")
        proposal["id"] = f"{base}.{slug or seen[base]}"
    return proposals


def _type_plural(type_schema: Mapping[str, Any], ctype: str) -> str:
    """What a group of these ports is called, in the driver's own words.

    Sentences here name ports the way the rest of the IDE does -- "the 3 Encoders
    this device reported", not "the registered 'encoder' children". The child
    type's own key is a wire name and an author never sees it anywhere else.
    """
    for key in ("label_plural", "label"):
        word = str(type_schema.get(key) or "").strip()
        if word:
            return word
    return ctype


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
            _with_report_values(
                [{"value": e["value"], "label": e["label"]} for e in enum], route_var,
            ),
            f"the values the route command's '{source_param_def.get('label') or 'source'}' "
            f"parameter accepts",
        )

    if source_ctype and source_ctype in child_types:
        schema = _as_mapping(child_types[source_ctype])
        name_prop = _child_name_property(_as_mapping(schema.get("state_variables")))
        entries = []
        for position, (local, padded, label, online) in enumerate(
            children.entries(source_ctype, schema),
        ):
            entry: dict[str, Any] = {"value": local}
            caption = _entry_label(
                label, schema, source_ctype, position + 1, live=bool(name_prop),
            )
            if caption:
                entry["label"] = caption
            if name_prop:
                entry["label_key"] = f"device.{device_id}.{source_ctype}.{padded}.{name_prop}"
            if online is False:
                entry["offline"] = True
            entries.append(entry)
        if entries:
            plural = _type_plural(schema, source_ctype)
            phrase = {
                REGISTERED: f"the {len(entries)} {plural} this device reported",
                DECLARED: f"the {len(entries)} {plural} this device is set up for",
                RANGE: f"the {plural} this driver can have",
            }[children.origin(source_ctype, schema)]
            return entries, phrase

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


def _reported_vocabulary(route_var: Mapping[str, Any]) -> list[str]:
    """The words the routed-source property says it reports, if it enumerates them."""
    values = route_var.get("values")
    if not isinstance(values, Sequence) or isinstance(values, str | bytes):
        return []
    return [str(v) for v in values if isinstance(v, str | int | float)]


def _with_report_values(
    sources: list[dict[str, Any]], route_var: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Pair what the route command ACCEPTS with what the property REPORTS.

    A route command's options are ``{value, label}``, and on the drivers that
    report in words the property's own enum IS that list of labels. So
    ``at_atdm_0604a`` declares both vocabularies already: send ``"0"``, read back
    ``"Mic"``. Nothing new has to be authored -- what was missing was somewhere
    to put the second one, and a source entry now carries it.

    All or nothing, and by label only. A partial pairing would be a guess about
    which end of the mapping was wrong, and this runs before an author has
    looked at anything; where the two do not line up the proposal keeps its
    warning and says so instead.
    """
    reported = {v.strip().casefold(): v for v in _reported_vocabulary(route_var)}
    if not reported or len(reported) < len(sources):
        return sources
    paired = []
    for entry in sources:
        match = reported.get(str(entry["label"]).strip().casefold())
        if match is None:
            return sources
        paired.append(entry | {"report_value": match})
    # Only worth saying when the two vocabularies actually differ; a device that
    # accepts and reports the same tokens needs no second value.
    if all(str(e["value"]) == str(e["report_value"]) for e in paired):
        return sources
    return paired


def _roster_field(
    type_schema: Mapping[str, Any], config_schema: Mapping[str, Any],
) -> str:
    """What to SET so this child type has ports, in the Settings form's own words.

    A declarative driver that covers a whole family of frames does not know how
    big this one is until somebody says: ``count_from: output_count`` makes the
    roster a config field, and until that field is filled in there are no
    outputs and nothing on the wire will change that. Telling the author to
    connect the device is then advice about a device that is already connected.

    "" when the roster comes from anywhere else -- code, a fixed count, or the
    device's own answer -- where connecting it really is the remedy.
    """
    instances = _as_mapping(type_schema.get("instances"))
    field = str(instances.get("count_from") or instances.get("ids_from") or "")
    if not field:
        return ""
    declared = _as_mapping(_as_mapping(config_schema).get(field))
    return str(declared.get("label") or "").strip() or field


def _proposal_warnings(
    sources: Sequence[Mapping[str, Any]],
    destinations: Sequence[Mapping[str, Any]],
    command: str | None,
    dest_ctype: str,
    route_var: Mapping[str, Any],
    unfilled_params: Sequence[str],
    origin: str,
    roster_field: str = "",
    declared: bool = False,
) -> list[str]:
    """What this proposal cannot know, said before it is applied rather than after.

    Every one of these is a real state a shipped driver reaches. They are
    warnings and not refusals because the author is the one who can settle them,
    and the picker is where they are standing.

    A declaration answers the one question that is about STRUCTURE -- whether
    this really is the end being routed to -- and none of the others, which are
    all about VALUES. A driver can say where its routing lives and still accept
    route tokens it does not report back.
    """
    warnings: list[str] = []
    if not destinations:
        warnings.append(
            "This device has not told the system which ports it has, so there is "
            "nothing to route to yet. "
            + (
                f"Set '{roster_field}' on this device, then press Re-read device."
                if roster_field
                else "Connect it and press Re-read device, or add the "
                     "destinations by hand."
            ),
        )
    elif origin == RANGE:
        # The difference between "a 16x16 frame" and "the four outputs this unit
        # has". A driver that is not connected registers no children, so the
        # list here is the widest thing the driver can be -- which draws a
        # perfectly convincing 16x16 for a 4x4 that is sitting on the bench.
        warnings.append(
            f"These are the {len(destinations)} ports this driver can have, not the "
            f"ones this device has. "
            + (
                f"Set '{roster_field}' on this device and press Re-read device, "
                f"or untick the ones that are not there."
                if roster_field
                else "Connect it and press Re-read device, or untick the ones "
                     "that are not there."
            ),
        )
    # A device can list a port it cannot reach, and the two read identically here.
    # An MXNet CBOX keeps an endpoint in its database after it leaves the rack and
    # then refuses every route to it in its own words ("Device not online"), which
    # is a fault nobody can see from a panel. Not a refusal and not an alarm:
    # switched off overnight is the ordinary case and the list is still right.
    absent = [
        str(entry.get("label") or entry.get("value"))
        for entry in list(destinations) + list(sources)
        if entry.get("offline")
    ]
    if absent:
        warnings.append(
            f"Not answering right now: {', '.join(absent)}. A route to or from one "
            f"of these will not take until it is back. Leave them in if they are "
            f"only switched off, and untick any that have left the rack.",
        )
    # Deliberately NOT warned about: a port the device could name and has not.
    # The renderer captions an unnamed row "Out 3" in the same words it uses for a
    # row with no live key at all, so nothing reads as a raw id and there is
    # nothing to tell anybody. Every unnamed frame in the corpus would have
    # tripped it.
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
    # Both of the next two are about a source whose SENT value is not what comes
    # back, and both stand down for a source that carries the reported value as
    # well -- which is the whole point of pairing them: the panel matches on what
    # is reported and sends what is accepted, so neither is a problem any more.
    zero = [
        str(s.get("label") or s.get("value"))
        for s in sources
        if str(s.get("value")) == "0" and s.get("report_value") is None
    ]
    if zero:
        warnings.append(
            f"{zero[0]} has the value 0, which a panel reads as 'nothing is routed', "
            f"so its crosspoint can never light. Give it the value the device "
            f"reports when it is selected.",
        )
    reported = _reported_vocabulary(route_var)
    if (
        sources
        and reported
        and not any(s.get("report_value") is not None for s in sources)
        and {str(s.get("value")) for s in sources} != set(reported)
    ):
        warnings.append(
            f"The device reports this as one of {', '.join(reported)}, "
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
    if _SOURCE_CHILD_TYPE.match(dest_ctype) and not declared:
        warnings.append(
            f"The destinations here are children called '{dest_ctype}', which usually "
            f"names a source. That is correct on some audio processors and wrong on a "
            f"video switcher -- check this is the end you meant to route TO.",
        )
    return warnings


#: Keys a declared plane may inherit from the ``routing:`` block above it.
_INHERITED: tuple[str, ...] = (
    "destination_child_type",
    "source_child_type",
    "command",
    "destination_param",
    "source_param",
)


class _Plane:
    """One routing plane, however it was arrived at: guessed or declared.

    The two paths differ entirely in how these fields are FOUND and not at all
    in what is done with them, so they meet here and one builder renders both.
    """

    __slots__ = (
        "dest_ctype", "prop", "route_var", "source_ctype", "command",
        "command_label", "dest_param", "source_param", "source_param_def",
        "params", "unfilled", "label", "declared", "typed",
    )

    def __init__(
        self, *, dest_ctype: str, prop: str, route_var: Mapping[str, Any],
        source_ctype: str | None, command: str | None, dest_param: str,
        source_param: str, source_param_def: Mapping[str, Any],
        params: Mapping[str, Any], unfilled: Sequence[str], label: str,
        declared: bool, typed: bool, command_label: str = "",
    ) -> None:
        # The command's own label, which is what the author sees on its Quick
        # Action button and in the Bindings tab. Its id is a wire name.
        self.command_label = command_label or (command or "")
        self.dest_ctype = dest_ctype
        self.prop = prop
        self.route_var = dict(route_var)
        self.source_ctype = source_ctype
        self.command = command
        self.dest_param = dest_param
        self.source_param = source_param
        self.source_param_def = dict(source_param_def)
        self.params = dict(params)
        self.unfilled = list(unfilled)
        self.label = label
        self.declared = declared
        self.typed = typed


def _route_ends(
    params: Mapping[str, Any], dest_ctype: str,
    declared_dest: str, declared_source: str,
) -> tuple[str, str]:
    """Which parameter of a NAMED command takes each end of a route.

    The guessing path searches every command for a pair; here the command is
    already known, so this only has to say which parameter is which. A driver
    that says so is believed outright, including saying nothing: a command
    addressing the device itself takes no destination at all, which is what an
    AVoIP endpoint's ``set_video_source(source)`` looks like.
    """
    dest = declared_dest
    if not dest:
        dest = next(
            (
                p for p, d in params.items()
                if _as_mapping(d).get("type") == "child_id"
                and str(_as_mapping(d).get("child_type") or "") == dest_ctype
            ),
            "",
        ) or next((p for p in params if _DESTINATION_PARAM.match(p)), "")
    source = declared_source or next(
        (p for p in params if p != dest and _SOURCE_PARAM.match(p)), "",
    )
    return dest, source


def _declared_unfilled(
    params: Mapping[str, Any], taken: Sequence[str], provided: Mapping[str, Any],
) -> list[str]:
    """Required parameters of a declared route that nothing supplies a value for."""
    return [
        name for name, raw in params.items()
        if name not in taken and name not in provided
        and _as_mapping(raw).get("required") and "default" not in _as_mapping(raw)
    ]


def _declared_planes(
    info: Mapping[str, Any], child_types: Mapping[str, Any], commands: Mapping[str, Any],
) -> list[_Plane] | None:
    """The planes a driver DECLARES, or None when it declares none.

    A ``routing:`` block replaces the guess rather than joining it: the planes
    it lists are the planes, in the order it lists them. That is most of its
    value. Inference is structural, so a property that merely reads like routing
    -- a clip indicator, a priority mode, a list of window aliases -- proposes a
    matrix whose crosspoints mean nothing, and only the driver can say it is not
    one.

    A block that yields no usable plane returns None and the guess runs, because
    the authoring gates already refuse a malformed block (``routing_block_errors``)
    and a driver that reached a running instance broken is better served by the
    guess than by nothing.
    """
    block = _as_mapping(info.get("routing"))
    raw_planes = block.get("planes")
    if not isinstance(raw_planes, Sequence) or isinstance(raw_planes, str | bytes):
        return None

    planes: list[_Plane] = []
    for raw in raw_planes:
        if not isinstance(raw, Mapping):
            continue
        merged: dict[str, Any] = {k: block.get(k) for k in _INHERITED}
        merged.update(raw)
        prop = str(merged.get("route_property") or "").strip()
        if not prop:
            continue

        dest_ctype = str(merged.get("destination_child_type") or "").strip()
        source_ctype = str(merged.get("source_child_type") or "").strip() or None
        command = str(merged.get("command") or "").strip() or None
        # Fixed extras belong to the plane, and only the plane's own -- a
        # `signal: VIDEO` inherited onto the audio plane would route video.
        params = _as_mapping(raw.get("params")) or _as_mapping(block.get("params"))

        dest_param = source_param = ""
        source_param_def: Mapping[str, Any] = {}
        unfilled: list[str] = []
        if command:
            command_params = _as_mapping(_as_mapping(commands.get(command)).get("params"))
            dest_param, source_param = _route_ends(
                command_params, dest_ctype,
                str(merged.get("destination_param") or "").strip(),
                str(merged.get("source_param") or "").strip(),
            )
            source_param_def = _as_mapping(command_params.get(source_param))
            unfilled = _declared_unfilled(
                command_params, [dest_param, source_param], params,
            )
        if source_param_def.get("type") == "child_id":
            source_ctype = str(source_param_def.get("child_type") or "") or source_ctype

        schema = _as_mapping(child_types.get(dest_ctype)) if dest_ctype else {}
        route_var = _as_mapping(_as_mapping(schema.get("state_variables")).get(prop))
        if not dest_ctype:
            route_var = _as_mapping(_as_mapping(info.get("state_variables")).get(prop))

        planes.append(_Plane(
            dest_ctype=dest_ctype,
            prop=prop,
            route_var=route_var,
            source_ctype=source_ctype,
            command=command,
            command_label=_command_label(commands, command),
            dest_param=dest_param,
            source_param=source_param,
            source_param_def=source_param_def,
            params=params,
            unfilled=unfilled,
            label=str(raw.get("label") or "").strip() or _plane_label(prop, route_var),
            declared=True,
            # A declared plane is not a guess, so it never reads as a weaker
            # one. The picker shows a caveat below "high" and there is nothing
            # here to be unsure about.
            typed=True,
        ))
    return planes or None


def _guessed_planes(
    child_types: Mapping[str, Any], commands: Mapping[str, Any], roster: _Children,
) -> list[_Plane]:
    """Every plane a driver's shape implies, for a driver that declares none."""
    planes: list[_Plane] = []
    for dest_ctype, raw_schema in child_types.items():
        schema = _as_mapping(raw_schema)
        state_vars = _as_mapping(schema.get("state_variables"))

        for prop, raw_var in state_vars.items():
            if prop.lower() in _NOT_ROUTED or not _ROUTED_PROPERTY.match(prop):
                continue
            route_var = _as_mapping(raw_var)
            # A routed source is a port, a name or a number; it is never a
            # yes/no. Two shipped drivers carry a boolean `input_clip` and
            # `input_signal_detect` -- clip and presence read-outs -- and each
            # proposed a matrix offering two dozen inputs against true/false.
            if route_var.get("type") == "boolean":
                continue

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

            params: dict[str, Any] = {}
            unfilled: list[str] = []
            if command:
                params, unfilled = _plane_params(
                    _as_mapping(_as_mapping(commands.get(command)).get("params")),
                    (dest_param, source_param),
                    _plane_token(prop),
                )

            planes.append(_Plane(
                dest_ctype=dest_ctype,
                prop=prop,
                route_var=route_var,
                source_ctype=source_ctype,
                command=command,
                command_label=_command_label(commands, command),
                dest_param=dest_param,
                source_param=source_param,
                source_param_def=source_param_def,
                params=params,
                unfilled=unfilled,
                label=_plane_label(prop, route_var),
                declared=False,
                typed=bool(found) and found.typed,
            ))
    return planes


def _proposal(
    device_id: str,
    plane: _Plane,
    child_types: Mapping[str, Any],
    roster: _Children,
    config_schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One plane, rendered as the matrix somebody could apply."""
    dest_ctype = plane.dest_ctype
    schema = _as_mapping(child_types.get(dest_ctype)) if dest_ctype else {}
    name_prop = _child_name_property(_as_mapping(schema.get("state_variables")))

    sources, source_origin = _sources_from(
        device_id, plane.source_param_def, plane.source_ctype, child_types,
        roster, plane.route_var,
    )

    destinations: list[dict[str, Any]] = []
    if dest_ctype:
        for position, (local, padded, label, online) in enumerate(
            roster.entries(dest_ctype, schema),
        ):
            entry: dict[str, Any] = {
                "value": local,
                "route_key": f"device.{device_id}.{dest_ctype}.{padded}.{plane.prop}",
            }
            caption = _entry_label(
                label, schema, dest_ctype, position + 1, live=bool(name_prop),
            )
            if caption:
                entry["label"] = caption
            if name_prop:
                entry["label_key"] = f"device.{device_id}.{dest_ctype}.{padded}.{name_prop}"
            # Live, so it rides on the proposal and never on the entry the picker
            # writes: whether a port answers today is not a fact about the panel.
            if online is False:
                entry["offline"] = True
            destinations.append(entry)
        origin = roster.origin(dest_ctype, schema)
        roster_field = _roster_field(schema, _as_mapping(config_schema))
        plural = _type_plural(schema, dest_ctype)
        where = (
            f"Destinations are {_roster_phrase(origin, len(destinations), plural)}, "
            f"each showing its {_shown_as(plane)}"
        )
    else:
        # The device routes ITSELF: an AVoIP endpoint whose display shows one
        # source at a time has no destination child, and its matrix is one row.
        # A room of them is one element per device, which the model already
        # allows -- every destination carries its own route key.
        destinations = [{
            "value": device_id,
            "label": device_id,
            "route_key": f"device.{device_id}.{plane.prop}",
        }]
        origin = REGISTERED
        roster_field = ""
        plural = "This device"
        where = f"This device is the destination, showing its {_shown_as(plane)}"

    route = (
        [{
            "action": "device.command",
            "device": device_id,
            "command": plane.command,
            "params": {
                **({plane.dest_param: "$output"} if plane.dest_param else {}),
                **({plane.source_param: "$input"} if plane.source_param else {}),
                **plane.params,
            },
        }]
        if plane.command
        else None
    )

    if not plane.command:
        ends = "and nothing on this driver routes one to another."
    elif plane.source_param or not plane.dest_param:
        ends = f"and a tap sends {plane.command_label}."
    else:
        # A command that names the destination and no source cannot say what to
        # route. Worth saying plainly rather than in parameter names.
        ends = (
            f"and a tap would send {plane.command_label}, which takes no source "
            f"-- so it cannot say what to route."
        )

    return {
        "id": f"{dest_ctype}.{plane.prop}" if dest_ctype else plane.prop,
        "device_id": device_id,
        "label": f"{plural} · {plane.label}",
        "destination_child_type": dest_ctype,
        "route_property": plane.prop,
        "source_child_type": plane.source_ctype,
        "command": plane.command,
        "command_label": plane.command_label,
        # Filled in by _pair_audio_planes once the whole set is known.
        "audio_plane_id": None,
        "confidence": "high" if plane.typed else "medium" if plane.command else "low",
        "why": (
            ("The driver declares this. " if plane.declared else "")
            + f"{where}, {ends} Sources come from {source_origin}."
        ),
        "from_roster": origin == REGISTERED,
        "warnings": _proposal_warnings(
            sources, destinations, plane.command, dest_ctype, plane.route_var,
            plane.unfilled, origin, roster_field, declared=plane.declared,
        ),
        "sources": sources,
        "destinations": destinations,
        "route": route,
    }


def propose_matrices(
    device_id: str,
    driver_info: Mapping[str, Any] | None,
    children: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Every routing plane this device has, as a matrix somebody could apply.

    A driver that declares a ``routing:`` block gets exactly what it declared,
    in its own order. Everything else is guessed from its shape and ordered
    most-confident first. An empty list means neither found any routing, which
    is a real answer and not a failure -- most drivers are not switchers.

    ``config`` is this device's resolved config, and it is what makes the
    proposal the right SIZE: one declarative driver covers a family of frames
    and sizes its roster from a field on the device (``count_from``), so an SIS
    frame with Output Count 4 offers four outputs rather than the 128 the
    protocol allows. Without it every unconnected switcher proposes its maximum.
    """
    info = _as_mapping(driver_info)
    child_types = _as_mapping(info.get("child_entity_types"))
    commands = _as_mapping(info.get("commands"))
    config_schema = _as_mapping(info.get("config_schema"))
    roster = _Children(children, config)

    declared = _declared_planes(info, child_types, commands)
    if declared is not None:
        return _pair_audio_planes(_uniquify_ids([
            _proposal(device_id, plane, child_types, roster, config_schema)
            for plane in declared
        ]))

    proposals = [
        _proposal(device_id, plane, child_types, roster, config_schema)
        for plane in _guessed_planes(child_types, commands, roster)
    ]
    order = {"high": 0, "medium": 1, "low": 2}
    proposals.sort(
        key=lambda p: (order[p["confidence"]], _plane_rank(p["route_property"]), p["id"]),
    )
    return _pair_audio_planes(_uniquify_ids(proposals))
