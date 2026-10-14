"""FIN-3 — Auditoria adversarial (achado ChatGPT, commit 4b83e16).

Cenário pedido: a corretora ACEITA a ordem, mas a resposta de rede se perde.
O que precisa ser comprovado:
  1. reconciliação por clientOrderId — a ordem é reencontrada mesmo sem a
     resposta do envio;
  2. prevenção de duplicação — nada é reenviado nem contado duas vezes;
  3. tratamento de estado desconhecido — sem prova, o portão fica fechado e
     a reserva permanece.

A ponte CCXT e o adaptador de corretora são os de PRODUÇÃO (sem rede: a
exchange é falsa e substituída no lugar da ccxt real). A corretora falsa usa
semântica binance-ish: `fetch_order` resolve SOMENTE o que a corretora
conhece — o id de exchange ou o clientOrderId recebido no envio (dz23-...).
O id interno da mesa nunca chega à corretora; consultá-lo recebe
OrderNotFound, exatamente como a Binance responderia. É essa assimetria que
o teste reproduz.

Nenhuma ordem real: a única corretora é a classe falsa deste arquivo.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.brokers.ccxt_bridge import CCXTBridge
from tradingagents.desk.brokers import BrokerTimeout, CCXTDeskBroker
from tradingagents.desk.execution import (
    ExecutionCoordinator,
    ExecutionRefused,
    OrderState,
)
from tradingagents.desk.intents import from_order_intent
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import (
    Action,
    DeskMandate,
    Mode,
    TradeProposal,
)
from tradingagents.desk.risk import RiskEngine as RiskDaMesa
from tradingagents.gates import MARK_KEY, MARK_SCHEMA

# Estado com marca de transporte ACEITA pelo gate de capital (mesmo formato
# dos testes da ponte): é o que o adaptador real envia ao `create_order`.
ESTADO_SEGURO = {
    "company_of_interest": "BTC/USDT",
    "trade_date": "2026-09-04",
    MARK_KEY: {"schema": MARK_SCHEMA, "insecure": False,
               "insecure_targets": [],
               "policy": "backend_policy/rodada5",
               "run": {"ticker": "BTC/USDT", "trade_date": "2026-09-04"}},
}


class RequestTimeout(Exception):
    """Erro de rede AMBÍGUO: a requisição pode ter chegado."""


class OrderNotFound(Exception):
    """A corretora respondeu que a ordem consultada não existe."""


class DuplicateOrder(Exception):
    """clientOrderId repetido — a corretora recusa criar de novo (−2010)."""


class CorretoraFalsaBinance:
    """Semântica de exchange real, exercitada OFFLINE.

    - create_order registra a ordem e, quando `perder_resposta`, estoura
      RequestTimeout DEPOIS de registrar (foi aceita; a resposta é que se
      perdeu);
    - `rejeitar_antes_de_registrar` modela a conexão que caiu ANTES do
      envio (a corretora nunca viu a ordem);
    - clientOrderId repetido é recusado (comportamento binance);
    - fetch_order só resolve o que a corretora conhece.
    """

    id = "binance"

    def __init__(self, *, perder_resposta=True):
        self.perder_resposta = perder_resposta
        self.rejeitar_antes_de_registrar = False
        self.ordens: dict[str, dict] = {}
        self.por_coid: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.consultas: list[str] = []      # ids consultados em fetch_order
        self.chamadas: list[dict] = []      # create_order recebidos
        self._n = 0

    def set_sandbox_mode(self, ativo):
        self.sandbox = ativo

    def create_order(self, symbol, typ, side, amount, price, params):
        coid = params["clientOrderId"]
        self.chamadas.append({"symbol": symbol, "side": side,
                              "amount": amount, "coid": coid})
        if coid in self.por_coid:
            raise DuplicateOrder(f"clientOrderId repetido: {coid}")
        if self.rejeitar_antes_de_registrar:
            raise RequestTimeout("conexão caiu antes de enviar")
        self._n += 1
        oid = f"ex-{self._n}"
        o = {"id": oid, "clientOrderId": coid, "symbol": symbol, "side": side,
             "amount": amount, "status": "new", "filled": 0.0,
             "average": None, "transactTime": 1, "info": {"clOrdId": coid}}
        self.ordens[oid] = o
        self.por_coid[coid] = o
        if self.perder_resposta:
            raise RequestTimeout("read timeout: resposta perdida após envio")
        return dict(o)

    def executar(self, oid: str, preco: Decimal, quantidade=None,
                 sem_id=False):
        """A corretora executa a ordem — possivelmente durante o blecaute."""
        o = self.ordens[oid]
        q = quantidade if quantidade is not None else Decimal(str(o["amount"]))
        o["status"] = "closed"
        o["filled"] = q
        o["average"] = preco
        trade = {"order": oid, "symbol": o["symbol"], "side": o["side"],
                 "amount": q, "price": preco, "timestamp": 1,
                 "fee": {"cost": Decimal("0"), "currency": "USDT"}}
        if not sem_id:
            trade["id"] = f"t-{len(self.trades) + 1}"
        self.trades.append(trade)

    def fetch_order(self, oid, symbol=None):
        self.consultas.append(oid)
        if oid in self.ordens:
            return dict(self.ordens[oid])
        if oid in self.por_coid:
            return dict(self.por_coid[oid])
        raise OrderNotFound(f"ordem {oid!r} não existe")

    def sem_consulta(self, oid, symbol=None):
        raise RuntimeError("exchange fora do ar")

    def fetch_my_trades(self, *a, **k):
        return [dict(t) for t in self.trades]

    def fetch_balance(self):
        btc = sum((t["amount"] for t in self.trades), Decimal("0"))
        return {"total": {"USDT": Decimal("1000"), "BTC": btc}}

    def fetch_orders(self, *a, **k):
        return list(self.ordens.values())

    def fetch_open_orders(self):
        return [dict(o) for o in self.ordens.values()
                if o["status"] in ("new", "open", "partially_filled")]


def _adaptador(tmp_path, exchange) -> CCXTDeskBroker:
    """Adaptador de PRODUÇÃO com a ponte de PRODUÇÃO — sem rede.

    A ponte é construída de verdade (gate de capital, clientOrderId dz23-*,
    journal) e `_ex` é substituído pela exchange falsa ANTES de qualquer uso.
    """
    ponte = CCXTBridge(
        env={"TRADINGAGENTS_EXCHANGE": "binance",
             "TRADINGAGENTS_BROKER_MODE": "testnet",
             "EXCHANGE_API_KEY": "k-test-000000",
             "EXCHANGE_API_SECRET": "s-test-000000"},
        journal_path=tmp_path / "j.jsonl")
    ponte._ex = exchange
    b = CCXTDeskBroker.__new__(CCXTDeskBroker)
    b.bridge = ponte
    b.final_state = ESTADO_SEGURO
    b.mode = "testnet"
    b.submitted = {}
    b.universe = ("BTC/USDT",)
    return b


def _mandato(**ajustes) -> DeskMandate:
    base = {"mandate_id": "m", "owner": "dono", "account": "conta-a",
            "mode": Mode.PAPER, "instruments": ("BTC/USDT",),
            "capital": Decimal("1000"), "slippage_buffer_pct": Decimal("0")}
    base.update(ajustes)
    return DeskMandate(**base)


def _sistema(tmp_path, *, perder_resposta=True, corretora=None):
    m = _mandato()
    risco = RiskDaMesa(m)
    livro = Ledger(opening_cash=m.capital)
    corretora = corretora or CorretoraFalsaBinance(
        perder_resposta=perder_resposta)
    b = _adaptador(tmp_path, corretora)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w", broker=b)
    return m, risco, livro, coord, corretora, b


def _proposta(coid_ref: str) -> TradeProposal:
    return TradeProposal(agent_id="trader-trend", strategy="trend_following",
                         instrument="BTC/USDT", action=Action.BUY,
                         reason=f"teste {coid_ref}",
                         desired_exposure_pct=Decimal("0.1"),
                         confidence=Decimal("0.8"),
                         valid_until=(datetime.now(timezone.utc)
                                      + timedelta(minutes=5)))


def _entrada(coord, risco, coid: str, *, preco=Decimal("100")):
    """Percorre o caminho ÚNICO (risco → intent → submit_intent) para o coid."""
    res = risco.reserve(_proposta(coid), preco, agent_id="trader-trend")
    intent = risco.intent(res, Action.BUY, Mode.PAPER, "w",
                          idempotency_key=coid)
    rec = coord.submit_intent(from_order_intent(intent))
    return rec, res


def _reconciliar(coord, corretora):
    """Mesma fiação do serviço (rota /v1/desk/reconciliar)."""
    return coord.reconciliar(
        corretora.fetch_order_by_client_id,
        consultar_posicoes=corretora.fetch_positions,
        consultar_fills=corretora.fetch_order_fills,
        consultar_ordens_abertas=corretora.fetch_open_orders)


def _dz23(coid: str) -> str:
    return CCXTBridge.client_order_id(coid, "BTC/USDT", "buy")


class TestEnvioAmbiguo:
    def test_resposta_perdida_fecha_o_portao_e_preserva_a_reserva(self,
                                                                  tmp_path):
        m, risco, livro, coord, corretora, _ = _sistema(tmp_path)
        rec, res = _entrada(coord, risco, "coid-amb-1")

        assert rec.state is OrderState.UNKNOWN_RECONCILIATION_REQUIRED
        assert coord.reconciliacao_pendente is True
        # A corretora ACEITOU: a ordem existe lá — sem prova, nada se decide.
        assert len(corretora.ordens) == 1
        assert len(corretora.chamadas) == 1
        # Capital NÃO foi liberado: o efeito externo pode existir.
        assert risco.reserva(res.reservation_id) is not None

    def test_pedido_novo_e_recusado_enquanto_o_estado_e_desconhecido(self,
                                                                    tmp_path):
        m, risco, livro, coord, corretora, _ = _sistema(tmp_path)
        _rec, res = _entrada(coord, risco, "coid-amb-1")
        # Pedido NOVO (outro coid) sobre a reserva ainda viva: o portão do
        # coordenador recusa no passo 3 — ANTES de autorizar e de enviar.
        novo = risco.intent(res, Action.BUY, Mode.PAPER, "w",
                            idempotency_key="coid-amb-2")
        with pytest.raises(ExecutionRefused, match="reconciliação pendente"):
            coord.submit_intent(from_order_intent(novo))
        assert len(corretora.chamadas) == 1      # nada novo saiu
        assert len(coord.orders) == 1            # nada novo foi registrado

    def test_execucao_no_blecaute_e_revivida_por_clientOrderId(self, tmp_path):
        """O caso limite do achado: aceita, executa e a resposta some."""
        m, risco, livro, coord, corretora, b = _sistema(tmp_path)
        rec, res = _entrada(coord, risco, "coid-amb-1")
        dz23 = _dz23("coid-amb-1")

        corretora.executar("ex-1", Decimal("100"))   # durante o blecaute

        rel = _reconciliar(coord, b)

        assert rel["seguro"] is True
        assert rel["fills_status"] == "MATCHED"
        assert rec.state is OrderState.FILLED
        assert rec.filled_qty == rec.quantidade
        # A prova da reconciliação: consultou o id QUE A CORRETORA CONHECE
        # (o clientOrderId dz23-*), nunca o id interno da mesa.
        assert dz23 in corretora.consultas
        assert "coid-amb-1" not in corretora.consultas
        assert rec.external_client_id == dz23
        posicoes = coord.ledger.open_positions_detail()
        assert (Decimal(str(posicoes["BTC/USDT"]["quantidade"]))
                == rec.filled_qty)
        assert coord.reconciliacao_pendente is False
        assert coord.relatorio_valido()[0] is True
        assert len(corretora.chamadas) == 1      # NUNCA reenviou

    def test_ordem_viva_nao_e_declarada_ausente(self, tmp_path):
        """Ordem aceita e ainda ABERTA na corretora: confirmar ausência
        liberaria capital de uma ordem viva (o defeito comprovado)."""
        m, risco, livro, coord, corretora, b = _sistema(tmp_path)
        rec, res = _entrada(coord, risco, "coid-amb-1")

        rel = _reconciliar(coord, b)

        assert rec.state is OrderState.UNKNOWN_RECONCILIATION_REQUIRED, \
            "ordem viva na corretora foi tratada como inexistente"
        assert risco.reserva(res.reservation_id) is not None
        assert rec.external_id == "ex-1"
        assert any("ordens ainda abertas" in d for d in rel["divergencias"])
        assert rel["seguro"] is False

    def test_estado_desconhecido_mantem_reserva_e_portao(self, tmp_path):
        m, risco, livro, coord, corretora, b = _sistema(tmp_path)
        rec, res = _entrada(coord, risco, "coid-amb-1")
        corretora.fetch_order = corretora.sem_consulta   # rede caiu

        rel = _reconciliar(coord, b)

        assert any("reserva mantida" in d for d in rel["divergencias"])
        assert rec.state is OrderState.UNKNOWN_RECONCILIATION_REQUIRED
        assert risco.reserva(res.reservation_id) is not None
        assert rel["seguro"] is False

    def test_ausencia_confirmada_de_verdade_libera(self, tmp_path):
        """A conexão caiu ANTES do envio: a corretora nunca viu a ordem.
        Aí (e só aí) a ausência é fato, e o capital volta."""
        m, risco, livro, coord, corretora, b = _sistema(tmp_path)
        corretora.rejeitar_antes_de_registrar = True
        rec, res = _entrada(coord, risco, "coid-amb-1")
        assert rec.state is OrderState.UNKNOWN_RECONCILIATION_REQUIRED

        rel = _reconciliar(coord, b)

        assert rec.state is OrderState.REJECTED
        assert "CONFIRMA" in rec.note
        assert risco.reserva(res.reservation_id) is None
        assert len(corretora.ordens) == 0
        assert rel["seguro"] is True


class TestNuncaDuplica:
    def test_replay_do_mesmo_pedido_nao_toca_a_corretora(self, tmp_path):
        m, risco, livro, coord, corretora, b = _sistema(tmp_path)
        rec, res = _entrada(coord, risco, "coid-amb-1")
        # Reenvio legítimo do MESMO pedido: a idempotência devolve o registro
        # existente antes de qualquer nova validação — e nada é reenviado.
        replay_intent = risco.intent(res, Action.BUY, Mode.PAPER, "w",
                                     idempotency_key="coid-amb-1")
        replay = coord.submit_intent(from_order_intent(replay_intent))
        assert replay.order_id == rec.order_id      # mesmo registro
        assert len(corretora.chamadas) == 1         # nenhum reenvio
        assert len(corretora.ordens) == 1

    def test_reenvio_direto_com_o_mesmo_clientOrderId_e_recusado(self,
                                                                 tmp_path):
        """Pior caso: um chamador contorna o coordenador (reinício + bug).
        A corretora recusa clientOrderId repetido e o sistema classifica o
        erro como AMBÍGUO — nunca como rejeição limpa."""
        m, risco, livro, coord, corretora, b = _sistema(
            tmp_path, perder_resposta=False)
        b.submit("coid-amb-1", "BTC/USDT", "buy", Decimal("1"),
                 final_state=ESTADO_SEGURO)
        assert len(corretora.ordens) == 1

        b2 = _adaptador(tmp_path, corretora)        # processo novo
        with pytest.raises(BrokerTimeout):
            b2.submit("coid-amb-1", "BTC/USDT", "buy", Decimal("1"),
                      final_state=ESTADO_SEGURO)
        assert len(corretora.chamadas) == 2         # tentou...
        assert len(corretora.ordens) == 1           # mas NADA duplicou
        assert (corretora.chamadas[0]["coid"]
                == corretora.chamadas[1]["coid"] == _dz23("coid-amb-1"))


class TestExecucoesAdversariais:
    def test_fills_divergentes_do_acumulado_mantem_o_portao_fechado(self,
                                                                    tmp_path):
        """A corretora relata mais que a soma das execuções: visão local
        incompleta — e incompleta não é segura."""
        m, risco, livro, coord, corretora, b = _sistema(tmp_path)
        rec, res = _entrada(coord, risco, "coid-amb-1")
        corretora.executar("ex-1", Decimal("100"), quantidade=Decimal("1"))
        corretora.ordens["ex-1"]["filled"] = Decimal("2")   # mentira

        rel = _reconciliar(coord, b)

        assert any("FILLS_DIVERGEM" in d for d in rel["divergencias"])
        assert rel["fills_status"] == "MISMATCH"
        assert rel["seguro"] is False
        assert coord.reconciliacao_pendente is True

    def test_execucao_sem_identificador_nao_vira_posicao(self, tmp_path):
        """Fill sem id não pode ser deduplicado — logo não pode existir."""
        m, risco, livro, coord, corretora, b = _sistema(tmp_path)
        rec, res = _entrada(coord, risco, "coid-amb-1")
        corretora.executar("ex-1", Decimal("100"), sem_id=True)

        rel = _reconciliar(coord, b)

        assert any("execuções" in d for d in rel["divergencias"])
        assert rel["fills_status"] == "UNKNOWN"
        assert rel["seguro"] is False
        assert rec.filled_qty == Decimal("0")
        assert risco.reserva(res.reservation_id) is not None


class TestSobreviveAoReinicio:
    def test_reconciliacao_apos_reinicio_usa_o_mesmo_clientOrderId(self,
                                                                   tmp_path):
        """A identidade da ordem precisa sobreviver ao reinício: sem ela o
        sistema consulta um id que a corretora não conhece e 'confirma' uma
        ausência que não existe."""
        m, risco, livro, coord, corretora, b = _sistema(tmp_path)
        rec, res = _entrada(coord, risco, "coid-amb-1")
        doc = coord.snapshot_estado()

        risco2 = RiskDaMesa(m)
        livro2 = Ledger(opening_cash=m.capital)
        b2 = _adaptador(tmp_path, corretora)        # processo novo
        coord2 = ExecutionCoordinator(m, livro2, risco2, worker_id="w",
                                      broker=b2)
        restaurado = coord2.restaurar_estado(doc)
        assert restaurado["exige_reconciliacao"] is True

        rec2 = coord2.orders[rec.order_id]
        assert rec2.external_client_id == _dz23("coid-amb-1")

        corretora.executar("ex-1", Decimal("100"))  # executa no reinício
        rel = _reconciliar(coord2, b2)

        assert rel["seguro"] is True
        assert coord2.orders[rec.order_id].state is OrderState.FILLED
        assert (Decimal(str(coord2.ledger.open_positions_detail()
                            ["BTC/USDT"]["quantidade"]))
                == coord2.orders[rec.order_id].filled_qty)
        assert len(corretora.chamadas) == 1         # nenhum reenvio


class TestNenhumaRedeNemOrdemReal:
    def test_a_unica_corretora_e_a_falsa_e_em_testnet(self, tmp_path):
        """Guarda estrutural: se alguém remover a substituição da exchange,
        estes testes começariam a falar com a testnet de verdade."""
        corretora = CorretoraFalsaBinance()
        m, risco, livro, coord, _fake, b = _sistema(tmp_path,
                                                    corretora=corretora)
        assert b.bridge._ex is corretora
        assert b.mode == "testnet"
        assert b.final_state is ESTADO_SEGURO
