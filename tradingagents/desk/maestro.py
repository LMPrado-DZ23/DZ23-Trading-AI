"""Maestro Central: supervisão de TODAS as salas (BT-8).

Um maestro, muitas salas. Ele não opera, não forma ninguém e não autoriza
LIVE — supervisiona, resolve conflitos e segura a exposição global quando o
dono define um teto.

Regras:
- **Um dono por instrumento, em toda a mesa.** Se uma sala já carrega
  BTC/USDT, outra sala que tente entrar no mesmo instrumento é negada: duas
  salas compradas no mesmo ativo é uma aposta dobrada com nomes diferentes.
- **Conflito entre candidatas novas é resolvido por prioridade declarada**
  (ou ordem determinística de chave, na falta dela): uma vence, a outra é
  negada com o motivo — nunca as duas, nunca ao acaso.
- **Pausa global** exige motivo, é persistida e auditável, e bloqueia TODAS
  as novas entradas de TODAS as salas até ser retomada. Saídas nunca são
  bloqueadas: travar saída é transformar proteção em armadilha.
- **Teto de exposição global**: soma do custo das posições de todas as
  salas. No teto, novas entradas param — fail-closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .models import utcnow
from .salas import RegistroDeSalas, Sala


class MaestroError(RuntimeError):
    """Conflito malformado ou tentativa de operar pausa sem motivo."""


def _valor_posicao(posicao: dict) -> Decimal:
    return (Decimal(str(posicao.get("quantidade", "0")))
            * Decimal(str(posicao.get("preco_entrada", "0"))))


@dataclass(frozen=True)
class Decisao:
    """Resultado auditável de uma candidata a entrada."""
    sala: str
    instrumento: str
    agente: str
    concedido: bool
    motivo: str

    def como_dict(self) -> dict:
        return {"sala": self.sala, "instrumento": self.instrumento,
                "agente": self.agente, "concedido": self.concedido,
                "motivo": self.motivo}


class MaestroCentral:
    """Supervisor de todas as salas. Nunca envia ordem; decide e bloqueia."""

    CHAVE_ARMAZEM = "maestro"
    MAX_EVENTOS = 100

    def __init__(self, registro: RegistroDeSalas, *,
                 teto_exposicao_global: Decimal | None = None,
                 prioridades: dict[str, int] | None = None):
        if teto_exposicao_global is not None:
            teto_exposicao_global = Decimal(str(teto_exposicao_global))
            if teto_exposicao_global <= 0:
                raise MaestroError("teto de exposição global precisa ser "
                                   "positivo (ou None para não definir teto)")
        self.registro = registro
        self.teto_exposicao_global = teto_exposicao_global
        self.prioridades = dict(prioridades or {})
        self._pausa: dict = {"ativo": False, "motivo": "", "desde": None,
                             "eventos": []}
        self._carregar()

    # -- persistência ---------------------------------------------------
    def _carregar(self) -> None:
        armazem = self.registro.armazem
        if armazem is None:
            return
        dados = armazem.carregar(self.registro.tenant_id, self.CHAVE_ARMAZEM)
        if isinstance(dados, dict) and isinstance(dados.get("pausa"), dict):
            pausa = dados["pausa"]
            self._pausa = {"ativo": bool(pausa.get("ativo")),
                           "motivo": str(pausa.get("motivo", "")),
                           "desde": pausa.get("desde"),
                           "eventos": list(pausa.get("eventos", []))}

    def salvar(self) -> None:
        armazem = self.registro.armazem
        if armazem is None:
            return
        armazem.salvar(self.registro.tenant_id, self.CHAVE_ARMAZEM,
                       {"pausa": self._pausa})

    # -- pausa global ---------------------------------------------------
    @property
    def pausado(self) -> bool:
        return bool(self._pausa.get("ativo"))

    def _registrar_evento(self, evento: str, motivo: str,
                          agora: datetime) -> None:
        self._pausa["eventos"].append({"quando": agora.isoformat(),
                                       "evento": evento,
                                       "motivo": motivo[:400]})
        if len(self._pausa["eventos"]) > self.MAX_EVENTOS:
            self._pausa["eventos"] = self._pausa["eventos"][-self.MAX_EVENTOS:]

    def pausar_global(self, motivo: str, *,
                      agora: datetime | None = None) -> dict:
        motivo = (motivo or "").strip()
        if not motivo:
            raise MaestroError("pausa global exige motivo — sem registro, "
                               "pausa vira mistério")
        agora = agora or utcnow()
        self._pausa.update({"ativo": True, "motivo": motivo,
                            "desde": agora.isoformat()})
        self._registrar_evento("pausada", motivo, agora)
        self.salvar()
        return dict(self._pausa)

    def retomar_global(self, motivo: str, *,
                       agora: datetime | None = None) -> dict:
        motivo = (motivo or "").strip()
        if not motivo:
            raise MaestroError("retomada exige motivo — quem pausou merece "
                               "saber por que voltou")
        agora = agora or utcnow()
        self._pausa.update({"ativo": False, "motivo": motivo,
                            "desde": agora.isoformat()})
        self._registrar_evento("retomada", motivo, agora)
        self.salvar()
        return dict(self._pausa)

    # -- exposição ------------------------------------------------------
    def exposicao_sala(self, sala: Sala) -> Decimal:
        return sum((_valor_posicao(p) for p in sala.posicoes.values()),
                   Decimal("0"))

    def exposicao_global(self) -> dict:
        por_sala = {chave: self.exposicao_sala(sala)
                    for chave, sala in sorted(self.registro.salas.items())}
        total = sum(por_sala.values(), Decimal("0"))
        excedido = (self.teto_exposicao_global is not None
                    and total >= self.teto_exposicao_global)
        return {"por_sala": {k: str(v) for k, v in por_sala.items()},
                "total": str(total),
                "teto": (str(self.teto_exposicao_global)
                         if self.teto_exposicao_global is not None else None),
                "excedido": excedido}

    def _motivo_para_bloquear(self) -> str:
        if self.pausado:
            return (f"pausa global ativa desde {self._pausa.get('desde')}: "
                    f"{self._pausa.get('motivo')}")
        exp = self.exposicao_global()
        if exp["excedido"]:
            return (f"exposição global {exp['total']} alcançou o teto "
                    f"{exp['teto']} — novas entradas bloqueadas até reduzir")
        return ""

    def pode_entrar(self, sala: Sala) -> tuple[bool, str]:
        motivo = self._motivo_para_bloquear()
        return (not motivo, motivo)

    def vigia_de(self, sala: Sala, agent_ids) -> object:
        """Callable pronto para o `vigia` do orquestrador da sala."""
        agentes = tuple(agent_ids)

        def vigia() -> dict:
            liberado, motivo = self.pode_entrar(sala)
            if liberado:
                return {}
            return dict.fromkeys(agentes, f"Maestro Central: {motivo}")

        return vigia

    # -- conflitos ------------------------------------------------------
    def dono_de(self, instrumento: str) -> dict | None:
        """Quem carrega o instrumento, em qualquer sala (determinístico)."""
        for chave, sala in sorted(self.registro.salas.items()):
            posicao = sala.posicoes.get(instrumento)
            if posicao:
                return {"sala": chave, "agente": posicao.get("agente", ""),
                        "desde": posicao.get("aberta_em", "")}
        return None

    def conflitos(self) -> list[dict]:
        """Instrumentos com posição viva em MAIS de uma sala: exposição
        correlacionada que o painel precisa mostrar."""
        por_instrumento: dict[str, list[str]] = {}
        for chave, sala in sorted(self.registro.salas.items()):
            for instrumento in sala.posicoes:
                por_instrumento.setdefault(instrumento, []).append(chave)
        return [
            {"instrumento": instrumento, "salas": salas,
             "aviso": ("posição viva em mais de uma sala: exposição "
                       "correlacionada — o mesmo ativo não é duas apostas")}
            for instrumento, salas in sorted(por_instrumento.items())
            if len(salas) > 1
        ]

    def _prioridade(self, sala: Sala) -> int:
        return int(self.prioridades.get(sala.contexto.chave, 0))

    def resolver_entradas(self, candidatas) -> list[Decisao]:
        """Um vencedor por instrumento. Candidatas:

            [{"sala": Sala, "instrumento": "BTC/USDT", "agente": "t1"}]

        Ordem de decisão, toda explícita: pausa/teto global → dono existente
        → prioridade entre candidatas novas. Segunda candidata no mesmo
        instrumento NUNCA passa — nem na mesma sala, nem em outra.
        """
        motivo_global = self._motivo_para_bloquear()
        decisoes: list[Decisao] = []

        por_instrumento: dict[str, list[dict]] = {}
        for c in candidatas:
            sala = c.get("sala")
            instrumento = str(c.get("instrumento", ""))
            if not isinstance(sala, Sala) or not instrumento:
                raise MaestroError("candidata precisa de sala (Sala) e "
                                   "instrumento — sem isso, não há decisão")
            por_instrumento.setdefault(instrumento, []).append(c)

        for instrumento in sorted(por_instrumento):
            grupo = por_instrumento[instrumento]
            if motivo_global:
                for c in grupo:
                    decisoes.append(Decisao(
                        c["sala"].contexto.chave, instrumento,
                        str(c.get("agente", "")), False,
                        f"bloqueio global: {motivo_global}"))
                continue
            vencedoras = sorted(
                grupo, key=lambda c: (-self._prioridade(c["sala"]),
                                      c["sala"].contexto.chave))
            vencedora = vencedoras[0]
            for c in grupo:
                sala = c["sala"]
                agente = str(c.get("agente", ""))
                if c is vencedora:
                    dono = self.dono_de(instrumento)
                    if dono and dono["sala"] != sala.contexto.chave:
                        decisoes.append(Decisao(
                            sala.contexto.chave, instrumento, agente, False,
                            f"conflito: {instrumento} já pertence à sala "
                            f"{dono['sala']} (agente {dono['agente']}) — uma "
                            "mesa, um dono por instrumento"))
                    elif dono:
                        decisoes.append(Decisao(
                            sala.contexto.chave, instrumento, agente, False,
                            f"{instrumento} já tem posição aberta nesta "
                            "mesa — uma posição por instrumento"))
                    else:
                        decisoes.append(Decisao(
                            sala.contexto.chave, instrumento, agente, True,
                            "sem conflito: instrumento livre em todas as "
                            "salas"))
                else:
                    decisoes.append(Decisao(
                        sala.contexto.chave, instrumento, agente, False,
                        f"conflito entre salas em {instrumento}: a sala "
                        f"{vencedora['sala'].contexto.chave} tem prioridade "
                        "declarada"))
        decisoes.sort(key=lambda d: (d.instrumento, d.sala))
        return decisoes

    # -- retrato --------------------------------------------------------
    def relatorio(self) -> dict:
        return {
            "pausa_global": dict(self._pausa),
            "exposicao": self.exposicao_global(),
            "prioridades": dict(self.prioridades),
            "conflitos": self.conflitos(),
            "aviso": ("o Maestro Central supervisiona e bloqueia; não opera, "
                      "não certifica e não autoriza operação real"),
        }


__all__ = ["MaestroCentral", "MaestroError", "Decisao"]
