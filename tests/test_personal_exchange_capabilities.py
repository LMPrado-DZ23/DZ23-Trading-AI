"""No network needed: CCXT advertised features do not enable LIVE."""
from service.personal_exchange_capabilities import inspect_capabilities, report_capabilities


def test_native_ccxt_features():
    result = inspect_capabilities("binance", {"fetchTicker": True, "fetchOHLCV": True, "createOrder": True})
    assert result.has_public_ticker
    assert result.has_ohlcv
    assert result.has_private_order
    assert result.status == "UNVERIFIED"


def test_emulated_features_not_native():
    result = inspect_capabilities("exchange", {"fetchTicker": "emulated", "fetchBalance": False})
    assert not result.has_public_ticker
    assert not result.has_private_balance


def test_catalog_never_authorizes_live():
    result = report_capabilities("okx", {"createOrder": True, "fetchTicker": True})
    assert result["order_api"] is True
    assert result["verified"] is False
    assert result["live_enabled"] is False


def test_malformed_non_true_capabilities_fail_closed():
    result = inspect_capabilities("x", {"createOrder": 1, "fetchTicker": "yes"})
    assert not result.has_private_order and not result.has_public_ticker
