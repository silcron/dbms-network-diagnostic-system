from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Mapping, Optional, Type, Union
from urllib.parse import quote, urlsplit

from scripts.fault_deadline import Deadline, DeadlineExpiredError
from scripts.fault_json import JsonBoundaryError, JsonValue, dumps_finite_json, loads_finite_json
from scripts.fault_transport import (
    MAX_HTTP_RESPONSE_BYTES,
    HttpTransportError,
    HttpTransportTimeout,
    HttpTransportUnavailable,
    perform_http_request,
)


API_TIMEOUT_SECONDS = 5.0
JsonScalar = Union[str, int, float, bool]


class ToxiproxyError(RuntimeError):
    pass


class ToxiproxyConfigurationError(ToxiproxyError):
    pass


class ToxiproxyUnavailableError(ToxiproxyError):
    pass


class ToxiproxyDeadlineError(ToxiproxyUnavailableError):
    pass


class ToxiproxyContractError(ToxiproxyError):
    pass


class ProxyCollisionError(ToxiproxyError):
    pass


class ToxiproxyHttpError(ToxiproxyError):
    def __init__(self, status: int, path: str) -> None:
        super().__init__(f"Toxiproxy API returned HTTP {status} for {path}.")
        self.status = status
        self.path = path


class ToxiproxyCleanupError(ToxiproxyError):
    pass


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class ProxyDefinition:
    name: str
    listen: str
    upstream: str


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class ProxyState:
    definition: ProxyDefinition
    canonical_payload: str


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class ToxicDefinition:
    name: str
    toxic_type: str
    stream: str
    attributes: Mapping[str, JsonScalar]


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class ApiCall:
    method: str
    path: str
    payload: Optional[Mapping[str, JsonValue]]
    expected_statuses: frozenset[int]


