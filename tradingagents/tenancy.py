from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

ROLES = frozenset({"viewer", "analyst", "operator", "risk_manager", "admin", "auditor"})
ROLE_PERMISSIONS = {
    "viewer": {"read"},
    "analyst": {"read", "analysis"},
    "operator": {"read", "analysis", "paper", "shadow", "demo"},
    "risk_manager": {"read", "analysis", "paper", "shadow", "demo", "risk", "kill_switch"},
    "admin": {"read", "analysis", "paper", "shadow", "demo", "risk", "kill_switch", "admin"},
    "auditor": {"read", "audit"},
}


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    email: str
    tenant_id: str
    role: str
    permissions: frozenset[str]
    token_id: str

    def can(self, permission: str) -> bool:
        return permission in self.permissions or "admin" in self.permissions


class AuthError(ValueError):
    pass


class TenantStore:
    """SQLite-backed identity store with tenant-scoped memberships.

    Production deployments should point this interface at PostgreSQL, but the
    schema and authorization semantics remain identical for local/Paper use.
    """

    def __init__(self, path: str | Path, *, signing_key: str | None = None):
        self.path = str(path)
        self.signing_key = (signing_key or os.environ.get("TENANT_TOKEN_SIGNING_KEY") or "").encode()
        if len(self.signing_key) < 32:
            raise ValueError("TENANT_TOKEN_SIGNING_KEY deve ter pelo menos 32 caracteres")
        self._init()

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self):
        with self._connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            );
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
                created_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'active'
            );
            CREATE TABLE IF NOT EXISTS memberships (
                tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL, created_at REAL NOT NULL,
                PRIMARY KEY (tenant_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                expires_at REAL NOT NULL, revoked_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
            """)

    @staticmethod
    def _hash_password(password: str, salt: bytes | None = None) -> str:
        if len(password) < 12:
            raise AuthError("a senha deve ter pelo menos 12 caracteres")
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return "scrypt$" + base64.urlsafe_b64encode(salt + digest).decode()

    @staticmethod
    def _verify_password(password: str, encoded: str) -> bool:
        try:
            raw = base64.urlsafe_b64decode(encoded.split("$", 1)[1].encode())
            salt, expected = raw[:16], raw[16:]
            actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
            return hmac.compare_digest(actual, expected)
        except (ValueError, IndexError):
            return False

    def create_tenant(self, name: str, *, tenant_id: str | None = None) -> str:
        tenant_id = tenant_id or "ten_" + secrets.token_urlsafe(12)
        with self._connect() as c:
            c.execute("INSERT INTO tenants(id,name,created_at) VALUES(?,?,?)", (tenant_id, name.strip(), time.time()))
        return tenant_id

    def create_user(self, email: str, password: str, *, user_id: str | None = None) -> str:
        user_id = user_id or "usr_" + secrets.token_urlsafe(12)
        with self._connect() as c:
            c.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,?,?)", (user_id, email.strip().lower(), self._hash_password(password), time.time()))
        return user_id

    def add_member(self, tenant_id: str, user_id: str, role: str) -> None:
        if role not in ROLES:
            raise AuthError("papel inválido")
        with self._connect() as c:
            c.execute("INSERT OR REPLACE INTO memberships(tenant_id,user_id,role,created_at) VALUES(?,?,?,?)", (tenant_id, user_id, role, time.time()))

    def login(self, email: str, password: str, tenant_id: str, *, ttl_seconds: int = 3600) -> str:
        with self._connect() as c:
            row = c.execute("SELECT * FROM users WHERE email=? AND status='active'", (email.strip().lower(),)).fetchone()
            if not row or not self._verify_password(password, row["password_hash"]):
                raise AuthError("credenciais inválidas")
            member = c.execute("SELECT role FROM memberships WHERE tenant_id=? AND user_id=?", (tenant_id, row["id"])).fetchone()
        if not member:
            raise AuthError("usuário não pertence ao tenant")
        session_id = "ses_" + secrets.token_urlsafe(18)
        expires = int(time.time()) + ttl_seconds
        with self._connect() as c:
            c.execute("INSERT INTO sessions(id,user_id,tenant_id,expires_at) VALUES(?,?,?,?)", (session_id, row["id"], tenant_id, expires))
        payload = {"sid": session_id, "uid": row["id"], "tid": tenant_id, "exp": expires}
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
        sig = hmac.new(self.signing_key, body.encode(), hashlib.sha256).hexdigest()
        return body + "." + sig

    def authenticate(self, token: str) -> Principal:
        try:
            body, signature = token.split(".", 1)
            expected = hmac.new(self.signing_key, body.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise AuthError("token inválido")
            payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AuthError("token inválido") from exc
        if int(payload.get("exp", 0)) <= int(time.time()):
            raise AuthError("token expirado")
        with self._connect() as c:
            row = c.execute("""SELECT u.email, u.status, s.expires_at, s.revoked_at, m.role
                FROM sessions s JOIN users u ON u.id=s.user_id
                JOIN memberships m ON m.user_id=s.user_id AND m.tenant_id=s.tenant_id
                WHERE s.id=? AND s.user_id=? AND s.tenant_id=?""", (payload.get("sid"), payload.get("uid"), payload.get("tid"))).fetchone()
        if not row or row["status"] != "active" or row["revoked_at"] or row["expires_at"] <= time.time():
            raise AuthError("sessão inválida")
        role = row["role"]
        return Principal(payload["uid"], row["email"], payload["tid"], role, frozenset(ROLE_PERMISSIONS[role]), payload["sid"])

    def revoke(self, token: str) -> None:
        payload = json.loads(base64.urlsafe_b64decode(token.split(".", 1)[0] + "=" * (-len(token.split(".", 1)[0]) % 4)))
        with self._connect() as c:
            c.execute("UPDATE sessions SET revoked_at=? WHERE id=?", (time.time(), payload.get("sid")))

    def list_tenants_for_user(self, user_id: str) -> list[dict[str, str]]:
        with self._connect() as c:
            return [dict(row) for row in c.execute("SELECT t.id,t.name,m.role FROM memberships m JOIN tenants t ON t.id=m.tenant_id WHERE m.user_id=? ORDER BY t.name", (user_id,))]

    def tenant_exists(self, tenant_id: str) -> bool:
        with self._connect() as c:
            return c.execute("SELECT 1 FROM tenants WHERE id=? AND status='active'", (tenant_id,)).fetchone() is not None

    def tenant_count(self) -> int:
        with self._connect() as c:
            return int(c.execute("SELECT COUNT(*) FROM tenants WHERE status='active'").fetchone()[0])


class PostgresTenantStore(TenantStore):
    """Production identity backend using PostgreSQL from DATABASE_URL."""

    def __init__(self, dsn: str, *, signing_key: str):
        self.path = dsn
        self.signing_key = signing_key.encode()
        if len(self.signing_key) < 32:
            raise ValueError("TENANT_TOKEN_SIGNING_KEY deve ter pelo menos 32 caracteres")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("psycopg[binary] é necessário para DATABASE_URL PostgreSQL") from exc
        self._psycopg = psycopg
        self._dict_row = dict_row
        self._init()

    def _connect(self):
        return self._psycopg.connect(self.path, row_factory=self._dict_row)

    def _init(self):
        with self._connect() as c:
            c.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            );
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL, status TEXT NOT NULL DEFAULT 'active'
            );
            CREATE TABLE IF NOT EXISTS memberships (
                tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (tenant_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                expires_at BIGINT NOT NULL, revoked_at DOUBLE PRECISION
            );
            CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
            """)

    def create_tenant(self, name: str, *, tenant_id: str | None = None) -> str:
        tenant_id = tenant_id or "ten_" + secrets.token_urlsafe(12)
        with self._connect() as c:
            c.execute("INSERT INTO tenants(id,name,created_at) VALUES(%s,%s,%s)", (tenant_id, name.strip(), time.time()))
        return tenant_id

    def create_user(self, email: str, password: str, *, user_id: str | None = None) -> str:
        user_id = user_id or "usr_" + secrets.token_urlsafe(12)
        with self._connect() as c:
            c.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(%s,%s,%s,%s)", (user_id, email.strip().lower(), self._hash_password(password), time.time()))
        return user_id

    def add_member(self, tenant_id: str, user_id: str, role: str) -> None:
        if role not in ROLES:
            raise AuthError("papel inválido")
        with self._connect() as c:
            c.execute("""INSERT INTO memberships(tenant_id,user_id,role,created_at) VALUES(%s,%s,%s,%s)
                ON CONFLICT (tenant_id,user_id) DO UPDATE SET role=EXCLUDED.role""", (tenant_id, user_id, role, time.time()))

    def login(self, email: str, password: str, tenant_id: str, *, ttl_seconds: int = 3600) -> str:
        with self._connect() as c:
            row = c.execute("SELECT * FROM users WHERE email=%s AND status='active'", (email.strip().lower(),)).fetchone()
            if not row or not self._verify_password(password, row["password_hash"]):
                raise AuthError("credenciais inválidas")
            member = c.execute("SELECT role FROM memberships WHERE tenant_id=%s AND user_id=%s", (tenant_id, row["id"])).fetchone()
        if not member:
            raise AuthError("usuário não pertence ao tenant")
        session_id = "ses_" + secrets.token_urlsafe(18)
        expires = int(time.time()) + ttl_seconds
        with self._connect() as c:
            c.execute("INSERT INTO sessions(id,user_id,tenant_id,expires_at) VALUES(%s,%s,%s,%s)", (session_id, row["id"], tenant_id, expires))
        payload = {"sid": session_id, "uid": row["id"], "tid": tenant_id, "exp": expires}
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
        sig = hmac.new(self.signing_key, body.encode(), hashlib.sha256).hexdigest()
        return body + "." + sig

    def authenticate(self, token: str) -> Principal:
        try:
            body, signature = token.split(".", 1)
            expected = hmac.new(self.signing_key, body.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise AuthError("token inválido")
            payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AuthError("token inválido") from exc
        if int(payload.get("exp", 0)) <= int(time.time()):
            raise AuthError("token expirado")
        with self._connect() as c:
            row = c.execute("""SELECT u.email, u.status, s.expires_at, s.revoked_at, m.role
                FROM sessions s JOIN users u ON u.id=s.user_id
                JOIN memberships m ON m.user_id=s.user_id AND m.tenant_id=s.tenant_id
                WHERE s.id=%s AND s.user_id=%s AND s.tenant_id=%s""", (payload.get("sid"), payload.get("uid"), payload.get("tid"))).fetchone()
        if not row or row["status"] != "active" or row["revoked_at"] or row["expires_at"] <= time.time():
            raise AuthError("sessão inválida")
        role = row["role"]
        return Principal(payload["uid"], row["email"], payload["tid"], role, frozenset(ROLE_PERMISSIONS[role]), payload["sid"])

    def revoke(self, token: str) -> None:
        body = token.split(".", 1)[0]
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        with self._connect() as c:
            c.execute("UPDATE sessions SET revoked_at=%s WHERE id=%s", (time.time(), payload.get("sid")))

    def tenant_count(self) -> int:
        with self._connect() as c:
            return int(c.execute("SELECT COUNT(*) AS count FROM tenants WHERE status='active'").fetchone()["count"])

    def list_tenants_for_user(self, user_id: str) -> list[dict[str, str]]:
        with self._connect() as c:
            rows = c.execute("""SELECT t.id,t.name,m.role FROM memberships m
                JOIN tenants t ON t.id=m.tenant_id WHERE m.user_id=%s ORDER BY t.name""", (user_id,)).fetchall()
        return [dict(row) for row in rows]

    def tenant_exists(self, tenant_id: str) -> bool:
        with self._connect() as c:
            return c.execute("SELECT 1 FROM tenants WHERE id=%s AND status='active'", (tenant_id,)).fetchone() is not None
