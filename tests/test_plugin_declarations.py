"""A plugin's declarations read from its source without importing it, and
judged by the rules the loader refuses a plugin with.

Most cases are differential: the same source is imported the way the loader
imports a plugin (``find_plugin_class``) and read as text
(``read_source``), and the two must hold the same values, except where the
text computes one, which the read says it could not read.
"""

import sys
import textwrap

import pytest

from openavc.core.plugin_declarations import UNREAD, Declared, first_problem
from openavc.core.plugin_import import find_plugin_class
from openavc.core.plugin_manifest import validate_manifest
from openavc.core.plugin_source import read_attribute, read_source

_INFO = (
    '"id": "acme_widget", "name": "Acme Widget", "version": "1.0.0", '
    '"author": "Acme", "description": "A widget.", "category": "utility", '
    '"license": "MIT"'
)


@pytest.fixture
def imported(tmp_path, monkeypatch):
    """Import a source the way the loader imports a plugin; its class."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    before = set(sys.modules)
    made = []

    def load(text):
        folder = tmp_path / f"acme_decl_{len(made)}"
        folder.mkdir()
        (folder / f"{folder.name}_plugin.py").write_text(text, encoding="utf-8")
        made.append(folder)
        return find_plugin_class(folder)

    yield load
    for name in set(sys.modules) - before:
        if name.startswith("plugin_acme_decl_"):
            sys.modules.pop(name, None)


def _src(text):
    return textwrap.dedent(text).lstrip()


def _values(declared):
    return (
        declared.info, declared.config_schema, declared.config_secrets,
        declared.macro_actions, declared.script_api, declared.extensions,
    )


def _same_as_imported(imported, text):
    """The static read of ``text`` and the class the loader imports from it."""
    cls = imported(text)
    assert cls is not None
    read = read_source(text)
    assert read is not None
    assert read.class_name == cls.__name__
    return read, Declared.of_class(cls)


# --- what is read -------------------------------------------------------------


def test_a_class_body_literal_reads_as_the_class_holds_it(imported):
    text = _src(f"""
        class AcmeWidgetPlugin:
            PLUGIN_INFO = {{{_INFO}, "capabilities": ["state_read"], "platforms": ["all"]}}
            CONFIG_SCHEMA = {{"host": {{"type": "string", "default": "10.0.0.5"}}}}
            CONFIG_SECRETS = {{"rooms": {{"id": "room_id", "fields": ["pin"]}}}}
            MACRO_ACTIONS = {{"acme_widget.ping": {{"handler": "ping", "params": []}}}}
            SCRIPT_API = {{"ping": {{"handler": "ping"}}}}
            EXTENSIONS = {{"views": [{{"id": "main", "label": "Main"}}]}}

            async def ping(self):
                pass
    """)
    read, held = _same_as_imported(imported, text)
    assert _values(read) == _values(held)
    assert read.unread == ()


def test_a_module_level_dict_bound_into_the_class_is_followed(imported):
    """The shape two published plugins use: each declaration a module-level
    dict, bound into the class under its own name."""
    text = _src(f"""
        PLUGIN_INFO = {{{_INFO}}}
        CONFIG_SCHEMA = {{"host": {{"type": "string"}}}}
        EXTENSIONS = {{"status_cards": [{{"id": "card"}}]}}

        class AcmeWidgetPlugin:
            PLUGIN_INFO = PLUGIN_INFO
            CONFIG_SCHEMA = CONFIG_SCHEMA
            EXTENSIONS = EXTENSIONS
    """)
    read, held = _same_as_imported(imported, text)
    assert _values(read) == _values(held)
    assert read.extensions == {"status_cards": [{"id": "card"}]}


def test_module_constants_are_read_as_values_and_inside_them(imported):
    text = _src("""
        _ALIAS = "acme"
        _CAP = "guest_endpoints"
        _DEPS = ["acme-sdk>=1.0"]
        _BASE = {"name": "Acme Widget", "version": "1.0.0"}

        class AcmeWidgetPlugin:
            PLUGIN_INFO = {
                **_BASE, "id": "acme_widget", "author": "Acme", "description": "d",
                "category": "utility", "license": "MIT",
                "guest_alias": _ALIAS, "capabilities": ["state_read", _CAP],
                "dependencies": _DEPS, "min_openavc_version": "0.1.0",
            }
    """)
    read, held = _same_as_imported(imported, text)
    assert _values(read) == _values(held)
    assert read.info["dependencies"] == ["acme-sdk>=1.0"]
    assert read.info["capabilities"] == ["state_read", "guest_endpoints"]


def test_a_chain_of_names_and_an_annotated_assignment_are_followed(imported):
    text = _src(f"""
        _INFO: dict = {{{_INFO}}}
        _SHARED = _INFO

        class AcmeWidgetPlugin:
            _PORTS = [{{"port": 9000, "reason": "r"}}]
            PLUGIN_INFO = _SHARED
            CONFIG_SCHEMA: dict = {{"ports": {{"type": "string", "default": _PORTS[0]["reason"]}}}}
    """)
    read, held = _same_as_imported(imported, text)
    assert read.info == held.info
    # A subscript is computed: the whole declaration is unread.
    assert read.config_schema is UNREAD
    assert read.unread == ("CONFIG_SCHEMA",)


def test_a_name_is_read_where_python_reads_it(imported):
    """The class body so far, then the module before the class: a later
    reassignment does not reach the class."""
    text = _src("""
        _VERSION = "1.0.0"
        _CATEGORY = "sensor"

        class AcmeWidgetPlugin:
            _CATEGORY = "utility"
            PLUGIN_INFO = {"id": "acme_widget", "name": "n", "version": _VERSION,
                           "author": "a", "description": "d",
                           "category": _CATEGORY, "license": "MIT"}

        _VERSION = "9.9.9"
    """)
    read, held = _same_as_imported(imported, text)
    assert read.info == held.info
    assert read.info["version"] == "1.0.0"
    assert read.info["category"] == "utility"


def test_the_class_the_loader_picks_is_the_one_read(imported):
    """The first class by name whose PLUGIN_INFO is a dict with an id, as the
    loader's dir() walk finds it, whatever order the file defines them in."""
    text = _src(f"""
        class Zeta:
            PLUGIN_INFO = {{{_INFO.replace("acme_widget", "acme_zeta")}}}

        class Beta:
            PLUGIN_INFO = {{"name": "no id"}}

        class Alpha:
            PLUGIN_INFO = {{{_INFO}}}

        class Aardvark:
            pass
    """)
    read, held = _same_as_imported(imported, text)
    assert read.class_name == "Alpha"
    assert read.info == held.info


