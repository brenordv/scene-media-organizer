from pathlib import Path

import pytest

from src.tasks.check_for_file_stability import check_is_file_stable


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)


def test_check_is_file_stable_unchanged_size_returns_true(tmp_path: Path) -> None:
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"video")

    assert check_is_file_stable(str(path)) is True


def test_check_is_file_stable_growing_file_returns_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"v")

    def grow(seconds: float) -> None:
        with path.open("ab") as handle:
            handle.write(b"v")

    monkeypatch.setattr("time.sleep", grow)

    assert check_is_file_stable(str(path)) is False


def test_check_is_file_stable_file_removed_while_waiting_returns_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"video")
    monkeypatch.setattr("time.sleep", lambda seconds: path.unlink(missing_ok=True))

    assert check_is_file_stable(str(path)) is False


def test_check_is_file_stable_missing_file_returns_false(tmp_path: Path) -> None:
    assert check_is_file_stable(str(tmp_path / "missing.mkv")) is False
