"""§5: após restaurar, existe UMA fonte financeira efetiva. O livro é recarregado
NO LUGAR (mesma instância), então o coordenador, o painel (desk['ledger']) e o
positions_provider ligado a esse livro concordam. Puro, sem rede."""
from __future__ import annotations

from decimal import Decimal

from tradingagents.desk.execution import ExecutionCoordinator, PaperBroker
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import DeskMandate, Mode
from tradingagents.desk.risk import RiskEngine


def _mandate():
    return DeskMandate(mandate_id="m", owner="t", account="paper-t", mode=Mode.PAPER,
                       instruments=("BTC/USDT",), capital=Decimal("10000"))


def _doc(cash: str):
    # lançamento BALANCEADO que estabelece o caixa restaurado
    return {"schema": "dz23.oms/1", "moeda": "USDT",
            "lancamentos": [{"tx_id": "t1", "kind": "restored", "entries": [
                {"account": "CASH:USDT", "amount": cash, "memo": "caixa restaurado"},
                {"account": "EQUITY", "amount": f"-{cash}", "memo": "contrapartida"}]}]}


def test_restore_recarrega_no_lugar_uma_fonte():
    m = _mandate()
    ledger = Ledger(opening_cash=Decimal("10000"))
    coord = ExecutionCoordinator(m, ledger, RiskEngine(m), worker_id="w",
                                 broker=PaperBroker(Decimal("100")))
    # como o serviço faz: desk['ledger'] e positions_provider ligados a ESTE livro
    desk_ledger = ledger
    positions_provider = ledger.open_positions
    ident = id(coord.ledger)

    coord.restaurar_estado(_doc("9900"))         # restaura caixa = 9900

    assert coord.ledger is ledger                 # MESMA instância (in-place)
    assert id(coord.ledger) == ident
    # uma única verdade: coordenador e painel concordam (antes: 9900 vs 10000)
    assert coord.ledger.cash() == Decimal("9900")
    assert desk_ledger.cash() == Decimal("9900")
    # o provider ligado ao livro antigo vê o estado restaurado
    assert positions_provider() == coord.ledger.open_positions()


def test_dois_reinicios_consecutivos_preservam_saldo():
    m = _mandate()
    ledger = Ledger(opening_cash=Decimal("10000"))
    coord = ExecutionCoordinator(m, ledger, RiskEngine(m), worker_id="w",
                                 broker=PaperBroker(Decimal("100")))
    coord.restaurar_estado(_doc("9900"))
    coord.restaurar_estado(_doc("9800"))          # 2º reinício
    assert coord.ledger is ledger
    assert coord.ledger.cash() == Decimal("9800")
    assert ledger.cash() == Decimal("9800")
