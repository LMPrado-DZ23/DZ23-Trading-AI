"""V9-SEC-01/02/03/04: fail-closed regressions, intentionally RED at 2b7754a.

Run in a child interpreter with an allowlisted environment and disposable
home/data/temp paths BEFORE collection. No service lifespan, providers, real
Postgres, Windows registry, or real credentials are needed by these tests.
"""
from __future__ import annotations

import importlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tradingagents.desk import storage
from tradingagents.desk.modo import EstadoDaMesa, ModoDaMesa

_TENANT = "v9-synthetic"
_NOW = 1_800_000_000.0
_PASSWORD = "v9-synthetic-replacement-password"
_ORIGINAL_PASSWORD = "v9-synthetic-original-password"
_SECRET = "v9-synthetic-secret-never-send"


def _lease(owner):
    return {
        "dono": owner,
        "expira_em": _NOW + 900,
        "expira_em_iso": "2027-01-15T08:15:00+00:00",
        "assumida_em_iso": "2027-01-15T08:00:00+00:00",
    }


def _claim(store, owner):
    return store.salvar_posse(
        _TENANT, "posse", _lease(owner), dono=owner, agora_epoch=_NOW,
    )[0]


class _LeasePostgres:
    """Only the CREATE/conditional-UPSERT boundary; never imports psycopg.

    A healthy transaction keeps the central lease. A failed INSERT must NOT
    authorize a second owner using a machine-local file.
    """

    def __init__(self):
        self.document = None

    @contextmanager
    def connect(self, *, fail_write=False):
        database = self

        class Cursor:
            def __init__(self):
                self.row = None
                self.pending = None

            @contextmanager
            def cursor(self):
                yield self

            def execute(self, sql, params=None):
                if sql.startswith("CREATE TABLE IF NOT EXISTS dz23_desk_state"):
                    return
                if not (sql.startswith("INSERT INTO dz23_desk_state")
                        and "RETURNING chave" in sql and "WHERE " in sql):
                    raise AssertionError("unexpected SQL in lease-only fake")
                tenant, key, raw, now, owner = params
                if (tenant, key, now) != (_TENANT, "posse", _NOW):
                    raise AssertionError("unexpected lease identity in SQL")
                if fail_write:
                    raise OSError("synthetic PostgreSQL write outage")
                current = database.document
                if current and current["dono"] != owner and current["expira_em"] > now:
                    self.row = None
                    return
                self.pending = json.loads(raw)
                self.row = (key,)

            def fetchone(self):
                return self.row

        connection = Cursor()
        yield connection
        if connection.pending is not None:
            self.document = connection.pending


def test_v9_sec01_postgres_write_outage_cannot_grant_local_ownership(tmp_path, monkeypatch):
    database = _LeasePostgres()
    monkeypatch.setattr(
        storage.ArmazemDeEstado, "_conectar",
        lambda self: database.connect(fail_write=self.diretorio.name == "partitioned"),
    )
    healthy = storage.ArmazemDeEstado(
        "postgresql://v9.invalid/synthetic", diretorio=tmp_path / "healthy",
    )
    partitioned = storage.ArmazemDeEstado(
        "postgresql://v9.invalid/synthetic", diretorio=tmp_path / "partitioned",
    )
    assert healthy.backend == partitioned.backend == "postgres"
    first = _claim(healthy, "owner-a")
    second = _claim(partitioned, "owner-b")
    local_lease = partitioned._caminho(_TENANT, "posse")
    assert (first, second, local_lease.exists()) == (True, False, False), (
        "central owner remains unexpired; a PostgreSQL outage must not grant a local lease"
    )


@pytest.mark.parametrize("failure", ["read-denied", "malformed-json"])
def test_v9_sec01_unverified_file_write_never_grants_ownership(tmp_path, monkeypatch, failure):
    store = storage.ArmazemDeEstado(diretorio=tmp_path)
    path = store._caminho(_TENANT, "posse")
    read_text = Path.read_text

    def unreadable_final_read(self, *args, **kwargs):
        if self == path and path.exists():
            if failure == "read-denied":
                raise PermissionError("synthetic verification read denied")
            return "{"
        return read_text(self, *args, **kwargs)

    # Initially absent: the real atomic write runs; only verification fails.
    monkeypatch.setattr(Path, "read_text", unreadable_final_read)
    granted = _claim(store, "owner-a")
    assert granted is False, "a proposed document is not proof of durable ownership"


