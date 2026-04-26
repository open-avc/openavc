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

REM Set environment variables for the service
"%INSTALL_DIR%\nssm.exe" set OpenAVC AppEnvironmentExtra "OPENAVC_DATA_DIR=%DATA_DIR%" "OPENAVC_LOG_DIR=%DATA_DIR%\logs" "OPENAVC_PROJECT=%DATA_DIR%\projects\default\project.avc" "OPENAVC_BIND=0.0.0.0"

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
if not exist "%DATA_DIR%\drivers" mkdir "%DATA_DIR%\drivers"
if not exist "%DATA_DIR%\backups" mkdir "%DATA_DIR%\backups"

REM Seed default project if not present
if not exist "%DATA_DIR%\projects\default\project.avc" (
    if exist "%INSTALL_DIR%\_internal\projects\default\project.avc" (
        copy "%INSTALL_DIR%\_internal\projects\default\project.avc" "%DATA_DIR%\projects\default\project.avc" >nul
    )
)

REM Start the service
"%INSTALL_DIR%\nssm.exe" start OpenAVC

echo OpenAVC service installed and started.
