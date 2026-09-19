"""Pagamentos concorrentes (§2): dois charge_ids distintos do mesmo tenant,
simultâneos, NÃO podem se sobrescrever — a assinatura recebe os DOIS períodos."""
from __future__ import annotations

import sqlite3
import threading

import pytest

from tradingagents.billing.store import ChargeConflict, SubscriptionStore
from tradingagents.billing.woovi import _add_months


def test_dois_pagamentos_concorrentes_somam_periodos(tmp_path):
    db = tmp_path / "s.db"
    st = SubscriptionStore(db)
    st.record_paid_charge("c0", "t", "dz23_monthly", 15000, "BRL")   # base = +1 mês
    base = st.get("t").expires_at

    inicio = threading.Barrier(2)
    erros: list[Exception] = []

    def pagar(cid: str) -> None:
        try:
            inicio.wait()
            st.record_paid_charge(cid, "t", "dz23_monthly", 15000, "BRL")
        except Exception as exc:               # noqa: BLE001
            erros.append(exc)

    t1 = threading.Thread(target=pagar, args=("c1",))
    t2 = threading.Thread(target=pagar, args=("c2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not erros, f"erros nos threads: {erros}"
    with sqlite3.connect(db) as con:
        n = con.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0]
    assert n == 3                              # c0, c1, c2 no ledger (nenhum perdido)
    # os DOIS pagamentos concorrentes somaram (sem lost update): base + 2 meses.
    assert st.get("t").expires_at == _add_months(base, 2)


def test_event_id_unico_sequencial(tmp_path):
    # §3: mesmo event_id sob charge_id DIFERENTE => conflito (não recredita).
    st = SubscriptionStore(tmp_path / "s.db")
    st.record_paid_charge("c1", "t", "dz23_monthly", 15000, "BRL", event_id="ev-1")
    with pytest.raises(ChargeConflict):
        st.record_paid_charge("c2", "t", "dz23_monthly", 15000, "BRL", event_id="ev-1")


def test_event_id_vazio_legado_nao_conflita(tmp_path):
    # event_id legado vazio ('') fica FORA do índice único parcial.
    st = SubscriptionStore(tmp_path / "s.db")
    a, _ = st.record_paid_charge("c1", "t", "dz23_monthly", 15000, "BRL", event_id="")
    b, _ = st.record_paid_charge("c2", "t", "dz23_monthly", 15000, "BRL", event_id="")
    assert a and b                                   # ambos aplicam (sem colisão)


def test_mesmo_event_id_concorrente_um_so_aplica(tmp_path):
    # §3 concorrência: dois charges distintos, MESMO event_id, ao mesmo tempo.
    db = tmp_path / "s.db"
    st = SubscriptionStore(db)
    inicio = threading.Barrier(2)
    conflitos: list[str] = []
    aplicados: list[str] = []

    def pagar(cid: str) -> None:
        inicio.wait()
        try:
            st.record_paid_charge(cid, "t", "dz23_monthly", 15000, "BRL", event_id="EV")
            aplicados.append(cid)
        except ChargeConflict:
            conflitos.append(cid)

    t1 = threading.Thread(target=pagar, args=("cA",))
    t2 = threading.Thread(target=pagar, args=("cB",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert len(aplicados) == 1 and len(conflitos) == 1      # exatamente um aplicou
    with sqlite3.connect(db) as con:
        n = con.execute("SELECT COUNT(*) FROM webhook_events WHERE event_id='EV'").fetchone()[0]
    assert n == 1                                          # unicidade garantida


def test_fingerprint_valor_divergente_conflita(tmp_path):
    # §3: fingerprint SEMÂNTICO inclui o valor — mesmo charge_id, mesmo plano,
    # VALOR diferente => conflito (não recredita). Mata mutante que solta o valor.
    st = SubscriptionStore(tmp_path / "s.db")
    st.record_paid_charge("c1", "t", "dz23_monthly", 15000, "BRL")
    with pytest.raises(ChargeConflict):
        st.record_paid_charge("c1", "t", "dz23_monthly", 15001, "BRL")   # só o valor muda


def test_fingerprint_moeda_divergente_conflita(tmp_path):
    # §3: a moeda também compõe o fingerprint semântico.
    st = SubscriptionStore(tmp_path / "s.db")
    st.record_paid_charge("c1", "t", "dz23_monthly", 15000, "BRL")
    with pytest.raises(ChargeConflict):
        st.record_paid_charge("c1", "t", "dz23_monthly", 15000, "USD")   # só a moeda muda


def test_conflito_sobrevive_restart(tmp_path):
    # §3: o incidente de conflito é DURÁVEL — sobrevive a um "restart" (nova
    # instância do store lê o ledger de conflitos gravado).
    db = tmp_path / "s.db"
    st = SubscriptionStore(db)
    st.record_paid_charge("c1", "t", "dz23_monthly", 15000, "BRL")
    with pytest.raises(ChargeConflict):
        st.record_paid_charge("c1", "t", "dz23_annual", 120000, "BRL")   # mesmo charge, divergente
    st2 = SubscriptionStore(db)                     # "restart"
    assert st2.count_conflicts("c1") >= 1           # incidente persistido
