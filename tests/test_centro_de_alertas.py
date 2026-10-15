"""Centro Inteligente de Alertas (E2/M5) — fila durável, canais e segurança."""
from __future__ import annotations

import contextlib
import importlib
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tradingagents.desk.canais_alerta import (
    CanalEmail,
    CanalPainel,
    CanalTelegram,
    CanalWindows,
    construir_canais,
)
from tradingagents.desk.notificacao_viva import (
    ACOES_TELEGRAM_PERMITIDAS,
    AlertaVivo,
    Despachante,
    FilaDeAlertas,
    MatrizDeRoteamento,
    processar_callback_telegram,
)

BASE = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)


def em(*, s=0, m=0, h=0):
    return BASE + timedelta(seconds=s, minutes=m, hours=h)


def fila(**kw):
    kw.setdefault("janela_dedupe_s", 3600)
    return FilaDeAlertas(**kw)


class CanalFalso:
    def __init__(self, nome="telegram", ok=True, erro="", efeito=None):
        self.nome = nome
        self.prioridade = 1
        self._ok = ok
        self._erro = erro
        self._efeito = efeito
        self.recebidos = []

    def configurado(self):
        return True

    def disponivel(self):
        return True

    def detalhe(self):
        return "canal de teste"

    def enviar(self, alerta, *, agora=None):
        if self._efeito is not None:
            self._efeito(alerta, agora)
        self.recebidos.append(alerta)
        if self._ok:
            return {"ok": True, "detalhe": "teste"}
        return {"ok": False, "erro": self._erro or "provedor fora do ar"}


def enfileirar(f, *, tipo="oportunidade", validade=None, dedupe="", dados=None,
               agora=None):
    return f.enfileirar(tipo=tipo, severidade="atencao", titulo=f"{tipo} em X",
                        detalhe="detalhe do evento", acao="Abra o painel",
                        fonte="fixture", preco="100.5", dedupe_key=dedupe,
                        validade=validade, dados=dados, agora=agora)


