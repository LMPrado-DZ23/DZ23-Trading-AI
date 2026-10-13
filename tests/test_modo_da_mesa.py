"""C019 — Modo da mesa e posse exclusiva de execução (workstream #29).

Windows local e Oracle ARM64 compartilham o MESMO núcleo. Duas máquinas
apontando para o mesmo estado não podem executar a mesma mesa ao mesmo
tempo: quem executa é quem segura a POSSE, e o dono tem estados claros —
ativa, observador (analisa e propõe SEM executar), pausada e bloqueada
(segurança, com desbloqueio explícito).

Fail-closed: estado desconhecido no disco vira BLOQUEADA, não "livre".
"""
from __future__ import annotations

import importlib
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.desk.modo import (
    TTL_POSSE_SEGUNDOS,
    EstadoDaMesa,
    ModoBloqueado,
    ModoDaMesa,
    PosseDeExecucao,
)
from tradingagents.desk.storage import ArmazemDeEstado


class RelogioFalso:
    def __init__(self):
        self.agora = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.agora

    def avancar(self, segundos: float) -> None:
        self.agora += timedelta(seconds=segundos)


def _armazem(tmp_path) -> ArmazemDeEstado:
    return ArmazemDeEstado(diretorio=tmp_path / "estado")


class TestModoDaMesa:
    def test_padrao_de_fabrica_e_ativo(self, tmp_path):
        modo = ModoDaMesa(_armazem(tmp_path), "legacy")
        assert modo.estado is EstadoDaMesa.ATIVA
        ok, motivo = modo.pode_executar()
        assert ok and motivo == ""

    def test_observador_nao_executa_mas_propoe(self, tmp_path):
        modo = ModoDaMesa(_armazem(tmp_path), "legacy")
        modo.definir("observador", por="dono")
        assert modo.estado is EstadoDaMesa.OBSERVADOR
        ok, motivo = modo.pode_executar()
        assert ok is False and "observador" in motivo
        # volta a ativa sem cerimônia: observador não é bloqueio
        modo.definir("ativa")
        assert modo.pode_executar()[0] is True

    def test_pausada_nao_executa(self, tmp_path):
        modo = ModoDaMesa(_armazem(tmp_path), "legacy")
        modo.definir(EstadoDaMesa.PAUSADA, motivo="parada pelo dono")
        ok, motivo = modo.pode_executar()
        assert ok is False and "pausada" in motivo

    def test_bloqueada_exige_desbloqueio_explicito(self, tmp_path):
        modo = ModoDaMesa(_armazem(tmp_path), "legacy")
        modo.bloquear("reconciliação pendente")
        assert modo.estado is EstadoDaMesa.BLOQUEADA
        # tentar "voltar ao normal" sem desbloqueio é RECUSADO
        with pytest.raises(ModoBloqueado):
            modo.definir("ativa")
        assert modo.estado is EstadoDaMesa.BLOQUEADA
        # com desbloqueio explícito, sai — e só então
        modo.definir("ativa", desbloqueio=True, por="dono")
        assert modo.estado is EstadoDaMesa.ATIVA

    def test_persistencia_entre_instancias(self, tmp_path):
        arm = _armazem(tmp_path)
        ModoDaMesa(arm, "legacy").definir("observador", motivo="teste de disco")
        outra = ModoDaMesa(arm, "legacy")
        assert outra.estado is EstadoDaMesa.OBSERVADOR
        assert "teste de disco" in outra.resumo()["motivo"]

    def test_estado_desconhecido_no_disco_vira_bloqueada(self, tmp_path):
        arm = _armazem(tmp_path)
        arm.salvar("legacy", "modo", {"estado": "turbo", "motivo": "?"})
        modo = ModoDaMesa(arm, "legacy")
        assert modo.estado is EstadoDaMesa.BLOQUEADA  # fail-closed
        assert modo.pode_executar()[0] is False

    def test_estado_invalido_recusado_com_422_na_origem(self, tmp_path):
        modo = ModoDaMesa(_armazem(tmp_path), "legacy")
        with pytest.raises(ValueError):
            modo.definir("turbo")

    def test_resumo_traz_rotulo_e_motivo_para_o_painel(self, tmp_path):
        modo = ModoDaMesa(_armazem(tmp_path), "legacy")
        r = modo.resumo()
        assert r["estado"] == "ativa"
        assert r["estado_label"]
        assert r["pode_executar"] is True


