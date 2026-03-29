# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for the OpenAVC system tray application.

Build: pyinstaller installer/tray.spec
Output: dist/openavc-tray/openavc-tray.exe
"""

from pathlib import Path

block_cipher = None

PROJECT_ROOT = Path(SPECPATH).parent
INSTALLER_DIR = PROJECT_ROOT / 'installer'

a = Analysis(
    [str(INSTALLER_DIR / 'tray.py')],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        (str(INSTALLER_DIR / 'openavc.ico'), '.'),
    ],
    hiddenimports=[
        'infi.systray',
        'infi.systray.traybar',
        'infi.systray.win32_adapter',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'unittest',
        'test',
        'numpy',
        'scipy',
        'matplotlib',
        'pandas',
        'PIL',
    ],
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
    name='openavc-tray',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # Windowed app (no console window)
    disable_windowed_traceback=False,
    icon=str(INSTALLER_DIR / 'openavc.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='openavc-tray',
)
