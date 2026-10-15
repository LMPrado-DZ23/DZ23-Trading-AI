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

    def test_ativacao_remove_apenas_caches_do_proprio_app(self):
        # V9-PWA-06: a purga de ativação apagava QUALQUER cache que não fosse
        # o CACHE atual — inclusive caches de outro app no mesmo domínio
        # (cache sentinela alheio) e o marcador persistente
        # "dz23-push-pendente" da reinscrição (V9-PWA-05). A purga passa a
        # ser escopada à família de caches do DZ23 ("dz23-pwa-"): alheios e
        # o marcador sobrevivem por construção (o marcador não casa o prefixo).
        fonte = (STATIC / "sw.js").read_text(encoding="utf-8")
        assert 'startsWith("dz23-pwa-")' in fonte
        assert "chaves.filter((k) => k !== CACHE)" not in fonte

    def test_push_mostra_notificacao_com_tag_e_abre_area_autenticada(self):
        fonte = (STATIC / "sw.js").read_text(encoding="utf-8")
        assert "showNotification" in fonte
        assert "tag: dados.id" in fonte  # reenvio do MESMO alerta não duplica
        assert '"/app#notificacoes"' in fonte  # fragmento não vai ao servidor
        assert "openWindow" in fonte and "focus" in fonte

    def test_troca_de_inscricao_reinscreve_e_marca_pendente(self):
        # V9-PWA-05: o navegador pode trocar a inscrição (expiração do
        # push service). Sem tratamento, o endpoint novo nunca chega ao
        # servidor e o aparelho para de receber em silêncio. O SW
        # re-inscreve com a MESMA chave VAPID e deixa o novo destino num
        # marcador persistente que o painel autenticado consome.
        fonte = (STATIC / "sw.js").read_text(encoding="utf-8")
        assert "pushsubscriptionchange" in fonte
        assert "oldSubscription" in fonte
        assert "applicationServerKey" in fonte
        assert "dz23-push-pendente" in fonte
        assert "postMessage" in fonte  # avisa janelas abertas para registrar


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

    def test_toast_do_teste_de_canal_declara_a_lacuna_quando_parcial(self):
        # V9-PWA-04: entrega parcial (1 de N aparelhos) não pode virar toast
        # de "entrega confirmada" sem declarar a lacuna que o backend expõe.
        html = self._html()
        assert "r.data.parcial" in html

    def test_ativar_apos_rotacao_vapid_descarta_assinatura_obsoleta(self):
        # V9-PWA-05: depois de trocar as chaves VAPID, a inscrição antiga do
        # navegador continua local e o subscribe() com a chave nova é
        # recusado (InvalidStateError) — sem recuperação, o aparelho nunca
        # volta a receber. Ativar precisa comparar a chave da inscrição
        # existente com a atual, cancelar a obsoleta e reinscrever.
        html = self._html()
        assert "getSubscription" in html
        assert "pwaB64uDe" in html
        assert "vapid_publica" in html
        assert "unsubscribe" in html

    def test_remover_aparelho_cancela_tambem_a_assinatura_local(self):
        # V9-PWA-05: cancelar no painel removia só o registro remoto — a
        # inscrição local continuava viva e o próximo subscribe()/teste
        # mentia sobre o estado. O id curto do painel é derivado do endpoint
        # (sha256[:10]); quando o id removido é o DESTE navegador, a
        # inscrição local também precisa ser cancelada.
        html = self._html()
        assert "pwaIdCurto" in html

    def test_marcador_de_reinscricao_e_consumido_pelo_painel(self):
        # V9-PWA-05: a reinscrição feita pelo SW (troca de endpoint sem a
        # página aberta) só restaura a entrega quando o servidor conhece o
        # destino novo. O painel (autenticado) consome o marcador persistente
        # na primeira abertura e registra via /v1/push/assinar.
        html = self._html()
        assert "dz23-push-pendente" in html
        assert "push-reinscrito" in html
        assert "/v1/push/assinar" in html


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
