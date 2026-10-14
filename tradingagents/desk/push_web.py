"""Web Push real (VAPID) do PWA — assinaturas de navegador, chaves e entrega
honesta.

O QUE É: o servidor 24/7 (Oracle, RUNBOOK.md) entrega notificações ao celular
via Web Push padrão (Push API + VAPID), mesmo com o app fechado. O celular
NUNCA fala com a corretora: a notificação nasce no mesmo Centro de Alertas
(fila durável + dedupe + TTL) e este módulo é só mais um canal de entrega.

HONESTIDADE (regra do produto): sem pywebpush no servidor, sem chaves VAPID
ou sem navegador inscrito, ``configurado()=False`` e o detalhe diz o que
falta — nunca fingir entrega. Inscrição expirada (404/410 no envio) é PODADA
por callback — o painel mostra a verdade.

SEGREDOS: a chave privada VAPID e as assinaturas ficam no config do
inquilino (``ALERTA_VAPID_PRIVADA`` / ``PUSH_ASSINURAS``) e passam pela
cifragem em repouso do SEC-1 (DPAPI no Windows, 0600 no POSIX). Nada de
segredo em log, resposta HTTP ou teste.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

LIMITE_DISPOSITIVOS = 10

CHAVE_VAPID_PUBLICA = "ALERTA_VAPID_PUBLICA"
CHAVE_VAPID_PRIVADA = "ALERTA_VAPID_PRIVADA"
CHAVE_ASSINATURAS = "PUSH_ASSINATURAS"

# Contato exigido pelo padrão VAPID; não é segredo e não é verificado.
VAPID_SUB_PADRAO = "mailto:dz23-trading-ai@localhost"

# TTL/urgency por severidade (spec do PWA): alerta urgente não pode chegar
# atrasado fingindo ser atual — vence em 15 min; informativo dura um dia.
TTL_POR_SEVERIDADE = {"urgente": 900, "emergencia": 900,
                      "atencao": 3600, "informativo": 86400}
URGENCIA_POR_SEVERIDADE = {"urgente": "high", "emergencia": "high",
                           "atencao": "normal", "informativo": "low"}

# URL do toque: só caminho + fragmento — o fragmento não viaja ao servidor,
# e NENHUM dado financeiro entra na URL da notificação.
URL_DO_TOQUE = "/app#notificacoes"


def pywebpush_disponivel() -> bool:
    """O servidor consegue enviar de verdade? (import LAZY em todo lugar)"""
    try:
        import pywebpush  # noqa: F401
        return True
    except Exception:
        return False


def gerar_chaves_vapid() -> dict:
    """Gera um par VAPID: {"publica": b64url, "privada_pem": PEM}.

    Levanta RuntimeError honesto quando py_vapid não está instalado (extra
    [push]) — a rota traduz para 503 com instrução, nunca gera chave falsa.
    """
    try:
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid
    except Exception as exc:  # pragma: no cover - depende do ambiente
        raise RuntimeError(
            "py_vapid ausente — instale o extra [push] no servidor"
        ) from exc
    vapid = Vapid()
    vapid.generate_keys()
    bruta = vapid.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint)
    publica = base64.urlsafe_b64encode(bruta).rstrip(b"=").decode("ascii")
    privada = vapid.private_pem()
    if isinstance(privada, bytes):
        privada = privada.decode("utf-8")
    return {"publica": publica, "privada_pem": privada}


def ttl_e_urgencia(severidade) -> tuple[int, str]:
    sev = str(severidade or "").strip().lower()
    return (TTL_POR_SEVERIDADE.get(sev, 3600),
            URGENCIA_POR_SEVERIDADE.get(sev, "normal"))


def montar_payload(alerta) -> dict:
    """Payload MÍNIMO do push — o texto do próprio alerta e nada mais."""
    corpo = getattr(alerta, "detalhe", "") or getattr(alerta, "acao", "") or ""
    return {
        "id": str(getattr(alerta, "id", "") or ""),
        "tipo": str(getattr(alerta, "tipo", "") or ""),
        "severidade": str(getattr(alerta, "severidade", "") or "informativo"),
        "titulo": str(getattr(alerta, "titulo", "") or "")[:120],
        "corpo": str(corpo)[:280],
        "url": URL_DO_TOQUE,
    }


@dataclass
class AssinaturaPush:
    """Uma inscrição de navegador (PushSubscription serializada)."""

    endpoint: str
    p256dh: str
    auth: str
    rotulo: str = ""
    criado_em: str = ""
    ultimo_ok: str = ""
    ultimo_erro: str = ""
    falhas: int = 0

    def validar(self) -> str:
        """'' quando válida; caso contrário o motivo (fail-closed)."""
        e = (self.endpoint or "").strip()
        if not e.startswith("https://"):
            return ("endpoint precisa ser https:// — o navegador só assina "
                    "em contexto seguro")
        if len(e) > 2000:
            return "endpoint longo demais"
        if re.search(r"[\s\x00-\x1f]", e):
            return "endpoint com caracteres inválidos"
        if not 16 <= len(self.p256dh or "") <= 200:
            return "chave p256dh ausente ou inválida"
        if not 8 <= len(self.auth or "") <= 200:
            return "segredo auth ausente ou inválido"
        return ""

    def identificador(self) -> str:
        """Id curto e estável para o painel (nunca o endpoint com token)."""
        return hashlib.sha256(
            (self.endpoint or "").encode("utf-8")).hexdigest()[:10]

    def resumo(self) -> dict:
        """Visão SEGURA para a API: sem endpoint (ele carrega a capacidade)."""
        return {"id": self.identificador(), "rotulo": self.rotulo,
                "criado_em": self.criado_em, "ultimo_ok": self.ultimo_ok,
                "ultimo_erro": self.ultimo_erro, "falhas": self.falhas}

    def to_dict(self) -> dict:
        return {"endpoint": self.endpoint, "p256dh": self.p256dh,
                "auth": self.auth, "rotulo": self.rotulo,
                "criado_em": self.criado_em, "ultimo_ok": self.ultimo_ok,
                "ultimo_erro": self.ultimo_erro, "falhas": self.falhas}

    @classmethod
    def from_dict(cls, doc: dict) -> AssinaturaPush:
        chaves = doc.get("keys") or {}
        return cls(
            endpoint=str(doc.get("endpoint") or ""),
            p256dh=str(doc.get("p256dh") or chaves.get("p256dh") or ""),
            auth=str(doc.get("auth") or chaves.get("auth") or ""),
            rotulo=str(doc.get("rotulo") or "")[:60],
            criado_em=str(doc.get("criado_em") or ""),
            ultimo_ok=str(doc.get("ultimo_ok") or ""),
            ultimo_erro=str(doc.get("ultimo_erro") or ""),
            falhas=int(doc.get("falhas") or 0),
        )


def carregar_assinaturas(cfg: dict) -> list[AssinaturaPush]:
    """Lê PUSH_ASSINATURAS do config (decifrado pelo load_cfg). Inválidas
    são descartadas em silêncio POSSÍVEL — elas nunca seriam usadas no
    envio; o limite de dispositivos é aplicado na leitura."""
    bruto = cfg.get(CHAVE_ASSINATURAS)
    if isinstance(bruto, str) and bruto.strip():
        try:
            dados = json.loads(bruto)
        except ValueError:
            return []
    elif isinstance(bruto, list):
        dados = bruto
    else:
        return []
    if not isinstance(dados, list):
        return []
    validas = []
    for item in dados:
        if not isinstance(item, dict):
            continue
        assinatura = AssinaturaPush.from_dict(item)
        if assinatura.validar() == "":
            validas.append(assinatura)
    return validas[:LIMITE_DISPOSITIVOS]


def salvar_assinaturas(cfg: dict, assinaturas) -> dict:
    """Muta o cfg com o JSON das assinaturas — o CHAMADOR persiste com
    save_cfg (que aplica a cifragem em repouso do SEC-1)."""
    cfg[CHAVE_ASSINATURAS] = json.dumps(
        [a.to_dict() for a in assinaturas], ensure_ascii=False)
    return cfg


def _agora_iso(agora: datetime | None = None) -> str:
    return (agora or datetime.now(timezone.utc)).isoformat(timespec="seconds")


def _status_do_erro(exc: Exception):
    resposta = getattr(exc, "response", None)
    return getattr(resposta, "status_code", None)


class EntregadorPush:
    """Fan-out para os navegadores inscritos — um por um, com poda de
    inscrições expiradas (404/410) e nenhuma entrega inventada."""

    def __init__(self, assinaturas, vapid_privada: str = "",
                 vapid_publica: str = "", *, remetente=None, podar=None,
                 sub: str = VAPID_SUB_PADRAO):
        self.assinaturas = list(assinaturas or [])
        self.vapid_privada = (vapid_privada or "").strip()
        self.vapid_publica = (vapid_publica or "").strip()
        self._remetente = remetente
        self._podar = podar
        self.sub = sub

    def enviar(self, alerta, agora: datetime | None = None) -> dict:
        if not self.vapid_privada:
            return {"ok": False,
                    "erro": ("sem chave privada VAPID — gere as chaves em "
                             "Configuração → Aplicativo e Notificações")}
        if not self.assinaturas:
            return {"ok": False, "erro": "nenhum navegador inscrito ainda"}
        if self._remetente is None and not pywebpush_disponivel():
            return {"ok": False,
                    "erro": ("pywebpush ausente no servidor — instale o "
                             "extra [push] (RUNBOOK.md)")}
        payload = montar_payload(alerta)
        ttl, urgencia = ttl_e_urgencia(getattr(alerta, "severidade", ""))
        quando = _agora_iso(agora)
        entregues, erros = 0, 0
        expiradas: list[AssinaturaPush] = []
        for assinatura in list(self.assinaturas):
            try:
                self._enviar_um(assinatura, payload, ttl, urgencia)
                assinatura.ultimo_ok = quando
                assinatura.ultimo_erro = ""
                assinatura.falhas = 0
                entregues += 1
            except Exception as exc:
                if _status_do_erro(exc) in (404, 410):
                    expiradas.append(assinatura)
                else:
                    assinatura.falhas += 1
                    assinatura.ultimo_erro = (
                        f"{type(exc).__name__}: {exc}"[:160])
                    erros += 1
        if expiradas and self._podar is not None:
            with contextlib.suppress(Exception):
                # Poda é melhor-esforço: a próxima leva de envio tenta de
                # novo; a entrega desta rodada já aconteceu de verdade.
                self._podar(expiradas)
        total = len(self.assinaturas)
        resultado = {"entregues": entregues,
                     "expiradas": [a.identificador() for a in expiradas],
                     "erros": erros}
        if entregues:
            resultado.update(ok=True, detalhe=(
                f"push entregue a {entregues}/{total} dispositivo(s)"
                + (f" · {len(expiradas)} inscrição(ões) expirada(s) removida(s)"
                   if expiradas else "")))
        elif expiradas and not erros:
            resultado.update(ok=False, erro=(
                "todas as inscrições expiraram (o navegador desinscreveu) — "
                "reinscreva o aparelho no painel"))
        else:
            resultado.update(ok=False, erro=(
                f"entrega falhou em {erros} dispositivo(s)"))
        return resultado

    def _enviar_um(self, assinatura: AssinaturaPush, payload: dict,
                   ttl: int, urgencia: str) -> None:
        if self._remetente is not None:
            self._remetente(assinatura, payload, ttl, urgencia)
            return
        from py_vapid import Vapid
        from pywebpush import webpush
        webpush(
            subscription_info={
                "endpoint": assinatura.endpoint,
                "keys": {"p256dh": assinatura.p256dh,
                         "auth": assinatura.auth}},
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=Vapid.from_pem(self.vapid_privada.encode("utf-8")),
            vapid_claims={"sub": self.sub},
            ttl=ttl,
            headers={"Urgency": urgencia},
            timeout=15)


def estado_publico(cfg: dict) -> dict:
    """Estado HONESTO para o painel — nunca chave privada, nunca endpoint."""
    assinaturas = carregar_assinaturas(cfg)
    return {
        "habilitado": str(cfg.get("ALERTA_WEBPUSH", "")).strip().lower()
        in ("1", "true", "on", "sim", "yes"),
        "vapid_publica": str(cfg.get(CHAVE_VAPID_PUBLICA) or "").strip(),
        "tem_privada": bool(str(cfg.get(CHAVE_VAPID_PRIVADA) or "").strip()),
        "pywebpush": pywebpush_disponivel(),
        "total": len(assinaturas),
        "limite": LIMITE_DISPOSITIVOS,
        "dispositivos": [a.resumo() for a in assinaturas],
        "url_do_toque": URL_DO_TOQUE,
    }


__all__ = [
    "LIMITE_DISPOSITIVOS", "CHAVE_VAPID_PUBLICA", "CHAVE_VAPID_PRIVADA",
    "CHAVE_ASSINATURAS", "VAPID_SUB_PADRAO", "TTL_POR_SEVERIDADE",
    "URGENCIA_POR_SEVERIDADE", "URL_DO_TOQUE", "AssinaturaPush",
    "EntregadorPush", "carregar_assinaturas", "salvar_assinaturas",
    "estado_publico", "gerar_chaves_vapid", "montar_payload",
    "pywebpush_disponivel", "ttl_e_urgencia",
]
