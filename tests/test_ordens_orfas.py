"""Ordens que existem SÓ na corretora.

A reconciliação conferia apenas o que o OMS local já conhecia. Uma ordem
aberta na corretora e desconhecida daqui — deixada por um processo antigo,
por outra ferramenta ou pelo próprio dono — é risco real na conta e passava
despercebida: sem posição e sem execução, nada a denunciava.
"""
from __future__ import annotations

from decimal import Decimal

from tradingagents.desk.execution import ExecutionCoordinator, OrderState
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import DeskMandate, Mode
from tradingagents.desk.risk import RiskEngine


def sistema():
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT",),
                    capital=Decimal("10000"))
    risco = RiskEngine(m)
    livro = Ledger(opening_cash=m.capital)
    return m, risco, livro, ExecutionCoordinator(m, livro, risco, worker_id="w")


def limpo(coord, **extra):
    base = {"consultar_posicoes": lambda: {},
            "consultar_fills": lambda coid, exid="": []}
    base.update(extra)
    return coord.reconciliar(
        lambda coid: {"resultado": "CONFIRMED_NOT_FOUND", "ordem": None}, **base)


class TestOrdemOrfaRemota:
    def test_an_order_only_on_the_exchange_blocks_safe(self):
        """Sem posição e sem execução: só a consulta de ordens abertas a
        encontra."""
        m, risco, livro, coord = sistema()
        rel = limpo(coord, consultar_ordens_abertas=lambda: [
            {"id": "ex-99", "symbol": "BTC/USDT", "side": "buy",
             "amount": Decimal("1"), "status": "open"}])
        assert rel["seguro"] is False
        assert any("ORDEM_ORFA_REMOTA" in d for d in rel["divergencias"])
        assert rel["ordens_status"] == "MISMATCH"

    def test_a_manual_order_without_our_client_id_still_counts(self):
        """Ordem criada pelo dono direto na corretora é risco na conta,
        mesmo sem identificador nosso."""
        m, risco, livro, coord = sistema()
        rel = limpo(coord, consultar_ordens_abertas=lambda: [
            {"id": "manual-1", "symbol": "BTC/USDT", "side": "sell",
             "amount": Decimal("0.5"), "status": "open"}])
        assert rel["seguro"] is False
        assert any("manual-1" in d for d in rel["divergencias"])

    def test_a_known_order_is_not_reported_as_orphan(self):
        m, risco, livro, coord = sistema()
        coord.orders["ord-1"] = coord._montar_registro(
            order_id="ord-1", state=OrderState.ACKNOWLEDGED,
            instrument="BTC/USDT", side="buy", quantity=Decimal("1"),
            client_order_id="coid-1", agent_id="A", external_id="ex-1")
        rel = coord.reconciliar(
            lambda coid: {"resultado": "FOUND", "ordem": {
                "id": "ex-1", "status": "canceled"}},
            consultar_posicoes=lambda: {},
            consultar_fills=lambda coid, exid="": [],
            consultar_ordens_abertas=lambda: [
                {"id": "ex-1", "clientOrderId": "coid-1", "symbol": "BTC/USDT",
                 "amount": Decimal("1"), "status": "open"}])
        assert not any("ORDEM_ORFA_REMOTA" in d for d in rel["divergencias"]), (
            "ordem conhecida foi tratada como órfã")

    def test_orphan_is_not_adopted_or_cancelled_silently(self):
        m, risco, livro, coord = sistema()
        limpo(coord, consultar_ordens_abertas=lambda: [
            {"id": "ex-99", "symbol": "BTC/USDT", "amount": Decimal("1"),
             "status": "open"}])
        assert "ex-99" not in {r.external_id for r in coord.orders.values()}, (
            "a ordem órfã foi adotada em silêncio")
        assert coord.reconciliacao_pendente is True


class TestVazioLocalNaoEProva:
    def test_without_querying_open_orders_the_status_is_unknown(self):
        """'Não tenho ordens' não prova 'não há ordens' — a prova está do
        lado da corretora."""
        m, risco, livro, coord = sistema()
        rel = limpo(coord)          # sem consultar_ordens_abertas
        assert rel["ordens_status"] == "UNKNOWN"
        assert rel["seguro"] is False

    def test_confirmed_empty_on_both_sides_can_be_safe(self):
        m, risco, livro, coord = sistema()
        rel = limpo(coord, consultar_ordens_abertas=lambda: [])
        assert rel["ordens_status"] == "MATCHED"
        assert rel["seguro"] is True, rel["divergencias"]

    def test_a_failing_query_is_unknown_not_empty(self):
        m, risco, livro, coord = sistema()

        def explode():
            raise RuntimeError("exchange fora do ar")

        rel = limpo(coord, consultar_ordens_abertas=explode)
        assert rel["ordens_status"] == "UNKNOWN" and rel["seguro"] is False


