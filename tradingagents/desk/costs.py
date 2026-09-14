"""Custos reais de operar (o que mata a maioria dos robôs).

Um sinal só vale se o movimento esperado paga **ida e volta**: taxa de
entrada, taxa de saída, spread e slippage. Em cripto de 1h isso soma perto de
0,3% por operação — uma estratégia com ganho médio de 0,4% fica no zero.

Aqui esse cálculo deixa de ser invisível: vira número explícito que o trader
precisa vencer antes de propor, e que o backtest desconta antes de dizer que
uma estratégia é boa.

Fontes das taxas: tabelas públicas padrão (taker) das exchanges, nível de
conta básico, sem desconto de token. Números conservadores de propósito —
errar para mais protege; errar para menos custa dinheiro.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# Taxa TAKER por exchange (fração). Ordem a mercado é sempre taker.
TAXAS_TAKER: dict[str, Decimal] = {
    "binance": Decimal("0.0010"),
    "bybit": Decimal("0.0010"),
    "okx": Decimal("0.0010"),
    "kucoin": Decimal("0.0010"),
    "gateio": Decimal("0.0020"),
    "bitget": Decimal("0.0010"),
    "kraken": Decimal("0.0026"),
    "coinbase": Decimal("0.0060"),        # a mais cara da lista, de longe
    "mercadobitcoin": Decimal("0.0070"),
}
TAXA_PADRAO = Decimal("0.0025")           # desconhecida: assume cara

# Spread típico (fração do preço) por liquidez do par.
SPREAD_POR_LIQUIDEZ: dict[str, Decimal] = {
    "alta": Decimal("0.0002"),            # BTC/USDT, ETH/USDT
    "media": Decimal("0.0008"),
    "baixa": Decimal("0.0030"),
}
PARES_ALTA_LIQUIDEZ = {"BTC/USDT", "ETH/USDT", "BTC/USD", "ETH/USD",
                       "BTC/BRL", "SOL/USDT"}


@dataclass(frozen=True)
class ModeloDeCusto:
    """Custo estimado de uma volta completa (entrar e sair)."""
    exchange: str
    taxa: Decimal
    spread: Decimal
    slippage: Decimal

    @property
    def ida_e_volta(self) -> Decimal:
        """Taxa 2x + spread 2x + slippage 2x. É isto que o sinal precisa vencer."""
        return (self.taxa + self.spread + self.slippage) * 2

    def detalhe(self) -> str:
        return (f"taxa {self.taxa:.2%} · spread {self.spread:.2%} · "
                f"slippage {self.slippage:.2%} (ida e volta: "
                f"{self.ida_e_volta:.2%})")


def liquidez_do_par(instrumento: str) -> str:
    if instrumento.upper() in PARES_ALTA_LIQUIDEZ:
        return "alta"
    if instrumento.upper().endswith(("/USDT", "/USD", "/BRL")):
        return "media"
    return "baixa"


def slippage_estimado(instrumento: str, valor_ordem: Decimal,
                      volatilidade: float | None = None) -> Decimal:
    """Slippage cresce com o tamanho da ordem e com a volatilidade.

    Ordem pequena em par líquido quase não escorrega; ordem grande em par
    ruim, muito. Modelo simples e conservador — melhor superestimar.
    """
    base = {"alta": Decimal("0.0003"), "media": Decimal("0.0010"),
            "baixa": Decimal("0.0040")}[liquidez_do_par(instrumento)]
    if valor_ordem > Decimal("50000"):
        base *= Decimal("3")
    elif valor_ordem > Decimal("10000"):
        base *= Decimal("2")
    elif valor_ordem > Decimal("1000"):
        base *= Decimal("1.3")
    if volatilidade:
        # mercado agitado escorrega mais: até dobra o slippage
        base *= Decimal(str(round(min(2.0, 1 + volatilidade * 10), 3)))
    return base.quantize(Decimal("0.000001"))


def custo_de_operar(instrumento: str, exchange: str, valor_ordem: Decimal,
                    volatilidade: float | None = None) -> ModeloDeCusto:
    return ModeloDeCusto(
        exchange=exchange,
        taxa=TAXAS_TAKER.get((exchange or "").lower(), TAXA_PADRAO),
        spread=SPREAD_POR_LIQUIDEZ[liquidez_do_par(instrumento)],
        slippage=slippage_estimado(instrumento, valor_ordem, volatilidade))


def vale_a_pena(movimento_esperado: Decimal, custo: ModeloDeCusto,
                margem_seguranca: Decimal = Decimal("1.5")) -> tuple[bool, str]:
    """Decide se o sinal paga o custo, com margem.

    A margem existe porque a estimativa de movimento é a parte mais frágil
    da conta: exigir empatar é garantir prejuízo na média.
    """
    minimo = custo.ida_e_volta * margem_seguranca
    if movimento_esperado <= 0:
        return False, "movimento esperado nulo ou negativo"
    if movimento_esperado < minimo:
        return False, (f"movimento esperado {movimento_esperado:.2%} não paga o "
                       f"custo ({custo.detalhe()}) com margem de "
                       f"{margem_seguranca}x — precisaria de {minimo:.2%}")
    return True, (f"movimento {movimento_esperado:.2%} cobre o custo "
                  f"{custo.ida_e_volta:.2%} com folga")


def retorno_liquido(retorno_bruto: Decimal, custo: ModeloDeCusto) -> Decimal:
    """Converte resultado bruto em líquido. Backtest que não faz isto mente."""
    return retorno_bruto - custo.ida_e_volta


__all__ = ["ModeloDeCusto", "custo_de_operar", "vale_a_pena", "retorno_liquido",
           "slippage_estimado", "liquidez_do_par", "TAXAS_TAKER"]


# ---------------------------------------------------------------- viabilidade
def viabilidade(taxa_acerto: Decimal, ganho_medio: Decimal, perda_media: Decimal,
                custo: ModeloDeCusto) -> dict:
    """Responde a pergunta que decide tudo: isso ganha dinheiro DEPOIS dos
    custos? Aritmética simples, sem misticismo.

    esperanca_bruta = p*ganho - (1-p)*perda
    esperanca_liquida = esperanca_bruta - custo_ida_e_volta
    """
    p = taxa_acerto
    bruta = p * ganho_medio - (Decimal("1") - p) * perda_media
    liquida = bruta - custo.ida_e_volta
    # acerto mínimo para empatar depois dos custos
    denominador = ganho_medio + perda_media
    if denominador > 0:
        acerto_minimo = (perda_media + custo.ida_e_volta) / denominador
    else:
        acerto_minimo = Decimal("1")
    return {
        "esperanca_bruta": bruta,
        "esperanca_liquida": liquida,
        "custo_por_operacao": custo.ida_e_volta,
        "acerto_minimo_para_empatar": acerto_minimo,
        "viavel": liquida > 0,
        "veredito": (
            f"VIÁVEL: sobra {liquida:.2%} por operação depois dos custos"
            if liquida > 0 else
            f"INVIÁVEL: perde {abs(liquida):.2%} por operação. Seria preciso "
            f"acertar {acerto_minimo:.0%} (hoje {p:.0%}) ou reduzir custo/"
            f"aumentar o alvo."),
        "aviso": "cálculo de expectativa; não prevê o futuro",
    }
