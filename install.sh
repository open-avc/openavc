#!/usr/bin/env bash
#
# OpenAVC Linux Installer
#
# One-line install:
#   curl -sSL https://get.openavc.com | bash
#
# What this script does:
#   1. Detects your Linux distribution and architecture
#   2. Installs Python 3.12+ if not present
#   3. Creates an 'openavc' system user
#   4. Downloads the latest OpenAVC release from GitHub
#   5. Extracts to /opt/openavc/ with a Python venv
#   6. Sets up the data directory at /var/lib/openavc/
#   7. Installs a systemd service (auto-start on boot)
#   8. Configures the firewall (if ufw or firewalld is active)
#   9. Starts the server
#
# Supports: Debian/Ubuntu, Fedora/RHEL/Rocky, Arch. x86_64 and arm64.
# Requires: root (or sudo). systemd.
#
# Re-running this script on an existing install will upgrade in place.

set -euo pipefail

# --- Configuration ---

GITHUB_REPO="open-avc/openavc"
INSTALL_DIR="/opt/openavc"
DATA_DIR="/var/lib/openavc"
LOG_DIR="/var/log/openavc"
SERVICE_NAME="openavc"
SERVICE_USER="openavc"
SERVICE_GROUP="openavc"
HTTP_PORT=8080
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=11

# --- Colors ---

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
fatal() { error "$*"; exit 1; }

# --- Pre-flight checks ---

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        fatal "This script must be run as root. Try: sudo bash or curl ... | sudo bash"
    fi
}

check_systemd() {
    if ! command -v systemctl &>/dev/null; then
        fatal "systemd is required but not found. This script does not support init.d or other init systems."
    fi
}

check_curl_or_wget() {
    if command -v curl &>/dev/null; then
        DOWNLOADER="curl"
    elif command -v wget &>/dev/null; then
        DOWNLOADER="wget"
    else
        fatal "curl or wget is required but neither was found."
    fi
}

# --- Platform detection ---

detect_arch() {
    local arch
    arch=$(uname -m)
    case "$arch" in
        x86_64|amd64)  ARCH="amd64" ;;
        aarch64|arm64) ARCH="arm64" ;;
        *) fatal "Unsupported architecture: $arch. OpenAVC supports x86_64 and arm64." ;;
    esac
    ok "Architecture: $ARCH"
}

detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        DISTRO_ID="${ID:-unknown}"
        DISTRO_ID_LIKE="${ID_LIKE:-}"
        DISTRO_NAME="${PRETTY_NAME:-$DISTRO_ID}"
    else
        fatal "Cannot detect Linux distribution (/etc/os-release not found)."
    fi

    # Determine package manager
    if command -v apt-get &>/dev/null; then
        PKG_MANAGER="apt"
    elif command -v dnf &>/dev/null; then
        PKG_MANAGER="dnf"
    elif command -v pacman &>/dev/null; then
        PKG_MANAGER="pacman"
    else
        fatal "No supported package manager found (apt, dnf, or pacman required)."
    fi

    ok "Distribution: $DISTRO_NAME (package manager: $PKG_MANAGER)"
}

# --- Python ---

python_version_ok() {
    local python_bin="$1"
    if ! command -v "$python_bin" &>/dev/null; then
        return 1
    fi
    local version
    version=$("$python_bin" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null) || return 1
    local major minor
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)
    if [ "$major" -gt "$MIN_PYTHON_MAJOR" ] || { [ "$major" -eq "$MIN_PYTHON_MAJOR" ] && [ "$minor" -ge "$MIN_PYTHON_MINOR" ]; }; then
        return 0
    fi
    return 1
}

find_python() {
    # Check common Python binary names
    for py in python3.13 python3.12 python3.11 python3; do
        if python_version_ok "$py"; then
            PYTHON_BIN=$(command -v "$py")
            ok "Python: $($PYTHON_BIN --version) at $PYTHON_BIN"
            return 0
        fi
    done
    return 1
}

install_python() {
    info "Installing Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+..."

    case "$PKG_MANAGER" in
        apt)
            # Try system Python first, then deadsnakes PPA for older Ubuntu
            apt-get update -qq
            if apt-cache show python3 2>/dev/null | grep -qE "Version: 3\.(1[1-9]|[2-9][0-9])"; then
                apt-get install -y -qq python3 python3-venv python3-pip
            else
                info "System Python is too old. Adding deadsnakes PPA..."
                apt-get install -y -qq software-properties-common
                add-apt-repository -y ppa:deadsnakes/ppa
                apt-get update -qq
                apt-get install -y -qq python3.12 python3.12-venv
            fi
            ;;
        dnf)
            dnf install -y -q python3 python3-pip
            ;;
        pacman)
            pacman -Sy --noconfirm --quiet python python-pip
            ;;
    esac

    if ! find_python; then
        fatal "Failed to install Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+. Please install it manually and re-run this script."
    fi
}

