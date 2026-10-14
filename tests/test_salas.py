"""Escola Central + Salas independentes por corretora (BT-6).

O que estes testes provam, em ordem de importância:

1. ISOLAMENTO FINANCEIRO — saldo, posições, taxas e desfechos de uma sala
   nunca aparecem na conta de outra; desfecho de corretora estranha é
   recusado na porta.
2. ISOLAMENTO DE CERTIFICAÇÃO — formado na Binance NÃO é formado na OKX
   (nem em outro ativo ou horizonte); a prova de um contexto não alimenta o
   outro, por construção.
3. HOMOLOGAÇÃO — sala só existe para corretora com adaptador registrado;
   IQ Option é laboratório: modo paper, métricas próprias e jamais
   elegível a operação real.
4. LIVE — esta camada nem cria sala LIVE: autorização real continua
   exclusiva do proprietário pelo fluxo de piloto.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.desk.costs import custo_de_operar
from tradingagents.desk.escola import EstadoDaEscola
from tradingagents.desk.learning import Desfecho
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.salas import (
    AdaptadorDeCorretora,
    ContextoDaSala,
    ContextoDeCertificacao,
    EscolaCentral,
    RegistroDeSalas,
    SalaError,
    adaptador_de,
    corretoras_homologadas,
    registrar_adaptador,
)

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
ESTRATEGIA = "trend_following"
ATIVO = "BTC/USDT"


class ArmazemFake:
    """Armazém em memória com ida-e-volta por JSON, como o real."""

    def __init__(self):
        self.dados: dict = {}

    def carregar(self, tenant_id, chave):
        valor = self.dados.get((tenant_id, chave))
        return json.loads(json.dumps(valor)) if valor is not None else None

    def salvar(self, tenant_id, chave, payload):
        self.dados[(tenant_id, chave)] = json.loads(json.dumps(payload))


def _ctx(corretora="binance", conta="paper-1", mercado="spot", modo="paper",
         payout=None):
    return ContextoDaSala(corretora, conta, mercado, modo, payout)


def _certificado(corretora="binance", mercado="spot", ativo=ATIVO,
                 horizonte="1h"):
    return ContextoDeCertificacao(corretora, mercado, ativo, horizonte)


def _desfechos(estrategia, retornos, *, inicio=BASE, versao="1",
               instrumento=ATIVO, horizonte="1h"):
    return [
        Desfecho(estrategia=estrategia, regime="tendencia",
                 instrumento=instrumento, retorno=Decimal(str(r)),
                 fechado_em=inicio + timedelta(minutes=i), versao=versao,
                 horizonte=horizonte)
        for i, r in enumerate(retornos)
    ]


def _alternando(a, b, n):
    return [a if i % 2 == 0 else b for i in range(n)]


def _positivos(n):
    return _alternando(0.010, 0.012, n)


def _formar(central, sala, *, estrategia=ESTRATEGIA, ativo=ATIVO,
            horizonte="1h"):
    """Formação pelo caminho real: parcela preliminar → prova independente."""
    t1 = BASE + timedelta(days=30)
    for d in _desfechos(estrategia, _positivos(220), inicio=BASE,
                        horizonte=horizonte):
        sala.registrar_desfecho(d)
    av = central.avaliar(sala=sala, estrategia=estrategia, ativo=ativo,
                         horizonte=horizonte, agora=t1)
    assert av.estado is EstadoDaEscola.CANDIDATA
    t2 = t1 + timedelta(days=90)
    for d in _desfechos(estrategia, _positivos(300),
                        inicio=t1 + timedelta(hours=1), horizonte=horizonte):
        sala.registrar_desfecho(d)
    av = central.avaliar(sala=sala, estrategia=estrategia, ativo=ativo,
                         horizonte=horizonte, agora=t2)
    assert av.estado is EstadoDaEscola.FORMADA
    return t2


# -- homologação ----------------------------------------------------------
class TestAdaptadoresEHomologacao:
    def test_corretoras_padrao_estao_homologadas(self):
        nomes = {c["nome"] for c in corretoras_homologadas()}
        assert {"binance", "bybit", "okx", "iq_option"} <= nomes
        lab = {c["nome"]: c for c in corretoras_homologadas()}["iq_option"]
        assert lab["laboratorio"] is True
        assert lab["mercados"] == ["opcoes_binarias"]

    def test_registrar_sem_substituir_recusa_duplicata(self):
        with pytest.raises(SalaError, match="já homologada"):
            registrar_adaptador(AdaptadorDeCorretora("binance", ("spot",)))

    def test_extensao_por_adaptador_novo(self):
        registrar_adaptador(AdaptadorDeCorretora("corretora_teste_x",
                                                 ("spot",)))
        ad = adaptador_de("corretora_teste_x")
        assert ad.suporta("spot") and not ad.laboratorio
        registrar_adaptador(AdaptadorDeCorretora("corretora_teste_x",
                                                 ("spot", "futuros"),
                                                 versao="2"),
                            substituir=True)
        assert adaptador_de("corretora_teste_x").versao == "2"

    def test_adaptador_com_mercado_desconhecido_recusa(self):
        with pytest.raises(SalaError, match="desconhecido"):
            registrar_adaptador(AdaptadorDeCorretora("corretora_teste_y",
                                                     ("acoes",)))

    def test_corretora_sem_adaptador_nenhuma_sala(self):
        with pytest.raises(SalaError, match="sem adaptador homologado"):
            RegistroDeSalas(armazem=ArmazemFake()).abrir_sala(
                _ctx(corretora="nao_existe"))

    def test_mercado_incompativel_com_a_corretora(self):
        reg = RegistroDeSalas(armazem=ArmazemFake())
        with pytest.raises(SalaError, match="não opera"):
            reg.abrir_sala(_ctx(mercado="opcoes_binarias", payout="0.85"))
        with pytest.raises(SalaError, match="não opera"):
            reg.abrir_sala(_ctx(corretora="iq_option", conta="lab-1",
                                mercado="spot"))

    def test_binaria_exige_payout_e_spot_recusa_payout(self):
        reg = RegistroDeSalas(armazem=ArmazemFake())
        with pytest.raises(SalaError, match="payout"):
            reg.abrir_sala(_ctx(corretora="iq_option", conta="lab-1",
                                mercado="opcoes_binarias"))
        with pytest.raises(SalaError, match="payout"):
            reg.abrir_sala(_ctx(payout="0.80"))

    def test_laboratorio_so_opera_em_paper(self):
        reg = RegistroDeSalas(armazem=ArmazemFake())
        with pytest.raises(SalaError, match="laboratório"):
            reg.abrir_sala(_ctx(corretora="iq_option", conta="lab-1",
                                mercado="opcoes_binarias", modo="shadow",
                                payout="0.85"))

    def test_sala_live_nao_e_criada_por_esta_camada(self):
        with pytest.raises(SalaError, match="LIVE"):
            RegistroDeSalas(armazem=ArmazemFake()).abrir_sala(
                _ctx(modo="live"))

    def test_modo_desconhecido_e_conta_vazia_recusam(self):
        reg = RegistroDeSalas(armazem=ArmazemFake())
        with pytest.raises(SalaError, match="modo"):
            reg.abrir_sala(_ctx(modo="voo_livre"))
        with pytest.raises(SalaError, match="conta"):
            reg.abrir_sala(_ctx(conta=""))


# -- isolamento financeiro -------------------------------------------------
class TestSalasIsolamentoFinanceiro:
    def test_saldo_posicoes_taxas_e_desfechos_nao_se_misturam(self):
        armazem = ArmazemFake()
        reg = RegistroDeSalas(armazem=armazem)
        sala_b = reg.abrir_sala(_ctx(corretora="binance"))
        sala_o = reg.abrir_sala(_ctx(corretora="okx"))

        sala_b.registrar_entrada(ATIVO, 1, Decimal("100"), agente="t1",
                                 estrategia=ESTRATEGIA)
        sala_o.registrar_entrada(ATIVO, 1, Decimal("100"), agente="t1",
                                 estrategia=ESTRATEGIA)
        sala_b.registrar_saida(ATIVO, Decimal("105"), quando=BASE)

        assert sala_b.saldo_realizado != 0
        assert sala_b.taxas_acumuladas > 0
        assert len(sala_b.desfechos) == 1

        assert sala_o.saldo_realizado == 0
        assert sala_o.taxas_acumuladas == 0
        assert sala_o.desfechos == []
        assert ATIVO in sala_o.posicoes          # a posição da OKX segue viva

    def test_custos_reais_descontados_da_evidencia(self):
        reg = RegistroDeSalas(armazem=ArmazemFake())
        sala = reg.abrir_sala(_ctx(corretora="binance"))
        sala.registrar_entrada(ATIVO, 1, Decimal("100"), agente="t1",
                               estrategia=ESTRATEGIA)
        r = sala.registrar_saida(ATIVO, Decimal("105"), quando=BASE)

        custo = custo_de_operar(ATIVO, "binance", Decimal("100"))
        liquido = (Decimal("105") - Decimal("100")) / Decimal("100") \
            - custo.ida_e_volta
        assert r["retorno_liquido"] == liquido
        assert sala.saldo_realizado == Decimal("100") * liquido
        assert sala.taxas_acumuladas == Decimal("100") * custo.ida_e_volta
        assert sala.desfechos[0].retorno == liquido

    def test_desfecho_de_outra_corretora_e_recusado(self):
        reg = RegistroDeSalas(armazem=ArmazemFake())
        sala = reg.abrir_sala(_ctx(corretora="binance"))
        estranho = _desfechos(ESTRATEGIA, [0.01])[0]
        estranho = Desfecho(estrategia=estranho.estrategia,
                            regime=estranho.regime,
                            instrumento=estranho.instrumento,
                            retorno=estranho.retorno,
                            fechado_em=estranho.fechado_em,
                            exchange="okx")
        with pytest.raises(SalaError, match="não se misturam"):
            sala.registrar_desfecho(estranho)
        assert sala.desfechos == []

    def test_desfecho_sem_corretora_recebe_a_da_sala(self):
        reg = RegistroDeSalas(armazem=ArmazemFake())
        sala = reg.abrir_sala(_ctx(corretora="okx"))
        sala.registrar_desfecho(_desfechos(ESTRATEGIA, [0.01])[0])
        assert sala.desfechos[0].exchange == "okx"

    def test_uma_posicao_por_instrumento(self):
        reg = RegistroDeSalas(armazem=ArmazemFake())
        sala = reg.abrir_sala(_ctx())
        sala.registrar_entrada(ATIVO, 1, Decimal("100"))
        with pytest.raises(SalaError, match="já tem posição"):
            sala.registrar_entrada(ATIVO, 1, Decimal("101"))

    def test_saida_sem_posicao_recusa_e_preco_invalido_nao_fecha(self):
        reg = RegistroDeSalas(armazem=ArmazemFake())
        sala = reg.abrir_sala(_ctx())
        with pytest.raises(SalaError, match="não tem posição"):
            sala.registrar_saida(ATIVO, Decimal("105"))
        sala.registrar_entrada(ATIVO, 1, Decimal("100"))
        with pytest.raises(SalaError, match="positivo"):
            sala.registrar_saida(ATIVO, Decimal("0"))
        assert ATIVO in sala.posicoes            # nada foi fechado por engano
        assert sala.saldo_realizado == 0

    def test_persistencia_em_chave_propria_por_sala(self):
        armazem = ArmazemFake()
        reg = RegistroDeSalas(armazem=armazem)
        sala_b = reg.abrir_sala(_ctx(corretora="binance"))
        sala_o = reg.abrir_sala(_ctx(corretora="okx"))
        sala_b.registrar_entrada(ATIVO, 1, Decimal("100"), agente="t1",
                                 estrategia=ESTRATEGIA)
        sala_b.registrar_saida(ATIVO, Decimal("105"), quando=BASE)
        sala_o.registrar_entrada("ETH/USDT", 2, Decimal("50"))

        assert ("legacy", "sala::binance|paper-1|spot|paper") in armazem.dados
        assert ("legacy", "sala::okx|paper-1|spot|paper") in armazem.dados

        novo = RegistroDeSalas(armazem=armazem)      # reinício
        b2 = novo.abrir_sala(_ctx(corretora="binance"))
        o2 = novo.abrir_sala(_ctx(corretora="okx"))
        assert b2.saldo_realizado == sala_b.saldo_realizado
        assert len(b2.desfechos) == 1
        assert o2.saldo_realizado == 0
        assert len(o2.desfechos) == 0
        assert "ETH/USDT" in o2.posicoes             # posição aberta sobrevive
        assert set(novo.salas) == {"binance|paper-1|spot|paper",
                                   "okx|paper-1|spot|paper"}


# -- isolamento de certificação -------------------------------------------
class TestCertificacaoPorContexto:
    def test_formado_na_binance_nao_vale_na_okx(self):
        central = EscolaCentral(armazem=ArmazemFake())
        sala_b = RegistroDeSalas(
            armazem=central.armazem).abrir_sala(_ctx(corretora="binance"))
        _formar(central, sala_b)

        ok, motivo = central.elegivel_para_live(
            ESTRATEGIA, "1", contexto=_certificado(corretora="binance"))
        assert ok is True and "elegível" in motivo

        nao, motivo2 = central.elegivel_para_live(
            ESTRATEGIA, "1", contexto=_certificado(corretora="okx"))
        assert nao is False
        assert "nenhuma evidência" in motivo2

    def test_outro_ativo_e_outro_horizonte_nao_herdam_formacao(self):
        central = EscolaCentral(armazem=ArmazemFake())
        sala = RegistroDeSalas(
            armazem=central.armazem).abrir_sala(_ctx(corretora="binance"))
        _formar(central, sala)

        nao, _ = central.elegivel_para_live(
            ESTRATEGIA, "1",
            contexto=_certificado(ativo="ETH/USDT"))
        assert nao is False
        nao, _ = central.elegivel_para_live(
            ESTRATEGIA, "1",
            contexto=_certificado(horizonte="4h"))
        assert nao is False

    def test_outra_versao_da_estrategia_nao_herda_formacao(self):
        central = EscolaCentral(armazem=ArmazemFake())
        sala = RegistroDeSalas(
            armazem=central.armazem).abrir_sala(_ctx(corretora="binance"))
        _formar(central, sala, estrategia=ESTRATEGIA)
        nao, motivo = central.elegivel_para_live(
            ESTRATEGIA, "2", contexto=_certificado(corretora="binance"))
        assert nao is False and "sem matrícula" in motivo

    def test_certificacao_persiste_e_aparece_no_relatorio(self):
        armazem = ArmazemFake()
        central = EscolaCentral(armazem=armazem)
        sala = RegistroDeSalas(armazem=armazem).abrir_sala(
            _ctx(corretora="binance"))
        _formar(central, sala)
        assert ("legacy", "escola::binance|spot|BTC/USDT|1h") in armazem.dados

        reaberta = EscolaCentral(armazem=armazem)     # reinício
        ok, _ = reaberta.elegivel_para_live(
            ESTRATEGIA, "1", contexto=_certificado(corretora="binance"))
        assert ok is True
        rel = reaberta.relatorio()
        assert rel["total_contextos"] == 1
        linha = rel["certificados"][0]
        assert linha["contexto"] == "binance|spot|BTC/USDT|1h"
        assert linha["estado"] == "formada"
        assert "proprietário" in rel["aviso"]

    def test_laboratorio_forma_mas_nunca_e_elegivel(self):
        armazem = ArmazemFake()
        central = EscolaCentral(armazem=armazem)
        sala_lab = RegistroDeSalas(armazem=armazem).abrir_sala(
            _ctx(corretora="iq_option", conta="lab-1",
                 mercado="opcoes_binarias", payout="0.85"))
        _formar(central, sala_lab)
        rel = central.relatorio()
        assert rel["certificados"][0]["estado"] == "formada"

        elegivel, motivo = central.elegivel_para_live(
            ESTRATEGIA, "1",
            contexto=_certificado(corretora="iq_option",
                                  mercado="opcoes_binarias"))
        assert elegivel is False
        assert "laboratório" in motivo

    def test_evidencia_da_sala_de_outra_corretora_nao_e_vista(self):
        armazem = ArmazemFake()
        central = EscolaCentral(armazem=armazem)
        reg = RegistroDeSalas(armazem=armazem)
        sala_b = reg.abrir_sala(_ctx(corretora="binance"))
        sala_o = reg.abrir_sala(_ctx(corretora="okx"))
        _formar(central, sala_b)

        # A mesma avaliação, a partir da sala da OKX, não vê nada: os
        # desfechos da Binance vivem na sala da Binance.
        av = central.avaliar(sala=sala_o, estrategia=ESTRATEGIA, ativo=ATIVO,
                             agora=BASE + timedelta(days=120))
        assert av.estado is EstadoDaEscola.COLETANDO
        assert av.amostras == 0


# -- livro-razão separado por sala ----------------------------------------
class TestLedgerPorSala:
    def test_livros_de_salas_diferentes_nao_se_misturam(self):
        reg = RegistroDeSalas(armazem=ArmazemFake())
        sala_b = reg.abrir_sala(_ctx(corretora="binance"),
                                ledger=Ledger(opening_cash=Decimal("1000")))
        sala_o = reg.abrir_sala(_ctx(corretora="okx"),
                                ledger=Ledger(opening_cash=Decimal("500")))
        sala_b.ledger.ai_cost(Decimal("30"), "t1", "custo de IA")
        assert sala_b.ledger.cash() == Decimal("970")
        assert sala_o.ledger.cash() == Decimal("500")

    def test_ledger_persiste_junto_com_a_sala(self):
        armazem = ArmazemFake()
        reg = RegistroDeSalas(armazem=armazem)
        sala = reg.abrir_sala(_ctx(corretora="binance"),
                              ledger=Ledger(opening_cash=Decimal("1000")))
        sala.ledger.ai_cost(Decimal("30"), "t1", "custo de IA")
        sala.salvar()

        nova = RegistroDeSalas(armazem=armazem).abrir_sala(
            _ctx(corretora="binance"))
        assert nova.ledger is not None
        assert nova.ledger.cash() == Decimal("970")
        assert nova.resumo()["tem_ledger"] is True
