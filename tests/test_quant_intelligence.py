"""Testes da camada quantitativa e de IA (itens 2–9 da ampliação).

Princípio dos testes: provar COMPORTAMENTO declarado, incluindo o que a
camada se RECUSA a fazer (promover para LIVE, comandar ordem, inventar
previsão sem modelo).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.quant import (
    NOMES_DE_FEATURES,
    CalibradorDeConfianca,
    ClassificadorDeRegime,
    ConselheiroQuantitativo,
    Desempenho,
    ModeloIndisponivel,
    MonitorDeDrift,
    ViolacaoDeGovernanca,
    Voz,
    auditar_aconselhador,
    comparar,
    consenso,
    disponibilidade,
    exigir_aconselhador,
    para_vetor,
    psi,
    retornos,
    vetor_de_features,
)


def serie(n=60, base=100.0, passo=1.0):
    return [base + i * passo for i in range(n)]


class TestFeatures:
    def test_vetor_completo_e_deterministico(self):
        d1 = vetor_de_features(serie(60))
        d2 = vetor_de_features(serie(60))
        assert d1 is not None and d1 == d2
        assert set(d1) == set(NOMES_DE_FEATURES)
        assert para_vetor(d1) == [d1[n] for n in NOMES_DE_FEATURES]

    def test_janela_insuficiente_vira_none(self):
        assert vetor_de_features([100.0] * 10) is None

    def test_retornos_simples(self):
        assert retornos([100.0, 110.0, 99.0]) == pytest.approx([0.1, -0.1])


class TestModelosExperimentais:
    def test_registro_de_disponibilidade(self):
        disp = disponibilidade()
        assert {"xgboost", "lightgbm", "torch"} <= set(disp)
        for info in disp.values():
            assert isinstance(info["instalado"], bool)
            assert "experimental" in info["papel"]

    def test_biblioteca_ausente_falha_explicita(self):
        c = ClassificadorDeRegime("biblioteca_que_nao_existe")
        assert c.disponivel is False
        with pytest.raises(ModeloIndisponivel):
            c.treinar([[0.1]], ["tendencia_alta"])

    def test_previsao_sem_treino_levanta(self):
        c = ClassificadorDeRegime("lightgbm")
        with pytest.raises(ModeloIndisponivel):
            c.prever_probabilidades([[0.1]])

    def test_lgbm_instalado_treina_e_preve(self):
        pytest.importorskip("lightgbm")
        c = ClassificadorDeRegime("lightgbm")
        X = [[i / 10, (i % 3) / 3] for i in range(12)]
        y = ["tendencia_alta" if i % 2 else "lateral" for i in range(12)]
        c.treinar(X, y)
        probs = c.prever_probabilidades([X[0]])[0]
        assert set(probs) == {"tendencia_alta", "lateral"}
        assert sum(probs.values()) == pytest.approx(1.0, abs=1e-6)


class TestCalibracao:
    def test_faixa_com_amostra_calibra(self):
        c = CalibradorDeConfianca()
        for _ in range(10):
            c.registrar(0.95, acertou=True)
        assert c.calibrar(0.95) == pytest.approx(1.0)

    def test_faixa_vazia_devolve_identidade(self):
        c = CalibradorDeConfianca()
        assert c.calibrar(0.55) == pytest.approx(0.55)

    def test_ece_mede_descalibracao(self):
        c = CalibradorDeConfianca()
        for _ in range(20):
            c.registrar(0.95, acertou=False)
        assert c.ece() == pytest.approx(0.95, abs=0.01)

    def test_ece_sem_amostras_e_none(self):
        assert CalibradorDeConfianca().ece() is None


class TestDrift:
    def test_psi_identidade_e_zero(self):
        r = serie(100)
        assert psi(r, r) == pytest.approx(0.0, abs=1e-9)

    def test_psi_deslocado_sinaliza(self):
        r = serie(100)
        deslocada = [x + 10 for x in r]
        assert psi(r, deslocada) > 0.25

    def test_monitor_gradua_niveis(self):
        r = serie(100)
        m = MonitorDeDrift(r)
        assert m.avaliar(r)["nivel"] == "estavel"
        assert m.avaliar([x + 20 for x in r])["deriva"] is True

    def test_monitor_amostra_insuficiente_nao_mente(self):
        m = MonitorDeDrift(serie(100))
        resultado = m.avaliar([1.0, 2.0])
        assert resultado["psi"] is None
        assert resultado["nivel"] == "indisponivel"
        assert resultado["deriva"] is False

    def test_psi_amostra_curta_levanta(self):
        with pytest.raises(ValueError):
            psi([1.0, 2.0], [3.0, 4.0])


class TestEnsemble:
    def test_maioria_compra(self):
        c = consenso([Voz("a", 1, 0.8), Voz("b", 1, 0.6), Voz("c", -1, 0.2)])
        assert c.direcao == "comprar"
        assert c.confianca == pytest.approx(0.75)

    def test_divisao_abstem(self):
        c = consenso([Voz("a", 1, 0.5), Voz("b", -1, 0.5)])
        assert c.direcao == "abster"
        assert c.liquido == pytest.approx(0.0)

    def test_limiar_respeitado(self):
        vozes = [Voz("a", 1, 0.4), Voz("b", 0, 0.6)]
        assert consenso(vozes).direcao == "comprar"
        assert consenso(vozes, limiar=0.5).direcao == "abster"

    def test_direcao_invalida_levanta(self):
        with pytest.raises(ValueError):
            consenso([Voz("a", 2, 0.5)])

    def test_calibrador_pesa_vozes(self):
        cal = CalibradorDeConfianca()
        for _ in range(5):
            cal.registrar(0.9, acertou=False)   # voz "a" vira peso zero
        c = consenso([Voz("a", 1, 0.9), Voz("b", -1, 0.4)], calibrador=cal)
        assert c.direcao == "vender"


class TestChampionChallenger:
    CAMPEAO = Desempenho("campeao", 60, Decimal("0.001"), Decimal("0.55"))

    def test_sem_amostra_minima_nao_promove(self):
        d = comparar(self.CAMPEAO, Desempenho("novo", 10, Decimal("0.02"), Decimal("0.60")))
        assert d.promover is False and d.destino is None and "amostra" in d.motivo

    def test_sem_margem_nao_promove(self):
        d = comparar(self.CAMPEAO, Desempenho("novo", 40, Decimal("0.0014"), Decimal("0.55")))
        assert d.promover is False and "margem" in d.motivo

    def test_desafiante_melhor_promove_para_paper(self):
        d = comparar(self.CAMPEAO, Desempenho("novo", 40, Decimal("0.003"), Decimal("0.55")))
        assert d.promover is True
        assert d.destino == "PAPER"
        assert "LIVE" in d.motivo and "fora do alcance" in d.motivo

    def test_destino_nunca_e_live(self):
        d = comparar(self.CAMPEAO, Desempenho("novo", 999, Decimal("9"), Decimal("0.99")))
        assert d.destino != "LIVE"

    def test_piora_acerto_nao_promove(self):
        d = comparar(self.CAMPEAO, Desempenho("novo", 40, Decimal("0.003"), Decimal("0.40")))
        assert d.promover is False and "acerto" in d.motivo


class TestGovernanca:
    def test_conselheiro_padrao_e_aprovado(self):
        assert auditar_aconselhador(ConselheiroQuantitativo()) == []

    def test_metodo_de_comando_e_violacao(self):
        class Falso:
            def enviar_ordem(self):
                return "nunca"

            def limitar_risco(self):
                return "nunca"

            def avaliar(self):
                return "ok"

        assert auditar_aconselhador(Falso()) == ["enviar_ordem", "limitar_risco"]
        with pytest.raises(ViolacaoDeGovernanca):
            exigir_aconselhador(Falso())

    def test_menu_do_conselheiro_nao_tem_comando(self):
        c = ConselheiroQuantitativo()
        proibidos = [n for n in dir(c) if not n.startswith("_")
                     and any(r in n.lower() for r in
                             ("enviar", "executar", "reservar", "ordem", "live"))]
        assert proibidos == []

    def test_calculo_deterministico_nunca_fala_com_llm(self):
        """Item 3: LLM jamais entra no cálculo determinístico. A garantia é
        estrutural — nenhum módulo de tradingagents/quant pode nem IMPORTAR
        um cliente de IA. Promessa em texto não basta; isto é auditoria."""
        import ast
        from pathlib import Path
        proibidos = ("llm_clients", "openai", "anthropic", "groq", "gemini")
        arquivos = sorted(Path("tradingagents/quant").glob("*.py"))
        assert arquivos, "o pacote quant sumiu"
        for arquivo in arquivos:
            arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
            for no in ast.walk(arvore):
                if isinstance(no, ast.Import):
                    nomes = [alias.name for alias in no.names]
                elif isinstance(no, ast.ImportFrom):
                    nomes = [no.module or ""]
                else:
                    continue
                for nome in nomes:
                    assert not any(p in nome.lower() for p in proibidos), (
                        f"{arquivo.name} importa {nome!r}: cálculo determinístico "
                        "não fala com LLM")


class TestConselheiro:
    class ModeloFake:
        disponivel = True
        treinado = True

        def prever_probabilidades(self, X):
            return [{"tendencia_alta": 0.7, "lateral": 0.3}]

    class ModeloQueExplode:
        disponivel = True
        treinado = True

        def prever_probabilidades(self, X):
            raise RuntimeError("boom")

    def test_sem_modelo_mesa_segue(self):
        c = ConselheiroQuantitativo()
        assert c.disponivel is False
        p = c.avaliar("BTC/USDT", serie(60))
        assert p.disponivel is False and p.direcao == "abster"
        assert "indisponíveis" in p.motivo

    def test_janela_curta_e_abstencao(self):
        c = ConselheiroQuantitativo(self.ModeloFake())
        p = c.avaliar("BTC/USDT", [100.0] * 5)
        assert p.disponivel is False and "janela insuficiente" in p.motivo

    def test_parecer_com_modelo_treinado(self):
        c = ConselheiroQuantitativo(self.ModeloFake())
        p = c.avaliar("BTC/USDT", serie(60))
        assert p.disponivel is True
        assert p.direcao == "comprar"
        assert p.confianca == pytest.approx(0.7)
        assert "tendencia_alta" in p.motivo

    def test_falha_do_modelo_vira_indisponivel(self):
        c = ConselheiroQuantitativo(self.ModeloQueExplode())
        p = c.avaliar("BTC/USDT", serie(60))
        assert p.disponivel is False and "falha do modelo" in p.motivo

    def test_drift_reduz_confianca(self):
        monitor = MonitorDeDrift(retornos(serie(200, passo=1.0)))
        c = ConselheiroQuantitativo(self.ModeloFake(), monitor_de_drift=monitor)
        queda = [200.0 - i for i in range(60)]
        p = c.avaliar("BTC/USDT", queda)
        assert p.disponivel is True
        assert p.confianca == pytest.approx(0.35)
        assert any("drift" in nota for nota in p.notas)

    def test_calibrador_aplica_se_historico(self):
        cal = CalibradorDeConfianca()
        for _ in range(10):
            cal.registrar(0.7, acertou=False)
        c = ConselheiroQuantitativo(self.ModeloFake(), calibrador=cal)
        p = c.avaliar("BTC/USDT", serie(60))
        assert p.confianca == pytest.approx(0.0)


class TestIntegracaoNaMesa:
    """Item 6: a mesa consome a leitura quant — e continua mandando nela."""

    class ConselheiroFake:
        def __init__(self, direcao: str):
            self._direcao = direcao

        def avaliar(self, instrumento: str, closes):
            from tradingagents.quant import ParecerQuant
            return ParecerQuant(True, self._direcao, 0.9, "fake")

    def _desk(self, conselheiro=None):
        from datetime import datetime, timezone

        from tradingagents.desk.models import (
            AgentStage,
            DeskMandate,
            MarketSnapshot,
            Mode,
        )
        from tradingagents.desk.orchestrator import DeskOrchestrator
        from tradingagents.desk.traders import default_desk

        traders = default_desk(("BTC/USDT",))
        for t in traders:
            for stage in (AgentStage.VALIDATED, AgentStage.SHADOW,
                          AgentStage.PAPER_ENABLED):
                t.spec.move_to(stage)
        mandate = DeskMandate(mandate_id="m", owner="o", account="a",
                              mode=Mode.PAPER, instruments=("BTC/USDT",),
                              capital=Decimal("1000"))
        desk = DeskOrchestrator(mandate, traders, worker_id="w",
                                conselheiro=conselheiro)
        snap = MarketSnapshot(
            instrument="BTC/USDT", timeframe="1h",
            cutoff=datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc),
            closes=tuple(Decimal(str(100 + i * 1.5)) for i in range(40)),
            source="fixture", quality="ok")
        return desk, snap

    def test_conselheiro_em_desacordo_reduz_exposicao(self):
        desk_base, snap = self._desk()
        desk_base.run_cycle([snap], {"BTC/USDT": Decimal("160")})
        base = desk_base.risk.reserved_total

        desk_quant, snap2 = self._desk(self.ConselheiroFake("vender"))
        desk_quant.run_cycle([snap2], {"BTC/USDT": Decimal("160")})
        assert desk_quant.risk.reserved_total < base
        assert desk_quant.risk.reserved_total > 0

    def test_conselheiro_de_acordo_nao_muda_nada(self):
        desk_base, snap = self._desk()
        desk_base.run_cycle([snap], {"BTC/USDT": Decimal("160")})
        desk_ok, snap2 = self._desk(self.ConselheiroFake("comprar"))
        desk_ok.run_cycle([snap2], {"BTC/USDT": Decimal("160")})
        assert desk_ok.risk.reserved_total == desk_base.risk.reserved_total

    def test_conselheiro_indisponivel_nao_derruba_ciclo(self):
        class Sumiu:
            def avaliar(self, instrumento, closes):
                from tradingagents.quant import ParecerQuant
                return ParecerQuant(False, "abster", 0.0, "sem modelo")

        desk, snap = self._desk(Sumiu())
        report = desk.run_cycle([snap], {"BTC/USDT": Decimal("160")})
        assert report.intents                # mesa NÃO para por falta de IA
        assert not report.errors


class TestPainelQuant:
    def test_route_reports_quant_status(self, tmp_path, monkeypatch):
        import importlib

        from fastapi.testclient import TestClient
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m
        importlib.reload(m)
        d = TestClient(m.app).get("/v1/quant/status",
                                  headers={"Authorization": "Bearer s3nh4"}).json()
        assert {"xgboost", "lightgbm", "torch"} <= set(d["modelos"])
        assert "LIVE" in d["governanca"]
        assert "champion/challenger" in d["aviso"]

    def test_painel_tem_o_botao_da_camada_quant(self):
        """Item 7: o painel precisa chegar até a rota — botão e função."""
        from pathlib import Path
        html = Path("service/static/index.html").read_text(encoding="utf-8")
        assert "quantStatus()" in html
        assert "/v1/quant/status" in html
        assert "ia_contexto" in html

    def test_ciclo_registra_latencia_da_ia_de_contexto(self, tmp_path, monkeypatch,
                                                       mock_llm_client):
        """O ciclo consulta o provedor de contexto e o painel expõe provedor,
        latência e saúde da última consulta (item 7)."""
        import importlib

        from fastapi.testclient import TestClient
        mock_llm_client.complete.return_value = (
            '{"veredito": "seguir", "motivo": "sem eventos", "confianca": 0.9}')
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        r = c.post("/v1/config", headers=h, json={"vars": {
            "TRADINGAGENTS_LLM_PROVIDER": "openrouter",
            "OPENROUTER_API_KEY": "chave-de-teste"}})
        assert r.status_code in (200, 201)
        assert c.post("/v1/desk/cycle", headers=h).status_code == 200
        ctx = c.get("/v1/quant/status", headers=h).json()["ia_contexto"]
        assert ctx is not None, "o ciclo consultou a IA; o painel tem de registrar"
        assert ctx["provedor"] == "openrouter"
        assert ctx["latencia_ms"] >= 0
        assert ctx["ok"] is True
        assert ctx["quando"]
