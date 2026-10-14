"""Modo da mesa e posse exclusiva de execução (portabilidade #29).

Windows local e Oracle ARM64 rodam o MESMO núcleo. Dois processos apontando
para o mesmo estado (o PC do dono e o servidor, durante a migração) não
podem executar a mesma mesa ao mesmo tempo — quem executa é quem segura a
POSSE, um arrendamento com validade gravado no armazém de estado.

Estados do modo (decisão do dono, persistida):
- ATIVA: executa normalmente;
- OBSERVADOR: analisa e propõe, NUNCA envia ordem — a porta de entrada do
  servidor novo antes de o dono confiar a mesa a ele;
- PAUSADA: nada roda; retomada é um clique;
- BLOQUEADA: parada de segurança por falha/auditoria; sair dela exige
  DESBLOQUEIO EXPLÍCITO do dono (a rota exige a flag).

Fail-closed: estado desconhecido no disco vira BLOQUEADA — documento
corrompido nunca reabre a execução sozinho.

Limite honesto da posse: o arrendamento vive no armazém. Com backend
postgres a disputa é atômica (UPSERT condicional) e vale entre máquinas;
com backend arquivo vale para os processos desta máquina — PC e Oracle em
máquinas diferentes só se coordenam pelo estado COMPARTILHADO (migração
exige banco ou desligamento do processo antigo; ver DEPLOY.md).
"""
from __future__ import annotations

import os
import socket
from datetime import datetime, timezone
from enum import Enum

TTL_POSSE_SEGUNDOS = 900          # renovada a cada ciclo (intervalo padrão 300 s)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _epoch(dt: datetime) -> float:
    return dt.timestamp()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class EstadoDaMesa(str, Enum):
    ATIVA = "ativa"
    OBSERVADOR = "observador"
    PAUSADA = "pausada"
    BLOQUEADA = "bloqueada"


_ROTULO = {
    EstadoDaMesa.ATIVA: "ativa — executa",
    EstadoDaMesa.OBSERVADOR: "observador — analisa e propõe, não executa",
    EstadoDaMesa.PAUSADA: "pausada",
    EstadoDaMesa.BLOQUEADA: "bloqueada (segurança)",
}

_MOTIVO_PADRAO = {
    EstadoDaMesa.OBSERVADOR: "a mesa analisa e propõe, mas não executa",
    EstadoDaMesa.PAUSADA: "mesa pausada pelo dono",
    EstadoDaMesa.BLOQUEADA: "mesa bloqueada por segurança",
}


class ModoBloqueado(Exception):
    """Transição recusada: sair de BLOQUEADA exige desbloqueio explícito."""


class ModoDaMesa:
    """Estado persistido da mesa; lido fresco a cada consulta (é minúsculo)."""

    CHAVE = "modo"

    def __init__(self, armazem, tenant_id: str = "legacy"):
        self._armazem = armazem
        self._tenant = tenant_id

    # -- leitura -------------------------------------------------------
    def _ler(self) -> dict:
        doc = self._armazem.carregar(self._tenant, self.CHAVE)
        if not isinstance(doc, dict):
            return {"estado": EstadoDaMesa.ATIVA.value, "motivo": "",
                    "por": "sistema", "em": ""}
        try:
            EstadoDaMesa(doc.get("estado"))
        except ValueError:
            # fail-closed: valor desconhecido no disco não reabre a execução
            return {"estado": EstadoDaMesa.BLOQUEADA.value,
                    "motivo": (f"estado desconhecido no disco "
                               f"({doc.get('estado')!r}) — desbloqueie "
                               "conscientemente"),
                    "por": "sistema", "em": doc.get("em", "")}
        return doc

    @property
    def estado(self) -> EstadoDaMesa:
        return EstadoDaMesa(self._ler()["estado"])

    def pode_executar(self) -> tuple[bool, str]:
        doc = self._ler()
        estado = EstadoDaMesa(doc["estado"])
        if estado is EstadoDaMesa.ATIVA:
            return True, ""
        return False, f"mesa {estado.value}: " \
                      f"{doc.get('motivo') or _MOTIVO_PADRAO[estado]}"

    # -- escrita -------------------------------------------------------
    def definir(self, estado, *, motivo: str = "", por: str = "dono",
                desbloqueio: bool = False) -> EstadoDaMesa:
        try:
            alvo = EstadoDaMesa(estado)
        except ValueError as exc:
            raise ValueError(f"estado inválido: {estado!r}") from exc
        atual = self.estado
        if atual is EstadoDaMesa.BLOQUEADA and alvo is not EstadoDaMesa.BLOQUEADA \
                and not desbloqueio:
            raise ModoBloqueado(
                "mesa bloqueada: sair deste estado exige desbloqueio "
                "explícito do dono")
        self._armazem.salvar(self._tenant, self.CHAVE, {
            "estado": alvo.value,
            "motivo": motivo or ("" if alvo is EstadoDaMesa.ATIVA
                                 else _MOTIVO_PADRAO[alvo]),
            "por": por,
            "em": _iso(_utcnow()),
        })
        return alvo

    def bloquear(self, motivo: str, *, por: str = "sistema") -> EstadoDaMesa:
        """Parada de segurança (watchers de falha chamam isto)."""
        return self.definir(EstadoDaMesa.BLOQUEADA, motivo=motivo, por=por)

    def resumo(self) -> dict:
        doc = self._ler()
        estado = EstadoDaMesa(doc["estado"])
        ok, _ = self.pode_executar()
        return {"estado": estado.value,
                "estado_label": _ROTULO[estado],
                "motivo": doc.get("motivo") or _MOTIVO_PADRAO.get(estado, ""),
                "por": doc.get("por", ""), "em": doc.get("em", ""),
                "pode_executar": ok}


