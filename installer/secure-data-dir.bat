@echo off
REM Restrict the OpenAVC data folder to the service and administrators.
REM Called by install-service.bat at every install and update, while the
REM service is stopped.
REM
REM Arguments:
REM   %1 = Data directory (e.g., C:\ProgramData\OpenAVC)
REM
REM A folder made under C:\ProgramData takes its permissions from it, and
REM those let every local account read the files and create new ones in the
REM subfolders. Left that way, anyone signed in to the PC can read
REM system.json (the cloud key), the project (device passwords) and the TLS
REM key, and can put a .py file in driver_repo or plugin_repo that the
REM service, running as Local System, loads at its next start.
REM
REM Every account is named by SID, so this works whatever language Windows is
REM installed in: S-1-5-18 is SYSTEM, S-1-5-32-544 Administrators,
REM S-1-5-32-545 Users. icacls's exit codes are not checked: a failure leaves
REM the folder as it was and must never fail an update.

set DATA_DIR=%~1
if "%DATA_DIR%"=="" exit /b 1
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"

REM The folder itself: stop inheriting from C:\ProgramData, full control for
REM SYSTEM and Administrators only. Only an administrator can have added any
REM other entry to it (Users never had permission to change it), so those
REM are left alone.
icacls "%DATA_DIR%" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" >nul

REM Everything inside: back to what it inherits from the folder. A file a
REM local account created under the old permissions can carry entries that
REM account set on it, and its owner can always change its permissions, so
REM those entries go and Administrators take ownership.
icacls "%DATA_DIR%\*" /reset /T /C >nul
icacls "%DATA_DIR%" /setowner "*S-1-5-32-544" /T /C >nul

REM status\ is the one folder every local account can read, and only read:
REM the server writes the ports it listens on and the reason it last failed
REM to start there for the tray app, which runs as the signed-in user.
REM Nothing secret goes in it, and only SYSTEM and Administrators can write
REM to it, so nobody can point the tray at another address.
if not exist "%DATA_DIR%\status" mkdir "%DATA_DIR%\status"
icacls "%DATA_DIR%\status" /grant:r "*S-1-5-32-545:(OI)(CI)RX" >nul

exit /b 0
