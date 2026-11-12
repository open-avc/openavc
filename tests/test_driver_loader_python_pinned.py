"""What the driver loader does with a Python driver file, pinned exactly.

Covers the import of a ``.py`` driver (which class is the driver, what is left
in ``sys.modules``, what is logged and under which logger), the hot reload
(each of its results, what it registers and unregisters, what it leaves in
``sys.modules``), the Code view's Python driver listing (every field of every
entry, which copy is listed when two files are one driver) and the scans that
find a YAML definition by the id it declares.

Several of these record behaviour that looks wrong, deliberately, so that a
change to it is a decision rather than a side effect: the listing reads only
the first class in a file and keeps the first directory's copy of an id where
loading keeps the last; the YAML listing keeps the first file of a directory
where loading keeps the one named for the id. Each such test says so, and
fixing one changes its assertion. A ``.avcdriver`` that is not UTF-8, and a
Python class whose ``DRIVER_INFO`` is not a mapping, are passed over with a
warning: neither may stop the server starting.

Every driver id and file stem here starts with ``pin_`` so that nothing in the
shared registry or ``sys.modules`` collides with another test, and the
``isolated`` fixture puts both back.
"""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import pytest

from openavc.drivers import registry
from openavc.drivers.driver_loader import (
    delete_driver_definition,
    is_builtin_driver,
    list_driver_definitions,
    list_python_drivers,
    load_driver_files,
    load_python_driver_file,
    load_python_drivers,
    reload_python_driver,
)

LOGGER = "openavc.drivers.driver_loader"

# The module whose globals the hot reload looks its collaborators up in.
RELOAD_HOME = "openavc.drivers.python_modules"

HEADER = "from openavc.drivers.base import BaseDriver\n"


def _driver(class_name: str, driver_id: str, name: str = "Pinned") -> str:
    return (
        HEADER
        + f"class {class_name}(BaseDriver):\n"
        + f"    DRIVER_INFO = {{'id': {driver_id!r}, 'name': {name!r}}}\n"
    )


@pytest.fixture
def isolated():
    saved = dict(registry._DRIVER_REGISTRY)
    before = set(sys.modules)
    yield
    registry._DRIVER_REGISTRY.clear()
    registry._DRIVER_REGISTRY.update(saved)
    for name in set(sys.modules) - before:
        if "_driver_pin_" in name or "_driver_validate_pin_" in name:
            sys.modules.pop(name, None)


def _records(caplog) -> list[tuple[str, str, str]]:
    return [
        (r.name, r.levelname, r.getMessage())
        for r in caplog.records
        if r.name == LOGGER
    ]


# --- importing one Python driver file ---------------------------------------


def test_the_driver_class_is_the_first_by_name_not_by_position(tmp_path, isolated):
    path = tmp_path / "pin_two_classes.py"
    path.write_text(
        HEADER
        + "class ZetaDriver(BaseDriver):\n"
        + "    DRIVER_INFO = {'id': 'pin_two_z', 'name': 'Zeta'}\n"
        + "class AlphaDriver(BaseDriver):\n"
        + "    DRIVER_INFO = {'id': 'pin_two_a', 'name': 'Alpha'}\n",
        encoding="utf-8",
    )
    cls = load_python_driver_file(path)
    assert cls.__name__ == "AlphaDriver"
    assert cls.__module__ == "openavc_driver_pin_two_classes"
    assert sys.modules["openavc_driver_pin_two_classes"].AlphaDriver is cls


@pytest.mark.parametrize(
    "body",
    [
        # imported from elsewhere, not defined here
        "from openavc.drivers.equipment import EquipmentDriver\n",
        # an empty id
        "class Blank(BaseDriver):\n    DRIVER_INFO = {'id': ''}\n",
        # no id at all
        "class NoId(BaseDriver):\n    DRIVER_INFO = {'name': 'No id'}\n",
        # an id, but not a driver
        "class NotADriver:\n    DRIVER_INFO = {'id': 'pin_not_a_driver'}\n",
        # a driver, but the base class is inherited without a DRIVER_INFO id
        "class Bare(BaseDriver):\n    pass\n",
    ],
)
def test_no_driver_class_is_none_and_the_module_stays_loaded(
    tmp_path, isolated, caplog, body
):
    path = tmp_path / "pin_no_class.py"
    path.write_text(HEADER + body, encoding="utf-8")
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert load_python_driver_file(path) is None
    assert "openavc_driver_pin_no_class" in sys.modules
    assert _records(caplog) == [
        (LOGGER, "WARNING", f"No BaseDriver subclass with DRIVER_INFO found in {path}")
    ]


def test_a_file_that_is_not_python_gets_no_module(tmp_path, isolated, caplog):
    path = tmp_path / "pin_not_python.txt"
    path.write_text(_driver("D", "pin_not_python"), encoding="utf-8")
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert load_python_driver_file(path) is None
    assert "openavc_driver_pin_not_python" not in sys.modules
    assert _records(caplog) == [
        (LOGGER, "WARNING", f"Could not create module spec for {path}")
    ]


