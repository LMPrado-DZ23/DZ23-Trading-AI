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

import contextlib
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
        aprovada = (
            self.retorno_liquido_total > 0
            and self.total >= 20
            and not ressalvas
        )
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
                f"{self.total} operações — "
                + ("; ".join(ressalvas) if ressalvas
                   else "resultado líquido não positivo")
            ),
            "aviso": "teste sobre o passado; não prevê o futuro",
        }


@dataclass(frozen=True)
class JanelaValidacao:
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    resultado: ResultadoValidacao


def validar(trader, closes, instrumento: str, *, exchange: str = "binance",
            capital: Decimal = Decimal("1000"),
            velas_maximas_na_posicao: int = 48,
            inicio_avaliacao: int = 0,
            fim_avaliacao: int | None = None) -> ResultadoValidacao:
    """Passa a estratégia vela a vela, sem nunca olhar adiante.

    A saída usa o alvo e o stop da própria estratégia; se nenhum dispara em
    `velas_maximas_na_posicao`, encerra a mercado (posição eterna esconde
    prejuízo).
    """
    for estagio in (AgentStage.VALIDATED, AgentStage.SHADOW, AgentStage.PAPER_ENABLED):
        if trader.spec.stage is not estagio:
            with contextlib.suppress(ValueError):
                trader.spec.move_to(estagio)

    closes = tuple(closes)
    if velas_maximas_na_posicao <= 0:
        raise ValueError("velas_maximas_na_posicao precisa ser positiva")
    fim = len(closes) if fim_avaliacao is None else int(fim_avaliacao)
    inicio = int(inicio_avaliacao)
    if not 0 <= inicio <= fim <= len(closes):
        raise ValueError("faixa de avaliação fora da série")
    resultado = ResultadoValidacao(trader.strategy, instrumento, exchange)
    posicao: tuple[int, Decimal] | None = None
    i = max(trader.needs, inicio)
    while i < fim:
        janela = closes[:i]           # SÓ o passado: nada da vela atual
        preco = closes[i]
        if posicao is None:
            snap = MarketSnapshot(instrumento, "1h", utcnow(), janela)
            proposta = trader.propose(snap)
            resultado.velas_avaliadas += 1
            if proposta.action is Action.BUY and i + 1 < fim:
                posicao = (i, preco)
            else:
                resultado.abstencoes += 1
        else:
            entrada_idx, entrada = posicao
            variacao = (preco - entrada) / entrada
            # A MESMA política que a produção usa — não uma aproximação.
            politica = trader.exit_policy
            sair, motivo_saida = politica.avaliar(variacao, i - entrada_idx)
            if not sair and i - entrada_idx >= velas_maximas_na_posicao:
                sair, motivo_saida = True, "tempo_validacao"
            if sair:
                custo = custo_de_operar(instrumento, exchange,
                                        capital * Decimal("0.15"))
                resultado.operacoes.append(Operacao(
                    entrada_idx, i, entrada, preco, variacao,
                    variacao - custo.ida_e_volta, motivo_saida))
                posicao = None
        i += 1
    if posicao is not None:
        entrada_idx, entrada = posicao
        saida_idx = fim - 1
        preco = closes[saida_idx]
        variacao = (preco - entrada) / entrada
        custo = custo_de_operar(
            instrumento, exchange, capital * Decimal("0.15"))
        resultado.operacoes.append(Operacao(
            entrada_idx, saida_idx, entrada, preco, variacao,
            variacao - custo.ida_e_volta, "fim_da_janela_oos"))
    return resultado


def validar_em_walk_forward(trader_factory, closes, instrumento: str, *,
                            train_size: int, test_size: int,
                            embargo: int = 0, exchange: str = "binance",
                            capital: Decimal = Decimal("1000"),
                            velas_maximas_na_posicao: int = 48,
                            ) -> tuple[JanelaValidacao, ...]:
    """Avalia a estratégia em blocos OOS, preservando warmup e embargo.

    Cada trader nasce novamente por janela. Ele enxerga somente o segmento de
    treino, o embargo e o passado já ocorrido dentro do teste; entradas antes
    do início OOS são impossíveis. Os blocos de teste não se sobrepõem.
    """
    from tradingagents.research.walk_forward import windows

    serie = tuple(closes)
    saida = []
    for janela in windows(len(serie), train_size, test_size, embargo=embargo):
        segmento = serie[janela.train_start:janela.test_end]
        inicio_local = janela.test_start - janela.train_start
        resultado = validar(
            trader_factory(), segmento, instrumento, exchange=exchange,
            capital=capital,
            velas_maximas_na_posicao=velas_maximas_na_posicao,
            inicio_avaliacao=inicio_local,
            fim_avaliacao=len(segmento))
        deslocamento = janela.train_start
        resultado.operacoes = [
            Operacao(
                op.entrada_idx + deslocamento,
                op.saida_idx + deslocamento,
                op.preco_entrada, op.preco_saida,
                op.retorno_bruto, op.retorno_liquido, op.motivo_saida)
            for op in resultado.operacoes]
        saida.append(JanelaValidacao(
            janela.train_start, janela.train_end,
            janela.test_start, janela.test_end, resultado))
    return tuple(saida)


def consolidar_walk_forward(
        janelas: tuple[JanelaValidacao, ...] | list[JanelaValidacao]
        ) -> ResultadoValidacao:
    """Consolida somente os blocos OOS não sobrepostos."""
    if not janelas:
        raise ValueError("nenhuma janela walk-forward completa")
    primeiro = janelas[0].resultado
    total = ResultadoValidacao(
        primeiro.estrategia, primeiro.instrumento, primeiro.exchange)
    for janela in janelas:
        total.operacoes.extend(janela.resultado.operacoes)
        total.velas_avaliadas += janela.resultado.velas_avaliadas
        total.abstencoes += janela.resultado.abstencoes
    return total


def veredito_walk_forward(
        janelas: tuple[JanelaValidacao, ...] | list[JanelaValidacao]
        ) -> dict:
    """Veredito agregado que também exige estabilidade entre janelas OOS."""
    consolidado = consolidar_walk_forward(janelas)
    veredito = consolidado.veredito()
    positivas = sum(
        janela.resultado.retorno_liquido_total > 0 for janela in janelas)
    minimo_positivas = (len(janelas) * 3 + 4) // 5  # teto de 60%
    ressalvas = list(veredito.get("ressalvas", []))
    if len(janelas) < 3:
        ressalvas.append(f"somente {len(janelas)} janelas OOS completas")
    if positivas < minimo_positivas:
        ressalvas.append(
            f"instável entre janelas: {positivas}/{len(janelas)} positivas")
    aprovada = bool(veredito.get("aprovada")) and not ressalvas
    veredito.update({
        "conclusivo": bool(veredito.get("conclusivo")) and not ressalvas,
        "aprovada": aprovada,
        "janelas_oos": len(janelas),
        "janelas_positivas": positivas,
        "ressalvas": ressalvas,
    })
    if not aprovada:
        detalhe = "; ".join(ressalvas) or "resultado líquido não positivo"
        veredito["resumo"] = (
            f"REPROVADA: {consolidado.retorno_liquido_total:+.2%} líquidos em "
            f"{consolidado.total} operações - {detalhe}")
    return veredito


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


__all__ = [
    "validar", "validar_em_walk_forward", "consolidar_walk_forward",
    "veredito_walk_forward",
    "comparar_exchanges", "ResultadoValidacao", "JanelaValidacao", "Operacao",
]
