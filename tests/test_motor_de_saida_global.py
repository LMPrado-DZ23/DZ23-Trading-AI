"""Motor de Saída GLOBAL (M3/M4): política versionada por posição, decisão
determinística HOLD/REDUCE/CLOSE/NO_ACTION, execução autônoma no ciclo,
persistência do livro de gestão e recuperação após reinício.

O que estes testes protegem:
- Stop nunca fica mais largo (afrouxamento é recusado; aperto é aceito e
  auditado).
- Lucro bruto positivo NÃO é sinal obrigatório de venda.
- Saída nunca excede o lote (oversell impossível por construção).
- Saída não duplica quando já há ordem de venda em curso.
- Reinício restaura a gestão (política, topo, parcial) — e o financeiro manda.
"""
from __future__ import annotations

from decimal import Decimal

from tradingagents.desk.execution import ExecutionCoordinator, OrderState, PaperBroker
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import AgentStage, DeskMandate, MarketSnapshot, Mode, utcnow
from tradingagents.desk.orchestrator import DeskOrchestrator, Position
from tradingagents.desk.risk import RiskEngine
from tradingagents.desk.saida import (
    PoliticaDeSaida,
    aplicar_ajuste,
    avaliar_posicao,
    politica_por,
)
from tradingagents.desk.scheduler import DeskScheduler
from tradingagents.desk.traders import default_desk

UNIV = ("BTC/USDT",)


def enable(ts):
    for t in ts:
        for st in (AgentStage.VALIDATED, AgentStage.SHADOW, AgentStage.PAPER_ENABLED):
            t.spec.move_to(st)
    return ts


def snap(closes, instrument="BTC/USDT"):
    return MarketSnapshot(instrument, "1h", utcnow(),
                          tuple(Decimal(str(c)) for c in closes))


def uptrend(n=40):
    return [100 + i * 1.5 for i in range(n)]


def queda(final=60, n=40):
    serie = [100 + i * 1.5 for i in range(n - 1)]
    serie.append(final)
    return serie


def pol(alvo="0.02", stop="-0.01", max_velas=48, versao="1",
        trailing=None, parcial=None) -> PoliticaDeSaida:
    return PoliticaDeSaida(
        politica_id="trend", versao=versao, horizonte="1h",
        alvo=Decimal(alvo), stop=Decimal(stop), max_velas=max_velas,
        trailing_pct=Decimal(trailing) if trailing is not None else None,
        parcial_no_alvo=Decimal(parcial) if parcial is not None else None)


def posicao(entry="100", qtd="1", politica=None, high_water=None,
            parcial_tomada=False) -> Position:
    return Position("BTC/USDT", Decimal(qtd), Decimal(entry), "trader-trend",
                    Decimal(entry) * Decimal(qtd), politica=politica,
                    high_water=Decimal(high_water) if high_water else None,
                    parcial_tomada=parcial_tomada)


