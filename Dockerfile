# Build stage: install the locked dependencies into /app/.venv.
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies first, so this layer is reused while only the application code changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# Runtime stage: the virtual environment and the application, without uv or build caches.
FROM python:3.14-slim

# System packages. psycopg[binary] bundles libpq, so no libpq-dev or build toolchain is required.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       curl \
       tar \
       xz-utils \
       rsync \
    && rm -rf /var/lib/apt/lists/*

# 7-Zip (7zz) pinned for reproducible builds, with the checksum GitHub publishes for the release asset. Do not go
# below 26.02: earlier releases are open to CVE-2026-14266 (XZ decompression heap overflow, remote code execution),
# and 7zz picks the handler from the file's content, whatever its name.
ARG SEVENZIP_VERSION=2605
ARG SEVENZIP_SHA256=22cf82fcc6eb7474f1d5bb4f02c0f8ebd573a718089d6833dc30f8350a3b232e
RUN curl -fL -o /tmp/7z.tar.xz \
      "https://github.com/ip7z/7zip/releases/download/26.05/7z${SEVENZIP_VERSION}-linux-x64.tar.xz" \
 && echo "${SEVENZIP_SHA256}  /tmp/7z.tar.xz" | sha256sum -c - \
 && tar -xJ -C /tmp -f /tmp/7z.tar.xz \
 && mv /tmp/7zz /usr/local/bin/ \
 && mv /tmp/7zzs /usr/local/bin/ \
 && chmod +x /usr/local/bin/7zz /usr/local/bin/7zzs \
 && rm /tmp/7z.tar.xz

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Files stay owned by root. The process also runs as root, because WATCHDOG_CHANGE_DEST_OWNERSHIP_ON_COPY calls chown.
COPY --from=builder /app /app

# App configuration (override at runtime). Do NOT add MQTT_USERNAME or
# MQTT_PASSWORD here: NotificationRepository treats a set-but-empty username
# as real credentials and would authenticate with empty strings.
ENV WATCH_FOLDER="" \
    MOVIES_BASE_FOLDER="" \
    SERIES_BASE_FOLDER="" \
    POSTGRES_HOST="" \
    POSTGRES_PORT="5432" \
    POSTGRES_USER="" \
    POSTGRES_PASSWORD="" \
    POSTGRES_DB="" \
    API_URL="" \
    MQTT_HOST="" \
    MQTT_PORT="1883" \
    MQTT_BASE_TOPIC="notifications" \
    MQTT_CLIENT_ID="" \
    TELEGRAM_BOT_TOKEN="" \
    TELEGRAM_CHAT_ID="" \
    TELEGRAM_PARSE_MODE="HTML" \
    TELEGRAM_DISABLE_WEB_PREVIEW="false" \
    TELEGRAM_DISABLE_NOTIFICATION="false" \
    WATCHDOG_CHANGE_DEST_OWNERSHIP_ON_COPY="false" \
    OTEL_EXPORTER_OTLP_ENDPOINT=""

COPY healthcheck.py /usr/local/bin/healthcheck.py
RUN chmod +x /usr/local/bin/healthcheck.py

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python /usr/local/bin/healthcheck.py || exit 1

CMD ["python", "main.py"]
