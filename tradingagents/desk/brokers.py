"""Adaptador de corretora para a mesa: a mesma interface serve PaperBroker e
CCXT (testnet/live), para não existirem dois livros de ordens.

O adaptador NÃO relaxa nada: ele delega à ponte CCXT já auditada, que exige
o gate de capital, modo explícito e — em live — a frase de autorização do
dono. Aqui só se acrescenta a tradução do contrato.
"""
from __future__ import annotations

from decimal import Decimal

from .execution import BrokerTimeout


class BrokerConsultaIndisponivel(RuntimeError):
    """Consulta à corretora falhou: o resultado é DESCONHECIDO, e desconhecido
    mantém o portão fechado. Nunca deve virar 'nada encontrado'."""


class BrokerAdapterError(RuntimeError):
    """Falha ANTES de a requisição poder ter chegado à corretora.

    Só use esta classe quando for possível afirmar que a exchange NÃO
    recebeu a ordem. Na dúvida, use BrokerTimeout.
    """


# Erros que indicam que a requisição pode ter sido entregue e a resposta se
# perdeu. Nomes de classe do ccxt e da pilha HTTP, comparados por nome para
# não exigir o pacote instalado.
_AMBIGUOS = {
    "RequestTimeout", "ReadTimeout", "ReadTimeoutError", "Timeout",
    "ConnectionResetError", "RemoteDisconnected", "ProtocolError",
    "IncompleteRead", "ChunkedEncodingError", "ConnectionClosed",
    "NetworkError", "ExchangeNotAvailable", "OperationFailed",
    "InvalidNonce", "RequestException",
}
# Erros que provam que a ordem NÃO foi criada.
_NAO_CHEGOU = {
    "AuthenticationError", "PermissionDenied", "InsufficientFunds",
    "InvalidOrder", "BadSymbol", "ArgumentsRequired", "BadRequest",
    "NotSupported", "InsecureTransportError", "BrokerSecurityError",
    "ValueError", "TypeError", "DNSError", "NameResolutionError",
    "ConnectionRefusedError",
}


def _pode_ter_chegado(exc: BaseException) -> bool:
    """Fail-closed: o desconhecido é tratado como AMBÍGUO.

    Um erro novo de biblioteca não pode ser silenciosamente classificado
    como "não chegou" — esse é exatamente o erro que libera capital sobre
    uma ordem que existe na exchange.
    """
    for classe in type(exc).__mro__:
        nome = classe.__name__
        if nome in _NAO_CHEGOU:
            return False
        if nome in _AMBIGUOS:
            return True
    return True                      # desconhecido => ambíguo => reconciliar


