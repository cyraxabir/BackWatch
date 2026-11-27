# ============================================================
# Dockerfile — Backup Health Monitor
# ============================================================
# Multi-stage build for minimal production image.
# Runs as non-root user with minimal capabilities.
# ============================================================

FROM python:3.12-slim AS builder

WORKDIR /build

# Install system dependencies for integrity checks
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gzip \
        tar \
        unzip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Production image ──────────────────────────────────────
FROM python:3.12-slim

# Install runtime dependencies for integrity checks
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gzip \
        tar \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages
COPY --from=builder /install /usr/local

# Create non-root user
RUN groupadd -r monitor && \
    useradd -r -g monitor -s /sbin/nologin -d /app monitor

# Create directories
RUN mkdir -p /app /state /backups && \
    chown -R monitor:monitor /app /state

WORKDIR /app

# Copy application
COPY --chown=monitor:monitor app/ /app/app/
COPY --chown=monitor:monitor config/ /app/config/

# Switch to non-root user
USER monitor

# Environment defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONFIG_PATH=/app/config/config.yml \
    BACKUP_ROOT=/backups \
    STATE_DIR=/state \
    DB_PATH=/state/history.db \
    METRICS_PORT=9108 \
    LOG_LEVEL=INFO

EXPOSE 9108

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9108/')" || exit 1

ENTRYPOINT ["python", "-m", "app.main"]
