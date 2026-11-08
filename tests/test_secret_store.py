import traceback
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from infisical_sdk.infisical_requests import APIError

from src import secret_store
from src.secret_store import (
    REQUIRED_BOOTSTRAP_VARIABLES,
    InfisicalBootstrap,
    InfisicalFetcher,
    SecretsError,
    SecretStore,
    get_secret_store,
)

CLIENT_ID = "machine-client-id"
CLIENT_SECRET = "machine-client-secret-value"
BOT_TOKEN = "123456:bot-token-value"
REQUIRED_BOOTSTRAP_ENV = {
    "INFISICAL_HOST": "https://infisical.example",
    "INFISICAL_MEDIA_ORGANIZER_CLIENT_ID": f" {CLIENT_ID}\n",
    "INFISICAL_MEDIA_ORGANIZER_CLIENT_SECRET": f"{CLIENT_SECRET}\n",
    "INFISICAL_MEDIA_ORGANIZER_PROJECT_ID": "project-42",
}
BOOTSTRAP_ENV = {**REQUIRED_BOOTSTRAP_ENV, "INFISICAL_ENV": "prod"}
BOOTSTRAP = InfisicalBootstrap.from_env(BOOTSTRAP_ENV)


class FakeInfisical:
    """Replaces ``InfisicalSDKClient``: records construction, logins and reads, and answers from a dict.

    ``values`` is keyed by (environment, name); a pair it lacks answers like Infisical does for a secret that does not
    exist (HTTP 404).
    """

    def __init__(self, values: dict[tuple[str, str], str | Exception], login_error: Exception | None = None) -> None:
        self.values = values
        self.login_error = login_error
        self.constructed: list[dict[str, Any]] = []
        self.logins: list[dict[str, str]] = []
        self.reads: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> SimpleNamespace:
        self.constructed.append(kwargs)
        return SimpleNamespace(
            auth=SimpleNamespace(universal_auth=SimpleNamespace(login=self._login)),
            secrets=SimpleNamespace(get_secret_by_name=self._get_secret_by_name),
        )

    def _login(self, **kwargs: str) -> None:
        self.logins.append(kwargs)
        if self.login_error is not None:
            raise self.login_error

    def _get_secret_by_name(self, **kwargs: Any) -> SimpleNamespace:
        self.reads.append(kwargs)
        key = (kwargs["environment_slug"], kwargs["secret_name"])
        outcome = self.values.get(key, APIError("Secret not found", 404, {}))
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(secretValue=outcome)


class RecordingFetch:
    """A fetch callable that answers from a dict and records every name it is asked for."""

    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.requested: list[str] = []

    def __call__(self, name: str) -> str | None:
        self.requested.append(name)
        return self.values.get(name)


def _use_infisical(monkeypatch: pytest.MonkeyPatch, fake: FakeInfisical) -> FakeInfisical:
    monkeypatch.setattr(secret_store, "InfisicalSDKClient", fake)
    return fake


def _rendered(error: BaseException) -> str:
    return "".join(traceback.format_exception(error))


def test_from_env_reads_and_trims_every_bootstrap_variable() -> None:
    bootstrap = InfisicalBootstrap.from_env(BOOTSTRAP_ENV)

    assert (bootstrap.host, bootstrap.client_id, bootstrap.client_secret, bootstrap.project_id, bootstrap.env_slug) == (
        "https://infisical.example",
        CLIENT_ID,
        CLIENT_SECRET,
        "project-42",
        "prod",
    )


def test_from_env_bare_host_defaults_to_https() -> None:
    bootstrap = InfisicalBootstrap.from_env({**BOOTSTRAP_ENV, "INFISICAL_HOST": "secrets.example.lan:8080"})

    assert bootstrap.host == "https://secrets.example.lan:8080"
    assert bootstrap.uses_plain_http is False


def test_from_env_keeps_an_explicit_http_scheme_and_flags_it() -> None:
    bootstrap = InfisicalBootstrap.from_env({**BOOTSTRAP_ENV, "INFISICAL_HOST": "http://secrets.example.lan:8080"})

    assert bootstrap.host == "http://secrets.example.lan:8080"
    assert bootstrap.uses_plain_http is True


@pytest.mark.parametrize("environ", [REQUIRED_BOOTSTRAP_ENV, {**REQUIRED_BOOTSTRAP_ENV, "INFISICAL_ENV": "  "}])
def test_from_env_missing_or_blank_infisical_env_reads_the_dev_environment(environ: dict[str, str]) -> None:
    assert InfisicalBootstrap.from_env(environ).env_slug == "dev"