def test_a_value_built_at_runtime_is_unread_and_the_rest_still_read(imported):
    text = _src("""
        import platform

        def _version():
            return "1.0.0"

        class AcmeWidgetPlugin:
            PLUGIN_INFO = {"id": "acme_widget", "name": f"Acme {1}", "version": _version(),
                           "author": "Acme", "description": "d", "category": "utility",
                           "license": "MIT", "platforms": [platform.system()]}
            CONFIG_SCHEMA = dict(host={"type": "string"})
    """)
    read, held = _same_as_imported(imported, text)
    assert read.unread == (
        "PLUGIN_INFO['name']", "PLUGIN_INFO['version']", "PLUGIN_INFO['platforms']",
        "CONFIG_SCHEMA",
    )
    for key in ("name", "version", "platforms"):
        assert read.info[key] is UNREAD
    assert {k: v for k, v in read.info.items() if v is not UNREAD} == {
        k: v for k, v in held.info.items() if k not in ("name", "version", "platforms")
    }
    assert read.config_schema is UNREAD


def test_one_attribute_reads_as_the_imported_class_holds_it(imported):
    """``SURFACE_LAYOUT``, which the start does not judge, read the way the
    declarations are: a module-level dict bound into the class included."""
    text = _src(f"""
        LAYOUT = {{"type": "matrix", "rows_state_pattern": "plugin.acme_widget.rx.*"}}

        class AcmeWidgetPlugin:
            PLUGIN_INFO = {{{_INFO}}}
            SURFACE_LAYOUT = LAYOUT
    """)
    cls = imported(text)
    assert read_attribute(text, "AcmeWidgetPlugin", "SURFACE_LAYOUT") == cls.SURFACE_LAYOUT


