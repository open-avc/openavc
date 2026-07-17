"""Tests for driver loader (.avcdriver YAML files)."""

import sys
import time
from pathlib import Path

import yaml

from server.utils.regex_safety import _regex_search_exceeds
from server.drivers.driver_loader import (
    DRIVER_EXTENSION,
    delete_driver_definition,
    find_driver_file_by_id,
    is_builtin_driver,
    list_driver_definitions,
    list_python_drivers,
    load_driver_file,
    load_driver_files,
    load_python_driver_file,
    reload_python_driver,
    save_driver_definition,
    validate_driver_definition,
)


VALID_DEFINITION = {
    "id": "test_loader_driver",
    "name": "Loader Test Driver",
    "transport": "tcp",
    "discovery": {"oui": ["aa:bb:cc"]},
    "commands": {
        "power_on": {"label": "Power On", "string": "PON\r", "params": {}},
    },
    "responses": [
        {"pattern": r"PWR=(\d)", "mappings": [{"group": 1, "state": "power"}]},
    ],
    "state_variables": {
        "power": {"type": "string", "label": "Power"},
    },
}


def _write_avcdriver(path: Path, data: dict) -> Path:
    """Helper to write a .avcdriver YAML file."""
    path.write_text(yaml.dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_validate_valid_definition():
    errors = validate_driver_definition(VALID_DEFINITION)
    assert errors == []


def test_validate_missing_required():
    errors = validate_driver_definition({"name": "X"})
    assert any("id" in e for e in errors)
    assert any("transport" in e for e in errors)


def test_validate_accepts_missing_discovery_block_with_warning():
    """A driver with no signals at all loads (the matcher silently
    ignores it) but the loader logs a warning. We don't reject the
    driver — community contributors can ship a placeholder and add
    discovery hints in a follow-up.
    """
    errors = validate_driver_definition({
        "id": "no_discovery",
        "name": "No Discovery",
        "transport": "tcp",
        "commands": {"power_on": {"string": "X\r"}},
    })
    assert errors == []


def test_validate_accepts_hint_only_discovery():
    errors = validate_driver_definition({
        "id": "hint_only_widget",
        "name": "Hint Only Widget",
        "transport": "tcp",
        "discovery": {"oui": ["aa:bb:cc"]},
        "commands": {"power_on": {"string": "X\r"}},
    })
    assert errors == []


def test_validate_accepts_fingerprint_discovery():
    errors = validate_driver_definition({
        "id": "fingerprint_driver",
        "name": "Fingerprint",
        "transport": "tcp",
        "discovery": {
            "tcp_probe": {
                "port": 4321, "send_ascii": "Q\r", "expect": "RESP",
            },
        },
        "commands": {"power_on": {"string": "X\r"}},
    })
    assert errors == []


def test_validate_skips_generic_templates():
    """generic_* templates are exempt from the discovery requirement."""
    errors = validate_driver_definition({
        "id": "generic_anything",
        "name": "Generic",
        "transport": "tcp",
    })
    # No discovery-block error.
    assert not any("discovery:" in e for e in errors)


def test_validate_bad_transport():
    errors = validate_driver_definition({
        "id": "x", "name": "x", "transport": "foobar",
    })
    assert any("transport" in e for e in errors)


def test_validate_bad_regex():
    defn = {**VALID_DEFINITION, "responses": [{"pattern": "[bad"}]}
    errors = validate_driver_definition(defn)
    assert any("regex" in e.lower() or "invalid" in e.lower() for e in errors)


def test_validate_json_response_needs_no_pattern():
    # A json: true rule parses the whole reply body — it carries no regex, so
    # the pattern requirement must not fire (novastar_h_series was the first
    # catalog driver to hit this).
    defn = {
        **VALID_DEFINITION,
        "responses": [
            {"json": True, "set": {"power": "status.power"}},
            {"json": True, "mappings": [{"state": "power", "key": "power"}]},
        ],
    }
    assert validate_driver_definition(defn) == []


def test_validate_json_response_requires_field_map():
    defn = {**VALID_DEFINITION, "responses": [{"json": True}]}
    errors = validate_driver_definition(defn)
    assert any("json response needs" in e for e in errors)


def test_validate_json_response_rejects_child_set():
    defn = {
        **VALID_DEFINITION,
        "responses": [
            {"json": True, "set": {"power": "power"},
             "child_set": [{"type": "output", "id": "$1", "state": {}}]},
        ],
    }
    errors = validate_driver_definition(defn)
    assert any("child_set is not supported on json responses" in e for e in errors)


# --- frame_parser validation (H-062: reject at load, not at connect) ---


def test_validate_frame_parser_valid_header_sizes():
    for size in (1, 2, 4):
        defn = {**VALID_DEFINITION,
                "frame_parser": {"type": "length_prefix", "header_size": size}}
        assert validate_driver_definition(defn) == []


def test_validate_frame_parser_bad_header_size():
    # The runtime LengthPrefixFrameParser raises on anything but 1/2/4, which
    # would crash connect() — catch it at load instead.
    for bad in (3, 8, 0):
        defn = {**VALID_DEFINITION,
                "frame_parser": {"type": "length_prefix", "header_size": bad}}
        errors = validate_driver_definition(defn)
        assert any("header_size" in e for e in errors), bad


def test_validate_frame_parser_negative_offset_ok():
    # A negative header_offset is the documented case (length field includes
    # the header) — it must validate, not be rejected.
    defn = {**VALID_DEFINITION,
            "frame_parser": {"type": "length_prefix", "header_size": 2,
                             "header_offset": -2}}
    assert validate_driver_definition(defn) == []


def test_validate_frame_parser_bad_offset_type():
    defn = {**VALID_DEFINITION,
            "frame_parser": {"type": "length_prefix", "header_offset": "two"}}
    assert any("header_offset" in e for e in validate_driver_definition(defn))


def test_validate_frame_parser_fixed_length():
    ok = {**VALID_DEFINITION, "frame_parser": {"type": "fixed_length", "length": 8}}
    assert validate_driver_definition(ok) == []
    bad = {**VALID_DEFINITION, "frame_parser": {"type": "fixed_length", "length": 0}}
    assert any("length" in e for e in validate_driver_definition(bad))


def test_validate_frame_parser_unknown_type():
    defn = {**VALID_DEFINITION, "frame_parser": {"type": "crc16"}}
    assert any("crc16" in e or "type" in e for e in validate_driver_definition(defn))


def test_load_driver_file_valid(tmp_path):
    filepath = tmp_path / "test.avcdriver"
    _write_avcdriver(filepath, VALID_DEFINITION)
    result = load_driver_file(filepath)
    assert result is not None
    assert result["id"] == "test_loader_driver"


def test_load_driver_file_invalid_yaml(tmp_path):
    filepath = tmp_path / "bad.avcdriver"
    filepath.write_text("{{{{not yaml!!", encoding="utf-8")
    result = load_driver_file(filepath)
    assert result is None


def test_find_driver_file_by_id_found(tmp_path):
    filepath = tmp_path / "test.avcdriver"
    _write_avcdriver(filepath, VALID_DEFINITION)
    found = find_driver_file_by_id([tmp_path], "test_loader_driver")
    assert found == filepath


def test_find_driver_file_by_id_not_found(tmp_path):
    _write_avcdriver(tmp_path / "test.avcdriver", VALID_DEFINITION)
    assert find_driver_file_by_id([tmp_path], "no_such_driver") is None
    # A missing directory is skipped, not an error.
    assert find_driver_file_by_id([tmp_path / "nope"], "test_loader_driver") is None


def test_find_driver_file_by_id_matches_declared_id_not_stem(tmp_path):
    # An upload keeps its original filename, so the stem need not match the id.
    filepath = tmp_path / "renamed-on-upload.avcdriver"
    _write_avcdriver(filepath, VALID_DEFINITION)
    found = find_driver_file_by_id([tmp_path], "test_loader_driver")
    assert found == filepath


def test_find_driver_file_by_id_first_directory_wins(tmp_path):
    # Earlier directories take precedence (built-in dirs are scanned first).
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    _write_avcdriver(builtin / "d.avcdriver", VALID_DEFINITION)
    _write_avcdriver(user / "d.avcdriver", VALID_DEFINITION)
    found = find_driver_file_by_id([builtin, user], "test_loader_driver")
    assert found == builtin / "d.avcdriver"


def test_load_driver_file_missing_fields(tmp_path):
    filepath = tmp_path / "incomplete.avcdriver"
    _write_avcdriver(filepath, {"name": "Missing ID"})
    result = load_driver_file(filepath)
    assert result is None


def test_save_and_load_roundtrip(tmp_path):
    saved_path = save_driver_definition(VALID_DEFINITION, tmp_path)
    assert saved_path.exists()
    assert saved_path.suffix == DRIVER_EXTENSION
    loaded = load_driver_file(saved_path)
    assert loaded is not None
    assert loaded["id"] == VALID_DEFINITION["id"]


def test_save_uses_avcdriver_extension(tmp_path):
    saved_path = save_driver_definition(VALID_DEFINITION, tmp_path)
    assert saved_path.name == "test_loader_driver.avcdriver"


def test_save_writes_yaml(tmp_path):
    saved_path = save_driver_definition(VALID_DEFINITION, tmp_path)
    text = saved_path.read_text(encoding="utf-8")
    # YAML doesn't have braces/brackets at the start like JSON
    assert not text.startswith("{")
    data = yaml.safe_load(text)
    assert data["id"] == "test_loader_driver"


def test_list_driver_definitions(tmp_path):
    save_driver_definition(VALID_DEFINITION, tmp_path)

    defn2 = {**VALID_DEFINITION, "id": "second_driver", "name": "Second"}
    save_driver_definition(defn2, tmp_path)

    result = list_driver_definitions([tmp_path])
    ids = [d["id"] for d in result]
    assert "test_loader_driver" in ids
    assert "second_driver" in ids


def test_list_ignores_nonexistent_dir():
    result = list_driver_definitions([Path("/nonexistent/dir")])
    assert result == []


def test_delete_driver_definition(tmp_path):
    save_driver_definition(VALID_DEFINITION, tmp_path)
    assert delete_driver_definition("test_loader_driver", [tmp_path]) is True
    assert list_driver_definitions([tmp_path]) == []


def test_delete_nonexistent():
    assert delete_driver_definition("no_such_id", []) is False


def test_load_driver_files_registers(tmp_path):
    """load_driver_files creates and registers driver classes."""
    save_driver_definition(VALID_DEFINITION, tmp_path)
    count = load_driver_files([tmp_path])
    assert count >= 1

    # Verify it's in the registry
    from server.core.device_manager import get_driver_registry
    registry = get_driver_registry()
    ids = [d["id"] for d in registry]
    assert "test_loader_driver" in ids


def test_list_python_drivers_skips_companions(tmp_path):
    """``list_python_drivers`` must not list ``_discovery.py`` /
    ``_sim.py`` companions or underscore-prefixed helpers as drivers.

    Regression test for the bug where YAML drivers' sibling discovery
    companions appeared in the Code tab tree and the Installed Drivers
    panel as if they were standalone Python drivers — clicking them
    triggered a fetch on a stem that has no driver class behind it.
    """
    # Real driver — has a class with DRIVER_INFO.
    (tmp_path / "real_driver.py").write_text(
        '"""A real driver."""\n'
        "from server.drivers.base import BaseDriver\n"
        "class RealDriver(BaseDriver):\n"
        '    DRIVER_INFO = {"id": "real_driver", "name": "Real Driver"}\n',
        encoding="utf-8",
    )

    # Discovery companion — has only an async probe(), no driver class.
    (tmp_path / "real_driver_discovery.py").write_text(
        "async def probe(ctx):\n"
        "    pass\n",
        encoding="utf-8",
    )

    # Python simulator companion — has a Simulator class, no driver.
    (tmp_path / "real_driver_sim.py").write_text(
        "class Simulator:\n"
        "    pass\n",
        encoding="utf-8",
    )

    # Underscore-prefixed helper — already filtered, kept for parity.
    (tmp_path / "_helpers.py").write_text(
        "X = 1\n",
        encoding="utf-8",
    )

    listed = list_python_drivers([tmp_path])
    listed_ids = [d["id"] for d in listed]

    assert "real_driver" in listed_ids
    assert "real_driver_discovery" not in listed_ids
    assert "real_driver_sim" not in listed_ids
    assert "_helpers" not in listed_ids
    assert "helpers" not in listed_ids


# --- H-050: malformed driver YAML must be reported, never crash the pass ---


def test_validate_non_mapping_definition():
    """A YAML file that parses to a non-mapping is reported, not raised."""
    assert validate_driver_definition([1, 2, 3]) == ["Driver definition must be a mapping"]
    assert validate_driver_definition("just a string") == ["Driver definition must be a mapping"]


def test_validate_commands_as_list_does_not_raise():
    """`commands` as a list (the original crash vector) is a reported error."""
    defn = {**VALID_DEFINITION, "commands": ["power_on", "power_off"]}
    errors = validate_driver_definition(defn)  # must not raise AttributeError
    assert any("commands" in e for e in errors)


def test_validate_state_variables_as_list_does_not_raise():
    defn = {**VALID_DEFINITION, "state_variables": ["power"]}
    errors = validate_driver_definition(defn)
    assert any("state_variables" in e for e in errors)


def test_validate_state_variable_unit_and_control():
    """`unit` (picker range prompt) and `control` (picker ordering) are
    optional but typed: unit must be a string, control a boolean."""
    good = {
        **VALID_DEFINITION,
        "state_variables": {
            "gain_db": {
                "type": "number", "label": "Gain (dB)",
                "min": -80.0, "max": 10.0, "unit": "dB", "control": True,
            },
        },
    }
    assert validate_driver_definition(good) == []

    bad_unit = {
        **VALID_DEFINITION,
        "state_variables": {
            "gain_db": {"type": "number", "label": "Gain", "unit": 5},
        },
    }
    errors = validate_driver_definition(bad_unit)
    assert any("unit must be a string" in e for e in errors)

    bad_control = {
        **VALID_DEFINITION,
        "state_variables": {
            "gain_db": {"type": "number", "label": "Gain", "control": "yes"},
        },
    }
    errors = validate_driver_definition(bad_control)
    assert any("control must be true or false" in e for e in errors)


def test_validate_state_variable_cloud_priority():
    """`cloud_priority` selects the cloud forwarding tier; a typo would
    silently fall back to the default cadence."""
    for value in ("low", "high"):
        good = {
            **VALID_DEFINITION,
            "state_variables": {
                "power": {
                    "type": "string", "label": "Power",
                    "cloud_priority": value,
                },
            },
        }
        assert validate_driver_definition(good) == []

    bad = {
        **VALID_DEFINITION,
        "state_variables": {
            "power": {
                "type": "string", "label": "Power",
                "cloud_priority": "hgih",
            },
        },
    }
    errors = validate_driver_definition(bad)
    assert any(
        "cloud_priority must be 'low' or 'high'" in e for e in errors
    )


def test_validate_polling_non_mapping():
    defn = {**VALID_DEFINITION, "polling": ["PWR?\r"]}
    errors = validate_driver_definition(defn)
    assert any("polling: must be a mapping" in e for e in errors)


def test_validate_polling_interval_rejected():
    """The runtime reads only default_config.poll_interval — an interval
    inside the polling block would silently do nothing."""
    defn = {
        **VALID_DEFINITION,
        "polling": {"interval": 10, "queries": ["PWR?\r"]},
    }
    errors = validate_driver_definition(defn)
    assert any(
        "polling.interval is not read by the runtime" in e for e in errors
    )

    ok = {**VALID_DEFINITION, "polling": {"queries": ["PWR?\r"]}}
    assert validate_driver_definition(ok) == []


def test_validate_config_derived_names_accepted():
    """config_derived keys resolve into config at runtime, so push
    templates, `when:` gates, and instances *_from may reference them."""
    defn = {
        **VALID_DEFINITION,
        "default_config": {"node": "3", "zones": "1,2", "gate": ""},
        "config_derived": {
            "mc_group": "239.1.2.{node}",
            "poll_gate": "{gate}",
            "zone_list": "{zones}",
        },
        "push": {"type": "multicast", "group": "{mc_group}", "port": 9131},
        "polling": {"queries": [{"send": "STAT?\r", "when": "poll_gate"}]},
        "child_entity_types": {
            "zone": {
                "label": "Zone",
                "state_variables": {},
                "instances": {"ids_from": "zone_list"},
            },
        },
    }
    assert validate_driver_definition(defn) == []

    # Without the config_derived block the same references are typos.
    bad = {
        **VALID_DEFINITION,
        "push": {"type": "multicast", "group": "{mc_group}", "port": 9131},
    }
    errors = validate_driver_definition(bad)
    assert any("'mc_group'" in e and "is not declared" in e for e in errors)


def test_validate_child_state_variable_unit_and_control():
    """Child-entity state variables carry the same typed unit/control."""
    good = {
        **VALID_DEFINITION,
        "child_entity_types": {
            "zone": {
                "label": "Zone",
                "state_variables": {
                    "fader_db": {
                        "type": "number", "label": "Fader (dB)",
                        "min": -80.0, "max": 10.0,
                        "unit": "dB", "control": True,
                    },
                },
            },
        },
    }
    assert validate_driver_definition(good) == []

    bad = {
        **VALID_DEFINITION,
        "child_entity_types": {
            "zone": {
                "label": "Zone",
                "state_variables": {
                    "fader_db": {"type": "number", "unit": 1, "control": "on"},
                },
            },
        },
    }
    errors = validate_driver_definition(bad)
    assert any("zone.state_variables.fader_db: unit must be a string" in e for e in errors)
    assert any(
        "zone.state_variables.fader_db: control must be true or false" in e
        for e in errors
    )


def test_validate_responses_as_dict_does_not_raise():
    defn = {**VALID_DEFINITION, "responses": {"not": "a list"}}
    errors = validate_driver_definition(defn)
    assert any("responses" in e for e in errors)


def test_validate_non_dict_response_entry():
    defn = {**VALID_DEFINITION, "responses": ["raw string entry"]}
    errors = validate_driver_definition(defn)
    assert any("must be a mapping" in e for e in errors)


def test_validate_non_string_pattern():
    defn = {**VALID_DEFINITION, "responses": [{"pattern": 1234}]}
    errors = validate_driver_definition(defn)
    assert any("must be a string" in e for e in errors)


def test_validate_child_entity_type_glob_metachar_rejected():
    """A child type name carrying a glob metachar (* ? [) becomes a
    device.<id>.<child_type>.* key segment and breaks fnmatch dispatch."""
    defn = {
        **VALID_DEFINITION,
        "child_entity_types": {"enc[oder]": {"state_variables": {}}},
    }
    errors = validate_driver_definition(defn)
    assert any("glob metacharacters" in e and "enc[oder]" in e for e in errors)


def test_validate_child_entity_type_dot_rejected():
    """A dotted child type name corrupts the state-key structure."""
    defn = {
        **VALID_DEFINITION,
        "child_entity_types": {"a.b": {"state_variables": {}}},
    }
    errors = validate_driver_definition(defn)
    assert any("must not contain dots" in e for e in errors)


def test_validate_child_entity_type_normal_name_ok():
    defn = {
        **VALID_DEFINITION,
        "child_entity_types": {"encoder": {"state_variables": {}}},
    }
    assert validate_driver_definition(defn) == []


def test_validate_child_entity_types_non_mapping():
    defn = {**VALID_DEFINITION, "child_entity_types": ["encoder"]}
    errors = validate_driver_definition(defn)
    assert any("child_entity_types: must be a mapping" in e for e in errors)


def test_bad_driver_file_does_not_abort_the_pass(tmp_path):
    """A single malformed .avcdriver must not stop other drivers loading.

    Regression for the bug where a non-dict `commands`/`responses`/etc. raised
    an uncaught AttributeError that aborted load_driver_files, so every
    alphabetically-later good driver silently failed to load too.
    """
    # `a_bad` sorts before `z_good`, so the crash (if any) happens first.
    _write_avcdriver(
        tmp_path / "a_bad.avcdriver",
        {"id": "a_bad", "name": "Bad", "transport": "tcp", "commands": ["nope"]},
    )
    good = {**VALID_DEFINITION, "id": "z_good", "name": "Good"}
    _write_avcdriver(tmp_path / "z_good.avcdriver", good)

    count = load_driver_files([tmp_path])  # must not raise

    from server.core.device_manager import get_driver_registry, unregister_driver

    try:
        ids = [d["id"] for d in get_driver_registry()]
        assert "z_good" in ids  # the good driver loaded despite the bad one
        assert "a_bad" not in ids  # the bad driver was skipped
        assert count >= 1
    finally:
        unregister_driver("z_good")


# --- H-051: built-in driver files must not be unlinked by the API ---


def test_is_builtin_driver_and_delete_guard(tmp_path, monkeypatch):
    import server.system_config as sc

    builtin_dir = tmp_path / "definitions"
    repo_dir = tmp_path / "repo"
    builtin_dir.mkdir()
    repo_dir.mkdir()
    monkeypatch.setattr(sc, "DRIVER_DEFINITIONS_DIR", builtin_dir)

    save_driver_definition(VALID_DEFINITION, builtin_dir)
    dirs = [builtin_dir, repo_dir]

    # An id served only by the built-in tree is protected.
    assert is_builtin_driver("test_loader_driver", dirs) is True
    assert delete_driver_definition("test_loader_driver", dirs) is False
    assert (builtin_dir / "test_loader_driver.avcdriver").exists()

    # A same-id user copy in the repo overrides it and IS deletable, while the
    # built-in file is left untouched.
    save_driver_definition(VALID_DEFINITION, repo_dir)
    assert is_builtin_driver("test_loader_driver", dirs) is False
    assert delete_driver_definition("test_loader_driver", dirs) is True
    assert not (repo_dir / "test_loader_driver.avcdriver").exists()
    assert (builtin_dir / "test_loader_driver.avcdriver").exists()


# --- M-096: a failed Python-driver import leaves no module behind ---


def test_failed_python_import_clears_sys_modules(tmp_path):
    filepath = tmp_path / "explode.py"
    filepath.write_text("raise RuntimeError('boom at import')\n", encoding="utf-8")
    module_name = "openavc_driver_explode"
    sys.modules.pop(module_name, None)

    result = load_python_driver_file(filepath)

    assert result is None
    assert module_name not in sys.modules  # not left half-initialized


# --- M-097 / L-064: reload Step-3 failure restores the old module + flag ---


def test_reload_step3_failure_preserves_old_driver(tmp_path, monkeypatch):
    src = (
        '"""Reload TOCTOU driver."""\n'
        "from server.drivers.base import BaseDriver\n"
        "class TocTouDriver(BaseDriver):\n"
        '    DRIVER_INFO = {"id": "reload_toctou", "name": "TocTou"}\n'
    )
    filepath = tmp_path / "reload_toctou.py"
    filepath.write_text(src, encoding="utf-8")
    module_name = "openavc_driver_reload_toctou"

    # Pre-load so the old module is resident (as after a real first load).
    sys.modules.pop(module_name, None)
    assert load_python_driver_file(filepath) is not None
    old_module = sys.modules.get(module_name)
    assert old_module is not None

    # Simulate the canonical re-import failing after Step-1 validation passed
    # (a TOCTOU edit/delete of the file between validation and reload).
    import server.drivers.driver_loader as dl

    monkeypatch.setattr(dl, "load_python_driver_file", lambda fp: None)

    try:
        result = reload_python_driver(filepath)
        assert result["status"] == "error"
        assert result["old_driver_preserved"] is True
        # The old module was restored, not left missing.
        assert sys.modules.get(module_name) is old_module
    finally:
        sys.modules.pop(module_name, None)


# --- L-062: list_python_drivers distinguishes unregistered from not-loaded ---


def test_list_python_drivers_reports_imported_but_unregistered(tmp_path, monkeypatch):
    src = (
        "from server.drivers.base import BaseDriver\n"
        "class GhostDriver(BaseDriver):\n"
        '    DRIVER_INFO = {"id": "ghost_driver", "name": "Ghost"}\n'
    )
    filepath = tmp_path / "ghost_driver.py"
    filepath.write_text(src, encoding="utf-8")

    # Module is resident in sys.modules but the id is NOT registered.
    module_name = "openavc_driver_ghost_driver"
    sys.modules[module_name] = object()  # stand-in resident module
    monkeypatch.setattr(
        "server.core.device_manager.is_driver_registered", lambda _id: False
    )
    try:
        listed = list_python_drivers([tmp_path])
        entry = next(d for d in listed if d["id"] == "ghost_driver")
        assert entry["loaded"] is False
        assert entry["load_error"] and "not registered" in entry["load_error"]
    finally:
        sys.modules.pop(module_name, None)


def test_list_python_drivers_reports_not_loaded(tmp_path, monkeypatch):
    src = (
        "from server.drivers.base import BaseDriver\n"
        "class AbsentDriver(BaseDriver):\n"
        '    DRIVER_INFO = {"id": "absent_driver", "name": "Absent"}\n'
    )
    filepath = tmp_path / "absent_driver.py"
    filepath.write_text(src, encoding="utf-8")
    sys.modules.pop("openavc_driver_absent_driver", None)
    monkeypatch.setattr(
        "server.core.device_manager.is_driver_registered", lambda _id: False
    )

    listed = list_python_drivers([tmp_path])
    entry = next(d for d in listed if d["id"] == "absent_driver")
    assert entry["loaded"] is False
    assert entry["load_error"] == "Not loaded"


# --- L-063: the empirical ReDoS probe is time-boxed ---


class _SlowSearch:
    """Stand-in for a compiled regex whose search backtracks for `delay` s."""

    def __init__(self, delay: float):
        self.delay = delay

    def search(self, _s):
        time.sleep(self.delay)
        return None


def test_regex_search_is_time_boxed():
    """A slow search returns control to the caller within the budget.

    The old code measured elapsed time only AFTER `search` returned, so a
    runaway probe blocked the caller for its full duration. The worker-thread
    probe must hand control back within ~budget regardless of how long the
    search runs (a deterministic 0.5s stub stands in for a catastrophic regex).
    """
    t0 = time.monotonic()
    exceeded = _regex_search_exceeds(_SlowSearch(0.5), "x", 0.1)
    elapsed = time.monotonic() - t0

    assert exceeded is True
    # Bounded by the budget (plus scheduling slack), NOT by the slow search.
    assert elapsed < 0.4


def test_regex_search_fast_pattern_within_budget():
    import re

    safe = re.compile(r"PWR=(\d)")
    assert _regex_search_exceeds(safe, "PWR=1", 0.5) is False


# --- driver_id_from_file: declared id without importing the module --------


def test_driver_id_from_file_yaml(tmp_path):
    from server.drivers.driver_loader import driver_id_from_file

    f = tmp_path / "weird_name.avcdriver"
    f.write_text("id: acme_widget\nname: Acme\ntransport: tcp\n", encoding="utf-8")
    assert driver_id_from_file(f) == "acme_widget"


def test_driver_id_from_file_python(tmp_path):
    from server.drivers.driver_loader import driver_id_from_file

    f = tmp_path / "weird_name.py"
    f.write_text(
        "from server.drivers.base import BaseDriver\n\n"
        "class AcmeDriver(BaseDriver):\n"
        "    DRIVER_INFO = {'id': 'acme_widget', 'name': 'Acme'}\n",
        encoding="utf-8",
    )
    assert driver_id_from_file(f) == "acme_widget"


def test_driver_id_from_file_python_does_not_execute_module(tmp_path):
    """The id is read via AST — a .py whose import would raise still yields
    its declared id (we never exec it)."""
    from server.drivers.driver_loader import driver_id_from_file

    f = tmp_path / "boom.py"
    f.write_text(
        "raise RuntimeError('importing me explodes')\n"
        "DRIVER_INFO = {'id': 'safe_id'}\n",
        encoding="utf-8",
    )
    assert driver_id_from_file(f) == "safe_id"


def test_driver_id_from_file_returns_none_on_garbage(tmp_path):
    from server.drivers.driver_loader import driver_id_from_file

    bad_py = tmp_path / "bad.py"
    bad_py.write_text("this is (not valid python\n", encoding="utf-8")
    assert driver_id_from_file(bad_py) is None

    no_info = tmp_path / "noinfo.avcdriver"
    no_info.write_text("name: No Id Here\ntransport: tcp\n", encoding="utf-8")
    assert driver_id_from_file(no_info) is None


# --- Param-picker option providers (§69 Phase 2) ---
#
# A command/action param can declare where its dropdown options come from:
# options_state / options_source (state-key lists) and options_from (cascade
# off a sibling param). validate_driver_definition flags a malformed provider
# so an author sees the typo at load instead of getting a silent free-text box.


def _def_with_command(params: dict) -> dict:
    """A minimal valid driver whose one command carries `params`."""
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "discovery": {"oui": ["aa:bb:cc"]},
        "commands": {
            "do_thing": {"label": "Do Thing", "string": "DO {bank}\r", "params": params},
        },
    }


