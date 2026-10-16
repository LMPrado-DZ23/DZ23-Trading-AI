"""Escola Central de Traders com Salas independentes por corretora (BT-6).

Arquitetura definitiva do DZ23: **uma escola, muitas salas**.

- A ESCOLA CENTRAL existe uma vez e é compartilhada pelos 10 traders
  especializados. Não há escolas duplicadas: o que muda de corretora para
  corretora é o CONTEXTO DE CERTIFICAÇÃO.
- Cada SALA é independente: corretora, conta, mercado e modo operacional
  próprios — histórico, saldo, posições, taxas, desfechos e (opcionalmente)
  livro-razão separados por construção, não por convenção. Resultado
  financeiro de uma corretora NUNCA entra na conta de outra.
- A certificação é individual por (trader, versão da estratégia, corretora,
  conta, mercado, modo operacional, ativo, horizonte e payout quando
  aplicável). O "trader" é a identidade da estratégia: a matrícula é
  `estrategia@versao`. Um trader formado na Binance não é considerado
  formado na OKX — e nem mesmo em outra conta, outro modo (paper/testnet)
  ou outro payout: a evidência de uma sala não alimenta a escola de outra.
  Contextos gravados no formato antigo (sem conta/modo) são LEGADO: nunca
  certificam um contexto novo — a formatura antiga não se estende.
- Corretoras novas entram por ADAPTADOR HOMOLOGADO (registro explícito com
  versão e mercados suportados). Sem adaptador, não há sala — nada de
  improviso. As nove corretoras CCXT do catálogo possuem sala; a IQ Option
  segue laboratório PAPER, mas pode entrar na preparação REAL bloqueada por
  usar a integração MCP oficial. Nenhum desses registros habilita LIVE.
- Classes de ativo (E6) são a camada de POLÍTICA de multimercado: moeda,
  lote, tick, sessão/feriado (fail-closed fora do pregão), liquidação e
  taxas (ver `mercados.py`). A classe é dimensão NOVA da sala: as chaves
  atuais continuam valendo, e a passagem para a chave estendida é migração
  explícita (`migrar_sala`). Classe sem execução homologada não abre sala —
  política não promete execução.
- Formar-se (em qualquer contexto) NÃO habilita negociação real. A
  autorização LIVE continua exclusiva do proprietário, pelo fluxo de
  piloto (`piloto.py`), com gates e vigia.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from .costs import custo_de_operar, retorno_liquido
from .escola import Avaliacao, EscolaDeTraders, PerfilDeMercado
from .learning import Desfecho, desfecho_de_dict, desfecho_para_dict, validar_desfecho
from .mercados import MERCADOS, MercadoError, classe_de, mercado_aberto
from .models import utcnow

if TYPE_CHECKING:                     # pragma: no cover
    from .ledger import Ledger


class SalaError(RuntimeError):
    """Sala inválida, corretora não homologada ou evidência cruzada."""


# MERCADOS vive em `mercados.py` (camada de política, E6) e é reexportado
# aqui para quem já importava deste módulo.
MODO_PAPER = "paper"
MODOS_OPERACIONAIS = ("paper", "shadow", "testnet")


# --------------------------------------------------------------- adaptadores
@dataclass(frozen=True)
class AdaptadorDeCorretora:
    """Contrato mínimo de uma corretora homologada (item: extensibilidade)."""
    nome: str
    mercados: tuple[str, ...]
    versao: str = "1"
    laboratorio: bool = False          # experimental: métricas próprias, sem LIVE
    preparacao_real: bool = False      # fila bloqueada; não concede execução

    def suporta(self, mercado: str) -> bool:
        return mercado in self.mercados

    def como_dict(self) -> dict:
        return {"nome": self.nome, "mercados": list(self.mercados),
                "versao": self.versao, "laboratorio": self.laboratorio,
                "preparacao_real": self.preparacao_real}


ADAPTADORES: dict[str, AdaptadorDeCorretora] = {}


def registrar_adaptador(adaptador: AdaptadorDeCorretora, *,
                        substituir: bool = False) -> None:
    """Homologa uma corretora. Sobrescrever exige intenção explícita."""
    nome = (adaptador.nome or "").strip().lower()
    if not nome:
        raise SalaError("adaptador sem nome de corretora")
    if not adaptador.mercados:
        raise SalaError(f"adaptador de {nome} sem mercados suportados")
    for m in adaptador.mercados:
        if m not in MERCADOS:
            raise SalaError(f"mercado '{m}' do adaptador de {nome} "
                            f"desconhecido (aceitos: {', '.join(MERCADOS)})")
    if nome in ADAPTADORES and not substituir:
        atual = ADAPTADORES[nome]
        raise SalaError(
            f"corretora '{nome}' já homologada (versão {atual.versao}); "
            "registre explicitamente outra versão com substituir=True")
    ADAPTADORES[nome] = replace(adaptador, nome=nome,
                                mercados=tuple(adaptador.mercados))


def adaptador_de(corretora: str) -> AdaptadorDeCorretora:
    ad = ADAPTADORES.get((corretora or "").strip().lower())
    if ad is None:
        raise SalaError(
            f"corretora '{corretora}' sem adaptador homologado — salas só "
            "existem para corretoras registradas; nada de improviso "
            f"(homologadas: {', '.join(sorted(ADAPTADORES)) or 'nenhuma'})")
    return ad


def corretoras_homologadas() -> list[dict]:
    return [a.como_dict() for a in sorted(ADAPTADORES.values(),
                                          key=lambda a: a.nome)]


def _homologar(nome: str, mercados: tuple[str, ...], *,
               laboratorio: bool = False,
               preparacao_real: bool = False) -> None:
    registrar_adaptador(AdaptadorDeCorretora(nome, mercados,
                                             laboratorio=laboratorio,
                                             preparacao_real=preparacao_real))


_homologar("binance", ("spot", "futuros"))
_homologar("bybit", ("spot", "futuros"))
_homologar("okx", ("spot", "futuros"))
_homologar("coinbase", ("spot",))
_homologar("gateio", ("spot",))
_homologar("kraken", ("spot",))
_homologar("kucoin", ("spot",))
_homologar("mercadobitcoin", ("spot",))
_homologar("bitget", ("spot",))
# IQ Option: laboratório independente, com mercado e métrica próprios. O MCP
# oficial permite preparação REAL bloqueada; esta camada segue sem executor LIVE.
_homologar("iq_option", ("opcoes_binarias",), laboratorio=True,
           preparacao_real=True)


# ---------------------------------------------------------------- contextos
@dataclass(frozen=True)
class ContextoDaSala:
    """Identidade de uma sala: corretora, conta, mercado, modo e (opcional)
    classe de ativo. Nunca se misturam salas com identidades diferentes."""
    corretora: str
    conta: str
    mercado: str = "spot"
    modo: str = MODO_PAPER
    payout: Decimal | None = None      # obrigatório em opções binárias
    classe: str = ""                   # dimensão E6; vazio = sala legada

    def __post_init__(self):
        object.__setattr__(self, "corretora",
                           (self.corretora or "").strip().lower())
        object.__setattr__(self, "conta", (self.conta or "").strip())
        object.__setattr__(self, "mercado",
                           (self.mercado or "").strip().lower())
        modo = getattr(self.modo, "value", self.modo)
        object.__setattr__(self, "modo", str(modo or "").strip().lower())
        object.__setattr__(self, "classe",
                           str(self.classe or "").strip().lower())
        if self.payout is not None:
            try:
                object.__setattr__(self, "payout", Decimal(str(self.payout)))
            except InvalidOperation as exc:
                raise SalaError(f"payout inválido: {self.payout!r}") from exc

    @property
    def chave(self) -> str:
        """Chave legada (4 partes) preservada; a classe, quando declarada,
        entra como quinta parte — a migração é explícita, nunca silenciosa."""
        base = f"{self.corretora}|{self.conta}|{self.mercado}|{self.modo}"
        return f"{base}|{self.classe}" if self.classe else base

    def como_dict(self) -> dict:
        return {"corretora": self.corretora, "conta": self.conta,
                "mercado": self.mercado, "modo": self.modo,
                "payout": str(self.payout) if self.payout is not None else None,
                "classe": self.classe}

    @classmethod
    def de_dict(cls, doc: dict) -> ContextoDaSala:
        return cls(str(doc.get("corretora", "")), str(doc.get("conta", "")),
                   str(doc.get("mercado", "spot")),
                   str(doc.get("modo", MODO_PAPER)), doc.get("payout"),
                   str(doc.get("classe") or ""))


@dataclass(frozen=True)
class ContextoDeCertificacao:
    """Chave da formação: corretora, conta, mercado, modo, ativo e horizonte
    (payout entra como sétima parte quando a sala declara um). Formar-se num
    contexto não forma em nenhum outro — conta e modo são dimensões de
    isolamento, não detalhes.

    Contextos gravados no formato antigo (sem `conta`/`modo`) são LEGADO:
    sua chave de 4 partes nunca colide com as chaves novas de 6-7 partes,
    então jamais certificam um contexto novo (fail-closed)."""
    corretora: str
    mercado: str
    ativo: str
    horizonte: str = "1h"
    conta: str = ""
    modo: str = ""
    payout: Decimal | None = None

    def __post_init__(self):
        object.__setattr__(self, "corretora",
                           (self.corretora or "").strip().lower())
        object.__setattr__(self, "conta", (self.conta or "").strip())
        object.__setattr__(self, "mercado",
                           (self.mercado or "").strip().lower())
        modo = getattr(self.modo, "value", self.modo)
        object.__setattr__(self, "modo", str(modo or "").strip().lower())
        object.__setattr__(self, "ativo", str(self.ativo or "").strip())
        object.__setattr__(self, "horizonte",
                           str(self.horizonte or "").strip().lower())
        if self.payout is not None:
            try:
                object.__setattr__(self, "payout", Decimal(str(self.payout)))
            except InvalidOperation as exc:
                raise SalaError(f"payout inválido: {self.payout!r}") from exc

    @property
    def chave(self) -> str:
        """6 partes (corretora|conta|mercado|modo|ativo|horizonte) + payout
        como sétima quando declarado. Nunca colide com a chave legada de 4
        partes — a evidência antiga não certifica contexto novo."""
        base = (f"{self.corretora}|{self.conta}|{self.mercado}|{self.modo}|"
                f"{self.ativo}|{self.horizonte}")
        return f"{base}|{self.payout}" if self.payout is not None else base

    def como_dict(self) -> dict:
        return {"corretora": self.corretora, "conta": self.conta,
                "mercado": self.mercado, "modo": self.modo,
                "ativo": self.ativo, "horizonte": self.horizonte,
                "payout": str(self.payout) if self.payout is not None else None}

    @classmethod
    def de_dict(cls, doc: dict) -> ContextoDeCertificacao:
        return cls(str(doc.get("corretora", "")),
                   str(doc.get("mercado", "")),
                   str(doc.get("ativo", "")),
                   str(doc.get("horizonte", "1h")),
                   str(doc.get("conta") or ""),
                   str(doc.get("modo") or ""),
                   doc.get("payout"))


# --------------------------------------------------------------------- sala
class Sala:
    """Estado financeiro e evidências de UMA corretora/conta/mercado/modo.

    Isolamento por construção: cada sala tem os próprios números; não existe
    caminho de código que some resultado de salas diferentes.
    """

    CHAVE_PREFIXO = "sala"
    MAX_DESFECHOS = 2000

    def __init__(self, contexto: ContextoDaSala, *, armazem=None,
                 tenant_id: str = "legacy", ledger: Ledger | None = None):
        self.contexto = contexto
        self.armazem = armazem
        self.tenant_id = tenant_id
        self.ledger = ledger               # livro-razão PRÓPRIO desta sala
        self.saldo_realizado = Decimal("0")
        self.taxas_acumuladas = Decimal("0")
        self.posicoes: dict[str, dict] = {}
        self._desfechos: list[Desfecho] = []
        # Priors de conhecimento recebidos de OUTRAS salas: informação
        # rotulada, jamais certificação (ver `conhecimento.py`).
        self.priors_conhecimento: list[dict] = []
        self._carregar()

    @property
    def chave_armazem(self) -> str:
        return f"{self.CHAVE_PREFIXO}::{self.contexto.chave}"

    # -- persistência ---------------------------------------------------
    def _carregar(self) -> None:
        if self.armazem is None:
            return
        dados = self.armazem.carregar(self.tenant_id, self.chave_armazem)
        if not isinstance(dados, dict):
            return
        self.saldo_realizado = Decimal(str(dados.get("saldo_realizado", "0")))
        self.taxas_acumuladas = Decimal(str(dados.get("taxas_acumuladas", "0")))
        posicoes = dados.get("posicoes")
        if isinstance(posicoes, dict):
            self.posicoes = posicoes
        for d in dados.get("desfechos", []):
            try:
                self._desfechos.append(desfecho_de_dict(d))
            except (KeyError, ValueError, TypeError):
                continue
        priors = dados.get("priors_conhecimento")
        if isinstance(priors, list):
            self.priors_conhecimento = priors
        livro = dados.get("ledger")
        if isinstance(livro, dict) and self.ledger is None:
            from .ledger import Ledger
            # Livro corrompido NÃO é ignorado em silêncio: fail-closed — um
            # razão que não fecha precisa aparecer, não virar história.
            self.ledger = Ledger.from_transactions(
                livro.get("txs", []), livro.get("currency", "USDT"))

    def salvar(self) -> None:
        if self.armazem is None:
            return
        payload = {
            "contexto": self.contexto.como_dict(),
            "saldo_realizado": str(self.saldo_realizado),
            "taxas_acumuladas": str(self.taxas_acumuladas),
            "posicoes": self.posicoes,
            "desfechos": [desfecho_para_dict(d)
                          for d in self._desfechos[-self.MAX_DESFECHOS:]],
            "priors_conhecimento": self.priors_conhecimento[-200:],
        }
        if self.ledger is not None:
            payload["ledger"] = {"currency": self.ledger.currency,
                                 "txs": self.ledger.export_transactions()}
        self.armazem.salvar(self.tenant_id, self.chave_armazem, payload)

    # -- evidência ------------------------------------------------------
    @property
    def desfechos(self) -> list[Desfecho]:
        return list(self._desfechos)

    def registrar_desfecho(self, desfecho: Desfecho) -> None:
        """Aceita evidência apenas da PRÓPRIA corretora. Resultado de outra
        sala entraria como contaminação — e é recusado na porta. A mesma
        porta recusa duplicata e data impossível: a estatística só vê
        evidência honesta."""
        corretora = (desfecho.exchange or "").strip().lower()
        if corretora and corretora != self.contexto.corretora:
            raise SalaError(
                f"desfecho da corretora '{desfecho.exchange}' não entra na "
                f"sala '{self.contexto.chave}': resultados financeiros não se "
                "misturam entre corretoras")
        if not corretora:
            desfecho = replace(desfecho, exchange=self.contexto.corretora)
        try:
            validar_desfecho(desfecho)
        except ValueError as erro:
            raise SalaError(str(erro)) from erro
        if desfecho in self._desfechos:
            raise SalaError(
                f"desfecho duplicado na sala '{self.contexto.chave}': a "
                "mesma operação não conta duas vezes")
        self._desfechos.append(desfecho)
        self.salvar()

    # -- operações ------------------------------------------------------
    def registrar_entrada(self, instrumento: str, quantidade, preco, *,
                          agente: str = "", estrategia: str = "",
                          versao: str = "1", horizonte: str = "1h",
                          regime: str = "indefinido", politica: str = "",
                          quando: datetime | None = None) -> dict:
        if not instrumento:
            raise SalaError("entrada sem instrumento")
        quantidade = Decimal(str(quantidade))
        preco = Decimal(str(preco))
        if quantidade <= 0 or preco <= 0:
            raise SalaError("quantidade e preço de entrada precisam ser "
                            "positivos")
        if instrumento in self.posicoes:
            raise SalaError(f"{instrumento} já tem posição aberta nesta sala "
                            "— uma posição por instrumento")
        posicao = {"quantidade": str(quantidade), "preco_entrada": str(preco),
                   "agente": agente, "estrategia": estrategia,
                   "versao": str(versao), "horizonte": horizonte,
                   "regime": regime, "politica": politica,
                   "aberta_em": (quando or utcnow()).isoformat()}
        self.posicoes[instrumento] = posicao
        self.salvar()
        return dict(posicao)

    def registrar_reducao(self, instrumento: str, quantidade, preco, *,
                          quando: datetime | None = None) -> dict:
        """Saída PARCIAL: realiza o resultado só da fatia vendida e mantém o
        resto da posição viva. Sem esta rota, uma redução parcial ou fecharia
        a posição inteira (mentira no diário) ou sumiria com o resultado."""
        posicao = self.posicoes.get(instrumento)
        if posicao is None:
            raise SalaError(f"{instrumento} não tem posição aberta nesta sala")
        preco = Decimal(str(preco))
        quantidade = Decimal(str(quantidade))
        total = Decimal(posicao["quantidade"])
        entrada = Decimal(posicao["preco_entrada"])
        if preco <= 0 or quantidade <= 0:
            raise SalaError("quantidade e preço da redução precisam ser "
                            "positivos")
        if quantidade > total:
            raise SalaError(f"redução de {instrumento} maior que a posição "
                            f"({quantidade} > {total})")
        valor_ordem = quantidade * entrada
        custo = custo_de_operar(instrumento, self.contexto.corretora,
                                valor_ordem)
        bruto = (preco - entrada) / entrada
        liquido = retorno_liquido(bruto, custo)
        resultado = valor_ordem * liquido
        self.saldo_realizado += resultado
        self.taxas_acumuladas += valor_ordem * custo.ida_e_volta
        restante = total - quantidade
        if restante <= 0:
            return self.registrar_saida(instrumento, preco, quando=quando)
        posicao["quantidade"] = str(restante)
        self.salvar()
        return {"instrumento": instrumento, "quantidade": str(quantidade),
                "quantidade_restante": str(restante),
                "preco_entrada": str(entrada), "preco_saida": str(preco),
                "retorno_liquido": liquido, "resultado": resultado,
                "saldo_realizado": self.saldo_realizado,
                "custo": custo.detalhe()}

    def registrar_saida(self, instrumento: str, preco, *,
                        quando: datetime | None = None) -> dict:
        """Fecha a posição: custo real descontado, resultado no saldo DESTA
        sala e desfecho registrado para a escola DESTE contexto."""
        posicao = self.posicoes.pop(instrumento, None)
        if posicao is None:
            raise SalaError(f"{instrumento} não tem posição aberta nesta sala")
        preco = Decimal(str(preco))
        quantidade = Decimal(posicao["quantidade"])
        entrada = Decimal(posicao["preco_entrada"])
        if preco <= 0:
            self.posicoes[instrumento] = posicao   # devolve: nada foi fechado
            raise SalaError("preço de saída precisa ser positivo")
        valor_ordem = quantidade * entrada
        custo = custo_de_operar(instrumento, self.contexto.corretora,
                                valor_ordem)
        bruto = (preco - entrada) / entrada
        liquido = retorno_liquido(bruto, custo)
        resultado = valor_ordem * liquido
        aberta_em = (datetime.fromisoformat(posicao["aberta_em"])
                     if posicao.get("aberta_em") else None)
        desfecho = Desfecho(
            estrategia=posicao.get("estrategia") or posicao.get("agente", ""),
            regime=posicao.get("regime", "indefinido"),
            instrumento=instrumento, retorno=liquido,
            fechado_em=quando or utcnow(), forca_do_sinal=0.0,
            exchange=self.contexto.corretora,
            versao=str(posicao.get("versao", "1")),
            agente=posicao.get("agente", ""),
            horizonte=posicao.get("horizonte", "1h"),
            aberta_em=aberta_em)
        # A porta da evidência valida ANTES de mexer no saldo: desfecho
        # impossível devolve a posição e nada foi fechado (nem contado).
        try:
            validar_desfecho(desfecho)
        except ValueError as erro:
            self.posicoes[instrumento] = posicao
            raise SalaError(str(erro)) from erro
        self.saldo_realizado += resultado
        self.taxas_acumuladas += valor_ordem * custo.ida_e_volta
        self._desfechos.append(desfecho)
        self.salvar()
        return {"instrumento": instrumento, "quantidade": str(quantidade),
                "preco_entrada": str(entrada), "preco_saida": str(preco),
                "retorno_liquido": liquido, "resultado": resultado,
                "saldo_realizado": self.saldo_realizado,
                "custo": custo.detalhe()}

    # -- janela de operação ---------------------------------------------
    def pode_operar(self, agora: datetime | None = None) -> tuple[bool, str]:
        """Janela de negociação da sala, segundo a classe declarada.

        Sala sem classe (legado) mantém o comportamento do mercado da
        corretora — cripto 24/7 não muda. Com classe, fora do pregão é
        fail-closed; classe desconhecida também fecha.
        """
        if not self.contexto.classe:
            return True, ("sala sem classe declarada — janela do mercado da "
                          "corretora (cripto 24/7)")
        return mercado_aberto(self.contexto.classe, agora or utcnow())

    # -- retrato --------------------------------------------------------
    def resumo(self) -> dict:
        ad = ADAPTADORES.get(self.contexto.corretora)
        return {
            "chave": self.contexto.chave,
            "classe": self.contexto.classe or None,
            "contexto": self.contexto.como_dict(),
            "saldo_realizado": str(self.saldo_realizado),
            "taxas_acumuladas": str(self.taxas_acumuladas),
            "posicoes_abertas": {
                i: {"quantidade": p["quantidade"],
                    "preco_entrada": p["preco_entrada"],
                    "agente": p.get("agente", "")}
                for i, p in self.posicoes.items()},
            "desfechos": len(self._desfechos),
            "priors_conhecimento": len(self.priors_conhecimento),
            "tem_ledger": self.ledger is not None,
            "laboratorio": bool(ad.laboratorio) if ad else None,
        }


# -------------------------------------------------------------- registro
class RegistroDeSalas:
    """Catálogo de salas do tenant. Abrir é idempotente; identidades são
    validadas contra o adaptador homologado da corretora."""

    CHAVE_CATALOGO = "salas"

    def __init__(self, armazem=None, tenant_id: str = "legacy"):
        self.armazem = armazem
        self.tenant_id = tenant_id
        self._salas: dict[str, Sala] = {}
        self._contextos: dict[str, dict] = {}
        self._carregar_catalogo()

    def _carregar_catalogo(self) -> None:
        if self.armazem is None:
            return
        dados = self.armazem.carregar(self.tenant_id, self.CHAVE_CATALOGO)
        if isinstance(dados, dict) and isinstance(dados.get("contextos"), dict):
            self._contextos = dados["contextos"]

    def _salvar_catalogo(self) -> None:
        if self.armazem is None:
            return
        self.armazem.salvar(self.tenant_id, self.CHAVE_CATALOGO,
                            {"contextos": self._contextos})

    # -- validação ------------------------------------------------------
    @staticmethod
    def _validar(contexto: ContextoDaSala, ad: AdaptadorDeCorretora) -> None:
        if not contexto.corretora:
            raise SalaError("sala sem corretora")
        if not contexto.conta:
            raise SalaError("sala exige conta explícita — 'a conta padrão' "
                            "não existe quando o assunto é dinheiro")
        if contexto.mercado not in MERCADOS:
            raise SalaError(f"mercado '{contexto.mercado}' desconhecido "
                            f"(aceitos: {', '.join(MERCADOS)})")
        if not ad.suporta(contexto.mercado):
            raise SalaError(
                f"corretora {ad.nome} não opera '{contexto.mercado}' "
                f"(mercados do adaptador: {', '.join(ad.mercados)})")
        if contexto.modo not in MODOS_OPERACIONAIS:
            if contexto.modo == "live":
                raise SalaError(
                    "sala LIVE não é criada por esta camada: operação real "
                    "exige autorização explícita do proprietário pelo fluxo "
                    "de piloto (FORMADO → ELEGÍVEL → AGUARDANDO → PILOTO "
                    "AUTORIZADO)")
            raise SalaError(f"modo '{contexto.modo}' inválido "
                            f"(aceitos: {', '.join(MODOS_OPERACIONAIS)})")
        if ad.laboratorio and contexto.modo != MODO_PAPER:
            raise SalaError(f"{ad.nome} é laboratório experimental: opera "
                            f"somente em modo '{MODO_PAPER}'")
        if contexto.mercado == "opcoes_binarias" and contexto.payout is None:
            raise SalaError("opções binárias exigem payout declarado — sem "
                            "ele a régua da escola (taxa de acerto vs "
                            "1/(1+payout)) ficaria indefinida")
        if contexto.mercado != "opcoes_binarias" and contexto.payout is not None:
            raise SalaError(f"payout só faz sentido em opções binárias "
                            f"(mercado atual: {contexto.mercado})")
        if contexto.payout is not None and contexto.payout <= 0:
            raise SalaError("payout precisa ser positivo")
        if contexto.classe:
            try:
                cl = classe_de(contexto.classe)
            except MercadoError as exc:
                raise SalaError(str(exc)) from exc
            if cl.mercado != contexto.mercado:
                raise SalaError(
                    f"classe '{cl.nome}' é do mercado '{cl.mercado}', não "
                    f"bate com o mercado declarado '{contexto.mercado}'")
            if not cl.execucao_homologada:
                raise SalaError(
                    f"classe '{cl.nome}' não tem execução homologada — "
                    "registro de política não habilita operar; homologar é "
                    "decisão explícita do proprietário")

    # -- API ------------------------------------------------------------
    def abrir_sala(self, contexto: ContextoDaSala, *,
                   ledger=None) -> Sala:
        # Chave legada de sala já migrada redireciona para a sala viva —
        # uma posição financeira tem UM dono, nunca dois registros.
        doc = self._contextos.get(contexto.chave)
        if isinstance(doc, dict) and doc.get("migrada_para"):
            alvo = self._contextos.get(str(doc["migrada_para"]))
            if isinstance(alvo, dict):
                return self.abrir_sala(ContextoDaSala.de_dict(alvo),
                                       ledger=ledger)
        ad = adaptador_de(contexto.corretora)
        self._validar(contexto, ad)
        chave = contexto.chave
        sala = self._salas.get(chave)
        if sala is None:
            sala = Sala(contexto, armazem=self.armazem,
                        tenant_id=self.tenant_id, ledger=ledger)
            self._salas[chave] = sala
            self._contextos[chave] = contexto.como_dict()
            self._salvar_catalogo()
        elif ledger is not None and sala.ledger is None:
            sala.ledger = ledger
            sala.salvar()
        return sala

    def migrar_sala(self, origem: ContextoDaSala, classe: str, *,
                    ledger=None) -> Sala:
        """Migração EXPLÍCITA: sala legada (sem classe) → sala com classe.

        Copia a prova viva (saldo, taxas, desfechos, priors e livro-razão)
        para a chave nova e marca a antiga como migrada: a chave legada
        continua resolvendo (redireciona) para a MESMA sala — sem estado
        duplicado nem resultado em duas contas. Exige posições fechadas:
        uma posição viva tem um dono só. Rodar duas vezes é recusado.
        """
        if origem.classe:
            raise SalaError("origem já declara classe — a migração parte de "
                            "uma sala legada sem classe")
        ad = adaptador_de(origem.corretora)
        self._validar(origem, ad)
        doc_legado = self._contextos.get(origem.chave)
        if not isinstance(doc_legado, dict):
            raise SalaError(f"sala de origem '{origem.chave}' não existe no "
                            "catálogo — migração não cria história")
        if doc_legado.get("migrada_para"):
            raise SalaError(
                f"sala '{origem.chave}' já foi migrada para "
                f"'{doc_legado['migrada_para']}' — migração é um evento, "
                "não um hábito")
        try:
            cl = classe_de(classe)
        except MercadoError as exc:
            raise SalaError(str(exc)) from exc
        if cl.mercado != origem.mercado:
            raise SalaError(
                f"classe '{cl.nome}' é do mercado '{cl.mercado}', não bate "
                f"com o mercado da origem '{origem.mercado}'")
        if not cl.execucao_homologada:
            raise SalaError(
                f"classe '{cl.nome}' não tem execução homologada — migrar "
                "para classe sem homologação não habilita nada e sujaria o "
                "histórico")
        destino = replace(origem, classe=cl.nome)
        if destino.chave in self._contextos:
            raise SalaError(f"sala '{destino.chave}' já existe no catálogo")
        src = self.abrir_sala(origem, ledger=ledger)
        if src.posicoes:
            raise SalaError(
                "migração exige posições fechadas — uma posição viva tem "
                "um dono só; feche as posições e migre depois")
        nova = Sala(destino, armazem=self.armazem,
                    tenant_id=self.tenant_id)
        nova.saldo_realizado = src.saldo_realizado
        nova.taxas_acumuladas = src.taxas_acumuladas
        nova.posicoes = dict(src.posicoes)
        nova._desfechos = src.desfechos
        nova.priors_conhecimento = [dict(p) for p in src.priors_conhecimento]
        if src.ledger is not None:
            from .ledger import Ledger
            nova.ledger = Ledger.from_transactions(
                src.ledger.export_transactions(), src.ledger.currency)
        nova.salvar()
        doc_legado["migrada_para"] = destino.chave
        self._salas.pop(origem.chave, None)
        self._salas[destino.chave] = nova
        self._contextos[destino.chave] = destino.como_dict()
        self._salvar_catalogo()
        return nova

    @property
    def salas(self) -> dict[str, Sala]:
        for chave, doc in list(self._contextos.items()):
            if isinstance(doc, dict) and doc.get("migrada_para"):
                continue      # a sala viva está na chave nova (migração)
            if chave not in self._salas:
                self.abrir_sala(ContextoDaSala.de_dict(doc))
        return {chave: sala for chave, sala in self._salas.items()
                if not (isinstance(self._contextos.get(chave), dict)
                        and self._contextos[chave].get("migrada_para"))}

    def relatorio(self) -> list[dict]:
        return [sala.resumo() for _, sala in sorted(self.salas.items())]


# ---------------------------------------------------------- escola central
class EscolaCentral:
    """UMA escola compartilhada, com uma instância por CONTEXTO de
    certificação. Evidência de uma corretora/ativo/horizonte nunca alimenta
    a formação de outro contexto."""

    CHAVE_CATALOGO = "escola_central"
    CHAVE_PREFIXO = "escola"

    def __init__(self, armazem=None, tenant_id: str = "legacy"):
        self.armazem = armazem
        self.tenant_id = tenant_id
        self._escolas: dict[str, EscolaDeTraders] = {}
        self._catalogo: dict[str, dict] = {}
        self._carregar_catalogo()

    def _carregar_catalogo(self) -> None:
        if self.armazem is None:
            return
        dados = self.armazem.carregar(self.tenant_id, self.CHAVE_CATALOGO)
        if isinstance(dados, dict) and isinstance(dados.get("contextos"), dict):
            self._catalogo = dados["contextos"]

    def _salvar_catalogo(self) -> None:
        if self.armazem is None:
            return
        self.armazem.salvar(self.tenant_id, self.CHAVE_CATALOGO,
                            {"contextos": self._catalogo})

    def _escola_de_chave(self, chave: str, *,
                         doc: dict | None = None) -> EscolaDeTraders:
        """Instância da escola para UMA chave de contexto. Quando `doc` é
        informado e a chave ainda não está no catálogo, ela é registrada —
        nunca o contrário: consultar não inventa contexto novo."""
        escola = self._escolas.get(chave)
        if escola is None:
            escola = EscolaDeTraders(
                self.armazem, self.tenant_id,
                chave_armazem=f"{self.CHAVE_PREFIXO}::{chave}")
            self._escolas[chave] = escola
            if doc is not None and chave not in self._catalogo:
                self._catalogo[chave] = dict(doc)
                self._salvar_catalogo()
        return escola

    # -- avaliação ------------------------------------------------------
    def avaliar(self, *, sala: Sala, estrategia: str, versao: str = "1",
                ativo: str, horizonte: str = "1h",
                agora: datetime | None = None) -> Avaliacao:
        """Avalia um aluno usando SOMENTE a evidência da sala informada.
        O contexto carrega TUDO o que identifica a sala — corretora, conta,
        mercado, modo e payout — para que evidência de uma conta/modo nunca
        alimente a formação de outra."""
        contexto = ContextoDeCertificacao(
            corretora=sala.contexto.corretora, conta=sala.contexto.conta,
            mercado=sala.contexto.mercado, modo=sala.contexto.modo,
            ativo=ativo, horizonte=horizonte,
            payout=sala.contexto.payout)
        escola = self._escola_de_chave(contexto.chave, doc={
            **contexto.como_dict(), "criada_em": utcnow().isoformat()})
        prova = [d for d in sala.desfechos
                 if d.estrategia == estrategia
                 and (d.versao or "1") == str(versao)
                 and d.instrumento == ativo
                 and (d.horizonte or "1h") == horizonte]
        perfil = PerfilDeMercado(nome=sala.contexto.mercado,
                                 payout=sala.contexto.payout)
        av = escola.avaliar(estrategia=estrategia, versao=str(versao),
                            desfechos=prova, perfil=perfil, agora=agora)
        doc = self._catalogo.get(contexto.chave)
        if isinstance(doc, dict):
            doc["ultima_avaliacao"] = {
                "estrategia": estrategia, "versao": str(versao),
                "estado": av.estado.value, "motivo": av.motivo,
                "atualizada_em": av.atualizada_em}
            self._salvar_catalogo()
        return av

    def elegivel_para_live(self, estrategia: str, versao: str = "1", *,
                           contexto: ContextoDeCertificacao) -> tuple[bool, str]:
        """FORMADO neste contexto ⇒ elegível ao PROCESSO de autorização.
        Nunca ativa nada. Laboratório só entra quando o adaptador declara
        preparação REAL; isso não concede executor LIVE."""
        ad = ADAPTADORES.get((contexto.corretora or "").strip().lower())
        if ad is not None and ad.laboratorio and not ad.preparacao_real:
            return False, (f"{ad.nome} é laboratório experimental: métricas "
                           "próprias e sem integração de preparação REAL")
        chave = contexto.chave
        if chave not in self._escolas and chave not in self._catalogo:
            return False, ("nenhuma evidência neste contexto de certificação "
                           f"({chave}) — a formatura não se transfere entre "
                           "corretoras, contas, modos, payouts, mercados, "
                           "ativos ou horizontes")
        escola = self._escola_de_chave(chave)
        return escola.elegivel_para_live(estrategia, versao)

    def estado_de(self, estrategia: str, versao: str = "1", *,
                  contexto: ContextoDeCertificacao) -> str:
        """Estado da matrícula NO contexto — 'coletando' quando nunca houve
        evidência ali (não finge formação que não existe)."""
        chave = contexto.chave
        if chave not in self._escolas and chave not in self._catalogo:
            return "coletando"
        estado = self._escola_de_chave(chave).estado_de(estrategia, versao)
        return estado.value if estado is not None else "coletando"

    # -- retratos -------------------------------------------------------
    def relatorio(self) -> dict:
        certificados = []
        por_estado: dict[str, int] = {}
        for chave in sorted(self._catalogo):
            doc = self._catalogo[chave]
            legado = (not isinstance(doc, dict) or "conta" not in doc
                      or "modo" not in doc)
            escola = self._escola_de_chave(chave)
            for linha in escola.relatorio():
                por_estado[linha["estado"]] = por_estado.get(linha["estado"], 0) + 1
                certificados.append({**linha, "contexto": chave,
                                     "legado": legado})
        return {
            "escola_central": ("uma escola, uma instância por contexto "
                               "(corretora|conta|mercado|modo|ativo|"
                               "horizonte[|payout])"),
            "total_contextos": len(self._catalogo),
            "por_estado": por_estado,
            "certificados": certificados,
            "aviso": ("formação em um contexto não vale em outro; contexto "
                      "legado (sem conta/modo) não certifica sala nova; quem "
                      "autoriza operação real é o proprietário — a escola "
                      "nunca ativa nada"),
        }


class EscolaDoPiloto:
    """Amarra a Escola Central a UM contexto de certificação para o fluxo
    de piloto. O supervisor consulta elegibilidade SEMPRE naquele contexto
    — nunca na escola "em geral", que não existe."""

    def __init__(self, central: EscolaCentral,
                 contexto: ContextoDeCertificacao):
        self._central = central
        self._contexto = contexto

    def elegivel_para_live(self, estrategia: str,
                           versao: str = "1") -> tuple[bool, str]:
        return self._central.elegivel_para_live(
            estrategia, versao, contexto=self._contexto)


__all__ = [
    "AdaptadorDeCorretora", "ContextoDaSala", "ContextoDeCertificacao",
    "EscolaCentral", "EscolaDoPiloto", "RegistroDeSalas", "Sala",
    "SalaError", "adaptador_de", "corretoras_homologadas",
    "registrar_adaptador", "MERCADOS", "MODO_PAPER", "MODOS_OPERACIONAIS",
]