class TestFilaNucleo:
    def test_entrega_marca_estado_canal_e_audita(self):
        f = fila()
        alerta, criado = enfileirar(f, agora=em())
        assert criado and alerta.estado == "novo"
        canal = CanalFalso()
        d = Despachante(f, [canal, CanalPainel()])
        r = d.despachar(agora=em(s=1))
        assert len(r["entregues"]) == 1 and r["entregues"][0]["canal"] == "telegram"
        assert alerta.estado == "entregue" and alerta.entregue_canal == "telegram"
        assert alerta.tentativas and alerta.tentativas[-1]["ok"] is True

    def test_dedupe_incrementa_ocorrencias_sem_segundo_alerta(self):
        f = fila()
        a1, novo1 = enfileirar(f, dedupe="x1", agora=em())
        a2, novo2 = enfileirar(f, dedupe="x1", agora=em(s=30))
        assert novo1 and not novo2 and a1 is a2
        assert a2.ocorrencias == 2 and len(f.alertas) == 1

    def test_dedupe_libera_apos_janela(self):
        f = fila(janela_dedupe_s=600)
        enfileirar(f, dedupe="x1", agora=em())
        _, novo = enfileirar(f, dedupe="x1", agora=em(s=700))
        assert novo and len(f.alertas) == 2

    def test_falha_aplica_backoff_exponencial_e_nao_repete_antes_da_hora(self):
        f = fila(backoff_base_s=30, backoff_fator=2, backoff_teto_s=300)
        a, _ = enfileirar(f, agora=em())
        falho = CanalFalso(ok=False)
        d = Despachante(f, [falho, ])
        d.despachar(agora=em(s=1))
        assert a.estado == "falhou" and a.tentativas[-1]["ok"] is False
        assert a.proxima_tentativa_em.startswith("2026-10-14T12:00:31")
        # antes da hora não tenta de novo
        r2 = d.despachar(agora=em(s=10))
        assert not r2["falhas"] and len(a.tentativas) == 1
        # depois tenta, e o backoff dobra
        d.despachar(agora=em(s=32))
        assert len(a.tentativas) == 2
        assert a.proxima_tentativa_em.startswith("2026-10-14T12:01:32")

    def test_retry_apos_janela_entrega(self):
        f = fila(backoff_base_s=30)
        a, _ = enfileirar(f, agora=em())
        falho = CanalFalso(ok=False)
        d = Despachante(f, [falho])
        d.despachar(agora=em(s=1))
        a2 = falho
        a2._ok = True
        d.despachar(agora=em(s=32))
        assert a.estado == "entregue" and a.entregue_canal == "telegram"

    def test_timeout_de_um_canal_nao_derruba_os_demais(self):
        f = fila()
        a, _ = enfileirar(f, agora=em())

        def explode(alerta, agora_):
            raise TimeoutError("telegram pendurou")

        quebrado = CanalFalso(efeito=lambda al, ag: explode(al, ag))
        bom = CanalFalso(nome="painel")
        d = Despachante(f, [quebrado, bom])
        r = d.despachar(agora=em(s=1))
        assert r["entregues"][0]["canal"] == "painel"
        assert any(t["ok"] is False and "TimeoutError" in t["erro"]
                   for t in a.tentativas)

    def test_rate_limit_adia_e_libera_depois_da_janela(self):
        f = fila(rate_limites={"telegram": 2}, janela_rate_s=60)
        for i in range(3):
            enfileirar(f, tipo=f"evento{i}", dedupe=f"k{i}", agora=em(s=i))
        canal = CanalFalso()
        d = Despachante(f, [canal])
        r = d.despachar(agora=em(s=5))
        assert len(r["entregues"]) == 2 and len(r["adiados"]) == 1
        r2 = d.despachar(agora=em(s=70))
        assert len(r2["entregues"]) == 1

    def test_max_tentativas_esgota_e_para_de_tentar(self):
        f = fila(backoff_base_s=1, backoff_fator=1, max_tentativas=3)
        a, _ = enfileirar(f, agora=em())
        d = Despachante(f, [CanalFalso(ok=False)])
        t = em(s=1)
        for _ in range(5):
            d.despachar(agora=t)
            t = t + timedelta(seconds=2)
        assert a.esgotado and a.estado == "falhou"
        assert len(a.tentativas) == 3          # não insiste para sempre
        assert f.pendentes(agora=t) == []

    def test_expirado_nao_e_despachado_nem_parece_atual(self):
        f = fila()
        a, _ = enfileirar(f, validade=em(s=10), agora=em())
        d = Despachante(f, [CanalFalso()])
        r = d.despachar(agora=em(s=20))
        assert r["expirados"] == 1 and a.estado == "expirado"
        assert not r["entregues"]
        assert a.vencido(agora=em(s=20))
        texto = a.texto(agora=em(s=20))
        assert "VENCIDO" in texto and "oportunidade atual" in texto

    def test_validade_ainda_futura_entrega_normal_e_sem_marca(self):
        f = fila()
        a, _ = enfileirar(f, validade=em(m=5), agora=em())
        Despachante(f, [CanalFalso()]).despachar(agora=em(s=1))
        # Relógio FALSO também na leitura: sem `agora`, texto() cairia no
        # relógio real e este teste viraria uma bomba-relógio na BASE fixa.
        assert a.estado == "entregue" and "VENCIDO" not in a.texto(agora=em(s=1))

    def test_reinicio_restaura_estados_e_retoma_pendentes(self):
        f = fila(backoff_base_s=30)
        a1, _ = enfileirar(f, dedupe="ok", agora=em())
        canal = CanalFalso(ok=True)
        Despachante(f, [canal]).despachar(agora=em(s=1))   # a1 entregue
        a2, _ = enfileirar(f, tipo="stop", dedupe="falho", agora=em(s=2))
        canal._ok = False
        Despachante(f, [canal]).despachar(agora=em(s=3))   # a2 falha
        assert a1.estado == "entregue" and a2.estado == "falhou"

        doc = f.to_dict()
        nova = FilaDeAlertas.from_dict(doc, backoff_base_s=30)
        assert len(nova.alertas) == 2
        estados = {x.tipo: x.estado for x in nova.alertas}
        assert estados == {"oportunidade": "entregue", "stop": "falhou"}
        canal2 = CanalFalso()
        r = Despachante(nova, [canal2]).despachar(agora=em(s=40))
        assert len(r["entregues"]) == 1                 # a pendente retomou
        assert nova.alertas[0].entregue_canal == "telegram"  # e não duplicou a entregue

    def test_inbox_marca_vencido_e_separa_ativos(self):
        f = fila()
        enfileirar(f, dedupe="viva", agora=em())
        enfileirar(f, tipo="velha", validade=em(s=1), agora=em())
        f.expirar_vencidos(agora=em(s=30))
        caixa = f.inbox(agora=em(s=40))
        assert {a["tipo"] for a in caixa["ativos"]} == {"oportunidade"}
        velha = next(h for h in caixa["historico"] if h["tipo"] == "velha")
        assert velha["vencido"] is True and velha["estado"] == "expirado"

    def test_sem_canal_externo_o_painel_e_a_entrega_garantida(self):
        f = fila()
        a, _ = enfileirar(f, agora=em())
        r = Despachante(f, [CanalPainel()]).despachar(agora=em(s=1))
        assert r["entregues"][0]["canal"] == "painel" and a.estado == "entregue"

    def test_despacho_concorrente_nao_entrega_o_mesmo_alerta_duas_vezes(self):
        # "Notificações repetidas": o despacho roda em threads diferentes
        # (ciclo da mesa, copiloto manual e rota HTTP compartilham a mesma
        # fila do inquilino). Sem serialização, as duas leem o alerta ainda
        # 'novo' e entregam a MESMA notificação duas vezes — a barreira
        # força exatamente essa sobreposição de forma determinística.
        f = fila()
        alerta, _ = enfileirar(f, agora=em())
        barreira = threading.Barrier(2, timeout=3.0)

        def segura(alerta_, agora_):
            # No caminho serializado só uma thread chega aqui: a barreira
            # vence por timeout — comportamento esperado, não falha.
            with contextlib.suppress(threading.BrokenBarrierError):
                barreira.wait()

        canal = CanalFalso(nome="windows", efeito=segura)
        resultados: list[dict] = []

        def rodar():
            resultados.append(
                Despachante(f, [canal]).despachar(agora=em(s=1)))

        t1 = threading.Thread(target=rodar)
        t2 = threading.Thread(target=rodar)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert not t1.is_alive() and not t2.is_alive()
        assert len(canal.recebidos) == 1              # entregue UMA vez
        assert sum(len(r["entregues"]) for r in resultados) == 1
        assert alerta.estado == "entregue"


