"""FM-4 (§7): contrato read-only das rotas manuais — por DESCOBERTA, não lista.

Os pins existentes (test_copiloto_manual[_api].py, test_copiloto_sinais.py)
usam listas fixas de funções: uma rota /v1/manual NOVA nasceria fora do pin
e poderia, em tese, chamar execução sem que nada falhasse. Este teste descobre
as rotas no próprio app e a fonte de cada função no AST de service/app.py —
qualquer rota manual futura entra automaticamente no contrato.

A prova comportamental (ponta a ponta, §5–§8) está em
`docs/mission/evidence/fm4-ponta-a-ponta.md`.
"""
from __future__ import annotations

import ast
from pathlib import Path

from fastapi.routing import APIRoute

from service.app import app, auth

APP_PY = Path(__file__).resolve().parents[1] / "service" / "app.py"

_CHAMADA_PROIBIDA = {
    "create_order",
    "cancel_order",
    "close_position",
    "executar_ordem",
    "submit",
    "withdraw",
    "transfer",
}


def _rotas_manuais() -> list[APIRoute]:
    return [
        rota
        for rota in app.routes
        if isinstance(rota, APIRoute) and rota.path.startswith("/v1/manual")
    ]


def _funcoes_manuais() -> set[str]:
    return {rota.endpoint.__name__ for rota in _rotas_manuais()}


def _fonte_da_funcao(nome: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    arvore = ast.parse(APP_PY.read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)) and no.name == nome:
            return no
    raise AssertionError(f"função {nome!r} não encontrada em service/app.py")


class TestContratoReadOnlyPorDescoberta:
    def test_rotas_manuais_existem_e_tem_fonte_localizavel(self):
        nomes = _funcoes_manuais()
        assert len(nomes) >= 14, (
            f"esperado o elenco completo de rotas /v1/manual; achou {sorted(nomes)}")
        for nome in sorted(nomes):
            _fonte_da_funcao(nome)  # levanta AssertionError se sumiu do app.py

    def test_nenhuma_funcao_manual_descoberta_chama_execucao(self):
        for nome in sorted(_funcoes_manuais()):
            fonte = _fonte_da_funcao(nome)
            for filho in ast.walk(fonte):
                if not isinstance(filho, ast.Call):
                    continue
                chamada = ""
                if isinstance(filho.func, ast.Attribute):
                    chamada = filho.func.attr
                elif isinstance(filho.func, ast.Name):
                    chamada = filho.func.id
                assert chamada not in _CHAMADA_PROIBIDA, (
                    f"{nome} chama {chamada}() — viola o contrato read-only do §7")

    def test_todas_as_rotas_manuais_exigem_autenticacao(self):
        rotas = _rotas_manuais()
        assert len(rotas) == len(_funcoes_manuais()), (
            "duas rotas manuais compartilhando a mesma função escondem uma delas")
        for rota in rotas:
            assert any(dep.call is auth for dep in rota.dependant.dependencies), (
                f"{sorted(rota.methods)} {rota.path} sem dependência de auth")
