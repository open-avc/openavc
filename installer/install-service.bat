@echo off
REM Install OpenAVC as a Windows service using NSSM.
REM Called by the Inno Setup installer at every install and update. The
REM installer then puts back the variables an administrator set on the
REM previous service (KeepAdministratorEnvironment in setup.iss) and runs
REM start-service.bat.
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
REM
REM The data, log and project paths and OPENAVC_SERVICE_MANAGED are the
REM installer's and are set to these values at every update.
REM OPENAVC_ALLOW_ANONYMOUS is a default: a value an administrator set on the
REM previous service replaces it, and any other variable they added is kept
REM (setup.iss). IsInstallerVariable in setup.iss names the installer's four;
REM keep the two in step.
REM
REM No OPENAVC_BIND: who can connect is Settings > Network > Accept
REM connections from, which is every network on an installed system until
REM someone changes it, and a value here would lock that setting. The
REM installer drops the 0.0.0.0 that earlier installers set here.
"%INSTALL_DIR%\nssm.exe" set OpenAVC AppEnvironmentExtra "OPENAVC_DATA_DIR=%DATA_DIR%" "OPENAVC_LOG_DIR=%DATA_DIR%\logs" "OPENAVC_PROJECT=%DATA_DIR%\projects\default\project.avc" "OPENAVC_ALLOW_ANONYMOUS=false" "OPENAVC_SERVICE_MANAGED=1"

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

REM Restrict the data folder to the service and administrators, at every
REM install and update, so an update also fixes a system installed before
REM this step existed. Done while the service is stopped.
call "%INSTALL_DIR%\secure-data-dir.bat" "%DATA_DIR%"

echo OpenAVC service installed.
exit /b 0
