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
  mercado, ativo, horizonte operacional). Um trader formado na Binance não
  é considerado formado na OKX: a evidência de uma sala não alimenta a
  escola de outra.
- Corretoras novas entram por ADAPTADOR HOMOLOGADO (registro explícito com
  versão e mercados suportados). Sem adaptador, não há sala — nada de
  improviso. IQ Option é registrada como laboratório experimental: métricas
  próprias, modo paper e jamais elegível a operação real.
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
from .learning import Desfecho, desfecho_de_dict, desfecho_para_dict
from .models import utcnow

if TYPE_CHECKING:                     # pragma: no cover
    from .ledger import Ledger


class SalaError(RuntimeError):
    """Sala inválida, corretora não homologada ou evidência cruzada."""


MERCADOS = ("spot", "futuros", "opcoes_binarias")
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

    def suporta(self, mercado: str) -> bool:
        return mercado in self.mercados

    def como_dict(self) -> dict:
        return {"nome": self.nome, "mercados": list(self.mercados),
                "versao": self.versao, "laboratorio": self.laboratorio}


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
               laboratorio: bool = False) -> None:
    registrar_adaptador(AdaptadorDeCorretora(nome, mercados,
                                             laboratorio=laboratorio))


_homologar("binance", ("spot", "futuros"))
_homologar("bybit", ("spot", "futuros"))
_homologar("okx", ("spot", "futuros"))
# IQ Option: laboratório experimental independente — mercado e métrica
# próprios (taxa de acerto vs payout estressado), jamais elegível a LIVE.
_homologar("iq_option", ("opcoes_binarias",), laboratorio=True)


# ---------------------------------------------------------------- contextos
@dataclass(frozen=True)
class ContextoDaSala:
    """Identidade de uma sala: corretora, conta, mercado e modo. Nunca se
    misturam salas com identidades diferentes."""
    corretora: str
    conta: str
    mercado: str = "spot"
    modo: str = MODO_PAPER
    payout: Decimal | None = None      # obrigatório em opções binárias

    def __post_init__(self):
        object.__setattr__(self, "corretora",
                           (self.corretora or "").strip().lower())
        object.__setattr__(self, "conta", (self.conta or "").strip())
        object.__setattr__(self, "mercado",
                           (self.mercado or "").strip().lower())
        modo = getattr(self.modo, "value", self.modo)
        object.__setattr__(self, "modo", str(modo or "").strip().lower())
        if self.payout is not None:
            try:
                object.__setattr__(self, "payout", Decimal(str(self.payout)))
            except InvalidOperation as exc:
                raise SalaError(f"payout inválido: {self.payout!r}") from exc

    @property
    def chave(self) -> str:
        return f"{self.corretora}|{self.conta}|{self.mercado}|{self.modo}"

    def como_dict(self) -> dict:
        return {"corretora": self.corretora, "conta": self.conta,
                "mercado": self.mercado, "modo": self.modo,
                "payout": str(self.payout) if self.payout is not None else None}

    @classmethod
    def de_dict(cls, doc: dict) -> ContextoDaSala:
        return cls(str(doc.get("corretora", "")), str(doc.get("conta", "")),
                   str(doc.get("mercado", "spot")),
                   str(doc.get("modo", MODO_PAPER)), doc.get("payout"))


