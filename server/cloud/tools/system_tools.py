"""Mixin for AI tool handlers that manage system-level operations."""

import asyncio
from pathlib import Path
from typing import Any

from server.utils.logger import get_logger
from server.utils.paths import safe_path_within

log = get_logger(__name__)


def _coerce_number(
    value: Any, *, default: float, lo: float, hi: float, integer: bool = False
) -> float | int | None:
    """Clamp a tool-supplied number to [lo, hi]; None when it isn't numeric.

    AI callers plausibly send numbers as strings ("60") or floats where an
    int is meant; coerce those instead of surfacing an opaque TypeError
    from a comparison or slice.
    """
    if value is None:
        value = default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN never clamps meaningfully
        return None
    number = max(lo, min(number, hi))
    return int(number) if integer else number


# Config keys whose string values are treated as secrets and scrubbed from
# log text before it leaves for the cloud (see _collect_secret_values).
_SECRET_KEY_HINTS = (
    "password",
    "passphrase",
    "secret",
    "token",
    "api_key",
    "community",
    "auth_key",
    "system_key",
    "lock_code",
)


def _looks_secret(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_KEY_HINTS)


def _redact_log_message(message: str, secrets: set[str]) -> str:
    for secret in secrets:
        if secret in message:
            message = message.replace(secret, "***")
    return message


