"""
Pydantic models for the REST API request/response bodies.
"""

from typing import Any

from pydantic import BaseModel


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


class ScriptSourceRequest(BaseModel):
    source: str


class ScriptCreateRequest(BaseModel):
    id: str
    file: str
    description: str = ""
    source: str = ""
    enabled: bool = True


class DriverDefinitionRequest(BaseModel):
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
    host: str
    port: int = 23
    transport: str = "tcp"
    command_string: str
    delimiter: str = "\\r"
    timeout: float = 5.0


class PythonDriverCreateRequest(BaseModel):
    id: str
    source: str


class CommunityDriverInstallRequest(BaseModel):
    driver_id: str
    file_url: str


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