class TestDecisaoDeterministica:
    def test_stop_exige_fechamento_com_justificativa(self):
        p = posicao(politica=pol(alvo="0.04", stop="-0.02"))
        d = avaliar_posicao(p, Decimal("98"), custo_ida_e_volta=Decimal("0.002"))
        assert d.acao == "CLOSE" and d.reason_code == "STOP_CONDITION"
        assert "stop" in d.motivo and d.urgencia == "alta"
        assert d.fracao == Decimal("1")
        assert d.lucro_bruto < 0 and d.lucro_liquido < d.lucro_bruto

    def test_alvo_fecha_posicao(self):
        p = posicao(politica=pol(alvo="0.02"))
        d = avaliar_posicao(p, Decimal("103"))
        assert d.acao == "CLOSE" and d.reason_code == "TARGET"
        assert "alvo" in d.motivo and d.urgencia == "alta"

    def test_parcial_no_alvo_reduz_uma_vez(self):
        p = posicao(politica=pol(alvo="0.02", parcial="0.5"))
        d = avaliar_posicao(p, Decimal("103"))
        assert d.acao == "REDUCE" and d.reason_code == "PARTIAL_TARGET"
        assert d.fracao == Decimal("0.5")
        p.parcial_tomada = True
        d2 = avaliar_posicao(p, Decimal("103"))
        assert d2.acao == "CLOSE" and d2.reason_code == "TARGET"

    def test_lucro_positivo_nao_vira_venda_obrigatoria(self):
        """Bruto positivo e até LÍQUIDO negativo: a política manda, não o
        otimismo do painel."""
        p = posicao(politica=pol(alvo="0.02"))
        d = avaliar_posicao(p, Decimal("100.5"),
                            custo_ida_e_volta=Decimal("0.006"))
        assert d.acao == "HOLD" and d.reason_code == "HOLD"
        assert d.lucro_bruto > 0 and d.lucro_liquido < 0

    def test_trailing_protege_lucro_apos_recuo(self):
        p = posicao(politica=pol(alvo="0.5", stop="-0.05", trailing="0.01"),
                    high_water="130")
        d = avaliar_posicao(p, Decimal("128"))
        assert d.acao == "CLOSE" and d.reason_code == "TRAILING"
        # topo 130, recuo tolerado 1% (128,70): 128 aciona
        assert "trailing" in d.motivo

    def test_trailing_nao_dispara_no_topo(self):
        p = posicao(politica=pol(alvo="0.5", stop="-0.05", trailing="0.01"),
                    high_water="130")
        d = avaliar_posicao(p, Decimal("129"))
        assert d.acao == "HOLD"

    def test_tempo_fecha_posicao_parada(self):
        p = posicao(politica=pol(max_velas=48))
        d = avaliar_posicao(p, Decimal("100.1"), velas=48)
        assert d.acao == "CLOSE" and d.reason_code == "TIME_EXIT"

    def test_sem_preco_nao_decide_as_cegas(self):
        p = posicao(politica=pol())
        d = avaliar_posicao(p, None)
        assert d.acao == "NO_ACTION" and d.reason_code == "DATA_UNAVAILABLE"
        assert "sem preço" in d.motivo

    def test_saida_em_curso_nao_duplica(self):
        p = posicao(politica=pol(alvo="0.04", stop="-0.02"))
        d = avaliar_posicao(p, Decimal("98"), saida_em_curso=True)
        assert d.acao == "HOLD" and d.reason_code == "EXIT_IN_PROGRESS"


class TestPoliticaImutavel:
    def test_afrouxar_stop_e_recusado_aperto_e_aceito(self):
        p = posicao(politica=pol(alvo="0.04", stop="-0.02"))
        from tradingagents.desk.saida import afrouxamento
        assert afrouxamento(p.politica, pol(alvo="0.04", stop="-0.03", versao="2"))  # mais largo
        assert not afrouxamento(p.politica, pol(alvo="0.04", stop="-0.01", versao="2"))  # aperto
        try:
            aplicar_ajuste(p, pol(alvo="0.04", stop="-0.03", versao="2"))
            raise AssertionError("afrouxamento deveria ser recusado")
        except ValueError as exc:
            assert "afroux" in str(exc)
        chave = aplicar_ajuste(p, pol(alvo="0.04", stop="-0.01", versao="2"))
        assert p.politica.stop == Decimal("-0.01")
        assert chave.endswith("@2")
        assert p.historico_politica and p.historico_politica[-1]["para"].endswith("@2")

    def test_alvo_mais_distante_e_trailing_removido_tambem_afrouxam(self):
        from tradingagents.desk.saida import afrouxamento
        base = pol(alvo="0.02", trailing="0.01")
        assert afrouxamento(base, pol(alvo="0.05", trailing="0.01", versao="2"))
        assert afrouxamento(base, pol(alvo="0.02", versao="2"))  # trailing sumiu

    def test_trailing_mais_largo_tambem_afrouxa(self):
        """Catch a silent widening of the trailing stop (1% → 10%).

        Um trailing maior deixa o preço recuar mais antes de proteger o lucro
        — é afrouxamento da proteção. Antes só a REMOÇÃO do trailing era
        detectada; aumentar o percentual passava batido e enfraquecia a saída
        sem recusa.
        """
        from tradingagents.desk.saida import afrouxamento
        base = pol(alvo="0.02", trailing="0.01")
        motivo = afrouxamento(base, pol(alvo="0.02", trailing="0.10", versao="2"))
        assert motivo, "trailing mais largo deveria ser recusado como afrouxamento"
        assert "trailing" in motivo
        p = posicao(politica=base)
        try:
            aplicar_ajuste(p, pol(alvo="0.02", trailing="0.10", versao="2"))
            raise AssertionError("afrouxamento de trailing deveria ser recusado")
        except ValueError as exc:
            assert "afroux" in str(exc)
        # apertar o trailing continua permitido
        assert not afrouxamento(base, pol(alvo="0.02", trailing="0.005", versao="2"))

    def test_politica_por_trader_carrega_versao_e_horizonte(self):
        t = enable([default_desk(UNIV)[0]])[0]
        p = politica_por(t)
        assert p.politica_id == t.strategy and p.versao == t.versao
        assert p.alvo == t.exit_policy.alvo and p.stop == t.exit_policy.stop
        assert p.horizonte == t.timeframe


