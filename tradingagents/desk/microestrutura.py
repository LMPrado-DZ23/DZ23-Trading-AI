"""MO-2-A — Microestrutura read-only (livro L2) para a mesa.

Só LEITURA pública de livro de ofertas — nunca caminho de ordem. Quem lê
mercado não envia ordem (mesma separação de poderes do market.py).

Regras codificadas:
- Nada de número inventado: dado ausente/cruzado/malformado ⇒ estado
  honesto SEM números (indisponivel/inconsistente);
- Validade temporal explícita: leitura mais velha que `max_idade_s` ⇒
  `vencida` — os números da última leitura ficam, marcados com a idade;
- Corretora sem `fetchOrderBook` NATIVO (flag True no ccxt; 'emulated' não
  vale) ⇒ `nao_suportado`, sem tocar a rede;
- Falha de rede nunca mente: sem leitura anterior ⇒ `indisponivel`; com
  leitura anterior ⇒ `vencida` com o motivo da falha;
- Slippage estimada POR LIVRO (caminhada de níveis). Profundidade
  insuficiente ⇒ None (não se chuta preço); estimativa só sobre leitura
  `disponivel` (vencida/inconsistente não alimentam decisão).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

ESTADOS = ("disponivel", "indisponivel", "inconsistente", "vencida",
           "nao_suportado", "nao_aplicavel")

_CASAS = Decimal("0.0001")
_CEM_MIL = Decimal("10000")
_MAX_NIVEIS_RETRATO = 5


def _d(valor) -> Decimal:
    return Decimal(str(valor))


def _texto(valor: Decimal | None) -> str | None:
    """Decimal ⇒ texto estável (4 casas, zeros à direita removidos)."""
    if valor is None:
        return None
    texto = format(valor.quantize(_CASAS), "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto or "0"


@dataclass(frozen=True, slots=True)
class LeituraDeLivro:
    """Fotografia do livro L2 — honesta sobre ausência, idade e origem."""
    instrumento: str
    exchange: str
    estado: str
    motivo: str
    obtido_em: str = ""
    idade_s: int = 0
    bid: Decimal | None = None
    ask: Decimal | None = None
    spread_bps: Decimal | None = None
    profundidade_bid: Decimal | None = None
    profundidade_ask: Decimal | None = None
    imbalance: Decimal | None = None
    niveis_usados: int = 0
    niveis_bid: tuple = ()
    niveis_ask: tuple = ()

    def para_dict(self) -> dict:
        """JSON-safe: sem Decimal/datetime soltos; níveis top-5 para a UI."""
        return {
            "instrumento": self.instrumento,
            "exchange": self.exchange,
            "estado": self.estado,
            "motivo": self.motivo,
            "obtido_em": self.obtido_em,
            "idade_s": self.idade_s,
            "somente_leitura": True,
            "bid": _texto(self.bid),
            "ask": _texto(self.ask),
            "spread_bps": _texto(self.spread_bps),
            "profundidade_bid": _texto(self.profundidade_bid),
            "profundidade_ask": _texto(self.profundidade_ask),
            "imbalance": _texto(self.imbalance),
            "niveis_usados": self.niveis_usados,
            "niveis_bid": [[_texto(p), _texto(q)]
                           for p, q in self.niveis_bid[:_MAX_NIVEIS_RETRATO]],
            "niveis_ask": [[_texto(p), _texto(q)]
                           for p, q in self.niveis_ask[:_MAX_NIVEIS_RETRATO]],
        }


def _vazia(exchange: str, instrumento: str, estado: str, motivo: str, *,
           agora: datetime) -> LeituraDeLivro:
    return LeituraDeLivro(instrumento=instrumento, exchange=exchange,
                          estado=estado, motivo=motivo,
                          obtido_em=agora.isoformat())


def _niveis(valor, *, invertido: bool) -> tuple | None:
    """Normaliza um lado do livro em tuplas (preco, qtd) positivas.
    Qualquer nível malformado/não-positivo ⇒ None (lado inconsistente)."""
    if not isinstance(valor, (list, tuple)) or not valor:
        return None
    niveis = []
    for item in valor:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            return None
        try:
            preco, qtd = _d(item[0]), _d(item[1])
        except (InvalidOperation, ValueError, TypeError):
            return None
        if not preco.is_finite() or not qtd.is_finite():
            return None
        if preco <= 0 or qtd <= 0:
            return None
        niveis.append((preco, qtd))
    return tuple(sorted(niveis, key=lambda x: x[0], reverse=invertido))


def analisar_livro(exchange: str, instrumento: str, bruto, *,
                   agora: datetime | None = None, max_idade_s: int = 15,
                   niveis: int = 10,
                   obtido_em: datetime | None = None) -> LeituraDeLivro:
    """Analisa um livro cru (bids/asks) com validação e validade temporal.

    Ordem de verificação: ausente ⇒ indisponivel; malformado/lado vazio/
    nível não-positivo ⇒ inconsistente; bid >= ask ⇒ inconsistente
    ('cruzado'). Só livro válido ganha números; leitura mais velha que
    `max_idade_s` ⇒ vencida (números ficam, marcados com idade)."""
    agora = agora or datetime.now(timezone.utc)
    if bruto is None:
        return _vazia(exchange, instrumento, "indisponivel",
                      "sem dados de livro da fonte", agora=agora)
    if not isinstance(bruto, dict):
        return _vazia(exchange, instrumento, "inconsistente",
                      "livro em formato inesperado (não é dict)", agora=agora)
    bids = _niveis(bruto.get("bids"), invertido=True)
    asks = _niveis(bruto.get("asks"), invertido=False)
    if bids is None or asks is None:
        return _vazia(exchange, instrumento, "inconsistente",
                      "livro com lado vazio ou nível malformado/não-positivo",
                      agora=agora)
    bid, ask = bids[0][0], asks[0][0]
    if bid >= ask:
        return _vazia(exchange, instrumento, "inconsistente",
                      f"livro cruzado: bid {_texto(bid)} >= ask "
                      f"{_texto(ask)}", agora=agora)
    usados = min(niveis, len(bids), len(asks))
    profundidade_bid = sum(q for _, q in bids[:niveis])
    profundidade_ask = sum(q for _, q in asks[:niveis])
    mid = (bid + ask) / 2
    spread_bps = (ask - bid) / mid * _CEM_MIL
    total = profundidade_bid + profundidade_ask
    imbalance = ((profundidade_bid - profundidade_ask) / total
                 if total else Decimal("0"))
    obtido = obtido_em or agora
    idade = max(0, int((agora - obtido).total_seconds()))
    campos = dict(
        instrumento=instrumento, exchange=exchange,
        obtido_em=obtido.isoformat(), idade_s=idade,
        bid=bid, ask=ask,
        spread_bps=spread_bps.quantize(_CASAS),
        profundidade_bid=profundidade_bid,
        profundidade_ask=profundidade_ask,
        imbalance=imbalance.quantize(_CASAS),
        niveis_usados=usados, niveis_bid=bids, niveis_ask=asks)
    if idade > max_idade_s:
        return LeituraDeLivro(estado="vencida", motivo=(
            f"leitura vencida: idade {idade} s excede validade de "
            f"{max_idade_s} s — números da última leitura, marcados"),
            **campos)
    return LeituraDeLivro(estado="disponivel", motivo="", **campos)


def slipagem_estimada(leitura: LeituraDeLivro, lado: str,
                      quantidade) -> dict | None:
    """Slippage estimada caminhando os níveis do livro.

    Compra come o lado dos asks; venda, o lado dos bids. Devolve None
    quando a leitura não está `disponivel`, o lado/quantidade é inválido
    ou a profundidade não cobre a quantidade — nunca se inventa preço."""
    if leitura.estado != "disponivel":
        return None
    if lado not in ("compra", "venda"):
        return None
    try:
        qtd = _d(quantidade)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not qtd.is_finite() or qtd <= 0:
        return None
    niveis = leitura.niveis_ask if lado == "compra" else leitura.niveis_bid
    melhor = leitura.ask if lado == "compra" else leitura.bid
    restante = qtd
    custo = Decimal("0")
    consumidos = 0
    for preco, disponivel in niveis:
        if restante <= 0:
            break
        usar = min(disponivel, restante)
        custo += preco * usar
        restante -= usar
        consumidos += 1
    if restante > 0:
        return None                       # profundidade insuficiente
    vwap = custo / qtd
    slip = ((vwap - melhor) if lado == "compra" else (melhor - vwap)) \
        / melhor * _CEM_MIL
    return {"lado": lado, "quantidade": _texto(qtd),
            "preco_best": _texto(melhor),
            "preco_medio_estimado": _texto(vwap),
            "slipagem_bps": _texto(slip),
            "niveis_consumidos": consumidos}


__all__ = ["ESTADOS", "LeituraDeLivro", "analisar_livro", "slipagem_estimada"]