def test_options_state_and_source_accepted():
    errors = validate_driver_definition(_def_with_command({
        "bank": {"type": "string", "options_state": "snapshot_banks"},
        "voice": {"type": "string", "options_source": "plugin.tts.voices"},
    }))
    assert errors == []


def test_options_state_must_be_nonempty_string():
    errors = validate_driver_definition(_def_with_command({
        "bank": {"type": "string", "options_state": ""},
    }))
    assert any("options_state" in e for e in errors)

    errors = validate_driver_definition(_def_with_command({
        "bank": {"type": "string", "options_source": 5},
    }))
    assert any("options_source" in e for e in errors)


def test_options_from_child_schema_accepted():
    errors = validate_driver_definition(_def_with_command({
        "component": {"type": "child_id", "child_type": "component"},
        "control": {
            "type": "string",
            "options_from": {"param": "component", "source": "child_schema"},
        },
    }))
    assert errors == []


def test_options_from_must_be_mapping():
    errors = validate_driver_definition(_def_with_command({
        "control": {"type": "string", "options_from": "component"},
    }))
    assert any("options_from must be a mapping" in e for e in errors)


def test_options_from_unknown_source_rejected():
    errors = validate_driver_definition(_def_with_command({
        "component": {"type": "child_id", "child_type": "component"},
        "control": {
            "type": "string",
            "options_from": {"param": "component", "source": "made_up"},
        },
    }))
    assert any("options_from.source" in e for e in errors)


