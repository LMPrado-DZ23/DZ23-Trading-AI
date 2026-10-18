"""Contrato fail-closed da futura execução binária IQ Option.

Estes testes não abrem sessão MCP e nunca chamam ``place_trade``. Eles fixam
o planejamento exato e a reconciliação que precisam existir antes de ligar a
IQ Option ao OMS.
"""
from decimal import Decimal

import pytest

from tradingagents.brokers.iq_option_binary import (
    BinaryContractError,
    BinaryReconciliationState,
    plan_binary_trade,
    reconcile_binary_trade,
)


def _assets(**overrides):
    asset = {
        "asset_id": 76,
        "symbol": "EUR/USD",
        "is_open": True,
        "min_amount": "1",
        "max_amount": "1000",
        "profit_percent": 85,
        "expirations": [1_800_000_000, 1_800_000_300],
    }
    asset.update(overrides)
    return {"assets": [asset]}


def test_planeja_argumentos_exatos_oferecidos_sem_chamar_trade():
    plan = plan_binary_trade(
        _assets(), balance_id=912345, instrument="eurusd",
        direction="call", amount="25.50",
        expiration=1_800_000_300, min_profit_percent=80,
    )

    assert plan.arguments == {
        "balance_id": 912345,
        "asset_id": 76,
        "direction": "call",
        "amount": 25.5,
        "profit_percent": 85,
        "expired": 1_800_000_300,
    }
    assert plan.instrument == "EUR/USD"
    assert plan.amount == Decimal("25.50")


@pytest.mark.parametrize(
    ("overrides", "kwargs", "message"),
    [
        ({"is_open": False}, {}, "fechado"),
        ({"profit_percent": 79}, {}, "payout"),
        ({"expirations": [1_800_000_000]}, {}, "vencimento"),
        ({"min_amount": "30"}, {}, "limites"),
        ({"max_amount": "20"}, {}, "limites"),
        ({"asset_id": True}, {}, "asset_id"),
        ({"profit_percent": 85.5}, {}, "payout"),
        ({}, {"direction": "buy"}, "direção"),
        ({}, {"amount": "NaN"}, "valor"),
    ],
)
def test_planejamento_recusa_qualquer_parametro_inventado_ou_inseguro(
        overrides, kwargs, message):
    request = {
        "balance_id": 912345, "instrument": "EUR/USD",
        "direction": "put", "amount": "25",
        "expiration": 1_800_000_300, "min_profit_percent": 80,
    }
    request.update(kwargs)
    with pytest.raises(BinaryContractError, match=message):
        plan_binary_trade(_assets(**overrides), **request)


def test_ativo_duplicado_ou_ausente_falha_fechado():
    duplicate = _assets()["assets"] * 2
    with pytest.raises(BinaryContractError, match="único"):
        plan_binary_trade(
            {"assets": duplicate}, balance_id=912345,
            instrument="EUR/USD", direction="call",
            amount="25", expiration=1_800_000_300,
            min_profit_percent=80,
        )
    with pytest.raises(BinaryContractError, match="único"):
        plan_binary_trade(
            _assets(), balance_id=912345, instrument="GBP/USD",
            direction="call", amount="25",
            expiration=1_800_000_300, min_profit_percent=80,
        )


def test_reconciliacao_prefere_posicao_aberta_identificada():
    result = reconcile_binary_trade(
        position_id="pos-7",
        positions_payload={"positions": [{
            "position_id": "pos-7", "balance_id": 912345,
            "asset_id": 76, "direction": "call", "amount": "25.50",
            "expiration": 1_800_000_300,
        }]},
        history_payload={"positions": []},
    )

    assert result.state is BinaryReconciliationState.OPEN
    assert result.position_id == "pos-7"
    assert result.amount == Decimal("25.50")
    assert result.result is None


@pytest.mark.parametrize("provider_result", ["win", "loss", "equal", "canceled"])
def test_reconciliacao_liquidada_preserva_resultado_oficial(provider_result):
    result = reconcile_binary_trade(
        position_id="pos-8", positions_payload={"positions": []},
        history_payload={"positions": [{
            "position_id": "pos-8", "balance_id": 912345,
            "asset_id": 76, "direction": "put", "amount": "10",
            "result": provider_result, "profit": "8.50",
            "expiration": 1_800_000_300,
        }]},
    )

    assert result.state is BinaryReconciliationState.SETTLED
    assert result.result == provider_result
    assert result.profit == Decimal("8.50")


def test_ausencia_na_janela_nao_prova_que_operacao_nao_existe():
    result = reconcile_binary_trade(
        position_id="pos-fora-da-janela",
        positions_payload={"positions": []},
        history_payload={"positions": []},
    )

    assert result.state is BinaryReconciliationState.UNKNOWN
    assert result.result is None


def test_reconciliacao_recusa_identidade_duplicada_e_payload_malformado():
    duplicate = {
        "positions": [{"position_id": "pos-9", "amount": "10"}] * 2,
    }
    with pytest.raises(BinaryContractError, match="duplicada"):
        reconcile_binary_trade(
            position_id="pos-9", positions_payload=duplicate,
            history_payload={"positions": []},
        )
    with pytest.raises(BinaryContractError, match="inválida"):
        reconcile_binary_trade(
            position_id="pos-9", positions_payload={"positions": "segredo"},
            history_payload={"positions": []},
        )
