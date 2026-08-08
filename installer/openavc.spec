# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for OpenAVC server.

Bundles the Python server, all dependencies, frontends, drivers, themes,
and default project into a standalone directory.

Build: pyinstaller installer/openavc.spec
Output: dist/openavc/
"""

import os
import sys
from pathlib import Path

block_cipher = None

# Project root (one level up from installer/)
PROJECT_ROOT = Path(SPECPATH).parent

# Collect data files: (source, dest_in_bundle)
datas = [
    # Frontend builds
    (str(PROJECT_ROOT / 'openavc' / 'web' / 'panel'), 'openavc/web/panel'),
    (str(PROJECT_ROOT / 'openavc' / 'web' / 'programmer' / 'dist'), 'openavc/web/programmer/dist'),
    (str(PROJECT_ROOT / 'openavc' / 'web' / 'simulator' / 'dist'), 'openavc/web/simulator/dist'),
    # Simulator package (Python — runs as subprocess)
    (str(PROJECT_ROOT / 'openavc' / 'simulator'), 'openavc/simulator'),
    # Driver definitions (built-in YAML drivers)
    (str(PROJECT_ROOT / 'openavc' / 'drivers' / 'definitions'), 'openavc/drivers/definitions'),
    # Built-in starter project templates (project_library.ensure_starter_projects
    # seeds the project library from here on first run; it silently skips when
    # the directory is missing, so leaving this out ships an empty starter
    # library with no error)
    (str(PROJECT_ROOT / 'openavc' / 'templates'), 'openavc/templates'),
    # Note: driver_repo and plugin_repo are no longer bundled into _internal/.
    # Both live under the persistent data directory now (see system_config.py)
    # so user-installed community drivers and plugins survive uninstalls and
    # upgrades. The runtime creates them on first start.
    # Themes
    (str(PROJECT_ROOT / 'openavc' / 'themes'), 'openavc/themes'),
    # Clean starter project (not the dev project which may have cloud pairing, assets, etc.)
    (str(PROJECT_ROOT / 'installer' / 'seed' / 'default'), 'projects/default'),
    # User templates
    (str(PROJECT_ROOT / 'user_templates'), 'user_templates'),
    # pyproject.toml (for version reading)
    (str(PROJECT_ROOT / 'pyproject.toml'), '.'),
]

# Filter out any source paths that don't exist
datas = [(src, dst) for src, dst in datas if os.path.exists(src)]

# Hidden imports that PyInstaller can't detect (dynamic imports in FastAPI, uvicorn, etc.)
hiddenimports = [
    # Uvicorn protocol implementations (loaded dynamically)
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.http.httptools_impl',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    'uvicorn.loops.auto',
    'uvicorn.loops.asyncio',
    # FastAPI/Starlette dynamic imports
    'starlette.responses',
    'starlette.routing',
    'starlette.middleware',
    'starlette.middleware.cors',
    'starlette.middleware.base',
    'starlette.staticfiles',
    'starlette.exceptions',
    'starlette.formparsers',
    'multipart',
    'multipart.multipart',
    # Pydantic (uses dynamic model compilation)
    'pydantic',
    'pydantic.deprecated',
    'pydantic_core',
    'annotated_types',
    # Encoding support
    'encodings.idna',
    # IANA tz database — zoneinfo loads it dynamically (cloud maintenance-window
    # timezones), so PyInstaller's static analysis misses it. Listing it here
    # makes the bundled hook-tzdata collect the data files into the frozen app.
    'tzdata',
    # Our server modules (some loaded dynamically)
    'openavc.main',
    'openavc.config',
    'openavc.system_config',
    'openavc.version',
    'openavc.core.engine',
    'openavc.core.state_store',
    'openavc.core.event_bus',
    'openavc.core.device_manager',
    'openavc.core.setup_actions',
    'openavc.core.macro_engine',
    'openavc.core.script_engine',
    'openavc.core.script_api',
    'openavc.core.trigger_engine',
    'openavc.core.isc',
    'openavc.core.plugin_api',
    'openavc.core.plugin_loader',
    'openavc.core.plugin_registry',
    'openavc.core.plugin_installer',
    'openavc.core.plugin_artifacts',
    'openavc.core.plugin_wheels',
    'openavc.core.plugin_native_deps',
    'openavc.core.project_loader',
    'openavc.core.project_migration',
    'openavc.core.project_library',
    'openavc.transport.tcp',
    'openavc.transport.serial_transport',
    'openavc.transport.udp',
    'openavc.transport.multicast_listener',
    'openavc.transport.tcp_listener',
    'openavc.transport.http_listener',
    'openavc.transport.http_client',
    'openavc.transport.osc',
    'openavc.transport.osc_codec',
    'openavc.transport.ssh',
    'openavc.transport.mqtt',
    'openavc.transport.frame_parsers',
    'openavc.transport.binary_helpers',
    'openavc.transport.wire_log',
    'openavc.transport.ir_codec',
    'openavc.transport.ir_render',
    'openavc.drivers.base',
    'openavc.drivers.actions',
    'openavc.drivers.configurable',
    'openavc.drivers.driver_loader',
    'openavc.drivers.registry',
    'openavc.discovery.engine',
    'openavc.discovery.network_scanner',
    'openavc.discovery.port_scanner',
    'openavc.discovery.snmp_scanner',
    'openavc.discovery.mdns_scanner',
    'openavc.discovery.ssdp_scanner',
    'openavc.discovery.amx_ddp_scanner',
    'openavc.discovery.tier_matcher',
    'openavc.discovery.community_index',
    'openavc.discovery.hints',
    'openavc.discovery.oui_database',
    'openavc.discovery.result',
    'openavc.discovery.probe_runner',
    'openavc.discovery.companion',
    'openavc.updater',
    'openavc.updater.checker',
    'openavc.updater.manager',
    'openavc.updater.backup',
    'openavc.updater.rollback',
    'openavc.updater.platform',
    'openavc.cloud.agent',
    'openavc.cloud.config',
    'openavc.cloud.crypto',
    'openavc.cloud.protocol',
    'openavc.cloud.handshake',
    'openavc.cloud.session',
    'openavc.cloud.sequencer',
    'openavc.cloud.heartbeat',
    'openavc.cloud.state_relay',
    'openavc.cloud.command_handler',
    'openavc.cloud.alert_monitor',
    'openavc.cloud.tunnel',
    'openavc.cloud.ai_tool_handler',
    'openavc.cloud.tools',
    'openavc.cloud.tools.project_tools',
    'openavc.cloud.tools.device_tools',
    'openavc.cloud.tools.ui_tools',
    'openavc.cloud.tools.plugin_tools',
    'openavc.cloud.tools.macro_tools',
    'openavc.cloud.tools.system_tools',
    'openavc.api.rest',
    'openavc.api.ws',
    'openavc.api.isc_ws',
    'openavc.api.discovery',
    'openavc.api.plugins',
    'openavc.api.assets',
    'openavc.api.themes',
    'openavc.api.auth',
    'openavc.api.models',
    # Every module under server/api/routes/ — keep this in step when adding one.
    'openavc.api.routes.auth',
    'openavc.api.routes.cloud',
    'openavc.api.routes.devices',
    'openavc.api.routes.driver_test',
    'openavc.api.routes.drivers',
    'openavc.api.routes.host',
    'openavc.api.routes.ir_db',
    'openavc.api.routes.isc',
    'openavc.api.routes.macros',
    'openavc.api.routes.network',
    'openavc.api.routes.pair',
    'openavc.api.routes.project',
    'openavc.api.routes.push',
    'openavc.api.routes.python_drivers',
    'openavc.api.routes.root',
    'openavc.api.routes.scripts',
    'openavc.api.routes.setup',
    'openavc.api.routes.simulation',
    'openavc.api.routes.system',
    'openavc.api.routes.tls',
    'openavc.api.routes.updates',
    'openavc.api.routes.variables',
    'openavc.utils.logger',
    'openavc.utils.log_buffer',
    'openavc.middleware.rate_limit',
    # Simulator package (launched as subprocess via python -m simulator)
    'openavc.simulator',
    'openavc.simulator.server',
    'openavc.simulator.engine',
    'openavc.simulator.api',
    'openavc.simulator.base',
    'openavc.simulator.tcp_simulator',
    'openavc.simulator.http_simulator',
    'openavc.simulator.osc_simulator',
    'openavc.simulator.udp_simulator',
    'openavc.simulator.yaml_auto',
    'openavc.simulator.network_conditions',
    'openavc.simulator.scaffold',
    'openavc.simulator.validate',
    'openavc.simulator._runtime',
    # httptools (C extension, sometimes missed)
    'httptools',
    'httptools.parser',
    'httptools.parser.parser',
    # Serial (pyserial) — list_ports lazily imports a per-OS backend, so bundle
    # every platform's so a frozen build enumerates ports wherever it runs.
    'serial',
    'serial.tools',
    'serial.tools.list_ports',
    'serial.tools.list_ports_windows',
    'serial.tools.list_ports_posix',
    'serial.tools.list_ports_linux',
    'serial.tools.list_ports_osx',
    # croniter
    'croniter',
    # yaml
    'yaml',
    # aiohttp (simulator HTTP device servers)
    'aiohttp',
    'aiohttp.web',
    'aiohttp.web_app',
    'aiohttp.web_runner',
    # defusedxml
    'defusedxml',
    'defusedxml.ElementTree',
    # ifaddr (network interface detection for discovery)
    'ifaddr',
    # psutil (system metrics for cloud heartbeat)
    'psutil',
    # httpx
    'httpx',
    'httpcore',
    'h11',
    'anyio',
    'anyio._backends',
    'anyio._backends._asyncio',
    'sniffio',
    'certifi',
    # gmqtt (MQTT transport) — imported lazily inside the transport, so list
    # every submodule so the frozen build bundles the whole client.
    'gmqtt',
    'gmqtt.client',
    'gmqtt.mqtt',
    'gmqtt.mqtt.connection',
    'gmqtt.mqtt.constants',
    'gmqtt.mqtt.handler',
    'gmqtt.mqtt.package',
    'gmqtt.mqtt.property',
    'gmqtt.mqtt.protocol',
    'gmqtt.mqtt.utils',
    'gmqtt.storage',
]

# Excludes: things we don't need in the bundle
excludes = [
    'tkinter',
    'unittest',
    'test',
    'distutils',
    'setuptools',
    'pip',
    'numpy',
    'scipy',
    'matplotlib',
    'pandas',
    'PIL',  # Pillow not needed at runtime
]

a = Analysis(
    [str(PROJECT_ROOT / 'openavc' / 'main.py')],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='openavc-server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # Console app (service runs headless)
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='openavc',
)
