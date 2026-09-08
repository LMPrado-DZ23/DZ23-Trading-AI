from __future__ import annotations

import base64
import json
import socket
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tradingagents.platform.connections import (
    BrokerConnection,
    BrokerCredential,
    BrokerCredentialInput,
    BrokerEnvironment,
    CCXTReadOnlyDiagnostics,
    ConnectionError,
    ConnectionService,
    CredentialKeyring,
    DiagnosticResult,
    TransitCredentialKeyring,
    exchange_capability,
    exchange_catalog,
)
from tradingagents.platform.connections.diagnostics import DiagnosticSafetyError
from tradingagents.platform.identity import (
    Base,
    IdentityPrincipal,
    IdentityService,
    MembershipRole,
    TokenHasher,
)


class FakeSession:
    def __init__(self) -> None:
        self.trust_env = True
        self.last_allow_redirects = None

    def request(self, method, url, **kwargs):
        self.last_allow_redirects = kwargs.get("allow_redirects")
        return method, url


class FakeExchange:
    def __init__(
        self,
        *,
        urls=None,
        markets=None,
        balance=None,
        accounts=None,
        failure: Exception | None = None,
    ) -> None:
        self.id = "fake"
        self.urls = urls or {"api": "https://testnet.binance.vision/api/v3"}
        self.hostname = "binance.com"
        self.has = {"fetchAccounts": accounts is not None}
        self.session = FakeSession()
        self.proxies = {"https": "http://ambient.invalid"}
        self._markets = markets or {
            "BTC/USDT": {"spot": True, "active": True},
            "OLD/USDT": {"spot": False, "active": False},
        }
        self._balance = balance or {"total": {"BTC": 1, "USDT": 0}}
        self._accounts = accounts or []
        self._failure = failure
        self.sandbox = False
        self.closed = False
        self.load_calls = 0

    def set_sandbox_mode(self, enabled: bool) -> None:
        self.sandbox = enabled

    def load_markets(self, reload: bool = False):
        self.load_calls += 1
        if self._failure:
            raise self._failure
        return self._markets

    def fetch_balance(self, params=None):
        return self._balance

    def fetch_accounts(self, params=None):
        return self._accounts

    def close(self) -> None:
        self.closed = True


def test_catalog_is_exact_spot_only_allowlist_and_never_claims_permission_proof():
    catalog = exchange_catalog()
    assert {item.slug for item in catalog} == {
        "binance",
        "bybit",
        "okx",
        "coinbase",
        "kraken",
        "gateio",
        "kucoin",
        "mercadobitcoin",
        "bitget",
    }
    assert all(item.spot_only for item in catalog)
    assert all(item.withdrawals_forbidden for item in catalog)
    assert all(not item.permission_probe for item in catalog)
    assert exchange_capability(" BINANCE ").ccxt_id == "binance"
    with pytest.raises(ValueError):
        exchange_capability("evil-exchange")


def test_envelope_encryption_uses_random_dek_and_rewraps_without_payload_reencrypt():
    old = b"o" * 32
    new = b"n" * 32
    context = b"tenant:connection:version"
    first_ring = CredentialKeyring(keys={"old": old}, active_key_id="old")
    envelope = first_ring.encrypt(b'{"api_key":"secret-value"}', context=context)

    assert b"secret-value" not in envelope.ciphertext
    assert len(envelope.wrapped_key) == 48
    assert first_ring.decrypt(envelope, context=context).endswith(b'"secret-value"}')
    with pytest.raises(InvalidTag):
        first_ring.decrypt(envelope, context=b"wrong-binding")

    rotating_ring = CredentialKeyring(
        keys={"old": old, "new": new}, active_key_id="new"
    )
    rotated = rotating_ring.rotate(envelope, context=context)
    assert rotated.key_id == "new"
    assert rotated.ciphertext == envelope.ciphertext
    assert rotated.payload_nonce == envelope.payload_nonce
    assert rotated.wrapped_key != envelope.wrapped_key
    assert rotating_ring.decrypt(rotated, context=context) == first_ring.decrypt(
        envelope, context=context
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("wrapped_key_nonce", b"x" * 12),
        ("wrapped_key", b"x" * 48),
        ("payload_nonce", b"x" * 12),
        ("ciphertext", b"x" * 32),
    ],
)
def test_envelope_tampering_is_always_detected(field, replacement):
    keyring = CredentialKeyring(keys={"v1": b"k" * 32}, active_key_id="v1")
    envelope = keyring.encrypt(b"sensitive", context=b"bound-context")
    with pytest.raises((InvalidTag, ValueError)):
        keyring.decrypt(replace(envelope, **{field: replacement}), context=b"bound-context")


