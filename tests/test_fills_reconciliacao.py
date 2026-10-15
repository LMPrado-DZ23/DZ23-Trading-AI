"""Reconciliação com FILLS REAIS.

`order.filled` é acumulado e não tem identidade: usá-lo como quantidade cria
um fill sintético que não corresponde a nenhuma execução da corretora — e é
exatamente o padrão que o resto do sistema já eliminou.
"""
from __future__ import annotations

import inspect
from decimal import Decimal

from tradingagents.desk.execution import ExecutionCoordinator
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import Action, DeskMandate, Mode, TradeProposal, utcnow
from tradingagents.desk.risk import RiskEngine


def sistema(capital="10000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT",),
                    capital=Decimal(capital))
    risco = RiskEngine(m)
    livro = Ledger(opening_cash=m.capital)
    return m, risco, livro, ExecutionCoordinator(m, livro, risco, worker_id="w")


def reserva(risco, agente="agente-A"):
    from datetime import timedelta
    p = TradeProposal(agent_id=agente, strategy="s", instrument="BTC/USDT",
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal("0.15"),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    return risco.reserve(p, Decimal("100"), agent_id=agente)


def doc(res, quantidade="1.5", estado="acknowledged"):
    return {"schema": "dz23.oms/2", "worker_id": "w", "dia": "",
            "perda_do_dia": "0", "oms_version": 1,
            "ordens": [{"order_id": "ord-1", "estado": estado,
                        "fingerprint": "fp-1", "filled": "0", "preco_medio": "0",
                        "fees": "0", "fills_vistos": [], "external_id": "ex-1",
                        "nota": "", "instrumento": "BTC/USDT",
                        "quantidade": quantidade, "lado": "buy",
                        "client_order_id": "coid-1", "agente": "agente-A",
                        "reserva_id": res.reservation_id, "procedencia": None,
                        "custo_referencia": ""}],
            "posicoes": {}, "caixa": "8500", "moeda": "USDT", "lancamentos": [],
            "reservas": [{"reservation_id": res.reservation_id,
                          "account": "conta-1", "agent_id": "agente-A",
                          "instrument": "BTC/USDT", "amount": str(res.amount),
                          "quantity": str(res.quantity), "mandate_version": 1,
                          "expires_at": None}]}


def ordem_remota(filled="1.5", status="closed"):
    return {"resultado": "FOUND", "ordem": {
        "id": "ex-1", "status": status, "filled": filled, "average": "100",
        "fee": "0.15"}}


class TestFillsIndividualizados:
    def test_each_remote_fill_becomes_its_own_execution(self):
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc(res))
        fills = [
            {"fill_id": "t-1", "quantity": Decimal("0.5"), "price": Decimal("100"),
             "fee": Decimal("0.05"), "side": "buy"},
            {"fill_id": "t-2", "quantity": Decimal("0.5"), "price": Decimal("102"),
             "fee": Decimal("0.05"), "side": "buy"},
            {"fill_id": "t-3", "quantity": Decimal("0.5"), "price": Decimal("101"),
             "fee": Decimal("0.05"), "side": "buy"},
        ]
        rel = coord.reconciliar(lambda coid: ordem_remota(),
                                consultar_posicoes=lambda: {"BTC/USDT": Decimal("1.5")},
            consultar_ordens_abertas=lambda: [],
                                consultar_fills=lambda coid, exid="": fills,
                                consultar_caixa=lambda: coord.ledger.cash())
        rec = coord.orders["ord-1"]
        assert rec.seen_fills == {"t-1", "t-2", "t-3"}, (
            "a reconciliação criou um fill sintético em vez de usar os reais")
        assert rec.filled_qty == Decimal("1.5")
        # média ponderada dos fills, não o `average` da ordem
        assert abs(rec.avg_price - Decimal("101")) < Decimal("0.0001")
        assert rel["seguro"] is True, rel["divergencias"]

    def test_replaying_the_same_fills_does_not_duplicate(self):
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc(res))
        fills = [{"fill_id": "t-1", "quantity": Decimal("1.5"),
                  "price": Decimal("100"), "fee": Decimal("0"), "side": "buy"}]
        for _ in range(3):
            coord.reconciliar(lambda coid: ordem_remota(),
                              consultar_posicoes=lambda: {"BTC/USDT": Decimal("1.5")},
            consultar_ordens_abertas=lambda: [],
                              consultar_fills=lambda coid, exid="": fills)
        assert coord.ledger.position_quantity("BTC/USDT") == Decimal("1.5"), (
            "reconciliar de novo duplicou a posição")

    def test_no_synthetic_fill_is_created_anymore(self):
        fonte = inspect.getsource(ExecutionCoordinator._aplicar_ordem_remota)
        assert "average" not in fonte or "fill_id=" not in fonte, (
            "ainda há fill fabricado a partir do acumulado")


