"""Painel Global do Maestro — o retrato da agência INTEIRA, segregado.

Consolida mesa, salas, escola, alertas e ciclo num único retrato. Função
pura: a rota coleta, este módulo apenas organiza — e recusa, por desenho,
duas tentações:

1. **Não misturar resultados PAPER, TESTNET e LIVE.** Cada modo tem
   subtotais próprios; um total só existe quando o modo é único.
2. **Não somar patrimônios ou resultados de moedas diferentes sem
   conversão identificada e verificável.** Sem cotações declaradas, cada
   moeda tem seu próprio subtotal e o total é omitido com motivo.

LIVE, além disso, é sempre apresentado como BLOQUEADO: este painel lê,
nunca libera — quem autoriza operação real é o proprietário, pelo fluxo
de piloto, não por um painel.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

AVISO_LIVE = ("negociação real permanece bloqueada até homologação e "
              "autorização explícita do proprietário")

REGRA_MODOS = ("PAPER, TESTNET e LIVE nunca compartilham um mesmo número — "
               "cada modo tem subtotais próprios")

_REGRA_MOEDAS = ("moedas diferentes não são somadas sem conversão "
                 "identificada e verificável")

_NOTA_PATRIMONIO = ("subtotais apenas dos livros das SALAS; o patrimônio "
                    "da mesa (conta operada pelo app) aparece em 'mesa'")


def _d(valor, padrao="0") -> Decimal:
    try:
        return Decimal(str(valor if valor not in (None, "") else padrao))
    except (InvalidOperation, ValueError):
        return Decimal(padrao)


def _soma(valores: list[Decimal]) -> Decimal:
    return sum(valores, Decimal("0"))


def _valor_posicao(posicao: dict) -> Decimal:
    return (_d(posicao.get("quantidade"))
            * _d(posicao.get("preco_entrada")))


def _traders(agentes: list[dict]) -> dict:
    por_tier: dict[str, int] = {}
    por_estado: dict[str, int] = {}
    suspensos: list[str] = []
    for a in agentes:
        tier = str(a.get("tier") or "experimental")
        por_tier[tier] = por_tier.get(tier, 0) + 1
        estado = str(a.get("stage") or "")
        por_estado[estado] = por_estado.get(estado, 0) + 1
        if estado == "paused":
            suspensos.append(str(a.get("id") or ""))
    return {"total": len(agentes), "por_tier": por_tier,
            "por_estado": por_estado, "suspensos": sorted(suspensos),
            "lista": list(agentes)}


def _comparacoes(agentes: list[dict]) -> dict:
    def stats(tier: str) -> dict:
        linhas = [a for a in agentes if str(a.get("tier")) == tier]
        maduros = [a["evidencia"] for a in linhas
                   if (a.get("evidencia") or {}).get("maduro")]
        if not maduros:
            return {"traders": len(linhas), "maduros": 0,
                    "media_taxa_acerto": None, "media_retorno_medio": None,
                    "motivo": ("sem evidência madura neste tier — média não "
                               "estimada")}
        n = Decimal(len(maduros))
        taxa = _soma([_d(e.get("taxa_acerto")) for e in maduros]) / n
        retorno = _soma([_d(e.get("retorno_medio")) for e in maduros]) / n
        return {"traders": len(linhas), "maduros": len(maduros),
                "media_taxa_acerto": str(taxa),
                "media_retorno_medio": str(retorno), "motivo": ""}

    return {"por_tier": {"homologado": stats("homologado"),
                         "experimental": stats("experimental")},
            "aviso": ("comparações usam SOMENTE evidência madura; amostra "
                      "imatura é declarada, nunca estimada")}


def _oportunidades(ciclo: dict | None) -> dict:
    ciclo = ciclo or {}
    por_trader: dict[str, dict] = {}

    def linha(aid: str, campo: str, motivo: str) -> None:
        if not aid:
            return
        d = por_trader.setdefault(aid, {"vetos": 0, "abstencoes": 0,
                                        "ultimo_motivo": ""})
        d[campo] += 1
        d["ultimo_motivo"] = motivo

    # vetos primeiro; a abstenção do próprio trader fala por último (é a
    # voz dele — mesma prioridade de `_agente_para_painel`)
    for linha_ in ciclo.get("vetos", []) or []:
        if ":" in str(linha_):
            aid, resto = str(linha_).split(":", 1)
            linha(aid.strip(), "vetos", resto.strip().split(": ", 1)[-1])
    for linha_ in ciclo.get("abstencoes", []) or []:
        if " · " in str(linha_):
            aid, resto = str(linha_).split(" · ", 1)
            linha(aid.strip(), "abstencoes", resto.split(": ", 1)[-1])

    vetos = len(ciclo.get("vetos", []) or [])
    abstencoes = len(ciclo.get("abstencoes", []) or [])
    descartados = len(ciclo.get("descartados", []) or [])
    ordens = len(ciclo.get("ordens", []) or [])
    return {"em": ciclo.get("em", ""), "origem": ciclo.get("origem", ""),
            "resumo": ciclo.get("resumo", ""), "vetos": vetos,
            "abstencoes": abstencoes, "descartados": descartados,
            "ordens": ordens, "por_trader": por_trader}


def _patrimonio(salas: list[dict], contabilidade: dict, *,
                moeda_mesa: str, modo_mesa: str) -> dict:
    por_modo: dict[str, dict[str, dict]] = {}
    for s in salas:
        modo = str(s.get("modo") or "") or "não declarado"
        moeda = str(s.get("moeda") or "") or "não declarada"
        celula = por_modo.setdefault(modo, {}).setdefault(
            moeda, {"modo": modo, "moeda": moeda, "livros": 0,
                    "saldo_realizado": Decimal("0"),
                    "taxas_acumuladas": Decimal("0"), "salas": []})
        celula["livros"] += 1
        celula["saldo_realizado"] += _d(s.get("saldo_realizado"))
        celula["taxas_acumuladas"] += _d(s.get("taxas_acumuladas"))
        celula["salas"].append(str(s.get("chave") or ""))

    por_modo_saida = {modo: {moeda: {**c, "saldo_realizado":
                                     str(c["saldo_realizado"]),
                                     "taxas_acumuladas":
                                     str(c["taxas_acumuladas"])}
                             for moeda, c in sorted(moedas.items())}
                      for modo, moedas in sorted(por_modo.items())}

    modos = {modo_mesa} | {str(s.get("modo") or "") for s in salas}
    moedas = {moeda_mesa} | {str(s.get("moeda") or "") for s in salas}
    motivos: list[str] = []
    if len(modos) > 1:
        motivos.append("modos diferentes nunca compartilham um total "
                       "(PAPER/TESTNET/LIVE têm contas e riscos próprios)")
    if "" in moedas:
        motivos.append("há livro com moeda não declarada — sem conversão "
                       "identificada e verificável, o total é omitido")
    elif len(moedas) > 1:
        motivos.append(_REGRA_MOEDAS)

    total = None
    total_resumo = ""
    if not motivos:
        total = (_d(contabilidade.get("patrimonio"))
                 + _soma([_d(s.get("saldo_realizado")) for s in salas]))
        total_resumo = ("soma identificada (mesmo modo e mesma moeda): "
                        "patrimônio da mesa + saldos realizados das salas")

    return {
        "mesa": {"modo": modo_mesa, "moeda": moeda_mesa,
                 "patrimonio": contabilidade.get("patrimonio"),
                 "saldos": dict(contabilidade.get("saldos") or {}),
                 "balanceado": contabilidade.get("balanceado")},
        "contas": [{"chave": s.get("chave"), "corretora": s.get("corretora"),
                    "conta": s.get("conta"), "mercado": s.get("mercado"),
                    "modo": s.get("modo"), "moeda": s.get("moeda"),
                    "saldo_realizado": s.get("saldo_realizado"),
                    "taxas_acumuladas": s.get("taxas_acumuladas"),
                    "posicoes_abertas": len(s.get("posicoes_abertas") or {}),
                    "laboratorio": s.get("laboratorio"),
                    "tem_ledger": s.get("tem_ledger")} for s in salas],
        "por_modo": por_modo_saida,
        "nota": _NOTA_PATRIMONIO,
        "total": str(total) if total is not None else None,
        "total_moeda": moeda_mesa if total is not None else "",
        "total_modo": modo_mesa if total is not None else "",
        "total_resumo": total_resumo,
        "motivo_sem_total": "; ".join(motivos),
    }


def _exposicao(posicoes: list[dict], salas: list[dict], maestro: dict | None,
               *, moeda_mesa: str, modo_mesa: str) -> dict:
    por_modo: dict[str, dict[str, Decimal]] = {}

    def somar(modo: str, moeda: str, valor: Decimal) -> None:
        modo = modo or "não declarado"
        moeda = moeda or "não declarada"
        celula = por_modo.setdefault(modo, {})
        celula[moeda] = celula.get(moeda, Decimal("0")) + valor

    valor_mesa = _soma([_valor_posicao(p) for p in posicoes])
    for p in posicoes:
        somar(modo_mesa, moeda_mesa, _valor_posicao(p))
    for s in salas:
        valor = _soma([_valor_posicao(p)
                       for p in (s.get("posicoes_abertas") or {}).values()])
        if valor:
            somar(str(s.get("modo") or ""), str(s.get("moeda") or ""), valor)

    modos = set(por_modo)
    moedas = {moeda for bolsa in por_modo.values() for moeda in bolsa}
    mistura_modos = len(modos) > 1
    mistura_moedas = len(moedas) > 1
    aviso = ""
    if mistura_modos or mistura_moedas:
        aviso = ("exposição exibida por modo e por moeda — nada é somado "
                 "através de modos ou de moedas (sem conversão verificável); "
                 "o 'maestro' usa valor de custo por posição e serve ao teto "
                 "global, não é conversão")
    return {
        "mesa": {"modo": modo_mesa, "moeda": moeda_mesa,
                 "valor": str(valor_mesa), "posicoes": len(posicoes)},
        "por_modo": {modo: {moeda: str(valor)
                            for moeda, valor in sorted(bolsa.items())}
                     for modo, bolsa in sorted(por_modo.items())},
        "mistura_modos": mistura_modos, "mistura_moedas": mistura_moedas,
        "maestro": maestro, "maestro_aviso": (
            "figura do Maestro para o teto global (custo por posição, todas "
            "as salas) — leitura de supervisão, não conversão"),
        "aviso": aviso,
    }


def retrato_global(*, agentes: list[dict], posicoes: list[dict],
                   contabilidade: dict, salas: list[dict],
                   ciclo: dict | None = None, fonte: dict | None = None,
                   alertas: list[dict] | None = None,
                   maestro: dict | None = None, risco: dict | None = None,
                   moeda_mesa: str = "USDT",
                   modo_mesa: str = "paper") -> dict:
    """Consolida o estado da agência INTEIRA em um retrato segregado.

    Entradas já em forma de painel (a rota coleta e repassa); nenhuma
    decisão financeira acontece aqui — este retrato LÊ.
    """
    agentes = list(agentes or [])
    posicoes = list(posicoes or [])
    salas = list(salas or [])
    alertas = list(alertas or [])

    presentes = sorted({modo_mesa} | {str(s.get("modo") or "") for s in salas})
    return {
        "titulo": "Painel Global do Maestro",
        "modos": {"mesa": modo_mesa, "presentes": presentes,
                  "regra": REGRA_MODOS,
                  "live": {"bloqueado": True, "motivo": AVISO_LIVE}},
        "traders": _traders(agentes),
        "patrimonio": _patrimonio(salas, contabilidade,
                                  moeda_mesa=moeda_mesa, modo_mesa=modo_mesa),
        "exposicao": _exposicao(posicoes, salas, maestro,
                                moeda_mesa=moeda_mesa, modo_mesa=modo_mesa),
        "risco": dict(risco or {}),
        "oportunidades": _oportunidades(ciclo),
        "posicoes": {"total": len(posicoes),
                     "por_agente": _por_agente(posicoes),
                     "valor_total": str(_soma([_valor_posicao(p)
                                               for p in posicoes])),
                     "moeda": moeda_mesa, "modo": modo_mesa,
                     "lista": posicoes},
        "alertas": {"ativos": alertas, "total": len(alertas)},
        "comparacoes": _comparacoes(agentes),
        "maestro": maestro,
        "fonte": dict(fonte or {}),
        "aviso": ("leitura consolidada — não opera, não certifica e não "
                  "autoriza operação real; LIVE permanece bloqueado"),
    }


def _por_agente(posicoes: list[dict]) -> dict[str, int]:
    contagem: dict[str, int] = {}
    for p in posicoes:
        agente = str(p.get("agente") or "")
        contagem[agente] = contagem.get(agente, 0) + 1
    return contagem


__all__ = ["retrato_global", "AVISO_LIVE", "REGRA_MODOS"]