def test_one_attribute_the_class_does_not_assign_or_builds_at_runtime():
    text = _src(f"""
        MODELS = ("Acme Pad",)

        class AcmeWidgetPlugin:
            PLUGIN_INFO = {{{_INFO}}}
            SURFACE_LAYOUT = {{"type": "grid", "virtual_models": list(MODELS)}}
    """)
    assert read_attribute(text, "AcmeWidgetPlugin", "SURFACE_LAYOUT") is UNREAD
    assert read_attribute(text, "AcmeWidgetPlugin", "EXTENSIONS") is None
    assert read_attribute(text, "Elsewhere", "SURFACE_LAYOUT") is None


def test_no_plugin_class_reads_as_none():
    assert read_source("x = 1\nclass Helper:\n    pass\n") is None
    assert read_source('class A:\n    PLUGIN_INFO = {"name": "no id"}\n') is None
    assert read_source("class A:\n    PLUGIN_INFO = build()\n") is None
    assert read_source("") is None


def test_source_that_does_not_parse_raises():
    with pytest.raises(SyntaxError):
        read_source("class A(:\n")


def test_a_key_that_cannot_be_hashed_is_unread_rather_than_raising():
    """Such a module would not import; the read still answers."""
    read = read_source(f'class A:\n    PLUGIN_INFO = {{{_INFO}, "x": {{[1]: 2}}}}\n')
    assert read.info["x"] is UNREAD
    assert read.unread == ("PLUGIN_INFO['x']",)


# --- what the rules say about a read ----------------------------------------------


@pytest.mark.parametrize("extra, attrs", [
    ('"id": "acme.widget"', ""),
    ('"license": "GPL-3.0"', ""),
    ('"category": "bogus"', ""),
    ('"capabilities": ["fly"]', ""),
    ('"guest_alias": "api", "capabilities": ["guest_endpoints"]', ""),
    ('"network_ports": [{"port": 22, "reason": "ssh"}], "capabilities": ["network_listen"]', ""),
    ("", "CONFIG_SCHEMA = []"),
    ("", 'CONFIG_SCHEMA = {"host": "10.0.0.5"}'),
    ("", 'CONFIG_SCHEMA = {"advanced": {"type": "group", "fields": {"retries": 3}}}'),
    ("", 'CONFIG_SECRETS = {"[b]": {"id": "x", "fields": ["y"]}}'),
    ("", 'MACRO_ACTIONS = {"other.ping": {"handler": "ping"}}'),
    ("", 'SCRIPT_API = {"register": {"handler": "ping"}}'),
    ("", 'EXTENSIONS = {"views": [{"label": "no id"}]}'),
])
def test_a_read_is_refused_with_the_loaders_own_sentence(imported, extra, attrs):
    """A key written twice keeps its last value, read or imported."""
    text = _src(f"""
        class AcmeWidgetPlugin:
            PLUGIN_INFO = {{{", ".join(p for p in (_INFO, extra) if p)}}}
            {attrs}

            async def ping(self):
                pass
    """)
    cls = imported(text)
    valid, message = validate_manifest(cls, "linux_x64")
    assert not valid
    assert first_problem(read_source(text)) == message


def test_a_read_that_passes_passes_at_start_too(imported):
    text = _src(f"""
        class AcmeWidgetPlugin:
            PLUGIN_INFO = {{{_INFO}, "capabilities": ["network_listen"],
                           "network_ports": [{{"port": 9000, "protocol": "udp", "reason": "media"}}]}}
            MACRO_ACTIONS = {{"acme_widget.ping": {{"handler": "ping"}}}}

            async def ping(self):
                pass
    """)
    assert validate_manifest(imported(text), "linux_x64") == (True, "")
    assert first_problem(read_source(text)) is None


def test_an_unread_value_is_never_refused():
    text = _src("""
        class AcmeWidgetPlugin:
            PLUGIN_INFO = {"id": PLUGIN_ID, "name": "n", "version": "1", "author": "a",
                           "description": "d", "category": CATEGORY, "license": LICENSE,
                           "capabilities": CAPS, "guest_alias": "acme",
                           "network_ports": PORTS, "platforms": PLATFORMS}
            CONFIG_SCHEMA = SCHEMA
            CONFIG_SECRETS = {"host": {"id": "x", "fields": ["y"]}}
            MACRO_ACTIONS = {"wrong_prefix.ping": {"handler": "ping"}}
            SCRIPT_API = {"register": {"handler": "ping"}}
    """)
    read = read_source(text)
    assert read.info["id"] is UNREAD
    assert first_problem(read) is None
    assert first_problem(read, host=lambda info: None) is None


