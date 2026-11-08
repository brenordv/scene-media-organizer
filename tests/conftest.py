"""Test-session setup: the suite runs without Postgres, MQTT, Telegram, Infisical, the media identifier or an OTEL
collector."""

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.fakes import FAKE_DATABASE, TEST_SECRETS, FakeDatabase, fake_get_pool


def pytest_configure(config: pytest.Config) -> None:
    """Prepare the environment, the settings and the database stand-in before any ``src`` module is imported.

    Several ``src`` modules read the environment and the settings, and build repositories (which borrow pool
    connections), at import time; they also bind ``get_pool`` when imported. All of it must be in place before test
    collection starts.
    """
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["OTEL_SDK_DISABLED"] = "true"
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://127.0.0.1:4318"
    for name in ("TELEGRAM_API_BASE", "ACTIVITY_DB_LOG_LEVEL", "INFISICAL_ENV"):
        os.environ.pop(name, None)

    root = Path(tempfile.mkdtemp(prefix="smo-tests-"))
    for name, folder in (("WATCH_FOLDER", "watch"), ("MOVIES_BASE_FOLDER", "movies"), ("SERIES_BASE_FOLDER", "series")):
        (root / folder).mkdir()
        os.environ[name] = str(root / folder)

    import raccoontools_db

    from src import secret_store

    for name in secret_store.REQUIRED_BOOTSTRAP_VARIABLES:
        os.environ.pop(name, None)
    secret_store._default_store = secret_store.SecretStore(dict(TEST_SECRETS).get, "test secrets")
    setattr(raccoontools_db, "get_pool", fake_get_pool)


@pytest.fixture(autouse=True)
def fake_db() -> Iterator[FakeDatabase]:
    """Give each test an empty fake database: no recorded statements, no queued results."""
    FAKE_DATABASE.reset()
    yield FAKE_DATABASE
    FAKE_DATABASE.reset()


@pytest.fixture(autouse=True)
def secret_values(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Serve the Infisical settings from a fresh copy of ``TEST_SECRETS``.

    The store caches what it reads, so a test changes or removes a value before the code under test reads it.
    """
    from src import secret_store

    values = dict(TEST_SECRETS)
    monkeypatch.setattr(secret_store, "_default_store", secret_store.SecretStore(values.get, "test secrets"))
    return values
