"""Honestidade de cópias públicas e da matriz de requisitos (V9-DOC-02).

Dois defeitos comprováveis de documentação:
(a) o cartão "Dono único" do site afirmava que cadastro/plano/cobrança
    "não existem no código" — falso: as rotas herdadas existem no repositório
    e ficam desligadas por trava fixa (``SINGLE_OWNER_ENFORCED = True`` é
    constante do código, não flag de ambiente; o middleware responde 404
    antes de auth/rotas — ``tests/test_single_owner_g2.py`` prova);
(b) a linha de "Testes e homologação" da matriz V8 agregava "reconexão" num
    HOMOLOGADO mais amplo que a evidência — precisa de correção datada que
    preserve a tabela histórica e declare o recorte testado e o que não está
    homologado (reconexão contra corretora real segue BLOQUEADO_EXTERNO).
"""
from __future__ import annotations

from pathlib import Path

SITE = Path("service/static/site.html").read_text(encoding="utf-8")
MATRIZ = Path("docs/mission/entrega-final/MATRIZ-REQUISITOS-V8.md").read_text(encoding="utf-8")
APP = Path("service/app.py").read_text(encoding="utf-8")


class TestSiteDonoUnico:
    def test_nao_nega_a_existencia_do_codigo_legado(self):
        assert "não existem no código" not in SITE, (
            "alegação falsa: as rotas SaaS existem no repositório, apenas bloqueadas"
        )

    def test_declara_trava_fixa_e_404(self):
        assert "respondem 404" in SITE, "o site precisa dizer como o bloqueio acontece"
        assert "histórico do código" in SITE, "o site precisa ser honesto sobre o código herdado"

    def test_mecanismo_de_bloqueio_continua_no_backend(self):
        # se a trava mudar de forma, a cópia do site vira mentira de novo
        assert "SINGLE_OWNER_ENFORCED = True" in APP
        assert "SAAS_BLOCKED_PREFIXES" in APP


class TestMatrizRequisitosReconexao:
    def test_reconexao_tem_correcao_datada(self):
        assert "Correção datada (2026-10-15, MISSÃO V9/F7)" in MATRIZ, (
            "a linha de §16 mantém 'reconexão' num HOMOLOGADO amplo sem a correção datada"
        )

    def test_correcao_declara_o_recorte_e_o_nao_homologado(self):
        assert "reconexão contra corretora real" in MATRIZ, (
            "a correção precisa declarar explicitamente o que NÃO está homologado"
        )
        assert "recorte testado" in MATRIZ
