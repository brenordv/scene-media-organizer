FROM python:3.13-slim

# System packages required by the app and some Python wheels (psycopg2), plus unrar
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       libpq-dev \
       p7zip-full \
       ca-certificates \
       rsync \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

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
    WATCHDOG_CHANGE_DEST_OWNERSHIP_ON_COPY="false"

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
