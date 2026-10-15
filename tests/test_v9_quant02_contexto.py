"""V9-QUANT-02 (RED→GREEN): certificação fechada pelo contexto COMPLETO.

Achado (V9-BASELINE §5): a chave de formação da Escola Central ignorava
conta, modo e payout — evidência formada na conta A em `paper` certificava
a conta B, e payout 0.85 certificava 0.92 no laboratório.

Contrato exigido por estes testes:

1. a chave de certificação inclui
   corretora|conta|mercado|modo|ativo|horizonte (e |payout quando houver);
2. evidência de OUTRA conta/modo/payout NÃO certifica — fail-closed;
3. o contexto completo idêntico permanece elegível (sem regressão);
4. entrada LEGADA de catálogo (sem conta/modo) não certifica nada e é
   rotulada como legada no relatório — sem criar contexto fantasma.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tradingagents.desk.escola import EstadoDaEscola
from tradingagents.desk.learning import Desfecho
from tradingagents.desk.salas import (
    ContextoDaSala,
    ContextoDeCertificacao,
    EscolaCentral,
    RegistroDeSalas,
)

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
ESTRATEGIA = "trend_following"
ATIVO = "BTC/USDT"
CHAVE_LEGADA = "binance|spot|BTC/USDT|1h"


class ArmazemFake:
    """Armazém em memória com ida-e-volta por JSON, como o real."""

    def __init__(self):
        self.dados: dict = {}

    def carregar(self, tenant_id, chave):
        valor = self.dados.get((tenant_id, chave))
        return json.loads(json.dumps(valor)) if valor is not None else None

    def salvar(self, tenant_id, chave, payload):
        self.dados[(tenant_id, chave)] = json.loads(json.dumps(payload))


def _cert(corretora="binance", conta="paper-1", mercado="spot", modo="paper",
          ativo=ATIVO, horizonte="1h",
          payout=None) -> ContextoDeCertificacao:
    """Contexto COMPLETO pelo caminho de leitura (dict → de_dict)."""
    doc = {"corretora": corretora, "conta": conta, "mercado": mercado,
           "modo": modo, "ativo": ativo, "horizonte": horizonte}
    if payout is not None:
        doc["payout"] = str(payout)
    return ContextoDeCertificacao.de_dict(doc)


def _desfechos(estrategia, retornos, *, inicio=BASE, horizonte="1h"):
    return [
        Desfecho(estrategia=estrategia, regime="tendencia",
                 instrumento=ATIVO, retorno=Decimal(str(r)),
                 fechado_em=inicio + timedelta(minutes=i), versao="1",
                 horizonte=horizonte)
        for i, r in enumerate(retornos)
    ]


def _positivos(n):
    return [0.010 if i % 2 == 0 else 0.012 for i in range(n)]


def _formar(central, sala, *, horizonte="1h"):
    """Formação pelo caminho real: parcela preliminar → prova independente."""
    t1 = BASE + timedelta(days=30)
    for d in _desfechos(ESTRATEGIA, _positivos(220), inicio=BASE,
                        horizonte=horizonte):
        sala.registrar_desfecho(d)
    av = central.avaliar(sala=sala, estrategia=ESTRATEGIA, ativo=ATIVO,
                         horizonte=horizonte, agora=t1)
    assert av.estado is EstadoDaEscola.CANDIDATA
    t2 = t1 + timedelta(days=90)
    for d in _desfechos(ESTRATEGIA, _positivos(300),
                        inicio=t1 + timedelta(hours=1), horizonte=horizonte):
        sala.registrar_desfecho(d)
    av = central.avaliar(sala=sala, estrategia=ESTRATEGIA, ativo=ATIVO,
                         horizonte=horizonte, agora=t2)
    assert av.estado is EstadoDaEscola.FORMADA


class TestChaveDeCertificacao:
    def test_chave_inclui_conta_modo_e_payout(self):
        c = _cert(conta="conta-a", modo="paper")
        assert c.chave == "binance|conta-a|spot|paper|BTC/USDT|1h"

        lab = _cert(corretora="iq_option", conta="lab-1",
                    mercado="opcoes_binarias", ativo="EUR/USD",
                    horizonte="5m", payout="0.85")
        assert lab.chave == ("iq_option|lab-1|opcoes_binarias|paper|"
                             "EUR/USD|5m|0.85")
        assert lab.como_dict()["payout"] == "0.85"
        assert ContextoDeCertificacao.de_dict(
            lab.como_dict()).chave == lab.chave

    def test_construtor_aceita_conta_modo_payout_por_nome(self):
        direto = ContextoDeCertificacao("binance", "spot", ATIVO, "1h",
                                        conta="conta-a", modo="paper")
        assert direto.chave == _cert(conta="conta-a", modo="paper").chave


class TestIsolamentoPorContextoCompleto:
    def test_evidencia_de_outra_conta_nao_certifica(self):
        armazem = ArmazemFake()
        central = EscolaCentral(armazem=armazem)
        sala = RegistroDeSalas(armazem=armazem).abrir_sala(
            ContextoDaSala("binance", "conta-a", "spot", "paper"))
        _formar(central, sala)

        ok, motivo = central.elegivel_para_live(
            ESTRATEGIA, "1", contexto=_cert(conta="conta-a"))
        assert ok is True and "formada" in motivo
        assert central.estado_de(ESTRATEGIA, "1",
                                 contexto=_cert(conta="conta-a")) == "formada"

        outra, motivo = central.elegivel_para_live(
            ESTRATEGIA, "1", contexto=_cert(conta="conta-b"))
        assert outra is False and "nenhuma evidência" in motivo
        assert central.estado_de(ESTRATEGIA, "1",
                                 contexto=_cert(conta="conta-b")) == "coletando"

    def test_evidencia_de_outro_modo_nao_certifica(self):
        armazem = ArmazemFake()
        central = EscolaCentral(armazem=armazem)
        sala = RegistroDeSalas(armazem=armazem).abrir_sala(
            ContextoDaSala("binance", "conta-a", "spot", "paper"))
        _formar(central, sala)

        outro, motivo = central.elegivel_para_live(
            ESTRATEGIA, "1", contexto=_cert(conta="conta-a", modo="testnet"))
        assert outro is False and "nenhuma evidência" in motivo
        assert central.estado_de(ESTRATEGIA, "1",
                                 contexto=_cert(conta="conta-a",
                                                modo="testnet")) == "coletando"

    def test_payout_diferente_nao_certifica_no_laboratorio(self):
        armazem = ArmazemFake()
        central = EscolaCentral(armazem=armazem)
        sala = RegistroDeSalas(armazem=armazem).abrir_sala(
            ContextoDaSala("iq_option", "lab-1", "opcoes_binarias",
                           "paper", "0.85"))
        _formar(central, sala)

        igual = _cert(corretora="iq_option", conta="lab-1",
                      mercado="opcoes_binarias", ativo=ATIVO, payout="0.85")
        assert central.estado_de(ESTRATEGIA, "1", contexto=igual) == "formada"
        outro = _cert(corretora="iq_option", conta="lab-1",
                      mercado="opcoes_binarias", ativo=ATIVO, payout="0.92")
        assert central.estado_de(ESTRATEGIA, "1",
                                 contexto=outro) == "coletando"


class TestLegadoFailClosed:
    def test_entrada_legada_nao_certifica_contexto_novo(self):
        armazem = ArmazemFake()
        doc_legado = {"corretora": "binance", "mercado": "spot",
                      "ativo": ATIVO, "horizonte": "1h",
                      "criada_em": "2026-01-01T00:00:00+00:00"}
        armazem.salvar("legacy", "escola_central",
                       {"contextos": {CHAVE_LEGADA: doc_legado}})
        armazem.salvar("legacy", f"escola::{CHAVE_LEGADA}", {"matriculas": {
            f"{ESTRATEGIA}@1": {"estado": "formada", "motivo": "legado",
                                "formado_em": "2026-01-01T00:00:00+00:00"}}})
        central = EscolaCentral(armazem=armazem)

        # Consulta reconstruída da própria entrada legada: sem conta/modo o
        # contexto nunca casa com a chave legada — evidência velha não
        # certifica nada no contrato novo (fail-closed).
        legado = ContextoDeCertificacao.de_dict(doc_legado)
        ok, motivo = central.elegivel_para_live(ESTRATEGIA, "1",
                                                contexto=legado)
        assert ok is False and "nenhuma evidência" in motivo
        assert central.estado_de(ESTRATEGIA, "1",
                                 contexto=legado) == "coletando"

        rel = central.relatorio()
        assert rel["total_contextos"] == 1
        assert set(central._catalogo) == {CHAVE_LEGADA}
        linha = rel["certificados"][0]
        assert linha["contexto"] == CHAVE_LEGADA
        assert linha.get("legado") is True
        assert all("||" not in chave for chave in central._escolas)
