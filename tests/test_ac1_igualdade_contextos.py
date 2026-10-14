"""AC-1/A2 — igualdade indicador-backtest×produção com warmup declarado
(débito FQ-3 #2).

Os dois contextos de decisão da casa:
- BACKTEST (`desk/validation.py:132`): janela `closes[:i]` — passado estrito
  da vela i;
- PRODUÇÃO (`desk/traders.py:136`): snapshot com as velas FECHADAS até o
  corte, alimentando o MESMO `interpretar`.

Pinos deste arquivo:
1. no MESMO corte, o backtest entrega ao trader exatamente a série que a
   produção entregaria (igualdade de contexto, não só causalidade);
2. a decisão do backtest na vela k é a decisão de produção num snapshot com
   as mesmas k velas fechadas;
3. o warmup é idêntico nos dois contextos: janela insuficiente vira
   abstenção/INDEFINIDO — nunca estimativa otimista;
4. (A2b) as duas definições de RSI da casa são distintas e declaradas —
   Wilder (série do painel da sala/Copiloto) e Cutler (valor único da
   evidência de decisão via `interpretar`). Unificá-las mudaria o que a
   Escola julgou: qualquer unificação consciente deve QUEBRAR o pino 4 de
   propósito, nunca acontecer por drift.
"""
from __future__ import annotations

import contextlib
from decimal import Decimal

from tradingagents.desk import signals
from tradingagents.desk.models import Action, AgentStage, MarketSnapshot, utcnow
from tradingagents.desk.sala_trader import rsi as rsi_wilder
from tradingagents.desk.traders import TrendTrader
from tradingagents.desk.validation import validar


def tendencia_forte(n=120):
    return tuple(Decimal(str(round(100 * (1.01 ** i), 6))) for i in range(n))


def trader_em_estagio_de_mesa(agent_id="t"):
    """Trader no MESMO estágio que `validar` deixa (PAPER_ENABLED): assim a
    comparação dos contextos não mistura estágio com indicador."""
    t = TrendTrader(agent_id, ("BTC/USDT",))
    for estagio in (AgentStage.VALIDATED, AgentStage.SHADOW,
                    AgentStage.PAPER_ENABLED):
        if t.spec.stage is not estagio:
            with contextlib.suppress(ValueError):
                t.spec.move_to(estagio)
    return t


class TestJanelaDoBacktestEOPrefixoDeProducao:
    def test_janela_e_exatamente_o_prefixo_no_mesmo_corte(self):
        """Na vela i o backtest entrega `serie[:i]`, byte a byte o que a
        produção entregaria num snapshot cortado em i. Sem isso, o veredito
        do passado julga outra série que a viva usa."""
        serie = tendencia_forte(120)
        t = trader_em_estagio_de_mesa()
        original = t.propose
        vistas = {}

        def espiao(snap):
            vistas[len(snap.closes)] = tuple(snap.closes)
            return original(snap)

        t.propose = espiao
        validar(t, serie, "BTC/USDT")
        assert vistas, "nenhuma decisão registrada"
        for tamanho, janela in vistas.items():
            assert janela == tuple(serie[:tamanho]), (
                f"janela do backtest ({tamanho} velas) divergiu do prefixo "
                "de produção")


class TestDecisaoIgualNosDoisContextos:
    def test_mesma_serie_fechada_mesma_decisao(self):
        serie = tendencia_forte(120)
        t = trader_em_estagio_de_mesa()
        original = t.propose
        decisoes = {}

        def espiao(snap):
            proposta = original(snap)
            decisoes[len(snap.closes)] = proposta.action
            return proposta

        t.propose = espiao
        validar(t, serie, "BTC/USDT")
        assert decisoes
        for tamanho, acao_backtest in list(decisoes.items())[:8]:
            prod = trader_em_estagio_de_mesa("t-prod")
            snap = MarketSnapshot("BTC/USDT", "1h", utcnow(),
                                  tuple(serie[:tamanho]))
            assert prod.propose(snap).action is acao_backtest, (
                f"decisão divergiu entre backtest e produção no corte "
                f"{tamanho}")


class TestWarmupIgualNosDoisContextos:
    def test_serie_curta_nao_gera_decisao_nem_estimativa(self):
        t = trader_em_estagio_de_mesa()
        curta = tendencia_forte(t.needs - 1)
        r = validar(t, curta, "BTC/USDT")
        assert r.velas_avaliadas == 0 and r.total == 0
        # produção: a MESMA série curta vira INDEFINIDO com motivo —
        # aquecimento declarado, nunca número otimista
        leitura = signals.interpretar("BTC/USDT", [float(c) for c in curta])
        assert leitura.regime is signals.Regime.INDEFINIDO
        assert leitura.rsi is None
        assert any("curta" in m for m in leitura.motivos)


class TestDuasDefinicoesDeRSIDeclaradas:
    """A2b: painel/Copiloto usam Wilder; a evidência da decisão usa Cutler.
    Pino duplo: cada implementação segue a PRÓPRIA fórmula (recalculada
    aqui de forma independente) e as duas são declaradas — divergência
    silenciosa entre contextos é o que este pino impede."""

    SERIE = ([100 + 2 * i for i in range(30)] + [160 - 3 * i for i in range(20)])

    def test_cutler_da_decisao_segue_a_formula_declarada(self):
        v = self.SERIE
        deltas = [b - a for a, b in zip(v[-15:-1], v[-14:], strict=True)]
        mg = sum(max(d, 0.0) for d in deltas) / 14
        mp = sum(max(-d, 0.0) for d in deltas) / 14
        cutler = 100 - 100 / (1 + mg / mp)
        assert abs(signals.rsi(v, 14) - cutler) < 1e-9
        assert "cutler" in (signals.rsi.__doc__ or "").lower()

    def test_wilder_do_painel_segue_a_formula_declarada(self):
        v = self.SERIE
        g = p = 0.0
        for i in range(1, 15):
            d = v[i] - v[i - 1]
            g += max(d, 0.0)
            p += max(-d, 0.0)
        g, p = g / 14, p / 14
        for i in range(15, len(v)):
            d = v[i] - v[i - 1]
            g = (g * 13 + max(d, 0.0)) / 14
            p = (p * 13 + max(-d, 0.0)) / 14
        wilder = 100 - 100 / (1 + g / p)
        serie_painel = rsi_wilder(v, 14)
        assert serie_painel[13] is None
        assert abs(serie_painel[-1] - wilder) < 1e-6
        assert "wilder" in (rsi_wilder.__doc__ or "").lower()

    def test_as_definicoes_sao_distintas_e_nao_convergem_por_acaso(self):
        v = self.SERIE
        assert abs(signals.rsi(v, 14) - rsi_wilder(v, 14)[-1]) > 1e-3, (
            "as duas fórmulas coincidiram: ou alguém unificou por drift "
            "(proibido sem decisão) ou o pino escolheu série ruim")


class TestAbstencoesNaoSaoErroNosDoisContextos:
    def test_action_data_insufficient_e_a_resposta_unica(self):
        """Dados insuficientes têm uma única resposta nos dois contextos:
        Action.DATA_INSUFFICIENT — o trader se abstém por desenhho."""
        t = trader_em_estagio_de_mesa()
        snap = MarketSnapshot("BTC/USDT", "1h", utcnow(),
                              tuple(tendencia_forte(t.needs - 1)))
        assert t.propose(snap).action is Action.DATA_INSUFFICIENT
