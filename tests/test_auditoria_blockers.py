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
    """Corretora que executa ao preço configurado e devolve a execução com
    identidade própria — o preço de execução vem da CORRETORA, não do
    parâmetro local."""

    def __init__(self, price=Decimal("100")):
        self.enviadas = []
        self.mode = "testnet"
        self.price = price

    def submit(self, coid, instrument, side, quantity, final_state=None):
        self.enviadas.append(coid)
        return {"id": f"ex-{coid}", "status": "closed", "filled": quantity,
                "average": self.price, "fee": Decimal("0"),
                "trades": [{"id": f"t-{coid}", "amount": quantity,
                            "price": self.price, "fee": {"cost": Decimal("0")}}]}


def coordenador(broker, com_posicao="1"):
    """Coordenador com posição REAL no livro quando pedido.

    Saída agora exige posição conhecida — vender sem ela seria venda a
    descoberto. Os cenários de freio diário precisam da compra antes.
    """
    m = mandato()
    risco = RiskEngine(m)
    livro = Ledger(opening_cash=m.capital)
    if com_posicao:
        livro.reserve(Decimal("100"), ref="r0")
        livro.fill_buy("BTC/USDT", Decimal(com_posicao), Decimal("100"),
                       Decimal("0"), reserved=Decimal("100"), ref="o0",
                       agent_id="t")
    return ExecutionCoordinator(m, livro, risco, worker_id="w",
                                broker=broker), risco


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
        coord.broker.price = Decimal("100") - Decimal(prejuizo)
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
        coord, _ = coordenador(CorretoraOk(), com_posicao="2")
        self._fechar_com_prejuizo(coord, "30")
        self._fechar_com_prejuizo(coord, "20")
        assert coord.perda_do_dia == Decimal("50")

    def test_profit_reduces_the_accumulated_loss(self):
        coord, _ = coordenador(CorretoraOk(), com_posicao="2")
        self._fechar_com_prejuizo(coord, "30")
        coord.broker.price = Decimal("120")
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
        risco = RiskEngine(m)
        traders = default_desk(("BTC/USDT",))
        for t in traders:
            for st in (AgentStage.VALIDATED, AgentStage.SHADOW, AgentStage.PAPER_ENABLED):
                t.spec.move_to(st)
        orch = DeskOrchestrator(m, traders, worker_id="w", risk=risco)
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


class TestParadaDeEmergenciaSobreviveAoReinicio:
    """ACHADO: o kill switch vivia em RAM. Um deploy, crash ou reboot o
    desarmava sozinho enquanto o dono acreditava ter parado tudo."""

    def _armazem(self, tmp_path):
        from tradingagents.desk.storage import ArmazemDeEstado
        return ArmazemDeEstado(None, diretorio=tmp_path / "estado")

    def test_engaged_switch_survives_a_restart(self, tmp_path):
        from tradingagents.desk.killswitch import KillSwitch
        a = self._armazem(tmp_path)
        KillSwitch(a, "t1").acionar("pânico do dono")
        assert KillSwitch(a, "t1").bloqueia is True, (
            "reiniciar o processo desarmou a parada de emergência")

    def test_unreadable_state_blocks_instead_of_releasing(self, tmp_path):
        """Fail-closed: não saber se está parado equivale a estar parado."""
        from tradingagents.desk.killswitch import EstadoParada
        assert EstadoParada.from_doc({"formato": "estranho"}).bloqueia is True
        assert EstadoParada.from_doc({"schema": "dz23.killswitch/1",
                                      "estado": "sei_la"}).bloqueia is True

    def test_release_requires_phrase_and_reconciliation(self, tmp_path):
        from tradingagents.desk.killswitch import KillSwitch, KillSwitchError
        k = KillSwitch(self._armazem(tmp_path), "t1")
        k.acionar("teste")
        with pytest.raises(KillSwitchError, match="autorização"):
            k.liberar(frase_correta=False, reconciliado=True)
        with pytest.raises(KillSwitchError, match="reconcilie"):
            k.liberar(frase_correta=True, reconciliado=False)
        k.liberar(frase_correta=True, reconciliado=True)
        assert k.bloqueia is False

    def test_tenants_have_independent_switches(self, tmp_path):
        from tradingagents.desk.killswitch import KillSwitch
        a = self._armazem(tmp_path)
        KillSwitch(a, "t1").acionar("parada do t1")
        assert KillSwitch(a, "t2").bloqueia is False


