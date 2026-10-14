"""FIN-2 — Auditoria adversarial (achado ChatGPT, commit 4b83e16).

Pedido do auditor: `assert_secure_for_capital` cobre o TRANSPORTE; os gates
de AUTORIZAÇÃO FINANCEIRA precisam ser comprovados SEPARADAMENTE:
proprietário, conta, estratégia, kill-switch, limites e reconciliação.

Esta suíte é a MATRIZ desse achado: uma classe por gate, e cada gate é
provado nos dois sentidos — a tentativa adversária é RECUSADA e, quando
legítimo, o caminho ABRE (um gate que nunca abre é indistinguível de um
gate quebrado). Nada aqui envia ordem real: só corretora simulada (mesma
classe que os testes de crash usam) e adaptador Paper.

Coberturas equivalentes em outras suítes (test_execution_router,
test_crash_restart, test_service_leigo, test_modo_da_mesa...) continuam
sendo a defesa contínua; esta é a leitura por achado, como o auditor pediu.
"""
from __future__ import annotations

import dataclasses
import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.desk.execution import (
    ExecutionCoordinator,
    ExecutionRefused,
    OrderState,
)
from tradingagents.desk.killswitch import (
    ACIONADO,
    LIBERADO,
    KillSwitch as KillSwitchDaMesa,
    KillSwitchError,
)
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import (
    Action,
    AgentSpec,
    AgentStage,
    DeskMandate,
    MandateError,
    Mode,
    TradeProposal,
)
from tradingagents.desk.risk import RiskEngine as RiskDaMesa, RiskVeto
from tradingagents.desk.storage import ArmazemDeEstado
from tradingagents.domain import (
    MarketSnapshot,
    OrderRequest,
    PortfolioSnapshot,
    Side,
    TradeDecision,
)
from tradingagents.execution import (
    ExecutionRouter,
    KillSwitch as KillSwitchDeExecucao,
    PaperAdapter,
)
from tradingagents.risk import RiskEngine as RiskDeMercado
from tradingagents.telemetry import AuditLog, EventBus

H = {"Authorization": "Bearer s3nh4"}


# ---------------------------------------------------------- corretora fake
class CorretoraSimulada:
    """Só simulação — nenhuma ordem real. Grava o que recebeu e, quando
    pedido, falha de propósito (para reproduzir cenários sem dinheiro)."""

    mode = "testnet"

    def __init__(self, preco: Decimal = Decimal("100"), falha=None):
        self.preco = preco
        self.falha = falha
        self.enviadas: list[dict] = []

    def submit(self, coid, instrument, side, quantity, final_state=None):
        self.enviadas.append({"coid": coid, "instrument": instrument,
                              "side": side, "quantidade": quantity})
        if self.falha is not None:
            raise self.falha
        return {"id": f"ex-{coid}", "status": "filled", "filled": quantity,
                "average": self.preco, "fee": Decimal("0"),
                "trades": [{"id": f"trade-{coid}", "amount": quantity,
                            "price": self.preco, "fee": {"cost": Decimal("0")}}]}


def _mandato(**ajustes) -> DeskMandate:
    base = {"mandate_id": "m", "owner": "dono", "account": "conta-a",
            "mode": Mode.PAPER, "instruments": ("BTC/USDT",),
            "capital": Decimal("1000"), "slippage_buffer_pct": Decimal("0")}
    base.update(ajustes)
    return DeskMandate(**base)


def _sistema(mandate=None, broker=None):
    m = mandate or _mandato()
    risco = RiskDaMesa(m)
    livro = Ledger(opening_cash=m.capital)
    b = broker or CorretoraSimulada()
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w", broker=b)
    return m, risco, livro, coord, b


def _proposta(exposicao="0.15", instrument="BTC/USDT") -> TradeProposal:
    return TradeProposal(agent_id="trader-trend", strategy="trend_following",
                         instrument=instrument, action=Action.BUY, reason="teste",
                         desired_exposure_pct=Decimal(exposicao),
                         confidence=Decimal("0.8"),
                         valid_until=datetime.now(timezone.utc) + timedelta(minutes=5))


def _intencao(risco, *, exposicao="0.1", instrument="BTC/USDT",
              modo=Mode.PAPER, coid="coid-ok"):
    res = risco.reserve(_proposta(exposicao, instrument), Decimal("100"),
                        agent_id="trader-trend")
    return risco.intent(res, Action.BUY, modo, "w", idempotency_key=coid), res


