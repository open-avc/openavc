"""Mixin for AI tool handlers that manage system-level operations."""

import asyncio
from pathlib import Path
from typing import Any

from openavc.cloud.tools import apply_tool_edit
from openavc.core import file_news
from openavc.core.asset_references import asset_users
from openavc.core.device_logins import SAVED_LOGIN
from openavc.core.themes import ThemeRefused, all_themes, find_theme, project_themes_dir, theme_mode
from openavc.drivers.registry import get_driver_class
from openavc.utils.log_redaction import (
    MIN_SECRET_LEN,
    get_secret_registry,
    is_secret_key,
    project_secret_values,
    redact_text,
    secret_config_keys,
)
from openavc.utils.secret_audience import Exit
from openavc.utils.logger import get_logger

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


async def _await_scan(discovery_engine: Any, timeout: Any) -> str | None:
    """Poll until the running scan stops, for at most ``timeout`` seconds
    (5 to 180, 120 when not sent); the refusal when it is not a number."""
    wait_timeout = _coerce_number(timeout, default=120, lo=5, hi=180)
    if wait_timeout is None:
        return "timeout must be a number of seconds"
    elapsed, poll_interval = 0.0, 3.0
    while elapsed < wait_timeout:
        if discovery_engine.get_status()["status"] != "running":
            break
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return None


def _delete_unless_shown(engine: Any, safe_name: str) -> dict[str, Any]:
    """The asset ``safe_name`` deleted, refused while something in the
    running project still shows it. The caller holds the project files
    (``Engine.holding_project_files``)."""
    from openavc.api.assets import _assets_dir, _installed_themes

    try:
        assets_dir = _assets_dir()
    except Exception:
        log.debug("Assets directory not available", exc_info=True)
        return {"error": "Assets directory not available"}

    path = assets_dir / safe_name
    if not path.exists():
        return {"error": f"Asset '{safe_name}' not found"}

    # Asked after the file is known to exist, so an element pointing at a
    # name that is already gone answers "not found" here and at the REST
    # door alike.
    project = getattr(engine, "project", None)
    if project is not None:
        still_shown = asset_users(project, safe_name, themes=_installed_themes())
        if still_shown:
            return {
                "error": (
                    f"'{safe_name}' is still shown by "
                    f"{', '.join(still_shown)}. Point them at another file, "
                    f"or delete them first, then this asset can go."
                ),
                "still_shown_by": still_shown,
            }

    path.unlink()
    return {"status": "deleted", "name": safe_name}


