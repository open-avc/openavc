"""What the driver-definition routes, the installed listing and the help route
do today, held exactly.

The definition routes (list, get, create, replace, patch, delete, reload) are
the Driver Builder's doors. Each save refuses in a fixed order (the first
refusal that applies wins, and nothing is written), then saves, removes the
other copies of the id, registers the class, reconnects the devices on the
driver and tells every Programmer, in that order. These pins record each door's
refusals, its side effects in order with the ids they carry, and the answer,
so the routes can be restructured without any of it moving.

The installed listing reads the driver folder as it is today, including what
it does with a file that is not UTF-8 (it raises).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import HTTPException

import openavc.drivers.driver_loader as driver_loader
import openavc.system_config as system_config
from openavc.api.models import DriverDefinitionRequest
from openavc.api.routes import driver_definitions as ROUTES
from openavc.api.routes import drivers as INSTALL_ROUTES
from openavc.drivers.driver_loader import save_driver_definition

WIDGET = {"id": "acme_widget", "name": "Acme Widget", "transport": "tcp",
          "commands": {"power_on": {"send": "PWR ON\\r"}}}
GADGET = {"id": "acme_gadget", "name": "Acme Gadget", "transport": "tcp",
          "commands": {"power_on": {"send": "GPWR 1\\r"}}}
TOO_NEW = "99.0.0"


class Env(SimpleNamespace):
    def files(self) -> list[str]:
        return sorted(p.name for p in self.repo.iterdir())


@pytest.fixture()
def env(tmp_path: Path, monkeypatch) -> Env:
    builtin = tmp_path / "definitions"
    repo = tmp_path / "driver_repo"
    builtin.mkdir()
    repo.mkdir()
    e = Env(builtin=builtin, repo=repo, events=[], restored=False, register_error=None)

    monkeypatch.setattr(ROUTES, "_get_driver_dirs", lambda: (builtin, repo))
    monkeypatch.setattr(system_config, "DRIVER_DEFINITIONS_DIR", builtin)

    def _register(cls):
        e.events.append(("register", cls.DRIVER_INFO["id"], tuple(e.files())))
        if e.register_error is not None:
            raise e.register_error

    async def _reload_driver(driver_id: str) -> list[str]:
        e.events.append(("reload", driver_id))
        return [f"{driver_id}-dev"]

    async def _announce(driver_id: str | None = None) -> None:
        e.events.append(("announce", driver_id))

    def _restore(driver_id, dirs):
        e.events.append(("restore", driver_id, tuple(Path(d) for d in dirs)))
        return e.restored

    monkeypatch.setattr(ROUTES, "register_driver", _register)
    monkeypatch.setattr(
        ROUTES, "_get_engine",
        lambda: SimpleNamespace(devices=SimpleNamespace(reload_driver=_reload_driver)),
    )
    monkeypatch.setattr(ROUTES, "announce_drivers_changed", _announce)
    monkeypatch.setattr(driver_loader, "restore_driver_registration", _restore)
    return e


def _refusal(exc: pytest.ExceptionInfo[HTTPException]) -> tuple[int, str, list[str] | None]:
    return (
        exc.value.status_code,
        exc.value.detail,
        getattr(exc.value, "fields", {}).get("errors"),
    )


def _saved(env: Env, name: str) -> dict:
    return yaml.safe_load((env.repo / name).read_text(encoding="utf-8"))


# --- GET /driver-definitions ------------------------------------------------


async def test_the_listing_tags_each_definition_by_the_folder_it_came_from(env):
    save_driver_definition(dict(WIDGET), env.builtin)
    save_driver_definition(dict(GADGET), env.repo)

    listing = (await ROUTES.list_driver_definitions())["definitions"]

    by_id = {d["id"]: d for d in listing}
    assert [d["id"] for d in listing] == ["acme_widget", "acme_gadget"]
    assert by_id["acme_widget"]["source"] == "builtin"
    assert by_id["acme_gadget"]["source"] == "user"
    assert all("_source_file" not in d for d in listing)
    assert all("_comment_lines" in d for d in listing)


async def test_the_listing_reads_built_in_as_a_path_that_starts_with_the_folders(env, tmp_path, monkeypatch):
    """A folder whose path only starts with the built-in folder's path reads
    as built-in: the tag compares text, not folders."""
    sibling = tmp_path / "definitions_extra"
    sibling.mkdir()
    save_driver_definition(dict(GADGET), sibling)
    monkeypatch.setattr(ROUTES, "_get_driver_dirs", lambda: (env.builtin, sibling))

    listing = (await ROUTES.list_driver_definitions())["definitions"]

    assert [(d["id"], d["source"]) for d in listing] == [("acme_gadget", "builtin")]


async def test_the_listing_tags_a_path_it_cannot_resolve_as_user(env, monkeypatch):
    saved = save_driver_definition(dict(GADGET), env.repo)
    real_resolve = Path.resolve

    def _resolve(self, *args, **kwargs):
        if self.name == saved.name:
            raise OSError("gone")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", _resolve)

    listing = (await ROUTES.list_driver_definitions())["definitions"]

    assert [(d["id"], d["source"]) for d in listing] == [("acme_gadget", "user")]


# --- GET /driver-definitions/{id} --------------------------------------------


async def test_get_answers_the_definition_without_its_source_file(env):
    save_driver_definition(dict(WIDGET), env.builtin)
    save_driver_definition(dict(GADGET), env.repo)

    answer = await ROUTES.get_driver_definition("acme_gadget")

    assert answer["id"] == "acme_gadget"
    assert answer["name"] == "Acme Gadget"
    assert "_source_file" not in answer
    assert "_comment_lines" in answer
    assert "source" not in answer


async def test_get_of_an_unknown_id_is_a_404(env):
    with pytest.raises(HTTPException) as exc:
        await ROUTES.get_driver_definition("acme_nothing")

    assert (exc.value.status_code, exc.value.detail) == (
        404, "Driver definition 'acme_nothing' not found",
    )


# --- POST /driver-definitions --------------------------------------------------


async def test_create_saves_registers_reconnects_and_announces_in_that_order(env):
    body = DriverDefinitionRequest(**{**GADGET, "source": "user", "_source_file": "x"})

    answer = await ROUTES.create_driver_definition(body)

    assert answer == {
        "status": "created", "driver_id": "acme_gadget",
        "devices_reconnected": ["acme_gadget-dev"],
    }
    assert env.events == [
        ("register", "acme_gadget", ("acme_gadget.avcdriver",)),
        ("reload", "acme_gadget"),
        ("announce", "acme_gadget"),
    ]
    saved = _saved(env, "acme_gadget.avcdriver")
    assert "source" not in saved and "_source_file" not in saved
    assert "manufacturer" not in saved  # only what the client sent


async def test_create_refuses_a_taken_id_before_checking_the_definition(env):
    save_driver_definition(dict(GADGET), env.builtin)

    with pytest.raises(HTTPException) as exc:
        await ROUTES.create_driver_definition(
            DriverDefinitionRequest(**{**GADGET, "bogus_key": 1, "min_platform_version": TOO_NEW})
        )

    status, _detail, errors = _refusal(exc)
    assert (status, errors) == (
        409, ['Another driver named "acme_gadget" already exists. Choose a different ID.'],
    )
    assert env.files() == [] and env.events == []


async def test_create_refuses_an_invalid_definition_before_its_floor(env):
    with pytest.raises(HTTPException) as exc:
        await ROUTES.create_driver_definition(
            DriverDefinitionRequest(**{**GADGET, "bogus_key": 1, "min_platform_version": TOO_NEW})
        )

    status, detail, errors = _refusal(exc)
    assert status == 422
    assert detail.startswith("The driver was not saved. Fix this first:\n")
    assert errors and any("bogus_key" in e for e in errors)
    assert env.files() == [] and env.events == []


async def test_create_refuses_a_floor_above_this_platform(env):
    with pytest.raises(HTTPException) as exc:
        await ROUTES.create_driver_definition(
            DriverDefinitionRequest(**{**GADGET, "min_platform_version": TOO_NEW})
        )

    assert exc.value.status_code == 422
    assert exc.value.detail.startswith("This driver requires OpenAVC 99.0.0 or later.")
    assert env.files() == [] and env.events == []


# --- PUT /driver-definitions/{id} ----------------------------------------------


async def test_replace_of_an_unknown_id_is_a_404_before_anything_else(env):
    save_driver_definition(dict(WIDGET), env.builtin)

    with pytest.raises(HTTPException) as exc:
        await ROUTES.update_driver_definition(
            "acme_nothing", DriverDefinitionRequest(**{**WIDGET, "bogus_key": 1})
        )

    assert (exc.value.status_code, exc.value.detail) == (
        404, "Driver definition 'acme_nothing' not found",
    )
    assert env.events == []


async def test_replace_of_a_built_in_is_refused_before_the_rename_checks(env):
    save_driver_definition(dict(WIDGET), env.builtin)
    save_driver_definition(dict(GADGET), env.builtin)

    with pytest.raises(HTTPException) as exc:
        await ROUTES.update_driver_definition(
            "acme_widget", DriverDefinitionRequest(**{**GADGET, "bogus_key": 1})
        )

    assert (exc.value.status_code, exc.value.detail) == (
        403, "Built-in drivers are read-only. Customize a copy to edit.",
    )
    assert env.files() == [] and env.events == []


async def test_a_rename_onto_a_built_ins_id_is_refused_before_the_taken_id(env):
    save_driver_definition(dict(WIDGET), env.repo)
    save_driver_definition(dict(GADGET), env.builtin)

    with pytest.raises(HTTPException) as exc:
        await ROUTES.update_driver_definition("acme_widget", DriverDefinitionRequest(**GADGET))

    assert (exc.value.status_code, exc.value.detail) == (
        403, "Driver id 'acme_gadget' belongs to a read-only built-in driver.",
    )
    assert env.files() == ["acme_widget.avcdriver"] and env.events == []


async def test_a_rename_onto_a_taken_id_is_refused_before_the_definition_check(env):
    save_driver_definition(dict(WIDGET), env.repo)
    save_driver_definition(dict(GADGET), env.repo)

    with pytest.raises(HTTPException) as exc:
        await ROUTES.update_driver_definition(
            "acme_widget", DriverDefinitionRequest(**{**GADGET, "bogus_key": 1})
        )

    status, _detail, errors = _refusal(exc)
    assert (status, errors) == (
        409, ['Another driver named "acme_gadget" already exists. Choose a different ID.'],
    )
    assert env.events == []


async def test_replace_refuses_an_invalid_definition_before_its_floor(env):
    save_driver_definition(dict(WIDGET), env.repo)

    with pytest.raises(HTTPException) as exc:
        await ROUTES.update_driver_definition(
            "acme_widget",
            DriverDefinitionRequest(**{**WIDGET, "bogus_key": 1, "min_platform_version": TOO_NEW}),
        )

    status, detail, errors = _refusal(exc)
    assert status == 422 and detail.startswith("The driver was not saved.")
    assert errors and any("bogus_key" in e for e in errors)
    assert _saved(env, "acme_widget.avcdriver") == WIDGET
    assert env.events == []


async def test_replace_refuses_a_floor_above_this_platform(env):
    save_driver_definition(dict(WIDGET), env.repo)

    with pytest.raises(HTTPException) as exc:
        await ROUTES.update_driver_definition(
            "acme_widget", DriverDefinitionRequest(**{**WIDGET, "min_platform_version": TOO_NEW})
        )

    assert exc.value.status_code == 422
    assert exc.value.detail.startswith("This driver requires OpenAVC 99.0.0 or later.")
    assert _saved(env, "acme_widget.avcdriver") == WIDGET
    assert env.events == []


async def test_replace_in_place_removes_other_copies_and_reconnects_once(env):
    save_driver_definition(dict(WIDGET), env.repo)
    (env.repo / "widget_copy.avcdriver").write_text(yaml.safe_dump(WIDGET), encoding="utf-8")

    answer = await ROUTES.update_driver_definition(
        "acme_widget", DriverDefinitionRequest(**{**WIDGET, "name": "Acme Widget 2", "source": "user"})
    )

    assert answer == {
        "status": "updated", "driver_id": "acme_widget",
        "devices_reconnected": ["acme_widget-dev"],
    }
    assert env.events == [
        ("register", "acme_widget", ("acme_widget.avcdriver",)),
        ("reload", "acme_widget"),
        ("announce", "acme_widget"),
    ]
    assert _saved(env, "acme_widget.avcdriver")["name"] == "Acme Widget 2"
    assert "source" not in _saved(env, "acme_widget.avcdriver")


async def test_a_rename_restores_the_old_id_and_reconnects_both(env):
    save_driver_definition(dict(WIDGET), env.repo)

    answer = await ROUTES.update_driver_definition(
        "acme_widget", DriverDefinitionRequest(**{**WIDGET, "id": "acme_widget_two"})
    )

    assert answer == {
        "status": "updated", "driver_id": "acme_widget_two",
        "devices_reconnected": ["acme_widget_two-dev", "acme_widget-dev"],
    }
    assert env.events == [
        ("register", "acme_widget_two", ("acme_widget_two.avcdriver",)),
        ("reload", "acme_widget_two"),
        ("restore", "acme_widget", (env.builtin, env.repo)),
        ("reload", "acme_widget"),
        ("announce", "acme_widget_two"),
    ]


async def test_a_class_that_fails_to_register_after_the_save_leaves_the_new_file(env):
    save_driver_definition(dict(WIDGET), env.repo)
    (env.repo / "widget_copy.avcdriver").write_text(yaml.safe_dump(WIDGET), encoding="utf-8")
    env.register_error = RuntimeError("no class")

    with pytest.raises(RuntimeError, match="no class"):
        await ROUTES.update_driver_definition(
            "acme_widget", DriverDefinitionRequest(**{**WIDGET, "name": "Acme Widget 2"})
        )

    assert env.files() == ["acme_widget.avcdriver"]
    assert _saved(env, "acme_widget.avcdriver")["name"] == "Acme Widget 2"
    assert env.events == [("register", "acme_widget", ("acme_widget.avcdriver",))]


# --- PATCH /driver-definitions/{id} --------------------------------------------


async def test_patch_of_an_unknown_id_is_a_404(env):
    with pytest.raises(HTTPException) as exc:
        await ROUTES.patch_driver_definition("acme_nothing", {"bogus_key": 1})

    assert (exc.value.status_code, exc.value.detail) == (
        404, "Driver definition 'acme_nothing' not found",
    )
    assert env.events == []


async def test_patch_of_a_built_in_is_refused_before_the_merge_is_checked(env):
    save_driver_definition(dict(WIDGET), env.builtin)

    with pytest.raises(HTTPException) as exc:
        await ROUTES.patch_driver_definition("acme_widget", {"bogus_key": 1})

    assert (exc.value.status_code, exc.value.detail) == (
        403, "Built-in drivers are read-only. Customize a copy to edit.",
    )
    assert env.files() == [] and env.events == []


async def test_patch_refuses_an_invalid_merge_before_its_floor(env):
    save_driver_definition(dict(WIDGET), env.repo)

    with pytest.raises(HTTPException) as exc:
        await ROUTES.patch_driver_definition(
            "acme_widget", {"bogus_key": 1, "min_platform_version": TOO_NEW}
        )

    status, _detail, errors = _refusal(exc)
    assert status == 422 and errors and any("bogus_key" in e for e in errors)
    assert _saved(env, "acme_widget.avcdriver") == WIDGET
    assert env.events == []


async def test_patch_refuses_a_merged_floor_above_this_platform(env):
    save_driver_definition({**WIDGET, "min_platform_version": TOO_NEW}, env.repo)

    with pytest.raises(HTTPException) as exc:
        await ROUTES.patch_driver_definition("acme_widget", {"name": "Renamed"})

    assert exc.value.status_code == 422
    assert exc.value.detail.startswith("This driver requires OpenAVC 99.0.0 or later.")
    assert env.events == []


async def test_patch_keeps_the_id_strips_the_listing_keys_and_saves_the_merge(env):
    save_driver_definition({**WIDGET, "min_platform_version": TOO_NEW}, env.repo)
    (env.repo / "widget_copy.avcdriver").write_text(yaml.safe_dump(WIDGET), encoding="utf-8")

    answer = await ROUTES.patch_driver_definition("acme_widget", {
        "id": "acme_other", "name": "Patched", "min_platform_version": None,
        "source": "user", "_source_file": "x", "_comment_lines": 3,
    })

    assert answer == {
        "status": "updated", "driver_id": "acme_widget",
        "devices_reconnected": ["acme_widget-dev"],
    }
    assert env.events == [
        ("register", "acme_widget", ("acme_widget.avcdriver",)),
        ("reload", "acme_widget"),
        ("announce", "acme_widget"),
    ]
    assert _saved(env, "acme_widget.avcdriver") == {**WIDGET, "name": "Patched"}


# --- DELETE /driver-definitions/{id} -------------------------------------------


async def test_delete_of_a_built_in_is_refused(env):
    save_driver_definition(dict(WIDGET), env.builtin)

    with pytest.raises(HTTPException) as exc:
        await ROUTES.delete_driver_definition_endpoint("acme_widget")

    assert (exc.value.status_code, exc.value.detail) == (403, "Built-in drivers can't be deleted.")
    assert env.events == []


async def test_delete_of_an_unknown_id_is_a_404(env):
    with pytest.raises(HTTPException) as exc:
        await ROUTES.delete_driver_definition_endpoint("acme_nothing")

    assert (exc.value.status_code, exc.value.detail) == (
        404, "Driver definition 'acme_nothing' not found",
    )
    assert env.events == []


@pytest.mark.parametrize("restored", [False, True])
async def test_delete_restores_reconnects_only_a_restored_id_and_announces(env, restored):
    save_driver_definition(dict(WIDGET), env.repo)
    env.restored = restored

    answer = await ROUTES.delete_driver_definition_endpoint("acme_widget")

    assert answer == {"status": "deleted", "driver_id": "acme_widget", "builtin_restored": restored}
    assert env.files() == []
    assert env.events == [
        ("restore", "acme_widget", (env.builtin, env.repo)),
        *([("reload", "acme_widget")] if restored else []),
        ("announce", "acme_widget"),
    ]


# --- POST /driver-definitions/{id}/reload ---------------------------------------


async def test_reload_of_an_id_with_no_file_is_a_404(env):
    with pytest.raises(HTTPException) as exc:
        await ROUTES.reload_driver_definition("acme_nothing")

    assert (exc.value.status_code, exc.value.detail) == (
        404, "No .avcdriver file declaring 'acme_nothing' found on disk",
    )
    assert env.events == []


@pytest.mark.parametrize(
    "load_answer, raw, errors",
    [
        ("invalid", {**WIDGET, "transport": 7}, "validator"),
        ("invalid", ["not", "a", "mapping"], ["not a YAML mapping"]),
        ("invalid", "unreadable", []),
    ],
    ids=["the validator's errors", "not a mapping", "unreadable"],
)
async def test_reload_of_a_file_that_does_not_load_names_the_file(env, monkeypatch, load_answer, raw, errors):
    path = env.repo / "acme_widget.avcdriver"
    path.write_text(yaml.safe_dump(WIDGET), encoding="utf-8")
    monkeypatch.setattr(driver_loader, "load_driver_file", lambda fp: None)
    if raw == "unreadable":
        path.write_text("key: [unclosed", encoding="utf-8")
        monkeypatch.setattr(driver_loader, "find_driver_file_by_id", lambda dirs, did: path)
    else:
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        monkeypatch.setattr(driver_loader, "find_driver_file_by_id", lambda dirs, did: path)
    if errors == "validator":
        errors = driver_loader.validate_driver_definition(raw)
        assert errors

    with pytest.raises(HTTPException) as exc:
        await ROUTES.reload_driver_definition("acme_widget")

    status, detail, got = _refusal(exc)
    preamble = (
        "Driver file 'acme_widget.avcdriver' is invalid or its discovery companion "
        "is missing (see server log)"
    )
    assert status == 422 and got == errors
    assert detail == (f"{preamble}:\n" + "\n".join(errors) if errors else preamble)
    assert env.events == []


async def test_reload_registers_and_reconnects_without_announcing(env):
    save_driver_definition(dict(WIDGET), env.repo)

    answer = await ROUTES.reload_driver_definition("acme_widget")

    assert answer == {
        "status": "reloaded", "driver_id": "acme_widget",
        "file": "acme_widget.avcdriver", "devices_reconnected": ["acme_widget-dev"],
    }
    assert env.events == [
        ("register", "acme_widget", ("acme_widget.avcdriver",)),
        ("reload", "acme_widget"),
    ]


async def test_reload_of_a_file_now_declaring_another_id_reconnects_both(env, monkeypatch):
    path = save_driver_definition({**WIDGET, "id": "acme_renamed"}, env.repo)
    monkeypatch.setattr(driver_loader, "find_driver_file_by_id", lambda dirs, did: path)

    answer = await ROUTES.reload_driver_definition("acme_widget")

    assert answer == {
        "status": "reloaded", "driver_id": "acme_renamed", "file": "acme_renamed.avcdriver",
        "devices_reconnected": ["acme_renamed-dev", "acme_widget-dev"],
    }
    assert env.events == [
        ("register", "acme_renamed", ("acme_renamed.avcdriver",)),
        ("reload", "acme_renamed"),
        ("reload", "acme_widget"),
    ]


# --- GET /drivers/installed ------------------------------------------------------


@pytest.fixture()
def installed(tmp_path: Path, monkeypatch):
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    monkeypatch.setattr(INSTALL_ROUTES, "_get_driver_repo_dir", lambda: repo)
    classes: list[tuple[str, type]] = []
    monkeypatch.setattr(INSTALL_ROUTES, "registered_driver_classes", lambda: list(classes))
    return SimpleNamespace(repo=repo, classes=classes)


def _driver_class(module: str, info: dict) -> type:
    return type("Driver", (), {"__module__": module, "DRIVER_INFO": info})


async def test_installed_is_empty_without_a_driver_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(INSTALL_ROUTES, "_get_driver_repo_dir", lambda: tmp_path / "missing")

    assert await INSTALL_ROUTES.list_installed_community_drivers() == {"drivers": []}


async def test_installed_lists_definitions_then_python_drivers_by_file_name(installed):
    repo = installed.repo
    (repo / "b_widget.avcdriver").write_text(
        yaml.safe_dump({"id": "acme_widget", "name": "Acme Widget", "version": "2.1.0"}), encoding="utf-8")
    (repo / "a_bare.avcdriver").write_text(yaml.safe_dump({"transport": "tcp"}), encoding="utf-8")
    (repo / "c_list.avcdriver").write_text(yaml.safe_dump(["not", "a", "mapping"]), encoding="utf-8")
    (repo / "d_broken.avcdriver").write_text("key: [unclosed", encoding="utf-8")
    (repo / "acme_box.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "acme_box_sim.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "acme_box_discovery.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "_helper.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "acme_cube.py").write_text("x = 1\n", encoding="utf-8")
    installed.classes.extend([
        ("other", _driver_class("openavc_driver_other", {"id": "other"})),
        ("noid", _driver_class("openavc_driver_acme_cube", {"name": "No Id"})),
        ("cube", _driver_class("openavc_driver_acme_cube", {"id": "acme_cube_id", "name": "Acme Cube", "version": "3.0"})),
        ("cube2", _driver_class("openavc_driver_acme_cube", {"id": "second", "name": "Second"})),
    ])

    answer = await INSTALL_ROUTES.list_installed_community_drivers()

    assert answer == {"drivers": [
        {"id": "a_bare", "name": "a_bare", "format": "avcdriver", "filename": "a_bare.avcdriver", "version": ""},
        {"id": "acme_widget", "name": "Acme Widget", "format": "avcdriver", "filename": "b_widget.avcdriver",
         "version": "2.1.0"},
        {"id": "d_broken", "name": "d_broken", "format": "avcdriver", "filename": "d_broken.avcdriver", "version": ""},
        {"id": "acme_box", "name": "Acme Box", "format": "python", "filename": "acme_box.py", "version": ""},
        {"id": "acme_cube_id", "name": "Acme Cube", "format": "python", "filename": "acme_cube.py",
         "version": "3.0"},
    ]}


async def test_installed_matches_a_python_driver_by_its_name_inside_the_module_name(installed):
    """The match is text inside text: ``box`` matches a class loaded from
    ``openavc_driver_acme_box``, and a class without a name keeps the file's."""
    (installed.repo / "box.py").write_text("x = 1\n", encoding="utf-8")
    installed.classes.append(("acme_box", _driver_class("openavc_driver_acme_box", {"id": "acme_box"})))

    answer = await INSTALL_ROUTES.list_installed_community_drivers()

    assert answer == {"drivers": [
        {"id": "acme_box", "name": "Box", "format": "python", "filename": "box.py", "version": ""},
    ]}


