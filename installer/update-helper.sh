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

# Directories inside $APP_DIR that hold runtime/user-installed content and
# must survive every swap (the release tarball never ships these). venv is
# a Python virtual environment built at install time. driver_repo and
# plugin_repo are legacy locations from before user-installed content moved
# to $DATA_DIR — we still preserve them across the swap so the runtime
# migration in server/system_config.migrate_legacy_repos can drain them
# into $DATA_DIR on the first start of the new release. The `[ -e ]`
# guard below makes their absence a no-op for clean post-migration installs.
PRESERVE_DIRS=(venv driver_repo plugin_repo)

# Sanity-check that a candidate install directory has the minimum set of
# files needed to actually start the service. Used before promoting
# $APP_DIR.previous on rollback so a partial cp -a (interrupted, disk
# full, OOM kill) cannot be promoted into the live slot and crash the
# service on the next start with no working install to fall back to (A61).
is_app_dir_valid() {
    local dir="$1"
    [ -f "$dir/pyproject.toml" ] && \
    [ -d "$dir/server" ] && \
    [ -f "$dir/venv/bin/python3" ]
}

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

    PREVIOUS="$APP_DIR.previous"
    STAGING="$APP_DIR.new"

    # Atomic-swap-extract (A62): extract the new release into a fresh
    # staging directory, migrate the preserved dirs across, then swap.
    # The old approach (tar xzf -C $APP_DIR overlay) left removed-in-
    # release files behind — driver_loader.py would scan and try to
    # load YAMLs that had been deleted upstream.

    # 1. Snapshot the current install so rollback has a complete copy
    #    (including venv/driver_repo/plugin_repo, since the migration
    #    in step 4 moves those out of $APP_DIR).
    rm -rf "$PREVIOUS"
    if ! cp -a "$APP_DIR" "$PREVIOUS"; then
        echo "$LOG_TAG: failed to snapshot $APP_DIR to $PREVIOUS, skipping update"
        rm -rf "$PREVIOUS"
        rm -f "$UPDATE_FILE"
        return
    fi

    # 2. Clean staging directory.
    rm -rf "$STAGING"
    if ! mkdir -p "$STAGING"; then
        echo "$LOG_TAG: failed to create $STAGING, skipping update"
        rm -rf "$PREVIOUS"
        rm -f "$UPDATE_FILE"
        return
    fi

    # 3. Extract tarball into staging (clean, no stragglers).
    if ! tar xzf "$ARTIFACT" -C "$STAGING"; then
        echo "$LOG_TAG: extraction failed, leaving current install untouched"
        rm -rf "$STAGING"
        rm -rf "$PREVIOUS"
        rm -f "$UPDATE_FILE"
        return
    fi

    # 4. Migrate venv/driver_repo/plugin_repo from $APP_DIR into staging.
    #    We move (not copy) — the snapshot in $PREVIOUS already has its
    #    own copies. Any stub the tarball might have shipped under these
    #    names is removed first.
    migrate_failed=0
    for sub in "${PRESERVE_DIRS[@]}"; do
        if [ -e "$APP_DIR/$sub" ]; then
            rm -rf "$STAGING/$sub"
            if ! mv "$APP_DIR/$sub" "$STAGING/$sub"; then
                echo "$LOG_TAG: failed to migrate $sub into staging"
                migrate_failed=1
                break
            fi
        fi
    done
    if [ "$migrate_failed" -eq 1 ]; then
        # Move anything we already migrated back into $APP_DIR so the
        # current install is still functional.
        for sub in "${PRESERVE_DIRS[@]}"; do
            if [ -e "$STAGING/$sub" ] && [ ! -e "$APP_DIR/$sub" ]; then
                mv "$STAGING/$sub" "$APP_DIR/$sub" 2>/dev/null || true
            fi
        done
        rm -rf "$STAGING"
        rm -rf "$PREVIOUS"
        rm -f "$UPDATE_FILE"
        return
    fi

    # 5. Swap: discard the old install (already snapshotted to .previous)
    #    and promote staging into place.
    if ! rm -rf "$APP_DIR"; then
        echo "$LOG_TAG: failed to remove old install, attempting recovery from snapshot"
        rm -rf "$APP_DIR"  # best effort
        if ! mv "$PREVIOUS" "$APP_DIR"; then
            echo "$LOG_TAG: recovery from snapshot failed; manual intervention required"
        fi
        rm -rf "$STAGING"
        rm -f "$UPDATE_FILE"
        return
    fi
    if ! mv "$STAGING" "$APP_DIR"; then
        echo "$LOG_TAG: failed to promote new install, recovering from snapshot"
        if ! mv "$PREVIOUS" "$APP_DIR"; then
            echo "$LOG_TAG: recovery from snapshot also failed; manual intervention required"
        fi
        rm -rf "$STAGING"
        rm -f "$UPDATE_FILE"
        return
    fi

    # 6. Self-update: replace this script with the version from the new release
    if [ -f "$APP_DIR/installer/update-helper.sh" ]; then
        cp "$APP_DIR/installer/update-helper.sh" "$APP_DIR/update-helper.sh"
        chmod 755 "$APP_DIR/update-helper.sh"
    fi

    # 7. Rebuild venv dependencies if pip and requirements.txt exist.
    #    The venv we migrated came from the old release; pip install
    #    syncs it against the new release's requirements.txt.
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

    # Integrity check (A61): a partial cp -a (interrupted, disk full, OOM
    # kill during a prior update) can leave $PREVIOUS as a directory that
    # exists but lacks key files. Promoting it would crash the service on
    # the next start and leave no working install at all.
    if ! is_app_dir_valid "$PREVIOUS"; then
        echo "$LOG_TAG: $PREVIOUS appears corrupt (missing key files), refusing rollback"
        rm -f "$ROLLBACK_FILE"
        return
    fi

    echo "$LOG_TAG: rolling back to previous version"

    FAILED="$APP_DIR.failed"
    rm -rf "$FAILED"
    if ! mv "$APP_DIR" "$FAILED"; then
        echo "$LOG_TAG: failed to move current install aside, skipping rollback"
        rm -f "$ROLLBACK_FILE"
        return
    fi

    if ! mv "$PREVIOUS" "$APP_DIR"; then
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
