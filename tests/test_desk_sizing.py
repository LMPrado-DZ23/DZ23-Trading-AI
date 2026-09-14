"""Tamanho por risco e correlação: os dois erros silenciosos do percentual fixo."""
from __future__ import annotations

from decimal import Decimal

from tradingagents.desk.sizing import (
    correlacao,
    exposicao_correlacionada,
    limite_de_correlacao,
    tamanho_por_risco,
)


def serie(vol_por_vela, n=60, base=100.0):
    """Série com volatilidade controlada (alterna para cima e para baixo)."""
    precos, p = [], base
    for i in range(n):
        p = p * (1 + vol_por_vela if i % 2 else 1 - vol_por_vela)
        precos.append(Decimal(str(round(p, 6))))
    return tuple(precos)


class TestTamanhoPorRisco:
    def test_volatile_asset_gets_a_smaller_position(self):
        calmo = tamanho_por_risco(Decimal("1000"), serie(0.002))
        violento = tamanho_por_risco(Decimal("1000"), serie(0.08))
        assert calmo and violento
        assert violento.fracao_do_capital < calmo.fracao_do_capital

    def test_risk_per_trade_is_respected_not_capital_percentage(self):
        calc = tamanho_por_risco(Decimal("1000"), serie(0.02),
                                 risco_maximo=Decimal("0.01"))
        assert calc.risco_por_operacao == Decimal("0.01")
        # exposição x stop ≈ risco assumido
        assert abs(calc.fracao_do_capital * calc.stop_sugerido - Decimal("0.01")) < Decimal("0.002")

    def test_mandate_cap_always_wins(self):
        calc = tamanho_por_risco(Decimal("1000"), serie(0.0005),
                                 teto_exposicao=Decimal("0.10"))
        assert calc.fracao_do_capital <= Decimal("0.10")

    def test_no_volatility_no_size(self):
        assert tamanho_por_risco(Decimal("1000"), tuple([Decimal("100")] * 60)) is None
        assert tamanho_por_risco(Decimal("1000"), (Decimal("100"),)) is None

    def test_explains_itself(self):
        calc = tamanho_por_risco(Decimal("1000"), serie(0.02))
        assert "volatilidade" in calc.motivo and "stop" in calc.motivo


class TestCorrelacao:
    def test_same_base_asset_is_almost_the_same_bet(self):
        assert correlacao("BTC/USDT", "BTC/BRL") > 0.9

    def test_btc_and_eth_are_highly_correlated(self):
        assert correlacao("BTC/USDT", "ETH/USDT") >= 0.8

    def test_unknown_pair_assumes_high_correlation_not_zero(self):
        """Presumir independência é o erro caro; presumir correlação é o seguro."""
        assert correlacao("XYZ/USDT", "ABC/USDT") >= 0.6

    def test_effective_exposure_counts_correlated_positions(self):
        efetiva = exposicao_correlacionada("ETH/USDT", {"BTC/USDT": Decimal("0.20")})
        assert efetiva > Decimal("0.15")        # 0.20 x 0.85

    def test_second_correlated_buy_is_vetoed(self):
        cabe, motivo = limite_de_correlacao(
            "ETH/USDT", Decimal("0.20"), {"BTC/USDT": Decimal("0.25")})
        assert cabe is False and "dobrar a mesma aposta" in motivo

    def test_small_addition_within_cap_is_allowed(self):
        cabe, _ = limite_de_correlacao(
            "ETH/USDT", Decimal("0.05"), {"BTC/USDT": Decimal("0.10")})
        assert cabe is True

    def test_first_position_is_never_blocked_by_correlation(self):
        cabe, _ = limite_de_correlacao("BTC/USDT", Decimal("0.20"), {})
        assert cabe is True


class TestIntegracaoComOAllocator:
    def test_allocator_vetoes_the_correlated_second_entry(self):
        from datetime import timedelta

        from tradingagents.desk.models import Action, TradeProposal, utcnow
        from tradingagents.desk.risk import PortfolioAllocator
        p = TradeProposal(agent_id="t", strategy="s", instrument="ETH/USDT",
                          action=Action.BUY, reason="",
                          desired_exposure_pct=Decimal("0.20"),
                          confidence=Decimal("0.7"),
                          valid_until=utcnow() + timedelta(minutes=5))
        d = PortfolioAllocator().arbitrate([p], exposicoes={"BTC/USDT": Decimal("0.25")})
        assert not d.admitted
        assert "correlacionada" in d.vetoed[0][1]
