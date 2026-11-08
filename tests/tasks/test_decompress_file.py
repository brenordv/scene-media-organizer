import bz2
import gzip
import io
import lzma
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from src.tasks.decompress_file import decompress_file


def _write_tar(path: Path, members: dict[str, bytes], mode: str = "w") -> None:
    with tarfile.open(path, mode) as archive:  # type: ignore[call-overload]
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def test_decompress_file_zip_extracts_next_to_archive(tmp_path: Path) -> None:
    archive_path = tmp_path / "release.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("movie.mkv", b"video")

    assert decompress_file(str(archive_path)) is True
    assert (tmp_path / "movie.mkv").read_bytes() == b"video"


def test_decompress_file_zip_member_with_parent_path_stays_inside(tmp_path: Path) -> None:
    extract_dir = tmp_path / "watch"
    extract_dir.mkdir()
    archive_path = extract_dir / "evil.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escaped.txt", b"nope")

    assert decompress_file(str(archive_path)) is True
    assert not (tmp_path / "escaped.txt").exists()
    assert (extract_dir / "escaped.txt").exists()


def test_decompress_file_bad_zip_returns_false(tmp_path: Path) -> None:
    archive_path = tmp_path / "broken.zip"
    archive_path.write_bytes(b"not a zip")

    assert decompress_file(str(archive_path)) is False


@pytest.mark.parametrize(("name", "mode"), [("bundle.tar", "w"), ("bundle.tar.gz", "w:gz"), ("bundle.tgz", "w:gz")])
def test_decompress_file_tar_extracts_members(tmp_path: Path, name: str, mode: str) -> None:
    archive_path = tmp_path / name
    _write_tar(archive_path, {"episode.mkv": b"episode"}, mode)

    assert decompress_file(str(archive_path)) is True
    assert (tmp_path / "episode.mkv").read_bytes() == b"episode"


def test_decompress_file_tar_member_with_parent_path_is_refused(tmp_path: Path) -> None:
    extract_dir = tmp_path / "watch"
    extract_dir.mkdir()
    archive_path = extract_dir / "evil.tar"
    _write_tar(archive_path, {"../escaped.txt": b"nope"})

    assert decompress_file(str(archive_path)) is False
    assert not (tmp_path / "escaped.txt").exists()


def test_decompress_file_tar_symlink_outside_is_refused(tmp_path: Path) -> None:
    extract_dir = tmp_path / "watch"
    extract_dir.mkdir()
    archive_path = extract_dir / "evil.tar"
    with tarfile.open(archive_path, "w") as archive:
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = str(tmp_path / "outside")
        archive.addfile(link)

    assert decompress_file(str(archive_path)) is False
    assert not (extract_dir / "link").exists()


@pytest.mark.parametrize(
    ("name", "compress"),
    [("movie.mkv.gz", gzip.compress), ("movie.mkv.bz2", bz2.compress), ("movie.mkv.xz", lzma.compress)],
)
def test_decompress_file_single_stream_writes_inner_file(tmp_path: Path, name: str, compress: Any) -> None:
    archive_path = tmp_path / name
    archive_path.write_bytes(compress(b"stream"))

    assert decompress_file(str(archive_path)) is True
    assert (tmp_path / "movie.mkv").read_bytes() == b"stream"


def test_decompress_file_corrupt_gzip_returns_false(tmp_path: Path) -> None:
    archive_path = tmp_path / "movie.mkv.gz"
    archive_path.write_bytes(b"\x1f\x8b corrupt")

    assert decompress_file(str(archive_path)) is False


def test_decompress_file_unknown_suffix_returns_false(tmp_path: Path) -> None:
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"video")

    assert decompress_file(str(path)) is False


def test_decompress_file_missing_file_returns_false(tmp_path: Path) -> None:
    assert decompress_file(str(tmp_path / "missing.zip")) is False


def test_decompress_file_rar_runs_7zz_without_credentials_in_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "release.rar"
    archive_path.write_bytes(b"Rar!")
    monkeypatch.setenv("INFISICAL_MEDIA_ORGANIZER_CLIENT_SECRET", "bootstrap-secret")
    monkeypatch.setenv("POSTGRES_PASSWORD", "db-secret")
    calls: list[dict[str, Any]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("subprocess.run", fake_run)

    assert decompress_file(str(archive_path)) is True
    assert calls[0]["command"][:3] == ["7zz", "x", "-y"]
    assert calls[0]["check"] is True
    assert "INFISICAL_MEDIA_ORGANIZER_CLIENT_SECRET" not in calls[0]["env"]
    assert "POSTGRES_PASSWORD" not in calls[0]["env"]


def test_decompress_file_rar_tool_failure_returns_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive_path = tmp_path / "release.rar"
    archive_path.write_bytes(b"Rar!")

    def failing_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.CalledProcessError(2, command)

    monkeypatch.setattr("subprocess.run", failing_run)

    assert decompress_file(str(archive_path)) is False
