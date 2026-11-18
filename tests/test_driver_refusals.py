"""A driver file that is in the driver folder but did not load says so, with
why, wherever someone looks for the driver: the page of a device that needs it,
the Installed list, the Code view's list, a project import, the AI's tools and
the Builder's reload. A change refused before it reaches the folder answers
with the same reasons and records nothing. A reason names no folder, and a
record ends when its file changes or goes.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from openavc.api.errors import StructuredApiError
from openavc.api.routes import driver_definitions
from openavc.api.routes import drivers as routes
from openavc.cloud.tools.driver_tools import DriverToolsMixin
from openavc.core import project_library
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.installed_equipment import missing_driver_reason
from openavc.core.state_store import StateStore
from openavc.drivers import driver_change, driver_loader, refused_files, registry
from openavc.drivers.driver_change import DriverChange, DriverRefused, admit
from openavc.drivers.python_modules import NO_DRIVER_CLASS

DRIVER_ID = "acme_widget"
MODULE = f"openavc_driver_{DRIVER_ID}"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """An empty driver folder, a registry without any Acme driver, and an
    empty record, all this test's own."""
    folder = tmp_path / "driver_repo"
    folder.mkdir()
    monkeypatch.setattr(
        registry, "_DRIVER_REGISTRY",
        {k: v for k, v in registry._DRIVER_REGISTRY.items() if not k.startswith("acme")},
    )
    monkeypatch.setattr(refused_files, "_refused", {})
    modules_before = {n: m for n, m in sys.modules.items() if "acme" in n}
    yield folder
    for name in [n for n in sys.modules if "acme" in n]:
        if name in modules_before:
            sys.modules[name] = modules_before[name]
        else:
            sys.modules.pop(name, None)


def _definition(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": DRIVER_ID,
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "commands": {"power_on": {"label": "On", "send": "P\r", "params": {}}},
    }
    body.update(extra)
    return body


def _yaml(**extra: Any) -> bytes:
    return yaml.safe_dump(_definition(**extra), sort_keys=False).encode()


# The finding's own file: a state variable of an unknown type.
BOGUS = {"state_variables": {"power": {"type": "bogus", "label": "Power"}}}


def _bogus_reasons() -> tuple[str, ...]:
    """What the checker says of BOGUS, asked of the checker rather than
    copied, so a rewording of its message is not this file's to follow."""
    errors = driver_loader.validate_driver_definition(_definition(**BOGUS))
    assert errors
    return tuple(errors)


def _python(body: str = "") -> bytes:
    return (
        f"{body}"
        "from openavc.drivers.base import BaseDriver\n\n\n"
        "class AcmeWidget(BaseDriver):\n"
        f"    DRIVER_INFO = {{'id': {DRIVER_ID!r}, 'name': 'Acme Widget', 'transport': 'tcp'}}\n"
    ).encode()


def _loaded_at_start(repo: Path) -> int:
    return driver_loader.load_all_drivers([repo])


def _sentence(reasons: tuple[str, ...]) -> str:
    return f"Driver '{DRIVER_ID}' is installed but did not load: " + "; ".join(reasons)


# --- the start-up pass records why each file did not load --------------------------


def test_the_finding_s_file_is_installed_but_did_not_load_with_the_checker_s_reason(repo):
    (repo / "acme_widget.avcdriver").write_bytes(_yaml(**BOGUS))
    assert _loaded_at_start(repo) == 0
    assert not registry.is_driver_registered(DRIVER_ID)
    assert missing_driver_reason(DRIVER_ID) == _sentence(_bogus_reasons())


YAML_REFUSALS = {
    "not UTF-8": (b"id: acme_widget\nname: \xff\xfe\n", ("The file is not UTF-8 text. Save it as UTF-8.",)),
    "not YAML": (b"id: [acme_widget\n", None),
    "not a mapping": (
        b"- acme_widget\n",
        ("The file holds no driver definition: its top level is not a YAML mapping.",),
    ),
    "a missing discovery file": (
        _yaml(discovery={"python": {"file": "./acme_widget_discovery.py"}}),
        (
            "It names the discovery file './acme_widget_discovery.py' (discovery.python.file), "
            "which is not in the driver folder. Add that file first.",
        ),
    ),
}


