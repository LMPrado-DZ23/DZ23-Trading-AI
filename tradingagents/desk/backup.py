"""Backup verificável do estado operacional local do DZ23.

O pacote cobre o estado persistido no ``DATA_DIR``. Estado mantido em um
PostgreSQL externo precisa de backup lógico próprio; copiar o volume local não
substitui esse procedimento.

Regras de segurança:
- somente arquivos e diretórios persistentes conhecidos entram no pacote;
- links simbólicos, temporários, WAL/SHM e o próprio diretório de backups são
  recusados ou ignorados;
- o manifesto traz SHA-256 de cada arquivo, sem conteúdo de segredo;
- toda a árvore é validada em memória/área temporária antes da primeira
  escrita no destino;
- a restauração não usa ``tar.extract``/``extractall`` e recusa membros extras,
  duplicados, links e caminhos fora de ``data/``;
- o pacote nasce com permissão 0600.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT_ITEMS = (
    "config.json",
    "owner_credentials.json",
    "jobs.db",
    "identity.db",
    "agency.db",
    "subscriptions.db",
    "agency-events.jsonl",
    "execution-events.jsonl",
    "paper-state.json",
    "shadow-state.json",
    "kill-switch.json",
    "live-halts.json",
    "ia-uso.json",
    "broker-journal.jsonl",
    # Compatibilidade com instalações antigas.
    "desk-state.json",
    "desk-broker.jsonl",
)

STATE_DIRS = ("desks", "tenants", "checkpoints")
ALLOWED_SUFFIXES = frozenset((".json", ".jsonl", ".db"))
SKIP_SUFFIXES = (".tmp", ".wal", ".shm", ".trava")
SCHEMA = "dz23.local-backup/2"


class BackupError(ValueError):
    """Pacote inválido, incompleto ou adulterado."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_relative(name: str) -> PurePosixPath:
    if "\\" in name or "\x00" in name or ":" in name:
        raise BackupError(f"caminho suspeito no pacote: {name}")
    rel = PurePosixPath(name)
    if rel.is_absolute() or not rel.parts or ".." in rel.parts:
        raise BackupError(f"caminho suspeito no pacote: {name}")
    return rel


