"""Pins for ``ui/page_references``: every finding, in order, with its key.

The tests beside this file check that a finding names the right thing, mostly
by a phrase in its sentence. These pin the whole answer the AI write door gets
back -- each finding's element id, kind, sentence and key, in the order they
are reported -- and which of the injected lookups are asked, with what, and in
what order. A lookup answering ``None`` is "no opinion"; an empty answer is not
the same thing for every lookup, and each one's reading of it is pinned here.

They record what the module does, not what it should do. Two answers below look
odd and are kept exactly as they are: a custom control's missing file is filed
under its PAGE's id, and a state key with no property after the device
(``device.acme_amp``) is reported as naming a device that is not there.

Every device, macro, plugin and file is invented.
"""

from __future__ import annotations

from types import SimpleNamespace


from openavc.core.project_loader import ProjectConfig
from openavc.ui.page_references import reference_findings

PAGES = {"main", "audio"}
DEVICES = {"acme_amp", "acme.sw", "acme"}
MACROS = {"system_on"}


def _model_page(elements: list, **page: object) -> object:
    """A page the way the write door has it: through ProjectConfig."""
    project = ProjectConfig.model_validate({
        "project": {"id": "pins", "name": "Pins", "description": ""},
        "ui": {"settings": {}, "pages": [{
            "id": "main", "name": "Main", "elements": elements, "layouts": [], **page,
        }], "master_elements": []},
    })
    return project.ui.pages[0]


def _raw_page(elements: list, **page: object) -> object:
    """A page the model would not build, for the shapes only a raw dict has."""
    return SimpleNamespace(id="main", elements=elements, **page)


class _Asked:
    """Lookups that answer from a table and write down every question."""

    def __init__(self, **answers) -> None:
        self.log: list[tuple[str, object]] = []
        self._answers = answers

    def lookups(self) -> dict:
        return {name: self._ask(name) for name in self._answers}

    def _ask(self, name: str):
        answer = self._answers[name]

        def ask(arg):
            self.log.append((name, arg))
            return answer(arg)
        return ask


def _answer(page, **kwargs) -> list[tuple]:
    return [
        (f.element_id, f.kind, f.message, f.key)
        for f in reference_findings(
            page, page_ids=PAGES, device_ids=DEVICES, macro_ids=MACROS, **kwargs,
        )
    ]


def _commands(device: str):
    return {"acme_amp": {"mute_on", "set_level", "send_file"}}.get(device)


def _undeclared(key: str):
    return None if key.endswith((".level", ".label")) else {"label", "level"}


def _roster(key: str):
    return {"1", "2", "3", "4"} if ".7." in key else None


# --- One page that sets off every check, in the order the door reports them ---

_EVERYTHING = [
    {"id": "mx", "type": "matrix",
     "grant": {"devices": ["ghost"]},
     "matrix_config": {
         "route_key_pattern": "device.ghost.output.*.route",
         "input_key_pattern": "device.acme_amp.input.*.name",
     },
     "bindings": {
         "show": {"value": {"key": "device.ghost.level"}},
         # Written route first: the report follows ACTION_SLOTS, not the page.
         "do": {
             "route": {"action": "macro", "macro": "nope"},
             "press": [{"action": "ui.navigate", "page": "nowhere"}],
         },
     }},
    {"id": "plg", "type": "plugin", "plugin_id": "acme_plugin", "plugin_type": "nope"},
    {"id": "cus", "type": "custom", "custom_file": "gone.html",
     "grant": {"devices": ["acme_amp", "ghost"]}},
    {"id": "nav", "type": "page_nav", "target_page": "nowhere"},
    {"id": "lbl", "type": "label",
     "bindings": {"show": {
         "look": {"key": "device.acme_amp.output.7.mute"},
         "value": {"key": "device.acme_amp.nope"},
     }}},
]


def test_a_page_reports_in_its_fixed_order() -> None:
    page = _model_page(
        _EVERYTHING,
        render_mode="custom", custom_file="screen.html",
        grant={"devices": ["ghost", "acme_amp"]},
    )
    assert _answer(
        page,
        device_commands=_commands,
        plugin_elements=lambda plugin: {"meter"} if plugin == "acme_plugin" else None,
        undeclared_property=_undeclared,
        unknown_child_id=_roster,
        ui_files={"room/index.html"},
    ) == EXPECTED_EVERYTHING