@pytest.mark.parametrize("case", sorted(YAML_REFUSALS))
def test_every_way_a_definition_does_not_load_is_recorded_without_its_path(repo, case):
    data, reasons = YAML_REFUSALS[case]
    (repo / "acme_widget.avcdriver").write_bytes(data)
    _loaded_at_start(repo)

    refusal = refused_files.refusal_for(DRIVER_ID)
    assert refusal is not None
    if reasons is None:  # the YAML parser's own words follow
        assert len(refusal.reasons) == 1 and refusal.reasons[0].startswith("The file is not valid YAML: ")
    else:
        assert refusal.reasons == reasons
    assert str(repo) not in refusal.detail
    assert missing_driver_reason(DRIVER_ID) == _sentence(refusal.reasons)


def test_a_definition_whose_class_cannot_be_made_is_recorded(repo, monkeypatch):
    def _broke(_definition):
        raise RuntimeError("broke here")

    monkeypatch.setattr("openavc.drivers.configurable.create_configurable_driver_class", _broke)
    (repo / "acme_widget.avcdriver").write_bytes(_yaml())
    _loaded_at_start(repo)
    assert refused_files.refusal_for(DRIVER_ID).reasons == ("RuntimeError: broke here",)


PYTHON_REFUSALS = {
    "a missing package": (
        _python("import acme_nonexistent_lib\n"),
        "ModuleNotFoundError: No module named 'acme_nonexistent_lib' (line 1)",
    ),
    "no driver class": (b"VALUE = 1\n", NO_DRIVER_CLASS),
    "a syntax error": (b"def (:\n", "SyntaxError: invalid syntax (acme_widget.py, line 1)"),
    "a retired import": (
        _python("from server.drivers.base import BaseDriver as _Old\n"),
        "It was written for an older OpenAVC and imports 'server', which no longer exists. "
        "Update it from Browse Drivers (or install it again) to get the current version.",
    ),
}


@pytest.mark.parametrize("case", sorted(PYTHON_REFUSALS))
def test_every_way_a_python_driver_does_not_load_is_recorded(repo, case):
    data, reason = PYTHON_REFUSALS[case]
    (repo / "acme_widget.py").write_bytes(data)
    _loaded_at_start(repo)
    assert refused_files.refusal_for(DRIVER_ID).reasons == (reason,)
    assert str(repo) not in reason


def test_a_python_driver_whose_registration_raises_is_recorded(repo, monkeypatch):
    def _broke(_cls):
        raise RuntimeError("broke here")

    monkeypatch.setattr(driver_loader, "register_driver", _broke)
    (repo / "acme_widget.py").write_bytes(_python())
    _loaded_at_start(repo)
    assert refused_files.refusal_for(DRIVER_ID).reasons == ("RuntimeError: broke here",)


def test_a_file_that_loads_has_no_record(repo):
    path = repo / "acme_widget.avcdriver"
    path.write_bytes(_yaml(**BOGUS))
    _loaded_at_start(repo)
    path.write_bytes(_yaml(version="1.0.1"))
    _loaded_at_start(repo)
    assert registry.is_driver_registered(DRIVER_ID)
    assert refused_files.refusal_of(path) is None
    assert refused_files.refusal_for(DRIVER_ID) is None


def test_a_record_ends_when_its_file_changes_or_goes(repo):
    path = repo / "acme_widget.avcdriver"
    path.write_bytes(_yaml(**BOGUS))
    _loaded_at_start(repo)
    path.write_bytes(_yaml(**BOGUS) + b"# edited by hand\n")
    assert refused_files.refusal_for(DRIVER_ID) is None
    assert missing_driver_reason(DRIVER_ID) == f"Driver '{DRIVER_ID}' is not installed"

    _loaded_at_start(repo)
    assert refused_files.refusal_for(DRIVER_ID) is not None
    path.unlink()
    assert refused_files.refusal_for(DRIVER_ID) is None


# --- the readers -------------------------------------------------------------------


async def test_the_device_page_of_a_device_that_needs_it_says_why(repo):
    (repo / "acme_widget.avcdriver").write_bytes(_yaml(**BOGUS))
    _loaded_at_start(repo)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    await dm.add_device({"id": "d1", "driver": DRIVER_ID, "name": "D1", "config": {}})
    await dm.add_device({"id": "d2", "driver": "acme_absent", "name": "D2", "config": {}})

    assert state.get("device.d1.orphan_reason") == _sentence(_bogus_reasons())
    info = dm.get_device_info("d1")
    assert info["orphan_reason"] == _sentence(_bogus_reasons())
    assert info["driver_load_error"] == "; ".join(_bogus_reasons())
    absent = dm.get_device_info("d2")
    assert absent["orphan_reason"] == "Driver 'acme_absent' is not installed"
    assert "driver_load_error" not in absent


