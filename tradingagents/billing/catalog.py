"""Catálogo comercial CANÔNICO — fonte única de preços/períodos.

Consumido por landing, /v1/billing/planos, checkout, webhook, conta/assinatura,
JSON-LD e testes. Preço em centavos (evita float). Um produto completo; o que
muda é o período. Trial é 24 horas.

Não duplicar preços em HTML + Python: `tests/test_catalog_contract.py` prova
que a landing usa exatamente estes valores.
"""
from __future__ import annotations

from dataclasses import dataclass

MENSAL_CENTS = 15000   # base do equivalente mensal (R$ 150,00)
TRIAL_CODE = "trial_24h"
TRIAL_HORAS = 24


@dataclass(frozen=True)
class PlanoComercial:
    code: str
    nome: str
    meses: int
    preco_cents: int          # total cobrado no período

    @property
    def preco_brl(self) -> str:
        return _brl(self.preco_cents)

    @property
    def equiv_mensal_cents(self) -> int:
        return self.preco_cents // self.meses

    @property
    def economia_cents(self) -> int:
        return MENSAL_CENTS * self.meses - self.preco_cents

    @property
    def desconto_pct(self) -> int:
        base = MENSAL_CENTS * self.meses
        return round((1 - self.preco_cents / base) * 100) if base else 0


CATALOGO: dict[str, PlanoComercial] = {
    "dz23_monthly": PlanoComercial("dz23_monthly", "Mensal", 1, 15000),
    "dz23_quarterly": PlanoComercial("dz23_quarterly", "Trimestral", 3, 40500),
    "dz23_semiannual": PlanoComercial("dz23_semiannual", "Semestral", 6, 72000),
    "dz23_annual": PlanoComercial("dz23_annual", "Anual", 12, 120000),
}


def _brl(cents: int) -> str:
    return "R$ " + f"{cents / 100:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def is_valid_code(code: str) -> bool:
    return code in CATALOGO


def resumo() -> list[dict]:
    """Resumo público (landing / /v1/billing/planos)."""
    out = []
    for p in CATALOGO.values():
        out.append({
            "code": p.code, "nome": p.nome, "meses": p.meses,
            "preco_cents": p.preco_cents, "preco": p.preco_brl,
            "equiv_mensal_cents": p.equiv_mensal_cents,
            "equiv_mensal": _brl(p.equiv_mensal_cents),
            "desconto_pct": p.desconto_pct,
            "economia_cents": p.economia_cents,
            "economia": _brl(p.economia_cents),
            "maior_economia": p.code == "dz23_annual",
        })
    return out


__all__ = ["PlanoComercial", "CATALOGO", "TRIAL_CODE", "TRIAL_HORAS",
           "MENSAL_CENTS", "is_valid_code", "resumo"]
