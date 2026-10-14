"""V8 §6d: contrato do painel para configuração de IA — provas estáticas.

Fixam o que o proprietário precisa NA TELA para a fase §6: registrar e
remover chaves (OpenAI, Anthropic, Google, DeepSeek, Groq, Mistral,
OpenRouter, compatível), escolher provedor/fallback, definir orçamento,
testar autenticação, buscar modelos e ver consumo/custo estimado. Sem
button morto: cada botão precisa apontar para rota real já existente.
"""
from __future__ import annotations

import importlib
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "service" / "static" / "index.html"

CHAVES_IA = [
    "OPENROUTER_API_KEY", "GROQ_API_KEY", "GOOGLE_API_KEY", "MISTRAL_API_KEY",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
    "OPENAI_COMPATIBLE_API_KEY",
]


def _html() -> str:
    return HTML.read_text(encoding="utf-8")


class TestConfigKeysECampos:
    def test_cfg_keys_cobrem_os_novos_campos_de_ia(self):
        html = _html()
        m = re.search(r"const CFG_KEYS=\[([^\]]+)\]", html)
        assert m, "CFG_KEYS não encontrado"
        chaves = set(re.findall(r"'([A-Z_0-9]+)'", m.group(1)))
        for k in CHAVES_IA + ["TRADINGAGENTS_LLM_FALLBACK",
                              "TRADINGAGENTS_IA_ORCAMENTO_USD"]:
            assert k in chaves, f"faltou {k} em CFG_KEYS"

    def test_select_de_provedor_tem_os_nove_do_catalogo(self):
        html = _html()
        m = re.search(
            r'<select id="TRADINGAGENTS_LLM_PROVIDER">(.*?)</select>', html,
            re.S)
        assert m, "select TRADINGAGENTS_LLM_PROVIDER não encontrado"
        opcoes = set(re.findall(r'value="([a-z_]+)"', m.group(1)))
        assert opcoes == {"openai", "anthropic", "google", "deepseek", "groq",
                          "mistral", "openrouter", "openai_compatible",
                          "ollama"}, f"opções divergentes: {sorted(opcoes)}"

    def test_campos_de_chave_novos_sao_password(self):
        html = _html()
        for k in ("DEEPSEEK_API_KEY", "OPENAI_COMPATIBLE_API_KEY"):
            m = re.search(rf'<input[^>]*id="{k}"[^>]*>', html)
            assert m, f"campo {k} não encontrado"
            assert 'type="password"' in m.group(0), f"{k} sem type=password"
            assert f'id="s_{k}"' in html, f"{k} sem status CONFIGURADA"

    def test_fallback_e_orcamento_tem_inputs(self):
        html = _html()
        assert 'id="TRADINGAGENTS_LLM_FALLBACK"' in html
        assert 'id="TRADINGAGENTS_IA_ORCAMENTO_USD"' in html


class TestRemocaoDeSegredos:
    def test_botao_remover_por_segredo_de_ia(self):
        html = _html()
        assert "function removerSegredo(" in html
        for k in CHAVES_IA:
            assert f"removerSegredo('{k}')" in html, (
                f"{k} sem botão Remover — requisito §6: registrar E remover")
        # a rota real aceita remover: []
        assert "remover:[k]" in html

    def test_remocao_e_honesta_sobre_ambiente(self):
        html = _html()
        i = html.find("async function removerSegredo")
        assert i > 0
        trecho = html[i:i + 900]
        assert "ainda_no_ambiente" in trecho, (
            "se a variável de ambiente continuar fornecendo a chave, a tela "
            "precisa dizer — nunca fingir que removeu")
        assert "loadConfig()" in trecho