def test_the_class_and_the_host_are_asked_only_when_given(imported):
    """A handler that is not on the class, and a platform the plugin does not
    run on, are refused only by a caller that has a class and a host."""
    text = _src(f"""
        class AcmeWidgetPlugin:
            PLUGIN_INFO = {{{_INFO}, "platforms": ["win_x64"]}}
            MACRO_ACTIONS = {{"acme_widget.ping": {{"handler": "missing"}}}}
    """)
    cls = imported(text)
    read = read_source(text)
    assert first_problem(read) is None
    assert validate_manifest(cls, "linux_x64") == (
        False, "Plugin not compatible with current platform 'linux_x64'. Supported: ['win_x64']")
    assert validate_manifest(cls, "win_x64") == (
        False, "MACRO_ACTIONS invalid: action 'acme_widget.ping' handler 'missing' not found on plugin class")


def test_a_merge_or_a_key_that_cannot_be_read_leaves_the_required_fields_unjudged():
    """What a ``**`` the reader cannot evaluate holds is not known, so a
    required field it may hold is not called missing."""
    text = _src("""
        from acme_shared import BASE_INFO

        class AcmeWidgetPlugin:
            PLUGIN_INFO = {**BASE_INFO, "id": "acme_widget", KEY: "x"}
    """)
    read = read_source(text)
    assert UNREAD in read.info
    assert read.unread == ("PLUGIN_INFO[**]", "PLUGIN_INFO[<computed key>]")
    assert first_problem(read) is None
    complete = read_source('class A:\n    PLUGIN_INFO = {"id": "acme_widget"}\n')
    assert first_problem(complete).startswith("Missing required fields: ")


# --- a value of the wrong type ------------------------------------------------------

_BASE_INFO = {
    "id": "acme_widget", "name": "Acme Widget", "version": "1.0.0", "author": "Acme",
    "description": "A widget.", "category": "utility", "license": "MIT",
}


@pytest.mark.parametrize("over, message", [
    ({"license": 5}, "License '5' is not MIT-compatible"),
    ({"license": None}, "License 'None' is not MIT-compatible"),
    ({"category": ["utility"]},
     "Invalid category '['utility']', must be one of "
     "['control_surface', 'integration', 'sensor', 'utility']"),
    ({"capabilities": 5}, "capabilities must be a list"),
    ({"capabilities": "state_read"}, "capabilities must be a list"),
    ({"capabilities": ["state_read", {"a": 1}]}, "Unknown capabilities: [\"{'a': 1}\"]"),
    ({"guest_alias": "acme", "capabilities": 7}, "capabilities must be a list"),
    ({"platforms": 5}, "Plugin not compatible with current platform 'linux_x64'. Supported: 5"),
    ({"platforms": "linux_x64"},
     "Plugin not compatible with current platform 'linux_x64'. Supported: linux_x64"),
])
def test_a_value_of_the_wrong_type_is_refused_rather_than_raising(over, message):
    cls = type("AcmeWidgetPlugin", (), {"PLUGIN_INFO": {**_BASE_INFO, **over}})
    assert validate_manifest(cls, "linux_x64") == (False, message)


def test_platforms_that_are_not_a_list_name_no_platform():
    from openavc.core.plugin_declarations import platform_supported

    assert platform_supported({"platforms": 5}, "linux_x64") is False
    assert platform_supported({"platforms": "all"}, "linux_x64") is False
    assert platform_supported({"platforms": ("all",)}, "linux_x64") is True


async def _handler(self):
    return None


