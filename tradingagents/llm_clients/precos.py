"""Tabela de preços de REFERÊNCIA dos provedores de IA (V8 §6).

Regras de honestidade (o painel mostra custo estimado, não conta de luz):
- Preço é referência pública, por 1M de tokens, [entrada, saída], em USD.
  Listas de preço mudam: a data de referência acompanha a tabela e o painel
  diz que é estimativa — conferir antes de qualquer decisão de custo.
- Modelo desconhecido → None. NUNCA chutar um preço para "fechar a conta".
- Serviços sem custo por token (Ollama local, variantes ":free") → 0.0 e são
  marcados como gratuitos, não "sem preço".

A busca por modelo é por PREFIXO MAIS LONGO: "gpt-4o-mini-2024-07-18" casa
com "gpt-4o-mini" (e não com "gpt-4o"), que é o comportamento correto para
nomes versionados.
"""

from __future__ import annotations

REFERENCIA = "2026-10"

# USD por 1M tokens: modelo -> (entrada, saída)
POR_PROVEDOR: dict[str, dict[str, tuple[float, float]]] = {
    "openai": {
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4o": (2.50, 10.00),
        "gpt-4.1-mini": (0.40, 1.60),
        "gpt-4.1": (2.00, 8.00),
        "o3-mini": (1.10, 4.40),
        "o4-mini": (1.10, 4.40),
    },
    "anthropic": {
        "claude-3-5-haiku": (0.80, 4.00),
        "claude-3-5-sonnet": (3.00, 15.00),
        "claude-3-7-sonnet": (3.00, 15.00),
        "claude-sonnet-4": (3.00, 15.00),
        "claude-opus-4": (15.00, 75.00),
    },
    "google": {
        "gemini-2.0-flash-lite": (0.075, 0.30),
        "gemini-2.0-flash": (0.10, 0.40),
        "gemini-1.5-flash": (0.075, 0.30),
        "gemini-2.5-flash": (0.30, 2.50),
        "gemini-2.5-pro": (1.25, 10.00),
    },
    "deepseek": {
        "deepseek-chat": (0.27, 1.10),
        "deepseek-reasoner": (0.55, 2.19),
    },
    "groq": {
        # Lista pública do provedor, docs oficiais (console.groq.com/docs)
        # consultadas ao vivo em 2026-10-14: os llama/gemma/mixtral foram
        # descontinuados e respondem 404 — manter preço de modelo morto
        # seria referência falsa. Cached input do 120b (0.075) é camada à
        # parte; a tabela usa o input padrão.
        "openai/gpt-oss-20b": (0.075, 0.30),
        "openai/gpt-oss-120b": (0.15, 0.60),
    },
    "mistral": {
        "mistral-small-latest": (0.10, 0.30),
        "mistral-large-latest": (2.00, 6.00),
        "open-mistral-nemo": (0.15, 0.15),
    },
    # OpenRouter: os IDs pagos mudam de preço por rota; só o sufixo ":free"
    # tem preço seguro (zero). Os demais ficam "sem preço de referência".
    "openrouter": {},
    # Serviços locais/sem cobrança por token.
    "ollama": {},
}

# Provedores em que toda chamada é gratuita por desenho (local/sem conta).
GRATUITOS = frozenset({"ollama"})

# OpenAI-compatível genérico pode apontar para qualquer serviço: preço
# desconhecido por construção — nunca inventar.
_PREFIXO_GRATUITO = ":free"


def preco(provedor: str, modelo: str) -> tuple[float, float] | None:
    """(entrada, saída) USD/1M ou None quando não há referência. Ollama e
    variantes ':free' devolvem (0.0, 0.0) — gratuito é diferente de incógnito."""
    provedor = (provedor or "").strip().lower()
    modelo = (modelo or "").strip().lower()
    if provedor in GRATUITOS:
        return (0.0, 0.0)
    if provedor == "openrouter" and modelo.endswith(_PREFIXO_GRATUITO):
        return (0.0, 0.0)
    tabela = POR_PROVEDOR.get(provedor)
    if not tabela or not modelo:
        return None
    for nome in sorted(tabela, key=len, reverse=True):  # prefixo mais longo
        if modelo.startswith(nome):
            return tabela[nome]
    return None


def custo_estimado(provedor: str, modelo: str,
                   tokens_entrada: int, tokens_saida: int) -> float | None:
    """Custo estimado em USD. None quando não há preço de referência."""
    p = preco(provedor, modelo)
    if p is None:
        return None
    return (max(0, tokens_entrada) * p[0] + max(0, tokens_saida) * p[1]) / 1_000_000
