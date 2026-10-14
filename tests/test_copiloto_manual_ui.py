"""Contrato de UI do Copiloto Manual (E4), página "Minhas Operações".

A página existe no menu e no roteador, fala SÓ com as rotas /v1/manual/* do
E3 (nenhuma rota de execução/ordem) e mantém a promessa central por escrito:
o copiloto acompanha, calcula e alerta — quem opera é o dono.
"""

from __future__ import annotations

from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / "service" / "static" / "index.html"


def _html() -> str:
    return HTML.read_text(encoding="utf-8")


def test_pagina_manual_no_menu_e_no_roteador():
    html = _html()
    assert "['manual','▣','Minhas Operações']" in html
    assert 'id="page-manual"' in html
    assert "if(id==='manual')manualLoad();" in html


def test_pagina_chama_somente_rotas_manual_do_e3():
    html = _html()
    for rota in (
        "/v1/manual/modo",
        "/v1/manual/positions",
        "/v1/manual/positions/'+manualSel+'/events",
        "/v1/manual/positions/'+manualSel+'/monitoring-policy",
        "/v1/manual/positions/'+manualSel+(pausar?'/pause':'/resume')",
        "/v1/manual/positions/'+manualSel+'/revisao",
        "/v1/manual/alerts/",
        "/v1/manual/monitor/health",
    ):
        assert rota in html, f"rota ausente na página manual: {rota}"


def test_pagina_manual_nao_referencia_rotas_de_execucao():
    html = _html()
    inicio = html.find('id="page-manual"')
    fim = html.find("</section>", inicio)
    assert 0 < inicio < fim
    secao = html[inicio:fim]
    for proibida in ("/v1/desk/", "/v1/orders", "executar ordem", "enviar ordem"):
        assert proibida not in secao, f"página manual não pode tocar {proibida}"


def test_promessa_honesta_na_pagina():
    html = _html()
    assert "ele nunca opera por você" in html
    assert "preço realmente executado" in html
    assert "cotação de tela nunca substitui" in html


def test_container_de_alertas_do_detalhe_existe():
    html = _html()
    assert 'id="manualAlertas"' in html, "detalhe precisa do container #manualAlertas"


def test_funcoes_ui_manual_definidas():
    html = _html()
    funcoes = (
        "manualLoad", "manualRenderModo", "manualCard", "manualDetalheIdx",
        "manualModoToggle", "manualCriar", "manualDetalhe", "manualSalvarPolitica",
        "manualEnviarEvento", "manualAlternarMonitor", "manualRevisao",
        "manualAlertas", "manualAckIdx", "manualHealth", "manualFecharDetalhe",
    )
    for fn in funcoes:
        assert f"function {fn}(" in html or f"async function {fn}(" in html, fn
