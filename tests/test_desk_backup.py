"""TA-017 / cenário 16: backup e restauração em destino vazio, com hashes."""
from __future__ import annotations

import json
import os
import sqlite3
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
    with sqlite3.connect(d / "jobs.db") as conn:
        conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, state TEXT)")
        conn.execute("INSERT INTO jobs(state) VALUES ('paper')")
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

    def test_includes_school_real_room_tenant_and_checkpoints(self, tmp_path):
        data = populate(tmp_path / "data")
        (data / "desks" / "legacy").mkdir(parents=True)
        (data / "desks" / "legacy" / "escola.json").write_text("{}")
        (data / "desks" / "legacy" / "pilotos.json").write_text("{}")
        (data / "tenants" / "owner").mkdir(parents=True)
        (data / "tenants" / "owner" / "kill-switch.json").write_text("{}")
        (data / "checkpoints").mkdir()
        with sqlite3.connect(data / "checkpoints" / "BTC_USDT.db") as conn:
            conn.execute("CREATE TABLE checkpoints (id INTEGER PRIMARY KEY)")
        (data / "desks" / "legacy" / "posse.json.trava").write_text("lock")

        out = create_backup(data, tmp_path / "bk.tar.gz")

        assert "desks/legacy/escola.json" in out["itens"]
        assert "desks/legacy/pilotos.json" in out["itens"]
        assert "tenants/owner/kill-switch.json" in out["itens"]
        assert "checkpoints/BTC_USDT.db" in out["itens"]
        assert not any(item.endswith(".trava") for item in out["itens"])

    def test_symbolic_link_in_state_is_refused(self, tmp_path):
        if not hasattr(os, "symlink"):
            pytest.skip("plataforma sem symlink")
        data = populate(tmp_path / "data")
        (data / "desks").mkdir()
        outside = tmp_path / "outside.json"
        outside.write_text('{"secret": true}')
        try:
            (data / "desks" / "escape.json").symlink_to(outside)
        except OSError:
            pytest.skip("usuário sem permissão para criar symlink")

        with pytest.raises(BackupError, match="link simbólico"):
            create_backup(data, tmp_path / "bk.tar.gz")


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

        with pytest.raises(BackupError, match="adulterado"):
            inspect_backup(forjado)

    def test_unmanifested_member_is_refused(self, tmp_path):
        data = populate(tmp_path / "data")
        original = tmp_path / "bk.tar.gz"
        create_backup(data, original)
        extracted = tmp_path / "ex-extra"
        extracted.mkdir()
        with tarfile.open(original, "r:gz") as tar:
            tar.extractall(extracted)
        (extracted / "data" / "extra.json").write_text("{}")
        forged = tmp_path / "extra.tar.gz"
        with tarfile.open(forged, "w:gz") as tar:
            tar.add(extracted / "MANIFEST.json", arcname="MANIFEST.json")
            for path in (extracted / "data").rglob("*"):
                if path.is_file():
                    tar.add(path, arcname="data/" + path.relative_to(
                        extracted / "data").as_posix())

        destination = tmp_path / "alvo-extra"
        with pytest.raises(BackupError, match="não declarado"):
            restore_backup(forged, destination)
        assert not destination.exists()

    def test_windows_style_traversal_is_refused(self, tmp_path):
        forged = tmp_path / "windows-traversal.tar.gz"
        with tarfile.open(forged, "w:gz") as tar:
            info = tarfile.TarInfo("data/..\\escape.json")
            info.size = 2
            import io
            tar.addfile(info, io.BytesIO(b"{}"))

        with pytest.raises(BackupError, match="caminho suspeito"):
            restore_backup(forged, tmp_path / "destino")

    def test_round_trip_preserves_files_and_sqlite_content(self, tmp_path):
        data = populate(tmp_path / "data")
        create_backup(data, tmp_path / "bk.tar.gz")
        novo = tmp_path / "n2"
        restore_backup(tmp_path / "bk.tar.gz", novo)
        for name in ("config.json", "desk-state.json"):
            assert (novo / name).read_bytes() == (data / name).read_bytes()
        with sqlite3.connect(novo / "jobs.db") as conn:
            assert conn.execute("SELECT state FROM jobs").fetchall() == [("paper",)]
