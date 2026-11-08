"""Test doubles for the PostgreSQL pool: every statement is recorded and answered from a queue of results."""

from collections import deque
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

Row = tuple[Any, ...]
ACTIVITY_INSERT_PREFIX = "INSERT INTO activity_tracker"


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