class TestMatrizDeRoteamento:
    def test_regra_por_trader_e_severidade(self):
        m = MatrizDeRoteamento([
            {"escopo": {"trader": "alpha", "severidade_min": "atencao"},
             "canais": ["discord", "painel"]}])
        a = AlertaVivo(tipo="stop", severidade="atencao", titulo="t",
                       detalhe="d", acao="a", dados={"trader": "alpha"})
        assert m.canais_para(a, padrao=["telegram"]) == ["discord", "painel"]
        b = AlertaVivo(tipo="stop", severidade="informativo", titulo="t",
                       detalhe="d", acao="a", dados={"trader": "alpha"})
        assert m.canais_para(b, padrao=["telegram"]) == ["telegram"]

    def test_canal_desconhecido_e_ignorado_com_aviso(self):
        m = MatrizDeRoteamento([{"escopo": {}, "canais": ["sms", "painel"]}])
        assert m.avisos and "sms" in m.avisos[0]
        assert m.canais_para(AlertaVivo(tipo="x", severidade="atencao",
                                        titulo="t", detalhe="d", acao="a"),
                             padrao=["telegram"]) == ["painel"]

    def test_regra_sem_canal_valido_e_descartada(self):
        m = MatrizDeRoteamento([{"escopo": {}, "canais": ["sms"]}])
        assert m.regras == []

    def test_matriz_e_serializavel_e_sobrevive(self):
        m = MatrizDeRoteamento([{"escopo": {"corretora": "binance"},
                                 "canais": ["telegram"]}])
        m2 = MatrizDeRoteamento.from_dict(m.to_dict())
        assert m2.regras == m.regras


