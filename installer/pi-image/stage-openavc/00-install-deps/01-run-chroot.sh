#!/bin/bash -e
# Create Python virtual environment and install dependencies.
# Idempotent: skips venv creation if it already exists (for CONTINUE builds).

OPENAVC_DIR="/opt/openavc"
VENV_DIR="$OPENAVC_DIR/venv"

mkdir -p "$OPENAVC_DIR"

if [ -d "$VENV_DIR/bin" ]; then
    echo "Python venv already exists at $VENV_DIR, skipping creation"
else
    echo "Creating Python venv..."
    python3 -m venv "$VENV_DIR" || {
        echo "venv creation failed, trying with --system-site-packages"
        python3 -m venv --system-site-packages "$VENV_DIR"
    }
fi

"$VENV_DIR/bin/pip" install --upgrade pip 2>/dev/null || true
echo "Python venv ready at $VENV_DIR"
