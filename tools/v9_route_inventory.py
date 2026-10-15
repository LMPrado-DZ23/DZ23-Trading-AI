"""V9 F7.1 — recontagem reproduzível de rotas × UI × teste no HEAD atual.

Conta rotas declaradas por decorador em `service/app.py` e em
`service/api/*.py`, e classifica cada rota por referência textual da
própria rota em (a) HTML estático do painel e (b) arquivos de teste.

Uma referência textual NÃO é prova de cobertura comportamental executada:
apenas localiza se algum consumidor/teste menciona o caminho. O resultado
existe para confrontar a alegação anterior (145 rotas / 52 sem UI / 16 sem
teste) com números ligados ao código atual.

Uso:  python tools/v9_route_inventory.py [--json caminho.json]
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
METODOS = {"get", "post", "put", "patch", "delete"}


def _depends_da_funcao(no: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Nomes passados a `Depends(...)` nos defaults dos parâmetros e em
    `dependencies=[...]` do decorador. É marcador textual/AST de que a rota
    declara uma dependência (tipicamente de autorização) — não é prova de
    que a dependência realmente nega acesso não autorizado."""
    nomes: list[str] = []

    def _nome(chamada: ast.Call) -> str:
        alvo = chamada.args[0] if chamada.args else None
        if isinstance(alvo, ast.Name):
            return alvo.id
        if isinstance(alvo, ast.Attribute):
            return alvo.attr
        return "?"

    def _coletar(expressao: ast.AST) -> None:
        for sub in ast.walk(expressao):
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "Depends"):
                nomes.append(_nome(sub))

    for padrao in list(no.args.defaults) + list(no.args.kw_defaults):
        if padrao is not None:
            _coletar(padrao)
    for decorador in no.decorator_list:
        if isinstance(decorador, ast.Call):
            for palavra in decorador.keywords:
                _coletar(palavra.value)
    return nomes


def _rotas_do_arquivo(caminho: Path, prefixo: str) -> list[dict]:
    fonte = caminho.read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    achadas: list[dict] = []
    for no in ast.walk(arvore):
        if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorador in no.decorator_list:
            if not isinstance(decorador, ast.Call):
                continue
            alvo = decorador.func
            if not isinstance(alvo, ast.Attribute) or alvo.attr not in METODOS:
                continue
            if not decorador.args:
                continue
            primeiro = decorador.args[0]
            if not isinstance(primeiro, ast.Constant) or not isinstance(primeiro.value, str):
                continue
            caminho_rota = prefixo + primeiro.value
            depends = _depends_da_funcao(no)
            achadas.append({
                "metodo": alvo.attr.upper(),
                "rota": caminho_rota,
                "funcao": no.name,
                "origem": str(caminho.relative_to(RAIZ)).replace("\\", "/"),
                "linha": no.lineno,
                "depends": sorted(set(depends)),
                "tem_depends": bool(depends),
            })
    return achadas


def _prefixo_do_router(caminho: Path) -> str:
    fonte = caminho.read_text(encoding="utf-8")
    m = re.search(r'APIRouter\(\s*prefix\s*=\s*"([^"]*)"', fonte)
    return m.group(1) if m else ""


def coletar_rotas() -> list[dict]:
    rotas = _rotas_do_arquivo(RAIZ / "service" / "app.py", "")
    for arquivo in sorted((RAIZ / "service" / "api").glob("*.py")):
        if arquivo.name == "__init__.py":
            continue
        rotas.extend(_rotas_do_arquivo(arquivo, _prefixo_do_router(arquivo)))
    return rotas


def _textos_ui() -> str:
    partes = []
    for html in sorted((RAIZ / "service" / "static").glob("*.html")):
        partes.append(html.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(partes)


def _textos_testes() -> str:
    partes = []
    for py in sorted((RAIZ / "tests").rglob("*.py")):
        partes.append(py.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(partes)


def _padrao(rota: str) -> re.Pattern:
    """Converte a rota em regex: parâmetros `{x}` casam com um segmento, e
    interpolação JS `${x}` também é aceita (o consumidor raramente escreve a
    rota literal quando há parâmetro)."""
    partes = re.split(r"\{[^}]*\}", rota)
    separador = r"(?:[^/\"'`\s]+|\$\{[^}]*\})"
    return re.compile(separador.join(re.escape(p) for p in partes))


def classificar(rotas: list[dict]) -> list[dict]:
    ui = _textos_ui()
    testes = _textos_testes()
    for rota in rotas:
        padrao = _padrao(rota["rota"])
        rota["tem_ui"] = padrao.search(ui) is not None
        rota["tem_teste"] = padrao.search(testes) is not None
    return rotas


def resumo(rotas: list[dict]) -> dict:
    total = len(rotas)
    com_ui = sum(1 for r in rotas if r["tem_ui"])
    com_teste = sum(1 for r in rotas if r["tem_teste"])
    com_depends = sum(1 for r in rotas if r["tem_depends"])
    return {
        "rotas_total": total,
        "com_referencia_ui": com_ui,
        "sem_referencia_ui": total - com_ui,
        "com_referencia_teste": com_teste,
        "sem_referencia_teste": total - com_teste,
        "sem_ui_e_sem_teste": sum(1 for r in rotas if not r["tem_ui"] and not r["tem_teste"]),
        "com_marcador_depends": com_depends,
        "sem_marcador_depends": total - com_depends,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_saida", default="")
    args = parser.parse_args(argv)

    rotas = classificar(coletar_rotas())
    rotas.sort(key=lambda r: (r["rota"], r["metodo"]))
    numeros = resumo(rotas)
    print(json.dumps(numeros, indent=2, ensure_ascii=False))
    sem_ui = [f'{r["metodo"]} {r["rota"]} ({r["origem"]}:{r["linha"]})'
              for r in rotas if not r["tem_ui"]]
    sem_teste = [f'{r["metodo"]} {r["rota"]} ({r["origem"]}:{r["linha"]})'
                 for r in rotas if not r["tem_teste"]]
    sem_depends = [f'{r["metodo"]} {r["rota"]} ({r["origem"]}:{r["linha"]})'
                   for r in rotas if not r["tem_depends"]]
    print(f"\n# Sem referência de UI ({len(sem_ui)}):")
    for linha in sem_ui:
        print("  " + linha)
    print(f"\n# Sem referência de teste ({len(sem_teste)}):")
    for linha in sem_teste:
        print("  " + linha)
    print(f"\n# Sem marcador Depends(...) ({len(sem_depends)}):")
    for linha in sem_depends:
        print("  " + linha)

    if args.json_saida:
        destino = Path(args.json_saida)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(json.dumps({
            "resumo": numeros,
            "rotas": rotas,
            "sem_referencia_ui": sem_ui,
            "sem_referencia_teste": sem_teste,
            "sem_marcador_depends": sem_depends,
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nJSON: {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
