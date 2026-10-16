"""Piloto supervisionado (princípios do Batalhão, adaptados ao DZ23).

A cadeia é obrigatória e não tem atalho:

    FORMADO na escola → SALA REAL (preparação, bloqueada) →
    LIMITES DO PROPRIETÁRIO → AGUARDANDO AUTORIZAÇÃO →
    PILOTO AUTORIZADO

O que este módulo garante, por construção:

- **A formatura nunca ativa nada.** Autorizar é ato EXPLÍCITO do
  proprietário, com trader, versão, corretora, conta, capital máximo,
  limites de perda, prazo e condições de suspensão escolhidos à mão.
- **Gates antes da ativação.** Segurança, integração, risco e execução
  precisam estar aprovados — e o gate de execução lista explicitamente
  que o envio real segue DESATIVADO (LIVE bloqueado nesta missão; o
  piloto roda em modo `paper (simulado)`).
- **Supervisão contínua.** O vigia suspende o piloto quando a escola
  retira a formatura, quando o prazo vence ou quando um limite de perda
  é atingido; o Maestro corta novas ENTRADAS de quem está bloqueado —
  nunca as saídas, porque bloquear saída prenderia posição.
- **Auditoria.** Toda transição guarda quando, para qual estado e por
  quê; revogação exige motivo. Suspenso é terminal até decisão do dono.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

MODO_DO_PILOTO = "paper (simulado)"
SCHEMA_PILOTO = "dz23.piloto/1"
SCHEMA_ENCAMINHAMENTO = "dz23.sala-real-preparacao/1"
PRAZO_MIN_DIAS = 1
PRAZO_MAX_DIAS = 365


class EstadoDoPiloto(str, Enum):
    AGUARDANDO_AUTORIZACAO = "aguardando_autorizacao"
    PILOTO_AUTORIZADO = "piloto_autorizado"
    SUSPENSO = "suspenso"
    EXPIRADO = "expirado"
    REVOGADO = "revogado"


# Encerrados: não aceitam mais transição. SUSPENSO fica fora de propósito —
# suspenso é terminal até o dono revogar.
ENCERRADOS = (EstadoDoPiloto.REVOGADO, EstadoDoPiloto.EXPIRADO)

GATES_ESPERADOS = ("segurança", "integração", "risco", "execução")


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class AutorizacaoDePiloto:
    """O que o proprietário concedeu — auditável e revogável."""
    authorization_id: str
    tenant_id: str
    agent_id: str
    estrategia: str
    versao: str
    corretora: str
    conta: str
    capital_maximo: Decimal
    perda_diaria_maxima: Decimal
    perda_total_maxima: Decimal
    prazo_dias: int
    condicoes_de_suspensao: tuple[str, ...]
    solicitado_em: str
    autorizado_em: str | None
    valido_ate: str | None
    estado: EstadoDoPiloto
    revogada_em: str | None = None
    motivo_revogacao: str = ""
    motivo_suspensao: str = ""
    modo: str = MODO_DO_PILOTO
    schema: str = SCHEMA_PILOTO

    def como_dict(self) -> dict:
        """Serialização auditável: Decimal vira string, estado vira valor —
        o mesmo contrato de `relatorio()`, para rota e painel nunca divergirem."""
        return {
            "authorization_id": self.authorization_id,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "estrategia": self.estrategia, "versao": self.versao,
            "corretora": self.corretora, "conta": self.conta,
            "capital_maximo": str(self.capital_maximo),
            "perda_diaria_maxima": str(self.perda_diaria_maxima),
            "perda_total_maxima": str(self.perda_total_maxima),
            "prazo_dias": self.prazo_dias,
            "condicoes_de_suspensao": list(self.condicoes_de_suspensao),
            "estado": self.estado.value,
            "solicitado_em": self.solicitado_em,
            "autorizado_em": self.autorizado_em,
            "valido_ate": self.valido_ate,
            "revogada_em": self.revogada_em,
            "motivo_revogacao": self.motivo_revogacao,
            "motivo_suspensao": self.motivo_suspensao,
            "modo": self.modo, "schema": self.schema,
        }


class SupervisorDePiloto:
    """Registra pedidos, aplica gates e vigia limites. NÃO opera nada.

    O supervisor nunca envia ordem nem ativa negociação: ele bloqueia ou
    libera ENTRADAS para o Maestro, com motivo explícito, e mantém o
    registro auditável de tudo.
    """

    CHAVE_ARMAZEM = "pilotos"
    MAX_EVENTOS = 200

    def __init__(self, armazem=None, tenant_id: str = "legacy", escola=None):
        self.armazem = armazem
        self.tenant_id = tenant_id
        # A escola é a ÚNICA fonte de elegibilidade: sem ela nada autoriza.
        self.escola = escola
        self._autorizacoes: dict[str, dict] = {}
        self._encaminhamentos: dict[str, dict] = {}
        self._carregar()

    # -- persistência ---------------------------------------------------
    def _carregar(self) -> None:
        if self.armazem is None:
            return
        dados = self.armazem.carregar(self.tenant_id, self.CHAVE_ARMAZEM)
        if isinstance(dados, dict):
            conteudo = dados.get("autorizacoes")
            if isinstance(conteudo, dict):
                self._autorizacoes = conteudo
            encaminhamentos = dados.get("encaminhamentos")
            if isinstance(encaminhamentos, dict):
                self._encaminhamentos = encaminhamentos

    def salvar(self) -> None:
        if self.armazem is None:
            return
        self.armazem.salvar(self.tenant_id, self.CHAVE_ARMAZEM,
                            {"autorizacoes": self._autorizacoes,
                             "encaminhamentos": self._encaminhamentos})

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _de_dict(d: dict) -> AutorizacaoDePiloto:
        return AutorizacaoDePiloto(
            authorization_id=d.get("authorization_id", ""),
            tenant_id=d.get("tenant_id", "legacy"),
            agent_id=d.get("agent_id", ""),
            estrategia=d.get("estrategia", ""),
            versao=str(d.get("versao", "1")),
            corretora=d.get("corretora", ""),
            conta=d.get("conta", ""),
            capital_maximo=Decimal(str(d.get("capital_maximo", "0"))),
            perda_diaria_maxima=Decimal(str(d.get("perda_diaria_maxima", "0"))),
            perda_total_maxima=Decimal(str(d.get("perda_total_maxima", "0"))),
            prazo_dias=int(d.get("prazo_dias", 0)),
            condicoes_de_suspensao=tuple(d.get("condicoes_de_suspensao", [])),
            solicitado_em=d.get("solicitado_em", ""),
            autorizado_em=d.get("autorizado_em"),
            valido_ate=d.get("valido_ate"),
            estado=EstadoDoPiloto(
                d.get("estado", EstadoDoPiloto.AGUARDANDO_AUTORIZACAO.value)),
            revogada_em=d.get("revogada_em"),
            motivo_revogacao=d.get("motivo_revogacao", ""),
            motivo_suspensao=d.get("motivo_suspensao", ""),
            modo=d.get("modo", MODO_DO_PILOTO),
            schema=d.get("schema", SCHEMA_PILOTO))

    def _transitar(self, agent_id: str, novo: EstadoDoPiloto, *,
                   agora: datetime, motivo: str, **campos) -> None:
        d = self._autorizacoes[agent_id]
        d["estado"] = novo.value
        d["atualizada_em"] = agora.isoformat()
        d.update(campos)
        d.setdefault("eventos", []).append({
            "quando": agora.isoformat(), "para": novo.value,
            "motivo": motivo[:400]})
        if len(d["eventos"]) > self.MAX_EVENTOS:
            d["eventos"] = d["eventos"][-self.MAX_EVENTOS:]
        self.salvar()

    # -- consulta -------------------------------------------------------
    def de(self, agent_id: str) -> AutorizacaoDePiloto | None:
        d = self._autorizacoes.get(agent_id)
        return self._de_dict(d) if d else None

    def autorizacoes(self) -> list[AutorizacaoDePiloto]:
        return [self._de_dict(d) for d in self._autorizacoes.values()]

    @staticmethod
    def _chave_encaminhamento(agent_id: str, contexto: dict) -> str:
        partes = [agent_id] + [str(contexto.get(k) or "") for k in (
            "corretora", "conta", "mercado", "modo", "ativo",
            "horizonte", "payout")]
        return "|".join(partes)

    def encaminhar_formado(self, agent_id: str, *, estrategia: str,
                           versao: str = "1", contexto: dict,
                           agora: datetime | None = None) -> dict:
        """Coloca um formando na fila da sala REAL, sem dar capital.

        A fila é uma preparação auditável: não possui limites, não aceita
        autorização direta e bloqueia novas entradas do agente até o dono
        configurar o pedido de piloto. Chamadas repetidas são idempotentes.
        """
        agora = _utc(agora or datetime.now(timezone.utc))
        agent_id = (agent_id or "").strip()
        estrategia = (estrategia or "").strip()
        if not agent_id or not estrategia:
            raise ValueError("agent_id e estrategia são obrigatórios")
        if self.escola is None:
            raise ValueError("escola ausente: elegibilidade não é verificável")
        ok, motivo = self.escola.elegivel_para_live(estrategia, versao)
        if not ok:
            raise ValueError(f"trader não elegível ao processo: {motivo}")
        contexto = dict(contexto or {})
        chave = self._chave_encaminhamento(agent_id, contexto)
        existente = self._encaminhamentos.get(chave)
        if existente:
            existente["elegibilidade_verificada_em"] = agora.isoformat()
            existente["motivo_elegibilidade"] = motivo[:400]
            self.salvar()
            return dict(existente)
        registro = {
            "encaminhamento_id": f"REAL-{agora:%Y%m%d}-{uuid.uuid4().hex[:8]}",
            "tenant_id": self.tenant_id, "agent_id": agent_id,
            "estrategia": estrategia, "versao": str(versao or "1"),
            "contexto": contexto,
            "corretora": str(contexto.get("corretora") or ""),
            "conta": str(contexto.get("conta") or ""),
            "ativo": str(contexto.get("ativo") or ""),
            "horizonte": str(contexto.get("horizonte") or "1h"),
            "estado": "aguardando_configuracao_do_proprietario",
            "criado_em": agora.isoformat(),
            "elegibilidade_verificada_em": agora.isoformat(),
            "motivo_elegibilidade": motivo[:400],
            "authorization_id": None,
            "schema": SCHEMA_ENCAMINHAMENTO,
            "aviso": ("sala REAL apenas preparada; sem limites, sem "
                      "autorização e sem capacidade de enviar ordens"),
        }
        self._encaminhamentos[chave] = registro
        self.salvar()
        return dict(registro)

    def encaminhamentos(self) -> list[dict]:
        return [dict(self._encaminhamentos[k])
                for k in sorted(self._encaminhamentos)]

    # -- cadeia obrigatória ---------------------------------------------
    def solicitar(self, agent_id: str, *, estrategia: str, versao: str = "1",
                  corretora: str, conta: str, capital_maximo: Decimal,
                  perda_diaria_maxima: Decimal, perda_total_maxima: Decimal,
                  prazo_dias: int, condicoes_de_suspensao=(),
                  contexto: dict | None = None,
                  agora: datetime | None = None) -> AutorizacaoDePiloto:
        """Registra o PEDIDO do proprietário — ainda sem autorizar nada.

        Só aceita aluno FORMADO na escola (formar-se é condição necessária,
        nunca suficiente) e recusa pedido duplicado enquanto o anterior
        não for encerrado.
        """
        agora = _utc(agora or datetime.now(timezone.utc))
        if self.escola is None:
            raise ValueError("escola ausente: elegibilidade não é verificável")
        ok, motivo = self.escola.elegivel_para_live(estrategia, versao)
        if not ok:
            raise ValueError(f"trader não elegível ao processo: {motivo}")
        atual = self._autorizacoes.get(agent_id)
        if atual and EstadoDoPiloto(atual["estado"]) not in ENCERRADOS:
            raise ValueError(
                f"{agent_id} já tem pedido em andamento "
                f"({atual['estado']}) — revogue antes de solicitar outro")
        capital_maximo = Decimal(str(capital_maximo))
        perda_diaria_maxima = Decimal(str(perda_diaria_maxima))
        perda_total_maxima = Decimal(str(perda_total_maxima))
        if capital_maximo <= 0:
            raise ValueError("capital máximo precisa ser positivo")
        if perda_diaria_maxima <= 0 or perda_total_maxima <= 0:
            raise ValueError("limites de perda precisam ser positivos")
        if perda_diaria_maxima > perda_total_maxima:
            raise ValueError("perda diária não pode exceder a perda total")
        if not (PRAZO_MIN_DIAS <= int(prazo_dias) <= PRAZO_MAX_DIAS):
            raise ValueError(
                f"prazo precisa estar entre {PRAZO_MIN_DIAS} e "
                f"{PRAZO_MAX_DIAS} dias")
        registro = {
            "authorization_id": f"AUT-{agora:%Y%m%d}-{uuid.uuid4().hex[:8]}",
            "tenant_id": self.tenant_id, "agent_id": agent_id,
            "estrategia": estrategia, "versao": str(versao or "1"),
            "corretora": corretora, "conta": conta,
            "capital_maximo": str(capital_maximo),
            "perda_diaria_maxima": str(perda_diaria_maxima),
            "perda_total_maxima": str(perda_total_maxima),
            "prazo_dias": int(prazo_dias),
            "condicoes_de_suspensao": [str(c) for c in condicoes_de_suspensao],
            # Contexto de certificação em que o pedido foi feito: sem ele o
            # vigia não consegue reavaliar a formatura — e formatura sem
            # contexto não existe. Ausente vira {}, nunca um contexto suposto.
            "contexto": dict(contexto or {}),
            "solicitado_em": agora.isoformat(), "autorizado_em": None,
            "valido_ate": None,
            "estado": EstadoDoPiloto.AGUARDANDO_AUTORIZACAO.value,
            "motivo_revogacao": "", "motivo_suspensao": "",
            "revogada_em": None, "modo": MODO_DO_PILOTO,
            "schema": SCHEMA_PILOTO, "atualizada_em": agora.isoformat(),
            "eventos": [{
                "quando": agora.isoformat(),
                "para": EstadoDoPiloto.AGUARDANDO_AUTORIZACAO.value,
                "motivo": "pedido do proprietário registrado"}],
        }
        self._autorizacoes[agent_id] = registro
        chave = self._chave_encaminhamento(agent_id, dict(contexto or {}))
        encaminhamento = self._encaminhamentos.get(chave)
        if encaminhamento:
            encaminhamento["estado"] = "configurado_aguardando_autorizacao"
            encaminhamento["authorization_id"] = registro["authorization_id"]
            encaminhamento["configurado_em"] = agora.isoformat()
        self.salvar()
        return self._de_dict(registro)

    @staticmethod
    def gates_de_ativacao(*, single_owner: bool, kill_switch: bool,
                          risco: bool, reconciliacao: bool, corretora: bool,
                          envio_real_desativado: bool) -> list[dict]:
        """Os quatro gates exigidos ANTES de ativar um piloto."""

        def _g(nome: str, ok: bool, detalhe: str) -> dict:
            return {"gate": nome, "ok": bool(ok), "detalhe": detalhe}

        seguranca_ok = bool(single_owner) and bool(kill_switch)
        integracao_ok = bool(corretora) and bool(reconciliacao)
        return [
            _g("segurança", seguranca_ok,
               "dono único e kill switch presentes" if seguranca_ok else
               f"dono único={'ok' if single_owner else 'ausente'}, kill "
               f"switch={'armado' if kill_switch else 'ausente'}"),
            _g("integração", integracao_ok,
               "corretora e reconciliação em dia" if integracao_ok else
               f"corretora={'ok' if corretora else 'ausente'}, "
               f"reconciliação={'ok' if reconciliacao else 'pendente'}"),
            _g("risco", bool(risco),
               "motor de risco ativo com limites" if risco
               else "motor de risco ausente"),
            _g("execução", bool(envio_real_desativado),
               "envio real desativado — piloto em " + MODO_DO_PILOTO
               if envio_real_desativado else
               "envio real ATIVO — proibido: LIVE bloqueado nesta missão"),
        ]

    def autorizar(self, agent_id: str, *, gates, central=None,
                  agora: datetime | None = None) -> AutorizacaoDePiloto:
        """Ato EXPLÍCITO do proprietário: pedido + todos os gates aprovados."""
        agora = _utc(agora or datetime.now(timezone.utc))
        d = self._autorizacoes.get(agent_id)
        if not d:
            raise ValueError(f"{agent_id} não tem pedido de piloto registrado")
        estado = EstadoDoPiloto(d["estado"])
        if estado is not EstadoDoPiloto.AGUARDANDO_AUTORIZACAO:
            raise ValueError(f"estado {estado.value} não aceita autorização")
        escola = self._escola_da(d, central)
        if escola is None:
            raise ValueError("escola indisponível: elegibilidade não verificável")
        elegivel, motivo = escola.elegivel_para_live(
            d.get("estrategia", ""), str(d.get("versao", "1")))
        if not elegivel:
            raise ValueError("elegibilidade retirada antes da autorização: "
                             + motivo)
        faltando = [g for g in GATES_ESPERADOS
                    if g not in {item.get("gate") for item in gates}]
        reprovados = [item for item in gates if not item.get("ok")]
        if faltando or reprovados:
            partes = ([f"gate ausente: {g}" for g in faltando] +
                      [f"{item.get('gate')}: "
                       f"{item.get('detalhe', 'reprovado')}"
                       for item in reprovados])
            raise ValueError("gates reprovados — " + "; ".join(partes))
        prazo = int(d.get("prazo_dias", 0))
        self._transitar(
            agent_id, EstadoDoPiloto.PILOTO_AUTORIZADO, agora=agora,
            motivo=(f"autorizado pelo proprietário por {prazo} dias "
                    f"(modo {MODO_DO_PILOTO})"),
            autorizado_em=agora.isoformat(),
            valido_ate=(agora + timedelta(days=prazo)).isoformat())
        return self._de_dict(self._autorizacoes[agent_id])

    def revogar(self, agent_id: str, *, motivo: str,
                agora: datetime | None = None) -> AutorizacaoDePiloto:
        """Encerra o piloto com motivo auditável. Sem motivo, não revoga."""
        agora = _utc(agora or datetime.now(timezone.utc))
        motivo = (motivo or "").strip()
        if not motivo:
            raise ValueError("revogação exige motivo (auditoria)")
        d = self._autorizacoes.get(agent_id)
        if not d:
            raise ValueError(f"{agent_id} não tem piloto registrado")
        estado = EstadoDoPiloto(d["estado"])
        if estado in ENCERRADOS:
            raise ValueError(
                f"piloto já encerrado ({estado.value}) — revogação sem efeito")
        self._transitar(
            agent_id, EstadoDoPiloto.REVOGADO, agora=agora,
            motivo=f"revogado pelo proprietário: {motivo}",
            revogada_em=agora.isoformat(), motivo_revogacao=motivo[:400])
        return self._de_dict(self._autorizacoes[agent_id])

    # -- supervisão contínua --------------------------------------------
    def _escola_da(self, d: dict, central):
        """Escola que reavalia ESTE piloto: a do contexto em que o pedido
        foi feito (via Escola Central), quando ambas conhecidas; senão a
        escola fixa do supervisor (comportamento antigo)."""
        doc = d.get("contexto")
        if central is not None and isinstance(doc, dict) and doc:
            try:
                from .salas import ContextoDeCertificacao, EscolaDoPiloto
                return EscolaDoPiloto(central,
                                      ContextoDeCertificacao.de_dict(doc))
            except Exception:
                return self.escola
        return self.escola

    def vigiar(self, *, agora: datetime | None = None,
               central=None) -> list[str]:
        """Reavalia os pilotos ativos e suspende o que deixou de valer.

        - prazo vencido ⇒ EXPIRADO;
        - escola sem formatura (revogada ou sem verificação) ⇒ SUSPENSO,
          porque operar sem elegibilidade viva é o que a escola existe
          para impedir. SUSPENSO não volta sozinho: exige revogação.

        Com `central`, cada piloto é reavaliado NO CONTEXTO em que o pedido
        foi feito — formatura na Binance não sustenta piloto na OKX.
        """
        agora = _utc(agora or datetime.now(timezone.utc))
        notas: list[str] = []
        for agent_id, d in list(self._autorizacoes.items()):
            if EstadoDoPiloto(d["estado"]) is not EstadoDoPiloto.PILOTO_AUTORIZADO:
                continue
            valido_ate = d.get("valido_ate")
            if valido_ate and agora >= _utc(datetime.fromisoformat(valido_ate)):
                self._transitar(
                    agent_id, EstadoDoPiloto.EXPIRADO, agora=agora,
                    motivo=f"prazo do piloto venceu em {valido_ate}")
                notas.append(f"{agent_id}: piloto EXPIRADO (prazo venceu)")
                continue
            escola = self._escola_da(d, central)
            if escola is None:
                self._transitar(
                    agent_id, EstadoDoPiloto.SUSPENSO, agora=agora,
                    motivo=("escola indisponível — elegibilidade não "
                            "verificável (fail-closed)"),
                    motivo_suspensao="escola indisponível (fail-closed)")
                notas.append(
                    f"{agent_id}: piloto SUSPENSO (escola indisponível)")
                continue
            ok, motivo = escola.elegivel_para_live(
                d.get("estrategia", ""), str(d.get("versao", "1")))
            if not ok:
                self._transitar(
                    agent_id, EstadoDoPiloto.SUSPENSO, agora=agora,
                    motivo=f"escola retirou a elegibilidade: {motivo}",
                    motivo_suspensao=f"escola: {motivo}"[:400])
                notas.append(f"{agent_id}: piloto SUSPENSO — {motivo}")
        return notas

    def bloqueios(self, *, capital_em_uso=None, perda_hoje=None,
                  perda_total=None,
                  agora: datetime | None = None,
                  central=None, exigir_autorizacao: bool = False,
                  agentes_esperados=()) -> dict[str, str]:
        """Quem NÃO pode abrir posição agora — e por quê, para o Maestro.

        O vigia roda antes: estado vencido/revogado/suspenso já chega aqui
        bloqueado. Dentro dos limites do piloto, o agente não aparece no
        dicionário — silêncio é permissão, e só para ENTRADAS.
        """
        agora = _utc(agora or datetime.now(timezone.utc))
        self.vigiar(agora=agora, central=central)
        uso = capital_em_uso or {}
        hoje = perda_hoje or {}
        total = perda_total or {}
        bloqueados: dict[str, str] = {}
        for agent_id, d in self._autorizacoes.items():
            estado = EstadoDoPiloto(d["estado"])
            if estado is EstadoDoPiloto.PILOTO_AUTORIZADO:
                cap_max = Decimal(str(d.get("capital_maximo", "0")))
                dia_max = Decimal(str(d.get("perda_diaria_maxima", "0")))
                total_max = Decimal(str(d.get("perda_total_maxima", "0")))
                em_uso = Decimal(str(uso.get(agent_id, 0)))
                if em_uso > cap_max:
                    bloqueados[agent_id] = (
                        f"capital em uso {em_uso} acima do máximo "
                        f"autorizado {cap_max}")
                    continue
                perdido_hoje = Decimal(str(hoje.get(agent_id, 0)))
                if perdido_hoje >= dia_max:
                    bloqueados[agent_id] = (
                        f"perda diária {perdido_hoje} atingiu o limite "
                        f"autorizado {dia_max}")
                    continue
                perdido = Decimal(str(total.get(agent_id, 0)))
                if perdido >= total_max:
                    self._transitar(
                        agent_id, EstadoDoPiloto.SUSPENSO, agora=agora,
                        motivo=(f"perda total {perdido} atingiu o limite "
                                f"autorizado {total_max}"),
                        motivo_suspensao=f"perda total {perdido} ≥ {total_max}")
                    bloqueados[agent_id] = (
                        f"perda total {perdido} atingiu o limite {total_max} "
                        "— piloto suspenso")
                    continue
            elif estado is EstadoDoPiloto.AGUARDANDO_AUTORIZACAO:
                bloqueados[agent_id] = (
                    "piloto aguardando autorização do proprietário")
            elif estado is EstadoDoPiloto.SUSPENSO:
                bloqueados[agent_id] = (
                    "piloto suspenso — "
                    + (d.get("motivo_suspensao") or "critérios violados")
                    + "; exige decisão do proprietário")
            elif estado is EstadoDoPiloto.EXPIRADO:
                bloqueados[agent_id] = "autorização expirada"
            else:  # REVOGADO
                bloqueados[agent_id] = "autorização revogada"
        for d in self._encaminhamentos.values():
            if d.get("estado") == "aguardando_configuracao_do_proprietario":
                bloqueados.setdefault(
                    str(d.get("agent_id") or ""),
                    "formado na escola; sala REAL aguardando configuração "
                    "e autorização explícita do proprietário")
        if exigir_autorizacao:
            for agent_id in agentes_esperados:
                agent_id = str(agent_id or "").strip()
                if not agent_id or agent_id in bloqueados:
                    continue
                registro = self._autorizacoes.get(agent_id)
                autorizado = bool(
                    registro and EstadoDoPiloto(registro["estado"])
                    is EstadoDoPiloto.PILOTO_AUTORIZADO)
                if not autorizado:
                    bloqueados[agent_id] = (
                        "modo LIVE exige piloto formado, configurado e "
                        "explicitamente autorizado pelo proprietário")
        return bloqueados

    # -- relatório ------------------------------------------------------
    def relatorio(self) -> list[dict]:
        """Visão auditável dos pilotos, para o painel e para o dono."""
        saida = []
        for agent_id in sorted(self._autorizacoes):
            a = self._de_dict(self._autorizacoes[agent_id])
            saida.append({
                "agent_id": a.agent_id,
                "authorization_id": a.authorization_id,
                "estrategia": a.estrategia, "versao": a.versao,
                "corretora": a.corretora, "conta": a.conta,
                "capital_maximo": str(a.capital_maximo),
                "perda_diaria_maxima": str(a.perda_diaria_maxima),
                "perda_total_maxima": str(a.perda_total_maxima),
                "prazo_dias": a.prazo_dias,
                "condicoes_de_suspensao": list(a.condicoes_de_suspensao),
                "estado": a.estado.value,
                "solicitado_em": a.solicitado_em,
                "autorizado_em": a.autorizado_em,
                "valido_ate": a.valido_ate,
                "revogada_em": a.revogada_em,
                "motivo_revogacao": a.motivo_revogacao,
                "motivo_suspensao": a.motivo_suspensao,
                "modo": a.modo,
                "eventos": self._autorizacoes[agent_id].get("eventos", [])[-10:],
                "aviso": ("piloto é simulado e não ativa operação real; "
                          "autorizar exige decisão explícita do proprietário"),
            })
        return saida


__all__ = [
    "AutorizacaoDePiloto", "EstadoDoPiloto", "SupervisorDePiloto",
    "MODO_DO_PILOTO", "SCHEMA_PILOTO", "PRAZO_MIN_DIAS", "PRAZO_MAX_DIAS",
    "SCHEMA_ENCAMINHAMENTO", "ENCERRADOS", "GATES_ESPERADOS",
]