def test_a_write_answers_for_its_page_and_what_it_touched() -> None:
    page = _model_page(
        _EVERYTHING,
        render_mode="custom", custom_file="screen.html",
        grant={"devices": ["ghost"]},
    )
    assert _answer(page, touched={"nav"}, ui_files=set()) == EXPECTED_TOUCHED
    assert _answer(page, touched=set(), ui_files=set()) == EXPECTED_TOUCHED[:2]


# --- value_map ---


def test_a_value_map_reports_its_strays_then_each_entry_in_written_order() -> None:
    page = _raw_page([{
        "id": "sel", "type": "select",
        "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}, "x",
                    {"label": "no value"}, {"value": None}],
        "bindings": {"do": {"change": [{"action": "value_map", "map": {
            "x": {"action": "macro", "macro": "nope"},
            "b": {"action": "value_map", "map": {
                "zz": {"action": "device.command", "device": "ghost", "command": "on"},
            }},
            "c": "not an action",
            "a": {"action": "ui.navigate", "page": "nowhere"},
            "d": None,
        }}]}},
    }])
    assert _answer(page) == EXPECTED_VALUE_MAP


def test_a_select_with_no_option_values_does_not_judge_its_map() -> None:
    page = _raw_page([{
        "id": "sel", "type": "select", "options": [{"label": "A"}],
        "bindings": {"do": {"change": [{"action": "value_map", "map": {"x": None}}]}},
    }, {
        "id": "sel2", "type": "select", "options": [],
        "bindings": {"do": {"change": [{"action": "value_map", "map": "not a map"}]}},
    }])
    assert _answer(page) == []


# --- What each lookup's empty answer means ---


def test_an_empty_command_list_warns() -> None:
    page = _raw_page([{"id": "btn", "type": "button", "bindings": {"do": {"press": [
        {"action": "device.command", "device": "acme_amp", "command": "mute_on"},
    ]}}}])
    assert _answer(page, device_commands=lambda device: set()) == EXPECTED_EMPTY_COMMANDS


def test_an_empty_plugin_element_list_warns() -> None:
    page = _raw_page([{"id": "plg", "type": "plugin", "plugin_id": "p", "plugin_type": "t"}])
    assert _answer(page, plugin_elements=lambda plugin: set()) == EXPECTED_EMPTY_PLUGIN


def test_an_empty_ui_folder_warns() -> None:
    page = _raw_page([{"id": "cus", "type": "custom", "custom_file": "a.html"}])
    assert _answer(page, ui_files=set()) == EXPECTED_EMPTY_UI_FILES


def test_the_other_lookups_read_an_empty_answer_as_nothing_to_say() -> None:
    page = _raw_page([{
        "id": "mx", "type": "matrix",
        "matrix_config": {"route_key_pattern": "device.acme_amp.output.*.route"},
        "bindings": {
            "show": {"value": {"key": "device.acme_amp.output.7.mute"}},
            "do": {"press": [
                {"action": "device.command", "device": "acme_amp", "command": "set_level"},
            ]},
        },
    }])
    asked = _Asked(
        device_commands=_commands,
        undeclared_property=lambda key: set(),
        unknown_child_id=lambda key: set(),
        missing_params=lambda action: [],
        file_command=lambda action: "",
    )
    assert _answer(page, **asked.lookups()) == []
    assert asked.log == EXPECTED_EMPTY_ASKED


# --- Which lookups are asked, and when ---