@pytest.mark.parametrize("over, attrs, answer", [
    ({"min_openavc_version": 1.0}, {}, (True, "")),
    ({"min_openavc_version": ["99.0.0"]}, {}, (True, "")),
    ({}, {"MACRO_ACTIONS": {"acme_widget.go": {"handler": "go", "params": [{"key": "k", "type": ["text"]}]}}},
     (False, "MACRO_ACTIONS invalid: action 'acme_widget.go' param 'k' has invalid type "
             "'['text']' (allowed: ['boolean', 'device_ref', 'float', 'integer', 'macro_ref', "
             "'select', 'state_key', 'text'])")),
    ({"id": 5}, {}, (False, "Invalid plugin ID '5': must be lowercase letters, numbers, and underscores only")),
    ({"id": None}, {},
     (False, "Invalid plugin ID 'None': must be lowercase letters, numbers, and underscores only")),
    ({"id": ["acme_widget"]}, {},
     (False, "Invalid plugin ID '['acme_widget']': must be lowercase letters, numbers, and underscores only")),
    ({"id": "1st"}, {"SCRIPT_API": {"go": {"handler": "go"}}},
     (False, "SCRIPT_API invalid: plugin id '1st' is not a valid Python identifier — "
             "SCRIPT_API can't be exposed under openavc.plugins")),
])
def test_a_declaration_of_the_wrong_type_is_answered_rather_than_raising(over, attrs, answer):
    """A minimum version that is not text cannot be compared, which passes, as
    one that does not parse does."""
    cls = type("AcmeWidgetPlugin", (), {"PLUGIN_INFO": {**_BASE_INFO, **over}, "go": _handler, **attrs})
    assert validate_manifest(cls, "linux_x64") == answer


class _RaisingMeta(type):
    @property
    def CONFIG_SCHEMA(cls):
        raise RuntimeError("acme schema broke")


async def test_a_check_that_raises_refuses_that_plugin_and_the_next_still_starts():
    """Reading a declaration can run the plugin's own code (a property on its
    metaclass): a raise there refuses that plugin, with the reason on its card,
    and the plugins after it in the project still start."""
    from unittest.mock import MagicMock

    from openavc.core.event_bus import EventBus
    from openavc.core.plugin_loader import _PLUGIN_CLASS_REGISTRY, PluginLoader, register_plugin_class
    from openavc.core.state_store import StateStore

    async def start(self, api):
        return None

    async def stop(self):
        return None

    broken = _RaisingMeta("AcmeBroken", (), {
        "PLUGIN_INFO": {**_BASE_INFO, "id": "acme_broken"}, "start": start, "stop": stop,
    })
    fine = type("AcmeFine", (), {
        "PLUGIN_INFO": {**_BASE_INFO, "id": "acme_fine"}, "start": start, "stop": stop,
    })
    saved = dict(_PLUGIN_CLASS_REGISTRY)
    try:
        register_plugin_class(broken)
        register_plugin_class(fine)
        state = StateStore()
        events = EventBus()
        state.set_event_bus(events)
        loader = PluginLoader(state, events, MagicMock(), MagicMock())
        await loader.start_plugins({
            "acme_broken": {"enabled": True, "config": {}},
            "acme_fine": {"enabled": True, "config": {}},
        })
        assert loader.is_running("acme_fine")
        assert not loader.is_running("acme_broken")
        assert loader._status["acme_broken"] == "error"
        assert loader._errors["acme_broken"] == (
            "Invalid manifest: could not be read (RuntimeError: acme schema broke)")
        await loader.stop_all()
    finally:
        _PLUGIN_CLASS_REGISTRY.clear()
        _PLUGIN_CLASS_REGISTRY.update(saved)


# --- the plugin id ------------------------------------------------------------------

#: Each id with whether it is a plugin id: lowercase letters, digits and
#: underscores, nothing else, a digit first included.
_IDS = [
    ("acme_widget", True), ("widget2", True), ("a", True), ("1st", True), ("_", True),
    ("acme.widget", False), ("Acme_Widget", False), ("acme-widget", False), ("a b", False),
    ("a/b", False), ("..", False), ("", False), ("acme_widget\n", False), ("acmé", False),
    ("acme٣", False),
]


@pytest.mark.parametrize("plugin_id, ok", _IDS)
def test_every_door_takes_the_same_ids(plugin_id, ok, monkeypatch):
    """The rule, the installer and the manifest check answer one corpus alike
    (the startup scan asks the same rule: test_plugin_loader_pins)."""
    from openavc.core import plugin_installer
    from openavc.core.plugin_declarations import is_plugin_id, plugin_id_problem

    assert is_plugin_id(plugin_id) is ok
    assert (plugin_id_problem(plugin_id) is None) is ok
    try:
        plugin_installer._validate_plugin_id(plugin_id)
        installer_ok = True
    except ValueError as exc:
        installer_ok = False
        assert str(exc) == plugin_id_problem(plugin_id)
    assert installer_ok is ok
    cls = type("AcmeWidgetPlugin", (), {"PLUGIN_INFO": {**_BASE_INFO, "id": plugin_id}})
    valid, message = validate_manifest(cls, "linux_x64")
    if plugin_id == "":
        assert message == plugin_id_problem("")
    assert valid is ok


