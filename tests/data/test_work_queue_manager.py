from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from src.data.work_queue_manager import WorkQueueManager
from tests.fakes import FakeDatabase

NOW = datetime.now(UTC)


def _work_row(item_id: str = "item-1", status: str = "WORKING", attempts: int = 1) -> tuple[Any, ...]:
    return (item_id, "/watch/a.mkv", "a.mkv", "/watch", None, status, False, False, NOW, NOW, None, attempts)


@pytest.fixture
def manager(fake_db: FakeDatabase) -> WorkQueueManager:
    created = WorkQueueManager()
    fake_db.reset()
    return created


def _statements(fake_db: FakeDatabase, prefix: str) -> list[tuple[str, Any]]:
    return [(" ".join(query.split()), params) for query, params in fake_db.executed if query.strip().startswith(prefix)]


def test_add_to_queue_returns_the_new_id(manager: WorkQueueManager, fake_db: FakeDatabase) -> None:
    fake_db.queue_result([("new-id",)])

    new_id = manager.add_to_queue("/watch/a.mkv", "a.mkv", "/watch", None, "PENDING", False, False, None)

    inserts = _statements(fake_db, "INSERT INTO work_queue")
    assert new_id == "new-id"
    assert inserts[0][1] == ("/watch/a.mkv", "a.mkv", "/watch", None, "PENDING", False, False, None)


def test_add_to_queue_database_error_raises_runtime_error(manager: WorkQueueManager, fake_db: FakeDatabase) -> None:
    fake_db.fail_with = psycopg.OperationalError("down")

    with pytest.raises(RuntimeError, match="Error adding /watch/a.mkv"):
        manager.add_to_queue("/watch/a.mkv", "a.mkv", "/watch", None, "PENDING", False, False, None)


def test_update_sets_every_column_except_identity_and_timestamps(
    manager: WorkQueueManager, fake_db: FakeDatabase
) -> None:
    item = {"id": "item-1", "status": "DONE", "target_path": "/movies/A", "created_at": NOW, "modified_at": NOW}

    manager.update(item)

    query, params = _statements(fake_db, "UPDATE work_queue")[0]
    assert query == "UPDATE work_queue SET status = %s, target_path = %s WHERE id = %s"
    assert params == ["DONE", "/movies/A", "item-1"]


def test_get_next_batch_with_batch_in_progress_returns_empty(manager: WorkQueueManager, fake_db: FakeDatabase) -> None:
    fake_db.queue_result([("batch-1", "item-9", True, False, NOW, NOW)])

    batch, batch_id = manager.get_next_batch(batch_id="batch-1")

    assert batch == []
    assert batch_id == "batch-1"


def test_get_next_batch_claims_pending_items_into_a_new_batch(manager: WorkQueueManager, fake_db: FakeDatabase) -> None:
    fake_db.queue_result([])
    fake_db.queue_result([_work_row("item-1"), _work_row("item-2")])

    batch, batch_id = manager.get_next_batch()

    memberships = _statements(fake_db, "INSERT INTO batch_control")
    assert [item["id"] for item in batch] == ["item-1", "item-2"]
    assert batch_id is not None
    assert [params for _, params in memberships] == [(batch_id, "item-1"), (batch_id, "item-2")]


def test_get_next_batch_resume_skips_items_already_in_the_batch(
    manager: WorkQueueManager, fake_db: FakeDatabase
) -> None:
    fake_db.queue_result([_work_row("item-1"), _work_row("item-2")])
    fake_db.queue_result([("item-1",)])

    batch, batch_id = manager.get_next_batch(force_new_batch=True, reuse_batch_id="batch-7")

    memberships = _statements(fake_db, "INSERT INTO batch_control")
    assert batch_id == "batch-7"
    assert len(batch) == 2
    assert [params for _, params in memberships] == [("batch-7", "item-2")]


def test_find_resumable_batch_closes_older_open_batches(manager: WorkQueueManager, fake_db: FakeDatabase) -> None:
    older = ("batch-old", NOW - timedelta(hours=2), NOW - timedelta(hours=1), 3)
    newer = ("batch-new", NOW - timedelta(minutes=5), NOW, 2)
    fake_db.queue_result([older, newer])

    resume = manager.find_resumable_batch()

    closes = _statements(fake_db, "UPDATE batch_control")
    assert resume is not None
    assert resume["batch_id"] == "batch-new"
    assert resume["item_count"] == 2
    assert [params for _, params in closes] == [("batch-old",)]


def test_find_resumable_batch_nothing_open_returns_none(manager: WorkQueueManager) -> None:
    assert manager.find_resumable_batch() is None


def test_move_working_items_back_to_pending_scopes_both_updates_to_the_batch(
    manager: WorkQueueManager, fake_db: FakeDatabase
) -> None:
    manager.move_working_items_back_to_pending("batch-1")

    updates = _statements(fake_db, "UPDATE work_queue")
    assert "attempts >= %s" in updates[0][0]
    assert updates[0][1] == (5, "batch-1")
    assert updates[1][1] == ("batch-1",)


def test_release_interrupted_items_caps_then_refunds(manager: WorkQueueManager, fake_db: FakeDatabase) -> None:
    fake_db.queue_result([("stuck.mkv",)])

    manager.release_interrupted_items()

    updates = _statements(fake_db, "UPDATE work_queue")
    assert "FAILED_MAX_ATTEMPTS" in updates[0][0]
    assert "GREATEST(attempts - 1, 0)" in updates[1][0]


def test_filter_only_existing_filenames_returns_known_names(manager: WorkQueueManager, fake_db: FakeDatabase) -> None:
    fake_db.queue_result([("a.mkv",)])

    assert manager.filter_only_existing_filenames(["a.mkv", "b.mkv"]) == ["a.mkv"]
    assert fake_db.executed[-1][1] == (["a.mkv", "b.mkv"],)
