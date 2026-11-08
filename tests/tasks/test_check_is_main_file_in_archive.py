import pytest

from src.tasks.check_is_main_file_in_archive import is_main_archive_file


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("example.rar", True),
        ("/watch/Show.S01/example.RAR", True),
        ("example.R00", False),
        ("example.r01", False),
        ("example.part01.rar", False),
        ("backup.zip", True),
        ("backup.z01", False),
        ("backup.zip.001", False),
        ("backup.part02.zip", False),
        ("data.7z", True),
        ("data.7z.001", False),
        ("data.part1.7z", False),
        ("archive.tar", True),
        ("archive.tar.gz", True),
        ("archive.tgz", True),
        ("archive.tar.gz.002", False),
        ("stuff.tbz2", True),
        ("stuff.tbz2.003", False),
        ("stuff.txz", True),
        ("single.gz", True),
        ("single.bz2", True),
        ("single.xz", True),
        ("document.txt", False),
        ("movie.mkv", False),
    ],
)
def test_is_main_archive_file_recognizes_first_volumes_only(path: str, expected: bool) -> None:
    assert is_main_archive_file(path) is expected
