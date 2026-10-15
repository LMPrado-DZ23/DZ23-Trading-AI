"""F8.5 — verificação de integridade dos artefatos da missão V9.

Recomputa o sha256 de cada artefato em docs/mission/evidence/v9-baseline/,
extrai cada hash de 64 hex registrado nos documentos de docs/mission/ e
cruza os dois conjuntos. Saída JSON em stdout; exit 1 se houver hash
registrado que não corresponda a nenhum arquivo real (referência órfã).

O próprio relatório, seu log e seu arquivo de invocação ficam fora do
manifesto: eles são regravados após a varredura e não podem ser
verificados contra si mesmos sem entrar em referência circular.

Somente leitura: não altera nenhum artefato.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVID = ROOT / "docs" / "mission" / "evidence" / "v9-baseline"
DOCS = ROOT / "docs" / "mission"

HEX64 = re.compile(r"\b[0-9a-f]{64}\b", re.IGNORECASE)

# Hashes registrados em docs de era V8 que NÃO são arquivos do repositório:
# são digests de binários Windows produzidos pelo CI (Setup/zip) ou agregados
# computados em memória. Verificados na época contra SHA256SUMS do CI;
# os binários não são armazenados no repo — não há arquivo local para bater.
EXTERNOS_CONHECIDOS: dict[str, str] = {
    "24b29dc1b7e1ee9ee0328211aa9a410ce43806fde9e4a866bf4e6a16019fec37":
        "CI run 38467455263 — Setup .exe (C018), binário CI não armazenado no repo",
    "865bb4e93e1d1f33916b133fcf5bd52cd6c0e1fafdeda54673e1b43202ed770c":
        "CI run 38467455263 — zip Windows x64 (C018), binário CI não armazenado no repo",
    "f40ab26db60f267e9e2f4d47042e08f1f68c331a5a899d13b4f25bb08c2f659f":
        "digest do artefato CI dz23-windows-x64 run 38437252278, não armazenado no repo",
    "bd160830a51a46222270317e932772bb82b9313054504410071d4e4848ae74de":
        "CI run 38450504893 — Setup v0.4.0 homologado (C008+C009+C010), binário CI",
    "2cd7ebc2f7d244707b8f51adc54a8d80f0e4fb8b331188f7777780ef3ab822a5":
        "CI run 38450504893 — zip Windows x64 homologado, binário CI",
    "3582b2af52bf64e837665988d46aa175893fa93486d3c1867dcbb225801b77f4":
        "agregado canônico dos 17 achados gitleaks (ordenados por fingerprint), computado em memória",
    "144de42193dfde54561a7b99bc3e9dcf8545fe818ea909ef0b768f0514045d3e":
        "FQ-2 rodada 1 — JSON interno do export Freqtrade (backtest-result-2026-10-15_11-37-48.json,"
        " 105 299 bytes), re-extraído byte-idêntico com zipfile; artefato do spike isolado"
        " (../freqtrade-spike/), fora do repo — ver fq2-round1-veredito.md",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
    excluidos: set[Path] = set()
    if out_path is not None:
        excluidos.add(out_path)
        excluidos.add(out_path.with_suffix(".log"))
        excluidos.add(out_path.with_name(out_path.stem + "-invocation.json"))
    manifest: dict[str, str] = {}
    for path in sorted(EVID.rglob("*")):
        if path.is_file() and path.resolve() not in excluidos:
            manifest[str(path.relative_to(ROOT)).replace("\\", "/")] = sha256(path)

    by_hash: dict[str, list[str]] = {}
    for rel, digest in manifest.items():
        by_hash.setdefault(digest, []).append(rel)

    referenced: dict[str, list[dict[str, str]]] = {}
    for doc in sorted(DOCS.rglob("*.md")):
        text = doc.read_text(encoding="utf-8", errors="replace")
        rel_doc = str(doc.relative_to(ROOT)).replace("\\", "/")
        for line in text.splitlines():
            for match in HEX64.finditer(line):
                token = match.group(0).lower()
                referenced.setdefault(token, []).append(
                    {"doc": rel_doc, "contexto": line.strip()[:180]}
                )

    checks = []
    orfas = []
    externos = []
    for token, refs in sorted(referenced.items()):
        files = by_hash.get(token, [])
        if files:
            verdict = "OK"
        elif token in EXTERNOS_CONHECIDOS:
            verdict = "EXTERNO_CONHECIDO"
            externos.append(token)
        else:
            verdict = "ORFA"
            orfas.append(token)
        checks.append({"sha256": token, "veredito": verdict, "arquivos": files,
                       "nota": EXTERNOS_CONHECIDOS.get(token, ""),
                       "citado_em": [{"doc": d, "contexto": c} for d, c in
                                     {(r["doc"], r["contexto"]) for r in refs}]})

    citados = {c["sha256"] for c in checks if c["veredito"] == "OK"}
    nao_citados = sorted(
        rel for rel, digest in manifest.items() if digest not in citados
    )

    relatorio = {
        "ferramenta": "tools/v9_f8_integridade.py",
        "head": "2b7754a94cc1e4233ad95ac6fa377be4078e5055",
        "artefatos_no_manifesto": len(manifest),
        "hashes_registrados_em_docs": len(referenced),
        "hashes_verificados_ok": sum(1 for c in checks if c["veredito"] == "OK"),
        "hashes_externos_conhecidos": externos,
        "hashes_orfaos": orfas,
        "checks": checks,
        "manifesto": manifest,
        "artefatos_sem_hash_citada": nao_citados,
    }
    json.dump(relatorio, sys.stdout, ensure_ascii=False, indent=1, sort_keys=True)
    sys.stdout.write("\n")
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(
            json.dumps(relatorio, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 1 if orfas else 0


def cited_hashes_pending(manifest: dict[str, str], referenced: dict[str, list]) -> set[str]:
    return {t for t in referenced if t in set(manifest.values())}


if __name__ == "__main__":
    raise SystemExit(main())
