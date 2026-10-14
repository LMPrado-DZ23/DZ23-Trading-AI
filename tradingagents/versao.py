"""Fonte única de versão em tempo de execução.

pyproject.toml é a fonte da verdade: executando a partir do código-fonte
(dev/editable) o número é lido direto dele — metadado editable fica velho
após um bump e não pode vencer o arquivo. No exe empacotado não existe
pyproject.toml: vale o metadado do pacote (`--copy-metadata`). Sem nenhum
dos dois, "dev" — nunca um número inventado.
"""

from __future__ import annotations

import re
from pathlib import Path


def _da_pyproject() -> str | None:
    caminho = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if not caminho.exists():
        return None
    achado = re.search(
        r'^version\s*=\s*"([^"]+)"', caminho.read_text(encoding="utf-8"), re.MULTILINE
    )
    return achado.group(1) if achado else None


def _do_metadado() -> str | None:
    try:
        from importlib.metadata import version

        return version("tradingagents")
    except Exception:
        return None


def versao_do_pacote() -> str:
    return _da_pyproject() or _do_metadado() or "dev"


__all__ = ["versao_do_pacote"]
