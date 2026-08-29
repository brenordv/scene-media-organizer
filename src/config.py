"""Configuration bootstrap. Precedence: CLI flags > process environment > .env file."""

import argparse
import os
import sys

from dotenv import load_dotenv

# argparse dest -> environment variable name
ENV_FLAG_MAP = {
    "watch_folder": "WATCH_FOLDER",
    "movies_base_folder": "MOVIES_BASE_FOLDER",
    "series_base_folder": "SERIES_BASE_FOLDER",
    "postgres_host": "POSTGRES_HOST",
    "postgres_port": "POSTGRES_PORT",
    "postgres_user": "POSTGRES_USER",
    "postgres_password": "POSTGRES_PASSWORD",
    "postgres_db": "POSTGRES_DB",
    "api_url": "API_URL",
    "mqtt_host": "MQTT_HOST",
    "mqtt_port": "MQTT_PORT",
    "mqtt_base_topic": "MQTT_BASE_TOPIC",
    "mqtt_client_id": "MQTT_CLIENT_ID",
    "mqtt_username": "MQTT_USERNAME",
    "mqtt_password": "MQTT_PASSWORD",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "otel_endpoint": "OTEL_EXPORTER_OTLP_ENDPOINT",
    "unrar_path": "UNRAR_PATH",
}


def add_config_arguments(parser: argparse.ArgumentParser) -> None:
    for dest, env_name in ENV_FLAG_MAP.items():
        flag = "--" + dest.replace("_", "-")
        parser.add_argument(flag, dest=dest, default=None, metavar="VALUE",
                            help=f"Sets {env_name}.")
    parser.add_argument("--env-file", dest="env_file", default=None, metavar="PATH",
                        help="Load fallback values from this file instead of ./.env.")


def apply_config(args: argparse.Namespace) -> None:
    for dest, env_name in ENV_FLAG_MAP.items():
        value = getattr(args, dest, None)
        if value is not None:
            os.environ[env_name] = str(value)
    # override stays False, so CLI values and the process environment win
    # over anything in the file.
    load_dotenv(dotenv_path=args.env_file)


def require_env(*names: str) -> None:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        print(f"Missing required configuration: {', '.join(missing)}", file=sys.stderr)
        print("Provide values as CLI flags, environment variables, or .env entries.",
              file=sys.stderr)
        sys.exit(2)
