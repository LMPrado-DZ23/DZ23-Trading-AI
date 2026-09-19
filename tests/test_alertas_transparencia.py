"""Alertas que acordam o dono e página pública que mostra a verdade."""
from __future__ import annotations

import importlib
from decimal import Decimal

from fastapi.testclient import TestClient

from tradingagents.desk.alerts import Alerta, Notificador, avaliar_estado


def estado(**kw):
    base = {"pendencias_reconciliacao": [], "contabilidade_balanceada": True,
                "perda_hoje": Decimal("0"), "limite_perda": Decimal("500"),
                "mesa_deveria_rodar": False, "mesa_rodando": False, "erros_do_ciclo": 0}
    base.update(kw)
    return avaliar_estado(**base)


class TestRegrasDeAlerta:
    def test_healthy_desk_generates_nothing(self):
        assert estado() == []

    def test_uncertain_order_is_urgent(self):
        a = estado(pendencias_reconciliacao=["ord-1"])[0]
        assert a.severidade == "urgente" and "incerto" in a.titulo
        assert "reconcilie" in a.acao

    def test_unbalanced_book_is_urgent(self):
        a = estado(contabilidade_balanceada=False)[0]
        assert a.codigo == "livro_desbalanceado" and a.severidade == "urgente"

    def test_warns_before_the_daily_limit_not_only_after(self):
        perto = estado(perda_hoje=Decimal("420"), limite_perda=Decimal("500"))
        assert perto[0].codigo == "limite_perda_proximo"
        assert perto[0].severidade == "atencao"

    def test_limit_reached_is_urgent(self):
        a = estado(perda_hoje=Decimal("500"), limite_perda=Decimal("500"))[0]
        assert a.codigo == "limite_perda_atingido" and a.severidade == "urgente"

    def test_desk_stopped_when_it_should_run(self):
        a = estado(mesa_deveria_rodar=True, mesa_rodando=False)[0]
        assert a.codigo == "mesa_parada"

    def test_running_desk_does_not_alert(self):
        assert estado(mesa_deveria_rodar=True, mesa_rodando=True) == []

    def test_repeated_cycle_errors_alert(self):
        assert any(a.codigo == "ciclos_com_erro" for a in estado(erros_do_ciclo=3))

    def test_every_alert_says_what_to_do(self):
        todos = estado(pendencias_reconciliacao=["o1"], contabilidade_balanceada=False,
                       perda_hoje=Decimal("500"), limite_perda=Decimal("500"),
                       mesa_deveria_rodar=True, erros_do_ciclo=5)
        assert len(todos) >= 4
        assert all(a.acao and a.titulo and a.detalhe for a in todos)


class TestEntregaSemVirarRuido:
    def _notificador(self, **kw):
        recebidos = []
        n = Notificador(webhook_url="https://exemplo/hook",
                        transporte=recebidos.append, **kw)
        return n, recebidos

    def test_same_alert_is_not_repeated_within_the_silence_window(self):
        n, recebidos = self._notificador(silencio=1800)
        a = estado(contabilidade_balanceada=False)
        n.enviar(a, agora=1000)
        r = n.enviar(a, agora=1200)
        assert len(recebidos) == 1 and r["silenciados"] == ["livro_desbalanceado"]

    def test_after_the_window_it_alerts_again(self):
        n, recebidos = self._notificador(silencio=600)
        a = estado(contabilidade_balanceada=False)
        n.enviar(a, agora=1000)
        n.enviar(a, agora=2000)
        assert len(recebidos) == 2

    def test_without_channel_the_alert_is_kept_not_lost(self):
        n = Notificador()
        r = n.enviar(estado(contabilidade_balanceada=False))
        assert r["canal_configurado"] is False
        assert n.historico and n.historico[-1]["codigo"] == "livro_desbalanceado"

    def test_delivery_failure_is_reported_not_swallowed(self):
        def explode(a):
            raise RuntimeError("telegram fora do ar")
        n = Notificador(webhook_url="https://x/h", transporte=explode)
        r = n.enviar(estado(contabilidade_balanceada=False))
        assert r["falhas"] and "livro_desbalanceado" in r["falhas"][0]

    def test_message_never_carries_secrets(self):
        a = Alerta("x", "urgente", "t", "d", "a")
        texto = a.texto()
        assert "token" not in texto.lower() and "api" not in texto.lower()


