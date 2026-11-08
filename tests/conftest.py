"""Test-session setup: the suite runs without Postgres, MQTT, Telegram, the media identifier or an OTEL collector."""

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.fakes import FAKE_DATABASE, FakeDatabase, fake_get_pool


def pytest_configure(config: pytest.Config) -> None:
    """Prepare the environment and the database stand-in before any ``src`` module is imported.

    Several ``src`` modules read the environment and build repositories (which borrow pool connections) at import
    time, and they bind ``get_pool`` when imported, so both must be in place before test collection starts.
    """
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["OTEL_SDK_DISABLED"] = "true"
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://127.0.0.1:4318"
    os.environ["MQTT_HOST"] = "127.0.0.1"
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_API_BASE", "API_URL"):
        os.environ.pop(name, None)

    root = Path(tempfile.mkdtemp(prefix="smo-tests-"))
    for name, folder in (("WATCH_FOLDER", "watch"), ("MOVIES_BASE_FOLDER", "movies"), ("SERIES_BASE_FOLDER", "series")):
        (root / folder).mkdir()
        os.environ[name] = str(root / folder)

    import raccoontools_db

    setattr(raccoontools_db, "get_pool", fake_get_pool)


@pytest.fixture(autouse=True)
def fake_db() -> Iterator[FakeDatabase]:
    """Give each test an empty fake database: no recorded statements, no queued results."""
    FAKE_DATABASE.reset()
    yield FAKE_DATABASE
    FAKE_DATABASE.reset()
