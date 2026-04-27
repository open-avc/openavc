#!/bin/bash
# OpenAVC Update Helper
#
# Runs before the openavc service starts via ExecStartPre=-+
# Checks for pending update or rollback instructions and applies them.
#
# - The "+" prefix runs this script as root, bypassing ProtectSystem=strict
# - The "-" prefix ensures non-zero exit doesn't block service startup
# - Environment= variables are NOT available to ExecStartPre (systemd #2545),
#   so the data directory is passed as a command-line argument
#
# MUST exit 0 always. Non-zero exit from ExecStartPre permanently stops the
# service (Restart=always does NOT retry ExecStartPre failures).

DATA_DIR="${1:-/var/lib/openavc}"
APP_DIR="${2:-/opt/openavc}"
UPDATE_FILE="$DATA_DIR/apply-update.json"
ROLLBACK_FILE="$DATA_DIR/apply-rollback"
LOG_TAG="update-helper"
PYTHON="${PYTHON:-/usr/bin/python3}"

handle_update() {
    # Parse instruction file using system Python (not venv — venv may change during update)
    ARTIFACT=$("$PYTHON" -c "import json,sys; print(json.load(open(sys.argv[1]))['artifact'])" "$UPDATE_FILE" 2>/dev/null)
    TO_VER=$("$PYTHON" -c "import json,sys; print(json.load(open(sys.argv[1]))['to_version'])" "$UPDATE_FILE" 2>/dev/null)

    # If python3 isn't available or JSON is malformed, skip the update
    if [ -z "$ARTIFACT" ]; then
        echo "$LOG_TAG: failed to parse $UPDATE_FILE (python3 missing or JSON malformed), skipping"
        rm -f "$UPDATE_FILE"
        return
    fi

    if [ ! -f "$ARTIFACT" ]; then
        echo "$LOG_TAG: artifact not found: $ARTIFACT, skipping"
        rm -f "$UPDATE_FILE"
        return
    fi

    echo "$LOG_TAG: applying update to v$TO_VER from $ARTIFACT"

    # Back up current install to .previous
    PREVIOUS="$APP_DIR.previous"
    rm -rf "$PREVIOUS"
    cp -a "$APP_DIR" "$PREVIOUS"
    if [ $? -ne 0 ]; then
        echo "$LOG_TAG: failed to back up $APP_DIR, skipping update"
        rm -f "$UPDATE_FILE"
        return
    fi

    # Extract new version over current install (archive has no wrapper directory)
    tar xzf "$ARTIFACT" -C "$APP_DIR"
    if [ $? -ne 0 ]; then
        echo "$LOG_TAG: extraction failed, restoring from backup"
        rm -rf "$APP_DIR"
        mv "$PREVIOUS" "$APP_DIR"
        rm -f "$UPDATE_FILE"
        return
    fi

    # Self-update: replace this script with the version from the new release
    if [ -f "$APP_DIR/installer/update-helper.sh" ]; then
        cp "$APP_DIR/installer/update-helper.sh" "$APP_DIR/update-helper.sh"
        chmod 755 "$APP_DIR/update-helper.sh"
    fi

    # Rebuild venv dependencies if pip and requirements.txt exist
    if [ -x "$APP_DIR/venv/bin/pip" ] && [ -f "$APP_DIR/requirements.txt" ]; then
        echo "$LOG_TAG: rebuilding venv dependencies"
        "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet 2>/dev/null || true
    fi

    # Ensure correct ownership (service runs as openavc user)
    chown -R openavc:openavc "$APP_DIR" 2>/dev/null || true

    rm -f "$UPDATE_FILE"
    echo "$LOG_TAG: update to v$TO_VER applied successfully"
}

handle_rollback() {
    PREVIOUS="$APP_DIR.previous"
    if [ ! -d "$PREVIOUS" ]; then
        echo "$LOG_TAG: no previous version at $PREVIOUS, cannot rollback"
        rm -f "$ROLLBACK_FILE"
        return
    fi

    echo "$LOG_TAG: rolling back to previous version"

    FAILED="$APP_DIR.failed"
    rm -rf "$FAILED"
    mv "$APP_DIR" "$FAILED"
    if [ $? -ne 0 ]; then
        echo "$LOG_TAG: failed to move current install aside, skipping rollback"
        rm -f "$ROLLBACK_FILE"
        return
    fi

    mv "$PREVIOUS" "$APP_DIR"
    if [ $? -ne 0 ]; then
        echo "$LOG_TAG: failed to restore previous version, attempting recovery"
        # Try to put the failed version back so we have something
        mv "$FAILED" "$APP_DIR" 2>/dev/null || true
        rm -f "$ROLLBACK_FILE"
        return
    fi

    rm -rf "$FAILED"
    chown -R openavc:openavc "$APP_DIR" 2>/dev/null || true
    rm -f "$ROLLBACK_FILE"
    echo "$LOG_TAG: rollback complete"
}

# Main — process instructions if present, then always exit 0
if [ -f "$UPDATE_FILE" ]; then
    handle_update
fi
if [ -f "$ROLLBACK_FILE" ]; then
    handle_rollback
fi
exit 0
