import pytest

from src.tasks.check_if_should_copy_file import check_should_copy_file


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("/watch/Movie.2020.mkv", True),
        ("/watch/Movie.2020.srt", True),
        ("/watch/Movie.2020.Sample.mkv", False),
        ("/watch/sample.mkv", False),
        ("/watch/install.sh", False),
        ("/watch/setup.EXE", False),
        ("/watch/script.py", False),
        ("/watch/lib.so", False),
    ],
)
def test_check_should_copy_file_skips_samples_and_executables(filename: str, expected: bool) -> None:
    assert check_should_copy_file(filename) is expected
