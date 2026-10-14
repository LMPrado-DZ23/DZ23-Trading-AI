"""Identidade visual DZ23 — marca, paleta e portão de login.

O logotipo original é a fonte da verdade (assets/brand) e os derivados em
service/static/marca saem de um gerador reproduzível. O portão de login
exibe o símbolo (que já contém DZ23) com "Trading AI" ao lado — nunca a
marca completa repetida — e os selos espelham o estado real do backend.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parents[1]
STATIC = RAIZ / "service" / "static"
MARCA = STATIC / "marca"
APP = (STATIC / "index.html").read_text(encoding="utf-8")
SITE = (STATIC / "site.html").read_text(encoding="utf-8")
FLOOR = (STATIC / "floor.html").read_text(encoding="utf-8")

PALETA = {
    "--bg": "#061526",
    "--line": "#16456B",
    "--text": "#EDF5FF",
    "--muted": "#93B6D3",
    "--green": "#42E2A3",
    "--cyan": "#00B8E6",
    "--red": "#FF637C",
}


def cliente():
    import service.app as m
    return TestClient(m.app)


def _dimensoes_png(caminho: Path) -> tuple[int, int]:
    dados = caminho.read_bytes()
    assert dados[:8] == b"\x89PNG\r\n\x1a\n", f"{caminho.name} não é PNG"
    largura, altura = struct.unpack(">II", dados[16:24])
    return largura, altura


class TestPaleta:
    def test_o_painel_usa_a_paleta_da_missao(self):
        for variavel, cor in PALETA.items():
            assert f"{variavel}:{cor}" in APP, f"{variavel} fora da paleta no painel"

    def test_o_site_publico_usa_a_mesma_paleta(self):
        for variavel, cor in PALETA.items():
            assert f"{variavel}:{cor}" in SITE, f"{variavel} fora da paleta no site"

    def test_o_floor_usa_a_mesma_paleta(self):
        for variavel, cor in PALETA.items():
            assert f"{variavel}:{cor}" in FLOOR, f"{variavel} fora da paleta no floor"

    def test_o_nome_antigo_nao_aparece_em_lugar_nenhum(self):
        for html in (APP, SITE, FLOOR):
            assert "TRADING PLATFORM" not in html


class TestPortaoDeLogin:
    def test_o_simbolo_ja_contem_dz23_e_a_marca_nao_se_repete(self):
        cabecalho = re.search(r'<div class="auth-head">(.*?)<div class="auth-body">', APP, re.S)
        assert cabecalho, "cabeçalho do portão não encontrado"
        bloco = cabecalho.group(1)
        assert "dz23-simbolo-192.png" in bloco, "o portão usa o símbolo"
        assert '<h1 class="auth-wordmark">Trading AI</h1>' in bloco
        assert "DZ23 Trading AI" not in bloco, (
            "a marca completa não se repete abaixo do símbolo")

    def test_os_selos_vem_do_backend_e_nao_de_texto_fixo(self):
        assert 'id="gateBadges"' in APP
        assert "carregarEstadoDoPortao" in APP
        assert "/v1/status/operacao" in APP
        for selo in ("PAPER MODE", "EXECUÇÃO GOVERNADA", "DINHEIRO REAL BLOQUEADO"):
            assert selo in APP, f"selo {selo} ausente no painel"

    def test_o_estado_nao_esta_fixo_em_texto_de_tela(self):
        assert "Dinheiro real bloqueado" not in APP

    def test_o_portao_tem_os_quatro_estados_de_autenticacao(self):
        for form in ("loginForm", "setupForm", "codigoForm", "recoverForm"):
            assert f'id="{form}"' in APP

    def test_nenhum_campo_de_senha_vem_preenchido(self):
        for campo in re.findall(r'<input[^>]*type="password"[^>]*>', APP):
            assert "value=" not in campo, f"campo de senha com valor fixo: {campo}"

    def test_o_painel_aponta_para_o_manifesto_e_tema(self):
        assert 'rel="manifest" href="/manifest.webmanifest"' in APP
        assert 'name="theme-color" content="#061526"' in APP


class TestAssetsDaMarca:
    def test_o_original_esta_preservado_fora_do_servido(self):
        original = RAIZ / "assets" / "brand" / "dz23-logo-original.png"
        assert original.exists(), "o logotipo original saiu do repositório"
        largura, altura = _dimensoes_png(original)
        assert largura >= 512 and altura >= 512

    def test_derivados_existem_com_as_dimensoes_anunciadas(self):
        esperados = {
            "dz23-logo.png": (1024, 1024),
            "dz23-logo-512.png": (512, 512),
            "dz23-logo-192.png": (192, 192),
            "dz23-simbolo-512.png": (512, 512),
            "dz23-simbolo-192.png": (192, 192),
            "dz23-simbolo-64.png": (64, 64),
            "favicon-32.png": (32, 32),
            "favicon-64.png": (64, 64),
        }
        for nome, medidas in esperados.items():
            assert _dimensoes_png(MARCA / nome) == medidas, f"{nome} divergente"

    def test_o_icone_windows_e_um_ico_valido(self):
        dados = (MARCA / "dz23-icone.ico").read_bytes()
        assert dados[:4] == b"\x00\x00\x01\x00"
        assert len(dados) > 10000, "ico pequeno demais para ter múltiplos tamanhos"


class TestRotasDaMarca:
    def test_os_arquivos_da_marca_sao_servidos(self):
        c = cliente()
        for nome in ("dz23-simbolo-192.png", "dz23-simbolo-64.png",
                     "favicon-64.png", "dz23-logo-512.png", "dz23-icone.ico"):
            assert c.get(f"/static/marca/{nome}").status_code == 200, nome

    def test_o_original_nao_e_servido_pela_web(self):
        assert cliente().get("/static/marca/dz23-logo-original.png").status_code == 404

    def test_o_manifesto_e_servido_no_formato_certo(self):
        r = cliente().get("/manifest.webmanifest")
        assert r.status_code == 200
        assert "manifest" in r.headers["content-type"]
        dados = json.loads(r.text)
        assert dados["name"] == "DZ23 Trading AI"
        assert dados["short_name"] == "Trading AI"
        assert dados["start_url"] == "/app"
        assert dados["theme_color"] == "#061526"