class TestNoServico:
    def _app(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        return m, TestClient(m.app), {"Authorization": "Bearer s3nh4"}

    def test_alert_route_lists_and_explains(self, tmp_path, monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        d = c.get("/v1/alertas", headers=h).json()
        assert "ativos" in d and "como_configurar" in d
        assert d["canal_configurado"] is False

    def test_public_page_is_off_by_default(self, tmp_path, monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        assert c.get("/publico/legacy").status_code == 404

    def test_public_page_shows_truth_when_enabled(self, tmp_path, monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {
            "TRANSPARENCIA_PUBLICA": "true", "TRANSPARENCIA_NOME": "Mesa do Prado"}})
        r = c.get("/publico/legacy")          # SEM autenticação, de propósito
        assert r.status_code == 200
        d = r.json()
        assert d["agencia"] == "Mesa do Prado"
        assert any("perdas" in x for x in d["compromissos"])
        assert any("passado não prevê" in x for x in d["compromissos"])
        assert "saldo" in d["o_que_nao_mostramos"]

    def test_public_page_never_leaks_account_data(self, tmp_path, monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {
            "TRANSPARENCIA_PUBLICA": "true", "EXCHANGE_API_KEY": "chave-secreta-xyz",
            "GROQ_API_KEY": "gsk-secreta"}})
        texto = c.get("/publico/legacy").text
        assert "chave-secreta-xyz" not in texto and "gsk-secreta" not in texto
        assert "saldo" not in texto.lower().split("o_que_nao_mostramos")[0]


class TestMudancaDeServidor:
    """Servidor provisório: o dono precisa levar tudo embora pelo navegador."""

    def _app(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        return m, TestClient(m.app), {"Authorization": "Bearer s3nh4"}

    def _stepup(self, c, h, purpose):
        return c.post("/v1/auth/step-up", headers=h,
                      json={"password": "s3nh4", "purpose": purpose}).json()["step_up_token"]

    def test_export_carries_everything_that_matters(self, tmp_path, monkeypatch):
        # §1/§2: export exige TOKEN de step-up (não senha crua) + frase; vem
        # CIFRADO (sem segredo em claro), attachment no-store; token é single-use.
        from tradingagents.portable import decrypt_package
        m, c, h = self._app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {"GROQ_API_KEY": "gsk-x"}})
        c.get("/v1/desk/status", headers=h)
        assert c.get("/v1/mudanca/exportar", headers=h).status_code == 401   # sem step-up
        tok = self._stepup(c, h, "export")
        hh = {**h, "X-Step-Up-Token": tok, "X-Export-Passphrase": "frase-forte-123"}
        r = c.get("/v1/mudanca/exportar", headers=hh)
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/octet-stream"
        assert r.headers["cache-control"].startswith("no-store")
        assert b"gsk-x" not in r.content                 # segredo nunca em claro
        dados = decrypt_package(r.json(), "frase-forte-123")
        assert dados["formato"] == "dz23.mudanca/1"
        assert dados["configuracao"]["GROQ_API_KEY"] == "gsk-x"
        # token single-use: reusar falha
        assert c.get("/v1/mudanca/exportar", headers=hh).status_code == 401

    def test_import_restores_on_a_fresh_server(self, tmp_path, monkeypatch):
        from decimal import Decimal as D

        from tradingagents.desk.learning import Desfecho
        from tradingagents.desk.models import utcnow as agora
        m, c, h = self._app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {"GROQ_API_KEY": "gsk-antigo"}})
        c.get("/v1/desk/status", headers=h)
        mem = m._DESKS["legacy"]["memoria"]
        for _ in range(12):
            mem.registrar(Desfecho("trend_following", "tendencia_alta", "BTC/USDT",
                                   D("0.02"), agora(), 0.8))
        tok = self._stepup(c, h, "export")
        pacote = c.get("/v1/mudanca/exportar",
                       headers={**h, "X-Step-Up-Token": tok,
                                "X-Export-Passphrase": "frase-forte-123"}).json()

        # "servidor novo": outro diretório, tudo zerado
        novo = tmp_path / "servidor-novo"
        monkeypatch.setenv("DATA_DIR", str(novo))
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        c2 = TestClient(m.app)
        tok2 = c2.post("/v1/auth/step-up", headers=h,
                       json={"password": "s3nh4", "purpose": "import"}).json()["step_up_token"]
        r = c2.post("/v1/mudanca/importar",
                    headers={**h, "X-Step-Up-Token": tok2,
                             "X-Export-Passphrase": "frase-forte-123"}, json=pacote)
        assert r.status_code == 200
        assert any("aprendizado" in x for x in r.json()["restaurado"])
        cfg = c2.get("/v1/config", headers=h).json()["config"]
        assert cfg["GROQ_API_KEY"]["set"] is True
        aprend = c2.get("/v1/desk/aprendizado", headers=h).json()
        linha = next(e for e in aprend["estrategias"]
                     if e["estrategia"] == "trend_following")
        assert linha["operacoes"] == 12          # o histórico atravessou

    def test_unknown_package_is_refused(self, tmp_path, monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        r = c.post("/v1/mudanca/importar", headers=h, json={"formato": "outro"})
        assert r.status_code == 422

    def test_importing_twice_does_not_duplicate(self, tmp_path, monkeypatch):
        # Pacote PLANO (formato dz23.mudanca/1) não exige step-up; importar 2x
        # é idempotente. (O pacote cifrado exige token; testado acima.)
        m, c, h = self._app(tmp_path, monkeypatch)
        c.get("/v1/desk/status", headers=h)
        pacote = {"formato": "dz23.mudanca/1", "aprendizado": {"desfechos": [
            {"estrategia": "breakout", "regime": "tendencia_alta",
             "instrumento": "BTC/USDT", "retorno": "0.01",
             "fechado_em": "2026-09-01T00:00:00+00:00", "forca_do_sinal": 0.5}]}}
        assert c.post("/v1/mudanca/importar", headers=h, json=pacote).status_code == 200
        c.post("/v1/mudanca/importar", headers=h, json=pacote)
        d = c.get("/v1/desk/aprendizado", headers=h).json()
        linha = next(e for e in d["estrategias"] if e["estrategia"] == "breakout")
        assert linha["operacoes"] == 1
