#!/bin/bash -e
# Install Python dependencies into the venv.
# Falls back to system-wide install if venv pip fails (QEMU compatibility).

OPENAVC_DIR="/opt/openavc"
VENV_DIR="$OPENAVC_DIR/venv"

if [ -f "$OPENAVC_DIR/requirements.txt" ]; then
    echo "Installing Python dependencies..."
    "$VENV_DIR/bin/pip" install --no-cache-dir -r "$OPENAVC_DIR/requirements.txt" || {
        echo "venv pip failed, trying system pip with --break-system-packages"
        pip3 install --break-system-packages -r "$OPENAVC_DIR/requirements.txt"
    }
    echo "Python dependencies installed."
else
    echo "WARNING: No requirements.txt found at $OPENAVC_DIR, skipping pip install"
fi