class TestCardProvedoresDeIA:
    def test_card_existe_com_os_controles(self):
        html = _html()
        assert "Provedores de IA" in html
        for needle in ['id="iaProvedores"', "iaLoad()", "iaTestar(",
                       "iaModelos(", "iaUsarModelo("]:
            assert needle in html, f"faltou no card: {needle}"

    def test_botoes_apontam_para_rotas_reais(self):
        html = _html()
        for rota in ("'/v1/ai/provedores'", "'/v1/ai/testar'",
                     "'/v1/ai/modelos", "'/v1/ai/uso'"):
            assert rota in html, f"rota {rota} sem fiação no painel"

    def test_testar_mostra_latencia_e_limites(self):
        html = _html()
        assert "latencia_ms" in html
        assert "limites" in html  # limites de taxa informados pela API

    def test_modelos_oferece_usar_como_profundo_ou_rapido(self):
        html = _html()
        i = html.find("async function iaModelos")
        assert i > 0
        trecho = html[i:i + 1400]
        assert "usar como profundo" in trecho and "usar como rápido" in trecho
        assert "TRADINGAGENTS_DEEP_THINK_LLM" in trecho
        assert "TRADINGAGENTS_QUICK_THINK_LLM" in trecho

    def test_consumo_mostra_custo_orcamento_e_ressalva(self):
        html = _html()
        i = html.find("async function iaUsoLoad")
        assert i > 0, "função iaUsoLoad não encontrada"
        trecho = html[i:i + 1600]
        assert "custo_parcial" in trecho
        assert "orcamento" in trecho
        assert "sem_preco" in trecho  # chamadas sem preço conhecido
        # orçamento definido aparece mesmo com zero chamadas (senão o dono
        # salva o orçamento e a tela continua dizendo "nenhuma chamada")
        assert "||o.definido)" in trecho
        # o card carrega junto com a página de configuração
        assert "iaUsoLoad()" in html

    def test_loadconfig_carrega_os_dados_de_ia(self):
        html = _html()
        i = html.find("async function loadConfig")
        i_fim = html.find("async function saveConfig", i)
        assert 0 < i < i_fim
        trecho = html[i:i_fim]
        assert "iaLoad()" in trecho and "iaUsoLoad()" in trecho


class TestSintaxeJavaScript:
    def test_js_inline_do_painel_compila(self, tmp_path):
        """O painel é um único <script>: um parêntese desbalanceado derruba a
        tela inteira em silêncio — os testes de string não pegam isso.
        node --check pega em milissegundos; sem node, pula honestamente."""
        node = shutil.which("node")
        if node is None:
            import pytest
            pytest.skip("node indisponível neste ambiente")
        js = "\n;\n".join(re.findall(r"<script>(.*?)</script>", _html(),
                                     re.S))
        alvo = tmp_path / "painel.js"
        alvo.write_text(js, encoding="utf-8")
        r = subprocess.run([node, "--check", str(alvo)],
                           capture_output=True, text=True)
        assert r.returncode == 0, (
            f"JS do painel não compila: {r.stderr[-400:]}")


