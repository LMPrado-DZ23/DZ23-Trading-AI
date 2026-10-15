"""F5 — prova viva READ-ONLY dos conectores de mercado (MISSÃO V9).

Roda DELIBERADAMENTE fora do executor hermético `tools/v9_pytest.py`:
este script precisa de rede real. Ele NUNCA envia ordem, NUNCA usa
credencial (nem chave de corretora nem de IA) e NUNCA registra segredo —
apenas dados públicos de mercado e fatos de conectividade.

Provas executadas (código REAL do produto onde existe):

1. PublicMarketFeed (feed da mesa, `tradingagents/desk/market.py`)
   - Binance PRODUÇÃO, público, sem chaves: velas 1h de BTC/USDT.
   - Livro L2 público (fetchOrderBook) de BTC/USDT.
   - Se a Binance estiver bloqueada por região, tenta Mercado Bitcoin.
2. CCXTBridge (`tradingagents/brokers/ccxt_bridge.py`) em modo TESTNET
   sem chaves: objeto real da ponte contra `testnet.binance.vision`
   (ticker público, leitura; nenhuma ordem é criada).
3. Dataflow YFinance do app (`tradingagents/dataflows/y_finance.py`):
   get_YFin_data_online("AAPL") — API pública do Yahoo, sem chave.

Saída: um JSON único em stdout (capturado para
docs/mission/evidence/v9-baseline/f5-probe-real-readonly.json).
Exit code 0 = o JSON foi produzido (mesmo que alguma prova registre
falha de conectividade, isso é evidência honesta e fica no JSON).
"""
from __future__ import annotations

import json
import platform
import sys
import tempfile
import time
import traceback
from datetime import datetime, timedelta, timezone


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _probe(nome: str, alvo: str, caminho_produto: str, fn):
    inicio = time.time()
    resultado = {
        "probe": nome,
        "alvo": alvo,
        "caminho_no_produto": caminho_produto,
        "credenciais_usadas": False,
        "ordens_enviadas": False,
    }
    try:
        extras = fn() or {}
        resultado.update({"ok": True, **extras})
    except Exception as exc:  # evidência honesta: tipo + mensagem curta
        resultado.update({
            "ok": False,
            "erro": f"{type(exc).__name__}: {exc}"[:300],
            "traceback_curto": traceback.format_exc(limit=3).splitlines()[-3:],
        })
    resultado["duracao_s"] = round(time.time() - inicio, 2)
    return resultado


def probe_feed_binance() -> dict:
    from tradingagents.desk.market import PublicMarketFeed

    feed = PublicMarketFeed("binance")
    snap = feed.snapshot("BTC/USDT", "1h", limit=5)
    if not snap.closes:
        raise RuntimeError(f"snapshot degradado sem velas: {snap.quality}")
    return {
        "fonte": snap.source,
        "qualidade": snap.quality,
        "n_velas_fechadas": len(snap.closes),
        "ultimo_fechamento_utc": snap.cutoff.astimezone(timezone.utc).isoformat(),
        "ultimo_preco_publico": str(snap.last),
    }


def probe_livro_binance() -> dict:
    from tradingagents.desk.market import PublicMarketFeed

    feed = PublicMarketFeed("binance")
    leitura = feed.leitura_de_livro("BTC/USDT", limit=20, niveis=5)
    campos = {}
    for nome in ("status", "motivo", "motivo_falha", "exchange", "instrumento",
                 "conteudo", "niveis_bid", "niveis_ask", "lancamentos"):
        if hasattr(leitura, nome):
            valor = getattr(leitura, nome)
            campos[nome] = valor if isinstance(valor, (str, int, float, bool)) \
                else f"{type(valor).__name__}({len(valor) if hasattr(valor, '__len__') else '?'})"
    return {"tipo_retorno": type(leitura).__name__, "campos": campos}


def _probe_feed_exchange(exchange_id: str) -> dict:
    from tradingagents.desk.market import PublicMarketFeed

    feed = PublicMarketFeed(exchange_id)
    snap = feed.snapshot("BTC/BRL" if exchange_id == "mercado" else "BTC/USDT",
                         "1h", limit=5)
    if not snap.closes:
        raise RuntimeError(f"snapshot degradado sem velas: {snap.quality}")
    return {
        "fonte": snap.source,
        "qualidade": snap.quality,
        "n_velas_fechadas": len(snap.closes),
        "ultimo_preco_publico": str(snap.last),
    }


