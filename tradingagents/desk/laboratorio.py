"""Laboratório de hipóteses pré-registradas (princípios do Batalhão).

Uma hipótese de estratégia só vale se for **declarada antes de olhar os
dados**. Aqui isso deixa de ser promessa e vira contrato:

- **Pré-registro com assinatura.** A hipótese inteira (regra, parâmetros,
  janelas, instrumentos, horizonte) é congelada num hash SHA-256. Executar
  uma versão alterada é impossível: a assinatura muda e o laboratório
  recusa. O registro fica no armazém — auditável.
- **Execução única.** Rodar, espiar o resultado e afinar parâmetros é
  p-hacking. A primeira execução fica registrada; repetir exige
  `forcar=True` explícito, e o painel mostra o histórico.
- **Sem look-ahead.** O executor só vê `serie[:i+1]`; a janela de teste é
  o fim da série — o passado informa o sinal, o futuro só mede o
  resultado, com o horizonte declarado na hipótese.
- **Controle aleatório permanente.** Toda hipótese é comparada com um
  controle de MESMO número de entradas em barras determinísticas
  (semente `id|instrumento|seed`). Estratégia que não supera o aleatório
  não tem vantagem para vender.
- **Correção para múltiplas hipóteses.** Bonferroni: a família declara
  quantas hipóteses disputam; o alfa cai na proporção — quem testa muito
  precisa de evidência mais forte.
- **Custos reais descontados.** As taxas/spread/slippage de `costs.py`
  entram em cada operação simulada; bruto que não paga a volta não
  engana ninguém.

E o freio final: **o laboratório NUNCA promove ninguém.** Ele é triagem
histórica; formatura exige desfechos REAIS na Escola de Traders, com
prova independente pós-candidatura.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .costs import custo_de_operar

ALPHA = 0.05
SEED_CONTROLE = 20261013
VALOR_REFERENCIA = Decimal("1000")   # tamanho de ordem padrão para o custo


# ------------------------------------------------------------------ regras
def _exec_reversao_z(serie: list[float], i: int, p: dict) -> float:
    janela = int(p.get("janela", 20))
    z_entrada = float(p.get("z_entrada", 2.0))
    if i < janela:
        return 0.0
    rec = serie[i - janela + 1: i + 1]
    m = sum(rec) / janela
    var = sum((c - m) ** 2 for c in rec) / janela
    sd = math.sqrt(var)
    if sd <= 0:
        return 0.0
    z = (serie[i] - m) / sd
    return 1.0 if z <= -z_entrada else 0.0


def _exec_rompimento(serie: list[float], i: int, p: dict) -> float:
    janela = int(p.get("janela", 20))
    if i < janela:
        return 0.0
    maximo = max(serie[i - janela: i])
    return 1.0 if serie[i] > maximo else 0.0


def _exec_momentum(serie: list[float], i: int, p: dict) -> float:
    janela = int(p.get("janela", 10))
    limiar = float(p.get("limiar", 0.01))
    if i < janela:
        return 0.0
    base = serie[i - janela]
    if base <= 0:
        return 0.0
    roc = (serie[i] - base) / base
    return 1.0 if roc >= limiar else 0.0


EXECUTORES = {
    "reversao_z": _exec_reversao_z,
    "rompimento": _exec_rompimento,
    "momentum": _exec_momentum,
}


# ----------------------------------------------------------------- modelos
@dataclass(frozen=True)
class Hipotese:
    """Hipótese congelada. Alterar qualquer campo muda a assinatura."""
    id: str
    titulo: str
    familia: str
    tipo_de_regra: str
    parametros: tuple[tuple[str, float | int | str], ...] = ()
    instrumentos: tuple[str, ...] = ("BTC/USDT",)
    janela_treino_barras: int = 120
    janela_teste_barras: int = 60
    horizonte_barras: int = 12
    exchange: str = "binance"
    registrada_em: str = ""

    @property
    def parametros_dict(self) -> dict:
        return dict(self.parametros)

    def assinatura(self) -> str:
        corpo = {
            "id": self.id, "titulo": self.titulo, "familia": self.familia,
            "tipo_de_regra": self.tipo_de_regra,
            "parametros": [[k, v] for k, v in self.parametros],
            "instrumentos": list(self.instrumentos),
            "janela_treino_barras": self.janela_treino_barras,
            "janela_teste_barras": self.janela_teste_barras,
            "horizonte_barras": self.horizonte_barras,
            "exchange": self.exchange,
        }
        canonico = json.dumps(corpo, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False)
        return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ResultadoDaHipotese:
    assinatura: str
    id: str
    familia: str
    alpha_ajustado: float
    familia_n: int
    amostras_treino: int
    operacoes_estrategia: int
    operacoes_controle: int
    media_estrategia: float
    media_controle: float
    p_valor: float
    aprovada: bool
    executada_em: str
    motivo: str

    @property
    def aviso(self) -> str:
        return ("laboratório NÃO promove: veredito é triagem histórica — "
                "formatura exige desfechos reais na Escola de Traders")


class Laboratorio:
    """Pré-registro e execução única de hipóteses. Triagem — não promove."""

    CHAVE_ARMAZEM = "laboratorio"

    def __init__(self, armazem=None, tenant_id: str = "legacy", *,
                 seed: int = SEED_CONTROLE):
        self.armazem = armazem
        self.tenant_id = tenant_id
        self.seed = seed
        self._hipoteses: dict[str, dict] = {}
        self._resultados: dict[str, dict] = {}
        self._carregar()

    # -- persistência ---------------------------------------------------
    def _carregar(self) -> None:
        if self.armazem is None:
            return
        dados = self.armazem.carregar(self.tenant_id, self.CHAVE_ARMAZEM)
        if isinstance(dados, dict):
            if isinstance(dados.get("hipoteses"), dict):
                self._hipoteses = dados["hipoteses"]
            if isinstance(dados.get("resultados"), dict):
                self._resultados = dados["resultados"]

    def salvar(self) -> None:
        if self.armazem is None:
            return
        self.armazem.salvar(self.tenant_id, self.CHAVE_ARMAZEM, {
            "hipoteses": self._hipoteses, "resultados": self._resultados})

    # -- pré-registro ---------------------------------------------------
    def registrar(self, hipotese: Hipotese,
                  agora: datetime | None = None) -> str:
        """Congela a hipótese no livro. Idempotente por assinatura."""
        agora = agora or datetime.now(timezone.utc)
        assinatura = hipotese.assinatura()
        if assinatura not in self._hipoteses:
            registro = asdict(hipotese)
            registro["parametros"] = [[k, v] for k, v in hipotese.parametros]
            registro["instrumentos"] = list(hipotese.instrumentos)
            registro["registrada_em"] = agora.isoformat()
            self._hipoteses[assinatura] = registro
            self.salvar()
        return assinatura

    def familia_n(self, familia: str) -> int:
        return sum(1 for r in self._hipoteses.values()
                   if r.get("familia") == familia)

    # -- execução -------------------------------------------------------
    def executar(self, hipotese: Hipotese, series: dict[str, list[float]],
                 *, forcar: bool = False,
                 agora: datetime | None = None) -> ResultadoDaHipotese:
        """Roda UMA vez sobre a série. Reexecução só com forcar=True."""
        agora = agora or datetime.now(timezone.utc)
        assinatura = hipotese.assinatura()
        if assinatura not in self._hipoteses:
            raise ValueError(
                "hipótese não pré-registrada — o pré-registro vem ANTES de "
                "olhar os dados; hipótese alterada não é a registrada")
        if assinatura in self._resultados and not forcar:
            raise RuntimeError(
                "hipótese já executada — reexecução só com forcar=True "
                "(anti p-hacking)")
        executor = EXECUTORES.get(hipotese.tipo_de_regra)
        if executor is None:
            raise ValueError(f"regra desconhecida: {hipotese.tipo_de_regra}")
        h = int(hipotese.horizonte_barras)
        if h < 1:
            raise ValueError("horizonte_barras deve ser >= 1")

        params = hipotese.parametros_dict
        trades_e: list[float] = []
        trades_c: list[float] = []
        amostras_treino = 0
        for instrumento in hipotese.instrumentos:
            closes = [float(c) for c in (series.get(instrumento) or [])]
            n = len(closes)
            if n <= hipotese.janela_treino_barras + h:
                continue
            inicio = max(hipotese.janela_treino_barras,
                         n - hipotese.janela_teste_barras)
            amostras_treino += min(inicio, hipotese.janela_treino_barras)
            bruto_e = []
            for i in range(inicio, n - h):
                if executor(closes, i, params) > 0:
                    bruto_e.append((closes[i + h] - closes[i]) / closes[i])
            bruto_c = self._controle(hipotese, instrumento, closes, inicio,
                                     len(bruto_e))
            custo = float(custo_de_operar(instrumento, hipotese.exchange,
                                          VALOR_REFERENCIA).ida_e_volta)
            trades_e += [b - custo for b in bruto_e]
            trades_c += [b - custo for b in bruto_c]

        familia_n = max(1, self.familia_n(hipotese.familia))
        alpha_ajustado = ALPHA / familia_n
        ne, nc = len(trades_e), len(trades_c)
        if ne < 2 or nc < 2:
            me = mc = 0.0
            p = 1.0
        else:
            me = sum(trades_e) / ne
            mc = sum(trades_c) / nc
            vse = sum((t - me) ** 2 for t in trades_e) / (ne - 1)
            vsc = sum((t - mc) ** 2 for t in trades_c) / (nc - 1)
            denom = math.sqrt(vse / ne + vsc / nc)
            z = (me - mc) / denom if denom > 0 else 0.0
            p = 0.5 * math.erfc(z / math.sqrt(2))
        aprovada = bool(p < alpha_ajustado and me > 0 and ne >= 2)
        if ne < 2:
            motivo = "sem operações no período de teste — nada a julgar"
        elif aprovada:
            motivo = (f"supera o controle aleatório (média líquida "
                      f"{me:+.2%} vs {mc:+.2%}, p={p:.2e} < "
                      f"{alpha_ajustado:.2e} após Bonferroni "
                      f"familia={familia_n}) — segue para a escola com "
                      "desfechos reais")
        elif me <= 0:
            motivo = (f"média líquida {me:+.2%} não paga os custos — "
                      "reprovada antes mesmo do controle")
        else:
            motivo = (f"não supera o controle aleatório com significância "
                      f"(média {me:+.2%} vs {mc:+.2%}, p={p:.3f} ≥ "
                      f"{alpha_ajustado:.3f})")
        resultado = ResultadoDaHipotese(
            assinatura=assinatura, id=hipotese.id, familia=hipotese.familia,
            alpha_ajustado=alpha_ajustado, familia_n=familia_n,
            amostras_treino=amostras_treino, operacoes_estrategia=ne,
            operacoes_controle=nc, media_estrategia=round(me, 6),
            media_controle=round(mc, 6), p_valor=round(p, 8),
            aprovada=aprovada, executada_em=agora.isoformat(), motivo=motivo)
        registro = asdict(resultado)
        registro["aviso"] = resultado.aviso
        registro["forcada"] = bool(assinatura in self._resultados and forcar)
        self._resultados[assinatura] = registro
        self.salvar()
        return resultado

    def _controle(self, hipotese: Hipotese, instrumento: str,
                  closes: list[float], inicio: int, n_entradas: int) -> list[float]:
        """Controle determinístico: MESMO número de entradas, barras sorteadas."""
        n = len(closes)
        h = int(hipotese.horizonte_barras)
        candidatos = list(range(inicio, n - h))
        if not candidatos or n_entradas <= 0:
            return []
        rng = random.Random(f"{hipotese.id}|{instrumento}|{self.seed}")
        escolhidos = rng.sample(candidatos, min(n_entradas, len(candidatos)))
        return [(closes[i + h] - closes[i]) / closes[i]
                for i in sorted(escolhidos)]

    # -- relatório ------------------------------------------------------
    def relatorio(self) -> list[dict]:
        saida = []
        for assinatura in sorted(self._hipoteses):
            reg = self._hipoteses[assinatura]
            saida.append({
                "assinatura": assinatura, "id": reg.get("id"),
                "titulo": reg.get("titulo"), "familia": reg.get("familia"),
                "tipo_de_regra": reg.get("tipo_de_regra"),
                "registrada_em": reg.get("registrada_em"),
                "executada": assinatura in self._resultados,
                "resultado": self._resultados.get(assinatura),
                "aviso": ("pré-registro imutável; o laboratório não promove "
                          "ninguém"),
            })
        return saida


__all__ = ["EXECUTORES", "Hipotese", "Laboratorio", "ResultadoDaHipotese",
           "ALPHA", "SEED_CONTROLE"]
