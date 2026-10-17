"""Sonda pública das nove corretoras: rede de leitura, nunca execução."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from service.personal_exchange_catalog import EXCHANGES
from service.personal_exchange_health import probe_public_exchange


class FakeExchange:
    has = {"fetchTicker": True, "fetchOHLCV": True}
    configs = []
    closed = 0

    def __init__(self, config):
        type(self).configs.append(dict(config))

    def fetch_ticker(self, symbol):
        return {"symbol": symbol, "last": 100}

    def fetch_ohlcv(self, symbol, timeframe, limit=3):
        assert timeframe == "1h" and limit == 3 and symbol
        return [[1, 90, 110, 80, 100, 10]] * 3

    def close(self):
        type(self).closed += 1


def _ccxt_falso():
    return SimpleNamespace(**{
        exchange.ccxt_id: FakeExchange for exchange in EXCHANGES
        if exchange.ccxt_id
    })


class TestSondaPublica:
    def setup_method(self):
        FakeExchange.configs.clear()
        FakeExchange.closed = 0

    @pytest.mark.parametrize(
        "slug", [exchange.slug for exchange in EXCHANGES
                 if exchange.read_only_enabled])
    def test_toda_corretora_aprovada_tem_sonda_sem_credencial(self, slug):
        tempos = iter((10.0, 10.125))
        resultado = probe_public_exchange(
            slug, loader=_ccxt_falso, monotonic=lambda: next(tempos))
        assert resultado["ok"] is True
        assert resultado["status"] == "PUBLIC_READ_OK"
        assert resultado["credentials_used"] is False
        assert resultado["methods"] == ["fetch_ticker", "fetch_ohlcv"]
        assert resultado["latency_ms"] == 125
        cfg = FakeExchange.configs[-1]
        assert not {"apiKey", "secret", "password", "uid"} & set(cfg)
        assert FakeExchange.closed >= 1

    def test_resposta_malformada_falha_fechada(self):
        class Malformada(FakeExchange):
            def fetch_ticker(self, symbol):
                return {"symbol": symbol, "last": "NaN"}

        modulo = _ccxt_falso()
        modulo.binance = Malformada
        resultado = probe_public_exchange("binance", loader=lambda: modulo)
        assert resultado["ok"] is False
        assert resultado["status"] == "PUBLIC_READ_UNAVAILABLE"
        assert resultado["error_type"] == "ValueError"
        assert resultado["credentials_used"] is False

    def test_experimental_nao_e_disfarcada_de_operacional(self):
        resultado = probe_public_exchange("iq_option", loader=_ccxt_falso)
        assert resultado["ok"] is False
        assert resultado["status"] == "NOT_ELIGIBLE"

    def test_slug_desconhecido_e_recusado(self):
        with pytest.raises(KeyError):
            probe_public_exchange("nao_existe", loader=_ccxt_falso)


class TestRotaDaSonda:
    def test_desconhecida_vira_404(self):
        from service.app import personal_exchange_public_health
        with pytest.raises(HTTPException) as erro:
            personal_exchange_public_health("nao_existe")
        assert erro.value.status_code == 404

    def test_retorna_prova_sem_credencial(self, monkeypatch):
        import service.personal_exchange_health as modulo
        monkeypatch.setattr(modulo, "probe_public_exchange", lambda slug: {
            "id": slug, "ok": True, "status": "PUBLIC_READ_OK",
            "credentials_used": False,
        })
        from service.app import personal_exchange_public_health
        resposta = personal_exchange_public_health("binance")
        assert resposta == {
            "id": "binance", "ok": True, "status": "PUBLIC_READ_OK",
            "credentials_used": False,
        }
