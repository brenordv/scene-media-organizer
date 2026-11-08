"""Settings read from Infisical, reached with the machine-identity bootstrap taken from the environment.

Secret values, the client id and the client secret never appear in this module's reprs or exception messages.
"""

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Self
from urllib.parse import urlsplit

import requests
from infisical_sdk.client import InfisicalSDKClient
from infisical_sdk.infisical_requests import APIError, InfisicalError

REQUIRED_BOOTSTRAP_VARIABLES = (
    "INFISICAL_HOST",
    "INFISICAL_MEDIA_ORGANIZER_CLIENT_ID",
    "INFISICAL_MEDIA_ORGANIZER_CLIENT_SECRET",
    "INFISICAL_MEDIA_ORGANIZER_PROJECT_ID",
)
DEFAULT_ENV_SLUG = "dev"
SECRET_PATH = "/"
_NOT_FOUND = 404


class SecretsError(Exception):
    """A setting could not be read: the bootstrap is incomplete or invalid, Infisical failed, or it is absent.

    Messages never carry values.
    """


@dataclass(frozen=True)
class InfisicalBootstrap:
    """Where the secrets live and the Universal Auth credentials that read them.

    Attributes:
        host: Base URL of the Infisical server, with its scheme.
        client_id: Machine identity client id.
        client_secret: Machine identity client secret.
        project_id: Infisical project that holds the secrets.
        env_slug: Infisical environment, such as ``dev`` or ``prod``, that holds every secret.
    """

    host: str
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    project_id: str
    env_slug: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Self:
        """Read the bootstrap from environment variables.

        A host without a scheme gets ``https://``; an explicit scheme is kept. Without ``INFISICAL_ENV``, or with it
        blank, the ``dev`` environment is read. Every value is trimmed.

        Args:
            environ: The environment, such as ``os.environ``.

        Returns:
            The bootstrap.

        Raises:
            SecretsError: If any required bootstrap variable is missing or blank (the message names all of them), or
                ``INFISICAL_HOST`` carries credentials, a query or a fragment.
        """
        missing = [name for name in REQUIRED_BOOTSTRAP_VARIABLES if not environ.get(name, "").strip()]
        if missing:
            raise SecretsError(f"Missing Infisical bootstrap environment variable(s): {', '.join(missing)}.")

        host = environ["INFISICAL_HOST"].strip()
        if "://" not in host:
            host = f"https://{host}"
        parts = urlsplit(host)
        if "@" in parts.netloc or parts.query or parts.fragment:
            raise SecretsError("INFISICAL_HOST must be a URL without credentials, query or fragment.")

        return cls(
            host=host,
            client_id=environ["INFISICAL_MEDIA_ORGANIZER_CLIENT_ID"].strip(),
            client_secret=environ["INFISICAL_MEDIA_ORGANIZER_CLIENT_SECRET"].strip(),
            project_id=environ["INFISICAL_MEDIA_ORGANIZER_PROJECT_ID"].strip(),
            env_slug=environ.get("INFISICAL_ENV", "").strip() or DEFAULT_ENV_SLUG,
        )

    @property
    def uses_plain_http(self) -> bool:
        """Whether the client secret would travel to Infisical unencrypted."""
        return urlsplit(self.host).scheme == "http"

    @property
    def source(self) -> str:
        """Where the secrets are read from: host, project, path and environment, never a credential."""
        return (
            f"Infisical at {self.host}, project {self.project_id}, path '{SECRET_PATH}', environment '{self.env_slug}'"
        )