def test_options_from_unknown_sibling_rejected():
    errors = validate_driver_definition(_def_with_command({
        "control": {
            "type": "string",
            "options_from": {"param": "nope", "source": "child_schema"},
        },
    }))
    assert any("is not a" in e and "param of this command" in e for e in errors)


def test_options_from_child_schema_sibling_must_be_child_id():
    errors = validate_driver_definition(_def_with_command({
        "component": {"type": "string"},  # not a child_id
        "control": {
            "type": "string",
            "options_from": {"param": "component", "source": "child_schema"},
        },
    }))
    assert any("must be a child_id param" in e for e in errors)


def test_options_from_validated_on_actions():
    driver = _def_with_command({
        "component": {"type": "child_id", "child_type": "component"},
    })
    driver["actions"] = [
        {
            "id": "bad_action",
            "kind": "command",
            "command": "do_thing",
            "label": "Bad",
            "params": {
                "control": {
                    "type": "string",
                    "options_from": {"param": "missing", "source": "child_schema"},
                },
            },
        },
    ]
    errors = validate_driver_definition(driver)
    assert any("actions[0]" in e and "options_from" in e for e in errors)


def _def_with_cascade_chain(value_params: dict) -> dict:
    """A command with component (child_id) -> control (child_schema cascade),
    plus a `value` param under test (merged in)."""
    params = {
        "component": {"type": "child_id", "child_type": "component"},
        "control": {
            "type": "string",
            "options_from": {"param": "component", "source": "child_schema"},
        },
    }
    params.update(value_params)
    return _def_with_command(params)


