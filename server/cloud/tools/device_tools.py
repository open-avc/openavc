"""Mixin for AI tool handlers that manage devices, drivers, and scripts."""

from typing import Any

import httpx


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
        from server.core.project_loader import DeviceConfig, save_project
        device_idx = None
        for i, d in enumerate(engine.project.devices):
            if d.id == device_id:
                device_idx = i
                break
        if device_idx is None:
            return {"error": f"Device '{device_id}' not found"}
        existing = engine.project.devices[device_idx]
        if "driver" in input and input["driver"] != existing.driver:
            from server.core.device_manager import _DRIVER_REGISTRY
            if input["driver"] not in _DRIVER_REGISTRY:
                from server.utils.logger import get_logger
                log = get_logger(__name__)
                log.warning("update_device: driver '%s' not in registry (may not be loaded yet)", input["driver"])
        updated = DeviceConfig(
            id=device_id,
            driver=input.get("driver", existing.driver),
            name=input.get("name", existing.name),
            config=input.get("config", existing.config),
            enabled=existing.enabled,
        )
        engine.project.devices[device_idx] = updated
        save_project(engine.project_path, engine.project)
        await engine.devices.update_device(device_id, updated.model_dump())
        return {"status": "updated", "device_id": device_id}

    async def _delete_device(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        device_id = input.get("device_id", "")
        original_count = len(engine.project.devices)
        engine.project.devices = [d for d in engine.project.devices if d.id != device_id]
        if len(engine.project.devices) == original_count:
            return {"error": f"Device '{device_id}' not found"}
        # Collect impact before saving
        impact = self._find_references("device", device_id)
        from server.core.project_loader import save_project
        save_project(engine.project_path, engine.project)
        await engine.devices.remove_device(device_id)
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

        from server.core.project_loader import DeviceConfig, save_project
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
        engine.project.devices.append(new_device)
        if conn_overrides:
            engine.project.connections[device_id] = conn_overrides
        save_project(engine.project_path, engine.project)

        # Hot-add via device manager with merged config
        await engine.devices.add_device(engine.resolved_device_config(new_device))
        await self._notify_project_changed()

        return {"status": "created", "id": device_id}

    async def _add_device_group(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        group_id = input.get("id", "")
        if not group_id:
            return {"error": "Group ID is required"}
        if any(g.id == group_id for g in engine.project.device_groups):
            return {"error": f"Group '{group_id}' already exists"}
        device_ids = input.get("device_ids", [])
        if device_ids:
            project_device_ids = {d.id for d in engine.project.devices}
            unknown = [did for did in device_ids if did not in project_device_ids]
            if unknown:
                return {"error": f"Device(s) not found in project: {', '.join(unknown)}"}

        from server.core.project_loader import DeviceGroup, save_project
        new_group = DeviceGroup(
            id=group_id,
            name=input.get("name", group_id),
            device_ids=device_ids,
        )
        engine.project.device_groups.append(new_group)
        save_project(engine.project_path, engine.project)
        # Reload groups in macro engine
        engine.macros.load_groups([g.model_dump() for g in engine.project.device_groups])
        await self._notify_project_changed()
        return {"status": "created", "id": group_id}

    async def _update_device_group(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        group_id = input.get("id", "")
        group_idx = None
        for i, g in enumerate(engine.project.device_groups):
            if g.id == group_id:
                group_idx = i
                break
        if group_idx is None:
            return {"error": f"Group '{group_id}' not found"}
        from server.core.project_loader import save_project
        existing = engine.project.device_groups[group_idx]
        if "device_ids" in input:
            project_device_ids = {d.id for d in engine.project.devices}
            unknown = [did for did in input["device_ids"] if did not in project_device_ids]
            if unknown:
                return {"error": f"Device(s) not found in project: {', '.join(unknown)}"}
        if "name" in input:
            existing.name = input["name"]
        if "device_ids" in input:
            existing.device_ids = input["device_ids"]
        save_project(engine.project_path, engine.project)
        engine.macros.load_groups([g.model_dump() for g in engine.project.device_groups])
        await self._notify_project_changed()
        return {"status": "updated", "id": group_id}

    async def _delete_device_group(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        group_id = input.get("id", "")
        original_count = len(engine.project.device_groups)
        engine.project.device_groups = [g for g in engine.project.device_groups if g.id != group_id]
        if len(engine.project.device_groups) == original_count:
            return {"error": f"Group '{group_id}' not found"}
        from server.core.project_loader import save_project
        save_project(engine.project_path, engine.project)
        engine.macros.load_groups([g.model_dump() for g in engine.project.device_groups])
        await self._notify_project_changed()
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
            tcp_port = int(port) if port else 23
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
        base_url = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main"
        index_url = f"{base_url}/index.json"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(index_url)
                resp.raise_for_status()
                data = resp.json()
                drivers = data.get("drivers", []) if isinstance(data, dict) else data
                # Add ready-to-use download URL so the AI doesn't have to construct it
                for drv in drivers:
                    if "file" in drv:
                        drv["download_url"] = f"{base_url}/{drv['file']}"
                return {"drivers": drivers, "error": None}
        except (httpx.HTTPError, OSError) as e:
            return {"drivers": [], "error": str(e)}

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

    async def _install_community_driver(self, input: dict) -> Any:
        from server.core.device_manager import register_driver
        from server.drivers.driver_loader import load_driver_file, load_python_driver_file
        from server.drivers.configurable import create_configurable_driver_class

        driver_id = input.get("driver_id", "")
        file_url = input.get("file_url", "")
        if not file_url:
            return {"error": "No file_url provided"}

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
                filepath.write_text(resp.text, encoding="utf-8")
        except (httpx.HTTPError, OSError) as e:
            return {"error": f"Download failed: {e}"}

        try:
            if ext == ".avcdriver":
                driver_def = load_driver_file(filepath)
                if driver_def is None:
                    filepath.unlink(missing_ok=True)
                    return {"error": "Invalid driver definition file"}
                driver_class = create_configurable_driver_class(driver_def)
                register_driver(driver_class)
            else:
                driver_class = load_python_driver_file(filepath)
                if driver_class is None:
                    filepath.unlink(missing_ok=True)
                    return {"error": "No valid driver class found in Python file"}
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
        from server.drivers.driver_loader import delete_driver_definition, list_driver_definitions, save_driver_definition, validate_driver_definition
        from server.drivers.configurable import create_configurable_driver_class
        from server.core.device_manager import register_driver

        driver_id = input.get("driver_id", "")
        definition = input.get("definition", {})
        dirs = self._get_driver_dirs()

        existing = list_driver_definitions(dirs)
        if not any(d.get("id") == driver_id for d in existing):
            return {"error": f"Driver definition '{driver_id}' not found"}

        errors = validate_driver_definition(definition)
        if errors:
            return {"error": "; ".join(errors)}

        delete_driver_definition(driver_id, dirs)
        save_dir = dirs[1] if len(dirs) > 1 else dirs[0]
        save_driver_definition(definition, save_dir)
        driver_class = create_configurable_driver_class(definition)
        register_driver(driver_class)
        return {"status": "updated", "id": definition.get("id", driver_id)}

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

        delimiter_bytes = delimiter.encode().decode("unicode_escape").encode()

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
                path = scripts_dir / s.file
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
        scripts_dir.mkdir(parents=True, exist_ok=True)
        path = scripts_dir / filename
        path.write_text(source, encoding="utf-8")

        from server.core.project_loader import ScriptConfig, save_project
        new_script = ScriptConfig(id=script_id, file=filename, enabled=enabled, description=description)
        engine.project.scripts.append(new_script)
        save_project(engine.project_path, engine.project)
        if self._reload_fn:
            await self._reload_fn()
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
                path = scripts_dir / s.file
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
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
        path = scripts_dir / cfg.file
        if path.exists():
            path.unlink()
        from server.core.project_loader import save_project
        engine.project.scripts = [s for s in engine.project.scripts if s.id != script_id]
        save_project(engine.project_path, engine.project)
        if self._reload_fn:
            await self._reload_fn()
        return {"status": "deleted"}
