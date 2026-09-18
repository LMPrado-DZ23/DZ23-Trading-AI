"""Compatibilidade real com Python 3.10 (contrato `requires-python >=3.10`).

O `StrEnum` da stdlib só existe no 3.11; o projeto usa um backport central
(`tradingagents._compat`). Estes testes rodam em TODAS as versões e fixam o
MESMO comportamento de string em todas — no 3.10 exercitam o backport, no
3.11+ o `enum.StrEnum` da stdlib. Se divergirem, o teste falha.
"""
from __future__ import annotations

import json
from enum import auto

from tradingagents._compat import StrEnum
from tradingagents.domain.enums import Environment, Side


def test_membro_e_uma_str():
    assert isinstance(Side.BUY, str)
    assert Side.BUY == "BUY"
    assert Environment.LIVE == "LIVE"


def test_str_devolve_o_valor_nao_o_nome_qualificado():
    assert str(Side.BUY) == "BUY"
    assert f"{Side.SELL}" == "SELL"
    assert format(Environment.PAPER) == "PAPER"      # __format__ -> valor


def test_serializacao_json_usa_o_valor():
    assert json.dumps({"side": Side.BUY}) == '{"side": "BUY"}'
    assert json.dumps(list(Environment)) == \
        '["DEMO", "PAPER", "SHADOW", "LIVE"]'


def test_concatenacao_e_comparacao_de_string():
    assert Side.BUY + "-LIMIT" == "BUY-LIMIT"
    assert Side.BUY in {"BUY", "SELL"}
    assert Side("BUY") is Side.BUY


def test_auto_gera_nome_em_minusculas():
    class Cor(StrEnum):
        VERMELHO = auto()
        AZUL = auto()

    assert Cor.VERMELHO == "vermelho"
    assert str(Cor.AZUL) == "azul"
