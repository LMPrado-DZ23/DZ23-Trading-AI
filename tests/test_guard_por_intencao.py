"""Guard decide por INTENÇÃO, não igual para tudo.

Três defeitos: o bloqueio por incidente também impedia FECHAR posição —
prender o dinheiro dentro do risco é o oposto de proteger; um incidente
reconhecido nunca chegava a RESOLVED, mesmo depois do ativo aportado; e o
preflight não olhava divergência entre livro e risco.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator, ExecutionRefused
from tradingagents.desk.intents import ExecutionIntent, Kind, Side
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import Action, DeskMandate, Mode, TradeProposal, utcnow
from tradingagents.desk.risk import RiskEngine


class Ator:
    actor_id = "risk-manager-1"
    capabilities = frozenset({"RESOLVE_RISK_INCIDENT", "VALUE_FEE_INCIDENT"})


class Corretora:
    mode = "testnet"

    def __init__(self):
        self.chamadas = 0

    def submit(self, coid, instrument, side, quantity, final_state=None):
        self.chamadas += 1
        return {"id": f"ex-{coid}", "status": "open", "filled": 0}


def sistema(capital="10000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT", "ETH/USDT"),
                    capital=Decimal(capital), slippage_buffer_pct=Decimal("0"))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=Corretora())
    return m, risco, livro, coord


def abrir_posicao(coord, risco, livro, instrumento="BTC/USDT", coid="entrada"):
    p = TradeProposal(agent_id="A", strategy="s", instrument=instrumento,
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal("0.05"),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    res = risco.reserve(p, Decimal("100"), agent_id="A")
    ei = ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY, instrument=instrumento,
                         quantity=res.quantity, agent_id="A",
                         account="conta-1", client_order_id=coid,
                         reservation_id=res.reservation_id,
                         reserved_amount=res.amount)
    rec = coord.submit_intent(ei)
    coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                       preco=Decimal("90"), taxa=Decimal("0"), fill_id="f1")
    return res


def intent_saida(instrumento, quantidade, coid="saida", agent="A"):
    return ExecutionIntent(kind=Kind.EXIT, side=Side.SELL,
                           instrument=instrumento, quantity=quantidade,
                           agent_id=agent, account="conta-1",
                           client_order_id=coid)


def intent_entrada(risco, instrumento="ETH/USDT", coid="nova"):
    p = TradeProposal(agent_id="A", strategy="s", instrument=instrumento,
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal("0.05"),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    res = risco.reserve(p, Decimal("100"), agent_id="A")
    return ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY,
                           instrument=instrumento, quantity=res.quantity,
                           agent_id="A", account="conta-1",
                           client_order_id=coid,
                           reservation_id=res.reservation_id,
                           reserved_amount=res.amount)


def descobrir(livro, moeda="BNB", qtd="0.5", ref="z"):
    livro.reserve(Decimal("50"), ref=ref, reservation_id=f"R{ref}")
    livro.fill_buy("BTC/USDT", Decimal("0.1"), Decimal("90"), Decimal("0"),
                   reserved=Decimal("50"), ref=ref, agent_id="A",
                   reservation_id=f"R{ref}",
                   fee_in_other_currency=(Decimal(qtd), moeda))


class TestIncidenteBloqueiaEntradaNaoSaida:
    def test_a_new_entry_is_blocked(self):
        m, risco, livro, coord = sistema()
        abrir_posicao(coord, risco, livro)
        antes = coord.broker.chamadas
        descobrir(livro)
        with pytest.raises(ExecutionRefused):
            coord.submit_intent(intent_entrada(risco))
        assert coord.broker.chamadas == antes

    def test_closing_the_position_is_allowed(self):
        """Prender o dinheiro DENTRO do risco é o oposto de proteger."""
        m, risco, livro, coord = sistema()
        res = abrir_posicao(coord, risco, livro)
        descobrir(livro)
        antes = coord.broker.chamadas
        coord.submit_intent(intent_saida("BTC/USDT", res.quantity))
        assert coord.broker.chamadas == antes + 1, (
            "o incidente impediu FECHAR posição")

    def test_the_incident_stays_open_after_the_exit(self):
        m, risco, livro, coord = sistema()
        res = abrir_posicao(coord, risco, livro)
        descobrir(livro)
        risco.conferir_ativos_descobertos()
        coord.submit_intent(intent_saida("BTC/USDT", res.quantity))
        assert risco.execution_blockers(), "a saída apagou o incidente"

    def test_the_decision_is_explicit_per_intent(self):
        m, risco, livro, coord = sistema()
        res = abrir_posicao(coord, risco, livro)
        descobrir(livro)
        entrada = risco.can_execute(intent_entrada(risco))
        saida = risco.can_execute(intent_saida("BTC/USDT", res.quantity))
        assert entrada["decisao"] == "BLOCK_ENTRY"
        assert saida["decisao"] == "ALLOW_REDUCE_ONLY"
        assert entrada["motivos"], "bloquear sem dizer por quê não ajuda"


class TestReduceOnlyDeVerdade:
    def test_selling_more_than_the_position_is_blocked(self):
        """A exceção não pode virar porta para AUMENTAR risco."""
        m, risco, livro, coord = sistema()
        res = abrir_posicao(coord, risco, livro)
        descobrir(livro)
        demais = res.quantity * 2
        d = risco.can_execute(intent_saida("BTC/USDT", demais))
        assert d["decisao"] != "ALLOW_REDUCE_ONLY"
        with pytest.raises(ExecutionRefused):
            coord.submit_intent(intent_saida("BTC/USDT", demais, "over"))

    def test_selling_what_does_not_exist_is_blocked(self):
        m, risco, livro, coord = sistema()
        descobrir(livro)
        d = risco.can_execute(intent_saida("ETH/USDT", Decimal("1")))
        assert d["decisao"] != "ALLOW_REDUCE_ONLY", (
            "venda a descoberto liberada como se fosse redução")

    def test_zero_or_negative_quantity_never_becomes_an_intent(self):
        """A validação é ANTERIOR ao guard: a intenção nem se constrói."""
        from tradingagents.desk.intents import IntentInvalid
        for q in (Decimal("0"), Decimal("-1")):
            with pytest.raises(IntentInvalid, match="positiva"):
                intent_saida("BTC/USDT", q)

    def test_the_guard_also_refuses_a_non_positive_quantity(self):
        """Defesa em profundidade: se a intenção vier de outro caminho."""
        m, risco, livro, coord = sistema()
        abrir_posicao(coord, risco, livro)
        descobrir(livro)

        class IntencaoCrua:
            kind, side = "EXIT", "SELL"
            instrument, agent_id = "BTC/USDT", "A"
            quantity = Decimal("0")

        d = risco.can_execute(IntencaoCrua())
        assert d["decisao"] != "ALLOW_REDUCE_ONLY"

    def test_another_agents_lot_is_not_reducible_without_capability(self):
        m, risco, livro, coord = sistema()
        res = abrir_posicao(coord, risco, livro)
        descobrir(livro)
        d = risco.can_execute(intent_saida("BTC/USDT", res.quantity,
                                           agent="OUTRO"))
        assert d["decisao"] != "ALLOW_REDUCE_ONLY", (
            "um agente reduziu o lote de outro sem capacidade")


class TestAcknowledgedChegaAResolved:
    def test_it_resolves_once_the_asset_is_covered(self):
        m, risco, livro, coord = sistema()
        descobrir(livro)
        risco.conferir_ativos_descobertos()
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "UNCOVERED_ASSET_BALANCE"][0]
        risco.reconhecer_incidente(inc["incident_id"], actor=Ator(),
                                   evidence="aceita")
        livro.capital_adjustment_asset("BNB", Decimal("0.5"), reason="aporte",
                                       actor="op")
        risco.resolve_risk_incident(inc["incident_id"], actor=Ator(),
                                    evidence="ativo aportado", reason="")
        registro = next(i for i in risco.incidentes()
                        if i["incident_id"] == inc["incident_id"])
        assert registro["status"] == "RESOLVED"
        assert not risco.execution_blockers(), (
            "o incidente corrigido continuou bloqueando")

    def test_the_acknowledgement_evidence_survives(self):
        m, risco, livro, coord = sistema()
        descobrir(livro)
        risco.conferir_ativos_descobertos()
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "UNCOVERED_ASSET_BALANCE"][0]
        risco.reconhecer_incidente(inc["incident_id"], actor=Ator(),
                                   evidence="aceita pela política")
        livro.capital_adjustment_asset("BNB", Decimal("0.5"), reason="a",
                                       actor="op")
        risco.resolve_risk_incident(inc["incident_id"], actor=Ator(),
                                    evidence="aportado", reason="")
        registro = next(i for i in risco.incidentes()
                        if i["incident_id"] == inc["incident_id"])
        assert registro["acknowledged_evidence"] == "aceita pela política"
        assert registro["resolution_evidence"]


class TestReconhecerNaoCorrompe:
    def test_it_is_idempotent(self):
        m, risco, livro, coord = sistema()
        descobrir(livro)
        risco.conferir_ativos_descobertos()
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "UNCOVERED_ASSET_BALANCE"][0]
        a = risco.reconhecer_incidente(inc["incident_id"], actor=Ator(),
                                       evidence="uma vez")
        b = risco.reconhecer_incidente(inc["incident_id"], actor=Ator(),
                                       evidence="outra vez")
        assert b["acknowledged_evidence"] == a["acknowledged_evidence"], (
            "o segundo reconhecimento sobrescreveu a evidência do primeiro")

    def test_a_resolved_incident_cannot_be_acknowledged(self):
        m, risco, livro, coord = sistema()
        descobrir(livro)
        risco.conferir_ativos_descobertos()
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "UNCOVERED_ASSET_BALANCE"][0]
        livro.capital_adjustment_asset("BNB", Decimal("0.5"), reason="a",
                                       actor="op")
        risco.resolve_risk_incident(inc["incident_id"], actor=Ator(),
                                    evidence="ok", reason="")
        with pytest.raises(ValueError, match="RESOLVED|terminal"):
            risco.reconhecer_incidente(inc["incident_id"], actor=Ator(),
                                       evidence="tentando reabrir")

    def test_a_fee_incident_cannot_be_acknowledged(self):
        """Reconhecer travaria a valoração para sempre."""
        m, risco, livro, coord = sistema()
        risco.registrar_taxa_nao_valorada(moeda="BNB", quantidade=Decimal("1"),
                                          order_id="o1", instrumento="BTC/USDT")
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "FEE_VALUATION_UNKNOWN"][0]
        with pytest.raises(ValueError, match="FEE_VALUATION_UNKNOWN|tipo"):
            risco.reconhecer_incidente(inc["incident_id"], actor=Ator(),
                                       evidence="aceita")


class TestPreflightDeConsistencia:
    def test_an_orphan_ledger_reservation_blocks_the_entry(self):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("100"), ref="orfa", reservation_id="R-ORFA")
        antes = coord.broker.chamadas
        with pytest.raises(ExecutionRefused, match="ORPHAN|R-ORFA"):
            coord.submit_intent(intent_entrada(risco))
        assert coord.broker.chamadas == antes

    def test_a_bound_reservation_without_backing_blocks(self):
        """Vinculada a uma ordem e sem lastro no livro: aí é divergência.

        Uma reserva apenas CRIADA e ainda não enviada está legitimamente
        ausente do livro — acusá-la impediria qualquer ciclo em que vários
        agentes reservam antes de enviar.
        """
        m, risco, livro, coord = sistema()
        # ordem ENVIADA e ainda não executada: a reserva segue viva e
        # vinculada (o fill a encerraria)
        ei = intent_entrada(risco, "BTC/USDT", "pendente")
        coord.submit_intent(ei)
        res = risco.reserva(ei.reservation_id)
        assert res is not None
        travado = livro.reserved_cash(ei.reservation_id)
        if travado > 0:                              # o livro perde a cobertura
            livro.release(travado, ref="sumico",
                          reservation_id=ei.reservation_id)
        assert risco.reserva(ei.reservation_id).bound_client_order_id, (
            "a reserva precisa estar vinculada a uma ordem")
        antes = coord.broker.chamadas
        with pytest.raises(ExecutionRefused, match="MISSING|divergen"):
            coord.submit_intent(intent_entrada(risco, "ETH/USDT"))
        assert coord.broker.chamadas == antes

    def test_an_unsent_reservation_is_not_an_error(self):
        m, risco, livro, coord = sistema()
        p = TradeProposal(agent_id="A", strategy="s", instrument="BTC/USDT",
                          action=Action.BUY, reason="t",
                          desired_exposure_pct=Decimal("0.05"),
                          confidence=Decimal("0.8"),
                          valid_until=utcnow() + timedelta(minutes=5))
        risco.reserve(p, Decimal("100"), agent_id="A")   # criada, não enviada
        antes = coord.broker.chamadas
        coord.submit_intent(intent_entrada(risco, "ETH/USDT"))
        assert coord.broker.chamadas == antes + 1

    def test_a_value_mismatch_blocks(self):
        m, risco, livro, coord = sistema()
        res = abrir_posicao(coord, risco, livro)
        livro.reserve(Decimal("10"), ref="desvio",
                      reservation_id=res.reservation_id)
        antes = coord.broker.chamadas
        with pytest.raises(ExecutionRefused):
            coord.submit_intent(intent_entrada(risco))
        assert coord.broker.chamadas == antes

    def test_the_entrys_own_pending_reservation_is_not_an_error(self):
        """A reserva desta ordem ainda não foi travada — quem trava é o
        passo seguinte. Acusá-la impediria qualquer envio."""
        m, risco, livro, coord = sistema()
        antes = coord.broker.chamadas
        coord.submit_intent(intent_entrada(risco))
        assert coord.broker.chamadas == antes + 1

    def test_another_reservations_mismatch_is_not_excused(self):
        """A dispensa vale SÓ para a reserva desta intenção.

        Usa uma ordem ENVIADA e não executada: assim a reserva continua
        viva no risco E travada no livro, e o desvio de valor é de fato um
        MISMATCH — não uma órfã.
        """
        m, risco, livro, coord = sistema()
        ei = intent_entrada(risco, "BTC/USDT", "viva")
        coord.submit_intent(ei)
        assert risco.reserva(ei.reservation_id) is not None
        livro.reserve(Decimal("7"), ref="desvio",
                      reservation_id=ei.reservation_id)   # livro > risco

        antes = coord.broker.chamadas
        with pytest.raises(ExecutionRefused, match="MISMATCH"):
            coord.submit_intent(intent_entrada(risco, "ETH/USDT", "nova2"))
        assert coord.broker.chamadas == antes


class TestGeracoesPreservamHistorico:
    def test_three_cycles_keep_three_records(self):
        m, risco, livro, coord = sistema()
        ids = []
        for i, qtd in enumerate(("0.5", "0.3", "0.2")):
            descobrir(livro, "BNB", qtd, f"g{i}")
            risco.conferir_ativos_descobertos()
            inc = [x for x in risco.incidentes_abertos()
                   if x["type"] == "UNCOVERED_ASSET_BALANCE"][0]
            ids.append(inc["incident_id"])
            falta = risco.ativos_descobertos()["BNB"]
            livro.capital_adjustment_asset("BNB", falta, reason="aporte",
                                           actor="op")
            risco.resolve_risk_incident(inc["incident_id"], actor=Ator(),
                                        evidence=f"ciclo {i}", reason="")
        assert len(set(ids)) == 3, "gerações se sobrescreveram"
        registros = [x for x in risco.incidentes()
                     if x["type"] == "UNCOVERED_ASSET_BALANCE"]
        assert len(registros) == 3
        assert all(x["status"] == "RESOLVED" for x in registros)
        assert {x["resolution_evidence"].split("|")[0].strip()
                for x in registros} == {"ciclo 0", "ciclo 1", "ciclo 2"}


class TestPersistencia:
    def test_acknowledged_survives_a_restart(self):
        m, risco, livro, coord = sistema()
        descobrir(livro)
        risco.conferir_ativos_descobertos()
        inc = [i for i in risco.incidentes_abertos()
               if i["type"] == "UNCOVERED_ASSET_BALANCE"][0]
        risco.reconhecer_incidente(inc["incident_id"], actor=Ator(),
                                   evidence="aceita")
        doc = risco.incidentes_doc()

        m2, risco2, livro2, coord2 = sistema()
        risco2.restaurar_incidentes(doc)
        registro = next(i for i in risco2.incidentes()
                        if i["incident_id"] == inc["incident_id"])
        assert registro["status"] == "ACKNOWLEDGED_BLOCKING"
        assert registro["acknowledged_evidence"] == "aceita"
