"""Contrato: a landing usa EXATAMENTE o catálogo canônico (sem preço duplicado
sem teste). Cobre o seletor JS e o JSON-LD (SEO)."""
import re
from pathlib import Path

from tradingagents.billing import catalog as C

SITE = Path(__file__).resolve().parents[1] / "service" / "static" / "site.html"


def test_math_do_catalogo():
    a = C.CATALOGO["dz23_annual"]
    assert a.equiv_mensal_cents == 10000            # R$ 100/mês
    assert a.economia_cents == 60000                # R$ 600
    assert a.desconto_pct == 33
    assert C.CATALOGO["dz23_quarterly"].economia_cents == 4500
    assert C.CATALOGO["dz23_semiannual"].economia_cents == 18000


def test_landing_js_bate_com_o_catalogo():
    html = SITE.read_text(encoding="utf-8")
    # meses:N,total:R (em reais) no objeto PLANOS
    pares = re.findall(r"meses:(\d+),total:(\d+)", html)
    assert pares, "não achei os planos no site.html"
    por_meses = {int(m): int(t) * 100 for m, t in pares}
    for p in C.CATALOGO.values():
        assert por_meses.get(p.meses) == p.preco_cents, \
            f"site diverge do catálogo em {p.meses} meses"


def test_jsonld_bate_com_o_catalogo():
    html = SITE.read_text(encoding="utf-8")
    precos = {int(round(float(x) * 100))
              for x in re.findall(r'"price":"(\d+\.\d+)","priceCurrency":"BRL"', html)}
    esperado = {p.preco_cents for p in C.CATALOGO.values()}
    assert esperado <= precos, "JSON-LD não cobre o catálogo canônico"
