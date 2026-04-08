#!/bin/bash
# Build OpenAVC Raspberry Pi image using pi-gen's Docker mode.
#
# Works from Git Bash on Windows with Docker Desktop, or any Linux host.
#
# Usage:
#   ./build.sh                  # Build the image
#   ./build.sh --continue       # Resume a failed build
#   ./build.sh --clean          # Remove previous build artifacts first
#
# Prerequisites:
#   - Docker Desktop running (Windows) or Docker Engine (Linux)
#   - Node.js + npm (to build the frontend)
#   - Internet connection (downloads Raspberry Pi OS base + packages)
#   - ~10GB free disk space
#   - ~30-60 minutes build time
#
# Output:
#   pi-gen/deploy/openavc-<version>-pi.img.xz

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PIGEN_DIR="$SCRIPT_DIR/pi-gen"
STAGE_DIR="$SCRIPT_DIR/stage-openavc"

# --- Preflight checks ---

echo "=== OpenAVC Pi Image Builder ==="
echo ""

# Check Docker is available
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is not installed or not in PATH."
    echo "Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
    exit 1
fi

if ! docker info &> /dev/null 2>&1; then
    echo "ERROR: Docker is not running. Start Docker Desktop and try again."
    exit 1
fi

# Check Node.js is available (for frontend build)
if ! command -v node &> /dev/null; then
    echo "ERROR: Node.js is not installed or not in PATH."
    echo "Install from: https://nodejs.org/"
    exit 1
fi

# Read version from pyproject.toml
VERSION=$(grep '^version' "$REPO_ROOT/pyproject.toml" | head -1 | sed 's/.*"\(.*\)".*/\1/')
echo "Version: ${VERSION}"

# --- Clone pi-gen ---

# IMPORTANT: Must use the arm64 branch for 64-bit images.
# The main/master branch is 32-bit only and targets Raspbian.
if [ ! -d "$PIGEN_DIR" ]; then
    echo ""
    echo "Cloning pi-gen (arm64 branch)..."
    git clone --depth 1 --branch arm64 https://github.com/RPi-Distro/pi-gen.git "$PIGEN_DIR"
else
    echo "Using existing pi-gen at $PIGEN_DIR"
fi

# --- Build frontend ---

echo ""
echo "Building Programmer UI..."
(cd "$REPO_ROOT/web/programmer" && npm ci --silent && npm run build) || { echo "ERROR: Programmer UI build failed"; exit 1; }

if [ -f "$REPO_ROOT/web/panel/package.json" ]; then
    echo "Building Panel UI..."
    (cd "$REPO_ROOT/web/panel" && npm ci --silent && npm run build) || { echo "ERROR: Panel UI build failed"; exit 1; }
fi

echo "Building Simulator UI..."
(cd "$REPO_ROOT/web/simulator" && npm ci --silent && npm run build) || { echo "ERROR: Simulator UI build failed"; exit 1; }

# --- Package server archive ---

echo ""
echo "Packaging OpenAVC server..."

# Ensure the target directory exists
mkdir -p "$STAGE_DIR/01-install-openavc/files"
OPENAVC_ARCHIVE="$STAGE_DIR/01-install-openavc/files/openavc.tar.gz"

tar czf "$OPENAVC_ARCHIVE" \
    -C "$REPO_ROOT" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='node_modules' \
    --exclude='.env' \
    --exclude='data' \
    --exclude='tests' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    --exclude='installer/pi-image/pi-gen' \
    --exclude='web/programmer/node_modules' \
    --exclude='web/programmer/src' \
    --exclude='web/programmer/.env*' \
    --exclude='web/simulator/node_modules' \
    --exclude='web/simulator/src' \
    --exclude='web/simulator/.env*' \
    server/ \
    simulator/ \
    web/panel/ \
    web/programmer/dist/ \
    web/simulator/dist/ \
    driver_repo/ \
    plugin_repo/ \
    themes/ \
    requirements.txt \
    pyproject.toml

ARCHIVE_SIZE=$(du -h "$OPENAVC_ARCHIVE" | cut -f1)
echo "Archive created: $ARCHIVE_SIZE"

# --- Configure pi-gen ---

echo ""
echo "Configuring pi-gen..."

