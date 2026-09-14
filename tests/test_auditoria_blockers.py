"""Regressões dos achados da auditoria independente.

Cada teste aqui nasceu de um defeito REAL encontrado por um auditor externo
— e todos existiam porque os testes anteriores provavam componentes
isolados, nunca as junções entre eles.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.brokers import BrokerAdapterError, _pode_ter_chegado
from tradingagents.desk.execution import BrokerTimeout, ExecutionCoordinator, OrderState
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import Action, DeskMandate, Mode, OrderIntent, utcnow
from tradingagents.desk.risk import RiskEngine


def _traduzir_como_o_adaptador(exc: Exception):
    """Reproduz a tradução que CCXTDeskBroker.submit faz, sem exigir ccxt
    instalado: é a mesma decisão, tomada pela mesma função do produto."""
    if _pode_ter_chegado(exc):
        return BrokerTimeout(f"envio ambíguo ({type(exc).__name__})")
    return BrokerAdapterError(f"{type(exc).__name__}: {exc}")


def mandato(capital="1000"):
    return DeskMandate(mandate_id="m", owner="p", account="a", mode=Mode.PAPER,
                       instruments=("BTC/USDT",), capital=Decimal(capital))


class CorretoraQueExplode:
    """Dublê que falha do jeito que a rede real falha."""

    def __init__(self, excecao):
        self.excecao = excecao
        self.enviadas = []
        self.mode = "testnet"

    def submit(self, coid, instrument, side, quantity):
        self.enviadas.append(coid)
        raise self.excecao


class CorretoraOk:
    def __init__(self):
        self.enviadas = []
        self.mode = "testnet"

    def submit(self, coid, instrument, side, quantity):
        # Sem "average": o coordenador usa o preço informado na saída, que é
        # o que interessa para medir o resultado realizado.
        self.enviadas.append(coid)
        return {"id": f"ex-{coid}", "status": "closed", "filled": quantity,
                "fee": Decimal("0")}


def coordenador(broker):
    m = mandato()
    risco = RiskEngine(m)
    return ExecutionCoordinator(m, Ledger(opening_cash=m.capital), risco,
                                worker_id="w", broker=broker), risco


def intencao(risco, quantidade="1", preco="100"):
    """Constrói a intenção pelo MESMO caminho que a mesa usa em produção —
    proposta real, reserva real. Atalho aqui esconderia o defeito."""
    from datetime import timedelta

    from tradingagents.desk.models import TradeProposal
    proposta = TradeProposal(
        agent_id="t", strategy="s", instrument="BTC/USDT", action=Action.BUY,
        reason="teste", desired_exposure_pct=Decimal("0.10"),
        confidence=Decimal("0.7"), valid_until=utcnow() + timedelta(minutes=5))
    reserva = risco.reserve(proposta, Decimal(preco), agent_id="t")
    return OrderIntent(intent_id="i1", reservation=reserva, action=Action.BUY,
                       mode=Mode.PAPER, owner_worker="w",
                       expires_at=utcnow() + timedelta(minutes=5))


class TestTimeoutAmbiguoNaoLiberaCapital:
    """ACHADO 2: toda exceção do adaptador virava REJEITADA e liberava a
    reserva. Se a exchange tiver recebido a ordem, isso cria posição órfã e
    promete o mesmo capital duas vezes."""

    @pytest.mark.parametrize("nome", [
        "RequestTimeout", "ReadTimeout", "ConnectionResetError",
        "NetworkError", "ExchangeNotAvailable", "ErroQueAindaNaoExiste"])
    def test_ambiguous_errors_keep_the_reservation(self, nome):
        """Exercita a CLASSIFICAÇÃO real: a exceção crua da biblioteca passa
        pelo adaptador, que decide se é ambígua. Injetar BrokerTimeout
        diretamente provaria apenas que o executor trata BrokerTimeout."""
        excecao = type(nome, (Exception,), {})("falha de rede")
        broker = CorretoraQueExplode(_traduzir_como_o_adaptador(excecao))
        coord, risco = coordenador(broker)
        intent = intencao(risco)
        reservado_antes = risco.reserved_total      # já com a reserva feita
        assert reservado_antes > 0
        rec = coord.submit(intent)
        assert rec.state is OrderState.UNKNOWN_RECONCILIATION_REQUIRED
        assert risco.reserved_total == reservado_antes, (
            "a reserva foi liberada sobre uma ordem que pode existir")

    def test_unknown_exception_class_is_treated_as_ambiguous(self):
        """Fail-closed: erro novo de biblioteca não pode ser 'não chegou'."""
        class ErroNovoDaLibDeRede(Exception):
            pass
        assert _pode_ter_chegado(ErroNovoDaLibDeRede()) is True

    def test_errors_that_prove_no_order_are_still_rejections(self):
        class AuthenticationError(Exception):
            pass

        class InsufficientFunds(Exception):
            pass
        assert _pode_ter_chegado(AuthenticationError()) is False
        assert _pode_ter_chegado(InsufficientFunds()) is False

    def test_rejection_releases_capital_as_it_should(self):
        class InvalidOrder(Exception):
            pass
        broker = CorretoraQueExplode(_traduzir_como_o_adaptador(InvalidOrder("símbolo")))
        coord, risco = coordenador(broker)
        rec = coord.submit(intencao(risco))
        assert rec.state is OrderState.REJECTED
        assert risco.reserved_total == Decimal("0")


class TestFreioDiarioVemDoLivro:
    """ACHADO 1: existia um limite de perda diária que a operação real nunca
    atualizava. O teste antigo chamava register_loss à mão e 'passava'."""

    def _fechar_com_prejuizo(self, coord, prejuizo="50"):
        return coord.close_position(
            instrument="BTC/USDT", quantity=Decimal("1"),
            price=Decimal("100") - Decimal(prejuizo),
            cost_basis=Decimal("100"), agent_id="t")

    def test_closing_at_a_loss_feeds_the_daily_brake(self):
        coord, _ = coordenador(CorretoraOk())
        assert coord.perda_do_dia == Decimal("0")
        self._fechar_com_prejuizo(coord, "30")
        assert coord.perda_do_dia == Decimal("30"), (
            "o resultado realizado não alimentou o freio diário")

    def test_losses_accumulate(self):
        coord, _ = coordenador(CorretoraOk())
        self._fechar_com_prejuizo(coord, "30")
        self._fechar_com_prejuizo(coord, "20")
        assert coord.perda_do_dia == Decimal("50")

    def test_profit_reduces_the_accumulated_loss(self):
        coord, _ = coordenador(CorretoraOk())
        self._fechar_com_prejuizo(coord, "30")
        coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                             price=Decimal("120"), cost_basis=Decimal("100"),
                             agent_id="t")
        assert coord.perda_do_dia == Decimal("10")

    def test_daily_state_survives_a_restart_within_the_same_day(self):
        coord, _ = coordenador(CorretoraOk())
        self._fechar_com_prejuizo(coord, "40")
        doc = coord.estado_do_dia()
        novo, _ = coordenador(CorretoraOk())      # "processo reiniciou"
        novo.restaurar_estado_do_dia(doc)
        assert novo.perda_do_dia == Decimal("40"), (
            "reiniciar o processo zerava a perda do dia")

    def test_yesterday_loss_does_not_block_today(self):
        coord, _ = coordenador(CorretoraOk())
        novo, _ = coordenador(CorretoraOk())
        novo.restaurar_estado_do_dia({"dia": "2020-01-01", "perda_do_dia": "999"})
        assert novo.perda_do_dia == Decimal("0")

    def test_the_bridge_receives_the_value_from_the_ledger(self):
        """A ponte não pode depender de alguém lembrar de chamá-la."""
        class PonteFalsa:
            def __init__(self):
                self.recebido = None

            def set_daily_loss(self, v):
                self.recebido = v
                return v

        broker = CorretoraOk()
        broker.bridge = PonteFalsa()
        coord, _ = coordenador(broker)
        self._fechar_com_prejuizo(coord, "25")
        assert broker.bridge.recebido == Decimal("25")


class TestProcedenciaRealChegaNaCorretora:
    """ACHADO 3: a mesa construía o broker com final_state={} e o gate de
    capital recusava TODA ordem. O caminho de produção não chegava à
    corretora — e os testes passavam porque injetavam um estado artificial.
    """

    def test_empty_state_is_still_refused_by_the_gate(self):
        """O gate não foi afrouxado — continua recusando estado vazio."""
        from tradingagents.gates import InsecureTransportError, assert_secure_for_capital
        with pytest.raises(InsecureTransportError):
            assert_secure_for_capital({})

    def test_desk_builds_provenance_that_the_gate_accepts(self):
        """O caminho REAL: snapshot → decisão → procedência → gate."""
        from tradingagents.desk.models import MarketSnapshot
        from tradingagents.desk.provenance import build_state, validar
        serie = tuple(Decimal(str(round(100 * (1.01 ** i), 6))) for i in range(60))
        snap = MarketSnapshot("BTC/USDT", "1h", utcnow(), serie)
        estado = build_state(instrument=snap.instrument, cutoff=snap.cutoff,
                             source="fixture", strategy="trend_following",
                             agent_id="trader-trend", worker_id="w")
        validar(estado)          # passa pelo MESMO gate da corretora

    def test_provenance_without_the_originating_data_is_refused(self):
        from tradingagents.desk.provenance import ProcedenciaInvalida, build_state
        with pytest.raises(ProcedenciaInvalida, match="vela de corte"):
            build_state(instrument="BTC/USDT", cutoff=None, source="fixture",
                        strategy="s", agent_id="a")

    def test_insecure_source_is_refused_before_any_send(self):
        from tradingagents.desk.provenance import ProcedenciaInvalida, build_state
        with pytest.raises(ProcedenciaInvalida, match="transporte seguro"):
            build_state(instrument="BTC/USDT", cutoff=utcnow(),
                        source="http://feed-inseguro", strategy="s", agent_id="a")

    def test_cycle_attaches_provenance_to_every_intent(self):
        from tradingagents.desk.models import AgentStage, MarketSnapshot
        from tradingagents.desk.orchestrator import DeskOrchestrator
        from tradingagents.desk.traders import default_desk
        m = mandato()
        traders = default_desk(("BTC/USDT",))
        for t in traders:
            for st in (AgentStage.VALIDATED, AgentStage.SHADOW, AgentStage.PAPER_ENABLED):
                t.spec.move_to(st)
        orch = DeskOrchestrator(m, traders, worker_id="w", risk=RiskEngine(m))
        serie = tuple(Decimal(str(round(100 * (1.01 ** i), 6))) for i in range(60))
        rep = orch.run_cycle([MarketSnapshot("BTC/USDT", "1h", utcnow(), serie)],
                             {"BTC/USDT": Decimal("150")})
        assert rep.intents, "o ciclo não produziu intenção"
        for intent in rep.intents:
            assert intent.provenance, "intenção sem procedência chegaria recusada"
            from tradingagents.gates import assert_secure_for_capital
            assert_secure_for_capital(intent.provenance)   # a corretora aceitaria

    def test_the_order_reaches_the_broker_with_its_provenance(self):
        """Prova a JUNÇÃO, não o componente: o que o executor manda à
        corretora é o estado que o gate aprova."""
        recebido = {}

        class CorretoraQueInspeciona:
            mode = "testnet"

            def submit(self, coid, instrument, side, quantity, final_state=None):
                recebido["estado"] = final_state
                return {"id": "ex-1", "status": "filled", "filled": quantity,
                        "average": Decimal("100"), "fee": Decimal("0")}

        from tradingagents.desk.models import AgentStage, MarketSnapshot
        from tradingagents.desk.orchestrator import DeskOrchestrator
        from tradingagents.desk.traders import default_desk
        m = mandato()
        risco = RiskEngine(m)
        traders = default_desk(("BTC/USDT",))
        for t in traders:
            for st in (AgentStage.VALIDATED, AgentStage.SHADOW, AgentStage.PAPER_ENABLED):
                t.spec.move_to(st)
        orch = DeskOrchestrator(m, traders, worker_id="w", risk=risco)
        serie = tuple(Decimal(str(round(100 * (1.01 ** i), 6))) for i in range(60))
        rep = orch.run_cycle([MarketSnapshot("BTC/USDT", "1h", utcnow(), serie)],
                             {"BTC/USDT": Decimal("100")})
        coord = ExecutionCoordinator(m, Ledger(opening_cash=m.capital), risco,
                                     worker_id="w", broker=CorretoraQueInspeciona())
        coord.submit(rep.intents[0])
        assert recebido["estado"], "a ordem chegou à corretora SEM procedência"
        from tradingagents.gates import assert_secure_for_capital
        assert_secure_for_capital(recebido["estado"])

    def test_paper_and_real_brokers_share_the_same_contract(self):
        """Interface diferente entre paper e real é como o caminho de
        produção deixa de ser exercitado pelos testes."""
        import inspect

        from tradingagents.desk.brokers import CCXTDeskBroker
        from tradingagents.desk.execution import PaperBroker
        assert (set(inspect.signature(PaperBroker.submit).parameters) ==
                set(inspect.signature(CCXTDeskBroker.submit).parameters))


class TestSegredoNaoVazaPelaApi:
    """ACHADO D: a classificação de segredo era por sufixo do nome, então o
    token do Telegram e a URL de webhook voltavam em texto claro pela API."""

    def _app(self, tmp_path, monkeypatch):
        import importlib

        from fastapi.testclient import TestClient
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        return m, TestClient(m.app), {"Authorization": "Bearer s3nh4"}

    def test_telegram_token_never_returns_in_cleartext(self, tmp_path, monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        segredo = "123456:AAH-token-super-secreto-do-bot"
        c.post("/v1/config", headers=h,
               json={"vars": {"ALERTA_TELEGRAM_TOKEN": segredo}})
        resposta = c.get("/v1/config", headers=h)
        assert segredo not in resposta.text
        assert resposta.json()["config"]["ALERTA_TELEGRAM_TOKEN"]["set"] is True

    def test_webhook_url_is_treated_as_secret(self, tmp_path, monkeypatch):
        """URL de webhook costuma embutir credencial no caminho."""
        m, c, h = self._app(tmp_path, monkeypatch)
        url = "https://hooks.exemplo.com/T0000/B0000/token-secreto-no-path"
        c.post("/v1/config", headers=h, json={"vars": {"ALERTA_WEBHOOK_URL": url}})
        assert "token-secreto-no-path" not in c.get("/v1/config", headers=h).text

    def test_secret_classification_is_declared_not_guessed(self):
        from service.app import SECRETish
        for chave in ("ALERTA_TELEGRAM_TOKEN", "ALERTA_WEBHOOK_URL",
                      "EXCHANGE_API_SECRET", "GROQ_API_KEY"):
            assert chave in SECRETish, f"{chave} não está classificada como segredo"

    def test_non_secret_config_is_still_visible(self):
        """Não basta esconder tudo: o dono precisa ver a configuração comum."""
        from service.app import SECRETish
        assert "TRADINGAGENTS_EXCHANGE" not in SECRETish
        assert "TRADINGAGENTS_DESK_CAPITAL" not in SECRETish
