"""§1: a FRONTEIRA de execução recusa combinações incompatíveis ANTES de
qualquer chamada externa. PAPER/SHADOW nunca despacham a destino real (live);
a frase de confirmação e as credenciais NÃO contornam o bloqueio de plataforma.
Puro, sem rede — usa substitutos de corretora."""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.brokers import build_desk_broker
from tradingagents.desk.execution import ExecutionCoordinator, ExecutionRefused
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import DeskMandate, Mode
from tradingagents.desk.risk import RiskEngine


def _mandate(mode=Mode.PAPER):
    return DeskMandate(mandate_id="m", owner="t", account="paper-t", mode=mode,
                       instruments=("BTC/USDT",), capital=Decimal("1000"))


class _FakeLiveBroker:
    mode = "live"

    def submit(self, *a, **k):
        raise AssertionError("PAPER/SHADOW não pode chegar ao submit de um destino live")


class _FakePaperBroker:
    mode = "paper"

    def submit(self, *a, **k):
        return {"id": "x"}


def _coord(mandate, broker):
    return ExecutionCoordinator(mandate, Ledger(opening_cash=mandate.capital),
                                RiskEngine(mandate), worker_id="w", broker=broker)


@pytest.mark.parametrize("mode", [Mode.PAPER, Mode.SHADOW])
def test_mandato_nao_live_recusa_corretora_live_na_construcao(mode):
    # recusa JÁ na construção, antes de qualquer envio
    with pytest.raises(ExecutionRefused):
        _coord(_mandate(mode), _FakeLiveBroker())


def test_mandato_paper_com_paper_broker_ok():
    c = _coord(_mandate(Mode.PAPER), _FakePaperBroker())
    assert c.mandate.mode is Mode.PAPER


def test_broker_vira_live_apos_construcao_bloqueia_no_envio():
    # defesa em profundidade: se a config efetiva mudar para live após a
    # construção, a checagem antes do submit recusa.
    c = _coord(_mandate(Mode.PAPER), _FakePaperBroker())
    c.broker.mode = "live"                       # config efetiva mudou
    with pytest.raises(ExecutionRefused):
        c._assert_destino_compativel()


def test_flag_desligada_bloqueia_broker_live_da_config_salva():
    # §1: config SALVA pedindo ccxt live + plataforma NÃO autoriza => recusa
    # antes de qualquer chamada externa. Frase e credenciais não contornam.
    from tradingagents.brokers.ccxt_bridge import BrokerSecurityError
    cfg = {"TRADINGAGENTS_DESK_BROKER": "ccxt", "TRADINGAGENTS_EXCHANGE": "binance",
           "TRADINGAGENTS_BROKER_MODE": "live",
           "LIVE_TRADING_CONFIRM": "EU AUTORIZO OPERAR COM DINHEIRO REAL",
           "EXCHANGE_API_KEY": "k", "EXCHANGE_API_SECRET": "s",
           "LIVE_MAX_ORDER_VALUE": "10", "LIVE_MAX_DAILY_LOSS": "10"}
    with pytest.raises(BrokerSecurityError):
        build_desk_broker(cfg, final_state={}, platform_allows_live=False)


def test_config_padrao_usa_paper_broker():
    broker, mode = build_desk_broker({}, final_state={}, platform_allows_live=False)
    assert mode == "paper"
