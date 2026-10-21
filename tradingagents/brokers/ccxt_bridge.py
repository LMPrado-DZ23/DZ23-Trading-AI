"""CCXT bridge — TESTNET-only, fail-closed, decisão do Prado registrada.

Regras (decisão do Prado, dono do sistema):
- Corretora selecionável entre a linha DZ23 (Binance, Bybit, OKX, Coinbase,
  Kraken, Gate.io, KuCoin, Mercado Bitcoin, Bitget) via CCXT.
- MODO TESTNET: padrão; exchange sem testnet real ⇒ recusa (nunca degrada
  silenciosamente para live).
- MODO LIVE (dinheiro real): existe e é FECHADO por construção, não removido.
  A ponte só cria cliente live com: (1) a frase exata do dono, (2) chaves
  configuradas, (3) teto por ordem e (4) teto de perda diária — todos
  positivos. Por cima disso, a MESA recusa construir corretora LIVE sem
  autorização explícita de código (`live_authorized=True`), que o painel
  não concede (auditoria FIN-1, ADR-006).
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
from decimal import Decimal, InvalidOperation
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


def _decimal_env(env, chave: str, padrao: str) -> Decimal:
    try:
        return Decimal(str(env.get(chave) or padrao))
    except (InvalidOperation, ValueError):
        return Decimal("0")


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
            # DINHEIRO REAL. Três fechaduras independentes, todas do dono:
            # 1) a frase exata, 2) o teto por ordem, 3) o teto diário.
            confirm = (env.get("LIVE_TRADING_CONFIRM") or "").strip()
            if confirm != LIVE_CONFIRM_PHRASE:
                raise BrokerSecurityError(
                    "Modo LIVE exige a frase exata do dono em "
                    f"LIVE_TRADING_CONFIRM: '{LIVE_CONFIRM_PHRASE}'.")
            if not (self.api_key and self.api_secret):
                raise BrokerSecurityError(
                    "Modo LIVE exige EXCHANGE_API_KEY e EXCHANGE_API_SECRET.")
            self.max_order_value = _decimal_env(
                env, "LIVE_MAX_ORDER_VALUE", "0")
            self.max_daily_loss = _decimal_env(env, "LIVE_MAX_DAILY_LOSS", "0")
            if self.max_order_value <= 0:
                raise BrokerSecurityError(
                    "Modo LIVE exige LIVE_MAX_ORDER_VALUE (teto por ordem, na "
                    "moeda de cotação). Sem teto, um erro de cálculo vira "
                    "ordem do tamanho da conta.")
            if self.max_daily_loss <= 0:
                raise BrokerSecurityError(
                    "Modo LIVE exige LIVE_MAX_DAILY_LOSS (perda máxima no dia).")
        else:
            self.max_order_value = Decimal("0")
            self.max_daily_loss = Decimal("0")
        self.kill_switch = False
        self.realized_loss_today = Decimal("0")
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

    # ---------------------- travas de dinheiro real -------------------
    def _preco_de_referencia(self, symbol: str) -> Decimal:
        """Preço para medir o tamanho da ordem. Sem preço confiável, NÃO
        envia: ordem sem tamanho conhecido é ordem sem teto."""
        try:
            ticker = self._ex.fetch_ticker(symbol)
            preco = ticker.get("last") or ticker.get("close") or ticker.get("bid")
            if preco:
                return Decimal(str(preco))
        except Exception as exc:
            raise BrokerSecurityError(
                f"sem preço para medir a ordem em {symbol} "
                f"({type(exc).__name__}) — envio recusado em modo LIVE") from exc
        raise BrokerSecurityError(
            f"sem preço para medir a ordem em {symbol} — envio recusado")

    def _assert_live_limits(self, symbol: str, side: str, amount: Decimal) -> None:
        if self.kill_switch:
            raise BrokerSecurityError(
                "PARADA DE EMERGÊNCIA ativa: nenhum envio em dinheiro real")
        valor = amount * self._preco_de_referencia(symbol)
        if valor > self.max_order_value:
            raise BrokerSecurityError(
                f"ordem de {valor:.2f} excede o teto por ordem "
                f"({self.max_order_value}). Aumente LIVE_MAX_ORDER_VALUE "
                "conscientemente ou reduza a quantidade.")
        if self.realized_loss_today >= self.max_daily_loss:
            raise BrokerSecurityError(
                f"perda do dia ({self.realized_loss_today}) atingiu o limite "
                f"({self.max_daily_loss}); operações em dinheiro real param "
                "até você revisar e liberar")
        self._journal({"ts": time.time(), "event": "live_limits_ok",
                       "symbol": symbol, "side": side, "valor_estimado": str(valor),
                       "teto_ordem": str(self.max_order_value)})

    def set_daily_loss(self, perda: Decimal) -> Decimal:
        """Define a perda acumulada do dia, vinda do LIVRO-RAZÃO.

        É esta a fonte da verdade: o contador manual anterior dependia de
        alguém lembrar de chamá-lo, e ninguém chamava no fluxo real.
        """
        self.realized_loss_today = max(Decimal("0"), Decimal(str(perda)))
        return self.realized_loss_today

    def register_loss(self, valor: Decimal) -> Decimal:
        """Compatibilidade: soma um prejuízo pontual. Prefira set_daily_loss,
        que vem do livro e não de chamada manual."""
        if valor > 0:
            self.realized_loss_today += valor
        return self.realized_loss_today

    def emergency_stop(self, motivo: str = "acionado pelo dono") -> None:
        """Botão de pânico: bloqueia envios até reiniciar deliberadamente."""
        self.kill_switch = True
        self._journal({"ts": time.time(), "event": "emergency_stop",
                       "motivo": motivo, "mode": self.mode})

    def resume(self, frase: str) -> None:
        """Retomar exige a MESMA frase do dono — não é um clique distraído."""
        if frase.strip() != LIVE_CONFIRM_PHRASE:
            raise BrokerSecurityError(
                "para retomar após a parada de emergência, repita a frase de "
                "autorização")
        self.kill_switch = False
        self._journal({"ts": time.time(), "event": "resume", "mode": self.mode})

    def status(self) -> dict:
        return {
            "exchange": self.exchange_name,
            "ccxt_id": self._ex.id,
            "mode": self.mode,
            "sandbox": self.mode == "testnet",
            "teto_por_ordem": str(self.max_order_value),
            "perda_maxima_dia": str(self.max_daily_loss),
            "perda_hoje": str(self.realized_loss_today),
            "parada_emergencia": self.kill_switch,
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
        if self.mode == "live":
            self._assert_live_limits(symbol, side, Decimal(str(amount)))
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

    def submit_protective_stop(self, final_state: dict, *, job_id: str,
                               symbol: str, amount: float,
                               stop_price: Decimal,
                               order_type: str | None = None) -> dict:
        """Stop de VENDA para uma posição comprada — DEPOIS do fill.

        O gate de capital roda antes de qualquer rede, exatamente como no
        envio. O tipo é configurável porque o CCXT não unifica stop em spot:
        `stop_loss_limit` (padrão) ou `stop_market`. Exchange que não suportar
        o tipo recusa; o executor então encerra a posição por segurança — não
        existe posição aberta "protegida" só na memória.
        """
        assert_secure_for_capital(final_state)  # incontornável, sem flag
        if not (isinstance(amount, (int, float)) and amount > 0):
            raise ValueError("amount deve ser > 0")
        try:
            stop_dec = Decimal(str(stop_price))
        except (ArithmeticError, ValueError, TypeError) as exc:
            raise ValueError("stop_price inválido") from exc
        if not stop_dec.is_finite() or stop_dec <= 0:
            raise ValueError("stop_price deve ser > 0")
        if self.mode == "live":
            self._assert_live_limits(symbol, "sell", Decimal(str(amount)))

        tipo = (order_type
                or os.environ.get("TRADINGAGENTS_STOP_ORDER_TYPE")
                or "stop_loss_limit").strip().lower()
        if tipo not in ("stop_loss_limit", "stop_market"):
            raise BrokerSecurityError(
                "TRADINGAGENTS_STOP_ORDER_TYPE deve ser stop_loss_limit ou "
                f"stop_market (recebido: {tipo!r})")
        try:
            offset = Decimal(os.environ.get(
                "TRADINGAGENTS_STOP_LIMIT_OFFSET_PCT") or "0.005")
        except (ArithmeticError, ValueError):
            offset = Decimal("0.005")
        if not (Decimal("0") <= offset < Decimal("0.1")):
            offset = Decimal("0.005")
        limit_price = (stop_dec * (Decimal("1") - offset)).quantize(
            Decimal("0.00000001"))

        coid = self.client_order_id(job_id, symbol, "sell")
        params = {"stopPrice": float(stop_dec), "timeInForce": "GTC"}
        if (os.environ.get("TRADINGAGENTS_STOP_REDUCE_ONLY") or "").strip().lower() in {
                "1", "true", "sim", "yes", "on"}:
            # Só faz sentido em derivativos; em spot a exchange recusa e o
            # executor encerra a posição. Declarado, não adivinhado.
            params["reduceOnly"] = True
        if self._ex.id == "okx":
            params["clOrdId"] = coid[:32]
        else:
            params["clientOrderId"] = coid
        preco_limite = float(limit_price) if tipo == "stop_loss_limit" else None
        self._journal({"ts": time.time(), "event": "stop_submit_attempt",
                       "exchange": self.exchange_name, "symbol": symbol,
                       "side": "sell", "amount": amount,
                       "stop_price": str(stop_dec), "order_type": tipo,
                       "client_order_id": coid, "job_id": job_id})
        try:
            order = self._ex.create_order(symbol, tipo, "sell", amount,
                                          preco_limite, params)
        except Exception as exc:
            self._journal({"ts": time.time(), "event": "stop_submit_error",
                           "client_order_id": coid,
                           "error": f"{type(exc).__name__}: {exc}"[:500]})
            raise
        self._journal({"ts": time.time(), "event": "stop_submit_ok",
                       "client_order_id": coid,
                       "order_id": order.get("id"),
                       "stop_price": str(stop_dec)})
        return {"client_order_id": coid, "order": order,
                "stop_price": str(stop_dec)}

    def fetch_order(self, order_id: str, symbol: str) -> dict:
        """Consulta READ-ONLY de uma ordem (para reconciliação do stop).

        Não envia nada e não exige o gate de capital; a resposta é do lado
        da corretora e não substitui o estado do OMS.
        """
        if not order_id:
            raise ValueError("order_id vazio — não há o que consultar")
        try:
            order = self._ex.fetch_order(order_id, symbol)
        except Exception as exc:
            self._journal({"ts": time.time(), "event": "fetch_order_error",
                           "order_id": order_id, "symbol": symbol,
                           "error": f"{type(exc).__name__}: {exc}"[:500]})
            raise
        self._journal({"ts": time.time(), "event": "fetch_order_ok",
                       "order_id": order_id, "symbol": symbol,
                       "status": (order or {}).get("status")})
        return order or {}
