#!/bin/sh
# Starts the OpenAVC server in the Docker image.
#
# python3-openavc is a copy of the interpreter that may open ports below 1024
# (see the Dockerfile). A container that drops that capability (cap_drop) can
# not start it at all, so it gets plain python instead, which runs exactly as
# the image always has; only a port below 1024 is then out of reach.
if /usr/local/bin/python3-openavc -c "" >/dev/null 2>&1; then
    exec /usr/local/bin/python3-openavc -m openavc.main "$@"
fi
exec python -m openavc.main "$@"
