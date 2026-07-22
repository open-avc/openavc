#!/bin/bash
# OpenAVC macOS launchd run wrapper.
#
# This script is the LaunchDaemon's ProgramArguments. launchd runs it as root
# on every (re)launch (RunAtLoad + KeepAlive). On each launch it applies any
# pending update or rollback instruction (it runs as root, so it can swap the
# .app under /Applications), then exec's the server. A clean server exit 0
# (self-update / cloud restart) and a crash both bring launchd back here.
#
# It mirrors the Linux update-helper.sh swap/recovery logic. macOS differences:
# the "app dir" is the OpenAVC.app bundle; there is no venv to preserve and no
# user content inside the bundle (data lives under OPENAVC_DATA_DIR), so the
# swap is a plain bundle replace.
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

# A candidate bundle is usable only if it carries the server executable.
# (Frozen binaries live in Contents/Resources/, not Contents/MacOS/ — see the
# build script for why.)
is_app_valid() {
    [ -x "$1/Contents/Resources/server/openavc-server" ]
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
    PREVIOUS="$APP.previous"
    STAGING="$APP.new"

    # 1. Extract the new release into a clean staging dir (no stragglers from
    #    the running install). The tarball carries OpenAVC.app at its root.
    rm -rf "$STAGING"
    if ! mkdir -p "$STAGING"; then
        echo "$LOG_TAG: could not create staging dir, skipping"
        rm -f "$UPDATE_FILE"
        return
    fi
    if ! tar xzf "$ARTIFACT" -C "$STAGING"; then
        echo "$LOG_TAG: extraction failed, leaving current install untouched"
        rm -rf "$STAGING"
        rm -f "$UPDATE_FILE"
        return
    fi
    NEW_APP="$STAGING/OpenAVC.app"
    if ! is_app_valid "$NEW_APP"; then
        echo "$LOG_TAG: staged bundle is invalid, leaving current install untouched"
        rm -rf "$STAGING"
        rm -f "$UPDATE_FILE"
        return
    fi

    # 2. Snapshot the current bundle so rollback has a complete copy.
    rm -rf "$PREVIOUS"
    if ! cp -a "$APP" "$PREVIOUS"; then
        echo "$LOG_TAG: snapshot to $PREVIOUS failed, skipping update"
        rm -rf "$PREVIOUS" "$STAGING"
        rm -f "$UPDATE_FILE"
        return
    fi

    # 3. Swap: remove the old bundle (already snapshotted) and promote staging.
    if ! rm -rf "$APP"; then
        echo "$LOG_TAG: could not remove old bundle, recovering from snapshot"
        mv "$PREVIOUS" "$APP" 2>/dev/null || echo "$LOG_TAG: recovery failed; manual fix required"
        rm -rf "$STAGING"
        rm -f "$UPDATE_FILE"
        return
    fi
    if ! mv "$NEW_APP" "$APP"; then
        echo "$LOG_TAG: could not promote new bundle, recovering from snapshot"
        mv "$PREVIOUS" "$APP" 2>/dev/null || echo "$LOG_TAG: recovery failed; manual fix required"
        rm -rf "$STAGING"
        rm -f "$UPDATE_FILE"
        return
    fi

    rm -rf "$STAGING"
    rm -f "$UPDATE_FILE"
    echo "$LOG_TAG: update to v$TO_VER applied"
}

handle_rollback() {
    PREVIOUS="$APP.previous"
    # Refuse to promote a missing or partial snapshot (a prior cp -a could have
    # been interrupted) — that would crash the service with nothing to fall
    # back to. Mirrors update-helper.sh's integrity guard.
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
}

[ -f "$UPDATE_FILE" ] && handle_update
[ -f "$ROLLBACK_FILE" ] && handle_rollback

# Always launch the server. exec so launchd tracks the server as the job's
# process (the wrapper itself is replaced, not left hanging around).
exec "$SERVER"