def _corretora_confirma(coord) -> dict:
    """Corretora que CONFIRMA o estado local: nenhuma ordem viva e as mesmas
    posições do livro. É o cenário 'tudo bate' — e só ele reabre o portão."""
    posicoes = {i: Decimal(str(d["quantidade"]))
                for i, d in coord.ledger.open_positions_detail().items()}
    return coord.reconciliar(lambda coid: None, consultar_posicoes=lambda: posicoes,
                             consultar_ordens_abertas=lambda: [])


# ------------------------------------------------------------- router fakes
def _decisao():
    now = datetime.now(timezone.utc)
    return TradeDecision("dec-router", "run-router", "strategy", "crypto", "BTCUSDT", "paper", "paper-account", Side.BUY, "MARKET", Decimal("100"), Decimal("98"), Decimal("105"), Decimal("10"), Decimal("0.001"), Decimal("1"), "GTC", now + timedelta(minutes=5), Decimal("0.8"), "TREND", ("evidence-1",), (), now), MarketSnapshot("snap-router", "BTCUSDT", "paper", Decimal("99.9"), Decimal("100.1"), Decimal("100"), Decimal("1"), Decimal("0.2"), now, now, "test", 1)


def _portfolio():
    return PortfolioSnapshot("portfolio-router", "paper-account", Decimal("10000"), Decimal("10000"), Decimal("0"), Decimal("10000"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))


def _request():
    return OrderRequest("coid-router", "dec-router", "run-router", "strategy", "paper-account", "BTCUSDT", "paper", Side.BUY, "MARKET", Decimal("1"), Decimal("100"), Decimal("98"), Decimal("105"))


def _router(tmp_path, *, guard=True, kill_switch=None):
    return ExecutionRouter(
        risk_engine=RiskDeMercado(), audit_log=AuditLog(tmp_path / "events.jsonl"),
        event_bus=EventBus(), kill_switch=kill_switch, metrics=None,
        reconciliation_guard=(lambda: True) if guard else (lambda: False))


