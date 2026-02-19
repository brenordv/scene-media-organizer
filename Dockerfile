FROM python:3.13-slim

# Install system packages required by the app and some Python wheels (psycopg2), plus unrar
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       libpq-dev \
       ca-certificates \
       curl \
       tar \
       xz-utils \
       rsync \
    && rm -rf /var/lib/apt/lists/*

# Install latest 7-Zip (7zz) from GitHub release
# Pin version here (25.01) so builds are reproducible
RUN curl -L https://github.com/ip7z/7zip/releases/download/25.01/7z2501-linux-x64.tar.xz \
    | tar -xJ -C /tmp \
 && mv /tmp/7zz /usr/local/bin/ \
 && mv /tmp/7zzs /usr/local/bin/ \
 && chmod +x /usr/local/bin/7zz /usr/local/bin/7zzs

# Improve Python behavior in containers
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# App configuration (override at runtime).
ENV WATCH_FOLDER="" \
    MOVIES_BASE_FOLDER="" \
    SERIES_BASE_FOLDER="" \
    POSTGRES_HOST="" \
    POSTGRES_PORT="5432" \
    POSTGRES_USER="" \
    POSTGRES_PASSWORD="" \
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

# Install Python dependencies first (better layer caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the source
COPY . .

# Enabling Healthcheck.
COPY healthcheck.py /usr/local/bin/healthcheck.py
RUN chmod +x /usr/local/bin/healthcheck.py

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python /usr/local/bin/healthcheck.py || exit 1

CMD ["python", "main.py"]
