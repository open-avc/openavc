"""A65 — install_community_driver enforces min_platform_version parsed from
the YAML body itself, so /api/discovery/install-and-match and other callers
that don't carry the field on the request still get the check.
"""

import pytest

from fastapi import HTTPException

from openavc.api.routes.drivers import (
    _enforce_min_platform_version,
    _parse_semver,
    _peek_min_platform_version,
)


def test_peek_yaml_min_platform_version():
    yaml_text = """
id: foo
name: Foo
transport: tcp
min_platform_version: "0.6.0"
"""
    assert _peek_min_platform_version(yaml_text) == "0.6.0"


def test_peek_returns_none_when_absent():
    yaml_text = """
id: foo
name: Foo
transport: tcp
"""
    assert _peek_min_platform_version(yaml_text) is None


def test_peek_handles_malformed_yaml():
    assert _peek_min_platform_version("::: not yaml :::") is None


def test_peek_handles_non_string():
    yaml_text = """
id: foo
min_platform_version: 5
"""
    assert _peek_min_platform_version(yaml_text) is None


def test_enforce_blocks_when_running_is_older(monkeypatch):
    # Pretend we're running 0.5.0 and the driver demands 0.6.0.
    import openavc.version
    monkeypatch.setattr(openavc.version, "__version__", "0.5.0")
    with pytest.raises(HTTPException) as excinfo:
        _enforce_min_platform_version("0.6.0")
    assert excinfo.value.status_code == 422
    assert "0.6.0" in str(excinfo.value.detail)


def test_enforce_passes_when_running_is_equal(monkeypatch):
    import openavc.version
    monkeypatch.setattr(openavc.version, "__version__", "0.6.0")
    # Should not raise.
    _enforce_min_platform_version("0.6.0")


def test_enforce_passes_when_running_is_newer(monkeypatch):
    import openavc.version
    monkeypatch.setattr(openavc.version, "__version__", "0.7.1")
    _enforce_min_platform_version("0.6.0")


def test_enforce_swallows_unparseable(monkeypatch):
    import openavc.version
    monkeypatch.setattr(openavc.version, "__version__", "0.7.1")
    # An unparseable required version logs and allows.
    _enforce_min_platform_version("not-a-version")


def test_parse_semver_pads_short_versions():
    # "0.22" must equal "0.22.0" — a short tuple compares less-than and
    # used to falsely block installs at the gate.
    assert _parse_semver("0.22") == (0, 22, 0)
    assert _parse_semver("0.22") == _parse_semver("0.22.0")
    assert _parse_semver("1") == (1, 0, 0)


def test_parse_semver_keeps_numeric_prefix_of_suffixed_parts():
    # A pre-release/build suffix used to make the whole part vanish from
    # the tuple ("0.22.0-rc1" -> (0, 22)), shortening the comparison.
    assert _parse_semver("0.22.0-rc1") == (0, 22, 0)
    assert _parse_semver("1.2.3+build7") == (1, 2, 3)


def test_parse_semver_plain_three_part():
    assert _parse_semver("1.2.3") == (1, 2, 3)
    assert _parse_semver("1.2.3") < _parse_semver("1.2.10")


def test_enforce_two_part_running_version_not_blocked(monkeypatch):
    import openavc.version
    monkeypatch.setattr(openavc.version, "__version__", "0.22")
    # Running "0.22" satisfies a "0.22.0" requirement.
    _enforce_min_platform_version("0.22.0")
