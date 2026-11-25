# shellcheck shell=bash
# OpenAVC apply library: how an update is applied to an installed copy.
#
# The one place that decides the order of an update's steps and what a failure
# at each one leaves behind:
#
#   1. The release is unpacked into a fresh staging directory beside the
#      install (<install>.new) and checked there. Nothing that runs moves
#      until it passes, so a damaged archive, or one that is not a release
#      this install can run, leaves the install and the copy an earlier update
#      kept to roll back to (<install>.previous) exactly as they were. A fresh
#      directory, not the install itself: unpacking over the install would
#      leave behind the files a release removed, for the driver loader and
#      Python's imports to find.
#   2. Only then is that older copy removed, and the folders the release never
#      ships (the caller's PRESERVE_DIRS) are copied into the staged release.
#      The older copy goes before the copy is made, so an update needs no more
#      room than the install, one more copy of it and the release; a failure
#      here leaves the install running, without the older copy.
#   3. The swap is two renames: the install that was running becomes
#      <install>.previous, whole, and the staged release takes its place. If
#      the second rename fails the first is undone.
#
# After a swap the install that was running is the copy kept to roll back to.
# Steps the caller runs after it (the Linux helper's Python environment sync)
# move that copy back into place themselves when they fail.
#
# It also decides what happens to the instructions the server leaves for the
# start (apply_instructions): a rollback asked for wins over an update, and an
# update that failed for a reason a later start could fix is set aside and
# tried again (defer_update), up to MAX_UPDATE_ATTEMPTS. A release that is at
# fault (it does not unpack, or is not a release this install can run) is
# dropped; one that was good when this install could not take it (no room
# for the staging folder, the older copy or the install could not be moved)
# is set aside.
#
# Sourced by update-helper.sh (Linux, the Raspberry Pi, appliance hardware),
# from the installer/ folder of the install it belongs to, and by the macOS
# wrapper, openavc-macos-run.sh, from beside it in the bundle. It keeps to
# bash 3.2 under set -u and to what the BSD and GNU tar, cp and mv share,
# which is what macOS has. The caller sets DATA_DIR before sourcing it, and
# UPDATE_FILE, ROLLBACK_FILE, PYTHON and LOG_TAG before calling it. Messages
# go to stdout, prefixed with $LOG_TAG.

# Where an update set aside to try again is kept. Not apply-update.json: the
# server deletes any of those that survives to its startup, so an update kept
# under that name would be swept away before it could ever be retried.
RETRY_FILE="$DATA_DIR/apply-update.retry"

# How many times an update set aside may be attempted before it is abandoned.
# Bounded: every attempt costs an extract and a swap, and on Linux a
# dependency sync, so an update that can never apply would otherwise add
# minutes to every start for the life of the box.
MAX_UPDATE_ATTEMPTS=3

# Failed attempts already spent on the instruction now in $UPDATE_FILE, read
# back by promote_deferred_update. Zero for a freshly staged one.
RETRY_ATTEMPTS=0

# What apply_release returns when the release was good and this install could
# not take it now, with the reason, a literal, in APPLY_FAILURE.
APPLY_NOT_NOW=2
APPLY_FAILURE=""

# Unpack ARTIFACT into a fresh, empty STAGING directory. Root extracting a tar
# restores the owners recorded in it, which are accounts on the machine that
# built the release, so the files are made the extracting user's instead.
# On failure nothing is left behind; a staging directory that cannot be made
# returns APPLY_NOT_NOW.
apply_unpack() {
    local artifact="$1" staging="$2"
    rm -rf "$staging"
    if ! mkdir -p "$staging"; then
        echo "$LOG_TAG: could not create $staging; the update was not applied"
        APPLY_FAILURE="could not create the folder to unpack the release into"
        return "$APPLY_NOT_NOW"
    fi
    if ! tar xzf "$artifact" -C "$staging" --no-same-owner; then
        echo "$LOG_TAG: $artifact could not be unpacked; the update was not applied"
        rm -rf "$staging"
        return 1
    fi
}

# Copy each of the caller's PRESERVE_DIRS that the install at LIVE has into
# the staged release at STAGED, in place of anything the release shipped under
# that name. A copy, not a move, so LIVE stays whole until the swap. The guard
# on the expansion is bash 3.2's: an empty array is unbound under set -u.
apply_carry_across() {
    local live="$1" staged="$2" sub
    for sub in ${PRESERVE_DIRS[@]+"${PRESERVE_DIRS[@]}"}; do
        [ -e "$live/$sub" ] || continue
        rm -rf "${staged:?}/${sub:?}"
        if ! cp -a "$live/$sub" "$staged/$sub"; then
            echo "$LOG_TAG: could not copy $sub into the new release; the update was not applied"
            APPLY_FAILURE="could not copy this install's own folders into the new release"
            return 1
        fi
    done
}

