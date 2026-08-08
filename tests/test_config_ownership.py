"""Which config module owns which setting — enforced, not just documented.

``server/config.py`` and ``server/system_config.py`` both hold settings, and
the split between them is easy to get wrong when adding one: a user-editable
setting that lands only in config.py has no Settings UI and no persistence,
and a mirror that carries its own fallback default states a second answer
that silently diverges from DEFAULTS the day someone edits one of them.

That second failure was real and shipped: ``BIND_ADDRESS`` fell back to
"0.0.0.0" here while DEFAULTS said "127.0.0.1". The fallback could never fire
(``load()`` seeds from DEFAULTS, so the key always exists), so the runtime was
fine and the *file* was the thing that lied — to anyone reading it to find out
what OpenAVC listens on by default.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from openavc import config
from openavc.system_config import DEFAULTS

CONFIG_PY = pathlib.Path(config.__file__)

# Settings config.py defines outright: env vars and derived paths, with no
# system.json entry and no Settings UI. Adding one here is a deliberate act;
# adding one that belongs in DEFAULTS instead is the mistake this catches.
ENV_ONLY = {
    "PROJECT_PATH",
    "SAVED_PROJECTS_DIR",
    "RATE_LIMIT_ENABLED",
    "RATE_LIMIT_OPEN_PER_MINUTE",
    "RATE_LIMIT_STANDARD_PER_MINUTE",
    "RATE_LIMIT_CONTROL_PER_MINUTE",
    "RATE_LIMIT_STRICT_PER_MINUTE",
    "CLOUD_HEARTBEAT_INTERVAL",
    "CLOUD_STATE_BATCH_INTERVAL",
}

# Section/key spellings that would read as an env-only name but are really
# mirrors, so the "not in DEFAULTS" check below can't be fooled by a rename.
_ENV_ONLY_HINTS = {
    "PROJECT_PATH": ("paths", "project_path"),
    "SAVED_PROJECTS_DIR": ("paths", "saved_projects_dir"),
    "RATE_LIMIT_ENABLED": ("rate_limit", "enabled"),
    "RATE_LIMIT_OPEN_PER_MINUTE": ("rate_limit", "open_per_minute"),
    "RATE_LIMIT_STANDARD_PER_MINUTE": ("rate_limit", "standard_per_minute"),
    "RATE_LIMIT_CONTROL_PER_MINUTE": ("rate_limit", "control_per_minute"),
    "RATE_LIMIT_STRICT_PER_MINUTE": ("rate_limit", "strict_per_minute"),
    "CLOUD_HEARTBEAT_INTERVAL": ("cloud", "heartbeat_interval"),
    "CLOUD_STATE_BATCH_INTERVAL": ("cloud", "state_batch_interval"),
}


def _mirror_reads() -> dict[str, tuple[str, str, bool]]:
    """Every module-level ``NAME = _cfg.get(section, key[, default])``.

    Parsed rather than imported so the *source* is what's checked — a default
    argument is invisible once the module has evaluated.
    """
    tree = ast.parse(CONFIG_PY.read_text(encoding="utf-8"))
    found: dict[str, tuple[str, str, bool]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Attribute):
            continue
        if value.func.attr != "get" or not isinstance(value.func.value, ast.Name):
            continue
        if value.func.value.id != "_cfg":
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        name = targets[0].id  # type: ignore[union-attr]
        section, key = value.args[0].value, value.args[1].value
        found[name] = (section, key, len(value.args) > 2)
    return found


def test_the_module_actually_has_both_kinds():
    """Guards the guard: if the parse silently found nothing, every
    assertion below would pass vacuously."""
    mirrors = _mirror_reads()
    assert len(mirrors) >= 10, mirrors
    assert ENV_ONLY <= set(vars(config))


@pytest.mark.parametrize("name", sorted(ENV_ONLY))
def test_env_only_settings_are_not_also_in_defaults(name):
    """An env-only setting duplicated into DEFAULTS gains a second source of
    truth and a Settings UI that writes somewhere nothing reads."""
    section, key = _ENV_ONLY_HINTS[name]
    assert key not in DEFAULTS.get(section, {}), (
        f"{name} is declared env-only but {section}.{key} is in DEFAULTS. "
        f"Pick one owner."
    )


def test_every_mirror_names_a_real_defaults_entry():
    """A mirror of a key DEFAULTS doesn't have resolves to None at import,
    which surfaces far from the typo that caused it."""
    missing = {
        name: (section, key)
        for name, (section, key, _) in _mirror_reads().items()
        if key not in DEFAULTS.get(section, {})
    }
    assert not missing, f"config.py mirrors keys absent from DEFAULTS: {missing}"


def test_no_mirror_carries_its_own_fallback_default():
    """DEFAULTS owns the defaults. ``load()`` seeds from it, so a fallback
    here can never fire — it can only state a second default that drifts."""
    with_fallback = {
        name: (section, key)
        for name, (section, key, has_default) in _mirror_reads().items()
        if has_default
    }
    assert not with_fallback, (
        f"These read DEFAULTS-owned keys but pass their own fallback: "
        f"{with_fallback}. Drop the fallback; set the default in DEFAULTS."
    )


def test_mirrors_resolve_to_the_defaults_value():
    """End to end, with no system.json and no env override in the test
    environment: the constant equals what DEFAULTS says."""
    for name, (section, key, _) in _mirror_reads().items():
        if section == "cloud":
            continue  # pairing routes write these back at runtime
        assert getattr(config, name) == DEFAULTS[section][key], name


def test_the_default_bind_address_is_loopback():
    """The specific lie this file was written to prevent: OpenAVC listens on
    loopback until someone opts out, and config.py must not imply otherwise."""
    assert DEFAULTS["network"]["bind_address"] == "127.0.0.1"
    assert config.BIND_ADDRESS == "127.0.0.1"
