import io
import shutil
from pathlib import Path
from typing import Any

import pytest

from src.tasks import copy_file as copy_file_module
from src.tasks.copy_file import CopyPermissionError, copy_file


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)


def test_copy_file_copies_into_new_destination_directory(tmp_path: Path) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    destination = tmp_path / "library" / "Movie--2020"

    assert copy_file(str(source), destination) is True
    assert (destination / "movie.mkv").read_bytes() == b"video"


def test_copy_file_missing_source_returns_false(tmp_path: Path) -> None:
    assert copy_file(str(tmp_path / "missing.mkv"), tmp_path / "library") is False


def test_copy_file_retries_after_a_failed_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    attempts: list[str] = []
    real_copy = shutil.copy

    def flaky_copy(src: str, dst: Path) -> Any:
        attempts.append(src)
        if len(attempts) == 1:
            raise OSError("share went away")
        return real_copy(src, dst)

    monkeypatch.setattr("shutil.copy", flaky_copy)

    assert copy_file(str(source), tmp_path / "library") is True
    assert len(attempts) == 2


def test_copy_file_every_attempt_fails_returns_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")

    def failing_copy(src: str, dst: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("shutil.copy", failing_copy)

    assert copy_file(str(source), tmp_path / "library") is False
    assert not (tmp_path / "library" / "movie.mkv").exists()


def test_copy_file_permission_error_raises_without_retrying(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    attempts: list[str] = []
    sleeps: list[float] = []

    def denied_copy(src: str, dst: Path) -> None:
        attempts.append(src)
        raise PermissionError(13, "Permission denied", src)

    monkeypatch.setattr("shutil.copy", denied_copy)
    monkeypatch.setattr("time.sleep", sleeps.append)

    with pytest.raises(CopyPermissionError, match="Permission denied"):
        copy_file(str(source), tmp_path / "library")

    assert len(attempts) == 1
    assert sleeps == []


def test_copy_file_unwritable_destination_folder_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")

    def denied_mkdir(self: Path, parents: bool = False, exist_ok: bool = False) -> None:
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "mkdir", denied_mkdir)

    with pytest.raises(CopyPermissionError):
        copy_file(str(source), tmp_path / "library")


def test_copy_file_denied_ownership_change_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    monkeypatch.setenv("WATCHDOG_CHANGE_DEST_OWNERSHIP_ON_COPY", "true")

    def denied_chown(dst_file: Path, src_file: str) -> None:
        raise PermissionError(1, "Operation not permitted", str(dst_file))

    monkeypatch.setattr(copy_file_module, "_change_destination_ownership", denied_chown)

    with pytest.raises(CopyPermissionError, match="Operation not permitted"):
        copy_file(str(source), tmp_path / "library")


class _FakeRsync:
    def __init__(self, exit_code: int, output: str = "sending incremental file list\n") -> None:
        self.stdout = io.StringIO(output)
        self._exit_code = exit_code

    def __enter__(self) -> _FakeRsync:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def wait(self) -> int:
        return self._exit_code


def test_copy_file_rsync_runs_without_credentials_in_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    monkeypatch.setenv("WATCHDOG_COPY_USING_RSYNC", "true")
    monkeypatch.setenv("INFISICAL_MEDIA_ORGANIZER_CLIENT_SECRET", "bootstrap-secret")
    calls: list[dict[str, Any]] = []

    def fake_popen(command: list[str], **kwargs: Any) -> _FakeRsync:
        calls.append({"command": command, **kwargs})
        return _FakeRsync(0)

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    assert copy_file(str(source), tmp_path / "library") is True
    assert calls[0]["command"][0] == "rsync"
    assert calls[0]["command"][-1] == str(tmp_path / "library" / "movie.mkv")
    assert "INFISICAL_MEDIA_ORGANIZER_CLIENT_SECRET" not in calls[0]["env"]


def test_copy_file_rsync_failure_returns_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    monkeypatch.setenv("WATCHDOG_COPY_USING_RSYNC", "true")
    monkeypatch.setattr("subprocess.Popen", lambda command, **kwargs: _FakeRsync(23))

    assert copy_file(str(source), tmp_path / "library") is False


def test_copy_file_rsync_permission_denied_raises_without_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    monkeypatch.setenv("WATCHDOG_COPY_USING_RSYNC", "true")
    denied_line = 'rsync: [sender] send_files failed to open "movie.mkv": Permission denied (13)\n'
    calls: list[list[str]] = []

    def fake_popen(command: list[str], **kwargs: Any) -> _FakeRsync:
        calls.append(command)
        return _FakeRsync(23, denied_line)

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    with pytest.raises(CopyPermissionError, match="Permission denied"):
        copy_file(str(source), tmp_path / "library")

    assert len(calls) == 1
