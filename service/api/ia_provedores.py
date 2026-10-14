"""Consulta de IA do serviço — fonte única (V8 §6).

Por que este módulo existe: os dois caminhos de consulta do serviço
(`_consultar` da IA de contexto e `_revisor_de_ia` do diário manual)
construíam o cliente passando o dict de configuração como `model` e
chamavam `cliente.complete()`/`cliente.invoke()` — métodos que NÃO existem
no `BaseLLMClient`. Toda consulta degenerava em AttributeError e a IA de
contexto nunca funcionou em produção.

O caminho correto (o mesmo da agência) é:
    cliente = create_llm_client(provider=<nome>, model=<string>, ...)
    llm = cliente.get_llm()
    resposta = llm.invoke(prompt)

Regras de segurança embutidas (V8 §6 + travas do repo):
- Credencial viaja EXPLÍCITA no kwarg `api_key`; a variável de ambiente é
  só uma janela curta sob lock, fechada assim que o cliente é construído —
  duas mesas de inquilinos diferentes nunca se cruzam.
- A URL efetiva passa pela `validate_backend_url` (trava anti-SSRF) antes
  de qualquer saída de rede.
- Erro de provedor vira tipo + status HTTP: mensagem de exceção nunca é
  repetida (pode conter corpo de resposta ou credencial).
- Tokens só quando o provedor reporta `usage_metadata`; nunca inventados.
- Fallback só entre provedores que o dono declarou; provedor sem chave é
  pulado com a verdade ("sem chave"), não com erro genérico.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable

# Serializa a janela de ambiente: provedores nativos leem a env var dentro
# de get_llm(). A janela cobre SÓ a construção — invocar o modelo (rede) fica
# fora do lock para não travar o servidor inteiro.
_IA_ENV_LOCK = threading.Lock()

# Catálogo = a definição do que é "primeira classe" no painel (V8 §6).
# rápido_padrao espelha FREE_PROVIDERS do service/app.py (há teste anti-drift).
# `cliente` é o adaptador do núcleo; provedores OpenAI-compatíveis usam o
# cliente `openai_compatible` com a URL oficial do fornecedor.
CATALOGO: list[dict] = [
    {"id": "openai", "nome": "OpenAI", "cliente": "openai",
     "chave_env": "OPENAI_API_KEY", "quick_padrao": "gpt-4o-mini",
     "url_padrao": None, "descoberta": "openai"},
    {"id": "anthropic", "nome": "Anthropic (Claude)", "cliente": "anthropic",
     "chave_env": "ANTHROPIC_API_KEY", "quick_padrao": "claude-3-5-haiku-latest",
     "url_padrao": None, "descoberta": "anthropic"},
    {"id": "google", "nome": "Google Gemini", "cliente": "google",
     "chave_env": "GOOGLE_API_KEY", "quick_padrao": "gemini-2.0-flash",
     "url_padrao": None, "descoberta": "google"},
    {"id": "deepseek", "nome": "DeepSeek", "cliente": "openai_compatible",
     "chave_env": "DEEPSEEK_API_KEY", "quick_padrao": "deepseek-chat",
     "url_padrao": "https://api.deepseek.com/v1", "descoberta": "openai"},
    {"id": "groq", "nome": "Groq", "cliente": "openai_compatible",
     "chave_env": "GROQ_API_KEY", "quick_padrao": "llama-3.1-8b-instant",
     "url_padrao": "https://api.groq.com/openai/v1", "descoberta": "openai",
     "gratuito": True},
    {"id": "mistral", "nome": "Mistral", "cliente": "openai_compatible",
     "chave_env": "MISTRAL_API_KEY", "quick_padrao": "mistral-small-latest",
     "url_padrao": "https://api.mistral.ai/v1", "descoberta": "openai",
     "gratuito": True},
    {"id": "openrouter", "nome": "OpenRouter", "cliente": "openai_compatible",
     "chave_env": "OPENROUTER_API_KEY",
     "quick_padrao": "meta-llama/llama-3.3-70b-instruct:free",
     "url_padrao": "https://openrouter.ai/api/v1", "descoberta": "openai",
     "gratuito": True},
    {"id": "openai_compatible", "nome": "Outro serviço OpenAI-compatível",
     "cliente": "openai_compatible",
     "chave_env": "OPENAI_COMPATIBLE_API_KEY",
     "quick_padrao": "", "url_padrao": None, "descoberta": "openai",
     "exige_url": True, "chave_opcional": True},
    {"id": "ollama", "nome": "Ollama (local, sem chave)", "cliente": "ollama",
     "chave_env": None, "quick_padrao": "llama3.1",
     "url_padrao": "http://localhost:11434/v1", "descoberta": "openai",
     "chave_opcional": True, "gratuito": True},
]

_POR_ID = {p["id"]: p for p in CATALOGO}


def provedor_conhecido(nome: str) -> dict | None:
    return _POR_ID.get((nome or "").strip().lower())


def _env_do_cliente(entrada: dict) -> str | None:
    """Nome da env var que o CLIENTE lê — fonte única no núcleo."""
    from tradingagents.llm_clients.api_key_env import get_api_key_env

    return get_api_key_env(entrada["cliente"])


def _candidato(entrada: dict, cfg: dict, modelo_do_painel: str) -> dict:
    chave_env = entrada["chave_env"]
    chave = str(cfg.get(chave_env) or "").strip() if chave_env else ""
    url = str(cfg.get("TRADINGAGENTS_BACKEND_URL") or "").strip() or entrada["url_padrao"]
    return {
        "painel": entrada["id"],
        "provider": entrada["cliente"],
        "modelo": modelo_do_painel or entrada["quick_padrao"],
        "chave": chave,
        "chave_env": chave_env,
        "env_cliente": _env_do_cliente(entrada),
        "url": url,
        "exige_chave": bool(chave_env) and not entrada.get("chave_opcional", False),
    }


def candidatos_llm(cfg: dict) -> list[dict]:
    """Fila de tentativas: principal do painel + fallbacks declarados.

    Ordem: provedor principal primeiro (com o modelo rápido escolhido no
    painel), depois `TRADINGAGENTS_LLM_FALLBACK` na ordem declarada. Nome
    desconhecido é ignorado — o catálogo é a definição do configurável.
    """
    ordem: list[tuple[str, str]] = []
    principal = str(cfg.get("TRADINGAGENTS_LLM_PROVIDER") or "").strip().lower()
    if principal:
        quick = str(cfg.get("TRADINGAGENTS_QUICK_THINK_LLM") or "").strip()
        ordem.append((principal, quick))
    for bruto in str(cfg.get("TRADINGAGENTS_LLM_FALLBACK") or "").split(","):
        nome = bruto.strip().lower()
        if nome:
            ordem.append((nome, ""))
    fila, visto = [], set()
    for nome, modelo in ordem:
        if nome in visto:
            continue
        visto.add(nome)
        entrada = provedor_conhecido(nome)
        if entrada is None:
            continue
        fila.append(_candidato(entrada, cfg, modelo))
    return fila


def _texto(resposta) -> str:
    conteudo = getattr(resposta, "content", None)
    if isinstance(conteudo, str):
        return conteudo
    return str(resposta)


def _extrair_uso(resposta) -> dict | None:
    uso = getattr(resposta, "usage_metadata", None)
    if not isinstance(uso, dict):
        return None
    entrada = uso.get("input_tokens")
    saida = uso.get("output_tokens")
    if entrada is None and saida is None:
        return None
    try:
        return {"entrada": int(entrada or 0), "saida": int(saida or 0)}
    except (TypeError, ValueError):
        return None


def _resumo_erro(exc: Exception) -> str:
    """Tipo + status HTTP apenas — NUNCA str(exc) (pode carregar credencial
    ou corpo de resposta)."""
    tipo = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return f"{tipo}: HTTP {status}"
    return tipo


def _invocar(candidato: dict, prompt: str,
             criar_cliente: Callable) -> tuple[str, dict | None]:
    provider, modelo = candidato["provider"], candidato["modelo"]
    if not modelo:
        raise ValueError(
            f"provedor '{candidato['painel']}' sem modelo definido — "
            "escolha um modelo na aba de configuração")
    kwargs: dict = {"provider": provider, "model": modelo}
    url = candidato["url"]
    if url:
        from tradingagents.llm_clients.backend_policy import validate_backend_url

        validate_backend_url(provider, url)  # trava anti-SSRF antes da rede
        kwargs["base_url"] = url
    if candidato["chave"]:
        kwargs["api_key"] = candidato["chave"]

    env_nome = candidato["env_cliente"]
    with _IA_ENV_LOCK:
        anterior = None
        if env_nome and candidato["chave"]:
            anterior = os.environ.get(env_nome)
            os.environ[env_nome] = candidato["chave"]
        try:
            cliente = criar_cliente(**kwargs)
            llm = cliente.get_llm()
        finally:
            if anterior is None and env_nome and candidato["chave"]:
                os.environ.pop(env_nome, None)
            elif env_nome and candidato["chave"]:
                os.environ[env_nome] = anterior
    # Rede fora do lock: a janela de ambiente já foi fechada.
    resposta = llm.invoke(prompt)
    return _texto(resposta), _extrair_uso(resposta)


def consultar_ia(cfg: dict, prompt: str, *,
                 criar_cliente: Callable | None = None) -> tuple[str, dict]:
    """Consulta o provedor do inquilino, com fallback declarado.

    Devolve (texto, meta). `meta["provedor"]` é o provedor que DE FATO
    respondeu (importa para o painel e para o registro de latência).
    Levanta RuntimeError só quando NENHUM candidato respondeu — a mensagem
    agrega os estados sem nunca citar valor de credencial.
    """
    if criar_cliente is None:
        from tradingagents.llm_clients.factory import create_llm_client

        criar_cliente = create_llm_client
    fila = candidatos_llm(cfg)
    if not fila:
        raise RuntimeError(
            "nenhum provedor de IA configurado para consulta de contexto")
    tentativas: list[dict] = []
    for candidato in fila:
        if candidato["exige_chave"] and not candidato["chave"]:
            tentativas.append({
                "provedor": candidato["painel"], "estado": "sem_chave",
                "detalhe": f"sem chave configurada ({candidato['chave_env']})"})
            continue
        try:
            texto, uso = _invocar(candidato, prompt, criar_cliente)
        except Exception as exc:
            tentativas.append({
                "provedor": candidato["painel"], "estado": "erro",
                "detalhe": _resumo_erro(exc)})
            continue
        tentativas.append({"provedor": candidato["painel"], "estado": "ok",
                           "detalhe": ""})
        return texto, {"provedor": candidato["painel"], "tokens": uso,
                       "tentativas": tentativas}
    resumo = "; ".join(
        f"{t['provedor']}: {t['estado']} ({t['detalhe']})" for t in tentativas)
    raise RuntimeError(f"nenhum provedor de IA respondeu: {resumo}")


__all__ = ["CATALOGO", "consultar_ia", "candidatos_llm", "provedor_conhecido"]
