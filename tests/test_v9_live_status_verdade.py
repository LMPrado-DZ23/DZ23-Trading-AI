"""V9-REAL-01 — checklist de dinheiro real: tetos, testnet e provas ausentes.

Auditoria V9 (baseline 2b7754a). `/v1/live/status` (service/app.py) é a
única superfície que diz ao dono POR QUE o dinheiro real está bloqueado e
o que falta. Três defeitos demonstrados contra este HEAD:

1. TETO "DEFINIDO" ACEITA LIXO: os itens de teto usam `bool(valor)`, então
   "0", "-5", "abc", "inf" e "NaN" aparecem como PRONTO — enquanto a ponte
   CCXT exige Decimal finito > 0 (`_decimal_env`, ccxt_bridge.py). O painel
   diz "teto definido" quando a fechadura de dinheiro real está aberta por
   falta de teto.
2. "JÁ OPEROU EM TESTNET" CUMPRIDO POR ORDEM PAPER: qualquer ordem
   preenchida satisfaz o item — o simulador local PaperBroker emite
   `id="paper-N"` (execution.py) e o ack grava esse id em `rec.external_id`
   (execution.py), então uma ordem de treino SEM nenhum contato com
   corretora marcava "operou em testnet".
3. PROVAS DA FASE 2 AUSENTES: permissões da chave e identidade da conta
   REAL não aparecem no checklist — o contador de pendências subestima o
   que falta para dinheiro real.

Contraprova (controles que precisam passar ANTES e DEPOIS da correção):
- Ordem preenchida com id de exchange externo (testnet — a única corretora
  externa construível enquanto LIVE está selado) continua satisfazendo o
  item: a correção troca a evidência, não a fecha para sempre.
- `pode_operar_real` continua False com TUDO verde: o selo de código
  (MESA_LIVE_LIBERADA=False, ADR-006) segue fechando o caminho.
- `checklist[0]` continua declarando a confirmação no momento da ativação
  (consumidor: test_service_leigo.py), sem persistir a frase,
  e só o item do selo contém "código" no texto do ITEM (consumidor:
  test_auditoria_fin1_live_desk_seal.py filtra por essa palavra).

RED→GREEN: escrito antes da correção de service/app.py; no baseline falha
nos tetos inválidos, no preenchimento paper/sem-id-externo e nos dois itens
ausentes.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace as NS

import pytest

from tradingagents.desk.execution import OrderState

H = {"Authorization": "Bearer s3nh4"}

# O estado máximo que o painel consegue construir sozinho (ALLOWED_KEYS),
# idêntico ao consumidor FIN-1 — nada aqui é inventado.
CFG_LIVE_COMPLETO = {
    "TRADINGAGENTS_DESK_BROKER": "ccxt",
    "TRADINGAGENTS_EXCHANGE": "binance",
    "TRADINGAGENTS_BROKER_MODE": "live",
    "EXCHANGE_API_KEY": "k",
    "EXCHANGE_API_SECRET": "s",
    "LIVE_MAX_ORDER_VALUE": "50",
    "LIVE_MAX_DAILY_LOSS": "25",
}

_TETOS = [("LIVE_MAX_ORDER_VALUE", "Teto por ordem"),
          ("LIVE_MAX_DAILY_LOSS", "Perda máxima diária")]


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


def _status(c):
    r = c.get("/v1/live/status", headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def _item(d, trecho):
    alvo = [i for i in d["checklist"] if trecho.lower() in i["item"].lower()]
    assert alvo, (
        f"o checklist não tem item contendo {trecho!r}: "
        f"{[i['item'] for i in d['checklist']]}")
    return alvo[0]


def _com_ordem(m, *, estado=OrderState.FILLED, external_id="paper-1"):
    """Instala um registro de ordem no formato REAL do OMS.

    `external_id="paper-1"` é o que o simulador local grava (execution.py);
    um id de exchange de verdade é um id opaco da corretora externa."""
    rec = NS(state=estado, external_id=external_id)
    m._DESKS["legacy"] = {"coordinator": NS(orders={"ord-1": rec})}


class TestTetoDeDinheiroHonesto:
    @pytest.mark.parametrize("chave,trecho", _TETOS)
    @pytest.mark.parametrize("valor", ["0", "-5", "abc", "inf", "NaN"])
    def test_valor_invalido_nunca_aparece_como_pronto(
            self, tmp_path, monkeypatch, chave, trecho, valor):
        _, c = _app(tmp_path, monkeypatch)
        r = c.post("/v1/config", headers=H, json={"vars": {chave: valor}})
        assert r.status_code == 200, r.text
        item = _item(_status(c), trecho)
        assert item["pronto"] is False, (
            f"{chave}={valor!r} não é teto (a ponte exige Decimal finito > 0) "
            f"— o checklist não pode dizer pronto: {item}")

    @pytest.mark.parametrize("chave,trecho", _TETOS)
    def test_teto_positivo_de_verdade_continua_pronto(
            self, tmp_path, monkeypatch, chave, trecho):
        _, c = _app(tmp_path, monkeypatch)
        r = c.post("/v1/config", headers=H, json={"vars": {chave: "50.00"}})
        assert r.status_code == 200, r.text
        assert _item(_status(c), trecho)["pronto"] is True


class TestTestnetDeVerdade:
    def test_ordem_paper_preenchida_nao_conta_como_testnet(
            self, tmp_path, monkeypatch):
        m, c = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=H, json={"vars": CFG_LIVE_COMPLETO})
        _com_ordem(m, external_id="paper-1")   # fill simulada neste PC
        item = _item(_status(c), "testnet")
        assert item["pronto"] is False, (
            "ordem PAPER (simulador local, sem corretora) não é evidência "
            f"de testnet: {item}")

    @pytest.mark.parametrize("external_id", ["", None])
    def test_ordem_sem_id_externo_nunca_conta(
            self, tmp_path, monkeypatch, external_id):
        m, c = _app(tmp_path, monkeypatch)
        _com_ordem(m, external_id=external_id)
        assert _item(_status(c), "testnet")["pronto"] is False

    def test_ordem_externa_sem_reconciliacao_duravel_nao_conta(
            self, tmp_path, monkeypatch):
        """Um id opaco isolado não prova corretora nem reconciliação.

        A contraprova positiva agora passa pela rota real de reconciliação e
        fica persistida em ``test_testnet_readiness_proof.py``.
        """
        m, c = _app(tmp_path, monkeypatch)
        _com_ordem(m, external_id="4380219706")   # id real de exchange
        assert _item(_status(c), "testnet")["pronto"] is False

    def test_fill_parcial_nao_conta(self, tmp_path, monkeypatch):
        m, c = _app(tmp_path, monkeypatch)
        _com_ordem(m, estado=OrderState.PARTIALLY_FILLED,
                   external_id="4380219706")
        assert _item(_status(c), "testnet")["pronto"] is False

    def test_sem_mesa_nenhuma_nao_conta(self, tmp_path, monkeypatch):
        _, c = _app(tmp_path, monkeypatch)
        assert _item(_status(c), "testnet")["pronto"] is False


class TestPreparacaoExplicita:
    def test_permissoes_da_chave_aparecem_e_nao_estao_prontas(
            self, tmp_path, monkeypatch):
        m, c = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=H, json={"vars": CFG_LIVE_COMPLETO})
        item = _item(_status(c), "permiss")
        assert item["pronto"] is False and item["acao"]

    def test_identidade_da_conta_real_aparece_e_nao_esta_pronta(
            self, tmp_path, monkeypatch):
        m, c = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=H, json={"vars": CFG_LIVE_COMPLETO})
        item = _item(_status(c), "identidade")
        assert item["pronto"] is False and item["acao"]

    def test_primeiro_item_reserva_confirmacao_para_ativacao(
            self, tmp_path, monkeypatch):
        _, c = _app(tmp_path, monkeypatch)
        d = _status(c)
        assert "momento da ativação" in d["checklist"][0]["item"].lower()
        assert d["checklist"][0]["pronto"] is True

    def test_selo_continua_fechando_mesmo_com_testnet_de_verdade(
            self, tmp_path, monkeypatch):
        """O caminho mais verde possível: cfg completa + fill externa
        preenchida. Ainda assim: BLOQUEADO — o selo de código é a última
        fechadura e ela fica fechada nesta missão."""
        m, c = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=H, json={"vars": CFG_LIVE_COMPLETO})
        _com_ordem(m, external_id="4380219706")
        d = _status(c)
        assert d["pode_operar_real"] is False
        selos = [i for i in d["checklist"] if "código" in i["item"].lower()]
        assert len(selos) == 1 and selos[0]["pronto"] is False
        assert selos[0]["acao"]
