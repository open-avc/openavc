# OpenAVC Docker Image
#
# Multi-stage build: build frontend, then create slim production image.
#
# Build:  docker build -t openavc/openavc:latest .
# Run:    docker compose up -d
#
# Data is stored in /data (mount a volume to persist across restarts).

# --- Stage 1: Build frontends ---
FROM node:20-alpine AS frontend
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
RUN apt-get update && apt-get install -y --no-install-recommends \
    iputils-ping \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r openavc && useradd -r -g openavc -d /app -s /usr/sbin/nologin openavc

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy server code and simulator package
COPY server/ ./server/
COPY simulator/ ./simulator/
COPY pyproject.toml .

# Copy built frontend from stage 1
COPY --from=frontend /build/programmer/dist/ ./web/programmer/dist/

# Copy Panel UI (includes icons.svg generated during programmer build)
COPY --from=frontend /build/panel/ ./web/panel/

# Copy built Simulator UI from stage 1
COPY --from=frontend /build/simulator/dist/ ./web/simulator/dist/

# Copy data files
COPY themes/ ./themes/
RUN mkdir -p driver_repo plugin_repo
COPY installer/seed/default/ ./seed/default/
COPY installer/openavc.service ./installer/openavc.service

# Set up data directory structure
RUN mkdir -p /data/projects/default /data/drivers /data/backups /data/logs \
    && cp seed/default/project.avc /data/projects/default/project.avc \
    && chown -R openavc:openavc /data /app

USER openavc

# Environment
ENV OPENAVC_DATA_DIR=/data
ENV OPENAVC_LOG_DIR=/data/logs
ENV OPENAVC_BIND=0.0.0.0
ENV OPENAVC_PROJECT=/data/projects/default/project.avc

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')"

ENTRYPOINT ["python", "-m", "server.main"]
