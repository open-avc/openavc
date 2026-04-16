"""Mixin for AI tool handlers that manage system-level operations."""

import asyncio
from pathlib import Path
from typing import Any

from server.utils.logger import get_logger

log = get_logger(__name__)


class SystemToolsMixin:
    """Logs, discovery, themes, assets, ISC, and async waiting tools."""

    async def _get_logs(self, input: dict) -> Any:
        import time
        from server.utils.log_buffer import get_log_buffer

        count = input.get("count", 100)
        category = input.get("category", "")
        level = input.get("level", "")
        search = input.get("search", "")
        since_seconds = input.get("since_seconds")

        entries = get_log_buffer().get_recent(count)
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
        return entries

    async def _wait(self, input: dict) -> Any:
        """Wait for a specified number of seconds before returning.

        Use this when an async operation (discovery scan, device connection test,
        etc.) needs time to complete. Avoids burning tool rounds on rapid polling.
        """
        seconds = input.get("seconds", 30)
        seconds = max(1, min(seconds, 120))  # Clamp to 1-120
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

        subnets = input.get("subnets")
        snmp_enabled = input.get("snmp_enabled", True)
        timeout = input.get("timeout", 120.0)

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
        min_confidence = input.get("min_confidence", 0.0)
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
        if min_confidence > 0:
            devices = [d for d in devices if d["confidence"] >= min_confidence]
        if category:
            devices = [d for d in devices if d.get("category") == category]

        status = discovery_engine.get_status()
        return {
            "devices": devices,
            "total_devices": len(devices),
            "scan_status": status["status"],
            "scan_duration_seconds": status["duration"],
        }

    async def _list_themes(self, input: dict) -> Any:
        from server.api.themes import _list_all_themes

        engine = self._get_engine()
        active_theme = None
        if engine and engine.project:
            active_theme = getattr(engine.project.ui, "theme", None)

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

        builtin_path = BUILTIN_THEMES_DIR / f"{theme_id}.json"
        if builtin_path.exists():
            theme = _load_theme(builtin_path)
            theme["_source"] = "builtin"
            return theme

        try:
            custom_path = _custom_themes_dir() / f"{theme_id}.json"
            if custom_path.exists():
                theme = _load_theme(custom_path)
                theme["_source"] = "custom"
                return theme
        except Exception:
            log.debug("Failed to load custom theme '%s'", theme_id, exc_info=True)

        return {"error": f"Theme '{theme_id}' not found"}

    async def _apply_theme(self, input: dict) -> Any:
        from server.api.themes import BUILTIN_THEMES_DIR, _custom_themes_dir
        from server.core.project_loader import save_project

        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        theme_id = input.get("theme_id", "")
        if not theme_id:
            return {"error": "theme_id is required"}

        # Verify theme exists
        builtin = (BUILTIN_THEMES_DIR / f"{theme_id}.json").exists()
        try:
            custom = (_custom_themes_dir() / f"{theme_id}.json").exists()
        except Exception:
            log.debug("Failed to check custom themes directory", exc_info=True)
            custom = False

        if not builtin and not custom:
            return {"error": f"Theme '{theme_id}' not found"}

        engine.project.ui.theme = theme_id
        save_project(engine.project_path, engine.project)

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