def test_transit_envelope_roundtrip_and_rewrap_never_changes_payload_ciphertext():
    wrapped: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "vault.example.test"
        assert request.headers["x-vault-token"] == "test-token"
        body = json.loads(request.content)
        operation = request.url.path.split("/")[-2]
        if operation == "encrypt":
            value = f"vault:v1:{len(wrapped) + 1}"
            wrapped[value] = base64.b64decode(body["plaintext"])
            return httpx.Response(200, json={"data": {"ciphertext": value}})
        if operation == "decrypt":
            value = base64.b64encode(wrapped[body["ciphertext"]]).decode()
            return httpx.Response(200, json={"data": {"plaintext": value}})
        assert operation == "rewrap"
        value = "vault:v2:rewrapped"
        wrapped[value] = wrapped[body["ciphertext"]]
        return httpx.Response(200, json={"data": {"ciphertext": value}})

    client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False
    )
    keyring = TransitCredentialKeyring(
        base_url="https://vault.example.test",
        key_name="broker-key",
        token="test-token",
        client=client,
    )
    envelope = keyring.encrypt(b"sensitive", context=b"tenant-bound")
    assert envelope.wrapped_key_nonce == b""
    assert keyring.decrypt(envelope, context=b"tenant-bound") == b"sensitive"
    with pytest.raises(ValueError, match="context"):
        keyring.decrypt(envelope, context=b"wrong-tenant")
    rotated = keyring.rotate(envelope, context=b"tenant-bound")
    assert rotated.ciphertext == envelope.ciphertext
    assert rotated.payload_nonce == envelope.payload_nonce
    assert rotated.wrapped_key != envelope.wrapped_key
    assert keyring.decrypt(rotated, context=b"tenant-bound") == b"sensitive"


def test_diagnostics_disables_ambient_proxy_and_redirects_before_network():
    exchange = FakeExchange()
    diagnostics = CCXTReadOnlyDiagnostics(
        client_factory=lambda _ccxt_id, _config: exchange, resolve_hosts=False
    )
    result = diagnostics.run(
        capability=exchange_capability("binance"),
        environment=BrokerEnvironment.TESTNET,
        credentials={"api_key": "key", "secret": "secret"},
    )

    assert result.success
    assert exchange.sandbox
    assert exchange.session.trust_env is False
    assert exchange.proxies == {}
    exchange.session.request("GET", "https://testnet.binance.vision/api/v3")
    assert exchange.session.last_allow_redirects is False
    assert exchange.closed


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_diagnostics_never_follows_redirects_to_another_origin(status):
    contacted_destination = 0

    class RedirectSession(FakeSession):
        def request(self, method, url, **kwargs):
            nonlocal contacted_destination
            assert kwargs.get("allow_redirects") is False
            if kwargs.get("allow_redirects"):
                contacted_destination += 1
            self.last_allow_redirects = kwargs.get("allow_redirects")
            return SimpleNamespace(status_code=status, headers={"location": "https://evil.test"})

    exchange = FakeExchange()
    exchange.session = RedirectSession()
    diagnostics = CCXTReadOnlyDiagnostics(
        client_factory=lambda _ccxt_id, _config: exchange, resolve_hosts=False
    )
    diagnostics.run(
        capability=exchange_capability("binance"),
        environment=BrokerEnvironment.TESTNET,
        credentials={"api_key": "key", "secret": "secret"},
    )
    exchange.session.request("GET", "https://testnet.binance.vision/api/v3")
    assert contacted_destination == 0


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fe80::1"],
)
def test_diagnostics_rejects_non_public_dns_answers(monkeypatch, address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(family, socket.SOCK_STREAM, 6, "", (address, 443))],
    )
    with pytest.raises(DiagnosticSafetyError, match="EXCHANGE_DNS_FORBIDDEN"):
        CCXTReadOnlyDiagnostics._assert_public_dns("api.example.test")


def test_diagnostics_does_not_call_any_mutating_exchange_method():
    exchange = FakeExchange()
    for name in ("create_order", "withdraw", "transfer", "borrow_margin", "set_leverage"):
        setattr(exchange, name, lambda *_args, _name=name, **_kwargs: pytest.fail(_name))
    diagnostics = CCXTReadOnlyDiagnostics(
        client_factory=lambda _ccxt_id, _config: exchange, resolve_hosts=False
    )
    assert diagnostics.run(
        capability=exchange_capability("binance"),
        environment=BrokerEnvironment.TESTNET,
        credentials={"api_key": "key", "secret": "secret"},
    ).success


