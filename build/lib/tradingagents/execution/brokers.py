"""SHADOW, local PAPER and official Alpaca PAPER adapters. No live endpoint exists."""

from __future__ import annotations

import os
from typing import Any, Protocol

import requests

from tradingagents.dataflows.redact import redact_secrets

from .models import ExecutionIntent, ExecutionReceipt

ALPACA_PAPER_ORDERS_URL = "https://paper-api.alpaca.markets/v2/orders"
_MAX_RESPONSE_BYTES = 64_000


class BrokerAdapter(Protocol):
    def submit(self, intent: ExecutionIntent) -> ExecutionReceipt: ...


def _receipt(intent: ExecutionIntent, broker: str, status: str, **kwargs: Any) -> ExecutionReceipt:
    return ExecutionReceipt(
        client_order_id=intent.client_order_id,
        mode=intent.mode,
        broker=broker,
        status=status,
        request_sha256=intent.request_sha256,
        **kwargs,
    )


class ShadowBroker:
    def submit(self, intent: ExecutionIntent) -> ExecutionReceipt:
        if intent.mode != "shadow":
            raise ValueError("ShadowBroker accepts only shadow intents")
        return _receipt(intent, "shadow", "observed")


class LocalPaperBroker:
    """Deterministic local simulator; it never opens a network connection."""

    def submit(self, intent: ExecutionIntent) -> ExecutionReceipt:
        if intent.mode != "paper":
            raise ValueError("LocalPaperBroker accepts only paper intents")
        status = "held" if intent.action == "HOLD" else "simulated_filled"
        return _receipt(
            intent,
            "local-paper",
            status,
            broker_order_id=intent.client_order_id,
            raw_status=status,
        )


class AlpacaPaperBroker:
    """Official Alpaca paper endpoint with pinned origin and retry-safe IDs."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        timeout_seconds: float = 10,
        _session: requests.Session | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("APCA_API_KEY_ID", "")
        self._api_secret = api_secret or os.environ.get("APCA_API_SECRET_KEY", "")
        if not self._api_key or not self._api_secret:
            raise ValueError("Alpaca paper credentials are required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout = timeout_seconds
        self._session = _session or requests.Session()
        self._session.trust_env = False

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._api_secret,
            "Content-Type": "application/json",
        }

    def _decode(self, response: requests.Response) -> dict[str, Any]:
        content = response.content
        if len(content) > _MAX_RESPONSE_BYTES:
            raise RuntimeError("Alpaca paper response exceeded size limit")
        try:
            value = response.json()
        except ValueError as exc:
            raise RuntimeError("Alpaca paper returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Alpaca paper returned an invalid response shape")
        return value

    def _lookup(self, client_order_id: str) -> dict[str, Any] | None:
        response = self._session.get(
            f"{ALPACA_PAPER_ORDERS_URL}:by_client_order_id",
            params={"client_order_id": client_order_id},
            headers=self._headers,
            timeout=self._timeout,
            allow_redirects=False,
        )
        if response.status_code == 404:
            return None
        if response.is_redirect or response.status_code >= 400:
            raise RuntimeError(f"Alpaca paper lookup failed ({response.status_code})")
        return self._decode(response)

    def submit(self, intent: ExecutionIntent) -> ExecutionReceipt:
        if intent.mode != "paper":
            raise ValueError("AlpacaPaperBroker accepts only paper intents")
        if intent.asset_type not in {"stock", "crypto"}:
            raise ValueError("Alpaca paper adapter supports only stocks and crypto")
        if intent.action == "HOLD" or intent.quantity == 0:
            return _receipt(intent, "alpaca-paper", "held")
        existing = self._lookup(intent.client_order_id)
        response_data = existing
        if response_data is None:
            time_in_force = "gtc" if intent.asset_type == "crypto" else "day"
            response = self._session.post(
                ALPACA_PAPER_ORDERS_URL,
                json={
                    "symbol": (
                        intent.ticker.replace("-", "/")
                        if intent.asset_type == "crypto"
                        else intent.ticker
                    ),
                    "qty": str(intent.quantity),
                    "side": intent.action.lower(),
                    "type": "market",
                    "time_in_force": time_in_force,
                    "client_order_id": intent.client_order_id,
                },
                headers=self._headers,
                timeout=self._timeout,
                allow_redirects=False,
            )
            if response.is_redirect or response.status_code >= 400:
                recovered = self._lookup(intent.client_order_id)
                if recovered is None:
                    detail = redact_secrets(response.text)[:500]
                    raise RuntimeError(
                        f"Alpaca paper order failed ({response.status_code}): {detail}"
                    )
                response_data = recovered
            else:
                response_data = self._decode(response)
        order_id = response_data.get("id")
        status = response_data.get("status")
        if not isinstance(order_id, str) or not isinstance(status, str):
            raise RuntimeError("Alpaca paper response lacks order identity/status")
        return _receipt(
            intent,
            "alpaca-paper",
            "submitted",
            broker_order_id=order_id,
            raw_status=status,
        )
