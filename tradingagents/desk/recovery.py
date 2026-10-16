"""Prova auditável de que um backup local foi restaurado e validado."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradingagents.desk.backup import inspect_backup, restore_backup
from tradingagents.desk.storage import escrever_json_atomico

SCHEMA = "dz23.local-restore-proof/1"
PROOF_NAME = "local-restore-proof.json"
MAX_AGE = timedelta(days=30)
MAX_FUTURE_SKEW = timedelta(minutes=5)


class RecoveryProofError(ValueError):
    """O ensaio ou a prova de recuperação não é confiável."""


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
        raise RecoveryProofError("horário da prova inválido") from exc
    if parsed.tzinfo is None:
        raise RecoveryProofError("horário da prova não possui fuso")
    return parsed.astimezone(timezone.utc)


def _semantic_checks(restored: Path, items: list[str]) -> dict[str, int]:
    checks = {"sqlite": 0, "json": 0, "jsonl": 0}
    for rel in items:
        path = restored.joinpath(*rel.split("/"))
        suffix = path.suffix.lower()
        try:
            if suffix == ".db":
                uri = path.resolve().as_uri() + "?mode=ro"
                with sqlite3.connect(uri, uri=True) as connection:
                    result = connection.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise RecoveryProofError(f"integridade SQLite falhou em {rel}")
                checks["sqlite"] += 1
            elif suffix == ".json":
                json.loads(path.read_text(encoding="utf-8"))
                checks["json"] += 1
            elif suffix == ".jsonl":
                with path.open("r", encoding="utf-8") as stream:
                    for number, line in enumerate(stream, start=1):
                        if line.strip():
                            try:
                                json.loads(line)
                            except json.JSONDecodeError as exc:
                                raise RecoveryProofError(
                                    f"JSONL inválido em {rel}:{number}") from exc
                checks["jsonl"] += 1
        except (OSError, sqlite3.Error, json.JSONDecodeError) as exc:
            raise RecoveryProofError(
                f"validação semântica falhou em {rel}: {type(exc).__name__}") from exc
    return checks


def rehearse_local_restore(data_dir: str | Path, archive: str | Path, *,
                           now: datetime | None = None) -> dict:
    """Restaura em área isolada, valida conteúdo e grava prova no DATA_DIR."""
    data_dir, archive = Path(data_dir), Path(archive)
    backups = (data_dir / "backups").resolve()
    resolved_archive = archive.resolve()
    try:
        resolved_archive.relative_to(backups)
    except ValueError as exc:
        raise RecoveryProofError(
            "o backup ensaiado deve estar dentro de DATA_DIR/backups") from exc
    if resolved_archive.parent != backups:
        raise RecoveryProofError(
            "o backup ensaiado deve estar diretamente em DATA_DIR/backups")

    manifest = inspect_backup(resolved_archive)
    items = sorted(manifest["itens"])
    with tempfile.TemporaryDirectory() as temporary:
        restored = Path(temporary) / "restored"
        result = restore_backup(resolved_archive, restored)
        if sorted(result["restaurados"]) != items:
            raise RecoveryProofError("restore não devolveu todos os itens do manifesto")
        checks = _semantic_checks(restored, items)

    verified_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    proof = {
        "schema": SCHEMA,
        "resultado": "restaurado_e_verificado",
        "backup_arquivo": resolved_archive.name,
        "backup_sha256": _sha256(resolved_archive),
        "backup_criado_em": manifest.get("criado_em"),
        "verificado_em": verified_at.isoformat(),
        "quantidade_itens": len(items),
        "validacoes_semanticas": checks,
        "restauracao_isolada": True,
        "envio_de_ordens": False,
        "cobre_postgresql_externo": False,
    }
    escrever_json_atomico(data_dir / PROOF_NAME, proof)
    return proof


def validate_local_restore_proof(data_dir: str | Path, *,
                                 now: datetime | None = None,
                                 max_age: timedelta = MAX_AGE) -> dict:
    """Valida prova, idade e vínculo com o backup que ainda está no volume."""
    data_dir = Path(data_dir)
    proof_path = data_dir / PROOF_NAME
    base = {
        "valida": False,
        "verificado_em": "",
        "idade_maxima_dias": max_age.days,
        "cobre_postgresql_externo": False,
    }
    if not proof_path.is_file() or proof_path.is_symlink():
        return {**base, "motivo": "ensaio de restore local ainda não registrado"}
    try:
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        if not isinstance(proof, dict) or proof.get("schema") != SCHEMA:
            raise RecoveryProofError("schema da prova incompatível")
        if proof.get("resultado") != "restaurado_e_verificado":
            raise RecoveryProofError("resultado do ensaio não foi aprovado")
        if proof.get("restauracao_isolada") is not True:
            raise RecoveryProofError("restore não foi feito em área isolada")
        if proof.get("envio_de_ordens") is not False:
            raise RecoveryProofError("prova não garante ausência de ordens")
        verified = _parse_time(proof.get("verificado_em"))
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if verified > current + MAX_FUTURE_SKEW:
            raise RecoveryProofError("prova possui horário futuro")
        if current - verified > max_age:
            raise RecoveryProofError("ensaio de restore expirado")

        filename = str(proof.get("backup_arquivo") or "")
        if not filename or Path(filename).name != filename:
            raise RecoveryProofError("nome do backup inválido")
        archive = data_dir / "backups" / filename
        if not archive.is_file() or archive.is_symlink():
            raise RecoveryProofError("backup ensaiado não está mais disponível")
        expected_hash = str(proof.get("backup_sha256") or "")
        if len(expected_hash) != 64 or _sha256(archive) != expected_hash:
            raise RecoveryProofError("hash do backup ensaiado divergiu")
        manifest = inspect_backup(archive)
        if len(manifest["itens"]) != proof.get("quantidade_itens"):
            raise RecoveryProofError("quantidade de itens da prova divergiu")
    except (OSError, json.JSONDecodeError, RecoveryProofError, ValueError) as exc:
        return {**base, "motivo": str(exc)}

    return {
        **base,
        "valida": True,
        "verificado_em": verified.isoformat(),
        "backup_arquivo": filename,
        "quantidade_itens": len(manifest["itens"]),
        "motivo": "backup local restaurado e validado em área isolada",
    }


__all__ = [
    "RecoveryProofError", "PROOF_NAME", "SCHEMA", "MAX_AGE",
    "rehearse_local_restore", "validate_local_restore_proof",
]
