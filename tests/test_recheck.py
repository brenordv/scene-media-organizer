from pathlib import Path
from typing import Any

import pytest

from src.recheck import RecheckChange, format_report, library_location, recheck_filed_items, run_recheck
from src.tasks.identify_file import IdentifierError, IdentifierUnavailableError

MOVIES = Path("/movies")
SERIES = Path("/series")


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("/movies/The-Matrix--1999", "The-Matrix--1999"),
        ("/series/Slow-Horses/Season06", "Slow-Horses/Season06"),
        ("D:\\media\\series\\Slow-Horses\\Season06", "Slow-Horses/Season06"),
        (Path("/movies/Heat--1995"), "Heat--1995"),
    ],
)
def test_library_location_keeps_the_identifying_folders(folder: str | Path, expected: str) -> None:
    assert library_location(folder) == expected


def _item(filename: str, target_path: str) -> dict[str, Any]:
    return {"filename": filename, "target_path": target_path}


class _Identifier:
    def __init__(self, answers: dict[str, dict[str, Any] | None | Exception]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def __call__(self, filename: str) -> dict[str, Any] | None:
        self.asked.append(filename)
        answer = self.answers[filename]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_recheck_sorts_items_into_unchanged_changed_unidentified_and_failed() -> None:
    identify = _Identifier(
        {
            "matrix.mkv": {"id": "1", "media_type": "movie", "title": "The Matrix", "year": 1999},
            "flesh.tx.mkv": {"id": "2", "media_type": "tv", "title": "Slow Horses", "season": 6},
            "rom.iso": None,
            "broken.mkv": IdentifierError("Media identifier failed (HTTP 500)"),
        }
    )
    items = [
        _item("matrix.mkv", "/movies/The-Matrix--1999"),
        _item("flesh.tx.mkv", "/movies/Flesh-TX--2023"),
        _item("rom.iso", "/movies/Some-Game--2001"),
        _item("broken.mkv", "/movies/Broken"),
    ]

    report = recheck_filed_items(items, identify, MOVIES, SERIES)

    assert report.unchanged == 1
    assert report.changed == [RecheckChange("flesh.tx.mkv", "Flesh-TX--2023", "Slow-Horses/Season06")]
    assert report.not_identified == ["rom.iso"]
    assert report.errors == ["broken.mkv"]
    assert report.stopped_early is False
    assert identify.asked == ["matrix.mkv", "flesh.tx.mkv", "rom.iso", "broken.mkv"]


def test_recheck_stops_when_the_identifier_is_unavailable() -> None:
    identify = _Identifier(
        {
            "a.mkv": {"id": "1", "media_type": "movie", "title": "A", "year": 2000},
            "b.mkv": IdentifierUnavailableError("Media identifier unavailable (HTTP 503)"),
        }
    )
    items = [_item("a.mkv", "/movies/A--2000"), _item("b.mkv", "/movies/B"), _item("c.mkv", "/movies/C")]

    report = recheck_filed_items(items, identify, MOVIES, SERIES)

    assert report.unchanged == 1
    assert report.stopped_early is True
    assert identify.asked == ["a.mkv", "b.mkv"]


def test_recheck_unplaceable_answer_is_reported_as_changed() -> None:
    identify = _Identifier({"x.mkv": {"id": "1", "media_type": "unknown", "title": "X"}})

    report = recheck_filed_items([_item("x.mkv", "/movies/X")], identify, MOVIES, SERIES)

    assert report.changed[0].now.startswith("unplaceable: media type")


def test_format_report_lists_every_finding() -> None:
    identify = _Identifier({"a.mkv": None, "b.mkv": IdentifierUnavailableError("down")})
    report = recheck_filed_items([_item("a.mkv", "/movies/A"), _item("b.mkv", "/movies/B")], identify, MOVIES, SERIES)

    text = format_report(report)

    assert "Not identified any more: 1" in text
    assert "a.mkv: no longer identified" in text
    assert "Stopped early" in text


@pytest.mark.parametrize(
    ("answer", "expected_exit_code"),
    [({"id": "1", "media_type": "movie", "title": "A", "year": 2000}, 0), (IdentifierUnavailableError("down"), 1)],
)
def test_run_recheck_exit_code_tells_whether_every_item_was_checked(
    capsys: pytest.CaptureFixture[str], answer: dict[str, Any] | Exception, expected_exit_code: int
) -> None:
    identify = _Identifier({"a.mkv": answer})

    exit_code = run_recheck([_item("a.mkv", "/movies/A--2000")], identify, MOVIES, SERIES)

    assert exit_code == expected_exit_code
    assert "Unchanged:" in capsys.readouterr().out


def test_format_report_lists_changes_and_failures() -> None:
    identify = _Identifier(
        {
            "flesh.tx.mkv": {"id": "2", "media_type": "tv", "title": "Slow Horses", "season": 6},
            "broken.mkv": IdentifierError("Media identifier failed (HTTP 500)"),
        }
    )
    items = [_item("flesh.tx.mkv", "/movies/Flesh-TX--2023"), _item("broken.mkv", "/movies/Broken")]

    text = format_report(recheck_filed_items(items, identify, MOVIES, SERIES))

    assert "  flesh.tx.mkv: Flesh-TX--2023 -> Slow-Horses/Season06" in text
    assert "  broken.mkv: identification failed" in text
    assert "Stopped early" not in text