def test_diagnostics_rejects_forbidden_endpoint_before_any_exchange_call():
    exchange = FakeExchange(urls={"api": "https://127.0.0.1/private"})
    diagnostics = CCXTReadOnlyDiagnostics(
        client_factory=lambda _ccxt_id, _config: exchange, resolve_hosts=False
    )
    with pytest.raises(DiagnosticSafetyError, match="EXCHANGE_ENDPOINT_FORBIDDEN"):
        diagnostics.run(
            capability=exchange_capability("binance"),
            environment=BrokerEnvironment.TESTNET,
            credentials={"api_key": "key", "secret": "secret"},
        )
    assert exchange.load_calls == 0
    assert exchange.closed


def test_live_diagnostic_never_treats_account_type_as_safe_key_permissions():
    exchange = FakeExchange(
        urls={"api": "https://www.okx.com/api/v5"},
        accounts=[{"type": "spot", "permission": "trade"}],
    )
    capability = replace(exchange_capability("okx"), permission_probe=True)
    diagnostics = CCXTReadOnlyDiagnostics(
        client_factory=lambda _ccxt_id, _config: exchange, resolve_hosts=False
    )
    result = diagnostics.run(
        capability=capability,
        environment=BrokerEnvironment.LIVE,
        credentials={"api_key": "key", "secret": "secret", "passphrase": "p"},
    )
    assert not result.success
    assert result.code == "PERMISSIONS_NOT_VERIFIABLE"


def test_permission_probe_rejects_explicit_dangerous_account_scope():
    exchange = FakeExchange(
        urls={"api": "https://www.okx.com/api/v5"},
        accounts=[{"type": "spot", "permission": "withdraw"}],
    )
    capability = replace(exchange_capability("okx"), permission_probe=True)
    diagnostics = CCXTReadOnlyDiagnostics(
        client_factory=lambda _ccxt_id, _config: exchange, resolve_hosts=False
    )
    result = diagnostics.run(
        capability=capability,
        environment=BrokerEnvironment.LIVE,
        credentials={"api_key": "key", "secret": "secret", "passphrase": "p"},
    )
    assert not result.success
    assert result.code == "DANGEROUS_PERMISSIONS"


def test_diagnostic_exception_never_returns_provider_message_or_secret():
    secret = "never-return-this-secret"

    class AuthenticationError(Exception):
        pass

    exchange = FakeExchange(failure=AuthenticationError(secret))
    diagnostics = CCXTReadOnlyDiagnostics(
        client_factory=lambda _ccxt_id, _config: exchange, resolve_hosts=False
    )
    result = diagnostics.run(
        capability=exchange_capability("binance"),
        environment=BrokerEnvironment.TESTNET,
        credentials={"api_key": "key", "secret": secret},
    )
    assert result.code == "AUTHENTICATION_FAILED"
    assert secret not in repr(result)


def _connection_fixture():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService(
        factory,
        token_hasher=TokenHasher(b"connection-test-token-pepper-value"),
    )
    registered = identity.register_tenant(
        tenant_name="Connection Test",
        tenant_slug=f"connection-{uuid4().hex[:8]}",
        owner_email=f"connection-{uuid4().hex[:8]}@example.test",
        display_name="Owner",
        password="correct horse battery staple",
    )
    principal = IdentityPrincipal(
        tenant_id=registered.tenant_id,
        user_id=registered.user_id,
        membership_id=registered.membership_id,
        session_id=uuid4(),
        role=MembershipRole.OWNER,
    )
    diagnostic = SimpleNamespace(
        run=lambda **_kwargs: DiagnosticResult(
            True,
            "DIAGNOSTIC_OK",
            2,
            1,
            True,
            True,
            "verified_safe",
            adapter_version="test-ccxt",
        )
    )
    service = ConnectionService(
        factory,
        keyring=CredentialKeyring(keys={"key-v1": b"k" * 32}, active_key_id="key-v1"),
        diagnostics=diagnostic,
    )
    return factory, principal, service, diagnostic


