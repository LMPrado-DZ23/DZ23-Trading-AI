"""Identidade de uma ordem — uma normalização só, para todas as camadas.

Cada corretora expõe o identificador do cliente num campo diferente:
Binance usa `clientOrderId`, OKX usa `info.clOrdId`, e cancelamentos trazem
`origClientOrderId`. Quando o adaptador e a reconciliação mantêm listas
próprias desses campos, uma ordem perfeitamente conhecida vira "órfã" só por
diferença de representação — e o portão fecha por um problema que não existe.

Pior: listas divergentes envelhecem em ritmos diferentes. Alguém adiciona um
campo novo num lugar e esquece do outro, e o defeito só aparece na corretora
que usa aquele campo.
"""
from __future__ import annotations

# Campos onde as corretoras colocam identificadores de ordem. Ordem não
# importa — o resultado é um conjunto.
_CAMPOS_RAIZ = ("id", "clientOrderId", "orderId", "order")
_CAMPOS_INFO = ("clientOrderId", "clOrdId", "origClientOrderId", "orderId",
                "ordId", "algoClOrdId")


def order_identities(order: dict | None) -> set[str]:
    """Todos os identificadores pelos quais esta ordem pode ser reconhecida.

    Valores vazios, None e a string "None" não entram: um campo ausente não
    é identidade, e tratá-lo como tal faria ordens distintas colidirem.
    """
    if not isinstance(order, dict):
        return set()
    bruto = [order.get(campo) for campo in _CAMPOS_RAIZ]
    info = order.get("info")
    if isinstance(info, dict):
        bruto += [info.get(campo) for campo in _CAMPOS_INFO]
    return {str(v).strip() for v in bruto
            if v is not None and str(v).strip() not in ("", "None")}


def matches(order: dict | None, identidades: set[str]) -> bool:
    """A ordem remota corresponde a alguma identidade conhecida?"""
    return bool(order_identities(order) & set(identidades))


__all__ = ["order_identities", "matches"]
