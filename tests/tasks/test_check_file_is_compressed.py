import zipfile
from pathlib import Path

import pytest

from src.tasks.check_file_is_compressed import is_compressed_file

GZIP_HEADER = b"\x1f\x8b\x08\x00\x00\x00\x00\x00"
BZIP2_HEADER = b"BZh9"
SEVEN_ZIP_HEADER = b"7z\xbc\xaf\x27\x1c"


@pytest.mark.parametrize("name", ["archive.zip", "backup.tar.gz", "data.part01.rar", "chunk.z01", "photo.r00"])
def test_is_compressed_file_archive_name_returns_true(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_bytes(b"")

    assert is_compressed_file(str(path)) is True


@pytest.mark.parametrize("header", [GZIP_HEADER, BZIP2_HEADER, SEVEN_ZIP_HEADER])
def test_is_compressed_file_magic_bytes_without_archive_name_returns_true(tmp_path: Path, header: bytes) -> None:
    path = tmp_path / "mystery.bin"
    path.write_bytes(header + b"payload")

    assert is_compressed_file(str(path)) is True


def test_is_compressed_file_zip_content_with_other_name_returns_true(tmp_path: Path) -> None:
    path = tmp_path / "renamed.dat"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("inner.txt", "hello")

    assert is_compressed_file(str(path)) is True


def test_is_compressed_file_plain_video_returns_false(tmp_path: Path) -> None:
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"\x1a\x45\xdf\xa3 matroska")

    assert is_compressed_file(str(path)) is False


def test_is_compressed_file_missing_path_returns_false(tmp_path: Path) -> None:
    assert is_compressed_file(str(tmp_path / "missing.zip")) is False
