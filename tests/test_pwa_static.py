"""PWA: Service Worker, manifesto e contrato do painel — provas estáticas.

Os pinos aqui impedem regressões silenciosas do PWA: cache só de estáticos
públicos (nunca /v1 nem página autenticada), botão Instalar escondido até o
navegador oferecer a capacidade, SW registrado com updateViaCache none e
fluxo de inscrição real (PushManager + VAPID).
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "service" / "static"


class TestServiceWorker:
    def test_cache_so_de_estaticos_publicos_e_versionado(self):
        fonte = (STATIC / "sw.js").read_text(encoding="utf-8")
        assert "dz23-pwa-v1" in fonte
        # O fetch handler só intercepta manifesto e ícones da marca —
        # /v1 e páginas autenticadas passam direto para a rede.
        assert 'url.pathname === "/manifest.webmanifest"' in fonte
        assert 'url.pathname.startsWith("/static/marca/")' in fonte
        codigo = re.sub(r"/\*[\s\S]*?\*/", "", fonte)  # sem comentários de bloco
        codigo = re.sub(r"//[^\n]*", "", codigo)       # nem de linha
        assert "/v1" not in codigo
        assert "caches.delete" in fonte  # purga de versões antigas no activate

    def test_push_mostra_notificacao_com_tag_e_abre_area_autenticada(self):
        fonte = (STATIC / "sw.js").read_text(encoding="utf-8")
        assert "showNotification" in fonte
        assert "tag: dados.id" in fonte  # reenvio do MESMO alerta não duplica
        assert '"/app#notificacoes"' in fonte  # fragmento não vai ao servidor
        assert "openWindow" in fonte and "focus" in fonte


class TestPainelPwa:
    def _html(self):
        return (STATIC / "index.html").read_text(encoding="utf-8")

    def test_instalar_comeca_escondido_ate_o_navegador_oferecer(self):
        html = self._html()
        m = re.search(r'<button[^>]*id="btnPwaInstalar"[^>]*>', html)
        assert m, "botão Instalar não encontrado"
        assert "hide" in m.group(0), (
            "botão Instalar precisa começar escondido — só aparece quando o "
            "navegador oferece a capacidade (beforeinstallprompt)")
        assert "beforeinstallprompt" in html

    def test_registra_sw_sem_cache_velho_e_inscreve_push_real(self):
        html = self._html()
        assert "register('/sw.js',{updateViaCache:'none'})" in html
        assert "pushManager.subscribe" in html
        assert "applicationServerKey:pwaB64u(" in html
        assert "'/v1/push/assinar'" in html and "'/v1/push/cancelar'" in html
        assert "requestPermission" in html

    def test_card_do_aplicativo_com_dica_iphone_e_estados(self):
        html = self._html()
        assert "Aplicativo e Notificações Push" in html
        assert 'id="pwaEstado"' in html and 'id="pwaDispositivos"' in html
        assert "Adicionar à Tela de Início" in html
        # Carrega junto com a página de configuração (loadConfig → pwaLoad).
        assert "notifLoad();pwaLoad()" in html

    def test_endereco_inseguro_degrada_honesto(self):
        html = self._html()
        assert "isSecureContext" in html
        assert "exigem HTTPS ou localhost" in html

    def test_fragmento_do_push_tem_consumidor_real_no_painel(self):
        # O sw.js e o backend (URL_DO_TOQUE) abrem /app#notificacoes; o
        # painel precisa CONSUMIR o fragmento (carga fria e navegação
        # same-document via hashchange) e levar a um destino existente —
        # senão o toque abre o app e o destino morre em silêncio.
        html = self._html()
        assert "#notificacoes" in html
        assert "hashchange" in html

    def test_ultimo_teste_do_canal_e_honesto_no_painel(self):
        # V9-PWA-03: o painel consome ultimo_teste_ok — um teste de canal
        # que FALHOU nunca aparece como "testado" sem ressalva.
        html = self._html()
        assert "ultimo_teste_ok" in html


class TestRotasPwa:
    def _app(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        from fastapi.testclient import TestClient
        return m, TestClient(m.app), {"Authorization": "Bearer s3nh4"}

    def test_sw_js_servido_com_escopo_raiz_e_sem_cache_velho(self, tmp_path, monkeypatch):
        _, c, _ = self._app(tmp_path, monkeypatch)
        r = c.get("/sw.js")
        assert r.status_code == 200
        assert "javascript" in r.headers["content-type"]
        assert r.headers.get("service-worker-allowed") == "/"
        assert r.headers.get("cache-control") == "no-cache"

    def test_manifest_continua_servido(self, tmp_path, monkeypatch):
        _, c, _ = self._app(tmp_path, monkeypatch)
        r = c.get("/manifest.webmanifest")
        assert r.status_code == 200
        d = r.json()
        assert d["start_url"] == "/app" and d["display"] == "standalone"

    def test_sw_exige_contexto_autenticado_apenas_para_painel_nao_para_o_arquivo(
            self, tmp_path, monkeypatch):
        # O sw.js é público por NECESSIDADE (o navegador o busca fora da
        # sessão); ele não carrega dado nenhum — só lógica de cache/push.
        _, c, _ = self._app(tmp_path, monkeypatch)
        assert c.get("/sw.js").status_code == 200
        assert c.get("/v1/push/estado").status_code == 401
