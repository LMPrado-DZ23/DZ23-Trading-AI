"""Sonda Windows: abertura do estado atômico sob escrita contínua (V9-TEST-03).

Mede a semântica do Windows que invalidava a prova de leitura concorrente do
teste `tests/test_escrita_atomica.py`: abrir o destino no instante do
`os.replace` pode ser NEGADO pelo SO (PermissionError / sharing violation)
sem que o arquivo esteja quebrado ou ausente. A sonda quantifica:

1. leitor ingênuo (como o teste antigo): quantas aberturas o SO nega, se
   alguma leitura observa JSON quebrado ou arquivo ausente, e se uma nova
   tentativa (imediata e após 1 ms) devolve o documento COMPLETO;
2. leitor de produção (`ArmazemDeEstado.carregar`): quantas exceções escapam
   sob a mesma disputa de escrita (`salvar` contínuo).

Executada FORA do executor hermético de propósito (o runner envolve apenas
pytest); sem rede, sem credenciais, somente diretórios temporários.
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

from tradingagents.desk.storage import ArmazemDeEstado, escrever_json_atomico


def _recupera(alvo: Path, local: Counter, espera_s: float) -> bool:
    """Nova tentativa de leitura após negação do SO; mede o que se observa."""
    if espera_s:
        time.sleep(espera_s)
    try:
        json.loads(alvo.read_text(encoding="utf-8"))
        return True
    except PermissionError:
        return False
    except ValueError:
        local["json_quebrado_pos_negacao"] += 1
        return False
    except FileNotFoundError:
        local["arquivo_ausente_pos_negacao"] += 1
        return False


def _fase_leitor_ingenuo(diretorio: Path, ciclos: int) -> dict:
    alvo = diretorio / "estado.json"
    escrever_json_atomico(alvo, {"inicial": True})
    parar = threading.Event()
    contagem: Counter[str] = Counter()
    travas = threading.Lock()

    def ler() -> None:
        local: Counter[str] = Counter()
        while not parar.is_set():
            try:
                json.loads(alvo.read_text(encoding="utf-8"))
                local["ok"] += 1
            except ValueError:
                local["json_quebrado"] += 1
            except FileNotFoundError:
                local["arquivo_ausente"] += 1
            except PermissionError:
                local["negada_pelo_so"] += 1
                if _recupera(alvo, local, espera_s=0.0):
                    local["recuperou_imediato"] += 1
                elif _recupera(alvo, local, espera_s=0.001):
                    local["recuperou_apos_1ms"] += 1
                else:
                    local["nao_recuperou_2_tentativas"] += 1
            except OSError as exc:
                local[f"outro_os:{type(exc).__name__}"] += 1
        with travas:
            contagem.update(local)

    t = threading.Thread(target=ler, daemon=True)
    t.start()
    erros_escrita = 0
    for i in range(ciclos):
        try:
            escrever_json_atomico(alvo, {"ciclo": i, "dados": "x" * 500})
        except Exception:
            erros_escrita += 1
    parar.set()
    t.join(timeout=10)
    final = json.loads(alvo.read_text(encoding="utf-8"))
    return {
        "ciclos": ciclos,
        "leitor": dict(contagem),
        "erros_de_escrita": erros_escrita,
        "thread_encerrou": not t.is_alive(),
        "final_valido": final.get("ciclo") == ciclos - 1,
    }


def _fase_carregar_producao(diretorio: Path, ciclos: int) -> dict:
    armazem = ArmazemDeEstado(None, diretorio=diretorio)
    parar = threading.Event()
    contagem: Counter[str] = Counter()
    travas = threading.Lock()

    def ler() -> None:
        local: Counter[str] = Counter()
        while not parar.is_set():
            try:
                doc = armazem.carregar("tenant", "mesa")
                local["ok" if doc else "vazio"] += 1
            except Exception as exc:
                local[f"escapou:{type(exc).__name__}"] += 1
        with travas:
            contagem.update(local)

    t = threading.Thread(target=ler, daemon=True)
    t.start()
    erros = 0
    for i in range(ciclos):
        try:
            armazem.salvar("tenant", "mesa", {"ciclo": i})
        except Exception:
            erros += 1
    parar.set()
    t.join(timeout=10)
    return {
        "ciclos": ciclos,
        "carregar": dict(contagem),
        "erros_de_salvar": erros,
        "thread_encerrou": not t.is_alive(),
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="dz23-sonda-escrita-") as tmp:
        base = Path(tmp)
        fase1 = _fase_leitor_ingenuo(base / "fase1", 500)
        fase2 = _fase_carregar_producao(base / "fase2", 500)
    print("SONDA V9-TEST-03 — escrita atômica x leitura concorrente (Windows/NTFS)")
    print(json.dumps({"fase1_leitor_ingenuo": fase1}, ensure_ascii=False))
    print(json.dumps({"fase2_carregar_producao": fase2}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
