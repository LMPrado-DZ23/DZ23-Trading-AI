"""SEC-1 — segredos cifrados em repouso: prova de proteção, migração e
recuperação, sem perda de credenciais e sem vazar valores.
"""
from __future__ import annotations

import importlib
import json

from fastapi.testclient import TestClient

from tradingagents.security.secret_store import SecretStore, mascarar

SEGREDO = "sk-chave-de-api-super-secreta-1234567890"


class TestSecretStoreModulo:
    def test_set_get_roundtrip(self, tmp_path):
        s = SecretStore(tmp_path, "legacy")
        s.set("OPENAI_API_KEY", SEGREDO)
        assert s.get("OPENAI_API_KEY") == SEGREDO

    def test_value_is_encrypted_at_rest(self, tmp_path):
        s = SecretStore(tmp_path, "legacy")
        s.set("OPENAI_API_KEY", SEGREDO)
        # O arquivo em disco NÃO pode conter o segredo em texto claro.
        bruto = (tmp_path / "secrets.legacy.enc").read_bytes()
        assert SEGREDO.encode() not in bruto
        assert SEGREDO not in bruto.decode("latin-1")

    def test_recovery_from_a_fresh_instance_same_dir(self, tmp_path):
        """Recuperação: outra instância (reinício do processo) lê de volta."""
        SecretStore(tmp_path, "legacy").set("GROQ_API_KEY", SEGREDO)
        assert SecretStore(tmp_path, "legacy").get("GROQ_API_KEY") == SEGREDO

    def test_namespaces_are_isolated(self, tmp_path):
        SecretStore(tmp_path, "a").set("EXCHANGE_API_KEY", "chave-de-A")
        assert SecretStore(tmp_path, "b").get("EXCHANGE_API_KEY") is None

    def test_corrupt_store_fails_closed_not_crash(self, tmp_path):
        s = SecretStore(tmp_path, "legacy")
        s.set("OPENAI_API_KEY", SEGREDO)
        (tmp_path / "secrets.legacy.enc").write_bytes(b"lixo-nao-cifrado")
        assert s.get("OPENAI_API_KEY") is None     # fail-closed, sem exceção

    def test_missing_key_file_fails_closed(self, tmp_path):
        s = SecretStore(tmp_path, "legacy")
        s.set("OPENAI_API_KEY", SEGREDO)
        (tmp_path / "secret.key").unlink()
        # Sem a chave, nova instância não devolve o segredo (nem o inventa).
        assert SecretStore(tmp_path, "legacy").get("OPENAI_API_KEY") is None

    def test_delete_removes_the_secret(self, tmp_path):
        s = SecretStore(tmp_path, "legacy")
        s.set("OPENAI_API_KEY", SEGREDO)
        s.delete("OPENAI_API_KEY")
        assert s.get("OPENAI_API_KEY") is None

    def test_mask_never_reveals_the_whole_value(self):
        m = mascarar(SEGREDO)
        assert SEGREDO not in m
        assert m.startswith("sk") and m.endswith("90")


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import service.app as m
    importlib.reload(m)
    m._FAILS.clear()
    m._SEGREDOS_MIGRADOS.clear()
    return m


class TestIntegracaoConfig:
    def test_save_cfg_keeps_secret_out_of_config_json(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        m.save_cfg({"OPENAI_API_KEY": SEGREDO,
                    "TRADINGAGENTS_EXCHANGE": "binance"}, "legacy")
        # config.json em disco: sem o segredo; com a config pública.
        disco = (m.CFG_PATH).read_text(encoding="utf-8")
        assert SEGREDO not in disco
        assert "binance" in disco
        # load_cfg recompõe o segredo a partir do store cifrado.
        assert m.load_cfg("legacy")["OPENAI_API_KEY"] == SEGREDO

    def test_migration_moves_plaintext_secret_and_backs_up(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        # Simula o estado ANTIGO: segredo em texto claro no config.json.
        m.CFG_PATH.write_text(json.dumps({
            "OPENAI_API_KEY": SEGREDO, "TRADINGAGENTS_EXCHANGE": "binance"}),
            encoding="utf-8")
        m._SEGREDOS_MIGRADOS.clear()
        cfg = m.load_cfg("legacy")                      # dispara a migração
        # Recuperável:
        assert cfg["OPENAI_API_KEY"] == SEGREDO
        # config.json não tem mais o segredo:
        assert SEGREDO not in m.CFG_PATH.read_text(encoding="utf-8")
        # Backup preservado com o conteúdo original (recuperação possível):
        backup = m.CFG_PATH.with_suffix(".json.bak-pre-secret-store")
        assert backup.exists() and SEGREDO in backup.read_text(encoding="utf-8")
        # E o store realmente guarda (recuperação por instância nova):
        assert m._secret_store("legacy").get("OPENAI_API_KEY") == SEGREDO

    def test_config_post_then_get_masks_and_disk_has_no_plaintext(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        r = c.post("/v1/config", headers=h, json={"vars": {"OPENAI_API_KEY": SEGREDO}})
        assert r.status_code == 200, r.text
        # A resposta/overview nunca devolve o segredo inteiro.
        corpo = c.get("/v1/config", headers=h).text
        assert SEGREDO not in corpo
        # E o disco não guarda o segredo em claro.
        assert SEGREDO not in m.CFG_PATH.read_text(encoding="utf-8")

    def test_migration_is_idempotent(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        m.CFG_PATH.write_text(json.dumps({"GROQ_API_KEY": SEGREDO}), encoding="utf-8")
        m._SEGREDOS_MIGRADOS.clear()
        m.load_cfg("legacy")
        primeiro = m.CFG_PATH.read_text(encoding="utf-8")
        m._SEGREDOS_MIGRADOS.clear()
        m.load_cfg("legacy")                            # de novo: no-op
        assert m.CFG_PATH.read_text(encoding="utf-8") == primeiro
