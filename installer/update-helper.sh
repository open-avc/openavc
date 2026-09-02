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

# Where an update that failed for a reason a later start could fix is set aside
# (defer_update). It is deliberately NOT apply-update.json: the server deletes
# any of those that survives to its startup as defence in depth, so an update
# kept under that name would be swept away before it could ever be retried.
RETRY_FILE="$DATA_DIR/apply-update.retry"

# How many times a deferred update may be attempted before it is abandoned.
# Bounded on purpose rather than retried forever: every attempt costs a full
# cp -a snapshot, an extract and a rollback, so an update that can never apply
# would otherwise add minutes to every boot for the life of the box.
MAX_UPDATE_ATTEMPTS=3

# Failed attempts already spent on the instruction now in $UPDATE_FILE, read
# back by promote_deferred_update. Zero for a freshly staged one.
RETRY_ATTEMPTS=0

# Directories inside $APP_DIR that hold content the release tarball never ships
# and that must survive every swap. venv is a Python virtual environment built
# at install time. driver_repo and plugin_repo are legacy locations from before
# user-installed content moved to $DATA_DIR — we still preserve them across the
# swap so the runtime migration in openavc/system_config.migrate_legacy_repos can
# drain them into $DATA_DIR on the first start of the new release. scripts holds
# launchers placed by the appliance image build (the kiosk/setup display, the
# first-boot and boot-info helpers); they live here but aren't in the tarball, so
# without preserving them an in-app update would wipe the on-screen display until
# the next re-flash. The `[ -e ]` guard below makes any of these absent (a clean
# generic install that never had them) a no-op.
PRESERVE_DIRS=(venv driver_repo plugin_repo scripts)

# The subset of PRESERVE_DIRS that systemd may bind-mount via ReadWritePaths
# under ProtectSystem=strict (the legacy pre-data_dir repo locations). A bind
# mount turns them into mountpoints, so the mv/rm of the atomic swap below
# fails with EBUSY ("Device or resource busy"). prepare_legacy_repos() detaches
# the mount and drops the dir once it's drained so it never reappears as an
# EBUSY source. venv is never a mountpoint and is always preserved.
LEGACY_REPO_DIRS=(driver_repo plugin_repo)

# Sanity-check that a candidate install directory has the minimum set of
# files needed to actually start the service. Used before promoting
# $APP_DIR.previous on rollback so a partial cp -a (interrupted, disk
# full, OOM kill) cannot be promoted into the live slot and crash the
# service on the next start with no working install to fall back to (A61).
# The venv interpreter is executed, not merely checked for existence: an OS
# Python minor upgrade (apt 3.11 -> 3.12) can leave venv/bin/python3 as a
# dangling symlink that passes a file test but can't run, so a snapshot with
# such a venv must not be treated as a valid rollback target.
#
# The package directory is accepted under EITHER name, and that is deliberate:
# the only caller checks $APP_DIR.previous, which is by definition the install
# we are rolling BACK to, so during the openavc/ rename upgrade it is a
# pre-rename tree carrying server/ rather than openavc/. Requiring the new name
# here made every rollback off the rename release refuse with "appears corrupt"
# -- stranding the box on precisely the version it was trying to escape, and
# killing the automatic rollback that arms when the new version fails to start.
# A snapshot is a snapshot of whatever shipped; it does not adopt the running
# version's layout.
is_app_dir_valid() {
    local dir="$1"
    [ -f "$dir/pyproject.toml" ] && \
    { [ -d "$dir/openavc" ] || [ -d "$dir/server" ]; } && \
    [ -f "$dir/venv/bin/python3" ] && \
    "$dir/venv/bin/python3" -c 'import sys' >/dev/null 2>&1
}

# True if $1 is a mount point. Reads /proc/self/mountinfo (always present under
# systemd) rather than the `mountpoint` binary, which minimal images may lack.
# Field 5 of each mountinfo line is the mount point path.
is_mountpoint() {
    awk -v t="$1" '$5 == t { found = 1 } END { exit !found }' \
        /proc/self/mountinfo 2>/dev/null
}