def test_type_from_accepted():
    errors = validate_driver_definition(_def_with_cascade_chain({
        "value": {"type": "string", "type_from": {"param": "control"}},
    }))
    assert errors == []


def test_type_from_must_be_mapping():
    errors = validate_driver_definition(_def_with_cascade_chain({
        "value": {"type": "string", "type_from": "control"},
    }))
    assert any("type_from must be a mapping" in e for e in errors)


def test_type_from_unknown_sibling_rejected():
    errors = validate_driver_definition(_def_with_cascade_chain({
        "value": {"type": "string", "type_from": {"param": "nope"}},
    }))
    assert any("type_from.param 'nope' is not a" in e for e in errors)


def test_type_from_sibling_must_be_child_schema_cascade():
    # `control` here is plain text (no options_from), so type_from can't chain.
    errors = validate_driver_definition(_def_with_command({
        "control": {"type": "string"},
        "value": {"type": "string", "type_from": {"param": "control"}},
    }))
    assert any("must itself be an options_from child_schema cascade" in e
               for e in errors)


# --- Param free-text validators (§69 Phase 3) ---
#
# A free-text param can declare `pattern` (a regex the value must match) and
# numeric min/max. validate_driver_definition compiles the pattern (rejecting a
# bad or ReDoS-prone one) and sanity-checks the bounds, so the author sees a
# bad declaration at load instead of a surprise at command time.


