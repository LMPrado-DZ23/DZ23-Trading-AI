"""Evolução autônoma de estratégias (E5, missão da evolução).

A evolução acontece pelo único caminho honesto: memória do que ACONTECEU,
comparação estatística entre parâmetros, e prova antes de trocar. Nada de
"achei melhor" — o challenger só vira champion com significância.

O que este módulo faz:
- **Memória auditável por trader** (reusa `learning.py`/`Laboratorio`):
  decisões, P&L líquido, custos e regimes de cada operação FECHADA — e a
  causa de cada perda classificada por regra DETERMINÍSTICA: risco normal /
  erro de execução / dados inadequados / regra descalibrada / mudança de
  regime. Sem LLM decidindo "o que deu errado".
- **Champion/challenger com parâmetros congelados**: cada experimento é
  pré-registrado com assinatura SHA-256 (padrão do Laboratório) — rodar,
  espiar e afinar é impossível; execução única, reexecução só explícita.
- **Walk-forward fora da amostra**: os trades são medidos em janelas
  sequenciais no fim da série, com o executável vendo apenas o passado
  (`serie[:i+1]`); o futuro só mede o resultado.
- **Bonferroni por família** (herdado do BT-2) e **benchmark** (comprado e
  segurado na própria janela) — quem testa muito precisa de prova mais forte.
- **Métricas de rentabilidade, não de vaidade**: expectativa líquida,
  drawdown, estabilidade e custos. Taxa de acerto NÃO decide nada aqui.

Os freios que este módulo NUNCA solta:
- Promoção automática **somente dentro de experimentos e PAPER**. LIVE
  continua exigindo a cadeia da Escola de Traders + aprovação explícita do
  dono — `promover(modo="live")` é recusado por construção.
- Nenhuma linha aqui altera código de execução, permissões ou limites.
- Rollback por deterioração volta para parâmetros que JÁ rodaram (seguro
  por construção); sem versão anterior para reverter, o trader é suspenso
  e entra no vigia (bloqueio de novas entradas, fail-closed).
- Deterioração exige amostra madura e expectativa líquida negativa — um dia
  ruim não derruba ninguém, e reabilitação exige motivo registrado.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from .costs import custo_de_operar
from .laboratorio import ALPHA, EXECUTORES, VALOR_REFERENCIA, Hipotese
from .learning import AMOSTRA_MINIMA, Desfecho

# Causas determinísticas de perda — em ordem de prioridade de diagnóstico.
CAUSA_RISCO_NORMAL = "risco_normal"
CAUSA_ERRO_EXECUCAO = "erro_execucao"
CAUSA_DADOS_INADEQUADOS = "dados_inadequados"
CAUSA_REGRA_DESCALIBRADA = "regra_descalibrada"
CAUSA_MUDANCA_REGIME = "mudanca_de_regime"

# 0,3% ≈ o custo de ida e volta em cripto líquido: acima disso a perda tem
# cara de execução (slippage/fill), não de tese.
LIMIAR_DESVIO_EXECUCAO = 0.003

AVISO = ("evolução NÃO altera código de execução, permissões nem limites; "
         "promoção automática só existe em PAPER; LIVE segue exigindo a "
         "Escola de Traders e aprovação explícita do dono")


# ------------------------------------------------------- causa da perda
@dataclass(frozen=True)
class ContextoDaOperacao:
    """O que cerca a operação FECHADA — além do desfecho em si."""
    desvio_de_execucao: float = 0.0   # |executado - esperado| / esperado
    dados_atrasados: bool = False     # feed velho/incompleto na decisão
    regime_na_saida: str = ""         # regime observado ao fechar (se conhecido)


def classificar_causa(desfecho: Desfecho, *,
                      contexto: ContextoDaOperacao | None = None,
                      memoria=None) -> tuple[str, str]:
    """Classificação DETERMINÍSTICA da causa de uma perda, por regras
    explícitas de prioridade: execução → dados → regime → regra → risco.

    Ganho não tem causa de perda a classificar — devolve causa vazia.
    """
    if desfecho.retorno >= 0:
        return "", "ganho realizado — sem causa de perda a classificar"
    ctx = contexto or ContextoDaOperacao()
    if ctx.desvio_de_execucao >= LIMIAR_DESVIO_EXECUCAO:
        return CAUSA_ERRO_EXECUCAO, (
            f"desvio de execução {ctx.desvio_de_execucao:.2%} ≥ limiar "
            f"{LIMIAR_DESVIO_EXECUCAO:.2%} — a perda tem cara de execução, "
            "não de tese")
    if ctx.dados_atrasados:
        return CAUSA_DADOS_INADEQUADOS, (
            "decisão tomada com dados atrasados/incompletos")
    if ctx.regime_na_saida and ctx.regime_na_saida != desfecho.regime:
        return CAUSA_MUDANCA_REGIME, (
            f"regime mudou entre entrada ({desfecho.regime}) e saída "
            f"({ctx.regime_na_saida})")
    if memoria is not None:
        st = memoria.estatistica(desfecho.estrategia, desfecho.regime)
        if st.maduro and st.retorno_medio < 0:
            return CAUSA_REGRA_DESCALIBRADA, (
                f"regra com histórico maduro e negativo no regime "
                f"({st.amostras} casos, retorno médio {st.retorno_medio:+.2%}) "
                "— parâmetros, não azar")
    return CAUSA_RISCO_NORMAL, (
        "perda dentro do risco esperado (sem sinal de execução, dados, "
        "regime ou regra)")


# ---------------------------------------------------------------- diário
@dataclass(frozen=True)
class RegistroDoDiario:
    """Uma operação fechada vista pela auditoria do trader."""
    agent_id: str
    estrategia: str
    versao: str
    regime: str
    instrumento: str
    decisao: str
    retorno_liquido: Decimal    # realizado, líquido de custos
    custo: Decimal              # fração paga em taxas/spread/slippage
    quando: datetime
    causa: str                  # "" quando não é perda
    motivo_da_causa: str
    contexto: dict = field(default_factory=dict)


def _registro_para_dict(r: RegistroDoDiario) -> dict:
    return {"agent_id": r.agent_id, "estrategia": r.estrategia,
            "versao": r.versao, "regime": r.regime,
            "instrumento": r.instrumento, "decisao": r.decisao,
            "retorno_liquido": str(r.retorno_liquido), "custo": str(r.custo),
            "quando": r.quando.isoformat(), "causa": r.causa,
            "motivo_da_causa": r.motivo_da_causa, "contexto": dict(r.contexto)}


def _registro_de_dict(d: dict) -> RegistroDoDiario:
    return RegistroDoDiario(
        str(d["agent_id"]), str(d["estrategia"]), str(d.get("versao", "1")),
        str(d.get("regime", "")), str(d.get("instrumento", "")),
        str(d.get("decisao", "")), Decimal(str(d["retorno_liquido"])),
        Decimal(str(d.get("custo", "0"))),
        datetime.fromisoformat(str(d["quando"])), str(d.get("causa", "")),
        str(d.get("motivo_da_causa", "")),
        dict(d.get("contexto") or {}))


# ------------------------------------------------------- experimento
@dataclass(frozen=True)
class Experimento:
    """Champion vs challenger com parâmetros CONGELADOS na assinatura.

    Alterar qualquer campo (inclusive o id) muda a assinatura — executar uma
    versão alterada de um experimento pré-registrado é impossível.
    """
    id: str
    trader: str
    champion: Hipotese
    challenger: Hipotese
    titulo: str = ""
    familia: str = ""
    criado_em: str = ""

    def assinatura(self) -> str:
        corpo = {"id": self.id, "trader": self.trader,
                 "titulo": self.titulo, "familia": self.familia,
                 "champion": self.champion.assinatura(),
                 "challenger": self.challenger.assinatura()}
        canonico = json.dumps(corpo, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False)
        return sha256(canonico.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MetricasDoLado:
    """Rentabilidade — nunca taxa de acerto (acerto é vaidade, expectativa
    é dinheiro)."""
    operacoes: int
    expectativa_liquida: float
    drawdown: float
    estabilidade: float
    custos: float


@dataclass(frozen=True)
class VereditoDoExperimento:
    assinatura: str
    id: str
    trader: str
    familia: str
    familia_n: int
    alpha_ajustado: float
    p_valor: float
    janelas: int
    champion: MetricasDoLado
    challenger: MetricasDoLado
    benchmark: MetricasDoLado
    aprovado: bool
    executada_em: str
    motivo: str

    @property
    def aviso(self) -> str:
        return AVISO


def _hipotese_para_dict(h: Hipotese) -> dict:
    return {"id": h.id, "titulo": h.titulo, "familia": h.familia,
            "tipo_de_regra": h.tipo_de_regra,
            "parametros": [[k, v] for k, v in h.parametros],
            "instrumentos": list(h.instrumentos),
            "janela_treino_barras": h.janela_treino_barras,
            "janela_teste_barras": h.janela_teste_barras,
            "horizonte_barras": h.horizonte_barras, "exchange": h.exchange,
            "assinatura": h.assinatura()}


def _janelas_de_avaliacao(n: int, hipoteses: list[Hipotese],
                          n_janelas: int) -> list[tuple[int, int]]:
    """Janelas de teste sequenciais no FIM da série (fora da amostra) —
    UMA régua para champion e challenger: treino exigido é o maior dos dois,
    zona de teste a menor, horizonte o maior. A última barra de entrada é
    `n - horizonte`: entrada precisa de barras futuras para ter desfecho —
    o futuro mede, não decide.
    """
    treino = max(h.janela_treino_barras for h in hipoteses)
    teste = min(h.janela_teste_barras for h in hipoteses)
    horizonte = max(int(h.horizonte_barras) for h in hipoteses)
    fim = n - horizonte  # índice de entrada exclusivo
    inicio = max(treino, n - teste)
    if fim - inicio < 2:
        return []
    n = max(1, min(n_janelas, (fim - inicio) // 2))
    tam = (fim - inicio) // n
    saida = []
    for k in range(n):
        a = inicio + k * tam
        b = inicio + (k + 1) * tam if k < n - 1 else fim
        saida.append((a, b))
    return saida


def _metricas(trades: list[float], custo_unit: float,
              por_janela: list[list[float]]) -> MetricasDoLado:
    """Métricas de um lado: expectativa líquida, drawdown multiplicativo,
    estabilidade (fração de janelas positivas) e custos pagos."""
    ops = len(trades)
    esp = sum(trades) / ops if ops else 0.0
    # drawdown sobre a curva composta, na ordem cronológica dos trades
    pico, eq, dd = 1.0, 1.0, 0.0
    for r in trades:
        eq *= (1.0 + r)
        pico = max(pico, eq)
        dd = max(dd, 1.0 - eq / pico if pico > 0 else 0.0)
    positivas = sum(1 for j in por_janela if j and sum(j) / len(j) > 0)
    estabilidade = positivas / len(por_janela) if por_janela else 0.0
    return MetricasDoLado(operacoes=ops,
                          expectativa_liquida=round(esp, 6),
                          drawdown=round(dd, 6),
                          estabilidade=round(estabilidade, 4),
                          custos=round(custo_unit * ops, 6))


def _o_que_mudou(registro: dict) -> dict:
    """Diff dos parâmetros champion → challenger — a base do relatório
    'o que mudou, por quê, evidência'."""
    champ = dict(registro["champion"]["parametros"])
    chall = dict(registro["challenger"]["parametros"])
    chaves = set(champ) | set(chall)
    return {k: {"de": champ.get(k), "para": chall.get(k)}
            for k in sorted(chaves) if champ.get(k) != chall.get(k)}


# ------------------------------------------------------------- evolução
class EvolucaoAutonoma:
    """Diário auditável + experimentos + promoção PAPER + vigia de
    deterioração. Persistida no armazém (chave "evolucao")."""

    CHAVE_ARMAZEM = "evolucao"

    def __init__(self, caminho: str | Path | None = None, *,
                 armazem=None, tenant_id: str = "legacy", memoria=None):
        self.caminho = Path(caminho) if caminho else None
        self.armazem = armazem
        self.tenant_id = tenant_id
        # memória do aprendizado (para diagnosticar "regra descalibrada");
        # NÃO é escrita aqui — o diário é a auditoria própria da evolução.
        self.memoria = memoria
        self._diario: list[RegistroDoDiario] = []
        self._experimentos: dict[str, dict] = {}
        self._resultados: dict[str, dict] = {}
        self._suspensoes: dict[str, str] = {}
        self._versoes: dict[str, dict] = {}
        self._eventos: list[dict] = []
        self._carregar()

    # -- persistência --------------------------------------------------
    def _carregar(self) -> None:
        dados = None
        if self.armazem is not None:
            dados = self.armazem.carregar(self.tenant_id, self.CHAVE_ARMAZEM)
        if dados is None:
            if not (self.caminho and self.caminho.exists()):
                return
            try:
                dados = json.loads(self.caminho.read_text())
            except ValueError:
                return
        for d in dados.get("diario", []):
            try:
                self._diario.append(_registro_de_dict(d))
            except (KeyError, ValueError, TypeError):
                continue
        if isinstance(dados.get("experimentos"), dict):
            self._experimentos = dados["experimentos"]
        if isinstance(dados.get("resultados"), dict):
            self._resultados = dados["resultados"]
        if isinstance(dados.get("suspensoes"), dict):
            self._suspensoes = dados["suspensoes"]
        if isinstance(dados.get("versoes"), dict):
            self._versoes = dados["versoes"]
        if isinstance(dados.get("eventos"), list):
            self._eventos = dados["eventos"]

    def salvar(self) -> None:
        payload = {
            "diario": [_registro_para_dict(r) for r in self._diario[-2000:]],
            "experimentos": self._experimentos,
            "resultados": self._resultados,
            "suspensoes": self._suspensoes,
            "versoes": self._versoes,
            "eventos": self._eventos[-500:],
        }
        if self.armazem is not None:
            self.armazem.salvar(self.tenant_id, self.CHAVE_ARMAZEM, payload)
            return
        if not self.caminho:
            return
        from .storage import escrever_json_atomico
        escrever_json_atomico(self.caminho, payload)

    # -- memória auditável ---------------------------------------------
    def registrar_operacao(self, desfecho: Desfecho, *, decisao: str = "",
                           custo: Decimal = Decimal("0"),
                           contexto: ContextoDaOperacao | None = None
                           ) -> RegistroDoDiario:
        """Uma operação FECHADA no diário do trader — com causa classificada
        por regra. Só desfecho realizado entra; posição aberta é esperança."""
        ctx = contexto or ContextoDaOperacao()
        causa, motivo = classificar_causa(desfecho, contexto=ctx,
                                          memoria=self.memoria)
        registro = RegistroDoDiario(
            agent_id=desfecho.agente or desfecho.estrategia,
            estrategia=desfecho.estrategia, versao=desfecho.versao or "1",
            regime=desfecho.regime, instrumento=desfecho.instrumento,
            decisao=decisao, retorno_liquido=desfecho.retorno,
            custo=custo, quando=desfecho.fechado_em, causa=causa,
            motivo_da_causa=motivo, contexto=asdict(ctx))
        self._diario.append(registro)
        self.salvar()
        return registro

    def diario(self, agent_id: str | None = None) -> list[dict]:
        return [_registro_para_dict(r) for r in self._diario
                if agent_id is None or r.agent_id == agent_id]

    def desempenho(self, agent_id: str) -> dict:
        entradas = [r for r in self._diario if r.agent_id == agent_id]
        ops = len(entradas)
        esp = (sum(float(r.retorno_liquido) for r in entradas) / ops
               if ops else 0.0)
        causas = Counter(r.causa for r in entradas if r.causa)
        regimes = Counter(r.regime for r in entradas)
        return {"agent_id": agent_id, "operacoes": ops,
                "expectativa_liquida": round(esp, 6),
                "custos_total": round(sum(float(r.custo) for r in entradas), 6),
                "causas": dict(causas), "regimes": dict(regimes),
                "suspenso": self._suspensoes.get(agent_id, ""),
                "versao": (self._versoes.get(agent_id) or {}).get("atual", "")}

    # -- versões -------------------------------------------------------
    def definir_base(self, agent_id: str, hipotese: Hipotese) -> str:
        """Registra a versão de partida do trader (sem promoção — a base é
        ponto de partida auditável, não conquista)."""
        assinatura = hipotese.assinatura()
        estado = self._versoes.setdefault(
            agent_id, {"atual": "", "anterior": "", "hipoteses": {},
                       "marcador": 0})
        estado["atual"] = assinatura
        estado["anterior"] = ""
        estado["hipoteses"][assinatura] = _hipotese_para_dict(hipotese)
        self.salvar()
        return assinatura

    def versao_vigente(self, agent_id: str) -> dict | None:
        estado = self._versoes.get(agent_id)
        if not estado:
            return None
        atual = estado.get("atual", "")
        return {"assinatura": atual, "anterior": estado.get("anterior", ""),
                "hipotese": (estado.get("hipoteses") or {}).get(atual, {})}

    # -- experimentos --------------------------------------------------
    def registrar_experimento(self, experimento: Experimento,
                              agora: datetime | None = None) -> str:
        """Congela o experimento no livro. Idempotente por assinatura."""
        agora = agora or datetime.now(timezone.utc)
        if (experimento.champion.assinatura()
                == experimento.challenger.assinatura()):
            raise ValueError(
                "champion e challenger idênticos — experimento sem mudança "
                "não é experimento")
        assinatura = experimento.assinatura()
        if assinatura not in self._experimentos:
            self._experimentos[assinatura] = {
                "id": experimento.id, "titulo": experimento.titulo,
                "trader": experimento.trader,
                "familia": experimento.familia or experimento.trader,
                "criado_em": agora.isoformat(),
                "champion": _hipotese_para_dict(experimento.champion),
                "challenger": _hipotese_para_dict(experimento.challenger),
                "assinatura_champion": experimento.champion.assinatura(),
                "assinatura_challenger": experimento.challenger.assinatura()}
            self.salvar()
        return assinatura

    def familia_n(self, familia: str) -> int:
        return sum(1 for r in self._experimentos.values()
                   if r.get("familia") == familia)

    def executar_experimento(self, experimento: Experimento,
                             series: dict[str, list[float]], *,
                             janelas: int = 4, forcar: bool = False,
                             agora: datetime | None = None
                             ) -> VereditoDoExperimento:
        """Walk-forward fora da amostra: champion vs challenger vs benchmark,
        com Bonferroni por família. Roda UMA vez; repetir exige forcar=True."""
        agora = agora or datetime.now(timezone.utc)
        if int(janelas) < 1:
            raise ValueError("janelas deve ser >= 1")
        assinatura = experimento.assinatura()
        registro = self._experimentos.get(assinatura)
        if registro is None:
            raise ValueError(
                "experimento não pré-registrado — o pré-registro vem ANTES "
                "de olhar os dados; experimento alterado não é o registrado")
        if assinatura in self._resultados and not forcar:
            raise RuntimeError(
                "experimento já executado — reexecução só com forcar=True "
                "(anti p-hacking)")
        executor_c = EXECUTORES.get(experimento.champion.tipo_de_regra)
        executor_l = EXECUTORES.get(experimento.challenger.tipo_de_regra)
        if executor_c is None or executor_l is None:
            raise ValueError("regra desconhecida no experimento")
        h = int(experimento.champion.horizonte_barras)
        h_l = int(experimento.challenger.horizonte_barras)
        if h < 1 or h_l < 1:
            raise ValueError("horizonte_barras deve ser >= 1")

        pares: list[tuple[list[float], list[float], float]] = []
        for instrumento, brutos in series.items():
            closes = [float(c) for c in (brutos or [])]
            if len(closes) <= max(
                    experimento.champion.janela_treino_barras,
                    experimento.challenger.janela_treino_barras) + max(h, h_l):
                continue
            custo = float(custo_de_operar(
                instrumento, experimento.challenger.exchange,
                VALOR_REFERENCIA).ida_e_volta)
            for a, b in _janelas_de_avaliacao(
                    len(closes),
                    [experimento.champion, experimento.challenger],
                    int(janelas)):
                nets_c = [((closes[i + h] - closes[i]) / closes[i]) - custo
                          for i in range(a, b)
                          if executor_c(closes, i,
                                        experimento.champion.parametros_dict) > 0]
                nets_l = [((closes[i + h_l] - closes[i]) / closes[i]) - custo
                          for i in range(a, b)
                          if executor_l(closes, i,
                                        experimento.challenger.parametros_dict) > 0]
                bench = ((closes[b - 1] - closes[a]) / closes[a]
                         if b - 1 > a else 0.0) - custo
                pares.append((nets_c, nets_l, bench))

        trades_c = [t for p in pares for t in p[0]]
        trades_l = [t for p in pares for t in p[1]]
        custo_ref = 0.0
        if series:
            primeiro = next(iter(series))
            custo_ref = float(custo_de_operar(
                primeiro, experimento.challenger.exchange,
                VALOR_REFERENCIA).ida_e_volta)
        m_champ = _metricas(trades_c, custo_ref, [p[0] for p in pares])
        m_chall = _metricas(trades_l, custo_ref, [p[1] for p in pares])
        m_bench = _metricas([p[2] for p in pares], 0.0,
                            [[p[2]] for p in pares])

        familia = registro.get("familia") or experimento.trader
        familia_n = max(1, self.familia_n(familia))
        alpha_ajustado = ALPHA / familia_n
        nl = len(trades_l)
        if nl < 2 or len(pares) < 2:
            p_valor = 1.0
        else:
            difs = []
            for nets_c, nets_l, _ in pares:
                ec = sum(nets_c) / len(nets_c) if nets_c else 0.0
                el = sum(nets_l) / len(nets_l) if nets_l else 0.0
                difs.append(el - ec)
            media = sum(difs) / len(difs)
            var = sum((d - media) ** 2 for d in difs) / (len(difs) - 1)
            sd = math.sqrt(var)
            if sd <= 0:
                p_valor = 0.0 if media > 0 else 1.0
            else:
                z = media / (sd / math.sqrt(len(difs)))
                p_valor = 0.5 * math.erfc(z / math.sqrt(2))
        aprovado = bool(
            p_valor < alpha_ajustado and nl >= 2
            and m_chall.expectativa_liquida > 0
            and m_chall.expectativa_liquida > m_champ.expectativa_liquida
            and m_chall.estabilidade > 0.5)
        if nl < 2:
            motivo = "sem operações no período de teste — nada a julgar"
        elif nl and aprovado:
            motivo = (
                f"challenger supera o champion com significância "
                f"(expectativa líquida {m_chall.expectativa_liquida:+.2%} vs "
                f"{m_champ.expectativa_liquida:+.2%}, p={p_valor:.2e} < "
                f"{alpha_ajustado:.2e} após Bonferroni familia={familia_n}; "
                f"benchmark {m_bench.expectativa_liquida:+.2%}) — segue para "
                "promoção PAPER")
        elif p_valor < alpha_ajustado:
            motivo = (
                f"significância existe mas os freios seguraram (expectativa "
                f"{m_chall.expectativa_liquida:+.2%} vs "
                f"{m_champ.expectativa_liquida:+.2%}, estabilidade "
                f"{m_chall.estabilidade:.0%}) — permanece como está")
        else:
            motivo = (
                f"challenger não supera o champion com significância "
                f"(expectativa líquida {m_chall.expectativa_liquida:+.2%} vs "
                f"{m_champ.expectativa_liquida:+.2%}, p={p_valor:.3f} ≥ "
                f"{alpha_ajustado:.3f})")
        resultado = VereditoDoExperimento(
            assinatura=assinatura, id=experimento.id,
            trader=experimento.trader, familia=familia,
            familia_n=familia_n, alpha_ajustado=alpha_ajustado,
            p_valor=round(p_valor, 8), janelas=len(pares),
            champion=m_champ, challenger=m_chall, benchmark=m_bench,
            aprovado=aprovado, executada_em=agora.isoformat(), motivo=motivo)
        gravado = asdict(resultado)
        gravado["aviso"] = resultado.aviso
        gravado["forcada"] = bool(assinatura in self._resultados and forcar)
        self._resultados[assinatura] = gravado
        self.salvar()
        return resultado

    # -- promoção (SÓ PAPER) -------------------------------------------
    def promover(self, experimento: Experimento, *, modo: str = "paper",
                 agora: datetime | None = None) -> dict:
        """Promove o challenger a champion — SOMENTE em PAPER e com veredito
        aprovado. LIVE exige a Escola de Traders e o dono; aqui é recusado."""
        if str(modo).lower() != "paper":
            raise ValueError(
                "promoção automática só existe em PAPER — LIVE exige a "
                "Escola de Traders e aprovação explícita do dono")
        assinatura = experimento.assinatura()
        registro = self._experimentos.get(assinatura)
        if registro is None:
            raise ValueError("experimento não pré-registrado")
        resultado = self._resultados.get(assinatura)
        if resultado is None:
            raise RuntimeError(
                "experimento ainda não executado — sem veredito não há "
                "promoção")
        if not resultado.get("aprovado"):
            raise ValueError(
                "veredito não aprovado — promoção exige prova estatística")
        return self._promover_registro(registro, resultado, assinatura,
                                       agora or datetime.now(timezone.utc))

    def _promover_registro(self, registro: dict, resultado: dict,
                           assinatura: str, agora: datetime) -> dict:
        agent_id = registro["trader"]
        nova = registro["assinatura_challenger"]
        estado = self._versoes.setdefault(
            agent_id, {"atual": "", "anterior": "", "hipoteses": {},
                       "marcador": 0})
        if estado.get("atual") == nova:
            return {"agent_id": agent_id, "assinatura": nova,
                    "de": estado.get("anterior", ""), "ja_promovido": True}
        anterior = estado.get("atual") or ""
        estado["anterior"] = anterior
        estado["atual"] = nova
        estado.setdefault("hipoteses", {})[nova] = registro["challenger"]
        evento = {"tipo": "promocao", "agent_id": agent_id,
                  "assinatura": nova, "de": anterior,
                  "evidencia": assinatura,
                  "motivo": resultado.get("motivo", ""),
                  "em": agora.isoformat()}
        self._eventos.append(evento)
        self.salvar()
        return {"agent_id": agent_id, "assinatura": nova, "de": anterior,
                "ja_promovido": False}

    def ciclo(self, agora: datetime | None = None) -> dict:
        """Um ciclo da evolução: vigia (rollback/suspensão por deterioração)
        e promoção automática dos challengers aprovados — tudo em PAPER."""
        agora = agora or datetime.now(timezone.utc)
        eventos: dict[str, list] = {"rollback": [], "suspensao": [],
                                    "promocao": []}
        for evento in self.vigiar(agora):
            eventos.setdefault(evento["tipo"], []).append(evento)
        for assinatura in sorted(self._resultados):
            resultado = self._resultados[assinatura]
            if not resultado.get("aprovado"):
                continue
            registro = self._experimentos.get(assinatura)
            if registro is None:
                continue
            estado = self._versoes.get(registro["trader"]) or {}
            if estado.get("atual") == registro["assinatura_challenger"]:
                continue
            promocao = self._promover_registro(registro, resultado,
                                               assinatura, agora)
            if not promocao.get("ja_promovido"):
                eventos["promocao"].append(promocao)
        return eventos

    # -- vigia da deterioração -----------------------------------------
    def avaliar_deterioracao(self, agent_id: str,
                             agora: datetime | None = None) -> tuple[bool, str]:
        """Deterioração exige amostra madura e expectativa líquida negativa
        na evidência ainda não consumida (pós-rollback/reabilitação) —
        um dia ruim não derruba ninguém."""
        estado = self._versoes.get(agent_id) or {}
        marcador = int(estado.get("marcador", 0) or 0)
        entradas = [r for r in self._diario if r.agent_id == agent_id]
        recentes = entradas[marcador:]
        if len(recentes) < AMOSTRA_MINIMA:
            return False, (f"amostra insuficiente "
                           f"({len(recentes)}/{AMOSTRA_MINIMA})")
        esp = sum(float(r.retorno_liquido) for r in recentes) / len(recentes)
        if esp < 0:
            return True, (f"expectativa líquida {esp:+.2%} em "
                          f"{len(recentes)} operações")
        return False, f"expectativa líquida {esp:+.2%} aceitável"

    def vigiar(self, agora: datetime | None = None) -> list[dict]:
        """Reavalia a saúde de cada trader do diário. Deteriorou com versão
        anterior? ROLLBACK (volta ao que já rodou). Sem anterior? SUSPENSÃO
        (bloqueio de novas entradas, via vigia). Idempotente."""
        agora = agora or datetime.now(timezone.utc)
        eventos = []
        agentes = sorted({r.agent_id for r in self._diario}
                         | set(self._versoes) | set(self._suspensoes))
        for agent_id in agentes:
            if agent_id in self._suspensoes:
                continue
            deteriorou, motivo = self.avaliar_deterioracao(agent_id, agora)
            if not deteriorou:
                continue
            entradas = [r for r in self._diario if r.agent_id == agent_id]
            estado = self._versoes.setdefault(
                agent_id, {"atual": "", "anterior": "", "hipoteses": {},
                           "marcador": 0})
            anterior = estado.get("anterior") or ""
            if anterior:
                estado["atual"] = anterior
                estado["anterior"] = ""
                estado["marcador"] = len(entradas)
                evento = {"tipo": "rollback", "agent_id": agent_id,
                          "volta_para": anterior, "motivo": motivo,
                          "em": agora.isoformat()}
            else:
                sufixo = "deterioração sem versão anterior para reverter"
                self._suspensoes[agent_id] = f"{sufixo} — {motivo}"
                evento = {"tipo": "suspensao", "agent_id": agent_id,
                          "motivo": self._suspensoes[agent_id],
                          "em": agora.isoformat()}
            self._eventos.append(evento)
            eventos.append(evento)
            self.salvar()
        return eventos

    def bloqueios(self, agora: datetime | None = None) -> dict[str, str]:
        """Quem a evolução bloqueia AGORA — formato do vigia do orquestrador
        (mesmo contrato de `SupervisorDePiloto.bloqueios`). Reavalia antes de
        responder: estado velho bloquearia ou liberaria a entrada errada."""
        self.vigiar(agora)
        return dict(self._suspensoes)

    def reabilitar(self, agent_id: str, *, motivo: str,
                   agora: datetime | None = None) -> None:
        """Liberar suspensão exige motivo REGISTRADO — e consome a evidência
        antiga: só desfechos novos reabrem a avaliação."""
        if not str(motivo or "").strip():
            raise ValueError("reabilitação exige motivo (evidência) — nada "
                             "de liberar sem por quê")
        agora = agora or datetime.now(timezone.utc)
        self._suspensoes.pop(agent_id, None)
        estado = self._versoes.setdefault(
            agent_id, {"atual": "", "anterior": "", "hipoteses": {},
                       "marcador": 0})
        estado["marcador"] = sum(1 for r in self._diario
                                 if r.agent_id == agent_id)
        self._eventos.append({"tipo": "reabilitacao", "agent_id": agent_id,
                              "motivo": str(motivo),
                              "em": agora.isoformat()})
        self.salvar()

    # -- relatório ------------------------------------------------------
    def eventos(self) -> list[dict]:
        return list(self._eventos)

    def relatorio(self) -> dict:
        agentes = sorted({r.agent_id for r in self._diario}
                         | set(self._versoes) | set(self._suspensoes))
        experimentos = []
        for assinatura in sorted(self._experimentos):
            registro = self._experimentos[assinatura]
            experimentos.append({
                "assinatura": assinatura, "id": registro.get("id"),
                "titulo": registro.get("titulo"), "trader": registro.get("trader"),
                "o_que_mudou": _o_que_mudou(registro),
                "executada": assinatura in self._resultados,
                "resultado": self._resultados.get(assinatura),
                "aviso": AVISO})
        return {"traders": [self.desempenho(a) for a in agentes],
                "experimentos": experimentos, "aviso": AVISO}


__all__ = ["AVISO", "CAUSA_DADOS_INADEQUADOS", "CAUSA_ERRO_EXECUCAO",
           "CAUSA_MUDANCA_REGIME", "CAUSA_REGRA_DESCALIBRADA",
           "CAUSA_RISCO_NORMAL", "ContextoDaOperacao", "EvolucaoAutonoma",
           "Experimento", "MetricasDoLado", "RegistroDoDiario",
           "VereditoDoExperimento", "classificar_causa",
           "LIMIAR_DESVIO_EXECUCAO"]