async def test_installed_raises_on_a_definition_that_is_not_utf8(installed):
    (installed.repo / "acme_widget.avcdriver").write_bytes(b"id: acme_widget\nname: Caf\xe9\n")

    with pytest.raises(UnicodeDecodeError):
        await INSTALL_ROUTES.list_installed_community_drivers()


# --- GET /drivers/{id}/help ------------------------------------------------------


@pytest.mark.parametrize(
    "drivers, answer",
    [
        ([{"id": "acme_widget", "help": {"overview": "O", "setup": "S"}}],
         {"driver_id": "acme_widget", "overview": "O", "setup": "S"}),
        ([{"id": "acme_widget", "help": {"overview": "O"}}],
         {"driver_id": "acme_widget", "overview": "O", "setup": ""}),
        ([{"id": "acme_widget", "help": "text"}], (404, "Driver has no help information")),
        ([{"id": "acme_widget"}, {"id": "acme_widget", "help": {"overview": "late"}}],
         (404, "Driver has no help information")),
        ([{"id": "acme_gadget", "help": {"overview": "O"}}], (404, "Driver not found")),
    ],
    ids=["both", "overview only", "not a mapping", "first match wins", "unknown"],
)
async def test_help_answers_the_first_registered_match(monkeypatch, drivers, answer):
    monkeypatch.setattr(INSTALL_ROUTES, "list_registered_drivers", lambda: drivers)

    if isinstance(answer, tuple):
        with pytest.raises(HTTPException) as exc:
            await INSTALL_ROUTES.get_driver_help("acme_widget")
        assert (exc.value.status_code, exc.value.detail) == answer
    else:
        assert await INSTALL_ROUTES.get_driver_help("acme_widget") == answer


def test_this_file_reaches_the_module_that_defines_each_route():
    """A patch on the wrong module would leave the real folders and registry
    in reach; every route these pins drive is defined where they patch."""
    for name in ("list_driver_definitions", "get_driver_definition", "create_driver_definition",
                 "update_driver_definition", "patch_driver_definition",
                 "delete_driver_definition_endpoint", "reload_driver_definition"):
        assert getattr(ROUTES, name).__module__ == ROUTES.__name__
    for name in ("list_installed_community_drivers", "get_driver_help"):
        assert getattr(INSTALL_ROUTES, name).__module__ == INSTALL_ROUTES.__name__
    assert sys.modules[ROUTES.__name__] is ROUTES