class TestRenderE2e:
    def _app(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        return m

    def test_pagina_autenticada_renderiza_card_de_ia(self, tmp_path,
                                                     monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        r = c.get("/app")
        assert r.status_code == 200
        corpo = r.text
        assert "Provedores de IA" in corpo
        assert 'id="DEEPSEEK_API_KEY"' in corpo

    def test_rotas_do_card_respondem_com_auth(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        assert c.get("/v1/ai/provedores", headers=h).status_code == 200
        assert c.get("/v1/ai/uso", headers=h).status_code == 200
        assert c.get("/v1/ai/modelos", headers=h,
                     params={"provedor": "openai"}).status_code == 200

    def test_pagina_renderiza_o_card_de_smart_setup(self, tmp_path,
                                                    monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        corpo = c.get("/app").text
        assert "Configuração Inteligente" in corpo
        assert 'id="ssPerfil"' in corpo
        assert 'id="TRADINGAGENTS_IA_ORCAMENTO_BLOQUEIO"' in corpo


class TestPainelSmartSetup:
    """Missão V8 (Smart Setup + diretriz UX): o painel precisa entregar o
    assistente, os perfis, o diagnóstico em 4 estados e o conserto do
    gráfico de fundo que sobrepunha os campos de IA (R13)."""

    def test_shell_fica_acima_do_grafico_de_fundo(self):
        """R13: o SVG decorativo (position:fixed;z-index:0) pintava POR CIMA
        do conteúdo estático depois do login — .shell precisa criar contexto
        posicionado (z-index:1), senão as tabelas/campos de IA aparecem
        atravessados pelo desenho."""
        html = _html()
        m = re.search(r"\.shell\{([^}]*)\}", html)
        assert m, ".shell não encontrado no CSS"
        regra = m.group(1)
        assert "position:relative" in regra and "z-index:1" in regra, (
            f".shell sem contexto posicionado — gráfico de fundo sobrepõe: {regra}")

    def test_cfg_keys_inclui_bloqueio_de_orcamento(self):
        html = _html()
        m = re.search(r"const CFG_KEYS=\[([^\]]+)\]", html)
        assert m
        chaves = set(re.findall(r"'([A-Z_0-9]+)'", m.group(1)))
        assert "TRADINGAGENTS_IA_ORCAMENTO_BLOQUEIO" in chaves

    def test_card_smart_setup_com_os_controles(self):
        html = _html()
        assert "Configuração Inteligente" in html
        for needle in ('id="ssPerfil"', 'id="ssEstado"', 'id="ssDiagnostico"',
                       "ssLoad()", "ssAplicar()"):
            assert needle in html, f"faltou no card Smart Setup: {needle}"
        m = re.search(r'<select id="ssPerfil">(.*?)</select>', html, re.S)
        assert m, "select de perfil não encontrado"
        opcoes = set(re.findall(r'value="([a-z_]+)"', m.group(1)))
        assert opcoes == {"pronto", "economico", "equilibrado", "avancado"}, (
            f"perfis divergentes: {sorted(opcoes)}")

    def test_bloqueio_orcamento_liga_e_desliga(self):
        html = _html()
        m = re.search(
            r'<select id="TRADINGAGENTS_IA_ORCAMENTO_BLOQUEIO">(.*?)</select>',
            html, re.S)
        assert m, "select do bloqueio de orçamento não encontrado"
        opcoes = set(re.findall(r'value="([^"]+)"', m.group(1)))
        assert opcoes == {"0", "1"}, f"opções do bloqueio: {sorted(opcoes)}"
        assert "Desligado" in m.group(1), (
            "o padrão precisa estar explícito como desligado/informativo")

    def test_botoes_apontam_para_rotas_reais_do_setup(self):
        html = _html()
        assert "'/v1/setup/estado" in html, "estado sem fiação no painel"
        assert "'/v1/setup/aplicar'" in html, "aplicar sem fiação no painel"

    def test_diagnostico_mostra_os_quatro_estados(self):
        html = _html()
        i = html.find("function ssDiagnostico")
        assert i > 0, "função ssDiagnostico não encontrada"
        trecho = html[i:i + 2000]
        for estado in ("configurado", "conectado", "autenticado",
                       "operacional"):
            assert estado in trecho, f"diagnóstico sem o estado '{estado}'"

    def test_assistente_de_primeira_execucao_lista_pendencias(self):
        html = _html()
        i = html.find("async function ssLoad")
        assert i > 0, "função ssLoad não encontrada"
        trecho = html[i:i + 2600]
        assert "primeira_execucao" in trecho
        assert "pendencias" in trecho
        # campos aplicados só quando vazios — a tela precisa dizer isso
        assert "vazio" in trecho or "nunca são sobrescritas" in html

    def test_ssload_carrega_junto_com_a_configuracao(self):
        html = _html()
        i = html.find("async function loadConfig")
        i_fim = html.find("async function saveConfig", i)
        assert 0 < i < i_fim
        assert "ssLoad()" in html[i:i_fim]

    def test_aplicar_envia_perfil_e_descobertas_ao_backend(self):
        html = _html()
        assert "IA_MODELOS_POR_PROVEDOR" in html, (
            "cache de descobertas por provedor ausente")
        i = html.find("async function ssAplicar")
        assert i > 0, "função ssAplicar não encontrada"
        trecho = html[i:i + 1400]
        assert "perfil" in trecho and "modelos" in trecho

    def test_aplicar_conta_campos_como_objeto(self):
        """Regressão (achado na homologação no navegador): a rota devolve
        `aplicados` como DICT chave→valor; `.length` de dict é undefined e o
        toast dizia 'nada para preencher' mesmo tendo preenchido campos."""
        html = _html()
        i = html.find("async function ssAplicar")
        assert i > 0
        trecho = html[i:i + 1400]
        assert "Object.entries(d.aplicados" in trecho, (
            "aplicados é dict — precisa contar com Object.entries/keys")

    def test_idioma_do_smart_setup_cabe_no_select_manual(self):
        """Regressão (achado na homologação): PADROES_SEGUROS escrevia
        'Português (Brasil)', valor que o <select> do painel não exibe — o
        campo aparecia VAZIO depois de aplicar um perfil, parecendo não
        salvo. O padrão precisa ser um valor exibível no controle manual."""
        from service.api.smart_setup import PADROES_SEGUROS
        html = _html()
        m = re.search(
            r'<select id="TRADINGAGENTS_OUTPUT_LANGUAGE">(.*?)</select>', html,
            re.S)
        assert m, "select de idioma não encontrado"
        opcoes = set(re.findall(r'value="([^"]+)"', m.group(1)))
        valor = PADROES_SEGUROS["TRADINGAGENTS_OUTPUT_LANGUAGE"]
        assert valor in opcoes, (
            f"padrão de idioma '{valor}' não é exibível no select "
            f"{sorted(opcoes)} — o campo apareceria vazio no painel")