ensure_python() {
    if find_python; then
        return 0
    fi
    install_python
}

# --- Download ---

download() {
    local url="$1"
    local dest="$2"
    if [ "$DOWNLOADER" = "curl" ]; then
        curl -fsSL -o "$dest" "$url"
    else
        wget -q -O "$dest" "$url"
    fi
}

get_latest_release_url() {
    local asset_name="openavc-.*-linux-${ARCH}\\.tar\\.gz"

    info "Checking for latest release..."

    # Try /releases/latest first (stable releases only), then fall back to
    # /releases (includes prereleases) so beta testers can install too.
    local release_json=""
    local api_urls=(
        "https://api.github.com/repos/${GITHUB_REPO}/releases/latest"
        "https://api.github.com/repos/${GITHUB_REPO}/releases?per_page=1"
    )

    for api_url in "${api_urls[@]}"; do
        if [ "$DOWNLOADER" = "curl" ]; then
            release_json=$(curl -fsSL -H "Accept: application/vnd.github.v3+json" "$api_url" 2>/dev/null) || true
        else
            release_json=$(wget -q -O - --header="Accept: application/vnd.github.v3+json" "$api_url" 2>/dev/null) || true
        fi

        if [ -n "$release_json" ]; then
            break
        fi
    done

    if [ -z "$release_json" ]; then
        return 1
    fi

    # Parse JSON without jq (grep + sed)
    RELEASE_VERSION=$(echo "$release_json" | grep -o '"tag_name":\s*"[^"]*"' | head -1 | sed 's/.*"tag_name":\s*"\([^"]*\)".*/\1/' | sed 's/^v//')
    RELEASE_URL=$(echo "$release_json" | grep -o '"browser_download_url":\s*"[^"]*linux-'"${ARCH}"'[^"]*\.tar\.gz"' | head -1 | sed 's/"browser_download_url":\s*"\([^"]*\)"/\1/')

    if [ -n "$RELEASE_URL" ] && [ -n "$RELEASE_VERSION" ]; then
        ok "Latest release: v${RELEASE_VERSION}"
        return 0
    fi

    return 1
}

download_release() {
    if ! get_latest_release_url; then
        fatal "Could not find a release for linux-${ARCH}. Check https://github.com/${GITHUB_REPO}/releases"
    fi

    local archive="/tmp/openavc-${RELEASE_VERSION}-linux-${ARCH}.tar.gz"
    info "Downloading v${RELEASE_VERSION} for linux-${ARCH}..."
    download "$RELEASE_URL" "$archive"
    ok "Downloaded: $(du -h "$archive" | cut -f1)"

    ARCHIVE_PATH="$archive"
}

# --- Install ---

create_user() {
    if id "$SERVICE_USER" &>/dev/null; then
        ok "User '$SERVICE_USER' already exists"
        return 0
    fi

    info "Creating system user '$SERVICE_USER'..."
    useradd --system --shell /usr/sbin/nologin --home-dir "$INSTALL_DIR" --create-home "$SERVICE_USER"
    ok "Created user: $SERVICE_USER"
}

install_files() {
    local is_upgrade=false
    if [ -d "$INSTALL_DIR/server" ]; then
        is_upgrade=true
        info "Existing installation found. Upgrading..."
        # Stop service before replacing files
        systemctl stop "$SERVICE_NAME" 2>/dev/null || true
        # Keep previous version for rollback
        if [ -d "${INSTALL_DIR}.previous" ]; then
            rm -rf "${INSTALL_DIR}.previous"
        fi
        cp -a "$INSTALL_DIR" "${INSTALL_DIR}.previous"
    fi

    info "Extracting to ${INSTALL_DIR}/..."
    mkdir -p "$INSTALL_DIR"
    tar -xzf "$ARCHIVE_PATH" -C "$INSTALL_DIR"
    ok "Extracted to ${INSTALL_DIR}/"

    # Clean up archive
    rm -f "$ARCHIVE_PATH"
}

create_venv() {
    info "Setting up Python virtual environment..."

    if [ -d "$INSTALL_DIR/venv" ]; then
        # Upgrade existing venv
        "$PYTHON_BIN" -m venv --upgrade "$INSTALL_DIR/venv"
    else
        "$PYTHON_BIN" -m venv "$INSTALL_DIR/venv"
    fi

    "$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
    "$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
    ok "Virtual environment ready"
}

