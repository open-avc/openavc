@echo off
REM Start the OpenAVC service and wait briefly for it to accept connections.
REM Called by the Inno Setup installer after install-service.bat, once the
REM installer has put back the environment an administrator set on the
REM previous service, so the server starts with it.
REM
REM Arguments:
REM   %1 = Install directory (e.g., C:\Program Files\OpenAVC)

set INSTALL_DIR=%~1

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
REM wait could be protecting. Whoever Settings > Network > Accept connections
REM from lets in, the server listens on it either on every address
REM (0.0.0.0:8080) or on this computer's own address (127.0.0.1:8080) beside
REM the adapter it was given, so one of the two always shows up. An
REM administrator who moved the server to another port never matches and
REM waits out the ceiling before continuing normally. That is why the ceiling
REM is short: overshooting costs such an update a few seconds, while
REM undershooting just returns the pre-existing behaviour of a browser that
REM may beat the socket.
REM
REM Match on the addresses, NOT on the word LISTENING: netstat localises its
REM state column, so "LISTENING" is absent on a German or French Windows and
REM every install there would sit out the full ceiling for nothing. Both
REM patterns are locale-independent. "0.0.0.0:8080" can only be a listening
REM socket -- 0.0.0.0 never appears as a peer address. "127.0.0.1:8080" can be
REM one end of a connection: everything the tray or a browser opened to the
REM previous server leaves such rows behind for minutes after it stops. So it
REM counts only with 0.0.0.0:0 in the next column, which is how netstat shows
REM a listening socket's peer. test_installer_keeps_admin_settings.py runs
REM this line against netstat rows of each kind.
set OPENAVC_WAIT_TRIES=20
:openavc_wait_loop
netstat -an | findstr /r /c:" 0\.0\.0\.0:8080 " /c:" 127\.0\.0\.1:8080  *0\.0\.0\.0:0 " >nul 2>&1
if not errorlevel 1 goto openavc_wait_done
set /a OPENAVC_WAIT_TRIES-=1
if %OPENAVC_WAIT_TRIES% LEQ 0 goto openavc_wait_done
ping -n 2 127.0.0.1 >nul 2>&1
goto openavc_wait_loop
:openavc_wait_done

echo OpenAVC service started.
exit /b 0
