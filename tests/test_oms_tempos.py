"""MO-2-C — Carimbo temporal do OMS: latência por estágio e trilha por ordem.

Prova o que a missão exige: separação temporal entre criação (intenção),
ordem enviada, ordem aceita e execução confirmada — com persistência
desses carimbos pelo reinício. Registro antigo SEM carimbo fica
honestamente desconhecido (nada de carimbo retroativo inventado).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from tradingagents.desk.execution import (
    ExecutionCoordinator,
    OrderRecord,
    OrderState,
    PaperBroker,
    latencias_ms,
)
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import (
    Action,
    DeskMandate,
    Mode,
    TradeProposal,
    utcnow,
)
from tradingagents.desk.risk import RiskEngine

INSTR = "BTC/USDT"


def _dt(valor: str) -> datetime:
    return datetime.fromisoformat(valor)


def mandate(**kw):
    base = {"mandate_id": "m1", "owner": "prado", "account": "acct-1",
            "mode": Mode.PAPER, "instruments": (INSTR, "ETH/USDT"),
            "capital": Decimal("1000")}
    base.update(kw)
    return DeskMandate(**base)


def proposal(pct="0.10"):
    return TradeProposal(agent_id="trader-trend", strategy="trend_following",
                         instrument=INSTR, action=Action.BUY, reason="teste",
                         desired_exposure_pct=Decimal(pct),
                         valid_until=utcnow() + timedelta(minutes=5))


def setup(price="100", broker=None):
    m = mandate()
    risk = RiskEngine(m)
    ledger = Ledger(opening_cash=m.capital)
    coord = ExecutionCoordinator(m, ledger, risk, worker_id="w1",
                                 broker=broker or PaperBroker(Decimal(price)))
    res = risk.reserve(proposal(), Decimal(price))
    intent = risk.intent(res, Action.BUY, Mode.PAPER, "w1",
                         idempotency_key="idem-carimbo-1")
    return m, risk, ledger, coord, intent


class TestCarimbosDaOrdem:
    def test_ordem_nova_nasce_carimbada(self):
        *_, coord, intent = setup()
        rec = coord.submit(intent)          # PaperBroker: ACK + fill na resposta
        assert rec.criada_em and rec.enviada_em
        assert _dt(rec.criada_em) <= _dt(rec.enviada_em)
        assert rec.reconhecida_em and rec.primeiro_fill_em
        assert _dt(rec.reconhecida_em) <= _dt(rec.primeiro_fill_em)
        assert rec.terminal_em                       # FILLED é terminal
        assert rec.atualizada_em

    def test_cancelamento_carimba_transicoes_e_terminal(self):
        m = mandate()
        risk = RiskEngine(m)
        ledger = Ledger(opening_cash=m.capital)
        coord = ExecutionCoordinator(m, ledger, risk, worker_id="w1",
                                     broker=None)
        res = risk.reserve(proposal(), Decimal("100"))
        intent = risk.intent(res, Action.BUY, Mode.PAPER, "w1",
                             idempotency_key="idem-canc-carimbo")
        coord.orders["o1"] = OrderRecord(order_id="o1", intent=intent,
                                         state=OrderState.ACKNOWLEDGED)
        coord.request_cancel("o1")
        rec = coord.orders["o1"]
        assert rec.state is OrderState.CANCEL_PENDING
        assert rec.atualizada_em                     # transição carimbada
        coord.confirm_cancel("o1")
        assert rec.state is OrderState.CANCELLED
        assert rec.terminal_em

    def test_latencias_por_estagio(self):
        *_, coord, intent = setup()
        rec = coord.submit(intent)
        lat = latencias_ms(rec)
        assert set(lat) == {"criacao_ate_envio", "envio_ate_aceite",
                            "aceite_ate_execucao", "envio_ate_execucao"}
        for valor in lat.values():
            assert valor is None or valor >= 0

    def test_latencias_none_sem_carimbo(self):
        m = mandate()
        risk = RiskEngine(m)
        res = risk.reserve(proposal(), Decimal("100"))
        intent = risk.intent(res, Action.BUY, Mode.PAPER, "w1",
                             idempotency_key="idem-sem-carimbo")
        rec = OrderRecord(order_id="z", intent=intent)   # direto, sem carimbo
        assert all(v is None for v in latencias_ms(rec).values())

    def test_ack_sem_preenchimento_nao_inventa_latencia_de_execucao(self):
        """Aceita mas NÃO preenchida: as latências de execução ficam None —
        'sem preenchimento' é a resposta honesta, nunca zero."""
        class _CorretoraSoAceita:
            def submit(self, client_order_id, instrument, side, quantity,
                       final_state=None):
                return {"id": "x1", "status": "open", "filled": 0,
                        "average": 0, "trades": []}   # ACK, sem execução

        *_, coord, intent = setup(broker=_CorretoraSoAceita())
        rec = coord.submit(intent)
        assert rec.state is OrderState.ACKNOWLEDGED
        assert rec.reconhecida_em and not rec.primeiro_fill_em
        assert not rec.terminal_em
        lat = latencias_ms(rec)
        assert lat["criacao_ate_envio"] is not None
        assert lat["envio_ate_aceite"] is not None
        assert lat["aceite_ate_execucao"] is None
        assert lat["envio_ate_execucao"] is None


class TestPersistenciaDosCarimbos:
    def test_snapshot_guarda_carimbos_e_restore_reidrata(self):
        _, risk, ledger, coord, intent = setup()
        rec = coord.submit(intent)
        doc = coord.snapshot_estado()
        od = [o for o in doc["ordens"] if o["order_id"] == rec.order_id][0]
        assert od["criada_em"] == rec.criada_em
        assert od["primeiro_fill_em"] == rec.primeiro_fill_em
        assert od["terminal_em"] == rec.terminal_em

        coord2 = ExecutionCoordinator(mandate(), Ledger(
            opening_cash=Decimal("1000")), RiskEngine(mandate()),
            worker_id="w1", broker=None)
        coord2.restaurar_estado(doc)
        volta = coord2.orders[rec.order_id]
        assert volta.criada_em == rec.criada_em
        assert volta.primeiro_fill_em == rec.primeiro_fill_em
        assert volta.terminal_em == rec.terminal_em

    def test_restore_de_documento_antigo_nao_inventa_carimbo(self):
        _, risk, ledger, coord, intent = setup()
        rec = coord.submit(intent)
        doc = coord.snapshot_estado()
        for od in doc["ordens"]:            # documento ANTIGO: sem carimbos
            for chave in ("criada_em", "enviada_em", "reconhecida_em",
                          "primeiro_fill_em", "terminal_em",
                          "atualizada_em"):
                od.pop(chave, None)
        coord2 = ExecutionCoordinator(mandate(), Ledger(
            opening_cash=Decimal("1000")), RiskEngine(mandate()),
            worker_id="w1", broker=None)
        coord2.restaurar_estado(doc)
        volta = coord2.orders[rec.order_id]
        assert volta.criada_em == "" and volta.primeiro_fill_em == ""
        assert volta.terminal_em == ""
        assert latencias_ms(volta)["envio_ate_execucao"] is None


class TestTrilhaNaRotaEPainel:
    def test_sala_expoe_carimbos_e_latencias_na_ordem(self):
        from pathlib import Path
        raiz = Path(__file__).resolve().parents[1]
        fonte = (raiz / "service" / "app.py").read_text(encoding="utf-8")
        assert '"latencias_ms": latencias_ms(r)' in fonte
        assert '"criada_em": r.criada_em' in fonte

    def test_painel_mostra_trilha_temporal_da_ordem(self):
        from pathlib import Path
        raiz = Path(__file__).resolve().parents[1]
        painel = (raiz / "service" / "static" / "index.html").read_text(
            encoding="utf-8")
        assert "latencias_ms" in painel
        assert "envio_ate_execucao" in painel
        assert "carimbos UTC do OMS" in painel


class TestTrilhaNaMesa:
    """MO-2-C: a MESMA trilha alimenta a tabela 'Ordens' da Mesa — a rota
    JÁ EXISTENTE (/v1/desk/status) expõe criação, preenchimento e latência
    por estágio. Sem rota paralela."""

    def _client(self, tmp_path, monkeypatch):
        import importlib

        from fastapi.testclient import TestClient

        from service.api.ia_provedores import CATALOGO
        for p in CATALOGO:            # máquina do dono tem chave de IA real
            if p["chave_env"]:
                monkeypatch.delenv(p["chave_env"], raising=False)
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        return TestClient(m.app)

    def test_rota_da_mesa_expoe_carimbos_e_latencias_reais(
            self, tmp_path, monkeypatch):
        c = self._client(tmp_path, monkeypatch)
        H = {"Authorization": "Bearer s3nh4"}
        r = c.post("/v1/desk/cycle", headers=H).json()
        assert len(r["ordens"]) == 2
        st = c.get("/v1/desk/status", headers=H).json()
        assert st["orders"], "ciclo gerou ordens mas a Mesa não as mostra"
        for o in st["orders"]:
            for chave in ("criada_em", "enviada_em", "reconhecida_em",
                          "primeiro_fill_em", "terminal_em",
                          "atualizada_em"):
                assert o[chave], f"{o['id']}: carimbo {chave} ausente"
            lat = o["latencias_ms"]
            assert set(lat) == {"criacao_ate_envio", "envio_ate_aceite",
                                "aceite_ate_execucao", "envio_ate_execucao"}
            assert all(v is None or v >= 0 for v in lat.values())

    def test_painel_da_mesa_mostra_coluna_de_trilha(self):
        from pathlib import Path
        raiz = Path(__file__).resolve().parents[1]
        painel = (raiz / "service" / "static" / "index.html").read_text(
            encoding="utf-8")
        assert "Trilha (UTC)" in painel
        assert "function omsLatTxt(o)" in painel
        assert "omsLatTxt(o)" in painel          # usada na tabela da Mesa
