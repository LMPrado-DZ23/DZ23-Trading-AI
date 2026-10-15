"""V9-F1.4 — posse exclusiva entre DOIS processos reais (backend arquivo).

O teste unitário de `test_modo_da_mesa` disputa a posse com threads, que
compartilham o `_WRITE_LOCK` do módulo. Aqui a disputa é entre processos de
SO de verdade — o par "PC e Oracle" que o módulo promete cobrir ("com
backend arquivo vale para os processos desta máquina"). O lock de thread
NÃO atravessa processos: sem uma trava de SO, o perdedor pode gravar DEPOIS
da leitura-de-conferência do vencedor e ambos terminam com `assumir()==True`
— dois executores para a mesma mesa.

Cenário 1 (determinístico): P1 é atrasado artificialmente ENTRE a leitura e
a escrita da posse (a janela real entre processos, só alargada); P2 só
começa quando P1 já está na janela. A invariante: exatamente UM vencedor, e
o documento durável pertence a ele.

Cenário 2 (sequencial): rival vivo é recusado por outro processo; morto o
dono, o TTL vence sozinho e o próximo assume (sem trava obsoleta).

Cenário 3 (concorrência natural): partidas simultâneas, sem hooks — um
único vencedor por rodada.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

_SCRIPT = r'''
import json, os, sys, time

cfg = json.loads(sys.argv[1])
sys.path.insert(0, cfg["raiz"])
from tradingagents.desk import storage
from tradingagents.desk.modo import PosseDeExecucao

_real = storage.escrever_json_atomico

def _atrasada(caminho, documento):
    if cfg["atraso"] > 0 and os.path.basename(str(caminho)).startswith("posse"):
        if cfg["marcador"]:
            with open(cfg["marcador"], "w", encoding="utf-8") as fh:
                fh.write("janela leitura->escrita")   # P1 já leu; escrita pendente
        time.sleep(cfg["atraso"])
    return _real(caminho, documento)

storage.escrever_json_atomico = _atrasada

if cfg["atraso"] <= 0 and cfg["marcador"] and cfg.get("espera_marcador"):
    limite = time.monotonic() + 60
    while not os.path.exists(cfg["marcador"]):
        if time.monotonic() > limite:
            raise SystemExit("marcador da janela não apareceu")
        time.sleep(0.02)

armazem = storage.ArmazemDeEstado(None, diretorio=cfg["estado"])
posse = PosseDeExecucao(armazem, "legacy", dono=cfg["dono"],
                        ttl_segundos=cfg["ttl"])
ok, motivo = posse.assumir()
print(json.dumps({"dono": cfg["dono"], "ok": ok, "motivo": motivo}))
'''


def _cfg(tmp_path, dono, *, atraso=0.0, ttl=300.0, marcador="",
         espera_marcador=False, estado=None):
    return {"raiz": str(RAIZ),
            "estado": str(estado or (tmp_path / "estado")),
            "dono": dono, "atraso": atraso, "ttl": ttl,
            "marcador": marcador, "espera_marcador": espera_marcador}


def _iniciar(cfg):
    return subprocess.Popen(
        [sys.executable, "-c", _SCRIPT, json.dumps(cfg)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _colher(proc):
    out, err = proc.communicate(timeout=120)
    assert proc.returncode == 0, f"processo falhou: {err}"
    return json.loads(out.strip().splitlines()[-1])


def _rodar(cfg):
    return _colher(_iniciar(cfg))


def _vencedores(resultados):
    return [r for r in resultados if r["ok"]]


def test_corrida_entre_dois_processos_um_unico_vencedor(tmp_path):
    estado = tmp_path / "estado"
    marcador = str(tmp_path / "janela.p1")
    p1 = _iniciar(_cfg(tmp_path, "pc-dono", atraso=8.0, marcador=marcador,
                       estado=estado))
    limite = time.monotonic() + 60
    while not Path(marcador).exists():
        assert time.monotonic() < limite, (
            "P1 não chegou à janela leitura->escrita")
        time.sleep(0.05)
    p2 = _iniciar(_cfg(tmp_path, "oracle-01", atraso=0.0,
                       marcador=marcador, estado=estado))
    r2 = _colher(p2)
    r1 = _colher(p1)

    ganhadores = _vencedores([r1, r2])
    assert len(ganhadores) == 1, (
        f"os DOIS processos acreditaram ter a posse da mesma mesa "
        f"(dois executores): {r1} | {r2}")
    dono_no_disco = json.loads(
        (estado / "legacy" / "posse.json").read_text(encoding="utf-8"))["dono"]
    assert dono_no_disco == ganhadores[0]["dono"], (
        f"documento durável ({dono_no_disco}) não pertence ao vencedor "
        f"({ganhadores[0]['dono']})")


def test_rival_vivo_recusa_e_posse_vencida_libera(tmp_path):
    estado = tmp_path / "estado"
    r1 = _rodar(_cfg(tmp_path, "pc-dono", ttl=6.0, estado=estado))
    assert r1["ok"] is True, r1

    r2 = _rodar(_cfg(tmp_path, "oracle-01", ttl=300.0, estado=estado))
    assert r2["ok"] is False, (
        f"rival vivo segurando a posse deveria recusar o segundo processo: {r2}")
    assert "pc-dono" in r2["motivo"], r2

    time.sleep(6.2)                       # TTL vence sem renovação
    r3 = _rodar(_cfg(tmp_path, "oracle-01", ttl=300.0, estado=estado))
    assert r3["ok"] is True, (
        f"processo morto deveria perder a posse sozinho pelo TTL: {r3}")


def test_partida_simultanea_sem_hooks_tem_um_vencedor_por_rodada(tmp_path):
    for rodada in range(3):
        estado = tmp_path / f"estado-{rodada}"
        p1 = _iniciar(_cfg(tmp_path, "pc-dono", estado=estado))
        p2 = _iniciar(_cfg(tmp_path, "oracle-01", estado=estado))
        resultados = [_colher(p1), _colher(p2)]
        ganhadores = _vencedores(resultados)
        assert len(ganhadores) == 1, (
            f"rodada {rodada}: um único vencedor esperado, veio "
            f"{[r['dono'] for r in ganhadores]}: {resultados}")
        dono_no_disco = json.loads(
            (estado / "legacy" / "posse.json").read_text(
                encoding="utf-8"))["dono"]
        assert dono_no_disco == ganhadores[0]["dono"], (
            f"rodada {rodada}: disco ({dono_no_disco}) != vencedor "
            f"({ganhadores[0]['dono']})")