def probe_bridge_testnet() -> dict:
    from tradingagents.brokers.ccxt_bridge import CCXTBridge

    env = {
        "TRADINGAGENTS_EXCHANGE": "binance",
        "TRADINGAGENTS_BROKER_MODE": "testnet",
        # sem EXCHANGE_API_KEY / EXCHANGE_API_SECRET de propósito
    }
    tmp = tempfile.mkdtemp(prefix="v9-f5-bridge-")
    bridge = CCXTBridge(env=env, journal_path=f"{tmp}/journal.jsonl")
    status = bridge.status()
    ticker = bridge._ex.fetch_ticker("BTC/USDT")  # leitura pública do sandbox
    ultimo = ticker.get("last") or ticker.get("close") or ticker.get("bid")
    if ultimo is None:
        raise RuntimeError("sandbox respondeu sem preço (last/close/bid)")
    return {
        "objeto_real": "CCXTBridge modo=testnet",
        "status_da_ponte": {
            "exchange": status["exchange"],
            "ccxt_id": status["ccxt_id"],
            "mode": status["mode"],
            "sandbox": status["sandbox"],
            "keys_set": status["keys_set"],
            "aviso": status["aviso"],
        },
        "ticker_sandbox": {
            "symbol": ticker.get("symbol"),
            "ultimo_preco_publico": str(ultimo),
            "exchange_no_payload": ticker.get("info", {}).get("timezone")
            if isinstance(ticker.get("info"), dict) else None,
        },
    }


def probe_yfinance() -> dict:
    from tradingagents.dataflows.y_finance import get_YFin_data_online

    fim = datetime.now(timezone.utc).date()
    inicio = fim - timedelta(days=14)
    texto = get_YFin_data_online(
        "AAPL", inicio.strftime("%Y-%m-%d"), fim.strftime("%Y-%m-%d"))
    total_linhas = None
    for linha in texto.splitlines():
        if linha.startswith("# Total records:"):
            total_linhas = int(linha.split(":")[1].strip())
            break
    ultima_linha_dados = next(
        (linha for linha in reversed(texto.splitlines())
         if linha and not linha.startswith("#")),
        None,
    )
    assert total_linhas and total_linhas > 0, \
        f"YFinance devolveu 0 registros (cabeçalho lido: {total_linhas})"
    return {
        "fonte": "Yahoo Finance (yfinance) via dataflow do app",
        "janela": f"{inicio} a {fim}",
        "total_registros": total_linhas,
        "ultima_linha_de_dados": (ultima_linha_dados or "")[:200],
    }


def main() -> int:
    import ccxt

    evidencias = {
        "missao": "V9-F5",
        "quando_utc": _iso_now(),
        "python": sys.version.split()[0],
        "plataforma": platform.platform(aliased=True)[:120],
        "ccxt_version": getattr(ccxt, "__version__", "?"),
        "observacao": ("Execução DELIBERADA fora do executor hermético: "
                       "rede real, somente leitura pública, sem credenciais, "
                       "sem ordens."),
        "provas": [],
    }

    evidencias["provas"].append(_probe(
        "feed_publico_binance_producao",
        "Binance produção — velas 1h BTC/USDT (CCXT público)",
        "tradingagents.desk.market.PublicMarketFeed.snapshot",
        probe_feed_binance,
    ))
    if not evidencias["provas"][-1]["ok"]:
        evidencias["provas"].append(_probe(
            "feed_publico_mercado_btc_brasil",
            "Mercado Bitcoin — velas 1h BTC/BRL (CCXT público)",
            "tradingagents.desk.market.PublicMarketFeed.snapshot",
            lambda: _probe_feed_exchange("mercado"),
        ))
    evidencias["provas"].append(_probe(
        "livro_l2_binance_producao",
        "Binance produção — livro L2 público BTC/USDT",
        "tradingagents.desk.market.PublicMarketFeed.leitura_de_livro",
        probe_livro_binance,
    ))
    evidencias["provas"].append(_probe(
        "ponte_ccxt_testnet_sem_chaves",
        "Binance TESTNET (testnet.binance.vision) — ticker público",
        "tradingagents.brokers.ccxt_bridge.CCXTBridge(modo=testnet)",
        probe_bridge_testnet,
    ))
    evidencias["provas"].append(_probe(
        "yfinance_publico",
        "Yahoo Finance — AAPL 14 dias (sem chave)",
        "tradingagents.dataflows.y_finance.get_YFin_data_online",
        probe_yfinance,
    ))

    print(json.dumps(evidencias, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
