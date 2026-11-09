"""Test doubles: a PostgreSQL pool that records statements and answers from a queue, a recording logger and span."""

from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, cast

from opentelemetry import trace
from simple_log_factory_ext_otel import TracedLogger


class RecordingLogger:
    """Stands in for a ``TracedLogger``: keeps every line as ``(level, text)``; spans do nothing."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def debug(self, text: str) -> None:
        self.lines.append(("debug", text))

    def info(self, text: str) -> None:
        self.lines.append(("info", text))

    def warning(self, text: str) -> None:
        self.lines.append(("warning", text))

    def error(self, text: str) -> None:
        self.lines.append(("error", text))

    def exception(self, text: str) -> None:
        self.lines.append(("exception", text))

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        yield


def as_traced(logger: RecordingLogger) -> TracedLogger:
    """Hand a :class:`RecordingLogger` to code typed for a ``TracedLogger``."""
    return cast(TracedLogger, logger)


class RecordingSpan(trace.NonRecordingSpan):
    """A span that keeps every attribute set on it.

    It extends ``NonRecordingSpan`` because the no-op tracer, which is all the suite has with the SDK disabled, keeps a
    current span of that type active instead of replacing it.
    """

    def __init__(self) -> None:
        super().__init__(trace.INVALID_SPAN_CONTEXT)
        self.attributes: dict[str, Any] = {}

    def is_recording(self) -> bool:
        """Report that attributes are wanted."""
        return True

    def set_attribute(self, key: str, value: Any) -> None:
        """Keep one attribute."""
        self.attributes[key] = value

    def set_attributes(self, attributes: Mapping[str, Any]) -> None:
        """Keep several attributes."""
        self.attributes.update(attributes)


Row = tuple[Any, ...]
ACTIVITY_INSERT_PREFIX = "INSERT INTO activity_tracker"

TEST_SECRETS = {
    "MEDIA_ORGANIZER_POSTGRES_CONNECTION_STRING": "postgresql://media_organizer:test-password@db.test:5432/smo_watchdog",
    "MEDIA_ORGANIZER_MEDIA_IDENTIFIER_URL": "http://media-identifier.test:10147",
    "MEDIA_ORGANIZER_MQTT_HOST": "mqtt.test",
}
"""Infisical settings every test starts with: the required ones, set to values that pass validation."""


class FakeDatabase:
    """Records executed statements and answers them in order from queued results.

    Attributes:
        executed: Every ``(query, params)`` pair, in execution order.
        commits: How many times a connection committed.
        fail_with: When set, the next ``execute`` raises it (once).
    """

    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []
        self.commits = 0
        self.fail_with: Exception | None = None
        self._results: deque[list[Row]] = deque()

    def queue_result(self, rows: Sequence[Row]) -> None:
        """Answer the next ``execute`` that has no earlier queued answer with ``rows``."""
        self._results.append(list(rows))

    def reset(self) -> None:
        """Forget every statement, commit, queued result and pending failure."""
        self.executed.clear()
        self.commits = 0
        self.fail_with = None
        self._results.clear()

    def queries(self) -> list[str]:
        """Return the executed statements with their whitespace collapsed."""
        return [" ".join(query.split()) for query, _ in self.executed]

    def run(self, query: str, params: Any) -> list[Row]:
        """Record one statement and return its rows.

        Activity-log inserts never take a queued result, so a test queues answers only for the statements it is
        about, whatever the code under test logs in between.
        """
        self.executed.append((query, params))
        if self.fail_with is not None:
            error, self.fail_with = self.fail_with, None
            raise error
        if query.lstrip().startswith(ACTIVITY_INSERT_PREFIX):
            return []
        return self._results.popleft() if self._results else []


class FakeCursor:
    """A cursor whose statements go to a :class:`FakeDatabase`."""

    def __init__(self, database: FakeDatabase) -> None:
        self._database = database
        self._rows: list[Row] = []
        self.rowcount = 0

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def execute(self, query: str, params: Any = None) -> FakeCursor:
        """Run one statement."""
        self._rows = self._database.run(query, params)
        self.rowcount = len(self._rows)
        return self

    def fetchall(self) -> list[Row]:
        """Return every row of the last statement."""
        return list(self._rows)

    def fetchone(self) -> Row | None:
        """Return the first row of the last statement, or None."""
        return self._rows[0] if self._rows else None


class FakeConnection:
    """A connection whose statements go to a :class:`FakeDatabase`."""

    def __init__(self, database: FakeDatabase) -> None:
        self._database = database

    def cursor(self) -> FakeCursor:
        """Open a cursor."""
        return FakeCursor(self._database)

    def execute(self, query: str, params: Any = None) -> FakeCursor:
        """Run one statement on a fresh cursor."""
        return FakeCursor(self._database).execute(query, params)

    def commit(self) -> None:
        """Count a commit."""
        self._database.commits += 1


class FakePool:
    """A pool that hands out connections to one :class:`FakeDatabase`."""

    def __init__(self, database: FakeDatabase) -> None:
        self.database = database

    @contextmanager
    def connection(self, timeout: float | None = None) -> Iterator[FakeConnection]:
        """Borrow a connection."""
        yield FakeConnection(self.database)


FAKE_DATABASE = FakeDatabase()
FAKE_POOL = FakePool(FAKE_DATABASE)


def fake_get_pool() -> FakePool:
    """Stand-in for ``raccoontools_db.get_pool``."""
    return FAKE_POOL