class TestPosseDeExecucao:
    def test_primeiro_dono_assume_e_renova(self, tmp_path):
        relogio = RelogioFalso()
        p = PosseDeExecucao(_armazem(tmp_path), "legacy", dono="pc-dono",
                            agora=relogio)
        ok, _ = p.assumir()
        assert ok and p.possuo()
        fim1 = p.atual()["expira_em"]
        relogio.avancar(60)
        ok, _ = p.assumir()          # de novo = renovação
        assert ok and p.atual()["expira_em"] > fim1

    def test_segundo_dono_recusado_com_o_nome_do_primeiro(self, tmp_path):
        relogio = RelogioFalso()
        arm = _armazem(tmp_path)
        PosseDeExecucao(arm, "legacy", dono="pc-dono", agora=relogio).assumir()
        outro = PosseDeExecucao(arm, "legacy", dono="oracle-01", agora=relogio)
        ok, motivo = outro.assumir()
        assert ok is False and "pc-dono" in motivo
        assert outro.possuo() is False and outro.atual()["dono"] == "pc-dono"

    def test_expiracao_libera_a_posse_por_ttl(self, tmp_path):
        relogio = RelogioFalso()
        arm = _armazem(tmp_path)
        PosseDeExecucao(arm, "legacy", dono="pc-dono", agora=relogio).assumir()
        relogio.avancar(TTL_POSSE_SEGUNDOS + 1)
        outro = PosseDeExecucao(arm, "legacy", dono="oracle-01", agora=relogio)
        ok, _ = outro.assumir()
        assert ok is True and outro.possuo()

    def test_libertar_devolve_a_posse(self, tmp_path):
        relogio = RelogioFalso()
        arm = _armazem(tmp_path)
        p = PosseDeExecucao(arm, "legacy", dono="pc-dono", agora=relogio)
        p.assumir()
        p.libertar()
        assert p.atual() is None
        outro = PosseDeExecucao(arm, "legacy", dono="oracle-01", agora=relogio)
        assert outro.assumir()[0] is True

    def test_resumo_para_o_painel(self, tmp_path):
        relogio = RelogioFalso()
        arm = _armazem(tmp_path)
        p = PosseDeExecucao(arm, "legacy", dono="pc-dono", agora=relogio)
        assert p.resumo()["valida"] is False
        p.assumir()
        r = p.resumo()
        assert r["valida"] is True and r["sou_eu"] is True
        assert r["dono"] == "pc-dono" and r["expira_em_iso"]


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"),
                    reason="sem DATABASE_URL — caminho postgres roda no CI")
class TestPosseNoPostgres:
    def test_corrida_entre_dois_donos_so_um_ganha(self):
        ArmazemDeEstado(os.environ["DATABASE_URL"], diretorio=None)
        arm = ArmazemDeEstado(os.environ["DATABASE_URL"], diretorio=None)
        p1 = PosseDeExecucao(arm, "cidadao-teste", dono="a")
        p2 = PosseDeExecucao(arm, "cidadao-teste", dono="b")
        assert arm.backend == "postgres", arm.aviso
        assert p1.assumir()[0] is True
        assert p2.assumir()[0] is False
        assert p1.renovar() is True


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import service.app as m
    importlib.reload(m)
    m._FAILS.clear()
    m._DESKS.clear()
    from fastapi.testclient import TestClient
    return TestClient(m.app), m


H = {"Authorization": "Bearer s3nh4"}


