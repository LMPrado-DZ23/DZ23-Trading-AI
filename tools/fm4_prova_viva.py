"""FM-4 (§5–§8) — prova viva ponta a ponta numa instância ISOLADA do app.

Sobe o serviço real (uvicorn) com DATA_DIR temporário — a receita do
tests/e2e/conftest.py — e autentica como dono (Bearer; o login é 404 pelo
portão G2 de dono único). O processo filho roda com as chaves de IA do
ambiente REMOVIDAS (a estação do dono tem chaves reais; a prova precisa
ser hermética e nunca sair à rede por acidente).

Cobre:
  §5 mesa: boot → ciclo síncrono (/v1/desk/cycle) → agendador autônomo
     (DESK_INTERVAL_SECONDS curto só NESTA instância) → 10 traders → sala.
  §6 dashboard: /v1/maestro/painel + /v1/status/operacao.
  §7 copiloto manual: modo → posição → evento → monitor → sinais →
     alertas → reversibilidade (desliga).
  §8 IA: provedores (estado honesto sem chave), testar (não sai à rede),
     uso.

Salva o JSON completo em docs/mission/evidence/fm4/prova-viva.json.
NUNCA toca a instalação do dono: usa porta livre própria e encerra só o
processo que ELE mesmo criou.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "docs" / "mission" / "evidence" / "fm4"
SENHA = "s3nh4-dz23-e2e"
SAIDA = EVIDENCE_DIR / "prova-viva.json"

captura: dict = {}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _ambiente_do_filho() -> dict:
    env = os.environ.copy()
    env.pop("ACCESS_PASSWORD", None)
    from service.api.ia_provedores import CATALOGO

    for p in CATALOGO:
        if p.get("chave_env"):
            env.pop(p["chave_env"], None)
    for extra in ("TRADINGAGENTS_LLM_PROVIDER", "TRADINGAGENTS_LLM_MODEL"):
        env.pop(extra, None)
    env.update(
        {
            "DATA_DIR": tempfile.mkdtemp(prefix="dz23-fm4-"),
            "MULTI_TENANT_ENABLED": "false",
            "ACCESS_PASSWORD": SENHA,
            "DESK_INTERVAL_SECONDS": "15",  # só nesta instância de prova
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def _req(base: str, metodo: str, rota: str, corpo: dict | None = None,
         timeout: float = 60.0):
    url = base + rota
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(url, data=dados, method=metodo)
    req.add_header("Authorization", f"Bearer {SENHA}")
    if dados is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            corpo_erro = json.loads(e.read().decode())
        except Exception:
            corpo_erro = {}
        return e.code, corpo_erro


def main() -> int:
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print("uvicorn ausente — instale o extra 'desk'")
        return 2

    porta = _free_port()
    base = f"http://127.0.0.1:{porta}"
    env = _ambiente_do_filho()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "service.app:app",
         "--host", "127.0.0.1", "--port", str(porta), "--log-level", "warning"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    captura["instancia"] = {"porta": porta, "data_dir": env["DATA_DIR"],
                            "intervalo_s": 15}
    try:
        limite = time.monotonic() + 60
        pronto = False
        while time.monotonic() < limite:
            if proc.poll() is not None:
                print(f"uvicorn morreu cedo (exit {proc.returncode})")
                return 3
            try:
                with urllib.request.urlopen(base + "/healthz", timeout=2) as r:
                    if r.status == 200:
                        pronto = True
                        break
            except OSError:
                time.sleep(0.4)
        if not pronto:
            print("servidor de prova não respondeu /healthz em 60s")
            return 3
        captura["healthz"] = True

        # ---------------- §5 MESA: ciclo + agendador + traders -------------
        st, me = _req(base, "GET", "/v1/auth/me")
        captura["auth_me"] = {"http": st, "resposta": me}

        st, estado = _req(base, "GET", "/v1/desk/status")
        captura["desk_status_inicial"] = {
            "http": st,
            "fonte": estado.get("fonte"),
            "modo": estado.get("modo"),
            "agendador": estado.get("agendador"),
            "decisao": estado.get("decisao"),
            "traders": [
                {"id": a.get("id"), "strategy": a.get("strategy"),
                 "stage": a.get("stage"), "tier": a.get("tier")}
                for a in estado.get("agents", [])
            ],
            "escola": {"motivo": (estado.get("escola") or {}).get("motivo")},
            "contabilidade_balanceada":
                (estado.get("contabilidade") or {}).get("balanceado"),
        }

        st, ciclo = _req(base, "POST", "/v1/desk/cycle", {}, timeout=120)
        captura["desk_cycle"] = {
            "http": st, "ok": ciclo.get("ok"), "resumo": ciclo.get("resumo"),
            "ordens": ciclo.get("ordens"), "vetos": ciclo.get("vetos"),
            "abstencoes": ciclo.get("abstencoes"),
            "descartados": ciclo.get("descartados"),
            "erros": ciclo.get("erros"),
        }

        st, start = _req(base, "POST", "/v1/desk/scheduler/start", {})
        captura["scheduler_start"] = {"http": st, "resposta": start}

        trilha = []
        ciclos_alvo = 2
        limite = time.monotonic() + 120
        autora_ok = False
        while time.monotonic() < limite:
            st, s = _req(base, "GET", "/v1/desk/status")
            ag = s.get("agendador") or {}
            trilha.append({"t_s": round(time.monotonic() - (limite - 120), 1),
                           "rodando": ag.get("rodando"), "ciclos": ag.get("ciclos"),
                           "erros": ag.get("erros")})
            if (ag.get("ciclos") or 0) >= ciclos_alvo:
                autora_ok = True
                break
            time.sleep(3)
        st, s_final = _req(base, "GET", "/v1/desk/status")
        captura["scheduler_autonomo"] = {
            "ciclos_alvo": ciclos_alvo, "alcancado": autora_ok,
            "trilha": trilha[-12:],
            "agendador_final": s_final.get("agendador"),
        }

        agentes = s_final.get("agents") or []
        captura["traders"] = {
            "total": len(agentes),
            "tiers": sorted({str(a.get("tier")) for a in agentes}),
            "amostra": [
                {"id": a.get("id"), "strategy": a.get("strategy"),
                 "stage": a.get("stage"), "tier": a.get("tier"),
                 "ultimo_no_trade": (a.get("ultimo_no_trade") or "")[:140]}
                for a in agentes[:4]
            ],
        }
        primeiro = next((a.get("id") for a in agentes if a.get("id")), "")
        if primeiro:
            st, sala = _req(base, "GET",
                            f"/v1/desk/traders/{primeiro}/sala?instrumento=BTC/USDT&timeframe=1h")
            if isinstance(sala, dict):
                for chave in ("serie", "velas", "curva", "pontos"):
                    if isinstance(sala.get(chave), list) and len(sala[chave]) > 8:
                        sala[chave] = {"total": len(sala[chave]),
                                       "ultimos": sala[chave][-4:]}
            captura["sala_trader"] = {"http": st, "agent_id": primeiro, "sala": sala}

        # ---------------- §6 DASHBOARD: maestro + status/operação ----------
        st, painel = _req(base, "GET", "/v1/maestro/painel")
        captura["maestro_painel"] = {"http": st, "resposta": painel}
        st, oper = _req(base, "GET", "/v1/status/operacao")
        captura["status_operacao"] = {"http": st, "resposta": oper}

        # ---------------- §7 COPILOTO MANUAL ---------------------------------
        st, modo_on = _req(base, "PUT", "/v1/manual/modo", {"ativo": True})
        captura["manual_modo_on"] = {"http": st, "ativo": modo_on.get("ativo"),
                                     "anterior": modo_on.get("anterior")}
        st_bloqueio, bloqueio = _req(base, "POST", "/v1/desk/cycle", {})
        captura["mesa_bloqueada_com_copiloto"] = {"http": st_bloqueio,
                                                  "detalhe": bloqueio.get("detail")}

        st, pos = _req(base, "POST", "/v1/manual/positions", {
            "instrumento": "BTC/USDT", "direcao": "compra",
            "quantidade": 0.01, "preco_entrada": 100000,
            "origem_preco": "declarado", "tese": "prova FM-4 (§7)",
            "stop": 95000, "alvos": [105000, 110000],
            "idempotency_key": "fm4-prova-pos-1",
        })
        pid = (pos.get("posicao") or {}).get("posicao_id") or \
            (pos.get("posicao") or {}).get("id")
        captura["manual_posicao"] = {"http": st, "criada": pos.get("criada"),
                                     "posicao_id": pid,
                                     "posicao": pos.get("posicao")}

        st, pos_b = _req(base, "POST", "/v1/manual/positions", {
            "instrumento": "BTC/USDT", "direcao": "compra",
            "quantidade": 0.01, "preco_entrada": 100000,
            "idempotency_key": "fm4-prova-pos-1",
        })
        captura["manual_posicao_idempotente"] = {
            "http": st, "criada": pos_b.get("criada"),
            "mesmo_id": ((pos_b.get("posicao") or {}).get("posicao_id") == pid)
            if pid else None}

        if pid:
            st, ev = _req(base, "POST", f"/v1/manual/positions/{pid}/events", {
                "tipo": "entrada_parcial", "quantidade": 0.004,
                "preco": 101000, "fonte": "declarado",
                "idempotency_key": "fm4-prova-ev-1"})
            captura["manual_evento"] = {"http": st, "aplicado": ev.get("aplicado"),
                                        "posicao": ev.get("posicao")}
            st, det = _req(base, "GET", f"/v1/manual/positions/{pid}")
            captura["manual_detalhe"] = {"http": st,
                                         "posicao": det.get("posicao"),
                                         "eventos": det.get("eventos")}
            st, al = _req(base, "GET", f"/v1/manual/positions/{pid}/alerts")
            captura["manual_alertas"] = {"http": st, "resposta": al}

        st, saude = _req(base, "GET", "/v1/manual/monitor/health")
        captura["manual_monitor_health"] = {"http": st, "resposta": saude}
        st, sinais = _req(base, "GET",
                          "/v1/manual/sinais?instrumento=BTC/USDT&timeframe=1h")
        captura["manual_sinais"] = {"http": st, "resposta": sinais}

        st, modo_off = _req(base, "PUT", "/v1/manual/modo", {"ativo": False})
        captura["manual_modo_off"] = {"http": st, "ativo": modo_off.get("ativo")}
        st_volta, volta = _req(base, "POST", "/v1/desk/cycle", {}, timeout=120)
        captura["mesa_volta_apos_copiloto"] = {"http": st_volta,
                                               "ok": volta.get("ok"),
                                               "resumo": volta.get("resumo")}

        # ---------------- §8 IA: estados honestos SEM chave ------------------
        st, provs = _req(base, "GET", "/v1/ai/provedores")
        captura["ia_provedores"] = {"http": st, "resposta": provs}
        st, teste = _req(base, "POST", "/v1/ai/testar", {"provedor": "openai"})
        captura["ia_testar_sem_chave"] = {"http": st, "resposta": teste}
        st, uso = _req(base, "GET", "/v1/ai/uso")
        captura["ia_uso"] = {"http": st, "resposta": uso}

        # ---------------- encerramento controlado ----------------------------
        st, parada = _req(base, "POST", "/v1/desk/scheduler/stop", {})
        captura["scheduler_stop"] = {"http": st, "resposta": parada}

        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        SAIDA.write_text(json.dumps(captura, ensure_ascii=False, indent=2,
                                    default=str), encoding="utf-8")
        print(f"OK — evidência em {SAIDA}")
        st, s2 = _req(base, "GET", "/v1/desk/status")
        print("agendador final:", json.dumps(s2.get("agendador"), ensure_ascii=False))
        print("traders:", captura["traders"]["total"],
              "tiers:", captura["traders"]["tiers"])
        print("ciclo §5:", captura["desk_cycle"]["resumo"])
        print("traders na sala:", captura.get("sala_trader", {}).get("http"))
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
