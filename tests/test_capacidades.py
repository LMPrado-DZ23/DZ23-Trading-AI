"""Matriz de capacidades: o que a corretora PODE provar.

Sem isso, capacidade ausente aparecia como falha genérica no meio de uma
lista de divergências — indistinguível de uma divergência real. Uma
corretora que não consulta execuções não é o mesmo que uma corretora que
diverge, e tratar as duas igual esconde o limite estrutural.
"""
from __future__ import annotations

from decimal import Decimal

from tradingagents.desk.capabilities import Capacidade, detectar_capacidades


class ExchangeCompleta:
    id = "binance"
    urls = {"test": "https://testnet"}

    def fetch_open_orders(self, *a, **k):
        return []

    def fetch_order(self, oid, symbol=None):
        return {}

    def fetch_orders(self, *a, **k):
        return []

    def fetch_my_trades(self, *a, **k):
        return []

    def fetch_balance(self):
        return {"total": {}}


class ExchangeSemTrades(ExchangeCompleta):
    id = "exchange-limitada"
    fetch_my_trades = None

    def __getattribute__(self, nome):
        if nome == "fetch_my_trades":
            raise AttributeError(nome)
        return object.__getattribute__(self, nome)


class ExchangeSemBuscaDireta:
    id = "so-historico"
    urls = {}

    def fetch_orders(self, *a, **k):
        return []

    def fetch_balance(self):
        return {"total": {}}


class TestDeteccao:
    def test_full_exchange_has_every_capability(self):
        cap = detectar_capacidades(ExchangeCompleta())
        for c in Capacidade:
            assert cap.suporta(c), f"{c.value} deveria estar disponível"

    def test_missing_trades_is_detected(self):
        cap = detectar_capacidades(ExchangeSemTrades())
        assert cap.suporta(Capacidade.CONSULTAR_EXECUCOES) is False
        assert cap.suporta(Capacidade.CONSULTAR_ORDEM_POR_ID) is True

    def test_exchange_without_direct_lookup_cannot_prove_absence(self):
        cap = detectar_capacidades(ExchangeSemBuscaDireta())
        assert cap.suporta(Capacidade.CONSULTAR_ORDEM_POR_ID) is False
        assert cap.pode_provar_ausencia() is False, (
            "sem busca direta não se prova que uma ordem não existe")

    def test_capabilities_are_explicit_not_guessed_by_name(self):
        """A matriz responde por comportamento observado, não por lista fixa
        de nomes de corretora — que envelheceria em silêncio."""
        cap = detectar_capacidades(ExchangeCompleta())
        assert cap.exchange == "binance"
        assert isinstance(cap.detalhes(), dict)


