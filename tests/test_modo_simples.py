"""SS-5 (diretriz definitiva de UX do dono): Modo Simples padrão + Avançado.

O dono pediu, verbatim: "Eu quero apenas cadastrar as APIs necessárias,
escolher DEMO, REAL ou COPILOTO DE SINAIS e apertar PLAY."

Regras que estes contratos pinam:
- Modo Simples é o PADRÃO: página "Início" com CONECTAR → MODO → PLAY.
- "SIMPLIFICAR NÃO SIGNIFICA REMOVER FUNCIONALIDADES": o Modo Avançado
  mostra TODAS as páginas/controles; o toggle é POR DEVICE (localStorage),
  puramente visual — nunca fala com o servidor e nunca altera configuração.
- "O modo avançado não pode desativar as regras inegociáveis de segurança
  financeira": REAL continua BLOQUEADO nos DOIS modos (radio disabled com
  motivo visível); nenhuma IA habilita LIVE.
- PLAY/PAUSAR usam as MESMAS rotas da mesa (/v1/desk/scheduler/*) — nenhum
  sistema paralelo; CONECTAR grava pelas MESMAS chaves de /v1/config.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "service" / "static" / "index.html"

PAGINAS_ANTIGAS = {"overview", "config", "analysis", "mesa", "manual",
                   "floor", "risk"}


def _html() -> str:
    return HTML.read_text(encoding="utf-8")


def _trecho(html: str, inicio: str, fim: str | None = None, tam: int = 2200) -> str:
    i = html.find(inicio)
    assert i > 0, f"{inicio!r} não encontrado no painel"
    if fim:
        j = html.find(fim, i)
        assert j > i, f"{fim!r} não encontrado depois de {inicio!r}"
        return html[i:j]
    return html[i:i + tam]


def _pagina_inicio(html: str) -> str:
    return _trecho(html, '<section id="page-inicio"', '<section id="page-overview"')


class TestPaginaInicioTresPassos:
    def test_pagina_inicio_existe_com_os_tres_passos(self):
        html = _html()
        pagina = _pagina_inicio(html)
        for marco in ("1 · CONECTAR", "2 · MODO", "3 · PLAY"):
            assert marco in pagina, f"passo {marco!r} ausente na página Início"

    def test_inicio_e_a_primeira_pagina_do_menu(self):
        html = _html()
        m = re.search(r"const pages=\[\[.+\]\]", html)
        assert m, "array pages não encontrado"
        assert m.group(0).startswith("const pages=[['inicio'"), (
            "Início precisa ser a primeira página do menu")

    def test_nada_foi_removido_do_menu_antigo(self):
        html = _html()
        m = re.search(r"const pages=\[\[.+\]\]", html)
        ids = set(re.findall(r"\['([a-z_]+)'", m.group(0)))
        assert ids >= PAGINAS_ANTIGAS, (
            f"página removida do menu (regra: simplificar ≠ remover): "
            f"faltam {sorted(PAGINAS_ANTIGAS - ids)}")


class TestModoSimpleXAvancado:
    def test_toggle_de_modo_existe_com_padrao_simples(self):
        html = _html()
        assert 'id="uiModo"' in html, "seletor de modo da interface ausente"
        trecho = _trecho(html, "function uiModoAtual(", tam=400)
        assert "'dz23_ui_modo'" in trecho, "preferência não persistida por device"
        assert "?'avancado':'simples'" in trecho, (
            "qualquer valor que não seja 'avancado' precisa cair em simples")

    def test_render_nav_filtra_no_simples_e_mostra_tudo_no_avancado(self):
        html = _html()
        assert "const PAGINAS_SIMPLES=" in html, "lista do modo simples ausente"
        trecho = _trecho(html, "function renderNav(", tam=700)
        assert "uiModoAtual()==='avancado'?pages:pages.filter(" in trecho, (
            "renderNav precisa filtrar SÓ no modo simples e mostrar TODAS as "
            "páginas no avançado")

    def test_toggle_e_puramente_visual_nunca_fala_com_o_servidor(self):
        html = _html()
        trecho = _trecho(html, "function uiModoSet(", "function inicioLoad(")
        assert "localStorage.setItem('dz23_ui_modo'" in trecho
        assert "renderNav()" in trecho
        assert "api(" not in trecho, (
            "o toggle de modo não pode chamar o servidor — é preferência de "
            "interface, não configuração da mesa")
        assert "iniModoReal" not in trecho, (
            "o toggle não pode mexer no bloqueio do modo REAL")

    def test_alternar_para_simples_sai_de_pagina_oculta(self):
        html = _html()
        trecho = _trecho(html, "function uiModoSet(", "function inicioLoad(")
        assert "showPage('inicio')" in trecho, (
            "estando numa página que o modo simples oculta, a troca precisa "
            "levar para o Início (em vez de deixar a tela sem navegação)")

    def test_entrar_mostra_inicio_no_simples(self):
        html = _html()
        trecho = _trecho(html, "function enterApp(", "async function logout(")
        assert "uiModoAtual()==='simples'?'inicio':'overview'" in trecho, (
            "no modo simples o dono entra direto no assistente Início")


class TestSegurancaVisivelNoModoSimples:
    def test_demo_e_paper_marcados_como_simulado(self):
        html = _html()
        pagina = _pagina_inicio(html)
        assert "DEMO" in pagina and "PAPER" in pagina
        assert "simulado" in pagina, (
            "PAPER precisa ser identificado como SIMULADO para o iniciante")
        assert "dinheiro real" in pagina.lower() and "BLOQUEADO" in pagina

    def test_real_bloqueado_com_motivo_e_sem_habilitacao(self):
        html = _html()
        pagina = _pagina_inicio(html)
        m = re.search(r'<input[^>]*id="iniModoReal"[^>]*>', pagina)
        assert m, "opção REAL não encontrada"
        assert "disabled" in m.group(0), (
            "REAL precisa estar DESABILITADO — 'O modo REAL continua "
            "BLOQUEADO até homologação e autorização explícita'")
        assert "autorização explícita do proprietário" in pagina, (
            "o motivo do bloqueio precisa estar visível, não só o disabled")

    def test_avancado_nao_mexe_no_real(self):
        html = _html()
        for fn in ("function renderNav(", "function uiModoSet("):
            trecho = _trecho(html, fn, tam=700)
            assert "iniModoReal" not in trecho, (
                f"{fn} não pode depender do modo para o bloqueio REAL")

    def test_copiloto_avisa_que_nunca_envia_ordens(self):
        html = _html()
        pagina = _pagina_inicio(html)
        assert "COPILOTO DE SINAIS" in pagina
        assert "nunca envia ordens" in pagina, (
            "'COPILOTO nunca pode enviar ordens' precisa estar dito na tela")
        trecho = _trecho(html, "function inicioModoSet(")
        assert "showPage('manual')" in trecho, (
            "escolher o copiloto precisa levar às operações acompanhadas")

    def test_limites_sao_deterministicos_texto_na_tela(self):
        html = _html()
        pagina = _pagina_inicio(html)
        assert "código determinístico" in pagina, (
            "a tela precisa dizer que limites são de código, não da IA")
        assert "recusa e avisa" in pagina, (
            "dados vencidos/inconsistentes: a mesa recusa e avisa — dito na tela")


class TestCapacidadesVisiveisNoModoSimples:
    """Missão §1 (auditoria funcional): o Simples mostra uma visão das
    principais capacidades com estado HONESTO (os quatro estados exatos) e
    caminho clicável para TODAS as telas — nada escondido por acidente."""

    def test_card_de_capacidades_na_pagina_inicio(self):
        pagina = _pagina_inicio(_html())
        assert 'id="capacidadesLista"' in pagina, (
            "card de capacidades ausente no Início")
        for estado in ("disponível", "configuração necessária",
                       "bloqueado por segurança", "ainda não implementado"):
            assert estado in pagina, (
                f"estado {estado!r} não apresentado na tela")

    def test_iq_option_mostra_metadata_validada_sem_prometer_conta_ou_ordem(
            self):
        capacidades = _trecho(
            _html(), "async function capacidadesLoad(",
            "async function preparoRealLoad(")
        assert "Leitura REAL preparada, execução bloqueada" in capacidades
        assert (
            "A prova de conta usa somente list_balances NORMAL"
            in capacidades
        )
        assert (
            "A prova recusa o token se tools/list anunciar qualquer "
            "ferramenta mutável" in capacidades
        )
        assert (
            "Como o token ainda anuncia ferramentas mutáveis no provedor"
            not in capacidades
        )
        assert "MCP oficial em validação passiva" not in capacidades
        assert "Sem token novo validado" not in capacidades

    def test_abrir_troca_para_avancado_quando_a_tela_esta_oculta(self):
        html = _html()
        trecho = _trecho(html, "function abrirPagina(", tam=400)
        assert "PAGINAS_SIMPLES.includes(id)" in trecho, (
            "abrirPagina precisa saber quais telas o Simples já mostra")
        assert "uiModoSet('avancado')" in trecho, (
            "telas ocultas no Simples precisam de acesso em um clique")
        assert "showPage(id)" in trecho, "abrirPagina precisa navegar de fato"

    def test_capacidades_derivam_de_fontes_reais_e_levam_a_cada_tela(self):
        html = _html()
        trecho = _trecho(html, "async function capacidadesLoad(",
                         "async function preparoRealLoad(")
        assert "'/v1/config'" in trecho, (
            "estado das capacidades precisa vir da configuração real")
        assert "'/v1/status/operacao'" in trecho, (
            "o bloqueio do dinheiro real precisa vir do servidor")
        assert "escapeHtml(" in trecho, (
            "dados do servidor precisam passar por escapamento")
        for alvo in ("'mesa'", "'analysis'", "'manual'", "'risk'", "'floor'",
                     "'config'"):
            assert alvo in trecho, f"capacidade sem caminho para {alvo}"

    def test_bloqueio_do_live_vem_do_servidor_nao_de_selo_fixo(self):
        html = _html()
        trecho = _trecho(html, "async function capacidadesLoad(",
                         "async function preparoRealLoad(")
        assert "bloqueado por segurança" in trecho
        assert "operacao_real" in trecho, (
            "o motivo do bloqueio do LIVE precisa vir de /v1/status/operacao")


class TestConectarModoPlayFiacaoReal:
    def test_play_e_parar_usam_as_mesmas_rotas_da_mesa(self):
        html = _html()
        pagina = _pagina_inicio(html)
        assert "inicioSched('start')" in pagina, "botão PLAY sem fiação"
        assert "inicioSched('stop')" in pagina, "botão PAUSAR/PARAR sem fiação"
        trecho = _trecho(html, "async function inicioSched(")
        assert "'/v1/desk/scheduler/'+action" in trecho, (
            "PLAY precisa usar a MESMA rota da mesa — nenhum sistema paralelo")

    def test_conectar_salva_pelas_mesmas_chaves_de_config(self):
        html = _html()
        trecho = _trecho(html, "async function inicioConectar(")
        assert "'/v1/config'" in trecho, "CONECTAR precisa gravar pela rota real"
        for chave in ("TRADINGAGENTS_EXCHANGE", "EXCHANGE_API_KEY",
                      "EXCHANGE_API_SECRET"):
            assert chave in trecho, f"CONECTAR não grava {chave}"
        assert "'_API_KEY'" in trecho, (
            "a chave de IA precisa mapear para a MESMA chave de /v1/config")

    def test_assistente_le_estado_real_sem_inventar(self):
        html = _html()
        trecho = _trecho(html, "async function inicioLoad(")
        assert "'/v1/config'" in trecho, "estado do CONECTAR sem fonte real"
        assert "'/v1/desk/status'" in trecho, "estado do PLAY sem fonte real"
        assert "'/v1/setup/estado" in trecho, (
            "pendências do assistente precisam vir do Smart Setup real")
        assert "RODANDO" in trecho and "PARADA" in trecho, (
            "o estado da mesa precisa ser exibido de forma explícita")

    def test_pagina_inicio_dentro_da_coluna_de_conteudo(self):
        html = _html()
        i_main = html.find('<main class="content">')
        i_pagina = html.find('<section id="page-inicio"')
        i_fim_main = html.find("</main>")
        assert 0 < i_main < i_pagina < i_fim_main, (
            "a página Início precisa viver na coluna de conteúdo (regressão "
            "do div órfão que jogava páginas para trás da sidebar)")


class TestRenderHttp:
    def _app(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        return m

    def test_pagina_autenticada_renderiza_o_assistente(self, tmp_path,
                                                       monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        corpo = c.get("/app").text
        assert 'id="page-inicio"' in corpo
        assert 'id="uiModo"' in corpo
        assert 'id="iniModoReal"' in corpo