def test_which_lookups_a_page_asks_and_in_what_order() -> None:
    page = _raw_page([{
        "id": "mx", "type": "matrix",
        "matrix_config": {
            "route_key_pattern": "device.acme_amp.output.*.*.route",
            "input_key_pattern": "device.acme_amp.level",
            "output_key_pattern": "var.x.*",
            "audio_route_key_pattern": 5,
        },
        "bindings": {
            "show": {
                "value": {"key": "device.acme_amp.output.7.mute"},
                "look": {"key": "device.acme_amp.power"},
            },
            "do": {"press": [
                {"action": "device.command", "device": "acme_amp", "command": "send_file"},
                {"action": "device.command", "device": "acme_amp", "command": "set_level"},
                {"action": "device.command", "device": "ghost", "command": "set_level"},
                {"action": "device.command", "device": "acme_amp", "command": "explode"},
            ]},
        },
    }])
    asked = _Asked(
        device_commands=_commands,
        undeclared_property=_undeclared,
        unknown_child_id=_roster,
        missing_params=lambda action: ["level"],
        file_command=lambda action: (
            "it takes a file" if action.get("command") == "send_file" else None
        ),
    )
    assert _answer(page, **asked.lookups()) == EXPECTED_ASKED_FINDINGS
    assert asked.log == EXPECTED_ASKED_LOG


def test_a_driver_with_no_command_list_still_has_its_commands_checked_for_a_press() -> None:
    page = _raw_page([{"id": "btn", "type": "button", "bindings": {"do": {"press": [
        {"action": "device.command", "device": "acme_amp", "command": "anything"},
    ]}}}])
    asked = _Asked(
        device_commands=lambda device: None,
        missing_params=lambda action: ["level", "channel"],
        file_command=lambda action: None,
    )
    assert _answer(page, **asked.lookups()) == EXPECTED_NO_COMMAND_LIST
    assert asked.log == [
        ("device_commands", "acme_amp"),
        ("file_command", {"action": "device.command", "device": "acme_amp",
                          "command": "anything"}),
        ("missing_params", {"action": "device.command", "device": "acme_amp",
                            "command": "anything"}),
    ]


def test_an_empty_device_id_is_not_missing_and_is_still_asked_about() -> None:
    page = _raw_page([{"id": "btn", "type": "button", "bindings": {"do": {"press": [
        {"action": "device.command", "device": "", "command": "on"},
    ]}}}])
    asked = _Asked(
        device_commands=lambda device: None,
        missing_params=lambda action: ["level"],
        file_command=lambda action: None,
    )
    assert _answer(page, **asked.lookups()) == EXPECTED_EMPTY_DEVICE
    assert [name for name, _ in asked.log] == [
        "device_commands", "file_command", "missing_params",
    ]


# --- Shapes that say nothing, and must not raise ---


def test_shapes_that_are_not_references_say_nothing() -> None:
    page = _raw_page([
        "not an element",
        None,
        {"id": "a", "type": "button", "bindings": {"do": {"press": [
            {"action": {"action": "macro", "macro": "nope"}},
            {"action": ["macro"], "macro": "nope"},
            {"action": None, "macro": "nope"},
            {"action": "script.call", "macro": "nope"},
            {"action": "device.command", "device": 4, "command": "on"},
            {"action": "device.command", "device": "acme_amp", "command": 7},
            {"action": "macro", "macro": ""},
            {"action": "macro", "macro": 3},
            {"action": "ui.navigate", "page": "$back"},
            {"action": "ui.navigate", "page": "$dismiss"},
            {"action": "ui.navigate", "page": ""},
            {"action": "ui.navigate", "page": 5},
            "press", 5, None,
        ], "release": "press", "weird_slot": [{"action": "macro", "macro": "nope"}]}}},
        {"id": "b", "type": "matrix", "matrix_config": "not a config"},
        {"id": "c", "type": "matrix", "matrix_config": {}},
        {"id": "d", "type": "label", "target_page": "nowhere",
         "bindings": {"show": {"value": {"key": "var.x"}, "look": "x", "text": {
             "key": "device.ghost.x"}}}},
        {"id": "e", "type": "page_nav", "target_page": "$back"},
        {"id": "f", "type": "page_nav", "target_page": ""},
        {"id": "g", "type": "custom", "custom_file": ""},
        {"id": "h", "type": "button", "grant": {"devices": ["", 5, "acme_amp"]}},
        {"id": "i", "type": "button", "grant": "not a grant"},
        {"id": "j", "type": "button", "bindings": "not bindings"},
    ], render_mode="custom", custom_file="", grant={"devices": None})
    assert _answer(
        page,
        device_commands=_commands,
        undeclared_property=_undeclared,
        unknown_child_id=_roster,
        missing_params=lambda action: ["level"],
        file_command=lambda action: None,
        ui_files=set(),
    ) == []