# Copy our config into pi-gen
cp "$SCRIPT_DIR/config" "$PIGEN_DIR/config"
sed -i "s/__VERSION__/${VERSION}/g" "$PIGEN_DIR/config"

# Copy our custom stage into pi-gen (copy, not symlink — Docker needs real files)
rm -rf "$PIGEN_DIR/stage-openavc"
cp -r "$STAGE_DIR" "$PIGEN_DIR/stage-openavc"

# Ensure all shell scripts are executable (Windows doesn't preserve this)
find "$PIGEN_DIR/stage-openavc" -name "*.sh" -exec chmod +x {} \;
chmod +x "$PIGEN_DIR/stage-openavc/prerun.sh"

# Fix CRLF line endings in ALL pi-gen files (Windows git checkout adds \r)
# Skip binary files. This is critical — bash scripts with CRLF will fail.
echo "Fixing line endings..."
find "$PIGEN_DIR" -type f -not -path '*/.git/*' \
    -not -name '*.tar.gz' -not -name '*.png' -not -name '*.ico' \
    -not -name '*.avc' -not -name '*.exe' -not -name '*.img*' \
    -not -name '*.xz' -not -name '*.zip' \
    -exec sed -i 's/\r$//' {} + 2>/dev/null

# Skip stages 4 and 5 (full desktop extras, not needed)
touch "$PIGEN_DIR/stage4/SKIP" "$PIGEN_DIR/stage4/SKIP_IMAGES"
touch "$PIGEN_DIR/stage5/SKIP" "$PIGEN_DIR/stage5/SKIP_IMAGES"

# Only generate the final image after our custom stage
touch "$PIGEN_DIR/stage0/SKIP_IMAGES"
touch "$PIGEN_DIR/stage1/SKIP_IMAGES"
touch "$PIGEN_DIR/stage2/SKIP_IMAGES"
touch "$PIGEN_DIR/stage3/SKIP_IMAGES"

# --- Handle QEMU/binfmt for ARM emulation ---

echo ""
echo "Setting up ARM emulation (QEMU binfmt)..."
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes 2>/dev/null || {
    echo "WARNING: Could not set up QEMU binfmt. ARM emulation may not work."
    echo "If the build fails, run this manually:"
    echo "  docker run --rm --privileged multiarch/qemu-user-static --reset -p yes"
}

# --- Run pi-gen Docker build ---

echo ""
echo "Starting pi-gen Docker build..."
echo "This will take 30-60 minutes. Go get coffee."
echo ""

# Handle --clean flag
if [ "${1:-}" = "--clean" ]; then
    echo "Cleaning previous build first..."
    (cd "$PIGEN_DIR" && rm -rf work/ deploy/)
fi

# Set CONTINUE flag for --continue
PIGEN_DOCKER_OPTS=""
if [ "${1:-}" = "--continue" ]; then
    export CONTINUE=1
    echo "Continuing from previous build..."
fi

# pi-gen's build-docker.sh handles everything
(cd "$PIGEN_DIR" && PRESERVE_CONTAINER=0 ./build-docker.sh)

# --- Check output ---

echo ""
IMG_FILE=$(ls "$PIGEN_DIR/deploy/"*.img.xz 2>/dev/null | tail -1)
if [ -n "$IMG_FILE" ]; then
    IMG_SIZE=$(du -h "$IMG_FILE" | cut -f1)
    echo "============================================"
    echo "  BUILD COMPLETE"
    echo "============================================"
    echo ""
    echo "  Image:  $IMG_FILE"
    echo "  Size:   $IMG_SIZE"
    echo ""
    echo "  Flash with Raspberry Pi Imager:"
    echo "  https://www.raspberrypi.com/software/"
    echo ""
    echo "  Default login:  openavc / openavc"
    echo "  Access:          http://openavc.local:8080"
    echo "============================================"

    # Copy output to a more accessible location
    OUTPUT_DIR="$SCRIPT_DIR/output"
    mkdir -p "$OUTPUT_DIR"
    cp "$IMG_FILE" "$OUTPUT_DIR/"
    echo ""
    echo "  Copied to: $OUTPUT_DIR/$(basename "$IMG_FILE")"
else
    echo "ERROR: No image file found in $PIGEN_DIR/deploy/"
    echo "Check the build log above for errors."
    exit 1
fi
