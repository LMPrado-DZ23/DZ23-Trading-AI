"""Homologação N01 — notificações Windows reais (executada no DESKTOP-AVELL).

Prova, com toasts REAIS no Windows:
 1. identidade AUMID registrada em HKCU\\Software\\Classes\\AppUserModelId;
 2. cinco tipos de alerta com o MODO explícito no texto (entrada paper,
    saída, stop, perda de conexão e emergência);
 3. a identidade persiste entre PROCESSOS (um segundo processo envia toast);
 4. o remetente aparece em Notifications\\Settings (gerenciável);
 5. só a chave do PRÓPRIO app é tocada — nada de outros aplicativos.

Uso: .venv/Scripts/python.exe scripts/homolog_notificacoes_win.py
Evidência: docs/mission/evidence/e2e/n01-notificacoes-win.log
"""
from __future__ import annotations

import subprocess
import sys
import winreg
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

LINHAS: list[str] = []


def linha(texto: str) -> None:
    carimbo = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    LINHAS.append(f"{carimbo} {texto}")
    print(texto)


def checa(nome: str, ok: bool, detalhe: str = "") -> bool:
    sufixo = f" — {detalhe}" if detalhe else ""
    linha(f"CHECA {nome}: {'OK' if ok else 'FALHOU'}{sufixo}")
    return bool(ok)


def ler_valor(caminho: str, valor: str) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, caminho) as chave:
            return str(winreg.QueryValueEx(chave, valor)[0])
    except OSError:
        return ""


def envia(titulo: str, detalhe: str, acao: str) -> tuple[bool, str]:
    from tradingagents.desk.canais_alerta import CanalWindows
    from tradingagents.desk.notificacao_viva import AlertaVivo

    r = CanalWindows().enviar(AlertaVivo(
        tipo="homolog_n01", severidade="atencao", titulo=titulo,
        detalhe=detalhe, acao=acao, fonte="homologacao"))
    return bool(r.get("ok")), str(r.get("detalhe") or r.get("erro") or "")[:140]


def main() -> int:
    from service.app import montar_alerta_entrada
    from tradingagents.desk.canais_alerta import AUMID, registrar_identidade_windows

    linha(f"identidade alvo: AUMID='{AUMID}'")
    resultados: list[bool] = []

    r = registrar_identidade_windows()
    resultados.append(checa("identidade_registrada", r["ok"], r["detalhe"]))
    chave_aumid = f"Software\\Classes\\AppUserModelId\\{AUMID}"
    resultados.append(checa(
        "aumid_display_name", ler_valor(chave_aumid, "DisplayName") == AUMID))
    icone = ler_valor(chave_aumid, "IconUri")
    linha(f"INFO icon_uri={icone or '(exe ainda nao instalado no LOCALAPPDATA)'}")

    entrada = montar_alerta_entrada("ord-3", "paper")
    alertas = [
        (entrada["titulo"], entrada["detalhe"], entrada["acao"]),
        ("[SIMULADO (PAPER)] Saída executada: BTC/USDT",
         "quantidade 0.5 a 62000 — ALVO atingido; lucro simulado no gerente.",
         "Confira a posição na Mesa de Agentes."),
        ("[SIMULADO (PAPER)] Stop atingido: ETH/USDT",
         "quantidade 1.0 a 2900 — STOP; saída simulada executada.",
         "Confira a posição na Mesa de Agentes."),
        ("[MESA] Perda de conexão com o feed",
         "sem velas novas além do limite; novas entradas bloqueadas até "
         "reconectar.", "Verifique a fonte de dados no painel."),
        ("[MESA] EMERGÊNCIA: kill switch acionado",
         "negociação PAPER parada por decisão do dono; nenhuma ordem sai "
         "até reativar.", "Abra o painel para revisar."),
    ]
    for i, (titulo, detalhe, acao) in enumerate(alertas, 1):
        ok, info = envia(titulo, detalhe, acao)
        resultados.append(checa(f"toast_{i}", ok, info))

    codigo = (
        "import sys; sys.path.insert(0, r'" + str(RAIZ) + "');"
        "from tradingagents.desk.canais_alerta import CanalWindows;"
        "from tradingagents.desk.notificacao_viva import AlertaVivo;"
        "r = CanalWindows().enviar(AlertaVivo(tipo='t', severidade='atencao',"
        " titulo='[SIMULADO (PAPER)] Reinício: identidade persiste',"
        " detalhe='toast enviado por um NOVO processo', acao=''));"
        "print('PROCESSO_OK' if r.get('ok') else 'PROCESSO_FALHOU',"
        " r.get('detalhe', r.get('erro', '')))")
    proc = subprocess.run([sys.executable, "-c", codigo],
                          capture_output=True, text=True, timeout=90)
    saida = (proc.stdout or proc.stderr or "").strip()
    resultados.append(checa("toast_novo_processo", "PROCESSO_OK" in saida,
                            saida[-140:]))

    caminho_settings = ("Software\\Microsoft\\Windows\\CurrentVersion\\"
                        f"Notifications\\Settings\\{AUMID}")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, caminho_settings):
            gerenciavel = True
    except OSError:
        gerenciavel = False
    resultados.append(checa("remetente_nas_configuracoes", gerenciavel,
                            caminho_settings))

    tudo_ok = all(resultados)
    LINHAS.append(f"RESULTADO={'OK' if tudo_ok else 'FALHOU'}")
    LINHAS.append(f"CHECAGENS={sum(resultados)}/{len(resultados)}")
    destino = (RAIZ / "docs" / "mission" / "evidence" / "e2e"
               / "n01-notificacoes-win.log")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join(LINHAS) + "\n", encoding="utf-8")
    print(f"RESULTADO={'OK' if tudo_ok else 'FALHOU'} "
          f"({sum(resultados)}/{len(resultados)}); evidência: {destino}")
    return 0 if tudo_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