def test_from_env_names_every_missing_variable_at_once() -> None:
    with pytest.raises(SecretsError) as raised:
        InfisicalBootstrap.from_env({})

    assert all(name in str(raised.value) for name in REQUIRED_BOOTSTRAP_VARIABLES)


@pytest.mark.parametrize("name", REQUIRED_BOOTSTRAP_VARIABLES)
def test_from_env_blank_variable_counts_as_missing(name: str) -> None:
    with pytest.raises(SecretsError, match=name):
        InfisicalBootstrap.from_env({**BOOTSTRAP_ENV, name: "  "})


def test_bootstrap_repr_hides_the_client_credentials() -> None:
    text = repr(BOOTSTRAP)

    assert CLIENT_ID not in text
    assert CLIENT_SECRET not in text


@pytest.mark.parametrize(
    "host",
    ["https://ops:hunter2@infisical.lan", "https://infisical.lan?token=hunter2", "https://infisical.lan#hunter2"],
)
def test_from_env_host_with_credentials_query_or_fragment_raises_without_echoing_it(host: str) -> None:
    with pytest.raises(SecretsError, match="INFISICAL_HOST must be") as raised:
        InfisicalBootstrap.from_env({**BOOTSTRAP_ENV, "INFISICAL_HOST": host})

    assert "hunter2" not in str(raised.value)


def test_source_names_host_project_path_and_environment() -> None:
    assert (
        BOOTSTRAP.source == "Infisical at https://infisical.example, project project-42, path '/', environment 'prod'"
    )


def test_store_trims_the_value() -> None:
    store = SecretStore({"MEDIA_ORGANIZER_TELEGRAM_BOT_TOKEN": f"  {BOT_TOKEN}\n"}.get, "test secrets")

    assert store.get("MEDIA_ORGANIZER_TELEGRAM_BOT_TOKEN") == BOT_TOKEN


def test_store_reads_each_secret_once() -> None:
    fetch = RecordingFetch({"MEDIA_ORGANIZER_TELEGRAM_BOT_TOKEN": BOT_TOKEN})
    store = SecretStore(fetch, "test secrets")

    store.get("MEDIA_ORGANIZER_TELEGRAM_BOT_TOKEN")
    store.get("MEDIA_ORGANIZER_TELEGRAM_BOT_TOKEN")

    assert fetch.requested == ["MEDIA_ORGANIZER_TELEGRAM_BOT_TOKEN"]


def test_store_caches_absence() -> None:
    fetch = RecordingFetch({})
    store = SecretStore(fetch, "test secrets")

    store.get_optional("MEDIA_ORGANIZER_MQTT_PASSWORD")
    store.get_optional("MEDIA_ORGANIZER_MQTT_PASSWORD")

    assert fetch.requested == ["MEDIA_ORGANIZER_MQTT_PASSWORD"]


@pytest.mark.parametrize("value", ["", "   "])
def test_store_blank_value_counts_as_absent(value: str) -> None:
    store = SecretStore({"MEDIA_ORGANIZER_MQTT_USERNAME": value}.get, "test secrets")

    assert store.get_optional("MEDIA_ORGANIZER_MQTT_USERNAME") is None


def test_store_does_not_cache_a_failed_read() -> None:
    outcomes: list[str | Exception] = [SecretsError("Infisical unavailable"), BOT_TOKEN]

    def flaky_fetch(name: str) -> str | None:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    store = SecretStore(flaky_fetch, "test secrets")
    with pytest.raises(SecretsError):
        store.get("MEDIA_ORGANIZER_TELEGRAM_BOT_TOKEN")

    assert store.get("MEDIA_ORGANIZER_TELEGRAM_BOT_TOKEN") == BOT_TOKEN


def test_store_get_absent_secret_raises_naming_the_secret_and_the_source() -> None:
    store = SecretStore(RecordingFetch({}), BOOTSTRAP.source)

    with pytest.raises(SecretsError) as raised:
        store.get("MEDIA_ORGANIZER_MQTT_HOST")

    assert "Secret 'MEDIA_ORGANIZER_MQTT_HOST' is not set in Infisical at https://infisical.example" in str(
        raised.value
    )
    assert "INFISICAL_ENV" in str(raised.value)


