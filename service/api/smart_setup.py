"""DZ23 Smart Setup — configuração inteligente (missão V8 complementar).

O que este módulo É: a camada que tira o aplicativo da instalação já pronto
para uso — pré-configuração segura na primeira execução, descoberta dos
provedores JÁ configurados, sugestão de modelo rápido/profundo/fallback a
partir do que existe (catálogo + descoberta real da API + latência medida),
perfis (Pronto para Usar, Econômico, Equilibrado, Avançado) e o bloqueio
CONFIGURÁVEL de orçamento para chamadas controladas pelo DZ23.

O que este módulo NÃO É:
- Um substituto do preenchimento manual avançado: aplicar um perfil só
  preenche campos VAZIOS. Preferência já definida pelo dono NUNCA é
  sobrescrita (idempotência de atualização — R11).
- Um lugar onde uma chave fictícia entra: nada aqui inventa credencial; só
  usa o que o dono colou (guardado cifrado pelo armazenamento existente).
- Um gate de risco: o bloqueio de orçamento é opcional, desligado por
  padrão, e só suspende as consultas de IA do próprio DZ23 — jamais travas
  de risco, execução da mesa ou qualquer política financeira.

Honestidade embutida: nome de modelo sugerido vem da DESCOBERTA real quando
existe (lista da API do provedor); sem descoberta, o padrão do catálogo com
a fonte declarada. Preço sem referência nunca vira zero; gratuito é zero.
"""

from __future__ import annotations

import re

# Pré-configuração segura (R1): valores aplicados SÓ quando o campo está
# vazio. Idioma "Portuguese" é o nome canônico da CLI E o valor exibível no
# <select> do painel (o dono é pt-BR; um valor que o select não mostra
# apareceria vazio depois de aplicar um perfil); rodadas enxutas porque cada
# rodada de debate é uma chamada paga.
PADROES_SEGUROS: dict[str, str] = {
    "TRADINGAGENTS_OUTPUT_LANGUAGE": "Portuguese",
    "TRADINGAGENTS_DESK_CONTROLE": "0",
}

# Perfis (R6). `preferencia` define a ordem de escolha de provedor quando o
# campo está vazio: econômico = mais barato primeiro; equilibrado = latência
# conhecida primeiro dentro do mesmo grupo de custo; avançado = saúde
# (provedor autenticado em teste) primeiro.
PERFIS: dict[str, dict] = {
    "pronto": {"nome": "Pronto para Usar",
               "desc": "padrões seguros, provedores gratuitos preferidos; "
                       "nada pago é escolhido sem uma chave que você colou",
               "debate": 1, "risco": 1, "preferencia": "economico"},
    "economico": {"nome": "Econômico",
                  "desc": "prioriza gratuitos e mais baratos, debate curto",
                  "debate": 1, "risco": 1, "preferencia": "economico"},
    "equilibrado": {"nome": "Equilibrado",
                    "desc": "custo e latência equilibrados, debate médio",
                    "debate": 2, "risco": 2, "preferencia": "equilibrado"},
    "avancado": {"nome": "Avançado",
                 "desc": "prioriza saúde/latência; debate longo (mais "
                         "chamadas) — escolha o modelo profundo você mesmo",
                 "debate": 3, "risco": 3, "preferencia": "avancado"},
}

# Classificação de modelo por SEGMENTO do nome (R3): nada de lista fixa de
# nomes desatualizados — os tokens valem para nomes versionados futuros.
_TOKENS_RAPIDOS = frozenset({
    "mini", "flash", "lite", "haiku", "small", "instant", "nano", "micro",
})
_TOKENS_PROFUNDOS = frozenset({
    "opus", "pro", "sonnet", "large", "reasoner", "max", "ultra", "medium",
    "thinking", "think",
})
# "70b", "405b", "8x7b": parâmetros no nome. ≥32B (ou "x" no meio) puxa para
# capacidade; ≤13B puxa para rápido.
_SEGMENTO_PARAMS = re.compile(r"^(\d{1,4})(x\d{1,4})?b$", re.IGNORECASE)
_SEPARADORES = re.compile(r"[-_./:@ ]+")