class CCXTDeskBroker:
    """Envia ordens reais (testnet por padrão) pelo contrato do executor.

    `final_state` é o estado com a marca de transporte exigida pelo gate de
    capital do Gate A — sem ele a ponte recusa antes de qualquer rede.
    """

    def __init__(self, env: dict, *, final_state: dict, journal_path: str | None = None):
        from tradingagents.brokers.ccxt_bridge import CCXTBridge
        self.bridge = CCXTBridge(env=env, journal_path=journal_path)
        self.final_state = final_state
        self.mode = self.bridge.mode
        self.submitted: dict[str, dict] = {}

    # ------------------------------------------------ consultas canônicas
    # A reconciliação depende destes contratos. Eles existiam apenas como
    # dublês nos testes: o caminho real nunca foi exercitado. O adaptador
    # traduz a resposta do CCXT para uma estrutura canônica — o coordenador
    # não pode interpretar objeto específico de exchange.
    def fetch_order_by_client_id(self, client_order_id: str) -> dict | None:
        """Estado canônico da ordem, ou None se a corretora não a conhece."""
        ex = self.bridge._ex
        try:
            ordens = ex.fetch_orders(None, None, 50) if hasattr(ex, "fetch_orders") else []
        except Exception as exc:
            raise BrokerConsultaIndisponivel(
                f"consulta de ordens falhou ({type(exc).__name__})") from exc
        for o in ordens or []:
            info = o.get("info") or {}
            candidatos = {o.get("clientOrderId"), info.get("clientOrderId"),
                          info.get("clOrdId"), o.get("id")}
            if client_order_id in {c for c in candidatos if c}:
                return self._ordem_canonica(o)
        return None

    def fetch_positions(self) -> dict:
        """Posições por instrumento, em Decimal. Zero é omitido."""
        ex = self.bridge._ex
        if not hasattr(ex, "fetch_balance"):
            raise BrokerConsultaIndisponivel(
                "corretora não expõe consulta de saldo/posição")
        try:
            saldo = ex.fetch_balance()
        except Exception as exc:
            raise BrokerConsultaIndisponivel(
                f"consulta de posições falhou ({type(exc).__name__})") from exc
        if not isinstance(saldo, dict):
            raise BrokerConsultaIndisponivel("resposta de saldo malformada")
        total = saldo.get("total")
        if not isinstance(total, dict):
            raise BrokerConsultaIndisponivel("saldo sem a seção 'total'")
        cotacao = self._moeda_de_cotacao()
        posicoes: dict[str, Decimal] = {}
        for moeda, quantidade in total.items():
            if moeda == cotacao:
                continue                     # caixa não é posição
            try:
                qtd = Decimal(str(quantidade))
            except (TypeError, ValueError, ArithmeticError):
                raise BrokerConsultaIndisponivel(
                    f"quantidade inválida para {moeda}: {quantidade!r}") from None
            if qtd != 0:
                posicoes[f"{moeda}/{cotacao}"] = qtd
        return posicoes

    def _moeda_de_cotacao(self) -> str:
        for instrumento in getattr(self, "universe", ()) or ():
            if "/" in instrumento:
                return instrumento.split("/")[1]
        return "USDT"

    @staticmethod
    def _ordem_canonica(o: dict) -> dict:
        info = o.get("info") or {}
        return {"id": o.get("id"), "status": (o.get("status") or "").lower(),
                "filled": o.get("filled"), "average": o.get("average") or o.get("price"),
                "fee": ((o.get("fee") or {}).get("cost") if isinstance(o.get("fee"), dict)
                        else o.get("fee")) or 0,
                "fill_id": info.get("tradeId") or info.get("execId"),
                "client_order_id": o.get("clientOrderId") or info.get("clOrdId")}

    def submit(self, client_order_id: str, instrument: str, side: str,
               quantity: Decimal, final_state: dict | None = None) -> dict:
        if client_order_id in self.submitted:
            return self.submitted[client_order_id]   # retry não duplica
        # Procedência POR ORDEM. O estado fixo do construtor era `{}` em
        # produção, e o gate recusava tudo: o caminho real nunca chegava à
        # corretora. Cada ordem agora carrega a prova da própria decisão.
        estado = final_state if final_state is not None else self.final_state
        try:
            out = self.bridge.submit_order(
                estado, job_id=client_order_id, symbol=instrument,
                side=side, amount=float(quantity))
        except Exception as exc:
            # CLASSIFICAÇÃO OBRIGATÓRIA. Antes, TODA exceção virava
            # BrokerAdapterError e o executor a interpretava como REJEITADA,
            # liberando a reserva. Se a exchange tiver recebido a ordem, isso
            # cria posição órfã e capital prometido duas vezes.
            #
            # A pergunta que decide: a requisição PODE ter chegado à
            # exchange? Se sim, o resultado é AMBÍGUO, nunca "rejeitado".
            if _pode_ter_chegado(exc):
                raise BrokerTimeout(
                    f"envio ambíguo ({type(exc).__name__}): a corretora pode "
                    "ter recebido a ordem — reconciliar antes de qualquer "
                    "nova exposição") from exc
            raise BrokerAdapterError(f"{type(exc).__name__}: {exc}"[:300]) from exc
        order = out.get("order") or {}
        result = {
            "id": order.get("id") or out.get("client_order_id"),
            "status": order.get("status") or "acknowledged",
            "filled": Decimal(str(order.get("filled") or 0)),
            "average": Decimal(str(order.get("average") or order.get("price") or 0)),
            "fee": Decimal(str((order.get("fee") or {}).get("cost") or 0)),
        }
        self.submitted[client_order_id] = result
        return result

    def status(self) -> dict:
        return self.bridge.status()


def build_desk_broker(cfg: dict, *, final_state: dict, journal_path: str | None = None,
                      paper_price: Decimal = Decimal("100")):
    """Escolhe a corretora da mesa a partir da configuração salva.

    Sem `TRADINGAGENTS_DESK_BROKER=ccxt` explícito, a mesa opera em PAPER —
    o padrão nunca toca corretora externa por acidente.
    """
    from .execution import PaperBroker
    if (cfg.get("TRADINGAGENTS_DESK_BROKER") or "paper").strip().lower() != "ccxt":
        return PaperBroker(paper_price), "paper"
    env = {k: cfg.get(k, "") for k in
           ("TRADINGAGENTS_EXCHANGE", "TRADINGAGENTS_BROKER_MODE",
            "EXCHANGE_API_KEY", "EXCHANGE_API_SECRET", "LIVE_TRADING_CONFIRM",
            "LIVE_MAX_ORDER_VALUE", "LIVE_MAX_DAILY_LOSS")}
    broker = CCXTDeskBroker(env, final_state=final_state, journal_path=journal_path)
    return broker, broker.mode


__all__ = ["CCXTDeskBroker", "BrokerAdapterError", "build_desk_broker"]