def test_connection_service_encrypts_versions_lists_without_secrets_and_removes_rows():
    factory, principal, service, _diagnostic = _connection_fixture()
    secret = "top-secret-provider-value"
    payload = BrokerCredentialInput(api_key="test-key", secret=secret)
    assert secret not in repr(payload)
    connection_id = service.upsert_connection(
        principal=principal,
        idempotency_key="create-account-0001",
        label="Conta principal",
        exchange_slug="binance",
        environment=BrokerEnvironment.TESTNET,
        credentials=payload,
    )
    service.upsert_connection(
        principal=principal,
        idempotency_key="rotate-account-0002",
        label="Conta principal",
        exchange_slug="binance",
        environment=BrokerEnvironment.TESTNET,
        credentials=BrokerCredentialInput(api_key="test-key-2", secret="second-secret"),
    )

    rows = service.list_connections(principal=principal)
    assert len(rows) == 1
    assert rows[0].credential_version == 2
    assert secret not in json.dumps([repr(row) for row in rows])
    with factory() as db:
        credentials = tuple(db.scalars(select(BrokerCredential)))
        assert len(credentials) == 2
        assert all(secret.encode() not in row.ciphertext for row in credentials)
        assert all(secret.encode() not in row.wrapped_key for row in credentials)

    assert service.diagnose(principal=principal, connection_id=connection_id).success
    service.remove_connection(principal=principal, connection_id=connection_id)
    with factory() as db:
        assert tuple(db.scalars(select(BrokerCredential))) == ()
        connection = db.get(BrokerConnection, connection_id)
        assert connection is not None and connection.status == "disabled"


def test_idempotency_replay_is_bound_to_the_exact_request_payload():
    factory, principal, service, _diagnostic = _connection_fixture()
    operation = "same-operation-key-01"
    first = service.upsert_connection(
        principal=principal,
        idempotency_key=operation,
        label="Conta idempotente",
        exchange_slug="binance",
        environment=BrokerEnvironment.TESTNET,
        credentials=BrokerCredentialInput(api_key="key1", secret="secret1"),
    )
    second = service.upsert_connection(
        principal=principal,
        idempotency_key=operation,
        label="Conta idempotente",
        exchange_slug="binance",
        environment=BrokerEnvironment.TESTNET,
        credentials=BrokerCredentialInput(api_key="key1", secret="secret1"),
    )
    assert first == second
    with factory() as db:
        assert len(tuple(db.scalars(select(BrokerCredential)))) == 1
    with pytest.raises(ConnectionError, match="IDEMPOTENCY_PAYLOAD_MISMATCH"):
        service.upsert_connection(
            principal=principal,
            idempotency_key=operation,
            label="Conta idempotente",
            exchange_slug="binance",
            environment=BrokerEnvironment.TESTNET,
            credentials=BrokerCredentialInput(api_key="key2", secret="secret2"),
        )


def test_service_rewraps_existing_deks_without_changing_payload_ciphertext():
    factory, principal, _service, diagnostic = _connection_fixture()
    old_ring = CredentialKeyring(keys={"old": b"o" * 32}, active_key_id="old")
    service = ConnectionService(factory, keyring=old_ring, diagnostics=diagnostic)
    connection_id = service.upsert_connection(
        principal=principal,
        idempotency_key="rewrap-credential-01",
        label="Conta para rotação",
        exchange_slug="binance",
        environment=BrokerEnvironment.TESTNET,
        credentials=BrokerCredentialInput(api_key="key1", secret="secret1"),
    )
    with factory() as db:
        before = db.scalar(
            select(BrokerCredential).where(BrokerCredential.connection_id == connection_id)
        )
        assert before is not None
        original_ciphertext = bytes(before.ciphertext)

    rotating_ring = CredentialKeyring(
        keys={"old": b"o" * 32, "new": b"n" * 32}, active_key_id="new"
    )
    rotating_service = ConnectionService(
        factory, keyring=rotating_ring, diagnostics=diagnostic
    )
    assert rotating_service.rewrap_tenant_credentials(principal=principal) == 1
    assert rotating_service.rewrap_tenant_credentials(principal=principal) == 0
    with factory() as db:
        after = db.scalar(
            select(BrokerCredential).where(BrokerCredential.connection_id == connection_id)
        )
        assert after is not None
        assert after.key_id == "new"
        assert bytes(after.ciphertext) == original_ciphertext