class PosseDeExecucao:
    """Arrendamento exclusivo: só o dono da posse submete ordem.

    O documento gravado no armazém é a fonte da verdade; a validade
    (TTL) é comparada com o relógio, não com o processo que gravou —
    um processo morto perde a posse sozinho, e o servidor novo assume
    sem tocar em nada do PC.
    """

    CHAVE = "posse"

    def __init__(self, armazem, tenant_id: str = "legacy", *, dono: str | None = None,
                 ttl_segundos: float = TTL_POSSE_SEGUNDOS, agora=None):
        self._armazem = armazem
        self._tenant = tenant_id
        self.dono = dono or f"{socket.gethostname()}-{os.getpid()}"
        self.ttl = float(ttl_segundos)
        self._agora = agora or _utcnow

    # -- leitura -------------------------------------------------------
    def atual(self) -> dict | None:
        doc = self._armazem.carregar(self._tenant, self.CHAVE)
        if not isinstance(doc, dict) or not doc.get("dono"):
            return None
        try:
            expira = float(doc.get("expira_em") or 0)
        except (TypeError, ValueError):
            return None
        if expira <= _epoch(self._agora()):
            return None                    # vencida: livre para o próximo
        return doc

    def possuo(self) -> bool:
        doc = self.atual()
        return bool(doc and doc.get("dono") == self.dono)

    # -- escrita -------------------------------------------------------
    def assumir(self) -> tuple[bool, str]:
        """Assume (ou renova) a posse. Devolve (ok, motivo-do-fracasso)."""
        doc = self.atual()
        if doc and doc.get("dono") != self.dono:
            return False, f"{doc['dono']} (expira {doc.get('expira_em_iso', '?')})"
        agora = self._agora()
        vence = agora.timestamp() + self.ttl
        novo = {"dono": self.dono, "expira_em": vence,
                "expira_em_iso": _iso(datetime.fromtimestamp(vence, timezone.utc)),
                "assumida_em_iso": _iso(agora)}
        ganhou, onde = self._armazem.salvar_posse(
            self._tenant, self.CHAVE, novo,
            dono=self.dono, agora_epoch=agora.timestamp())
        if not ganhou:
            rival = self.atual() or {}
            return False, (f"{rival.get('dono', onde)} "
                           f"(expira {rival.get('expira_em_iso', '?')})")
        return True, ""

    def renovar(self) -> bool:
        if not self.possuo():
            return False
        return self.assumir()[0]

    def libertar(self) -> None:
        if not self.possuo():
            return
        agora = self._agora()
        self._armazem.salvar(self._tenant, self.CHAVE, {
            "dono": self.dono, "expira_em": 0.0, "expira_em_iso": _iso(agora),
            "liberada_em_iso": _iso(agora)})

    def resumo(self) -> dict:
        doc = self.atual()
        if doc is None:
            return {"dono": "", "valida": False, "sou_eu": False,
                    "expira_em_iso": ""}
        return {"dono": doc.get("dono", ""),
                "expira_em": doc.get("expira_em"),
                "expira_em_iso": doc.get("expira_em_iso", ""),
                "valida": True, "sou_eu": doc.get("dono") == self.dono}


__all__ = ["EstadoDaMesa", "ModoBloqueado", "ModoDaMesa", "PosseDeExecucao",
           "TTL_POSSE_SEGUNDOS"]
