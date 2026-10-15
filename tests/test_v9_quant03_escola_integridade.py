"""V9-QUANT-03: integridade da evidência da Escola.

Achado (V9-BASELINE.md §5): o `Desfecho` não tinha ID imutável nem horário
de ENTRADA; duplicatas, datas futuras e operações abertas ANTES da
candidatura podiam contaminar a prova independente. Tratamento: a porta da
evidência recusa duplicata e data impossível, e a prova conta pela ABERTURA
da operação — legado sem abertura cai no fechamento, como antes.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.desk.escola import (
    AMOSTRA_PROVA,
    AMOSTRA_REVOGACAO,
    EscolaDeTraders,
    EstadoDaEscola,
    PerfilDeMercado,
)
from tradingagents.desk.learning import (
    Desfecho,
    MemoriaDeDesempenho,
    desfecho_de_dict,
    desfecho_para_dict,
)
from tradingagents.desk.salas import (
    ContextoDaSala,
    RegistroDeSalas,
    SalaError,
)

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
ESTRATEGIA = "trend_following"
ATIVO = "BTC/USDT"
SPOT = PerfilDeMercado("spot")
CANDIDATA = EstadoDaEscola.CANDIDATA
FORMADA = EstadoDaEscola.FORMADA


class ArmazemFake:
    """Armazém em memória com ida-e-volta por JSON, como o real."""

    def __init__(self):
        self.dados: dict = {}

    def carregar(self, tenant_id, chave):
        valor = self.dados.get((tenant_id, chave))
        return json.loads(json.dumps(valor)) if valor is not None else None

    def salvar(self, tenant_id, chave, payload):
        self.dados[(tenant_id, chave)] = json.loads(json.dumps(payload))


def _sala():
    return RegistroDeSalas(armazem=ArmazemFake()).abrir_sala(
        ContextoDaSala("binance", "paper-1", "spot", "paper", None))


def _desfecho(retorno, *, fechado_em=BASE, aberta_em=None, versao="1"):
    campos = {} if aberta_em is None else {"aberta_em": aberta_em}
    return Desfecho(estrategia=ESTRATEGIA, regime="tendencia",
                    instrumento=ATIVO, retorno=Decimal(str(retorno)),
                    fechado_em=fechado_em, versao=versao, **campos)


def _serie(retornos, *, inicio, aberta_em=None):
    campos = {} if aberta_em is None else {"aberta_em": aberta_em}
    return [Desfecho(estrategia=ESTRATEGIA, regime="tendencia",
                     instrumento=ATIVO, retorno=Decimal(str(r)),
                     fechado_em=inicio + timedelta(minutes=i),
                     versao="1", **campos)
            for i, r in enumerate(retornos)]


def _alternando(a, b, n):
    return [a if i % 2 == 0 else b for i in range(n)]


def _positivos(n):      # média +0.011 — IC inferior acima do alvo zero
    return _alternando(0.010, 0.012, n)


def _ruins(n):          # média -0.011 — IC superior abaixo do alvo zero
    return _alternando(-0.010, -0.012, n)


def _avaliar(esc, desfechos, agora):
    return esc.avaliar(estrategia=ESTRATEGIA, versao="1",
                       desfechos=desfechos, perfil=SPOT, agora=agora)


def _candidatar(esc):
    """Parcela preliminar aprovada: devolve (desfechos, instante t1)."""
    d1 = _serie(_positivos(220), inicio=BASE)
    t1 = BASE + timedelta(days=30)
    av = _avaliar(esc, d1, t1)
    assert av.estado is CANDIDATA
    return d1, t1


class TestPortaDaEvidencia:
    """Duplicata e data impossível são recusadas ANTES de virar estatística."""

    def test_memoria_rejeita_desfecho_duplicado(self):
        mem = MemoriaDeDesempenho()
        d = _desfecho("0.01")
        mem.registrar(d)
        with pytest.raises(ValueError, match="duplicad"):
            mem.registrar(d)
        assert len(mem.desfechos_de(ESTRATEGIA)) == 1

    def test_memoria_rejeita_fechamento_no_futuro(self):
        mem = MemoriaDeDesempenho()
        futuro = datetime.now(timezone.utc) + timedelta(hours=2)
        with pytest.raises(ValueError, match="futuro"):
            mem.registrar(_desfecho("0.01", fechado_em=futuro))
        assert mem.desfechos_de(ESTRATEGIA) == []

    def test_sala_rejeita_desfecho_duplicado(self):
        sala = _sala()
        d = _desfecho("0.01")
        sala.registrar_desfecho(d)
        with pytest.raises(SalaError, match="duplicad"):
            sala.registrar_desfecho(d)
        assert len(sala.desfechos) == 1

    def test_sala_rejeita_fechamento_no_futuro(self):
        sala = _sala()
        futuro = datetime.now(timezone.utc) + timedelta(hours=2)
        with pytest.raises(SalaError, match="futuro"):
            sala.registrar_desfecho(_desfecho("0.01", fechado_em=futuro))
        assert sala.desfechos == []

    def test_sala_rejeita_fechamento_anterior_a_abertura(self):
        sala = _sala()
        t = BASE + timedelta(days=1)
        with pytest.raises(SalaError, match="antes de ser aberto"):
            sala.registrar_desfecho(
                _desfecho("0.01", fechado_em=BASE, aberta_em=t))
        assert sala.desfechos == []


class TestProvaPelaEntrada:
    """A prova final conta pela ABERTURA: abrir antes da candidatura zera a
    independência, mesmo que o fechamento seja posterior."""

    def test_prova_nao_conta_operacao_aberta_antes_da_candidatura(self):
        esc = EscolaDeTraders()
        d1, t1 = _candidatar(esc)
        contaminadas = _serie(_positivos(AMOSTRA_PROVA),
                              inicio=t1 + timedelta(minutes=1),
                              aberta_em=t1 - timedelta(hours=2))
        av = _avaliar(esc, d1 + contaminadas, t1 + timedelta(days=90))
        assert av.estado is CANDIDATA

    def test_prova_conta_operacao_aberta_depois_da_candidatura(self):
        esc = EscolaDeTraders()
        d1, t1 = _candidatar(esc)
        prova = _serie(_positivos(AMOSTRA_PROVA),
                       inicio=t1 + timedelta(hours=1),
                       aberta_em=t1 + timedelta(minutes=5))
        av = _avaliar(esc, d1 + prova, t1 + timedelta(days=90))
        assert av.estado is FORMADA

    def test_sem_aberta_em_o_fechamento_segue_decidindo(self):
        # legado persistido sem horário de entrada: comportamento preservado
        esc = EscolaDeTraders()
        d1, t1 = _candidatar(esc)
        prova = _serie(_positivos(AMOSTRA_PROVA),
                       inicio=t1 + timedelta(hours=1))
        av = _avaliar(esc, d1 + prova, t1 + timedelta(days=90))
        assert av.estado is FORMADA

    def test_revogacao_nao_conta_operacao_aberta_antes_da_formatura(self):
        esc = EscolaDeTraders()
        d1, t1 = _candidatar(esc)
        prova = _serie(_positivos(AMOSTRA_PROVA),
                       inicio=t1 + timedelta(hours=1))
        t2 = t1 + timedelta(days=90)
        assert _avaliar(esc, d1 + prova, t2).estado is FORMADA
        novas = _serie(_ruins(AMOSTRA_REVOGACAO),
                       inicio=t2 + timedelta(hours=1),
                       aberta_em=t2 - timedelta(hours=1))
        av = _avaliar(esc, d1 + prova + novas, t2 + timedelta(days=90))
        assert av.estado is FORMADA
        assert av.novas_pos_formada == 0


class TestPonteEPersistencia:
    """A abertura percorre a ponte posição → desfecho → disco → escola."""

    def test_saida_da_sala_grava_aberta_em_no_desfecho(self):
        sala = _sala()
        t_a = BASE
        t_b = BASE + timedelta(hours=3)
        sala.registrar_entrada(ATIVO, "0.5", "100", estrategia=ESTRATEGIA,
                               quando=t_a)
        sala.registrar_saida(ATIVO, "110", quando=t_b)
        d = sala.desfechos[-1]
        assert d.aberta_em == t_a
        assert d.fechado_em == t_b

    def test_persistencia_preserva_aberta_em_e_aceita_legado_sem_ela(self):
        d = Desfecho(ESTRATEGIA, "tendencia", ATIVO, Decimal("0.01"),
                     BASE + timedelta(hours=1), aberta_em=BASE)
        volta = desfecho_de_dict(
            json.loads(json.dumps(desfecho_para_dict(d))))
        assert volta == d
        antigo = {k: v for k, v in desfecho_para_dict(d).items()
                  if k != "aberta_em"}
        assert desfecho_de_dict(antigo).aberta_em is None


class TestFiacaoDoOrquestrador:
    """A porta da evidência nunca pode derrubar uma saída já realizada."""

    def test_evidencia_recusada_vira_aviso_e_nao_derruba_a_saida(
            self, monkeypatch):
        from tradingagents.desk import orchestrator as mod
        from tradingagents.desk.models import DeskMandate, Mode
        from tradingagents.desk.risk import RiskEngine

        # Relógio congelado: dois fechamentos idênticos no MESMO instante —
        # exatamente o caso de duplicata que a porta precisa recusar.
        quando = BASE + timedelta(days=1)
        monkeypatch.setattr(mod, "utcnow", lambda: quando)
        mandato = DeskMandate(mandate_id="m", owner="p", account="a",
                              mode=Mode.PAPER, instruments=(ATIVO,),
                              capital=Decimal("1000"))
        mem = MemoriaDeDesempenho()
        orch = mod.DeskOrchestrator(mandato, [], worker_id="w1",
                                    risk=RiskEngine(mandato), memoria=mem)
        for _ in range(2):
            orch.register_fill(ATIVO, Decimal("1"), Decimal("100"), "t1",
                               Decimal("100"))
            assert orch.close_position(ATIVO,
                                       preco_saida=Decimal("110")) is not None
        assert len(mem.desfechos_de("t1")) == 1     # a segunda NÃO conta
        assert any("duplicad" in a for a in orch.avisos_de_evolucao)