# --- State keys: which device a key belongs to ---


def test_a_state_key_is_resolved_by_its_longest_device_id() -> None:
    page = _raw_page([{"id": "lbl", "type": "label", "bindings": {"show": {
        "value": {"key": "device.acme.sw.power"},
        "look": {"key": "device.acme.power"},
    }}}, {"id": "lbl2", "type": "label", "bindings": {"show": {
        "value": {"key": "device.acme_amp"},
        "look": {"key": "device."},
    }}}])
    asked = _Asked(undeclared_property=lambda key: {"level"})
    assert _answer(page, **asked.lookups()) == EXPECTED_LONGEST_DEVICE
    assert asked.log == [
        ("undeclared_property", "device.acme.sw.power"),
        ("undeclared_property", "device.acme.power"),
    ]


# --- Grants ---


def test_each_granted_device_that_is_missing_is_its_own_finding() -> None:
    page = _raw_page(
        [{"id": "cus", "type": "custom", "grant": {"devices": ["zed", "acme", "ghost", "zed"]}}],
        grant={"devices": ["zed", "", "ghost"], "variables": ["v"]},
    )
    assert _answer(page) == EXPECTED_GRANTS


# --- Shape of an action list ---


def test_an_object_shaped_slot_is_said_once_and_still_checked() -> None:
    page = _raw_page([{"id": "t", "type": "toggle", "bindings": {"do": {
        "release": [{"action": "macro", "macro": "nope"}],
        "press": {"action": "macro", "macro": "nope2"},
        "hold": {"action": "macro", "macro": "system_on"},
    }}}])
    assert _answer(page) == EXPECTED_OBJECT_SHAPE


# --- Expected answers, recorded from the module as it was (openavc ad49ef6a) ---

EXPECTED_EVERYTHING = [('main',
  'dangling_reference',
  "main (page) is granted device 'ghost', which is not in this project, so the page it shows "
  'can neither read nor control it. The devices are: acme, acme.sw, acme_amp.',
  ('dangling_reference', 'main', 'grant.devices.ghost')),
 ('main',
  'dangling_reference',
  "main (page) shows 'screen.html', which is not in the project's ui/ folder, so it draws an "
  'empty box. The files there are: room/index.html.',
  ('dangling_reference', 'main', 'custom_file.screen.html')),
 ('mx',
  'dangling_reference',
  "mx (matrix) is granted device 'ghost', which is not in this project, so it can neither read "
  'nor control it. The devices are: acme, acme.sw, acme_amp.',
  ('dangling_reference', 'mx', 'grant.devices.ghost')),
 ('mx',
  'dangling_reference',
  "mx (matrix) reads matrix_config.route_key_pattern from 'device.ghost.output.*.route', and "
  "no device 'ghost' is in this project. The devices are: acme, acme.sw, acme_amp.",
  ('dangling_reference', 'mx', 'matrix_config.route_key_pattern')),
 ('mx',
  'dangling_reference',
  "mx (matrix) reads matrix_config.input_key_pattern from 'device.acme_amp.input.*.name', and "
  'its driver does not declare that. It declares: label, level.',
  ('dangling_reference', 'mx', 'matrix_config.input_key_pattern.property')),
 ('mx',
  'dangling_reference',
  "mx (matrix) reads show.value from 'device.ghost.level', and no device 'ghost' is in this "
  'project. The devices are: acme, acme.sw, acme_amp.',
  ('dangling_reference', 'mx', 'show.value')),
 ('mx',
  'dangling_reference',
  "mx (matrix) do.press[0] navigates to page 'nowhere', which does not exist. The pages are: "
  'audio, main.',
  ('dangling_reference', 'mx', 'do.press[0]', 'page')),
 ('mx',
  'object_action_list',
  'mx (matrix) writes do.route as an object, not an array. The panel does run it -- a single '
  'action is wrapped before it executes -- but an object holds exactly one, so a second cannot '
  'be added without rewriting it, and the Builder stores the array form on the next edit '
  'anyway. Write do.route as [{...}].',
  ('object_action_list', 'mx', 'route')),
 ('mx',
  'dangling_reference',
  "mx (matrix) do.route[0] runs macro 'nope', which does not exist. The macros are: system_on.",
  ('dangling_reference', 'mx', 'do.route[0]', 'macro')),
 ('plg',
  'plugin_element_unconfigured',
  "plg (plugin) asks 'acme_plugin' for a 'nope' element, which it does not declare, so the "
  'panel loads a renderer that is not there. acme_plugin declares: meter.',
  ('plugin_element_unconfigured', 'plg', 'undeclared')),
 ('main',
  'dangling_reference',
  "cus (custom control) shows 'gone.html', which is not in the project's ui/ folder, so it "
  'draws an empty box. The files there are: room/index.html.',
  ('dangling_reference', 'cus', 'custom_file.gone.html')),
 ('cus',
  'dangling_reference',
  "cus (custom) is granted device 'ghost', which is not in this project, so it can neither "
  'read nor control it. The devices are: acme, acme.sw, acme_amp.',
  ('dangling_reference', 'cus', 'grant.devices.ghost')),
 ('nav',
  'dangling_reference',
  "nav (page_nav) navigates to page 'nowhere', which does not exist. The pages are: audio, "
  'main.',
  ('dangling_reference', 'nav', 'target_page')),
 ('lbl',
  'dangling_reference',
  "lbl (label) reads show.value from 'device.acme_amp.nope', and its driver does not declare "
  'that. It declares: label, level.',
  ('dangling_reference', 'lbl', 'show.value.property')),
 ('lbl',
  'dangling_reference',
  "lbl (label) reads show.look from 'device.acme_amp.output.7.mute', and its driver declares "
  'no such child. It declares: 1, 2, 3, 4.',
  ('dangling_reference', 'lbl', 'show.look.child'))]

