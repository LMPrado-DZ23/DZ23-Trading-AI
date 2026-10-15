"""Motor de Saída GLOBAL (M3/M4): uma política versionada POR POSIÇÃO decido
se deve manter, reduzir, proteger ou sair — em qualquer mercado, com números
determinísticos, auditáveis e reproduzíveis.

Por que este módulo existe:
- A política de saída vivia só no trader no MOMENTO da avaliação; nada a
  registrava junto da posição. Reiniciar o processo ou trocar uma versão de
  estratégia mudava, silenciosamente, o stop de uma posição aberta — e a
  posição reiniciava sem gestão nenhuma (achado do item 22: o livro da mesa
  não sobrevivia ao reinício).
- Stop mais largo depois da entrada é a forma clássica de transformar uma
  perda planejada em prejuízo sem plano. Aqui o afrouxamento é RECUSADO por
  construção; aperto é aceito e auditado.
- Lucro bruto positivo não é sinal de venda. A decisão olha a política
  (alvo, stop, trailing, tempo) e o resultado LÍQUIDO — nunca o otimismo do
  painel.

A matemática da política continua sendo a MESMA do backtest (ExitPolicy em
policy.py); este módulo só a versiona, a prende à posição e embrulha a
decisão com justificativa, urgência e validade.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .models import utcnow
from .policy import ExitPolicy, policy_for

# Ações da decisão (equivalentes às decisões de saída da missão):
HOLD = "HOLD"
REDUCE = "REDUCE"
CLOSE = "CLOSE"
NO_ACTION = "NO_ACTION"


@dataclass(frozen=True)
class PoliticaDeSaida:
    """Política versionada de UMA posição. Imutável: mudar = nova versão."""
    politica_id: str          # identidade (estratégia)
    versao: str               # versão da política (nova validação = nova)
    horizonte: str            # "1h", "swing", ...
    alvo: Decimal             # fração (ex.: 0.02)
    stop: Decimal             # fração NEGATIVA (ex.: -0.01)
    max_velas: int = 48
    trailing_pct: Decimal | None = None     # recuo do topo que protege lucro
    parcial_no_alvo: Decimal | None = None  # fração a realizar no alvo
    exigir_payoff_minimo: bool = True

    def __post_init__(self):
        # A validação é a MESMA do backtest — uma verdade só.
        base = ExitPolicy(alvo=Decimal(str(self.alvo)),
                          stop=Decimal(str(self.stop)),
                          max_velas_na_posicao=int(self.max_velas),
                          exigir_payoff_minimo=self.exigir_payoff_minimo)
        if self.parcial_no_alvo is not None:
            p = Decimal(str(self.parcial_no_alvo))
            if not (Decimal("0") < p <= Decimal("1")):
                raise ValueError("parcial_no_alvo precisa estar em (0, 1]")
        if self.trailing_pct is not None:
            t = Decimal(str(self.trailing_pct))
            if not (Decimal("0") < t < Decimal("1")):
                raise ValueError("trailing_pct precisa estar em (0, 1)")
        object.__setattr__(self, "_base", base)

    @property
    def chave(self) -> str:
        return f"{self.politica_id}@{self.versao}"

    def acerto_minimo(self, custo_ida_e_volta: Decimal = Decimal("0")) -> Decimal:
        return self._base.acerto_minimo(custo_ida_e_volta)

    def expectativa(self, taxa_acerto: Decimal,
                    custo_ida_e_volta: Decimal = Decimal("0")) -> Decimal:
        return self._base.expectativa(taxa_acerto, custo_ida_e_volta)

    def razao_risco_retorno(self) -> Decimal:
        return self._base.razao_risco_retorno()

    def doc(self) -> dict:
        return {"politica_id": self.politica_id, "versao": self.versao,
                "horizonte": self.horizonte,
                "alvo": str(self.alvo), "stop": str(self.stop),
                "max_velas": self.max_velas,
                "trailing_pct": (str(self.trailing_pct)
                                 if self.trailing_pct is not None else None),
                "parcial_no_alvo": (str(self.parcial_no_alvo)
                                    if self.parcial_no_alvo is not None else None),
                "exigir_payoff_minimo": self.exigir_payoff_minimo}

    @classmethod
    def from_doc(cls, d: dict) -> PoliticaDeSaida | None:
        if not d:
            return None
        try:
            return cls(politica_id=str(d.get("politica_id") or "?"),
                       versao=str(d.get("versao") or "1"),
                       horizonte=str(d.get("horizonte") or "1h"),
                       alvo=Decimal(str(d["alvo"])),
                       stop=Decimal(str(d["stop"])),
                       max_velas=int(d.get("max_velas") or 48),
                       trailing_pct=(Decimal(str(d["trailing_pct"]))
                                     if d.get("trailing_pct") is not None else None),
                       parcial_no_alvo=(Decimal(str(d["parcial_no_alvo"]))
                                        if d.get("parcial_no_alvo") is not None else None),
                       exigir_payoff_minimo=bool(d.get("exigir_payoff_minimo", True)))
        except (KeyError, ValueError, ArithmeticError):
            return None


@dataclass(frozen=True)
class DecisaoDeSaida:
    """O que fazer com a posição AGORA — com números, motivo e validade."""
    acao: str                  # HOLD | REDUCE | CLOSE | NO_ACTION
    reason_code: str           # STOP_CONDITION | TARGET | PARTIAL_TARGET |
                               # TRAILING | TIME_EXIT | HOLD | DATA_UNAVAILABLE |
                               # EXIT_IN_PROGRESS | SEM_POLITICA
    motivo: str                # justificativa em PT-BR
    urgencia: str              # alta | media | baixa
    fracao: Decimal = Decimal("0")        # fração da posição a realizar
    lucro_bruto: Decimal = Decimal("0")
    lucro_liquido: Decimal = Decimal("0")
    preco_referencia: Decimal | None = None
    fonte: str = ""
    frescor_s: int | None = None
    politica: str = ""         # chave "id@versao"
    valido_ate: str = ""       # ISO — depois disso a decisão precisa ser refeita


def politica_por(trader, *, trailing_pct: Decimal | None = None,
                 parcial_no_alvo: Decimal | None = None) -> PoliticaDeSaida:
    """Política versionada DO trader — derivada da MESMA ExitPolicy do
    backtest (uma definição, dois consumidores)."""
    base = getattr(trader, "exit_policy", None)
    if base is None:
        base = policy_for(getattr(trader, "movimento_alvo", Decimal("0.02")))
    return PoliticaDeSaida(
        politica_id=str(getattr(trader, "strategy", "?")),
        versao=str(getattr(trader, "versao", "1")),
        horizonte=str(getattr(trader, "timeframe", "1h")),
        alvo=base.alvo, stop=base.stop,
        max_velas=int(base.max_velas_na_posicao),
        trailing_pct=trailing_pct, parcial_no_alvo=parcial_no_alvo,
        exigir_payoff_minimo=base.exigir_payoff_minimo)


def afrouxamento(atual: PoliticaDeSaida, proposta: PoliticaDeSaida) -> str | None:
    """Devolve o motivo se a proposta AFROUXA a proteção — ou None.

    Afrouxar = permitir perda maior (stop mais largo), exigir mais do mercado
    (alvo mais distante), dar mais tempo (prazo maior), remover o trailing ou
    alargá-lo (deixa o preço recuar mais antes de proteger o lucro).
    """
    if proposta.stop < atual.stop:
        return (f"stop mais largo ({atual.stop:.2%} → {proposta.stop:.2%}) — "
                "perda planejada maior")
    if proposta.alvo > atual.alvo:
        return (f"alvo mais distante ({atual.alvo:.2%} → {proposta.alvo:.2%}) — "
                "posição fica mais tempo exposta")
    if proposta.max_velas > atual.max_velas:
        return (f"prazo maior ({atual.max_velas} → {proposta.max_velas} velas)")
    if atual.trailing_pct is not None and proposta.trailing_pct is None:
        return "trailing removido — lucro deixa de ser protegido no recuo"
    if (atual.trailing_pct is not None and proposta.trailing_pct is not None
            and proposta.trailing_pct > atual.trailing_pct):
        return (f"trailing mais largo ({atual.trailing_pct:.2%} → "
                f"{proposta.trailing_pct:.2%}) — recuo maior antes de proteger "
                "o lucro")
    return None


def aplicar_ajuste(pos, proposta: PoliticaDeSaida,
                   agora: datetime | None = None) -> str:
    """Aplica NOVA política à posição — nunca afrouxando a proteção.

    Toda mudança fica auditada no histórico da posição (de → para). Uma
    proposta que afrouxa é recusada com ValueError: stop de posição aberta
    não anda para trás por conveniência.
    """
    atual = getattr(pos, "politica", None)
    if atual is not None:
        motivo = afrouxamento(atual, proposta)
        if motivo:
            raise ValueError(f"ajuste recusado: afrouxa a proteção — {motivo}")
    pos.politica = proposta
    de = atual.chave if atual is not None else ""
    pos.historico_politica.append(
        {"de": de, "para": proposta.chave,
         "em": (agora or utcnow()).astimezone(timezone.utc).isoformat()})
    return proposta.chave


def avaliar_posicao(pos, preco, *, politica: PoliticaDeSaida | None = None,
                    velas: int = 0, agora: datetime | None = None,
                    custo_ida_e_volta: Decimal = Decimal("0"),
                    fonte: str = "", saida_em_curso: bool = False
                    ) -> DecisaoDeSaida:
    """A decisão da posição AGORA. Determinística: mesmo dado, mesma resposta.

    Ordem declarada (primeira que dispara manda): stop → alvo/parcial →
    trailing → tempo → manter. Nenhuma regra vende SÓ por lucro bruto
    positivo — lucro só realiza com a política, com o trailing ou com a
    tese; e o número que importa é o LÍQUIDO.
    """
    agora = agora or utcnow()
    valido_ate = (agora + timedelta(hours=1)).astimezone(timezone.utc).isoformat()
    polit = politica or getattr(pos, "politica", None)
    if polit is None:
        return DecisaoDeSaida(NO_ACTION, "SEM_POLITICA",
                              "posição sem política registrada — registre ou "
                              "adote antes de decidir", "baixa",
                              valido_ate=valido_ate)
    if saida_em_curso:
        return DecisaoDeSaida(HOLD, "EXIT_IN_PROGRESS",
                              "saída já em curso — não duplica a ordem",
                              "baixa", politica=polit.chave,
                              valido_ate=valido_ate)
    try:
        preco = Decimal(str(preco)) if preco is not None else None
    except (ArithmeticError, TypeError):
        preco = None
    if preco is None or preco <= 0:
        return DecisaoDeSaida(NO_ACTION, "DATA_UNAVAILABLE",
                              "sem preço confiável — não decide às cegas",
                              "baixa", fonte=fonte, politica=polit.chave,
                              valido_ate=valido_ate)
    entrada = pos.entry_price
    if not entrada or entrada <= 0:
        return DecisaoDeSaida(NO_ACTION, "DATA_UNAVAILABLE",
                              "posição sem preço de entrada confiável",
                              "baixa", politica=polit.chave,
                              valido_ate=valido_ate)
    retorno = (preco - entrada) / entrada
    liquido = retorno - Decimal(str(custo_ida_e_volta))
    comum = {"preco_referencia": preco, "fonte": fonte,
             "lucro_bruto": retorno, "lucro_liquido": liquido,
             "politica": polit.chave, "valido_ate": valido_ate}

    if retorno <= polit.stop:
        return DecisaoDeSaida(
            CLOSE, "STOP_CONDITION",
            f"stop: {retorno:.2%} desde a entrada (limite {polit.stop:.2%}); "
            f"líquido {liquido:+.2%}", "alta", fracao=Decimal("1"), **comum)

    if retorno >= polit.alvo:
        if (polit.parcial_no_alvo is not None
                and not getattr(pos, "parcial_tomada", False)
                and Decimal("0") < polit.parcial_no_alvo < Decimal("1")):
            return DecisaoDeSaida(
                REDUCE, "PARTIAL_TARGET",
                f"parcial no alvo: {retorno:.2%} ≥ alvo {polit.alvo:.2%} — "
                f"realiza {polit.parcial_no_alvo:.0%} e mantém o resto "
                "com a política", "media", fracao=polit.parcial_no_alvo, **comum)
        return DecisaoDeSaida(
            CLOSE, "TARGET",
            f"alvo atingido: {retorno:.2%} (alvo {polit.alvo:.2%}); "
            f"líquido {liquido:+.2%}", "alta", fracao=Decimal("1"), **comum)

    topo = getattr(pos, "high_water", None)
    if topo is None or preco > topo:
        topo = preco
    if (polit.trailing_pct is not None and topo > entrada
            and preco <= topo * (Decimal("1") - polit.trailing_pct)):
        recuo = (topo - preco) / topo
        return DecisaoDeSaida(
            CLOSE, "TRAILING",
            f"trailing: recuo de {recuo:.2%} do topo ({topo}) — protege o "
            f"lucro (líquido {liquido:+.2%})", "alta", fracao=Decimal("1"),
            **comum)

    if polit.max_velas and velas >= polit.max_velas:
        return DecisaoDeSaida(
            CLOSE, "TIME_EXIT",
            f"tempo: {velas} velas na posição ≥ limite {polit.max_velas} "
            "— a tese expirou", "media", fracao=Decimal("1"), **comum)

    return DecisaoDeSaida(
        HOLD, "HOLD",
        f"tese de pé ({retorno:+.2%} bruto / {liquido:+.2%} líquido) — "
        f"mantendo sob {polit.chave}", "baixa", **comum)


__all__ = ["HOLD", "REDUCE", "CLOSE", "NO_ACTION",
           "PoliticaDeSaida", "DecisaoDeSaida",
           "politica_por", "avaliar_posicao", "afrouxamento", "aplicar_ajuste"]
