import psycopg
import pytest

from src.data import db as db_module
from src.data.db import POSTGRES_CONNECTION_STRING_SECRET, build_conninfo, probe_database
from src.secret_store import SecretsError
from tests.fakes import FakeDatabase

PASSWORD = "s3cr3t-p@ss"


@pytest.mark.parametrize(
    "conninfo",
    [
        "postgresql://media_organizer:pw@db.lan:5432/smo_watchdog",
        "host=db.lan port=5432 dbname=smo_watchdog user=media_organizer password='pass with spaces'",
    ],
)
def test_build_conninfo_accepts_uri_and_key_value_forms(secret_values: dict[str, str], conninfo: str) -> None:
    secret_values[POSTGRES_CONNECTION_STRING_SECRET] = conninfo

    assert build_conninfo() == conninfo


@pytest.mark.parametrize(
    "conninfo",
    [
        "postgresql://media_organizer:pw@db.lan:5432",
        f"postgresql://media_organizer:{PASSWORD}@db.lan:5432/smo_watchdog",
        f"host=db.lan dbname=smo_watchdog password='{PASSWORD}",
    ],
)
def test_build_conninfo_invalid_string_raises_without_quoting_it(secret_values: dict[str, str], conninfo: str) -> None:
    secret_values[POSTGRES_CONNECTION_STRING_SECRET] = conninfo

    with pytest.raises(ValueError) as raised:
        build_conninfo()

    assert POSTGRES_CONNECTION_STRING_SECRET in str(raised.value)
    assert PASSWORD not in str(raised.value)
    assert raised.value.__context__ is None


def test_build_conninfo_missing_secret_raises(secret_values: dict[str, str]) -> None:
    del secret_values[POSTGRES_CONNECTION_STRING_SECRET]

    with pytest.raises(SecretsError, match=POSTGRES_CONNECTION_STRING_SECRET):
        build_conninfo()


def test_probe_database_runs_select_one(fake_db: FakeDatabase) -> None:
    assert probe_database() is True
    assert fake_db.queries() == ["SELECT 1"]


def test_probe_database_failure_returns_false(monkeypatch: pytest.MonkeyPatch, fake_db: FakeDatabase) -> None:
    fake_db.fail_with = psycopg.OperationalError("connection refused")

    assert probe_database() is False


def test_probe_database_without_a_pool_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_pool() -> None:
        raise RuntimeError("Connection pool not initialized")

    monkeypatch.setattr(db_module, "get_pool", no_pool)

    assert probe_database() is False
