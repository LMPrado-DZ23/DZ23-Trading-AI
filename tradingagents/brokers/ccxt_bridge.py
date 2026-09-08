"""Legacy CCXT bridge retained for read-only compatibility.

Regras (decisão do Prado, dono do sistema):
- Corretora selecionável entre a linha DZ23 (Binance, Bybit, OKX, Coinbase,
  Kraken, Gate.io, KuCoin, Mercado Bitcoin, Bitget) via CCXT.
- MODO TESTNET: padrão; exchange sem testnet real ⇒ recusa (nunca degrada
  silenciosamente para live).
- Order submission is permanently disabled in this legacy adapter.  The A5
  platform will introduce a separate adapter behind immutable intents,
  reservations, an outbox and reconciliation.
- TODO envio de ordem passa OBRIGATORIAMENTE por assert_secure_for_capital
  (consumidor selado do Gate B) ANTES de qualquer chamada de rede.
- Journal compatibility remains available and redacts every payload.
"""
from __future__ import annotations

import hashlib
import json
import os
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
            "submission_enabled": False,
            "aviso": "adaptador legado bloqueado; nenhuma ordem pode ser enviada",
        }

    def submit_order(self, final_state: dict, *, job_id: str, symbol: str,
                     side: str, amount: float, order_type: str = "market") -> dict:
        """Refuse before network even when the legacy transport mark is valid."""
        assert_secure_for_capital(final_state)
        raise BrokerSecurityError(
            "legacy CCXT order submission is disabled; use an audited ExecutionIntent"
        )
