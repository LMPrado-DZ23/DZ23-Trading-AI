"""TA-017 / cenário 16: backup e restauração em destino vazio, com hashes."""
from __future__ import annotations

import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

from tradingagents.desk.backup import BackupError, create_backup, inspect_backup, restore_backup

ROOT = Path(__file__).resolve().parents[1]


def _pular_fora_de_posix():
    if sys.platform == "win32":
        pytest.skip("POSIX permissions (600) are not natively supported on Windows NTFS")


def populate(d):
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps({"GROQ_API_KEY": "gsk-segredo-123"}))
    (d / "jobs.db").write_bytes(b"SQLite format 3\x00fake")
    (d / "desk-state.json").write_text(json.dumps({"cycles_run": 7,
                                                   "open_positions": ["BTC/USDT"]}))
    (d / "cache.tmp").write_text("descartável")
    return d


class TestBackup:
    def test_packs_only_operational_state(self, tmp_path):
        data = populate(tmp_path / "data")
        out = create_backup(data, tmp_path / "bk.tar.gz")
        assert set(out["itens"]) == {"config.json", "jobs.db", "desk-state.json"}
        with tarfile.open(out["arquivo"], "r:gz") as tar:
            names = tar.getnames()
        assert "MANIFEST.json" in names and "data/cache.tmp" not in names

    def test_manifest_carries_hashes_not_secrets(self, tmp_path):
        data = populate(tmp_path / "data")
        create_backup(data, tmp_path / "bk.tar.gz")
        meta = inspect_backup(tmp_path / "bk.tar.gz")
        assert len(meta["itens"]["config.json"]) == 64
        assert "gsk-segredo-123" not in json.dumps(meta)

    def test_empty_dir_refuses_instead_of_silent_empty_backup(self, tmp_path):
        (tmp_path / "vazio").mkdir()
        with pytest.raises(BackupError, match="nada a salvar"):
            create_backup(tmp_path / "vazio", tmp_path / "bk.tar.gz")


class TestArchivePermissions:
    """V9-SEC-03 (metade backup): o pacote carrega config.json com segredos;
    ele não pode nascer legível para o mundo (umask) nem ficar largo se já
    existir no disco."""

    def test_criacao_do_pacote_usa_modo_restrito(self):
        fonte = (ROOT / "tradingagents" / "desk" / "backup.py").read_text(encoding="utf-8")
        assert "os.open(" in fonte
        assert "0o600" in fonte
        assert "fchmod" in fonte

    def test_archive_does_not_widen_secret_read_permissions(self, tmp_path):
        _pular_fora_de_posix()
        anterior = os.umask(0o022)
        try:
            data = populate(tmp_path / "data")
            create_backup(data, tmp_path / "bk.tar.gz")
        finally:
            os.umask(anterior)
        assert oct((tmp_path / "bk.tar.gz").stat().st_mode)[-3:] == "600"

    def test_preexisting_wide_archive_is_tightened(self, tmp_path):
        _pular_fora_de_posix()
        alvo = tmp_path / "bk.tar.gz"
        alvo.write_bytes(b"lixo antigo")
        os.chmod(alvo, 0o644)
        data = populate(tmp_path / "data")
        create_backup(data, alvo)
        assert oct(alvo.stat().st_mode)[-3:] == "600"


class TestRestore:
    def test_restore_into_empty_destination(self, tmp_path):
        data = populate(tmp_path / "data")
        create_backup(data, tmp_path / "bk.tar.gz")
        novo = tmp_path / "maquina-nova"
        out = restore_backup(tmp_path / "bk.tar.gz", novo)
        assert out["restaurados"] == ["config.json", "desk-state.json", "jobs.db"]
        assert json.loads((novo / "config.json").read_text())["GROQ_API_KEY"] == "gsk-segredo-123"
        assert json.loads((novo / "desk-state.json").read_text())["cycles_run"] == 7
        assert (novo / "jobs.db").read_bytes().startswith(b"SQLite format 3")

    def test_config_permissions_are_restored_tight(self, tmp_path):
        import sys
        if sys.platform == "win32":
            pytest.skip("POSIX permissions (600) are not natively supported on Windows NTFS")
        data = populate(tmp_path / "data")
        create_backup(data, tmp_path / "bk.tar.gz")
        novo = tmp_path / "novo"
        restore_backup(tmp_path / "bk.tar.gz", novo)
        assert oct((novo / "config.json").stat().st_mode)[-3:] == "600"

    def test_existing_destination_requires_explicit_overwrite(self, tmp_path):
        data = populate(tmp_path / "data")
        create_backup(data, tmp_path / "bk.tar.gz")
        with pytest.raises(BackupError, match="sobrescrita"):
            restore_backup(tmp_path / "bk.tar.gz", data)
        out = restore_backup(tmp_path / "bk.tar.gz", data, overwrite=True)
        assert out["restaurados"]

    def test_tampered_package_is_refused_and_nothing_is_written(self, tmp_path):
        data = populate(tmp_path / "data")
        create_backup(data, tmp_path / "bk.tar.gz")
        # adultera o conteúdo mantendo o manifesto antigo
        meta = inspect_backup(tmp_path / "bk.tar.gz")
        forjado = tmp_path / "forjado.tar.gz"
        extraido = tmp_path / "ex"
        extraido.mkdir()
        with tarfile.open(tmp_path / "bk.tar.gz", "r:gz") as tar:
            tar.extractall(extraido)
        (extraido / "data" / "desk-state.json").write_text('{"cycles_run": 999}')
        with tarfile.open(forjado, "w:gz") as tar:
            tar.add(extraido / "MANIFEST.json", arcname="MANIFEST.json")
            for n in meta["itens"]:
                tar.add(extraido / "data" / n, arcname=f"data/{n}")
        destino = tmp_path / "alvo"
        with pytest.raises(BackupError, match="adulterado"):
            restore_backup(forjado, destino)
        assert not (destino / "desk-state.json").exists()   # nada gravado

    def test_round_trip_is_byte_identical(self, tmp_path):
        data = populate(tmp_path / "data")
        create_backup(data, tmp_path / "bk.tar.gz")
        novo = tmp_path / "n2"
        restore_backup(tmp_path / "bk.tar.gz", novo)
        for name in ("config.json", "jobs.db", "desk-state.json"):
            assert (novo / name).read_bytes() == (data / name).read_bytes()
