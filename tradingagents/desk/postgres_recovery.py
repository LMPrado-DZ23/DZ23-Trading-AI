"""Backup lógico e prova de restore do estado PostgreSQL do DZ23."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradingagents.desk.storage import escrever_json_atomico

SCHEMA = "dz23.postgres-restore-proof/1"
PROOF_NAME = "postgres-restore-proof.json"
MAX_AGE = timedelta(days=30)
MAX_FUTURE_SKEW = timedelta(minutes=5)

# Somente tabelas criadas pelo produto. Um banco Supabase pode conter muitas
# outras tabelas; incluí-las seria ampliar o backup e capturar dados alheios.
DZ23_TABLES = (
    "dz23_desk_state",
    "jobs",
    "memberships",
    "sessions",
    "subscriptions",
    "tenants",
    "users",
)
REQUIRED_TABLES = frozenset({"dz23_desk_state", "jobs"})

_PG_ENV = {
    "dbname": "PGDATABASE",
    "host": "PGHOST",
    "hostaddr": "PGHOSTADDR",
    "port": "PGPORT",
    "user": "PGUSER",
    "password": "PGPASSWORD",
    "sslmode": "PGSSLMODE",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcrl": "PGSSLCRL",
    "sslcrldir": "PGSSLCRLDIR",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
    "connect_timeout": "PGCONNECT_TIMEOUT",
}
_PROCESS_ENV_ALLOWLIST = frozenset({
    "COMSPEC",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
})


class PostgresRecoveryError(ValueError):
    """O ensaio PostgreSQL não é isolado, completo ou verificável."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_time(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PostgresRecoveryError("horário da prova PostgreSQL inválido") from exc
    if parsed.tzinfo is None:
        raise PostgresRecoveryError("horário da prova PostgreSQL não possui fuso")
    return parsed.astimezone(timezone.utc)


def _connect(dsn: str):
    try:
        import psycopg
        return psycopg.connect(dsn, connect_timeout=5)
    except Exception as exc:
        raise PostgresRecoveryError(
            f"conexão PostgreSQL indisponível ({type(exc).__name__})") from None


def _connection_params(dsn: str) -> dict[str, str]:
    if not isinstance(dsn, str) or not dsn.strip():
        raise PostgresRecoveryError("DSN PostgreSQL ausente")
    try:
        from psycopg.conninfo import conninfo_to_dict
        params = {key: str(value) for key, value in conninfo_to_dict(dsn).items() if value}
    except Exception as exc:
        raise PostgresRecoveryError(
            f"DSN PostgreSQL inválida ({type(exc).__name__})") from None
    if not params.get("dbname"):
        raise PostgresRecoveryError("DSN PostgreSQL não identifica o banco")
    return params


def _tool_environment(dsn: str) -> tuple[dict[str, str], str]:
    params = _connection_params(dsn)
    # Não herda PG* nem segredos de outros provedores. Um PGPASSFILE/PGSERVICE
    # esquecido poderia redirecionar o dump; uma API key não tem motivo para
    # chegar ao processo filho.
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() in _PROCESS_ENV_ALLOWLIST
    }
    for source, target in _PG_ENV.items():
        if source in params:
            environment[target] = params[source]
    environment.setdefault("PGCONNECT_TIMEOUT", "5")
    return environment, params["dbname"]


def _database_identity(dsn: str) -> str:
    """Impressão não reversível para vincular a prova ao banco atual."""
    with _connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_database(), COALESCE(inet_server_addr()::text, 'local'), "
            "inet_server_port(), (SELECT oid FROM pg_database "
            "WHERE datname=current_database()), current_setting('server_version_num')"
        )
        row = cursor.fetchone()
    payload = json.dumps(list(row), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _existing_public_tables(dsn: str) -> list[str]:
    with _connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE' "
            "ORDER BY table_name"
        )
        return [str(row[0]) for row in cursor.fetchall()]


