"""E5 — Evolução Autônoma de Estratégias (task #60).

Contrato testado:
- Memória auditável por trader: decisões, P&L líquido, custos, regimes — e
  classificação DETERMINÍSTICA da causa de cada perda (risco normal / erro de
  execução / dados inadequados / regra descalibrada / mudança de regime).
- Experimentos champion/challenger com parâmetros CONGELADOS (assinatura),
  walk-forward fora da amostra, correção de múltiplas hipóteses (Bonferroni)
  e benchmark; veredito expõe métricas de RENTABILIDADE (expectativa líquida,
  drawdown, estabilidade, custos) — nunca taxa de acerto.
- Promoção automática SÓ dentro de experimentos e PAPER; LIVE jamais.
- Rollback/suspensão por deterioração — ligado ao vigia (bloqueios).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.desk.evolucao import (
    ALPHA,
    CAUSA_DADOS_INADEQUADOS,
    CAUSA_ERRO_EXECUCAO,
    CAUSA_MUDANCA_REGIME,
    CAUSA_REGRA_DESCALIBRADA,
    CAUSA_RISCO_NORMAL,
    ContextoDaOperacao,
    EvolucaoAutonoma,
    Experimento,
    MetricasDoLado,
    classificar_causa,
)
from tradingagents.desk.laboratorio import Hipotese
from tradingagents.desk.learning import (
    AMOSTRA_MINIMA,
    Desfecho,
    MemoriaDeDesempenho,
)
from tradingagents.desk.models import (
    Action,
    AgentStage,
    DeskMandate,
    MarketSnapshot,
    Mode,
)
from tradingagents.desk.orchestrator import DeskOrchestrator
from tradingagents.desk.risk import RiskEngine
from tradingagents.desk.traders import ControleAleatorio


class ArmazemFake:
    def __init__(self):
        self.dados: dict = {}

    def carregar(self, tenant_id, chave):
        valor = self.dados.get((tenant_id, chave))
        return json.loads(json.dumps(valor)) if valor is not None else None

    def salvar(self, tenant_id, chave, payload):
        self.dados[(tenant_id, chave)] = json.loads(json.dumps(payload))


AGORA = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)


def _desfecho(agente: str, retorno: str, *, estrategia: str = "momentum",
              regime: str = "tendencia_alta", quando: datetime | None = None,
              versao: str = "1") -> Desfecho:
    return Desfecho(estrategia=estrategia, regime=regime,
                    instrumento="BTC/USDT", retorno=Decimal(retorno),
                    fechado_em=quando or AGORA, agente=agente, versao=versao)


def _serie_teste(n: int = 300) -> list[float]:
    """De lado na primeira parte; sobe firme na região de teste (+0,4%/barra,
    bem acima do custo de ida e volta ~0,32%)."""
    lado = [100.0, 99.7, 100.0, 100.3, 99.8]
    valores = []
    for i in range(n):
        if i < 180:
            valores.append(lado[i % 5])
        else:
            valores.append(round(valores[-1] * 1.004, 6))
    return valores


def _h_momente(**kw) -> Hipotese:
    base = {
        "id": "H-MOM", "titulo": "momentum simples", "familia": "momentum",
        "tipo_de_regra": "momentum",
        "parametros": (("janela", 5), ("limiar", 0.001)),
        "instrumentos": ("BTC/USDT",),
        "janela_treino_barras": 100, "janela_teste_barras": 120,
        "horizonte_barras": 2,
    }
    base.update(kw)
    return Hipotese(**base)


def _h_reversao() -> Hipotese:
    return Hipotese(id="H-REV", titulo="reversão z", familia="reversao",
                    tipo_de_regra="reversao_z",
                    parametros=(("janela", 10), ("z_entrada", 1.0)),
                    instrumentos=("BTC/USDT",),
                    janela_treino_barras=100, janela_teste_barras=120,
                    horizonte_barras=2)


def _exp(**kw) -> Experimento:
    base = {
        "id": "X-001", "trader": "trader-momentum",
        "champion": _h_momente(parametros=(("janela", 10), ("limiar", 0.05))),
        "challenger": _h_momente(),
    }
    base.update(kw)
    return Experimento(**base)


class TestClassificacaoDePerda:
    def test_ganho_nao_tem_causa_de_perda(self):
        causa, motivo = classificar_causa(_desfecho("a", "0.02"))
        assert causa == ""
        assert "ganho" in motivo

    def test_desvio_de_execucao_vira_erro_de_execucao(self):
        causa, motivo = classificar_causa(
            _desfecho("a", "-0.01"),
            contexto=ContextoDaOperacao(desvio_de_execucao=0.005))
        assert causa == CAUSA_ERRO_EXECUCAO
        assert "execução" in motivo

    def test_dados_atrasados_vira_dados_inadequados(self):
        causa, _ = classificar_causa(
            _desfecho("a", "-0.01"),
            contexto=ContextoDaOperacao(dados_atrasados=True))
        assert causa == CAUSA_DADOS_INADEQUADOS

    def test_regime_diferente_na_saida_vira_mudanca_de_regime(self):
        causa, motivo = classificar_causa(
            _desfecho("a", "-0.01"),
            contexto=ContextoDaOperacao(regime_na_saida="queda"))
        assert causa == CAUSA_MUDANCA_REGIME
        assert "tendencia_alta" in motivo

    def test_regra_madura_negativa_vira_regra_descalibrada(self):
        mem = MemoriaDeDesempenho()
        for _i in range(AMOSTRA_MINIMA):
            # Carimbo distinto por operação: registros idênticos são a
            # MESMA operação e a porta da evidência os recusa.
            mem.registrar(_desfecho("a", "-0.01",
                                    quando=AGORA + timedelta(seconds=_i)))
        causa, _ = classificar_causa(_desfecho("a", "-0.01"), memoria=mem)
        assert causa == CAUSA_REGRA_DESCALIBRADA

    def test_sem_sinais_vira_risco_normal(self):
        causa, motivo = classificar_causa(_desfecho("a", "-0.01"))
        assert causa == CAUSA_RISCO_NORMAL
        assert "risco" in motivo

    def test_execucao_tem_prioridade_sobre_demais_sinais(self):
        causa, _ = classificar_causa(
            _desfecho("a", "-0.01"),
            contexto=ContextoDaOperacao(desvio_de_execucao=0.01,
                                        dados_atrasados=True))
        assert causa == CAUSA_ERRO_EXECUCAO


class TestDiarioAuditavel:
    def test_registra_decisao_pnl_liquido_e_custos(self):
        evo = EvolucaoAutonoma()
        r = evo.registrar_operacao(_desfecho("trader-a", "-0.01"),
                                   decisao="entrou no dip",
                                   custo=Decimal("0.003"))
        assert r.decisao == "entrou no dip"
        assert r.retorno_liquido == Decimal("-0.01")
        assert r.custo == Decimal("0.003")
        assert r.causa == CAUSA_RISCO_NORMAL

    def test_desempenho_agrega_por_trader(self):
        evo = EvolucaoAutonoma()
        for retorno in ("0.02", "-0.01", "0.04"):
            evo.registrar_operacao(_desfecho("trader-a", retorno),
                                   custo=Decimal("0.003"))
        d = evo.desempenho("trader-a")
        assert d["operacoes"] == 3
        assert d["expectativa_liquida"] == pytest.approx((0.02 - 0.01 + 0.04) / 3,
                                                         abs=1e-6)
        assert d["custos_total"] == pytest.approx(0.003 * 3)
        assert d["causas"][CAUSA_RISCO_NORMAL] == 1
        assert d["regimes"]["tendencia_alta"] == 3
        # memória de um trader não vaza para outro
        assert evo.desempenho("trader-b")["operacoes"] == 0

    def test_diario_sobrevive_reconstrucao(self):
        arm = ArmazemFake()
        evo = EvolucaoAutonoma(armazem=arm, tenant_id="t1")
        evo.registrar_operacao(_desfecho("trader-a", "-0.01"))
        evo.definir_base("trader-a", _h_momente())
        revivida = EvolucaoAutonoma(armazem=arm, tenant_id="t1")
        assert revivida.desempenho("trader-a")["operacoes"] == 1
        assert revivida.diario("trader-a")[0]["causa"] == CAUSA_RISCO_NORMAL
        assert (revivida.versao_vigente("trader-a")["assinatura"]
                == _h_momente().assinatura())


class TestExperimentoChampionChallenger:
    def test_assinatura_estavel_e_sensivel(self):
        assert _exp().assinatura() == _exp().assinatura()
        outro = _exp(challenger=_h_reversao())
        assert outro.assinatura() != _exp().assinatura()

    def test_champion_igual_challenger_recusado(self):
        evo = EvolucaoAutonoma()
        with pytest.raises(ValueError, match="idênticos"):
            evo.registrar_experimento(_exp(challenger=_h_momente(
                parametros=(("janela", 10), ("limiar", 0.05)))))

    def test_executar_sem_registro_recusa(self):
        evo = EvolucaoAutonoma()
        with pytest.raises(ValueError, match="não pré-registrado"):
            evo.executar_experimento(_exp(), {"BTC/USDT": _serie_teste()})

    def test_execucao_unica_e_forcar(self):
        evo = EvolucaoAutonoma()
        exp = _exp()
        evo.registrar_experimento(exp)
        evo.executar_experimento(exp, {"BTC/USDT": _serie_teste()})
        with pytest.raises(RuntimeError, match="já executado"):
            evo.executar_experimento(exp, {"BTC/USDT": _serie_teste()})
        forcar = evo.executar_experimento(exp, {"BTC/USDT": _serie_teste()},
                                          forcar=True)
        assert forcar.executada_em

    def test_challenger_melhor_e_aprovado_com_benchmark(self):
        evo = EvolucaoAutonoma()
        exp = _exp()
        evo.registrar_experimento(exp)
        v = evo.executar_experimento(exp, {"BTC/USDT": _serie_teste()})
        assert v.aprovado
        assert v.p_valor < v.alpha_ajustado
        assert v.challenger.expectativa_liquida > 0
        assert v.challenger.estabilidade == 1.0
        assert v.challenger.drawdown == 0.0
        assert v.challenger.custos > 0
        # champion com limiar alto demais não entra — e não vence por omissão
        assert v.champion.operacoes == 0
        # benchmark (comprado e segurado na janela) é medido e reportado
        assert v.benchmark.expectativa_liquida > 0
        assert "supera" in v.motivo

    def test_challenger_equivalente_nao_aprova(self):
        evo = EvolucaoAutonoma()
        exp = _exp(champion=_h_momente(parametros=(("janela", 6),
                                                   ("limiar", 0.0012))),
                   challenger=_h_momente())
        evo.registrar_experimento(exp)
        v = evo.executar_experimento(exp, {"BTC/USDT": _serie_teste()})
        assert not v.aprovado
        assert "não supera" in v.motivo

    def test_sem_operacoes_suficientes_nao_aprova(self):
        # toda a série é de lado: nenhum momentum dispara e o último pump
        # (fora da janela de entrada) NÃO pode ser capturado — sem look-ahead
        lado = [100.0, 99.7, 100.0, 100.3, 99.8]
        serie = [lado[i % 5] for i in range(300)]
        serie[-1] = 130.0
        evo = EvolucaoAutonoma()
        exp = _exp(champion=_h_momente(),
                   challenger=_h_momente(parametros=(("janela", 5),
                                                     ("limiar", 0.0001))))
        evo.registrar_experimento(exp)
        v = evo.executar_experimento(exp, {"BTC/USDT": serie})
        assert not v.aprovado
        assert v.challenger.operacoes == 0
        assert v.champion.operacoes == 0
        assert "sem operações" in v.motivo

    def test_bonferroni_encolhe_alpha_por_familia(self):
        evo = EvolucaoAutonoma()
        for k in range(3):
            evo.registrar_experimento(_exp(id=f"X-00{k + 1}"))
        v = evo.executar_experimento(_exp(id="X-001"),
                                     {"BTC/USDT": _serie_teste()})
        assert v.familia_n == 3
        assert v.alpha_ajustado == pytest.approx(ALPHA / 3)

    def test_metricas_sao_de_rentabilidade_sem_taxa_de_acerto(self):
        campos = set(MetricasDoLado.__dataclass_fields__)
        assert campos == {"operacoes", "expectativa_liquida", "drawdown",
                          "estabilidade", "custos"}
        assert not any("acerto" in c for c in campos)

    def test_veredito_e_persistido(self):
        arm = ArmazemFake()
        evo = EvolucaoAutonoma(armazem=arm, tenant_id="t1")
        exp = _exp()
        evo.registrar_experimento(exp)
        evo.executar_experimento(exp, {"BTC/USDT": _serie_teste()})
        revivida = EvolucaoAutonoma(armazem=arm, tenant_id="t1")
        rel = revivida.relatorio()
        assert rel["experimentos"][0]["executada"] is True
        assert rel["experimentos"][0]["resultado"]["aprovado"] is True
        assert "o_que_mudou" in rel["experimentos"][0]


class TestPromocaoERollback:
    def _evo_com_aprovado(self, arm=None):
        evo = EvolucaoAutonoma(armazem=arm, tenant_id="t1")
        exp = _exp()
        evo.registrar_experimento(exp)
        evo.executar_experimento(exp, {"BTC/USDT": _serie_teste()})
        return evo, exp

    def test_promocao_recusada_em_live(self):
        evo, exp = self._evo_com_aprovado()
        with pytest.raises(ValueError, match="PAPER"):
            evo.promover(exp, modo="live")

    def test_promocao_exige_veredito_aprovado(self):
        evo = EvolucaoAutonoma()
        exp = _exp(champion=_h_momente(parametros=(("janela", 6),
                                                   ("limiar", 0.0012))),
                   challenger=_h_momente())
        evo.registrar_experimento(exp)
        evo.executar_experimento(exp, {"BTC/USDT": _serie_teste()})
        with pytest.raises(ValueError, match="não aprovado"):
            evo.promover(exp, modo="paper")

    def test_promocao_em_paper_atualiza_versao_e_e_idempotente(self):
        evo, exp = self._evo_com_aprovado()
        evo.definir_base("trader-momentum", _h_momente(
            parametros=(("janela", 10), ("limiar", 0.05))))
        p1 = evo.promover(exp, modo="paper")
        assert p1["assinatura"] == _h_momente().assinatura()
        vigente = evo.versao_vigente("trader-momentum")
        assert vigente["assinatura"] == _h_momente().assinatura()
        assert vigente["anterior"] == _h_momente(
            parametros=(("janela", 10), ("limiar", 0.05))).assinatura()
        evo.promover(exp, modo="paper")  # idempotente: não duplica evento
        promocoes = [e for e in evo.eventos() if e["tipo"] == "promocao"]
        assert len(promocoes) == 1

    def test_ciclo_autopromove_so_aprovado_em_paper(self):
        evo, aprovado = self._evo_com_aprovado()
        recusado = _exp(id="X-002",
                        champion=_h_momente(parametros=(("janela", 6),
                                                        ("limiar", 0.0012))),
                        challenger=_h_momente())
        evo.registrar_experimento(recusado)
        evo.executar_experimento(recusado, {"BTC/USDT": _serie_teste()})
        resumo = evo.ciclo()
        assert len(resumo["promocao"]) == 1
        assert (evo.versao_vigente("trader-momentum")["assinatura"]
                == _h_momente().assinatura())
        # segundo ciclo: nada novo promovido (idempotente)
        assert evo.ciclo()["promocao"] == []

    def test_rollback_por_deterioracao_com_versao_anterior(self):
        arm = ArmazemFake()
        evo, exp = self._evo_com_aprovado(arm=arm)
        base = _h_momente(parametros=(("janela", 10), ("limiar", 0.05)))
        evo.definir_base("trader-momentum", base)
        evo.promover(exp, modo="paper")
        for _ in range(AMOSTRA_MINIMA + 2):
            evo.registrar_operacao(_desfecho("trader-momentum", "-0.01",
                                             versao="2"))
        eventos = evo.vigiar()
        assert any(e["tipo"] == "rollback" for e in eventos)
        assert "trader-momentum" not in evo.bloqueios()
        assert evo.versao_vigente("trader-momentum")["assinatura"] == base.assinatura()

    def test_suspensao_sem_versao_anterior_bloqueia_e_alimenta_vigia(self):
        evo = EvolucaoAutonoma()
        evo.definir_base("trader-scalping", _h_momente())
        for _ in range(AMOSTRA_MINIMA + 2):
            evo.registrar_operacao(_desfecho("trader-scalping", "-0.02"))
        eventos = evo.vigiar()
        assert any(e["tipo"] == "suspensao" for e in eventos)
        bloqueios = evo.bloqueios()
        assert "trader-scalping" in bloqueios
        assert "expectativa líquida" in bloqueios["trader-scalping"]
        # vigia idempotente: segunda passada não duplica evento
        assert evo.vigiar() == []
        suspensoes = [e for e in evo.eventos() if e["tipo"] == "suspensao"]
        assert len(suspensoes) == 1

    def test_amostra_insuficiente_nao_suspende(self):
        evo = EvolucaoAutonoma()
        for _ in range(AMOSTRA_MINIMA - 1):
            evo.registrar_operacao(_desfecho("trader-a", "-0.02"))
        assert evo.vigiar() == []
        assert evo.bloqueios() == {}

    def test_expectativa_positiva_nao_suspende(self):
        evo = EvolucaoAutonoma()
        for _ in range(AMOSTRA_MINIMA + 2):
            evo.registrar_operacao(_desfecho("trader-a", "0.01"))
        assert evo.vigiar() == []
        assert evo.bloqueios() == {}

    def test_reabilitar_exige_motivo_e_esta_na_evidencia(self):
        evo = EvolucaoAutonoma()
        for _ in range(AMOSTRA_MINIMA + 2):
            evo.registrar_operacao(_desfecho("trader-a", "-0.02"))
        evo.vigiar()
        with pytest.raises(ValueError, match="motivo"):
            evo.reabilitar("trader-a", motivo="")
        evo.reabilitar("trader-a", motivo="parâmetros corrigidos por experimento X-9")
        assert evo.bloqueios() == {}
        reabilitacoes = [e for e in evo.eventos() if e["tipo"] == "reabilitacao"]
        assert reabilitacoes[0]["motivo"].startswith("parâmetros corrigidos")

    def test_suspensao_sobrevive_reconstrucao(self):
        arm = ArmazemFake()
        evo = EvolucaoAutonoma(armazem=arm, tenant_id="t1")
        for _ in range(AMOSTRA_MINIMA + 2):
            evo.registrar_operacao(_desfecho("trader-a", "-0.02"))
        evo.vigiar()
        revivida = EvolucaoAutonoma(armazem=arm, tenant_id="t1")
        assert "trader-a" in revivida.bloqueios()

    def test_relatorio_traz_traders_e_experimentos_com_o_que_mudou(self):
        evo, exp = self._evo_com_aprovado()
        rel = evo.relatorio()
        assert rel["traders"] == []
        item = rel["experimentos"][0]
        assert item["id"] == "X-001"
        assert item["o_que_mudou"]["janela"] == {"de": 10, "para": 5}
        assert item["resultado"]["aprovado"] is True
        assert "aviso" in item


# -- fiação no orquestrador (integração E5) -------------------------------
BASE_INT = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
UNIV_INT = ("BTC/USDT",)


def _saw(n: int = 80) -> tuple[Decimal, ...]:
    base = [100, 99.5, 99, 98.5, 98, 98.5, 99, 99.5]
    return tuple(Decimal(str(base[i % 8])) for i in range(n))


def _snap_controle(indice: int) -> MarketSnapshot:
    return MarketSnapshot(instrument="BTC/USDT", timeframe="1h",
                          cutoff=BASE_INT + timedelta(hours=indice),
                          closes=_saw())


def _trader_controle() -> ControleAleatorio:
    t = ControleAleatorio("trader-controle", UNIV_INT)
    for st in (AgentStage.VALIDATED, AgentStage.SHADOW,
               AgentStage.PAPER_ENABLED):
        t.spec.move_to(st)
    return t


def _entrada_sorteada(t, n: int = 2000) -> MarketSnapshot | None:
    """Cutoff determinístico em que o sorteio do controle vira BUY."""
    for i in range(n):
        snap = _snap_controle(i)
        if t.propose(snap).action is Action.BUY:
            return snap
    return None


class TestIntegracaoComOrquestrador:
    """Fiação E5: o fechamento de posição alimenta o diário na MESMA fonte do
    aprendizado (falha vira aviso, nunca derruba a saída) e a deterioração
    chega ao ciclo como bloqueio de entrada — com reabilitação auditável
    devolvendo o trader ao jogo."""

    def _mandato(self) -> DeskMandate:
        return DeskMandate(mandate_id="m", owner="p", account="a",
                           mode=Mode.PAPER, instruments=UNIV_INT,
                           capital=Decimal("1000"))

    def _orch(self, t, *, evolucao=None, vigia=None, memoria=None):
        m = self._mandato()
        return DeskOrchestrator(m, [t], worker_id="w1", risk=RiskEngine(m),
                                memoria=memoria, vigia=vigia,
                                evolucao=evolucao)

    def test_fechamento_alimenta_o_diario_com_retorno_liquido(self):
        t = _trader_controle()
        evo = EvolucaoAutonoma()
        orch = self._orch(t, evolucao=evo, memoria=MemoriaDeDesempenho())
        orch.register_fill("BTC/USDT", Decimal("1"), Decimal("100"),
                           "trader-controle", Decimal("100"))
        fechada = orch.close_position("BTC/USDT", preco_saida=Decimal("110"))
        assert fechada is not None
        entradas = evo.diario("trader-controle")
        assert len(entradas) == 1
        registro = entradas[0]
        # bruto +10% menos custo ida-e-volta (0,3% em BTC/USDT na binance)
        assert Decimal(registro["retorno_liquido"]) == Decimal("0.097")
        assert Decimal(registro["custo"]) == Decimal("0.003")
        assert registro["estrategia"] == "controle_aleatorio"
        assert registro["causa"] == ""            # ganho não é perda
        assert "encerrou BTC/USDT" in registro["decisao"]
        assert orch.avisos_de_evolucao == []

    def test_falha_da_evolucao_vira_aviso_sem_derrubar_a_saida(self):
        class EvolucaoQuebrada:
            def registrar_operacao(self, *args, **kwargs):
                raise RuntimeError("disco cheio")

        t = _trader_controle()
        orch = self._orch(t, evolucao=EvolucaoQuebrada(),
                          memoria=MemoriaDeDesempenho())
        for _i in range(12):
            orch.register_fill("BTC/USDT", Decimal("1"), Decimal("100"),
                               "trader-controle", Decimal("100"))
            # Preço distinto por rodada: evidências idênticas seriam a
            # MESMA operação e a porta as recusaria (o teste aqui é outro:
            # o diário da evolução quebrado não derruba a saída).
            assert orch.close_position(
                "BTC/USDT",
                preco_saida=Decimal("110") + Decimal(_i)) is not None
        assert len(orch.avisos_de_evolucao) == 10   # lista limitada
        assert "disco cheio" in orch.avisos_de_evolucao[-1]

    def test_deterioracao_bloqueia_entradas_no_ciclo(self):
        evo = EvolucaoAutonoma()
        vigia = evo.bloqueios
        t = _trader_controle()
        snap = _entrada_sorteada(t)
        assert snap is not None
        rep = self._orch(t, evolucao=evo, vigia=vigia,
                         memoria=MemoriaDeDesempenho()).run_cycle(
            [snap], {snap.instrument: snap.last})
        assert len(rep.intents) == 1                # sem deterioração: passa
        for _ in range(AMOSTRA_MINIMA + 2):
            evo.registrar_operacao(_desfecho("trader-controle", "-0.02"))
        assert evo.vigiar()                         # suspensão registrada
        t2 = _trader_controle()
        snap2 = _entrada_sorteada(t2)
        rep2 = self._orch(t2, evolucao=evo, vigia=vigia,
                          memoria=MemoriaDeDesempenho()).run_cycle(
            [snap2], {snap2.instrument: snap2.last})
        assert rep2.intents == []
        assert any("Maestro/piloto" in motivo and "deterioração" in motivo
                   for _, _, motivo in rep2.abstentions)
        evo.reabilitar("trader-controle", motivo="parâmetros corrigidos")
        t3 = _trader_controle()
        snap3 = _entrada_sorteada(t3)
        rep3 = self._orch(t3, evolucao=evo, vigia=vigia,
                          memoria=MemoriaDeDesempenho()).run_cycle(
            [snap3], {snap3.instrument: snap3.last})
        assert len(rep3.intents) == 1               # reabilitado volta a operar