class TestEstadoFinanceiroSobreviveAoReinicio:
    """ACHADO: reiniciar recriava orders={} e Ledger zerado enquanto a
    exchange continuava com posição aberta — e o sistema se achava livre
    para comprar de novo."""

    def _coord(self):
        from tradingagents.desk.execution import PaperBroker
        m = mandato()
        return ExecutionCoordinator(m, Ledger(opening_cash=m.capital),
                                    RiskEngine(m), worker_id="w",
                                    broker=PaperBroker(Decimal("100")))

    def test_snapshot_carries_the_financial_truth(self):
        coord = self._coord()
        doc = coord.snapshot_estado()
        for campo in ("ordens", "posicoes", "caixa", "perda_do_dia", "reservas"):
            assert campo in doc, f"o estado não persiste {campo}"

    def test_restored_state_blocks_new_risk_until_reconciled(self):
        """UNKNOWN => BLOCK NEW RISK. Nunca UNKNOWN => 'deve estar tudo bem'."""
        from tradingagents.desk.execution import ExecutionRefused
        coord = self._coord()
        info = coord.restaurar_estado({
            "schema": "dz23.oms/1", "dia": "", "perda_do_dia": "0",
            "ordens": [{"order_id": "ord-7",
                        "estado": "unknown_reconciliation_required"}],
            "posicoes": {}, "caixa": "1000", "reservas": []})
        assert info["exige_reconciliacao"] is True
        assert "ord-7" in info["pendencias"]
        # intenção VÁLIDA: assim a recusa prova o PORTÃO, não uma validação
        # de tipo — usar object() como atalho escondia qual regra atuava
        from decimal import Decimal as _D

        from tradingagents.desk.intents import ExecutionIntent as _EI, Kind, Side
        ei = _EI(kind=Kind.ENTRY, side=Side.BUY, instrument="BTC/USDT",
                 quantity=_D("1"), agent_id="t", account="a")
        with pytest.raises(ExecutionRefused, match="reconciliação pendente"):
            coord.submit_intent(ei)

    def test_open_position_from_previous_run_also_blocks(self):
        coord = self._coord()
        info = coord.restaurar_estado({
            "schema": "dz23.oms/1", "ordens": [], "caixa": "1000",
            "posicoes": {"BTC/USDT": {"quantidade": "0.1", "custo": "100",
                                      "preco_medio": "1000", "agente": "t"}},
            "reservas": []})
        assert info["exige_reconciliacao"] is True
        assert coord.reconciliacao_pendente is True

    def test_clean_state_does_not_block(self):
        coord = self._coord()
        info = coord.restaurar_estado({"schema": "dz23.oms/1", "ordens": [],
                                       "posicoes": {}, "caixa": "1000",
                                       "reservas": []})
        assert info["exige_reconciliacao"] is False
        assert coord.reconciliacao_pendente is False

    def test_only_reconciliation_can_release_the_gate(self):
        coord = self._coord()
        coord.exigir_reconciliacao("teste")
        assert coord.reconciliacao_pendente is True
        coord.reconciliacao_concluida()
        assert coord.reconciliacao_pendente is False


class TestNomesDeEstadoExistem:
    """Regressão de um erro meu: o código citava OrderState.SUBMITTED, que não
    existe — e só aparecia em tempo de execução, virando 503 na mesa."""

    def test_every_referenced_state_exists(self):
        import re
        from pathlib import Path

        from tradingagents.desk.execution import OrderState
        nomes = {e.name for e in OrderState}
        for arquivo in Path("tradingagents").rglob("*.py"):
            citados = set(re.findall(r"OrderState\.([A-Z_]+)",
                                     arquivo.read_text(encoding="utf-8")))
            assert not (citados - nomes), (
                f"{arquivo}: estados inexistentes {citados - nomes}")


