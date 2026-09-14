"""O que a corretora PODE provar (matriz de capacidades).

Motivo: capacidade ausente aparecia como falha genérica no meio das
divergências — indistinguível de uma divergência real. Mas "a corretora não
tem como consultar execuções" e "as execuções não batem" são coisas
diferentes: a primeira é um limite ESTRUTURAL, permanente enquanto o cliente
usar aquela corretora; a segunda é um problema a resolver.

Misturar as duas produz dois erros opostos: o dono tenta "resolver" um limite
que não tem solução, ou aprende a ignorar divergências porque "sempre
aparece isso".

A detecção é por COMPORTAMENTO observado (o método existe?), não por lista
fixa de nomes de corretora — uma lista assim envelhece em silêncio quando a
exchange muda sua API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Capacidade(str, Enum):
    CONSULTAR_ORDEM_POR_ID = "consultar_ordem_por_id"
    LISTAR_ORDENS = "listar_ordens"
    CONSULTAR_EXECUCOES = "consultar_execucoes"
    CONSULTAR_POSICOES = "consultar_posicoes"


# Método do CCXT que evidencia cada capacidade.
_METODOS = {
    Capacidade.CONSULTAR_ORDEM_POR_ID: "fetch_order",
    Capacidade.LISTAR_ORDENS: "fetch_orders",
    Capacidade.CONSULTAR_EXECUCOES: "fetch_my_trades",
    Capacidade.CONSULTAR_POSICOES: "fetch_balance",
}

_EXPLICACAO = {
    Capacidade.CONSULTAR_ORDEM_POR_ID: (
        "consultar uma ordem pelo identificador — é a ÚNICA forma de provar "
        "que uma ordem não existe"),
    Capacidade.LISTAR_ORDENS: "listar ordens recentes",
    Capacidade.CONSULTAR_EXECUCOES: (
        "consultar as execuções (trades) de uma ordem — sem isso os efeitos "
        "econômicos não podem ser confirmados individualmente"),
    Capacidade.CONSULTAR_POSICOES: "consultar saldo/posição da conta",
}


@dataclass(frozen=True)
class MatrizDeCapacidades:
    exchange: str
    disponiveis: frozenset = field(default_factory=frozenset)

    def suporta(self, capacidade: Capacidade) -> bool:
        return capacidade in self.disponiveis

    def ausentes(self) -> list[Capacidade]:
        return [c for c in Capacidade if c not in self.disponiveis]

    def pode_provar_ausencia(self) -> bool:
        """Sem consulta direta por id, não se prova que uma ordem não existe.

        Varrer histórico pode ENCONTRAR, nunca provar ausência — e tratar
        "não achei" como "não existe" devolveria capital de ordem viva.
        """
        return self.suporta(Capacidade.CONSULTAR_ORDEM_POR_ID)

    def nivel_de_confianca(self) -> str:
        if not self.ausentes():
            return "completo"
        if self.disponiveis:
            return "parcial"
        return "insuficiente"

    def explicacao(self) -> str:
        faltando = self.ausentes()
        if not faltando:
            return (f"{self.exchange}: todas as verificações de reconciliação "
                    "são possíveis nesta corretora.")
        itens = "; ".join(_EXPLICACAO[c] for c in faltando)
        return (f"{self.exchange}: esta corretora NÃO permite {itens}. "
                "Isso é um limite da corretora, não um defeito do sistema — "
                "a reconciliação declara o que não pôde verificar em vez de "
                "supor que está tudo certo.")

    def limitacoes(self) -> list[str]:
        """Limitações em formato de relatório, nomeadas e separadas das
        divergências."""
        return [f"CAPACIDADE_AUSENTE {c.value}: {_EXPLICACAO[c]}"
                for c in self.ausentes()]

    def detalhes(self) -> dict:
        return {"exchange": self.exchange,
                "nivel": self.nivel_de_confianca(),
                "disponiveis": sorted(c.value for c in self.disponiveis),
                "ausentes": sorted(c.value for c in self.ausentes()),
                "pode_provar_ausencia": self.pode_provar_ausencia(),
                "explicacao": self.explicacao()}


def detectar_capacidades(exchange) -> MatrizDeCapacidades:
    """Detecta por comportamento: o método existe e é chamável?"""
    nome = getattr(exchange, "id", None) or type(exchange).__name__
    disponiveis = set()
    for capacidade, metodo in _METODOS.items():
        try:
            alvo = getattr(exchange, metodo, None)
        except AttributeError:
            alvo = None
        if callable(alvo):
            disponiveis.add(capacidade)
    return MatrizDeCapacidades(exchange=nome, disponiveis=frozenset(disponiveis))


DESCONHECIDA = MatrizDeCapacidades(exchange="desconhecida",
                                   disponiveis=frozenset())

__all__ = ["Capacidade", "MatrizDeCapacidades", "detectar_capacidades",
           "DESCONHECIDA"]