# Detach any self-bind-mount on the legacy repo dirs and remove them if drained,
# so the atomic swap below can mv/rm $APP_DIR without hitting EBUSY on a
# mountpoint. systemd implements ReadWritePaths by bind-mounting each path onto
# itself inside the unit's mount namespace; renaming or removing a mountpoint
# fails with EBUSY. The umount is namespace-local (root via the unit's `+`
# ExecStartPre) and does not affect the host or the next start — the `-` prefix
# on the ReadWritePaths entries means a missing dir is simply skipped. Safe
# no-op on fresh installs (dirs absent) and on unmounted dirs.
prepare_legacy_repos() {
    local dir sub
    for sub in "${LEGACY_REPO_DIRS[@]}"; do
        dir="$APP_DIR/$sub"
        [ -d "$dir" ] || continue
        if is_mountpoint "$dir"; then
            umount "$dir" 2>/dev/null || umount -l "$dir" 2>/dev/null || \
                echo "$LOG_TAG: could not detach bind mount on $dir"
        fi
        # Empty means a prior migrate_legacy_repos already drained it into
        # $DATA_DIR — remove it for good so it stops recurring as an EBUSY
        # source and the install reaches the no-bind-mount end state. Non-empty
        # dirs are left for the runtime migration to drain and are carried
        # across the swap by the PRESERVE_DIRS loop.
        if [ -d "$dir" ] && [ -z "$(ls -A "$dir" 2>/dev/null)" ]; then
            rmdir "$dir" 2>/dev/null && echo "$LOG_TAG: removed drained legacy $sub"
        fi
    done
}

# Abort an in-progress update and restore the snapshot taken at the top of
# handle_update. Called when the freshly-installed tree can't be made startable
# (the venv interpreter can't be recreated, or the dependency sync fails), so
# the service never comes up live on a broken venv. Both callers hand the
# instruction to defer_update afterwards, so the attempt is retried on the next
# start rather than thrown away; what this function leaves behind is exactly the
# install that was running before the attempt.
recover_from_snapshot() {
    local prev="$APP_DIR.previous"
    if [ ! -d "$prev" ]; then
        echo "$LOG_TAG: no snapshot at $prev to recover from; manual intervention required"
        return 1
    fi
    echo "$LOG_TAG: restoring previous install from $prev (update aborted)"
    rm -rf "$APP_DIR"
    if mv "$prev" "$APP_DIR"; then
        chown -R openavc:openavc "$APP_DIR" 2>/dev/null || true
        harden_privileged_paths
        echo "$LOG_TAG: in-place rollback complete; staying on the previous version"
    else
        echo "$LOG_TAG: in-place rollback FAILED; manual intervention required"
    fi
}

# Set a failed update aside to be retried on the next start, instead of
# consuming the instruction the way a malformed or unverifiable one is consumed.
#
# The two callers fail for reasons that are about the BOX AT THIS MOMENT rather
# than about the release: python3-venv missing, and a dependency sync that could
# not reach the package index. Deleting the instruction there is what turns a
# first boot with no working DNS into a unit that runs its months-old golden
# baseline, healthy, forever — the release was staged, the attempt failed once,
# and nothing ever tried again.
#
# $ARTIFACT and $TO_VER are already parsed and validated by handle_update, and
# every reason string here is a literal, so the printf below cannot produce
# invalid JSON.
defer_update() {
    local reason="$1"
    local attempts=$((RETRY_ATTEMPTS + 1))
    # Whether anything will try again. Recorded rather than left for a reader to
    # work out from the count, so the ceiling stays owned by this script alone
    # and no second copy of it has to exist in the server or the IDE.
    local final=false
    [ "$attempts" -ge "$MAX_UPDATE_ATTEMPTS" ] && final=true

    if printf '{"artifact": "%s", "to_version": "%s", "attempts": %s, "reason": "%s", "final": %s}\n' \
            "$ARTIFACT" "$TO_VER" "$attempts" "$reason" "$final" > "$RETRY_FILE.tmp" 2>/dev/null &&
        mv "$RETRY_FILE.tmp" "$RETRY_FILE"; then
        rm -f "$UPDATE_FILE"
        if [ "$attempts" -ge "$MAX_UPDATE_ATTEMPTS" ]; then
            echo "$LOG_TAG: update to v$TO_VER has now failed $attempts times ($reason); no further attempts"
            echo "$LOG_TAG: fix the cause, then install v$TO_VER again from the Programmer's Updates page"
        else
            echo "$LOG_TAG: update to v$TO_VER deferred after attempt $attempts ($reason); will retry on the next start"
        fi
    else
        # The record is what the server reads to say the box is not running the
        # release somebody installed on it, so losing it is worse than losing
        # the retry: say so plainly rather than failing quietly.
        rm -f "$RETRY_FILE.tmp" "$UPDATE_FILE"
        echo "$LOG_TAG: could not write $RETRY_FILE, so update to v$TO_VER is abandoned with no record ($reason)"
    fi
}