class TestGateDoProprietario:
    """Rotas de dinheiro sem token: 401. Com token errado: 401. Com o token
    do dono: abrem. A interface inteira depende deste gate."""

    def _app(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        from fastapi.testclient import TestClient
        return m, TestClient(m.app)

    @pytest.mark.parametrize("metodo,rota", [
        ("GET", "/v1/config"),
        ("GET", "/v1/desk/status"),
        ("GET", "/v1/live/status"),
        ("POST", "/v1/live/parada-emergencia"),
    ])
    def test_rota_de_dinheiro_sem_token_e_401(self, tmp_path, monkeypatch, metodo, rota):
        m, c = self._app(tmp_path, monkeypatch)
        r = c.request(metodo, rota)
        assert r.status_code == 401, f"{metodo} {rota} abriu sem token"

    def test_token_errado_tambem_e_401(self, tmp_path, monkeypatch):
        m, c = self._app(tmp_path, monkeypatch)
        errado = {"Authorization": "Bearer nao-e-a-senha"}
        assert c.get("/v1/config", headers=errado).status_code == 401
        assert c.get("/v1/live/status", headers=errado).status_code == 401
        assert c.post("/v1/config", headers=errado,
                      json={"vars": {"TRADINGAGENTS_EXCHANGE": "binance"}}).status_code == 401

    def test_o_dono_entra(self, tmp_path, monkeypatch):
        m, c = self._app(tmp_path, monkeypatch)
        assert c.get("/v1/live/status", headers=H).status_code == 200
        assert c.get("/v1/config", headers=H).status_code == 200


class TestGateDaConta:
    """A ordem e o relatório pertencem a UMA conta. Conteúdo idêntico com
    conta trocada não é a mesma ordem — é dinheiro de outro lugar."""

    def test_router_recusa_conta_trocada(self, tmp_path):
        decision, snapshot = _decisao()
        request = dataclasses.replace(_request(), account_id="conta-invadida")
        with pytest.raises(PermissionError, match="não corresponde"):
            _router(tmp_path).submit(decision, _portfolio(), snapshot,
                                     PaperAdapter(), request=request)

    def test_coordenador_recusa_intencao_de_outra_conta(self):
        m, risco, livro, coord, broker = _sistema()
        intent, res = _intencao(risco)
        from tradingagents.desk.intents import ExecutionIntent, Kind, Side as LadoIntent
        ei = ExecutionIntent(kind=Kind.ENTRY, side=LadoIntent.BUY,
                             instrument="BTC/USDT", quantity=Decimal("1"),
                             agent_id="trader-trend", account="conta-b",
                             reservation_id=res.reservation_id,
                             reserved_amount=res.amount)
        with pytest.raises(ExecutionRefused, match="conta"):
            coord.submit_intent(ei)
        assert broker.enviadas == [], "ordem saiu mesmo com conta trocada"

    def test_reserva_troca_de_conta_nao_libera_dinheiro(self):
        """Venda com a conta de outra reserva: a autorização de saída compara
        com o LOTE, não com o caixa — mas a entrada compara com a conta da
        reserva. Ambas as faces do mesmo gate."""
        m, risco, livro, coord, broker = _sistema()
        res = risco.reserve(_proposta("0.1"), Decimal("100"), agent_id="trader-trend")
        with pytest.raises(ExecutionRefused, match="conta"):
            from tradingagents.desk.intents import ExecutionIntent, Kind, Side as LadoIntent
            coord.submit_intent(ExecutionIntent(
                kind=Kind.ENTRY, side=LadoIntent.BUY, instrument="BTC/USDT",
                quantity=Decimal("0.5"), agent_id="trader-trend",
                account="outra-conta", reservation_id=res.reservation_id,
                reserved_amount=Decimal("50")))

    def test_relatorio_de_outra_conta_nao_autoriza(self):
        m, risco, livro, coord, broker = _sistema()
        _corretora_confirma(coord)
        assert coord.relatorio_valido()[0] is True
        # o mesmo relatório, mas de outra conta: recusado
        coord.ultimo_relatorio = dict(coord.ultimo_relatorio,
                                      account="conta-de-outro")
        ok, motivo = coord.relatorio_valido()
        assert ok is False and "conta" in motivo

    def test_intencao_da_conta_certa_passa(self):
        m, risco, livro, coord, broker = _sistema()
        intent, _ = _intencao(risco)
        rec = coord.submit(intent)
        # a corretora simulada devolve execução completa: ACK e fills aplicados
        assert rec.state in (OrderState.ACKNOWLEDGED, OrderState.FILLED)
        assert broker.enviadas and broker.enviadas[0]["instrument"] == "BTC/USDT"


class TestGateDaEstrategia:
    """Estratégia: a ordem carrega a identidade da decisão aprovada, e a
    classe da sala só executa com homologação de política. Registrar classe
    não habilita negociar."""

    def test_router_recusa_estrategia_trocada(self, tmp_path):
        decision, snapshot = _decisao()
        errada = dataclasses.replace(_request(), strategy_id="estrategia-x")
        with pytest.raises(PermissionError, match="não corresponde"):
            _router(tmp_path).submit(decision, _portfolio(), snapshot,
                                     PaperAdapter(), request=errada)

    def test_router_recusa_quantidade_trocada(self, tmp_path):
        decision, snapshot = _decisao()
        maior = dataclasses.replace(_request(), quantity=Decimal("999"))
        with pytest.raises(PermissionError, match="não corresponde"):
            _router(tmp_path).submit(decision, _portfolio(), snapshot,
                                     PaperAdapter(), request=maior)

    def test_classe_sem_execucao_homologada_nao_abre_sala(self):
        from tradingagents.desk.salas import (
            ADAPTADORES,
            AdaptadorDeCorretora,
            ContextoDaSala,
            RegistroDeSalas,
            SalaError,
            registrar_adaptador,
        )
        registrar_adaptador(AdaptadorDeCorretora("b3_teste", ("acoes",)))
        try:
            reg = RegistroDeSalas()
            with pytest.raises(SalaError, match="homologada"):
                reg.abrir_sala(ContextoDaSala("b3_teste", "conta-1", "acoes",
                                              "paper", classe="b3_acoes"))
        finally:
            ADAPTADORES.pop("b3_teste", None)

    def test_classe_homologada_abre_sala(self):
        from tradingagents.desk.salas import ContextoDaSala, RegistroDeSalas
        reg = RegistroDeSalas()
        sala = reg.abrir_sala(ContextoDaSala("binance", "conta-1", "spot",
                                             "paper", classe="cripto_spot"))
        assert sala is not None

    def test_sala_live_nao_e_criada_por_esta_camada(self):
        from tradingagents.desk.salas import ContextoDaSala, RegistroDeSalas, SalaError
        reg = RegistroDeSalas()
        with pytest.raises(SalaError, match="LIVE"):
            reg.abrir_sala(ContextoDaSala("binance", "conta-1", "spot", "live"))


class TestGateDoKillSwitch:
    """Duas dimensões independentes: o kill switch de EXECUÇÃO (arquivo,
    lido pelo router) e a parada de EMERGÊNCIA da mesa (armazém, com
    liberação em duas condições). Ambos fail-closed."""

    def test_kill_switch_de_execucao_engaja_e_recusa(self, tmp_path):
        ks = KillSwitchDeExecucao(tmp_path / "ks.json")
        ks.engage("teste de auditoria", actor="dono")
        decision, snapshot = _decisao()
        adapter = PaperAdapter()
        with pytest.raises(PermissionError, match="kill switch"):
            _router(tmp_path, kill_switch=ks).submit(
                decision, _portfolio(), snapshot, adapter, request=_request())
        assert not adapter.orders, "adapter chamado com kill switch engajado"

    def test_kill_switch_desarma_so_com_reconciliacao(self, tmp_path):
        ks = KillSwitchDeExecucao(tmp_path / "ks.json")
        ks.engage("incidente", actor="dono")
        with pytest.raises(PermissionError, match="reconcilia"):
            ks.reset(actor="dono", reconciliation_ok=False)
        assert ks.state.enabled is True, "desarmou sem reconciliação"
        ks.reset(actor="dono", reconciliation_ok=True)
        assert ks.state.enabled is False

    def test_kill_switch_arquivo_ilegivel_e_engajado_por_precaucao(self, tmp_path):
        path = tmp_path / "ks.json"
        path.write_text("isto não é json", encoding="utf-8")
        ks = KillSwitchDeExecucao(path)
        assert ks.state.enabled is True
        assert "inválido" in ks.state.reason

    def test_parada_de_emergencia_da_mesa_persiste(self, tmp_path):
        armazem = ArmazemDeEstado(diretorio=tmp_path / "estado")
        ks = KillSwitchDaMesa(armazem, "legacy")
        ks.acionar("parada de auditoria")
        outra = KillSwitchDaMesa(ArmazemDeEstado(diretorio=tmp_path / "estado"),
                                 "legacy")
        assert outra.estado.estado == ACIONADO and outra.bloqueia

    def test_liberar_exige_frase_e_reconciliacao(self, tmp_path):
        ks = KillSwitchDaMesa(ArmazemDeEstado(diretorio=tmp_path / "e"), "legacy")
        ks.acionar("teste")
        with pytest.raises(KillSwitchError, match="autorização"):
            ks.liberar(frase_correta=False, reconciliado=True)
        with pytest.raises(KillSwitchError, match="reconcilie"):
            ks.liberar(frase_correta=True, reconciliado=False)
        assert ks.estado.estado == ACIONADO, "liberou sem as duas condições"

    def test_liberar_com_as_duas_condicoes(self, tmp_path):
        ks = KillSwitchDaMesa(ArmazemDeEstado(diretorio=tmp_path / "e"), "legacy")
        ks.acionar("teste")
        ks.marcar_reconciliado()
        ks.liberar(frase_correta=True, reconciliado=True)
        assert ks.estado.estado == LIBERADO and not ks.bloqueia

    def test_armazem_ilegivel_bloqueia_por_precaucao(self):
        class ArmazemQuebrado:
            def carregar(self, *a, **k):
                raise OSError("disco indisponível")

            def salvar(self, *a, **k):
                raise OSError("disco indisponível")

        ks = KillSwitchDaMesa(ArmazemQuebrado(), "legacy")
        assert ks.bloqueia is True, "estado indeterminável tratado como liberado"

    def test_estado_desconhecido_no_disco_vira_acionado(self):
        from tradingagents.desk.killswitch import EstadoParada
        e = EstadoParada.from_doc({"schema": "outra.coisa/9", "estado": "liberado"})
        assert e.estado == ACIONADO


class TestGateDeLimites:
    """Os tetos do mandato (frações, quantidade de posições, cobertura) são
    veto do risco ANTES de qualquer chamada de rede."""

    def test_teto_por_posicao_limita_a_exposicao(self):
        m, risco, livro, coord, broker = _sistema(_mandato(max_position_pct=Decimal("0.25")))
        res = risco.reserve(_proposta("0.9"), Decimal("100"), agent_id="trader-trend")
        assert res.amount == Decimal("250.00"), (
            "proposta de 90% não foi limitada pelo teto de 25% do mandato")

    def test_capital_insuficiente_veta_sem_enviar(self):
        m, risco, livro, coord, broker = _sistema(_mandato(
            instruments=("BTC/USDT", "ETH/USDT"), max_position_pct=Decimal("1")))
        risco.reserve(_proposta("0.9"), Decimal("100"), agent_id="trader-trend")
        with pytest.raises(RiskVeto, match="capital insuficiente"):
            risco.reserve(_proposta("0.2", "ETH/USDT"), Decimal("100"),
                          agent_id="trader-trend")
        assert broker.enviadas == []

    def test_limite_de_posicoes_simultaneas_veta(self):
        m, risco, livro, coord, broker = _sistema(_mandato(
            instruments=("BTC/USDT", "ETH/USDT"), max_open_positions=1))
        risco.reserve(_proposta("0.1"), Decimal("100"), agent_id="trader-trend")
        with pytest.raises(RiskVeto, match="posições"):
            risco.reserve(_proposta("0.1", "ETH/USDT"), Decimal("100"),
                          agent_id="trader-trend")

    def test_entrada_sem_reserva_e_recusada(self):
        from tradingagents.desk.intents import ExecutionIntent, Kind, Side as LadoIntent
        m, risco, livro, coord, broker = _sistema()
        ei = ExecutionIntent(kind=Kind.ENTRY, side=LadoIntent.BUY,
                             instrument="BTC/USDT", quantity=Decimal("1"),
                             agent_id="trader-trend", account="conta-a")
        with pytest.raises(ExecutionRefused, match="reserva"):
            coord.submit_intent(ei)


class TestGateDeReconciliacao:
    """Estado DESCONHECIDO bloqueia risco novo — compra E venda. Só a
    comparação real com a corretora reabre."""

    def test_router_sem_guard_e_fail_closed(self, tmp_path):
        decision, snapshot = _decisao()
        adapter = PaperAdapter()
        with pytest.raises(PermissionError, match="reconcilia"):
            _router(tmp_path, guard=False).submit(
                decision, _portfolio(), snapshot, adapter, request=_request())
        assert not adapter.orders

    def test_portao_da_mesa_bloqueia_compra(self):
        m, risco, livro, coord, broker = _sistema()
        coord.exigir_reconciliacao("ordem em estado ambíguo")
        intent, _ = _intencao(risco)
        with pytest.raises(ExecutionRefused, match="reconciliação pendente"):
            coord.submit(intent)
        assert broker.enviadas == []

    def test_portao_da_mesa_bloqueia_venda(self):
        from tradingagents.desk.intents import exit_intent
        m, risco, livro, coord, broker = _sistema()
        livro.reserve(Decimal("100"), ref="r0")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("100"), Decimal("0"),
                       reserved=Decimal("100"), ref="o0", agent_id="trader-trend")
        coord.exigir_reconciliacao("posição restaurada não conferida")
        ei = exit_intent(instrument="BTC/USDT", quantity=Decimal("1"),
                         agent_id="trader-trend", account="conta-a",
                         client_order_id="coid-exit")
        with pytest.raises(ExecutionRefused, match="reconciliação pendente"):
            coord.submit_intent(ei)
        assert broker.enviadas == []

    def test_reconciliacao_confirmada_reabre_o_portao(self):
        m, risco, livro, coord, broker = _sistema()
        coord.exigir_reconciliacao("teste")
        relatorio = _corretora_confirma(coord)
        assert relatorio["seguro"] is True
        assert coord.relatorio_valido()[0] is True
        intent, _ = _intencao(risco)
        rec = coord.submit(intent)
        assert rec.state in (OrderState.ACKNOWLEDGED, OrderState.FILLED)
        assert broker.enviadas, "portão reaberto mas ordem não saiu"

    def test_reconciliacao_sem_acesso_a_corretora_nao_e_segura(self):
        m, risco, livro, coord, broker = _sistema()
        coord.exigir_reconciliacao("teste")
        relatorio = coord.reconciliar(None)
        assert relatorio["seguro"] is False
        assert coord.relatorio_valido()[0] is False


