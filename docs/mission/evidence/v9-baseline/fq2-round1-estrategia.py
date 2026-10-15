# FQ-2 RODADA 1 - estrategia equivalente para o confronto DZ23 x Freqtrade.
# Codigo SEPARADO do produto (GPLv3 respeitada como processo separado; nada copiado
# do produto, nada importado pelo produto). Nenhuma credencial; backtest somente.
# Itens da rodada fixados (protocolo FQ-2, secao 2) - os MESMOS nos dois motores:
#  1 sinais: cruzamento SMA(10)/SMA(30) de fechamento; entrada no cross-up, saida no cross-down
#  2 calendario: UTC; cripto 24/7 sem exclusoes; sem feriados
#  3 velas: BTC/USDT 1h, timestamp = abertura da barra
#  4 taxas: 0,001 por lado (10 bps) nos dois motores
#  5 slippage: 0 nos dois motores
#  6 fills: mercado no OPEN da vela seguinte ao sinal (modelo nativo freqtrade replicado no lado DZ23)
#  7 stop: nenhum (stoploss -0,99 e ROI {0:100} inalcancaveis por desenho)
#  8 capital: 1000 USDT; posicao fixa 100 USDT (10%)
#  9 reinvestimento: nao (capital fixo; stake_amount constante)
# 10 timeframe: 1h no sinal e na execucao
# 11 convencoes: execucao no open da vela de fill; sem barra final incompleta forcada
from freqtrade.strategy import IStrategy


class Dz23SmaCross(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False
    stoploss = -0.99
    minimal_roi = {"0": 100}
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    startup_candle_count = 30

    def populate_indicators(self, dataframe, metadata):
        dataframe["sma_fast"] = dataframe["close"].rolling(10).mean()
        dataframe["sma_slow"] = dataframe["close"].rolling(30).mean()
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        cross_up = (dataframe["sma_fast"] > dataframe["sma_slow"]) & (
            dataframe["sma_fast"].shift(1) <= dataframe["sma_slow"].shift(1)
        )
        dataframe.loc[cross_up, "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        cross_down = (dataframe["sma_fast"] < dataframe["sma_slow"]) & (
            dataframe["sma_fast"].shift(1) >= dataframe["sma_slow"].shift(1)
        )
        dataframe.loc[cross_down, "exit_long"] = 1
        return dataframe
