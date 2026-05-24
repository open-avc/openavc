# OpenAVC Docker Image
#
# Multi-stage build: build frontend, then create slim production image.
#
# Build:  docker build -t openavc/openavc:latest .
# Run:    docker compose up -d
#
# Data is stored in /data (mount a volume to persist across restarts).

# --- Stage 1: Build frontends ---
# Pin the frontend build to the build host's native architecture. The output
# is static JS/CSS that's arch-agnostic, so running Node under QEMU emulation
# for the linux/arm64 leg of a multi-arch build buys us nothing except
# intermittent SIGILLs when QEMU mistranslates Node's JIT. Build once on
# amd64 and COPY the dist/ into both final images.
FROM --platform=$BUILDPLATFORM node:20-alpine AS frontend
WORKDIR /build
COPY web/programmer/package*.json ./programmer/
COPY web/simulator/package*.json ./simulator/
RUN cd programmer && npm ci && cd ../simulator && npm ci
COPY web/programmer/ ./programmer/
COPY web/simulator/ ./simulator/
# Panel dir needed because the build copies icons.svg into it
COPY web/panel/ ./panel/
RUN cd programmer && npm run build && cd ../simulator && npm run build

# --- Stage 2: Production image ---
FROM python:3.12-slim

# Discovery needs `ping` and `ip` (slim image does not include them).
# setcap on ping lets the unprivileged `openavc` user open ICMP sockets without
# requiring a host-level `net.ipv4.ping_group_range` sysctl change. Requires
# `cap_add: NET_RAW` in the compose file so the capability survives in the
# container's bounding set.
RUN apt-get update && apt-get install -y --no-install-recommends \
    iputils-ping \
    iproute2 \
    libcap2-bin \
    && setcap cap_net_raw+ep /usr/bin/ping \
    && rm -rf /var/lib/apt/lists/*


# Install UV from Astral's "distroless" image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Use the system's Python (avoid downloading uv's Python binaries)
ENV UV_PYTHON_DOWNLOADS=0

# Enable UV's bytecode compilation for faster startup.
ENV UV_COMPILE_BYTECODE=1

# Disable UV's cache dir
ENV UV_NO_CACHE=1

# Create non-root user
RUN groupadd -r openavc && useradd -r -g openavc -d /app -s /usr/sbin/nologin openavc

WORKDIR /app

# Install dependencies first (layer caching)
COPY uv.lock .
COPY pyproject.toml .

# Install runtime dependencies only (no dev dependencies, no project sources)
RUN uv sync --locked --no-install-project --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Copy server code and simulator package
COPY server/ ./server/
COPY simulator/ ./simulator/

# Install the project in editable mode
RUN uv sync --locked --no-dev


# Copy built frontend from stage 1
COPY --from=frontend /build/programmer/dist/ ./web/programmer/dist/

# Copy Panel UI (includes icons.svg generated during programmer build)
COPY --from=frontend /build/panel/ ./web/panel/

# Copy built Simulator UI from stage 1
COPY --from=frontend /build/simulator/dist/ ./web/simulator/dist/

# Copy data files
COPY themes/ ./themes/
COPY installer/seed/default/ ./seed/default/
COPY installer/openavc.service ./installer/openavc.service

# Set up data directory structure. plugin_repo and driver_repo are
# created under /data at first start by the runtime — keeping them off
# the container image guarantees user-installed content survives an image
# pull (the old /app/{driver,plugin}_repo layout was wiped by every
# `docker compose up -d` with a new image).
RUN mkdir -p /data/projects/default /data/backups /data/logs \
    && cp seed/default/project.avc /data/projects/default/project.avc \
    && chown -R openavc:openavc /data /app

USER openavc

# Environment
ENV OPENAVC_DATA_DIR=/data
ENV OPENAVC_LOG_DIR=/data/logs
ENV OPENAVC_BIND=0.0.0.0
ENV OPENAVC_PROJECT=/data/projects/default/project.avc
# Tells main.py that the container's restart policy will relaunch us, so
# cloud-restart can just exit cleanly. PID-1 / /.dockerenv detection in
# main.py also catches this; this env makes the contract explicit.
ENV OPENAVC_SERVICE_MANAGED=1

EXPOSE 8080

# Hits the plain HTTP port: when TLS is enabled with redirect_http=true (the
# default), the redirect listener returns 301 to https://, and urllib follows
# it. The unverified SSL context lets that redirect succeed for the self-signed
# cert without affecting the TLS-off path (HTTP ignores the context arg).
# Caveat: with TLS on AND redirect_http=false, this healthcheck cannot reach
# the TLS listener at port 8443 — document in deployment.md.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import ssl,urllib.request; urllib.request.urlopen('http://localhost:8080/api/health', context=ssl._create_unverified_context())"

ENTRYPOINT ["python", "-m", "server.main"]
