"""CCXT bridge — TESTNET-only, fail-closed, decisão do Prado registrada.

Regras (decisão do Prado, dono do sistema):
- Corretora selecionável entre a linha DZ23 (Binance, Bybit, OKX, Coinbase,
  Kraken, Gate.io, KuCoin, Mercado Bitcoin, Bitget) via CCXT.
- MODO TESTNET: padrão; exchange sem testnet real ⇒ recusa (nunca degrada
  silenciosamente para live).
- MODO LIVE (dinheiro real): existe e funciona, mas SÓ nasce com a frase de
  autorização explícita do dono em LIVE_TRADING_CONFIRM — a confirmação é
  DELE, não do software. Sem a frase exata, live recusa nascer.
- TODO envio de ordem passa OBRIGATORIAMENTE por assert_secure_for_capital
  (consumidor selado do Gate B) ANTES de qualquer chamada de rede.
- Idempotência: clientOrderId determinístico derivado do job — retry NUNCA
  duplica ordem.
- Journal: qualquer payload logado passa por redact_secrets.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from tradingagents.dataflows.redact import redact_secrets
from tradingagents.gates import assert_secure_for_capital

# Nome exposto ao usuário -> id real no ccxt 4.x (verificado em runtime).
EXCHANGE_ALIASES = {
    "binance": "binance",
    "bybit": "bybit",
    "okx": "okx",
    "coinbase": "coinbaseexchange",
    "kraken": "kraken",
    "gateio": "gate",
    "kucoin": "kucoin",
    "mercadobitcoin": "mercado",
    "bitget": "bitget",
}


LIVE_CONFIRM_PHRASE = "EU AUTORIZO OPERAR COM DINHEIRO REAL"


class BrokerSecurityError(RuntimeError):
    """Refusal by construction — live nunca acontece por acidente."""


class CCXTBridge:
    def __init__(self, env: dict | None = None, journal_path: str | None = None):
        env = os.environ if env is None else env
        self.exchange_name = (env.get("TRADINGAGENTS_EXCHANGE") or "").strip().lower()
        mode = (env.get("TRADINGAGENTS_BROKER_MODE") or "").strip().lower()
        self.api_key = env.get("EXCHANGE_API_KEY") or ""
        self.api_secret = env.get("EXCHANGE_API_SECRET") or ""
        self.journal_path = Path(journal_path or env.get(
            "TRADINGAGENTS_BROKER_JOURNAL", "/data/broker-journal.jsonl"))

        # --- fail-closed ANTES de qualquer objeto de rede existir ---
        if mode not in ("testnet", "live"):
            raise BrokerSecurityError(
                "TRADINGAGENTS_BROKER_MODE deve ser 'testnet' ou 'live' "
                f"(recebido: {mode!r}).")
        self.mode = mode
        if mode == "live":
            confirm = (env.get("LIVE_TRADING_CONFIRM") or "").strip()
            if confirm != LIVE_CONFIRM_PHRASE:
                raise BrokerSecurityError(
                    "Modo LIVE exige a frase exata de autorização do dono em "
                    f"LIVE_TRADING_CONFIRM: '{LIVE_CONFIRM_PHRASE}'. Sem ela, "
                    "dinheiro real não entra em jogo.")
            if not (self.api_key and self.api_secret):
                raise BrokerSecurityError(
                    "Modo LIVE exige EXCHANGE_API_KEY e EXCHANGE_API_SECRET.")
        if self.exchange_name not in EXCHANGE_ALIASES:
            raise BrokerSecurityError(
                f"exchange {self.exchange_name!r} fora da linha DZ23 "
                f"({', '.join(sorted(EXCHANGE_ALIASES))}).")

        import ccxt  # import tardio: erro de dependência é erro claro
        ccxt_id = EXCHANGE_ALIASES[self.exchange_name]
        if not hasattr(ccxt, ccxt_id):
            raise BrokerSecurityError(
                f"ccxt instalado não possui {ccxt_id!r}; atualizar dependência.")
        self._ex = getattr(ccxt, ccxt_id)({
            "apiKey": self.api_key, "secret": self.api_secret,
            "enableRateLimit": True,
        })
        if self.mode == "testnet":
            if not (self._ex.urls or {}).get("test"):
                raise BrokerSecurityError(
                    f"{self.exchange_name} não expõe testnet no CCXT. Em "
                    "testnet use binance, bybit, okx, gateio ou coinbase; "
                    "para as demais, o modo live (com a frase de autorização) "
                    "é o único caminho — nunca degrado silenciosamente.")
            self._ex.set_sandbox_mode(True)
        # live: endpoints reais, sem sandbox — autorizado pela frase acima.

    # ------------------------------------------------------------------
    @staticmethod
    def client_order_id(job_id: str, symbol: str, side: str) -> str:
        base = f"{job_id}|{symbol}|{side}".encode()
        return "dz23-" + hashlib.sha256(base).hexdigest()[:20]

    def _journal(self, event: dict) -> None:
        # Defesa em profundidade: cada campo string é redigido ISOLADO (como
        # fragmento, onde o scanner pega '"secret": "..."'), e a linha inteira
        # é redigida de novo após a serialização.
        event = {k: (redact_secrets(v) if isinstance(v, str) else v)
                 for k, v in event.items()}
        line = json.dumps(event, ensure_ascii=False, default=str)
        line = redact_secrets(line)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.journal_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def status(self) -> dict:
        return {
            "exchange": self.exchange_name,
            "ccxt_id": self._ex.id,
            "mode": self.mode,
            "sandbox": self.mode == "testnet",
            "keys_set": bool(self.api_key and self.api_secret),
            "aviso": ("DINHEIRO REAL — autorizado pela frase do dono"
                      if self.mode == "live" else
                      "testnet: ordens de mentira, risco zero"),
        }

    def submit_order(self, final_state: dict, *, job_id: str, symbol: str,
                     side: str, amount: float, order_type: str = "market") -> dict:
        """Única porta de envio. O gate roda ANTES de qualquer rede."""
        assert_secure_for_capital(final_state)  # incontornável, sem flag
        if side not in ("buy", "sell"):
            raise ValueError("side deve ser buy|sell")
        if not (isinstance(amount, (int, float)) and amount > 0):
            raise ValueError("amount deve ser > 0")
        coid = self.client_order_id(job_id, symbol, side)
        params = {"clientOrderId": coid}
        if self._ex.id == "okx":
            params = {"clOrdId": coid[:32]}
        self._journal({"ts": time.time(), "event": "submit_attempt",
                       "exchange": self.exchange_name, "symbol": symbol,
                       "side": side, "amount": amount, "client_order_id": coid,
                       "job_id": job_id})
        try:
            order = self._ex.create_order(symbol, order_type, side, amount,
                                          None, params)
        except Exception as exc:  # rede/exchange: journal redigido + repropaga
            self._journal({"ts": time.time(), "event": "submit_error",
                           "client_order_id": coid,
                           "error": f"{type(exc).__name__}: {exc}"[:500]})
            raise
        self._journal({"ts": time.time(), "event": "submit_ok",
                       "client_order_id": coid,
                       "order_id": order.get("id")})
        return {"client_order_id": coid, "order": order}
