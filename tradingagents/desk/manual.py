"""Copiloto Manual (E3) — acompanhamento READ-ONLY de operações manuais.

O dono informa no app as operações que ele mesmo executou na corretora ou
por fora. Este módulo observa, calcula e ALERTA. Ele estruturalmente não
executa nada: não importa ExecutionCoordinator, não chama ordem, não tem
broker de escrita. A única forma de uma posição virar ENCERRADA é o dono
registrar um evento — preço atingir alvo NUNCA fecha nada.

Persistência: um documento por inquilino na chave Armazem "manual"
(schema dz23.manual/1), mesma filosofia do "livro": Decimals como string,
timestamps UTC ISO, concorrência otimista por `version`, idempotência por
`idempotency_key`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from .models import utcnow

SCHEMA = "dz23.manual/1"

DIRECOES = ("long", "short")
ORIGENS = ("declarado", "api_confirmado", "estimado")
TIPOS_EVENTO = ("entrada", "entrada_parcial", "aumento", "reducao", "encerramento")
_TIPOS_SOMA = ("entrada", "entrada_parcial", "aumento")
ESTADOS_MONITOR = ("MONITORING", "PAUSED", "FEED_STALE", "CONNECTION_LOST",
                   "RECONCILIATION_REQUIRED", "MONITORING_STOPPED")
ESTADOS_FINANCEIROS = ("ABERTA", "PARCIAL", "ENCERRADA")

INTERVALO_MIN_S = 15
INTERVALO_MAX_S = 3600
RECONCILIACAO_PADRAO_S = 900
SNAPSHOTS_MAX = 240


class ErroManual(ValueError):
    """Entrada inválida do dono — a mensagem explica o que corrigir."""


class PosicaoNaoEncontrada(ErroManual):
    pass


class ConflitoDeVersao(ErroManual):
    """Alguém alterou a posição entre a leitura e a escrita (concorrência)."""


def _d(valor: Any, campo: str) -> Decimal:
    if isinstance(valor, Decimal):
        return valor
    try:
        return Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ErroManual(f"{campo} não é um número válido: {valor!r}") from exc


def _dopt(valor: Any, campo: str) -> Decimal | None:
    if valor is None or valor == "":
        return None
    return _d(valor, campo)


def _ts(valor: Any, campo: str) -> datetime:
    if isinstance(valor, datetime):
        quando = valor
    else:
        try:
            quando = datetime.fromisoformat(str(valor))
        except (TypeError, ValueError) as exc:
            raise ErroManual(f"{campo} não é um instante válido: {valor!r}") from exc
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=timezone.utc)
    return quando.astimezone(timezone.utc)


def passo_s(timeframe: str) -> int:
    """Passo do candle em segundos; desconhecido cai em 1h (conservador)."""
    tf = (timeframe or "").strip().lower()
    try:
        if tf.endswith("m"):
            return max(60, int(tf[:-1]) * 60)
        if tf.endswith("h"):
            return max(300, int(tf[:-1]) * 3600)
        if tf.endswith("d"):
            return max(300, int(tf[:-1]) * 86400)
    except ValueError:
        pass
    return 3600


@dataclass(frozen=True)
class QuoteManual:
    """Cotação para o monitor: preço + instante da bolsa + instante de recebimento.

    A separação cutoff × recebido_em é o que permite distinguir "mercado
    parado" de "minha conexão parou" — nunca usar a hora local como
    substituta do instante da bolsa.
    """

    preco: Decimal
    cutoff: datetime | None = None
    recebido_em: datetime | None = None
    fonte: str = ""
    timeframe: str = "1h"
    quality: str = "ok"

    def atraso_s(self, agora: datetime) -> float | None:
        if self.cutoff is None:
            return None
        return (agora - self.cutoff).total_seconds()

    def fresco(self, agora: datetime) -> bool:
        atraso = self.atraso_s(agora)
        if atraso is None:
            return True
        return atraso <= 3 * passo_s(self.timeframe)


@dataclass(frozen=True)
class PoliticaManual:
    """Stop/alvos/invalidação declarados pelo dono. Monitor só compara."""

    stop: Decimal | None = None
    alvos: tuple[Decimal, ...] = ()
    invalidacao: Decimal | None = None
    expira_em: datetime | None = None
    trailing_pct: Decimal | None = None
    intervalo_s: int = 120

    def doc(self) -> dict:
        return {"stop": str(self.stop) if self.stop is not None else None,
                "alvos": [str(a) for a in self.alvos],
                "invalidacao": (str(self.invalidacao)
                                if self.invalidacao is not None else None),
                "expira_em": self.expira_em.isoformat() if self.expira_em else None,
                "trailing_pct": (str(self.trailing_pct)
                                 if self.trailing_pct is not None else None),
                "intervalo_s": self.intervalo_s}

    @staticmethod
    def from_doc(doc: dict | None) -> PoliticaManual:
        doc = doc or {}
        return PoliticaManual(
            stop=_dopt(doc.get("stop"), "stop"),
            alvos=tuple(_d(a, "alvo") for a in doc.get("alvos") or []),
            invalidacao=_dopt(doc.get("invalidacao"), "invalidacao"),
            expira_em=(_ts(doc["expira_em"], "expira_em")
                       if doc.get("expira_em") else None),
            trailing_pct=_dopt(doc.get("trailing_pct"), "trailing_pct"),
            intervalo_s=int(doc.get("intervalo_s") or 120))

    @staticmethod
    def validar(direcao: str, preco_entrada: Decimal, politica: PoliticaManual,
                ) -> None:
        if not (INTERVALO_MIN_S <= politica.intervalo_s <= INTERVALO_MAX_S):
            raise ErroManual(
                f"intervalo_s deve ficar entre {INTERVALO_MIN_S} e "
                f"{INTERVALO_MAX_S} segundos (teto seguro)")
        if politica.trailing_pct is not None and politica.trailing_pct <= 0:
            raise ErroManual("trailing_pct deve ser positivo")
        if direcao == "long":
            if politica.stop is not None and politica.stop >= preco_entrada:
                raise ErroManual(
                    "stop de posição comprada deve ficar ABAIXO do preço de entrada")
            for alvo in politica.alvos:
                if alvo <= preco_entrada:
                    raise ErroManual("alvo de posição comprada deve ficar ACIMA "
                                     "do preço de entrada")
            if (politica.invalidacao is not None
                    and politica.invalidacao > preco_entrada):
                raise ErroManual("invalidação de compra não pode ficar acima da entrada")
        else:
            if politica.stop is not None and politica.stop <= preco_entrada:
                raise ErroManual(
                    "stop de posição vendida deve ficar ACIMA do preço de entrada")
            for alvo in politica.alvos:
                if alvo >= preco_entrada:
                    raise ErroManual("alvo de posição vendida deve ficar ABAIXO "
                                     "do preço de entrada")
            if (politica.invalidacao is not None
                    and politica.invalidacao < preco_entrada):
                raise ErroManual("invalidação de venda não pode ficar abaixo da entrada")


@dataclass(frozen=True)
class EventoManual:
    """Fato registrado pelo dono. O preço é SEMPRE o executado/declarado —
    cotação de tela nunca substitui preço executado."""

    evento_id: str
    posicao_id: str
    tipo: str
    quantidade: Decimal
    preco: Decimal
    custos: Decimal
    fonte: str
    motivo: str
    em: datetime
    registrado_em: datetime
    idempotency_key: str = ""
    version_posicao: int = 0

    def doc(self) -> dict:
        return {"evento_id": self.evento_id, "posicao_id": self.posicao_id,
                "tipo": self.tipo, "quantidade": str(self.quantidade),
                "preco": str(self.preco), "custos": str(self.custos),
                "fonte": self.fonte, "motivo": self.motivo,
                "em": self.em.isoformat(), "registrado_em": self.registrado_em.isoformat(),
                "idempotency_key": self.idempotency_key,
                "version_posicao": self.version_posicao}

    @staticmethod
    def from_doc(doc: dict) -> EventoManual:
        return EventoManual(
            evento_id=str(doc.get("evento_id") or ""),
            posicao_id=str(doc.get("posicao_id") or ""),
            tipo=str(doc.get("tipo") or ""),
            quantidade=_d(doc.get("quantidade") or 0, "quantidade"),
            preco=_d(doc.get("preco") or 0, "preco"),
            custos=_d(doc.get("custos") or 0, "custos"),
            fonte=str(doc.get("fonte") or "declarado"),
            motivo=str(doc.get("motivo") or ""),
            em=_ts(doc.get("em") or utcnow(), "em"),
            registrado_em=_ts(doc.get("registrado_em") or utcnow(), "registrado_em"),
            idempotency_key=str(doc.get("idempotency_key") or ""),
            version_posicao=int(doc.get("version_posicao") or 0))


@dataclass
class PosicaoManual:
    posicao_id: str
    instrumento: str
    direcao: str
    quantidade: Decimal
    quantidade_aberta: Decimal
    preco_entrada: Decimal
    entrada_em: datetime
    origem_preco: str
    tese: str
    politica: PoliticaManual
    estado_monitor: str
    estado_financeiro: str
    version: int
    idempotency_key: str
    custos_total: Decimal
    lucro_realizado_liquido: Decimal
    high_water: Decimal | None
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    eventos: list[EventoManual] = field(default_factory=list)
    historico_politica: list[dict[str, Any]] = field(default_factory=list)
    ultimo_ciclo_em: datetime | None = None
    encerrada_em: datetime | None = None
    criado_em: datetime | None = None
    atualizado_em: datetime | None = None

    # -- cálculo -----------------------------------------------------------
    @property
    def sinal(self) -> int:
        return 1 if self.direcao == "long" else -1

    def lucro_bruto(self, preco: Decimal) -> Decimal:
        return (preco - self.preco_entrada) * self.quantidade_aberta * self.sinal

    def lucro_liquido(self, preco: Decimal) -> Decimal:
        """Não realizado, líquido de TODOS os custos conhecidos."""
        return self.lucro_bruto(preco) - self.custos_total

    def distancias(self, preco: Decimal) -> dict[str, str | None]:
        def pct(alvo: Decimal | None) -> str | None:
            if alvo is None or preco == 0:
                return None
            return f"{((alvo - preco) / preco * 100):+.2f}"

        return {"stop_pct": pct(self.politica.stop),
                "invalidacao_pct": pct(self.politica.invalidacao),
                "alvos_pct": [pct(a) for a in self.politica.alvos]}

    def para_painel(self) -> dict[str, Any]:
        ultimo = self.snapshots[-1] if self.snapshots else None
        return {"posicao_id": self.posicao_id, "instrumento": self.instrumento,
                "direcao": self.direcao,
                "quantidade": str(self.quantidade),
                "quantidade_aberta": str(self.quantidade_aberta),
                "preco_entrada": str(self.preco_entrada),
                "entrada_em": self.entrada_em.isoformat(),
                "origem_preco": self.origem_preco, "tese": self.tese,
                "estado_monitor": self.estado_monitor,
                "estado_financeiro": self.estado_financeiro,
                "version": self.version,
                "custos_total": str(self.custos_total),
                "lucro_realizado_liquido": str(self.lucro_realizado_liquido),
                "lucro_liquido": (str(self.lucro_liquido(_d(ultimo["preco"], "preco")))
                                  if ultimo else None),
                "ultimo_preco": ultimo["preco"] if ultimo else None,
                "ultimo_snapshot_em": ultimo["em"] if ultimo else None,
                "politica": self.politica.doc(),
                "snapshots": len(self.snapshots), "eventos": len(self.eventos)}

    # -- persistência ------------------------------------------------------
    def doc(self) -> dict:
        return {"posicao_id": self.posicao_id, "instrumento": self.instrumento,
                "direcao": self.direcao, "quantidade": str(self.quantidade),
                "quantidade_aberta": str(self.quantidade_aberta),
                "preco_entrada": str(self.preco_entrada),
                "entrada_em": self.entrada_em.isoformat(),
                "origem_preco": self.origem_preco, "tese": self.tese,
                "politica": self.politica.doc(),
                "estado_monitor": self.estado_monitor,
                "estado_financeiro": self.estado_financeiro,
                "version": self.version,
                "idempotency_key": self.idempotency_key,
                "custos_total": str(self.custos_total),
                "lucro_realizado_liquido": str(self.lucro_realizado_liquido),
                "high_water": str(self.high_water) if self.high_water is not None else None,
                "snapshots": self.snapshots[-SNAPSHOTS_MAX:],
                "eventos": [e.doc() for e in self.eventos],
                "historico_politica": self.historico_politica[-50:],
                "ultimo_ciclo_em": (self.ultimo_ciclo_em.isoformat()
                                    if self.ultimo_ciclo_em else None),
                "encerrada_em": (self.encerrada_em.isoformat()
                                 if self.encerrada_em else None),
                "criado_em": (self.criado_em or self.entrada_em).isoformat(),
                "atualizado_em": (self.atualizado_em or self.entrada_em).isoformat()}

    @staticmethod
    def from_doc(doc: dict) -> PosicaoManual:
        entrada_em = _ts(doc.get("entrada_em") or utcnow(), "entrada_em")
        return PosicaoManual(
            posicao_id=str(doc.get("posicao_id") or ""),
            instrumento=str(doc.get("instrumento") or ""),
            direcao=str(doc.get("direcao") or ""),
            quantidade=_d(doc.get("quantidade") or 0, "quantidade"),
            quantidade_aberta=_d(doc.get("quantidade_aberta") or 0, "quantidade_aberta"),
            preco_entrada=_d(doc.get("preco_entrada") or 0, "preco_entrada"),
            entrada_em=entrada_em,
            origem_preco=str(doc.get("origem_preco") or "declarado"),
            tese=str(doc.get("tese") or ""),
            politica=PoliticaManual.from_doc(doc.get("politica")),
            estado_monitor=str(doc.get("estado_monitor") or "MONITORING"),
            estado_financeiro=str(doc.get("estado_financeiro") or "ABERTA"),
            version=int(doc.get("version") or 1),
            idempotency_key=str(doc.get("idempotency_key") or ""),
            custos_total=_d(doc.get("custos_total") or 0, "custos_total"),
            lucro_realizado_liquido=_d(doc.get("lucro_realizado_liquido") or 0,
                                       "lucro_realizado_liquido"),
            high_water=_dopt(doc.get("high_water"), "high_water"),
            snapshots=list(doc.get("snapshots") or []),
            eventos=[EventoManual.from_doc(e) for e in doc.get("eventos") or []],
            historico_politica=list(doc.get("historico_politica") or []),
            ultimo_ciclo_em=(_ts(doc["ultimo_ciclo_em"], "ultimo_ciclo_em")
                             if doc.get("ultimo_ciclo_em") else None),
            encerrada_em=(_ts(doc["encerrada_em"], "encerrada_em")
                          if doc.get("encerrada_em") else None),
            criado_em=(_ts(doc["criado_em"], "criado_em")
                       if doc.get("criado_em") else None),
            atualizado_em=(_ts(doc["atualizado_em"], "atualizado_em")
                           if doc.get("atualizado_em") else None))


class CopilotoManual:
    """Estado do copiloto por inquilino. Nenhum método executa ordem."""

    def __init__(self) -> None:
        self.ativo = False
        self.reconciliacao_s = RECONCILIACAO_PADRAO_S
        self.posicoes: dict[str, PosicaoManual] = {}
        self.diario: list[dict[str, Any]] = []

    # -- carga/serialização ------------------------------------------------
    @classmethod
    def from_dict(cls, doc: dict | None) -> CopilotoManual:
        cop = cls()
        if not doc:
            return cop
        cfg = doc.get("config") or {}
        cop.ativo = bool(cfg.get("ativo"))
        cop.reconciliacao_s = int(cfg.get("reconciliacao_s") or RECONCILIACAO_PADRAO_S)
        cop.posicoes = {p["posicao_id"]: PosicaoManual.from_doc(p)
                        for p in (doc.get("posicoes") or {}).values()}
        cop.diario = list(doc.get("diario") or [])
        return cop

    def to_dict(self) -> dict:
        return {"schema": SCHEMA,
                "config": {"ativo": self.ativo,
                           "reconciliacao_s": self.reconciliacao_s},
                "posicoes": {pid: p.doc() for pid, p in self.posicoes.items()},
                "diario": self.diario[-200:]}

    # -- flag do modo ------------------------------------------------------
    def definir_ativo(self, ativo: bool) -> bool:
        """Liga/desliga o modo copiloto manual. Ligado = a mesa autônoma não
        executa; o copiloto apenas acompanha e alerta."""
        anterior = self.ativo
        self.ativo = bool(ativo)
        return anterior

    def posicoes_abertas(self) -> list[PosicaoManual]:
        return [p for p in self.posicoes.values()
                if p.estado_financeiro != "ENCERRADA"]

    # -- criação -----------------------------------------------------------
    def criar_posicao(self, *, instrumento: str, direcao: str, quantidade: Any,
                      preco_entrada: Any, entrada_em: Any = None,
                      origem_preco: str = "declarado", tese: str = "",
                      politica: PoliticaManual | None = None,
                      stop: Any = None, alvos: tuple | list = (),
                      invalidacao: Any = None, expira_em: Any = None,
                      trailing_pct: Any = None, intervalo_s: int | None = None,
                      custos_entrada: Any = 0, idempotency_key: str = "",
                      ) -> tuple[PosicaoManual, bool]:
        instrumento = str(instrumento or "").strip().upper()
        if not instrumento:
            raise ErroManual("instrumento é obrigatório")
        direcao = str(direcao or "").strip().lower()
        if direcao in ("compra", "buy"):
            direcao = "long"
        if direcao in ("venda", "sell"):
            direcao = "short"
        if direcao not in DIRECOES:
            raise ErroManual("direcao deve ser 'long' ou 'short'")
        if origem_preco not in ORIGENS:
            raise ErroManual(f"origem_preco deve ser uma de {ORIGENS}")
        if idempotency_key:
            for existente in self.posicoes.values():
                if existente.idempotency_key == idempotency_key:
                    return existente, False
        qtd = _d(quantidade, "quantidade")
        preco = _d(preco_entrada, "preco_entrada")
        if qtd <= 0:
            raise ErroManual("quantidade deve ser positiva")
        if preco <= 0:
            raise ErroManual("preco_entrada deve ser positivo")
        if politica is None:
            politica = PoliticaManual(
                stop=_dopt(stop, "stop"),
                alvos=tuple(_d(a, "alvo") for a in (alvos or ())),
                invalidacao=_dopt(invalidacao, "invalidacao"),
                expira_em=_ts(expira_em, "expira_em") if expira_em else None,
                trailing_pct=_dopt(trailing_pct, "trailing_pct"),
                intervalo_s=(int(intervalo_s) if intervalo_s is not None else 120))
        PoliticaManual.validar(direcao, preco, politica)
        quando = _ts(entrada_em, "entrada_em") if entrada_em else utcnow()
        agora = utcnow()
        pos = PosicaoManual(
            posicao_id=f"mp_{uuid4().hex[:12]}", instrumento=instrumento,
            direcao=direcao, quantidade=qtd, quantidade_aberta=qtd,
            preco_entrada=preco, entrada_em=quando, origem_preco=origem_preco,
            tese=str(tese or ""), politica=politica, estado_monitor="MONITORING",
            estado_financeiro="ABERTA", version=1,
            idempotency_key=idempotency_key,
            custos_total=_d(custos_entrada or 0, "custos_entrada"),
            lucro_realizado_liquido=Decimal("0"), high_water=preco,
            criado_em=agora, atualizado_em=agora)
        self.posicoes[pos.posicao_id] = pos
        return pos, True

    # -- acesso ------------------------------------------------------------
    def obter(self, posicao_id: str) -> PosicaoManual:
        pos = self.posicoes.get(str(posicao_id))
        if pos is None:
            raise PosicaoNaoEncontrada(f"posição {posicao_id!r} não existe")
        return pos

    def listar(self, estado: str | None = None) -> list[PosicaoManual]:
        itens = list(self.posicoes.values())
        if estado:
            itens = [p for p in itens if p.estado_financeiro == estado]
        return sorted(itens, key=lambda p: p.entrada_em, reverse=True)

    # -- eventos -----------------------------------------------------------
    def registrar_evento(self, posicao_id: str, *, tipo: str, quantidade: Any = None,
                         preco: Any = None, custos: Any = 0, fonte: str = "declarado",
                         motivo: str = "", em: Any = None, idempotency_key: str = "",
                         expected_version: int | None = None,
                         ) -> tuple[EventoManual, bool]:
        pos = self.obter(posicao_id)
        tipo = str(tipo or "").strip().lower()
        if tipo not in TIPOS_EVENTO:
            raise ErroManual(f"tipo de evento deve ser um de {TIPOS_EVENTO}")
        if idempotency_key:
            for existente in pos.eventos:
                if existente.idempotency_key == idempotency_key:
                    return existente, False
        if expected_version is not None and int(expected_version) != pos.version:
            raise ConflitoDeVersao(
                f"posição mudou (você viu v{expected_version}, está v{pos.version}) "
                "— recarregue antes de registrar")
        if fonte not in ORIGENS:
            raise ErroManual(f"fonte deve ser uma de {ORIGENS}")
        if tipo == "encerramento":
            if quantidade in (None, ""):
                qtd = pos.quantidade_aberta
            else:
                qtd = _d(quantidade, "quantidade")
                if qtd != pos.quantidade_aberta:
                    raise ErroManual(
                        f"encerramento fecha a posição inteira "
                        f"({pos.quantidade_aberta}); para saída parcial use 'reducao'")
        else:
            qtd = _d(quantidade, "quantidade")
        if qtd <= 0:
            raise ErroManual("quantidade do evento deve ser positiva")
        if preco in (None, ""):
            raise ErroManual(
                "preço executado é obrigatório — cotação de tela não substitui "
                "o preço real da operação")
        preco_d = _d(preco, "preco")
        if preco_d <= 0:
            raise ErroManual("preço do evento deve ser positivo")
        custos_d = _d(custos or 0, "custos")
        agora = utcnow()
        quando = _ts(em, "em") if em else agora

        if tipo in _TIPOS_SOMA:
            novo_aberta = pos.quantidade_aberta + qtd
            pos.preco_entrada = ((pos.preco_entrada * pos.quantidade_aberta
                                  + preco_d * qtd) / novo_aberta)
            pos.quantidade_aberta = novo_aberta
            pos.quantidade = pos.quantidade + qtd
            if pos.estado_financeiro == "PARCIAL":
                pos.estado_financeiro = "ABERTA"
        else:
            if qtd > pos.quantidade_aberta:
                raise ErroManual(
                    f"redução de {qtd} excede a quantidade aberta "
                    f"({pos.quantidade_aberta})")
            realizado = ((preco_d - pos.preco_entrada) * qtd * pos.sinal) - custos_d
            pos.lucro_realizado_liquido += realizado
            pos.quantidade_aberta = pos.quantidade_aberta - qtd
            if tipo == "encerramento" or pos.quantidade_aberta <= 0:
                pos.quantidade_aberta = Decimal("0")
                pos.estado_financeiro = "ENCERRADA"
                pos.estado_monitor = "MONITORING_STOPPED"
                pos.encerrada_em = quando
            else:
                pos.estado_financeiro = "PARCIAL"
        pos.custos_total += custos_d
        pos.version += 1
        pos.atualizado_em = agora
        evento = EventoManual(
            evento_id=f"ev_{uuid4().hex[:12]}", posicao_id=pos.posicao_id,
            tipo=tipo, quantidade=qtd, preco=preco_d, custos=custos_d, fonte=fonte,
            motivo=str(motivo or ""), em=quando, registrado_em=agora,
            idempotency_key=idempotency_key, version_posicao=pos.version)
        pos.eventos.append(evento)
        return evento, True

    # -- política / monitor states ------------------------------------------
    def definir_politica(self, posicao_id: str, politica: PoliticaManual, *,
                         expected_version: int | None = None) -> PosicaoManual:
        pos = self.obter(posicao_id)
        if pos.estado_financeiro == "ENCERRADA":
            raise ErroManual("posição encerrada não aceita nova política")
        if expected_version is not None and int(expected_version) != pos.version:
            raise ConflitoDeVersao(
                f"posição mudou (você viu v{expected_version}, está v{pos.version})")
        PoliticaManual.validar(pos.direcao, pos.preco_entrada, politica)
        pos.historico_politica.append(
            {"de": pos.politica.doc(), "para": politica.doc(),
             "em": utcnow().isoformat()})
        pos.politica = politica
        pos.version += 1
        pos.atualizado_em = utcnow()
        return pos

    def pausar(self, posicao_id: str, motivo: str = "") -> PosicaoManual:
        pos = self.obter(posicao_id)
        if pos.estado_financeiro == "ENCERRADA":
            raise ErroManual("posição encerrada não pode ser pausada")
        pos.estado_monitor = "PAUSED"
        pos.version += 1
        pos.atualizado_em = utcnow()
        pos.historico_politica.append(
            {"pausa": True, "motivo": str(motivo or ""),
             "em": pos.atualizado_em.isoformat()})
        return pos

    def retomar(self, posicao_id: str) -> PosicaoManual:
        pos = self.obter(posicao_id)
        if pos.estado_financeiro == "ENCERRADA":
            raise ErroManual("posição encerrada não pode ser retomada")
        pos.estado_monitor = "MONITORING"
        pos.ultimo_ciclo_em = None       # retomada reavalia sem pular intervalo
        pos.version += 1
        pos.atualizado_em = utcnow()
        return pos

    # -- diário com revisão por IA (opcional, jamais inventa preço/fill) ---
    def registrar_revisao(self, posicao_id: str, *, revisor: Callable | None = None,
                          observacao: str = "") -> dict:
        """Anexa uma entrada ao diário. O revisor (IA) recebe FATOS imutáveis
        e devolve apenas texto; falha de IA não apaga nada — a entrada fica
        com o erro registrado."""
        pos = self.obter(posicao_id)
        ultimo = pos.snapshots[-1] if pos.snapshots else None
        fatos = {"posicao_id": pos.posicao_id, "instrumento": pos.instrumento,
                 "direcao": pos.direcao,
                 "quantidade_aberta": str(pos.quantidade_aberta),
                 "preco_entrada": str(pos.preco_entrada),
                 "estado_financeiro": pos.estado_financeiro,
                 "estado_monitor": pos.estado_monitor,
                 "custos_total": str(pos.custos_total),
                 "lucro_realizado_liquido": str(pos.lucro_realizado_liquido),
                 "ultimo_preco": ultimo["preco"] if ultimo else None,
                 "lucro_liquido": (str(pos.lucro_liquido(_d(ultimo["preco"], "preco")))
                                   if ultimo else None),
                 "tese": pos.tese, "observacao": str(observacao or "")}
        entrada = {"em": utcnow().isoformat(), "posicao_id": pos.posicao_id,
                   "fatos": fatos, "revisao": None, "erro": None}
        if revisor is not None:
            try:
                texto = revisor(dict(fatos))
                entrada["revisao"] = str(texto) if texto else None
            except Exception as exc:      # IA fora do ar não perde o diário
                entrada["erro"] = f"{type(exc).__name__}: {exc}"[:200]
        self.diario.append(entrada)
        return entrada

    def revisoes(self, posicao_id: str) -> list[dict]:
        return [e for e in self.diario if e.get("posicao_id") == posicao_id]

    # -- monitor ------------------------------------------------------------
    def avaliar_posicoes(self, quotes: Mapping[str, QuoteManual], *,
                         agora: datetime | None = None,
                         enfileirar: Callable[..., Any] | None = None,
                         forcar: bool = False) -> dict[str, Any]:
        """Um tick do monitor. Só lê mercado, grava snapshots e emite alertas
        (via `enfileirar`, formato FilaDeAlertas). NUNCA executa, NUNCA fecha
        posição: preço atingir alvo vira ALERTA, não encerramento."""
        agora = agora or utcnow()
        resumo: dict[str, Any] = {"em": agora.isoformat(), "avaliadas": 0,
                                  "alertas": [], "sem_preco": [], "stale": [],
                                  "reconciliadas": [], "reconciliacao_pendente": [],
                                  "financeiras_fechadas": 0}

        def alertar(**kwargs) -> None:
            resumo["alertas"].append(kwargs)
            if enfileirar is not None:
                enfileirar(**kwargs)

        for pos in self.posicoes_abertas():
            if pos.estado_monitor == "PAUSED":
                continue
            if (not forcar and pos.ultimo_ciclo_em is not None
                    and pos.politica.intervalo_s > 0
                    and (agora - pos.ultimo_ciclo_em).total_seconds()
                    < pos.politica.intervalo_s):
                continue
            quote = quotes.get(pos.instrumento)
            if quote is None:
                pos.estado_monitor = "CONNECTION_LOST"
                alertar(tipo="manual_sem_cotacao", severidade="atencao",
                        titulo=f"Sem cotação para {pos.instrumento}",
                        detalhe=("O copiloto não encontra cotação para esta "
                                 "posição manual — resolva a fonte ou pause o "
                                 "acompanhamento."),
                        acao="Verifique a fonte de dados ou pause a posição.",
                        fonte="copiloto_manual",
                        dedupe_key=f"manual:{pos.posicao_id}:sem_cotacao",
                        dados={"posicao_id": pos.posicao_id,
                               "instrumento": pos.instrumento})
                resumo["sem_preco"].append(pos.posicao_id)
                continue

            if pos.estado_monitor == "RECONCILIATION_REQUIRED":
                # Ainda pendente: sem avaliação de política até retomar().
                continue

            # Cotações velhas não avaliam política nenhuma (preço de agora
            # com dado de ontem é decisão inventada) — e o tick velho NÃO
            # conta como ciclo: staleness longa vira reconciliação ao voltar.
            if not quote.fresco(agora):
                pos.estado_monitor = "FEED_STALE"
                alertar(tipo="manual_feed_stale", severidade="atencao",
                        titulo=f"Cotação atrasada em {pos.instrumento}",
                        detalhe=("Último candle fechado em "
                                 f"{quote.cutoff.isoformat() if quote.cutoff else '?'} "
                                 "— decisões de stop/alvo pausadas com preço velho."),
                        acao="Verifique a conexão; nada foi avaliado com este preço.",
                        fonte="copiloto_manual",
                        dedupe_key=f"manual:{pos.posicao_id}:feed_stale",
                        dados={"posicao_id": pos.posicao_id,
                               "instrumento": pos.instrumento})
                resumo["stale"].append(pos.posicao_id)
                continue

            # Gap de acompanhamento com preço fresco (reinício/queda/feed
            # mudo por muito tempo): o estado financeiro pode ter mudado por
            # fora — exige reconciliação ANTES de voltar a avaliar política.
            if (pos.ultimo_ciclo_em is not None
                    and (agora - pos.ultimo_ciclo_em).total_seconds()
                    > self.reconciliacao_s):
                pos.estado_monitor = "RECONCILIATION_REQUIRED"
                alertar(tipo="manual_reconciliacao_pendente", severidade="atencao",
                        titulo=f"Reconciliação necessária: {pos.instrumento}",
                        detalhe=("O acompanhamento ficou sem ciclos por tempo demais "
                                 "(reinício/queda) — confira o preço real e retome."),
                        acao="Abra a posição e retome o acompanhamento.",
                        fonte="copiloto_manual",
                        dedupe_key=f"manual:{pos.posicao_id}:reconciliacao",
                        dados={"posicao_id": pos.posicao_id})
                resumo["reconciliacao_pendente"].append(pos.posicao_id)
                continue

            # Feed voltou (CONNECTION_LOST/FEED_STALE) com preço fresco:
            # recupera o acompanhamento — o financeiro não mudou com isso.
            if pos.estado_monitor in ("CONNECTION_LOST", "FEED_STALE"):
                resumo["reconciliadas"].append(pos.posicao_id)
            pos.ultimo_ciclo_em = agora
            pos.estado_monitor = "MONITORING"

            preco = quote.preco
            if preco <= 0:
                alertar(tipo="manual_preco_invalido", severidade="erro",
                        titulo=f"Preço inválido para {pos.instrumento}",
                        detalhe=f"Feed devolveu {preco!r} — avaliação ignorada.",
                        acao="Nada foi calculado com este preço.",
                        fonte="copiloto_manual",
                        dedupe_key=f"manual:{pos.posicao_id}:preco_invalido",
                        dados={"posicao_id": pos.posicao_id})
                continue

            if pos.high_water is None:
                pos.high_water = preco
            elif pos.direcao == "long":
                pos.high_water = max(pos.high_water, preco)
            else:
                pos.high_water = min(pos.high_water, preco)

            liquido = pos.lucro_liquido(preco)
            snap = {"em": agora.isoformat(), "preco": str(preco),
                    "cutoff": quote.cutoff.isoformat() if quote.cutoff else None,
                    "recebido_em": (quote.recebido_em.isoformat()
                                    if quote.recebido_em else agora.isoformat()),
                    "fonte": quote.fonte, "quality": quote.quality,
                    "lucro_bruto": str(pos.lucro_bruto(preco)),
                    "lucro_liquido": str(liquido),
                    "high_water": str(pos.high_water),
                    "distancias": pos.distancias(preco)}
            pos.snapshots.append(snap)
            del pos.snapshots[:-SNAPSHOTS_MAX]
            resumo["avaliadas"] += 1

            alertar_ctx = {"fonte": "copiloto_manual",
                           "dados": {"posicao_id": pos.posicao_id,
                                     "instrumento": pos.instrumento,
                                     "preco": str(preco),
                                     "lucro_liquido": str(liquido)}}
            pol = pos.politica
            # ORDEM DE SEGURANÇA: stop primeiro; alvo é ALERTA, nunca fechamento.
            if pol.stop is not None:
                cruzou = (preco <= pol.stop) if pos.direcao == "long" \
                    else (preco >= pol.stop)
                if cruzou:
                    alertar(tipo="manual_stop_atingido", severidade="critico",
                            titulo=f"Stop atingido: {pos.instrumento}",
                            detalhe=(f"Preço {preco} cruzou seu stop {pol.stop} — "
                                     "decida e registre o encerramento se for o caso."),
                            acao="Registre o evento de encerramento/redução real.",
                            dedupe_key=f"manual:{pos.posicao_id}:stop",
                            **alertar_ctx)
            for i, alvo in enumerate(pol.alvos):
                atingiu = (preco >= alvo) if pos.direcao == "long" else (preco <= alvo)
                if atingiu:
                    alertar(tipo="manual_alvo_atingido", severidade="atencao",
                            titulo=f"Alvo {alvo} atingido: {pos.instrumento}",
                            detalhe=("O preço tocou seu alvo declarado. O copiloto "
                                     "NÃO encerra nada por isso — a posição segue "
                                     "aberta até VOCÊ registrar o que fez."),
                            acao="Realize parcial/total e registre o evento real.",
                            dedupe_key=f"manual:{pos.posicao_id}:alvo:{i}",
                            **alertar_ctx)
            if pol.invalidacao is not None:
                invalidou = (preco <= pol.invalidacao) if pos.direcao == "long" \
                    else (preco >= pol.invalidacao)
                if invalidou:
                    alertar(tipo="manual_tese_invalidada", severidade="critico",
                            titulo=f"Tese invalidada: {pos.instrumento}",
                            detalhe=(f"Preço {preco} cruzou sua invalidação "
                                     f"{pol.invalidacao}. Sem ordem automática — "
                                     "sua decisão e seu registro."),
                            acao="Registre o que decidir; nada foi executado.",
                            dedupe_key=f"manual:{pos.posicao_id}:invalidacao",
                            **alertar_ctx)
            if pol.trailing_pct is not None and pos.high_water is not None:
                limite = (pos.high_water * (1 - pol.trailing_pct / 100)
                          if pos.direcao == "long"
                          else pos.high_water * (1 + pol.trailing_pct / 100))
                recuou = (preco <= limite) if pos.direcao == "long" \
                    else (preco >= limite)
                if recuou:
                    alertar(tipo="manual_trailing_acionado", severidade="atencao",
                            titulo=f"Trailing acionado: {pos.instrumento}",
                            detalhe=(f"Melhor preço {pos.high_water}; agora {preco} "
                                     f"recuou além de {pol.trailing_pct}%. Sugestão: "
                                     f"considere proteger em ~{limite:.2f}."),
                            acao="Sugestão de proteção — sem execução automática.",
                            dedupe_key=f"manual:{pos.posicao_id}:trailing",
                            **alertar_ctx)
            if pol.expira_em is not None and agora > pol.expira_em:
                alertar(tipo="manual_tese_expirada", severidade="atencao",
                        titulo=f"Prazo da tese venceu: {pos.instrumento}",
                        detalhe=(f"Sua tese venceria em {pol.expira_em.isoformat()} "
                                 "— revalide ou registre o encerramento."),
                        acao="Revalide a tese ou registre o que decidir.",
                        dedupe_key=f"manual:{pos.posicao_id}:expiracao",
                        **alertar_ctx)

        resumo["financeiras_fechadas"] = sum(
            1 for p in self.posicoes.values() if p.estado_financeiro == "ENCERRADA")
        return resumo

    def saude_do_monitor(self) -> dict[str, Any]:
        abertas = self.posicoes_abertas()
        estados: dict[str, int] = {}
        for p in abertas:
            estados[p.estado_monitor] = estados.get(p.estado_monitor, 0) + 1
        return {"ativo": self.ativo, "posicoes_abertas": len(abertas),
                "estados": estados,
                "ultimo_tick_em": max((p.ultimo_ciclo_em for p in abertas
                                       if p.ultimo_ciclo_em), default=None),
                "intervalo_limites_s": [INTERVALO_MIN_S, INTERVALO_MAX_S],
                "reconciliacao_s": self.reconciliacao_s}
