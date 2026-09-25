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
from typing import Any

# Rate limit de login (§4): persistente no banco de identidade — sobrevive a
# restart e é COMPARTILHADO entre instâncias (ao contrário de contador em RAM).
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW = 900          # janela de contagem de falhas (s)
LOGIN_BASE_LOCK = 30       # trava inicial ao estourar o limite (s)
LOGIN_MAX_LOCK = 3600      # teto do backoff exponencial (s)
# Limiares SEPARADOS (max_fails, base_lock) por tipo de chave (§6). O par é
# estrito; IP e identidade têm limiar alto para não trancar NAT/vítima cedo.
_LOGIN_KINDS = {"pair": (5, 30), "ip": (50, 15), "email": (12, 30)}


def _dummy_scrypt() -> None:
    """scrypt DESCARTÁVEL com o mesmo custo do hash real: o tempo de resposta a
    um e-mail inexistente não deve denunciar a existência de contas (§6)."""
    hashlib.scrypt(b"x" * 12, salt=b"\0" * 16, n=2 ** 14, r=8, p=1)

ROLES = frozenset({"viewer", "analyst", "operator", "risk_manager", "admin", "auditor"})
ROLE_PERMISSIONS = {
    "viewer": {"read"},
    "analyst": {"read", "analysis", "agency_submit"},
    "operator": {"read", "analysis", "agency_submit", "agency_run", "paper", "shadow", "demo"},
    "risk_manager": {"read", "analysis", "agency_submit", "agency_run", "agency_approve", "paper", "shadow", "demo", "risk", "kill_switch"},
    "admin": {"read", "analysis", "agency_submit", "agency_run", "agency_approve", "agent_admin", "paper", "shadow", "demo", "risk", "kill_switch", "admin"},
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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self) -> None:
        with self._connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            );
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
                created_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                oauth_only INTEGER NOT NULL DEFAULT 0
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
            CREATE TABLE IF NOT EXISTS login_attempts (
                key TEXT PRIMARY KEY, fails INTEGER NOT NULL,
                first_at REAL NOT NULL, locked_until REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS step_up_tokens (
                token_hash TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL, purpose TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                expires_at REAL NOT NULL, used_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
            """)
        self._migrate()

    def _dialect(self) -> str:
        return "sqlite"

    def _migrate(self) -> None:
        """§1/§6: migração versionada e verificada do schema de identidade
        (ex.: users.oauth_only em bancos antigos)."""
        from tradingagents.dbschema import add_column_if_missing, run_migrations

        def _m1(c: Any, d: str) -> None:
            add_column_if_missing(c, d, "users", "oauth_only", "INTEGER NOT NULL DEFAULT 0")

        run_migrations(self._connect, self._dialect(), [(1, _m1)], component="identity")

    def _sql(self, query: str) -> str:
        """SQLite usa '?'. Postgres sobrescreve para '%s'."""
        return query

    def _hmac(self, s: str) -> str:
        return hmac.new(self.signing_key, s.encode(), hashlib.sha256).hexdigest()

    def _login_key(self, ip: str, email: str) -> str:
        # Compat: chave do PAR ip|email (HMAC; sem PII em claro).
        ipn = (ip or "?").strip()
        em = (email or "").strip().lower()
        return self._hmac(f"pair|{ipn}|{em}")

    def _login_keys(self, ip: str, email: str) -> dict[str, str]:
        """Chaves SEPARADAS (§6): por PAR (ip+email), por IP e por identidade.
        Limiar mais alto no IP/identidade evita lockout global de uma vítima."""
        ipn = (ip or "?").strip()
        em = (email or "").strip().lower()
        return {"pair": self._hmac(f"pair|{ipn}|{em}"),
                "ip": self._hmac(f"ip|{ipn}"),
                "email": self._hmac(f"email|{em}")}

    def _token_hash(self, token: str) -> str:
        return hmac.new(self.signing_key, token.encode(), hashlib.sha256).hexdigest()

    def create_step_up(self, tenant_id: str, user_id: str, purpose: str,
                       session_id: str = "", ttl_seconds: int = 300) -> str:
        """Emite um TOKEN de step-up: aleatório, escopado (tenant/user/purpose/
        SESSÃO), curto (<=5min), single-use. Guardado HASHEADO (HMAC) — nunca em
        claro. Devolve o token puro UMA vez (não é recuperável depois)."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._connect() as c:
            c.execute(self._sql("DELETE FROM step_up_tokens WHERE expires_at < ?"), (now,))
            c.execute(self._sql(
                "INSERT INTO step_up_tokens(token_hash,tenant_id,user_id,purpose,"
                "session_id,expires_at,used_at) VALUES(?,?,?,?,?,?,NULL)"),
                (self._token_hash(token), tenant_id, user_id, purpose,
                 session_id, now + ttl_seconds))
        return token

    def consume_step_up(self, token: str, tenant_id: str, user_id: str,
                        purpose: str, session_id: str = "") -> bool:
        """Valida e REVOGA o token numa única operação atômica (single-use). Além
        de tenant/user/purpose, exige a MESMA sessão que o emitiu — um token
        vazado para outra sessão não vale."""
        now = time.time()
        with self._connect() as c:
            cur = c.execute(self._sql(
                "UPDATE step_up_tokens SET used_at=? WHERE token_hash=? "
                "AND used_at IS NULL AND expires_at>=? AND tenant_id=? "
                "AND user_id=? AND purpose=? AND session_id=?"),
                (now, self._token_hash(token), now, tenant_id, user_id, purpose, session_id))
        return (getattr(cur, "rowcount", 0) or 0) == 1

    def revoke_step_up_for_session(self, session_id: str) -> None:
        """Revoga todos os step-up de uma sessão (chamado no logout)."""
        with self._connect() as c:
            c.execute(self._sql("DELETE FROM step_up_tokens WHERE session_id=?"),
                      (session_id,))

    def login_lock_seconds(self, ip: str, email: str) -> int:
        """Bloqueio efetivo = o MAIOR entre os limites por par/IP/identidade."""
        now = time.time()
        pior = 0.0
        with self._connect() as c:
            for k in self._login_keys(ip, email).values():
                row = c.execute(self._sql(
                    "SELECT locked_until FROM login_attempts WHERE key=?"), (k,)).fetchone()
                if row:
                    rem = float(row["locked_until"] or 0) - now
                    pior = max(pior, rem)
        return int(pior) + 1 if pior > 0 else 0

    def record_login_failure(self, ip: str, email: str) -> int:
        """Incrementa (SQL ATÔMICO) os três contadores e aplica backoff. Faz
        cleanup/TTL de linhas velhas. Limiares separados evitam lockout global."""
        now = time.time()
        with self._connect() as c:
            # cleanup/TTL: some com o que já não tranca e saiu da janela.
            c.execute(self._sql("DELETE FROM login_attempts WHERE locked_until < ? "
                                "AND first_at < ?"), (now, now - LOGIN_WINDOW))
            pair_fails = 0
            for kind, k in self._login_keys(ip, email).items():
                maxf, base = _LOGIN_KINDS[kind]
                # incremento ATÔMICO; reinicia a janela se a linha ficou velha.
                c.execute(self._sql(
                    "INSERT INTO login_attempts(key,fails,first_at,locked_until) "
                    "VALUES(?,1,?,0) ON CONFLICT (key) DO UPDATE SET "
                    "fails = CASE WHEN login_attempts.first_at < ? THEN 1 "
                    "             ELSE login_attempts.fails + 1 END, "
                    "first_at = CASE WHEN login_attempts.first_at < ? THEN ? "
                    "                ELSE login_attempts.first_at END"),
                    (k, now, now - LOGIN_WINDOW, now - LOGIN_WINDOW, now))
                row = c.execute(self._sql("SELECT fails FROM login_attempts WHERE key=?"),
                                (k,)).fetchone()
                fails = int(row["fails"]) if row else 1
                if kind == "pair":
                    pair_fails = fails
                if fails >= maxf:
                    lock = min(LOGIN_MAX_LOCK, base * (2 ** (fails - maxf)))
                    c.execute(self._sql("UPDATE login_attempts SET locked_until=? WHERE key=?"),
                              (now + lock, k))
        return pair_fails

    def clear_login_failures(self, ip: str, email: str) -> None:
        with self._connect() as c:
            for k in self._login_keys(ip, email).values():
                c.execute(self._sql("DELETE FROM login_attempts WHERE key=?"), (k,))

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

    def add_user_to_tenant(self, tenant_id: str, email: str, password: str,
                           role: str = "viewer") -> str:
        """§9: cria usuário + vínculo num tenant EXISTENTE, ATÔMICO e validando
        ANTES (papel válido e tenant existe). Funciona em SQLite e Postgres
        (via self._sql). E-mail repetido vira AuthError; nada fica pela metade."""
        if role not in ROLES:
            raise AuthError("papel inválido")
        if not self.tenant_exists(tenant_id):
            raise AuthError("tenant inexistente")
        user_id = "usr_" + secrets.token_urlsafe(12)
        pwd = self._hash_password(password)        # valida tamanho antes da tx
        now = time.time()
        try:
            with self._connect() as c:
                c.execute(self._sql("INSERT INTO users(id,email,password_hash,created_at) "
                                    "VALUES(?,?,?,?)"),
                          (user_id, email.strip().lower(), pwd, now))
                c.execute(self._sql("INSERT INTO memberships(tenant_id,user_id,role,"
                                    "created_at) VALUES(?,?,?,?)"),
                          (tenant_id, user_id, role, now))
        except Exception as exc:                   # noqa: BLE001
            raise AuthError("não foi possível criar o usuário (e-mail já existe?)") from exc
        return user_id

    def create_account(self, agency_name: str, email: str, password: str, *,
                       role: str = "admin", tenant_id: str | None = None,
                       user_id: str | None = None, oauth_only: bool = False) -> tuple[str, str]:
        """Cria tenant + usuário + vínculo em UMA transação.

        Atômico: qualquer falha desfaz tudo — nunca fica tenant órfão nem
        usuário sem vínculo. E-mail repetido (UNIQUE) vira AuthError; o
        chamador responde de forma genérica (anti-enumeração). A senha é
        validada e o hash calculado ANTES de abrir a transação.
        """
        if role not in ROLES:
            raise AuthError("papel inválido")
        tenant_id = tenant_id or "ten_" + secrets.token_urlsafe(12)
        user_id = user_id or "usr_" + secrets.token_urlsafe(12)
        pwd_hash = self._hash_password(password)
        now = time.time()
        try:
            with self._connect() as c:      # commit no sucesso, rollback na exceção
                c.execute("INSERT INTO tenants(id,name,created_at) VALUES(?,?,?)",
                          (tenant_id, agency_name.strip(), now))
                c.execute("INSERT INTO users(id,email,password_hash,created_at,oauth_only) "
                          "VALUES(?,?,?,?,?)",
                          (user_id, email.strip().lower(), pwd_hash, now, 1 if oauth_only else 0))
                c.execute("INSERT INTO memberships(tenant_id,user_id,role,created_at) "
                          "VALUES(?,?,?,?)", (tenant_id, user_id, role, now))
        except sqlite3.IntegrityError as exc:
            raise AuthError("não foi possível concluir o cadastro") from exc
        return tenant_id, user_id

    def is_oauth_only(self, user_id: str) -> bool:
        """Conta criada por OAuth (senha interna aleatória): step-up por senha NÃO
        é fluxo válido (§6). O chamador bloqueia export/import dessas contas."""
        with self._connect() as c:
            row = c.execute(self._sql("SELECT oauth_only FROM users WHERE id=?"),
                            (user_id,)).fetchone()
        return bool(row and (row["oauth_only"] if hasattr(row, "keys") else row[0]))

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

    def _verify_credentials(self, email: str, password: str) -> str:
        """Prova e-mail+senha SEM escopo de tenant. Falha SEMPRE com o mesmo
        erro genérico (anti-enumeração). Faz um scrypt DUMMY quando o e-mail não
        existe, para o tempo de resposta não denunciar contas (§6)."""
        with self._connect() as c:
            row = c.execute("SELECT id,password_hash FROM users WHERE email=? "
                            "AND status='active'",
                            (email.strip().lower(),)).fetchone()
        if not row:
            _dummy_scrypt()
            raise AuthError("credenciais inválidas")
        if not self._verify_password(password, row["password_hash"]):
            raise AuthError("credenciais inválidas")
        return row["id"]

    def login_by_email(self, email: str, password: str, *,
                       ttl_seconds: int = 3600) -> dict:
        """Login SEM Tenant ID. Prova a senha, descobre os workspaces do
        usuário e emite a sessão (um só) ou devolve a seleção (vários). A
        seleção só aparece DEPOIS da senha correta — nunca enumera contas.

        Usa apenas métodos polimórficos (`_verify_credentials`, `login`,
        `list_tenants_for_user`), então funciona igual em SQLite e Postgres.
        """
        user_id = self._verify_credentials(email, password)
        tenants = self.list_tenants_for_user(user_id)
        if not tenants:
            raise AuthError("credenciais inválidas")
        if len(tenants) == 1:
            t = tenants[0]
            token = self.login(email, password, t["id"],
                               ttl_seconds=ttl_seconds)
            return {"token": token, "tenant_id": t["id"], "role": t["role"]}
        return {"select": [{"tenant_id": t["id"], "name": t["name"],
                            "role": t["role"]} for t in tenants]}

    def find_membership_by_email(self, email: str) -> tuple[str | None, str | None]:
        """Login social: descobre a qual agência o e-mail pertence."""
        email = (email or "").strip().lower()
        with self._connect() as c:
            row = c.execute("SELECT id FROM users WHERE email=? AND status='active'",
                            (email,)).fetchone()
            if not row:
                return None, None
            member = c.execute(
                "SELECT tenant_id FROM memberships WHERE user_id=? "
                "ORDER BY created_at LIMIT 1", (row["id"],)).fetchone()
        return (member["tenant_id"], row["id"]) if member else (None, None)

    def issue_token_for_user(self, user_id: str, tenant_id: str, *,
                             ttl_seconds: int = 3600) -> str:
        """Emite sessão SEM senha — só para identidade já provada por OAuth.
        Exige que o vínculo usuário↔agência exista."""
        with self._connect() as c:
            member = c.execute(
                "SELECT role FROM memberships WHERE tenant_id=? AND user_id=?",
                (tenant_id, user_id)).fetchone()
            if not member:
                raise AuthError("usuário não pertence ao tenant")
            user = c.execute("SELECT status FROM users WHERE id=?", (user_id,)).fetchone()
            if not user or user["status"] != "active":
                raise AuthError("usuário inativo")
        session_id = "ses_" + secrets.token_urlsafe(18)
        expires = int(time.time()) + ttl_seconds
        with self._connect() as c:
            c.execute("INSERT INTO sessions(id,user_id,tenant_id,expires_at) "
                      "VALUES(?,?,?,?)", (session_id, user_id, tenant_id, expires))
        payload = {"sid": session_id, "uid": user_id, "tid": tenant_id, "exp": expires}
        body = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
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

    def list_members(self, tenant_id: str) -> list[dict[str, str]]:
        with self._connect() as c:
            rows = c.execute("""SELECT u.id,u.email,u.status,m.role,m.created_at
                FROM memberships m JOIN users u ON u.id=m.user_id
                WHERE m.tenant_id=? ORDER BY u.email""", (tenant_id,)).fetchall()
        return [dict(row) for row in rows]

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

    def _connect(self) -> Any:      # psycopg.Connection
        return self._psycopg.connect(self.path, row_factory=self._dict_row)

    def _init(self) -> None:
        from tradingagents.dbschema import SCHEMA_LOCK_KEY
        with self._connect() as c:
            # Serializa a criação de tabelas entre processos (corrida de pg_type).
            c.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
            c.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            );
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                oauth_only INTEGER NOT NULL DEFAULT 0
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
            CREATE TABLE IF NOT EXISTS login_attempts (
                key TEXT PRIMARY KEY, fails INTEGER NOT NULL,
                first_at DOUBLE PRECISION NOT NULL, locked_until DOUBLE PRECISION NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS step_up_tokens (
                token_hash TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL, purpose TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                expires_at DOUBLE PRECISION NOT NULL, used_at DOUBLE PRECISION
            );
            CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
            """)
        self._migrate()

    def _dialect(self) -> str:
        return "postgres"

    def _sql(self, query: str) -> str:
        return query.replace("?", "%s")

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

    def create_account(self, agency_name: str, email: str, password: str, *,
                       role: str = "admin", tenant_id: str | None = None,
                       user_id: str | None = None, oauth_only: bool = False) -> tuple[str, str]:
        """Versão transacional para Postgres: tenant + usuário + vínculo numa
        única transação (commit no sucesso, rollback em qualquer falha)."""
        if role not in ROLES:
            raise AuthError("papel inválido")
        tenant_id = tenant_id or "ten_" + secrets.token_urlsafe(12)
        user_id = user_id or "usr_" + secrets.token_urlsafe(12)
        pwd_hash = self._hash_password(password)
        now = time.time()
        try:
            with self._connect() as c:      # psycopg: commit no exit, rollback na exceção
                c.execute("INSERT INTO tenants(id,name,created_at) VALUES(%s,%s,%s)",
                          (tenant_id, agency_name.strip(), now))
                c.execute("INSERT INTO users(id,email,password_hash,created_at,oauth_only) "
                          "VALUES(%s,%s,%s,%s,%s)",
                          (user_id, email.strip().lower(), pwd_hash, now, 1 if oauth_only else 0))
                c.execute("INSERT INTO memberships(tenant_id,user_id,role,created_at) "
                          "VALUES(%s,%s,%s,%s)", (tenant_id, user_id, role, now))
        except self._psycopg.errors.UniqueViolation as exc:
            raise AuthError("não foi possível concluir o cadastro") from exc
        return tenant_id, user_id

    def _verify_credentials(self, email: str, password: str) -> str:
        with self._connect() as c:
            row = c.execute("SELECT id,password_hash FROM users WHERE email=%s "
                            "AND status='active'",
                            (email.strip().lower(),)).fetchone()
        if not row:
            _dummy_scrypt()                     # §6: iguala o tempo p/ e-mail inexistente
            raise AuthError("credenciais inválidas")
        if not self._verify_password(password, row["password_hash"]):
            raise AuthError("credenciais inválidas")
        return row["id"]

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

    def list_members(self, tenant_id: str) -> list[dict[str, str]]:
        with self._connect() as c:
            rows = c.execute("""SELECT u.id,u.email,u.status,m.role,m.created_at
                FROM memberships m JOIN users u ON u.id=m.user_id
                WHERE m.tenant_id=%s ORDER BY u.email""", (tenant_id,)).fetchall()
        return [dict(row) for row in rows]
