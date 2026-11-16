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
from openavc.core.plugin_source import read_source

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
    ('"license": "GPL-3.0"', ""),
    ('"category": "bogus"', ""),
    ('"capabilities": ["fly"]', ""),
    ('"guest_alias": "api", "capabilities": ["guest_endpoints"]', ""),
    ('"network_ports": [{"port": 22, "reason": "ssh"}], "capabilities": ["network_listen"]', ""),
    ("", "CONFIG_SCHEMA = []"),
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
    ({"id": 5}, {"SCRIPT_API": {"go": {"handler": "go"}}},
     (False, "SCRIPT_API invalid: plugin id '5' is not a valid Python identifier — "
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