def test_v9_sec01_file_contenders_cannot_both_win(tmp_path):
    stores = [storage.ArmazemDeEstado(diretorio=tmp_path) for _ in range(4)]
    path = stores[0]._caminho(_TENANT, "posse")
    entry = threading.Barrier(4, timeout=10)

    def contender(index):
        entry.wait()
        return _claim(stores[index], f"owner-{index}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        for _ in range(30):
            storage.escrever_json_atomico(
                path, {"dono": "expired-owner", "expira_em": _NOW - 1},
            )
            futures = [pool.submit(contender, i) for i in range(4)]
            winners = [future.result(timeout=15) for future in futures]
            assert sum(winners) == 1, (
                f"one unexpired lease yielded {sum(winners)} successful owners"
            )
            winner = winners.index(True)
            assert stores[0].carregar(_TENANT, "posse")["dono"] == f"owner-{winner}"


def test_v9_sec01_existing_valid_file_lease_rejects_rival(tmp_path):
    store = storage.ArmazemDeEstado(diretorio=tmp_path)
    assert _claim(store, "owner-a") is True
    assert _claim(store, "owner-b") is False
    assert store.carregar(_TENANT, "posse")["dono"] == "owner-a"


@pytest.mark.parametrize(
    "contents", ["{", "[]", "null", '"pausada"', "42"],
    ids=["malformed-json", "array", "null", "string", "number"],
)
def test_v9_sec02_existing_invalid_mode_blocks_execution(tmp_path, contents):
    store = storage.ArmazemDeEstado(diretorio=tmp_path)
    path = store._caminho(_TENANT, "modo")
    path.parent.mkdir(parents=True)
    path.write_text(contents, encoding="utf-8")
    mode = ModoDaMesa(store, _TENANT)
    assert mode.pode_executar()[0] is False, "invalid existing mode must not enable execution"
    assert mode.estado is EstadoDaMesa.BLOQUEADA


def test_v9_sec02_genuinely_missing_mode_keeps_new_install_default(tmp_path):
    store = storage.ArmazemDeEstado(diretorio=tmp_path)
    assert not store._caminho(_TENANT, "modo").exists()
    mode = ModoDaMesa(store, _TENANT)
    assert mode.estado is EstadoDaMesa.ATIVA
    assert mode.pode_executar() == (True, "")


def test_v9_sec02_unknown_state_in_object_remains_blocked(tmp_path):
    store = storage.ArmazemDeEstado(diretorio=tmp_path)
    store.salvar(_TENANT, "modo", {"estado": "invalid-state"})
    assert ModoDaMesa(store, _TENANT).pode_executar()[0] is False


@pytest.fixture
def service_module(tmp_path, monkeypatch):
    # Import lazily, only AFTER the runner's isolated child environment exists.
    # No reload and no lifespan: workers/providers never start. Persistent
    # import-time stores also live under the disposable DATA_DIR.
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    module = importlib.import_module("service.app")
    from service.api.owner_credentials import CredenciaisDoDono

    monkeypatch.setattr(module, "DATA", tmp_path)
    monkeypatch.setattr(module, "CFG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(module, "PASSWORD", "")
    monkeypatch.setattr(module, "OWNER_CREDENTIALS", CredenciaisDoDono(tmp_path / "owner_credentials.json"))
    monkeypatch.setattr(module, "_FAILS", {})
    return module


def _anonymous_setup(module):
    from fastapi import HTTPException, Request

    async def receive():
        return {"type": "http.request", "body": json.dumps({"senha": _PASSWORD}).encode(),
                "more_body": False}

    request = Request({
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/v1/auth/setup", "raw_path": b"/v1/auth/setup",
        "query_string": b"", "root_path": "", "headers": [],
        "client": ("v9-in-memory", 0), "server": ("v9-in-memory", 0),
    }, receive)
    # The real handler awaits only the in-memory request body. Drive that
    # coroutine directly: Windows asyncio/TestClient creates a loopback socket
    # pair even for ASGI tests, which is forbidden in this audit. No handler,
    # body parser, credential check, hash, lock or writer is replaced.
    coroutine = module.owner_setup(request)
    try:
        coroutine.send(None)
    except StopIteration:
        return 200
    except HTTPException as exc:
        return exc.status_code
    finally:
        coroutine.close()
    raise RuntimeError("setup handler unexpectedly requested external asynchronous I/O")


@pytest.mark.parametrize(
    "existing", [False, True], ids=["new-config", "existing-config"],
)
def test_v9_sec03_windows_without_dpapi_refuses_secret_save(service_module, monkeypatch, existing):
    from service.api import segredos_em_repouso as encryption

    path = service_module.CFG_PATH
    before = b'{"TRADINGAGENTS_DESK_CAPITAL": "100"}' if existing else None
    if before is not None:
        path.write_bytes(before)

    def unavailable_dll():
        raise OSError("synthetic DPAPI loader failure")

    # Exercise real tem_protecao -> cifrar -> save_cfg. Do not replace cifrar
    # with an exception: that would miss the plaintext fallback under audit.
    monkeypatch.setattr(encryption, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(encryption, "_crypt32", unavailable_dll)
    refused = False
    try:
        service_module.save_cfg({"GROQ_API_KEY": _SECRET})
    except (encryption.SegredoIlegivel, OSError):
        refused = True
    after = path.read_bytes() if path.exists() else None
    # Boolean observations prevent pytest from printing even synthetic secrets.
    observations = (refused, after == before, after is not None and _SECRET.encode() in after)
    assert observations == (True, True, False), (
        "Windows save must refuse unavailable DPAPI, preserve disk, and never write plaintext"
    )


@pytest.mark.parametrize(
    "damage", ["malformed-json", "non-object", "missing-hash", "invalid-hash-type", "read-denied"],
)
def test_v9_sec04_existing_owner_storage_never_reopens_anonymous_setup(
    service_module, monkeypatch, damage,
):
    credentials = service_module.OWNER_CREDENTIALS
    path = credentials.caminho
    if damage == "read-denied":
        credentials.criar(_ORIGINAL_PASSWORD)
    else:
        path.write_text({
            "malformed-json": "{",
            "non-object": "[]",
            "missing-hash": '{"versao": 1}',
            "invalid-hash-type": '{"versao": 1, "senha_hash": 42}',
        }[damage], encoding="utf-8")
    before = path.read_bytes()
    read_text = Path.read_text

    def denied_owner_read(self, *args, **kwargs):
        if self == path:
            raise PermissionError("synthetic owner read denied")
        return read_text(self, *args, **kwargs)

    with monkeypatch.context() as read_failure:
        if damage == "read-denied":
            read_failure.setattr(Path, "read_text", denied_owner_read)
        status = service_module.auth_status()
        # No Authorization header and no recovery proof. The password/hash
        # writer stays real, so an unauthorized replacement is observable.
        response_status = _anonymous_setup(service_module)
    observations = {
        "setup_required": status["setup_required"],
        "setup_rejected": response_status in {401, 403, 409, 503},
        "file_preserved": path.read_bytes() == before,
        "replacement_authenticates": credentials.verificar_senha(_PASSWORD),
    }
    assert observations == {
        "setup_required": False,
        "setup_rejected": True,
        "file_preserved": True,
        "replacement_authenticates": False,
    }, "existing invalid/inaccessible owner storage is not a new installation"


def test_v9_sec04_genuinely_missing_owner_allows_first_setup(service_module):
    credentials = service_module.OWNER_CREDENTIALS
    assert not credentials.caminho.exists()
    assert service_module.auth_status()["setup_required"] is True
    assert _anonymous_setup(service_module) == 200
    assert credentials.verificar_senha(_PASSWORD) is True
    assert service_module.auth_status()["setup_required"] is False


def test_v9_sec04_valid_existing_owner_rejects_anonymous_replacement(service_module):
    credentials = service_module.OWNER_CREDENTIALS
    credentials.criar(_ORIGINAL_PASSWORD)
    before = credentials.caminho.read_bytes()
    assert _anonymous_setup(service_module) == 409
    assert credentials.caminho.read_bytes() == before
    assert credentials.verificar_senha(_PASSWORD) is False
