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
# Sourced by update-helper.sh (Linux, the Raspberry Pi, appliance hardware),
# from the installer/ folder of the install it belongs to. It keeps to bash 3.2
# and to what the BSD and GNU tar, cp and mv share, which is what macOS has.
# Messages go to stdout, prefixed with the caller's $LOG_TAG.

# Unpack ARTIFACT into a fresh, empty STAGING directory. Root extracting a tar
# restores the owners recorded in it, which are accounts on the machine that
# built the release, so the files are made the extracting user's instead.
# On failure nothing is left behind.
apply_unpack() {
    local artifact="$1" staging="$2"
    rm -rf "$staging"
    if ! mkdir -p "$staging"; then
        echo "$LOG_TAG: could not create $staging; the update was not applied"
        return 1
    fi
    if ! tar xzf "$artifact" -C "$staging" --no-same-owner; then
        echo "$LOG_TAG: $artifact could not be unpacked; the update was not applied"
        rm -rf "$staging"
        return 1
    fi
}

# Copy each of the caller's PRESERVE_DIRS that the install at LIVE has into
# the staged release at STAGED, in place of anything the release shipped under
# that name. A copy, not a move, so LIVE stays whole until the swap.
apply_carry_across() {
    local live="$1" staged="$2" sub
    for sub in "${PRESERVE_DIRS[@]}"; do
        [ -e "$live/$sub" ] || continue
        rm -rf "${staged:?}/${sub:?}"
        if ! cp -a "$live/$sub" "$staged/$sub"; then
            echo "$LOG_TAG: could not copy $sub into the new release; the update was not applied"
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
        return 1
    fi
    apply_carry_across "$live" "$staged" || return 1
    if ! mv "$live" "$previous"; then
        echo "$LOG_TAG: could not move $live aside; the update was not applied"
        return 1
    fi
    if ! mv "$staged" "$live"; then
        echo "$LOG_TAG: could not put the new release at $live; putting the running install back"
        mv "$previous" "$live" ||
            echo "$LOG_TAG: could not put $previous back at $live; manual intervention required"
        return 1
    fi
}

# Apply the release in ARTIFACT to the install at LIVE (steps 1 to 3 above).
# The release's tree is the staging directory itself, or the directory named
# INSIDE within it when the archive carries one at its root. CHECK names the
# caller's function that says whether a directory holds a release this install
# can run. Succeeds only when the release is in place; on any failure LIVE is
# the install that was running and the staging directory is gone.
apply_release() {
    local artifact="$1" live="$2" inside="$3" check="$4"
    local staging="$live.new"
    local staged="$staging${inside:+/$inside}"
    apply_unpack "$artifact" "$staging" || return 1
    if ! "$check" "$staged"; then
        echo "$LOG_TAG: $artifact is not an OpenAVC release this install can run; the update was not applied"
        rm -rf "$staging"
        return 1
    fi
    if ! apply_swap "$live" "$staged"; then
        rm -rf "$staging"
        return 1
    fi
    rm -rf "$staging"
}