setup_data_dir() {
    info "Setting up data directory at ${DATA_DIR}/..."

    mkdir -p "$DATA_DIR"/{projects/default,drivers,backups,logs}
    mkdir -p "$LOG_DIR"

    # Seed default project if not present
    if [ ! -f "$DATA_DIR/projects/default/project.avc" ]; then
        if [ -f "$INSTALL_DIR/installer/seed/default/project.avc" ]; then
            cp "$INSTALL_DIR/installer/seed/default/project.avc" "$DATA_DIR/projects/default/project.avc"
            info "Seeded default project"
        fi
    fi

    # Set ownership
    chown -R "$SERVICE_USER:$SERVICE_GROUP" "$DATA_DIR"
    chown -R "$SERVICE_USER:$SERVICE_GROUP" "$LOG_DIR"
    chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR"

    ok "Data directory ready"
}

install_service() {
    info "Installing systemd service..."

    local service_file="/etc/systemd/system/${SERVICE_NAME}.service"

    if [ -f "$INSTALL_DIR/installer/openavc.service" ]; then
        cp "$INSTALL_DIR/installer/openavc.service" "$service_file"
    else
        # Fallback: write the service file inline
        cat > "$service_file" << 'UNIT'
[Unit]
Description=OpenAVC Room Control Server
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User=openavc
Group=openavc
WorkingDirectory=/opt/openavc
ExecStart=/opt/openavc/venv/bin/python -m server.main
Restart=always
RestartSec=5
Environment=OPENAVC_DATA_DIR=/var/lib/openavc
Environment=OPENAVC_LOG_DIR=/var/log/openavc
Environment=OPENAVC_BIND=0.0.0.0
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/openavc /var/log/openavc
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
    fi

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    ok "Service installed and enabled"
}

configure_firewall() {
    # ufw (Ubuntu/Debian)
    if command -v ufw &>/dev/null && ufw status | grep -q "active"; then
        info "Configuring ufw firewall..."
        ufw allow "$HTTP_PORT/tcp" comment "OpenAVC" >/dev/null 2>&1
        ok "Firewall: port $HTTP_PORT opened (ufw)"
        return 0
    fi

    # firewalld (Fedora/RHEL)
    if command -v firewall-cmd &>/dev/null && systemctl is-active firewalld &>/dev/null; then
        info "Configuring firewalld..."
        firewall-cmd --permanent --add-port="$HTTP_PORT/tcp" >/dev/null 2>&1
        firewall-cmd --reload >/dev/null 2>&1
        ok "Firewall: port $HTTP_PORT opened (firewalld)"
        return 0
    fi

    warn "No active firewall detected (ufw or firewalld). Port $HTTP_PORT may already be accessible."
}

start_service() {
    info "Starting OpenAVC..."
    systemctl start "$SERVICE_NAME"

    # Wait a moment and check if it's running
    sleep 3
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        ok "OpenAVC is running"
    else
        error "OpenAVC failed to start. Check: journalctl -u $SERVICE_NAME -n 50"
        return 1
    fi
}

# --- Main ---

main() {
    echo ""
    echo -e "${GREEN}============================================================${NC}"
    echo -e "${GREEN}  OpenAVC Linux Installer${NC}"
    echo -e "${GREEN}============================================================${NC}"
    echo ""

    check_root
    check_systemd
    check_curl_or_wget

    detect_arch
    detect_distro
    ensure_python

    download_release
    create_user
    install_files
    create_venv
    setup_data_dir
    install_service
    configure_firewall
    start_service

    # Get the server's IP address for the URL
    local ip
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    if [ -z "$ip" ]; then
        ip="<your-server-ip>"
    fi

    echo ""
    echo -e "${GREEN}============================================================${NC}"
    echo -e "${GREEN}  OpenAVC v${RELEASE_VERSION} installed successfully!${NC}"
    echo -e "${GREEN}============================================================${NC}"
    echo ""
    echo -e "  Programmer IDE:  ${BLUE}http://${ip}:${HTTP_PORT}/programmer${NC}"
    echo -e "  Panel UI:        ${BLUE}http://${ip}:${HTTP_PORT}/panel${NC}"
    echo -e "  REST API:        ${BLUE}http://${ip}:${HTTP_PORT}/api${NC}"
    echo ""
    echo -e "  Service:         systemctl {start|stop|restart|status} $SERVICE_NAME"
    echo -e "  Logs:            journalctl -u $SERVICE_NAME -f"
    echo -e "  Data:            $DATA_DIR/"
    echo ""
    echo -e "  To upgrade later, re-run this script."
    echo ""
}

main "$@"