class TestRelatorioDeclaraOLimite:
    def _coord(self, exchange):
        from tradingagents.desk.execution import ExecutionCoordinator
        from tradingagents.desk.ledger import Ledger
        from tradingagents.desk.models import DeskMandate, Mode
        from tradingagents.desk.risk import RiskEngine
        m = DeskMandate(mandate_id="m", owner="p", account="c", mode=Mode.PAPER,
                        instruments=("BTC/USDT",), capital=Decimal("1000"))
        coord = ExecutionCoordinator(m, Ledger(opening_cash=m.capital),
                                     RiskEngine(m), worker_id="w")
        coord.capacidades = detectar_capacidades(exchange)
        return coord

    def test_report_declares_what_could_be_verified(self):
        coord = self._coord(ExchangeCompleta())
        rel = coord.reconciliar(
            lambda coid: {"resultado": "CONFIRMED_NOT_FOUND", "ordem": None},
            consultar_posicoes=lambda: {}, consultar_ordens_abertas=lambda: [],
            consultar_fills=lambda coid, exid="": [])
        assert "capacidades" in rel, "relatório não declara o que pôde verificar"
        assert rel["capacidades"]["exchange"] == "binance"

    def test_missing_capability_is_named_not_a_generic_failure(self):
        coord = self._coord(ExchangeSemTrades())
        rel = coord.reconciliar(
            lambda coid: {"resultado": "CONFIRMED_NOT_FOUND", "ordem": None},
            consultar_posicoes=lambda: {}, consultar_ordens_abertas=lambda: [])
        assert any("CAPACIDADE_AUSENTE" in d for d in rel["limitacoes"]), (
            "limitação estrutural apareceu como divergência comum")

    def test_structural_limit_is_separate_from_divergence(self):
        coord = self._coord(ExchangeSemTrades())
        rel = coord.reconciliar(
            lambda coid: {"resultado": "CONFIRMED_NOT_FOUND", "ordem": None},
            consultar_posicoes=lambda: {}, consultar_ordens_abertas=lambda: [])
        assert "limitacoes" in rel and "divergencias" in rel
        assert rel["limitacoes"] != rel["divergencias"]

    def test_missing_capability_never_becomes_safe_when_it_matters(self):
        """Com ordem viva e sem como provar ausência, SAFE é impossível.

        (Sem nada a verificar, SAFE continua legítimo — a limitação é
        declarada, mas não há afirmação a sustentar.)"""
        coord = self._coord(ExchangeSemBuscaDireta())
        coord.exigir_reconciliacao("ordem incerta do processo anterior")
        coord.orders["ord-1"] = coord._montar_registro(
            order_id="ord-1",
            state=__import__("tradingagents.desk.execution", fromlist=["x"])
            .OrderState.ACKNOWLEDGED,
            instrument="BTC/USDT", side="buy", quantity=Decimal("1"),
            client_order_id="coid-1", agent_id="A")
        rel = coord.reconciliar(
            lambda coid: {"resultado": "UNKNOWN", "ordem": None, "motivo": "x"},
            consultar_posicoes=lambda: {}, consultar_ordens_abertas=lambda: [])
        assert rel["seguro"] is False

    def test_nothing_to_verify_can_still_be_safe(self):
        coord = self._coord(ExchangeSemBuscaDireta())
        rel = coord.reconciliar(
            lambda coid: {"resultado": "UNKNOWN", "ordem": None, "motivo": "x"},
            consultar_posicoes=lambda: {}, consultar_ordens_abertas=lambda: [])
        assert rel["seguro"] is True
        assert rel["limitacoes"], "a limitação precisa aparecer mesmo no SAFE"


class TestNivelDeConfiancaDeclarado:
    def test_present_but_unprobed_is_not_full_confidence(self):
        """Corrige uma premissa MINHA da rodada anterior: método que só
        existe não torna a corretora verificada."""
        cap = detectar_capacidades(ExchangeCompleta())
        assert cap.nivel_de_confianca() == "nao_verificado"

    def test_probing_upgrades_what_it_can_exercise(self):
        """A sondagem é só de LEITURA e sem efeito: consultas que exigem um
        identificador real (fetch_order) não podem ser exercitadas em vazio
        e permanecem PRESENTE. Isso é honesto — e é por isso que
        pode_provar_ausencia continua False até haver consulta de verdade."""
        from tradingagents.desk.capabilities import Capacidade, Status
        cap = detectar_capacidades(ExchangeCompleta(), sondar=True)
        assert cap.status(Capacidade.LISTAR_ORDENS_ABERTAS) is Status.SUPORTADA
        assert cap.status(Capacidade.CONSULTAR_POSICOES) is Status.SUPORTADA
        assert cap.status(Capacidade.CONSULTAR_ORDEM_POR_ID) is Status.PRESENTE
        assert cap.nivel_de_confianca() == "parcial"
        assert cap.pode_provar_ausencia() is False

    def test_partial_capabilities_report_partial_confidence(self):
        cap = detectar_capacidades(ExchangeSemTrades(), sondar=True)
        assert cap.nivel_de_confianca() == "parcial"

    def test_the_level_is_explained_in_words(self):
        cap = detectar_capacidades(ExchangeSemTrades())
        explicacao = cap.explicacao()
        assert "execuç" in explicacao.lower()
        assert isinstance(explicacao, str) and len(explicacao) > 20
