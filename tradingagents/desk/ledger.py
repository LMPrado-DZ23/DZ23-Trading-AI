"""Contabilidade da mesa (TA-010).

Lançamentos imutáveis em partidas dobradas: toda linha tem contrapartida e a
soma dos débitos é igual à dos créditos, sempre. Correção só por compensação
— nada é editado retroativamente para o resultado parecer melhor.

EQUAÇÃO MULTIMOEDA
------------------
A partida dobrada vale na moeda FUNCIONAL (a de cotação da conta, `currency`):

    soma(contas funcionais) == 0

Quantidades em outras moedas — taxa cobrada em BNB, por exemplo — vivem num
SUBLEDGER NATIVO, uma escrituração por moeda, cada uma fechando em si:

    para cada moeda M != funcional:  soma(contas nativas de M) == 0

As duas escriturações só se encontram numa CONVERSÃO explícita
(`fee_valuation`), que declara os dois lados: `native_amount`/`native_currency`
e `functional_amount`/`functional_currency`, com a cotação e sua origem.

Somar as duas num total único produziria `600 USDT - 2 BNB + 598 = 0`: os
números fecham porque foram tratados como se não tivessem dimensão — o mesmo
erro de somar metro com quilo e concluir que deu certo.

Contas usadas:
  CASH:<moeda>          caixa disponível
  POSITION:<instrumento> posição em quantidade valorizada ao custo
  FEES                   custos de corretagem
  PNL:<agente>           resultado atribuído ao trader
  RESERVED               capital travado por reserva de risco
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from .models import utcnow

ZERO = Decimal("0")


class LedgerError(RuntimeError):
    """Lançamento desbalanceado ou conta inexistente."""


@dataclass(frozen=True)
class Entry:
    """Uma linha imutável. Débito positivo, crédito negativo."""
    account: str
    amount: Decimal
    memo: str = ""


def _instante(valor):
    """Converte o instante persistido, preservando o que foi gravado."""
    from datetime import datetime
    if isinstance(valor, datetime):
        return valor
    try:
        return datetime.fromisoformat(str(valor))
    except ValueError:
        return utcnow()


@dataclass(frozen=True)
class Transaction:
    tx_id: str
    entries: tuple[Entry, ...]
    kind: str
    at: datetime = field(default_factory=utcnow)
    ref: str = ""
    # Quantidade e dono não são deriváveis dos saldos (o livro guarda CUSTO,
    # não unidades). Sem isto, restaurar a posição era impossível.
    instrument: str = ""
    quantity: Decimal = ZERO
    agent_id: str = ""
    # Movimento de capital: a que se destina e a qual incidente pertence.
    # Sem o vínculo, um aporte feito para o incidente A resolvia o B.
    purpose: str = ""
    incident_id: str = ""
    settlement_id: str = ""
    # Conversão entre escriturações: os DOIS lados declarados, nunca um
    # número solto que se soma ao outro.
    fee_component_ids: tuple = ()
    valuation_id: str = ""
    valuation_fingerprint: str = ""
    # Versão da política sob a qual a valoração foi impressa. Guardada para o
    # recovery recomputar a impressão com a MESMA política de origem, em vez
    # da atual — senão toda retomada após uma troca de política viraria
    # conflito falso.
    valuation_policy_version: str = ""
    native_amount: Decimal = ZERO
    native_currency: str = ""
    functional_amount: Decimal = ZERO
    functional_currency: str = ""
    # Identidade DURÁVEL dos componentes de taxa criados nesta transação —
    # dado estruturado, não texto de memo. Um memo editado não pode destruir
    # a prova de qual cobrança existiu.
    fee_component_records: tuple = ()

    def _moeda_nativa(self, conta: str, funcional: str) -> str:
        for prefixo in ("FEES:", "ASSET:", "CONTRIBUTED_CAPITAL:"):
            if conta.startswith(prefixo):
                moeda = conta.split(":", 1)[1]
                if moeda != funcional:
                    return moeda
        return ""

    def validar_dimensoes(self, funcional: str) -> None:
        """Cada escrituração fecha DENTRO da transação.

        Aceitar +2 BNB com -2 USDT porque a soma dá zero é somar unidades
        diferentes. Movimento entre escriturações exige os metadados de
        conversão declarados — aí a diferença tem significado.
        """
        por_moeda: dict = {}
        for e in self.entries:
            moeda = self._moeda_nativa(e.account, funcional) or funcional
            por_moeda[moeda] = por_moeda.get(moeda, ZERO) + e.amount
        abertas = {m: v for m, v in por_moeda.items() if v != ZERO}
        if not abertas:
            return
        declarada = (self.native_currency and self.functional_currency
                     and self.native_amount != ZERO)
        if not declarada:
            raise ValueError(
                f"transação mistura unidades sem declarar conversão: "
                f"{ {m: str(v) for m, v in abertas.items()} }. Some débitos "
                "e créditos de moedas diferentes não é partida dobrada")

    def validate(self) -> None:
        total = sum((e.amount for e in self.entries), ZERO)
        if total != ZERO:
            raise LedgerError(f"transação {self.kind} desbalanceada em {total}")
        if len(self.entries) < 2:
            raise LedgerError("partida dobrada exige ao menos duas linhas")


class Ledger:
    """Livro-razão append-only. Saldos derivam das linhas, nunca de um campo."""

    def __init__(self, opening_cash: Decimal = ZERO, currency: str = "USDT"):
        self.currency = currency
        self._txs: list[Transaction] = []
        if opening_cash:
            self._post("opening", [Entry(f"CASH:{currency}", opening_cash, "abertura"),
                                   Entry("EQUITY", -opening_cash, "aporte inicial")])

    # -- núcleo --------------------------------------------------------
    def _post(self, kind: str, entries, ref: str = "", *, instrument: str = "",
              quantity: Decimal = ZERO, agent_id: str = "", purpose: str = "",
              incident_id: str = "", settlement_id: str = "",
              fee_component_ids: list | None = None,
              valuation_id: str = "", valuation_fingerprint: str = "",
              valuation_policy_version: str = "",
              native_amount: Decimal = ZERO, native_currency: str = "",
              functional_amount: Decimal = ZERO, functional_currency: str = "",
              fee_component_records: list | None = None,
              at=None) -> Transaction:
        tx = Transaction(uuid.uuid4().hex[:16], tuple(entries), kind, ref=ref,
                         instrument=instrument, quantity=quantity,
                         agent_id=agent_id, purpose=purpose,
                         incident_id=incident_id, settlement_id=settlement_id,
                         fee_component_ids=tuple(fee_component_ids or ()),
                         valuation_id=valuation_id,
                         valuation_fingerprint=valuation_fingerprint,
                         valuation_policy_version=valuation_policy_version,
                         native_amount=native_amount,
                         native_currency=native_currency,
                         functional_amount=functional_amount,
                         functional_currency=functional_currency,
                         fee_component_records=tuple(
                             fee_component_records or ()),
                         **({"at": at} if at is not None else {}))
        tx.validate()
        tx.validar_dimensoes(self.currency)
        self._txs.append(tx)
        return tx

    def balance(self, account: str) -> Decimal:
        return sum((e.amount for tx in self._txs for e in tx.entries
                    if e.account == account), ZERO)

    def balances(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for tx in self._txs:
            for e in tx.entries:
                out[e.account] = out.get(e.account, ZERO) + e.amount
        return {k: v for k, v in out.items() if v != ZERO}

    @property
    def transactions(self) -> tuple[Transaction, ...]:
        return tuple(self._txs)

    def _e_nativa(self, conta: str) -> str:
        """Moeda NATIVA desta conta, ou "" se ela é da escrituração funcional."""
        for prefixo in ("FEES:", "ASSET:", "CONTRIBUTED_CAPITAL:"):
            if conta.startswith(prefixo):
                moeda = conta.split(":", 1)[1]
                if moeda != self.currency:
                    return moeda
        return ""

    def functional_balances(self) -> dict:
        """Contas da moeda funcional — onde a partida dobrada é verificada."""
        return {c: s for c, s in self.balances().items()
                if not self._e_nativa(c)}

    def native_balances(self) -> dict:
        """Subledger por moeda: {moeda: {tipo: saldo}}."""
        saidas: dict = {}
        for conta, saldo in self.balances().items():
            moeda = self._e_nativa(conta)
            if not moeda:
                continue
            tipo = conta.split(":", 1)[0]
            saidas.setdefault(moeda, {})[tipo] = saldo
        return saidas

    def functional_balanced(self) -> bool:
        """A partida dobrada da moeda funcional, sozinha."""
        return sum(self.functional_balances().values(), ZERO) == ZERO

    def native_balanced(self) -> bool:
        """Cada subledger nativo fecha em si."""
        return all(sum(contas.values(), ZERO) == ZERO
                   for contas in self.native_balances().values())

    def is_balanced(self) -> bool:
        """Balanceado = funcional fecha E cada moeda nativa fecha.

        Somar tudo num total único daria `600 USDT - 2 BNB + 598` e chamaria
        isso de zero.
        """
        return self.functional_balanced() and self.native_balanced()

    # -- operações da mesa ---------------------------------------------
    @staticmethod
    def conta_reserva(reservation_id: str = "") -> str:
        """Conta de capital travado DESTA reserva.

        Uma conta global fazia o livro saber apenas o total: uma ordem podia
        consumir ou liberar dinheiro de outra, e a conferência por soma não
        via nada de errado (R1=50/R2=150 soma igual a R1=100/R2=100).
        """
        return f"RESERVED:{reservation_id}" if reservation_id else "RESERVED"

    def reserve(self, amount: Decimal, ref: str,
                reservation_id: str = "") -> Transaction:
        conta = self.conta_reserva(reservation_id)
        return self._post("reserve", [Entry(conta, amount, "capital travado"),
                                      Entry(f"CASH:{self.currency}", -amount)], ref)

    def release(self, amount: Decimal, ref: str,
                reservation_id: str = "") -> Transaction:
        # Liberar mais do que ESTA reserva tem levaria a conta abaixo de zero
        # — dinheiro criado do nada, com o livro ainda "balanceado".
        if amount <= 0:
            raise ValueError(f"liberação precisa ser positiva (recebido {amount})")
        conta = self.conta_reserva(reservation_id)
        reservado = self.balances().get(conta, ZERO)
        if amount > reservado:
            raise ValueError(
                f"liberação de {amount} maior que o reservado em {conta} "
                f"({reservado})")
        return self._post("release", [Entry(f"CASH:{self.currency}", amount),
                                      Entry(conta, -amount, "reserva liberada")], ref)

    def transferir_reserva_legada(self, amount: Decimal,
                                  reservation_id: str) -> Transaction:
        """Move o saldo da conta global antiga para a conta identificada.

        Só é chamada quando há evidência de quem é o dono — nunca por rateio.
        """
        return self._post("migracao_reserva",
                          [Entry(self.conta_reserva(reservation_id), amount,
                                 "reserva identificada na migração"),
                           Entry("RESERVED", -amount, "saldo legado movido")],
                          f"migracao:{reservation_id}")

    def fill_buy(self, instrument: str, quantity: Decimal, price: Decimal,
                 fee: Decimal, *, reserved: Decimal, ref: str = "",
                 agent_id: str = "", reservation_id: str = "",
                 fill_id: str = "",
                 native_fees: list | tuple | None = None,
                 fee_in_other_currency: tuple | None = None) -> Transaction:
        """Compra executada: reserva vira posição; sobra volta ao caixa.

        `reserved` sai da conta DESTA reserva — nunca do bolo comum.
        """
        cost = (quantity * price).quantize(Decimal("0.00000001"))
        # A taxa tem MOEDA. Guardar tudo numa conta "FEES" fazia 0,001 BNB
        # somar 0,001 ao custo em USDT — número errado num livro que fecha,
        # porque a aritmética está certa e o significado não.
        entries = [Entry(f"POSITION:{instrument}", cost, "posição ao custo"),
                   Entry(f"FEES:{self.currency}", fee, "corretagem"),
                   Entry(self.conta_reserva(reservation_id), -reserved,
                         "reserva consumida")]
        rest = reserved - cost - fee
        if rest != ZERO:
            entries.append(Entry(f"CASH:{self.currency}", rest, "sobra devolvida"))
        pernas, registros = self._pernas_nativas(
            self._um_parametro_de_taxa(native_fees, fee_in_other_currency),
            "corretagem em outra moeda", fill_id=fill_id or ref)
        entries.extend(pernas)
        return self._post("fill_buy", entries, ref, instrument=instrument,
                          quantity=quantity, agent_id=agent_id,
                          fee_component_records=registros)

    def fill_sell(self, instrument: str, quantity: Decimal, price: Decimal,
                  fee: Decimal, *, cost_basis: Decimal, agent_id: str,
                  ref: str = "",
                  native_fees: list | tuple | None = None,
                  fee_in_other_currency: tuple | None = None,
                  fill_id: str = "") -> Transaction:
        """Venda executada: baixa a posição pelo custo e atribui o resultado.

        A conta PNL:<agente> carrega o resultado BRUTO (venda − custo); a
        corretagem vive em FEES, visível e nunca embutida. Somar as duas é
        o resultado líquido — contar a taxa nos dois lugares desbalanceia o
        livro (erro pego por teste ao implementar as saídas).
        """
        proceeds = (quantity * price).quantize(Decimal("0.00000001"))
        bruto = proceeds - cost_basis
        entries = [Entry(f"CASH:{self.currency}", proceeds - fee, "caixa da venda"),
                   Entry(f"FEES:{self.currency}", fee, "corretagem da saída"),
                   Entry(f"POSITION:{instrument}", -cost_basis, "baixa ao custo"),
                   Entry(f"PNL:{agent_id}", -bruto, "resultado bruto atribuído")]
        pernas, registros = self._pernas_nativas(
            self._um_parametro_de_taxa(native_fees, fee_in_other_currency),
            "corretagem da saída em outra moeda", fill_id=fill_id or ref)
        entries.extend(pernas)
        return self._post("fill_sell", entries, ref, instrument=instrument,
                          quantity=quantity, agent_id=agent_id,
                          fee_component_records=registros)

    def ai_cost(self, amount: Decimal, agent_id: str, ref: str = "") -> Transaction:
        """Custo de IA é custo real — entra na conta, não some."""
        return self._post("ai_cost", [Entry("COST:AI", amount, f"IA {agent_id}"),
                                      Entry(f"CASH:{self.currency}", -amount)], ref)

    def compensate(self, tx: Transaction, why: str) -> Transaction:
        """Corrige por compensação — a linha errada continua no histórico."""
        return self._post("compensation",
                          [Entry(e.account, -e.amount, why) for e in tx.entries],
                          ref=tx.tx_id)

    # -- relatórios -----------------------------------------------------
    def attribution(self) -> dict[str, Decimal]:
        """Resultado BRUTO realizado por trader (custos ficam em FEES)."""
        return {acc.split(":", 1)[1]: -bal for acc, bal in self.balances().items()
                if acc.startswith("PNL:")}

    def quote_currency(self) -> str:
        return self.currency

    def cash(self) -> Decimal:
        """Caixa na moeda de cotação do livro.

        O snapshot procurava a conta 'CASH' fixa, que não existe (a conta é
        'CASH:USDT'), e persistia zero. Caixa errado no restore é perda de
        verdade financeira.
        """
        return self.balances().get(f"CASH:{self.currency}", ZERO)

    def open_positions_detail(self) -> dict:
        """Posições abertas com custo e dono — o necessário para reconstruir.

        Derivado dos lançamentos, como todo o resto: o livro não guarda
        objeto de posição, e inventar um seria criar uma segunda verdade.
        """
        saldos = self.balances()
        detalhe: dict[str, dict] = {}
        for conta, custo in saldos.items():
            if not conta.startswith("POSITION:") or custo <= ZERO:
                continue
            instrumento = conta.split(":", 1)[1]
            qtd = self.position_quantity(instrumento)
            detalhe[instrumento] = {
                "instrumento": instrumento,
                "quantidade": str(qtd),
                "custo_total": str(custo),
                "preco_medio": str((custo / qtd) if qtd else ZERO),
                "agente": self.position_owner(instrumento),
            }
        return detalhe

    # ---------------------------------------------- visão de capital
    # Estados DISTINTOS do dinheiro. Tratá-los como sinônimos é o que fazia o
    # capital investido reaparecer como disponível:
    #   caixa + reservado + investido = patrimônio (antes do resultado)
    def cash_available(self) -> Decimal:
        """Dinheiro livre: nem reservado, nem transformado em posição."""
        return self.cash()

    def reserved_cash(self, reservation_id: str | None = None) -> Decimal:
        """Dinheiro travado — de UMA reserva, ou o total.

        Sem identificador devolve a soma, que serve para métricas. Toda
        MOVIMENTAÇÃO, porém, exige a reserva específica: é o que impede uma
        ordem de gastar a cobertura da outra.
        """
        saldos = self.balances()
        if reservation_id is not None:
            return saldos.get(self.conta_reserva(reservation_id), ZERO)
        return sum((s for c, s in saldos.items()
                    if c == "RESERVED" or c.startswith("RESERVED:")), ZERO)

    def find_incident_compensations(self, incident_id: str) -> list[dict]:
        """Compensações já lançadas para este incidente.

        É o que permite ao retry pós-crash DESCOBRIR que o dinheiro já saiu,
        em vez de lançar de novo.
        """
        return [tx for tx in self.export_transactions()
                if tx.get("kind") == "risk_incident_compensation"
                and tx.get("incident_id") == incident_id]

    def fee_valuation(self, *, moeda: str, quantidade: Decimal,
                      rate: Decimal, fonte: str, em: str,
                      fee_component_ids: list,
                      incident_id: str = "",
                      valuation_id: str = "",
                      policy_version: str = "") -> Transaction:
        """API OPERACIONAL: valora a taxa com LASTRO obrigatório.

        Exige a lista de componentes que estão sendo convertidos e cobre a
        quantidade EXATA deles — nem sobra, nem parte. Sem isso, um
        componente de 2 BNB marcaria VALUED enquanto só 1 foi valorado, e a
        outra unidade sumiria do controle de ativo descoberto.

        Validações (todas recusam, nenhuma silenciosa):
        - lista não vazia (sem lastro não há prova de qual cobrança converteu);
        - sem duplicados (um componente financia UMA valoração);
        - todos existentes, na moeda `moeda` e ainda NÃO valorados;
        - soma dos componentes == `quantidade`.
        """
        ids = list(fee_component_ids or [])
        if not ids:
            raise ValueError(
                "valoração operacional exige fee_component_ids: sem lastro a "
                "valoração não prova qual cobrança está convertendo. Para a "
                "mecânica sem lastro use fee_valuation_sem_lastro (compat)")
        if len(ids) != len(set(ids)):
            raise ValueError(
                f"fee_component_ids com duplicados: {ids}. Um componente "
                "financia UMA valoração — citá-lo duas vezes lastraria o dobro")
        disponiveis = {c["fee_component_id"]: c
                       for c in self.fee_components(moeda, True)}
        ausentes = [c for c in ids if c not in disponiveis]
        if ausentes:
            raise ValueError(
                f"componentes já valorados ou inexistentes: {ausentes}. "
                "Um componente financia UMA valoração")
        soma = sum((Decimal(str(disponiveis[c]["amount"])) for c in ids), ZERO)
        if soma != quantidade:
            raise ValueError(
                f"soma dos componentes ({soma} {moeda}) diferente da "
                f"quantidade valorada ({quantidade} {moeda}): a valoração "
                "cobre a quantidade EXATA dos componentes, sem sobra nem parte")
        return self._valorar(
            moeda=moeda, quantidade=quantidade, rate=rate, fonte=fonte, em=em,
            fee_component_ids=ids, incident_id=incident_id,
            valuation_id=valuation_id, policy_version=policy_version)

    def fee_valuation_sem_lastro(self, *, moeda: str, quantidade: Decimal,
                                 rate: Decimal, fonte: str, em: str,
                                 incident_id: str = "",
                                 valuation_id: str = "",
                                 policy_version: str = "") -> Transaction:
        """COMPAT: valoração SEM lastro por componente. Não usar em fluxo novo.

        Mantida, explicitamente separada da API operacional, apenas para os
        chamadores antigos que exercitam a MECÂNICA da valoração (lado nativo
        intacto, passivo funcional, FX) sem se importar com a identidade do
        componente. Não prova qual cobrança converteu — por isso é uma porta
        distinta, com nome que denuncia o que ela deixa de fora.
        """
        return self._valorar(
            moeda=moeda, quantidade=quantidade, rate=rate, fonte=fonte, em=em,
            fee_component_ids=[], incident_id=incident_id,
            valuation_id=valuation_id, policy_version=policy_version)

    def _valorar(self, *, moeda: str, quantidade: Decimal, rate: Decimal,
                 fonte: str, em: str, fee_component_ids: list,
                 incident_id: str, valuation_id: str,
                 policy_version: str) -> Transaction:
        """Núcleo do lançamento de valoração, comum às duas portas.

        Enquanto a conversão não acontece, a taxa existe no livro na moeda
        original e o custo real em cotação é desconhecido. A procedência da
        cotação vai no memo: sem ela, ninguém audita de onde saiu o número.
        """
        if quantidade <= ZERO or rate <= ZERO:
            raise ValueError("valoração exige quantidade e cotação positivas")
        ids = list(fee_component_ids or [])
        memo = (f"taxa {quantidade} {moeda} valorada a {rate} "
                f"{self.currency}/{moeda} | fonte {fonte} | em {em}")
        valor = (quantidade * rate).quantize(Decimal("0.00000001"))
        # Baixa apenas o que REALMENTE está registrado naquela moeda: zerar
        # mais do que existe criaria saldo negativo — o oposto do conserto.
        # A valoração responde UMA pergunta: quanto essa taxa vale na moeda
        # funcional. Ela NÃO financia a origem — creditar ASSET:<moeda> faria
        # um ativo descoberto desaparecer sem aporte nenhum, que é criar
        # dinheiro por contabilidade.
        #
        # LADO FUNCIONAL: a despesa entra contra um PASSIVO, porque a mesa
        # deve esse valor até alguém aportar o ativo ou comprá-lo.
        entradas = [Entry(f"FEES:{self.currency}", valor, memo),
                    Entry("LIABILITY:FEE_FUNDING", -valor, memo)]
        return self._post("fee_valuation", entradas,
                          incident_id or f"valoracao:{moeda}",
                          incident_id=incident_id,
                          fee_component_ids=ids,
                          valuation_id=valuation_id,
                          valuation_policy_version=policy_version,
                          valuation_fingerprint=self.fingerprint_de_valoracao(
                              moeda=moeda, quantidade=quantidade, rate=rate,
                              fonte=fonte, em=em, fee_component_ids=ids,
                              incident_id=incident_id,
                              valuation_id=valuation_id,
                              functional_currency=self.currency,
                              policy_version=policy_version),
                          native_amount=quantidade, native_currency=moeda,
                          functional_amount=valor,
                          functional_currency=self.currency)

    def asset_contribution(self, moeda: str, quantidade: Decimal, *,
                           reason: str, actor: str, rate: Decimal,
                           fonte: str, em: str,
                           external_reference: str = "") -> Transaction:
        """Aporte de ATIVO em moeda não funcional, com valor declarado.

        É isto — não a valoração — que cobre um ASSET negativo. Os dois
        lados são declarados: a quantidade nativa que entrou e quanto ela
        vale, com cotação e origem.
        """
        if quantidade <= ZERO:
            raise ValueError(
                f"aporte de ativo precisa ser positivo (recebido {quantidade})")
        return self._movimento_de_ativo(
            moeda, quantidade, reason=reason, actor=actor, rate=rate,
            fonte=fonte, em=em, external_reference=external_reference,
            kind="asset_contribution", purpose="ASSET_CONTRIBUTION")

    def asset_withdrawal(self, moeda: str, quantidade: Decimal, *,
                         reason: str, actor: str, rate: Decimal,
                         fonte: str, em: str,
                         external_reference: str = "") -> Transaction:
        """Retirada de ativo — operação SEPARADA do aporte."""
        if quantidade <= ZERO:
            raise ValueError(
                f"retirada de ativo precisa ser positiva (recebido {quantidade})")
        return self._movimento_de_ativo(
            moeda, -quantidade, reason=reason, actor=actor, rate=rate,
            fonte=fonte, em=em, external_reference=external_reference,
            kind="asset_withdrawal", purpose="ASSET_WITHDRAWAL")

    def _movimento_de_ativo(self, moeda: str, quantidade: Decimal, *,
                            reason: str, actor: str, rate: Decimal,
                            fonte: str, em: str, external_reference: str,
                            kind: str, purpose: str) -> Transaction:
        if rate is None or Decimal(str(rate)) <= ZERO:
            raise ValueError("movimento de ativo exige cotação positiva")
        if not fonte or not em:
            raise ValueError("movimento de ativo exige fonte e instante")
        valor = (abs(quantidade) * Decimal(str(rate))).quantize(
            Decimal("0.00000001"))
        memo = (f"{reason} | por {actor} | {quantidade} {moeda} a {rate} "
                f"{self.currency}/{moeda} | fonte {fonte} | em {em}")
        # O aporte acontece na escrituração NATIVA: entram 2 BNB contra o
        # capital aportado NAQUELA moeda. A escrituração funcional não se
        # move — ninguém pagou em USDT —, e o valor declarado
        # (functional_amount) registra quanto isso representa, com cotação e
        # origem, para o relatório.
        #
        # Debitar FEES:<moeda> aqui seria pior: reduziria a DESPESA, como se
        # aportar o ativo desfizesse a taxa que já foi cobrada.
        return self._post(
            kind,
            [Entry(f"ASSET:{moeda}", quantidade, memo),
             Entry(f"CONTRIBUTED_CAPITAL:{moeda}", -quantidade, memo)],
            external_reference or reason, purpose=purpose,
            native_amount=abs(quantidade), native_currency=moeda,
            functional_amount=valor, functional_currency=self.currency)

    def capital_adjustment_asset(self, moeda: str, quantidade: Decimal, *,
                                 reason: str, actor: str,
                                 external_reference: str = "") -> Transaction:
        """USO INTERNO/MIGRAÇÃO: aporte de ativo sem valor declarado.

        Não é API operacional. O fluxo normal usa asset_contribution, que
        exige cotação, fonte e instante — sem isso não há como auditar quanto
        o aporte representou.

        Lançava ASSET:<moeda> contra CONTRIBUTED_CAPITAL na moeda funcional,
        misturando unidades numa única soma. Agora fica inteiramente na
        escrituração nativa. Para registrar quanto o aporte VALE, use
        asset_contribution, que exige cotação, fonte e instante.
        """
        if quantidade <= ZERO:
            raise ValueError(
                f"aporte de ativo precisa ser positivo (recebido {quantidade}); "
                "para retirar, use asset_withdrawal, que exige cotação, fonte "
                "e instante")
        memo = f"{reason} | por {actor}"
        if external_reference:
            memo += f" | ref {external_reference}"
        return self._post("asset_adjustment",
                          [Entry(f"ASSET:{moeda}", quantidade, memo),
                           Entry(f"CONTRIBUTED_CAPITAL:{moeda}", -quantidade,
                                 memo)],
                          external_reference or reason,
                          purpose="ASSET_CONTRIBUTION")

    @staticmethod
    def _um_parametro_de_taxa(native_fees, fee_in_other_currency):
        """Recusa os dois parâmetros juntos.

        Aceitar um e ignorar o outro em silêncio faria uma taxa real
        desaparecer sem que ninguém percebesse.
        """
        if native_fees and fee_in_other_currency:
            raise ValueError(
                "conflito de contrato: use native_fees OU "
                "fee_in_other_currency (compatibilidade), nunca ambos — "
                "ignorar um em silêncio perderia uma taxa")
        return native_fees or fee_in_other_currency

    def _pernas_nativas(self, native_fees, memo: str,
                        fill_id: str = "") -> tuple:
        """Uma perna por COMPONENTE nativo — nenhum pode ficar de fora.

        A versão anterior aceitava uma tupla só: com BNB e SOL na mesma
        execução, a segunda sumia do livro e ficava só no incidente.

        Devolve `(pernas, registros)`: as pernas contábeis e os registros
        ESTRUTURADOS de identidade dos componentes (id, fill, moeda, valor),
        para que a identidade não dependa do texto do memo.
        """
        if not native_fees:
            return [], []
        if isinstance(native_fees, tuple) and len(native_fees) == 2 \
                and not isinstance(native_fees[0], (list, tuple)):
            # Compatibilidade com o parâmetro antigo, que aceitava UMA tupla
            # (valor, moeda). Mantido para não reescrever dezenas de chamadas
            # numa rodada que trata de outra coisa.
            native_fees = [native_fees]
        pernas = []
        registros = []
        for componente in native_fees:
            valor, moeda = componente
            # O LIVRO valida por conta própria: depender do parser acima
            # ter feito o trabalho deixa a última porta aberta para um
            # lançamento impossível.
            try:
                valor = Decimal(str(valor))
            except (ArithmeticError, ValueError, TypeError) as exc:
                raise ValueError(
                    f"componente de taxa com valor inválido: {componente!r}"
                ) from exc
            if not valor.is_finite():
                raise ValueError(
                    f"componente de taxa não finito ({valor}): NaN e infinito "
                    "não são valores contabilizáveis")
            if valor < ZERO:
                raise ValueError(
                    f"componente de taxa negativo ({valor}): rebate é outro "
                    "contrato, não uma taxa com sinal trocado")
            moeda = str(moeda or "")
            if not moeda:
                raise ValueError(
                    "componente de taxa sem moeda: não há conta onde lançar")
            if moeda != moeda.strip().upper():
                raise ValueError(
                    f"moeda {moeda!r} não está canônica: use {moeda.strip().upper()!r} "
                    "— formas diferentes criariam contas diferentes")
            if moeda == self.currency:
                raise ValueError(
                    "native_fees é para moedas DIFERENTES da cotação; use o "
                    f"parâmetro fee para {self.currency}")
            if valor == ZERO:
                continue
            # Sai do ativo pago e entra na despesa daquela moeda. Nenhuma
            # conversão: converter com cotação adivinhada produz um número
            # que parece certo e não é.
            # IDENTIDADE do componente: o saldo acumulado não distingue uma
            # cobrança nova de uma antiga já valorada, e usá-lo como prova
            # deixaria a taxa velha financiar a dívida nova.
            import uuid as _uuid
            cid = "fc_" + _uuid.uuid4().hex[:12]
            # O memo mantém a marca (auditoria humana e compat com livros
            # antigos), mas a identidade CANÔNICA é o registro estruturado.
            marca = f"{memo} | fee_component_id={cid} | fill_id={fill_id}"
            pernas.append(Entry(f"FEES:{moeda}", valor, marca))
            pernas.append(Entry(f"ASSET:{moeda}", -valor,
                                "ativo usado para pagar a corretagem"))
            registros.append({"fee_component_id": cid, "fill_id": fill_id,
                              "currency": moeda, "amount": format(valor, "f"),
                              "source": "native_fee"})
        return pernas, registros

    @staticmethod
    def _fp_decimal(x) -> str:
        """Forma canônica de um Decimal: 300 e 300.0 imprimem igual, e sem
        notação exponencial (300, não 3E+2)."""
        d = Decimal(str(x))
        if not d.is_finite():
            raise ValueError(f"valor não finito na impressão: {x!r}")
        return format(d.normalize(), "f")

    @staticmethod
    def _fp_instante(em) -> str:
        """Forma canônica do instante: o MESMO ponto no tempo em offsets
        diferentes imprime igual (tudo normalizado para UTC)."""
        texto = str(em).strip()
        try:
            dt = datetime.fromisoformat(texto)
        except ValueError:
            return texto           # não-ISO: preserva o texto, determinístico
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat()

    @staticmethod
    def fingerprint_de_valoracao(*, moeda: str, quantidade, rate, fonte: str,
                                 em, fee_component_ids=None,
                                 incident_id: str = "",
                                 valuation_id: str = "",
                                 functional_currency: str = "",
                                 policy_version: str = "") -> str:
        """Impressão do CONTEÚDO da valoração.

        Cobre todas as dimensões que definem o resultado — moeda nativa e
        funcional, quantidade e cotação normalizadas, fonte, instante em UTC,
        componentes, incidente e a versão da política. Sem isto, o retry
        pós-crash adota um lançamento e grava metadados divergentes por cima:
        o registro diz 400 e o lançamento diz 300. Normalizar evita o oposto —
        tratar 300 e 300.0, ou o mesmo instante em offsets diferentes, como
        conteúdos distintos. `valuation_id` fica FORA: dois identificadores
        para o MESMO conteúdo não são divergência.
        """
        import hashlib
        partes = [
            (moeda or "").strip().upper(),
            Ledger._fp_decimal(quantidade),
            Ledger._fp_decimal(rate),
            (fonte or "").strip(),
            Ledger._fp_instante(em),
            ",".join(sorted(fee_component_ids or [])),
            incident_id,
            (functional_currency or "").strip().upper(),
            (policy_version or "").strip(),
        ]
        return hashlib.sha256("|".join(partes).encode()).hexdigest()[:32]

    def fee_components(self, moeda: str,
                       apenas_nao_valorados: bool = False) -> list:
        """Componentes de taxa registrados nesta moeda, com identidade.

        `FEES:<moeda>` é saldo HISTÓRICO: permanece depois da valoração. Por
        isso ele não prova que uma ocorrência nova existe — quem prova é o
        componente, que só pode ser usado uma vez.
        """
        valorados = set()
        for tx in self.export_transactions():
            if tx.get("kind") == "fee_valuation":
                for cid in (tx.get("fee_component_ids") or []):
                    valorados.add(cid)
        saida = []
        for tx in self.export_transactions():
            registros = tx.get("fee_component_records") or []
            if registros:
                # IDENTIDADE ESTRUTURADA: não depende do texto do memo. Um
                # memo higienizado ou reformatado não apaga o componente.
                for r in registros:
                    if r.get("currency") != moeda:
                        continue
                    cid = r.get("fee_component_id", "")
                    status = "VALUED" if cid in valorados else "UNVALUED"
                    if apenas_nao_valorados and status == "VALUED":
                        continue
                    saida.append({
                        "fee_component_id": cid, "currency": moeda,
                        "amount": r.get("amount", "0"),
                        "fill_id": r.get("fill_id", ""),
                        "order_id": r.get("order_id") or tx.get("ref") or "",
                        "occurred_at": r.get("occurred_at") or tx.get("at") or "",
                        "source": r.get("source", ""),
                        "valuation_status": status})
                continue
            # COMPAT: livros antigos, sem registro estruturado, lêem do memo.
            for e in tx.get("entries", []):
                if e["account"] != f"FEES:{moeda}":
                    continue
                memo = e.get("memo") or ""
                if "fee_component_id=" not in memo:
                    continue
                cid = memo.split("fee_component_id=")[1].split(" |")[0].strip()
                fid = (memo.split("fill_id=")[1].strip()
                       if "fill_id=" in memo else "")
                status = "VALUED" if cid in valorados else "UNVALUED"
                if apenas_nao_valorados and status == "VALUED":
                    continue
                saida.append({
                    "fee_component_id": cid, "currency": moeda,
                    "amount": e["amount"], "fill_id": fid,
                    "order_id": tx.get("ref") or "",
                    "occurred_at": tx.get("at") or "",
                    "source": "memo", "valuation_status": status})
        return saida

    def find_fee_valuation(self, incident_id: str) -> dict | None:
        """Lançamento de valoração já feito para este incidente.

        É o que permite ao retry pós-crash descobrir que a conversão já
        aconteceu, em vez de lançar de novo.
        """
        for tx in self.export_transactions():
            if (tx.get("kind") == "fee_valuation"
                    and tx.get("incident_id") == incident_id):
                return tx
        return None

    def find_fee_valuation_component(self, incident_id: str,
                                     fee_component_id: str) -> dict | None:
        """Lançamento de valoração DESTE componente neste incidente (F3).

        Cada componente tem sua própria valoração; a busca por incidente só
        não basta quando há várias. A identidade é o `fee_component_id`.
        """
        for tx in self.export_transactions():
            if (tx.get("kind") == "fee_valuation"
                    and tx.get("incident_id") == incident_id
                    and fee_component_id in (tx.get("fee_component_ids") or [])):
                return tx
        return None

    def _find_fee_funding_settlement(self, fee_component_id: str) -> dict | None:
        for tx in self.export_transactions():
            if (tx.get("kind") == "fee_funding_settlement"
                    and fee_component_id in (tx.get("fee_component_ids") or [])):
                return tx
        return None

    def liquidar_fee_funding(self, fee_component_id: str, *, actor: str,
                             reason: str = "",
                             external_reference: str = "") -> Transaction | dict:
        """Liquida o passivo `LIABILITY:FEE_FUNDING` DESTE componente (F4).

        Valorar cria o passivo funcional (a mesa deve o valor da taxa paga em
        moeda não funcional). Depois que o ativo descoberto é financiado, o
        funding é quitado: o passivo é extinto contra o capital que o cobriu.
        Exatamente uma vez por componente, amarrado à sua valoração.
        """
        ja = self._find_fee_funding_settlement(fee_component_id)
        if ja is not None:
            return ja                    # idempotente: não move dinheiro de novo
        val = None
        for tx in self.export_transactions():
            if (tx.get("kind") == "fee_valuation"
                    and fee_component_id in (tx.get("fee_component_ids") or [])):
                val = tx
                break
        if val is None:
            raise ValueError(
                f"componente {fee_component_id} não tem valoração: não há "
                "funding para liquidar")
        moeda = val.get("native_currency") or ""
        valor = Decimal(str(val.get("functional_amount") or "0"))
        if valor <= ZERO:
            raise ValueError(
                f"valoração do componente {fee_component_id} sem valor "
                "funcional para liquidar")
        # O ativo descoberto precisa ter sido financiado ANTES: liquidar sem
        # isso fingiria um lastro que não existe.
        if moeda and self.balance(f"ASSET:{moeda}") < ZERO:
            raise ValueError(
                f"ASSET:{moeda} ainda descoberto ({self.balance(f'ASSET:{moeda}')}"
                "): financie o ativo antes de liquidar o funding da taxa")
        memo = (f"liquidação do funding | componente {fee_component_id} | "
                f"{valor} {self.currency} | por {actor}")
        if reason:
            memo += f" | {reason}"
        return self._post(
            "fee_funding_settlement",
            [Entry("LIABILITY:FEE_FUNDING", valor, memo),
             Entry("CONTRIBUTED_CAPITAL", -valor, memo)],
            external_reference or fee_component_id,
            fee_component_ids=[fee_component_id],
            functional_amount=valor, functional_currency=self.currency)

    def find_incident_compensation(self, incident_id: str,
                                   settlement_id: str) -> dict | None:
        """A compensação DESTA liquidação — exige os dois identificadores.

        Buscar só pelo incidente fazia uma liquidação adotar a transação de
        outra e reescrever a linhagem: o sistema seguia como se tivesse
        pago, sem saber qual das duas identidades era a verdadeira.
        """
        for tx in self.find_incident_compensations(incident_id):
            if tx.get("settlement_id") == settlement_id:
                return tx
        return None

    def risk_incident_compensation(self, *, incident_id: str, amount: Decimal,
                                   external_reference: str, actor: str,
                                   reason: str = "",
                                   settlement_id: str = "") -> Transaction:
        """Compensação de UM incidente específico.

        Um aporte genérico não serve de prova: ele pode ter sido feito por
        qualquer motivo, e aceitar qualquer um ainda não usado deixava um
        aporte destinado ao incidente A resolver o B. A transação nasce
        amarrada ao incidente.
        """
        if amount <= ZERO:
            raise ValueError(
                f"compensação precisa ser positiva (recebido {amount}): "
                "retirada não cobre déficit")
        if not incident_id:
            raise ValueError("compensação exige o incidente de destino")
        return self.capital_adjustment(
            amount, reason=reason or f"compensação do incidente {incident_id}",
            actor=actor, external_reference=external_reference,
            kind="risk_incident_compensation",
            purpose="RISK_INCIDENT_COMPENSATION", incident_id=incident_id,
            settlement_id=settlement_id)

    def capital_withdrawal(self, amount: Decimal, *, reason: str, actor: str,
                           external_reference: str = "") -> Transaction:
        """Retirada de capital — nunca cobre déficit."""
        if amount <= ZERO:
            raise ValueError("retirada precisa ser positiva")
        return self.capital_adjustment(-amount, reason=reason, actor=actor,
                                       external_reference=external_reference,
                                       kind="capital_withdrawal",
                                       purpose="CAPITAL_WITHDRAWAL")

    def capital_adjustment(self, amount: Decimal, *, reason: str, actor: str,
                           external_reference: str = "",
                           kind: str = "capital_adjustment",
                           purpose: str = "",
                           incident_id: str = "",
                           settlement_id: str = "") -> Transaction:
        """Aporte ou retirada de capital EXTERNO, com partida dobrada.

        Entra como CONTRIBUTED_CAPITAL, não como resultado: dinheiro que o
        dono colocou não é lucro de trader, e misturar os dois faria a
        atribuição de performance mentir na direção mais perigosa — a de
        parecer que a estratégia ganhou.

        O lançamento de abertura NUNCA é reescrito: reescrever história
        apaga a evidência de que houve um ajuste.
        """
        if amount == ZERO:
            raise ValueError("ajuste de capital não pode ser zero")
        if not reason or not actor:
            raise ValueError("ajuste de capital exige motivo e responsável")
        memo = f"{reason} | por {actor}"
        if external_reference:
            memo += f" | ref {external_reference}"
        return self._post(kind,
                          [Entry(f"CASH:{self.currency}", amount, memo),
                           Entry("CONTRIBUTED_CAPITAL", -amount, memo)],
                          external_reference or reason,
                          purpose=purpose or ("CAPITAL_CONTRIBUTION"
                                              if amount > 0
                                              else "CAPITAL_WITHDRAWAL"),
                          incident_id=incident_id,
                          settlement_id=settlement_id)

    def find_transaction(self, tx_id: str) -> dict | None:
        for tx in self.export_transactions():
            if tx["tx_id"] == tx_id:
                return tx
        return None

    def reservation_balances(self) -> dict:
        """Saldo travado POR reserva — uma leitura só, sem parsing espalhado."""
        saldos = {}
        for conta, saldo in self.balances().items():
            if conta.startswith("RESERVED:") and saldo != ZERO:
                saldos[conta.split(":", 1)[1]] = saldo
        return saldos

    def invested_cost(self) -> Decimal:
        """Custo de aquisição das posições ABERTAS.

        Este é o valor que sumia da conta: depois do fill a reserva acaba, e
        sem esta parcela o mesmo dinheiro voltava a parecer livre.
        """
        total = ZERO
        for conta, saldo in self.balances().items():
            if conta.startswith("POSITION:"):
                total += saldo
        return total

    def portfolio_equity(self) -> Decimal:
        """Patrimônio contábil: caixa + reservado + custo das posições.

        Não inclui resultado NÃO realizado — posição a mercado exigiria preço
        corrente, que o livro não conhece e não deve adivinhar.
        """
        return self.cash_available() + self.reserved_cash() + self.invested_cost()

    def position_lots(self, instrument: str) -> list[dict]:
        """Lotes vivos POR AGENTE, derivados dos lançamentos.

        O dono da posição era o agente da COMPRA MAIS RECENTE: com dois
        agentes no mesmo instrumento, o último a comprar podia fechar o lote
        do outro. Aqui cada agente tem seu lote, e a soma dos lotes é
        exatamente a posição agregada — não existe segunda verdade, tudo sai
        do mesmo histórico append-only.

        Saídas consomem FIFO dentro do lote do próprio agente.
        """
        compras: dict[str, list[dict]] = {}
        for tx in self._txs:
            if tx.instrument != instrument or tx.quantity <= ZERO:
                continue
            agente = tx.agent_id or "desconhecido"
            if tx.kind == "fill_buy":
                custo = sum((e.amount for e in tx.entries
                             if e.account.startswith("POSITION:")), ZERO)
                compras.setdefault(agente, []).append(
                    {"quantidade": tx.quantity, "custo": custo,
                     "origem": tx.ref, "em": tx.at})
            elif tx.kind == "fill_sell":
                restante = tx.quantity
                for lote in compras.get(agente, []):
                    if restante <= ZERO:
                        break
                    consome = min(lote["quantidade"], restante)
                    if lote["quantidade"] > 0:
                        lote["custo"] -= (lote["custo"] * consome
                                          / lote["quantidade"])
                    lote["quantidade"] -= consome
                    restante -= consome
        saida = []
        for agente, lotes in compras.items():
            viva = sum((lote["quantidade"] for lote in lotes), ZERO)
            custo = sum((lote["custo"] for lote in lotes), ZERO)
            saida.append({"agente": agente, "instrumento": instrument,
                          "quantidade": str(viva), "custo_restante": str(custo),
                          "origem": "DZ23"})
        return saida

    def lot_quantity(self, instrument: str, agent_id: str) -> Decimal:
        """Quanto ESTE agente ainda tem — a base para autorizar uma saída."""
        for lote in self.position_lots(instrument):
            if lote["agente"] == agent_id:
                return Decimal(lote["quantidade"])
        return ZERO

    def lot_cost(self, instrument: str, agent_id: str) -> Decimal:
        for lote in self.position_lots(instrument):
            if lote["agente"] == agent_id:
                return Decimal(lote["custo_restante"])
        return ZERO

    def position_quantity(self, instrument: str) -> Decimal:
        """Quantidade viva do instrumento, somada dos fills registrados."""
        qtd = ZERO
        for tx in self._txs:
            if tx.instrument != instrument:
                continue
            if tx.kind == "fill_buy":
                qtd += tx.quantity
            elif tx.kind == "fill_sell":
                qtd -= tx.quantity
        return qtd

    def position_owner(self, instrument: str) -> str:
        for tx in reversed(self._txs):
            if tx.instrument == instrument and tx.kind == "fill_buy":
                return tx.agent_id or ""
        return ""

    def export_transactions(self) -> list[dict]:
        """Lançamentos para persistência. Guardar só saldos destruiria a
        auditabilidade — e é o histórico que prova o resultado."""
        return [
            {"tx_id": tx.tx_id, "kind": tx.kind, "ref": tx.ref,
             # O INSTANTE é parte do histórico: sem ele, cada reinício
             # reescrevia a data e a ordem dos fatos se perdia — justamente
             # o que a auditoria precisa para saber o que veio antes.
             "at": tx.at.isoformat() if hasattr(tx.at, "isoformat") else str(tx.at),
             "incident_id": getattr(tx, "incident_id", ""),
             "settlement_id": getattr(tx, "settlement_id", ""),
             "fee_component_ids": list(getattr(tx, "fee_component_ids", ())),
             "valuation_id": getattr(tx, "valuation_id", ""),
             "valuation_fingerprint": getattr(tx, "valuation_fingerprint", ""),
             "valuation_policy_version": getattr(
                 tx, "valuation_policy_version", ""),
             "native_amount": str(getattr(tx, "native_amount", ZERO)),
             "native_currency": getattr(tx, "native_currency", ""),
             "functional_amount": str(getattr(tx, "functional_amount", ZERO)),
             "functional_currency": getattr(tx, "functional_currency", ""),
             "fee_component_records": [dict(r) for r in getattr(
                 tx, "fee_component_records", ())],
             "purpose": getattr(tx, "purpose", ""),
             "instrument": getattr(tx, "instrument", ""),
             "quantity": str(getattr(tx, "quantity", ZERO)),
             "agent_id": getattr(tx, "agent_id", ""),
             "entries": [{"account": e.account, "amount": str(e.amount),
                          "memo": e.memo} for e in tx.entries]}
            for tx in self._txs]

    @classmethod
    def from_transactions(cls, docs: list[dict], currency: str = "USDT") -> Ledger:
        """Reconstrói o livro a partir dos lançamentos persistidos.

        Sem abertura automática: o caixa vem do histórico, senão o restore
        criaria dinheiro que não existe.
        """
        led = cls(opening_cash=ZERO, currency=currency)
        for doc in docs or []:
            entries = tuple(
                Entry(e["account"], Decimal(str(e["amount"])), e.get("memo", ""))
                for e in doc.get("entries", []))
            if not entries:
                continue
            tx = Transaction(
                doc.get("tx_id") or uuid.uuid4().hex[:16], entries,
                doc.get("kind", "restored"), ref=doc.get("ref", ""),
                instrument=doc.get("instrument", ""),
                quantity=Decimal(str(doc.get("quantity") or 0)),
                agent_id=doc.get("agent_id", ""),
                purpose=doc.get("purpose", ""),
                incident_id=doc.get("incident_id", ""),
                settlement_id=doc.get("settlement_id", ""),
                fee_component_ids=doc.get("fee_component_ids") or [],
                valuation_id=doc.get("valuation_id", ""),
                valuation_fingerprint=doc.get("valuation_fingerprint", ""),
                valuation_policy_version=doc.get(
                    "valuation_policy_version", ""),
                native_amount=Decimal(str(doc.get("native_amount") or 0)),
                native_currency=doc.get("native_currency", ""),
                functional_amount=Decimal(str(doc.get("functional_amount") or 0)),
                functional_currency=doc.get("functional_currency", ""),
                fee_component_records=tuple(
                    doc.get("fee_component_records") or ()),
                # O instante ORIGINAL, não o de agora: substituir por utcnow
                # reescreveria a data de todo o histórico a cada reinício.
                **({"at": _instante(doc["at"])} if doc.get("at") else {}))
            tx.validate()               # lançamento desbalanceado não entra
            # E as DIMENSÕES: um doc que mistura unidades sem declarar a
            # conversão seria recusado por _post na entrada. Deixá-lo voltar
            # pelo restore abriria a porta dos fundos para o lançamento
            # impossível que a validação de entrada barra.
            tx.validar_dimensoes(currency)
            led._txs.append(tx)
        return led

    def recarregar_de(self, docs: list[dict], currency: str = "USDT") -> None:
        """§5: recarrega o livro NO LUGAR (mesma instância). Reidrata a partir
        dos lançamentos SEM trocar o objeto — assim TODAS as referências (o
        coordenador, o painel via desk['ledger'] e o positions_provider ligado a
        ESTE livro) enxergam a MESMA verdade financeira após a restauração.
        Antes, o restore criava um Ledger novo e as referências antigas ficavam
        obsoletas (duas verdades sobre o mesmo dinheiro)."""
        novo = Ledger.from_transactions(docs, currency=currency)
        self.currency = novo.currency
        self._txs = novo._txs

    def open_positions(self) -> set[str]:
        """Instrumentos com posição viva (saldo de custo > 0)."""
        return {acc.split(":", 1)[1] for acc, bal in self.balances().items()
                if acc.startswith("POSITION:") and bal > ZERO}

    def equity(self) -> Decimal:
        """Caixa + reservado + posições ao custo. Não estima preço de mercado."""
        b = self.balances()
        return (b.get(f"CASH:{self.currency}", ZERO) + b.get("RESERVED", ZERO)
                + sum((v for k, v in b.items() if k.startswith("POSITION:")), ZERO))


__all__ = ["Ledger", "Entry", "Transaction", "LedgerError"]
