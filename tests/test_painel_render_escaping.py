"""Auditoria AUD: escapagem de dados do servidor em pontos de innerHTML.

Convenção do painel: todo dado vindo do servidor passa por ``escapeHtml``
antes de entrar em ``innerHTML``. Dois pontos destoavam — ``operacaoReal``
(estado/motivo do modo de operação real) e ``renderEvents`` (eventos da
agência). Este arquivo os pina como regressão estática (o painel é
minificado em linha única; a asserção é sobre o bloco renderizador).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "service" / "static" / "index.html"


def _html() -> str:
    return HTML.read_text(encoding="utf-8")


def test_escape_html_continua_definido():
    assert "function escapeHtml(" in _html()


def test_operacao_real_escapa_estado_e_motivo():
    m = re.search(r"\$\('operacaoReal'\)\.innerHTML=(.*?);", _html(), re.S)
    assert m, "bloco de operacaoReal não encontrado"
    bloco = m.group(1)
    assert bloco.count("escapeHtml") >= 2, (
        "op.estado e op.motivo precisam passar por escapeHtml: " + bloco
    )


def test_render_events_escapa_campos_do_servidor():
    m = re.search(r"function renderEvents\(events,target\)\{(.*)\}", _html())
    assert m, "renderEvents não encontrado"
    linha = m.group(1)
    assert linha.count("escapeHtml") >= 3, (
        "event_type, aggregate_id e sequence precisam passar por escapeHtml: "
        + linha
    )