@pytest.mark.parametrize("value", [5, None, 1.5, ["acme"], {"id": "acme"}, b"acme"])
def test_an_id_that_is_not_text_is_not_a_plugin_id(value):
    from openavc.core.plugin_declarations import is_plugin_id, plugin_id_problem

    assert is_plugin_id(value) is False
    assert plugin_id_problem(value) == (
        f"Invalid plugin ID '{value}': must be lowercase letters, numbers, and underscores only")


def test_an_unread_id_is_not_judged_and_a_missing_one_is_the_required_rows():
    from openavc.core.plugin_declarations import problems

    assert list(problems(Declared({**_BASE_INFO, "id": UNREAD}))) == []
    info = dict(_BASE_INFO)
    info.pop("id")
    assert list(problems(Declared(info))) == ["Missing required fields: ['id']"]


# --- every refusal at once ------------------------------------------------------------


def test_problems_gives_each_rows_refusal_in_order_and_the_first_is_first_problems():
    read = Declared(
        {**_BASE_INFO, "license": "GPL", "capabilities": 5, "guest_alias": "api",
         "network_ports": [{"port": 22, "reason": "ssh"}]},
        config_schema=[],
        config_secrets={"[b]": {"id": "x", "fields": ["y"]}},
        macro_actions={"other.ping": {"handler": "ping"}},
        script_api={"register": {"handler": "ping"}},
        extensions={"views": [{"label": "no id"}]},
    )
    from openavc.core.plugin_declarations import problems

    found = list(problems(read))
    assert found == [
        "License 'GPL' is not MIT-compatible",
        "capabilities must be a list",
        "guest_alias 'api' is a reserved platform path",
        "network_ports may not request port 22 -- it belongs to the host, not to a plugin",
        "CONFIG_SCHEMA must be a dict",
        "CONFIG_SECRETS key '[b]' must be a config key",
        "MACRO_ACTIONS invalid: action 'other.ping' must be prefixed with the plugin id ('acme_widget.')",
        "SCRIPT_API invalid: method name 'register' is reserved",
        "EXTENSIONS invalid: views[0] missing 'id' (string)",
    ]
    assert first_problem(read) == found[0]
    assert list(problems(Declared(None))) == ["Missing or invalid PLUGIN_INFO dict"]
    assert list(problems(Declared({}))) == [
        "Missing required fields: ['author', 'category', 'description', 'id', 'license', 'name', 'version']"]
    assert list(problems(Declared(dict(_BASE_INFO)))) == []


def test_a_capability_list_that_is_not_a_list_is_refused_once():
    """Its own row refuses it; the rows that ask whether a capability is
    declared do not raise on it, or refuse it again."""
    from openavc.core.plugin_declarations import problems

    read = Declared({**_BASE_INFO, "capabilities": 5, "guest_alias": "acme",
                     "network_ports": [{"port": 9000, "reason": "media"}]})
    assert list(problems(read)) == ["capabilities must be a list"]


# --- the platform ids a plugin lists ---------------------------------------------------


@pytest.mark.parametrize("platforms, message", [
    (["all"], None),
    (["win_x64", "linux_x64", "linux_arm64"], None),
    (("linux_arm64",), None),
    ("all", "platforms must be a list"),
    (5, "platforms must be a list"),
    ([], "platforms lists no platform; list 'all' or each platform the plugin runs on"),
    (["linux_x64", "macos", 3],
     "Unknown platforms: ['3', 'macos']; each must be one of "
     "['all', 'linux_arm64', 'linux_x64', 'win_x64']"),
])
def test_platforms_problem(platforms, message):
    from openavc.core.plugin_declarations import platforms_problem

    assert platforms_problem({**_BASE_INFO, "platforms": platforms}) == message


def test_no_platforms_or_unread_ones_are_no_problem():
    from openavc.core.plugin_declarations import platforms_problem

    assert platforms_problem(dict(_BASE_INFO)) is None
    assert platforms_problem({**_BASE_INFO, "platforms": UNREAD}) is None
    assert platforms_problem({**_BASE_INFO, UNREAD: UNREAD}) is None


