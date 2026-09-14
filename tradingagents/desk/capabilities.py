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


class Status(str, Enum):
    """Quatro estados distintos — `callable()` prova que o método EXISTE,
    não que ele funciona nesta corretora.

    Confundir PRESENTE com SUPORTADA fazia o sistema contar com uma
    verificação que estoura na hora de usar; confundir falha transitória com
    NAO_SUPORTADA condenaria a corretora por causa de uma queda de rede.
    """
    SUPORTADA = "suportada"          # exercitada com sucesso
    PRESENTE = "presente"            # o método existe; ainda não exercitado
    NAO_SUPORTADA = "nao_suportada"  # a corretora respondeu que não oferece
    DESCONHECIDA = "desconhecida"    # ausente, ou falha que não conclui nada


class Capacidade(str, Enum):
    CONSULTAR_ORDEM_POR_ID = "consultar_ordem_por_id"
    LISTAR_ORDENS = "listar_ordens"
    LISTAR_ORDENS_ABERTAS = "listar_ordens_abertas"
    CONSULTAR_EXECUCOES = "consultar_execucoes"
    CONSULTAR_POSICOES = "consultar_posicoes"


# Método do CCXT que evidencia cada capacidade.
_METODOS = {
    Capacidade.CONSULTAR_ORDEM_POR_ID: "fetch_order",
    Capacidade.LISTAR_ORDENS: "fetch_orders",
    Capacidade.LISTAR_ORDENS_ABERTAS: "fetch_open_orders",
    Capacidade.CONSULTAR_EXECUCOES: "fetch_my_trades",
    Capacidade.CONSULTAR_POSICOES: "fetch_balance",
}

_EXPLICACAO = {
    Capacidade.CONSULTAR_ORDEM_POR_ID: (
        "consultar uma ordem pelo identificador — é a ÚNICA forma de provar "
        "que uma ordem não existe"),
    Capacidade.LISTAR_ORDENS: "listar ordens recentes",
    Capacidade.LISTAR_ORDENS_ABERTAS: (
        "listar as ordens ABERTAS da conta — sem isso não há como descobrir "
        "ordem viva que o sistema não conhece"),
    Capacidade.CONSULTAR_EXECUCOES: (
        "consultar as execuções (trades) de uma ordem — sem isso os efeitos "
        "econômicos não podem ser confirmados individualmente"),
    Capacidade.CONSULTAR_POSICOES: "consultar saldo/posição da conta",
}


@dataclass(frozen=True)
class MatrizDeCapacidades:
    exchange: str
    estados: dict = field(default_factory=dict)

    def status(self, capacidade: Capacidade) -> Status:
        return self.estados.get(capacidade, Status.DESCONHECIDA)

    def suporta(self, capacidade: Capacidade) -> bool:
        """Utilizável: o método existe e não foi contestado pela corretora.

        Cuidado: isto NÃO é prova. Para afirmações que sustentam dinheiro —
        provar que uma ordem não existe, por exemplo — use `comprovada`.
        """
        return self.status(capacidade) in (Status.SUPORTADA, Status.PRESENTE)

    def comprovada(self, capacidade: Capacidade) -> bool:
        """Exercitada com sucesso. É o único estado que PROVA algo."""
        return self.status(capacidade) is Status.SUPORTADA

    @property
    def disponiveis(self) -> frozenset:
        return frozenset(c for c in Capacidade if self.suporta(c))

    def ausentes(self) -> list[Capacidade]:
        return [c for c in Capacidade if not self.suporta(c)]

    def pode_provar_ausencia(self) -> bool:
        """Só uma consulta EXERCITADA prova que uma ordem não existe.

        Varrer histórico pode ENCONTRAR, nunca provar ausência. E um método
        que apenas EXISTE não provou nada: pode estourar na primeira chamada
        real — justamente quando a resposta decide se capital volta ou não.
        """
        return self.comprovada(Capacidade.CONSULTAR_ORDEM_POR_ID)

    def pode_descobrir_ordens_orfas(self) -> bool:
        return self.suporta(Capacidade.LISTAR_ORDENS_ABERTAS)

    def nivel_de_confianca(self) -> str:
        """`completo` exige TODAS exercitadas com sucesso.

        Métodos que só existem não tornam a corretora verificada — chamar
        isso de completo seria prometer uma garantia que ninguém testou.
        """
        if all(self.comprovada(c) for c in Capacidade):
            return "completo"
        if not self.disponiveis:
            return "insuficiente"
        if any(self.comprovada(c) for c in Capacidade):
            return "parcial"
        return "nao_verificado"

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
                "estados": {c.value: self.status(c).value for c in Capacidade},
                "disponiveis": sorted(c.value for c in self.disponiveis),
                "ausentes": sorted(c.value for c in self.ausentes()),
                "pode_provar_ausencia": self.pode_provar_ausencia(),
                "explicacao": self.explicacao()}


def _e_nao_suportado(exc: BaseException) -> bool:
    """A corretora afirmou que NÃO oferece a operação?

    Só o que significa exatamente isso. Timeout, erro de rede ou de
    autenticação não concluem nada sobre a capacidade.
    """
    return type(exc).__name__ in {"NotSupported", "NotImplementedError"}


def detectar_capacidades(exchange, *, sondar: bool = False
                         ) -> MatrizDeCapacidades:
    """Detecta capacidades. Com `sondar=True`, EXERCITA cada método.

    Sem sondagem o máximo que se pode afirmar é PRESENTE: o método existe.
    A sondagem é somente de leitura e nunca envia ordem.
    """
    nome = getattr(exchange, "id", None) or type(exchange).__name__
    estados: dict = {}
    for capacidade, metodo in _METODOS.items():
        try:
            alvo = getattr(exchange, metodo, None)
        except AttributeError:
            alvo = None
        if not callable(alvo):
            estados[capacidade] = Status.DESCONHECIDA
            continue
        if not sondar:
            estados[capacidade] = Status.PRESENTE
            continue
        try:
            alvo()
        except TypeError:
            # Assinatura exige parâmetros: o método existe e responde, mas
            # não foi exercitado de verdade.
            estados[capacidade] = Status.PRESENTE
        except Exception as exc:
            estados[capacidade] = (Status.NAO_SUPORTADA
                                   if _e_nao_suportado(exc)
                                   else Status.DESCONHECIDA)
        else:
            estados[capacidade] = Status.SUPORTADA
    return MatrizDeCapacidades(exchange=nome, estados=estados)


DESCONHECIDA = MatrizDeCapacidades(exchange="desconhecida", estados={})

__all__ = ["Capacidade", "Status", "MatrizDeCapacidades",
           "detectar_capacidades", "DESCONHECIDA"]
