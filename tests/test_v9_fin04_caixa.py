"""V9-FIN-04 (sub-item): conferência de CAIXA na reconciliação.

O adaptador buscava o saldo da corretora e DESCARTAVA a moeda de cotação
("caixa não é posição"); o caixa do livro-razão nunca era comparado com o
caixa real da conta. Uma divergência APENAS de cotação — posições todas
batendo, ordens todas vistas — virava SAFE, e o portão liberava operação
sobre dinheiro que não existe.

Contraprava da missão: "divergência apenas de cotação deve ser detectada".
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.brokers import BrokerConsultaIndisponivel, CCXTDeskBroker
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


def reconciliar(coord, *, caixa, posicoes=None, fills=None,
                ordens_abertas=None, corretora=None):
    """Wiring igual ao da rota: só passa consultar_caixa quando pedido."""
    extra = {}
    if caixa is not None:
        extra["consultar_caixa"] = caixa
    if fills is not None:
        extra["consultar_fills"] = fills
    return coord.reconciliar(
        corretora or (lambda coid: {"resultado": "CONFIRMED_NOT_FOUND",
                                    "ordem": None}),
        consultar_posicoes=posicoes if posicoes is not None else (lambda: {}),
        consultar_ordens_abertas=(ordens_abertas if ordens_abertas is not None
                                  else (lambda: [])),
        **extra)


class TestCaixaNaReconciliacao:
    """ACHADO: divergência SÓ de cotação passava como SAFE."""

    def test_only_cash_divergence_blocks_safe(self):
        m, risco, livro, coord = sistema()
        rel = reconciliar(coord, caixa=lambda: Decimal("9999"))
        assert rel["caixa_status"] == "MISMATCH"
        assert rel["seguro"] is False, (
            "posições batendo NÃO basta: o caixa divergia e o portão abriu")

    def test_the_divergence_shows_both_values(self):
        m, risco, livro, coord = sistema()
        rel = reconciliar(coord, caixa=lambda: Decimal("9999"))
        mensagens = [d for d in rel["divergencias"] if "CAIXA_DIVERGENTE" in d]
        assert mensagens, rel["divergencias"]
        # o dono precisa ver OS DOIS números para saber para onde olhar
        assert "10000" in mensagens[0] and "9999" in mensagens[0]

    def test_matching_cash_allows_safe(self):
        m, risco, livro, coord = sistema()
        rel = reconciliar(coord, caixa=lambda: Decimal("10000"))
        assert rel["caixa_status"] == "MATCHED"
        assert rel["seguro"] is True, rel["divergencias"]

    def test_cash_is_read_after_remote_fills_are_applied(self):
        """Ordem de leitura importa: se o caixa for consultado ANTES de os
        fills remotos entrarem no livro, um preenchimento legítimo viraria
        'divergência' — e o inverso esconderia a divergência residual."""
        m, risco, livro, coord = sistema()
        res = _reserva(risco)
        coord.restaurar_estado(_doc_com_ordem(res))
        fills = [
            {"fill_id": "t-1", "quantity": Decimal("0.5"), "price": Decimal("100"),
             "fee": Decimal("0.05"), "fee_currency": "USDT", "side": "buy"},
            {"fill_id": "t-2", "quantity": Decimal("0.5"), "price": Decimal("102"),
             "fee": Decimal("0.05"), "fee_currency": "USDT", "side": "buy"},
            {"fill_id": "t-3", "quantity": Decimal("0.5"), "price": Decimal("101"),
             "fee": Decimal("0.05"), "fee_currency": "USDT", "side": "buy"},
        ]
        visto = {}

        def caixa_spy():
            visto["livro_no_momento_da_consulta"] = livro.cash()
            return livro.cash()

        rel = reconciliar(
            coord,
            corretora=lambda coid: {"resultado": "FOUND", "ordem": {
                "id": "ex-1", "status": "closed", "filled": "1.5",
                "average": "100", "fee": "0.15"}},
            posicoes=lambda: {"BTC/USDT": Decimal("1.5")},
            fills=lambda coid, exid="": fills,
            caixa=caixa_spy)
        assert visto["livro_no_momento_da_consulta"] == Decimal("9848.35"), (
            "o caixa foi lido antes de os fills remotos entrarem no livro: "
            f"livro era {visto['livro_no_momento_da_consulta']}")
        assert rel["caixa_status"] == "MATCHED"
        assert rel["seguro"] is True, rel["divergencias"]

    def test_without_a_cash_query_there_is_no_safe(self):
        """Fail-closed: um chamador que não consulta o caixa nunca libera —
        'não perguntei' não pode virar 'bate'."""
        m, risco, livro, coord = sistema()
        rel = reconciliar(coord, caixa=None)
        assert rel["caixa_status"] == "UNKNOWN"
        assert rel["seguro"] is False
        assert any("caixa" in d.lower() for d in rel["divergencias"])

    def test_a_failing_cash_query_stays_unknown(self):
        m, risco, livro, coord = sistema()

        def explodir():
            raise ConnectionError("rede caiu")

        rel = reconciliar(coord, caixa=explodir)
        assert rel["caixa_status"] == "UNKNOWN"
        assert rel["seguro"] is False
        assert any("consulta de caixa falhou" in d for d in rel["divergencias"])

    def test_a_malformed_cash_answer_stays_unknown(self):
        m, risco, livro, coord = sistema()
        rel = reconciliar(coord, caixa=lambda: "abc")
        assert rel["caixa_status"] == "UNKNOWN"
        assert rel["seguro"] is False
        assert any("caixa" in d.lower() for d in rel["divergencias"])


class TestRelatorioAntigoSemCaixaNaoAutoriza:
    """ACHADO: um relatório SAFE gravado ANTES desta checagem (sem
    `caixa_status`) era restaurado no reinício e continuava autorizando —
    justamente quando o sistema mais precisa reconferir."""

    def test_report_without_caixa_status_is_refused(self):
        m, risco, livro, coord = sistema()
        rel = reconciliar(coord, caixa=lambda: Decimal("10000"))
        assert rel["seguro"] is True
        coord.ultimo_relatorio = {k: v for k, v in rel.items()
                                  if k != "caixa_status"}
        ok, motivo = coord.relatorio_valido()
        assert ok is False and "caixa" in motivo.lower()

    def test_report_with_unknown_cash_is_refused(self):
        m, risco, livro, coord = sistema()
        rel = reconciliar(coord, caixa=lambda: Decimal("10000"))
        coord.ultimo_relatorio = {**rel, "caixa_status": "UNKNOWN"}
        ok, _ = coord.relatorio_valido()
        assert ok is False

    def test_restart_with_pre_fix_report_is_refused(self):
        m, risco, livro, coord = sistema()
        reconciliar(coord, caixa=lambda: Decimal("10000"))
        doc = coord.snapshot_estado()
        doc["ultimo_relatorio"] = {k: v for k, v
                                   in doc["ultimo_relatorio"].items()
                                   if k != "caixa_status"}
        m2, risco2, livro2, coord2 = sistema()
        coord2.restaurar_estado(doc)
        ok, motivo = coord2.relatorio_valido()
        assert ok is False and "caixa" in motivo.lower(), (
            "relatório pré-checagem de caixa voltou a autorizar após reinício")


def _reserva(risco, agente="agente-A"):
    from datetime import timedelta
    p = TradeProposal(agent_id=agente, strategy="s", instrument="BTC/USDT",
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal("0.15"),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    return risco.reserve(p, Decimal("100"), agent_id=agente)


def _doc_com_ordem(res, quantidade="1.5", estado="acknowledged"):
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


class _CorretoraFalsa:
    def __init__(self, total=None, erro=None, resposta=None):
        self._total, self._erro, self._resposta = total, erro, resposta

    def fetch_balance(self):
        if self._erro is not None:
            raise self._erro
        if self._resposta is not None:
            return self._resposta
        return {"total": self._total}


def _adaptador(exchange) -> CCXTDeskBroker:
    """Adaptador REAL sem tocar a rede — mesmo padrão de test_broker_contrato."""
    b = CCXTDeskBroker.__new__(CCXTDeskBroker)
    ponte = type("PonteFalsa", (), {})()
    ponte._ex = exchange
    ponte.mode = "testnet"
    b.bridge = ponte
    b.final_state = {}
    b.mode = "testnet"
    b.submitted = {}
    b.universe = ("BTC/USDT",)
    return b


class TestAdaptadorConsultaCaixa:
    """Contrato do adaptador: o caixa é a moeda de COTAÇÃO — exatamente a
    que `fetch_positions` descarta. Ausência dela é DESCONHECIDO, nunca zero."""

    def test_quote_currency_cash_is_returned(self):
        b = _adaptador(_CorretoraFalsa(total={"BTC": "0.5", "USDT": "900.25"}))
        assert b.fetch_cash() == Decimal("900.25")

    def test_missing_quote_currency_is_unavailable_not_zero(self):
        b = _adaptador(_CorretoraFalsa(total={"BTC": "0.5"}))
        with pytest.raises(BrokerConsultaIndisponivel, match="USDT"):
            b.fetch_cash()

    def test_network_failure_is_unavailable(self):
        b = _adaptador(_CorretoraFalsa(erro=ConnectionError("x")))
        with pytest.raises(BrokerConsultaIndisponivel, match="consulta de caixa"):
            b.fetch_cash()

    def test_response_without_total_section_is_unavailable(self):
        b = _adaptador(_CorretoraFalsa(resposta={"free": {}}))
        with pytest.raises(BrokerConsultaIndisponivel, match="total"):
            b.fetch_cash()

    def test_non_dict_response_is_unavailable(self):
        b = _adaptador(_CorretoraFalsa(resposta="nope"))
        with pytest.raises(BrokerConsultaIndisponivel, match="malformada"):
            b.fetch_cash()

    def test_invalid_amount_is_unavailable(self):
        b = _adaptador(_CorretoraFalsa(total={"USDT": "abc"}))
        with pytest.raises(BrokerConsultaIndisponivel, match="USDT"):
            b.fetch_cash()

    def test_exchange_without_balance_query_is_unavailable(self):
        b = _adaptador(object())
        with pytest.raises(BrokerConsultaIndisponivel, match="saldo"):
            b.fetch_cash()
