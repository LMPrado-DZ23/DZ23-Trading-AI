"""Escola de Traders (princípios do Batalhão, adaptados ao DZ23).

A Escola decide, por evidência ESTATÍSTICA, quando uma (estratégia × versão)
pode ser declarada **formada**. Formar-se é condição NECESSÁRIA — nunca
suficiente — para qualquer elegibilidade de operação real: a escola não
opera, não promove sozinha e não autoriza LIVE. Ela produz um estado
auditável que o gate de promoção e o Supervisor de Piloto consultam.

Regras herdadas do Batalhão, adaptadas aos mercados do DZ23:

- **Prova final independente.** Só valem desfechos fechados DEPOIS da
  candidatura (corte temporal estrito, `>`). Reaproveitar resultado de
  treino como prova é fraude estatística — aqui é impossível por
  construção.
- **Critério contra o ponto de equilíbrio pós-custos.** Spot exige retorno
  LÍQUIDO acima de zero (os custos reais já vieram descontados no
  desfecho). Opção binária tem outra régua: taxa de acerto acima de
  1/(1+payout), com payout ESTRESSADO — o pior caso plausível, não o
  folheto da corretora.
- **IC 95% unilateral sobre amostra ponderada** por decaimento temporal: o
  mercado de meses atrás não manda no de hoje. O tamanho efetivo da
  amostra (`n_eff`) entra no erro-padrão — poder estatístico explícito.
- **Formatura é revogável.** Novas evidências contra a estratégia
  rebaixam para `formatura_revogada` de forma automática e auditável, e o
  estado revogado não voltar atrás — só uma versão nova recomeça.
- **Estabilidade temporal obrigatória.** IC aprovado não basta: as duas
  metades temporais da prova final precisam ser positivas — resultado
  concentrado num único período não forma; o caso bloqueado segue
  coletando (bloqueio é gate, não sentença).
- **Referências iniciais**: 200 operações na parcela preliminar e 300
  novas na prova final. São piso, não garantia: quem aprova ou reprova é o
  intervalo de confiança, sempre.

Nada aqui promove trader ou autoriza capital: quem faz isso é o dono, pelo
fluxo FORMADO → ELEGÍVEL PARA LIVE → AGUARDANDO AUTORIZAÇÃO → PILOTO
AUTORIZADO (ver `piloto.py`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

Z_95 = 1.6448536269514722            # quantil unilateral de 95%
AMOSTRA_CANDIDATA = 200              # parcela preliminar (referência inicial)
AMOSTRA_PROVA = 300                  # prova final independente (referência inicial)
AMOSTRA_SEM_VANTAGEM = 300           # sem IC acima do alvo até aqui ⇒ sem_vantagem
AMOSTRA_REVOGACAO = 50               # novas evidências mínimas para revogar
ESTRESSE_PAYOUT = Decimal("0.03")    # binária: payout descontado no alvo


class EstadoDaEscola(str, Enum):
    COLETANDO = "coletando"
    SEM_VANTAGEM = "sem_vantagem"
    CANDIDATA = "candidata"
    PROVA_FINAL = "prova_final"
    FORMADA = "formada"
    REPROVADA = "reprovada"
    FORMATURA_REVOGADA = "formatura_revogada"


TERMINAIS = (EstadoDaEscola.REPROVADA, EstadoDaEscola.FORMATURA_REVOGADA)


def _consciente(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class PerfilDeMercado:
    """O que muda de um mercado para outro no critério de aprovação.

    - spot/futuros: a métrica é o retorno líquido realizado; alvo = 0.
    - opção binária: a métrica é a taxa de acerto; alvo = 1/(1+payout'),
      com payout' = payout − estresse (pior caso plausível).
    """
    nome: str = "spot"
    payout: Decimal | None = None
    estresse_payout: Decimal = ESTRESSE_PAYOUT

    @property
    def binaria(self) -> bool:
        return self.payout is not None

    @property
    def alvo(self) -> float:
        if not self.binaria:
            return 0.0
        p = max(Decimal("0.01"), Decimal(self.payout) - max(Decimal("0"),
                                                           self.estresse_payout))
        return float(Decimal("1") / (Decimal("1") + p))

    def valor(self, desfecho) -> float:
        if not self.binaria:
            return float(desfecho.retorno)
        return 1.0 if desfecho.retorno > 0 else 0.0

    def metrica(self, desfechos, agora: datetime | None = None) -> dict:
        """Média ponderada + IC 95% unilateral, com tamanho efetivo real."""
        desfechos = list(desfechos)
        pesos = [d.peso(agora) for d in desfechos]
        valores = [self.valor(d) for d in desfechos]
        sw = sum(pesos)
        sw2 = sum(p * p for p in pesos)
        n = len(valores)
        if sw <= 0:
            media = erro = 0.0
            n_efetivo = 0.0
        else:
            media = sum(v * p for v, p in zip(valores, pesos, strict=True)) / sw
            var = sum(p * (v - media) ** 2
                      for v, p in zip(valores, pesos, strict=True)) / sw
            n_efetivo = (sw * sw / sw2) if sw2 > 0 else 0.0
            erro = math.sqrt(var / n_efetivo) if n_efetivo > 0 else 0.0
        ic_inferior = media - Z_95 * erro
        ic_superior = media + Z_95 * erro
        return {
            "media": media, "erro": erro,
            "ic_inferior": ic_inferior, "ic_superior": ic_superior,
            "n_efetivo": n_efetivo, "amostras": n, "alvo": self.alvo,
            "aprovada": ic_inferior > self.alvo,
            "revogada": ic_superior < self.alvo,
            "unidade": "taxa_de_acerto" if self.binaria else "retorno_liquido",
        }


def metricas_de_formacao(desfechos, agora: datetime | None = None) -> dict:
    """Métricas §8 sobre desfechos FECHADOS já líquidos (custos incluídos).

    Mede o que a formação deve exibir — expectativa líquida, fator de
    lucro, drawdown da curva realizada, retorno ajustado ao risco e risco
    de cauda (Sharpe/Sortino por operação), estabilidade temporal (metades
    por ORDEM TEMPORAL, soma ponderada) e dependência de regime. É medição
    auditável: quem decide formação continua sendo o IC contra o alvo —
    estas métricas abrem a porta, não assinam o diploma.
    """
    desfechos = list(desfechos)
    if not desfechos:
        return {"amostras": 0, "expectativa_liquida": 0.0,
                "fator_de_lucro": None, "max_drawdown": 0.0,
                "retorno_ajustado_ao_risco": None, "sortino": None,
                "estabilidade": "indeterminada", "metades": [],
                "por_regime": {},
                "unidade": "retorno_liquido",
                "custos": ("já descontados — o desfecho é o retorno "
                           "líquido realizado")}
    agora = agora or datetime.now(timezone.utc)
    pesos = [d.peso(agora) for d in desfechos]
    valores = [float(d.retorno) for d in desfechos]
    sw = sum(pesos)
    media = sum(v * p for v, p in zip(valores, pesos, strict=True)) / sw
    var = sum(p * (v - media) ** 2
              for v, p in zip(valores, pesos, strict=True)) / sw
    desvio = math.sqrt(var)
    downside = math.sqrt(sum(p * min(v, 0.0) ** 2
                             for v, p in zip(valores, pesos, strict=True)) / sw)
    ganhos = sum(p * v for v, p in zip(valores, pesos, strict=True) if v > 0)
    perdas = sum(p * -v for v, p in zip(valores, pesos, strict=True) if v < 0)

    # Drawdown da curva ADITIVA realizada, em ordem temporal: o pico inicial
    # é o capital (1.0) — série toda perdedora fica "submersa" desde o berço.
    ordenados = sorted(desfechos, key=lambda d: _consciente(d.fechado_em))
    cum = 0.0
    pico = 1.0
    max_dd = 0.0
    for d in ordenados:
        cum += float(d.retorno)
        equity = 1.0 + cum
        pico = max(pico, equity)
        max_dd = max(max_dd, (pico - equity) / pico)

    n = len(valores)
    if n < 2:
        estabilidade = "indeterminada"
        metades: list[float] = []
    else:
        corte = n // 2
        pesos_t = [d.peso(agora) for d in ordenados]
        valores_t = [float(d.retorno) for d in ordenados]
        primeira = sum(p * v for p, v in
                       zip(pesos_t[:corte], valores_t[:corte], strict=True))
        segunda = sum(p * v for p, v in
                      zip(pesos_t[corte:], valores_t[corte:], strict=True))
        metades = [primeira, segunda]
        estabilidade = ("estavel" if primeira > 0 and segunda > 0
                        else "instavel")

    por_regime: dict[str, dict] = {}
    for d, p, v in zip(desfechos, pesos, valores, strict=True):
        item = por_regime.setdefault(
            str(d.regime), {"amostras": 0, "peso": 0.0, "soma": 0.0})
        item["amostras"] += 1
        item["peso"] += p
        item["soma"] += p * v
    for item in por_regime.values():
        item["media"] = (round(item["soma"] / item["peso"], 6)
                         if item["peso"] else 0.0)
    return {
        "amostras": n,
        "expectativa_liquida": round(media, 6),
        "fator_de_lucro": (round(ganhos / perdas, 6) if perdas > 0
                           else None),
        "max_drawdown": round(max_dd, 6),
        "retorno_ajustado_ao_risco": (round(media / desvio, 6)
                                      if desvio > 0 else None),
        "sortino": (round(media / downside, 6) if downside > 0 else None),
        "estabilidade": estabilidade,
        "metades": [round(x, 6) for x in metades],
        "por_regime": por_regime,
        "unidade": "retorno_liquido",
        "custos": "já descontados — o desfecho é o retorno líquido realizado",
    }


@dataclass(frozen=True)
class Avaliacao:
    """Retrato auditável de um aluno (estratégia × versão) na escola."""
    chave: str
    estrategia: str
    versao: str
    estado: EstadoDaEscola
    motivo: str
    amostras: int
    amostras_prova: int
    n_efetivo: float
    media: float
    ic_inferior: float
    ic_superior: float
    alvo: float
    unidade: str
    aprovada: bool
    candidatura_em: str | None
    formado_em: str | None
    atualizada_em: str
    novas_pos_formada: int = 0
    fator_de_lucro: float | None = None
    max_drawdown: float = 0.0
    retorno_ajustado_ao_risco: float | None = None
    estabilidade: str = "indeterminada"
    base_das_metricas: str = "conjunto_completo"

    def como_dict(self) -> dict:
        return {
            "chave": self.chave, "estrategia": self.estrategia,
            "versao": self.versao, "estado": self.estado.value,
            "motivo": self.motivo, "amostras": self.amostras,
            "amostras_prova": self.amostras_prova,
            "novas_pos_formada": self.novas_pos_formada,
            "n_efetivo": self.n_efetivo, "media": self.media,
            "ic_inferior": self.ic_inferior,
            "ic_superior": self.ic_superior, "alvo": self.alvo,
            "unidade": self.unidade, "aprovada": self.aprovada,
            "candidatura_em": self.candidatura_em,
            "formado_em": self.formado_em,
            "atualizada_em": self.atualizada_em,
            "fator_de_lucro": self.fator_de_lucro,
            "max_drawdown": self.max_drawdown,
            "retorno_ajustado_ao_risco": self.retorno_ajustado_ao_risco,
            "estabilidade": self.estabilidade,
            "base_das_metricas": self.base_das_metricas,
        }


class EscolaDeTraders:
    """Avalia elegibilidade por (estratégia × versão). NÃO promove ninguém."""

    CHAVE_ARMAZEM = "escola"
    MAX_EVENTOS = 500

    def __init__(self, armazem=None, tenant_id: str = "legacy", *,
                 chave_armazem: str | None = None):
        # `chave_armazem` permite UMA escola por contexto de certificação
        # (Escola Central): cada instância persiste na própria chave e nunca
        # enxerga evidência de outro contexto.
        if chave_armazem:
            self.CHAVE_ARMAZEM = chave_armazem
        self.armazem = armazem
        self.tenant_id = tenant_id
        self._matriculas: dict[str, dict] = {}
        self._carregar()

    # -- persistência ---------------------------------------------------
    def _carregar(self) -> None:
        if self.armazem is None:
            return
        dados = self.armazem.carregar(self.tenant_id, self.CHAVE_ARMAZEM)
        if isinstance(dados, dict):
            conteudo = dados.get("matriculas")
            if isinstance(conteudo, dict):
                self._matriculas = conteudo

    def salvar(self) -> None:
        if self.armazem is None:
            return
        self.armazem.salvar(self.tenant_id, self.CHAVE_ARMAZEM,
                            {"matriculas": self._matriculas})

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _chave(estrategia: str, versao: str) -> str:
        return f"{estrategia}@{versao or '1'}"

    def _matricula(self, chave: str) -> dict:
        m = self._matriculas.setdefault(chave, {})
        m.setdefault("estado", EstadoDaEscola.COLETANDO.value)
        m.setdefault("eventos", [])
        return m

    def _transitar(self, chave: str, novo: EstadoDaEscola, motivo: str,
                   agora: datetime, **campos) -> None:
        m = self._matricula(chave)
        anterior = m.get("estado", EstadoDaEscola.COLETANDO.value)
        m["estado"] = novo.value
        m["motivo"] = motivo
        m["atualizada_em"] = agora.isoformat()
        m.update(campos)
        m["eventos"].append({
            "quando": agora.isoformat(), "de": anterior,
            "para": novo.value, "motivo": motivo[:400]})
        if len(m["eventos"]) > self.MAX_EVENTOS:
            m["eventos"] = m["eventos"][-self.MAX_EVENTOS:]
        self.salvar()

    # -- avaliação ------------------------------------------------------
    def avaliar(self, *, estrategia: str, versao: str = "1", desfechos,
                perfil: PerfilDeMercado | None = None,
                agora: datetime | None = None) -> Avaliacao:
        """Aplica as regras da escola e devolve o retrato ATUALIZADO.

        A ordem importa: estado terminal primeiro (evidência já julgada),
        depois vigia de revogação (formada), depois parcela preliminar
        (candidatura) e, por fim, a prova independente.
        """
        agora = agora or datetime.now(timezone.utc)
        perfil = perfil or PerfilDeMercado()
        desfechos = list(desfechos)
        chave = self._chave(estrategia, versao)
        m = self._matricula(chave)
        estado = EstadoDaEscola(m.get("estado", EstadoDaEscola.COLETANDO.value))

        # 1) terminais: reprovada / formatura revogada não voltam atrás.
        if estado in TERMINAIS:
            return self._resumo(chave, estrategia, versao, m, perfil,
                                desfechos, agora, [])

        # 2) formada: vigia permanente de revogação.
        if estado is EstadoDaEscola.FORMADA:
            formado_em = _consciente(datetime.fromisoformat(m["formado_em"]))
            novas = [d for d in desfechos
                     if _consciente(d.fechado_em) > formado_em]
            if len(novas) >= AMOSTRA_REVOGACAO:
                r = perfil.metrica(novas, agora)
                if r["revogada"]:
                    self._transitar(
                        chave, EstadoDaEscola.FORMATURA_REVOGADA,
                        f"novas evidências ({len(novas)} operações) derrubaram "
                        f"o limite superior do IC ({r['ic_superior']:.4f}) "
                        f"abaixo do alvo {r['alvo']:.4f} — elegibilidade "
                        "revogada automaticamente", agora)
            av = self._resumo(chave, estrategia, versao, m, perfil,
                              desfechos, agora, [])
            return replace(av, novas_pos_formada=len(novas))

        # 3) sem candidatura: parcela preliminar de coleta.
        if not m.get("candidatura_em"):
            r = perfil.metrica(desfechos, agora)
            n = len(desfechos)
            if n >= AMOSTRA_CANDIDATA and r["aprovada"]:
                self._transitar(
                    chave, EstadoDaEscola.CANDIDATA,
                    f"parcela preliminar de {n} operações: IC 95% inferior "
                    f"{r['ic_inferior']:.4f} acima do alvo {r['alvo']:.4f} "
                    f"(unidade: {r['unidade']}) — inicia prova final "
                    f"independente de {AMOSTRA_PROVA} operações", agora,
                    candidatura_em=agora.isoformat())
            elif n >= AMOSTRA_SEM_VANTAGEM and estado is not EstadoDaEscola.SEM_VANTAGEM:
                self._transitar(
                    chave, EstadoDaEscola.SEM_VANTAGEM,
                    f"{n} operações sem IC acima do alvo "
                    f"(limite inferior {r['ic_inferior']:.4f} ≤ "
                    f"{r['alvo']:.4f}) — sem vantagem comprovada; a escola "
                    "segue coletando", agora)
            return self._resumo(chave, estrategia, versao, m, perfil,
                                desfechos, agora, [])

        # 4) candidata / prova final: só desfechos POSTERIORES à candidatura.
        candidatura_em = _consciente(datetime.fromisoformat(m["candidatura_em"]))
        prova = [d for d in desfechos
                 if _consciente(d.fechado_em) > candidatura_em]
        if len(prova) >= AMOSTRA_PROVA:
            r = perfil.metrica(prova, agora)
            metricas = metricas_de_formacao(prova, agora)
            if r["aprovada"] and metricas["estabilidade"] == "estavel":
                self._transitar(
                    chave, EstadoDaEscola.FORMADA,
                    f"prova final independente: {len(prova)} operações novas, "
                    f"IC 95% inferior {r['ic_inferior']:.4f} acima do alvo "
                    f"{r['alvo']:.4f}; estabilidade temporal confirmada "
                    "(duas metades positivas)", agora,
                    formado_em=agora.isoformat())
            elif r["aprovada"]:
                motivo = (
                    "prova final com IC 95% aprovado, mas estabilidade "
                    "temporal não confirmada (metades não ambas positivas) — "
                    "formação BLOQUEADA; a escola segue coletando")
                if m.get("motivo") != motivo:
                    self._transitar(chave, EstadoDaEscola.PROVA_FINAL,
                                    motivo, agora)
            else:
                self._transitar(
                    chave, EstadoDaEscola.REPROVADA,
                    f"prova final de {len(prova)} operações não superou o "
                    f"alvo (limite inferior {r['ic_inferior']:.4f} ≤ "
                    f"{r['alvo']:.4f}) — reprovada", agora)
        elif prova and estado is not EstadoDaEscola.PROVA_FINAL:
            self._transitar(
                chave, EstadoDaEscola.PROVA_FINAL,
                f"prova final em andamento ({len(prova)}/{AMOSTRA_PROVA} "
                "operações independentes)", agora)
        return self._resumo(chave, estrategia, versao, m, perfil,
                            desfechos, agora, prova)

    def _resumo(self, chave: str, estrategia: str, versao: str, m: dict,
                perfil: PerfilDeMercado, desfechos, agora: datetime,
                prova) -> Avaliacao:
        # Na prova final, o número reportado tem de ser o MESMO que decide a
        # formação (só a prova independente) — nunca o conjunto treino+prova,
        # que dilui instabilidade e engana quem audita o estado.
        base = list(prova) if prova else list(desfechos)
        base_nome = "prova_final" if prova else "conjunto_completo"
        r = perfil.metrica(base, agora)
        metricas = metricas_de_formacao(base, agora)
        return Avaliacao(
            chave=chave, estrategia=estrategia, versao=versao or "1",
            estado=EstadoDaEscola(m.get("estado",
                                        EstadoDaEscola.COLETANDO.value)),
            motivo=m.get("motivo", "em coleta — sem julgamento ainda"),
            amostras=len(desfechos), amostras_prova=len(prova),
            n_efetivo=round(r["n_efetivo"], 2), media=round(r["media"], 6),
            ic_inferior=round(r["ic_inferior"], 6),
            ic_superior=round(r["ic_superior"], 6),
            alvo=r["alvo"], unidade=r["unidade"], aprovada=r["aprovada"],
            candidatura_em=m.get("candidatura_em"),
            formado_em=m.get("formado_em"),
            atualizada_em=m.get("atualizada_em", agora.isoformat()),
            fator_de_lucro=metricas["fator_de_lucro"],
            max_drawdown=metricas["max_drawdown"],
            retorno_ajustado_ao_risco=metricas["retorno_ajustado_ao_risco"],
            estabilidade=metricas["estabilidade"],
            base_das_metricas=base_nome)

    # -- consulta -------------------------------------------------------
    def estado_de(self, estrategia: str,
                  versao: str = "1") -> EstadoDaEscola | None:
        m = self._matriculas.get(self._chave(estrategia, versao))
        return EstadoDaEscola(m["estado"]) if m else None

    def elegivel_para_live(self, estrategia: str,
                           versao: str = "1") -> tuple[bool, str]:
        """FORMADO ⇒ elegível ao PROCESSO de autorização — nunca ativa nada."""
        chave = self._chave(estrategia, versao)
        m = self._matriculas.get(chave)
        if not m:
            return False, ("sem matrícula na escola — nenhuma evidência "
                           "registrada para esta versão")
        estado = EstadoDaEscola(m.get("estado", EstadoDaEscola.COLETANDO.value))
        if estado is EstadoDaEscola.FORMADA:
            return True, ("formada em " + str(m.get("formado_em")) +
                          ": elegível para o processo de autorização do "
                          "proprietário — a escola nunca ativa operação real")
        return False, (f"estado {estado.value}: "
                       + m.get("motivo", "sem evidência suficiente"))

    # -- relatórios -----------------------------------------------------
    def relatorio(self) -> list[dict]:
        """Relatório individual por aluno: estado, motivo e histórico."""
        saida = []
        for chave in sorted(self._matriculas):
            m = self._matriculas[chave]
            estrategia, _, versao = chave.partition("@")
            saida.append({
                "chave": chave, "estrategia": estrategia,
                "versao": versao or "1",
                "estado": m.get("estado", EstadoDaEscola.COLETANDO.value),
                "motivo": m.get("motivo", "em coleta — sem julgamento ainda"),
                "candidatura_em": m.get("candidatura_em"),
                "formado_em": m.get("formado_em"),
                "atualizada_em": m.get("atualizada_em"),
                "eventos": m.get("eventos", [])[-10:],
                "aviso": ("a escola avalia evidência; quem autoriza operação "
                          "real é o proprietário"),
            })
        return saida

    def progresso(self) -> dict:
        por_estado = {e.value: 0 for e in EstadoDaEscola}
        for m in self._matriculas.values():
            e = m.get("estado", EstadoDaEscola.COLETANDO.value)
            por_estado[e] = por_estado.get(e, 0) + 1
        return {"total": len(self._matriculas), "por_estado": por_estado,
                "referencias": {"parcela_preliminar": AMOSTRA_CANDIDATA,
                                "prova_final": AMOSTRA_PROVA,
                                "sem_vantagem": AMOSTRA_SEM_VANTAGEM,
                                "revogacao": AMOSTRA_REVOGACAO}}


def comparar_com_controle(desfechos, desfechos_controle,
                          perfil: PerfilDeMercado | None = None,
                          agora: datetime | None = None) -> dict:
    """Benchmark permanente: a estratégia supera o controle aleatório?

    Comparação conservadora: só declara vantagem quando os ICs NÃO se
    sobrepõem. Empate estatístico é empate — sem vantagem demonstrada.
    """
    perfil = perfil or PerfilDeMercado()
    a = perfil.metrica(desfechos, agora)
    b = perfil.metrica(desfechos_controle, agora)
    if a["ic_inferior"] > b["ic_superior"]:
        vantagem = "estrategia"
        veredito = (f"estratégia acima do controle aleatório "
                    f"({a['media']:.4f} vs {b['media']:.4f})")
    elif b["ic_inferior"] > a["ic_superior"]:
        vantagem = "controle"
        veredito = (f"controle aleatório SUPEROU a estratégia "
                    f"({b['media']:.4f} vs {a['media']:.4f}) — sem vantagem")
    else:
        vantagem = "inconclusivo"
        veredito = ("intervalos se sobrepõem — sem vantagem demonstrada "
                    "sobre o aleatório")
    return {"estrategia": a, "controle": b, "vantagem": vantagem,
            "veredito": veredito,
            "aviso": ("o controle aleatório é o piso: estratégia que não o "
                      "supera não tem vantagem para vender")}


__all__ = [
    "Avaliacao", "EscolaDeTraders", "EstadoDaEscola", "PerfilDeMercado",
    "comparar_com_controle", "metricas_de_formacao", "Z_95",
    "AMOSTRA_CANDIDATA", "AMOSTRA_PROVA", "AMOSTRA_SEM_VANTAGEM",
    "AMOSTRA_REVOGACAO",
]