def _database_manifest(dsn: str) -> dict:
    """Hash determinístico de esquema e conteúdo, sem persistir linhas."""
    try:
        from psycopg import sql
    except ImportError as exc:
        raise PostgresRecoveryError("psycopg[binary] é obrigatório") from exc

    with _connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE' "
            "AND table_name = ANY(%s) ORDER BY table_name",
            (list(DZ23_TABLES),),
        )
        tables = [str(row[0]) for row in cursor.fetchall()]
        missing = sorted(REQUIRED_TABLES - set(tables))
        if missing:
            raise PostgresRecoveryError(
                "tabelas PostgreSQL obrigatórias ausentes: " + ", ".join(missing)
            )

        manifest: dict[str, dict[str, object]] = {}
        for table in tables:
            cursor.execute(
                "SELECT column_name, data_type, is_nullable, COALESCE(column_default, '') "
                "FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name=%s ORDER BY ordinal_position",
                (table,),
            )
            columns = [list(row) for row in cursor.fetchall()]
            schema_hash = hashlib.sha256(
                json.dumps(columns, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            ).hexdigest()

            digest = hashlib.sha256()
            count = 0
            cursor.execute(
                sql.SQL(
                    "SELECT row_to_json(t)::text FROM {}.{} AS t "
                    "ORDER BY row_to_json(t)::text COLLATE \"C\""
                ).format(sql.Identifier("public"), sql.Identifier(table))
            )
            for row in cursor:
                digest.update(str(row[0]).encode("utf-8"))
                digest.update(b"\n")
                count += 1
            manifest[table] = {
                "schema_sha256": schema_hash,
                "rows_sha256": digest.hexdigest(),
                "row_count": count,
            }
    return {"tables": tables, "details": manifest}


def _manifest_hash(manifest: dict) -> str:
    canonical = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _run_pg_tool(
    executable: str,
    arguments: list[str],
    *,
    dsn: str,
    timeout_seconds: int,
) -> None:
    resolved = shutil.which(executable)
    if not resolved:
        raise PostgresRecoveryError(f"{executable} não encontrado no PATH")
    environment, database = _tool_environment(dsn)
    command = [resolved]
    if executable == "pg_restore":
        command.extend(["--dbname", database])
    command.extend(arguments)
    try:
        subprocess.run(
            command,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        raise PostgresRecoveryError(
            f"{executable} excedeu o tempo limite") from None
    except subprocess.CalledProcessError as exc:
        # stdout/stderr podem conter host, usuário ou caminhos: não os propaga.
        raise PostgresRecoveryError(
            f"{executable} falhou (código {exc.returncode})") from None


def rehearse_postgres_restore(
    data_dir: str | Path,
    source_dsn: str,
    target_dsn: str,
    *,
    now: datetime | None = None,
    timeout_seconds: int = 300,
) -> dict:
    """Faz dump e restore em banco vazio distinto e grava prova auditável."""
    if not isinstance(timeout_seconds, int) or not 10 <= timeout_seconds <= 3600:
        raise PostgresRecoveryError("timeout deve estar entre 10 e 3600 segundos")
    data_dir = Path(data_dir)
    backups = data_dir / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    if backups.is_symlink():
        raise PostgresRecoveryError("DATA_DIR/backups não pode ser link simbólico")

    source_identity = _database_identity(source_dsn)
    target_identity = _database_identity(target_dsn)
    if source_identity == target_identity:
        raise PostgresRecoveryError("origem e destino de restore são o mesmo banco")
    target_tables = _existing_public_tables(target_dsn)
    if target_tables:
        raise PostgresRecoveryError(
            "banco de restore não está vazio; nenhuma alteração foi feita")

    before = _database_manifest(source_dsn)
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    archive_name = (
        "postgres-" + timestamp.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8] + ".dump"
    )
    archive = backups / archive_name
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".postgres-dump-", suffix=".tmp", dir=backups
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        arguments = [
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(temporary),
        ]
        for table in before["tables"]:
            arguments.extend(["--table", f"public.{table}"])
        _run_pg_tool(
            "pg_dump", arguments, dsn=source_dsn, timeout_seconds=timeout_seconds
        )
        after = _database_manifest(source_dsn)
        if before != after:
            raise PostgresRecoveryError(
                "a origem mudou durante o backup; pare as escritas e repita"
            )
        os.chmod(temporary, 0o600)
        os.replace(temporary, archive)
    finally:
        if temporary.exists():
            temporary.unlink()

    try:
        _run_pg_tool(
            "pg_restore",
            ["--exit-on-error", "--no-owner", "--no-privileges", str(archive)],
            dsn=target_dsn,
            timeout_seconds=timeout_seconds,
        )
        restored = _database_manifest(target_dsn)
        if restored != after:
            raise PostgresRecoveryError(
                "conteúdo restaurado diverge do backup lógico"
            )
    except Exception:
        # O dump íntegro é preservado para perícia; o destino nunca é limpo
        # automaticamente porque agora contém estado externo potencialmente útil.
        raise

    proof = {
        "schema": SCHEMA,
        "resultado": "restaurado_e_verificado",
        "backup_arquivo": archive.name,
        "backup_sha256": _sha256(archive),
        "verificado_em": timestamp.isoformat(),
        "source_fingerprint": source_identity,
        "target_fingerprint": target_identity,
        "manifest_sha256": _manifest_hash(after),
        "tabelas": list(after["tables"]),
        "quantidade_tabelas": len(after["tables"]),
        "restauracao_isolada": True,
        "destino_estava_vazio": True,
        "origem_estavel_durante_backup": True,
        "envio_de_ordens": False,
    }
    escrever_json_atomico(data_dir / PROOF_NAME, proof)
    return proof


def validate_postgres_restore_proof(
    data_dir: str | Path,
    database_url: str,
    *,
    now: datetime | None = None,
    max_age: timedelta = MAX_AGE,
) -> dict:
    """Valida idade, dump retido e vínculo com o PostgreSQL configurado."""
    data_dir = Path(data_dir)
    base = {
        "necessaria": True,
        "valida": False,
        "verificado_em": "",
        "idade_maxima_dias": max_age.days,
        "cobre_postgresql_externo": True,
    }
    proof_path = data_dir / PROOF_NAME
    if not proof_path.is_file() or proof_path.is_symlink():
        return {**base, "motivo": "ensaio de restore PostgreSQL ainda não registrado"}
    try:
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        if not isinstance(proof, dict) or proof.get("schema") != SCHEMA:
            raise PostgresRecoveryError("schema da prova PostgreSQL incompatível")
        required_true = (
            "restauracao_isolada",
            "destino_estava_vazio",
            "origem_estavel_durante_backup",
        )
        if proof.get("resultado") != "restaurado_e_verificado":
            raise PostgresRecoveryError("restore PostgreSQL não foi aprovado")
        if any(proof.get(field) is not True for field in required_true):
            raise PostgresRecoveryError("prova PostgreSQL não garante isolamento")
        if proof.get("envio_de_ordens") is not False:
            raise PostgresRecoveryError("prova PostgreSQL não garante ausência de ordens")
        verified = _parse_time(proof.get("verificado_em"))
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if verified > current + MAX_FUTURE_SKEW:
            raise PostgresRecoveryError("prova PostgreSQL possui horário futuro")
        if current - verified > max_age:
            raise PostgresRecoveryError("ensaio de restore PostgreSQL expirado")

        filename = str(proof.get("backup_arquivo") or "")
        if not filename or Path(filename).name != filename:
            raise PostgresRecoveryError("nome do dump PostgreSQL inválido")
        archive = data_dir / "backups" / filename
        if not archive.is_file() or archive.is_symlink():
            raise PostgresRecoveryError("dump PostgreSQL ensaiado não está disponível")
        expected_hash = str(proof.get("backup_sha256") or "")
        if len(expected_hash) != 64 or _sha256(archive) != expected_hash:
            raise PostgresRecoveryError("hash do dump PostgreSQL divergiu")
        tables = proof.get("tabelas")
        if not isinstance(tables, list) or not REQUIRED_TABLES.issubset(set(tables)):
            raise PostgresRecoveryError("prova não cobre tabelas PostgreSQL obrigatórias")
        if proof.get("quantidade_tabelas") != len(tables):
            raise PostgresRecoveryError("quantidade de tabelas PostgreSQL divergiu")
        for field in ("source_fingerprint", "target_fingerprint", "manifest_sha256"):
            value = str(proof.get(field) or "")
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise PostgresRecoveryError(f"{field} inválido")
        if proof["source_fingerprint"] == proof["target_fingerprint"]:
            raise PostgresRecoveryError("prova usou o banco de origem como destino")
        if _database_identity(database_url) != proof["source_fingerprint"]:
            raise PostgresRecoveryError("prova pertence a outro banco PostgreSQL")
    except (OSError, json.JSONDecodeError, PostgresRecoveryError, ValueError) as exc:
        return {**base, "motivo": str(exc)}

    return {
        **base,
        "valida": True,
        "verificado_em": verified.isoformat(),
        "backup_arquivo": filename,
        "quantidade_tabelas": len(tables),
        "motivo": "backup lógico restaurado e comparado em banco isolado",
    }


def not_required_status() -> dict:
    return {
        "necessaria": False,
        "valida": True,
        "verificado_em": "",
        "idade_maxima_dias": MAX_AGE.days,
        "cobre_postgresql_externo": False,
        "motivo": "PostgreSQL externo não está configurado nesta instalação",
    }


__all__ = [
    "DZ23_TABLES",
    "MAX_AGE",
    "PROOF_NAME",
    "REQUIRED_TABLES",
    "SCHEMA",
    "PostgresRecoveryError",
    "not_required_status",
    "rehearse_postgres_restore",
    "validate_postgres_restore_proof",
]