class TestAcumuladoEApenasInvariante:
    def test_sum_of_fills_must_match_the_cumulative(self):
        """Corretora diz filled=1.5 mas só entrega fills de 1.0: divergência,
        não 'deve estar certo'."""
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc(res))
        fills = [{"fill_id": "t-1", "quantity": Decimal("1.0"),
                  "price": Decimal("100"), "fee": Decimal("0"), "side": "buy"}]
        rel = coord.reconciliar(lambda coid: ordem_remota(filled="1.5"),
                                consultar_posicoes=lambda: {"BTC/USDT": Decimal("1.0", consultar_ordens_abertas=lambda: [])},
                                consultar_fills=lambda coid, exid="": fills)
        assert rel["seguro"] is False
        assert any("FILLS_DIVERGEM" in d for d in rel["divergencias"])

    def test_matching_sum_reports_fills_status(self):
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc(res))
        fills = [{"fill_id": "t-1", "quantity": Decimal("1.5"),
                  "price": Decimal("100"), "fee": Decimal("0"), "side": "buy"}]
        rel = coord.reconciliar(lambda coid: ordem_remota(),
                                consultar_posicoes=lambda: {"BTC/USDT": Decimal("1.5")},
            consultar_ordens_abertas=lambda: [],
                                consultar_fills=lambda coid, exid="": fills)
        assert rel["fills_status"] == "MATCHED"


class TestSemFillsNaoSeInventa:
    def test_without_a_fills_provider_the_status_is_unknown(self):
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc(res))
        rel = coord.reconciliar(lambda coid: ordem_remota(),
                                consultar_posicoes=lambda: {}, consultar_ordens_abertas=lambda: [])
        assert rel["fills_status"] == "UNKNOWN"
        assert rel["seguro"] is False
        assert coord.ledger.position_quantity("BTC/USDT") == Decimal("0"), (
            "posição foi criada sem execução comprovada")

    def test_fills_query_failure_is_unknown_not_empty(self):
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc(res))

        def explode(coid, exid=""):
            raise RuntimeError("exchange fora do ar")

        rel = coord.reconciliar(lambda coid: ordem_remota(),
                                consultar_posicoes=lambda: {}, consultar_ordens_abertas=lambda: [],
                                consultar_fills=explode)
        assert rel["fills_status"] == "UNKNOWN" and rel["seguro"] is False

    def test_empty_fills_with_nonzero_filled_is_a_divergence(self):
        """Nenhuma execução mas acumulado positivo: não se conclui nada."""
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc(res))
        rel = coord.reconciliar(lambda coid: ordem_remota(filled="1.5"),
                                consultar_posicoes=lambda: {}, consultar_ordens_abertas=lambda: [],
                                consultar_fills=lambda coid, exid="": [])
        assert rel["seguro"] is False
        assert any("FILLS_DIVERGEM" in d or "sem execuções" in d
                   for d in rel["divergencias"])


class TestLadoDoFillVemDaExecucao:
    def test_a_sell_fill_reduces_the_position(self):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("100"), ref="r0")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("100"), Decimal("0"),
                       reserved=Decimal("100"), ref="o0", agent_id="agente-A")
        res = reserva(risco)
        d = doc(res, quantidade="1")
        d["ordens"][0]["lado"] = "sell"
        coord.restaurar_estado(d)
        fills = [{"fill_id": "s-1", "quantity": Decimal("1"),
                  "price": Decimal("110"), "fee": Decimal("0"), "side": "sell"}]
        coord.reconciliar(lambda coid: ordem_remota(filled="1"),
                          consultar_posicoes=lambda: {}, consultar_ordens_abertas=lambda: [],
                          consultar_fills=lambda coid, exid="": fills)
        assert coord.ledger.position_quantity("BTC/USDT") == Decimal("0")


class TestFillsStatusEntraNoVeredito:
    """SAFE precisa exigir as QUATRO dimensões. Se fills_status ficar de
    fora, uma ordem sem execuções comprovadas passaria por segura."""

    def test_unknown_fills_forbid_safe_even_with_everything_else_matched(self):
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc(res))
        # ordens e posições batendo; só as execuções não foram consultadas
        rel = coord.reconciliar(
            lambda coid: ordem_remota(filled="0", status="closed"),
            consultar_posicoes=lambda: {}, consultar_ordens_abertas=lambda: [])
        assert rel["posicoes_status"] == "MATCHED"
        assert rel["livro_status"] == "BALANCED"
        assert rel["fills_status"] == "UNKNOWN"
        assert rel["seguro"] is False, (
            "SAFE declarado sem execuções comprovadas")

    def test_mismatched_fills_forbid_safe(self):
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc(res))
        fills = [{"fill_id": "t-1", "quantity": Decimal("0.5"),
                  "price": Decimal("100"), "fee": Decimal("0"), "side": "buy"}]
        rel = coord.reconciliar(
            lambda coid: ordem_remota(filled="1.5"),
            consultar_posicoes=lambda: {"BTC/USDT": Decimal("0.5", consultar_ordens_abertas=lambda: [])},
            consultar_fills=lambda coid, exid="": fills)
        assert rel["fills_status"] == "MISMATCH" and rel["seguro"] is False

    def test_the_four_dimensions_are_required_for_safe(self):
        import inspect
        fonte = inspect.getsource(ExecutionCoordinator._selar)
        for dimensao in ("ordens_status", "posicoes_status", "fills_status",
                         "livro_status"):
            assert dimensao in fonte, f"{dimensao} fora do veredito"
