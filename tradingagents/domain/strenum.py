"""Compatibilidade de enum.StrEnum.

enum.StrEnum foi adicionado no Python 3.11, mas o projeto declara
requires-python >= 3.10 (mantido: o CI testa a matriz 3.10-3.13).
Este módulo fornece o StrEnum real em >= 3.11 e um equivalente fiel em
3.10: str(member) e format(member) devolvem o valor, como no padrão.
"""
from __future__ import annotations

try:
    from enum import StrEnum  # Python >= 3.11
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        def __str__(self) -> str:
            return str.__str__(self)

        def __format__(self, format_spec: str) -> str:
            return str.__format__(self, format_spec)

        def _generate_next_value_(self, start, count, last_values):
            return self.name.lower()


__all__ = ["StrEnum"]
