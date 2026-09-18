"""Detecção de ativo descoberto: por moeda, idempotente e sem efeito colateral.

Três defeitos meus da rodada anterior: duas moedas descobertas viravam UM
incidente com a moeda da última, cada chamada SOMAVA de novo o saldo (e a
verificação de consistência chama a detecção, então o número crescia só de
alguém ler o estado), e um incidente resolvido voltava a abrir sozinho.
"""
from __future__ import annotations

from decimal import Decimal

from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import DeskMandate, Mode
from tradingagents.desk.risk import RiskEngine


class Ator:
    actor_id = "risk-manager-1"
    capabilities = frozenset({"RESOLVE_RISK_INCIDENT", "VALUE_FEE_INCIDENT"})


def sistema(capital="1000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT",),
                    capital=Decimal(capital), slippage_buffer_pct=Decimal("0"))
    livro = Ledger(opening_cash=m.capital)
    return m, RiskEngine(m, carteira=livro), livro


def taxa_em(livro, moeda, qtd, ref="o"):
    livro.reserve(Decimal("100"), ref=ref, reservation_id=f"R{ref}")
    livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                   reserved=Decimal("100"), ref=ref, agent_id="A",
                   reservation_id=f"R{ref}",
                   fee_in_other_currency=(Decimal(qtd), moeda))


def descobertos(risco):
    return [i for i in risco.incidentes_abertos()
            if i["type"] == "UNCOVERED_ASSET_BALANCE"]


class TestUmIncidentePorMoeda:
    def test_two_currencies_are_two_incidents(self):
        m, risco, livro = sistema()
        taxa_em(livro, "BNB", "0.5", "a")
        taxa_em(livro, "SOL", "0.3", "b")
        risco.conferir_ativos_descobertos()
        moedas = {i["fee_currency"] for i in descobertos(risco)}
        assert moedas == {"BNB", "SOL"}, (
            f"as moedas se misturaram num incidente só: {moedas}")

    def test_each_incident_carries_its_own_amount(self):
        m, risco, livro = sistema()
        taxa_em(livro, "BNB", "0.5", "a")
        taxa_em(livro, "SOL", "0.3", "b")
        risco.conferir_ativos_descobertos()
        por_moeda = {i["fee_currency"]: Decimal(i["amount"])
                     for i in descobertos(risco)}
        assert por_moeda == {"BNB": Decimal("0.5"), "SOL": Decimal("0.3")}


class TestIdempotencia:
    def test_calling_twice_does_not_double_the_amount(self):
        m, risco, livro = sistema()
        taxa_em(livro, "BNB", "0.5", "a")
        risco.conferir_ativos_descobertos()
        primeiro = Decimal(descobertos(risco)[0]["amount"])
        risco.conferir_ativos_descobertos()
        risco.conferir_ativos_descobertos()
        assert Decimal(descobertos(risco)[0]["amount"]) == primeiro, (
            "o valor cresceu só de alguém olhar o estado")

    def test_reading_the_capital_state_does_not_change_it(self):
        m, risco, livro = sistema()
        taxa_em(livro, "BNB", "0.5", "a")
        risco.conferir_ativos_descobertos()
        antes = Decimal(descobertos(risco)[0]["amount"])
        for _ in range(3):
            risco.capital_state_consistent()
        assert Decimal(descobertos(risco)[0]["amount"]) == antes, (
            "ler a consistência alterou o incidente")

    def test_the_amount_follows_the_real_balance(self):
        """Mais taxa na mesma moeda aumenta o descoberto; cobrir diminui."""
        m, risco, livro = sistema()
        taxa_em(livro, "BNB", "0.5", "a")
        risco.conferir_ativos_descobertos()
        taxa_em(livro, "BNB", "0.2", "b")
        risco.conferir_ativos_descobertos()
        assert Decimal(descobertos(risco)[0]["amount"]) == Decimal("0.7")


