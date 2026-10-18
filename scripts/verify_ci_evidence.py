"""Valida que documentos atuais só citem runs de CI realmente bem-sucedidos.

Cada alegação vigente fica em ``docs/finalization/CI-EVIDENCE.json``. O
arquivo registra apenas o ID público do run e o SHA já publicado; a conclusão
é consultada da API do GitHub durante a CI, para que um texto não consiga se
autodeclarar verde.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCHEMA = "dz23.ci-evidence/1"
DEFAULT_EVIDENCE = Path("docs/finalization/CI-EVIDENCE.json")
JsonOpener = Callable[[str], dict[str, Any]]


def load_evidence(path: Path) -> dict[str, Any]:
    """Lê o manifesto estrito; formato ambíguo não vira evidência."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"não foi possível ler evidência: {type(exc).__name__}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ValueError("schema de evidência inválido")
    claims = payload.get("claims")
    if not isinstance(claims, list):
        raise ValueError("claims deve ser uma lista")
    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError("claim inválido")
        run_id = claim.get("run_id")
        head_sha = claim.get("head_sha")
        if (not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0
                or not isinstance(head_sha, str) or len(head_sha) != 40):
            raise ValueError("claim precisa de run_id e head_sha válidos")
    return payload


def _github_json(url: str, token: str) -> dict[str, Any]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ValueError(f"consulta da evidência falhou: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise ValueError("API devolveu evidência inválida")
    return payload


def validate_claims(evidence: dict[str, Any], repository: str, token: str,
                    *, opener: JsonOpener | None = None) -> list[str]:
    """Retorna erros sem incluir token ou corpos de resposta externos."""
    if not repository or "/" not in repository:
        return ["repositório inválido"]
    claims = evidence.get("claims", [])
    if not claims:
        return []
    fetch = opener or (lambda url: _github_json(url, token))
    errors = []
    for claim in claims:
        run_id = claim["run_id"]
        expected_sha = claim["head_sha"]
        try:
            run = fetch(f"https://api.github.com/repos/{repository}/actions/runs/{run_id}")
        except ValueError as exc:
            errors.append(f"run {run_id}: {exc}")
            continue
        conclusion = run.get("conclusion")
        actual_sha = run.get("head_sha")
        if conclusion != "success":
            errors.append(f"run {run_id}: conclusion={conclusion!s}, esperada success")
        if actual_sha != expected_sha:
            errors.append(f"run {run_id}: SHA diferente da evidência")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="owner/repo")
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--token-env", default="GITHUB_TOKEN",
                        help="nome da variável de token; nunca seu valor")
    args = parser.parse_args(argv)
    try:
        evidence = load_evidence(args.evidence)
        errors = validate_claims(
            evidence, args.repository, os.environ.get(args.token_env, ""))
    except ValueError as exc:
        print(f"evidência de CI inválida: {exc}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"evidência de CI inválida: {error}", file=sys.stderr)
        return 2
    print(f"evidência de CI válida: {len(evidence['claims'])} claim(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
