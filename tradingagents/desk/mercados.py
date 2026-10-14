"""Fundação multimercado (E6): classes de ativo, sessões e custos — POLÍTICA.

Aqui vive o que cada classe de ativo exige para ser operada com
responsabilidade: moeda de cotação, lote, tick, liquidação, calendário de
sessão e taxas de referência. É a camada de POLÍTICA, não de execução:
registrar uma classe NÃO habilita negociar. `execucao_homologada` começa
FALSO em tudo que não tem adaptador homologado (ver `salas.py`), e só o
proprietário promove uma classe, amarrando-a a um adaptador real.

Fail-closed é a regra: classe desconhecida, fim de semana, feriado ou fora
da janela ⇒ fechado, com motivo explícito. Cripto continua 24/7; nada muda
para quem já opera nesse regime.

Horário de verão é calculado por REGRA (EUA: 2º domingo de março → 1º
domingo de novembro; Brasil: extinto desde 2019, UTC-3 fixo) — sem depender
de bancos de fuso do sistema, que variam de máquina para máquina.

Os números de taxas/lote são estimativas de política conservadoras; a
calibração definitiva de uma classe acontece na sua homologação (adaptador
+ custos reais), nunca por suposição.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .costs import SPREAD_POR_LIQUIDEZ, TAXAS_TAKER


class MercadoError(RuntimeError):
    """Classe de ativo inválida, desconhecida ou metadata incoerente."""


# Mercados aceitos por salas/adaptadores. Os três primeiros são cripto (já
# homologados); ações/ETFs/FIIs existem como POLÍTICA — nenhum adaptador os
# opera ainda, e nenhuma classe desses mercados está homologada.
MERCADOS = ("spot", "futuros", "opcoes_binarias", "acoes", "etfs", "fiis")

# Snapshot anual de feriados (política declarada, revisada a cada janeiro).
# Fonte: calendários oficiais de B3 e NYSE/Nasdaq para 2026.
FERIADOS_2026_B3 = (
    "2026-01-01",  # Confraternização Universal
    "2026-02-16",  # Carnaval (segunda)
    "2026-02-17",  # Carnaval (terça)
    "2026-04-03",  # Sexta-feira Santa
    "2026-04-21",  # Tiradentes
    "2026-05-01",  # Dia do Trabalho
    "2026-06-04",  # Corpus Christi
    "2026-09-07",  # Independência
    "2026-10-12",  # Nossa Senhora Aparecida
    "2026-11-02",  # Finados
    "2026-11-20",  # Consciência Negra
    "2026-12-25",  # Natal
)
FERIADOS_2026_US = (
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # Martin Luther King Jr. Day
    "2026-02-16",  # Presidents' Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observado; 04/07 cai no sábado)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
)


def _enesimo_domingo(ano: int, mes: int, n: int) -> date:
    primeiro = date(ano, mes, 1)
    desloc = (6 - primeiro.weekday()) % 7      # segunda=0 … domingo=6
    return primeiro + timedelta(days=desloc + 7 * (n - 1))


def _em_horario_de_verao_eua(agora_utc: datetime) -> bool:
    """Regra dos EUA: do 2º domingo de março (02:00 EST = 07:00 UTC) ao 1º
    domingo de novembro (02:00 EDT = 06:00 UTC)."""
    inicio = datetime.combine(_enesimo_domingo(agora_utc.year, 3, 2),
                              time(7, 0), timezone.utc)
    fim = datetime.combine(_enesimo_domingo(agora_utc.year, 11, 1),
                           time(6, 0), timezone.utc)
    return inicio <= agora_utc < fim


@dataclass(frozen=True)
class SessaoDeMercado:
    """Janela de negociação de um mercado, em hora local do fuso.

    `abertura`/`fechamento` nulos = 24/7 (cripto). Feriados são um SNAPSHOT
    de política, não um serviço de calendário.
    """
    fuso: str
    abertura: tuple[int, int] | None = None
    fechamento: tuple[int, int] | None = None
    feriados: tuple[str, ...] = ()

    def deslocamento_utc(self, agora_utc: datetime) -> timedelta:
        if self.fuso == "America/Sao_Paulo":
            return timedelta(hours=-3)      # horário de verão extinto (2019)
        if self.fuso == "America/New_York":
            return timedelta(hours=-4 if _em_horario_de_verao_eua(agora_utc)
                             else -5)
        return timedelta(0)

    def aberta_em(self, agora: datetime) -> tuple[bool, str]:
        if agora.tzinfo is None:
            agora = agora.replace(tzinfo=timezone.utc)
        agora = agora.astimezone(timezone.utc)
        if self.abertura is None or self.fechamento is None:
            return True, "24/7 sem janela de pregão"
        local = agora + self.deslocamento_utc(agora)
        if local.weekday() >= 5:
            return False, f"fora do pregão: fim de semana ({self.fuso})"
        dia = local.date().isoformat()
        if dia in self.feriados:
            return False, (f"fora do pregão: feriado {dia} ({self.fuso}, "
                           "snapshot de política)")
        hm = (local.hour, local.minute)
        if self.abertura <= hm < self.fechamento:
            return True, f"pregão aberto ({self.fuso}, {local:%H:%M})"
        return False, (f"fora do pregão: {local:%H:%M} em {self.fuso} "
                       f"(janela {self.abertura[0]:02d}:{self.abertura[1]:02d}"
                       f"–{self.fechamento[0]:02d}:{self.fechamento[1]:02d})")


SESSAO_CRIPTO = SessaoDeMercado(fuso="UTC")
SESSAO_B3 = SessaoDeMercado(fuso="America/Sao_Paulo", abertura=(10, 0),
                            fechamento=(17, 0), feriados=FERIADOS_2026_B3)
SESSAO_EUA = SessaoDeMercado(fuso="America/New_York", abertura=(9, 30),
                             fechamento=(16, 0), feriados=FERIADOS_2026_US)


@dataclass(frozen=True)
class ClasseDeAtivo:
    """Metadados de política de UMA classe de ativo. Registrar não habilita:
    `execucao_homologada` só vira True por decisão explícita do dono."""
    nome: str
    mercado: str
    moeda: str
    lote_minimo: Decimal
    passo_lote: Decimal
    tick: Decimal
    liquidacao: str
    taxas_por_lado: Decimal
    spread_tipico: Decimal
    sessao: SessaoDeMercado
    execucao_homologada: bool = False
    observacao: str = ""

    @property
    def custo_ida_e_volta(self) -> Decimal:
        """Estimativa de política (2× taxas + spread). NÃO substitui a mesa
        de custos real (`costs.py`) na contabilização de uma sala."""
        return self.taxas_por_lado * 2 + self.spread_tipico

    def lote_valido(self, quantidade) -> bool:
        try:
            qtd = Decimal(str(quantidade))
        except (InvalidOperation, ValueError, TypeError):
            return False
        if qtd <= 0 or qtd < self.lote_minimo:
            return False
        return qtd % self.passo_lote == 0

    def como_dict(self) -> dict:
        return {
            "nome": self.nome, "mercado": self.mercado, "moeda": self.moeda,
            "lote_minimo": str(self.lote_minimo),
            "passo_lote": str(self.passo_lote), "tick": str(self.tick),
            "liquidacao": self.liquidacao, "sessao": self.sessao.fuso,
            "taxas_por_lado": str(self.taxas_por_lado),
            "spread_tipico": str(self.spread_tipico),
            "execucao_homologada": self.execucao_homologada,
            "observacao": self.observacao,
        }


CLASSES: dict[str, ClasseDeAtivo] = {}


def registrar_classe(classe: ClasseDeAtivo, *, substituir: bool = False) -> None:
    """Registra uma classe na camada de política. Sobrescrever exige
    intenção explícita."""
    nome = (classe.nome or "").strip().lower()
    if not nome:
        raise MercadoError("classe sem nome")
    if classe.mercado not in MERCADOS:
        raise MercadoError(
            f"mercado '{classe.mercado}' da classe '{nome}' desconhecido "
            f"(aceitos: {', '.join(MERCADOS)})")
    if classe.lote_minimo <= 0 or classe.passo_lote <= 0:
        raise MercadoError(f"classe '{nome}': lote mínimo e passo precisam "
                           "ser positivos")
    if classe.tick <= 0:
        raise MercadoError(f"classe '{nome}': tick precisa ser positivo")
    if classe.taxas_por_lado < 0 or classe.spread_tipico < 0:
        raise MercadoError(f"classe '{nome}': taxas e spread não podem ser "
                           "negativos")
    tem_janela = (classe.sessao.abertura is not None
                  or classe.sessao.fechamento is not None)
    if (classe.sessao.abertura is None) != (classe.sessao.fechamento is None):
        raise MercadoError(f"classe '{nome}': janela de sessão incompleta "
                           "(abertura e fechamento andam juntos)")
    if tem_janela and classe.sessao.abertura >= classe.sessao.fechamento:
        raise MercadoError(f"classe '{nome}': janela que cruza a meia-noite "
                           "não é suportada pela política")
    if nome in CLASSES and not substituir:
        raise MercadoError(
            f"classe '{nome}' já registrada — registrar outra versão exige "
            "substituir=True explícito")
    CLASSES[nome] = replace(classe, nome=nome)


def classe_de(nome) -> ClasseDeAtivo:
    cl = CLASSES.get(str(nome or "").strip().lower())
    if cl is None:
        raise MercadoError(
            f"classe de ativo '{nome}' desconhecida (registradas: "
            f"{', '.join(sorted(CLASSES)) or 'nenhuma'})")
    return cl


def mercado_aberto(classe_expr, agora_utc: datetime) -> tuple[bool, str]:
    """Fail-closed: classe desconhecida ⇒ FECHADO, com motivo."""
    if isinstance(classe_expr, ClasseDeAtivo):
        cl = classe_expr
    else:
        try:
            cl = classe_de(classe_expr)
        except MercadoError as exc:
            return False, str(exc)
    return cl.sessao.aberta_em(agora_utc)


def lote_valido(classe: str, quantidade) -> bool:
    """Valida quantidade contra o lote da classe. Classe desconhecida é
    erro — política indefinida não autoriza nada."""
    return classe_de(classe).lote_valido(quantidade)


def custo_da_classe(classe: str) -> Decimal:
    """Custo de referência de ida e volta da classe (política)."""
    return classe_de(classe).custo_ida_e_volta


# ------------------------------------------------------- classes do escopo
def _cripto_spot() -> ClasseDeAtivo:
    return ClasseDeAtivo(
        nome="cripto_spot", mercado="spot", moeda="USDT",
        lote_minimo=Decimal("0.00001"), passo_lote=Decimal("0.00001"),
        tick=Decimal("0.01"), liquidacao="D+0",
        taxas_por_lado=TAXAS_TAKER["binance"],
        spread_tipico=SPREAD_POR_LIQUIDEZ["alta"], sessao=SESSAO_CRIPTO,
        execucao_homologada=True,
        observacao=("pares de alta liquidez; taxas taker básicas (costs.py); "
                    "slippage fica na mesa de custos da sala"))


def _cripto_futuros() -> ClasseDeAtivo:
    return ClasseDeAtivo(
        nome="cripto_futuros", mercado="futuros", moeda="USDT",
        lote_minimo=Decimal("0.00001"), passo_lote=Decimal("0.00001"),
        tick=Decimal("0.01"), liquidacao="D+0",
        taxas_por_lado=Decimal("0.0005"),
        spread_tipico=SPREAD_POR_LIQUIDEZ["alta"], sessao=SESSAO_CRIPTO,
        execucao_homologada=True,
        observacao=("perpétuos; funding fora desta política; alavancagem "
                    "segue o gestor de risco, não a classe"))


def _b3(nome: str, mercado: str, spread: str, obs: str) -> ClasseDeAtivo:
    return ClasseDeAtivo(
        nome=nome, mercado=mercado, moeda="BRL",
        lote_minimo=Decimal("100"), passo_lote=Decimal("100"),
        tick=Decimal("0.01"), liquidacao="D+2",
        taxas_por_lado=Decimal("0.000325"), spread_tipico=Decimal(spread),
        sessao=SESSAO_B3, execucao_homologada=False,
        observacao=(f"{obs}; emolumentos+liquidação estimados por lado — "
                    "calibrar na homologação; sem adaptador B3 homologado"))


def _us(nome: str, mercado: str, spread: str, obs: str) -> ClasseDeAtivo:
    return ClasseDeAtivo(
        nome=nome, mercado=mercado, moeda="USD",
        lote_minimo=Decimal("1"), passo_lote=Decimal("1"),
        tick=Decimal("0.01"), liquidacao="T+1",
        taxas_por_lado=Decimal("0"), spread_tipico=Decimal(spread),
        sessao=SESSAO_EUA, execucao_homologada=False,
        observacao=(f"{obs}; corretagem zero típica (retail) e taxas de "
                    "venue/SEC fora da estimativa — calibrar na homologação"))


for _c in (
    _cripto_spot(), _cripto_futuros(),
    _b3("b3_acoes", "acoes", "0.0005",
        "ações à vista; mercado fracionário fora da política"),
    _b3("b3_fiis", "fiis", "0.0010",
        "FIIs; mesma sessão da B3"),
    _b3("b3_etfs", "etfs", "0.0005",
        "ETFs listados na B3"),
    _us("us_acoes", "acoes", "0.0002",
        "ações EUA; sessão NYSE/Nasdaq com horário de verão por regra"),
    _us("us_etfs", "etfs", "0.0002",
        "ETFs EUA; liquidação T+1 desde maio/2024"),
):
    registrar_classe(_c)
del _c


__all__ = [
    "CLASSES", "ClasseDeAtivo", "MercadoError", "MERCADOS", "SessaoDeMercado",
    "classe_de", "custo_da_classe", "lote_valido", "mercado_aberto",
    "registrar_classe",
]