def classificar_modelo(modelo: str) -> str:
    """'rapido' | 'profundo' | 'neutro' pelos tokens do nome. Neutro é
    resposta honesta: sem token reconhecível o módulo não finge saber."""
    nome = str(modelo or "").strip().lower()
    if not nome:
        return "neutro"
    segmentos = [s for s in _SEPARADORES.split(nome) if s]
    for seg in segmentos:
        if seg in _TOKENS_RAPIDOS:
            return "rapido"
        if seg in _TOKENS_PROFUNDOS:
            return "profundo"
    for seg in segmentos:
        m = _SEGMENTO_PARAMS.match(seg)
        if m:
            if m.group(2):
                continue      # MoE ("8x7b"): ambiguidade honesta, não chuta
            digitos = int(m.group(1))
            if digitos >= 32:
                return "profundo"
            if digitos <= 13:
                return "rapido"
    return "neutro"


def sugerir_modelos(provedor: str, modelos: list[str]) -> dict:
    """Escolhe rápido = classificado rápido mais barato (preço de referência;
    sem preço vai para o fim) e profundo = classificado profundo de maior
    referência de preço (proxy de capacidade — declarado como proxy)."""
    from tradingagents.llm_clients import precos

    def _custo(nome: str) -> tuple[float, int]:
        p = precos.preco(provedor, nome)
        if p is None:
            return (9e9, 0)   # sem referência: nunca na frente de um preço real
        return (p[0], 0)

    classificados = [(i, m, classificar_modelo(m)) for i, m in enumerate(modelos)]
    rapidos = [(i, m) for i, m, c in classificados if c == "rapido"]
    profundos = [(i, m) for i, m, c in classificados if c == "profundo"]
    rapidos.sort(key=lambda t: (_custo(t[1]), t[0]))
    profundos.sort(key=lambda t: (-_custo(t[1])[0], t[0]))
    nota = ("profundo escolhido pela maior referência de preço entre os "
            "classificados como profundos — proxy de capacidade, confira a "
            "sua conta no provedor")
    if not profundos:
        # Sem token de profundidade no nome (ex.: "gpt-4o"): escolher o de
        # maior referência entre os NEUTROS é honesto — a nota diz que o
        # critério foi só preço, não classificação de capacidade.
        neutros = sorted(((i, m) for i, m, c in classificados if c == "neutro"),
                         key=lambda t: (-_custo(t[1])[0], t[0]))
        if neutros:
            profundos = [neutros[0]]
            nota = ("nenhum modelo com marcador de profundidade no nome; "
                    "profundo escolhido pela maior referência de preço — "
                    "proxy de capacidade, confira a sua conta no provedor")
    return {"rapido": rapidos[0][1] if rapidos else "",
            "profundo": profundos[0][1] if profundos else "",
            "fonte": "descoberta",
            "nota": nota}


def provedores_configurados(cfg: dict) -> list[str]:
    """Provedores com credencial/utilização já configurada (R2). Só ids —
    nenhum valor, nenhuma duplicação de credencial. Provedor sem chave
    (Ollama, relai próprio) só conta quando o dono apontou o endereço: um
    servidor local pode não estar no ar, e a escolha automática não pode
    preferi-lo a um provedor com chave de verdade."""
    from service.api.ia_provedores import CATALOGO

    url_propria = str(cfg.get("TRADINGAGENTS_BACKEND_URL") or "").strip()
    out = []
    for p in CATALOGO:
        chave_env = p["chave_env"]
        if chave_env and str(cfg.get(chave_env) or "").strip() or p.get("chave_opcional") and url_propria:
            out.append(p["id"])
    return out