@dataclass(frozen=True)
class ContextoDeCertificacao:
    """Chave da formação: corretora, mercado, ativo e horizonte. Formar-se
    num contexto não forma em nenhum outro."""
    corretora: str
    mercado: str
    ativo: str
    horizonte: str = "1h"

    @property
    def chave(self) -> str:
        return f"{self.corretora}|{self.mercado}|{self.ativo}|{self.horizonte}"

    def como_dict(self) -> dict:
        return {"corretora": self.corretora, "mercado": self.mercado,
                "ativo": self.ativo, "horizonte": self.horizonte}

    @classmethod
    def de_dict(cls, doc: dict) -> ContextoDeCertificacao:
        return cls(str(doc.get("corretora", "")), str(doc.get("mercado", "")),
                   str(doc.get("ativo", "")), str(doc.get("horizonte", "1h")))


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
        sala entraria como contaminação — e é recusado na porta."""
        corretora = (desfecho.exchange or "").strip().lower()
        if corretora and corretora != self.contexto.corretora:
            raise SalaError(
                f"desfecho da corretora '{desfecho.exchange}' não entra na "
                f"sala '{self.contexto.chave}': resultados financeiros não se "
                "misturam entre corretoras")
        if not corretora:
            desfecho = replace(desfecho, exchange=self.contexto.corretora)
        self._desfechos.append(desfecho)
        self.salvar()

    # -- operações ------------------------------------------------------
    def registrar_entrada(self, instrumento: str, quantidade, preco, *,
                          agente: str = "", estrategia: str = "",
                          versao: str = "1", horizonte: str = "1h",
                          regime: str = "indefinido",
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
                   "regime": regime,
                   "aberta_em": (quando or utcnow()).isoformat()}
        self.posicoes[instrumento] = posicao
        self.salvar()
        return dict(posicao)

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
        self.saldo_realizado += resultado
        self.taxas_acumuladas += valor_ordem * custo.ida_e_volta
        desfecho = Desfecho(
            estrategia=posicao.get("estrategia") or posicao.get("agente", ""),
            regime=posicao.get("regime", "indefinido"),
            instrumento=instrumento, retorno=liquido,
            fechado_em=quando or utcnow(), forca_do_sinal=0.0,
            exchange=self.contexto.corretora,
            versao=str(posicao.get("versao", "1")),
            agente=posicao.get("agente", ""),
            horizonte=posicao.get("horizonte", "1h"))
        self._desfechos.append(desfecho)
        self.salvar()
        return {"instrumento": instrumento, "quantidade": str(quantidade),
                "preco_entrada": str(entrada), "preco_saida": str(preco),
                "retorno_liquido": liquido, "resultado": resultado,
                "saldo_realizado": self.saldo_realizado,
                "custo": custo.detalhe()}

    # -- retrato --------------------------------------------------------
    def resumo(self) -> dict:
        ad = ADAPTADORES.get(self.contexto.corretora)
        return {
            "chave": self.contexto.chave,
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

    # -- API ------------------------------------------------------------
    def abrir_sala(self, contexto: ContextoDaSala, *,
                   ledger=None) -> Sala:
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

    @property
    def salas(self) -> dict[str, Sala]:
        for chave, doc in list(self._contextos.items()):
            if chave not in self._salas:
                self.abrir_sala(ContextoDaSala.de_dict(doc))
        return dict(self._salas)

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

    def _escola(self, contexto: ContextoDeCertificacao) -> EscolaDeTraders:
        chave = contexto.chave
        escola = self._escolas.get(chave)
        if escola is None:
            escola = EscolaDeTraders(
                self.armazem, self.tenant_id,
                chave_armazem=f"{self.CHAVE_PREFIXO}::{chave}")
            self._escolas[chave] = escola
            if chave not in self._catalogo:
                self._catalogo[chave] = {**contexto.como_dict(),
                                         "criada_em": utcnow().isoformat()}
                self._salvar_catalogo()
        return escola

    # -- avaliação ------------------------------------------------------
    def avaliar(self, *, sala: Sala, estrategia: str, versao: str = "1",
                ativo: str, horizonte: str = "1h",
                agora: datetime | None = None) -> Avaliacao:
        """Avalia um aluno usando SOMENTE a evidência da sala informada."""
        contexto = ContextoDeCertificacao(sala.contexto.corretora,
                                          sala.contexto.mercado, ativo,
                                          horizonte)
        escola = self._escola(contexto)
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
        Nunca ativa nada — e laboratórios jamais são elegíveis."""
        ad = ADAPTADORES.get((contexto.corretora or "").strip().lower())
        if ad is not None and ad.laboratorio:
            return False, (f"{ad.nome} é laboratório experimental: métricas "
                           "próprias e jamais elegível a operação real")
        chave = contexto.chave
        if chave not in self._escolas and chave not in self._catalogo:
            return False, ("nenhuma evidência neste contexto de certificação "
                           f"({chave}) — a formatura não se transfere entre "
                           "corretoras, mercados, ativos ou horizontes")
        escola = self._escola(contexto)
        return escola.elegivel_para_live(estrategia, versao)

    def estado_de(self, estrategia: str, versao: str = "1", *,
                  contexto: ContextoDeCertificacao) -> str:
        """Estado da matrícula NO contexto — 'coletando' quando nunca houve
        evidência ali (não finge formação que não existe)."""
        chave = contexto.chave
        if chave not in self._escolas and chave not in self._catalogo:
            return "coletando"
        estado = self._escola(contexto).estado_de(estrategia, versao)
        return estado.value if estado is not None else "coletando"

    # -- retratos -------------------------------------------------------
    def relatorio(self) -> dict:
        certificados = []
        por_estado: dict[str, int] = {}
        for chave in sorted(self._catalogo):
            escola = self._escola(ContextoDeCertificacao.de_dict(
                self._catalogo[chave]))
            for linha in escola.relatorio():
                por_estado[linha["estado"]] = por_estado.get(linha["estado"], 0) + 1
                certificados.append({**linha, "contexto": chave})
        return {
            "escola_central": ("uma escola, uma instância por contexto "
                               "(corretora|mercado|ativo|horizonte)"),
            "total_contextos": len(self._catalogo),
            "por_estado": por_estado,
            "certificados": certificados,
            "aviso": ("formação em um contexto não vale em outro; quem "
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