EXPECTED_TOUCHED = [('main',
  'dangling_reference',
  "main (page) is granted device 'ghost', which is not in this project, so the page it shows "
  'can neither read nor control it. The devices are: acme, acme.sw, acme_amp.',
  ('dangling_reference', 'main', 'grant.devices.ghost')),
 ('main',
  'dangling_reference',
  "main (page) shows 'screen.html', which is not in the project's ui/ folder, so it draws an "
  'empty box. The files there are: none yet.',
  ('dangling_reference', 'main', 'custom_file.screen.html')),
 ('nav',
  'dangling_reference',
  "nav (page_nav) navigates to page 'nowhere', which does not exist. The pages are: audio, "
  'main.',
  ('dangling_reference', 'nav', 'target_page'))]

EXPECTED_VALUE_MAP = [('sel',
  'dangling_reference',
  "sel (select) do.change[0] maps 'c', 'd', 'x', which are not among its options (a, b), so "
  'nothing the user can pick reaches them.',
  ('dangling_reference', 'sel', 'do.change[0]', 'value_map')),
 ('sel',
  'dangling_reference',
  "sel (select) do.change[0]['x'] runs macro 'nope', which does not exist. The macros are: "
  'system_on.',
  ('dangling_reference', 'sel', "do.change[0]['x']", 'macro')),
 ('sel',
  'dangling_reference',
  "sel (select) do.change[0]['b']['zz'] commands device 'ghost', which is not in this project. "
  'The devices are: acme, acme.sw, acme_amp.',
  ('dangling_reference', 'sel', "do.change[0]['b']['zz']", 'device')),
 ('sel',
  'dangling_reference',
  "sel (select) do.change[0]['a'] navigates to page 'nowhere', which does not exist. The pages "
  'are: audio, main.',
  ('dangling_reference', 'sel', "do.change[0]['a']", 'page'))]

EXPECTED_EMPTY_COMMANDS = [('btn',
  'dangling_reference',
  "btn (button) do.press[0] sends 'mute_on' to 'acme_amp', which its driver does not have. Its "
  'commands are: none.',
  ('dangling_reference', 'btn', 'do.press[0]', 'command'))]