def _chave_de_escolha(provedor_id: str, modo: str, testes: dict) -> tuple:
    from service.api.ia_provedores import provedor_conhecido
    from tradingagents.llm_clients import precos

    entrada = provedor_conhecido(provedor_id) or {}
    modelo = str(entrada.get("quick_padrao") or "")
    p = precos.preco(provedor_id, modelo) if modelo else None
    if p is None:
        grupo, custo = 2, 9e9
    elif p == (0.0, 0.0):
        grupo, custo = 0, 0.0
    else:
        grupo, custo = 1, p[0]
    t = testes.get(provedor_id) or {}
    lat = t.get("latencia_ms")
    lat_key = (0, float(lat)) if isinstance(lat, (int, float)) else (1, 9e9)
    saude = 0 if t.get("ok") is True else 1
    if modo == "economico":
        return (grupo, custo, lat_key, provedor_id)
    if modo == "equilibrado":
        return (grupo, lat_key, custo, provedor_id)
    return (saude, lat_key, grupo, custo, provedor_id)


def escolher_provedor(cfg: dict, perfil: str, testes: dict | None = None) -> str | None:
    """Provedor sugerido entre os CONFIGURADOS (R4): capacidade/latência/
    custo/saúde conforme o perfil. Provedor sem chave nunca é escolhido —
    a escolha automática não pode gerar cobrança sem credencial do dono."""
    configurados = provedores_configurados(cfg)
    if not configurados:
        return None
    modo = PERFIS.get(perfil, PERFIS["pronto"])["preferencia"]
    return sorted(configurados,
                  key=lambda pid: _chave_de_escolha(pid, modo, testes or {}))[0]


def sugestao(cfg: dict, perfil: str = "pronto", testes: dict | None = None,
             modelos_por_provedor: dict | None = None) -> dict:
    """Sugestão completa (R3/R4/R5): provedor, modelos e cadeia de fallback.

    Modelos vêm da descoberta real quando o painel a tiver (R3 — sem nomes
    fixos desatualizados); sem descoberta, o padrão do catálogo com a fonte
    declarada. Fallback = demais provedores configurados ordenados por custo.
    """
    from service.api.ia_provedores import provedor_conhecido

    principal = str(cfg.get("TRADINGAGENTS_LLM_PROVIDER") or "").strip().lower()
    if principal and provedor_conhecido(principal) is not None:
        escolhido: str | None = principal
    else:
        escolhido = escolher_provedor(cfg, perfil, testes)
    resultado = {"provedor": escolhido or "", "modelo_rapido": "",
                 "modelo_profundo": "", "fallback": "", "notas": []}
    if not escolhido:
        resultado["notas"].append(
            "nenhum provedor com chave — cole uma chave em 'Chaves dos "
            "provedores' e a sugestão automática libera")
        return resultado
    entrada = provedor_conhecido(escolhido) or {}
    descobertos = [str(m) for m in
                   ((modelos_por_provedor or {}).get(escolhido) or []) if m]
    if descobertos:
        sug = sugerir_modelos(escolhido, descobertos)
        resultado["modelo_rapido"] = sug["rapido"]
        resultado["modelo_profundo"] = sug["profundo"]
        resultado["notas"].append(
            f"modelos escolhidos da lista real do provedor ({len(descobertos)} "
            "descobertos) — confira a conta no painel do provedor")
    else:
        resultado["modelo_rapido"] = str(entrada.get("quick_padrao") or "")
        resultado["notas"].append(
            "modelo rápido = padrão do catálogo; use 'Buscar modelos' no "
            "provedor para escolher da lista real da API")
    outros = [p for p in provedores_configurados(cfg) if p != escolhido]
    outros.sort(key=lambda pid: _chave_de_escolha(pid, "economico", testes or {}))
    resultado["fallback"] = ", ".join(outros)
    if not outros:
        resultado["notas"].append(
            "sem segundo provedor configurado — o fallback entra quando "
            "existir outra chave")
    return resultado


