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
    (str(PROJECT_ROOT / 'web' / 'panel'), 'web/panel'),
    (str(PROJECT_ROOT / 'web' / 'programmer' / 'dist'), 'web/programmer/dist'),
    (str(PROJECT_ROOT / 'web' / 'simulator' / 'dist'), 'web/simulator/dist'),
    # Simulator package (Python — runs as subprocess)
    (str(PROJECT_ROOT / 'simulator'), 'simulator'),
    # Driver definitions (built-in YAML drivers)
    (str(PROJECT_ROOT / 'server' / 'drivers' / 'definitions'), 'server/drivers/definitions'),
    # Default drivers
    (str(PROJECT_ROOT / 'driver_repo'), 'driver_repo'),
    # Themes
    (str(PROJECT_ROOT / 'themes'), 'themes'),
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
    # Our server modules (some loaded dynamically)
    'server.main',
    'server.config',
    'server.system_config',
    'server.version',
    'server.core.engine',
    'server.core.state_store',
    'server.core.event_bus',
    'server.core.device_manager',
    'server.core.macro_engine',
    'server.core.script_engine',
    'server.core.script_api',
    'server.core.scheduler',
    'server.core.trigger_engine',
    'server.core.isc',
    'server.core.plugin_api',
    'server.core.plugin_loader',
    'server.core.plugin_registry',
    'server.core.plugin_installer',
    'server.core.project_loader',
    'server.core.project_migration',
    'server.core.project_library',
    'server.transport.tcp',
    'server.transport.serial_transport',
    'server.transport.udp',
    'server.transport.http_client',
    'server.transport.frame_parsers',
    'server.transport.binary_helpers',
    'server.drivers.base',
    'server.drivers.configurable',
    'server.drivers.driver_loader',
    'server.drivers.generic_tcp',
    'server.discovery.engine',
    'server.discovery.network_scanner',
    'server.discovery.port_scanner',
    'server.discovery.protocol_prober',
    'server.discovery.snmp_scanner',
    'server.discovery.mdns_scanner',
    'server.discovery.ssdp_scanner',
    'server.discovery.driver_matcher',
    'server.discovery.community_index',
    'server.discovery.hints',
    'server.discovery.oui_database',
    'server.discovery.result',
    'server.updater',
    'server.updater.checker',
    'server.updater.manager',
    'server.updater.backup',
    'server.updater.rollback',
    'server.updater.platform',
    'server.cloud.agent',
    'server.cloud.config',
    'server.cloud.crypto',
    'server.cloud.protocol',
    'server.cloud.handshake',
    'server.cloud.session',
    'server.cloud.sequencer',
    'server.cloud.heartbeat',
    'server.cloud.state_relay',
    'server.cloud.command_handler',
    'server.cloud.alert_monitor',
    'server.cloud.tunnel',
    'server.cloud.ai_tool_handler',
    'server.api.rest',
    'server.api.ws',
    'server.api.isc_ws',
    'server.api.discovery',
    'server.api.plugins',
    'server.api.assets',
    'server.api.themes',
    'server.api.auth',
    'server.api.models',
    'server.utils.logger',
    'server.utils.log_buffer',
    'server.middleware.rate_limit',
    # Simulator package (launched as subprocess via python -m simulator)
    'simulator',
    'simulator.server',
    'simulator.engine',
    'simulator.api',
    'simulator.base',
    'simulator.tcp_simulator',
    'simulator.http_simulator',
    'simulator.yaml_auto',
    'simulator.network_conditions',
    'simulator.scaffold',
    'simulator.validate',
    'simulator._runtime',
    # httptools (C extension, sometimes missed)
    'httptools',
    'httptools.parser',
    'httptools.parser.parser',
    # Serial (pyserial)
    'serial',
    'serial.tools',
    'serial.tools.list_ports',
    'serial.tools.list_ports_windows',
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
    [str(PROJECT_ROOT / 'server' / 'main.py')],
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
