from types import SimpleNamespace

from service.personal_exchange_discovery import (
    discover_ccxt_catalog,
    discover_exchange_ids,
    installed_ccxt_exchange_ids,
)


def test_deduplicates_and_sorts():
    assert discover_exchange_ids(["okx", "binance", "okx"]) == ("binance", "okx")


def test_invalid_names_not_accepted():
    assert discover_exchange_ids(["../secrets", "__import__('os')", "", "okx"]) == ("okx",)


def test_catalog_never_enables_orders():
    entries = discover_ccxt_catalog(["okx", "binance"], enabled_ids=["okx"])
    assert len(entries) == 2
    assert all(not e.execution_enabled for e in entries)
    assert {e.id: e.status for e in entries} == {
        "binance": "AVAILABLE_NOT_CONFIGURED", "okx": "SELECTED_NOT_VERIFIED"
    }


def test_absent_unknown_selected_exchange_is_not_advertised():
    assert discover_ccxt_catalog(["binance"], enabled_ids=["fake"]) == discover_ccxt_catalog(["binance"])


def test_can_read_installed_adapter_list_without_network():
    assert installed_ccxt_exchange_ids(lambda: SimpleNamespace(exchanges=["kraken", "okx"])) == ("kraken", "okx")
