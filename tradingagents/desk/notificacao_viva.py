"""Centro Inteligente de Alertas (E2/M5) — fila VIVA e durável.

O problema que este módulo resolve: alerta em memória morre no reinício,
alerta repetido vira ruído, alerta vencido vira armadilha (parece
oportunidade atual), e alerta sem auditoria não se sabe se chegou.

Invariantes codificadas:
- A fila é persistida (Armazem, chave ``alertas``) e sobrevive a reinício;
  o que estava pendente volta a ser tentado.
- DEDUPE por chave: o mesmo problema não vira 200 mensagens; a reincidência
  incrementa ``ocorrencias`` em vez de criar alerta novo.
- Retry com BACKOFF exponencial e teto; depois de ``max_tentativas`` o
  alerta fica visível como esgotado — nunca some em silêncio.
- RATE LIMIT por canal: rajada não derruba a reputação do canal.
- EXPIRAÇÃO explícita: alerta com ``validade`` vencida é marcado VENCIDO e
  não é despachado como se fosse atual; a entrega (se forçada) carrega a
  marca. Vencido nunca se passa por oportunidade atual.
- Toda tentativa de entrega é auditada (canal, resultado, erro, horário).
- Este módulo NÃO conhece execução: não importa coordinator, broker ou
  ordem. O teste estrutural garante isso (botão de alerta jamais opera).
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

SCHEMA = "dz23.alertas/1"

ESTADOS = ("novo", "entregue", "expirado", "falhou")

# Prioridade de canais (M5): Telegram > Windows > painel; demais opcionais.
PRIORIDADE_CANAIS = ("telegram", "windows", "discord", "whatsapp",
                     "webpush", "painel")

RATE_LIMITE_PADRAO = {"telegram": 20, "windows": 30, "discord": 20,
                      "whatsapp": 20, "webpush": 30, "painel": 1000}
JANELA_RATE_S = 60.0

JANELA_DEDUPE_S = 3600.0
BACKOFF_BASE_S = 30.0
BACKOFF_FATOR = 2.0
BACKOFF_TETO_S = 1800.0
MAX_TENTATIVAS = 8


def agora_iso(agora: datetime | None = None) -> str:
    return (agora or datetime.now(timezone.utc)).isoformat()


def _parse(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@dataclass
class AlertaVivo:
    tipo: str
    severidade: str              # urgente | atencao | informativo
    titulo: str
    detalhe: str
    acao: str
    criado_em: str = ""
    fonte: str = ""              # de onde veio o dado (feed/corretora/relógio)
    preco: str = ""              # preço no momento, com contexto no dado
    link: str = ""               # rota do painel para detalhes
    validade: str = ""           # ISO; vazio = sem prazo
    dedupe_key: str = ""
    dados: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    estado: str = "novo"
    ocorrencias: int = 1
    entregue_em: str = ""
    entregue_canal: str = ""
    lido_em: str = ""
    esgotado: bool = False
    proxima_tentativa_em: str = ""
    tentativas: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.criado_em:
            self.criado_em = agora_iso()
        if not self.dedupe_key:
            self.dedupe_key = self.tipo

    def vencido(self, agora: datetime | None = None) -> bool:
        limite = _parse(self.validade)
        return limite is not None and (agora or datetime.now(timezone.utc)) > limite

    def chave_dedupe(self) -> str:
        return self.dedupe_key

    def marca_vencido(self, agora: datetime | None = None) -> str:
        marca = "⚠️ VENCIDO — não é mais oportunidade atual. "
        return marca if self.vencido(agora) else ""

    def texto(self, agora: datetime | None = None) -> str:
        marca = {"urgente": "🔴 URGENTE", "atencao": "🟡 ATENÇÃO",
                 "informativo": "🔵"}.get(self.severidade, "")
        linhas = [f"{marca} {self.titulo}", self.detalhe]
        contexto = []
        if self.preco:
            contexto.append(f"preço {self.preco}"
                            + (f" ({self.fonte})" if self.fonte else ""))
        elif self.fonte:
            contexto.append(f"fonte {self.fonte}")
        contexto.append(f"hora {self.criado_em[:19]}Z")
        if self.validade:
            contexto.append(f"válido até {self.validade[:19]}Z")
        linhas.append(" · ".join(contexto))
        if self.acao:
            linhas.append(f"➡️ {self.acao}")
        return self.marca_vencido(agora) + "\n".join(linhas)

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, doc: dict) -> AlertaVivo:
        campos = {k: v for k, v in doc.items() if k in cls.__annotations__}
        return cls(**campos)


class FilaDeAlertas:
    """Fila durável com dedupe, backoff, rate limit e expiração."""

    def __init__(self, *, janela_dedupe_s: float = JANELA_DEDUPE_S,
                 backoff_base_s: float = BACKOFF_BASE_S,
                 backoff_fator: float = BACKOFF_FATOR,
                 backoff_teto_s: float = BACKOFF_TETO_S,
                 max_tentativas: int = MAX_TENTATIVAS,
                 rate_limites: dict | None = None,
                 janela_rate_s: float = JANELA_RATE_S,
                 alertas: list[AlertaVivo] | None = None):
        self.janela_dedupe_s = janela_dedupe_s
        self.backoff_base_s = backoff_base_s
        self.backoff_fator = backoff_fator
        self.backoff_teto_s = backoff_teto_s
        self.max_tentativas = max_tentativas
        self.rate_limites = dict(RATE_LIMITE_PADRAO)
        if rate_limites:
            self.rate_limites.update(rate_limites)
        self.janela_rate_s = janela_rate_s
        self.alertas: list[AlertaVivo] = list(alertas or [])
        # Serializa o DESPACHO desta fila: ciclo da mesa, copiloto manual e
        # rota HTTP entregam a MESMA fila em threads diferentes. Sem a trava,
        # dois despachos leem o mesmo alerta 'novo' e entregam a mesma
        # notificação duas vezes (reproduzido no teste com barreira).
        self.trava = threading.Lock()
        self._entregas_canal: dict[str, list[datetime]] = {}

    # -- entrada -------------------------------------------------------
    def enfileirar(self, *, tipo: str, severidade: str, titulo: str,
                   detalhe: str, acao: str, fonte: str = "", preco: str = "",
                   link: str = "", validade: datetime | None = None,
                   dedupe_key: str = "", dados: dict | None = None,
                   agora: datetime | None = None) -> tuple[AlertaVivo, bool]:
        """Devolve (alerta, criado_agora). Reincidência dentro da janela de
        dedupe incrementa ocorrências — não cria alerta novo."""
        agora = agora or datetime.now(timezone.utc)
        chave = dedupe_key or tipo
        for existente in reversed(self.alertas):
            if existente.dedupe_key != chave or existente.estado == "expirado":
                continue
            criado = _parse(existente.criado_em)
            if criado and (agora - criado).total_seconds() < self.janela_dedupe_s:
                existente.ocorrencias += 1
                existente.dados["ultima_ocorrencia"] = agora_iso(agora)
                if preco:
                    existente.preco = preco
                return existente, False
        alerta = AlertaVivo(
            tipo=tipo, severidade=severidade, titulo=titulo, detalhe=detalhe,
            acao=acao, criado_em=agora_iso(agora), fonte=fonte, preco=preco,
            link=link, validade=agora_iso(validade) if validade else "",
            dedupe_key=chave, dados=dict(dados or {}))
        self.alertas.append(alerta)
        del self.alertas[:-400]
        return alerta, True

    # -- ciclo de vida -------------------------------------------------
    def pendentes(self, agora: datetime | None = None) -> list[AlertaVivo]:
        agora = agora or datetime.now(timezone.utc)
        saida = []
        for a in self.alertas:
            if a.estado not in ("novo", "falhou") or a.esgotado:
                continue
            if a.vencido(agora):
                continue
            proxima = _parse(a.proxima_tentativa_em)
            if proxima and agora < proxima:
                continue
            saida.append(a)
        return saida

    def tentativas_de_entrega(self, a: AlertaVivo) -> int:
        return sum(1 for t in a.tentativas if not t.get("ok"))

    def registrar_tentativa(self, a: AlertaVivo, canal: str, *,
                            ok: bool, erro: str = "", detalhe: str = "",
                            agora: datetime | None = None) -> None:
        a.tentativas.append({"em": agora_iso(agora), "canal": canal,
                             "ok": ok, "erro": erro[:200],
                             "detalhe": detalhe[:200]})

    def marcar_entregue(self, a: AlertaVivo, canal: str, *,
                        agora: datetime | None = None,
                        detalhe: str = "") -> None:
        agora = agora or datetime.now(timezone.utc)
        a.estado = "entregue"
        a.entregue_em = agora_iso(agora)
        a.entregue_canal = canal
        a.proxima_tentativa_em = ""
        self.registrar_tentativa(a, canal, ok=True, detalhe=detalhe,
                                 agora=agora)

    def marcar_falha(self, a: AlertaVivo, canal: str, erro: str, *,
                     agora: datetime | None = None) -> None:
        """Fecha a rodada como falha e calcula o próximo backoff. As
        tentativas em si são auditadas por ``registrar_tentativa`` (uma por
        canal tentado) — aqui só o estado agregado muda."""
        agora = agora or datetime.now(timezone.utc)
        a.estado = "falhou"
        falhas = self.tentativas_de_entrega(a)
        if falhas >= self.max_tentativas:
            a.esgotado = True
            a.proxima_tentativa_em = ""
            return
        # 1ª falha espera a base; cada reincidência dobra (com teto).
        espera = min(self.backoff_base_s * (self.backoff_fator ** (falhas - 1)),
                     self.backoff_teto_s)
        a.proxima_tentativa_em = agora_iso(agora + timedelta(seconds=espera))
        if erro:
            a.dados["ultimo_erro"] = erro[:200]

    def expirar_vencidos(self, agora: datetime | None = None) -> int:
        agora = agora or datetime.now(timezone.utc)
        n = 0
        for a in self.alertas:
            if a.estado in ("novo", "falhou") and a.vencido(agora):
                a.estado = "expirado"
                n += 1
        return n

    def marcar_lido(self, alerta_id: str, *,
                    agora: datetime | None = None) -> AlertaVivo | None:
        for a in self.alertas:
            if a.id == alerta_id:
                a.lido_em = agora_iso(agora)
                return a
        return None

    # -- rate limit ----------------------------------------------------
    def permite_canal(self, canal: str, agora: datetime | None = None) -> bool:
        agora = agora or datetime.now(timezone.utc)
        limite = self.rate_limites.get(canal)
        if limite is None:
            return True
        janela = self._entregas_canal.get(canal, [])
        janela = [t for t in janela
                  if (agora - t).total_seconds() < self.janela_rate_s]
        self._entregas_canal[canal] = janela
        return len(janela) < limite

    def registrar_entrega_canal(self, canal: str, *,
                                agora: datetime | None = None) -> None:
        agora = agora or datetime.now(timezone.utc)
        self._entregas_canal.setdefault(canal, []).append(agora)

    # -- visão do painel -----------------------------------------------
    def _para_painel(self, a: AlertaVivo, agora: datetime) -> dict:
        return {**a.to_dict(), "vencido": a.vencido(agora),
                "esgotado": a.esgotado}

    def inbox(self, agora: datetime | None = None) -> dict:
        agora = agora or datetime.now(timezone.utc)
        ativos, recentes, historico = [], [], []
        contadores = dict.fromkeys(ESTADOS, 0)
        for a in sorted(self.alertas, key=lambda x: x.criado_em, reverse=True):
            contadores[a.estado] = contadores.get(a.estado, 0) + 1
            item = self._para_painel(a, agora)
            historico.append(item)
            if a.estado in ("novo", "falhou") and not a.vencido(agora):
                ativos.append(item)
            elif a.estado == "entregue" and not a.lido_em:
                recentes.append(item)
        return {"ativos": ativos, "nao_lidos": recentes,
                "historico": historico[:60], "contadores": contadores,
                "total": len(self.alertas)}

    # -- persistência --------------------------------------------------
    def to_dict(self) -> dict:
        return {"schema": SCHEMA,
                "alertas": [a.to_dict() for a in self.alertas]}

    @classmethod
    def from_dict(cls, doc: dict | None, **kw) -> FilaDeAlertas:
        if not doc or doc.get("schema") != SCHEMA:
            return cls(**kw)
        alertas = []
        for item in doc.get("alertas", []):
            try:
                alertas.append(AlertaVivo.from_dict(item))
            except TypeError:
                continue
        return cls(alertas=alertas, **kw)


class Despachante:
    """Entrega pendentes pelos canais, na ordem de prioridade. O canal
    'painel' é a entrega garantida — se nenhum canal externo estiver
    configurado, o alerta É entregue no painel e contado como tal."""

    def __init__(self, fila: FilaDeAlertas, canais: list, *,
                 matriz=None, auditoria: list | None = None):
        self.fila = fila
        self.canais = {c.nome: c for c in canais}
        self.matriz = matriz
        self.auditoria = auditoria if auditoria is not None else []

    def _canais_do_alerta(self, alerta: AlertaVivo) -> list[str]:
        padrao = [n for n in PRIORIDADE_CANAIS if n in self.canais]
        if self.matriz is not None:
            escolhidos = self.matriz.canais_para(alerta, padrao=padrao)
            return [c for c in escolhidos if c in self.canais]
        return padrao

    def despachar(self, agora: datetime | None = None) -> dict:
        agora = agora or datetime.now(timezone.utc)
        # Seção crítica POR FILA (não global): inquilinos distintos seguem
        # despachando em paralelo; a mesma fila, nunca.
        with self.fila.trava:
            return self._despachar_interno(agora)

    def _despachar_interno(self, agora: datetime) -> dict:
        expirados = self.fila.expirar_vencidos(agora)
        entregues, falhas, adiados = [], [], []
        for alerta in list(self.fila.pendentes(agora)):
            candidatos = self._canais_do_alerta(alerta)
            if not candidatos:
                candidatos = ["painel"] if "painel" in self.canais else []
            if not candidatos:
                adiados.append(alerta.id)
                continue
            liberados = [n for n in candidatos
                         if self.fila.permite_canal(n, agora)]
            if not liberados:
                adiados.append(alerta.id)
                self.auditoria.append(
                    {"em": agora_iso(agora), "alerta": alerta.id,
                     "evento": "rate_limit", "canais": candidatos})
                continue
            entregue = False
            falhas_canal: list[tuple[str, str]] = []
            for nome in liberados:
                canal = self.canais[nome]
                if not canal.configurado():
                    continue
                try:
                    resultado = canal.enviar(alerta, agora=agora) or {}
                except Exception as exc:
                    resultado = {"ok": False,
                                 "erro": f"{type(exc).__name__}: {exc}"[:160]}
                if resultado.get("ok"):
                    self.fila.marcar_entregue(
                        alerta, nome, agora=agora,
                        detalhe=str(resultado.get("detalhe", ""))[:200])
                    self.fila.registrar_entrega_canal(nome, agora=agora)
                    for c_falho, erro_f in falhas_canal:
                        self.fila.registrar_tentativa(
                            alerta, c_falho, ok=False, erro=erro_f,
                            agora=agora)
                    self.auditoria.append(
                        {"em": agora_iso(agora), "alerta": alerta.id,
                         "evento": "entregue", "canal": nome,
                         "latencia_ms": resultado.get("latencia_ms", 0)})
                    entregues.append({"id": alerta.id, "canal": nome,
                                      "tipo": alerta.tipo})
                    entregue = True
                    break
                falhas_canal.append(
                    (nome, str(resultado.get("erro") or "falhou")))
            if entregue:
                continue
            if falhas_canal:
                for c_falho, erro_f in falhas_canal:
                    self.fila.registrar_tentativa(
                        alerta, c_falho, ok=False, erro=erro_f, agora=agora)
                resumo_erro = " | ".join(f"{c}: {e}" for c, e in falhas_canal)
                self.fila.marcar_falha(alerta, ",".join(liberados),
                                       resumo_erro, agora=agora)
                self.auditoria.append(
                    {"em": agora_iso(agora), "alerta": alerta.id,
                     "evento": "falha", "erro": resumo_erro[:200]})
                falhas.append({"id": alerta.id, "erro": resumo_erro[:200]})
            else:
                # nenhum canal configurado: o painel é a entrega garantida
                if "painel" in self.canais:
                    painel = self.canais["painel"]
                    resultado = painel.enviar(alerta, agora=agora) or {}
                    self.fila.marcar_entregue(
                        alerta, "painel", agora=agora,
                        detalhe=str(resultado.get("detalhe", ""))[:200])
                    self.fila.registrar_entrega_canal("painel", agora=agora)
                    entregues.append({"id": alerta.id, "canal": "painel",
                                      "tipo": alerta.tipo})
                else:
                    adiados.append(alerta.id)
        del self.auditoria[:-300]
        return {"entregues": entregues, "falhas": falhas, "adiados": adiados,
                "expirados": expirados}


class MatrizDeRoteamento:
    """Regras de roteamento por trader/estratégia/corretora/ativo/severidade.

    Regra: {"escopo": {"trader": "alpha", "severidade_min": "atencao"},
            "canais": ["telegram", "painel"]}
    A primeira regra que casa vence. Canal desconhecido na regra é IGNORADO
    com aviso — a matriz nunca inventa canal que não existe (fail-safe)."""

    CAMPOS = ("trader", "estrategia", "corretora", "ativo", "severidade_min")
    PESO_SEVERIDADE = {"informativo": 0, "atencao": 1, "urgente": 2}

    def __init__(self, regras: list[dict] | None = None):
        self.regras = []
        self.avisos: list[str] = []
        for regra in regras or []:
            self.adicionar(regra)

    def adicionar(self, regra: dict) -> bool:
        escopo = dict(regra.get("escopo") or {})
        canais = [c for c in (regra.get("canais") or [])
                  if c in PRIORIDADE_CANAIS]
        ignorados = set(regra.get("canais") or []) - set(canais)
        if ignorados:
            self.avisos.append(
                f"matriz: canal(ais) desconhecido(s) ignorado(s): "
                f"{', '.join(sorted(ignorados))}")
        if not canais:
            self.avisos.append("matriz: regra sem canal válido — ignorada")
            return False
        campos = {k: str(v) for k, v in escopo.items() if k in self.CAMPOS}
        self.regras.append({"escopo": campos, "canais": canais})
        return True

    def canais_para(self, alerta: AlertaVivo, *,
                    padrao: list[str]) -> list[str]:
        for regra in self.regras:
            escopo = regra["escopo"]
            if not self._casa(escopo, alerta):
                continue
            return list(regra["canais"])
        return list(padrao)

    def _casa(self, escopo: dict, alerta: AlertaVivo) -> bool:
        for campo, valor in escopo.items():
            if campo == "severidade_min":
                if (self.PESO_SEVERIDADE.get(alerta.severidade, 0)
                        < self.PESO_SEVERIDADE.get(valor, 9)):
                    return False
                continue
            alvo = str(alerta.dados.get(campo, "") or "")
            if alvo != valor:
                return False
        return True

    def to_dict(self) -> dict:
        return {"regras": self.regras, "avisos": self.avisos[-10:]}

    @classmethod
    def from_dict(cls, doc: dict | None) -> MatrizDeRoteamento:
        if not doc:
            return cls()
        return cls(regras=doc.get("regras") or [])


# --------------------------------------------------------------- segurança
# Ações que um botão de Telegram PODE executar. Nenhuma delas opera na
# corretora: abrir detalhes é navegação, ler/intenção são registros.
ACOES_TELEGRAM_PERMITIDAS = ("ler", "intencao")


def processar_callback_telegram(payload: dict, fila: FilaDeAlertas, *,
                                registrar_intencao=None,
                                agora: datetime | None = None) -> dict:
    """Processa callback de botão do Telegram. ESTRUTURALMENTE incapaz de
    executar ordem: só conhece as ações de ``ACOES_TELEGRAM_PERMITIDAS`` e
    não importa (nem pode importar) execução. Ação desconhecida é recusada."""
    cq = (payload or {}).get("callback_query") or {}
    data = str(cq.get("data") or "")
    acao, _, alerta_id = data.partition(":")
    if acao not in ACOES_TELEGRAM_PERMITIDAS:
        return {"ok": False,
                "detalhe": (f"ação '{acao or '?'}' não permitida — botões de "
                            "alerta só abrem detalhes e registram leitura/"
                            "intenção; nada aqui executa ordem")}
    if acao == "ler":
        alerta = fila.marcar_lido(alerta_id, agora=agora)
        if alerta is None:
            return {"ok": False, "detalhe": "alerta não encontrado"}
        return {"ok": True, "acao": "ler", "alerta": alerta.id,
                "detalhe": "leitura registrada"}
    # intencao: registra o que o dono pretende fazer — nunca faz.
    alerta = next((a for a in fila.alertas if a.id == alerta_id), None)
    if alerta is None:
        return {"ok": False, "detalhe": "alerta não encontrado"}
    registro = {"em": agora_iso(agora), "tipo": "intencao_declarada",
                "origem": "telegram"}
    if registrar_intencao is not None:
        try:
            registro["observacao"] = str(registrar_intencao(alerta))[:200]
        except Exception as exc:
            registro["observacao"] = f"registro falhou ({type(exc).__name__})"
    alerta.dados.setdefault("intencoes", []).append(registro)
    return {"ok": True, "acao": "intencao", "alerta": alerta.id,
            "detalhe": ("intenção registrada — nenhuma ordem foi enviada; "
                        "execução continua sendo ato manual seu")}


__all__ = ["AlertaVivo", "FilaDeAlertas", "Despachante", "MatrizDeRoteamento",
           "processar_callback_telegram", "ACOES_TELEGRAM_PERMITIDAS",
           "PRIORIDADE_CANAIS", "ESTADOS", "SCHEMA"]
