"""Read-only CCXT diagnostics behind a fixed egress and spot-only boundary."""

from __future__ import annotations

import ipaddress
import socket
import types
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from tradingagents.platform.connections.catalog import (
    BrokerEnvironment,
    ExchangeCapability,
)


class ReadOnlyDiagnosticClient(Protocol):
    id: str
    urls: Mapping[str, Any]
    has: Mapping[str, Any]
    hostname: str | None

    def set_sandbox_mode(self, enabled: bool) -> None: ...

    def load_markets(self, reload: bool = False) -> Mapping[str, Any]: ...

    def fetch_balance(self, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]: ...

    def fetch_accounts(
        self, params: Mapping[str, Any] | None = None
    ) -> list[Mapping[str, Any]]: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    success: bool
    code: str
    market_count: int = 0
    nonzero_asset_count: int = 0
    permissions_verified: bool = False
    permissions_safe: bool = False
    permission_status: str = "unverifiable"
    spot_only: bool = True
    adapter_version: str = "unknown"


class DiagnosticSafetyError(RuntimeError):
    pass


_DANGEROUS_ACCOUNT_TOKENS = frozenset(
    {
        "borrow",
        "derivative",
        "futures",
        "margin",
        "option",
        "perpetual",
        "transfer",
        "withdraw",
    }
)