class TestCapacidadeReal:
    """`callable()` prova que o método EXISTE, não que funciona."""

    def test_a_method_raising_notsupported_is_not_supported(self):
        from tradingagents.desk.capabilities import Capacidade, Status, detectar_capacidades

        class NotSupported(Exception):
            pass

        class Exchange:
            id = "limitada"

            def fetch_order(self, *a, **k):
                raise NotSupported("not supported by this exchange")

            def fetch_orders(self, *a, **k):
                return []

            def fetch_open_orders(self, *a, **k):
                return []

            def fetch_my_trades(self, *a, **k):
                return []

            def fetch_balance(self):
                return {"total": {}}

        cap = detectar_capacidades(Exchange(), sondar=True)
        assert cap.status(Capacidade.CONSULTAR_ORDEM_POR_ID) is Status.NAO_SUPORTADA
        assert cap.pode_provar_ausencia() is False

    def test_a_transient_failure_is_unknown_not_unsupported(self):
        from tradingagents.desk.capabilities import Capacidade, Status, detectar_capacidades

        class Exchange:
            id = "instavel"

            def fetch_order(self, *a, **k):
                raise TimeoutError("rede caiu")

            def fetch_balance(self):
                return {"total": {}}

        cap = detectar_capacidades(Exchange(), sondar=True)
        assert cap.status(Capacidade.CONSULTAR_ORDEM_POR_ID) is Status.DESCONHECIDA, (
            "falha transitória virou conclusão sobre a corretora")

    def test_without_probing_the_status_is_present_not_supported(self):
        from tradingagents.desk.capabilities import Capacidade, Status, detectar_capacidades

        class Exchange:
            id = "x"

            def fetch_order(self, *a, **k):
                return {}

        cap = detectar_capacidades(Exchange())      # sem sondagem
        assert cap.status(Capacidade.CONSULTAR_ORDEM_POR_ID) is Status.PRESENTE

    def test_listing_open_orders_is_its_own_capability(self):
        from tradingagents.desk.capabilities import Capacidade
        assert Capacidade.LISTAR_ORDENS_ABERTAS


class TestPresenteNaoEProva:
    """`PRESENTE` significa 'o método existe'. Tratar isso como capacidade
    comprovada faz uma corretora nunca exercitada parecer plenamente
    verificada — e a reconciliação se apoiar em algo que pode estourar."""

    class Completa:
        id = "tudo-presente"

        def fetch_order(self, *a, **k):
            return {}

        def fetch_orders(self, *a, **k):
            return []

        def fetch_open_orders(self, *a, **k):
            return []

        def fetch_my_trades(self, *a, **k):
            return []

        def fetch_balance(self):
            return {"total": {}}

    def test_without_probing_nothing_is_supported(self):
        from tradingagents.desk.capabilities import Capacidade, Status, detectar_capacidades
        cap = detectar_capacidades(self.Completa())
        for c in Capacidade:
            assert cap.status(c) is not Status.SUPORTADA, (
                f"{c.value} foi dada como comprovada sem ter sido exercitada")
            assert cap.status(c) is Status.PRESENTE

    def test_all_present_is_not_full_confidence(self):
        from tradingagents.desk.capabilities import detectar_capacidades
        cap = detectar_capacidades(self.Completa())
        assert cap.nivel_de_confianca() != "completo"
        assert cap.nivel_de_confianca() == "nao_verificado"

    def test_probing_upgrades_what_it_can_exercise(self):
        from tradingagents.desk.capabilities import Capacidade as C, Status, detectar_capacidades
        cap = detectar_capacidades(self.Completa(), sondar=True)
        # esta corretora falsa aceita fetch_order() sem argumentos
        assert cap.status(C.CONSULTAR_ORDEM_POR_ID) is Status.SUPORTADA
        assert cap.nivel_de_confianca() == "completo"

    def test_proving_absence_requires_a_supported_lookup(self):
        from tradingagents.desk.capabilities import detectar_capacidades
        presente = detectar_capacidades(self.Completa())
        assert presente.pode_provar_ausencia() is False, (
            "método apenas presente não prova que uma ordem não existe")
        sondada = detectar_capacidades(self.Completa(), sondar=True)
        assert sondada.pode_provar_ausencia() is True


class TestIdentidadeCanonicaDaOrdem:
    """Adaptador e reconciliação precisam ler identidade da MESMA forma:
    listas divergentes fazem uma ordem conhecida virar órfã só por diferença
    de representação entre corretoras."""

    def test_okx_style_client_id_is_recognised(self):
        from tradingagents.desk.identity import order_identities
        ids = order_identities({"id": "ex-1", "clientOrderId": None,
                                "info": {"clOrdId": "dz23-X"}})
        assert "dz23-X" in ids and "ex-1" in ids

    def test_orig_client_order_id_is_recognised(self):
        from tradingagents.desk.identity import order_identities
        ids = order_identities({"id": "ex-2",
                                "info": {"origClientOrderId": "dz23-Y"}})
        assert "dz23-Y" in ids

    def test_empty_values_are_not_identities(self):
        from tradingagents.desk.identity import order_identities
        ids = order_identities({"id": None, "clientOrderId": "",
                                "info": {"clOrdId": None}})
        assert ids == set()

    def test_a_known_okx_order_is_not_an_orphan(self):
        m, risco, livro, coord = sistema()
        coord.orders["ord-1"] = coord._montar_registro(
            order_id="ord-1", state=OrderState.ACKNOWLEDGED,
            instrument="BTC/USDT", side="buy", quantity=Decimal("1"),
            client_order_id="dz23-X", agent_id="A")
        rel = coord.reconciliar(
            lambda coid: {"resultado": "FOUND", "ordem": {"id": "ex-1",
                                                          "status": "canceled"}},
            consultar_posicoes=lambda: {},
            consultar_fills=lambda coid, exid="": [],
            consultar_ordens_abertas=lambda: [
                {"id": "ex-1", "clientOrderId": None,
                 "info": {"clOrdId": "dz23-X"}, "symbol": "BTC/USDT",
                 "amount": Decimal("1"), "status": "open"}])
        assert not any("ORDEM_ORFA_REMOTA" in d for d in rel["divergencias"]), (
            "ordem conhecida virou órfã por diferença de representação")

    def test_both_layers_use_the_same_function(self):
        import inspect

        from tradingagents.desk import brokers, execution
        assert "order_identities" in inspect.getsource(execution)
        assert "order_identities" in inspect.getsource(brokers)