class InfisicalFetcher:
    """Reads secrets from one environment of one Infisical project.

    Infisical secret imports are not followed. Logs in with Universal Auth on the first read.
    """

    def __init__(self, bootstrap: InfisicalBootstrap) -> None:
        self._bootstrap = bootstrap
        self._client: InfisicalSDKClient | None = None

    def fetch(self, name: str) -> str | None:
        """Read one secret.

        Args:
            name: The secret's name.

        Returns:
            The secret's value, or None when the secret does not exist in the environment.

        Raises:
            SecretsError: If logging in or reading the secret fails.
        """
        client = self._logged_in_client()
        try:
            secret = client.secrets.get_secret_by_name(
                secret_name=name,
                project_id=self._bootstrap.project_id,
                environment_slug=self._bootstrap.env_slug,
                secret_path=SECRET_PATH,
                include_imports=False,
            )
        except (InfisicalError, requests.RequestException) as e:
            if isinstance(e, APIError) and e.status_code == _NOT_FOUND:
                return None
            raise SecretsError(
                f"Reading secret '{name}' from {self._bootstrap.source} failed ({_describe_failure(e)})."
            ) from e

        return secret.secretValue

    def _logged_in_client(self) -> InfisicalSDKClient:
        if self._client is not None:
            return self._client

        try:
            # The SDK annotates cache_ttl as int but documents None as turning its cache off; SecretStore caches.
            client = InfisicalSDKClient(host=self._bootstrap.host, cache_ttl=None)  # type: ignore[arg-type]
            client.auth.universal_auth.login(
                client_id=self._bootstrap.client_id, client_secret=self._bootstrap.client_secret
            )
        except (InfisicalError, requests.RequestException) as e:
            raise SecretsError(
                f"Logging in to Infisical at {self._bootstrap.host} failed ({_describe_failure(e)}); check "
                "INFISICAL_HOST, INFISICAL_MEDIA_ORGANIZER_CLIENT_ID and INFISICAL_MEDIA_ORGANIZER_CLIENT_SECRET."
            ) from e

        self._client = client
        return client


def _describe_failure(error: Exception) -> str:
    if isinstance(error, APIError):
        return f"{type(error).__name__}, HTTP {error.status_code}"
    return type(error).__name__


class SecretStore:
    """Named secrets cached for the life of the process, absence included.

    A blank value counts as absent; any other value is returned trimmed of surrounding whitespace, which a secret
    pasted into a web form can pick up. A failed read caches nothing.

    Args:
        fetch: Reads one secret by name and returns its value, or None when the secret does not exist.
        source: Where the secrets come from, for messages and logs; kept as the ``source`` attribute.
        uses_plain_http: Whether reads travel to the secret manager unencrypted; kept as the ``uses_plain_http``
            attribute.
    """

    def __init__(self, fetch: Callable[[str], str | None], source: str, uses_plain_http: bool = False) -> None:
        self._fetch = fetch
        self._values: dict[str, str | None] = {}
        self.source = source
        self.uses_plain_http = uses_plain_http

    def __repr__(self) -> str:
        return f"SecretStore(source={self.source!r}, cached={len(self._values)})"

    def get_optional(self, name: str) -> str | None:
        """Return a secret that may be absent.

        Args:
            name: The secret's name.

        Returns:
            The trimmed value, or None when the secret is absent or blank.

        Raises:
            SecretsError: If reading the secret fails.
        """
        if name not in self._values:
            value = self._fetch(name)
            self._values[name] = value.strip() if value and value.strip() else None
        return self._values[name]

    def get(self, name: str) -> str:
        """Return a secret that must be set.

        Args:
            name: The secret's name.

        Returns:
            The trimmed value.

        Raises:
            SecretsError: If the secret is absent or blank, or reading it fails.
        """
        value = self.get_optional(name)
        if value is None:
            raise SecretsError(
                f"Secret '{name}' is not set in {self.source}. If it exists, check that it is in that environment, "
                "and check INFISICAL_ENV and INFISICAL_MEDIA_ORGANIZER_PROJECT_ID."
            )
        return value


_default_store: SecretStore | None = None


def get_secret_store() -> SecretStore:
    """Return the process-wide store, built on first use from the Infisical bootstrap in the environment.

    Building the store makes no network call; its first read logs in.

    Returns:
        The store.

    Raises:
        SecretsError: If a bootstrap variable is missing, blank or invalid.
    """
    global _default_store

    if _default_store is None:
        bootstrap = InfisicalBootstrap.from_env(os.environ)
        _default_store = SecretStore(InfisicalFetcher(bootstrap).fetch, bootstrap.source, bootstrap.uses_plain_http)
    return _default_store


def get_secret(name: str) -> str:
    """Return a secret that must be set, from the process-wide store.

    Args:
        name: The secret's name.

    Returns:
        The trimmed value.

    Raises:
        SecretsError: If the bootstrap is incomplete or invalid, the secret is absent or blank, or reading it fails.
    """
    return get_secret_store().get(name)


def get_optional_secret(name: str) -> str | None:
    """Return a secret that may be absent, from the process-wide store.

    Args:
        name: The secret's name.

    Returns:
        The trimmed value, or None when the secret is absent or blank.

    Raises:
        SecretsError: If the bootstrap is incomplete or invalid, or reading the secret fails.
    """
    return get_secret_store().get_optional(name)