class CCXTReadOnlyDiagnostics:
    """Construct and exercise a CCXT client without invoking mutating methods."""

    def __init__(
        self,
        *,
        client_factory: Callable[[str, Mapping[str, Any]], ReadOnlyDiagnosticClient] | None = None,
        resolve_hosts: bool = True,
        timeout_ms: int = 15_000,
    ) -> None:
        if not 1_000 <= timeout_ms <= 30_000:
            raise ValueError("diagnostic timeout must be between 1 and 30 seconds")
        self._factory = client_factory or self._ccxt_factory
        self._resolve_hosts = resolve_hosts
        self._timeout_ms = timeout_ms

    @staticmethod
    def _ccxt_factory(ccxt_id: str, config: Mapping[str, Any]) -> ReadOnlyDiagnosticClient:
        import ccxt

        try:
            exchange_type = getattr(ccxt, ccxt_id)
        except AttributeError as exc:
            raise DiagnosticSafetyError("EXCHANGE_ADAPTER_UNAVAILABLE") from exc
        client = exchange_type(dict(config))
        client._dz23_ccxt_version = str(ccxt.__version__)
        return client

    def run(
        self,
        *,
        capability: ExchangeCapability,
        environment: BrokerEnvironment,
        credentials: Mapping[str, str],
    ) -> DiagnosticResult:
        if environment is BrokerEnvironment.TESTNET and not capability.has_real_testnet:
            raise DiagnosticSafetyError("TESTNET_UNAVAILABLE")
        config: dict[str, Any] = {
            "apiKey": credentials["api_key"],
            "secret": credentials["secret"],
            "enableRateLimit": True,
            "timeout": self._timeout_ms,
            "options": {"defaultType": "spot"},
        }
        if credentials.get("passphrase"):
            config["password"] = credentials["passphrase"]
        if credentials.get("uid"):
            config["uid"] = credentials["uid"]

        client = self._factory(capability.ccxt_id, config)
        try:
            self._harden_ambient_transport(client, capability, environment)
            if environment is BrokerEnvironment.TESTNET:
                client.set_sandbox_mode(True)
                self._harden_ambient_transport(client, capability, environment)
            self._assert_allowed_egress(client, capability, environment)
            markets = client.load_markets(reload=True)
            if not isinstance(markets, Mapping) or not markets:
                return DiagnosticResult(False, "MARKETS_UNAVAILABLE")
            if any(self._is_non_spot_market(market) for market in markets.values()):
                return DiagnosticResult(False, "NON_SPOT_MARKET_DEFAULT", len(markets))

            balance = client.fetch_balance({"type": "spot"})
            if not isinstance(balance, Mapping):
                return DiagnosticResult(False, "BALANCE_INVALID", len(markets))
            nonzero_assets = self._count_nonzero_assets(balance)
            verified, safe = self._probe_permissions(client, capability)
            if verified and not safe:
                return DiagnosticResult(
                    False,
                    "DANGEROUS_PERMISSIONS",
                    len(markets),
                    nonzero_assets,
                    True,
                    False,
                    "unsafe",
                    adapter_version=self._adapter_version(client),
                )
            if environment is BrokerEnvironment.LIVE and not verified:
                return DiagnosticResult(
                    False,
                    "PERMISSIONS_NOT_VERIFIABLE",
                    len(markets),
                    nonzero_assets,
                    False,
                    False,
                    "unverifiable",
                    adapter_version=self._adapter_version(client),
                )
            return DiagnosticResult(
                True,
                "DIAGNOSTIC_OK",
                len(markets),
                nonzero_assets,
                verified,
                safe,
                "verified_safe" if verified and safe else "not_supported",
                adapter_version=self._adapter_version(client),
            )
        except DiagnosticSafetyError:
            raise
        except Exception as exc:
            code = self._safe_exception_code(exc)
            return DiagnosticResult(False, code)
        finally:
            with suppress(Exception):
                client.close()

    def _harden_ambient_transport(
        self,
        client: ReadOnlyDiagnosticClient,
        capability: ExchangeCapability,
        environment: BrokerEnvironment,
    ) -> None:
        session = getattr(client, "session", None)
        if session is not None and hasattr(session, "trust_env"):
            session.trust_env = False
            if not getattr(session, "_dz23_redirects_disabled", False):
                original_request = session.request

                def request_without_redirects(_session, method: str, url: str, **kwargs):
                    self._assert_request_url(url, capability, environment)
                    kwargs["allow_redirects"] = False
                    return original_request(method, url, **kwargs)

                session.request = types.MethodType(request_without_redirects, session)
                session._dz23_redirects_disabled = True
        for name, empty in (
            ("proxies", {}),
            ("aiohttp_proxy", None),
            ("httpProxy", None),
            ("httpsProxy", None),
            ("socksProxy", None),
        ):
            if hasattr(client, name):
                setattr(client, name, empty)

    def _assert_allowed_egress(
        self,
        client: ReadOnlyDiagnosticClient,
        capability: ExchangeCapability,
        environment: BrokerEnvironment,
    ) -> None:
        urls = getattr(client, "urls", {}).get("api")
        flattened = tuple(self._flatten_urls(urls))
        if not flattened:
            raise DiagnosticSafetyError("EXCHANGE_ENDPOINT_MISSING")
        hostname_template = (getattr(client, "hostname", None) or "").strip().lower()
        allowed = self._origins(capability, environment)
        configured_origins: set[str] = set()
        for raw_url in flattened:
            value = raw_url.replace("{hostname}", hostname_template)
            try:
                configured_origins.add(self._normalized_origin(value))
            except DiagnosticSafetyError:
                continue
        if not configured_origins.intersection(allowed):
            raise DiagnosticSafetyError("EXCHANGE_ENDPOINT_FORBIDDEN")

    def _assert_request_url(
        self,
        url: str,
        capability: ExchangeCapability,
        environment: BrokerEnvironment,
    ) -> None:
        origin = self._normalized_origin(url)
        if origin not in self._origins(capability, environment):
            raise DiagnosticSafetyError("EXCHANGE_ENDPOINT_FORBIDDEN")
        if self._resolve_hosts:
            self._assert_public_dns(urlparse(origin).hostname or "")

    @staticmethod
    def _origins(capability: ExchangeCapability, environment: BrokerEnvironment) -> frozenset[str]:
        configured = (
            capability.testnet_origins
            if environment is BrokerEnvironment.TESTNET
            else capability.live_origins
        )
        if not configured:
            raise DiagnosticSafetyError("EXCHANGE_ENDPOINT_MISSING")
        return frozenset(configured)

    @staticmethod
    def _normalized_origin(url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
        ):
            raise DiagnosticSafetyError("EXCHANGE_ENDPOINT_FORBIDDEN")
        return f"https://{host}"

    @classmethod
    def _flatten_urls(cls, value: Any):
        if isinstance(value, str):
            yield value
        elif isinstance(value, Mapping):
            for nested in value.values():
                yield from cls._flatten_urls(nested)
        elif isinstance(value, (tuple, list)):
            for nested in value:
                yield from cls._flatten_urls(nested)

    @staticmethod
    def _assert_public_dns(host: str) -> None:
        try:
            records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise DiagnosticSafetyError("EXCHANGE_DNS_FAILED") from exc
        if not records:
            raise DiagnosticSafetyError("EXCHANGE_DNS_FAILED")
        for record in records:
            address = ipaddress.ip_address(record[4][0])
            if not address.is_global:
                raise DiagnosticSafetyError("EXCHANGE_DNS_FORBIDDEN")

    @staticmethod
    def _is_non_spot_market(value: Any) -> bool:
        if not isinstance(value, Mapping):
            return True
        if value.get("active") is False:
            return False
        return value.get("spot") is not True

    @staticmethod
    def _count_nonzero_assets(balance: Mapping[str, Any]) -> int:
        total = balance.get("total")
        if not isinstance(total, Mapping):
            return 0
        count = 0
        for value in total.values():
            if isinstance(value, bool):
                continue
            try:
                if float(value) > 0:
                    count += 1
            except (TypeError, ValueError, OverflowError):
                continue
        return count

    @staticmethod
    def _probe_permissions(
        client: ReadOnlyDiagnosticClient, capability: ExchangeCapability
    ) -> tuple[bool, bool]:
        if not capability.permission_probe or not client.has.get("fetchAccounts"):
            return False, False
        accounts = client.fetch_accounts({"type": "spot"})
        if not isinstance(accounts, list) or not accounts:
            return False, False
        searchable = " ".join(str(account).casefold() for account in accounts)
        if any(token in searchable for token in _DANGEROUS_ACCOUNT_TOKENS):
            return True, False
        # Account type is not proof that the API key cannot withdraw or use
        # margin.  A venue-specific permission endpoint must establish that in
        # M6; until then LIVE remains fail-closed even for spot-only accounts.
        return False, False

    @staticmethod
    def _safe_exception_code(exc: Exception) -> str:
        name = type(exc).__name__.upper()
        if "AUTH" in name or "PERMISSION" in name:
            return "AUTHENTICATION_FAILED"
        if "TIMEOUT" in name:
            return "EXCHANGE_TIMEOUT"
        if "NETWORK" in name or "CONNECTION" in name:
            return "EXCHANGE_UNAVAILABLE"
        return "DIAGNOSTIC_FAILED"

    @staticmethod
    def _adapter_version(client: ReadOnlyDiagnosticClient) -> str:
        value = str(getattr(client, "_dz23_ccxt_version", "unknown"))
        return value[:40] if value else "unknown"
