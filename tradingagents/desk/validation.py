"""Validação honesta de estratégia (melhoria ②).

Roda a estratégia sobre dados históricos, dobra por dobra, **descontando os
custos reais**, e diz sem enfeite se ela sobrevive.

Regras que tornam o resultado confiável:
- Decisão usa apenas as velas ATÉ o instante da decisão. Nunca a vela atual
  inteira, nunca as seguintes.
- Cada operação paga taxa, spread e slippage de ida e volta.
- Janelas de teste não se sobrepõem (já garantido em `evaluation.py`).
- O resultado vem com o número de operações: 4 trades lucrativos não
  significam nada e o relatório diz isso.
- Não existe otimização de parâmetro aqui. Ajustar até o backtest ficar
  bonito é a forma mais popular de se enganar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .costs import custo_de_operar
from .models import Action, AgentStage, MarketSnapshot, utcnow


@dataclass
class Operacao:
    entrada_idx: int
    saida_idx: int
    preco_entrada: Decimal
    preco_saida: Decimal
    retorno_bruto: Decimal
    retorno_liquido: Decimal
    motivo_saida: str


@dataclass
class ResultadoValidacao:
    estrategia: str
    instrumento: str
    exchange: str
    operacoes: list[Operacao] = field(default_factory=list)
    velas_avaliadas: int = 0
    abstencoes: int = 0

    @property
    def total(self) -> int:
        return len(self.operacoes)

    @property
    def retorno_liquido_total(self) -> Decimal:
        return sum((o.retorno_liquido for o in self.operacoes), Decimal("0"))

    @property
    def retorno_bruto_total(self) -> Decimal:
        return sum((o.retorno_bruto for o in self.operacoes), Decimal("0"))

    @property
    def custo_total(self) -> Decimal:
        return self.retorno_bruto_total - self.retorno_liquido_total

    @property
    def taxa_acerto(self) -> float:
        if not self.operacoes:
            return 0.0
        return sum(1 for o in self.operacoes if o.retorno_liquido > 0) / self.total

    @property
    def retorno_medio(self) -> Decimal:
        return (self.retorno_liquido_total / self.total) if self.total else Decimal("0")

    @property
    def maior_perda_seguida(self) -> Decimal:
        pior, atual = Decimal("0"), Decimal("0")
        for o in self.operacoes:
            atual = min(Decimal("0"), atual + o.retorno_liquido)
            pior = min(pior, atual)
        return pior

    def veredito(self) -> dict:
        if self.total == 0:
            return {"conclusivo": False, "aprovada": False,
                    "resumo": (f"nenhuma operação em {self.velas_avaliadas} velas "
                               f"({self.abstencoes} abstenções) — a estratégia é "
                               "seletiva demais ou o período não serve a ela")}
        ressalvas = []
        if self.total < 20:
            ressalvas.append(f"amostra pequena: {self.total} operações")
        if self.custo_total > abs(self.retorno_bruto_total) * Decimal("0.5"):
            ressalvas.append("os custos consumiram mais da metade do resultado bruto")
        aprovada = self.retorno_liquido_total > 0 and self.total >= 20
        return {
            "conclusivo": not ressalvas,
            "aprovada": aprovada,
            "operacoes": self.total,
            "acerto": round(self.taxa_acerto, 3),
            "retorno_bruto": f"{self.retorno_bruto_total:.2%}",
            "custo_pago": f"{self.custo_total:.2%}",
            "retorno_liquido": f"{self.retorno_liquido_total:.2%}",
            "retorno_medio_por_operacao": f"{self.retorno_medio:.3%}",
            "pior_sequencia": f"{self.maior_perda_seguida:.2%}",
            "ressalvas": ressalvas,
            "resumo": (
                f"APROVADA: {self.retorno_liquido_total:+.2%} líquidos em "
                f"{self.total} operações"
                if aprovada else
                f"REPROVADA: {self.retorno_liquido_total:+.2%} líquidos em "
                f"{self.total} operações — os custos "
                f"({self.custo_total:.2%}) comeram o resultado"),
            "aviso": "teste sobre o passado; não prevê o futuro",
        }


def validar(trader, closes, instrumento: str, *, exchange: str = "binance",
            capital: Decimal = Decimal("1000"),
            velas_maximas_na_posicao: int = 48) -> ResultadoValidacao:
    """Passa a estratégia vela a vela, sem nunca olhar adiante.

    A saída usa o alvo e o stop da própria estratégia; se nenhum dispara em
    `velas_maximas_na_posicao`, encerra a mercado (posição eterna esconde
    prejuízo).
    """
    for estagio in (AgentStage.VALIDATED, AgentStage.SHADOW, AgentStage.PAPER_ENABLED):
        if trader.spec.stage is not estagio:
            try:
                trader.spec.move_to(estagio)
            except ValueError:
                pass

    resultado = ResultadoValidacao(trader.strategy, instrumento, exchange)
    posicao: tuple[int, Decimal] | None = None
    i = trader.needs
    while i < len(closes):
        janela = closes[:i]           # SÓ o passado: nada da vela atual
        preco = closes[i]
        if posicao is None:
            snap = MarketSnapshot(instrumento, "1h", utcnow(), janela)
            proposta = trader.propose(snap)
            resultado.velas_avaliadas += 1
            if proposta.action is Action.BUY:
                posicao = (i, preco)
            else:
                resultado.abstencoes += 1
        else:
            entrada_idx, entrada = posicao
            variacao = (preco - entrada) / entrada
            alvo = trader.movimento_alvo
            stop = -alvo / 2
            venceu = i - entrada_idx >= velas_maximas_na_posicao
            if variacao >= alvo or variacao <= stop or venceu:
                custo = custo_de_operar(instrumento, exchange,
                                        capital * Decimal("0.15"))
                resultado.operacoes.append(Operacao(
                    entrada_idx, i, entrada, preco, variacao,
                    variacao - custo.ida_e_volta,
                    "alvo" if variacao >= alvo else
                    ("stop" if variacao <= stop else "tempo")))
                posicao = None
        i += 1
    return resultado


def comparar_exchanges(trader_factory, closes, instrumento: str,
                       exchanges=("binance", "kraken", "coinbase")) -> list[dict]:
    """A mesma estratégia, o mesmo período, corretoras diferentes. Mostra em
    números que a escolha da corretora decide o resultado."""
    saida = []
    for ex in exchanges:
        r = validar(trader_factory(ex), closes, instrumento, exchange=ex)
        v = r.veredito()
        saida.append({"exchange": ex, "operacoes": r.total,
                      "liquido": f"{r.retorno_liquido_total:.2%}",
                      "custo": f"{r.custo_total:.2%}",
                      "aprovada": v.get("aprovada", False)})
    return saida


__all__ = ["validar", "comparar_exchanges", "ResultadoValidacao", "Operacao"]
