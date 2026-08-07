@echo off
REM Install OpenAVC as a Windows service using NSSM.
REM Called by the Inno Setup installer during installation.
REM
REM Arguments:
REM   %1 = Install directory (e.g., C:\Program Files\OpenAVC)
REM   %2 = Data directory (e.g., C:\ProgramData\OpenAVC)

set INSTALL_DIR=%~1
set DATA_DIR=%~2

REM Stop existing service if running
"%INSTALL_DIR%\nssm.exe" stop OpenAVC >nul 2>&1

REM Remove existing service if present
"%INSTALL_DIR%\nssm.exe" remove OpenAVC confirm >nul 2>&1

REM Install the service
"%INSTALL_DIR%\nssm.exe" install OpenAVC "%INSTALL_DIR%\openavc-server.exe"

REM Configure service parameters
"%INSTALL_DIR%\nssm.exe" set OpenAVC DisplayName "OpenAVC Room Control Server"
"%INSTALL_DIR%\nssm.exe" set OpenAVC Description "Open-source AV room control platform"
"%INSTALL_DIR%\nssm.exe" set OpenAVC Start SERVICE_AUTO_START
"%INSTALL_DIR%\nssm.exe" set OpenAVC AppDirectory "%INSTALL_DIR%"

REM Set environment variables for the service.
REM OPENAVC_SERVICE_MANAGED=1 tells main.py that NSSM will relaunch us on
REM exit, so the cloud-restart path can just exit instead of also
REM spawning its own replacement (which would double-start under NSSM).
"%INSTALL_DIR%\nssm.exe" set OpenAVC AppEnvironmentExtra "OPENAVC_DATA_DIR=%DATA_DIR%" "OPENAVC_LOG_DIR=%DATA_DIR%\logs" "OPENAVC_PROJECT=%DATA_DIR%\projects\default\project.avc" "OPENAVC_BIND=0.0.0.0" "OPENAVC_ALLOW_ANONYMOUS=false" "OPENAVC_SERVICE_MANAGED=1"

REM Configure restart on failure
"%INSTALL_DIR%\nssm.exe" set OpenAVC AppExit Default Restart
"%INSTALL_DIR%\nssm.exe" set OpenAVC AppRestartDelay 5000

REM Exit code 42 = update/rollback in progress, don't restart (installer handles it)
"%INSTALL_DIR%\nssm.exe" set OpenAVC AppExit 42 Exit

REM Configure logging (NSSM's own stdout/stderr capture)
"%INSTALL_DIR%\nssm.exe" set OpenAVC AppStdout "%DATA_DIR%\logs\service-stdout.log"
"%INSTALL_DIR%\nssm.exe" set OpenAVC AppStderr "%DATA_DIR%\logs\service-stderr.log"
"%INSTALL_DIR%\nssm.exe" set OpenAVC AppStdoutCreationDisposition 4
"%INSTALL_DIR%\nssm.exe" set OpenAVC AppStderrCreationDisposition 4
"%INSTALL_DIR%\nssm.exe" set OpenAVC AppRotateFiles 1
"%INSTALL_DIR%\nssm.exe" set OpenAVC AppRotateBytes 52428800

REM Create data and log directories
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
if not exist "%DATA_DIR%\logs" mkdir "%DATA_DIR%\logs"
if not exist "%DATA_DIR%\projects\default" mkdir "%DATA_DIR%\projects\default"
if not exist "%DATA_DIR%\backups" mkdir "%DATA_DIR%\backups"

REM Seed default project if not present
if not exist "%DATA_DIR%\projects\default\project.avc" (
    if exist "%INSTALL_DIR%\_internal\projects\default\project.avc" (
        copy "%INSTALL_DIR%\_internal\projects\default\project.avc" "%DATA_DIR%\projects\default\project.avc" >nul
    )
)

REM Start the service
"%INSTALL_DIR%\nssm.exe" start OpenAVC

REM Wait briefly for the server to actually accept connections.
REM
REM The installer opens http://localhost:8080/programmer right after this
REM script returns, and `nssm start` returns once the process is LAUNCHED, not
REM once it is LISTENING. In that window the browser shows its own
REM connection-refused page. The startup splash cannot cover it: the splash is
REM served BY the server, so it needs the socket already bound. On a first run
REM the gap is real (a freshly written bundle gets virus-scanned before it
REM gets far), even though a warm machine usually binds before the user can
REM click Finish.
REM
REM Deliberately FAIL-OPEN: if the port never shows up we fall through and the
REM install completes exactly as it did before. A slow start must never become
REM a failed installation. Same reason the ceiling is bounded rather than
REM waiting forever, and why the port is checked before the first sleep -- the
REM normal case (already listening) costs nothing.
REM
REM `ping` is the delay, not `timeout`: timeout aborts with "input redirection
REM is not supported" when stdin is redirected, which is how the installer
REM invokes this (runhidden).
REM
REM The port is the 8080 default on purpose -- it is what the installer's own
REM shortcuts and the Finish-page browser link use, so it is the only port this
REM wait could be protecting. This script also runs on upgrades, so someone who
REM moved the server to a custom port or bind address never matches and waits
REM out the ceiling before continuing normally. That is why the ceiling is
REM short: overshooting costs a rare upgrade a few seconds, while undershooting
REM just returns the pre-existing behaviour of a browser that may beat the
REM socket.
REM
REM Match on the local address, NOT on the word LISTENING: netstat localises
REM its state column, so "LISTENING" is absent on a German or French Windows
REM and every install there would sit out the full ceiling for nothing.
REM "0.0.0.0:8080" is locale-independent, and it can only be a listening
REM socket -- 0.0.0.0 never appears as a peer address. The service is pinned to
REM that bind address above (OPENAVC_BIND), so this is exactly our own socket.
set OPENAVC_WAIT_TRIES=20
:openavc_wait_loop
netstat -an | findstr /c:"0.0.0.0:8080" >nul 2>&1
if not errorlevel 1 goto openavc_wait_done
set /a OPENAVC_WAIT_TRIES-=1
if %OPENAVC_WAIT_TRIES% LEQ 0 goto openavc_wait_done
ping -n 2 127.0.0.1 >nul 2>&1
goto openavc_wait_loop
:openavc_wait_done

echo OpenAVC service installed and started.
exit /b 0
