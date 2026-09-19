"""Migrações de schema VERSIONADAS, transacionais e verificadas (§1).

`CREATE TABLE IF NOT EXISTS` só cria tabela nova — não adiciona colunas a uma
tabela antiga. Aqui há um runner mínimo, sem dependências, que:
  - registra versões aplicadas em `schema_migrations`;
  - roda cada migração pendente em UMA transação;
  - adiciona colunas de forma idempotente (checando o catálogo, não engolindo
    erro de verdade);
  - VERIFICA o resultado (colunas existem) e LEVANTA se algo faltar.

Funciona em SQLite e PostgreSQL (o dialeto vem do chamador).
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

# Chave FIXA do lock de schema ('dz23'): serializa criação de tabelas/migrações
# entre processos. `CREATE TABLE IF NOT EXISTS` do Postgres NÃO é seguro sob
# concorrência (corrida no pg_type do tipo-linha) — o advisory lock resolve.
SCHEMA_LOCK_KEY = 0x647A3233


def _ph(dialect: str) -> str:
    return "%s" if dialect == "postgres" else "?"


def advisory_lock(conn: Any, dialect: str) -> None:
    """Trava de schema por transação (no-op no SQLite, que é single-writer)."""
    if dialect == "postgres":
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))


def column_exists(conn: Any, dialect: str, table: str, column: str) -> bool:
    if dialect == "postgres":
        cur = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name=%s AND column_name=%s", (table, column))
        return cur.fetchone() is not None
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any((r["name"] if hasattr(r, "keys") else r[1]) == column for r in rows)


def add_column_if_missing(conn: Any, dialect: str, table: str, column: str,
                          ddl_type: str) -> bool:
    """Adiciona a coluna se faltar (idempotente e seguro sob concorrência).

    Postgres usa `ADD COLUMN IF NOT EXISTS` nativo (pega ACCESS EXCLUSIVE, então
    dois processos se serializam sem erro). SQLite (single-writer) checa o
    catálogo e adiciona. Verifica o resultado; erro REAL propaga."""
    if dialect == "postgres":
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl_type}")
    else:
        if column_exists(conn, dialect, table, column):
            return False
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
    if not column_exists(conn, dialect, table, column):
        raise RuntimeError(f"migração falhou: coluna {table}.{column} não foi criada")
    return True


def _ensure_versions_table(conn: Any, dialect: str) -> None:
    advisory_lock(conn, dialect)         # serializa a criação (corrida de pg_type)
    ts = "DOUBLE PRECISION" if dialect == "postgres" else "REAL"
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                 f"version INTEGER PRIMARY KEY, applied_at {ts} NOT NULL)")


def applied_versions(conn: Any, dialect: str) -> set[int]:
    _ensure_versions_table(conn, dialect)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    out = set()
    for r in rows:
        out.add(int(r["version"] if hasattr(r, "keys") else r[0]))
    return out


def run_migrations(connect: Callable[[], Any], dialect: str,
                   migrations: Sequence[tuple[int, Callable[[Any, str], None]]]) -> list[int]:
    """Aplica migrações pendentes (por versão crescente), CADA UMA em sua
    transação. Verifica e registra. Devolve as versões aplicadas nesta execução.
    Não engole erro: qualquer falha aborta a transação daquela migração e sobe.
    """
    aplicadas: list[int] = []
    with connect() as c0:
        ja = applied_versions(c0, dialect)
    for version, fn in sorted(migrations, key=lambda m: m[0]):
        if version in ja:
            continue
        with connect() as c:            # transação própria (commit no exit)
            advisory_lock(c, dialect)   # serializa a migração entre processos
            fn(c, dialect)
            # ON CONFLICT DO NOTHING: dois processos aplicando a MESMA migração
            # concorrentemente não colidem na PK de schema_migrations.
            c.execute(f"INSERT INTO schema_migrations(version,applied_at) "
                      f"VALUES({_ph(dialect)},{_ph(dialect)}) "
                      f"ON CONFLICT (version) DO NOTHING", (version, time.time()))
            if dialect != "postgres":
                c.commit()
        aplicadas.append(version)
    return aplicadas


__all__ = ["column_exists", "add_column_if_missing", "applied_versions",
           "run_migrations"]
