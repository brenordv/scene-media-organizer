"""Startup steps that run before the connection pool exists: read the settings, then migrate the database."""

import sys

import psycopg
from simple_log_factory_ext_otel import TracedLogger

from src.data.db import build_conninfo
from src.data.migrations import MigrationError, apply_migrations, describe_migration_failure
from src.secret_store import SecretsError
from src.settings import load_settings
from src.utils import flush_all_otel_loggers

SETTINGS_ERROR_EXIT_CODE = 2
MIGRATION_ERROR_EXIT_CODE = 1


def prepare_services(logger: TracedLogger) -> None:
    """Read every Infisical setting and bring the database schema up to date, or exit.

    The process exits rather than raising because nothing can run without either step: code 2 for a missing or
    invalid setting, code 1 when migrating fails. The message goes to stderr and, flushed, to the OTEL logger.

    Args:
        logger: An OTEL-only logger; the activity table may not exist yet.

    Raises:
        SystemExit: When a setting is missing or invalid, or a migration cannot be applied.
    """
    try:
        load_settings(logger)
    except (SecretsError, ValueError) as e:
        _stop(logger, f"Settings error: {e}", SETTINGS_ERROR_EXIT_CODE)

    try:
        apply_migrations(build_conninfo(), logger)
    except (MigrationError, psycopg.Error) as e:
        _stop(logger, f"Migration failed: {describe_migration_failure(e)}", MIGRATION_ERROR_EXIT_CODE)


def _stop(logger: TracedLogger, message: str, exit_code: int) -> None:
    logger.error(message)
    print(message, file=sys.stderr)
    flush_all_otel_loggers()
    sys.exit(exit_code)
