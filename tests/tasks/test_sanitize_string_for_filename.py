import pytest

from src.tasks.sanitize_string_for_filename import sanitize_string_for_filename


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("the matrix", "The-matrix"),
        ("Tom & Jerry", "Tom-and-Jerry"),
        ("Mission: Impossible", "Mission-Impossible"),
        ("What's Up, Doc?", "Whats-Up_-Doc"),
        ("AC/DC <Live>", "AC_DC-_Live"),
        ("...hidden.", "Hidden"),
        ("Amélie", "Amélie"),
    ],
)
def test_sanitize_string_for_filename_replaces_unsafe_characters(title: str, expected: str) -> None:
    assert sanitize_string_for_filename(title) == expected


@pytest.mark.parametrize("title", ["", "???", "...", "'\"`:"])
def test_sanitize_string_for_filename_nothing_usable_returns_empty(title: str) -> None:
    assert sanitize_string_for_filename(title) == ""