def test_store_repr_shows_no_values() -> None:
    store = SecretStore({"MEDIA_ORGANIZER_TELEGRAM_BOT_TOKEN": BOT_TOKEN}.get, "test secrets")
    store.get("MEDIA_ORGANIZER_TELEGRAM_BOT_TOKEN")

    assert BOT_TOKEN not in repr(store)


def test_fetcher_does_not_contact_infisical_before_the_first_read(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _use_infisical(monkeypatch, FakeInfisical({}))

    InfisicalFetcher(BOOTSTRAP)

    assert fake.constructed == []


def test_fetch_logs_in_once_with_the_cache_off_and_reads_from_the_bootstrap_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_infisical(
        monkeypatch,
        FakeInfisical(
            {("prod", "MEDIA_ORGANIZER_MQTT_HOST"): "mqtt.lan", ("prod", "MEDIA_ORGANIZER_MQTT_PORT"): "1883"}
        ),
    )
    fetcher = InfisicalFetcher(BOOTSTRAP)

    values = [fetcher.fetch("MEDIA_ORGANIZER_MQTT_HOST"), fetcher.fetch("MEDIA_ORGANIZER_MQTT_PORT")]

    assert values == ["mqtt.lan", "1883"]
    assert fake.constructed == [{"host": "https://infisical.example", "cache_ttl": None}]
    assert fake.logins == [{"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}]
    assert fake.reads[0] == {
        "secret_name": "MEDIA_ORGANIZER_MQTT_HOST",
        "project_id": "project-42",
        "environment_slug": "prod",
        "secret_path": "/",
        "include_imports": False,
    }


def test_fetch_missing_secret_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_infisical(monkeypatch, FakeInfisical({}))

    assert InfisicalFetcher(BOOTSTRAP).fetch("MEDIA_ORGANIZER_MQTT_PASSWORD") is None


@pytest.mark.parametrize(
    ("error", "described"),
    [
        (APIError("Permission denied", 403, {}), "APIError, HTTP 403"),
        (requests.ConnectionError("connection refused"), "ConnectionError"),
    ],
)
def test_fetch_failure_raises_naming_the_secret_without_credentials(
    monkeypatch: pytest.MonkeyPatch, error: Exception, described: str
) -> None:
    _use_infisical(monkeypatch, FakeInfisical({("prod", "MEDIA_ORGANIZER_MQTT_HOST"): error}))

    with pytest.raises(SecretsError) as raised:
        InfisicalFetcher(BOOTSTRAP).fetch("MEDIA_ORGANIZER_MQTT_HOST")

    assert f"Reading secret 'MEDIA_ORGANIZER_MQTT_HOST' from {BOOTSTRAP.source} failed ({described})" in str(
        raised.value
    )
    assert CLIENT_SECRET not in _rendered(raised.value)


@pytest.mark.parametrize(
    ("error", "described"),
    [
        (APIError("Invalid credentials", 401, {}), "APIError, HTTP 401"),
        (requests.ConnectionError("connection refused"), "ConnectionError"),
    ],
)
def test_login_failure_raises_naming_the_host_without_credentials(
    monkeypatch: pytest.MonkeyPatch, error: Exception, described: str
) -> None:
    fake = _use_infisical(monkeypatch, FakeInfisical({}, login_error=error))

    with pytest.raises(SecretsError) as raised:
        InfisicalFetcher(BOOTSTRAP).fetch("MEDIA_ORGANIZER_MQTT_HOST")

    assert f"Logging in to Infisical at https://infisical.example failed ({described})" in str(raised.value)
    assert fake.reads == []
    rendered = _rendered(raised.value)
    assert CLIENT_ID not in rendered
    assert CLIENT_SECRET not in rendered


def test_default_store_without_bootstrap_raises_naming_the_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in REQUIRED_BOOTSTRAP_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(secret_store, "_default_store", None)

    with pytest.raises(SecretsError, match="INFISICAL_MEDIA_ORGANIZER_CLIENT_SECRET"):
        get_secret_store()


def test_default_store_is_built_once_from_the_environment_without_contacting_infisical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in BOOTSTRAP_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(secret_store, "_default_store", None)
    fake = _use_infisical(monkeypatch, FakeInfisical({}))

    store = get_secret_store()

    assert get_secret_store() is store
    assert store.source == BOOTSTRAP.source
    assert fake.constructed == []
