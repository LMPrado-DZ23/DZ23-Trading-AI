"""Identidade das notificações Windows (missão N01).

O toast precisa sair como "DZ23 Trading AI" (AUMID registrado, gerenciável
nas Configurações do Windows) e o texto do alerta precisa dizer o MODO real
da ordem — o achado do dono foi "Entrada confirmada: ord-3" dizendo
"executou na corretora" para uma ordem PAPER.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_alertas_de_entrada_carregam_o_modo():
    from service.app import _rotulo_modo_execucao, montar_alerta_entrada

    paper = montar_alerta_entrada("ord-3", "paper")
    assert paper["titulo"].startswith("[SIMULADO (PAPER)]")
    assert "não foi enviada à corretora real" in paper["detalhe"]

    testnet = montar_alerta_entrada("ord-9", "testnet")
    assert testnet["titulo"].startswith("[TESTNET")
    assert "sem dinheiro real" in testnet["detalhe"]

    live = montar_alerta_entrada("ord-1", "live")
    assert live["titulo"].startswith("[REAL (LIVE)]")

    # Modo vazio/desconhecido NUNCA pode virar promessa de execução real.
    assert _rotulo_modo_execucao("") == "SIMULADO (PAPER)"
    assert _rotulo_modo_execucao("desconhecido") == "SIMULADO (PAPER)"


def test_script_toast_usa_aumid_do_app():
    from tradingagents.desk.canais_alerta import _SCRIPT_TOAST, AUMID

    assert AUMID == "DZ23 Trading AI"
    script = _SCRIPT_TOAST.format(titulo="t", corpo="c", aumid=AUMID)
    assert "CreateToastNotifier('DZ23 Trading AI')" in script


def test_detalhe_do_toast_nunca_promete_visualizacao():
    import tradingagents.desk.canais_alerta as canais

    fonte = Path(canais.__file__).read_text(encoding="utf-8")
    assert "a visualização depende do usuário" in fonte
    # Sucesso da API do Windows != o usuário viu a notificação.
    assert "toast exibido" not in fonte


@pytest.mark.skipif(sys.platform != "win32", reason="registro é do Windows")
def test_identidade_registrada_no_windows():
    import winreg

    from tradingagents.desk.canais_alerta import AUMID, registrar_identidade_windows

    resultado = registrar_identidade_windows()
    assert resultado["ok"] is True
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        f"Software\\Classes\\AppUserModelId\\{AUMID}",
    ) as chave:
        nome, _ = winreg.QueryValueEx(chave, "DisplayName")
    assert nome == AUMID


def test_setup_e_launcher_assinam_a_mesma_identidade():
    iss = (ROOT / "desktop" / "installer" / "setup.iss").read_text(
        encoding="utf-8")
    assert "Software\\Classes\\AppUserModelId\\DZ23 Trading AI" in iss
    assert "AppUserModelID" in iss
    launcher = (ROOT / "desktop" / "launcher.py").read_text(encoding="utf-8")
    assert "SetCurrentProcessExplicitAppUserModelID" in launcher
