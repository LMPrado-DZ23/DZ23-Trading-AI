"""Contract checks for authenticated read-only exchange discovery."""
from fastapi.routing import APIRoute

from service.app import app, auth


def test_personal_exchanges_route_is_get_only():
    routes = [
        route for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/v1/personal/exchanges"
    ]
    assert len(routes) == 1
    assert routes[0].methods == {"GET"}


def test_personal_exchanges_route_requires_authentication():
    route = next(
        route for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/v1/personal/exchanges"
    )
    assert any(dep.call is auth for dep in route.dependant.dependencies)


def test_personal_exchanges_no_execution(monkeypatch):
    import service.personal_exchange_discovery as discovery
    from service.app import personal_exchanges

    monkeypatch.setattr(discovery, "installed_ccxt_exchange_ids", lambda: ("okx", "binance"))
    payload = personal_exchanges()
    assert payload["execution_enabled"] is False
    assert all(not exchange["execution_enabled"] for exchange in payload["ccxt_available"])
    assert all(not room["live_enabled"] for room in payload["fixed_rooms"])
    assert payload["discovery_error"] is None