def aplicar(cfg: dict, perfil: str, *, testes: dict | None = None,
            modelos_por_provedor: dict | None = None) -> dict:
    """Aplica um perfil SOMENTE em campos vazios (R11/R12). Devolve o cfg
    novo, o que foi aplicado e o que foi mantido por preferência do dono.
    Nenhuma chave de API é criada, alterada ou removida por aqui (R10)."""
    meta = PERFIS.get(perfil)
    if meta is None:
        raise ValueError(f"perfil desconhecido: {perfil!r}")
    sug = sugestao(cfg, perfil, testes, modelos_por_provedor)
    alvos: dict[str, str] = {
        **PADROES_SEGUROS,
        "TRADINGAGENTS_MAX_DEBATE_ROUNDS": str(meta["debate"]),
        "TRADINGAGENTS_MAX_RISK_ROUNDS": str(meta["risco"]),
    }
    if sug["provedor"]:
        alvos["TRADINGAGENTS_LLM_PROVIDER"] = sug["provedor"]
    if sug["modelo_rapido"]:
        alvos["TRADINGAGENTS_QUICK_THINK_LLM"] = sug["modelo_rapido"]
    if sug["modelo_profundo"]:
        alvos["TRADINGAGENTS_DEEP_THINK_LLM"] = sug["modelo_profundo"]
    if sug["fallback"]:
        alvos["TRADINGAGENTS_LLM_FALLBACK"] = sug["fallback"]
    novos = dict(cfg)
    aplicados: dict[str, str] = {}
    mantidos: list[str] = []
    for chave, valor in alvos.items():
        if str(cfg.get(chave) or "").strip():
            mantidos.append(chave)
        else:
            novos[chave] = valor
            aplicados[chave] = valor
    return {"cfg": novos, "aplicados": aplicados, "mantidos": mantidos,
            "sugestao": sug}


def bloqueio_ligado(cfg: dict) -> bool:
    return str(cfg.get("TRADINGAGENTS_IA_ORCAMENTO_BLOQUEIO") or "").strip() \
        .lower() in ("1", "true", "sim", "on", "ligado", "yes")


def ia_permitida(cfg: dict, orcamento: dict | None) -> tuple[bool, str]:
    """Permissão de consulta pelo orçamento (R7). Padrão: permitido (o
    orçamento é informativo — NUNCA um gate por si só). O bloqueio só existe
    quando o dono liga `TRADINGAGENTS_IA_ORCAMENTO_BLOQUEIO` E há orçamento
    definido E o uso estimado o alcançou. Não toca travas de risco."""
    if not bloqueio_ligado(cfg):
        return True, ""
    o = orcamento or {}
    if not o.get("definido") or o.get("usd") is None:
        return True, ""
    limite = float(o.get("usd") or 0.0)
    usado = float(o.get("usado_usd") or 0.0)
    if limite > 0 and usado >= limite:
        parcial = (" — estimativa PARCIAL (há chamadas sem preço de "
                   "referência)" if o.get("parcial") else "")
        return False, (f"orçamento de IA atingido: {usado:.4f} de "
                       f"{limite:.2f} USD{parcial}; bloqueio configurado pelo "
                       "dono para chamadas controladas pelo DZ23 "
                       "(não afeta travas de risco)")
    return True, ""


