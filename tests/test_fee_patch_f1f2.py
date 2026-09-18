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


# ===========================================================================
# BLOCKER 7 — fingerprint canônico (normalização + dimensões que faltam)
# ===========================================================================
_FP = Ledger.fingerprint_de_valoracao
_EM = "2026-01-01T00:00:00+00:00"


def test_b7a_decimal_equivalente_mesma_impressao():
    """300 e 300.0 são o mesmo valor — não podem gerar impressões diferentes."""
    base = {"moeda": "BNB", "rate": Decimal("2"), "fonte": "x", "em": _EM}
    assert _FP(quantidade=Decimal("300"), **base) \
        == _FP(quantidade=Decimal("300.0"), **base)


def test_b7b_mesmo_instante_offsets_diferentes():
    """20:00-03:00 e 23:00Z são o MESMO ponto no tempo."""
    base = {"moeda": "BNB", "quantidade": Decimal("2"), "rate": Decimal("300"),
            "fonte": "x"}
    assert _FP(em="2026-01-01T20:00:00-03:00", **base) \
        == _FP(em="2026-01-01T23:00:00+00:00", **base)


def test_b7c_functional_currency_entra_na_impressao():
    """USDT vs EUR: mesma taxa nativa, custo funcional diferente."""
    base = {"moeda": "BNB", "quantidade": Decimal("2"), "rate": Decimal("300"),
            "fonte": "x", "em": _EM}
    assert _FP(**base, functional_currency="USDT") \
        != _FP(**base, functional_currency="EUR")


def test_b7d_policy_version_entra_na_impressao():
    """Política de valoração diferente => impressão diferente."""
    base = {"moeda": "BNB", "quantidade": Decimal("2"), "rate": Decimal("300"),
            "fonte": "x", "em": _EM}
    assert _FP(**base, policy_version="dz23.resolucao/1") \
        != _FP(**base, policy_version="dz23.resolucao/2")


def test_b7e_moeda_normalizada():
    """Formas diferentes da mesma moeda não são conteúdos diferentes."""
    base = {"quantidade": Decimal("2"), "rate": Decimal("300"), "fonte": "x",
            "em": _EM}
    assert _FP(moeda=" bnb ", **base) == _FP(moeda="BNB", **base)


def test_b7_id_continua_fora_da_impressao():
    """Regra preservada: dois ids para o MESMO conteúdo não divergem."""
    base = {"moeda": "BNB", "quantidade": Decimal("2"), "rate": Decimal("300"),
            "fonte": "x", "em": _EM}
    assert _FP(**base, valuation_id="A") == _FP(**base, valuation_id="B")
