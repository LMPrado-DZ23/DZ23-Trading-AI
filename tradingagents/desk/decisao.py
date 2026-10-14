"""Decisão do ciclo da mesa — abstenção inteligente (V8.1 §4).

O dono nomeou quatro desfechos possíveis para uma análise da agência, e
"operar" é só um deles:

- OPERAR   — quando o último ciclo de fato executou entrada ou saída;
- AGUARDAR — sem oportunidade adequada; esperar é a decisão certa e é
  apresentada como decisão, nunca como falha nem como lucro;
- BLOQUEAR — risco, dados ou condições operacionais inadequadas
  (modo não ativo, posse de outro processo, perda do dia, pendência de
  reconciliação, erro no último ciclo);
- SUSPENDER — falha crítica de integridade (contabilidade desbalanceada).

Módulo PURO de propósito: sem I/O, sem corretora, sem caminho de ordem.
A decisão é determinística e auditável; nenhuma IA pode contorná-la — a
rota /v1/desk/status só a publica, e o kill switch (parada de emergência)
bloqueia ANTES, com 423, sem chegar aqui.
"""
from __future__ import annotations

OPERAR = "OPERAR"
AGUARDAR = "AGUARDAR"
BLOQUEAR = "BLOQUEAR"
SUSPENDER = "SUSPENDER"


def decidir_do_ciclo(*, mesa_ativa: bool, mesa_estado: str = "",
                     motivo_do_modo: str = "",
                     posse_sou_eu: bool = True, posse_dono: str = "",
                     pendencias_de_reconciliacao: int = 0,
                     contabilidade_balanceada: bool = True,
                     perda_do_dia_atingida: bool = False,
                     ultimo_ciclo: dict | None = None) -> dict:
    """Devolve {"estado", "motivo"} — motivo em português, auditável.

    `ultimo_ciclo` é o dicionário gravado em desk["cycles"] (propostas,
    ordens, encerradas, vetos, abstencoes, erros). None = ainda sem ciclo.
    Posse LIVRE (ninguém segura) não bloqueia: o próximo ciclo assume —
    bloquear ali seria alarme falso.
    """
    # Falha crítica de integridade: nada opera com livro desbalanceado.
    if not contabilidade_balanceada:
        return {"estado": SUSPENDER,
                "motivo": ("contabilidade desbalanceada — reconcilie o "
                           "livro-razão antes de operar")}

    if not mesa_ativa:
        return {"estado": BLOQUEAR,
                "motivo": (f"mesa {mesa_estado or 'inativa'}: "
                           + (motivo_do_modo or "sem motivo registrado")
                           + " — retome pelo PLAY consciente")}
    if not posse_sou_eu and posse_dono:
        return {"estado": BLOQUEAR,
                "motivo": f"posse de execução pertence a {posse_dono}"}
    if perda_do_dia_atingida:
        return {"estado": BLOQUEAR,
                "motivo": ("limite de perda do dia atingido — novas "
                           "entradas bloqueadas até o próximo dia (UTC)")}
    if pendencias_de_reconciliacao > 0:
        return {"estado": BLOQUEAR,
                "motivo": (f"{pendencias_de_reconciliacao} ordem(ns) "
                           "pendente(s) de reconciliação — resolva antes "
                           "de operar de novo")}
    erros = list((ultimo_ciclo or {}).get("erros") or [])
    if erros:
        return {"estado": BLOQUEAR,
                "motivo": (f"último ciclo teve {len(erros)} erro(s) — "
                           "ver detalhes antes de retomar")}

    if ultimo_ciclo:
        ordens = list(ultimo_ciclo.get("ordens") or [])
        encerradas = list(ultimo_ciclo.get("encerradas") or [])
        if ordens or encerradas:
            return {"estado": OPERAR,
                    "motivo": (f"{len(ordens)} entrada(s) e "
                               f"{len(encerradas)} saída(s) no último "
                               "ciclo")}

    if not ultimo_ciclo:
        return {"estado": AGUARDAR,
                "motivo": ("ainda sem ciclo avaliado — ligue a mesa e "
                           "aguarde a primeira avaliação")}
    n_prop = int(ultimo_ciclo.get("propostas") or 0)
    n_abs = len(ultimo_ciclo.get("abstencoes") or [])
    n_vet = len(ultimo_ciclo.get("vetos") or [])
    return {"estado": AGUARDAR,
            "motivo": (f"sem oportunidade adequada no último ciclo: "
                       f"{n_prop} proposta(s), {n_abs} abstenção(ões), "
                       f"{n_vet} veto(s) — esperar também é decidir")}


__all__ = ["AGUARDAR", "BLOQUEAR", "OPERAR", "SUSPENDER", "decidir_do_ciclo"]
