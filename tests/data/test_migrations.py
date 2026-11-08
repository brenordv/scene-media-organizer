from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Self

import psycopg
import pytest

from src.data import migrations as migrations_module
from src.data.migrations import (
    MIGRATIONS_DIR,
    Migration,
    MigrationError,
    _connect_autocommit,
    apply_migrations,
    checksum_of,
    describe_migration_failure,
    discover_migrations,
    plan_migrations,
)
from tests.fakes import RecordingLogger, as_traced

PASSWORD = "s3cret-db-password"
CONNINFO = f"host=db.example port=5432 user=media_organizer password={PASSWORD} dbname=smo_watchdog"


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakeCursor:
    def __init__(self, connection: FakeMigrationConnection) -> None:
        self._connection = connection
        self.statusmessage: str | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def execute(self, query: str, params: Any = None) -> None:
        if query == self._connection.failing_sql:
            raise psycopg.errors.SyntaxError('syntax error at or near "BROKEN"')
        self._connection.cursor_statements.append((query, params))
        self.statusmessage = "INSERT 0 1" if params else "CREATE TABLE"

    def nextset(self) -> bool:
        return False


class FakeMigrationConnection:
    """Records what a migration run does; answers lock attempts from ``lock_answers`` and can fail one file's SQL."""

    def __init__(
        self,
        applied: dict[int, str] | None = None,
        lock_answers: list[bool] | None = None,
        failing_sql: str | None = None,
    ) -> None:
        self.applied = applied or {}
        self.lock_answers = list(lock_answers or [True])
        self.failing_sql = failing_sql
        self.statements: list[str] = []
        self.cursor_statements: list[tuple[str, Any]] = []
        self.transactions: list[str] = []
        self.closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.closed = True

    def execute(self, query: str, params: Any = None) -> FakeResult:
        self.statements.append(query)
        if "pg_try_advisory_lock" in query:
            answer = self.lock_answers.pop(0) if len(self.lock_answers) > 1 else self.lock_answers[0]
            return FakeResult([(answer,)])
        if query.startswith("SELECT version, checksum"):
            return FakeResult(list(self.applied.items()))
        return FakeResult([])

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
        except BaseException:
            self.transactions.append("rollback")
            raise
        self.transactions.append("commit")

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def ledger_versions(self) -> list[int]:
        return [params[0] for query, params in self.cursor_statements if "schema_migrations" in query]


def _write(directory: Path, name: str, sql: str = "SELECT 1;") -> None:
    (directory / name).write_text(sql, encoding="utf-8")


@pytest.fixture
def migrations_dir(tmp_path: Path) -> Path:
    _write(tmp_path, "0002_second.sql", "ALTER TABLE t ADD COLUMN b INT;")
    _write(tmp_path, "0001_first.sql", "CREATE TABLE t (a INT);")
    return tmp_path


def test_repository_migrations_are_discovered_in_order() -> None:
    labels = [migration.label for migration in discover_migrations(MIGRATIONS_DIR)]

    assert labels == [
        "0001_baseline",
        "0002_uuid_defaults_without_extension",
        "0003_work_queue_indexes",
        "0004_work_queue_content_hash",
    ]


def test_content_hash_index_covers_done_items_and_allows_duplicates() -> None:
    migration = (MIGRATIONS_DIR / "0004_work_queue_content_hash.sql").read_text(encoding="utf-8")

    assert "ALTER TABLE work_queue ADD COLUMN IF NOT EXISTS content_hash TEXT NULL;" in migration
    assert "ON work_queue (content_hash) WHERE status = 'DONE';" in migration
    assert "UNIQUE" not in migration


def test_baseline_creates_the_tables_the_code_queries_without_an_extension() -> None:
    baseline = (MIGRATIONS_DIR / "0001_baseline.sql").read_text(encoding="utf-8")

    for table in ("work_queue", "batch_control", "activity_tracker"):
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in baseline
    assert "uuid_generate_v4" not in baseline
    assert "CREATE EXTENSION" not in baseline


def test_repository_migrations_avoid_statements_that_cannot_run_in_a_transaction() -> None:
    for migration in discover_migrations(MIGRATIONS_DIR):
        text = migration.sql.upper()
        assert "CONCURRENTLY" not in text
        assert "BEGIN;" not in text
        assert "COMMIT;" not in text


def test_discover_migrations_orders_by_version(migrations_dir: Path) -> None:
    assert [migration.version for migration in discover_migrations(migrations_dir)] == [1, 2]


@pytest.mark.parametrize("bad_name", ["1_first.sql", "0003_Mixed_Case.sql", "0003-dash.sql"])
def test_discover_migrations_rejects_misnamed_files(migrations_dir: Path, bad_name: str) -> None:
    _write(migrations_dir, bad_name)

    with pytest.raises(MigrationError, match="Invalid migration file name"):
        discover_migrations(migrations_dir)


def test_discover_migrations_rejects_duplicate_versions(migrations_dir: Path) -> None:
    _write(migrations_dir, "0002_other.sql")

    with pytest.raises(MigrationError, match="used by more than one file"):
        discover_migrations(migrations_dir)


def test_discover_migrations_missing_folder_raises(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="not found"):
        discover_migrations(tmp_path / "missing")


def test_discover_migrations_empty_folder_raises(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="No migration files"):
        discover_migrations(tmp_path)


def test_checksum_is_the_same_for_crlf_and_lf_text() -> None:
    assert checksum_of("SELECT 1;\r\nSELECT 2;\r\n") == checksum_of("SELECT 1;\nSELECT 2;\n")


