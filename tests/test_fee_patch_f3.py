"""F3 — valoração POR COMPONENTE (fee_component_id é a identidade canônica).

Uma cotação por ocorrência: dois BNB cobrados em horários diferentes valem
a soma das suas conversões, nunca uma cotação única sobre o agregado.
`fill_id` é apenas procedência — um fill pode ter vários componentes, cada um
com identidade, moeda, valor e valoração próprios.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import DeskMandate, Mode
from tradingagents.desk.risk import RecoveryConflict, RiskEngine


class Ator:
    actor_id = "risk-manager-1"
    capabilities = frozenset({"RESOLVE_RISK_INCIDENT", "VALUE_FEE_INCIDENT"})


def sistema(capital="10000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT",),
                    capital=Decimal(capital), slippage_buffer_pct=Decimal("0"))
    livro = Ledger(opening_cash=m.capital)
    return m, RiskEngine(m, carteira=livro), livro


def cobrar(livro, fees, ref="a", fill_id=""):
    """Cobra `fees` (lista de (qtd, moeda)) num fill e devolve os ids novos,
    na ordem em que foram criados."""
    antes = {c["fee_component_id"]
             for moeda in {m for _, m in fees}
             for c in livro.fee_components(moeda)}
    livro.reserve(Decimal("100"), ref=ref, reservation_id=f"R{ref}")
    livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                   reserved=Decimal("100"), ref=ref, agent_id="A",
                   reservation_id=f"R{ref}",
                   native_fees=[(Decimal(q), m) for q, m in fees],
                   fill_id=fill_id or ref)
    novos = []
    for moeda in {m for _, m in fees}:
        for c in livro.fee_components(moeda):
            if c["fee_component_id"] not in antes:
                novos.append(c)
    novos.sort(key=lambda c: (c["fill_id"], c["fee_component_id"]))
    return [c["fee_component_id"] for c in novos]


def inc_bnb(risco, qtd, fill_id):
    risco.registrar_taxa_nao_valorada(moeda="BNB", quantidade=Decimal(qtd),
                                      fill_id=fill_id)
    return next(i for i in risco.incidentes_abertos()
               if i["type"] == "FEE_VALUATION_UNKNOWN" and i["fee_currency"] == "BNB")


_T1 = "2026-09-14T10:00:00+00:00"
_T2 = "2026-09-14T15:00:00+00:00"


def test_a_dois_bnb_horarios_diferentes_totalizam_900():
    """A: 1 BNB @300 + 1 BNB @600 = 900, nunca 600 nem 1200."""
    m, risco, livro = sistema()
    (a,) = cobrar(livro, [("1", "BNB")], ref="fa", fill_id="F1")
    (b,) = cobrar(livro, [("1", "BNB")], ref="fb", fill_id="F2")
    risco.registrar_taxa_nao_valorada(moeda="BNB", quantidade=Decimal("1"),
                                      fill_id="F1")
    inc = inc_bnb(risco, "1", "F2")
    risco.valorar_taxa(inc["incident_id"], cotacoes={
        a: {"rate": Decimal("300"), "fonte": "binance", "em": _T1},
        b: {"rate": Decimal("600"), "fonte": "binance", "em": _T2}})
    risco.aplicar_valoracao(inc["incident_id"], actor=Ator(), evidence="e")
    assert livro.balance("FEES:USDT") == Decimal("900")


def test_b_dois_componentes_mesmo_fill_rates_diferentes():
    """B: dois componentes do MESMO fill recebem cotações diferentes."""
    m, risco, livro = sistema()
    a, b = cobrar(livro, [("1", "BNB"), ("1", "BNB")], ref="f", fill_id="F1")
    inc = inc_bnb(risco, "2", "F1")
    risco.valorar_taxa(inc["incident_id"], cotacoes={
        a: {"rate": Decimal("300"), "fonte": "binance", "em": _T1},
        b: {"rate": Decimal("600"), "fonte": "binance", "em": _T2}})
    risco.aplicar_valoracao(inc["incident_id"], actor=Ator(), evidence="e")
    assert livro.balance("FEES:USDT") == Decimal("900")


def test_c_bnb_e_sol_no_mesmo_fill_sao_independentes():
    """C: BNB e SOL no mesmo fill são incidentes/valorações separados."""
    m, risco, livro = sistema()
    ids = cobrar(livro, [("1", "BNB"), ("2", "SOL")], ref="f", fill_id="F1")
    bnb = next(c for c in livro.fee_components("BNB")
               if c["fee_component_id"] in ids)
    sol = next(c for c in livro.fee_components("SOL")
               if c["fee_component_id"] in ids)
    inc_b = inc_bnb(risco, "1", "F1")
    risco.registrar_taxa_nao_valorada(moeda="SOL", quantidade=Decimal("2"),
                                      fill_id="F1")
    inc_s = next(i for i in risco.incidentes_abertos()
                 if i["fee_currency"] == "SOL")
    risco.valorar_taxa(inc_b["incident_id"], cotacoes={
        bnb["fee_component_id"]: {"rate": Decimal("300"), "fonte": "x", "em": _T1}})
    risco.aplicar_valoracao(inc_b["incident_id"], actor=Ator(), evidence="e")
    risco.valorar_taxa(inc_s["incident_id"], cotacoes={
        sol["fee_component_id"]: {"rate": Decimal("50"), "fonte": "x", "em": _T1}})
    risco.aplicar_valoracao(inc_s["incident_id"], actor=Ator(), evidence="e")
    assert livro.balance("FEES:USDT") == Decimal("400")   # 1*300 + 2*50


def test_d_componente_novo_depois_da_proposta_fica_fora():
    """D: componente que chega depois da proposta não entra no batch antigo."""
    m, risco, livro = sistema()
    (a,) = cobrar(livro, [("1", "BNB")], ref="fa", fill_id="F1")
    inc = inc_bnb(risco, "1", "F1")
    risco.valorar_taxa(inc["incident_id"], cotacoes={
        a: {"rate": Decimal("300"), "fonte": "x", "em": _T1}})
    (b,) = cobrar(livro, [("1", "BNB")], ref="fb", fill_id="F2")  # depois
    risco.aplicar_valoracao(inc["incident_id"], actor=Ator(), evidence="e")
    assert livro.balance("FEES:USDT") == Decimal("300")           # só o A
    ainda = [c["fee_component_id"]
             for c in livro.fee_components("BNB", apenas_nao_valorados=True)]
    assert b in ainda


def test_e_retry_identico_nao_duplica():
    """E: reaplicar com o mesmo conteúdo não lança de novo."""
    m, risco, livro = sistema()
    (a,) = cobrar(livro, [("1", "BNB")], ref="fa", fill_id="F1")
    inc = inc_bnb(risco, "1", "F1")
    risco.valorar_taxa(inc["incident_id"], cotacoes={
        a: {"rate": Decimal("300"), "fonte": "x", "em": _T1}})
    risco.aplicar_valoracao(inc["incident_id"], actor=Ator(), evidence="e",
                            valuation_id="val-1")
    saldo = livro.balance("FEES:USDT")
    risco.aplicar_valoracao(inc["incident_id"], actor=Ator(), evidence="retry",
                            valuation_id="val-1")
    assert livro.balance("FEES:USDT") == saldo


def test_f_retry_divergente_gera_conflito():
    """F: reaplicar com cotação diferente é conflito, não sobrescrita."""
    m, risco, livro = sistema()
    (a,) = cobrar(livro, [("1", "BNB")], ref="fa", fill_id="F1")
    inc = inc_bnb(risco, "1", "F1")
    # proposta feita com o componente ainda NÃO valorado
    risco.valorar_taxa(inc["incident_id"], cotacoes={
        a: {"rate": Decimal("300"), "fonte": "x", "em": _T1}})
    # um lançamento DIVERGENTE já existe no livro (processo anterior)
    livro.fee_valuation(moeda="BNB", quantidade=Decimal("1"), rate=Decimal("999"),
                        fonte="y", em=_T2, fee_component_ids=[a],
                        incident_id=inc["incident_id"], valuation_id="val-x")
    with pytest.raises(RecoveryConflict):
        risco.aplicar_valoracao(inc["incident_id"], actor=Ator(), evidence="e")


def test_g_restart_preserva_cada_valoracao():
    """G: após restart, cada valoração por componente sobrevive."""
    m, risco, livro = sistema()
    a, b = cobrar(livro, [("1", "BNB"), ("1", "BNB")], ref="f", fill_id="F1")
    inc = inc_bnb(risco, "2", "F1")
    risco.valorar_taxa(inc["incident_id"], cotacoes={
        a: {"rate": Decimal("300"), "fonte": "x", "em": _T1},
        b: {"rate": Decimal("600"), "fonte": "x", "em": _T2}})
    risco.aplicar_valoracao(inc["incident_id"], actor=Ator(), evidence="e")
    restaurado = Ledger.from_transactions(livro.export_transactions(),
                                          currency="USDT")
    vals = [t for t in restaurado.export_transactions()
            if t["kind"] == "fee_valuation"]
    assert len(vals) == 2
    montantes = sorted(Decimal(t["functional_amount"]) for t in vals)
    assert montantes == [Decimal("300"), Decimal("600")]


def test_h_incidente_nao_resolve_com_componente_pendente():
    """H: se um componente não foi valorado, o incidente segue aberto."""
    m, risco, livro = sistema()
    a, b = cobrar(livro, [("1", "BNB"), ("1", "BNB")], ref="f", fill_id="F1")
    inc = inc_bnb(risco, "2", "F1")
    # só um componente recebe cotação
    risco.valorar_taxa(inc["incident_id"], cotacoes={
        a: {"rate": Decimal("300"), "fonte": "x", "em": _T1}})
    risco.aplicar_valoracao(inc["incident_id"], actor=Ator(), evidence="e")
    abertos = [i["incident_id"] for i in risco.incidentes_abertos()]
    assert inc["incident_id"] in abertos, "resolveu com componente pendente"


def test_i_valoracao_nao_usa_fill_id_como_identidade():
    """I: dois componentes do MESMO fill_id são valorados separadamente —
    a identidade é o fee_component_id, não o fill_id."""
    m, risco, livro = sistema()
    a, b = cobrar(livro, [("1", "BNB"), ("1", "BNB")], ref="f", fill_id="F1")
    assert a != b
    inc = inc_bnb(risco, "2", "F1")
    risco.valorar_taxa(inc["incident_id"], cotacoes={
        a: {"rate": Decimal("300"), "fonte": "x", "em": _T1},
        b: {"rate": Decimal("600"), "fonte": "x", "em": _T2}})
    risco.aplicar_valoracao(inc["incident_id"], actor=Ator(), evidence="e")
    vals = [t for t in livro.export_transactions() if t["kind"] == "fee_valuation"]
    # duas valorações distintas, uma por componente, mesmo fill_id
    ids = {t["fee_component_ids"][0] for t in vals}
    assert ids == {a, b}


def test_j_cotacao_agregada_em_varias_ocorrencias_falha():
    """J: uma cotação única aplicada ao agregado de várias ocorrências falha."""
    m, risco, livro = sistema()
    cobrar(livro, [("1", "BNB")], ref="fa", fill_id="F1")
    cobrar(livro, [("1", "BNB")], ref="fb", fill_id="F2")
    risco.registrar_taxa_nao_valorada(moeda="BNB", quantidade=Decimal("1"),
                                      fill_id="F1")
    inc = inc_bnb(risco, "1", "F2")   # incidente agregado tem 2 componentes
    with pytest.raises(ValueError, match="cota|component|ocorr"):
        risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                           fonte="x", em=_T1)


def test_k_cada_componente_registra_source_e_valued_at():
    """Cada valoração de componente carrega a SUA fonte e o SEU instante —
    ignorá-los apagaria a procedência por ocorrência."""
    m, risco, livro = sistema()
    a, b = cobrar(livro, [("1", "BNB"), ("1", "BNB")], ref="f", fill_id="F1")
    inc = inc_bnb(risco, "2", "F1")
    risco.valorar_taxa(inc["incident_id"], cotacoes={
        a: {"rate": Decimal("300"), "fonte": "fonte-A", "em": _T1},
        b: {"rate": Decimal("600"), "fonte": "fonte-B", "em": _T2}})
    risco.aplicar_valoracao(inc["incident_id"], actor=Ator(), evidence="e")
    vals = {t["fee_component_ids"][0]: t
            for t in livro.export_transactions()
            if t["kind"] == "fee_valuation"}
    memo_a = " ".join(e["memo"] for e in vals[a]["entries"])
    memo_b = " ".join(e["memo"] for e in vals[b]["entries"])
    assert "fonte-A" in memo_a and _T1 in memo_a
    assert "fonte-B" in memo_b and _T2 in memo_b
