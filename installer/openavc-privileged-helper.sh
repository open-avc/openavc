#!/bin/bash
# OpenAVC privileged action helper. Runs as ROOT via openavc-privileged.service,
# triggered by openavc-privileged.path when the unprivileged server drops a
# request file. Hard-coded to the 'openavc' user and a fixed action vocabulary;
# it never executes content from a request file.
#
# PROTOCOL: set_password-in-request
#
# That marker is read by the server (openavc/host_control.py) to tell this
# helper from the one that shipped before it, which read the password out of
# system.json instead. Do not reword it. The server refuses to send a
# password-carrying request to a helper that does not declare it -- an older
# copy would read the stored value, which is a hash now, and set the OS account
# password to the literal hash. This file is installed by the Pi image AND
# refreshed by installer/update-helper.sh, so an appliance flashed before the
# change picks the new one up on the start that applies the update.
#
# The password arriving in the request grants no privilege the server did not
# already have: it is the server that set it, one moment earlier. It is read
# from the file rather than passed as an argument so it never appears in the
# process list, and the request is deleted below whatever the outcome.
set -u

DATA_DIR="${1:-/var/lib/openavc}"
REQ_DIR="$DATA_DIR/priv-requests"
RES_DIR="$DATA_DIR/priv-results"
OPENAVC_USER="openavc"
PYTHON="${PYTHON:-/usr/bin/python3}"
LOG_TAG="openavc-privileged"

mkdir -p "$RES_DIR"
chown "$OPENAVC_USER:$OPENAVC_USER" "$RES_DIR" 2>/dev/null || true

reboot_after=0
shopt -s nullglob
for req in "$REQ_DIR"/*.json; do
    id="$(basename "$req" .json)"
    action="$("$PYTHON" -c "import json,sys; print(json.load(open(sys.argv[1])).get('action',''))" "$req" 2>/dev/null)"
    want_result="$("$PYTHON" -c "import json,sys; print(bool(json.load(open(sys.argv[1])).get('want_result', False)))" "$req" 2>/dev/null)"
    ok=false
    error=""

    case "$action" in
        set_password)
            pw="$("$PYTHON" -c "import json,sys; sys.stdout.write(json.load(open(sys.argv[1])).get('password',''))" "$req" 2>/dev/null)"
            if [ -n "$pw" ]; then
                if printf '%s:%s\n' "$OPENAVC_USER" "$pw" | chpasswd; then
                    ok=true
                    echo "$LOG_TAG: synced OS password for $OPENAVC_USER"
                else
                    error="chpasswd failed"
                fi
            else
                # No web password (unclaimed / cleared) -> keep the account
                # locked so it never has a usable password.
                if passwd -l "$OPENAVC_USER" >/dev/null 2>&1; then
                    ok=true
                    echo "$LOG_TAG: no web password set; locked $OPENAVC_USER"
                else
                    error="lock failed"
                fi
            fi
            ;;
        set_ssh)
            enabled="$("$PYTHON" -c "import json,sys; print(bool(json.load(open(sys.argv[1])).get('enabled', False)))" "$req" 2>/dev/null)"
            if [ "$enabled" = "True" ]; then
                if systemctl enable --now ssh >/dev/null 2>&1; then ok=true; else error="ssh enable failed"; fi
            else
                systemctl disable --now ssh >/dev/null 2>&1
                systemctl disable --now ssh.socket >/dev/null 2>&1
                ok=true
            fi
            ;;
        reboot)
            ok=true
            reboot_after=1
            ;;
        *)
            error="unknown action"
            ;;
    esac

    # Result file (only when the server is waiting on one). error strings are a
    # fixed ASCII set with no quotes/backslashes, so this raw JSON is safe.
    if [ "$want_result" = "True" ]; then
        if [ "$ok" = "true" ]; then
            printf '{"ok": true, "error": ""}\n' > "$RES_DIR/$id.json"
        else
            printf '{"ok": false, "error": "%s"}\n' "$error" > "$RES_DIR/$id.json"
        fi
        chown "$OPENAVC_USER:$OPENAVC_USER" "$RES_DIR/$id.json" 2>/dev/null || true
    fi
    rm -f "$req"
    [ -n "$error" ] && echo "$LOG_TAG: action=$action id=$id error=$error"
done

if [ "$reboot_after" -eq 1 ]; then
    ( sleep 2; /sbin/reboot ) &
fi
exit 0
