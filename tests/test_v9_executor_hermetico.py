"""V9-TEST-01/TEST-02: o executor hermetico entrega testes sem credenciais da
estacao, sem dotenv e sem registro do Windows — e nunca ecoa valores herdados
nos artefatos de evidencia."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = ROOT / "tools" / "v9_pytest.py"
EVIDENCIA = ROOT / "docs" / "mission" / "evidence" / "v9-baseline"
CANARIO = "V9_CANARY_CREDENCIAL_FALSA"
VALOR = "valor-canario-nao-e-credencial"
LABEL = "v9-test01-child"

FILHO = """
import os
import sys

import pytest


def test_filho_sem_credenciais_da_estacao():
    # variaveis locais de proposito: falha de assert nunca faz o pytest
    # despejar os.environ inteiro (com chaves reais da estacao) no log.
    canario = os.environ.get("V9_CANARY_CREDENCIAL_FALSA")
    dotenv = os.environ.get("PYTHON_DOTENV_DISABLED")
    alerta = os.environ.get("ALERTA_WINDOWS")
    dinheiro = os.environ.get("REAL_MONEY_EXECUTION")
    multitenant = os.environ.get("MULTI_TENANT_ENABLED")
    assert canario is None, f"canario visivel no filho: {canario!r}"
    assert dotenv == "1"
    assert alerta == "false"
    assert dinheiro == "false"
    assert multitenant == "false"


@pytest.mark.skipif(sys.platform != "win32", reason="winreg so existe no Windows")
def test_registro_do_windows_bloqueado_pelo_gancho():
    import winreg
    with pytest.raises(RuntimeError):
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Software")
"""


@pytest.fixture
def canario_no_ambiente(monkeypatch):
    monkeypatch.setenv(CANARIO, VALOR)


def test_gancho_de_auditoria_precede_import_de_pytest():
    fonte = EXECUTOR.read_text(encoding="utf-8")
    filho = fonte.split('CHILD = """', 1)[1].split('"""', 1)[0]
    assert filho.index("sys.addaudithook(guard)") < filho.index("import pytest")


def test_controle_filho_comum_herda_o_ambiente_da_estacao(tmp_path, canario_no_ambiente):
    """Reproduz o vetor original (V9-TEST-01): sem o executor, o filho enxerga
    o ambiente da estacao — aqui representado pelo canario — e falha ao exigir
    isolamento. Este controle mantem a reproducao viva para sempre."""
    filho = tmp_path / "test_filho_cru.py"
    filho.write_text(FILHO, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", str(filho), "-q",
         "--noconftest", "-p", "no:cacheprovider", "-k", "sem_credenciais"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode != 0, "esperado vazamento do canario sem o executor"


def test_executor_entrega_filho_sem_credenciais_nem_registro(tmp_path, canario_no_ambiente):
    filho = tmp_path / "test_filho.py"
    filho.write_text(FILHO, encoding="utf-8")
    log = EVIDENCIA / f"{LABEL}.log"
    xml = EVIDENCIA / f"{LABEL}.xml"
    invocacao = EVIDENCIA / f"{LABEL}-invocation.json"
    for artefato in (log, xml, invocacao):
        artefato.unlink(missing_ok=True)
    r = subprocess.run([sys.executable, str(EXECUTOR), LABEL, str(filho)],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    texto = log.read_text(encoding="utf-8")
    assert "passed" in texto and "failed" not in texto
    registro = invocacao.read_text(encoding="utf-8")
    dados = json.loads(registro)
    assert CANARIO not in registro and VALOR not in registro
    assert CANARIO not in dados["environment_names"]
    assert "PYTHON_DOTENV_DISABLED" in dados["environment_names"]
    assert VALOR not in texto
    log.unlink()
    xml.unlink()
    invocacao.unlink()