def desk_completo(price="100"):
    m = DeskMandate(mandate_id="m", owner="p", account="a", mode=Mode.PAPER,
                    instruments=UNIV, capital=Decimal("1000"))
    risk = RiskEngine(m)
    led = Ledger(opening_cash=m.capital)
    coord = ExecutionCoordinator(m, led, risk, worker_id="w1",
                                 broker=PaperBroker(Decimal(price)))
    orch = DeskOrchestrator(m, enable(default_desk(UNIV)), worker_id="w1",
                            risk=risk,
                            positions_provider=lambda: coord.ledger.open_positions())
    return m, risk, led, coord, orch


class TestCicloAutonomoDeSaida:
    def _entrar(self, coord, orch):
        rep = orch.run_cycle([snap(uptrend())], {"BTC/USDT": Decimal("100")})
        assert rep.intents
        rec = coord.submit(rep.intents[0])
        agent = rec.intent.reservation.agent_id
        orch.register_fill("BTC/USDT", rec.filled_qty, rec.avg_price, agent,
                           rec.filled_qty * rec.avg_price)
        return agent, rec.filled_qty

    def test_ciclo_executa_a_saida_e_nao_repete(self, tmp_path):
        m, risk, led, coord, orch = desk_completo()
        agent, lot = self._entrar(coord, orch)
        pos = orch.book["BTC/USDT"]
        dono = next(t for t in orch.traders if t.spec.agent_id == agent)
        assert pos.politica is not None
        assert pos.politica.chave == politica_por(dono).chave   # política DO dono

        coord.broker.price = Decimal("60")     # mercado desabou: stop aciona
        sched = DeskScheduler(orch, coord,
                              snapshot_provider=lambda: [snap(queda(60))],
                              price_provider=lambda: {"BTC/USDT": Decimal("60")},
                              state_path=tmp_path / "state.json")
        # A semente do ciclo é o preço do broker (o snapshot orienta; quem
        # executa é o PaperBroker).
        out = sched.run_once()
        assert out["encerradas"], out
        e = out["encerradas"][0]
        assert e["estado"] == "filled" and e["reason_code"] == "STOP_CONDITION"
        assert led.position_quantity("BTC/USDT") == 0
        assert "BTC/USDT" not in orch.book
        # Ciclo seguinte: nada a vender, nenhuma venda nova.
        out2 = sched.run_once()
        assert not out2.get("encerradas")
        vendas = [r for r in coord.orders.values() if r.lado == "sell"]
        assert len(vendas) == 1

    def test_saida_nunca_excede_o_lote(self, tmp_path):
        m, risk, led, coord, orch = desk_completo()
        agent, lot = self._entrar(coord, orch)
        orch.book["BTC/USDT"].quantity = lot + Decimal("5")   # diário inflado
        coord.broker.price = Decimal("60")
        sched = DeskScheduler(orch, coord,
                              snapshot_provider=lambda: [snap(queda(60))],
                              price_provider=lambda: {"BTC/USDT": Decimal("60")},
                              state_path=tmp_path / "state.json")
        out = sched.run_once()
        assert out["encerradas"]
        venda = [r for r in coord.orders.values() if r.lado == "sell"][0]
        assert venda.quantity == lot                          # nunca mais que o lote
        assert led.position_quantity("BTC/USDT") == 0

    def test_ordem_de_saida_em_curso_bloqueia_segunda(self, tmp_path):
        m, risk, led, coord, orch = desk_completo()
        agent, lot = self._entrar(coord, orch)
        pendente = coord._montar_registro(
            order_id="ord-sell-pend", state=OrderState.SUBMITTING,
            instrument="BTC/USDT", side="sell", quantity=lot,
            client_order_id="coid-sell-pend", agent_id=agent)
        coord.orders[pendente.order_id] = pendente
        coord.broker.price = Decimal("60")
        sched = DeskScheduler(orch, coord,
                              snapshot_provider=lambda: [snap(queda(60))],
                              price_provider=lambda: {"BTC/USDT": Decimal("60")},
                              state_path=tmp_path / "state.json")
        out = sched.run_once()
        assert out["encerradas"] and out["encerradas"][0]["estado"] == "em_curso"
        vendas = [r for r in coord.orders.values() if r.lado == "sell"]
        assert len(vendas) == 1                               # a pendente, e só

    def test_livro_persiste_no_armazem(self, tmp_path):
        from tradingagents.desk.storage import ArmazemDeEstado
        m, risk, led, coord, orch = desk_completo()
        arm = ArmazemDeEstado(None, diretorio=tmp_path / "desks")
        sched = DeskScheduler(orch, coord,
                              snapshot_provider=lambda: [snap(uptrend())],
                              price_provider=lambda: {"BTC/USDT": Decimal("100")},
                              state_path=tmp_path / "state.json",
                              armazem=arm, tenant_id="t1")
        sched.run_once()
        doc = arm.carregar("t1", "livro")
        assert doc and doc["schema"] == "dz23.livro/1"
        assert "BTC/USDT" in doc["posicoes"]
        assert doc["posicoes"]["BTC/USDT"]["politica"]


