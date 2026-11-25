#!/bin/bash
# OpenAVC macOS launchd run wrapper.
#
# This script is the LaunchDaemon's ProgramArguments. launchd runs it as root
# on every (re)launch (RunAtLoad + KeepAlive). On each launch it applies any
# pending update or rollback instruction (it runs as root, so it can swap the
# .app under /Applications), then exec's the server. A clean server exit 0
# (self-update / cloud restart) and a crash both bring launchd back here.
#
# An update is applied by the apply library the Linux update-helper.sh uses
# (apply-lib.sh, beside this script in the bundle): the order of the steps,
# the copy kept to roll back to, a rollback winning over an update asked with
# it, and an update set aside to retry. macOS differences: the "app dir" is
# the OpenAVC.app bundle; there is no venv to preserve and no user content
# inside the bundle (data lives under OPENAVC_DATA_DIR), so nothing is carried
# across the swap.
#
# The script MUST always end by exec'ing the server, even when an apply step
# fails, so a bad instruction can never take the service down.

set -u

DATA_DIR="${OPENAVC_DATA_DIR:-/Library/Application Support/OpenAVC}"
APP="${OPENAVC_APP:-/Applications/OpenAVC.app}"
SERVER="$APP/Contents/Resources/server/openavc-server"
UPDATE_FILE="$DATA_DIR/apply-update.json"
ROLLBACK_FILE="$DATA_DIR/apply-rollback"
LOG_TAG="openavc-macos-run"
PYTHON="${PYTHON:-/usr/bin/python3}"
PLISTBUDDY="${PLISTBUDDY:-/usr/libexec/PlistBuddy}"
# The LaunchDaemon's installed launch settings. Only the installed bundle's own
# run reads or changes the real file; a sandbox run (OPENAVC_APP pointed
# elsewhere, as the update tests do) names its own or leaves them alone.
if [ "$APP" = "/Applications/OpenAVC.app" ]; then
    DAEMON_PLIST="${OPENAVC_DAEMON_PLIST:-/Library/LaunchDaemons/com.openavc.server.plist}"
else
    DAEMON_PLIST="${OPENAVC_DAEMON_PLIST:-}"
fi
# The launchctl that restarts the menu bar after a swap. The same rule: a
# sandbox run restarts nothing unless it names its own (the update tests pass a
# stand-in), so running the tests never touches the menu bar of the Mac they
# run on.
if [ "$APP" = "/Applications/OpenAVC.app" ]; then
    LAUNCHCTL="${OPENAVC_LAUNCHCTL:-/bin/launchctl}"
else
    LAUNCHCTL="${OPENAVC_LAUNCHCTL:-}"
fi
MENUBAR_LABEL="com.openavc.menubar"

# launchd always runs this as root. A non-root run against the real installed
# bundle therefore means a person started it by hand, or an old alias did —
# and that would quietly bring up a second, unprivileged server that cannot
# write the system data dir and fights the daemon for the port, with no window
# to show for it. Refuse, and name the supported controls instead.
#
# Scoped to the installed bundle: a run with OPENAVC_APP pointed somewhere else
# is a sandbox (the update tests do exactly this) and is nobody's accident.
if [ "$(id -u)" -ne 0 ] && [ "$APP" = "/Applications/OpenAVC.app" ]; then
    cat >&2 <<'MSG'
This is the OpenAVC server's startup wrapper, not the app. It is run by the
system service as root; starting it by hand does not work.

To control the OpenAVC server, open OpenAVC from Applications and use the menu
bar item, or from Terminal:

  sudo launchctl bootout system/com.openavc.server                             # stop
  sudo launchctl bootstrap system /Library/LaunchDaemons/com.openavc.server.plist   # start
MSG
    exit 1
fi