class SystemToolsMixin:
    """Logs, discovery, themes, assets, ISC, and async waiting tools."""

    def _collect_secret_values(self) -> set[str]:
        """Every secret string the runtime knows about.

        Device/connection/plugin configs and the system config carry the
        credentials that transports put on the wire; at DEBUG those wire
        payloads land in the log buffer verbatim. Scrubbing the known
        VALUES (rather than pattern-guessing over free text) removes them
        from anything shipped to the cloud without mangling the rest of
        the line. Values shorter than 4 chars are skipped so a trivial
        substring can't blank unrelated text.
        """
        secrets: set[str] = set()

        def harvest(mapping: Any) -> None:
            if not isinstance(mapping, dict):
                return
            for key, value in mapping.items():
                if isinstance(value, str) and len(value) >= 4 and _looks_secret(str(key)):
                    secrets.add(value)

        engine = self._get_engine()
        if engine and engine.project:
            for device in engine.project.devices:
                harvest(getattr(device, "config", None))
            for conn in (engine.project.connections or {}).values():
                harvest(conn)
            plugins = getattr(engine.project, "plugins", None) or {}
            for plugin_cfg in plugins.values():
                harvest(plugin_cfg)
                if isinstance(plugin_cfg, dict):
                    harvest(plugin_cfg.get("config"))

        try:
            from server.system_config import get_system_config

            data = get_system_config().to_dict()
            for section in ("auth", "cloud", "isc"):
                harvest(data.get(section))
        except Exception:
            log.debug("Could not read system config for log redaction", exc_info=True)

        return secrets

    async def _get_logs(self, input: dict) -> Any:
        import time
        from server.utils.log_buffer import get_log_buffer

        count = _coerce_number(
            input.get("count", 100), default=100, lo=1, hi=500, integer=True
        )
        if count is None:
            return {"error": "count must be a number of entries (1-500)"}
        category = input.get("category", "")
        level = input.get("level", "")
        search = input.get("search", "")
        since_seconds = input.get("since_seconds")
        if since_seconds is not None:
            since_seconds = _coerce_number(
                since_seconds, default=0, lo=0, hi=10**9
            )
            if since_seconds is None:
                return {"error": "since_seconds must be a number of seconds"}

        # Filter the WHOLE buffer first, then return the newest `count`
        # matches — slicing first made a category/level/search/since query
        # see only the newest `count` entries and miss older matches.
        entries = get_log_buffer().get_recent(1_000_000)
        if category:
            entries = [e for e in entries if e.get("category") == category]
        if level:
            level_upper = level.upper()
            entries = [e for e in entries if e.get("level") == level_upper]
        if search:
            search_lower = search.lower()
            entries = [e for e in entries if search_lower in (e.get("message", "")).lower()]
        if since_seconds is not None:
            cutoff = time.time() - since_seconds
            entries = [e for e in entries if e.get("timestamp", 0) >= cutoff]
        entries = entries[-count:]

        # This result ships to the cloud AI (and its stored conversation
        # history): scrub known credential values from the message text —
        # transport TX/RX lines at DEBUG carry them verbatim.
        secrets = self._collect_secret_values()
        if secrets:
            for entry in entries:
                message = entry.get("message", "")
                if isinstance(message, str):
                    entry["message"] = _redact_log_message(message, secrets)
        return entries

    async def _wait(self, input: dict) -> Any:
        """Wait for a specified number of seconds before returning.

        Use this when an async operation (discovery scan, device connection test,
        etc.) needs time to complete. Avoids burning tool rounds on rapid polling.
        """
        seconds = _coerce_number(input.get("seconds", 30), default=30, lo=1, hi=120)
        if seconds is None:
            return {"error": "seconds must be a number between 1 and 120"}
        reason = input.get("reason", "")

        log.info(f"AI wait: {seconds}s (reason: {reason or 'none'})")
        await asyncio.sleep(seconds)

        return {
            "waited_seconds": seconds,
            "message": f"Waited {seconds} seconds. You can now check the results of your operation.",
        }

    async def _start_discovery_scan(self, input: dict) -> Any:
        from server.api.discovery import _engine as discovery_engine
        if discovery_engine is None:
            return {"error": "Discovery engine not available"}

        # Validate before start_scan: a bad value would otherwise kill the
        # scan AFTER this tool reported it running (the engine's runner
        # catches the error and still marks the scan complete), and a bare
        # string subnets value would be iterated character by character.
        subnets = input.get("subnets")
        if subnets is not None and (
            not isinstance(subnets, list)
            or not all(isinstance(s, str) for s in subnets)
        ):
            return {"error": 'subnets must be a list of CIDR strings (e.g. ["192.168.1.0/24"])'}
        snmp_enabled = input.get("snmp_enabled", True)
        timeout = _coerce_number(input.get("timeout", 120.0), default=120.0, lo=10, hi=600)
        if timeout is None:
            return {"error": "timeout must be a number of seconds"}

        discovery_engine.config["snmp_enabled"] = snmp_enabled

        try:
            scan_id = await discovery_engine.start_scan(
                subnets=subnets,
                timeout=timeout,
            )
        except RuntimeError:
            return {"error": "A scan is already in progress"}
        except ValueError as e:
            return {"error": str(e)}

        status = discovery_engine.get_status()
        return {
            "scan_id": scan_id,
            "status": status["status"],
            "subnets": status["subnets"],
        }

    async def _get_discovery_results(self, input: dict) -> Any:
        from server.api.discovery import _engine as discovery_engine
        if discovery_engine is None:
            return {"error": "Discovery engine not available"}

        wait = input.get("wait", False)
        wait_timeout = input.get("timeout", 120)
        category = input.get("category")

        # If wait=True, poll until scan completes (or timeout)
        if wait:
            wait_timeout = max(5, min(wait_timeout, 180))
            elapsed = 0.0
            poll_interval = 3.0
            while elapsed < wait_timeout:
                status = discovery_engine.get_status()
                if status["status"] not in ("running", "scanning"):
                    break
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

        devices = discovery_engine.get_results()
        if category:
            devices = [d for d in devices if d.get("category") == category]

        # Tally identification.state across the returned set so Claude
        # gets a state-bucketed count without scanning every device's
        # nested identification block. Devices without an identification
        # record (older results, in-flight scans) fall through to
        # ``unknown`` — the same bucket the UI uses for "no signal
        # matched."
        identification_summary = {"identified": 0, "possible": 0, "unknown": 0}
        for d in devices:
            ident = d.get("identification") or {}
            state = ident.get("state")
            if state in identification_summary:
                identification_summary[state] += 1
            else:
                identification_summary["unknown"] += 1

        status = discovery_engine.get_status()
        return {
            "devices": devices,
            "total_devices": len(devices),
            "identification_summary": identification_summary,
            "scan_status": status["status"],
            "scan_duration_seconds": status["duration"],
        }

    async def _list_themes(self, input: dict) -> Any:
        from server.api.themes import _list_all_themes

        engine = self._get_engine()
        active_theme = None
        if engine and engine.project:
            settings = getattr(engine.project.ui, "settings", None)
            if settings is not None:
                active_theme = getattr(settings, "theme_id", None) or None

        themes = _list_all_themes()
        return [
            {
                "id": t["id"],
                "name": t["name"],
                "description": t.get("description", ""),
                "source": t.get("_source", "custom"),
                "active": t["id"] == active_theme if active_theme else False,
            }
            for t in themes
        ]

    async def _get_theme(self, input: dict) -> Any:
        from server.api.themes import BUILTIN_THEMES_DIR, _custom_themes_dir, _load_theme

        theme_id = input.get("theme_id", "")
        if not theme_id:
            return {"error": "theme_id is required"}

        # Reject a theme_id that escapes the themes dir (../, absolute, or a
        # Windows backslash jump) — otherwise it reads arbitrary .json files
        # (e.g. cloud.json, the system key) and returns them to the cloud AI.
        builtin_path = safe_path_within(BUILTIN_THEMES_DIR, f"{theme_id}.json")
        if builtin_path is None:
            return {"error": "Invalid theme id"}
        if builtin_path.exists():
            theme = _load_theme(builtin_path)
            theme["_source"] = "builtin"
            return theme

        try:
            custom_path = safe_path_within(_custom_themes_dir(), f"{theme_id}.json")
            if custom_path and custom_path.exists():
                theme = _load_theme(custom_path)
                theme["_source"] = "custom"
                return theme
        except Exception:
            log.debug("Failed to load custom theme '%s'", theme_id, exc_info=True)

        return {"error": f"Theme '{theme_id}' not found"}

    async def _apply_theme(self, input: dict) -> Any:
        from server.api.themes import BUILTIN_THEMES_DIR, _custom_themes_dir
        from server.core.project_loader import save_project_async

        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        theme_id = input.get("theme_id", "")
        if not theme_id:
            return {"error": "theme_id is required"}

        # Reject a traversal theme_id before probing the filesystem or
        # persisting it as the active theme.
        builtin_path = safe_path_within(BUILTIN_THEMES_DIR, f"{theme_id}.json")
        if builtin_path is None:
            return {"error": "Invalid theme id"}

        # Verify theme exists
        builtin = builtin_path.exists()
        try:
            custom_path = safe_path_within(_custom_themes_dir(), f"{theme_id}.json")
            custom = bool(custom_path) and custom_path.exists()
        except Exception:
            log.debug("Failed to check custom themes directory", exc_info=True)
            custom = False

        if not builtin and not custom:
            return {"error": f"Theme '{theme_id}' not found"}

        engine.project.ui.settings.theme_id = theme_id
        engine.project.ui.settings.theme = (
            "light" if ("light" in theme_id or theme_id == "minimal") else "dark"
        )
        await save_project_async(engine.project_path, engine.project)

        if self._reload_fn:
            await self._reload_fn()

        return {"status": "applied", "theme_id": theme_id}

    async def _list_assets(self, input: dict) -> Any:
        from server.api.assets import _assets_dir, ALLOWED_EXTENSIONS

        try:
            assets_dir = _assets_dir()
        except Exception:
            log.debug("Assets directory not available", exc_info=True)
            return {"assets": [], "total_size": 0}

        assets = []
        for f in sorted(assets_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in ALLOWED_EXTENSIONS:
                assets.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "type": f.suffix.lower().lstrip("."),
                })
        total_size = sum(a["size"] for a in assets)
        return {"assets": assets, "total_size": total_size}

    async def _delete_asset(self, input: dict) -> Any:
        from server.api.assets import _assets_dir

        filename = input.get("filename", "")
        if not filename:
            return {"error": "filename is required"}

        try:
            assets_dir = _assets_dir()
        except Exception:
            log.debug("Assets directory not available", exc_info=True)
            return {"error": "Assets directory not available"}

        # Sanitize: use only the filename part
        safe_name = Path(filename).name
        path = assets_dir / safe_name
        if not path.exists():
            return {"error": f"Asset '{safe_name}' not found"}

        path.unlink()
        return {"status": "deleted", "name": safe_name}

    async def _get_isc_status(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}
        if engine.isc is None:
            return {"enabled": False}
        return engine.isc.get_status()

    async def _list_isc_peers(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}
        if engine.isc is None:
            return []
        return engine.isc.get_instances()

    async def _send_isc_command(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}
        if engine.isc is None:
            return {"error": "ISC not enabled"}

        instance_id = input.get("instance_id", "")
        device_id = input.get("device_id", "")
        command = input.get("command", "")
        params = input.get("params", {})

        if not instance_id or not device_id or not command:
            return {"error": "instance_id, device_id, and command are required"}

        try:
            result = await engine.isc.send_command(instance_id, device_id, command, params)
            return {"success": True, "result": result}
        except ConnectionError:
            return {"error": f"ISC peer '{instance_id}' is not connected"}
        except TimeoutError:
            return {"error": f"Command timed out on ISC peer '{instance_id}'"}
