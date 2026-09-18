"""Camada de compatibilidade central para Python 3.10+.

O projeto declara `requires-python = ">=3.10"`. `enum.StrEnum` só existe a
partir do 3.11; aqui há um backport FIEL (o mesmo comportamento de string do
`StrEnum` do CPython 3.11) num único lugar testado, em vez de `try/except`
espalhados pelo código. Módulos que precisam de `StrEnum` importam daqui.
"""
from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # pragma: no cover - caminho exercido no CI py3.10
    from enum import Enum

    class StrEnum(str, Enum):
        """Backport de `enum.StrEnum` (3.11).

        Mesmo contrato do CPython 3.11: o membro É a string do seu valor,
        `str()`/`format()` devolvem o valor (não `Classe.MEMBRO`), e `auto()`
        gera o nome em minúsculas. `__str__`/`__format__` delegam para `str`,
        exatamente como a implementação da stdlib.
        """

        __str__ = str.__str__
        __format__ = str.__format__

        @staticmethod
        def _generate_next_value_(name, start, count, last_values):
            return name.lower()


__all__ = ["StrEnum"]