def test_a_failed_import_leaves_no_module(tmp_path, isolated, caplog):
    path = tmp_path / "pin_raises.py"
    path.write_text(HEADER + "raise RuntimeError('at import')\n", encoding="utf-8")
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert load_python_driver_file(path) is None
    assert "openavc_driver_pin_raises" not in sys.modules
    records = _records(caplog)
    assert [(n, lvl, msg) for n, lvl, msg in records] == [
        (LOGGER, "ERROR", f"Failed to load Python driver from {path}")
    ]
    assert caplog.records[-1].exc_info[0] is RuntimeError


def test_a_driver_info_problem_is_a_warning_naming_the_driver(tmp_path, isolated, caplog):
    path = tmp_path / "pin_info_issue.py"
    path.write_text(
        HEADER
        + "class IssueDriver(BaseDriver):\n"
        + "    DRIVER_INFO = {'id': 'pin_info_issue', 'name': 'Issue',\n"
        + "        'device_settings': {'a': {'type': 'string'}},\n"
        + "        'actions': [{'kind': 'setup', 'label': 'S', 'id': 's'}]}\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        cls = load_python_driver_file(path)
    assert cls.__name__ == "IssueDriver"
    assert _records(caplog) == [
        (
            LOGGER,
            "WARNING",
            "Python driver 'pin_info_issue': declares a kind:'setup' action but "
            "does not override run_setup_action, so the wizard will 501 on launch",
        ),
        (
            LOGGER,
            "WARNING",
            "Python driver 'pin_info_issue': declares device_settings but does not "
            "override set_device_setting, so every write will 501",
        ),
    ]


def test_overrides_silence_the_driver_info_warnings(tmp_path, isolated, caplog):
    path = tmp_path / "pin_info_ok.py"
    path.write_text(
        HEADER
        + "class OkDriver(BaseDriver):\n"
        + "    DRIVER_INFO = {'id': 'pin_info_ok', 'name': 'Ok',\n"
        + "        'device_settings': {'a': {'type': 'string'}},\n"
        + "        'actions': [{'kind': 'setup', 'label': 'S', 'id': 's'}]}\n"
        + "    async def run_setup_action(self, *a, **k):\n"
        + "        return None\n"
        + "    async def set_device_setting(self, *a, **k):\n"
        + "        return None\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert load_python_driver_file(path) is not None
    assert _records(caplog) == []


# --- loading a directory of Python drivers ----------------------------------


def test_loading_python_drivers_skips_what_is_not_a_driver(tmp_path, isolated, caplog):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pin_ld_ok.py").write_text(_driver("OkDriver", "pin_ld_ok"), encoding="utf-8")
    (repo / "_pin_helper.py").write_text("raise SystemExit('never imported')\n", encoding="utf-8")
    (repo / "pin_ld_ok_sim.py").write_text("raise SystemExit('never imported')\n", encoding="utf-8")
    (repo / "pin_ld_none.py").write_text(HEADER, encoding="utf-8")
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        count = load_python_drivers([tmp_path / "missing", repo])
    assert count == 1
    assert registry._DRIVER_REGISTRY["pin_ld_ok"].__module__ == "openavc_driver_pin_ld_ok"
    assert _records(caplog) == [
        (LOGGER, "WARNING", f"No BaseDriver subclass with DRIVER_INFO found in {repo / 'pin_ld_none.py'}"),
        (LOGGER, "INFO", "Loaded Python driver: pin_ld_ok from pin_ld_ok.py"),
    ]


def test_loading_python_drivers_reports_a_refused_registration(
    tmp_path, isolated, caplog, monkeypatch
):
    (tmp_path / "pin_ld_refused.py").write_text(
        _driver("RefusedDriver", "pin_ld_refused"), encoding="utf-8"
    )

    def refuse(_cls):
        raise RuntimeError("refused")

    monkeypatch.setattr("openavc.drivers.driver_loader.register_driver", refuse)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert load_python_drivers([tmp_path]) == 0
    assert _records(caplog) == [
        (LOGGER, "ERROR", f"Failed to register Python driver from {tmp_path / 'pin_ld_refused.py'}")
    ]


# --- hot reload ---------------------------------------------------------------


def test_reload_same_id_registers_the_new_class(tmp_path, isolated, caplog):
    path = tmp_path / "pin_rl_same.py"
    path.write_text(_driver("SameDriver", "pin_rl_same", "First release"), encoding="utf-8")
    assert load_python_drivers([tmp_path]) == 1
    old_class = registry._DRIVER_REGISTRY["pin_rl_same"]

    # A different size: a same-size rewrite within the same second would be
    # read back from the bytecode cache.
    path.write_text(_driver("SameDriver", "pin_rl_same", "Two"), encoding="utf-8")
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = reload_python_driver(path)

    assert result == {
        "status": "reloaded",
        "driver_id": "pin_rl_same",
        "old_driver_id": "pin_rl_same",
    }
    new_class = registry._DRIVER_REGISTRY["pin_rl_same"]
    assert new_class is not old_class
    assert new_class.DRIVER_INFO["name"] == "Two"
    assert new_class.__module__ == "openavc_driver_pin_rl_same"
    assert sys.modules["openavc_driver_pin_rl_same"].SameDriver is new_class
    assert "_openavc_driver_validate_pin_rl_same" not in sys.modules
    assert _records(caplog) == [
        (LOGGER, "INFO", "Hot-reloaded Python driver: pin_rl_same from pin_rl_same.py")
    ]


def test_reload_with_a_new_id_unregisters_the_old_one(tmp_path, isolated):
    path = tmp_path / "pin_rl_rename.py"
    path.write_text(_driver("RenameDriver", "pin_rl_before"), encoding="utf-8")
    assert load_python_drivers([tmp_path]) == 1

    path.write_text(_driver("RenameDriver", "pin_rl_after"), encoding="utf-8")
    result = reload_python_driver(path)

    assert result == {
        "status": "reloaded",
        "driver_id": "pin_rl_after",
        "old_driver_id": "pin_rl_before",
    }
    assert "pin_rl_before" not in registry._DRIVER_REGISTRY
    assert registry._DRIVER_REGISTRY["pin_rl_after"].DRIVER_INFO["id"] == "pin_rl_after"


def test_reload_of_a_file_never_loaded(tmp_path, isolated):
    path = tmp_path / "pin_rl_fresh.py"
    path.write_text(_driver("FreshDriver", "pin_rl_fresh"), encoding="utf-8")
    result = reload_python_driver(path)
    assert result == {"status": "reloaded", "driver_id": "pin_rl_fresh", "old_driver_id": None}
    assert registry._DRIVER_REGISTRY["pin_rl_fresh"].__module__ == "openavc_driver_pin_rl_fresh"


def test_reload_when_the_resident_module_holds_no_driver(tmp_path, isolated):
    path = tmp_path / "pin_rl_stub.py"
    path.write_text(_driver("StubDriver", "pin_rl_stub"), encoding="utf-8")
    stand_in = types.ModuleType("openavc_driver_pin_rl_stub")
    sys.modules["openavc_driver_pin_rl_stub"] = stand_in
    result = reload_python_driver(path)
    assert result == {"status": "reloaded", "driver_id": "pin_rl_stub", "old_driver_id": None}
    assert sys.modules["openavc_driver_pin_rl_stub"] is not stand_in


def test_reload_picks_the_driver_class_the_way_loading_does(tmp_path, isolated):
    path = tmp_path / "pin_rl_two.py"
    path.write_text(
        HEADER
        + "class ZetaDriver(BaseDriver):\n"
        + "    DRIVER_INFO = {'id': 'pin_rl_two_z'}\n"
        + "class AlphaDriver(BaseDriver):\n"
        + "    DRIVER_INFO = {'id': 'pin_rl_two_a'}\n",
        encoding="utf-8",
    )
    result = reload_python_driver(path)
    assert result["driver_id"] == "pin_rl_two_a"
    assert "pin_rl_two_z" not in registry._DRIVER_REGISTRY


def test_reload_reads_the_old_id_by_the_same_rule(tmp_path, isolated):
    """The old id comes from the resident module's driver class, chosen as
    loading chooses it: by name, defined in that module, with an id."""
    path = tmp_path / "pin_rl_old.py"
    old = types.ModuleType("openavc_driver_pin_rl_old")
    from openavc.drivers.base import BaseDriver

    def make(name, info, module):
        cls = type(name, (BaseDriver,), {"DRIVER_INFO": info})
        cls.__module__ = module
        return cls

    # sorts first by name, but is not defined in the module
    old.Alien = make("Alien", {"id": "pin_rl_elsewhere"}, "somewhere_else")
    old.Zed = make("Zed", {"id": "pin_rl_old_z"}, "openavc_driver_pin_rl_old")
    old.Mid = make("Mid", {"id": ""}, "openavc_driver_pin_rl_old")
    old.Bee = make("Bee", {"id": "pin_rl_old_b"}, "openavc_driver_pin_rl_old")
    sys.modules["openavc_driver_pin_rl_old"] = old
    registry._DRIVER_REGISTRY["pin_rl_old_b"] = old.Bee
    registry._DRIVER_REGISTRY["pin_rl_old_z"] = old.Zed

    path.write_text(_driver("NewDriver", "pin_rl_new"), encoding="utf-8")
    result = reload_python_driver(path)

    assert result == {"status": "reloaded", "driver_id": "pin_rl_new", "old_driver_id": "pin_rl_old_b"}
    assert "pin_rl_old_b" not in registry._DRIVER_REGISTRY
    assert registry._DRIVER_REGISTRY["pin_rl_old_z"] is old.Zed


@pytest.mark.parametrize(
    "body",
    [
        "",
        "class Blank(BaseDriver):\n    DRIVER_INFO = {'id': ''}\n",
        "from openavc.drivers.equipment import EquipmentDriver\n",
    ],
)
def test_reload_with_no_driver_class(tmp_path, isolated, body):
    path = tmp_path / "pin_rl_none.py"
    path.write_text(HEADER + body, encoding="utf-8")
    assert reload_python_driver(path) == {
        "status": "error",
        "error": "No BaseDriver subclass with DRIVER_INFO found",
        "old_driver_preserved": True,
    }
    assert "_openavc_driver_validate_pin_rl_none" not in sys.modules
    assert "openavc_driver_pin_rl_none" not in sys.modules


def test_reload_of_a_file_that_is_not_python(tmp_path, isolated):
    path = tmp_path / "pin_rl_text.txt"
    path.write_text(_driver("D", "pin_rl_text"), encoding="utf-8")
    assert reload_python_driver(path) == {
        "status": "error",
        "error": f"Could not create module spec for {path}",
    }


def test_reload_of_a_syntax_error(tmp_path, isolated):
    path = tmp_path / "pin_rl_syntax.py"
    source = HEADER + "class Broken(BaseDriver)\n    pass\n"
    path.write_text(source, encoding="utf-8")
    try:
        compile(source, str(path), "exec")
    except SyntaxError as exc:
        expected_msg, expected_line = exc.msg, exc.lineno
    assert reload_python_driver(path) == {
        "status": "error",
        "error": f"SyntaxError: {expected_msg} (pin_rl_syntax.py, line {expected_line})",
        "line": expected_line,
        "old_driver_preserved": True,
    }
    assert "_openavc_driver_validate_pin_rl_syntax" not in sys.modules


def test_reload_of_an_import_error_names_the_deepest_line_in_the_file(tmp_path, isolated):
    path = tmp_path / "pin_rl_boom.py"
    path.write_text(
        HEADER                                   # line 1
        + "def boom():\n"                         # line 2
        + "    raise RuntimeError('at three')\n"  # line 3
        + "\n"                                    # line 4
        + "boom()\n",                             # line 5
        encoding="utf-8",
    )
    assert reload_python_driver(path) == {
        "status": "error",
        "error": "RuntimeError: at three",
        "line": 3,
        "old_driver_preserved": True,
    }
    assert "_openavc_driver_validate_pin_rl_boom" not in sys.modules


def test_reload_of_a_missing_file_has_no_line(tmp_path, isolated):
    path = tmp_path / "pin_rl_gone.py"
    assert reload_python_driver(path) == {
        "status": "error",
        "error": f"FileNotFoundError: [Errno 2] No such file or directory: {str(path)!r}",
        "line": None,
        "old_driver_preserved": True,
    }
    assert "_openavc_driver_validate_pin_rl_gone" not in sys.modules


def test_reload_whose_second_import_fails_with_nothing_resident(tmp_path, isolated, monkeypatch):
    path = tmp_path / "pin_rl_toctou.py"
    path.write_text(_driver("TocTou", "pin_rl_toctou"), encoding="utf-8")
    monkeypatch.setattr(f"{RELOAD_HOME}.load_python_driver_file", lambda _p: None)
    assert reload_python_driver(path) == {
        "status": "error",
        "error": (
            "Failed to reload driver after validation passed; the "
            "previously loaded driver is still active"
        ),
        "old_driver_preserved": True,
    }
    assert "openavc_driver_pin_rl_toctou" not in sys.modules
    assert "pin_rl_toctou" not in registry._DRIVER_REGISTRY


def test_reload_returns_the_validated_id_and_registers_the_second_import(
    tmp_path, isolated, monkeypatch
):
    path = tmp_path / "pin_rl_split.py"
    path.write_text(_driver("FirstDriver", "pin_rl_first"), encoding="utf-8")
    second = type("SecondDriver", (), {"DRIVER_INFO": {"id": "pin_rl_second"}})
    monkeypatch.setattr(f"{RELOAD_HOME}.load_python_driver_file", lambda _p: second)
    result = reload_python_driver(path)
    assert result == {"status": "reloaded", "driver_id": "pin_rl_first", "old_driver_id": None}
    assert registry._DRIVER_REGISTRY["pin_rl_second"] is second
    assert "pin_rl_first" not in registry._DRIVER_REGISTRY


def test_reload_whose_registration_raises_has_already_dropped_the_old_id(
    tmp_path, isolated, monkeypatch
):
    path = tmp_path / "pin_rl_refuse.py"
    path.write_text(_driver("RefuseDriver", "pin_rl_refuse_old"), encoding="utf-8")
    assert load_python_drivers([tmp_path]) == 1
    path.write_text(_driver("RefuseDriver", "pin_rl_refuse_newer"), encoding="utf-8")

    def refuse(_cls):
        raise RuntimeError("refused")

    monkeypatch.setattr(f"{RELOAD_HOME}.register_driver", refuse)
    with pytest.raises(RuntimeError, match="refused"):
        reload_python_driver(path)
    assert "pin_rl_refuse_old" not in registry._DRIVER_REGISTRY
    assert "pin_rl_refuse_newer" not in registry._DRIVER_REGISTRY
    assert sys.modules["openavc_driver_pin_rl_refuse"].RefuseDriver.DRIVER_INFO["id"] == (
        "pin_rl_refuse_newer"
    )


# --- the Code view's listing of Python drivers --------------------------------


def _entry(stem, *, id=None, name=None, manufacturer="", category="", loaded=False,
           load_error="Not loaded"):
    return {
        "id": id or stem,
        "filename": f"{stem}.py",
        "name": name or stem,
        "manufacturer": manufacturer,
        "category": category,
        "loaded": loaded,
        "load_error": load_error,
        "devices_using": [],
    }


def test_listing_python_drivers(tmp_path, isolated, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    files = {
        # every metadata field read, a computed value and a non-text key skipped
        first / "pin_ls_full.py": (
            "NAME = 'computed'\n"
            "class Full:\n"
            "    DRIVER_INFO = {'id': 'pin_ls_full', 'name': NAME,\n"
            "        'manufacturer': 'Acme', 'category': 'utility', 1: 'x',\n"
            "        'description': 'not listed'}\n"
        ),
        # only the first class is read: a helper first leaves the file's name
        first / "pin_ls_helper_first.py": (
            "class Helper:\n    pass\n"
            "class Real:\n    DRIVER_INFO = {'id': 'pin_ls_real', 'name': 'Real'}\n"
        ),
        # the first class's DRIVER_INFO, but no id or an empty one
        first / "pin_ls_no_id.py": "class A:\n    DRIVER_INFO = {'name': 'Named', 'id': ''}\n",
        # an annotated DRIVER_INFO is not read
        first / "pin_ls_annotated.py": "class A:\n    DRIVER_INFO: dict = {'id': 'pin_ls_ann'}\n",
        # a module-level DRIVER_INFO is not read
        first / "pin_ls_module_level.py": "DRIVER_INFO = {'id': 'pin_ls_module'}\n",
        # a syntax error keeps the defaults
        first / "pin_ls_syntax.py": "class (:\n",
        # duplicates within a directory: the file named for the id replaces a copy
        first / "pin_ls_a_copy.py": "class A:\n    DRIVER_INFO = {'id': 'pin_ls_dup', 'name': 'Copy'}\n",
        first / "pin_ls_dup.py": "class A:\n    DRIVER_INFO = {'id': 'pin_ls_dup', 'name': 'Named'}\n",
        # ...and a later copy not named for the id is skipped
        first / "pin_ls_kept.py": "class A:\n    DRIVER_INFO = {'id': 'pin_ls_kept'}\n",
        first / "pin_ls_z_kept.py": "class A:\n    DRIVER_INFO = {'id': 'pin_ls_kept'}\n",
        # listed as loaded, and as imported but not registered
        first / "pin_ls_loaded.py": "class A:\n    DRIVER_INFO = {'id': 'pin_ls_loaded'}\n",
        first / "pin_ls_resident.py": "class A:\n    DRIVER_INFO = {'id': 'pin_ls_resident'}\n",
        # never drivers
        first / "_pin_ls_private.py": "class A:\n    DRIVER_INFO = {'id': 'pin_ls_private'}\n",
        first / "pin_ls_full_sim.py": "class A:\n    DRIVER_INFO = {'id': 'pin_ls_sim'}\n",
        first / "pin_ls_full_discovery.py": "class A:\n    DRIVER_INFO = {'id': 'pin_ls_disc'}\n",
        # across directories the first directory's copy is kept unless the
        # later file is named for the id (loading keeps the later directory's)
        second / "pin_ls_other_name.py": "class A:\n    DRIVER_INFO = {'id': 'pin_ls_full'}\n",
        second / "pin_ls_kept.py": "class A:\n    DRIVER_INFO = {'id': 'pin_ls_kept', 'name': 'Second'}\n",
    }
    for path, text in files.items():
        path.write_text(text, encoding="utf-8")
    (first / "pin_ls_latin.py").write_bytes(b"class A:\n    DRIVER_INFO = {'id': 'caf\xe9'}\n")

    monkeypatch.setattr(
        "openavc.drivers.driver_loader.is_driver_registered",
        lambda driver_id: driver_id == "pin_ls_loaded",
    )
    sys.modules["openavc_driver_pin_ls_resident"] = types.ModuleType("stand_in")

    listed = list_python_drivers([tmp_path / "missing", first, second])

    resident = "Imported but not registered — duplicate driver ID or a failed last reload"
    # A replacing copy goes to the end of the list as it stands when it is read.
    assert listed == [
        _entry("pin_ls_annotated"),
        _entry("pin_ls_dup", name="Named"),
        _entry(
            "pin_ls_full", id="pin_ls_full", manufacturer="Acme", category="utility"
        ),
        _entry("pin_ls_helper_first"),
        _entry("pin_ls_latin"),
        _entry("pin_ls_loaded", loaded=True, load_error=None),
        _entry("pin_ls_module_level"),
        _entry("pin_ls_no_id", name="Named"),
        _entry("pin_ls_resident", load_error=resident),
        _entry("pin_ls_syntax"),
        _entry("pin_ls_kept", name="Second"),
    ]


def test_listing_python_drivers_reports_a_driver_whose_class_was_not_found(
    tmp_path, isolated
):
    path = tmp_path / "pin_ls_classless.py"
    path.write_text(HEADER + "class Plain:\n    DRIVER_INFO = {'id': 'pin_ls_classless'}\n",
                    encoding="utf-8")
    assert load_python_driver_file(path) is None
    assert list_python_drivers([tmp_path]) == [
        _entry(
            "pin_ls_classless",
            load_error="Imported but not registered — duplicate driver ID or a failed last reload",
        )
    ]


# --- YAML definitions found by the id they declare ----------------------------


def _avcdriver(driver_id: str, name: str = "Pinned") -> str:
    return f"id: {driver_id}\nname: {name}\ntransport: tcp\n"


def test_the_yaml_listing_keeps_a_directory_s_first_file(tmp_path):
    """Loading serves the file named for the id; the listing shows the first
    file of the directory. Pinned as it is."""
    (tmp_path / "pin_a_copy.avcdriver").write_text(_avcdriver("pin_yl", "Copy"), encoding="utf-8")
    (tmp_path / "pin_yl.avcdriver").write_text(_avcdriver("pin_yl", "Named"), encoding="utf-8")
    listed = list_driver_definitions([tmp_path])
    assert [(d["name"], Path(d["_source_file"]).name) for d in listed] == [
        ("Copy", "pin_a_copy.avcdriver")
    ]


def test_the_yaml_listing_keeps_a_replaced_entry_in_its_place(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "pin_m.avcdriver").write_text(_avcdriver("pin_m", "Built in"), encoding="utf-8")
    (first / "pin_n.avcdriver").write_text(
        "# a comment\n  # another\n" + _avcdriver("pin_n"), encoding="utf-8"
    )
    (first / "pin_bad.avcdriver").write_text("- not\n- a mapping\n", encoding="utf-8")
    (second / "pin_m.avcdriver").write_text(_avcdriver("pin_m", "User copy"), encoding="utf-8")
    (second / "pin_m_again.avcdriver").write_text(_avcdriver("pin_m", "Again"), encoding="utf-8")
    listed = list_driver_definitions([tmp_path / "missing", first, second])
    assert [
        (d["id"], d["name"], Path(d["_source_file"]).parent.name, d["_comment_lines"])
        for d in listed
    ] == [("pin_m", "User copy", "second", 0), ("pin_n", "Pinned", "first", 2)]


def _latin(directory: Path) -> None:
    (directory / "pin_latin.avcdriver").write_bytes(b"id: pin_latin\nname: caf\xe9\n")


@pytest.mark.parametrize(
    "scan",
    [
        lambda dirs: load_driver_files(dirs),
        lambda dirs: list_driver_definitions(dirs),
        lambda dirs: is_builtin_driver("pin_anything", dirs),
        lambda dirs: delete_driver_definition("pin_anything", dirs),
    ],
    ids=["load", "list", "is_builtin", "delete"],
)
def test_a_definition_that_is_not_utf8_is_passed_over_by_every_yaml_scan(tmp_path, scan):
    _latin(tmp_path)
    assert scan([tmp_path]) in (0, [], False)
    assert (tmp_path / "pin_latin.avcdriver").exists()


def test_loading_a_definition_that_is_not_utf8_says_which_file(tmp_path, caplog):
    _latin(tmp_path)
    (tmp_path / "pin_fine.avcdriver").write_text(_avcdriver("pin_fine"), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert load_driver_files([tmp_path]) == 1
    assert [r.getMessage() for r in caplog.records if "pin_latin" in r.getMessage()] == [
        f"Driver file {tmp_path / 'pin_latin.avcdriver'} is not UTF-8 text; save it as UTF-8 to load it",
    ]


def test_is_builtin_driver_skips_what_it_cannot_read(tmp_path, monkeypatch):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    (builtin / "pin_b.avcdriver").write_text(_avcdriver("pin_b"), encoding="utf-8")
    (user / "pin_broken.avcdriver").write_text("id: [unclosed\n", encoding="utf-8")
    (user / "pin_folder.avcdriver").mkdir()
    (user / "pin_list.avcdriver").write_text("- pin_b\n", encoding="utf-8")
    monkeypatch.setattr(
        "openavc.drivers.driver_loader.is_builtin_definition_path",
        lambda p: Path(p).parent == builtin,
    )
    dirs = [tmp_path / "missing", builtin, user]
    assert is_builtin_driver("pin_b", dirs) is True
    (user / "pin_b_copy.avcdriver").write_text(_avcdriver("pin_b"), encoding="utf-8")
    assert is_builtin_driver("pin_b", dirs) is False
    assert is_builtin_driver("pin_nowhere", dirs) is False


def test_is_builtin_driver_is_true_for_a_reserved_id(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", {"pin_reserved"})
    assert is_builtin_driver("pin_reserved", [tmp_path]) is True


def test_delete_skips_what_it_cannot_read_or_must_keep(tmp_path, monkeypatch, caplog):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    (builtin / "pin_d.avcdriver").write_text(_avcdriver("pin_d"), encoding="utf-8")
    (user / "pin_broken.avcdriver").write_text("id: [unclosed\n", encoding="utf-8")
    (user / "pin_folder.avcdriver").mkdir()
    kept = user / "pin_d.avcdriver"
    kept.write_text(_avcdriver("pin_d"), encoding="utf-8")
    monkeypatch.setattr(
        "openavc.drivers.driver_loader.is_builtin_definition_path",
        lambda p: Path(p).parent == builtin,
    )
    dirs = [tmp_path / "missing", builtin, user]
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert delete_driver_definition("pin_d", dirs, keep=kept) is False
    assert kept.exists()
    assert (builtin / "pin_d.avcdriver").exists()
    assert _records(caplog) == [
        (LOGGER, "WARNING",
         f"Refusing to delete built-in driver definition: {builtin / 'pin_d.avcdriver'}")
    ]
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert delete_driver_definition("pin_d", dirs) is True
    assert not kept.exists()
    assert _records(caplog)[-1] == (LOGGER, "INFO", f"Deleted driver definition: {kept}")


def test_delete_moves_on_when_a_file_cannot_be_removed(tmp_path, monkeypatch):
    target = tmp_path / "pin_e.avcdriver"
    target.write_text(_avcdriver("pin_e"), encoding="utf-8")
    real_unlink = Path.unlink

    def unlink(self, *a, **k):
        if self == target:
            raise PermissionError("in use")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", unlink)
    assert delete_driver_definition("pin_e", [tmp_path]) is False
    assert target.exists()


def test_reload_whose_second_import_fails_puts_the_old_module_back(
    tmp_path, isolated, monkeypatch
):
    path = tmp_path / "pin_rl_restore.py"
    path.write_text(_driver("RestoreDriver", "pin_rl_restore"), encoding="utf-8")
    assert load_python_drivers([tmp_path]) == 1
    resident = sys.modules["openavc_driver_pin_rl_restore"]
    registered = registry._DRIVER_REGISTRY["pin_rl_restore"]
    monkeypatch.setattr(f"{RELOAD_HOME}.load_python_driver_file", lambda _p: None)
    result = reload_python_driver(path)
    assert result["status"] == "error" and result["old_driver_preserved"] is True
    assert sys.modules["openavc_driver_pin_rl_restore"] is resident
    assert registry._DRIVER_REGISTRY["pin_rl_restore"] is registered


# --- the id a .py file declares, read without importing it ---------------------


@pytest.mark.parametrize(
    "source, expected",
    [
        # a class attribute
        ("class A:\n    DRIVER_INFO = {'id': 'pin_cls'}\n", "pin_cls"),
        # module level counts here
        ("DRIVER_INFO = {'id': 'pin_mod'}\n", "pin_mod"),
        # so does a function's local, and one of several names
        ("def f():\n    DRIVER_INFO = {'id': 'pin_local'}\n", "pin_local"),
        ("A = DRIVER_INFO = {'id': 'pin_chain'}\n", "pin_chain"),
        # an annotated one does not
        ("class A:\n    DRIVER_INFO: dict = {'id': 'pin_ann'}\n", None),
        # an empty id reads as no id, and stops the search
        (
            "DRIVER_INFO = {'id': ''}\nclass A:\n    DRIVER_INFO = {'id': 'pin_later'}\n",
            None,
        ),
        # a dict with no id is passed over for a later one
        (
            "DRIVER_INFO = {'name': 'x'}\nclass A:\n    DRIVER_INFO = {'id': 'pin_next'}\n",
            "pin_next",
        ),
        # a non-text id is passed over for a later key in the same dict
        ("DRIVER_INFO = {'id': 5, 'id': 'pin_text'}\n", "pin_text"),
        # a computed dict or id is not read
        ("DRIVER_INFO = dict(id='pin_call')\n", None),
        ("ID = 'x'\nDRIVER_INFO = {'id': ID}\n", None),
        # the first by walk order: the module's own statements before a class body
        (
            "class A:\n    DRIVER_INFO = {'id': 'pin_inner'}\nDRIVER_INFO = {'id': 'pin_outer'}\n",
            "pin_outer",
        ),
        # no DRIVER_INFO at all
        ("class A:\n    INFO = {'id': 'pin_other'}\n", None),
    ],
)
def test_the_id_a_python_file_declares(tmp_path, source, expected):
    from openavc.drivers.driver_loader import driver_id_from_file

    path = tmp_path / "pin_declared.py"
    path.write_text(source, encoding="utf-8")
    assert driver_id_from_file(path) == expected


def test_a_python_file_with_an_empty_id_is_the_driver_its_name_says(tmp_path):
    from openavc.drivers.driver_loader import driver_files_for

    (tmp_path / "pin_blank.py").write_text(
        "class A:\n    DRIVER_INFO = {'id': ''}\n", encoding="utf-8"
    )
    assert driver_files_for(tmp_path, "pin_blank", ".py") == [tmp_path / "pin_blank.py"]


def test_loading_python_drivers_keeps_one_copy_of_an_id(tmp_path, isolated, caplog):
    (tmp_path / "pin_ld_dup.py").write_text(_driver("DupDriver", "pin_ld_dup", "Named"), encoding="utf-8")
    (tmp_path / "pin_ld_dup_z.py").write_text(_driver("DupDriver", "pin_ld_dup", "Copy"), encoding="utf-8")
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert load_python_drivers([tmp_path]) == 1
    assert registry._DRIVER_REGISTRY["pin_ld_dup"].DRIVER_INFO["name"] == "Named"
    assert _records(caplog)[-1] == (
        LOGGER, "WARNING", "Driver 'pin_ld_dup' is in both pin_ld_dup.py and pin_ld_dup_z.py; pin_ld_dup.py is used"
    )


def test_delete_goes_on_to_a_later_copy_when_one_cannot_be_removed(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    stuck = first / "pin_f.avcdriver"
    stuck.write_text(_avcdriver("pin_f"), encoding="utf-8")
    loose = second / "pin_f.avcdriver"
    loose.write_text(_avcdriver("pin_f"), encoding="utf-8")
    real_unlink = Path.unlink

    def unlink(self, *a, **k):
        if self == stuck:
            raise PermissionError("in use")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", unlink)
    assert delete_driver_definition("pin_f", [first, second]) is True
    assert stuck.exists()
    assert not loose.exists()


@pytest.mark.parametrize(
    "declared",
    ["sub/pin_comp_discovery.py", "../pin_comp_discovery.py", "/abs/pin_comp_discovery.py"],
)
def test_a_yaml_companion_must_sit_beside_its_driver(tmp_path, declared):
    from openavc.drivers.driver_loader import driver_companions

    (tmp_path / "pin_comp_discovery.py").write_text("x = 1\n", encoding="utf-8")
    main = tmp_path / "pin_comp.avcdriver"
    main.write_text(
        _avcdriver("pin_comp") + f"discovery:\n  python: {declared}\n", encoding="utf-8"
    )
    assert driver_companions(main) == []
    main.write_text(
        _avcdriver("pin_comp") + "discovery:\n  python: ./pin_comp_discovery.py\n",
        encoding="utf-8",
    )
    assert driver_companions(main) == [tmp_path / "pin_comp_discovery.py"]


@pytest.mark.parametrize("info", ["None", "'pin_rl_bad_info'", "['id']"])
def test_a_class_whose_driver_info_is_not_a_mapping_is_no_driver(tmp_path, isolated, caplog, info):
    """Skipped with the scan's warning, refused on reload in the reload's
    words, and a fixed file then reloads without a restart."""
    path = tmp_path / "pin_rl_bad_info.py"
    path.write_text(HEADER + f"class BadInfo(BaseDriver):\n    DRIVER_INFO = {info}\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert load_python_driver_file(path) is None
    assert [r.getMessage() for r in caplog.records if "pin_rl_bad_info" in r.getMessage()] == [
        f"No BaseDriver subclass with DRIVER_INFO found in {path}",
    ]
    assert reload_python_driver(path) == {
        "status": "error", "error": "No BaseDriver subclass with DRIVER_INFO found", "old_driver_preserved": True,
    }

    path.write_text(_driver("FixedDriver", "pin_rl_bad_info", "Fixed at last"), encoding="utf-8")
    assert reload_python_driver(path)["status"] == "reloaded"
    assert registry._DRIVER_REGISTRY["pin_rl_bad_info"].DRIVER_INFO["name"] == "Fixed at last"


def test_a_library_holding_both_unreadable_kinds_still_loads_the_rest(tmp_path, isolated):
    """What the startup pass reads: neither file stops it."""
    _latin(tmp_path)
    (tmp_path / "pin_fine.avcdriver").write_text(_avcdriver("pin_fine"), encoding="utf-8")
    (tmp_path / "pin_bad_info.py").write_text(HEADER + "class BadInfo(BaseDriver):\n    DRIVER_INFO = None\n", encoding="utf-8")
    (tmp_path / "pin_good_py.py").write_text(_driver("GoodDriver", "pin_good_py"), encoding="utf-8")

    from openavc.drivers.driver_loader import load_all_drivers

    assert load_all_drivers([tmp_path]) == 2
    assert {"pin_fine", "pin_good_py"} <= set(registry._DRIVER_REGISTRY)


def test_a_same_id_reload_unregisters_nothing(tmp_path, isolated, caplog):
    path = tmp_path / "pin_rl_quiet.py"
    path.write_text(_driver("QuietDriver", "pin_rl_quiet", "First"), encoding="utf-8")
    assert load_python_drivers([tmp_path]) == 1
    path.write_text(_driver("QuietDriver", "pin_rl_quiet", "Second one"), encoding="utf-8")
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        assert reload_python_driver(path)["status"] == "reloaded"
    assert [r.getMessage() for r in caplog.records if r.name == "openavc.drivers.registry"] == [
        "Registered driver: pin_rl_quiet"
    ]


def test_finding_a_definition_by_id_prefers_the_file_named_for_it(tmp_path):
    from openavc.drivers.driver_loader import find_driver_file_by_id

    (tmp_path / "pin_a_copy.avcdriver").write_text(_avcdriver("pin_nf", "Copy"), encoding="utf-8")
    (tmp_path / "pin_nf.avcdriver").write_text(_avcdriver("pin_nf", "Named"), encoding="utf-8")
    (tmp_path / "pin_z_copy.avcdriver").write_text(_avcdriver("pin_nf", "Later"), encoding="utf-8")
    assert find_driver_file_by_id([tmp_path], "pin_nf") == tmp_path / "pin_nf.avcdriver"
    (tmp_path / "pin_nf.avcdriver").unlink()
    assert find_driver_file_by_id([tmp_path], "pin_nf") == tmp_path / "pin_a_copy.avcdriver"
