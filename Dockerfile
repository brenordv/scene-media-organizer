FROM python:3.13-slim

# uv binary from the official distroless image. Keep the version pinned; for a
# fully immutable reference, append the image digest for this tag
# (ghcr.io/astral-sh/uv:0.12.7@sha256:...).
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /uvx /bin/

# System packages. psycopg[binary] bundles libpq, so no libpq-dev or build
# toolchain is required.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       curl \
       tar \
       xz-utils \
       rsync \
    && rm -rf /var/lib/apt/lists/*

# 7-Zip (7zz) pinned for reproducible builds. Do not downgrade below the
# current 25.01 pin. The checksum is verified; compute it once from the
# downloaded artifact and keep it next to the version.
ARG SEVENZIP_SHA256=4ca3b7c6f2f67866b92622818b58233dc70367be2f36b498eb0bdeaaa44b53f4
RUN curl -L -o /tmp/7z.tar.xz \
      https://github.com/ip7z/7zip/releases/download/25.01/7z2501-linux-x64.tar.xz \
 && echo "${SEVENZIP_SHA256}  /tmp/7z.tar.xz" | sha256sum -c - \
 && tar -xJ -C /tmp -f /tmp/7z.tar.xz \
 && mv /tmp/7zz /usr/local/bin/ \
 && mv /tmp/7zzs /usr/local/bin/ \
 && chmod +x /usr/local/bin/7zz /usr/local/bin/7zzs \
 && rm /tmp/7z.tar.xz

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependency layer: cached until pyproject.toml or uv.lock changes
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"

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