# Put a deferred update back in play for this start, and remember how many
# attempts it has already cost so defer_update can count toward the ceiling.
#
# A freshly staged instruction wins: somebody (or the cloud) asked for a
# specific release just now, and an older deferred one must not resurface
# behind it.
promote_deferred_update() {
    [ -f "$RETRY_FILE" ] || return 0

    if [ -f "$UPDATE_FILE" ]; then
        echo "$LOG_TAG: a newly staged update supersedes the deferred one; dropping $RETRY_FILE"
        rm -f "$RETRY_FILE"
        return 0
    fi

    RETRY_ATTEMPTS=$("$PYTHON" -c \
        "import json,sys; print(int(json.load(open(sys.argv[1])).get('attempts', 0)))" \
        "$RETRY_FILE" 2>/dev/null)
    # An unreadable count must not read as "no attempts yet" and loop forever;
    # treat it as one short of the ceiling so this start is the last try.
    case "$RETRY_ATTEMPTS" in
        ''|*[!0-9]*) RETRY_ATTEMPTS=$((MAX_UPDATE_ATTEMPTS - 1)) ;;
    esac

    # Out of attempts. The record STAYS: it is the only thing that tells anyone
    # this box is still on the version it was trying to leave, and it clears
    # itself the moment a new update is staged (above).
    if [ "$RETRY_ATTEMPTS" -ge "$MAX_UPDATE_ATTEMPTS" ]; then
        echo "$LOG_TAG: an update was abandoned after $RETRY_ATTEMPTS attempts; install it again from the Programmer's Updates page"
        return 0
    fi

    if mv "$RETRY_FILE" "$UPDATE_FILE"; then
        echo "$LOG_TAG: retrying a deferred update (attempt $((RETRY_ATTEMPTS + 1)) of $MAX_UPDATE_ATTEMPTS)"
    else
        echo "$LOG_TAG: could not promote $RETRY_FILE; leaving it for the next start"
    fi
}

