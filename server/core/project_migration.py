"""
OpenAVC project format migration system.

Applies versioned transforms when loading older project files.
Each migration is a pure function: dict -> dict.
"""

from server.utils.logger import get_logger

log = get_logger(__name__)

CURRENT_VERSION = "0.3.0"

# Connection-related config fields that belong in the connections table
CONNECTION_FIELDS = {
    "host", "port", "com_port", "baud_rate", "username", "password",
    "base_url", "ssl",
}


def migrate_0_1_to_0_2(data: dict) -> dict:
    """
    Migrate from 0.1.0 to 0.2.0:
    - Move connection fields from device.config to connections table
    - Add empty driver_dependencies (populated on save)
    - Bump version
    """
    connections: dict[str, dict] = {}

    for device in data.get("devices", []):
        device_id = device.get("id", "")
        config = device.get("config", {})
        conn_overrides: dict = {}

        for key in list(config.keys()):
            if key in CONNECTION_FIELDS:
                conn_overrides[key] = config.pop(key)

        if conn_overrides:
            connections[device_id] = conn_overrides

    data["connections"] = connections
    data.setdefault("driver_dependencies", [])
    data["openavc_version"] = "0.2.0"
    return data


def migrate_0_2_to_0_3(data: dict) -> dict:
    """
    Migrate from 0.2.0 to 0.3.0:
    - Add empty plugins dict
    - Add empty plugin_dependencies list
    - Bump version
    """
    data.setdefault("plugins", {})
    data.setdefault("plugin_dependencies", [])
    data["openavc_version"] = "0.3.0"
    return data


# Ordered list of migrations: (source_version, target_version, transform_fn)
MIGRATIONS = [
    ("0.1.0", "0.2.0", migrate_0_1_to_0_2),
    ("0.2.0", "0.3.0", migrate_0_2_to_0_3),
]


def migrate_project(data: dict) -> tuple[dict, bool]:
    """
    Apply all needed migrations to bring a project to the current version.

    Returns:
        (migrated_data, was_migrated) — the transformed dict and whether
        any migrations were applied.
    """
    current = data.get("openavc_version", "0.1.0")
    migrated = False

    for source_ver, target_ver, migrator in MIGRATIONS:
        if current == source_ver:
            log.info(f"Migrating project from {source_ver} to {target_ver}")
            data = migrator(data)
            current = target_ver
            migrated = True

    return data, migrated