def test_plan_migrations_skips_applied_versions(migrations_dir: Path) -> None:
    first, second = discover_migrations(migrations_dir)

    assert plan_migrations([first, second], {1: first.checksum}) == [second]


def test_plan_migrations_applied_file_changed_raises(migrations_dir: Path) -> None:
    migrations = discover_migrations(migrations_dir)

    with pytest.raises(MigrationError, match="0001_first changed after it was applied"):
        plan_migrations(migrations, {1: "checksum-of-the-original-file"})


def test_plan_migrations_pending_file_numbered_below_an_applied_one_raises(migrations_dir: Path) -> None:
    first, second = discover_migrations(migrations_dir)

    with pytest.raises(MigrationError, match="0001_first is pending but version 2 is already applied"):
        plan_migrations([first, second], {2: second.checksum})


def test_apply_migrations_runs_pending_files_in_order_and_records_them(migrations_dir: Path) -> None:
    connection = FakeMigrationConnection()
    logger = RecordingLogger()

    applied = apply_migrations(CONNINFO, as_traced(logger), migrations_dir, connect=lambda conninfo: connection)

    assert [migration.version for migration in applied] == [1, 2]
    assert connection.ledger_versions() == [1, 2]
    assert connection.transactions == ["commit", "commit"]
    assert "pg_try_advisory_lock" in connection.statements[0]
    assert connection.closed
    assert any("Applied migration 0001_first" in text for _, text in logger.lines)


def test_apply_migrations_with_nothing_pending_changes_nothing(migrations_dir: Path) -> None:
    migrations = discover_migrations(migrations_dir)
    connection = FakeMigrationConnection(applied={m.version: m.checksum for m in migrations})
    logger = RecordingLogger()

    applied = apply_migrations(CONNINFO, as_traced(logger), migrations_dir, connect=lambda conninfo: connection)

    assert applied == []
    assert connection.ledger_versions() == []
    assert logger.lines == [("info", "Database schema at version 2; no migrations pending.")]


def test_apply_migrations_failure_rolls_back_that_file_and_stops(migrations_dir: Path) -> None:
    failing: Migration = discover_migrations(migrations_dir)[1]
    connection = FakeMigrationConnection(failing_sql=failing.sql)

    with pytest.raises(MigrationError, match="0002_second failed and was rolled back"):
        apply_migrations(CONNINFO, as_traced(RecordingLogger()), migrations_dir, connect=lambda conninfo: connection)

    assert connection.ledger_versions() == [1]
    assert connection.transactions == ["commit", "rollback"]
    assert connection.closed


def test_apply_migrations_waits_for_a_lock_held_by_another_run(
    migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(migrations_module, "LOCK_POLL_SECONDS", 0)
    connection = FakeMigrationConnection(lock_answers=[False, False, True])
    logger = RecordingLogger()

    applied = apply_migrations(CONNINFO, as_traced(logger), migrations_dir, connect=lambda conninfo: connection)

    lock_attempts = [statement for statement in connection.statements if "pg_try_advisory_lock" in statement]
    waiting_lines = [text for _, text in logger.lines if "Waiting for another process" in text]
    assert len(lock_attempts) == 3
    assert len(waiting_lines) == 1
    assert [migration.version for migration in applied] == [1, 2]


def test_apply_migrations_lock_held_elsewhere_raises_after_waiting(migrations_dir: Path) -> None:
    connection = FakeMigrationConnection(lock_answers=[False])

    with pytest.raises(MigrationError, match="Timed out waiting"):
        apply_migrations(
            CONNINFO,
            as_traced(RecordingLogger()),
            migrations_dir,
            connect=lambda conninfo: connection,
            lock_wait_seconds=0,
        )

    assert connection.ledger_versions() == []
    assert connection.closed


def test_connect_retries_a_database_that_is_still_starting(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[dict[str, Any]] = []

    def flaky_connect(conninfo: str, **kwargs: Any) -> str:
        attempts.append(kwargs)
        if len(attempts) < 3:
            raise psycopg.OperationalError("the database system is starting up")
        return "connection"

    monkeypatch.setattr("raccoontools.decorators.retry.sleep", lambda seconds: None)
    monkeypatch.setattr("psycopg.connect", flaky_connect)

    assert _connect_autocommit(CONNINFO) == "connection"
    assert len(attempts) == 3
    assert attempts[0] == {"autocommit": True, "connect_timeout": 10}


def test_connect_gives_up_after_five_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[str] = []

    def refusing_connect(conninfo: str, **kwargs: Any) -> None:
        attempts.append(conninfo)
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr("raccoontools.decorators.retry.sleep", lambda seconds: None)
    monkeypatch.setattr("psycopg.connect", refusing_connect)

    with pytest.raises(psycopg.OperationalError):
        _connect_autocommit(CONNINFO)
    assert len(attempts) == 5


def test_describe_migration_failure_hides_connection_details() -> None:
    error = psycopg.OperationalError(f'connection to server failed: password "{PASSWORD}" rejected')

    description = describe_migration_failure(error)

    assert "OperationalError" in description
    assert PASSWORD not in description


def test_describe_migration_failure_keeps_the_migration_message() -> None:
    error = MigrationError("Migration 0002_second failed and was rolled back: syntax error")

    assert describe_migration_failure(error) == "Migration 0002_second failed and was rolled back: syntax error"