class TestNaoReabreResolvido:
    def test_a_resolved_incident_is_not_reopened_by_the_check(self):
        m, risco, livro = sistema()
        taxa_em(livro, "BNB", "0.5", "a")
        risco.conferir_ativos_descobertos()
        inc = descobertos(risco)[0]
        # aporte do ativo: o descoberto deixou de existir
        livro.capital_adjustment_asset("BNB", Decimal("0.5"), reason="aporte",
                                       actor="op")
        assert livro.balance("ASSET:BNB") == Decimal("0")
        risco.resolve_risk_incident(inc["incident_id"], actor=Ator(),
                                    evidence="ativo aportado", reason="")
        risco.conferir_ativos_descobertos()
        assert not descobertos(risco), "o incidente resolvido voltou sozinho"

    def test_a_new_shortfall_after_resolution_opens_a_new_incident(self):
        m, risco, livro = sistema()
        taxa_em(livro, "BNB", "0.5", "a")
        risco.conferir_ativos_descobertos()
        inc = descobertos(risco)[0]
        livro.capital_adjustment_asset("BNB", Decimal("0.5"), reason="aporte",
                                       actor="op")
        risco.resolve_risk_incident(inc["incident_id"], actor=Ator(),
                                    evidence="ok", reason="")
        taxa_em(livro, "BNB", "0.4", "c")
        risco.conferir_ativos_descobertos()
        novos = descobertos(risco)
        assert novos, "descoberto novo não foi detectado"
        assert novos[0]["incident_id"] != inc["incident_id"], (
            "reabriu o antigo em vez de abrir um novo")


class TestDeteccaoSemEfeitoColateral:
    def test_capital_state_consistent_is_read_only(self):
        import inspect
        fonte = inspect.getsource(RiskEngine.capital_state_consistent)
        assert "conferir_ativos_descobertos" not in fonte, (
            "a leitura do estado dispara detecção que ESCREVE incidentes")

    def test_it_still_reports_an_existing_shortfall(self):
        m, risco, livro = sistema()
        taxa_em(livro, "BNB", "0.5", "a")
        risco.conferir_ativos_descobertos()
        ok, motivo = risco.capital_state_consistent()
        assert ok is False and "UNCOVERED_ASSET_BALANCE" in motivo

    def test_an_undetected_shortfall_still_blocks(self):
        """Mesmo sem alguém ter chamado a detecção, saldo negativo de ativo
        não pode passar por estado consistente."""
        m, risco, livro = sistema()
        taxa_em(livro, "BNB", "0.5", "a")
        ok, motivo = risco.capital_state_consistent()
        assert ok is False, "saldo de ativo negativo passou como consistente"
        assert "ASSET:BNB" in motivo or "descoberto" in motivo.lower()


class TestMoedaVemDoFillReal:
    """O adaptador já devolve fee_currency; o caminho precisa usá-lo."""

    def test_the_reconciliation_passes_the_fee_currency(self):
        import inspect

        from tradingagents.desk.execution import ExecutionCoordinator
        fonte = inspect.getsource(ExecutionCoordinator._aplicar_fills_remotos)
        assert 'fill.get("fee_currency")' in fonte, (
            "a reconciliação descarta a moeda que o adaptador devolveu")

    def test_a_remote_fill_in_another_currency_opens_the_incident(self):
        from tradingagents.desk.execution import ExecutionCoordinator, OrderState
        m, risco, livro = sistema("10000")
        coord = ExecutionCoordinator(m, livro, risco, worker_id="w")
        coord.orders["ord-1"] = coord._montar_registro(
            order_id="ord-1", state=OrderState.ACKNOWLEDGED,
            instrument="BTC/USDT", side="buy", quantity=Decimal("1"),
            client_order_id="coid-1", agent_id="A", external_id="ex-1")
        fills = [{"fill_id": "t-1", "quantity": Decimal("1"),
                  "price": Decimal("100"), "fee": Decimal("0.01"),
                  "fee_currency": "BNB", "side": "buy"}]
        coord.reconciliar(
            lambda coid: {"resultado": "FOUND", "ordem": {
                "id": "ex-1", "status": "filled", "filled": "1",
                "average": "100", "fee": "0.01"}},
            consultar_posicoes=lambda: {"BTC/USDT": Decimal("1")},
            consultar_ordens_abertas=lambda: [],
            consultar_fills=lambda coid, exid="": fills)
        assert [i for i in risco.incidentes_abertos()
                if i["type"] == "FEE_VALUATION_UNKNOWN"], (
            "taxa em BNB vinda da corretora passou como se fosse cotação")


