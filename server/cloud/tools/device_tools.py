"""Mixin for AI tool handlers that manage devices, drivers, and scripts."""

from typing import Any

import httpx

from server.cloud.tools import ToolEditError, apply_tool_edit
from server.utils.paths import is_safe_script_filename, safe_path_within


class DeviceToolsMixin:
    """Device, driver, and script management tools."""

    # ===== DEVICE TOOLS =====

    async def _list_devices(self, input: dict) -> Any:
        return self._devices.list_devices()

    async def _get_device_info(self, input: dict) -> Any:
        device_id = input.get("device_id", "")
        return self._devices.get_device_info(device_id)

    async def _update_device(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        device_id = input.get("device_id", "")
        from server.core.project_loader import DeviceConfig

        # The edit is diffed against the live project, and the devices
        # reconcile hot-swaps the runtime device from the resolved
        # (connection-table-merged) config.
        def mutate(project):
            device_idx = None
            for i, d in enumerate(project.devices):
                if d.id == device_id:
                    device_idx = i
                    break
            if device_idx is None:
                raise ToolEditError({"error": f"Device '{device_id}' not found"})
            existing = project.devices[device_idx]
            if "driver" in input and input["driver"] != existing.driver:
                from server.core.device_manager import _DRIVER_REGISTRY
                if input["driver"] not in _DRIVER_REGISTRY:
                    from server.utils.logger import get_logger
                    log = get_logger(__name__)
                    log.warning("update_device: driver '%s' not in registry (may not be loaded yet)", input["driver"])

            # Split an incoming config the same way _add_device and the REST editor
            # do: connection fields (host/port/...) live in project.connections, not
            # device.config. Without this the AI's host/port edits land in the wrong
            # place and the IDE can't edit them consistently (v0.5.0 layout).
            if "config" in input:
                from server.core.project_migration import CONNECTION_FIELDS
                raw_config = input.get("config") or {}
                protocol_config: dict = {}
                conn_overrides = dict(project.connections.get(device_id, {}))
                for key, value in raw_config.items():
                    if key in CONNECTION_FIELDS:
                        conn_overrides[key] = value
                    else:
                        protocol_config[key] = value
                new_config = protocol_config
                conn_overrides = {k: v for k, v in conn_overrides.items() if v is not None}
                if conn_overrides:
                    project.connections[device_id] = conn_overrides
                elif device_id in project.connections:
                    del project.connections[device_id]
            else:
                new_config = existing.config

            # Re-validate the existing record's full dump instead of building a
            # fresh DeviceConfig from scratch. The base model is extra='allow', so
            # a from-scratch rebuild drops any forward-compat top-level field a
            # newer platform version wrote (__pydantic_extra__) on every AI edit.
            # Dumping then re-validating preserves those and keeps pending_settings
            # + child-entity metadata (user labels / per-child config) — rebuilding
            # from the tool input alone would drop them on disk and in the
            # re-seeded live driver. Honor an `enabled` toggle (the schema declares
            # it); the old code pinned existing.enabled so disable/enable no-op'd.
            merged = existing.model_dump()
            merged.update({
                "driver": input.get("driver", existing.driver),
                "name": input.get("name", existing.name),
                "config": new_config,
                "enabled": input.get("enabled", existing.enabled),
            })
            updated = DeviceConfig.model_validate(merged)
            project.devices[device_idx] = updated

        # The devices reconcile hot-swaps the runtime device with the new
        # resolved config, and the revision bump means a stale IDE PUT gets
        # a 409 instead of silently reverting this edit.
        err = await apply_tool_edit(engine, mutate)
        if err:
            return err
        return {"status": "updated", "device_id": device_id}

    async def _delete_device(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        device_id = input.get("device_id", "")
        # Collect impact before deleting
        impact = self._find_references("device", device_id)

        def mutate(project):
            original_count = len(project.devices)
            project.devices = [d for d in project.devices if d.id != device_id]
            if len(project.devices) == original_count:
                raise ToolEditError({"error": f"Device '{device_id}' not found"})
            # Drop the connections-table entry too — leaving it behind hands a
            # stale host/port to any future device re-added with the same id.
            project.connections.pop(device_id, None)

        # The devices reconcile removes the runtime device and sweeps its
        # orphaned device.<id>.* state keys; the revision bump means a stale
        # tab can't resurrect the deleted device on its next save.
        err = await apply_tool_edit(engine, mutate)
        if err:
            return err
        result: dict = {"status": "deleted", "device_id": device_id}
        if impact:
            result["impact"] = impact
        return result

    async def _add_device(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        device_id = input.get("id", "")
        if not device_id:
            return {"error": "Device ID is required"}
        if any(d.id == device_id for d in engine.project.devices):
            return {"error": f"Device '{device_id}' already exists"}

        driver_id = input.get("driver", "")
        if driver_id:
            from server.core.device_manager import _DRIVER_REGISTRY
            if driver_id not in _DRIVER_REGISTRY:
                from server.utils.logger import get_logger
                log = get_logger(__name__)
                log.warning("add_device: driver '%s' not in registry (may not be loaded yet)", driver_id)

        from server.core.project_loader import DeviceConfig
        from server.core.project_migration import CONNECTION_FIELDS

        # Split config into connection fields and protocol fields
        raw_config = input.get("config", {})
        protocol_config = {}
        conn_overrides = {}
        for key, value in raw_config.items():
            if key in CONNECTION_FIELDS:
                conn_overrides[key] = value
            else:
                protocol_config[key] = value

        new_device = DeviceConfig(
            id=device_id,
            driver=driver_id,
            name=input.get("name", device_id),
            config=protocol_config,
            enabled=input.get("enabled", True),
        )

        # The devices reconcile hot-adds the runtime device from the
        # resolved (driver-defaults + connection-table) config.
        def mutate(project):
            if any(d.id == device_id for d in project.devices):
                raise ToolEditError({"error": f"Device '{device_id}' already exists"})
            project.devices.append(new_device)
            if conn_overrides:
                project.connections[device_id] = conn_overrides

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return {"status": "created", "id": device_id}

    async def _add_device_group(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        group_id = input.get("id", "")
        if not group_id:
            return {"error": "Group ID is required"}
        device_ids = input.get("device_ids", [])

        # The device_groups reconcile reloads the macro engine's group table.
        def mutate(project):
            if any(g.id == group_id for g in project.device_groups):
                raise ToolEditError({"error": f"Group '{group_id}' already exists"})
            if device_ids:
                project_device_ids = {d.id for d in project.devices}
                unknown = [did for did in device_ids if did not in project_device_ids]
                if unknown:
                    raise ToolEditError({"error": f"Device(s) not found in project: {', '.join(unknown)}"})

            from server.core.project_loader import DeviceGroup
            new_group = DeviceGroup(
                id=group_id,
                name=input.get("name", group_id),
                device_ids=device_ids,
            )
            project.device_groups.append(new_group)

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err
        return {"status": "created", "id": group_id}

    async def _update_device_group(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        group_id = input.get("id", "")

        def mutate(project):
            existing = next(
                (g for g in project.device_groups if g.id == group_id), None
            )
            if existing is None:
                raise ToolEditError({"error": f"Group '{group_id}' not found"})
            if "device_ids" in input:
                project_device_ids = {d.id for d in project.devices}
                unknown = [did for did in input["device_ids"] if did not in project_device_ids]
                if unknown:
                    raise ToolEditError({"error": f"Device(s) not found in project: {', '.join(unknown)}"})
            if "name" in input:
                existing.name = input["name"]
            if "device_ids" in input:
                existing.device_ids = input["device_ids"]

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err
        return {"status": "updated", "id": group_id}

    async def _delete_device_group(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        group_id = input.get("id", "")

        def mutate(project):
            original_count = len(project.device_groups)
            project.device_groups = [g for g in project.device_groups if g.id != group_id]
            if len(project.device_groups) == original_count:
                raise ToolEditError({"error": f"Group '{group_id}' not found"})

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err
        return {"status": "deleted", "id": group_id}

    async def _send_device_command(self, input: dict) -> Any:
        device_id = input.get("device_id", "")
        command = input.get("command", "")
        params = input.get("params", {})
        await self._events.emit("ai.device_command", {
            "device_id": device_id, "command": command, "params": params,
        })
        await self._devices.send_command(device_id, command, params)
        return "OK"

    async def _test_device_connection(self, input: dict) -> Any:
        import asyncio as _asyncio
        import time as _time

        device_id = input.get("device_id", "")
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"success": False, "error": "No project loaded", "latency_ms": None}

        device_cfg = None
        for d in engine.project.devices:
            if d.id == device_id:
                device_cfg = d
                break
        if device_cfg is None:
            return {"success": False, "error": f"Device '{device_id}' not found", "latency_ms": None}

        cfg = device_cfg.config
        host = cfg.get("host", "")
        port = cfg.get("port")
        transport = cfg.get("transport", "tcp")
        start = _time.monotonic()

        if transport == "http":
            url = cfg.get("base_url", cfg.get("url", ""))
            if not url and host:
                scheme = "https" if cfg.get("ssl") else "http"
                url = f"{scheme}://{host}" + (f":{port}" if port else "")
            if not url:
                return {"success": False, "error": "No URL configured", "latency_ms": None}
            try:
                import httpx
                async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
                    await client.head(url)
                latency = round((_time.monotonic() - start) * 1000, 1)
                return {"success": True, "error": None, "latency_ms": latency}
            except (httpx.HTTPError, OSError) as e:
                return {"success": False, "error": str(e), "latency_ms": None}
        else:
            if not host:
                return {"success": False, "error": "No host configured", "latency_ms": None}
            # A missing port is a configuration gap, not Telnet — report it
            # rather than silently probing :23 and returning a misleading result.
            if port in (None, "", 0):
                return {"success": False, "error": "No port configured", "latency_ms": None}
            try:
                tcp_port = int(port)
            except (TypeError, ValueError):
                return {"success": False, "error": f"Invalid port: {port!r}", "latency_ms": None}
            if not 0 < tcp_port <= 65535:
                return {"success": False, "error": f"Port out of range: {tcp_port}", "latency_ms": None}
            try:
                reader, writer = await _asyncio.wait_for(
                    _asyncio.open_connection(host, tcp_port), timeout=5.0
                )
                writer.close()
                await writer.wait_closed()
                latency = round((_time.monotonic() - start) * 1000, 1)
                return {"success": True, "error": None, "latency_ms": latency}
            except _asyncio.TimeoutError:
                return {"success": False, "error": "Connection timed out (5s)", "latency_ms": None}
            except OSError as e:
                return {"success": False, "error": str(e), "latency_ms": None}

    async def _get_device_settings(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}

        device_id = input.get("device_id", "")
        if not device_id:
            return {"error": "device_id is required"}

        try:
            settings = engine.devices.get_device_settings(device_id)
            return {"device_id": device_id, "settings": settings}
        except ValueError:
            return {"error": f"Device '{device_id}' not found"}

    async def _set_device_setting(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}

        device_id = input.get("device_id", "")
        setting_key = input.get("setting_key", "")
        value = input.get("value")

        if not device_id or not setting_key:
            return {"error": "device_id and setting_key are required"}

        # State holds flat primitives only — reject a list/dict/other before it
        # reaches the driver (which str()-ifies it into the protocol) or the
        # state store. bool is an int subclass, so it's covered.
        if value is not None and not isinstance(value, (str, int, float)):
            return {"error": "Setting value must be a string, number, boolean, or null"}

        try:
            await engine.devices.set_device_setting(device_id, setting_key, value)
            return {"success": True, "device_id": device_id, "key": setting_key, "value": value}
        except ValueError:
            return {"error": f"Device '{device_id}' or setting '{setting_key}' not found"}
        except ConnectionError:
            return {"error": f"Device '{device_id}' is not connected"}
        except NotImplementedError:
            return {"error": f"Device '{device_id}' does not support writable settings"}

    # ===== DRIVER TOOLS =====

    async def _list_drivers(self, input: dict) -> Any:
        from server.core.device_manager import get_driver_registry
        return get_driver_registry()

    async def _search_community_drivers(self, input: dict) -> Any:
        """Search the community driver catalog with filters and ranking.

        Inputs (all optional):
          query        - free-text matched against id, name, manufacturer,
                         description, tags, and compatible model names
          category     - exact category filter (projector, display, switcher,
                         audio, camera, lighting, video, streaming, power,
                         utility)
          manufacturer - case-insensitive manufacturer match
          transport    - tcp | http | osc | serial | udp
          limit        - cap on result count (default 25, max 100)

        Returns lean entries (omits help and compatible_models — call
        get_community_driver_detail for those). Results are ranked: exact id
        and manufacturer matches first, then name matches, then description /
        tag hits.
        """
        drivers = await self._fetch_community_index()
        if drivers is None:
            return {"drivers": [], "total": 0, "error": "Failed to fetch community index"}

        query = (input.get("query") or "").strip().lower()
        category = (input.get("category") or "").strip().lower()
        manufacturer = (input.get("manufacturer") or "").strip().lower()
        transport = (input.get("transport") or "").strip().lower()
        try:
            limit = max(1, min(int(input.get("limit", 25)), 100))
        except (TypeError, ValueError):
            limit = 25

        results: list[tuple[int, dict]] = []
        for drv in drivers:
            if category and (drv.get("category") or "").lower() != category:
                continue
            if manufacturer and (drv.get("manufacturer") or "").lower() != manufacturer:
                continue
            if transport and (drv.get("transport") or "").lower() != transport:
                continue

            score = self._score_driver_match(drv, query) if query else 1
            if query and score == 0:
                continue
            results.append((score, drv))

        results.sort(key=lambda pair: (-pair[0], pair[1].get("id", "")))
        total = len(results)
        trimmed = [self._lean_driver_entry(d) for _, d in results[:limit]]
        return {
            "drivers": trimmed,
            "total": total,
            "returned": len(trimmed),
            "truncated": total > len(trimmed),
            "error": None,
        }

    async def _get_community_driver_detail(self, input: dict) -> Any:
        """Return the full community catalog entry for a single driver.

        Includes help.overview, help.setup, compatible_models, and other heavy
        fields stripped from search results. Use this after search to read a
        driver's setup notes or check whether a specific model is supported.
        """
        driver_id = (input.get("driver_id") or "").strip()
        if not driver_id:
            return {"error": "driver_id is required"}

        drivers = await self._fetch_community_index()
        if drivers is None:
            return {"error": "Failed to fetch community index"}

        for drv in drivers:
            if drv.get("id") == driver_id:
                base_url = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main"
                if "file" in drv and "download_url" not in drv:
                    drv = {**drv, "download_url": f"{base_url}/{drv['file']}"}
                return drv
        return {"error": f"Driver '{driver_id}' not found in community catalog"}

    async def _find_driver_for_device(self, input: dict) -> Any:
        """Look up community drivers that control a specific device by exact
        manufacturer + model match using the curated devices.json catalog.

        Use this when the user names specific hardware (e.g. "Sharp NEC
        NP-PA853UL"). Returns matching driver entries with confidence
        ('verified', 'tested', 'untested'). Empty list when no exact match —
        fall back to search_community_drivers in that case.
        """
        manufacturer = (input.get("manufacturer") or "").strip()
        model = (input.get("model") or "").strip()
        if not manufacturer or not model:
            return {"matches": [], "error": "manufacturer and model are required"}

        base_url = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{base_url}/devices.json")
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, OSError) as e:
            return {"matches": [], "error": str(e)}

        devices = data.get("devices", []) if isinstance(data, dict) else data
        mfr_lower = manufacturer.lower()
        model_lower = model.lower()
        matches: list[dict] = []
        for dev in devices:
            if (dev.get("manufacturer") or "").lower() != mfr_lower:
                continue
            if (dev.get("model") or "").lower() != model_lower:
                continue
            for drv in dev.get("drivers", []) or []:
                matches.append({
                    "driver_id": drv.get("id"),
                    "confidence": drv.get("confidence", "untested"),
                    "notes": drv.get("notes"),
                })
        return {
            "manufacturer": manufacturer,
            "model": model,
            "matches": matches,
            "error": None,
        }

    # --- helpers for community catalog tools ---

    async def _fetch_community_index(self) -> list[dict] | None:
        """Fetch and cache the community index.json. Returns None on failure."""
        cache = getattr(self, "_community_index_cache", None)
        import time
        now = time.time()
        if cache and (now - cache["fetched_at"]) < 600:
            return cache["drivers"]

        base_url = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{base_url}/index.json")
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, OSError):
            return cache["drivers"] if cache else None

        drivers = data.get("drivers", []) if isinstance(data, dict) else data
        for drv in drivers:
            if "file" in drv:
                drv["download_url"] = f"{base_url}/{drv['file']}"
        self._community_index_cache = {"drivers": drivers, "fetched_at": now}
        return drivers

    @staticmethod
    def _score_driver_match(drv: dict, query: str) -> int:
        """Rank a driver against a free-text query. Higher score = better match."""
        if not query:
            return 1
        score = 0
        drv_id = (drv.get("id") or "").lower()
        name = (drv.get("name") or "").lower()
        manufacturer = (drv.get("manufacturer") or "").lower()
        description = (drv.get("description") or "").lower()
        tags = [t.lower() for t in (drv.get("tags") or []) if isinstance(t, str)]
        compat_models = []
        for entry in drv.get("compatible_models") or []:
            for m in entry.get("models") or []:
                if isinstance(m, str):
                    compat_models.append(m.lower())

        if query == drv_id or query == manufacturer:
            score += 100
        if query in drv_id:
            score += 30
        if query in manufacturer:
            score += 30
        if query in name:
            score += 20
        if any(query == t for t in tags):
            score += 15
        if any(query in t for t in tags):
            score += 5
        if any(query in m for m in compat_models):
            score += 25
        if query in description:
            score += 3
        return score

    @staticmethod
    def _lean_driver_entry(drv: dict) -> dict:
        """Return only the fields needed to pick a driver. Drops help, compatible_models, and source_url."""
        keep = (
            "id", "name", "manufacturer", "category", "version", "author",
            "transport", "description", "file", "format", "ports", "protocols",
            "tags", "simulated", "verified", "deprecated", "replacement_id",
            "min_platform_version", "download_url",
        )
        return {k: drv[k] for k in keep if k in drv}

    async def _get_installed_drivers(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"drivers": []}
        driver_repo = self._get_driver_repo_dir()
        if not driver_repo.exists():
            return {"drivers": []}

        installed = []
        for filepath in sorted(driver_repo.glob("*.avcdriver")):
            try:
                import yaml
                data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    installed.append({
                        "id": data.get("id", filepath.stem),
                        "name": data.get("name", filepath.stem),
                        "format": "avcdriver",
                        "filename": filepath.name,
                    })
            except (OSError, yaml.YAMLError, ValueError):
                installed.append({"id": filepath.stem, "name": filepath.stem, "format": "avcdriver", "filename": filepath.name})

        for filepath in sorted(driver_repo.glob("*.py")):
            if filepath.name.startswith("_"):
                continue
            installed.append({"id": filepath.stem, "name": filepath.stem.replace("_", " ").title(), "format": "python", "filename": filepath.name})

        return {"drivers": installed}

    async def _get_driver_definition(self, input: dict) -> Any:
        from server.drivers.driver_loader import list_driver_definitions
        driver_id = input.get("driver_id", "")
        dirs = self._get_driver_dirs()
        for d in list_driver_definitions(dirs):
            if d.get("id") == driver_id:
                d.pop("_source_file", None)
                return d
        return {"error": f"Driver definition '{driver_id}' not found"}

    @staticmethod
    def _platform_too_old(required: str) -> bool:
        """True when the running OpenAVC is older than ``required`` (semver).

        Non-raising counterpart of the REST ``_enforce_min_platform_version``
        (which raises HTTPException) for the dict-returning tool layer.
        """
        from server.version import __version__

        def _parse(v: str) -> tuple:
            return tuple(int(x) for x in v.split(".")[:3] if x.isdigit())

        try:
            return _parse(__version__) < _parse(required)
        except (ValueError, AttributeError):
            return False

    async def _install_community_driver(self, input: dict) -> Any:
        from urllib.parse import urlparse

        from server.core.device_manager import register_driver
        from server.drivers.driver_loader import load_driver_file, load_python_driver_file
        from server.drivers.configurable import create_configurable_driver_class
        # Reuse the REST install's GitHub allowlist + YAML peek so both install
        # paths stay on one source of truth.
        from server.api.routes.drivers import _GITHUB_HOSTS, _peek_min_platform_version

        driver_id = input.get("driver_id", "")
        file_url = input.get("file_url", "")
        if not file_url:
            return {"error": "No file_url provided"}

        # SSRF guard: the AI is semi-trusted and fetches from the agent's network
        # position. Restrict to GitHub hosts (the community repo) so the AI path
        # can't be steered at intranet or cloud-metadata endpoints — parity with
        # the REST /api/drivers/install allowlist.
        parsed_url = urlparse(file_url)
        if not parsed_url.hostname or parsed_url.hostname not in _GITHUB_HOSTS:
            return {"error": f"Driver URL must be from GitHub ({', '.join(sorted(_GITHUB_HOSTS))})"}

        # min_platform_version gate (parity with REST): block a driver declaring a
        # newer platform than we run. Enforced from the request field up front,
        # then again from the YAML itself after download.
        req_min = input.get("min_platform_version")
        if req_min and self._platform_too_old(req_min):
            return {"error": f"This driver requires OpenAVC {req_min} or later. Update OpenAVC first."}

        driver_repo = self._get_driver_repo_dir()
        driver_repo.mkdir(parents=True, exist_ok=True)

        ext = ".avcdriver" if file_url.endswith(".avcdriver") else ".py" if file_url.endswith(".py") else ""
        if not ext:
            return {"error": "URL must point to a .avcdriver or .py file"}

        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in driver_id)
        filepath = driver_repo / f"{safe_id}{ext}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(file_url)
                resp.raise_for_status()
                text = resp.text
                filepath.write_text(text, encoding="utf-8")
        except (httpx.HTTPError, OSError) as e:
            return {"error": f"Download failed: {e}"}

        # Authoritative min-version gate from the file itself (independent of the
        # request field, so a caller can't skip it by omitting the field).
        if ext == ".avcdriver":
            yaml_min = _peek_min_platform_version(text)
            if yaml_min and self._platform_too_old(yaml_min):
                filepath.unlink(missing_ok=True)
                return {"error": f"This driver requires OpenAVC {yaml_min} or later. Update OpenAVC first."}

        try:
            if ext == ".avcdriver":
                driver_def = load_driver_file(filepath)
                if driver_def is None:
                    filepath.unlink(missing_ok=True)
                    return {"error": "Invalid driver definition file"}
                driver_class = create_configurable_driver_class(driver_def)
            else:
                driver_class = load_python_driver_file(filepath)
                if driver_class is None:
                    filepath.unlink(missing_ok=True)
                    return {"error": "No valid driver class found in Python file"}
            # The registry keys on the file's own DRIVER_INFO id, but the
            # filename and the listing/edit/delete tools key on the requested
            # driver_id. If they diverge, later edit/delete can't find the driver
            # and a mismatched id can silently overwrite an unrelated registered
            # driver — require them to agree before registering.
            info = getattr(driver_class, "DRIVER_INFO", None)
            internal_id = info.get("id") if isinstance(info, dict) else None
            if internal_id and internal_id != driver_id:
                filepath.unlink(missing_ok=True)
                return {"error": f"Downloaded driver declares id '{internal_id}', not the requested '{driver_id}'. Install it under id '{internal_id}'."}
            register_driver(driver_class)
        except Exception as e:
            # Catch-all: driver loading can fail with YAML, import, or validation errors
            filepath.unlink(missing_ok=True)
            return {"error": f"Failed to load driver: {e}"}

        return {"status": "installed", "driver_id": driver_id, "file": filepath.name}

    async def _create_driver_definition(self, input: dict) -> Any:
        from server.drivers.driver_loader import list_driver_definitions, save_driver_definition, validate_driver_definition
        from server.drivers.configurable import create_configurable_driver_class
        from server.core.device_manager import register_driver

        definition = input.get("definition", {})
        if not definition.get("id"):
            return {"error": "Driver definition must have an 'id' field"}

        dirs = self._get_driver_dirs()
        existing = list_driver_definitions(dirs)
        if any(d.get("id") == definition["id"] for d in existing):
            return {"error": f"Driver definition '{definition['id']}' already exists"}

        errors = validate_driver_definition(definition)
        if errors:
            return {"error": "; ".join(errors)}

        save_dir = dirs[1] if len(dirs) > 1 else dirs[0]
        save_driver_definition(definition, save_dir)
        driver_class = create_configurable_driver_class(definition)
        register_driver(driver_class)
        return {"status": "created", "id": definition["id"]}

    async def _update_driver_definition(self, input: dict) -> Any:
        from pathlib import Path

        from server.drivers.driver_loader import (
            list_driver_definitions,
            restore_driver_registration,
            save_driver_definition,
            validate_driver_definition,
        )
        from server.drivers.configurable import create_configurable_driver_class
        from server.core.device_manager import register_driver
        from server.system_config import DRIVER_DEFINITIONS_DIR

        driver_id = input.get("driver_id", "")
        definition = input.get("definition", {})
        if not definition.get("id"):
            return {"error": "Driver definition must have an 'id' field"}
        dirs = self._get_driver_dirs()

        existing = list_driver_definitions(dirs)
        match = next((d for d in existing if d.get("id") == driver_id), None)
        if match is None:
            return {"error": f"Driver definition '{driver_id}' not found"}

        # Source guard: never modify a shipped built-in (the IDE enforces this
        # with a read-only gate; the AI path must too). Built-ins live in the
        # read-only DRIVER_DEFINITIONS_DIR; user drivers live in driver_repo.
        builtin_root = str(Path(DRIVER_DEFINITIONS_DIR).resolve())
        src = match.get("_source_file")
        old_path = Path(src) if src else None
        if old_path and str(old_path.resolve()).startswith(builtin_root):
            return {"error": f"Driver '{driver_id}' is a built-in and cannot be modified. Duplicate it under a new id first."}

        errors = validate_driver_definition(definition)
        if errors:
            return {"error": "; ".join(errors)}

        # A rename onto an id owned by a DIFFERENT driver would clobber it.
        new_id = definition["id"]
        if new_id != driver_id and any(d.get("id") == new_id for d in existing):
            return {"error": f"A driver definition with id '{new_id}' already exists"}

        # Save the replacement FIRST (save_driver_definition is an atomic
        # temp-write + os.replace), then drop the old file only when the id
        # changed (different filename). The previous delete-then-save could leave
        # the user with no driver file at all if the save failed.
        save_dir = dirs[1] if len(dirs) > 1 else dirs[0]
        new_path = save_driver_definition(definition, save_dir)
        if old_path and old_path.resolve() != new_path.resolve():
            old_path.unlink(missing_ok=True)
        driver_class = create_configurable_driver_class(definition)
        register_driver(driver_class)
        if new_id != driver_id:
            # The rename removed the user file for the old id. If it was
            # overriding a shipped built-in, re-register the built-in so the
            # old id keeps working; otherwise drop the stale registration.
            restore_driver_registration(driver_id, dirs)
        return {"status": "updated", "id": new_id}

    async def _test_driver_command(self, input: dict) -> Any:
        import asyncio
        from server.transport.tcp import TCPTransport

        host = input.get("host", "")
        port = input.get("port", 23)
        command_string = input.get("command_string", "")
        delimiter = input.get("delimiter", "\\r\\n")
        timeout = input.get("timeout", 5)

        if not host or not command_string:
            return {"success": False, "error": "host and command_string are required", "response": None}

        # A delimiter routinely carries backslash escapes (\r\n); a truncated one
        # like '\x' raises here, outside the connection try/except below — catch
        # it and return an actionable message instead of an opaque codec crash.
        try:
            delimiter_bytes = delimiter.encode().decode("unicode_escape").encode()
        except (UnicodeDecodeError, UnicodeEncodeError) as e:
            return {"success": False, "error": f"Invalid delimiter escape sequence {delimiter!r}: {e}", "response": None}

        try:
            transport = await TCPTransport.create(
                host=host, port=port,
                on_data=lambda d: None, on_disconnect=lambda: None,
                delimiter=delimiter_bytes, timeout=timeout,
            )
        except ConnectionError as e:
            return {"success": False, "error": str(e), "response": None}

        try:
            cmd_data = command_string.encode().decode("unicode_escape").encode()
            response = await transport.send_and_wait(cmd_data, timeout=timeout)
            response_text = response.decode("ascii", errors="replace")
            return {"success": True, "error": None, "response": response_text}
        except asyncio.TimeoutError:
            return {"success": False, "error": "Timeout waiting for response", "response": None}
        except (OSError, ConnectionError) as e:
            return {"success": False, "error": str(e), "response": None}
        finally:
            await transport.close()

    # ===== SCRIPT TOOLS =====

    async def _get_script_source(self, input: dict) -> Any:
        script_id = input.get("script_id", "")
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        for s in engine.project.scripts:
            if s.id == script_id:
                scripts_dir = engine.project_path.parent / "scripts"
                path = safe_path_within(scripts_dir, s.file)
                if path is None:
                    return {"error": "Invalid script filename"}
                if path.exists():
                    source = path.read_text(encoding="utf-8")
                    return {"id": script_id, "file": s.file, "source": source}
                return {"error": f"Script file not found: {s.file}"}
        return {"error": f"Script '{script_id}' not found"}

    async def _create_script(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        script_id = input.get("id", "")
        filename = input.get("file", f"{script_id}.py")
        source = input.get("source", "")
        description = input.get("description", "")
        enabled = input.get("enabled", True)

        for s in engine.project.scripts:
            if s.id == script_id:
                return {"error": f"Script '{script_id}' already exists"}

        if source:
            from server.cloud.ai_tool_handler import _validate_script_syntax
            err = _validate_script_syntax(source, filename)
            if err:
                return {"error": f"Script '{script_id}': {err}"}

        scripts_dir = engine.project_path.parent / "scripts"
        if not is_safe_script_filename(filename):
            return {"error": f"Invalid script filename '{filename}': must be a plain .py name with no path separators"}
        path = safe_path_within(scripts_dir, filename)
        if path is None:
            return {"error": "Invalid script filename"}
        scripts_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

        from server.core.project_loader import ScriptConfig
        new_script = ScriptConfig(id=script_id, file=filename, enabled=enabled, description=description)

        # The scripts reconcile loads just this script (per-script reload,
        # not the old full-project reload).
        def mutate(project):
            for s in project.scripts:
                if s.id == script_id:
                    raise ToolEditError({"error": f"Script '{script_id}' already exists"})
            project.scripts.append(new_script)

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err
        return {"status": "created", "id": script_id}

    async def _update_script_source(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        script_id = input.get("script_id", "")
        source = input.get("source", "")
        for s in engine.project.scripts:
            if s.id == script_id:
                if source:
                    from server.cloud.ai_tool_handler import _validate_script_syntax
                    err = _validate_script_syntax(source, s.file)
                    if err:
                        return {"error": f"Script '{script_id}': {err}"}
                scripts_dir = engine.project_path.parent / "scripts"
                path = safe_path_within(scripts_dir, s.file)
                if path is None:
                    return {"error": "Invalid script filename"}
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
                # A source write alone is inert — reload the running script
                # like the IDE reload button. reload_script imports the new
                # version before unloading the old, so a broken edit leaves
                # the previous version active (reported in the result).
                if engine.scripts:
                    reload_result = engine.scripts.reload_script(s.model_dump())
                    return {"status": "saved", "reload": reload_result}
                return {"status": "saved"}
        return {"error": f"Script '{script_id}' not found"}

    async def _delete_script(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        script_id = input.get("script_id", "")
        cfg = None
        for s in engine.project.scripts:
            if s.id == script_id:
                cfg = s
                break
        if not cfg:
            return {"error": f"Script '{script_id}' not found"}

        scripts_dir = engine.project_path.parent / "scripts"
        path = safe_path_within(scripts_dir, cfg.file)
        if path is None:
            return {"error": "Invalid script filename"}
        if path.exists():
            path.unlink()

        # The scripts reconcile unloads just this script.
        def mutate(project):
            original_count = len(project.scripts)
            project.scripts = [s for s in project.scripts if s.id != script_id]
            if len(project.scripts) == original_count:
                raise ToolEditError({"error": f"Script '{script_id}' not found"})

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err
        return {"status": "deleted"}
