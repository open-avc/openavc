"""A name rule takes only the characters it names, and not one more.

Python's ``re.match`` with a pattern ending in ``$`` also matches before a
final newline, so ``^[a-z0-9_]+$`` read that way takes ``"room\\n"``. Each rule
below says what a name (a file, a folder, an id, a state key segment, a
hostname label) may hold; each is asked here, through the function that reads
it, with a trailing newline, and refuses it. The ordinary name beside it shows
the door still opens.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from openavc.api.assets import _sanitize_filename
from openavc.api.models import ScriptCreateRequest
from openavc.audit.report import ReportStore
from openavc.cloud.cert_manager import _valid_dns_name
from openavc.core.client_origin import parse_client_id
from openavc.core.custom_ui import CustomUIPathError, normalize_relpath
from openavc.core.project_loader import ScriptConfig
from openavc.drivers.child_entities import _string_child_id
from openavc.support.problem_report import NAME_RE as PROBLEM_REPORT_NAME
from openavc.system.network import validate_hostname
from openavc.updater.panel_software import INSTALL_AT_PANEL, _Record
from openavc.utils.paths import is_safe_script_filename


def test_an_asset_file_name():
    assert _sanitize_filename("logo.png") == "logo.png"
    with pytest.raises(HTTPException):
        _sanitize_filename("logo.png\n")


def test_a_script_id_sent_to_the_api():
    assert ScriptCreateRequest(id="room_logic", file="room_logic.py").id == "room_logic"
    with pytest.raises(ValidationError):
        ScriptCreateRequest(id="room_logic\n", file="room_logic.py")


def test_a_script_id_in_a_project_file():
    assert ScriptConfig(id="room_logic", file="room_logic.py").id == "room_logic"
    with pytest.raises(ValidationError):
        ScriptConfig(id="room_logic\n", file="room_logic.py")


def test_a_script_file_name():
    assert is_safe_script_filename("room_logic.py")
    assert not is_safe_script_filename("room_logic.py\n")


def test_a_remote_access_hostname():
    assert _valid_dns_name("room-1.example.com")
    assert not _valid_dns_name("room-1\n.example.com")
    assert not _valid_dns_name("room-1.example.com\n")


def test_a_custom_ui_folder_name():
    assert normalize_relpath("room_map/index.html") == "room_map/index.html"
    with pytest.raises(CustomUIPathError):
        normalize_relpath("room_map\n/index.html")


def test_a_client_id_header_is_trimmed_not_taken_with_its_newline():
    assert parse_client_id("tab_1") == "tab_1"
    assert parse_client_id("tab_1\n") == "tab_1"


def test_a_child_string_id():
    assert _string_child_id("zone", "lobby_1", {}) == "lobby_1"
    with pytest.raises(ValueError):
        _string_child_id("zone", "lobby_1\n", {})


def test_a_hostname_label():
    assert validate_hostname("room-1.local") == "room-1.local"
    with pytest.raises(ValueError):
        validate_hostname("room-1\n.local")


@pytest.mark.parametrize("pattern, name", [
    (None, "openavc-device-audit-abc123.zip"),
    (PROBLEM_REPORT_NAME, "openavc-problem-report-abc123.zip"),
])
def test_a_report_file_name(tmp_path, pattern, name):
    store = ReportStore(tmp_path) if pattern is None else ReportStore(tmp_path, name_pattern=pattern)
    assert store.save(name, b"zip") == name
    assert store.path(name) is not None
    assert store.path(name + "\n") is None
    with pytest.raises(ValueError):
        store.save(name + "\n", b"zip")


def test_a_staged_panel_update_record():
    record = {
        "sha256": "a" * 64, "version_code": 7, "install": INSTALL_AT_PANEL,
        "size": 1, "mtime_ns": 1, "staged_at": "",
    }
    assert _Record.from_dict(record) is not None
    assert _Record.from_dict({**record, "sha256": "a" * 64 + "\n"}) is None