def test_param_pattern_accepted():
    errors = validate_driver_definition(_def_with_command({
        "host": {"type": "string", "pattern": r"^\d{1,3}(\.\d{1,3}){3}$"},
        "level": {"type": "integer", "min": 0, "max": 100},
    }))
    assert errors == []


def test_param_uncompilable_pattern_rejected():
    errors = validate_driver_definition(_def_with_command({
        "host": {"type": "string", "pattern": "([0-9"},  # unbalanced group
    }))
    assert any("pattern" in e and "host" in e for e in errors)


def test_param_redos_pattern_rejected():
    errors = validate_driver_definition(_def_with_command({
        "host": {"type": "string", "pattern": "(a+)+$"},  # catastrophic
    }))
    assert any("pattern" in e and "host" in e for e in errors)


def test_param_inverted_min_max_rejected():
    errors = validate_driver_definition(_def_with_command({
        "level": {"type": "integer", "min": 100, "max": 0},
    }))
    assert any("min" in e and "max" in e and "level" in e for e in errors)


def test_param_non_numeric_bound_rejected():
    errors = validate_driver_definition(_def_with_command({
        "level": {"type": "integer", "min": "low"},
    }))
    assert any("min must be a number" in e for e in errors)


def test_param_pattern_validated_on_actions():
    driver = _def_with_command({})
    driver["actions"] = [
        {
            "id": "bad_action",
            "kind": "command",
            "command": "do_thing",
            "label": "Bad",
            "params": {"host": {"type": "string", "pattern": "([0-9"}},
        },
    ]
    errors = validate_driver_definition(driver)
    assert any("actions[0]" in e and "pattern" in e for e in errors)