# The apply library, from this script's own folder: Contents/MacOS in the
# bundle, installer/ in the source tree. A bundle without it applies no update
# and says so; it still rolls back.
case "${BASH_SOURCE[0]}" in
    */*) WRAPPER_DIR="${BASH_SOURCE[0]%/*}" ;;
    *) WRAPPER_DIR=. ;;
esac
APPLY_LIB=""
# shellcheck source=apply-lib.sh
if [ -f "$WRAPPER_DIR/apply-lib.sh" ] && . "$WRAPPER_DIR/apply-lib.sh"; then
    APPLY_LIB="$WRAPPER_DIR/apply-lib.sh"
fi

# The bundle holds nothing the release does not ship, so a swap carries no
# folder across.
# shellcheck disable=SC2034  # read by the apply library
PRESERVE_DIRS=()

# A candidate bundle is usable only if it carries the server executable.
# (Frozen binaries live in Contents/Resources/, not Contents/MacOS/ — see the
# build script for why.)
is_app_valid() {
    [ -x "$1/Contents/Resources/server/openavc-server" ]
}

# What the apply library asks of the staged bundle before anything that runs
# moves: made root's, then carrying the server. The library's extract already
# makes the extracting user the owner, which is root here, but the requirement
# is stated outright rather than inferred from who ran the script: the
# LaunchDaemon runs this bundle's server as root, so a bundle a local account
# could write would hand that account root. A .pkg install writes root:wheel
# itself.
root_and_check_bundle() {
    chown -R root:wheel "$1" 2>/dev/null || true
    is_app_valid "$1"
}

# Verify an update artifact's detached signature against the trusted public
# keys shipped in the CURRENTLY-INSTALLED bundle
# ($APP/Contents/Resources/trusted-keys/*.pem). Mirrors update-helper.sh's
# gate. On macOS both this wrapper and the server run as root, so unlike Linux
# there is no service-user-to-root boundary here — this is supply-chain
# defense-in-depth: the update tarball is fetched over the network and
# extracted/launched by a LaunchDaemon, which Gatekeeper never checks, so the
# .sig is what proves the artifact came from an OpenAVC release.
#
# openssl is the verifier for parity with Linux; macOS ships /usr/bin/openssl
# (LibreSSL), whose `dgst -sha256 -verify` handles EC P-256 the same way.
#
# Trust state (same as Linux):
#   - no trusted-keys dir / no *.pem  -> release signing NOT YET ARMED: warn
#     and allow, so this code ships before the production key exists without
#     bricking updates.
#   - keys present                    -> ENFORCED fail-closed: "${artifact}.sig"
#     must verify against at least one key, else refuse.
verify_artifact_signature() {
    local artifact="$1"
    local keys_dir="$APP/Contents/Resources/trusted-keys"
    local sig="${artifact}.sig"

    local keys=()
    if [ -d "$keys_dir" ]; then
        local k
        for k in "$keys_dir"/*.pem; do
            [ -f "$k" ] && keys+=("$k")
        done
    fi

    if [ "${#keys[@]}" -eq 0 ]; then
        echo "$LOG_TAG: no trusted signing keys in $keys_dir — release signing not yet armed, skipping signature check"
        return 0
    fi

    if ! command -v openssl >/dev/null 2>&1; then
        echo "$LOG_TAG: openssl not found but signing is armed — refusing update (cannot verify integrity)"
        return 1
    fi

    if [ ! -f "$sig" ]; then
        echo "$LOG_TAG: signature $sig missing — refusing unverified update"
        return 1
    fi

    local key
    for key in "${keys[@]}"; do
        if openssl dgst -sha256 -verify "$key" -signature "$sig" "$artifact" >/dev/null 2>&1; then
            echo "$LOG_TAG: signature verified against $(basename "$key")"
            return 0
        fi
    done
    echo "$LOG_TAG: signature did not verify against any trusted key — refusing update"
    return 1
}

# The menu bar (a LaunchAgent in the signed-in user's session) keeps running the
# code it started with: a swap replaces the bundle under it and nothing else
# tells it, so it would run the old version, from deleted files, until the next
# login. After a swap that applied, restart it into the bundle now in place,
# the way the .pkg's postinstall does. Only one that is running: "Quit Menu Bar
# App" stops the process and leaves the job loaded, and that choice stays. No one
# signed in at the screen, nothing to restart.
console_uid() {
    if [ -n "${OPENAVC_CONSOLE_UID+x}" ]; then
        echo "$OPENAVC_CONSOLE_UID"
    else
        stat -f %u /dev/console 2>/dev/null
    fi
}

restart_menu_bar() {
    local uid
    uid=$(console_uid)
    case "$uid" in ''|0|*[!0-9]*) return 0 ;; esac
    "$LAUNCHCTL" print "gui/$uid/$MENUBAR_LABEL" 2>/dev/null |
        grep -Eq '^[[:space:]]state = running$' || return 0
    # kickstart -k replaces the running process in one step, as the app's own
    # launcher (openavc-menubar-launch.sh) does when someone opens the app.
    if "$LAUNCHCTL" kickstart -k "gui/$uid/$MENUBAR_LABEL" >/dev/null 2>&1; then
        echo "$LOG_TAG: restarted the menu bar so it runs the new version"
    else
        echo "$LOG_TAG: could not restart the menu bar; it runs the new version from the next login"
    fi
}

# Nothing here may hold up the server: the restart gets five seconds, then the
# server starts whether it has finished or not.
restart_menu_bar_after_swap() {
    [ -n "$LAUNCHCTL" ] || return 0
    restart_menu_bar &
    local pid=$! waited=0
    while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 50 ]; do
        sleep 0.1
        waited=$((waited + 1))
    done
    kill -0 "$pid" 2>/dev/null || wait "$pid" 2>/dev/null
    return 0
}

handle_update() {
    ARTIFACT=$("$PYTHON" -c "import json,sys; print(json.load(open(sys.argv[1]))['artifact'])" "$UPDATE_FILE" 2>/dev/null)
    TO_VER=$("$PYTHON" -c "import json,sys; print(json.load(open(sys.argv[1]))['to_version'])" "$UPDATE_FILE" 2>/dev/null)

    if [ -z "$ARTIFACT" ] || [ ! -f "$ARTIFACT" ]; then
        echo "$LOG_TAG: update artifact missing or unparseable, skipping"
        rm -f "$UPDATE_FILE"
        return
    fi

    # Integrity gate: verify the artifact's signature before doing ANY work
    # with it (extract/snapshot/swap). Fail-closed once signing is armed.
    if ! verify_artifact_signature "$ARTIFACT"; then
        echo "$LOG_TAG: artifact failed signature verification, skipping update"
        rm -f "$UPDATE_FILE"
        return
    fi

    echo "$LOG_TAG: applying update to v$TO_VER from $ARTIFACT"

    # Unpack the release into $APP.new and check it, keep the running bundle as
    # $APP.previous and swap (apply-lib.sh has the order, what each failure
    # leaves, and whether the instruction is dropped or set aside to retry).
    # The tarball carries OpenAVC.app at its root.
    apply_update "$APP" OpenAVC.app root_and_check_bundle || return

    rm -f "$UPDATE_FILE"
    echo "$LOG_TAG: update to v$TO_VER applied"
    restart_menu_bar_after_swap
}

handle_rollback() {
    PREVIOUS="$APP.previous"
    # Refuse to promote a missing or partial snapshot (an earlier release's
    # wrapper made it with cp -a, which can be interrupted) — that would crash
    # the service with nothing to fall back to. Mirrors update-helper.sh's
    # integrity guard.
    if [ ! -d "$PREVIOUS" ] || ! is_app_valid "$PREVIOUS"; then
        echo "$LOG_TAG: no valid previous bundle at $PREVIOUS, cannot rollback"
        rm -f "$ROLLBACK_FILE"
        return
    fi

    echo "$LOG_TAG: rolling back to previous version"
    FAILED="$APP.failed"
    rm -rf "$FAILED"
    if ! mv "$APP" "$FAILED"; then
        echo "$LOG_TAG: could not move current bundle aside, skipping rollback"
        rm -f "$ROLLBACK_FILE"
        return
    fi
    if ! mv "$PREVIOUS" "$APP"; then
        echo "$LOG_TAG: restore failed, putting the failed bundle back"
        mv "$FAILED" "$APP" 2>/dev/null || true
        rm -f "$ROLLBACK_FILE"
        return
    fi
    rm -rf "$FAILED"
    rm -f "$ROLLBACK_FILE"
    echo "$LOG_TAG: rollback complete"
    restart_menu_bar_after_swap
}

# Who can connect is Settings > Network > Accept connections from, and
# OPENAVC_BIND in the launch settings holds it and locks it there. Every .pkg
# before this one put OPENAVC_BIND=0.0.0.0 in them, and an in-app update never
# replaces the installed file, so that one value is removed from it: the
# setting's own default on an installed system is every network anyway. Any
# other value was set by someone on purpose and stays.
#
# launchd keeps the environment it read from the file when it loaded the job
# until it loads it again (a restart of the Mac, or the .pkg reinstalling
# it), so a change to the file, this one or an administrator's, would not
# reach the server through a restart from Settings or the menu bar. The
# server is started with OPENAVC_BIND as the installed file holds it now.
launch_settings_readable() {
    [ -n "$DAEMON_PLIST" ] && [ -f "$DAEMON_PLIST" ] && [ -x "$PLISTBUDDY" ] &&
        "$PLISTBUDDY" -c "Print :Label" "$DAEMON_PLIST" >/dev/null 2>&1
}

installed_bind() {
    "$PLISTBUDDY" -c "Print :EnvironmentVariables:OPENAVC_BIND" "$DAEMON_PLIST" 2>/dev/null
}

drop_earlier_installer_bind() {
    [ "$(installed_bind)" = "0.0.0.0" ] || return 0
    if "$PLISTBUDDY" -c "Delete :EnvironmentVariables:OPENAVC_BIND" "$DAEMON_PLIST" >/dev/null 2>&1; then
        # launchd refuses a daemon plist that is not root's or that others can
        # write, so the rewritten file gets what the .pkg's postinstall gives it.
        chown root:wheel "$DAEMON_PLIST" 2>/dev/null || true
        chmod 644 "$DAEMON_PLIST" 2>/dev/null || true
        echo "$LOG_TAG: removed OPENAVC_BIND=0.0.0.0, which earlier installers set, from $DAEMON_PLIST"
    else
        echo "$LOG_TAG: could not remove OPENAVC_BIND=0.0.0.0 from $DAEMON_PLIST"
    fi
}

follow_installed_bind() {
    local value
    if value=$(installed_bind); then
        export OPENAVC_BIND="$value"
    else
        unset OPENAVC_BIND
    fi
}

# How long a stop gives the server after launchd's SIGTERM before it kills it.
# launchd's own default is 5 seconds, which cuts the server's stop short, and
# every .pkg before this one named none; an in-app update never replaces the
# installed file, so the bound is added to a file that has none. The wrapper
# that runs right after an update is the one it replaced, so this first runs at
# the server's next start, and launchd reads it the next time it loads the job
# after that (a restart of the Mac, or the .pkg reinstalling it). A value
# someone set stays.
STOP_BOUND_SECONDS=20

add_stop_bound() {
    "$PLISTBUDDY" -c "Print :ExitTimeOut" "$DAEMON_PLIST" >/dev/null 2>&1 && return 0
    if "$PLISTBUDDY" -c "Add :ExitTimeOut integer $STOP_BOUND_SECONDS" "$DAEMON_PLIST" >/dev/null 2>&1; then
        chown root:wheel "$DAEMON_PLIST" 2>/dev/null || true
        chmod 644 "$DAEMON_PLIST" 2>/dev/null || true
        echo "$LOG_TAG: gave the server's stop $STOP_BOUND_SECONDS seconds in $DAEMON_PLIST, from the next time launchd loads it"
    else
        echo "$LOG_TAG: could not add ExitTimeOut to $DAEMON_PLIST"
    fi
}

FIRMWARE_HOST="${OPENAVC_FIRMWARE_HOST:-/etc/openavc-firmware-host.json}"
if [ "${OPENAVC_FIRMWARE_HOST+x}" != x ] && [ ! -e "$FIRMWARE_HOST" ] && [ ! -L "$FIRMWARE_HOST" ]; then
    # Which instruction is acted on, and what becomes of the others, is the
    # apply library's (apply_instructions).
    if [ -n "$APPLY_LIB" ]; then
        apply_instructions handle_update handle_rollback
    else
        [ -f "$UPDATE_FILE" ] &&
            echo "$LOG_TAG: $WRAPPER_DIR/apply-lib.sh is missing, so no update can be applied; reinstall OpenAVC"
        [ -f "$ROLLBACK_FILE" ] && handle_rollback
    fi
    if launch_settings_readable; then
        drop_earlier_installer_bind
        add_stop_bound
        follow_installed_bind
    fi
fi

# Always launch the server. exec so launchd tracks the server as the job's
# process (the wrapper itself is replaced, not left hanging around).
exec "$SERVER"
