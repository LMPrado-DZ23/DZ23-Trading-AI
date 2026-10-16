"""Piloto supervisionado (BT-3): a cadeia obrigatória do Batalhão —
FORMADO → ELEGÍVEL → AGUARDANDO AUTORIZAÇÃO → PILOTO AUTORIZADO — com
gates de ativação, limites do dono, vigia contínuo e revogação auditável.

Nada aqui ativa operação real: o piloto roda em modo `paper (simulado)` e
o gate de execução exige explicitamente o envio real DESATIVADO.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.desk.escola import EscolaDeTraders, EstadoDaEscola
from tradingagents.desk.learning import Desfecho, MemoriaDeDesempenho
from tradingagents.desk.models import (
    Action,
    AgentStage,
    DeskMandate,
    MarketSnapshot,
    Mode,
)
from tradingagents.desk.orchestrator import DeskOrchestrator
from tradingagents.desk.piloto import (
    GATES_ESPERADOS,
    MODO_DO_PILOTO,
    EstadoDoPiloto,
    SupervisorDePiloto,
)
from tradingagents.desk.risk import RiskEngine
from tradingagents.desk.traders import (
    CHANCE_ENTRADA_CONTROLE,
    ControleAleatorio,
    TrendTrader,
    default_desk,
)

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
AGORA = BASE + timedelta(days=200)
AGENTE = "trader-trend"
UNIV = ("BTC/USDT",)


class ArmazemFake:
    """Armazém em memória com ida-e-volta por JSON, como o real."""

    def __init__(self):
        self.dados: dict = {}

    def carregar(self, tenant_id, chave):
        valor = self.dados.get((tenant_id, chave))
        return json.loads(json.dumps(valor)) if valor is not None else None

    def salvar(self, tenant_id, chave, payload):
        self.dados[(tenant_id, chave)] = json.loads(json.dumps(payload))


# -- escola: caminho real até FORMADA (sem atalhos) ----------------------
def _desfechos(estrategia, retornos, *, inicio=BASE, versao="1"):
    return [
        Desfecho(estrategia=estrategia, regime="tendencia",
                 instrumento="BTC/USDT", retorno=Decimal(str(r)),
                 fechado_em=inicio + timedelta(minutes=i), versao=versao)
        for i, r in enumerate(retornos)
    ]


def _alternando(a, b, n):
    return [a if i % 2 == 0 else b for i in range(n)]


def _positivos(n):
    return _alternando(0.010, 0.012, n)


def _ruins(n):
    return _alternando(-0.010, -0.012, n)


def _escola_formada(estrategia="trend_following"):
    esc = EscolaDeTraders(armazem=ArmazemFake())
    d1 = _desfechos(estrategia, _positivos(220))
    t1 = BASE + timedelta(days=30)
    esc.avaliar(estrategia=estrategia, desfechos=d1, agora=t1)
    prova = _desfechos(estrategia, _positivos(300),
                       inicio=t1 + timedelta(hours=1))
    t2 = t1 + timedelta(days=90)
    esc.avaliar(estrategia=estrategia, desfechos=d1 + prova, agora=t2)
    assert esc.estado_de(estrategia) is EstadoDaEscola.FORMADA
    return esc, t2


def _revogar_escola(esc, estrategia, t2, qtd=50):
    novas = _desfechos(estrategia, _ruins(qtd),
                       inicio=t2 + timedelta(hours=1))
    av = esc.avaliar(estrategia=estrategia, desfechos=novas,
                     agora=t2 + timedelta(days=10))
    assert av.estado is EstadoDaEscola.FORMATURA_REVOGADA


# -- supervisor: fábricas -------------------------------------------------
def _gates_ok(**over):
    base = {"single_owner": True, "kill_switch": True, "risco": True,
            "reconciliacao": True, "corretora": True,
            "envio_real_desativado": True}
    base.update(over)
    return SupervisorDePiloto.gates_de_ativacao(**base)


def _supervisor_formado(estrategia="trend_following"):
    esc, t2 = _escola_formada(estrategia)
    sup = SupervisorDePiloto(armazem=ArmazemFake(), escola=esc)
    return sup, esc, t2


def _solicitar(sup, agora=None, **over):
    kw = {"estrategia": "trend_following", "versao": "1",
          "corretora": "binance", "conta": "paper-1",
          "capital_maximo": Decimal("1000"),
          "perda_diaria_maxima": Decimal("50"),
          "perda_total_maxima": Decimal("200"), "prazo_dias": 30,
          "condicoes_de_suspensao": ("revogação da formatura",)}
    kw.update(over)
    return sup.solicitar(AGENTE, agora=agora or AGORA, **kw)


# -- controle aleatório: série e snapshots --------------------------------
def _saw(n=80):
    base = [100, 99.5, 99, 98.5, 98, 98.5, 99, 99.5]
    return tuple(Decimal(str(base[i % 8])) for i in range(n))


def _snap(corte, closes=None):
    return MarketSnapshot(instrument="BTC/USDT", timeframe="1h",
                          cutoff=corte, closes=closes or _saw())


def _trader_controle():
    t = ControleAleatorio("trader-controle", UNIV)
    return _enable(t)


def _enable(t):
    for st in (AgentStage.VALIDATED, AgentStage.SHADOW, AgentStage.PAPER_ENABLED):
        t.spec.move_to(st)
    return t


def _entrada_sorteada(t, n=2000):
    """Procura deterministicamente um cutoff em que o sorteio vira BUY."""
    for i in range(n):
        snap = _snap(BASE + timedelta(hours=i))
        if t.propose(snap).action is Action.BUY:
            return snap
    return None


class TestCadeiaObrigatoria:
    CONTEXTO = {
        "corretora": "binance", "conta": "paper-1", "mercado": "spot",
        "modo": "paper", "ativo": "BTC/USDT", "horizonte": "1h",
    }

    def test_formatura_encaminha_para_sala_real_sem_autorizar(self):
        sup, _, _ = _supervisor_formado()
        fila = sup.encaminhar_formado(
            AGENTE, estrategia="trend_following", contexto=self.CONTEXTO,
            agora=AGORA)
        assert fila["estado"] == "aguardando_configuracao_do_proprietario"
        assert fila["authorization_id"] is None
        assert sup.de(AGENTE) is None
        assert "sala REAL" in sup.bloqueios()[AGENTE]
        with pytest.raises(ValueError, match="não tem pedido"):
            sup.autorizar(AGENTE, gates=_gates_ok(), agora=AGORA)

    def test_encaminhamento_e_idempotente_persistente_e_consumido(self):
        arm = ArmazemFake()
        esc, _ = _escola_formada()
        sup = SupervisorDePiloto(armazem=arm, escola=esc)
        primeiro = sup.encaminhar_formado(
            AGENTE, estrategia="trend_following", contexto=self.CONTEXTO,
            agora=AGORA)
        segundo = sup.encaminhar_formado(
            AGENTE, estrategia="trend_following", contexto=self.CONTEXTO,
            agora=AGORA + timedelta(minutes=1))
        assert primeiro["encaminhamento_id"] == segundo["encaminhamento_id"]
        assert len(sup.encaminhamentos()) == 1
        sup2 = SupervisorDePiloto(armazem=arm, escola=esc)
        assert len(sup2.encaminhamentos()) == 1
        _solicitar(sup2, contexto=self.CONTEXTO)
        fila = sup2.encaminhamentos()[0]
        assert fila["estado"] == "configurado_aguardando_autorizacao"
        assert fila["authorization_id"] == sup2.de(AGENTE).authorization_id

    def test_encaminhamento_tardio_nao_regride_piloto_ja_autorizado(self):
        sup, _, _ = _supervisor_formado()
        _solicitar(sup, contexto=self.CONTEXTO)
        autorizado = sup.autorizar(AGENTE, gates=_gates_ok(), agora=AGORA)

        fila = sup.encaminhar_formado(
            AGENTE, estrategia="trend_following", contexto=self.CONTEXTO,
            agora=AGORA + timedelta(minutes=1))

        assert fila["authorization_id"] == autorizado.authorization_id
        assert fila["estado"] == "piloto_autorizado"
        assert AGENTE not in sup.bloqueios(agora=AGORA + timedelta(minutes=2))

    def test_fila_existente_acompanha_autorizacao_e_revogacao_persistidas(self):
        """A Sala REAL precisa refletir a cadeia comum, não só a tardia."""
        sup, esc, _ = _supervisor_formado()
        fila = sup.encaminhar_formado(
            AGENTE, estrategia="trend_following", contexto=self.CONTEXTO,
            agora=AGORA)
        _solicitar(sup, contexto=self.CONTEXTO, agora=AGORA)

        autorizado = sup.autorizar(
            AGENTE, gates=_gates_ok(), agora=AGORA + timedelta(minutes=1))

        fila_autorizada = sup.encaminhamentos()[0]
        assert fila_autorizada["encaminhamento_id"] == fila["encaminhamento_id"]
        assert fila_autorizada["authorization_id"] == autorizado.authorization_id
        assert fila_autorizada["estado"] == "piloto_autorizado"
        assert fila_autorizada["atualizada_em"] == (
            AGORA + timedelta(minutes=1)).isoformat()
        recarregado = SupervisorDePiloto(
            armazem=sup.armazem, escola=esc).encaminhamentos()[0]
        assert recarregado["estado"] == "piloto_autorizado"

        sup.revogar(
            AGENTE, motivo="decisão do proprietário",
            agora=AGORA + timedelta(minutes=2))

        fila_revogada = sup.encaminhamentos()[0]
        assert fila_revogada["estado"] == "revogado"
        assert fila_revogada["atualizada_em"] == (
            AGORA + timedelta(minutes=2)).isoformat()

    def test_fila_existente_acompanha_expiracao_e_suspensao(self):
        sup, _, _ = _supervisor_formado()
        sup.encaminhar_formado(
            AGENTE, estrategia="trend_following", contexto=self.CONTEXTO,
            agora=AGORA)
        _solicitar(sup, contexto=self.CONTEXTO, agora=AGORA, prazo_dias=1)
        sup.autorizar(AGENTE, gates=_gates_ok(), agora=AGORA)

        sup.vigiar(agora=AGORA + timedelta(days=1))

        assert sup.de(AGENTE).estado is EstadoDoPiloto.EXPIRADO
        assert sup.encaminhamentos()[0]["estado"] == "expirado"

        outro, escola, formado_em = _supervisor_formado()
        outro.encaminhar_formado(
            AGENTE, estrategia="trend_following", contexto=self.CONTEXTO,
            agora=AGORA)
        _solicitar(outro, contexto=self.CONTEXTO, agora=AGORA)
        outro.autorizar(AGENTE, gates=_gates_ok(), agora=AGORA)
        _revogar_escola(escola, "trend_following", formado_em)

        outro.vigiar(agora=AGORA + timedelta(minutes=1))

        assert outro.de(AGENTE).estado is EstadoDoPiloto.SUSPENSO
        assert outro.encaminhamentos()[0]["estado"] == "suspenso"

    def test_sem_formatura_solicitar_recusa(self):
        esc = EscolaDeTraders(armazem=ArmazemFake())
        sup = SupervisorDePiloto(armazem=ArmazemFake(), escola=esc)
        with pytest.raises(ValueError, match="não elegível"):
            _solicitar(sup)
        assert sup.de(AGENTE) is None

    def test_versao_diferente_nao_e_elegivel(self):
        sup, _, _ = _supervisor_formado()
        with pytest.raises(ValueError, match="não elegível"):
            _solicitar(sup, versao="2")

    def test_sem_escola_solicitar_recusa(self):
        sup = SupervisorDePiloto(armazem=ArmazemFake())
        with pytest.raises(ValueError, match="escola ausente"):
            _solicitar(sup)

    def test_formado_solicita_aguardando(self):
        sup, _, _ = _supervisor_formado()
        a = _solicitar(sup)
        assert a.estado is EstadoDoPiloto.AGUARDANDO_AUTORIZACAO
        assert a.modo == MODO_DO_PILOTO
        assert a.autorizado_em is None and a.valido_ate is None
        assert a.capital_maximo == Decimal("1000")
        # aguardando autorização ⇒ já bloqueado para entradas
        assert "aguardando" in sup.bloqueios()[AGENTE]

    def test_pedido_duplicado_recusado(self):
        sup, _, _ = _supervisor_formado()
        _solicitar(sup)
        with pytest.raises(ValueError, match="já tem pedido em andamento"):
            _solicitar(sup)

    def test_limites_invalidos_recusados(self):
        sup, _, _ = _supervisor_formado()
        with pytest.raises(ValueError, match="positivo"):
            _solicitar(sup, capital_maximo=Decimal("0"))
        with pytest.raises(ValueError, match="perda diária"):
            _solicitar(sup, perda_diaria_maxima=Decimal("500"),
                       perda_total_maxima=Decimal("100"))
        with pytest.raises(ValueError, match="prazo"):
            _solicitar(sup, prazo_dias=0)
        assert sup.de(AGENTE) is None

    @pytest.mark.parametrize(
        "campo,valor",
        [("capital_maximo", Decimal("NaN")),
         ("perda_diaria_maxima", Decimal("Infinity")),
         ("perda_total_maxima", Decimal("-Infinity"))],
    )
    def test_limites_nao_finitos_recusados_no_supervisor(self, campo, valor):
        sup, _, _ = _supervisor_formado()

        with pytest.raises(ValueError, match="finitos"):
            _solicitar(sup, **{campo: valor})

        assert sup.de(AGENTE) is None

    def test_autorizar_sem_pedido_recusa(self):
        sup, _, _ = _supervisor_formado()
        with pytest.raises(ValueError, match="não tem pedido"):
            sup.autorizar(AGENTE, gates=_gates_ok(), agora=AGORA)

    def test_autorizar_exige_gates(self):
        sup, _, _ = _supervisor_formado()
        _solicitar(sup)
        with pytest.raises(ValueError, match="execução"):
            sup.autorizar(AGENTE, gates=_gates_ok(envio_real_desativado=False),
                          agora=AGORA)
        with pytest.raises(ValueError, match="gate ausente"):
            sup.autorizar(AGENTE,
                          gates=[{"gate": "risco", "ok": True, "detalhe": ""}],
                          agora=AGORA)
        assert sup.de(AGENTE).estado is EstadoDoPiloto.AGUARDANDO_AUTORIZACAO

    def test_autorizar_revalida_formatura_no_ultimo_instante(self):
        sup, esc, t2 = _supervisor_formado()
        _solicitar(sup)
        _revogar_escola(esc, "trend_following", t2)
        with pytest.raises(ValueError, match="elegibilidade retirada"):
            sup.autorizar(AGENTE, gates=_gates_ok(), agora=AGORA)
        assert sup.de(AGENTE).estado is EstadoDoPiloto.AGUARDANDO_AUTORIZACAO

    def test_live_bloqueia_todo_agente_sem_autorizacao_explicita(self):
        sup, _, _ = _supervisor_formado()
        assert "outro-agente" not in sup.bloqueios(
            agentes_esperados=["outro-agente"])
        bloqueios = sup.bloqueios(
            exigir_autorizacao=True,
            agentes_esperados=[AGENTE, "outro-agente"])
        assert set(bloqueios) == {AGENTE, "outro-agente"}
        assert all("LIVE exige piloto" in motivo
                   for motivo in bloqueios.values())

    def test_live_libera_somente_o_piloto_autorizado(self):
        sup, _, _ = _supervisor_formado()
        _solicitar(sup)
        sup.autorizar(AGENTE, gates=_gates_ok(), agora=AGORA)
        bloqueios = sup.bloqueios(
            exigir_autorizacao=True,
            agentes_esperados=[AGENTE, "outro-agente"], agora=AGORA)
        assert AGENTE not in bloqueios
        assert "outro-agente" in bloqueios

    def test_cadeia_completa_autoriza(self):
        sup, _, _ = _supervisor_formado()
        _solicitar(sup)
        a = sup.autorizar(AGENTE, gates=_gates_ok(), agora=AGORA)
        assert a.estado is EstadoDoPiloto.PILOTO_AUTORIZADO
        assert a.autorizado_em == AGORA.isoformat()
        assert a.valido_ate == (AGORA + timedelta(days=30)).isoformat()
        # dentro dos limites: silêncio do vigia = permissão de ENTRADA
        assert sup.bloqueios(agora=AGORA + timedelta(days=1)) == {}
        rel = sup.relatorio()[0]
        assert rel["estado"] == "piloto_autorizado"
        assert len(rel["eventos"]) >= 2
        assert "proprietário" in rel["aviso"]

    def test_gates_de_ativacao(self):
        gates = _gates_ok()
        assert [g["gate"] for g in gates] == list(GATES_ESPERADOS)
        assert all(g["ok"] for g in gates)
        por_nome = {g["gate"]: g for g in _gates_ok(kill_switch=False)}
        assert por_nome["segurança"]["ok"] is False
        por_nome = {g["gate"]: g for g in _gates_ok(corretora=False)}
        assert por_nome["integração"]["ok"] is False


class TestLimitesEVigia:
    def _autorizado(self, **over):
        sup, esc, t2 = _supervisor_formado()
        _solicitar(sup, **over)
        sup.autorizar(AGENTE, gates=_gates_ok(), agora=AGORA)
        return sup, esc, t2

    def test_capital_acima_do_maximo_bloqueia(self):
        sup, _, _ = self._autorizado()
        b = sup.bloqueios(agora=AGORA, capital_em_uso={AGENTE: Decimal("1200")})
        assert "capital" in b[AGENTE]
        # limite de capital corta entrada, NÃO suspende o piloto
        assert sup.de(AGENTE).estado is EstadoDoPiloto.PILOTO_AUTORIZADO

    def test_perda_diaria_atingida_bloqueia(self):
        sup, _, _ = self._autorizado()
        b = sup.bloqueios(agora=AGORA, perda_hoje={AGENTE: Decimal("50")})
        assert "perda diária" in b[AGENTE]
        assert sup.de(AGENTE).estado is EstadoDoPiloto.PILOTO_AUTORIZADO

    def test_perda_total_suspende_terminal(self):
        sup, _, _ = self._autorizado()
        b = sup.bloqueios(agora=AGORA, perda_total={AGENTE: Decimal("200")})
        assert "suspenso" in b[AGENTE]
        assert sup.de(AGENTE).estado is EstadoDoPiloto.SUSPENSO
        # suspenso NÃO volta sozinho, nem com tudo dentro dos limites
        sup.vigiar(agora=AGORA + timedelta(days=1))
        assert sup.de(AGENTE).estado is EstadoDoPiloto.SUSPENSO
        assert "suspenso" in sup.bloqueios(agora=AGORA + timedelta(days=1))[AGENTE]

    def test_dentro_dos_limites_nao_bloqueia(self):
        sup, _, _ = self._autorizado()
        b = sup.bloqueios(agora=AGORA, capital_em_uso={AGENTE: Decimal("999.99")},
                          perda_hoje={AGENTE: Decimal("49.99")},
                          perda_total={AGENTE: Decimal("199.99")})
        assert b == {}

    @pytest.mark.parametrize(
        "campo,valor",
        [("capital_em_uso", Decimal("NaN")),
         ("capital_em_uso", Decimal("Infinity")),
         ("capital_em_uso", Decimal("-Infinity")),
         ("perda_hoje", Decimal("NaN")),
         ("perda_hoje", Decimal("Infinity")),
         ("perda_hoje", Decimal("-Infinity")),
         ("perda_total", Decimal("NaN")),
         ("perda_total", Decimal("Infinity")),
         ("perda_total", Decimal("-Infinity")),
         ("capital_em_uso", "telemetria-corrompida"),
         ("capital_em_uso", Decimal("-1")),
         ("perda_hoje", Decimal("-0.01")),
         ("perda_total", Decimal("-0.01"))],
    )
    def test_metrica_de_risco_invalida_bloqueia_sem_quebrar_o_vigia(
            self, campo, valor):
        sup, _, _ = self._autorizado()

        bloqueios = sup.bloqueios(
            agora=AGORA, **{campo: {AGENTE: valor}})

        assert "métrica de risco" in bloqueios[AGENTE]
        assert sup.de(AGENTE).estado is EstadoDoPiloto.PILOTO_AUTORIZADO

    @pytest.mark.parametrize(
        "alteracoes",
        [{"capital_maximo": "NaN"},
         {"capital_maximo": "0"},
         {"perda_diaria_maxima": "-1"},
         {"perda_total_maxima": "0"},
         {"perda_diaria_maxima": "300", "perda_total_maxima": "200"}],
    )
    def test_limite_persistido_invalido_suspende_piloto_fail_closed(
            self, alteracoes):
        sup, _, _ = self._autorizado()
        sup._autorizacoes[AGENTE].update(alteracoes)

        bloqueios = sup.bloqueios(agora=AGORA)

        assert "limites persistidos inválidos" in bloqueios[AGENTE]
        assert sup.de(AGENTE).estado is EstadoDoPiloto.SUSPENSO

    def test_prazo_vencido_expira(self):
        sup, _, _ = self._autorizado(prazo_dias=1)
        notas = sup.vigiar(agora=AGORA + timedelta(days=3))
        assert any("EXPIRADO" in n for n in notas)
        assert sup.de(AGENTE).estado is EstadoDoPiloto.EXPIRADO
        b = sup.bloqueios(agora=AGORA + timedelta(days=4))
        assert "expirada" in b[AGENTE]

    def test_escola_revoga_formatura_e_suspende(self):
        sup, esc, t2 = self._autorizado()
        _revogar_escola(esc, "trend_following", t2)
        notas = sup.vigiar(agora=AGORA + timedelta(days=10))
        assert any("SUSPENSO" in n for n in notas)
        a = sup.de(AGENTE)
        assert a.estado is EstadoDoPiloto.SUSPENSO
        assert "escola" in a.motivo_suspensao

    def test_vigia_sem_escola_suspende(self):
        sup, _, _ = self._autorizado()
        sup.escola = None
        notas = sup.vigiar(agora=AGORA + timedelta(days=1))
        assert any("escola indisponível" in n for n in notas)
        assert sup.de(AGENTE).estado is EstadoDoPiloto.SUSPENSO

    def test_revogar_exige_motivo_e_encerra(self):
        sup, _, _ = self._autorizado()
        with pytest.raises(ValueError, match="motivo"):
            sup.revogar(AGENTE, motivo="   ", agora=AGORA)
        a = sup.revogar(AGENTE, motivo="proprietário decidiu pausar",
                        agora=AGORA)
        assert a.estado is EstadoDoPiloto.REVOGADO
        assert a.revogada_em == AGORA.isoformat()
        assert "pausar" in a.motivo_revogacao
        assert "revogada" in sup.bloqueios(agora=AGORA)[AGENTE]
        with pytest.raises(ValueError, match="já encerrado"):
            sup.revogar(AGENTE, motivo="de novo", agora=AGORA)
        # encerrado, novo pedido volta a ser aceito (escola segue formada)
        nova = _solicitar(sup, agora=AGORA + timedelta(days=40))
        assert nova.estado is EstadoDoPiloto.AGUARDANDO_AUTORIZACAO


class TestPersistencia:
    def test_estado_sobrevive_reinicio(self):
        arm = ArmazemFake()
        esc, _ = _escola_formada()
        sup = SupervisorDePiloto(armazem=arm, escola=esc)
        _solicitar(sup)
        sup.autorizar(AGENTE, gates=_gates_ok(), agora=AGORA)
        sup2 = SupervisorDePiloto(armazem=arm, escola=esc)
        a = sup2.de(AGENTE)
        assert a is not None
        assert a.estado is EstadoDoPiloto.PILOTO_AUTORIZADO
        assert a.capital_maximo == Decimal("1000")
        assert sup2.bloqueios(agora=AGORA + timedelta(days=1)) == {}


class TestControleAleatorio:
    def test_optin_na_mesa(self):
        assert len(default_desk(UNIV)) == 10
        mesa = default_desk(UNIV, incluir_controle=True)
        assert len(mesa) == 11
        assert isinstance(mesa[-1], ControleAleatorio)
        assert mesa[-1].strategy == "controle_aleatorio"
        assert mesa[-1].homologado is False

    def test_versao_guardada_no_trader(self):
        t = TrendTrader("trader-trend", UNIV, version="7")
        assert t.versao == "7"
        assert t.spec.version == "7"

    def test_sorteio_deterministico(self):
        t = _trader_controle()
        concedido = _entrada_sorteada(t)
        assert concedido is not None
        # o corte achado é de fato um sorteio concedido (seed reproduzível)
        semente = f"BTC/USDT|{concedido.cutoff.isoformat()}"
        assert random.Random(semente).random() < CHANCE_ENTRADA_CONTROLE
        p1 = t.propose(concedido)
        p2 = t.propose(_snap(concedido.cutoff))
        assert p1.action == p2.action == Action.BUY
        assert p1.reason == p2.reason and "sorteada" in p1.reason
        negado = next(
            c for c in (BASE + timedelta(hours=i) for i in range(500))
            if random.Random(f"BTC/USDT|{c.isoformat()}").random()
            >= CHANCE_ENTRADA_CONTROLE)
        p3 = t.propose(_snap(negado))
        assert p3.action is Action.NO_TRADE
        assert "sem entrada neste sorteio" in p3.reason

    def test_imune_a_rebaixamento_e_ao_aprendizado(self):
        memoria = MemoriaDeDesempenho()
        for d in _desfechos("controle_aleatorio", _ruins(30)):
            memoria.registrar(d)
        rebaixar, _ = memoria.deve_rebaixar("controle_aleatorio")
        assert rebaixar  # o histórico condenaria qualquer outro...
        t = _trader_controle()
        t.memoria = memoria
        com_hist = _entrada_sorteada(t)
        assert com_hist is not None  # ...mas o controle ignora e segue sorteando
        p1 = t.propose(com_hist)
        assert "rebaixada" not in p1.reason
        p2 = _trader_controle().propose(com_hist)
        # aprendizado não toca o controle: decisão idêntica com e sem histórico
        assert p1.action == p2.action == Action.BUY
        assert p1.confidence == p2.confidence
        assert p1.desired_exposure_pct == p2.desired_exposure_pct

    def test_guard_de_rebaixamento_vale_para_trader_normal(self):
        memoria = MemoriaDeDesempenho()
        for d in _desfechos("trend_following", _ruins(30)):
            memoria.registrar(d)
        closes = tuple(Decimal(str(100 + 0.5 * i)) for i in range(60))
        t = _enable(TrendTrader("trader-trend", UNIV, memoria=memoria))
        p = t.propose(_snap(BASE, closes))
        assert p.action is Action.NO_TRADE
        assert "rebaixada" in p.reason
        sem_memoria = _enable(TrendTrader("trader-trend", UNIV))
        p2 = sem_memoria.propose(_snap(BASE, closes))
        assert "rebaixada" not in p2.reason


class TestVigiaNoCiclo:
    def _mandato(self):
        return DeskMandate(mandate_id="m", owner="p", account="a",
                           mode=Mode.PAPER, instruments=UNIV,
                           capital=Decimal("1000"))

    def _orch(self, t, vigia=None):
        m = self._mandato()
        return DeskOrchestrator(m, [t], worker_id="w1", risk=RiskEngine(m),
                                vigia=vigia)

    def test_vigia_corta_entrada_bloqueada(self):
        t = _trader_controle()
        snap = _entrada_sorteada(t)
        assert snap is not None
        # sem vigia: a entrada vira intenção
        rep = self._orch(t).run_cycle([snap], {snap.instrument: snap.last})
        assert len(rep.intents) == 1
        assert rep.intents[0].action is Action.BUY
        # com vigia bloqueando: nada passa, com motivo registrado
        t2 = _trader_controle()
        epoca = _entrada_sorteada(t2)
        rep2 = self._orch(
            t2, vigia=lambda: {"trader-controle": "perda diária atingiu"}
        ).run_cycle([epoca], {epoca.instrument: epoca.last})
        assert rep2.intents == []
        assert any("Maestro/piloto" in motivo
                   for _, _, motivo in rep2.abstentions)

    def test_vigia_quebrado_fail_closed(self):
        t = _trader_controle()
        snap = _entrada_sorteada(t)
        assert snap is not None

        def vigia_quebrado():
            raise RuntimeError("vigia caiu")

        rep = self._orch(t, vigia=vigia_quebrado).run_cycle(
            [snap], {snap.instrument: snap.last})
        assert rep.intents == []
        assert any("vigia" in e for e in rep.errors)
        assert any("fail-closed" in motivo for _, _, motivo in rep.abstentions)
