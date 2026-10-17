"""Resolucao privada do balance_id LIVE da IQ Option."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from service.iq_option_execution import (
    IqOptionExecutionPreparationError,
    resolve_iq_option_live_account_async,
)
from tests.test_iq_option_live_preparation import _env, _tools


def _run(coroutine):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


def test_resolve_balance_id_numerico_sem_expor_em_repr_ou_prova_publica():
    async def reader(_endpoint, _token, _arguments):
        return _tools(), {
            "balances": [{"type": "regular", "balance_id": "912345"}]}

    resolved = _run(resolve_iq_option_live_account_async(
        _env(LIVE_EXECUTION_ACCOUNT_ID="912345"), session_reader=reader))

    assert resolved.balance_id == 912345
    assert "912345" not in repr(resolved)
    public = resolved.public_proof()
    assert "balance_id" not in public
    assert "912345" not in str(public)
    assert public["broker_identity_verified"] is True


@pytest.mark.parametrize(
    "balance_id",
    [True, 0, -1, "", "abc", "12.5", "0", "-7"],
)
def test_resolver_recusa_id_nao_inteiro_positivo(balance_id):
    async def reader(_endpoint, _token, _arguments):
        return _tools(), {
            "balances": [{"type": "regular", "balance_id": balance_id}]}

    with pytest.raises(
            IqOptionExecutionPreparationError, match="resposta MCP passiva"):
        _run(resolve_iq_option_live_account_async(
            _env(
                LIVE_EXECUTION_ACCOUNT_ID=str(balance_id),
                IQ_OPTION_CONFIRM_UNIQUE_REGULAR_ACCOUNT=True,
            ),
            session_reader=reader))


def test_descoberta_unica_devolve_id_somente_ao_runtime():
    async def reader(_endpoint, _token, _arguments):
        return _tools(), {
            "balances": [{"type": "regular", "balance_id": 77}]}

    resolved = _run(resolve_iq_option_live_account_async(
        _env(
            LIVE_EXECUTION_ACCOUNT_ID="",
            IQ_OPTION_CONFIRM_UNIQUE_REGULAR_ACCOUNT=True,
        ),
        session_reader=reader,
    ))

    assert resolved.balance_id == 77
    assert "77" not in repr(resolved)


def test_multiplas_contas_continuam_fail_closed_sem_id_explicito():
    async def reader(_endpoint, _token, _arguments):
        return _tools(), {"balances": [
            {"type": "regular", "balance_id": 77},
            {"type": "regular", "balance_id": 88},
        ]}

    with pytest.raises(
            IqOptionExecutionPreparationError, match="exatamente uma"):
        _run(resolve_iq_option_live_account_async(
            _env(
                LIVE_EXECUTION_ACCOUNT_ID="",
                IQ_OPTION_CONFIRM_UNIQUE_REGULAR_ACCOUNT=True,
            ),
            session_reader=reader,
        ))
