"""
Pydantic models for the REST API request/response bodies.
"""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.utils.paths import is_safe_script_filename


class CommandRequest(BaseModel):
    command: str
    params: dict[str, Any] = {}


class RawSendRequest(BaseModel):
    """Body for ``POST /api/devices/{id}/send-raw`` — the device-page
    "Send raw" box. ``data`` is the literal command string; the driver encodes
    escape sequences and appends the device's line terminator on send."""

    data: str


class IRImportRequest(BaseModel):
    """Body for ``POST /api/devices/{bridge_id}/ir-import`` — convert a
    bridge-native wire code (e.g. a Global Cache ``sendir`` string an integrator
    typed) to canonical Pronto hex for storage in a code-set."""

    wire: str


class IREmitRequest(BaseModel):
    """Body for ``POST /api/devices/{bridge_id}/ir-emit`` — the bridge card's
    raw IR-emit diagnostic. Fires an arbitrary Pronto code out one of the
    bridge's emitter ports, independent of any device's code-set. ``port`` is
    the bridge port id (e.g. ``ir:1``); ``repeat`` is the emit repeat count."""

    port: str
    pronto: str
    repeat: int = 1


class ActionInvokeRequest(BaseModel):
    """Body for ``POST /api/devices/{id}/actions/{action_id}``.

    ``params`` are collected from the action's input dialog (keyed by the
    action's declared param names). A no-param action sends an empty dict.
    """

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

    @field_validator("file")
    @classmethod
    def validate_file(cls, v: str) -> str:
        # A script file is a flat .py basename under scripts/ — reject path
        # separators, `..`, and non-.py extensions so a request can't write
        # into a nested subdir or drop a non-Python file. (safe_path_within at
        # the write site blocks escaping scripts/, but not these shapes.)
        if not is_safe_script_filename(v):
            raise ValueError(
                "Script file must be a plain .py filename "
                "(letters, numbers, hyphens, underscores), no path separators"
            )
        return v


class DriverDefinitionRequest(BaseModel):
    # The YAML driver schema is intentionally extensible — fields like
    # `discovery`, `device_settings`, `simulator`, `help`, `on_connect`,
    # `auth`, `protocols`, `min_platform_version`, etc. are read by the
    # ConfigurableDriver runtime and validated by
    # `validate_driver_definition()` in driver_loader.py. This API model
    # acts only as a transit container; allow extra fields so they survive
    # the round-trip to disk instead of being silently stripped.
    # The full field set lives in the contract registry
    # (server/drivers/spec.py FIELDS); a test pins every field declared
    # here to a registry entry.
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
    # Send-side packet framing — the send twin of frame_parser. Wraps every
    # byte-stream command in a computed-length binary header (e.g. eISCP).
    # Declared explicitly (like frame_parser) so the Driver Builder round-trips
    # it; the model is also extra='allow'.
    send_frame: dict[str, Any] | None = None
    # Quick Action strip declarations. Declared explicitly (the model is also
    # extra='allow') so the Driver Builder round-trips them to disk: actions is
    # the full form (kind:"command" promotes a command, kind:"setup" is a
    # provisioning wizard), quick_actions is the flat promote-these-commands sugar.
    actions: list[dict[str, Any]] = []
    quick_actions: list[str] = []
    # Param-picker option providers (§69 Phase 2) — `options_state` /
    # `options_source` / `options_from` — and the free-text validators (§69
    # Phase 3) — `min` / `max` / `pattern` — on a command or action param are
    # nested inside the untyped `commands` / `actions` dicts above, so they ride
    # through to disk and out to the IDE verbatim with no per-field model. (The
    # execute path, CommandRequest / ActionInvokeRequest, carries only param
    # *values*, so those models need nothing here.)


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
    # Bounded: each test holds a real transport (and often a device's single
    # TCP control session) open for the full wait.
    timeout: float = Field(default=5.0, gt=0, le=60)

    # Definition mode
    definition: dict[str, Any] | None = None
    command_name: str | None = None
    params: dict[str, Any] = {}
    config_overrides: dict[str, Any] = {}

    # Raw mode (legacy) — also used as the substituted display string in
    # responses so the UI can show what was actually sent.
    command_string: str = ""
    delimiter: str = "\\r"
    # Raw HTTP probes only. Definition-mode tests go through the real HTTP
    # transport, which reads verify_ssl from the driver config instead.
    verify_ssl: bool = True


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


class NetworkIPv4Request(BaseModel):
    """Body for ``POST /api/system/network/ipv4``.

    ``confirmed: false`` is a dry run: the server validates and returns
    warnings (e.g. gateway outside the subnet) without changing anything.
    ``confirmed: true`` applies the change, with automatic rollback if the
    connection fails to activate.
    """

    connection: str
    method: str  # "auto" (DHCP) or "manual" (static)
    address: str | None = None  # CIDR, required when method == "manual"
    gateway: str | None = None
    dns: list[str] = []
    confirmed: bool = False


class WifiConnectRequest(BaseModel):
    ssid: str
    psk: str | None = None


class WifiRadioRequest(BaseModel):
    enabled: bool


class HostnameRequest(BaseModel):
    hostname: str