class TestGuardaNoServico:
    def test_status_mostra_modo_ativo_e_posse(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        st = c.get("/v1/desk/status", headers=H).json()
        assert st["modo"]["estado"] == "ativa"
        assert st["modo"]["pode_executar"] is True
        assert "posse" in st["modo"]

    def test_observador_propoe_sem_submeter_ordem(self, tmp_path, monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        r = c.post("/v1/desk/modo", headers=H, json={"estado": "observador"})
        assert r.status_code == 200 and r.json()["estado"] == "observador"
        r = c.post("/v1/desk/cycle", headers=H)
        assert r.status_code == 200
        out = r.json()
        assert out["modo"] == "observador"
        assert "propostas" in out and out["ordens"] == []
        assert "nenhuma ordem" in out["resumo"]
        # a mesa REALMENTE não submeteu nada
        d = m._DESKS["legacy"]
        assert d["coordinator"].orders == {}

    def test_pausada_bloqueia_ciclo_com_motivo(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        c.post("/v1/desk/modo", headers=H, json={"estado": "pausada"})
        r = c.post("/v1/desk/cycle", headers=H)
        assert r.status_code == 409 and "pausada" in r.json()["detail"]
        r = c.post("/v1/desk/scheduler/start", headers=H)
        assert r.status_code == 409 and "pausada" in r.json()["detail"]

    def test_bloqueada_exige_desbloqueio_na_rota(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        r = c.post("/v1/desk/modo", headers=H,
                   json={"estado": "bloqueada", "motivo": "auditoria"})
        assert r.status_code == 200
        r = c.post("/v1/desk/modo", headers=H, json={"estado": "ativa"})
        assert r.status_code == 409 and "desbloqueio" in r.json()["detail"]
        r = c.post("/v1/desk/modo", headers=H,
                   json={"estado": "ativa", "desbloqueio": True})
        assert r.status_code == 200 and r.json()["estado"] == "ativa"
        assert c.post("/v1/desk/cycle", headers=H).status_code == 200

    def test_modo_invalido_422(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        r = c.post("/v1/desk/modo", headers=H, json={"estado": "turbo"})
        assert r.status_code == 422

    def test_posse_de_outro_processo_bloqueia_execucao(self, tmp_path, monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        d = m._DESKS.get("legacy") or m._desk_boot("legacy")
        m._DESKS["legacy"] = d
        futuro = time.time() + 600
        d["armazem"].salvar("legacy", "posse", {
            "dono": "oracle-01", "expira_em": futuro,
            "expira_em_iso": datetime.fromtimestamp(
                futuro, timezone.utc).isoformat()})
        r = c.post("/v1/desk/cycle", headers=H)
        assert r.status_code == 409 and "oracle-01" in r.json()["detail"]
        r = c.post("/v1/desk/scheduler/start", headers=H)
        assert r.status_code == 409 and "oracle-01" in r.json()["detail"]

    def test_posse_vencida_libera_execucao(self, tmp_path, monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        d = m._DESKS.get("legacy") or m._desk_boot("legacy")
        m._DESKS["legacy"] = d
        d["armazem"].salvar("legacy", "posse", {
            "dono": "oracle-01", "expira_em": time.time() - 5,
            "expira_em_iso": "2026-10-13T00:00:00+00:00"})
        assert c.post("/v1/desk/cycle", headers=H).status_code == 200
        # e a posse passou a ser deste processo
        assert d["armazem"].carregar("legacy", "posse")["dono"] != "oracle-01"

    def test_reinicio_preserva_modo(self, tmp_path, monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        c.post("/v1/desk/modo", headers=H, json={"estado": "observador"})
        m._desk_reset("legacy")
        st = c.get("/v1/desk/status", headers=H).json()
        assert st["modo"]["estado"] == "observador"

    def test_painel_tem_o_controle_de_modo(self):
        from pathlib import Path
        html = Path("service/static/index.html").read_text(encoding="utf-8")
        for marcador in ("Modo da mesa", "modoMesa", "deskModo(",
                         "Posse de execução", "desbloquearMesa("):
            assert marcador in html, f"faltou no painel: {marcador}"


class TestLacoRespeitaModo:
    def _stub(self, tmp_path):
        from tradingagents.desk.scheduler import DeskScheduler

        class Orch:
            def open_exposure(self):
                return set()

            def run_cycle(self, snaps, prices):   # pragma: no cover
                raise AssertionError("não deveria rodar sem snapshots")

        class Coord:
            def pending_reconciliation(self):
                return []

            def submit(self, intent):             # pragma: no cover
                raise AssertionError("observador/bloqueado não submete")

        self.guarda = {"acao": "observar", "motivo": ""}
        return DeskScheduler(
            Orch(), Coord(), snapshot_provider=lambda: [], price_provider=lambda: {},
            state_path=tmp_path / "state.json",
            guarda_operacao=lambda: (self.guarda["acao"], self.guarda["motivo"]),
            interval_seconds=0)

    @staticmethod
    def _esperar(cond, limite=4.0):
        fim = time.time() + limite
        while time.time() < fim:
            if cond():
                return True
            time.sleep(0.02)
        return False

    def test_laco_observa_sem_executar_e_bloqueia_quando_manda(self, tmp_path):
        sched = self._stub(tmp_path)
        sched.start()
        try:
            assert self._esperar(lambda: sched.state.cycles_run >= 1)
            assert sched.state.ultimo_modo == "observador"
            assert sched.state.ultimo_bloqueio == ""
            # agora o guarda manda bloquear: o laço para de ciclar
            self.guarda.update(acao="bloquear", motivo="mesa pausada")
            assert self._esperar(lambda: sched.state.ultimo_bloqueio != "")
            ciclos = sched.state.cycles_run
            time.sleep(0.2)
            assert sched.state.cycles_run == ciclos
            # e volta: sai do bloqueio e volta a ciclar
            self.guarda.update(acao="executar", motivo="")
            assert self._esperar(lambda: sched.state.cycles_run > ciclos)
            assert sched.status()["ultimo_bloqueio"] == ""
        finally:
            sched.stop()