# Which config keys hold credentials, and how a credential VALUE is masked out
# of log text, both live in openavc/utils/log_redaction.py — the same rule the
# transport TX/RX formatter and the log filter use. This module was where that
# rule was first written; it is now one of its callers, so the cloud AI's view
# of a log line and the log itself can never disagree about what a secret is.
_looks_secret = is_secret_key
_redact_log_message = redact_text


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

        def harvest(mapping: Any, schema: Any = None) -> None:
            if not isinstance(mapping, dict):
                return
            # Named like a credential, or a secret by the driver's own schema.
            keys = secret_config_keys(mapping, schema if isinstance(schema, dict) else None)
            for key, value in mapping.items():
                if (
                    isinstance(value, str)
                    and len(value) >= MIN_SECRET_LEN
                    and (key in keys or _looks_secret(str(key)))
                ):
                    secrets.add(value)

        engine = self._get_engine()
        if engine and engine.project:
            for device in engine.project.devices:
                cls = get_driver_class(getattr(device, "driver", "") or "")
                schema = cls.DRIVER_INFO.get("config_schema") if cls is not None else None
                harvest(getattr(device, "config", None), schema)
            for conn in (engine.project.connections or {}).values():
                harvest(conn)
            plugins = getattr(engine.project, "plugins", None) or {}
            for plugin_cfg in plugins.values():
                # Project plugin entries are PluginConfig models whose
                # secrets live in .config; raw dicts appear in tests and
                # forward-compat paths.
                if isinstance(plugin_cfg, dict):
                    harvest(plugin_cfg)
                    harvest(plugin_cfg.get("config"))
                else:
                    harvest(getattr(plugin_cfg, "config", None))
            # The project's own (the mesh key).
            secrets |= {
                value for value in project_secret_values(engine.project)
                if len(value) >= MIN_SECRET_LEN
            }
        # What the project holds as the marker, saved on this system instead:
        # the device logins, the plugin logins and the mesh key the engine
        # registered from the login file (Engine._register_project_secrets).
        # The marker itself is no secret, and masked it would read as one.
        secrets |= get_secret_registry().to_mask(Exit.CLOUD)
        secrets.discard(SAVED_LOGIN)

        try:
            from openavc.system_config import get_system_config

            data = get_system_config().to_dict()
            for section in ("auth", "cloud", "isc"):
                harvest(data.get(section))
        except Exception:
            log.debug("Could not read system config for log redaction", exc_info=True)

        return secrets

    async def _get_logs(self, input: dict) -> Any:
        import time
        from openavc.utils.log_buffer import get_log_buffer

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
        from openavc.api.discovery import _engine as discovery_engine
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

        from openavc.discovery.engine import ScanBlocked

        try:
            scan_id = await discovery_engine.start_scan(
                subnets=subnets,
                timeout=timeout,
            )
        except ScanBlocked as e:
            return {"error": str(e)}
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
        from openavc.api.discovery import _engine as discovery_engine
        if discovery_engine is None:
            return {"error": "Discovery engine not available"}

        wait = input.get("wait", False)
        category = input.get("category")

        # If wait=True, poll until scan completes (or timeout)
        refused = await _await_scan(discovery_engine, input.get("timeout")) if wait else None
        if refused:
            return {"error": refused}

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

        # How the scan ended and its warnings: a scan cut short ("partial")
        # or stopped ("cancelled") says why, so the answer can say so too.
        status = discovery_engine.get_status()
        return {
            "devices": devices,
            "total_devices": len(devices),
            "identification_summary": identification_summary,
            "scan_status": status["status"],
            "scan_duration_seconds": status["duration"],
            "warnings": status["warnings"],
        }

    async def _list_themes(self, input: dict) -> Any:
        engine = self._get_engine()
        active_theme = None
        if engine and engine.project:
            settings = getattr(engine.project.ui, "settings", None)
            if settings is not None:
                # The theme the panel draws, a project with no theme id included.
                active_theme = settings.drawn_theme_id()

        return [
            {
                "id": t["id"],
                "name": t["name"],
                "description": t.get("description", ""),
                "source": t.get("_source", "custom"),
                "active": t["id"] == active_theme if active_theme else False,
            }
            for t in all_themes(self._custom_themes_dir())
        ]

    def _custom_themes_dir(self) -> Path | None:
        """The custom themes folder of the running project, or None without one."""
        engine = self._get_engine()
        project_path = getattr(engine, "project_path", None) if engine else None
        if not project_path:
            return None
        try:
            return project_themes_dir(project_path)
        except OSError:
            log.debug("No custom themes folder for %s", project_path, exc_info=True)
            return None

    def _find_theme(self, input: dict) -> dict:
        """The theme ``theme_id`` names, looked up where the panel looks, or an error."""
        theme_id = input.get("theme_id", "")
        if not theme_id:
            return {"error": "theme_id is required"}
        try:
            theme = find_theme(theme_id, self._custom_themes_dir())
        except ThemeRefused as refused:
            return {"error": str(refused)}
        if theme is None:
            return {"error": f"Theme '{theme_id}' not found"}
        return theme

    async def _get_theme(self, input: dict) -> Any:
        return self._find_theme(input)

    async def _apply_theme(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        theme = self._find_theme(input)
        if "error" in theme:
            return theme
        theme_id = input["theme_id"]
        mode = theme_mode(theme)

        # UI-section change: the apply pushes the new ui.definition to
        # connected panels — no other subsystem is touched.
        def mutate(project):
            project.ui.settings.theme_id = theme_id
            project.ui.settings.theme = mode

        await apply_tool_edit(engine, mutate)

        return {"status": "applied", "theme_id": theme_id}

    async def _list_assets(self, input: dict) -> Any:
        """The uploaded files, and who still shows each one.

        ``used_by`` is the same walk the delete refuses on, carried here for
        the same reason ``list_ui_files`` carries it: the assistant is the one
        surface that cannot see the panel, so the only way it knows a file is
        load-bearing is being told.
        """
        from openavc.api.assets import (
            _assets_dir, _installed_themes, ALLOWED_EXTENSIONS,
        )

        try:
            assets_dir = _assets_dir()
        except Exception:
            log.debug("Assets directory not available", exc_info=True)
            return {"assets": [], "total_size": 0}

        engine = self._get_engine()
        project = getattr(engine, "project", None) if engine else None
        themes = _installed_themes() if project is not None else None

        assets = []
        for f in sorted(assets_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in ALLOWED_EXTENSIONS:
                entry = {
                    "name": f.name,
                    "size": f.stat().st_size,
                    "type": f.suffix.lower().lstrip("."),
                }
                if project is not None:
                    entry["used_by"] = asset_users(project, f.name, themes=themes)
                assets.append(entry)
        total_size = sum(a["size"] for a in assets)
        return {"assets": assets, "total_size": total_size}

    async def _delete_asset(self, input: dict) -> Any:
        """Delete an uploaded asset, unless something still shows it.

        Refuses rather than reports, which is the opposite of the device and
        script deletes beside it and the same call ``delete_ui_file`` makes: a
        stranded device reference can be pointed somewhere else afterwards, and
        a deleted asset leaves nothing to point -- there is no tool here that
        uploads one back, so the bytes are gone until somebody finds the
        original. The panel does not report it either; a background that has
        gone draws the colour underneath and an icon that has gone draws
        nothing at all.
        """
        filename = input.get("filename", "")
        if not filename:
            return {"error": "filename is required"}
        engine = self._get_engine()
        if engine is None:
            return {"error": "Assets directory not available"}
        # The check and the delete under the project files' hold, so the
        # project asked is the one the file on disk belongs to.
        async with engine.holding_project_files():
            reply = _delete_unless_shown(engine, Path(filename).name)
        if "error" not in reply:
            await file_news.assets_changed(engine)
        return reply

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