class ToxiproxyClient:
    def __init__(self, base_url: str, timeout_seconds: float = API_TIMEOUT_SECONDS) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ToxiproxyConfigurationError("Toxiproxy URL must use HTTP on a loopback host.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ToxiproxyConfigurationError("Toxiproxy URL must not contain credentials, a path, query, or fragment.")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ToxiproxyConfigurationError("Toxiproxy URL has an invalid port.") from exc
        if port is None or not 1 <= port <= 65535:
            raise ToxiproxyConfigurationError("Toxiproxy URL requires a port from 1 to 65535.")
        if not 0 < timeout_seconds <= API_TIMEOUT_SECONDS:
            raise ToxiproxyConfigurationError("Toxiproxy API timeout must be greater than zero and at most 5 seconds.")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _request(self, call: ApiCall, deadline: Optional[Deadline] = None) -> JsonValue:
        operation_deadline = deadline or Deadline.after(self.timeout_seconds)
        try:
            body = None if call.payload is None else dumps_finite_json(dict(call.payload)).encode("utf-8")
            response = perform_http_request(
                call.method,
                self.base_url + call.path,
                body,
                operation_deadline.remaining(self.timeout_seconds),
            )
        except JsonBoundaryError as exc:
            raise ToxiproxyContractError("Toxiproxy request could not be encoded as finite JSON.") from exc
        except (DeadlineExpiredError, HttpTransportTimeout) as exc:
            raise ToxiproxyDeadlineError(
                f"Toxiproxy API deadline expired at {self.base_url}: {type(exc).__name__}."
            ) from exc
        except HttpTransportUnavailable as exc:
            raise ToxiproxyUnavailableError(
                f"Toxiproxy API is unavailable at {self.base_url}: {type(exc).__name__}."
            ) from exc
        except HttpTransportError as exc:
            raise ToxiproxyContractError("Toxiproxy HTTP transport returned an invalid response.") from exc
        if response.status not in call.expected_statuses:
            raise ToxiproxyHttpError(response.status, call.path)
        if len(response.body) > MAX_HTTP_RESPONSE_BYTES:
            raise ToxiproxyContractError("Toxiproxy API response exceeded 1 MiB.")
        if not response.body:
            return None
        try:
            return loads_finite_json(response.body)
        except JsonBoundaryError as exc:
            raise ToxiproxyContractError("Toxiproxy API returned invalid JSON.") from exc

    def list_proxies(self, deadline: Optional[Deadline] = None) -> dict[str, ProxyState]:
        payload = self._request(ApiCall("GET", "/proxies", None, frozenset({200})), deadline)
        if not isinstance(payload, dict):
            raise ToxiproxyContractError("Toxiproxy proxy list must be a JSON object.")
        proxies: dict[str, ProxyState] = {}
        for name, raw_proxy in payload.items():
            if not isinstance(name, str) or not isinstance(raw_proxy, dict):
                raise ToxiproxyContractError("Toxiproxy proxy list contains an invalid entry.")
            definition = self._parse_proxy(raw_proxy)
            if definition.name != name:
                raise ToxiproxyContractError("Toxiproxy proxy key does not match its name.")
            proxies[name] = ProxyState(
                definition=definition,
                canonical_payload=self._canonical_payload(raw_proxy),
            )
        return proxies

    def create_proxy(self, definition: ProxyDefinition, deadline: Optional[Deadline] = None) -> ProxyState:
        operation_deadline = deadline or Deadline.after(self.timeout_seconds)
        if definition.name in self.list_proxies(operation_deadline):
            raise ProxyCollisionError(f"Toxiproxy proxy name already exists: {definition.name}")
        try:
            payload = self._request(
                ApiCall(
                    "POST",
                    "/proxies",
                    {
                        "name": definition.name,
                        "listen": definition.listen,
                        "upstream": definition.upstream,
                        "enabled": True,
                    },
                    frozenset({200, 201}),
                ),
                operation_deadline,
            )
        except ToxiproxyHttpError as exc:
            if exc.status == 409:
                raise ProxyCollisionError(f"Toxiproxy proxy name already exists: {definition.name}") from exc
            raise
        try:
            if not isinstance(payload, dict):
                raise ToxiproxyContractError("Toxiproxy create-proxy response must be a JSON object.")
            created = self._parse_proxy(payload)
            if created != definition:
                raise ToxiproxyContractError("Toxiproxy created proxy does not match the requested definition.")
        except ToxiproxyContractError as contract_error:
            try:
                self.delete_proxy(definition.name, operation_deadline)
            except ToxiproxyError as cleanup_error:
                raise ToxiproxyCleanupError("Could not clean up a proxy after an invalid create response.") from cleanup_error
            raise contract_error
        return ProxyState(
            definition=created,
            canonical_payload=self._canonical_payload(payload),
        )

    def create_toxic(self, proxy_name: str, toxic: ToxicDefinition, deadline: Optional[Deadline] = None) -> None:
        path = f"/proxies/{quote(proxy_name, safe='')}/toxics"
        payload = self._request(
            ApiCall(
                "POST",
                path,
                {
                    "name": toxic.name,
                    "type": toxic.toxic_type,
                    "stream": toxic.stream,
                    "toxicity": 1.0,
                    "attributes": dict(toxic.attributes),
                },
                frozenset({200, 201}),
            ),
            deadline,
        )
        if not isinstance(payload, dict) or payload.get("name") != toxic.name:
            raise ToxiproxyContractError("Toxiproxy create-toxic response did not identify the requested toxic.")

    def delete_toxic(self, proxy_name: str, toxic_name: str, deadline: Optional[Deadline] = None) -> None:
        path = f"/proxies/{quote(proxy_name, safe='')}/toxics/{quote(toxic_name, safe='')}"
        self._request(ApiCall("DELETE", path, None, frozenset({200, 204})), deadline)

    def delete_proxy(self, proxy_name: str, deadline: Optional[Deadline] = None) -> None:
        path = f"/proxies/{quote(proxy_name, safe='')}"
        self._request(ApiCall("DELETE", path, None, frozenset({200, 204})), deadline)

    @staticmethod
    def _parse_proxy(payload: Mapping[str, JsonValue]) -> ProxyDefinition:
        name, listen, upstream = payload.get("name"), payload.get("listen"), payload.get("upstream")
        if not isinstance(name, str) or not isinstance(listen, str) or not isinstance(upstream, str):
            raise ToxiproxyContractError("Toxiproxy proxy response has invalid identity fields.")
        return ProxyDefinition(name=name, listen=listen, upstream=upstream)

    @staticmethod
    def _canonical_payload(payload: JsonValue) -> str:
        try:
            return dumps_finite_json(payload)
        except JsonBoundaryError as exc:
            raise ToxiproxyContractError("Toxiproxy proxy response could not be canonicalized.") from exc


class OwnedProxy:
    def __init__(self, client: ToxiproxyClient, definition: ProxyDefinition) -> None:
        self.client = client
        self.definition = definition
        self._created = False
        self._toxic_names: list[str] = []

    def open(self, deadline: Optional[Deadline] = None) -> "OwnedProxy":
        self.client.create_proxy(self.definition, deadline)
        self._created = True
        return self

    def __enter__(self) -> "OwnedProxy":
        return self.open()

    def add_toxic(self, toxic: ToxicDefinition, deadline: Optional[Deadline] = None) -> None:
        if not self._created:
            raise ToxiproxyConfigurationError("Owned proxy must be created before adding a toxic.")
        self.client.create_toxic(self.definition.name, toxic, deadline)
        self._toxic_names.append(toxic.name)

    def remove_toxic(self, toxic_name: str, deadline: Optional[Deadline] = None) -> None:
        if toxic_name not in self._toxic_names:
            raise ToxiproxyConfigurationError(f"Toxic is not owned by this run: {toxic_name}")
        self.client.delete_toxic(self.definition.name, toxic_name, deadline)
        self._toxic_names.remove(toxic_name)

    def close(self, deadline: Optional[Deadline] = None) -> None:
        cleanup_deadline = deadline or Deadline.after(self.client.timeout_seconds)
        failures: list[str] = []
        for toxic_name in reversed(self._toxic_names.copy()):
            try:
                self.client.delete_toxic(self.definition.name, toxic_name, cleanup_deadline)
                self._toxic_names.remove(toxic_name)
            except ToxiproxyError as exc:
                failures.append(str(exc))
        if self._created:
            try:
                self.client.delete_proxy(self.definition.name, cleanup_deadline)
                self._created = False
            except ToxiproxyError as exc:
                failures.append(str(exc))
        if failures:
            raise ToxiproxyCleanupError("Owned Toxiproxy cleanup failed: " + "; ".join(failures))

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> bool:
        try:
            self.close()
        except ToxiproxyCleanupError as cleanup_error:
            if exc_value is not None:
                raise cleanup_error from exc_value
            raise
        return False