async def test_the_installed_list_says_why_a_file_did_not_load(repo, monkeypatch):
    monkeypatch.setattr(routes, "_get_driver_repo_dir", lambda: repo)
    (repo / "acme_widget.avcdriver").write_bytes(_yaml(**BOGUS))
    (repo / "acme_gadget.py").write_bytes(b"VALUE = 1\n")
    (repo / "acme_good.avcdriver").write_bytes(_yaml(id="acme_good"))
    _loaded_at_start(repo)

    listed = {entry["filename"]: entry for entry in (await routes.list_installed_community_drivers())["drivers"]}
    assert listed["acme_widget.avcdriver"]["load_error"] == "; ".join(_bogus_reasons())
    assert listed["acme_gadget.py"]["load_error"] == NO_DRIVER_CLASS
    assert listed["acme_good.avcdriver"]["load_error"] is None


def test_the_code_view_s_list_says_why_a_python_driver_did_not_load(repo):
    (repo / "acme_widget.py").write_bytes(_python("import acme_nonexistent_lib\n"))
    _loaded_at_start(repo)
    [entry] = driver_loader.list_python_drivers([repo])
    assert (entry["loaded"], entry["load_error"]) == (
        False, "ModuleNotFoundError: No module named 'acme_nonexistent_lib' (line 1)",
    )


def test_a_project_import_says_installed_but_did_not_load(repo):
    (repo / "acme_widget.avcdriver").write_bytes(_yaml(**BOGUS))
    _loaded_at_start(repo)
    warnings = project_library._missing_driver_warnings([
        {"driver_id": DRIVER_ID, "affected_devices": ["d1", "d2"]},
        {"driver_id": "acme_absent", "affected_devices": ["d3"]},
    ])
    assert warnings == [
        f"{_sentence(_bogus_reasons())} (used by: d1, d2)",
        "Driver 'acme_absent' is not installed (used by: d3)",
    ]


def test_a_project_bundle_s_driver_that_does_not_load_is_recorded(repo, monkeypatch):
    monkeypatch.setattr(project_library, "_DRIVER_REPO_DIR", repo)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("drivers/acme_widget.avcdriver", _yaml(**BOGUS))
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as archive:
        assert project_library._install_bundled_drivers(archive) == []
    assert (repo / "acme_widget.avcdriver").is_file()
    assert missing_driver_reason(DRIVER_ID) == _sentence(_bogus_reasons())


class _Tools(DriverToolsMixin):
    def __init__(self, repo: Path):
        self.repo = repo

    def _get_engine(self):
        return object()

    def _get_driver_repo_dir(self):
        return self.repo


async def test_the_ai_s_tools_say_installed_but_did_not_load(repo):
    (repo / "acme_widget.avcdriver").write_bytes(_yaml(**BOGUS))
    _loaded_at_start(repo)
    tools = _Tools(repo)

    [entry] = (await tools._get_installed_drivers({}))["drivers"]
    assert entry["load_error"] == "; ".join(_bogus_reasons())
    answer = await tools._get_driver_definition({"driver_id": DRIVER_ID})
    assert answer["error"].startswith(_sentence(_bogus_reasons()) + ".")
    missing = await tools._get_driver_definition({"driver_id": "acme_absent"})
    assert missing["error"].startswith("No driver 'acme_absent' is installed.")


@pytest.mark.parametrize("case", ["the checker", "a missing discovery file"])
async def test_the_builder_s_reload_answers_with_the_loader_s_reasons_and_records_them(
    repo, monkeypatch, case,
):
    monkeypatch.setattr(driver_definitions, "_get_driver_dirs", lambda: [repo])
    if case == "the checker":
        data, reasons = _yaml(**BOGUS), _bogus_reasons()
    else:
        data, reasons = YAML_REFUSALS["a missing discovery file"]
    (repo / "acme_widget.avcdriver").write_bytes(data)

    with pytest.raises(StructuredApiError) as caught:
        await driver_definitions.reload_driver_definition(DRIVER_ID)
    assert caught.value.status_code == 422
    assert tuple(caught.value.fields["errors"]) == reasons
    assert caught.value.detail.startswith("Driver file 'acme_widget.avcdriver' did not load. Fix th")
    assert "server log" not in caught.value.detail
    assert refused_files.refusal_for(DRIVER_ID).reasons == reasons


# --- a change refused before it reaches the folder ---------------------------------


