"""SEC-1 — Auditoria adversarial (achado ChatGPT, commit 4b83e16).

ANTES: `save_cfg` gravava o config.json inteiro em texto claro; a única
proteção era `os.chmod(path, 0o600)` — que no Windows altera só o atributo
SOMENTE-LEITURA e não toca em ACL: qualquer usuário local lia as chaves de
IA, da corretora e os tokens de alerta direto do disco.

AGORA: segredos vão CIFRADOS com a DPAPI do usuário do Windows
(`enc:dpapi:v1:`), com migração automática do config legado — verificada
com ida-e-volta ANTES de gravar — e regras de não-perda: segredo ilegível
(outro usuário/máquina/blob corrompido) nunca é apagado, nunca vira vazio
e nunca é confundido com "não configurado". No POSIX o arquivo continua
0600 (proteção real ali) e o status diz isso com honestidade.

Os testes são conscientes da plataforma: cada asserção diz o que É verdade
em Windows e em POSIX — sem skip que esconda divergência.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from types import SimpleNamespace

import pytest

from service.api import segredos_em_repouso

H = {"Authorization": "Bearer s3nh4"}
PROTEGIDO = segredos_em_repouso.tem_protecao()
PREFIXO = segredos_em_repouso.PREFIXO
TOKEN_ILEGIVEL = PREFIXO + "bm90LXVtLWJsb2ItZGUtdHJ1ZQ=="   # base64 válido, blob falso

SEGREDO = "gsk-teste-secreto-1234567890"
TELEGRAM = "123456:AAH-token-do-bot"


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import service.app as m
    importlib.reload(m)
    m._FAILS.clear()
    m._DESKS.clear()
    from fastapi.testclient import TestClient
    return m, TestClient(m.app)


def _config_no_disco(tmp_path) -> dict:
    return json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))


class TestProtecaoEmRepouso:
    def test_segredo_nao_esta_legivel_para_quem_le_o_arquivo(self, tmp_path, monkeypatch):
        m, c = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=H, json={"vars": {"GROQ_API_KEY": SEGREDO}})
        bruto = (tmp_path / "config.json").read_text(encoding="utf-8")
        no_disco = _config_no_disco(tmp_path)["GROQ_API_KEY"]
        if PROTEGIDO:
            assert SEGREDO not in bruto, "segredo legível em texto claro no disco"
            assert no_disco.startswith(PREFIXO)
        else:
            # POSIX: a proteção É o 0600 do arquivo — e ele está lá.
            assert no_disco == SEGREDO
            modo = oct((tmp_path / "config.json").stat().st_mode)[-3:]
            assert modo == "600", f"arquivo sem 0600 no POSIX (modo {modo})"

    def test_leitura_devolve_o_segredo_original_apos_reinicio(self, tmp_path, monkeypatch):
        m, c = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=H, json={"vars": {"GROQ_API_KEY": SEGREDO}})
        # "reinício": módulo recarregado, estado em memória descartado
        m2, c2 = _app(tmp_path, monkeypatch)
        assert m2.load_cfg("legacy")["GROQ_API_KEY"] == SEGREDO
        d = c2.get("/v1/config", headers=H).json()
        assert d["config"]["GROQ_API_KEY"]["set"] is True
        assert SEGREDO not in c2.get("/v1/config", headers=H).text

    def test_ciclos_de_save_nao_corrompem_o_segredo(self, tmp_path, monkeypatch):
        """Cada Salvar do painel é um ciclo load→save. Se o load decifrasse
        errado (ou o save cifrasse de novo), o segredo viraria lixo no
        terceiro clique — o teste força 3 ciclos."""
        m, c = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=H, json={"vars": {"GROQ_API_KEY": SEGREDO}})
        for i in range(3):
            c.post("/v1/config", headers=H,
                   json={"vars": {"TRADINGAGENTS_DESK_CAPITAL": str(100 + i)}})
        assert m.load_cfg("legacy")["GROQ_API_KEY"] == SEGREDO

    def test_status_do_painel_e_honesto(self, tmp_path, monkeypatch):
        m, c = _app(tmp_path, monkeypatch)
        st = c.get("/v1/config", headers=H).json()["segredos_em_repouso"]
        esperado = "dpapi-usuario" if PROTEGIDO else "arquivo-0600"
        assert st["protecao"] == esperado
        assert st["efetiva"] is True and st["detalhe"]

    def test_falha_de_cifragem_aborta_o_save_sem_gravar_em_claro(self, tmp_path, monkeypatch):
        m, c = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=H, json={"vars": {"GROQ_API_KEY": SEGREDO}})
        antes = (tmp_path / "config.json").read_text(encoding="utf-8")

        def cifrar_quebrado(valor):
            raise segredos_em_repouso.SegredoIlegivel("DPAPI indisponível")

        monkeypatch.setattr(m.segredos_em_repouso, "cifrar", cifrar_quebrado)
        with pytest.raises(segredos_em_repouso.SegredoIlegivel):
            c.post("/v1/config", headers=H,
                   json={"vars": {"ANTHROPIC_API_KEY": "sk-ant-nova"}})
        depois = (tmp_path / "config.json").read_text(encoding="utf-8")
        assert depois == antes, "save abortado não pode alterar o arquivo"
        assert "sk-ant-nova" not in depois


class TestMigracaoDoConfigLegado:
    """Instalação do commit 4b83e16: config em texto claro no disco.
    A migração precisa cifrar SEM perder nada — e no POSIX não mexer."""

    def _legado(self, tmp_path) -> bytes:
        documento = {
            "GROQ_API_KEY": SEGREDO,
            "ALERTA_TELEGRAM_TOKEN": TELEGRAM,
            "TRADINGAGENTS_EXCHANGE": "binance",
            "TRADINGAGENTS_DESK_CAPITAL": "500",
        }
        (tmp_path / "config.json").write_text(json.dumps(documento),
                                              encoding="utf-8")
        return (tmp_path / "config.json").read_bytes()

    def test_migracao_preserva_todas_as_chaves(self, tmp_path, monkeypatch):
        m, c = _app(tmp_path, monkeypatch)
        bruto_antes = self._legado(tmp_path)
        cfg = m.load_cfg("legacy")
        assert cfg["GROQ_API_KEY"] == SEGREDO
        assert cfg["ALERTA_TELEGRAM_TOKEN"] == TELEGRAM
        assert cfg["TRADINGAGENTS_EXCHANGE"] == "binance"
        if PROTEGIDO:
            no_disco = _config_no_disco(tmp_path)
            assert no_disco["GROQ_API_KEY"].startswith(PREFIXO)
            assert no_disco["ALERTA_TELEGRAM_TOKEN"].startswith(PREFIXO)
            assert SEGREDO not in json.dumps(no_disco)
            # chaves comuns ficam intocadas
            assert no_disco["TRADINGAGENTS_EXCHANGE"] == "binance"
            assert no_disco["TRADINGAGENTS_DESK_CAPITAL"] == "500"
            # e a leitura seguinte devolve os valores originais
            assert m.load_cfg("legacy")["GROQ_API_KEY"] == SEGREDO
        else:
            assert (tmp_path / "config.json").read_bytes() == bruto_antes, (
                "POSIX não inventa criptografia caseira — o arquivo não muda")

    def test_leitura_ilegivel_fica_intacta_e_reportada(self, tmp_path, monkeypatch):
        m, c = _app(tmp_path, monkeypatch)
        (tmp_path / "config.json").write_text(json.dumps({
            "ALERTA_TELEGRAM_TOKEN": TOKEN_ILEGIVEL,
            "TRADINGAGENTS_EXCHANGE": "binance",
        }), encoding="utf-8")
        cfg = m.load_cfg("legacy")
        assert cfg["ALERTA_TELEGRAM_TOKEN"] == TOKEN_ILEGIVEL, (
            "ilegível virou vazio/ausente — isso descartaria a credencial")
        d = c.get("/v1/config", headers=H).json()
        assert d["config"]["ALERTA_TELEGRAM_TOKEN"]["set"] is True
        assert TOKEN_ILEGIVEL not in c.get("/v1/config", headers=H).text
        assert "ALERTA_TELEGRAM_TOKEN" in d["segredos_em_repouso"]["ilegiveis"]

    def test_token_ilegivel_sobrevive_a_saves_de_outras_chaves(self, tmp_path, monkeypatch):
        m, c = _app(tmp_path, monkeypatch)
        (tmp_path / "config.json").write_text(json.dumps({
            "ALERTA_TELEGRAM_TOKEN": TOKEN_ILEGIVEL,
        }), encoding="utf-8")
        c.post("/v1/config", headers=H,
               json={"vars": {"TRADINGAGENTS_EXCHANGE": "bybit"}})
        no_disco = _config_no_disco(tmp_path)
        assert no_disco["ALERTA_TELEGRAM_TOKEN"] == TOKEN_ILEGIVEL, (
            "save de outra chave apagou/alterou o segredo ilegível")
        assert no_disco["TRADINGAGENTS_EXCHANGE"] == "bybit"

    def test_recolar_o_segredo_recupera_o_cofre(self, tmp_path, monkeypatch):
        m, c = _app(tmp_path, monkeypatch)
        (tmp_path / "config.json").write_text(json.dumps({
            "ALERTA_TELEGRAM_TOKEN": TOKEN_ILEGIVEL,
        }), encoding="utf-8")
        c.post("/v1/config", headers=H,
               json={"vars": {"ALERTA_TELEGRAM_TOKEN": "999:BBB-fresco"}})
        assert m.load_cfg("legacy")["ALERTA_TELEGRAM_TOKEN"] == "999:BBB-fresco"
        d = c.get("/v1/config", headers=H).json()
        assert "ALERTA_TELEGRAM_TOKEN" not in d["segredos_em_repouso"]["ilegiveis"]

    @pytest.mark.parametrize("remover", [False, True], ids=["substituicao", "exclusao"])
    def test_sem_dpapi_atualiza_ilegiveis_so_da_chave_alterada(
            self, tmp_path, monkeypatch, remover):
        """V9-CI-02: o caminho POSIX também é exercitado no Windows.

        Re-colar/remover limpa o diagnóstico antigo, não outras credenciais
        ilegíveis nem o registro do mesmo segredo em outro inquilino.
        """
        # V9 SEC-03 endureceu `cifrar`: no Windows, a AUSÊNCIA de DPAPI agora
        # RECUSA gravar em claro. Simular só `tem_protecao()=False` deixou de
        # reproduzir o POSIX — o `sys.platform` real segue "win32" e a recusa
        # entra no meio. A simulação honesta do POSIX (o que o docstring
        # promete) é: sem DPAPI E sem ser Windows. As asserções seguem intactas.
        monkeypatch.setattr(segredos_em_repouso, "tem_protecao", lambda: False)
        monkeypatch.setattr(segredos_em_repouso, "sys",
                            SimpleNamespace(platform="linux"))
        monkeypatch.delenv("ALERTA_TELEGRAM_TOKEN", raising=False)
        m, c = _app(tmp_path, monkeypatch)
        outro_path = m.tenant_cfg_path("outro-tenant")
        outro_path.write_text(json.dumps({
            "ALERTA_TELEGRAM_TOKEN": TOKEN_ILEGIVEL,
        }), encoding="utf-8")
        m.load_cfg("outro-tenant")
        outro_registro = dict(m._SEGREDOS_ILEGIVEIS["outro-tenant"])
        assert set(outro_registro) == {"ALERTA_TELEGRAM_TOKEN"}
        outro_antes = outro_path.read_bytes()

        (tmp_path / "config.json").write_text(json.dumps({
            "ALERTA_TELEGRAM_TOKEN": TOKEN_ILEGIVEL,
            "GROQ_API_KEY": TOKEN_ILEGIVEL,
        }), encoding="utf-8")
        m.load_cfg("legacy")
        assert set(m._SEGREDOS_ILEGIVEIS["legacy"]) == {
            "ALERTA_TELEGRAM_TOKEN", "GROQ_API_KEY"}
        groq_antes = m._SEGREDOS_ILEGIVEIS["legacy"]["GROQ_API_KEY"]

        pedido = ({"remover": ["ALERTA_TELEGRAM_TOKEN"]} if remover else
                  {"vars": {"ALERTA_TELEGRAM_TOKEN": TELEGRAM}})
        assert c.post("/v1/config", headers=H, json=pedido).status_code == 200
        cfg = m.load_cfg("legacy")
        no_disco = _config_no_disco(tmp_path)
        esperado = None if remover else TELEGRAM
        assert cfg.get("ALERTA_TELEGRAM_TOKEN") == esperado
        assert no_disco.get("ALERTA_TELEGRAM_TOKEN") == esperado
        assert cfg["GROQ_API_KEY"] == no_disco["GROQ_API_KEY"] == TOKEN_ILEGIVEL
        assert m._SEGREDOS_ILEGIVEIS["outro-tenant"] == outro_registro
        assert outro_path.read_bytes() == outro_antes
        assert m._SEGREDOS_ILEGIVEIS["legacy"]["GROQ_API_KEY"] == groq_antes
        assert "ALERTA_TELEGRAM_TOKEN" not in m._SEGREDOS_ILEGIVEIS["legacy"]

        d = c.get("/v1/config", headers=H).json()
        assert d["segredos_em_repouso"]["ilegiveis"] == {"GROQ_API_KEY": groq_antes}
        assert d["config"]["ALERTA_TELEGRAM_TOKEN"]["set"] is (not remover)
        assert d["config"]["GROQ_API_KEY"]["set"] is True

    def test_blob_valido_ilegivel_aqui_nao_e_apagado(self, tmp_path, monkeypatch):
        """Blob que PARECE cifrado de verdade (vem de outra máquina/usuário):
        decifrar falha — e o valor continua lá, esperando a máquina certa."""
        if not PROTEGIDO:
            pytest.skip("sem DPAPI não há blob válido-de-outra-máquina a forjar")
        bom = segredos_em_repouso.cifrar("segredo-de-outra-maquina")
        # corrompe um byte do blob: vira indistinguível de blob de outro dono
        quebrado = bom[:-4] + ("AAAA" if not bom.endswith("AAAA") else "BBBB")
        (tmp_path / "config.json").write_text(json.dumps({
            "ALERTA_TELEGRAM_TOKEN": quebrado}), encoding="utf-8")
        m, c = _app(tmp_path, monkeypatch)
        cfg = m.load_cfg("legacy")
        assert cfg["ALERTA_TELEGRAM_TOKEN"] == quebrado
        assert _config_no_disco(tmp_path)["ALERTA_TELEGRAM_TOKEN"] == quebrado


class TestRoundtripDoModulo:
    def test_ida_e_volta(self):
        if PROTEGIDO:
            token = segredos_em_repouso.cifrar("valor-secreto")
            assert token.startswith(PREFIXO)
            assert segredos_em_repouso.decifrar(token) == "valor-secreto"
        else:
            assert segredos_em_repouso.cifrar("valor-secreto") == "valor-secreto"

    def test_ilegivel_levanta_nunca_devolve_vazio(self):
        with pytest.raises(segredos_em_repouso.SegredoIlegivel):
            segredos_em_repouso.decifrar(TOKEN_ILEGIVEL)

    def test_valor_nao_cifrado_volta_como_esta(self):
        assert segredos_em_repouso.decifrar("texto-simples") == "texto-simples"
        assert not segredos_em_repouso.e_cifrado("texto-simples")


class TestSemQuebraDeComportamento:
    """O painel continua funcionando: ver a config, mascarar segredos,
    salvar não-segredos — em texto claro como sempre."""

    def test_nao_segredo_permanece_legivel_para_o_dono(self, tmp_path, monkeypatch):
        m, c = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=H,
               json={"vars": {"TRADINGAGENTS_EXCHANGE": "kraken"}})
        d = c.get("/v1/config", headers=H).json()
        assert d["config"]["TRADINGAGENTS_EXCHANGE"]["value"] == "kraken"
        assert _config_no_disco(tmp_path)["TRADINGAGENTS_EXCHANGE"] == "kraken"

    def test_segredo_como_em_constante_de_plataforma(self):
        assert (sys.platform == "win32" and os.name == "nt") == PROTEGIDO
