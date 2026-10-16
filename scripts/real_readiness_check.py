"""Preflight remoto da preparação REAL, sem executar ação de escrita."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def avaliar(payload: dict, alvo: str) -> dict:
    """Avalia somente o contrato público; payload incompleto falha fechado."""
    if not isinstance(payload, dict):
        payload = {}
    dinheiro = payload.get("dinheiro_real")
    dinheiro = dinheiro if isinstance(dinheiro, dict) else {}
    estado = dinheiro.get("estado_de_liberacao")
    estado = estado if isinstance(estado, dict) else {}
    invariantes = payload.get("invariantes")
    invariantes = invariantes if isinstance(invariantes, dict) else {}
    prerequisitos = estado.get("pre_requisitos_sem_selo_concluidos") is True
    selo = invariantes.get("selo_de_codigo_live") is True
    envio = invariantes.get("envio_real_habilitado") is True
    pode = dinheiro.get("pode_operar_real") is True

    if alvo == "preparacao":
        ok = bool(
            prerequisitos
            and estado.get("estado") == "aguardando_liberacao_de_codigo"
            and not selo and not envio and not pode)
        resultado = "preparado_sem_live" if ok else "preparacao_pendente"
    elif alvo == "live":
        ok = bool(
            prerequisitos and estado.get("estado") == "liberado"
            and selo and envio and pode)
        resultado = "live_liberado" if ok else "live_bloqueado"
    else:
        raise ValueError(f"alvo desconhecido: {alvo}")
    return {
        "ok": ok,
        "alvo": alvo,
        "resultado": resultado,
        "estado": estado.get("estado", "indisponivel"),
        "pre_requisitos_sem_selo_concluidos": prerequisitos,
        "selo_de_codigo_live": selo,
        "envio_real_habilitado": envio,
    }


def _get_json(url: str, token: str | None, timeout: float) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError(f"resposta inválida de {url}")
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Valida a prontidão REAL sem enviar ordens nem mudar estado")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--target", choices=("preparacao", "live"),
                        default="preparacao")
    parser.add_argument("--token-env", default="ACCESS_PASSWORD",
                        help="nome da variável que contém o token; nunca o valor")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    token = os.environ.get(args.token_env, "").strip()
    if not token:
        print(f"token ausente na variável {args.token_env}", file=sys.stderr)
        return 2
    base = args.base_url.rstrip("/")
    try:
        health = _get_json(base + "/healthz", None, args.timeout)
        readiness = _get_json(
            base + "/v1/operational-readiness", token, args.timeout)
        avaliacao = avaliar(readiness, args.target)
        relatorio = {
            "schema": "dz23.real-readiness-preflight/1",
            "base_url": base,
            "health": health,
            "avaliacao": avaliacao,
            "operational_readiness": readiness,
        }
    except (OSError, ValueError, json.JSONDecodeError,
            urllib.error.URLError) as exc:
        print(f"preflight indisponível: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2

    texto = json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(texto + "\n", encoding="utf-8")
    print(texto)
    return 0 if avaliacao["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