def test_a_refused_change_carries_the_loader_s_reasons_and_records_nothing(repo):
    with pytest.raises(DriverRefused) as caught:
        admit(DriverChange(repo, "acme_widget.avcdriver", {"acme_widget.avcdriver": _yaml(**BOGUS)}))
    assert caught.value.errors == _bogus_reasons()
    assert refused_files.refusal_for(DRIVER_ID) is None

    with pytest.raises(DriverRefused) as caught:
        admit(DriverChange(repo, "acme_widget.py", {"acme_widget.py": _python("import acme_nonexistent_lib\n")}))
    assert caught.value.errors == ("ModuleNotFoundError: No module named 'acme_nonexistent_lib' (line 1)",)
    assert ".staging-" not in caught.value.errors[0]
    assert refused_files.refusal_for(DRIVER_ID) is None


async def test_a_file_refused_at_start_then_installed_over_has_no_load_error_anywhere(repo, monkeypatch):
    monkeypatch.setattr(routes, "_get_driver_repo_dir", lambda: repo)
    path = repo / "acme_widget.avcdriver"
    path.write_bytes(_yaml(**BOGUS))
    _loaded_at_start(repo)

    admit(DriverChange(repo, "acme_widget.avcdriver", {"acme_widget.avcdriver": _yaml()}))
    assert refused_files.refusal_of(path) is None
    assert refused_files.refusal_for(DRIVER_ID) is None
    [entry] = (await routes.list_installed_community_drivers())["drivers"]
    assert entry["load_error"] is None


# --- the doors answer with the reasons ---------------------------------------------


class _Upload:
    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _form(filename: str, data: bytes):
    class _Request:
        async def form(self):
            return {"file": _Upload(filename, data)}

    return _Request()


@pytest.fixture
def doors(repo, monkeypatch):
    monkeypatch.setattr(routes, "_get_driver_repo_dir", lambda: repo)

    async def _nothing(*_args, **_kwargs):
        return []

    monkeypatch.setattr(driver_change, "_running_engine", lambda: None)
    monkeypatch.setattr("openavc.api.discovery.refresh_all_device_matches", _nothing)
    return repo


async def test_an_import_of_the_finding_s_file_answers_with_the_checker_s_reasons(doors):
    with pytest.raises(StructuredApiError) as caught:
        await routes.upload_driver(_form("acme_widget.avcdriver", _yaml(**BOGUS)))
    refusal = caught.value
    assert refusal.status_code == 422
    assert refusal.detail == "The driver was not imported. Fix the driver file first:\n" + "\n".join(_bogus_reasons())
    assert tuple(refusal.fields["errors"]) == _bogus_reasons()
    assert list(doors.iterdir()) == []
    assert refused_files.refusal_for(DRIVER_ID) is None


async def test_an_import_of_a_python_file_that_does_not_import_answers_with_its_error(doors):
    with pytest.raises(StructuredApiError) as caught:
        await routes.upload_driver(_form("acme_widget.py", _python("import acme_nonexistent_lib\n")))
    assert caught.value.detail == (
        "The driver was not imported. Fix the driver file first:\n"
        "ModuleNotFoundError: No module named 'acme_nonexistent_lib' (line 1)"
    )


async def test_a_file_the_loader_takes_is_still_imported(doors):
    answer = await routes.upload_driver(_form("acme_widget.avcdriver", _yaml()))
    assert answer["status"] == "uploaded"
    assert registry.is_driver_registered(DRIVER_ID)


async def test_the_missing_drivers_answer_says_which_did_not_load(repo, monkeypatch):
    from types import SimpleNamespace

    from openavc.api.routes import devices as device_routes
    from openavc.discovery.community_index import CommunityIndexCache

    (repo / "acme_widget.avcdriver").write_bytes(_yaml(**BOGUS))
    _loaded_at_start(repo)
    engine = SimpleNamespace(
        project=None,
        devices=SimpleNamespace(
            get_missing_drivers=lambda: [DRIVER_ID, "acme_absent"],
            list_devices=lambda: [
                {"id": "d1", "driver": DRIVER_ID, "orphaned": True},
                {"id": "d2", "driver": "acme_absent", "orphaned": True},
            ],
        ),
        installed_equipment=SimpleNamespace(describe_missing_reference=lambda driver_id, project: None),
    )
    monkeypatch.setattr(device_routes, "_get_engine", lambda: engine)

    async def _no_catalog(self, *args, **kwargs):
        return []

    monkeypatch.setattr(CommunityIndexCache, "get_drivers", _no_catalog)
    monkeypatch.delattr(device_routes.list_missing_drivers, "_cache", raising=False)

    missing = {m["driver_id"]: m for m in (await device_routes.list_missing_drivers())["missing"]}
    assert missing[DRIVER_ID]["load_error"] == "; ".join(_bogus_reasons())
    assert "load_error" not in missing["acme_absent"]
