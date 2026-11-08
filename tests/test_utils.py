import pytest
from opentelemetry.util.http import parse_excluded_urls

from src.utils import (
    child_process_env,
    get_telegram_api_base,
    instrumentation_options,
    telegram_excluded_urls,
    to_bool_env,
    to_int,
)


def test_child_process_env_passes_only_the_allowed_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("INFISICAL_MEDIA_ORGANIZER_CLIENT_SECRET", "bootstrap-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")

    env = child_process_env()

    assert env["PATH"] == "/usr/bin"
    assert env["LANG"] == "C.UTF-8"
    assert set(env) <= {"PATH", "LANG", "LC_ALL", "HOME"}


def test_get_telegram_api_base_defaults_to_public_api() -> None:
    assert get_telegram_api_base() == "https://api.telegram.org"


def test_get_telegram_api_base_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_API_BASE", "https://telegram-proxy.lan:8443/")

    assert get_telegram_api_base() == "https://telegram-proxy.lan:8443"


@pytest.mark.parametrize(
    "api_base",
    ["http://api.telegram.org", "https://user:pass@api.telegram.org", "https://api.telegram.org?x=1", "https://"],
)
def test_get_telegram_api_base_rejects_unsafe_urls(monkeypatch: pytest.MonkeyPatch, api_base: str) -> None:
    monkeypatch.setenv("TELEGRAM_API_BASE", api_base)

    with pytest.raises(ValueError, match="TELEGRAM_API_BASE"):
        get_telegram_api_base()


def test_telegram_excluded_urls_matches_bot_urls_only() -> None:
    excluded = parse_excluded_urls(telegram_excluded_urls("https://api.telegram.org"))

    assert excluded.url_disabled("https://api.telegram.org/bot123:abc/sendMessage")
    assert not excluded.url_disabled("http://media-identifier:8080/api/guess?it=api.telegram.org")
    assert not excluded.url_disabled("https://apiXtelegram.org/bot123/sendMessage")


def test_instrumentation_options_exclude_telegram_from_requests_tracing() -> None:
    options = instrumentation_options()

    assert options["instrument_db"] == {"psycopg": {"enable_commenter": True}}
    assert options["instrument_requests"] == {"excluded_urls": telegram_excluded_urls("https://api.telegram.org")}


@pytest.mark.parametrize(("value", "expected"), [("7", 7), (3, 3), (None, 5), ("x", 5)])
def test_to_int_falls_back_to_default(value: str | int | None, expected: int) -> None:
    assert to_int(value, 5) == expected


@pytest.mark.parametrize(("raw", "expected"), [("true", True), (" YES ", True), ("0", False), ("off", False)])
def test_to_bool_env_parses_common_spellings(monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    monkeypatch.setenv("SMO_TEST_FLAG", raw)

    assert to_bool_env("SMO_TEST_FLAG", not expected) is expected
