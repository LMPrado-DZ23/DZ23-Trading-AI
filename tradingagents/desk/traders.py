"""Três traders operacionais com estratégias distintas (MA-04).

Cada um tem universo, regime de elegibilidade, entrada, saída, invalidação e
razão explícita de NO_TRADE. O cálculo é determinístico (código); o LLM entra
como interpretação opcional e NUNCA decide tamanho, risco ou autorização.
"""
from __future__ import annotations

from decimal import Decimal
from statistics import fmean, pstdev

from .costs import custo_de_operar, vale_a_pena
from .learning import MemoriaDeDesempenho
from .models import Action, AgentSpec, AgentStage, MarketSnapshot, TradeProposal, default_expiry
from .policy import policy_for
from .signals import Leitura, Regime, confirmacao_superior, interpretar
from .sizing import tamanho_por_risco


def _sma(values, n: int) -> Decimal:
    return Decimal(str(fmean([float(v) for v in values[-n:]])))


class BaseTrader:
    strategy = "base"
    needs = 20

    regimes_favoraveis: tuple[Regime, ...] = ()
    # Movimento que a estratégia espera capturar (alvo). Sem isto não há como
    # saber se o sinal paga o custo — e o robô opera no vermelho sem notar.
    movimento_alvo = Decimal("0.02")
    # Confirmação no prazo maior: sinal de 1h contra tendência de 4h é o
    # falso rompimento clássico. None = a estratégia não exige confirmação.
    fator_confirmacao: int | None = 4

    def __init__(self, agent_id: str, universe: tuple[str, ...], version: str = "1",
                 memoria: MemoriaDeDesempenho | None = None,
                 exchange: str = "binance", capital: Decimal = Decimal("1000")):
        self.exchange = exchange
        self.capital = capital
        # Uma política, consumida por backtest e operação.
        self.exit_policy = policy_for(self.movimento_alvo)
        # A memória é COMPARTILHADA pela mesa: o que um trader aprende sobre
        # um regime não fica preso nele.
        self.memoria = memoria
        self.spec = AgentSpec(agent_id=agent_id, role="trader",
                              strategy=self.strategy, version=version,
                              stage=AgentStage.REGISTERED,
                              tools=("market_data", "indicators"))
        self.universe = universe

    def _no(self, snap: MarketSnapshot, reason: str, action=Action.NO_TRADE) -> TradeProposal:
        return TradeProposal(agent_id=self.spec.agent_id, strategy=self.strategy,
                             instrument=snap.instrument, action=action, reason=reason,
                             snapshot_cutoff=snap.cutoff)

    def manage(self, snap: MarketSnapshot, position) -> TradeProposal:
        """Acompanha posição aberta: propõe SAÍDA quando a tese morre.
        `position` traz entry_price e agent_id de quem abriu."""
        if not snap.sufficient(self.needs):
            return self._no(snap, "sem dados para reavaliar a posição — mantendo",
                            action=Action.HOLD)
        entry = position.entry_price
        pnl_pct = (snap.last - entry) / entry if entry else Decimal("0")
        # MESMA política do backtest — antes a produção usava -5%/+8% fixos
        # enquanto o backtest simulava o alvo da estratégia: dois sistemas
        # diferentes, e o relatório media o que não operava.
        # IDADE REAL da posição: o backtest passava as velas decorridas e a
        # produção não passava nada, então a regra de tempo NUNCA disparava
        # em operação. Mesma política exige mesmo significado temporal.
        velas = self._velas_na_posicao(snap, position)
        sair, motivo = self.exit_policy.avaliar(pnl_pct, velas)
        if sair and motivo == "stop":
            return self._exit(snap, f"stop: {pnl_pct:.2%} desde a entrada")
        if sair and motivo == "alvo":
            return self._exit(snap, f"alvo atingido: {pnl_pct:.2%}")
        reason = self._thesis_dead(snap)
        if reason:
            return self._exit(snap, f"tese invalidada: {reason}")
        return self._no(snap, f"tese de pé ({pnl_pct:+.2%}) — mantendo",
                        action=Action.HOLD)

    @staticmethod
    def _velas_na_posicao(snap: MarketSnapshot, position) -> int:
        """Quantas velas se passaram desde a entrada, no timeframe do snapshot.

        Sem horário de entrada não se inventa idade: devolve 0, e a regra de
        tempo simplesmente não dispara (em vez de disparar errado).
        """
        abertura = getattr(position, "opened_at", None)
        if abertura is None or snap.cutoff is None:
            return 0
        minutos = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60,
                   "4h": 240, "1d": 1440}.get(snap.timeframe, 60)
        segundos = (snap.cutoff - abertura).total_seconds()
        return max(0, int(segundos // (minutos * 60)))

    def _thesis_dead(self, snap: MarketSnapshot) -> str:
        return ""

    def _exit(self, snap: MarketSnapshot, reason: str) -> TradeProposal:
        return TradeProposal(
            agent_id=self.spec.agent_id, strategy=self.strategy,
            instrument=snap.instrument, action=Action.REDUCE, reason=reason,
            desired_exposure_pct=Decimal("1"), confidence=Decimal("0.9"),
            valid_until=default_expiry(10), snapshot_cutoff=snap.cutoff)

    def propose(self, snap: MarketSnapshot) -> TradeProposal:
        if snap.instrument not in self.universe:
            return self._no(snap, "instrumento fora do universo deste trader")
        if not self.spec.can_propose():
            return self._no(snap, f"agente no estágio {self.spec.stage.value} não propõe")
        if not snap.sufficient(self.needs):
            # Cenário 06: dado insuficiente nunca vira ordem.
            return self._no(snap, f"dados insuficientes (precisa de {self.needs} velas)",
                            action=Action.DATA_INSUFFICIENT)
        leitura = interpretar(snap.instrument, snap.closes)
        self.ultima_leitura = leitura
        self.ultima_serie = snap.closes
        if not leitura.suficiente():
            return self._no(snap, f"leitura indefinida: {leitura.resumo()}",
                            action=Action.DATA_INSUFFICIENT)
        if self.regimes_favoraveis and leitura.regime not in self.regimes_favoraveis:
            return self._no(snap, f"regime {leitura.regime.value} não é o meu "
                                  f"terreno ({leitura.resumo()})")
        if self.memoria:
            rebaixar, motivo = self.memoria.deve_rebaixar(self.strategy)
            if rebaixar:
                return self._no(snap, f"estratégia rebaixada pelo histórico: {motivo}")
        proposta = self._evaluate(snap)
        if proposta.action is Action.BUY and self.fator_confirmacao:
            ok, nota = confirmacao_superior(snap.closes, self.fator_confirmacao)
            if ok is None:
                return self._no(snap, f"sem confirmação de prazo maior: {nota}")
            if ok is False:
                return self._no(snap, f"sinal sem apoio: {nota}")
            proposta = proposta.__class__(**{**proposta.__dict__,
                                             "reason": proposta.reason + f" | {nota}"})
        return self._aplicar_aprendizado(proposta, leitura)

    def _aplicar_aprendizado(self, p: TradeProposal, leitura: Leitura) -> TradeProposal:
        """Ajusta confiança e tamanho pelo histórico NAQUELE regime. Só reduz
        exposição; aumentar é prerrogativa do mandato, não do aprendizado."""
        if p.action not in (Action.BUY, Action.SELL, Action.REDUCE):
            return p
        conf, expo = p.confidence, p.desired_exposure_pct
        notas = [f"leitura: {leitura.resumo()}", f"força {leitura.forca:.2f}"]
        if self.memoria:
            conf = self.memoria.ajuste_de_confianca(self.strategy, leitura.regime, conf)
            fator = self.memoria.fator_de_exposicao(self.strategy, leitura.regime)
            if fator < 1:
                expo = (expo * fator).quantize(Decimal("0.0001"))
                notas.append(f"exposição reduzida a {fator} pelo histórico")
            st = self.memoria.estatistica(self.strategy, leitura.regime)
            if st.maduro:
                notas.append(f"histórico neste regime: {st.taxa_acerto:.0%} de "
                             f"acerto em {st.amostras} operações")
        # a força do sinal modula a confiança: sinal fraco, convicção menor
        conf = (conf * Decimal(str(0.6 + 0.4 * leitura.forca))).quantize(Decimal("0.001"))

        # TAMANHO POR RISCO: percentual fixo trata ativo calmo e ativo violento
        # como se fossem a mesma aposta. Aqui o tamanho sai da volatilidade.
        calculado = tamanho_por_risco(self.capital, self.ultima_serie or (),
                                      teto_exposicao=expo)
        if calculado is not None:
            expo = calculado.fracao_do_capital
            notas.append(calculado.motivo)
            if expo <= 0:
                return self._no_com_leitura(p, leitura,
                                            "volatilidade exige posição menor que o mínimo")

        # GATE DE CUSTO: o movimento esperado precisa pagar ida e volta, com
        # margem. É o filtro que separa robô que opera de robô que lucra.
        esperado = self.movimento_esperado(leitura)
        custo = custo_de_operar(p.instrument, self.exchange,
                                self.capital * expo, leitura.volatilidade)
        paga, motivo_custo = vale_a_pena(esperado, custo)
        if not paga:
            return self._no_com_leitura(p, leitura, f"custo não compensa: {motivo_custo}")
        notas.append(motivo_custo)

        from dataclasses import replace
        return replace(p, confidence=conf, desired_exposure_pct=expo,
                       reason=p.reason + " | " + " | ".join(notas))

    def movimento_esperado(self, leitura: Leitura) -> Decimal:
        """Quanto esta estratégia espera capturar nesta leitura.

        Ancorado na volatilidade observada: em mercado parado o alvo encolhe,
        e é justamente aí que o custo engole o resultado.
        """
        alvo = self.movimento_alvo
        if leitura.volatilidade:
            # alvo realista: no máximo ~3 velas de amplitude típica
            alvo = min(alvo, Decimal(str(round(leitura.volatilidade * 3, 6))))
        return (alvo * Decimal(str(0.5 + 0.5 * leitura.forca))).quantize(
            Decimal("0.000001"))

    def _no_com_leitura(self, p: TradeProposal, leitura: Leitura,
                        motivo: str) -> TradeProposal:
        from dataclasses import replace
        return replace(p, action=Action.NO_TRADE,
                       desired_exposure_pct=Decimal("0"),
                       reason=f"{motivo} | leitura: {leitura.resumo()}")

    def _evaluate(self, snap: MarketSnapshot) -> TradeProposal:  # pragma: no cover
        raise NotImplementedError

    def _buy(self, snap, pct: str, reason: str, invalidation: str, conf: str) -> TradeProposal:
        return TradeProposal(
            agent_id=self.spec.agent_id, strategy=self.strategy,
            instrument=snap.instrument, action=Action.BUY, reason=reason,
            desired_exposure_pct=Decimal(pct), confidence=Decimal(conf),
            valid_until=default_expiry(10), invalidation=invalidation,
            snapshot_cutoff=snap.cutoff)


class TrendTrader(BaseTrader):
    """Segue tendência: compra quando a média curta cruza acima da longa."""
    strategy = "trend_following"
    needs = 30
    regimes_favoraveis = (Regime.ALTA,)
    movimento_alvo = Decimal("0.04")     # tendência: alvo maior, menos trades

    def _thesis_dead(self, snap):
        fast, slow = _sma(snap.closes, 10), _sma(snap.closes, 30)
        return "média curta cruzou abaixo da longa" if fast < slow else ""

    def _evaluate(self, snap):
        fast, slow = _sma(snap.closes, 10), _sma(snap.closes, 30)
        if fast <= slow:
            return self._no(snap, "regime sem tendência de alta (média curta <= longa)")
        return self._buy(snap, "0.15",
                         f"tendência de alta: média curta {fast:.2f} acima da longa {slow:.2f}",
                         "média curta voltar abaixo da longa", "0.6")


class MeanReversionTrader(BaseTrader):
    """Reversão à média: compra exagero de queda contra a média."""
    strategy = "mean_reversion"
    needs = 20
    regimes_favoraveis = (Regime.LATERAL, Regime.BAIXA, Regime.VOLATIL)
    movimento_alvo = Decimal("0.025")    # volta à média: alvo curto
    fator_confirmacao = None             # opera contra o movimento, por desenho

    def _thesis_dead(self, snap):
        window = [float(c) for c in snap.closes[-20:]]
        mean = Decimal(str(fmean(window)))
        return "preço voltou à média (tese cumprida)" if snap.last >= mean else ""

    def _evaluate(self, snap):
        window = [float(c) for c in snap.closes[-20:]]
        mean, sd = Decimal(str(fmean(window))), Decimal(str(pstdev(window)))
        if sd == 0:
            return self._no(snap, "volatilidade nula: sem desvio para reverter")
        z = (snap.last - mean) / sd
        if z > Decimal("-2"):
            return self._no(snap, f"preço dentro da banda (z={z:.2f}); só opero abaixo de -2")
        return self._buy(snap, "0.10",
                         f"exagero de queda: z={z:.2f} contra a média de 20",
                         "z voltar acima de 0", "0.55")


class BreakoutTrader(BaseTrader):
    """Rompimento: compra quando supera a máxima recente com folga."""
    strategy = "breakout"
    needs = 25
    regimes_favoraveis = (Regime.ALTA, Regime.VOLATIL)
    movimento_alvo = Decimal("0.035")

    def _evaluate(self, snap):
        prior = snap.closes[-25:-1]
        high = max(prior)
        if snap.last <= high * Decimal("1.01"):
            return self._no(snap, f"sem rompimento: {snap.last:.2f} não superou {high:.2f} + 1%")
        return self._buy(snap, "0.12",
                         f"rompimento de {high:.2f} com folga",
                         "perder a máxima rompida", "0.5")


def default_desk(universe: tuple[str, ...],
                 memoria: MemoriaDeDesempenho | None = None,
                 exchange: str = "binance",
                 capital: Decimal = Decimal("1000")) -> list[BaseTrader]:
    """Mesa padrão: três estratégias distintas, cada uma com seu terreno, e
    todas compartilhando a mesma memória de desempenho."""
    return [TrendTrader("trader-trend", universe, memoria=memoria,
                        exchange=exchange, capital=capital),
            MeanReversionTrader("trader-reversion", universe, memoria=memoria,
                                exchange=exchange, capital=capital),
            BreakoutTrader("trader-breakout", universe, memoria=memoria,
                           exchange=exchange, capital=capital)]
