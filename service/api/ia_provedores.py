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
import time
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
     "url_padrao": None, "url_sonda": "https://api.openai.com/v1",
     "descoberta": "openai"},
    {"id": "anthropic", "nome": "Anthropic (Claude)", "cliente": "anthropic",
     "chave_env": "ANTHROPIC_API_KEY", "quick_padrao": "claude-3-5-haiku-latest",
     "url_padrao": None, "url_sonda": "https://api.anthropic.com",
     "descoberta": "anthropic"},
    {"id": "google", "nome": "Google Gemini", "cliente": "google",
     "chave_env": "GOOGLE_API_KEY", "quick_padrao": "gemini-2.0-flash",
     "url_padrao": None,
     "url_sonda": "https://generativelanguage.googleapis.com",
     "descoberta": "google"},
    {"id": "deepseek", "nome": "DeepSeek", "cliente": "openai_compatible",
     "chave_env": "DEEPSEEK_API_KEY", "quick_padrao": "deepseek-chat",
     "url_padrao": "https://api.deepseek.com/v1",
     "url_sonda": "https://api.deepseek.com/v1", "descoberta": "openai"},
    {"id": "groq", "nome": "Groq", "cliente": "openai_compatible",
     "chave_env": "GROQ_API_KEY", "quick_padrao": "llama-3.1-8b-instant",
     "url_padrao": "https://api.groq.com/openai/v1",
     "url_sonda": "https://api.groq.com/openai/v1", "descoberta": "openai",
     "gratuito": True},
    {"id": "mistral", "nome": "Mistral", "cliente": "openai_compatible",
     "chave_env": "MISTRAL_API_KEY", "quick_padrao": "mistral-small-latest",
     "url_padrao": "https://api.mistral.ai/v1",
     "url_sonda": "https://api.mistral.ai/v1", "descoberta": "openai",
     "gratuito": True},
    {"id": "openrouter", "nome": "OpenRouter", "cliente": "openai_compatible",
     "chave_env": "OPENROUTER_API_KEY",
     "quick_padrao": "meta-llama/llama-3.3-70b-instruct:free",
     "url_padrao": "https://openrouter.ai/api/v1",
     "url_sonda": "https://openrouter.ai/api/v1", "descoberta": "openai",
     "gratuito": True},
    {"id": "openai_compatible", "nome": "Outro serviço OpenAI-compatível",
     "cliente": "openai_compatible",
     "chave_env": "OPENAI_COMPATIBLE_API_KEY",
     "quick_padrao": "", "url_padrao": None, "url_sonda": None,
     "descoberta": "openai",
     "exige_url": True, "chave_opcional": True},
    {"id": "ollama", "nome": "Ollama (local, sem chave)", "cliente": "ollama",
     "chave_env": None, "quick_padrao": "llama3.1",
     "url_padrao": "http://localhost:11434/v1",
     "url_sonda": "http://localhost:11434/v1", "descoberta": "openai",
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
        return texto, {"provedor": candidato["painel"],
                       "modelo": candidato["modelo"],
                       "tokens": uso,
                       "tentativas": tentativas}
    resumo = "; ".join(
        f"{t['provedor']}: {t['estado']} ({t['detalhe']})" for t in tentativas)
    raise RuntimeError(f"nenhum provedor de IA respondeu: {resumo}")


__all__ = ["CATALOGO", "consultar_ia", "candidatos_llm", "provedor_conhecido",
           "testar_provedor", "descobrir_modelos"]


# --- Teste de autenticação e descoberta de modelos (V8 §6) ----------------
# A sondagem fala o endpoint de modelos OFICIAL de cada provedor — é o teste
# de autenticação mais barato e não gasta tokens de inferência. Regras:
# - sem chave configurada não há saída de rede: o estado é "sem_chave";
# - a URL efetiva passa pela trava anti-SSRF antes do primeiro byte;
# - erro devolve estado classificado + status quando existe; NUNCA o corpo
#   da resposta nem a chave (um 401 com eco do Authorization viraria vazamento).

_TIMEOUT_SONDAGEM = 8.0

_CAMINHO_DESCOBERTA = {
    "openai": "/models",
    "anthropic": "/v1/models",
    "google": "/v1beta/models",
}


def _chave_do_provedor(cfg: dict, entrada: dict) -> str:
    chave_env = entrada["chave_env"]
    return str(cfg.get(chave_env) or "").strip() if chave_env else ""


def _url_de_sondagem(cfg: dict, entrada: dict) -> str:
    return (str(cfg.get("TRADINGAGENTS_BACKEND_URL") or "").strip()
            or entrada["url_sonda"] or "")


def _cabecalhos_sondagem(entrada: dict, chave: str) -> dict:
    if not chave:
        return {}
    descoberta = entrada["descoberta"]
    if descoberta == "anthropic":
        return {"x-api-key": chave, "anthropic-version": "2023-06-01"}
    if descoberta == "google":
        return {"x-goog-api-key": chave}
    return {"Authorization": f"Bearer {chave}"}


def _ids_dos_modelos(descoberta: str, dados) -> list[str]:
    if not isinstance(dados, dict):
        raise TypeError("corpo não é objeto JSON")
    if descoberta == "google":
        itens = dados.get("models") or []
        ids = [str(m.get("name") or "").removeprefix("models/")
               for m in itens if isinstance(m, dict)]
    else:
        itens = dados.get("data") or []
        ids = [str(m.get("id") or "") for m in itens if isinstance(m, dict)]
    return [i for i in ids if i]


def _classificar_http(status: int) -> str:
    if status == 401:
        return "chave_rejeitada"
    if status == 403:
        return "sem_permissao"
    if status == 429:
        return "limite_de_taxa"
    return f"erro_http_{status}"


def _limites_do_cabecalho(cabecalhos) -> dict:
    return {k: v for k, v in cabecalhos.items()
            if k.lower().startswith("x-ratelimit-") or k.lower() == "retry-after"}


def _sondar(cfg: dict, entrada: dict, *, transport=None) -> dict:
    """GET no endpoint de modelos do provedor. Sem chave (quando exigida)
    não há rede — o teste não pode 'passar' sem credencial de verdade."""
    import httpx

    from tradingagents.llm_clients.backend_policy import (
        UntrustedBackendError,
        validate_backend_url,
    )

    chave = _chave_do_provedor(cfg, entrada)
    if entrada["chave_env"] and not chave and not entrada.get("chave_opcional"):
        return {"ok": False, "estado": "sem_chave",
                "detalhe": f"sem chave salva para o provedor ({entrada['chave_env']})"}
    url = _url_de_sondagem(cfg, entrada)
    if not url:
        return {"ok": False, "estado": "sem_url",
                "detalhe": "informe o Endereço do servidor de IA na configuração"}
    try:
        validate_backend_url(entrada["cliente"], url)
    except UntrustedBackendError as exc:
        return {"ok": False, "estado": "endereco_recusado",
                "detalhe": _resumo_erro(exc)}
    destino = url.rstrip("/") + _CAMINHO_DESCOBERTA.get(entrada["descoberta"], "/models")
    inicio = time.perf_counter()

    def _ms() -> float:
        return round((time.perf_counter() - inicio) * 1000.0, 1)

    try:
        with httpx.Client(transport=transport, timeout=_TIMEOUT_SONDAGEM,
                          follow_redirects=False) as cli:
            resposta = cli.get(destino, headers=_cabecalhos_sondagem(entrada, chave))
    except httpx.TimeoutException:
        return {"ok": False, "estado": "tempo_esgotado", "latencia_ms": _ms()}
    except httpx.HTTPError:
        return {"ok": False, "estado": "sem_conexao", "latencia_ms": _ms()}
    except Exception as exc:  # transporte inesperado: reporta o tipo, sem corpo
        return {"ok": False, "estado": "erro", "detalhe": _resumo_erro(exc),
                "latencia_ms": _ms()}
    limites = _limites_do_cabecalho(resposta.headers)
    if resposta.status_code != 200:
        return {"ok": False, "estado": _classificar_http(resposta.status_code),
                "status": resposta.status_code, "latencia_ms": _ms(),
                "limites": limites}
    try:
        modelos = _ids_dos_modelos(entrada["descoberta"], resposta.json())
    except (ValueError, TypeError):
        return {"ok": False, "estado": "resposta_ilegivel",
                "status": resposta.status_code, "latencia_ms": _ms(),
                "limites": limites}
    return {"ok": True, "estado": "ok", "status": resposta.status_code,
            "latencia_ms": _ms(), "limites": limites, "modelos": modelos}


def testar_provedor(cfg: dict, nome: str, *, transport=None) -> dict:
    """Testa a autenticação do provedor: chave aceita? qual latência? quais
    limites? Quanto custa: uma chamada ao endpoint de modelos, zero tokens."""
    entrada = provedor_conhecido(nome)
    if entrada is None:
        return {"ok": False, "provedor": str(nome or "")[:40],
                "estado": "desconhecido",
                "detalhe": "provedor não está no catálogo"}
    relatorio = {**_sondar(cfg, entrada, transport=transport),
                 "provedor": entrada["id"], "nome": entrada["nome"]}
    modelos = relatorio.get("modelos")
    if isinstance(modelos, list):
        relatorio["total_modelos"] = len(modelos)
        relatorio["modelos"] = modelos[:12]  # amostra; lista completa em /modelos
    return relatorio


def descobrir_modelos(cfg: dict, nome: str, *, transport=None) -> dict:
    """Lista os modelos disponíveis pela própria API quando a chave permite;
    sem chave ou com falha, devolve o padrão do catálogo com a fonte HONESTA
    ('catalogo') — nunca finge uma descoberta ao vivo."""
    entrada = provedor_conhecido(nome)
    if entrada is None:
        return {"ok": False, "provedor": str(nome or "")[:40], "fonte": "catalogo",
                "estado": "desconhecido", "modelos": [],
                "detalhe": "provedor não está no catálogo"}
    relatorio = _sondar(cfg, entrada, transport=transport)
    base = {"provedor": entrada["id"], "nome": entrada["nome"],
            "latencia_ms": relatorio.get("latencia_ms")}
    if relatorio.get("ok"):
        return {**base, "ok": True, "fonte": "api", "estado": "ok",
                "modelos": relatorio["modelos"],
                "limites": relatorio.get("limites", {})}
    padrao = entrada["quick_padrao"]
    return {**base, "ok": False, "fonte": "catalogo",
            "estado": relatorio.get("estado", "erro"),
            "detalhe": relatorio.get("detalhe", ""),
            "modelos": [padrao] if padrao else []}
