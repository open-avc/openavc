@echo off
REM Stop and remove the OpenAVC Windows service.
REM Called by the Inno Setup uninstaller.
REM
REM Arguments:
REM   %1 = Install directory (e.g., C:\Program Files\OpenAVC)

set INSTALL_DIR=%~1

REM Stop the service
"%INSTALL_DIR%\nssm.exe" stop OpenAVC >nul 2>&1

REM Wait for service to fully stop
timeout /t 3 /nobreak >nul 2>&1

REM Remove the service
"%INSTALL_DIR%\nssm.exe" remove OpenAVC confirm >nul 2>&1

echo OpenAVC service removed.
