"""Patch F1/F2 — blockers da auditoria adversarial.

Cada teste nasce VERMELHO contra o comportamento atual e prova um blocker
concreto. Ver docs/finalization/TEST_MATRIX.md para o mapa completo.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.ledger import Ledger


# ===========================================================================
# BLOCKER 8 — restore deve revalidar dimensões
# ===========================================================================
def test_b8_restore_recusa_documento_cross_unit_sem_metadata():
    """Um doc que mistura unidades SEM metadata de conversão é recusado pelo
    _post (validar_dimensoes). Não pode voltar em silêncio pelo restore."""
    doc = {
        "tx_id": "t1", "kind": "restored", "ref": "",
        # +2 BNB (nativo) contra -2 USDT (funcional): soma dá zero, mas são
        # unidades diferentes. Sem native_currency/functional_currency o
        # movimento entre escriturações não está declarado.
        "entries": [
            {"account": "FEES:BNB", "amount": "2", "memo": "taxa"},
            {"account": "CASH:USDT", "amount": "-2", "memo": "pgto"},
        ],
    }
    with pytest.raises(ValueError, match="mistura unidades"):
        Ledger.from_transactions([doc], currency="USDT")


def test_b8_restore_aceita_documento_legitimo():
    """Um doc funcional balanceado (mesma unidade) restaura normalmente."""
    doc = {
        "tx_id": "t2", "kind": "opening",
        "entries": [
            {"account": "CASH:USDT", "amount": "100", "memo": "abertura"},
            {"account": "EQUITY", "amount": "-100", "memo": "aporte"},
        ],
    }
    led = Ledger.from_transactions([doc], currency="USDT")
    assert led.balance("CASH:USDT") == Decimal("100")


def test_b8_restore_aceita_cross_unit_com_metadata():
    """Cross-unit COM metadata de conversão declarada restaura (a diferença
    tem significado)."""
    doc = {
        "tx_id": "t3", "kind": "fee_valuation",
        "native_amount": "2", "native_currency": "BNB",
        "functional_amount": "600", "functional_currency": "USDT",
        "entries": [
            {"account": "FEES:USDT", "amount": "600", "memo": "valorada"},
            {"account": "LIABILITY:FEE_FUNDING", "amount": "-600", "memo": "v"},
        ],
    }
    led = Ledger.from_transactions([doc], currency="USDT")
    assert led.balance("FEES:USDT") == Decimal("600")