# Verify an update artifact's detached signature against the root-owned set of
# trusted public keys shipped in the CURRENTLY-INSTALLED release
# ($APP_DIR/installer/trusted-keys/*.pem). This is the authoritative integrity
# gate for the privilege boundary (H-075): anything running as the openavc
# service user can write apply-update.json pointing at its own tarball and
# trigger a restart, but it cannot forge a signature over that tarball, and it
# cannot replace the root-owned trusted keys — so root never extracts an
# attacker-supplied artifact.
#
# openssl is the verifier because the helper runs before/around the venv swap
# and must not depend on the venv it is replacing; openssl is present on every
# supported Linux and works on 1.1.1 + 3.x.
#
# Trust state:
#   - no trusted-keys dir / no *.pem  -> release signing NOT YET ARMED: warn and
#     allow, so this code can ship before the production key exists without
#     bricking every update. An attacker can't create the root-owned key dir to
#     force this state.
#   - keys present                    -> ENFORCED fail-closed: "${artifact}.sig"
#     must verify against at least one key, else refuse.
verify_artifact_signature() {
    local artifact="$1"
    local keys_dir="$APP_DIR/installer/trusted-keys"
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

# Defense-in-depth for the trust root: keep the root-executed scripts and the
# signing-key store root-owned and unreachable-for-write by the service user, so
# the H-075 gate above never depends on the service user being unable to swap in
# its own key or rewrite the helper root runs. On Linux this is already enforced
# at runtime by the unit's ProtectSystem=strict (/opt/openavc is read-only to
# the service); asserting ownership too covers non-strict deployments and any
# future unit change. Owning a parent directory is enough to rename/replace a
# root-owned child, so $APP_DIR and installer/ are rooted as well — venv/openavc
# and the rest stay openavc-owned.
harden_privileged_paths() {
    local keys_dir="$APP_DIR/installer/trusted-keys"
    [ -d "$keys_dir" ] || return 0
    chown root:root "$APP_DIR" 2>/dev/null || true
    chown -R root:root "$APP_DIR/installer" 2>/dev/null || true
    chown root:root "$APP_DIR/update-helper.sh" "$APP_DIR/firewall-sync.sh" 2>/dev/null || true
    chmod 755 "$APP_DIR" "$APP_DIR/installer" "$keys_dir" 2>/dev/null || true
    chmod 644 "$keys_dir"/*.pem 2>/dev/null || true
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

    # Integrity gate (H-075): verify the artifact's signature before doing ANY
    # work with it (snapshot/extract/swap). Fail-closed once signing is armed.
    if ! verify_artifact_signature "$ARTIFACT"; then
        echo "$LOG_TAG: artifact failed signature verification, skipping update"
        rm -f "$UPDATE_FILE"
        return
    fi

    echo "$LOG_TAG: applying update to v$TO_VER from $ARTIFACT"

    # Detach/clean up the legacy repo bind mounts before any snapshot or swap so
    # the mv/rm steps below don't fail with EBUSY on a mountpoint.
    prepare_legacy_repos

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

    # The swap removed and recreated $APP_DIR, so this shell's working directory
    # (the unit's WorkingDirectory=$APP_DIR) now points at a deleted inode. Move
    # to / so the venv rebuild, chown, and the re-exec below don't emit
    # "getcwd: cannot access parent directories" warnings.
    cd / 2>/dev/null || true

    # 6. Self-update: replace this script with the version from the new release,
    #    and place/refresh the firewall sync helper the same way (its
    #    ExecStartPre line arrives via sync_unit, so an install that predates
    #    it picks up both the file and the unit line from one normal update).
    if [ -f "$APP_DIR/installer/update-helper.sh" ]; then
        cp "$APP_DIR/installer/update-helper.sh" "$APP_DIR/update-helper.sh"
        chmod 755 "$APP_DIR/update-helper.sh"
    fi
    if [ -f "$APP_DIR/installer/firewall-sync.sh" ]; then
        cp "$APP_DIR/installer/firewall-sync.sh" "$APP_DIR/firewall-sync.sh"
        chmod 755 "$APP_DIR/firewall-sync.sh"
    fi

    # 7. Make the venv functional, then sync dependencies against the new
    #    release's requirements.txt.
    #
    #    The migrated venv came from the old release. An OS Python minor bump
    #    (apt moves 3.11 -> 3.12) can leave its interpreter dangling, or
    #    repointed at a minor whose site-packages it lacks — either way the
    #    next start runs a non-functional install. Test the interpreter; if it
    #    can't run, recreate the venv with the current system python3 (needs
    #    python3-venv, shipped by install.sh and the Pi image). A recreated venv
    #    is empty, so the pip sync below repopulates it.
    if [ -d "$APP_DIR/venv" ] && ! "$APP_DIR/venv/bin/python3" -c 'import sys' >/dev/null 2>&1; then
        echo "$LOG_TAG: venv interpreter is non-functional (likely an OS python minor bump) — recreating"
        rm -rf "$APP_DIR/venv"
        if ! "$PYTHON" -m venv "$APP_DIR/venv"; then
            echo "$LOG_TAG: failed to recreate venv (is python3-venv installed?); aborting update"
            recover_from_snapshot
            defer_update "could not rebuild the Python environment"
            return
        fi
    fi

    #    A failed dependency sync must NOT be reported as success: going live on
    #    a venv missing its packages crash-loops the service on ImportError with
    #    no rollback armed. Keep pip's stderr in the journal, check its real exit
    #    status, and on failure abort the update — rolling back in place to the
    #    snapshot so the service comes up on the previous working version.
    if [ -x "$APP_DIR/venv/bin/pip" ] && [ -f "$APP_DIR/requirements.txt" ]; then
        echo "$LOG_TAG: syncing venv dependencies"
        if ! "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet; then
            echo "$LOG_TAG: venv dependency sync failed; aborting update and rolling back in place"
            recover_from_snapshot
            defer_update "could not download the release's dependencies"
            return
        fi
    fi

    # Ensure correct ownership (service runs as openavc user)
    chown -R openavc:openavc "$APP_DIR" 2>/dev/null || true
    # Re-assert root ownership on the trust root + root-executed scripts.
    harden_privileged_paths

    rm -f "$UPDATE_FILE"
    echo "$LOG_TAG: update to v$TO_VER applied successfully"

    # Re-exec the freshly-installed helper (step 6) so any newer post-update
    # logic it carries — notably sync_unit below — runs in THIS update cycle
    # instead of one update later. UPDATE_FILE is already gone, so the re-exec'd
    # helper skips handle_update and proceeds straight to sync_unit. The env
    # guard prevents an exec loop. (A box still on a pre-this-change helper has
    # no re-exec, so its unit sync lands on the next start after the update —
    # the irreducible one-update bootstrap lag.)
    if [ -z "${OPENAVC_HELPER_REEXEC:-}" ] && [ -x "$APP_DIR/update-helper.sh" ]; then
        export OPENAVC_HELPER_REEXEC=1
        echo "$LOG_TAG: re-exec'ing updated helper to finish post-update steps"
        exec "$APP_DIR/update-helper.sh" "$DATA_DIR" "$APP_DIR"
    fi
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
    harden_privileged_paths
    rm -f "$ROLLBACK_FILE"
    echo "$LOG_TAG: rollback complete"
}

# Mirror of install.sh install_service: CAP_NET_RAW is capability bit 13; test
# it in the bounding set from /proc/self/status. Returns success (cap present)
# if it can't read the mask — every normal systemd host has CAP_NET_RAW; this
# only reports absence on a genuinely capability-stripped container.
host_has_cap_net_raw() {
    local capbnd
    capbnd=$(awk '/^CapBnd:/ {print $2}' /proc/self/status 2>/dev/null) || return 0
    [ -n "$capbnd" ] || return 0
    (( (0x$capbnd >> 13) & 1 ))
}

# Keep the active systemd unit in sync with the one shipped in the release.
# The in-app update swaps $APP_DIR but never rewrites
# /etc/systemd/system/openavc.service, so unit changes (hardening, env vars, the
# AmbientCapabilities=CAP_NET_RAW that discovery's ping sweep needs) otherwise
# never deploy via update — only via an install.sh re-run. Idempotent: acts only
# when the effective desired unit differs from the active one, so it's a cheap
# no-op on every normal start and self-heals a stale unit after a reboot.
#
# A daemon-reload during ExecStartPre does NOT re-apply to the already-parsed
# current job, so a changed unit is activated by a deferred one-shot restart.
# The delay is deliberate and must stay comfortably longer than a normal
# engine start: an update is confirmed when the engine finishes starting
# (openavc/main._initialize_engine -> updater.rollback.confirm_startup), and a
# restart landing before that leaves the pending-update marker on disk, so the
# next start counts as a second failed startup and rolls back a perfectly good
# update.
sync_unit() {
    local src="$APP_DIR/installer/openavc.service"
    local dst="/etc/systemd/system/openavc.service"
    [ -f "$src" ] || return 0
    command -v systemctl >/dev/null 2>&1 || return 0

    # Build the effective desired unit: the shipped file, with the cap line
    # neutralized on hosts whose bounding set lacks CAP_NET_RAW. This mirrors
    # install.sh exactly so the comparison below stays stable on such hosts
    # (otherwise the active neutralized unit would forever differ from the
    # shipped one and restart every boot).
    local desired
    desired=$(mktemp 2>/dev/null) || return 0
    if ! cp "$src" "$desired" 2>/dev/null; then
        rm -f "$desired"
        return 0
    fi
    if ! host_has_cap_net_raw; then
        sed -i 's/^AmbientCapabilities=CAP_NET_RAW.*/# AmbientCapabilities=CAP_NET_RAW  (disabled: CAP_NET_RAW unavailable here)/' "$desired"
    fi

    if [ -f "$dst" ] && cmp -s "$desired" "$dst"; then
        rm -f "$desired"
        return 0  # already in sync — nothing to do
    fi

    echo "$LOG_TAG: systemd unit changed — refreshing $dst"
    if ! cp "$desired" "$dst" 2>/dev/null; then
        echo "$LOG_TAG: failed to write $dst (service continues on the old unit)"
        rm -f "$desired"
        return 0
    fi
    rm -f "$desired"
    chmod 644 "$dst" 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true

    # Activate the new unit with a deferred restart (see above). If the
    # timer is lost before it fires (e.g. a reboot first), the next start's
    # sync_unit re-detects the diff and reschedules, so the unit still converges.
    if command -v systemd-run >/dev/null 2>&1; then
        if systemd-run --on-active=90 --timer-property=AccuracySec=1s \
                systemctl restart openavc.service >/dev/null 2>&1; then
            echo "$LOG_TAG: scheduled service restart (~90s) to apply the new unit"
        else
            echo "$LOG_TAG: could not schedule unit-refresh restart; new unit applies on next restart"
        fi
    else
        echo "$LOG_TAG: systemd-run unavailable; new unit applies on next restart"
    fi
}

# Keep the privileged action helper in sync with the one shipped in the release.
#
# It lives in /usr/local/sbin (root-only, outside the server-writable tree) and
# is installed by the Pi appliance image — so before this, the only way a change
# to it reached a running appliance was a reflash. That mattered the moment the
# admin password stopped being stored in cleartext: the old copy reads
# auth.programmer_password out of system.json to run chpasswd, which now yields
# a hash, and it would set the OS account password to that literal string.
#
# Refreshes only when the destination already exists. Its presence is what says
# "this is an appliance with the privileged path unit"; a generic Linux box
# never had one and does not get one from an update. Same idempotent
# compare-then-copy shape as sync_unit, minus the restart — the helper is
# oneshot, so the next request picks up the new file with nothing to reload.
sync_privileged_helper() {
    local src="$APP_DIR/installer/openavc-privileged-helper.sh"
    local dst="/usr/local/sbin/openavc-privileged-helper.sh"
    [ -f "$src" ] || return 0
    [ -f "$dst" ] || return 0
    cmp -s "$src" "$dst" && return 0

    echo "$LOG_TAG: privileged helper changed — refreshing $dst"
    if ! cp "$src" "$dst" 2>/dev/null; then
        echo "$LOG_TAG: failed to write $dst (appliance keeps the previous helper)"
        return 0
    fi
    chown root:root "$dst" 2>/dev/null || true
    chmod 755 "$dst" 2>/dev/null || true
}

# Main — process instructions if present, sync the unit, then always exit 0.
# handle_update may exec the freshly-installed helper; in that case sync_unit
# and sync_privileged_helper run from the re-exec'd process instead
# (UPDATE_FILE already consumed).
# A queued rollback wins over any (possibly stale) update instruction: running
# handle_update first would do a full extract + venv sync only for the rollback
# to immediately revert it. If a rollback is pending, drop the update instruction
# so it can't resurface on a later start, and roll back directly.
if [ -f "$ROLLBACK_FILE" ]; then
    rm -f "$UPDATE_FILE" "$RETRY_FILE"
    handle_rollback
else
    promote_deferred_update
    if [ -f "$UPDATE_FILE" ]; then
        handle_update
    fi
fi
sync_unit
sync_privileged_helper
exit 0
