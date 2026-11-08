"""Configuration bootstrap. Precedence: CLI flags > process environment > .env file.

Only settings that are not secret live here. Service addresses and credentials come from Infisical (``src.settings``),
whose bootstrap variables are read from the environment or the ``.env`` file.
"""

import argparse
import os
import sys

from dotenv import load_dotenv

# argparse dest -> environment variable name
ENV_FLAG_MAP = {
    "watch_folder": "WATCH_FOLDER",
    "movies_base_folder": "MOVIES_BASE_FOLDER",
    "series_base_folder": "SERIES_BASE_FOLDER",
    "mqtt_base_topic": "MQTT_BASE_TOPIC",
    "mqtt_client_id": "MQTT_CLIENT_ID",
    "otel_endpoint": "OTEL_EXPORTER_OTLP_ENDPOINT",
    "unrar_path": "UNRAR_PATH",
}

# Flags that 2.x accepted and that now live in Infisical. Their values are secrets, so they are never echoed.
REMOVED_SECRET_FLAGS = frozenset(
    {
        "--postgres-host",
        "--postgres-port",
        "--postgres-user",
        "--postgres-password",
        "--postgres-db",
        "--api-url",
        "--mqtt-host",
        "--mqtt-port",
        "--mqtt-username",
        "--mqtt-password",
        "--telegram-bot-token",
        "--telegram-chat-id",
    }
)

USAGE_ERROR_EXIT_CODE = 2


def add_config_arguments(parser: argparse.ArgumentParser) -> None:
    """Add one flag per entry of ``ENV_FLAG_MAP``, plus ``--env-file``."""
    for dest, env_name in ENV_FLAG_MAP.items():
        flag = "--" + dest.replace("_", "-")
        parser.add_argument(flag, dest=dest, default=None, metavar="VALUE", help=f"Sets {env_name}.")
    parser.add_argument(
        "--env-file",
        dest="env_file",
        default=None,
        metavar="PATH",
        help="Load fallback values from this file instead of ./.env.",
    )


def parse_command_line(parser: argparse.ArgumentParser, argv: list[str]) -> argparse.Namespace:
    """Parse the command line without ever echoing an unrecognized argument's value.

    argparse's own error quotes every unrecognized token, which for a removed secret flag would print the secret.

    Args:
        parser: The configured parser.
        argv: The arguments, without the program name.

    Returns:
        The parsed arguments.

    Raises:
        SystemExit: With code 2 when an argument is not recognized; the message names flags only.
    """
    args, unknown = parser.parse_known_args(argv)
    if not unknown:
        return args

    for token in unknown:
        flag = token.split("=", 1)[0]
        if flag in REMOVED_SECRET_FLAGS:
            print(f"{flag} is no longer a command-line flag; set it in Infisical (see readme).", file=sys.stderr)
        elif flag.startswith("-"):
            print(f"Unrecognized option: {flag}", file=sys.stderr)
    print(f"Stopped because of unrecognized arguments; see {parser.prog} --help.", file=sys.stderr)
    sys.exit(USAGE_ERROR_EXIT_CODE)


def apply_config(args: argparse.Namespace) -> None:
    """Write the flags given on the command line into the environment, then fill the rest from the ``.env`` file."""
    for dest, env_name in ENV_FLAG_MAP.items():
        value = getattr(args, dest, None)
        if value is not None:
            os.environ[env_name] = str(value)
    # override stays False, so CLI values and the process environment win
    # over anything in the file.
    load_dotenv(dotenv_path=args.env_file)


def require_env(*names: str) -> None:
    """Exit with code 2, naming every missing variable, unless all of ``names`` are set and non-empty."""
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        print(f"Missing required configuration: {', '.join(missing)}", file=sys.stderr)
        print("Provide values as CLI flags, environment variables, or .env entries.", file=sys.stderr)
        sys.exit(USAGE_ERROR_EXIT_CODE)