class TestGateDoMandato:
    """O mandato é o PORQUÊ da ordem existir: ativo, dentro do escopo, não
    expirado, dono correto, mesma versão de política — e LIVE só com
    autorização viva."""

    def test_mandato_revogado_recusa(self):
        m = dataclasses.replace(_mandato(), revoked_at=datetime.now(timezone.utc))
        with pytest.raises(MandateError, match="revogado"):
            m.assert_active()

    def test_mandato_expirado_recusa(self):
        m = dataclasses.replace(_mandato(),
                                valid_until=datetime.now(timezone.utc) - timedelta(minutes=1))
        with pytest.raises(MandateError, match="expirado"):
            m.assert_active()

    def test_instrumento_fora_do_mandato_recusa(self):
        with pytest.raises(MandateError, match="fora do mandato"):
            _mandato().assert_active(instrument="ETH/USDT")

    def test_worker_sem_posse_da_intencao_recusa(self):
        m, risco, livro, coord, broker = _sistema()
        intent, _ = _intencao(risco)
        with pytest.raises(MandateError, match="posse"):
            intent.assert_sendable(worker_id="intruso", mandate=m)

    def test_intencao_expirada_recusa(self):
        m, risco, livro, coord, broker = _sistema()
        intent, _ = _intencao(risco)
        with pytest.raises(MandateError, match="expirada"):
            intent.assert_sendable(worker_id="w", mandate=m,
                                   now=datetime.now(timezone.utc) + timedelta(minutes=10))

    def test_politica_mudou_desde_a_reserva(self):
        m, risco, livro, coord, broker = _sistema()
        intent, _ = _intencao(risco)
        nova_versao = dataclasses.replace(m, version=2)
        with pytest.raises(MandateError, match="mudou"):
            intent.assert_sendable(worker_id="w", mandate=nova_versao)

    def test_coordenador_recusa_instrumento_fora_do_mandato(self):
        from tradingagents.desk.intents import ExecutionIntent, Kind, Side as LadoIntent
        m, risco, livro, coord, broker = _sistema()
        ei = ExecutionIntent(kind=Kind.ENTRY, side=LadoIntent.BUY,
                             instrument="ETH/USDT", quantity=Decimal("1"),
                             agent_id="trader-trend", account="conta-a")
        with pytest.raises(ExecutionRefused, match="mandato inativo"):
            coord.submit_intent(ei)

    def test_live_sem_autorizacao_no_mandato_recusa_na_execucao(self):
        m, risco, livro, coord, broker = _sistema()
        intent, _ = _intencao(risco, modo=Mode.LIVE)
        with pytest.raises(ExecutionRefused, match="LIVE sem autorização"):
            coord.submit(intent)
        assert broker.enviadas == []

    def test_mandato_live_sem_autorizacao_recusa_na_politica(self):
        m = _mandato(mode=Mode.LIVE)
        with pytest.raises(MandateError, match="autorização ativa"):
            m.assert_active()


class TestSaidasDoGateNaoSaoBecos:
    """O gate não pode ser uma parede: com tudo legítimo, a operação sai —
    senão 'bloqueado' e 'quebrado' ficariam indistinguíveis."""

    def test_entrada_legitima_atravessa_todos_os_gates(self):
        m, risco, livro, coord, broker = _sistema()
        intent, res = _intencao(risco)
        rec = coord.submit(intent)
        assert rec.state in (OrderState.ACKNOWLEDGED, OrderState.FILLED)
        assert broker.enviadas[0]["coid"] == "coid-ok"
        assert rec.client_order_id == "coid-ok"

    def test_replay_idempotente_nao_duplica(self):
        m, risco, livro, coord, broker = _sistema()
        intent, _ = _intencao(risco)
        primeiro = coord.submit(intent)
        segundo = coord.submit(intent)
        assert segundo is primeiro
        assert len(broker.enviadas) == 1, "retry duplicou ordem"

    def test_agente_sem_estagio_nao_propoe(self):
        spec = AgentSpec(agent_id="t-1", role="trader")
        assert spec.stage is AgentStage.REGISTERED
        assert spec.can_propose() is False