# Make the staged release at STAGED the install at LIVE, and the install that
# was running LIVE.previous (steps 2 and 3 above). On failure LIVE is the
# install that was running.
apply_swap() {
    local live="$1" staged="$2" previous="$1.previous"
    rm -rf "$previous"
    # mv puts a directory INSIDE a destination that is still a directory, so a
    # copy that survived its removal would swallow the install.
    if [ -e "$previous" ] || [ -L "$previous" ]; then
        echo "$LOG_TAG: could not remove $previous; the update was not applied"
        APPLY_FAILURE="could not remove the copy an earlier update kept to roll back to"
        return 1
    fi
    apply_carry_across "$live" "$staged" || return 1
    if ! mv "$live" "$previous"; then
        echo "$LOG_TAG: could not move $live aside; the update was not applied"
        APPLY_FAILURE="could not move the running install aside"
        return 1
    fi
    if ! mv "$staged" "$live"; then
        echo "$LOG_TAG: could not put the new release at $live; putting the running install back"
        mv "$previous" "$live" ||
            echo "$LOG_TAG: could not put $previous back at $live; manual intervention required"
        APPLY_FAILURE="could not move the new release into place"
        return 1
    fi
}

# Apply the release in ARTIFACT to the install at LIVE (steps 1 to 3 above).
# The release's tree is the staging directory itself, or the directory named
# INSIDE within it when the archive carries one at its root. CHECK names the
# caller's function that says whether a directory holds a release this install
# can run. Succeeds only when the release is in place; on any failure LIVE is
# the install that was running and the staging directory is gone. Returns
# APPLY_NOT_NOW when the release passed its check and this install could not
# take it, 1 when the release is at fault.
apply_release() {
    local artifact="$1" live="$2" inside="$3" check="$4"
    local staging="$live.new" status=0
    local staged="$staging${inside:+/$inside}"
    apply_unpack "$artifact" "$staging" || return
    if ! "$check" "$staged"; then
        echo "$LOG_TAG: $artifact is not an OpenAVC release this install can run; the update was not applied"
        rm -rf "$staging"
        return 1
    fi
    apply_swap "$live" "$staged" || status="$APPLY_NOT_NOW"
    rm -rf "$staging"
    return "$status"
}

# Apply the release the update instruction names ($ARTIFACT, to v$TO_VER, both
# read by the caller) to the install at LIVE; INSIDE and CHECK are
# apply_release's. When it fails the instruction is dropped if the release is
# at fault and set aside to try again if it is not. When it succeeds the
# instruction is left for the caller to remove once its own steps are done.
apply_update() {
    local status=0
    apply_release "$ARTIFACT" "$1" "$2" "$3" || status=$?
    case "$status" in
        0) return 0 ;;
        "$APPLY_NOT_NOW") defer_update "$APPLY_FAILURE" ;;
        *) rm -f "$UPDATE_FILE" ;;
    esac
    return 1
}

# Set the update in $UPDATE_FILE aside to be tried again at the next start,
# instead of dropping it the way a malformed, unverifiable or faulty one is
# dropped. REASON says what failed, in a few words the Updates page shows
# ("Update not applied yet (REASON); ..."). Every reason is a literal and
# $ARTIFACT and $TO_VER are the caller's, already read and checked, so the
# printf below cannot produce invalid JSON.
#
# The callers fail for reasons about the box at that moment rather than about
# the release: the Linux helper's Python environment that cannot be rebuilt or
# a dependency sync that cannot reach the package index, and apply_release's
# APPLY_NOT_NOW. Dropping the instruction there is what turns a first boot
# with no working DNS into a unit that runs its months-old golden baseline,
# healthy, forever.
defer_update() {
    local reason="$1"
    local attempts=$((RETRY_ATTEMPTS + 1))
    # Whether anything will try again. Recorded rather than left for a reader to
    # work out from the count, so the ceiling stays owned here alone and no
    # second copy of it has to exist in the server or the IDE.
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

# Put an update set aside back in play for this start, and remember how many
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

# Act on what the server left for this start. ON_UPDATE and ON_ROLLBACK name
# the caller's functions that apply an update and a rollback.
#
# A rollback asked for wins over any update, staged or set aside: applied
# first, the update would make the install that was running the copy kept to
# roll back to, and the rollback would put that back instead of the copy it
# was asked for. Both update records are dropped so neither resurfaces at a
# later start and re-applies the release the rollback exists to escape. Otherwise an update set aside earlier comes back into play
# (promote_deferred_update) and the update, if any, is applied.
apply_instructions() {
    local on_update="$1" on_rollback="$2"
    if [ -f "$ROLLBACK_FILE" ]; then
        rm -f "$UPDATE_FILE" "$RETRY_FILE"
        "$on_rollback"
        return 0
    fi
    promote_deferred_update
    if [ -f "$UPDATE_FILE" ]; then
        "$on_update"
    fi
    return 0
}