def diagnostico(cfg: dict, testes: dict | None = None,
                uso: dict | None = None) -> list[dict]:
    """Estados por provedor (R8): configurado → conectado → autenticado →
    operacional, cada um derivado de EVIDÊNCIA local (config, último teste,
    registro de consumo) — nunca de um 'ping' imaginário."""
    from service.api.ia_provedores import CATALOGO

    testes = testes or {}
    por_provedor = (uso or {}).get("por_provedor") or {}
    out = []
    for p in CATALOGO:
        pid = p["id"]
        chave_env = p["chave_env"]
        tem_chave = (bool(str(cfg.get(chave_env) or "").strip())
                     if chave_env else None)
        url_propria = str(cfg.get("TRADINGAGENTS_BACKEND_URL") or "").strip()
        configurado = bool(tem_chave) or bool(
            p.get("chave_opcional") and url_propria)
        t = testes.get(pid) or {}
        estado_teste = str(t.get("estado") or "")
        conectado = bool(t) and estado_teste not in (
            "sem_chave", "sem_url", "endereco_recusado", "sem_conexao",
            "tempo_esgotado", "")
        autenticado = bool(t.get("ok")) if t else False
        chamadas = int((por_provedor.get(pid) or {}).get("chamadas") or 0)
        out.append({
            "id": pid, "nome": p["nome"], "tem_chave": tem_chave,
            "configurado": configurado, "conectado": conectado,
            "autenticado": autenticado,
            "operacional": bool(autenticado and chamadas > 0),
            "chamadas_registradas": chamadas,
            "ultimo_teste": ({k: t.get(k) for k in
                              ("estado", "latencia_ms", "quando", "detalhe")}
                             if t else None),
        })
    return out


def estado(cfg: dict, *, perfil: str = "pronto", tem_config: bool = True,
           testes: dict | None = None, uso: dict | None = None) -> dict:
    """Estado completo para o assistente de primeira execução (R9): só as
    pendências realmente necessárias, sem percentual inventado."""
    testes = testes or {}
    configurados = provedores_configurados(cfg)
    principal = str(cfg.get("TRADINGAGENTS_LLM_PROVIDER") or "").strip()
    rapido = str(cfg.get("TRADINGAGENTS_QUICK_THINK_LLM") or "").strip()
    profundo = str(cfg.get("TRADINGAGENTS_DEEP_THINK_LLM") or "").strip()
    fallback = str(cfg.get("TRADINGAGENTS_LLM_FALLBACK") or "").strip()
    orcamento = str(cfg.get("TRADINGAGENTS_IA_ORCAMENTO_USD") or "").strip()
    idioma = str(cfg.get("TRADINGAGENTS_OUTPUT_LANGUAGE") or "").strip()
    pendencias: list[dict] = []
    if not principal:
        pendencias.append({
            "id": "provedor",
            "titulo": "Escolher o provedor de IA",
            "detalhe": ("opcional para a mesa operar; necessário para a IA "
                        "comentar a mesa e o diário"),
            "como": "cole a chave em 'Chaves dos provedores' e aplique um perfil"})
    if principal and not rapido:
        pendencias.append({
            "id": "modelo_rapido",
            "titulo": "Definir o modelo rápido",
            "detalhe": "a mesa responde melhor com um modelo pequeno e barato",
            "como": "use 'Buscar modelos' no provedor ou aplique um perfil"})
    if len(configurados) > 1 and not fallback:
        pendencias.append({
            "id": "fallback",
            "titulo": "Definir a cadeia de fallback",
            "detalhe": "você tem mais de um provedor com chave",
            "como": "aplique um perfil para montar a ordem por custo"})
    if not idioma:
        pendencias.append({
            "id": "idioma",
            "titulo": "Idioma de saída da IA",
            "detalhe": "o padrão de fábrica é inglês; você escreve em português",
            "como": "aplique o perfil 'Pronto para Usar'"})
    return {
        "primeira_execucao": not tem_config,
        "pendencias": pendencias,
        "provedores_configurados": configurados,
        "principal": principal, "modelo_rapido": rapido,
        "modelo_profundo": profundo, "fallback": fallback,
        "orcamento_usd": orcamento,
        "bloqueio_orcamento": bloqueio_ligado(cfg),
        "perfis": {k: {"nome": v["nome"], "desc": v["desc"]}
                   for k, v in PERFIS.items()},
        "sugestao": sugestao(cfg, perfil, testes),
        "diagnostico": diagnostico(cfg, testes, uso),
    }


__all__ = ["PADROES_SEGUROS", "PERFIS", "aplicar", "bloqueio_ligado",
           "classificar_modelo", "diagnostico", "escolher_provedor", "estado",
           "ia_permitida", "provedores_configurados", "sugerir_modelos",
           "sugestao"]
