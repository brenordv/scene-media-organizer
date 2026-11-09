import io
import shutil
from pathlib import Path
from typing import Any

import pytest

from src.tasks import copy_file as copy_file_module
from src.tasks.copy_file import PARTIAL_COPY_PREFIX, CopyPermissionError, copy_file


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


def _partial_files(folder: Path) -> list[Path]:
    return list(folder.glob(f"{PARTIAL_COPY_PREFIX}*"))


def test_copy_file_success_leaves_no_temporary_file_and_logs_no_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    library = tmp_path / "library"
    warnings: list[str] = []
    monkeypatch.setattr(copy_file_module._logger, "warning", warnings.append)

    assert copy_file(str(source), library) is True
    assert [path.name for path in library.iterdir()] == ["movie.mkv"]
    assert warnings == []


def test_copy_file_failed_copy_keeps_the_existing_file_and_removes_the_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"new content")
    library = tmp_path / "library"
    library.mkdir()
    (library / "movie.mkv").write_bytes(b"existing content")
    real_copy = shutil.copy

    def copy_then_fail(src: str, dst: Path) -> None:
        real_copy(src, dst)
        raise OSError("disk full")

    monkeypatch.setattr("shutil.copy", copy_then_fail)

    assert copy_file(str(source), library) is False
    assert (library / "movie.mkv").read_bytes() == b"existing content"
    assert _partial_files(library) == []


def test_copy_file_interrupted_by_a_failed_rename_keeps_the_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"new content")
    library = tmp_path / "library"
    library.mkdir()
    (library / "movie.mkv").write_bytes(b"existing content")

    def failing_replace(src: Path, dst: Path) -> None:
        raise OSError("cross-device link")

    monkeypatch.setattr("os.replace", failing_replace)

    assert copy_file(str(source), library) is False
    assert (library / "movie.mkv").read_bytes() == b"existing content"
    assert _partial_files(library) == []


def test_copy_file_keeps_the_source_mode_on_the_copy(tmp_path: Path) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    source.chmod(0o444)
    library = tmp_path / "library"

    assert copy_file(str(source), library) is True
    assert (library / "movie.mkv").stat().st_mode == source.stat().st_mode


def test_copy_file_temporary_file_cleanup_failure_keeps_the_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    warnings: list[str] = []
    monkeypatch.setattr(copy_file_module._logger, "warning", warnings.append)

    def failing_unlink(self: Path, missing_ok: bool = False) -> None:
        raise OSError("share went away")

    monkeypatch.setattr(Path, "unlink", failing_unlink)

    assert copy_file(str(source), tmp_path / "library") is True
    assert (tmp_path / "library" / "movie.mkv").read_bytes() == b"video"
    assert len(warnings) == 1
    assert "Could not remove the temporary file" in warnings[0]


def test_copy_file_250_byte_filename_copies(tmp_path: Path) -> None:
    source = tmp_path / f"{'x' * 246}.mkv"
    source.write_bytes(b"video")
    library = tmp_path / "library"

    assert copy_file(str(source), library) is True
    assert (library / source.name).read_bytes() == b"video"


def test_copy_file_denied_temporary_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")

    def denied_mkstemp(dir: Path, prefix: str) -> tuple[int, str]:
        raise PermissionError(13, "Permission denied", str(dir))

    monkeypatch.setattr("tempfile.mkstemp", denied_mkstemp)

    with pytest.raises(CopyPermissionError, match="Permission denied"):
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


def test_copy_file_rsync_does_not_keep_partial_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    monkeypatch.setenv("WATCHDOG_COPY_USING_RSYNC", "true")
    commands: list[list[str]] = []

    def fake_popen(command: list[str], **kwargs: Any) -> _FakeRsync:
        commands.append(command)
        return _FakeRsync(0)

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    assert copy_file(str(source), tmp_path / "library") is True
    assert "--partial" not in commands[0]
    assert "--append-verify" not in commands[0]


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
