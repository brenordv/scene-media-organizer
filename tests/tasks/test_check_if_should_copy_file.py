import pytest

from src.tasks.check_if_should_copy_file import check_should_copy_file


@pytest.mark.parametrize(
    "extension", ["mkv", "mp4", "avi", "m4v", "mov", "ts", "wmv", "mpg", "mpeg", "webm", "srt", "ass", "sub", "idx"]
)
def test_check_should_copy_file_allows_video_and_subtitle_extensions(extension: str) -> None:
    assert check_should_copy_file(f"/watch/Movie.2020.{extension}", is_archive=False) is True


@pytest.mark.parametrize(
    "filename",
    [
        "/watch/Some.Game.nes",
        "/watch/Movie.2020.nfo",
        "/watch/Movie.2020.iso",
        "/watch/release.tar",
        "/watch/no_extension",
        "/watch/install.sh",
        "/watch/setup.EXE",
        "/watch/lib.so",
    ],
)
def test_check_should_copy_file_rejects_other_extensions(filename: str) -> None:
    assert check_should_copy_file(filename, is_archive=False) is False


def test_check_should_copy_file_extension_ignores_case() -> None:
    assert check_should_copy_file("/watch/MOVIE.2020.MKV", is_archive=False) is True


@pytest.mark.parametrize(
    ("filename", "is_archive"),
    [
        ("/watch/Movie.2020.Sample.mkv", False),
        ("/watch/sample.mkv", False),
        ("/watch/release-sample.rar", True),
    ],
)
def test_check_should_copy_file_rejects_samples(filename: str, is_archive: bool) -> None:
    assert check_should_copy_file(filename, is_archive) is False


@pytest.mark.parametrize("filename", ["/watch/release.rar", "/watch/release.r00", "/watch/release.unusual"])
def test_check_should_copy_file_allows_an_archive_whatever_its_extension(filename: str) -> None:
    assert check_should_copy_file(filename, is_archive=True) is True