def test_the_loader_starts_a_plugin_that_also_lists_a_platform_it_does_not_know():
    """A platform id a later release adds must not stop the plugin starting
    on this one, on a machine the plugin does list."""
    cls = type("AcmeWidgetPlugin", (), {"PLUGIN_INFO": {**_BASE_INFO, "platforms": ["linux_x64", "acme_os"]}})
    assert validate_manifest(cls, "linux_x64") == (True, "")


# --- which file is the plugin's --------------------------------------------------------


def _folder(tmp_path, files):
    folder = tmp_path / "acme_widget"
    folder.mkdir()
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


_PLUGIN_CLASS = f"class AcmeWidgetPlugin:\n    PLUGIN_INFO = {{{_INFO}}}\n"


def test_candidate_files_in_the_order_the_loader_tries_them(tmp_path):
    from openavc.core.plugin_source import candidate_files

    folder = _folder(tmp_path, {name: "" for name in (
        "zeta.py", "acme_widget_plugin.py", "_private.py", "__init__.py", "alpha.py", "notes.txt")})
    assert [p.name for p in candidate_files(folder)] == [
        "__init__.py", "acme_widget_plugin.py", "alpha.py", "zeta.py"]


def test_read_folder_reads_the_file_the_loader_imports(tmp_path):
    """The loader's first file holding a plugin class is the one read: a
    helper that holds none is passed over, and a later plugin module is never
    reached."""
    from openavc.core.plugin_source import read_folder

    other = _PLUGIN_CLASS.replace("Acme Widget", "Other Widget")
    folder = _folder(tmp_path, {
        "__init__.py": "HELPER = 1\n",
        "acme_widget_plugin.py": _PLUGIN_CLASS,
        "alpha_plugin.py": other,
    })
    read = read_folder(folder)
    assert read.path == folder / "acme_widget_plugin.py"
    assert read.declared.info["name"] == "Acme Widget"
    assert read.passed_over == ()


def test_read_folder_passes_over_a_file_that_does_not_read_and_says_why(tmp_path):
    from openavc.core.plugin_source import read_folder

    folder = _folder(tmp_path, {"acme_widget_plugin.py": "class A(:\n", "beta.py": _PLUGIN_CLASS})
    (folder / "alpha.py").write_bytes(b"\xff\xfe")
    read = read_folder(folder)
    assert read.path == folder / "beta.py"
    assert [(p.name, why.split(":")[0]) for p, why in read.passed_over] == [
        ("acme_widget_plugin.py", "SyntaxError"),
        ("alpha.py", "UnicodeDecodeError"),
    ]


def test_read_folder_with_no_plugin_class(tmp_path):
    from openavc.core.plugin_source import read_folder

    folder = _folder(tmp_path, {"acme_widget_plugin.py": 'class A:\n    PLUGIN_INFO = {}\n'})
    read = read_folder(folder)
    assert (read.path, read.declared, read.passed_over) == (None, None, ())


#: A module whose PLUGIN_INFO reads a value through 5000 names: Python runs it,
#: and following the names recurses past the interpreter's limit.
_NAME_CHAIN = (
    "A0 = 1\n" + "".join(f"A{i} = A{i - 1}\n" for i in range(1, 5000))
    + f"class AcmeWidgetPlugin:\n    PLUGIN_INFO = {{{_INFO}, \"x\": A4999}}\n"
)


@pytest.mark.parametrize("text, errors", [
    ("x = " + "-" * 100000 + "1\n", ("MemoryError", "RecursionError")),
    (_NAME_CHAIN, ("RecursionError",)),
], ids=["deep_expression", "long_name_chain"])
def test_a_source_too_deep_to_read_is_passed_over_rather_than_raising(tmp_path, text, errors):
    from openavc.core.plugin_source import read_folder

    folder = _folder(tmp_path, {"acme_widget_plugin.py": text, "beta.py": _PLUGIN_CLASS})
    read = read_folder(folder)
    assert read.path == folder / "beta.py"
    (passed,) = read.passed_over
    assert passed[0].name == "acme_widget_plugin.py"
    assert passed[1].split(":")[0] in errors
