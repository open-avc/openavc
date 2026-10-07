"""The hardened deployment profile (docs/hardened-deployment.md) against the code.

The page hands IT a block of system.json to set, a table explaining each value,
a table of environment variables and the Settings labels to look for. Each of
those is a claim about this code, so each is read back from the page and checked
here: a renamed key, a changed type, a retired field, an environment variable
that no longer maps to the setting the page names, or a Settings label that was
reworded fails this test instead of going stale in a security document.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from openavc import system_config as system_config_module
from openavc.api import auth as auth_module
from openavc.core import panel_devices
from openavc.system_config import DEFAULTS, ENV_OVERRIDES, REMOVED_KEYS, SystemConfig

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "hardened-deployment.md"
SETTINGS_VIEW = REPO / "openavc" / "web" / "programmer" / "src" / "views" / "SystemSettingsView.tsx"
SIDEBAR = REPO / "openavc" / "web" / "programmer" / "src" / "components" / "layout" / "Sidebar.tsx"

TEXT = DOC.read_text(encoding="utf-8")


def _profile() -> dict:
    blocks = re.findall(r"```json\n(.*?)```", TEXT, flags=re.S)
    assert len(blocks) == 1, "the page should have exactly one system.json block"
    return json.loads(blocks[0])


def _settings() -> list[tuple[str, str, object]]:
    return [
        (section, key, value)
        for section, values in _profile().items()
        for key, value in values.items()
    ]


def _table_rows(first_header: str) -> list[list[str]]:
    """The body rows of the Markdown table whose first header cell is given."""
    lines = TEXT.splitlines()
    for i, line in enumerate(lines):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] == first_header:
            rows = []
            for body in lines[i + 2:]:
                if not body.startswith("|"):
                    break
                rows.append([c.strip() for c in body.strip().strip("|").split("|")])
            return rows
    raise AssertionError(f"no table headed {first_header!r} on the page")


def test_every_key_in_the_profile_is_a_live_setting_of_the_right_type():
    removed = set(REMOVED_KEYS)
    for section, key, value in _settings():
        assert section in DEFAULTS, f"{section} is not a system.json section"
        assert key in DEFAULTS[section], f"{section}.{key} is not a system.json setting"
        assert (section, key) not in removed, f"{section}.{key} is a retired field"
        default = DEFAULTS[section][key]
        assert type(value) is type(default), (
            f"{section}.{key} is {type(value).__name__} on the page, "
            f"{type(default).__name__} in DEFAULTS"
        )


def test_the_settings_table_matches_the_profile_block():
    profile = {f"{s}.{k}": v for s, k, v in _settings()}
    rows = _table_rows("Setting")
    named = {}
    for row in rows:
        setting = row[0].strip("`")
        named[setting] = json.loads(row[1].strip("`"))
    assert named == profile


def test_the_profile_produces_the_posture_it_describes(tmp_path, monkeypatch):
    """Loaded the way the server loads it, the block closes what it says it closes."""
    for env_var, _ in ENV_OVERRIDES.values():
        monkeypatch.delenv(env_var, raising=False)
    (tmp_path / "system.json").write_text(json.dumps(_profile()), encoding="utf-8")
    cfg = SystemConfig()
    cfg._data_dir = tmp_path
    cfg._file_path = tmp_path / "system.json"
    cfg.load()
    monkeypatch.setattr(system_config_module, "get_system_config", lambda: cfg)
    monkeypatch.setattr(auth_module, "get_system_config", lambda: cfg)

    assert auth_module.anonymous_access_allowed() is False
    assert panel_devices.access_mode() == panel_devices.ACCESS_APPROVED
    assert cfg.get("tls", "enabled") is True
    assert cfg.get("tls", "redirect_http") is True
    assert cfg.get("isc", "enabled") is False
    assert cfg.get("cloud", "enabled") is False
    assert cfg.get("discovery", "advertise") is False
    assert cfg.get("network", "trust_forwarded_for") is False
    assert cfg.get("network", "port80_redirect") is False


def test_every_environment_variable_maps_to_the_setting_the_page_names():
    by_setting = {f"{s}.{k}": var for (s, k), (var, _) in ENV_OVERRIDES.items()}
    known = set(by_setting.values())
    for name in set(re.findall(r"OPENAVC_[A-Z0-9_]+", TEXT)):
        assert name in known, f"{name} is not an environment override"

    for row in _table_rows("Variable"):
        variables = re.findall(r"OPENAVC_[A-Z0-9_]+", row[0])
        settings = re.findall(r"`([a-z_]+\.[a-z_0-9]+)`", row[1])
        if not settings:
            # "the admin password": the one row described in words.
            assert variables == [by_setting["auth.programmer_password"]]
            continue
        assert [by_setting.get(s) for s in settings] == variables


@pytest.mark.parametrize(
    "label",
    sorted(set(re.findall(r"Settings > [A-Za-z]+ > \*\*([^*]+)\*\*", TEXT))),
)
def test_settings_labels_named_on_the_page_exist(label):
    assert label in SETTINGS_VIEW.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "section",
    sorted(set(re.findall(r"Settings > ([A-Za-z]+) >", TEXT))),
)
def test_settings_sections_named_on_the_page_exist(section):
    assert f">{section}</h3>" in SETTINGS_VIEW.read_text(encoding="utf-8")


@pytest.mark.parametrize("page", ["Cloud", "Inter-System"])
def test_programmer_pages_named_on_the_page_exist(page):
    assert f'label: "{page}"' in SIDEBAR.read_text(encoding="utf-8")