class TestContaDeConversao:
    def test_the_conversion_account_is_visible(self):
        """A conta de conversão vive na escrituração FUNCIONAL: ela é a
        contrapartida da despesa em cotação, não um balde que absorve a
        diferença entre 2 BNB e 600 USDT."""
        m, risco, livro = sistema()
        taxa_em(livro, "BNB", "2", "a")
        livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00")
        assert livro.balance("FX:CONVERSION_FUNCIONAL") != Decimal("0")
        assert livro.is_balanced()

    def test_the_risk_engine_can_read_it(self):
        m, risco, livro = sistema()
        taxa_em(livro, "BNB", "2", "a")
        livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="x",
                            em="2026-09-14T23:00:00+00:00")
        assert risco.conversao_acumulada() == livro.balance(
            "FX:CONVERSION_FUNCIONAL"), (
            "a diferença de conversão não é consultável pelo risco")

    def test_it_is_not_silently_treated_as_profit(self):
        m, risco, livro = sistema()
        taxa_em(livro, "BNB", "2", "a")
        livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="x",
                            em="2026-09-14T23:00:00+00:00")
        assert all(v == Decimal("0") for v in livro.attribution().values()) \
            or not livro.attribution(), (
            "a diferença de conversão entrou como resultado de trader")


class TestReconhecidoComDescobertoVivo:
    """Um descoberto VIVO não se resolve: ele se RECONHECE.

    Resolver com o ativo ainda faltando encerrava o alarme com o problema
    de pé — e a mesa voltava a operar devendo o que não tem. O
    reconhecimento preserva a evidência e continua bloqueando.
    """

    def test_the_check_does_not_reopen_it(self):
        m, risco, livro = sistema()
        taxa_em(livro, "BNB", "0.5", "a")
        risco.conferir_ativos_descobertos()
        inc = descobertos(risco)[0]
        risco.reconhecer_incidente(
            inc["incident_id"], actor=Ator(),
            evidence="diferença aceita e registrada")
        assert livro.balance("ASSET:BNB") == Decimal("-0.5"), (
            "o cenário precisa manter o descoberto vivo")
        assert risco.execution_blockers(), "reconhecer não pode liberar"

        risco.conferir_ativos_descobertos()
        risco.conferir_ativos_descobertos()
        reconhecidos = [i for i in risco.incidentes()
                        if i["type"] == "UNCOVERED_ASSET_BALANCE"]
        assert len(reconhecidos) == 1, (
            "a varredura abriu outro incidente para o mesmo descoberto")
        assert reconhecidos[0]["status"] == "ACKNOWLEDGED_BLOCKING"

    def test_a_worse_shortfall_updates_the_acknowledged_amount(self):
        m, risco, livro = sistema()
        taxa_em(livro, "BNB", "0.5", "a")
        risco.conferir_ativos_descobertos()
        inc = descobertos(risco)[0]
        risco.reconhecer_incidente(inc["incident_id"], actor=Ator(),
                                   evidence="aceita")
        taxa_em(livro, "BNB", "0.3", "b")      # descoberto piorou
        risco.conferir_ativos_descobertos()
        vivos = [i for i in risco.incidentes()
                 if i["type"] == "UNCOVERED_ASSET_BALANCE"
                 and i["status"] in risco.ESTADOS_BLOQUEANTES]
        assert len(vivos) == 1, "a piora virou um segundo incidente"
        assert Decimal(vivos[0]["amount"]) == Decimal("0.8"), (
            "o valor não acompanhou a piora")