class TestSegurancaDosBotoes:
    def test_acao_desconhecida_recusada(self):
        f = fila()
        a, _ = enfileirar(f, agora=em())
        r = processar_callback_telegram(
            {"callback_query": {"data": f"comprar:{a.id}"}}, f, agora=em())
        assert r["ok"] is False and "não permitida" in r["detalhe"]

    def test_ler_registra_recebimento(self):
        f = fila()
        a, _ = enfileirar(f, agora=em())
        r = processar_callback_telegram(
            {"callback_query": {"data": f"ler:{a.id}"}}, f, agora=em(s=5))
        assert r["ok"] and a.lido_em.startswith("2026-10-14T12:00:05")

    def test_intencao_registra_sem_executar(self):
        f = fila()
        a, _ = enfileirar(f, agora=em())
        chamadas = []
        r = processar_callback_telegram(
            {"callback_query": {"data": f"intencao:{a.id}"}}, f,
            registrar_intencao=lambda al: chamadas.append(al.id) or "ok",
            agora=em())
        assert r["ok"] and chamadas == [a.id]
        assert a.dados["intencoes"][-1]["tipo"] == "intencao_declarada"
        assert "nenhuma ordem" in r["detalhe"]

    def test_notificacao_viva_nao_conhece_execucao(self):
        from pathlib import Path
        raiz = Path(__file__).resolve().parents[1] / "tradingagents" / "desk"
        for nome in ("notificacao_viva.py", "canais_alerta.py"):
            fonte = (raiz / nome).read_text(encoding="utf-8")
            for proibido in ("create_order", "cancel_order", "close_position",
                             "coordinator.submit", "submit("):
                assert proibido not in fonte, f"{nome} contém {proibido!r}"

    def test_apenas_ler_e_intencao_sao_acoes_permitidas(self):
        assert ACOES_TELEGRAM_PERMITIDAS == ("ler", "intencao")


class TestCanais:
    def test_telegram_envia_texto_e_botoes_sem_execucao(self):
        enviados = []
        c = CanalTelegram("tok", "42", painel_url="http://p/",
                          transporte=enviados.append)
        a = AlertaVivo(tipo="oportunidade", severidade="atencao", titulo="T",
                       detalhe="D", acao="A", preco="10", fonte="fixture")
        assert c.configurado() and c.enviar(a)["ok"]
        teclado = c._teclado(a)
        textos = [b["text"] for linha in teclado["inline_keyboard"] for b in linha]
        datas = [b.get("callback_data", "") for linha in
                 teclado["inline_keyboard"] for b in linha]
        assert all(d.startswith(("ler:", "intencao:")) or "url" in b
                   for b, d in zip([b for linha in teclado["inline_keyboard"]
                                    for b in linha], datas, strict=True))
        assert "Comprar" not in " ".join(textos)

    def test_telegram_sem_config_nao_esta_configurado(self):
        assert CanalTelegram("", "").configurado() is False

    def test_windows_aceita_executor_injetavel(self):
        scripts = []
        c = CanalWindows(ativo=True, executar=scripts.append)
        a = AlertaVivo(tipo="stop", severidade="urgente", titulo="T",
                       detalhe="D", acao="A")
        assert c.configurado() and c.enviar(a)["ok"] and scripts
        assert "ToastNotificationManager" in scripts[0]

    def test_windows_desligado_nao_esta_configurado(self):
        assert CanalWindows(ativo=False).configurado() is False

    def test_email_recusa_alerta_por_estrutura(self):
        c = CanalEmail("smtp.x", "587", "u", "s", "de@x", "para@x")
        a = AlertaVivo(tipo="x", severidade="atencao", titulo="t",
                       detalhe="d", acao="a")
        r = c.enviar(a)
        assert r["ok"] is False and "RELATÓRIOS" in r["erro"]

    def test_email_envia_relatorio_com_smtp_injetado(self):
        enviados = []
        c = CanalEmail("smtp.x", "587", "u", "s", "de@x", "para@x",
                       smtp_envio=lambda a, c_: enviados.append((a, c_)))
        r = c.enviar_relatorio("Resumo", "corpo")
        assert r["ok"] and enviados == [("Resumo", "corpo")]

    def test_construir_canais_ordena_por_prioridade(self):
        canais = construir_canais({}, transportes={
            "telegram": lambda a: None, "windows": lambda s: None})
        nomes = [c.nome for c in canais]
        assert nomes[0] == "telegram" and "painel" in nomes and "email" in nomes

    def test_webpush_sem_flag_nao_finge_funcionar(self):
        from tradingagents.desk.canais_alerta import CanalWebPush
        c = CanalWebPush(habilitado=False)
        assert c.configurado() is False and "VAPID" in c.detalhe()