def _iter_state_files(data_dir: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for name in ROOT_ITEMS:
        path = data_dir / name
        if path.is_symlink():
            raise BackupError(f"link simbólico não permitido no estado: {name}")
        if path.exists():
            if not path.is_file():
                raise BackupError(f"item operacional não é arquivo: {name}")
            found.append((name, path))

    for dirname in STATE_DIRS:
        root = data_dir / dirname
        if root.is_symlink():
            raise BackupError(f"link simbólico não permitido no estado: {dirname}")
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise BackupError(
                    f"link simbólico não permitido no estado: "
                    f"{path.relative_to(data_dir)}")
            if not path.is_file() or path.name.endswith(SKIP_SUFFIXES):
                continue
            if path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            rel = path.relative_to(data_dir).as_posix()
            _safe_relative(rel)
            found.append((rel, path))
    return sorted(found, key=lambda item: item[0])


def _snapshot_file(source: Path, target: Path) -> None:
    """Copia JSON/journal ou usa a API transacional de backup do SQLite."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() != ".db":
        shutil.copyfile(source, target)
        return
    try:
        uri = source.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
    except sqlite3.Error as exc:
        raise BackupError(
            f"banco SQLite inválido ou indisponível: {source.name}") from exc


def create_backup(data_dir: str | Path, out_path: str | Path) -> dict:
    data_dir, out_path = Path(data_dir), Path(out_path)
    if not data_dir.exists():
        raise BackupError(f"diretório de dados inexistente: {data_dir}")
    files = _iter_state_files(data_dir)
    if not files:
        raise BackupError("nada a salvar — diretório sem estado operacional")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "snapshot"
        snapshot.mkdir()
        manifest: dict[str, str] = {}
        for rel, source in files:
            target = snapshot.joinpath(*PurePosixPath(rel).parts)
            _snapshot_file(source, target)
            manifest[rel] = _sha256(target)

        meta = {
            "schema": SCHEMA,
            "criado_em": datetime.now(timezone.utc).isoformat(),
            "itens": manifest,
            "observacao": (
                "estado PostgreSQL externo não faz parte deste pacote local"),
        }
        manifest_path = Path(tmp) / "MANIFEST.json"
        manifest_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")

        temporary = out_path.with_name(out_path.name + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh, tarfile.open(
                    fileobj=fh, mode="w:gz") as tar:
                tar.add(manifest_path, arcname="MANIFEST.json", recursive=False)
                for rel in sorted(manifest):
                    tar.add(snapshot.joinpath(*PurePosixPath(rel).parts),
                            arcname=f"data/{rel}", recursive=False)
            os.replace(temporary, out_path)
            os.chmod(out_path, 0o600)
        except BaseException:
            with suppress(OSError):
                os.unlink(temporary)
            raise

    return {
        "arquivo": str(out_path),
        "itens": sorted(manifest),
        "quantidade": len(manifest),
        "bytes": out_path.stat().st_size,
        "sha256": _sha256(out_path),
        "schema": SCHEMA,
    }


def _read_manifest(tar: tarfile.TarFile) -> dict:
    members = tar.getmembers()
    names = [member.name for member in members]
    if len(names) != len(set(names)):
        raise BackupError("pacote contém membros duplicados")
    for member in members:
        _safe_relative(member.name)
        if not member.isfile():
            raise BackupError(f"tipo de membro não permitido: {member.name}")
    try:
        member = tar.getmember("MANIFEST.json")
    except KeyError as exc:
        raise BackupError("pacote sem MANIFEST") from exc
    stream = tar.extractfile(member)
    if stream is None:
        raise BackupError("pacote sem MANIFEST legível")
    try:
        meta = json.loads(stream.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("MANIFEST inválido") from exc
    if not isinstance(meta, dict) or meta.get("schema") != SCHEMA:
        raise BackupError("schema de backup ausente ou incompatível")
    items = meta.get("itens")
    if not isinstance(items, dict) or not items:
        raise BackupError("MANIFEST sem itens")
    expected = {"MANIFEST.json"}
    for rel, digest in items.items():
        if not isinstance(rel, str) or not isinstance(digest, str):
            raise BackupError("MANIFEST contém item inválido")
        _safe_relative(rel)
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise BackupError(f"hash inválido no MANIFEST: {rel}")
        expected.add(f"data/{rel}")
    if set(names) != expected:
        raise BackupError("pacote contém item ausente ou não declarado")
    return meta


def inspect_backup(archive: str | Path) -> dict:
    try:
        with tarfile.open(archive, "r:gz") as tar:
            meta = _read_manifest(tar)
            for rel, expected in meta["itens"].items():
                stream = tar.extractfile(tar.getmember(f"data/{rel}"))
                if stream is None:
                    raise BackupError(f"pacote incompleto: falta {rel}")
                digest = hashlib.sha256()
                while chunk := stream.read(65536):
                    digest.update(chunk)
                if digest.hexdigest() != expected:
                    raise BackupError(
                        f"hash divergente em {rel} — pacote adulterado")
            return meta
    except (OSError, tarfile.TarError) as exc:
        raise BackupError(f"pacote ilegível: {type(exc).__name__}") from exc


def restore_backup(archive: str | Path, data_dir: str | Path, *,
                   overwrite: bool = False) -> dict:
    archive, data_dir = Path(archive), Path(data_dir)
    try:
        with tarfile.open(archive, "r:gz") as tar, tempfile.TemporaryDirectory() as tmp:
            meta = _read_manifest(tar)
            staged = Path(tmp)
            for rel, expected in meta["itens"].items():
                member = tar.getmember(f"data/{rel}")
                stream = tar.extractfile(member)
                if stream is None:
                    raise BackupError(f"pacote incompleto: falta {rel}")
                target = staged.joinpath(*PurePosixPath(rel).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as fh:
                    shutil.copyfileobj(stream, fh)
                if _sha256(target) != expected:
                    raise BackupError(f"hash divergente em {rel} — pacote adulterado")

            existing = [
                rel for rel in meta["itens"]
                if data_dir.joinpath(*PurePosixPath(rel).parts).exists()
            ]
            if existing and not overwrite:
                raise BackupError(
                    "destino já tem estado operacional — peça sobrescrita "
                    "explícita: " + ", ".join(sorted(existing)[:5]))

            data_dir.mkdir(parents=True, exist_ok=True)
            restored: list[str] = []
            for rel in sorted(meta["itens"]):
                source = staged.joinpath(*PurePosixPath(rel).parts)
                destination = data_dir.joinpath(*PurePosixPath(rel).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + ".restore.tmp")
                shutil.copyfile(source, temporary)
                os.replace(temporary, destination)
                if destination.name in {"config.json", "owner_credentials.json"}:
                    os.chmod(destination, 0o600)
                restored.append(rel)
    except BackupError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise BackupError(f"falha ao restaurar pacote: {type(exc).__name__}") from exc

    return {
        "restaurados": restored,
        "origem_backup": meta.get("criado_em"),
        "destino": str(data_dir),
        "schema": SCHEMA,
    }


__all__ = [
    "create_backup", "restore_backup", "inspect_backup", "BackupError",
    "ROOT_ITEMS", "STATE_DIRS", "SCHEMA",
]
