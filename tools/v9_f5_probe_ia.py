"""F5 — homologação REAL da autenticação dos provedores de IA (MISSÃO V9).

Roda DELIBERADAMENTE fora do executor hermético `tools/v9_pytest.py`:
este script precisa de rede real e das chaves já instaladas no ambiente do
dono. Ele usa o CAMINHO DO PRÓPRIO PRODUTO —
`service.api.ia_provedores.testar_provedor(cfg, nome)` — que sonda o
endpoint OFICIAL de modelos de cada provedor: custo ZERO de inferência
(uma chamada GET, nenhum token).

Garantias de segurança deste script:
- NUNCA imprime valor de credencial. Antes de emitir o JSON, verifica se
  qualquer valor das env vars de chave aparece no texto serializado; se
  aparecer, ABORTA sem imprimir (exit 2).
- Respeita o desenho do produto: provedor sem chave e obrigatória tem
  estado `sem_chave` SEM saída de rede (fail-closed); URL passa pela trava
  anti-SSRF antes do primeiro byte.
- Não cria ordens, não consome inferência, não liga modo pago: apenas o
  GET de listagem de modelos que o painel usa no botão "Testar".

Provedores sondados: todos do CATALOGO (`ia_provedores.py`). O que vier de
estado é evidência honesta: `ok` = serviço real aceitou a chave; `sem_chave`
= sem credencial no ambiente (sem rede); `sem_conexao` = host inalcançável
(ex.: Ollama local não instalado); `sem_url` = exige endereço não informado.

Saída: um JSON único em stdout (capturado para
docs/mission/evidence/v9-baseline/f5-probe-ia-auth.json).
Exit 0 = o JSON foi produzido (estados não-ok são evidência, não erro).
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
from datetime import datetime, timezone


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Env vars de chave que NUNCA podem aparecer no texto emitido. Inclui as do
# catálogo do painel e as alternativas reconhecidas do núcleo llm_clients.
_ENV_VARS_CHAVE = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
    "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "GROQ_API_KEY",
    "MISTRAL_API_KEY", "OPENROUTER_API_KEY", "OPENAI_COMPATIBLE_API_KEY",
    "XAI_API_KEY", "ALPHA_VANTAGE_API_KEY", "FRED_API_KEY",
)


def _valores_sensiveis() -> list[str]:
    valores = []
    for nome in _ENV_VARS_CHAVE:
        valor = os.environ.get(nome) or ""
        if len(valor) >= 8:  # valores curtos não são chave; evita falso positivo
            valores.append(valor)
    return valores


def _probe_provedor(nome: str, cfg: dict) -> dict:
    from service.api.ia_provedores import provedor_conhecido, testar_provedor

    entrada = provedor_conhecido(nome)
    chave_env = entrada["chave_env"] if entrada else None
    relatorio = {
        "provedor": nome,
        "env_var": chave_env,
        "env_set": bool(chave_env and os.environ.get(chave_env)),
        "credenciais_impressas": False,
        "ordens_enviadas": False,
        "inferencia_executada": False,
    }
    inicio = time.time()
    try:
        resultado = testar_provedor(cfg, nome, transport=None)  # httpx REAL
    except Exception as exc:
        relatorio.update({"ok": False, "estado": "excecao",
                          "detalhe": f"{type(exc).__name__}: {exc}"[:200]})
    else:
        # Só campos factuais; `modelos` truncado a uma amostra de IDs.
        amostra = resultado.get("modelos") or []
        relatorio.update({
            "ok": bool(resultado.get("ok")),
            "estado": resultado.get("estado"),
            "status_http": resultado.get("status"),
            "latencia_ms": resultado.get("latencia_ms"),
            "total_modelos": resultado.get("total_modelos"),
            "amostra_modelos": amostra[:5],
            "limites": resultado.get("limites") or {},
        })
        if resultado.get("detalhe"):
            relatorio["detalhe"] = str(resultado["detalhe"])[:200]
    relatorio["duracao_s"] = round(time.time() - inicio, 2)
    return relatorio


def main() -> int:
    from service.api.ia_provedores import CATALOGO

    # cfg do painel: chave lida da env var que o PRÓPRIO catálogo declara.
    # A chave viaja dentro do processo (o painel faz o mesmo); nada é ecoado.
    cfg: dict = {}
    for entrada in CATALOGO:
        nome_env = entrada["chave_env"]
        if nome_env:
            valor = os.environ.get(nome_env) or ""
            if valor:
                cfg[nome_env] = valor

    evidencias = {
        "missao": "V9-F5 — provedores de IA autorizados",
        "quando_utc": _iso_now(),
        "python": sys.version.split()[0],
        "plataforma": platform.platform(aliased=True)[:120],
        "caminho_no_produto": "service.api.ia_provedores.testar_provedor",
        "custo": "zero tokens (GET no endpoint de modelos; nenhuma inferência)",
        "observacao": ("Execução DELIBERADA fora do executor hermético: "
                       "rede real com as chaves do dono já presentes no "
                       "ambiente; nenhum valor de chave é registrado."),
        "provedores": [],
    }
    for entrada in CATALOGO:
        evidencias["provedores"].append(
            _probe_provedor(entrada["id"], cfg))

    texto = json.dumps(evidencias, ensure_ascii=False, indent=2)

    # Autoverificação anti-vazamento: aborta SEM imprimir se qualquer valor
    # de chave presente no ambiente aparecer no texto final.
    for valor in _valores_sensiveis():
        if valor in texto:
            sys.stderr.write(
                "ABORTADO: valor de credencial detectado no texto de saída; "
                "nada foi impresso.\n")
            return 2

    print(texto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
