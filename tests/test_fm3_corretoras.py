"""FM-3 (§3+§4 da missão): corretoras/CCXT visuais + preparação REAL sem ativação.

O que estes contratos pinam:
- §3: a tela de corretora mostra o catálogo REAL (`/v1/personal/exchanges`),
  as capacidades VERDADEIRAS do adaptador instalado (`/capabilities`, sem
  credenciais e sem rede), o detalhe da validação (`/v1/broker/status`) e a
  reconciliação (`/v1/desk/reconciliar`); rotação = trocar (colar e salvar)
  ou remover a chave salva.
- §4: a preparação para REAL é VISÍVEL e verificável (checklist de 6 itens
  de `/v1/live/status`) — e continua BLOQUEADA: nenhuma tela ativa LIVE,
  nenhum caminho de saque/transferência existe no painel.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from service.app import app, auth

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "service" / "static" / "index.html"

CAP_ROTA = "/v1/personal/exchanges/{exchange_id}/capabilities"
HEALTH_ROTA = "/v1/personal/exchanges/{exchange_id}/public-health"
IQ_MCP_ROTA = "/v1/personal/exchanges/iq_option/mcp-health"


def _html() -> str:
    return HTML.read_text(encoding="utf-8")


def _funcao(html: str, assinatura: str) -> str:
    """Corpo exato da função: do seu cabeçalho até a PRÓXIMA definição."""
    i = html.find(assinatura)
    assert i > 0, f"{assinatura!r} não encontrado no painel"
    fim = len(html)
    for marcador in ("\nasync function ", "\nfunction "):
        j = html.find(marcador, i + len(assinatura))
        if j != -1:
            fim = min(fim, j)
    return html[i:fim]


# ---------------------------------------------------------------- rota HTTP

class TestRotaCapacidades:
    """Fecha a lacuna 'sem teste direto' desta rota na matriz FM-2."""

    def test_rota_get_only_e_autenticada(self):
        rotas = [r for r in app.routes
                 if isinstance(r, APIRoute) and r.path == CAP_ROTA]
        assert len(rotas) == 1
        assert rotas[0].methods == {"GET"}
        assert any(d.call is auth for d in rotas[0].dependant.dependencies)

    def test_capacidades_offline_honestas(self):
        from service.app import personal_exchange_capabilities

        d = personal_exchange_capabilities("binance")
        assert d["id"] == "binance"
        assert d["verified"] is False
        assert d["live_enabled"] is False
        assert d["status"] == "UNVERIFIED"
        for chave in ("ticker", "order_book", "ohlcv", "balance", "order_api"):
            assert isinstance(d[chave], bool), (
                f"{chave} precisa ser bool nativo (True/False/'emulated' ≠ nativo)")
        assert d["order_api"] in (True, False)

    def test_corretora_desconhecida_404(self):
        from service.app import personal_exchange_capabilities

        with pytest.raises(HTTPException) as e:
            personal_exchange_capabilities("nao_existe")
        assert e.value.status_code == 404

    def test_sonda_publica_get_only_e_autenticada(self):
        rotas = [r for r in app.routes
                 if isinstance(r, APIRoute) and r.path == HEALTH_ROTA]
        assert len(rotas) == 1
        assert rotas[0].methods == {"GET"}
        assert any(d.call is auth for d in rotas[0].dependant.dependencies)

    def test_sonda_iq_mcp_get_only_e_autenticada(self):
        rotas = [r for r in app.routes
                 if isinstance(r, APIRoute) and r.path == IQ_MCP_ROTA]
        assert len(rotas) == 1
        assert rotas[0].methods == {"GET"}
        assert any(d.call is auth for d in rotas[0].dependant.dependencies)


# ------------------------------------------------------------------ painel

class TestTelaCorretora:
    def test_card_tem_catalogo_detalhe_e_acoes(self):
        html = _html()
        assert 'id="brokerCatalogo"' in html, "catálogo de corretoras ausente"
        assert 'id="brokerDetalhe"' in html, "detalhe da validação ausente"
        assert 'onclick="catalogoCarregar()"' in html
        assert 'onclick="brokerReconciliar()"' in html
        assert 'onclick="brokerTest()"' in html

    def test_rotacao_remover_chaves_salvas(self):
        html = _html()
        assert 'id="r_EXCHANGE_API_KEY"' in html, (
            "sem botão Remover a chave antiga não sai do painel — rotação exige")
        assert 'id="r_EXCHANGE_API_SECRET"' in html
        assert html.count("removerSegredo('EXCHANGE_API_KEY')") >= 1
        assert "Rotação" in html, "a tela precisa explicar como trocar a chave"

    def test_catalogo_le_a_rota_real_e_nao_promete(self):
        html = _html()
        t = _funcao(html, "async function catalogoCarregar(")
        assert "'/v1/personal/exchanges'" in t
        for estado in ("AVAILABLE_TESTNET", "AVAILABLE_READ_ONLY",
                       "OFFICIAL_MCP_READ_ONLY_PREPARED"):
            assert estado in t, "estado real do catálogo precisa aparecer como é"
        assert "execution_enabled" in t, (
            "o painel precisa mostrar que execução está desligada")
        assert "escapeHtml" in t, "dados do servidor entram escapados (AUD rodada-8)"
        assert "sem credenciais" in t and "sem rede" in t
        assert "x.read_only_enabled&&x.integration==='ccxt'" in t, (
            "leitura pública CCXT não pode ser anunciada para a sala MCP")

    def test_capacidades_por_corretora_com_aviso_honesto(self):
        html = _html()
        t = _funcao(html, "function capacidadesCorretora(")
        assert "'/capabilities'" in t, (
            "capacidades vêm da rota real, montada com o id da corretora")
        assert "UNVERIFIED" in t, "capacidade anunciada ≠ corretora testada"
        assert "verified" in t and "live_enabled" in t

    def test_leitura_publica_tem_acao_e_nao_usa_credenciais(self):
        html = _html()
        catalogo = _funcao(html, "async function catalogoCarregar(")
        sonda = _funcao(html, "async function saudePublicaCorretora(")
        assert "saudePublicaCorretora" in catalogo
        assert "'/public-health'" in sonda
        assert "nenhuma credencial usada" in sonda
        assert "ordem enviada" in sonda

    def test_iq_option_tem_sonda_mcp_passiva(self):
        html = _html()
        catalogo = _funcao(html, "async function catalogoCarregar(")
        sonda = _funcao(html, "async function saudeMcpIqOption(")
        assert "saudeMcpIqOption" in catalogo
        assert "'/v1/personal/exchanges/iq_option/mcp-health'" in sonda
        assert "nenhuma ferramenta chamada" in sonda
        assert "nenhuma ordem enviada" in sonda

    def test_reconciliacao_usa_a_rota_real(self):
        html = _html()
        t = _funcao(html, "async function brokerReconciliar(")
        assert "'/v1/desk/reconciliar'" in t
        assert "divergencias" in t and "limitacoes" in t and "seguro" in t, (
            "divergência ≠ limitação: a tela mostra as duas separadas")

    def test_broker_test_renderiza_detalhe(self):
        html = _html()
        t = _funcao(html, "async function brokerTest(")
        assert "brokerDetalhe" in t
        for campo in ("ccxt_id", "mode", "teto_por_ordem", "keys_set", "aviso"):
            assert campo in t, f"detalhe da validação precisa mostrar {campo}"


class TestPreparacaoReal:
    def test_pagina_inicio_tem_preparacao_visivel(self):
        html = _html()
        assert 'id="preparoReal"' in html
        assert 'onclick="preparoRealLoad()"' in html

    def test_preparacao_le_o_checklist_real_e_diz_por_que_bloqueia(self):
        html = _html()
        t = _funcao(html, "async function preparoRealLoad(")
        assert "'/v1/live/status'" in t
        assert "checklist" in t and "pendencias" in t
        assert "expira_em" in t and "aviso" in t
        assert "BLOQUEADO" in t, "o estado real (bloqueado) precisa estar visível"
        assert "escapeHtml" in t

    def test_preparacao_nao_ativa_nada(self):
        html = _html()
        t = _funcao(html, "async function preparoRealLoad(")
        assert "method:'POST'" not in t and 'method:"POST"' not in t, (
            "ver preparação é leitura pura — nada é enviado ao servidor")
        assert "live/parada-emergencia" not in t

    def test_configuracao_do_piloto_marca_destino_como_conta_real(self):
        html = _html()
        t = _funcao(html, "async function pilotoConfigurar(")
        assert "conta_real:true" in t
        assert "prova.valida" in t

    def test_prova_salva_corretora_e_nao_reusa_chave_em_outro_provedor(self):
        html = _html()
        t = _funcao(html, "async function brokerPrepararLeitura(")
        assert "TRADINGAGENTS_EXCHANGE:exchange" in t
        assert "REAL_READ_ONLY_EXCHANGE" in t
        assert "exchange!==realReadOnlyExchange" in t
        assert "(!chave||!segredo)" in t

    def test_piloto_exibe_conta_limites_e_validade_antes_da_decisao(self):
        html = _html()
        t = _funcao(html, "async function escolaCentral(")
        for campo in ("conta_real", "capital_maximo", "perda_diaria_maxima",
                      "perda_total_maxima", "prazo_dias", "valido_ate"):
            assert campo in t, f"o proprietário precisa revisar {campo} no painel"
        assert "window._pilotosConfigurados" in t

    def test_autorizacao_confirma_limites_e_permanece_paper(self):
        html = _html()
        t = _funcao(html, "async function pilotoAutorizar(")
        assert "window._pilotosConfigurados" in t
        for campo in ("capital_maximo", "perda_diaria_maxima",
                      "perda_total_maxima", "prazo_dias"):
            assert campo in t
        assert "confirm(resumo)" in t
        assert "'/autorizar'" in t
        assert "method:'POST'" in t
        assert "continua PAPER" in t and "LIVE permanece bloqueado" in t

    def test_revogacao_exige_motivo_e_usa_rota_dedicada(self):
        html = _html()
        t = _funcao(html, "async function pilotoRevogar(")
        assert "window._pilotosConfigurados" in t
        assert "prompt(" in t and "motivo.trim()" in t
        assert "'/revogar'" in t
        assert "JSON.stringify({motivo:motivo.trim()})" in t
        assert "method:'POST'" in t


class TestTravasDoPainel:
    """O painel inteiro: sem caminho para dinheiro real, saque ou transferência."""

    def test_nenhum_caminho_de_ordem_saque_transferencia(self):
        html = _html()
        for proibido in ("broker/order", "withdraw", "transfer",
                         "saque", "sacar"):
            assert proibido not in html.lower(), (
                f"{proibido!r} não pode existir no painel (missão §3)")

    def test_live_nunca_habilitado_pelo_painel(self):
        html = _html()
        assert "MESA_LIVE_LIBERADA" not in html, (
            "o painel não fala de liberação de código — é trava de código, "
            "não configuração de tela")
        assert "LIVE_TRADING_CONFIRM" not in html or "disabled" in html.lower()
        m = re.search(r"<option value=\"live\">", html)
        assert m is None, "modo LIVE não pode ser uma opção selecionável"

    def test_teto_e_perda_visiveis_na_validacao(self):
        html = _html()
        t = _funcao(html, "async function brokerTest(")
        assert "teto_por_ordem" in t and "perda_maxima_dia" in t
