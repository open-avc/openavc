@echo off
REM OpenAVC Windows Installer Build Script
REM
REM Prerequisites:
REM   - Python 3.12+ with pip
REM   - Node.js 20+ with npm
REM   - Inno Setup 6 (https://jrsoftware.org/isdl.php)
REM
REM This script:
REM   1. Builds the Programmer UI frontend
REM   2. Installs Python build dependencies
REM   3. Bundles the server with PyInstaller
REM   4. Bundles the tray app with PyInstaller
REM   5. Compiles the installer with Inno Setup
REM
REM Output: dist\OpenAVC-Setup-{version}.exe

echo ============================================================
echo  OpenAVC Windows Installer Build
echo ============================================================
echo.

cd /d "%~dp0.."

REM Step 1: Build frontends
echo [1/5] Building Programmer UI...
cd web\programmer
call npm ci
if errorlevel 1 (echo FAILED: npm ci & exit /b 1)
call npm run build
if errorlevel 1 (echo FAILED: npm run build & exit /b 1)
cd ..\..
echo       Done.
echo.
echo       Building Simulator UI...
cd web\simulator
call npm ci
if errorlevel 1 (echo FAILED: npm ci & exit /b 1)
call npm run build
if errorlevel 1 (echo FAILED: npm run build & exit /b 1)
cd ..\..
echo       Done.
echo.

REM Step 2: Install build dependencies
echo [2/5] Installing build dependencies...
pip install pyinstaller infi.systray --quiet
if errorlevel 1 (echo FAILED: pip install & exit /b 1)
echo       Done.
echo.

REM Step 3: Bundle server
echo [3/5] Bundling server with PyInstaller...
pyinstaller installer\openavc.spec --noconfirm --clean
if errorlevel 1 (echo FAILED: pyinstaller server & exit /b 1)
echo       Done.
echo.

REM Step 4: Bundle tray app
echo [4/5] Bundling tray app with PyInstaller...
pyinstaller installer\tray.spec --noconfirm --clean
if errorlevel 1 (echo FAILED: pyinstaller tray & exit /b 1)
echo       Done.
echo.

REM Step 5: Compile installer
echo [5/5] Compiling installer with Inno Setup...
set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="C:\Program Files\Inno Setup 6\ISCC.exe"
if not exist %ISCC% (
    echo ERROR: Inno Setup 6 not found. Install from https://jrsoftware.org/isdl.php
    exit /b 1
)
%ISCC% installer\setup.iss
if errorlevel 1 (echo FAILED: Inno Setup & exit /b 1)
echo       Done.
echo.

echo ============================================================
echo  Build complete!
echo  Installer: dist\OpenAVC-Setup-0.4.1.exe
echo ============================================================
