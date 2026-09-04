"""Settings the PROJECT carries, and the line around what may be one.

A customer with a hundred panels sets these once and deploys one template.
Before this block they were per-box, so every panel needed a visit.

The important test here is not that the values round-trip -- it is
`test_no_unsafe_setting_has_been_added`. A project file is a MOVABLE artifact:
it arrives from a cloud template, an import, a backup restore, and the AI. So
the blast radius of a bad value is the whole fleet at once, and the rule is
that a setting may only live here when its worst case is "the panel looks
wrong". Anything whose worst case is "the box cannot be reached" or "the wrong
people can reach it" stays in system.json.
"""

import pytest

from openavc.core.project_loader import (
    DeviceSettings,
    DisplaySettings,
    ProjectConfig,
    ProjectSettings,
)
from openavc.core.project_migration import CURRENT_VERSION, migrate_project


# ── the boundary ────────────────────────────────────────────────────────────

# Every field the project is allowed to carry. Adding a line here is a claim
# that a hostile or merely wrong value in this field cannot cost anyone a
# panel they can no longer reach.
ALLOWED = {
    "display.idle_dim_enabled",
    "display.idle_dim_timeout_seconds",
    "display.idle_dim_level_percent",
    "display.idle_dim_wake_passes_touch",
    "display.idle_dim_hold_state_key",
    "display.brightness_percent",
    "devices.reconnect_interval_seconds",
}


def _actual_fields() -> set[str]:
    out = set()
    for block, model in ProjectSettings.model_fields.items():
        for name in model.annotation.model_fields:
            out.add(f"{block}.{name}")
    return out


def test_no_unsafe_setting_has_been_added():
    """The whole point of the block, guarded.

    If this fails because you added a field, the question to answer is not
    "is it useful" but "what happens when a hundred panels receive a wrong
    value for it, and can the person standing in front of one undo it".
    """
    assert _actual_fields() == ALLOWED, (
        "The project's settings block changed.\n"
        "  Added: " + str(sorted(_actual_fields() - ALLOWED)) + "\n"
        "  Gone:  " + str(sorted(ALLOWED - _actual_fields())) + "\n"
        "A project file travels (template, import, backup restore, AI). A "
        "setting belongs here only when its worst case is a panel that looks "
        "wrong -- never one that cannot be reached."
    )


@pytest.mark.parametrize(
    "forbidden",
    ["network", "auth", "cloud", "tls", "isc", "updates", "logging", "simulation"],
)
def test_the_dangerous_sections_never_became_project_settings(forbidden):
    """Named individually so the reason each one is out survives.

    network: a pushed bind_address strands every panel at once, and nothing on
    them can undo it. auth: credentials. cloud: `system_key` IS the device's
    identity, so one project would give a hundred panels the same one. tls:
    certificates are per-device. updates: `channel` is a real fleet lever and
    belongs in cloud fleet operations, not hidden in a room file where a
    restored old backup moves a site onto beta with nobody noticing.
    """
    assert forbidden not in ProjectSettings.model_fields


# ── defaults ────────────────────────────────────────────────────────────────

def test_the_dim_is_on_by_default():
    """Off-by-default meant every panel needed a visit to switch it on, which
    is the opposite of what a burn-in guard is for."""
    d = DisplaySettings()
    assert d.idle_dim_enabled is True
    assert d.idle_dim_timeout_seconds == 300
    assert d.idle_dim_level_percent == 20


def test_brightness_defaults_to_not_managed():
    """None is not 100. Every panel is unmanaged until somebody sets it, and
    reading that as "set me to full" would brighten every panel on update."""
    assert DisplaySettings().brightness_percent is None


def test_reconnect_interval_defaults_to_deferring_to_the_instance():
    assert DeviceSettings().reconnect_interval_seconds is None


def test_a_project_with_no_settings_block_gets_the_defaults():
    p = ProjectConfig(project={"id": "p1", "name": "t"})
    assert p.settings.display.idle_dim_enabled is True
    assert p.settings.devices.reconnect_interval_seconds is None


# ── migration ───────────────────────────────────────────────────────────────

def test_migration_stamps_the_version_and_writes_nothing():
    """An ABSENT block and a block full of today's defaults mean the same
    thing now, but only the absent one still means "nobody chose" if a default
    ever changes. Writing them in would freeze today's numbers into every
    project that had been opened once."""
    data, changed = migrate_project({"openavc_version": "0.12.0", "project": {"id": "p1", "name": "t"}})
    assert changed is True
    assert data["openavc_version"] == "0.13.0"
    assert "settings" not in data


def test_an_old_project_migrates_all_the_way_up():
    data, _ = migrate_project({"openavc_version": "0.1.0", "project": {"id": "p1", "name": "t"}})
    assert data["openavc_version"] == CURRENT_VERSION == "0.13.0"


def test_a_project_that_already_chose_keeps_its_choice():
    data, _ = migrate_project({
        "openavc_version": "0.12.0",
        "project": {"id": "p1", "name": "t"},
        "settings": {"display": {"idle_dim_enabled": False, "idle_dim_level_percent": 0}},
    })
    p = ProjectConfig(**data)
    assert p.settings.display.idle_dim_enabled is False
    assert p.settings.display.idle_dim_level_percent == 0


# ── round trip ──────────────────────────────────────────────────────────────

def test_the_settings_survive_a_save_and_reload():
    """The template case in one line: what is set here is what a panel on the
    other end receives."""
    p = ProjectConfig(
        project={"id": "p1", "name": "t"},
        settings={
            "display": {
                "idle_dim_timeout_seconds": 600,
                "idle_dim_level_percent": 0,
                "brightness_percent": 65,
                "idle_dim_hold_state_key": "var.in_use",
            },
            "devices": {"reconnect_interval_seconds": 30},
        },
    )
    again = ProjectConfig(**p.model_dump())
    assert again.settings.display.idle_dim_timeout_seconds == 600
    assert again.settings.display.idle_dim_level_percent == 0
    assert again.settings.display.brightness_percent == 65
    assert again.settings.display.idle_dim_hold_state_key == "var.in_use"
    assert again.settings.devices.reconnect_interval_seconds == 30