EXPECTED_EMPTY_PLUGIN = [('plg',
  'plugin_element_unconfigured',
  "plg (plugin) asks 'p' for a 't' element, which it does not declare, so the panel loads a "
  'renderer that is not there. p declares: none.',
  ('plugin_element_unconfigured', 'plg', 'undeclared'))]

EXPECTED_EMPTY_UI_FILES = [('main',
  'dangling_reference',
  "cus (custom control) shows 'a.html', which is not in the project's ui/ folder, so it draws "
  'an empty box. The files there are: none yet.',
  ('dangling_reference', 'cus', 'custom_file.a.html'))]

EXPECTED_EMPTY_ASKED = [('undeclared_property', 'device.acme_amp.output.1.route'),
 ('unknown_child_id', 'device.acme_amp.output.7.mute'),
 ('undeclared_property', 'device.acme_amp.output.7.mute'),
 ('device_commands', 'acme_amp'),
 ('file_command', {'action': 'device.command', 'device': 'acme_amp', 'command': 'set_level'}),
 ('missing_params', {'action': 'device.command', 'device': 'acme_amp', 'command': 'set_level'})]

EXPECTED_ASKED_FINDINGS = [('mx',
  'dangling_reference',
  "mx (matrix) reads matrix_config.route_key_pattern from 'device.acme_amp.output.*.*.route', "
  'and its driver does not declare that. It declares: label, level.',
  ('dangling_reference', 'mx', 'matrix_config.route_key_pattern.property')),
 ('mx',
  'dangling_reference',
  "mx (matrix) reads show.value from 'device.acme_amp.output.7.mute', and its driver declares "
  'no such child. It declares: 1, 2, 3, 4.',
  ('dangling_reference', 'mx', 'show.value.child')),
 ('mx',
  'dangling_reference',
  "mx (matrix) reads show.look from 'device.acme_amp.power', and its driver does not declare "
  'that. It declares: label, level.',
  ('dangling_reference', 'mx', 'show.look.property')),
 ('mx',
  'unrunnable_command',
  "mx (matrix) do.press[0] sends 'send_file' to 'acme_amp', and every press is refused with "
  '"it takes a file"',
  ('unrunnable_command', 'mx', 'do.press[0]')),
 ('mx',
  'unrunnable_command',
  "mx (matrix) do.press[1] sends 'set_level' to 'acme_amp' without 'level'. Its driver "
  'requires it, so the device refuses every press with "\'set_level\': \'level\' is required".',
  ('unrunnable_command', 'mx', 'do.press[1]')),
 ('mx',
  'dangling_reference',
  "mx (matrix) do.press[2] commands device 'ghost', which is not in this project. The devices "
  'are: acme, acme.sw, acme_amp.',
  ('dangling_reference', 'mx', 'do.press[2]', 'device')),
 ('mx',
  'dangling_reference',
  "mx (matrix) do.press[3] sends 'explode' to 'acme_amp', which its driver does not have. Its "
  'commands are: mute_on, send_file, set_level.',
  ('dangling_reference', 'mx', 'do.press[3]', 'command'))]

EXPECTED_ASKED_LOG = [('undeclared_property', 'device.acme_amp.output.1.*.route'),
 ('unknown_child_id', 'device.acme_amp.output.7.mute'),
 ('unknown_child_id', 'device.acme_amp.power'),
 ('undeclared_property', 'device.acme_amp.power'),
 ('device_commands', 'acme_amp'),
 ('file_command', {'action': 'device.command', 'device': 'acme_amp', 'command': 'send_file'}),
 ('device_commands', 'acme_amp'),
 ('file_command', {'action': 'device.command', 'device': 'acme_amp', 'command': 'set_level'}),
 ('missing_params', {'action': 'device.command', 'device': 'acme_amp', 'command': 'set_level'}),
 ('device_commands', 'acme_amp')]

EXPECTED_NO_COMMAND_LIST = [('btn',
  'unrunnable_command',
  "btn (button) do.press[0] sends 'anything' to 'acme_amp' without 'level' and 'channel'. Its "
  'driver requires them, so the device refuses every press with "\'anything\': \'level\' is '
  'required".',
  ('unrunnable_command', 'btn', 'do.press[0]'))]

