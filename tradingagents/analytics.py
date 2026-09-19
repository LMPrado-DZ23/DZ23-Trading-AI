"""Analytics de conversão SEM PII.

Conta apenas EVENTOS anônimos (nome + origem), de uma lista fixa. Não guarda
IP, e-mail, user-agent, id de usuário nem qualquer identificador. Serve para
saber "quantos cliques em Solicitar acesso à beta", não "quem clicou".

DECISÃO ASSUMIDA (A): agregado BEST-EFFORT e NÃO-DURÁVEL — contadores em
memória do processo. Reinício zera; múltiplas instâncias não somam. É um
sinal de tendência, não fonte contábil. Por ser efêmero e agregado, não vira
armazenamento de PII e o teto de chaves mantém a memória finita.
"""
from __future__ import annotations

import threading
from collections import Counter

# Lista FIXA de eventos aceitos. Nome fora daqui é ignorado — evita virar um
# canal de dado arbitrário (e potencial PII) enviado pelo cliente.
EVENTOS = frozenset({
    "page_view", "beta_request", "pricing_view", "cta_click",
    "login_view", "signup_view",
})
_ORIGEM_MAX = 32
# Teto de pares (evento, origem) distintos guardados. Origem é rótulo livre do
# cliente; sem teto, mandar origens sempre novas cresceria a memória sem limite.
# Atingido o teto, origens novas dobram em 'other' — chaves permanecem finitas.
_MAX_CHAVES = 512


class ConversionCounter:
    """Contadores em memória, thread-safe. Zero PII."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Counter = Counter()

    @staticmethod
    def _origem_limpa(origem: str) -> str:
        # Só rótulos curtos e simples de origem (ex.: 'hero', 'pricing').
        o = "".join(c for c in str(origem or "") if c.isalnum() or c in "-_")
        return o[:_ORIGEM_MAX] or "none"

    def record(self, event: str, origem: str = "none") -> bool:
        if event not in EVENTOS:
            return False
        chave = (event, self._origem_limpa(origem))
        with self._lock:
            # Teto de chaves: origem nova depois do limite vira 'other'. Evita
            # crescimento de memória sem limite por origens arbitrárias.
            if chave not in self._counts and len(self._counts) >= _MAX_CHAVES:
                chave = (event, "other")
            self._counts[chave] += 1
        return True

    def summary(self) -> dict:
        with self._lock:
            por_evento: Counter = Counter()
            detalhado = {}
            for (ev, org), n in self._counts.items():
                por_evento[ev] += n
                detalhado[f"{ev}:{org}"] = n
            return {"por_evento": dict(por_evento), "detalhado": detalhado,
                    "total": sum(por_evento.values())}


__all__ = ["EVENTOS", "ConversionCounter"]
