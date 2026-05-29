"""
Pydantic models for the REST API request/response bodies.
"""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class CommandRequest(BaseModel):
    command: str
    params: dict[str, Any] = {}


class StateSetRequest(BaseModel):
    value: Any


class MacroExecuteRequest(BaseModel):
    macro_id: str | None = None


class DeviceResponse(BaseModel):
    id: str
    name: str
    driver: str
    connected: bool
    state: dict[str, Any] = {}
    commands: dict[str, Any] = {}


class StatusResponse(BaseModel):
    status: str
    uptime_seconds: float
    project_name: str
    device_count: int
    macro_count: int
    ws_clients: int


class ErrorResponse(BaseModel):
    error: str
    detail: str = ""


class DeviceUpdateRequest(BaseModel):
    name: str | None = None
    driver: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    # Child-entity metadata (user labels / per-child config) keyed by
    # child_type -> padded local_id -> {label, config}. Declared so an
    # explicit edit round-trips instead of being dropped by extra='ignore'.
    # Omitted/None means "leave the existing map untouched" — the common
    # name/driver/config edit must not wipe it.
    child_entities: dict[str, Any] | None = None


class ScriptSourceRequest(BaseModel):
    source: str


_SCRIPT_ID_RE = re.compile(r"^[a-z0-9_]+$")


class ScriptCreateRequest(BaseModel):
    id: str
    file: str
    description: str = ""
    source: str = ""
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v or not _SCRIPT_ID_RE.match(v):
            raise ValueError("Script ID must be lowercase alphanumeric with underscores")
        return v


class DriverDefinitionRequest(BaseModel):
    # The YAML driver schema is intentionally extensible — fields like
    # `discovery`, `device_settings`, `simulator`, `help`, `on_connect`,
    # `auth`, `protocols`, `min_platform_version`, etc. are read by the
    # ConfigurableDriver runtime and validated by
    # `validate_driver_definition()` in driver_loader.py. This API model
    # acts only as a transit container; allow extra fields so they survive
    # the round-trip to disk instead of being silently stripped.
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    manufacturer: str = "Generic"
    category: str = "utility"
    version: str = "1.0.0"
    author: str = "Community"
    description: str = ""
    transport: str = "tcp"
    delimiter: str = "\\r"
    default_config: dict[str, Any] = {}
    config_schema: dict[str, Any] = {}
    state_variables: dict[str, Any] = {}
    commands: dict[str, Any] = {}
    responses: list[dict[str, Any]] = []
    polling: dict[str, Any] = {}
    frame_parser: dict[str, Any] | None = None


class TestCommandRequest(BaseModel):
    """Driver Builder live-test request.

    Two modes:

    1. Definition mode (preferred): caller provides the full driver
       `definition` (the live, possibly-unsaved YAML dict) along with
       `command_name` + `params` and any `config_overrides` (host, port,
       credentials). The endpoint instantiates a real ConfigurableDriver,
       runs auth + on_connect, then sends the named command — exactly
       matching the production code path.

    2. Raw mode (legacy fallback): caller provides `command_string` and
       a transport. The endpoint opens a transport, sends the bytes, and
       returns whatever comes back. No auth or on_connect.
    """

    # Common — host is unused for serial; port carries the serial-port path
    # (e.g. "COM3", "/dev/ttyUSB0") for that transport.
    host: str = ""
    port: int | str = 23
    transport: str = "tcp"
    timeout: float = 5.0

    # Definition mode
    definition: dict[str, Any] | None = None
    command_name: str | None = None
    params: dict[str, Any] = {}
    config_overrides: dict[str, Any] = {}

    # Raw mode (legacy) — also used as the substituted display string in
    # responses so the UI can show what was actually sent.
    command_string: str = ""
    delimiter: str = "\\r"


class PythonDriverCreateRequest(BaseModel):
    id: str
    source: str


class CommunityDriverInstallRequest(BaseModel):
    driver_id: str
    file_url: str
    min_platform_version: str | None = None


class InstallMissingDriversRequest(BaseModel):
    driver_ids: list[str]


# --- Project Library ---


class LibrarySaveRequest(BaseModel):
    id: str
    name: str
    description: str = ""


class LibraryOpenRequest(BaseModel):
    library_id: str
    project_name: str
    project_id: str | None = None


class BlankProjectRequest(BaseModel):
    project_name: str
    project_id: str | None = None


class LibraryDuplicateRequest(BaseModel):
    new_id: str
    new_name: str


class LibraryUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


# --- ISC ---


class ISCSendRequest(BaseModel):
    instance_id: str
    event: str
    payload: dict[str, Any] = {}


class ISCCommandRequest(BaseModel):
    instance_id: str
    device_id: str
    command: str
    params: dict[str, Any] = {}


class DeviceSettingRequest(BaseModel):
    value: Any


class PendingSettingsRequest(BaseModel):
    settings: dict[str, Any]


class CloudPairRequest(BaseModel):
    token: str
    cloud_api_url: str = "https://cloud.openavc.com"


class ChildEntityPatchRequest(BaseModel):
    """Patch body for ``PATCH /api/devices/{id}/children/{type}/{local_id}``.

    Both fields are optional. ``label`` updates the user-set friendly name
    (persisted to the project file and, when the child is registered,
    mirrored into the live state key ``device.<id>.<type>.<padded>.label``).
    ``config`` replaces the freeform per-child config dict.
    """

    label: str | None = None
    config: dict[str, Any] | None = None