def test_service_always_requests_transit_rewrap_without_holding_payload_plaintext():
    factory, principal, _service, diagnostic = _connection_fixture()
    wrapped: dict[str, bytes] = {}
    rewrap_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal rewrap_calls
        body = json.loads(request.content)
        operation = request.url.path.split("/")[-2]
        if operation == "encrypt":
            value = "vault:v1:initial"
            wrapped[value] = base64.b64decode(body["plaintext"])
            return httpx.Response(200, json={"data": {"ciphertext": value}})
        if operation == "rewrap":
            rewrap_calls += 1
            value = f"vault:v2:rewrapped-{rewrap_calls}"
            wrapped[value] = wrapped[body["ciphertext"]]
            return httpx.Response(200, json={"data": {"ciphertext": value}})
        value = base64.b64encode(wrapped[body["ciphertext"]]).decode()
        return httpx.Response(200, json={"data": {"plaintext": value}})

    transit = TransitCredentialKeyring(
        base_url="https://vault.example.test",
        key_name="broker-key",
        token="test-token",
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        ),
    )
    service = ConnectionService(factory, keyring=transit, diagnostics=diagnostic)
    connection_ids = tuple(
        service.upsert_connection(
            principal=principal,
            idempotency_key=f"transit-rewrap-test-{index:02d}",
            label=f"Conta Transit {index}",
            exchange_slug="binance",
            environment=BrokerEnvironment.TESTNET,
            credentials=BrokerCredentialInput(
                api_key=f"key{index}", secret=f"secret{index}"
            ),
        )
        for index in range(3)
    )
    with factory() as db:
        before = tuple(
            db.scalars(
                select(BrokerCredential).where(
                    BrokerCredential.connection_id.in_(connection_ids)
                )
            )
        )
        original_ciphertexts = {row.id: bytes(row.ciphertext) for row in before}

    assert service.rewrap_tenant_credentials(principal=principal, batch_size=2) == 3
    assert rewrap_calls == 3
    with factory() as db:
        after = tuple(
            db.scalars(
                select(BrokerCredential).where(
                    BrokerCredential.connection_id.in_(connection_ids)
                )
            )
        )
        assert len(after) == 3
        assert all(bytes(row.ciphertext) == original_ciphertexts[row.id] for row in after)
        assert all(bytes(row.wrapped_key).startswith(b"vault:v2:") for row in after)


def test_exchange_specific_credential_schema_rejects_unused_fields():
    _factory, principal, service, _diagnostic = _connection_fixture()
    with pytest.raises(ConnectionError, match="CREDENTIAL_FIELDS_INVALID"):
        service.upsert_connection(
            principal=principal,
            idempotency_key="unused-field-test-01",
            label="Campo extra",
            exchange_slug="binance",
            environment=BrokerEnvironment.TESTNET,
            credentials=BrokerCredentialInput(
                api_key="key1", secret="secret1", passphrase="unused"
            ),
        )


def test_testnet_without_real_sandbox_is_refused_before_persistence():
    factory, principal, service, _diagnostic = _connection_fixture()
    with pytest.raises(ConnectionError, match="TESTNET_UNAVAILABLE"):
        service.upsert_connection(
            principal=principal,
            idempotency_key="no-sandbox-test-01",
            label="Conta sem sandbox",
            exchange_slug="mercadobitcoin",
            environment=BrokerEnvironment.TESTNET,
            credentials=BrokerCredentialInput(api_key="key1", secret="secret1"),
        )
    with factory() as db:
        assert tuple(db.scalars(select(BrokerConnection))) == ()


def test_diagnostic_network_phase_holds_no_database_lock_and_detects_rotation():
    _factory, principal, service, diagnostic = _connection_fixture()
    connection_id = service.upsert_connection(
        principal=principal,
        idempotency_key="concurrent-create-01",
        label="Conta concorrente",
        exchange_slug="binance",
        environment=BrokerEnvironment.TESTNET,
        credentials=BrokerCredentialInput(api_key="key1", secret="secret1"),
    )

    def rotate_during_network(**_kwargs):
        service.upsert_connection(
            principal=principal,
            idempotency_key="concurrent-rotate-02",
            label="Conta concorrente",
            exchange_slug="binance",
            environment=BrokerEnvironment.TESTNET,
            credentials=BrokerCredentialInput(api_key="key2", secret="secret2"),
        )
        return DiagnosticResult(True, "DIAGNOSTIC_OK")

    diagnostic.run = rotate_during_network
    with pytest.raises(ConnectionError, match="CREDENTIAL_CHANGED_DURING_DIAGNOSTIC"):
        service.diagnose(principal=principal, connection_id=connection_id)
