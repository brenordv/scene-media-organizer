from typing import Any

import psycopg
import pytest

from src import startup as startup_module
from src.data.db import POSTGRES_CONNECTION_STRING_SECRET
from src.data.migrations import MigrationError
from src.settings import MQTT_HOST_SECRET
from src.startup import prepare_services
from tests.fakes import RecordingLogger, as_traced


@pytest.fixture
def migrations(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_apply(conninfo: str, logger: Any) -> list[Any]:
        calls.append(conninfo)
        return []

    monkeypatch.setattr(startup_module, "apply_migrations", fake_apply)
    return calls


def test_prepare_services_reads_settings_then_migrates(migrations: list[str]) -> None:
    prepare_services(as_traced(RecordingLogger()))

    assert migrations == ["postgresql://media_organizer:test-password@db.test:5432/smo_watchdog"]


@pytest.mark.parametrize(
    ("option", "euid", "warned"), [("true", 1000, True), ("false", 1000, False), ("true", 0, False)]
)
def test_prepare_services_warns_only_when_ownership_option_is_on_without_root(
    migrations: list[str], monkeypatch: pytest.MonkeyPatch, option: str, euid: int, warned: bool
) -> None:
    monkeypatch.setenv("WATCHDOG_CHANGE_DEST_OWNERSHIP_ON_COPY", option)
    monkeypatch.setattr("os.geteuid", lambda: euid, raising=False)
    logger = RecordingLogger()

    prepare_services(as_traced(logger))

    warnings = [text for level, text in logger.lines if level == "warning" and "OWNERSHIP_ON_COPY" in text]
    assert bool(warnings) is warned


def test_prepare_services_missing_setting_exits_2_before_migrating(
    secret_values: dict[str, str], migrations: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    del secret_values[MQTT_HOST_SECRET]
    logger = RecordingLogger()

    with pytest.raises(SystemExit) as exc:
        prepare_services(as_traced(logger))

    assert exc.value.code == 2
    assert migrations == []
    assert MQTT_HOST_SECRET in capsys.readouterr().err
    assert logger.lines[-1][0] == "error"


def test_prepare_services_invalid_connection_string_exits_2_without_echoing_it(
    secret_values: dict[str, str], migrations: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    secret_values[POSTGRES_CONNECTION_STRING_SECRET] = "postgresql://media_organizer:hunter2@db.test:5432"

    with pytest.raises(SystemExit) as exc:
        prepare_services(as_traced(RecordingLogger()))

    stderr = capsys.readouterr().err
    assert exc.value.code == 2
    assert migrations == []
    assert POSTGRES_CONNECTION_STRING_SECRET in stderr
    assert "hunter2" not in stderr


def test_prepare_services_invalid_activity_level_exits_2(
    monkeypatch: pytest.MonkeyPatch, migrations: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ACTIVITY_DB_LOG_LEVEL", "VERBOSE")

    with pytest.raises(SystemExit) as exc:
        prepare_services(as_traced(RecordingLogger()))

    assert exc.value.code == 2
    assert migrations == []
    assert "ACTIVITY_DB_LOG_LEVEL" in capsys.readouterr().err


@pytest.mark.parametrize(
    "error",
    [MigrationError("Migration 0002_x failed and was rolled back: boom"), psycopg.OperationalError("refused")],
)
def test_prepare_services_migration_failure_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    def failing_apply(conninfo: str, logger: Any) -> list[Any]:
        raise error

    monkeypatch.setattr(startup_module, "apply_migrations", failing_apply)
    logger = RecordingLogger()

    with pytest.raises(SystemExit) as exc:
        prepare_services(as_traced(logger))

    assert exc.value.code == 1
    assert capsys.readouterr().err.startswith("Migration failed:")
    assert logger.lines[-1][0] == "error"
    assert "test-password" not in logger.lines[-1][1]