EXPECTED_EMPTY_DEVICE = [('btn',
  'unrunnable_command',
  "btn (button) do.press[0] sends 'on' to '' without 'level'. Its driver requires it, so the "
  'device refuses every press with "\'on\': \'level\' is required".',
  ('unrunnable_command', 'btn', 'do.press[0]'))]

EXPECTED_LONGEST_DEVICE = [('lbl',
  'dangling_reference',
  "lbl (label) reads show.value from 'device.acme.sw.power', and its driver does not declare "
  'that. It declares: level.',
  ('dangling_reference', 'lbl', 'show.value.property')),
 ('lbl',
  'dangling_reference',
  "lbl (label) reads show.look from 'device.acme.power', and its driver does not declare that. "
  'It declares: level.',
  ('dangling_reference', 'lbl', 'show.look.property')),
 ('lbl2',
  'dangling_reference',
  "lbl2 (label) reads show.value from 'device.acme_amp', and no device 'acme_amp' is in this "
  'project. The devices are: acme, acme.sw, acme_amp.',
  ('dangling_reference', 'lbl2', 'show.value')),
 ('lbl2',
  'dangling_reference',
  "lbl2 (label) reads show.look from 'device.', and no device '' is in this project. The "
  'devices are: acme, acme.sw, acme_amp.',
  ('dangling_reference', 'lbl2', 'show.look'))]

EXPECTED_GRANTS = [('main',
  'dangling_reference',
  "main (page) is granted device 'zed', which is not in this project, so the page it shows can "
  'neither read nor control it. The devices are: acme, acme.sw, acme_amp.',
  ('dangling_reference', 'main', 'grant.devices.zed')),
 ('main',
  'dangling_reference',
  "main (page) is granted device 'ghost', which is not in this project, so the page it shows "
  'can neither read nor control it. The devices are: acme, acme.sw, acme_amp.',
  ('dangling_reference', 'main', 'grant.devices.ghost')),
 ('cus',
  'dangling_reference',
  "cus (custom) is granted device 'zed', which is not in this project, so it can neither read "
  'nor control it. The devices are: acme, acme.sw, acme_amp.',
  ('dangling_reference', 'cus', 'grant.devices.zed')),
 ('cus',
  'dangling_reference',
  "cus (custom) is granted device 'ghost', which is not in this project, so it can neither "
  'read nor control it. The devices are: acme, acme.sw, acme_amp.',
  ('dangling_reference', 'cus', 'grant.devices.ghost')),
 ('cus',
  'dangling_reference',
  "cus (custom) is granted device 'zed', which is not in this project, so it can neither read "
  'nor control it. The devices are: acme, acme.sw, acme_amp.',
  ('dangling_reference', 'cus', 'grant.devices.zed'))]

EXPECTED_OBJECT_SHAPE = [('t',
  'object_action_list',
  't (toggle) writes do.press as an object, not an array. The panel does run it -- a single '
  'action is wrapped before it executes -- but an object holds exactly one, so a second cannot '
  'be added without rewriting it, and the Builder stores the array form on the next edit '
  'anyway. Write do.press as [{...}].',
  ('object_action_list', 't', 'press')),
 ('t',
  'dangling_reference',
  "t (toggle) do.press[0] runs macro 'nope2', which does not exist. The macros are: system_on.",
  ('dangling_reference', 't', 'do.press[0]', 'macro')),
 ('t',
  'dangling_reference',
  "t (toggle) do.release[0] runs macro 'nope', which does not exist. The macros are: "
  'system_on.',
  ('dangling_reference', 't', 'do.release[0]', 'macro')),
 ('t',
  'object_action_list',
  't (toggle) writes do.hold as an object, not an array. The panel does run it -- a single '
  'action is wrapped before it executes -- but an object holds exactly one, so a second cannot '
  'be added without rewriting it, and the Builder stores the array form on the next edit '
  'anyway. Write do.hold as [{...}].',
  ('object_action_list', 't', 'hold'))]
