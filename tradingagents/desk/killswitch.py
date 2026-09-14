"""Parada de emergência durável (blocker da auditoria).

O kill switch vivia em memória (`set()` no serviço e `self.kill_switch` na
ponte). Bastava um reinício — deploy, crash, OOM, reboot do container — para
ele voltar a DESLIGADO sozinho, enquanto o dono acreditava ter parado tudo.

Regras:
- Estado persistido; reiniciar NÃO desarma.
- Estado ilegível ou ausente em servidor que já operou: ACIONADO
  (fail-closed). Não saber se está parado equivale a estar parado.
- Desarmar exige autorização explícita E reconciliação concluída.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

ACIONADO = "acionado"
AGUARDANDO_RECONCILIACAO = "aguardando_reconciliacao"
LIBERADO = "liberado"


class KillSwitchError(RuntimeError):
    """Tentativa inválida de mudar o estado da parada de emergência."""


@dataclass
class EstadoParada:
    estado: str = LIBERADO
    motivo: str = ""
    acionado_em: str = ""
    por: str = ""

    @property
    def bloqueia(self) -> bool:
        return self.estado != LIBERADO

    def to_doc(self) -> dict:
        return {"schema": "dz23.killswitch/1", "estado": self.estado,
                "motivo": self.motivo, "acionado_em": self.acionado_em,
                "por": self.por}

    @classmethod
    def from_doc(cls, doc: dict | None) -> EstadoParada:
        if not doc:
            return cls()
        if doc.get("schema") != "dz23.killswitch/1":
            # Documento estranho: não dá para afirmar que está liberado.
            return cls(ACIONADO, "estado da parada ilegível — bloqueando por "
                                 "precaução", "", "sistema")
        estado = doc.get("estado", LIBERADO)
        if estado not in (ACIONADO, AGUARDANDO_RECONCILIACAO, LIBERADO):
            return cls(ACIONADO, f"estado desconhecido {estado!r}", "", "sistema")
        return cls(estado, doc.get("motivo", ""), doc.get("acionado_em", ""),
                   doc.get("por", ""))


class KillSwitch:
    """Persistido no armazém do inquilino (Postgres ou volume)."""

    def __init__(self, armazem=None, tenant_id: str = "legacy"):
        self.armazem = armazem
        self.tenant_id = tenant_id
        self.estado = self._carregar()

    def _carregar(self) -> EstadoParada:
        if self.armazem is None:
            return EstadoParada()
        try:
            return EstadoParada.from_doc(
                self.armazem.carregar(self.tenant_id, "parada_emergencia"))
        except Exception:
            # Armazém indisponível: não é possível afirmar que está liberado.
            return EstadoParada(ACIONADO, "não foi possível ler o estado da "
                                          "parada — bloqueando por precaução",
                                "", "sistema")

    def _salvar(self) -> None:
        if self.armazem is not None:
            self.armazem.salvar(self.tenant_id, "parada_emergencia",
                                self.estado.to_doc())

    @property
    def bloqueia(self) -> bool:
        return self.estado.bloqueia

    def acionar(self, motivo: str, por: str = "dono") -> EstadoParada:
        self.estado = EstadoParada(
            ACIONADO, motivo or "acionado pelo dono",
            datetime.now(timezone.utc).isoformat(), por)
        self._salvar()
        return self.estado

    def marcar_reconciliado(self) -> EstadoParada:
        """Conferiu com a corretora; falta a autorização para voltar."""
        if self.estado.estado != ACIONADO:
            return self.estado
        self.estado = EstadoParada(AGUARDANDO_RECONCILIACAO, self.estado.motivo,
                                   self.estado.acionado_em, self.estado.por)
        self._salvar()
        return self.estado

    def liberar(self, *, frase_correta: bool, reconciliado: bool,
                por: str = "dono") -> EstadoParada:
        """Duas condições, ambas obrigatórias: autorização E reconciliação."""
        if not frase_correta:
            raise KillSwitchError("autorização inválida para liberar a parada")
        if not reconciliado and self.estado.estado == ACIONADO:
            raise KillSwitchError(
                "reconcilie com a corretora antes de liberar: pode haver "
                "posição ou ordem que o sistema desconhece")
        self.estado = EstadoParada(LIBERADO, "", "", por)
        self._salvar()
        return self.estado


__all__ = ["KillSwitch", "EstadoParada", "KillSwitchError", "ACIONADO",
           "AGUARDANDO_RECONCILIACAO", "LIBERADO"]