class TestNoServico:
    def _app(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        return m, TestClient(m.app), {"Authorization": "Bearer s3nh4"}

    def test_canais_status_nunca_vaza_segredo(self, tmp_path, monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {
            "ALERTA_TELEGRAM_TOKEN": "123456:AAAsegredoAAA",
            "ALERTA_TELEGRAM_CHAT": "999"}})
        r = c.get("/v1/notificacoes/canais", headers=h)
        assert r.status_code == 200
        assert "AAAsegredoAAA" not in r.text
        d = r.json()
        tg = next(x for x in d["canais"] if x["nome"] == "telegram")
        assert tg["configurado"] is True

    def test_testar_canal_sem_config_recusa_com_instrucao(self, tmp_path,
                                                         monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        r = c.post("/v1/notificacoes/testar", headers=h,
                   json={"canal": "whatsapp"})
        assert r.status_code == 400
        assert "opcional" in r.json()["detail"]
        assert c.post("/v1/notificacoes/testar", headers=h,
                      json={"canal": "fax"}).status_code == 400

    def test_testar_canal_configurado_devolve_evidencia(self, tmp_path,
                                                        monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {
            "ALERTA_TELEGRAM_TOKEN": "tok", "ALERTA_TELEGRAM_CHAT": "1"}})
        m._TESTE_CANAL_TRANSPORTES["legacy"] = {"telegram": lambda a: None}
        r = c.post("/v1/notificacoes/testar", headers=h,
                   json={"canal": "telegram"})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] and d["canal"] == "telegram" and "latencia_ms" in d

    # ----- V9-PWA-03: "último teste" do canal persistido e honesto -----

    def test_ultimo_teste_de_canal_sobrevive_ao_reinicio(self, tmp_path,
                                                        monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {
            "ALERTA_TELEGRAM_TOKEN": "tok", "ALERTA_TELEGRAM_CHAT": "1"}})
        m._TESTE_CANAL_TRANSPORTES["legacy"] = {"telegram": lambda a: None}
        r = c.post("/v1/notificacoes/testar", headers=h,
                   json={"canal": "telegram"})
        assert r.status_code == 200 and r.json()["ok"] is True

        # Reinício: o registro do teste precisa vir do armazém, não da RAM.
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        m._FILAS_ALERTAS.clear()
        c2 = TestClient(m.app)
        d = c2.get("/v1/notificacoes/canais", headers=h).json()
        tg = next(x for x in d["canais"] if x["nome"] == "telegram")
        assert tg["ultimo_teste"], \
            "o último teste do canal precisa sobreviver ao reinício"
        assert tg["ultimo_teste_ok"] is True

    def test_teste_de_canal_falho_fica_registrado_sem_se_disfarcar(
            self, tmp_path, monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {
            "ALERTA_TELEGRAM_TOKEN": "tok", "ALERTA_TELEGRAM_CHAT": "1"}})

        def transporte_quebrado(a):
            raise RuntimeError("rede fora do ar")

        m._TESTE_CANAL_TRANSPORTES["legacy"] = {
            "telegram": transporte_quebrado}
        r = c.post("/v1/notificacoes/testar", headers=h,
                   json={"canal": "telegram"})
        assert r.status_code == 502

        d = c.get("/v1/notificacoes/canais", headers=h).json()
        tg = next(x for x in d["canais"] if x["nome"] == "telegram")
        assert tg.get("ultimo_teste_ok") is False, \
            "um teste que falhou não pode aparecer como sucesso"
        assert tg["ultimo_teste"], "o horário da falha precisa ser registrado"

    def test_inbox_e_ack(self, tmp_path, monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        c.get("/v1/desk/status", headers=h)          # garante a mesa montada
        f = m._FILAS_ALERTAS["legacy"]
        a, _ = f.enfileirar(tipo="parada", severidade="urgente", titulo="T",
                            detalhe="D", acao="A")
        d = c.get("/v1/notificacoes/inbox", headers=h).json()
        assert any(x["id"] == a.id for x in d["ativos"])
        r = c.post(f"/v1/notificacoes/{a.id}/ack", headers=h)
        assert r.status_code == 200 and r.json()["lido_em"]

    def test_webhook_telegram_exige_segredo(self, tmp_path, monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        # sem segredo configurado: recusa (fail-closed)
        r = c.post("/v1/telegram/webhook", json={"callback_query": {
            "data": "ler:abc"}})
        assert r.status_code in (401, 403)
        c.post("/v1/config", headers=h, json={"vars": {
            "ALERTA_TELEGRAM_WEBHOOK_SECRET": "segredo-telegram-1",
            "ALERTA_TELEGRAM_TOKEN": "tok", "ALERTA_TELEGRAM_CHAT": "1"}})
        m._FILAS_ALERTAS.setdefault("legacy", m._nova_fila_alertas("legacy"))
        a, _ = m._FILAS_ALERTAS["legacy"].enfileirar(
            tipo="x", severidade="atencao", titulo="T", detalhe="D", acao="A")
        r2 = c.post("/v1/telegram/webhook",
                    headers={"X-Telegram-Bot-Api-Secret-Token":
                             "segredo-telegram-1"},
                    json={"callback_query": {"data": f"ler:{a.id}"}})
        assert r2.status_code == 200 and r2.json()["ok"] is True
        r3 = c.post("/v1/telegram/webhook",
                    headers={"X-Telegram-Bot-Api-Secret-Token":
                             "segredo-telegram-1"},
                    json={"callback_query": {"data": f"comprar:{a.id}"}})
        assert r3.json()["ok"] is False

    def test_alertas_rota_antiga_compativel(self, tmp_path, monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        d = c.get("/v1/alertas", headers=h).json()
        assert "ativos" in d and "como_configurar" in d
        assert d["canal_configurado"] is False

    def test_matriz_rota_salva_e_le(self, tmp_path, monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        c.get("/v1/desk/status", headers=h)
        r = c.put("/v1/notificacoes/matriz", headers=h, json={"regras": [
            {"escopo": {"ativo": "BTC/USDT"}, "canais": ["painel"]}]})
        assert r.status_code == 200
        d = c.get("/v1/notificacoes/matriz", headers=h).json()
        assert d["regras"][0]["escopo"]["ativo"] == "BTC/USDT"

    def test_fila_sobrevive_ao_reinicio_do_servico(self, tmp_path, monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        c.get("/v1/desk/status", headers=h)
        m._FILAS_ALERTAS["legacy"].enfileirar(
            tipo="stop", severidade="urgente", titulo="T", detalhe="D",
            acao="A")
        m._persistir_fila_alertas("legacy")   # grava no armazém, como o ciclo faz
        # reinício: novo processo lê a fila do armazém
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        m._FILAS_ALERTAS.clear()
        c2 = TestClient(m.app)
        d = c2.get("/v1/notificacoes/inbox", headers=h).json()
        assert any(x["tipo"] == "stop" for x in d["historico"]), \
            "a fila precisa sobreviver ao reinício (persistida no armazém)"


@pytest.mark.parametrize("campo", ["preco", "fonte", "link"])
def test_alerta_vivo_carrega_contexto(campo):
    a = AlertaVivo(tipo="t", severidade="atencao", titulo="T", detalhe="D",
                   acao="A", preco="10", fonte="binance", link="/painel#mesa")
    assert getattr(a, campo)