class TestCredencialDeInquilinoNaoVazaPorAmbiente:
    """ACHADO: a mesa escrevia a credencial do inquilino em os.environ sem
    trava. Duas mesas concorrentes podiam trocar de chave entre si."""

    def test_desk_llm_call_is_not_an_unlocked_env_mutation(self):
        import inspect

        import service.app as m
        fonte = inspect.getsource(m._desk_boot)
        assert "with _lock:" in fonte, (
            "a consulta de IA da mesa mutila o ambiente global sem trava")
        assert "api_key=chave" in fonte, (
            "o caminho preferido deve passar a credencial explicitamente")

    def test_job_path_still_holds_the_lock(self):
        import inspect

        import service.app as m
        assert "with _lock:" in inspect.getsource(m.run_job)

    def test_no_env_mutation_escapes_without_restore(self):
        """Toda escrita em os.environ no serviço precisa ter restauração."""
        from pathlib import Path
        fonte = Path("service/app.py").read_text(encoding="utf-8")
        escritas = fonte.count("os.environ.update(")
        restauracoes = fonte.count("os.environ.pop(")
        assert restauracoes >= escritas, (
            f"{escritas} escritas no ambiente e só {restauracoes} restaurações")


class TestBacktestMedeOQueOperaDeVerdade:
    """ACHADO E: produção saía em -5%/+8% e o backtest simulava -alvo/2 e
    +alvo. O relatório media uma estratégia que não era a operada."""

    def test_backtest_and_production_share_the_same_policy_object(self):
        import inspect

        from tradingagents.desk.traders import default_desk
        from tradingagents.desk.validation import validar as _validar_backtest
        fonte = inspect.getsource(_validar_backtest)
        assert "trader.exit_policy" in fonte, (
            "o backtest precisa consumir a política da estratégia, não uma cópia")
        for trader in default_desk(("BTC/USDT",)):
            assert trader.exit_policy.alvo == trader.movimento_alvo

    def test_production_exit_uses_the_policy_too(self):
        import inspect

        from tradingagents.desk.traders import BaseTrader
        fonte = inspect.getsource(BaseTrader.manage)
        assert "self.exit_policy.avaliar" in fonte
        assert '"-0.05"' not in fonte and '"0.08"' not in fonte, (
            "número de saída fixo na produção é o que divergia do backtest")

    def test_payoff_below_one_is_a_house_policy_not_a_math_law(self):
        """Correção de um exagero MEU: payoff 1:2 não é matematicamente
        inválido — é lucrativo com acerto suficiente (~67% sem custos). A
        recusa é decisão de produto, e quem quiser assumir o perfil declara."""
        from tradingagents.desk.policy import ExitPolicy
        with pytest.raises(ValueError, match="política da casa"):
            ExitPolicy(alvo=Decimal("0.02"), stop=Decimal("-0.05"))

        # alvo 2% / stop 5% => payoff 0,4 => empate em 0,05/0,07 = 71,4%
        arriscada = ExitPolicy(alvo=Decimal("0.02"), stop=Decimal("-0.05"),
                               exigir_payoff_minimo=False)
        acerto = arriscada.acerto_minimo()
        assert abs(acerto - Decimal("5") / Decimal("7")) < Decimal("0.0001"), acerto
        assert arriscada.expectativa(Decimal("0.80")) > 0   # acima do empate
        assert arriscada.expectativa(Decimal("0.60")) < 0   # abaixo do empate

        # payoff 1:2 clássico => empate em 2/3
        meio_a_meio = ExitPolicy(alvo=Decimal("0.01"), stop=Decimal("-0.02"),
                                 exigir_payoff_minimo=False)
        assert abs(meio_a_meio.acerto_minimo() - Decimal("2") / Decimal("3")) \
            < Decimal("0.0001")

        # e o custo SOBE o acerto necessário — é o que mata robôs
        assert (meio_a_meio.acerto_minimo(Decimal("0.003")) >
                meio_a_meio.acerto_minimo())

    def test_policy_reports_the_break_even_hit_rate(self):
        from tradingagents.desk.policy import policy_for
        p = policy_for(Decimal("0.04"))
        sem_custo = p.acerto_minimo()
        com_custo = p.acerto_minimo(Decimal("0.003"))
        assert com_custo > sem_custo, "o custo tem de elevar o acerto necessário"
        assert Decimal("0.3") < com_custo < Decimal("0.45")

    def test_same_policy_decides_in_both_environments(self):
        """A prova direta: a mesma função decide a saída nos dois lados."""
        from tradingagents.desk.traders import TrendTrader
        t = TrendTrader("t", ("BTC/USDT",))
        assert t.exit_policy.avaliar(t.exit_policy.alvo)[0] is True
        assert t.exit_policy.avaliar(t.exit_policy.stop)[1] == "stop"
        assert t.exit_policy.avaliar(Decimal("0.001"))[0] is False
