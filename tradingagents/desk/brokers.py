"""Adaptador de corretora para a mesa: a mesma interface serve PaperBroker e
CCXT (testnet/live), para não existirem dois livros de ordens.

O adaptador NÃO relaxa nada: ele delega à ponte CCXT já auditada, que exige
o gate de capital, modo explícito e — em live — a frase de autorização do
dono. Aqui só se acrescenta a tradução do contrato.
"""
from __future__ import annotations

from decimal import Decimal

from .execution import BrokerTimeout


def _e_ordem_inexistente(exc: BaseException) -> bool:
    """A corretora afirmou que a ordem não existe?

    Só classes que significam EXATAMENTE isso. Erro de rede, permissão ou
    parâmetro não provam ausência — e tratá-los como ausência devolveria
    capital de uma ordem viva.
    """
    # SOMENTE a exceção que significa exatamente "esta ordem não existe".
    # InvalidOrder cobre identificador malformado, símbolo ausente e consulta
    # não suportada — nada disso prova ausência, e tratar como prova
    # devolveria capital de uma ordem viva.
    return type(exc).__name__ == "OrderNotFound"


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
    JANELA_HISTORICO = 200
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
    def fetch_order_by_client_id(self, client_order_id: str) -> dict:
        """Estado da ordem na corretora, com TRÊS respostas distintas.

        O contrato anterior devolvia None tanto para "a corretora confirma
        que não existe" quanto para "não apareci na janela que consultei" —
        e o coordenador DEVOLVIA O CAPITAL nos dois casos. Uma ordem antiga,
        fora das últimas 50, liberava reserva estando viva.

        resultado:
          FOUND               -> `ordem` preenchida
          CONFIRMED_NOT_FOUND -> a corretora respondeu que não conhece
          UNKNOWN             -> não foi possível provar ausência
        """
        ex = self.bridge._ex
        # 1) consulta DIRETA por id, quando a corretora suporta: é a única
        #    que prova ausência.
        if hasattr(ex, "fetch_order"):
            try:
                o = ex.fetch_order(client_order_id)
                if o:
                    return {"resultado": "FOUND", "ordem": self._ordem_canonica(o)}
            except Exception as exc:
                if _e_ordem_inexistente(exc):
                    return {"resultado": "CONFIRMED_NOT_FOUND", "ordem": None,
                            "motivo": f"corretora respondeu {type(exc).__name__}"}
                # qualquer outra falha é desconhecimento, não ausência
                return {"resultado": "UNKNOWN", "ordem": None,
                        "motivo": f"consulta direta falhou ({type(exc).__name__})"}

        # 2) varredura do histórico: pode ENCONTRAR, nunca provar ausência.
        if not hasattr(ex, "fetch_orders"):
            return {"resultado": "UNKNOWN", "ordem": None,
                    "motivo": "corretora não expõe consulta de ordens"}
        try:
            ordens = ex.fetch_orders(None, None, self.JANELA_HISTORICO)
        except Exception as exc:
            return {"resultado": "UNKNOWN", "ordem": None,
                    "motivo": f"consulta de histórico falhou ({type(exc).__name__})"}
        for o in ordens or []:
            if client_order_id in self._identidades(o):
                return {"resultado": "FOUND", "ordem": self._ordem_canonica(o)}
        return {"resultado": "UNKNOWN", "ordem": None,
                "motivo": (f"ordem não apareceu nas últimas "
                           f"{len(ordens or [])} ordens consultadas — isso NÃO "
                           "prova que ela não existe")}

    def fetch_order_fills(self, client_order_id: str,
                          exchange_order_id: str = "") -> list[dict]:
        """Execuções (trades) da ordem. É daqui que saem os efeitos
        econômicos: `order.filled` é CUMULATIVO e não serve como delta."""
        ex = self.bridge._ex
        if not hasattr(ex, "fetch_my_trades"):
            raise BrokerConsultaIndisponivel(
                "corretora não expõe consulta de execuções")
        try:
            trades = ex.fetch_my_trades()
        except Exception as exc:
            raise BrokerConsultaIndisponivel(
                f"consulta de execuções falhou ({type(exc).__name__})") from exc
        saida = []
        for t in trades or []:
            if (t.get("order") not in (exchange_order_id, client_order_id)
                    and client_order_id not in self._identidades(t)):
                continue
            fid = t.get("id") or (t.get("info") or {}).get("tradeId")
            if not fid:
                # Sem identidade de execução não há como deduplicar: inventar
                # unicidade é o caminho para contabilizar duas vezes.
                raise BrokerConsultaIndisponivel(
                    "execução sem identificador: impossível deduplicar")
            taxa = t.get("fee") or {}
            saida.append({
                "fill_id": str(fid),
                "exchange_order_id": t.get("order") or "",
                "client_order_id": client_order_id,
                "instrument": t.get("symbol") or "",
                "side": (t.get("side") or "").lower(),
                "quantity": Decimal(str(t.get("amount") or 0)),
                "price": Decimal(str(t.get("price") or 0)),
                "fee": Decimal(str(taxa.get("cost") or 0)),
                "fee_currency": taxa.get("currency") or "",
                "timestamp": t.get("timestamp"),
            })
        return saida

    @staticmethod
    def _identidades(o: dict) -> set:
        """Delegação estrita à normalização canônica — sem lista própria."""
        from .identity import order_identities
        return order_identities(o)

    def capacidades(self):
        """Matriz de capacidades desta corretora, detectada por comportamento."""
        from .capabilities import detectar_capacidades
        return detectar_capacidades(self.bridge._ex)

    def fetch_open_orders(self) -> list:
        """Ordens ABERTAS da conta — inclusive as que este sistema não criou."""
        ex = self.bridge._ex
        if not hasattr(ex, "fetch_open_orders"):
            raise BrokerConsultaIndisponivel(
                "corretora não expõe consulta de ordens abertas")
        try:
            return ex.fetch_open_orders() or []
        except Exception as exc:
            raise BrokerConsultaIndisponivel(
                f"consulta de ordens abertas falhou ({type(exc).__name__})"
            ) from exc

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
                # A moeda viaja junto: sem ela o custo real fica desconhecido
                # e o consumidor teria de adivinhar.
                "fee_currency": ((o.get("fee") or {}).get("currency")
                                 if isinstance(o.get("fee"), dict) else "") or "",
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
            "fee_currency": (order.get("fee") or {}).get("currency") or "",
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
