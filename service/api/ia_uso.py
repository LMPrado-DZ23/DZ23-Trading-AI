"""Consumo de IA por inquilino (V8 §6): chamadas, tokens e custo estimado.

O que este registro É: contabilidade local honesta do que a IA consumiu —
quantas chamadas, quantos tokens o provedor reportou, quanto isso custa pela
tabela de referência (tradingagents.llm_clients.precos) e como anda o
orçamento configurado.

O que este registro NÃO É: trava de segurança. Orçamento aqui é
INFORMATIVO — nunca desativa a verificação de contexto da mesa nem qualquer
gate financeiro. Quem desliga proteção por causa de orçamento troca risco
por troco.

Regras:
- Chamada sem tokens reportados conta como chamada ("sem_uso"), sem inventar
  token nenhum; custo de modelo sem preço de referência fica contado em
  "chamadas_sem_preco" — o total estimado diz que é parcial.
- Persistência atômica em DATA/ia-uso.json, histórico de dias com teto.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from tradingagents.desk.storage import escrever_json_atomico
from tradingagents.llm_clients import precos

_LOCK = threading.Lock()
_DIAS_MAX = 62

_ZEROS = ("chamadas", "tokens_entrada", "tokens_saida", "chamadas_sem_uso",
          "chamadas_sem_preco")


def _novo_alvo() -> dict:
    return {**dict.fromkeys(_ZEROS, 0), "custo_estimado_usd": 0.0}


class RegistroDeUso:
    """Contador por inquilino, persistido em um JSON atômico."""

    def __init__(self, caminho: Path):
        self.caminho = Path(caminho)
        self._dados: dict | None = None

    def _carregar(self) -> dict:
        if self._dados is None:
            try:
                bruto = json.loads(self.caminho.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                bruto = {}
            self._dados = bruto if isinstance(bruto, dict) else {}
        return self._dados

    def registrar(self, tenant_id: str, meta: dict) -> None:
        """meta: {"provedor": ..., "modelo": ..., "tokens": {entrada, saida}|None}."""
        provedor = str(meta.get("provedor") or "?")
        modelo = str(meta.get("modelo") or "")
        tokens = meta.get("tokens")
        entrada = int(tokens.get("entrada") or 0) if isinstance(tokens, dict) else 0
        saida = int(tokens.get("saida") or 0) if isinstance(tokens, dict) else 0
        custo = (precos.custo_estimado(provedor, modelo, entrada, saida)
                 if isinstance(tokens, dict) else None)
        dia = time.strftime("%Y-%m-%d", time.gmtime())
        with _LOCK:
            dados = self._carregar()
            inquilino = dados.setdefault(tenant_id, {})
            for alvo in (inquilino.setdefault("total", _novo_alvo()),
                         inquilino.setdefault("por_dia", {}).setdefault(dia, _novo_alvo()),
                         inquilino.setdefault("por_provedor", {}).setdefault(provedor, _novo_alvo())):
                alvo["chamadas"] += 1
                if not isinstance(tokens, dict):
                    alvo["chamadas_sem_uso"] += 1
                    continue
                alvo["tokens_entrada"] += entrada
                alvo["tokens_saida"] += saida
                if custo is None:
                    alvo["chamadas_sem_preco"] += 1
                else:
                    alvo["custo_estimado_usd"] = round(
                        alvo["custo_estimado_usd"] + custo, 8)
            por_dia = inquilino.get("por_dia", {})
            for antigo in sorted(por_dia)[:-_DIAS_MAX]:
                por_dia.pop(antigo, None)
            escrever_json_atomico(self.caminho, dados)

    def resumo(self, tenant_id: str, *, orcamento_usd: float | None = None,
               aviso_orcamento: str = "") -> dict:
        with _LOCK:
            inquilino = self._carregar().get(tenant_id, {})
        total = {**_novo_alvo(), **inquilino.get("total", {})}
        total["custo_estimado_usd"] = round(total["custo_estimado_usd"], 6)
        parcial = total["chamadas_sem_preco"] > 0
        orcamento = {"definido": orcamento_usd is not None,
                     "usd": orcamento_usd,
                     "usado_usd": total["custo_estimado_usd"],
                     "restante_usd": None, "fracao": None,
                     "aviso": aviso_orcamento,
                     "nota": ("orçamento é informativo — nunca desativa a "
                              "verificação de contexto da mesa")}
        if orcamento_usd is not None:
            restante = orcamento_usd - total["custo_estimado_usd"]
            orcamento["restante_usd"] = round(restante, 6)
            if orcamento_usd > 0:
                orcamento["fracao"] = round(
                    min(total["custo_estimado_usd"] / orcamento_usd, 9.9999), 4)
        return {
            "total": total,
            "custo_parcial": parcial,
            "por_provedor": {p: {**_novo_alvo(), **v}
                             for p, v in inquilino.get("por_provedor", {}).items()},
            "por_dia": {d: {**_novo_alvo(), **v}
                        for d, v in sorted(inquilino.get("por_dia", {}).items())[-31:]},
            "orcamento": orcamento,
            "referencia_precos": precos.REFERENCIA,
            "moeda": "USD",
        }


__all__ = ["RegistroDeUso"]
