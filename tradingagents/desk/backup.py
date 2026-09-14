"""Backup e restauração do estado operacional (TA-017).

O que precisa sobreviver a uma máquina nova: configuração (com segredos),
fila de jobs, estado da mesa e o journal da corretora. O que NÃO entra:
cache e artefatos recriáveis.

Regras:
- O pacote é um .tar.gz com MANIFEST de SHA-256 por arquivo; restaurar
  confere cada hash e recusa pacote adulterado.
- Restaurar é explícito e para destino vazio ou com sobrescrita pedida —
  nunca "quase restaura" deixando metade.
- Segredos ficam dentro do backup (é o ponto dele) mas o MANIFEST e os logs
  só carregam nomes e hashes, nunca conteúdo.
"""
from __future__ import annotations

import hashlib
import json
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BACKUP_ITEMS = ("config.json", "jobs.db", "desk-state.json", "broker-journal.jsonl",
                "desk-broker.jsonl")
SKIP_SUFFIXES = (".tmp", ".wal", ".shm")


class BackupError(RuntimeError):
    """Pacote inválido, incompleto ou adulterado."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def create_backup(data_dir: str | Path, out_path: str | Path) -> dict:
    data_dir, out_path = Path(data_dir), Path(out_path)
    if not data_dir.exists():
        raise BackupError(f"diretório de dados inexistente: {data_dir}")
    included, manifest = [], {}
    for name in BACKUP_ITEMS:
        p = data_dir / name
        if p.exists() and p.is_file() and not p.name.endswith(SKIP_SUFFIXES):
            manifest[name] = _sha256(p)
            included.append(p)
    if not included:
        raise BackupError("nada a salvar — diretório sem estado operacional")
    meta = {"criado_em": datetime.now(timezone.utc).isoformat(),
            "itens": manifest, "origem": str(data_dir)}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        manifest_path = Path(tmp) / "MANIFEST.json"
        manifest_path.write_text(json.dumps(meta, indent=2))
        with tarfile.open(out_path, "w:gz") as tar:
            tar.add(manifest_path, arcname="MANIFEST.json")
            for p in included:
                tar.add(p, arcname=f"data/{p.name}")
    return {"arquivo": str(out_path), "itens": sorted(manifest),
            "bytes": out_path.stat().st_size, "sha256": _sha256(out_path)}


def inspect_backup(archive: str | Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        member = tar.extractfile("MANIFEST.json")
        if member is None:
            raise BackupError("pacote sem MANIFEST")
        return json.loads(member.read().decode())


def restore_backup(archive: str | Path, data_dir: str | Path, *,
                   overwrite: bool = False) -> dict:
    archive, data_dir = Path(archive), Path(data_dir)
    meta = inspect_backup(archive)
    data_dir.mkdir(parents=True, exist_ok=True)
    existing = [n for n in meta["itens"] if (data_dir / n).exists()]
    if existing and not overwrite:
        raise BackupError(
            f"destino já tem {', '.join(sorted(existing))} — peça sobrescrita explícita")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        with tarfile.open(archive, "r:gz") as tar:
            for m in tar.getmembers():
                if m.name.startswith(("/", "..")) or ".." in Path(m.name).parts:
                    raise BackupError(f"caminho suspeito no pacote: {m.name}")
                tar.extract(m, tmpdir)          # extrai para área temporária
        restored = []
        for name, expected in meta["itens"].items():
            src = tmpdir / "data" / name
            if not src.exists():
                raise BackupError(f"pacote incompleto: falta {name}")
            got = _sha256(src)
            if got != expected:
                raise BackupError(f"hash divergente em {name} — pacote adulterado")
            restored.append((src, data_dir / name))
        for src, dst in restored:               # só copia após validar TUDO
            dst.write_bytes(src.read_bytes())
            if dst.name == "config.json":
                dst.chmod(0o600)                # segredos voltam protegidos
    return {"restaurados": sorted(n for n in meta["itens"]),
            "origem_backup": meta.get("criado_em"), "destino": str(data_dir)}


__all__ = ["create_backup", "restore_backup", "inspect_backup", "BackupError"]
