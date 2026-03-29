#!/bin/bash -e
# Copy the rootfs from the previous stage (stage3) so we can customize it.
if [ ! -d "${ROOTFS_DIR}" ]; then
	copy_previous
fi