class TestReinicioDoGerente:
    def _entrar_com_estado(self):
        m, risk, led, coord, orch = desk_completo()
        rep = orch.run_cycle([snap(uptrend())], {"BTC/USDT": Decimal("100")})
        rec = coord.submit(rep.intents[0])
        agent = rec.intent.reservation.agent_id
        orch.register_fill("BTC/USDT", rec.filled_qty, rec.avg_price, agent,
                           rec.filled_qty * rec.avg_price)
        orch.book["BTC/USDT"].high_water = Decimal("115")
        return coord, orch, agent

    def test_reinicio_restaura_politica_e_topo(self):
        coord_a, orch_a, agent = self._entrar_com_estado()
        livro = orch_a.livro_doc()
        # "reinício": novo processo restaura o OMS e depois o livro de gestão
        m2, risk2, led2, coord_b, orch_b = desk_completo()
        coord_b.restaurar_estado(coord_a.snapshot_estado())
        res = orch_b.restaurar_livro(livro, ledger=coord_b.ledger)
        assert res["restaurado"] and "BTC/USDT" in orch_b.book
        pos = orch_b.book["BTC/USDT"]
        dono = next(t for t in orch_b.traders if t.spec.agent_id == agent)
        assert pos.politica is not None
        assert pos.politica.chave == politica_por(dono).chave
        assert pos.high_water == Decimal("115")
        assert pos.agent_id == agent

    def test_livro_sem_lastro_financeiro_e_descartado(self):
        coord_a, orch_a, _ = self._entrar_com_estado()
        livro = orch_a.livro_doc()
        m2, risk2, led2, coord_b, orch_b = desk_completo()
        # SEM restaurar o OMS: o livro financeiro está vazio
        res = orch_b.restaurar_livro(livro, ledger=coord_b.ledger)
        assert not orch_b.book
        assert any("sem lote vivo" in a for a in res["avisos"])

    def test_posicao_no_financeiro_sem_livro_e_adotada(self):
        coord_a, orch_a, agent = self._entrar_com_estado()
        m2, risk2, led2, coord_b, orch_b = desk_completo()
        coord_b.restaurar_estado(coord_a.snapshot_estado())
        res = orch_b.restaurar_livro(None, ledger=coord_b.ledger)
        assert "BTC/USDT" in orch_b.book
        pos = orch_b.book["BTC/USDT"]
        assert pos.politica is not None          # política padrão do trader
        assert pos.agent_id == agent
        assert any("adotada" in a for a in res["avisos"])

    def test_quantidade_do_painel_vem_do_livro(self):
        coord_a, orch_a, agent = self._entrar_com_estado()
        painel = orch_a.livro_para_painel()
        assert painel and painel[0]["instrumento"] == "BTC/USDT"
        assert painel[0]["stop"] and painel[0]["alvo"]
        dono = next(t for t in orch_a.traders if t.spec.agent_id == agent)
        assert painel[0]["politica"] == politica_por(dono).chave
