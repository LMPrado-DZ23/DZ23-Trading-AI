"""Canais de entrega do Centro de Alertas (E2.2).

Regras que valem para TODOS os canais:
- Nenhuma mensagem carrega token, chave ou saldo (o texto só tem o que o
  ``AlertaVivo`` monta; segredos nem chegam aqui).
- Todo canal aceita um ``transporte`` injetável — o teste prova entrega,
  falha e timeout sem tocar a internet.
- ``configurado()=False`` não é falha: é um canal que o dono ainda não
  ligou; o painel explica o que falta preencher.
- E-mail é canal EXCLUSIVO de relatórios: nunca entrega alerta (guard
  estrutural em ``CanalEmail.enviar``).
- Botões do Telegram abrem o painel ou registram leitura/intenção —
  nenhum botão executa ordem (o processamento vive em notificacao_viva e
  não conhece execução).
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime

logger = logging.getLogger(__name__)

PAINEL_PADRAO = "http://127.0.0.1:8000/"

# Identidade do app no Windows (AUMID). O valor já era usado no E2 (o
# Windows criou 'Notifications\Settings\DZ23 Trading AI' ao exibir o
# primeiro toast); o que faltava era o REGISTRO em Classes\AppUserModelId —
# sem ele o app não aparece com nome próprio na lista de remetentes das
# Configurações e o toast fica sem identidade gerenciável.
AUMID = "DZ23 Trading AI"


def registrar_identidade_windows(icone: str = "") -> dict:
    """Registra HKCU\\Software\\Classes\\AppUserModelId\\<AUMID> (idempotente).

    Mexe SOMENTE na chave do próprio app. DisplayName faz o toast sair com
    nome nas Configurações do Windows; IconUri usa o exe instalado quando
    existir. Nunca falha o chamador: devolve ok=False com o motivo.
    """
    if sys.platform != "win32":
        return {"ok": False,
                "detalhe": "identidade de toast é exclusiva do Windows"}
    if not icone:
        padrao = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                              "DZ23-Trading-AI", "DZ23-Trading-AI.exe")
        icone = padrao if padrao and os.path.exists(padrao) else ""
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                f"Software\\Classes\\AppUserModelId\\{AUMID}",
                                0, winreg.KEY_SET_VALUE) as chave:
            winreg.SetValueEx(chave, "DisplayName", 0, winreg.REG_SZ, AUMID)
            if icone:
                winreg.SetValueEx(chave, "IconUri", 0, winreg.REG_SZ, icone)
        return {"ok": True, "detalhe": f"identidade '{AUMID}' registrada"}
    except Exception as exc:
        return {"ok": False,
                "detalhe": f"falha ao registrar identidade: {exc}"[:160]}

_SCRIPT_TOAST = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI."
    "Notifications, ContentType = WindowsRuntime] | Out-Null; "
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType "
    "= WindowsRuntime] | Out-Null; "
    "$tpl = [Windows.UI.Notifications.ToastNotificationManager]::"
    "GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::"
    "ToastText02); "
    "$textos = $tpl.GetElementsByTagName('text'); "
    "$textos.Item(0).AppendChild($tpl.CreateTextNode('DZ23 — {titulo}')) "
    "| Out-Null; "
    "$textos.Item(1).AppendChild($tpl.CreateTextNode('{corpo}')) | Out-Null; "
    "$toast = [Windows.UI.Notifications.ToastNotification]::new($tpl); "
    "[Windows.UI.Notifications.ToastNotificationManager]::"
    "CreateToastNotifier('{aumid}').Show($toast)")


def _ligado(cfg: dict, chave: str, padrao: bool = True) -> bool:
    valor = str(cfg.get(chave) or "").strip().lower()
    if not valor:
        return padrao
    return valor not in ("0", "false", "nao", "não", "off")


class CanalBase:
    nome = "base"
    prioridade = 99

    def configurado(self) -> bool:
        raise NotImplementedError

    def disponivel(self) -> bool:
        return True

    def detalhe(self) -> str:
        return ""

    def enviar(self, alerta, *, agora: datetime | None = None) -> dict:
        inicio = time.monotonic()
        try:
            resultado = self._entregar(alerta, agora=agora)
        except Exception as exc:
            return {"ok": False, "erro": f"{type(exc).__name__}: {exc}"[:160],
                    "latencia_ms": int((time.monotonic() - inicio) * 1000)}
        resultado.setdefault("ok", True)
        resultado["latencia_ms"] = int((time.monotonic() - inicio) * 1000)
        return resultado

    def _entregar(self, alerta, *, agora=None) -> dict:
        raise NotImplementedError


class CanalPainel(CanalBase):
    nome = "painel"
    prioridade = 3

    def configurado(self) -> bool:
        return True

    def detalhe(self) -> str:
        return "entrega garantida: o alerta fica na Caixa de entrada do painel"

    def _entregar(self, alerta, *, agora=None) -> dict:
        return {"ok": True, "detalhe": "visível na Caixa de entrada do painel"}


class CanalTelegram(CanalBase):
    nome = "telegram"
    prioridade = 1

    def __init__(self, token: str = "", chat: str = "",
                 painel_url: str = "", transporte=None):
        self.token = (token or "").strip()
        self.chat = (chat or "").strip()
        self.painel_url = (painel_url or PAINEL_PADRAO).strip()
        self._transporte = transporte

    def configurado(self) -> bool:
        return bool(self.token and self.chat)

    def detalhe(self) -> str:
        if self.configurado():
            return "configurado (bot + chat)"
        return ("preencha ALERTA_TELEGRAM_TOKEN e ALERTA_TELEGRAM_CHAT "
                "vindos do @BotFather")

    def _teclado(self, alerta) -> dict:
        return {"inline_keyboard": [
            [{"text": "🔎 Abrir no painel", "url": self.painel_url}],
            [{"text": "✓ Confirmar leitura",
              "callback_data": f"ler:{alerta.id}"},
             {"text": "📝 Registrar intenção",
              "callback_data": f"intencao:{alerta.id}"}]]}

    def _entregar(self, alerta, *, agora=None) -> dict:
        if self._transporte is not None:
            self._transporte(alerta)
            return {"ok": True, "detalhe": "transporte de teste"}
        import httpx
        resposta = httpx.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            json={"chat_id": self.chat, "text": alerta.texto(),
                  "reply_markup": self._teclado(alerta)},
            timeout=10)
        ok = resposta.status_code == 200
        return {"ok": ok, "detalhe": f"HTTP {resposta.status_code}",
                "erro": "" if ok else f"HTTP {resposta.status_code}"}


class CanalWindows(CanalBase):
    nome = "windows"
    prioridade = 2

    def __init__(self, ativo: bool = True, executar=None):
        self.ativo = ativo
        self._executar = executar

    def configurado(self) -> bool:
        return self.ativo

    def disponivel(self) -> bool:
        return sys.platform == "win32"

    def detalhe(self) -> str:
        if not self.ativo:
            return "desligado (ALERTA_WINDOWS=false)"
        if sys.platform != "win32":
            return "toast nativo só existe no Windows"
        return "notificação nativa do Windows (toast)"

    def _entregar(self, alerta, *, agora=None) -> dict:
        titulo = alerta.titulo.replace("'", "''")[:60]
        corpo = f"{alerta.detalhe} {alerta.acao}".replace("'", "''")[:120]
        script = _SCRIPT_TOAST.format(titulo=titulo, corpo=corpo, aumid=AUMID)
        if self._executar is not None:
            self._executar(script)
            return {"ok": True, "detalhe": "executor de teste"}
        if sys.platform != "win32":
            return {"ok": False, "erro": "plataforma sem toast nativo"}
        identidade = registrar_identidade_windows()
        import subprocess
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20)
        if proc.returncode != 0:
            return {"ok": False,
                    "erro": (proc.stderr or "powershell falhou")[:160]}
        # Honestidade: sucesso aqui é entrega à API do Windows — NUNCA prova
        # de que o dono viu. O texto reflete só o que foi provado.
        if identidade["ok"]:
            detalhe = (f"toast entregue à API do Windows sob a identidade "
                       f"'{AUMID}' — a visualização depende do usuário "
                       f"(Não Perturbe/notificações desligadas suprimem)")
        else:
            detalhe = ("toast entregue à API do Windows, mas a identidade "
                       f"do app não foi registrada: {identidade['detalhe']}")
        return {"ok": True, "detalhe": detalhe}


class CanalDiscord(CanalBase):
    nome = "discord"
    prioridade = 4

    def __init__(self, webhook_url: str = "", transporte=None):
        self.webhook_url = (webhook_url or "").strip()
        self._transporte = transporte

    def configurado(self) -> bool:
        return bool(self.webhook_url)

    def detalhe(self) -> str:
        return ("configurado (webhook)" if self.configurado()
                else "opcional — preencha ALERTA_DISCORD_WEBHOOK")

    def _entregar(self, alerta, *, agora=None) -> dict:
        if self._transporte is not None:
            self._transporte(alerta)
            return {"ok": True, "detalhe": "transporte de teste"}
        import httpx
        resposta = httpx.post(self.webhook_url,
                              json={"content": alerta.texto()}, timeout=10)
        ok = resposta.status_code in (200, 204)
        return {"ok": ok, "detalhe": f"HTTP {resposta.status_code}",
                "erro": "" if ok else f"HTTP {resposta.status_code}"}


class CanalWhatsApp(CanalBase):
    nome = "whatsapp"
    prioridade = 5

    def __init__(self, token: str = "", phone_id: str = "",
                 destino: str = "", transporte=None):
        self.token = (token or "").strip()
        self.phone_id = (phone_id or "").strip()
        self.destino = (destino or "").strip()
        self._transporte = transporte

    def configurado(self) -> bool:
        return bool(self.token and self.phone_id and self.destino)

    def detalhe(self) -> str:
        if self.configurado():
            return "configurado (WhatsApp Cloud API oficial)"
        return ("opcional — exige conta oficial (ALERTA_WHATSAPP_TOKEN, "
                "ALERTA_WHATSAPP_PHONE, ALERTA_WHATSAPP_DESTINO)")

    def _entregar(self, alerta, *, agora=None) -> dict:
        if self._transporte is not None:
            self._transporte(alerta)
            return {"ok": True, "detalhe": "transporte de teste"}
        import httpx
        resposta = httpx.post(
            f"https://graph.facebook.com/v20.0/{self.phone_id}/messages",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"messaging_product": "whatsapp", "to": self.destino,
                  "type": "text", "text": {"body": alerta.texto()}},
            timeout=10)
        ok = resposta.status_code in (200, 201)
        return {"ok": ok, "detalhe": f"HTTP {resposta.status_code}",
                "erro": "" if ok else f"HTTP {resposta.status_code}"}


class CanalWebPush(CanalBase):
    """Estrutura pronta para o servidor na Oracle (VAPID) — feature-flag.
    Não finge funcionar: sem chaves configuradas, ``configurado()=False`` e
    o painel explica o que falta. Assinaturas ficam persistidas no Armazem."""

    nome = "webpush"
    prioridade = 6

    def __init__(self, habilitado: bool = False, vapid_publica: str = "",
                 assinaturas: list | None = None, transporte=None):
        self.habilitado = habilitado
        self.vapid_publica = (vapid_publica or "").strip()
        self.assinaturas = list(assinaturas or [])
        self._transporte = transporte

    def configurado(self) -> bool:
        return bool(self.habilitado and self.vapid_publica
                    and self.assinaturas)

    def detalhe(self) -> str:
        if not self.habilitado:
            return ("estrutura pronta — ligue ALERTA_WEBPUSH=true quando o "
                    "servidor público (Oracle) tiver as chaves VAPID")
        if not self.vapid_publica:
            return "falta a chave pública VAPID"
        if not self.assinaturas:
            return "sem navegador inscrito ainda"
        return f"{len(self.assinaturas)} navegador(es) inscrito(s)"

    def _entregar(self, alerta, *, agora=None) -> dict:
        if self._transporte is not None:
            self._transporte(alerta)
            return {"ok": True, "detalhe": "transporte de teste"}
        return {"ok": False,
                "erro": ("envio Web Push exige o servidor com as chaves "
                         "VAPID do Oracle")}


class CanalEmail(CanalBase):
    """EXCLUSIVO de relatórios. Alerta por e-mail nunca — guard estrutural."""

    nome = "email"
    prioridade = 7

    MOTIVO = ("e-mail é canal exclusivo de RELATÓRIOS; alertas nunca vão "
              "por e-mail (chega tarde demais para ação)")

    def __init__(self, smtp_host: str = "", smtp_port: str = "",
                 usuario: str = "", senha: str = "", de: str = "", para: str = "",
                 smtp_envio=None):
        self.smtp_host = (smtp_host or "").strip()
        self.smtp_port = (smtp_port or "").strip()
        self.usuario = (usuario or "").strip()
        self.senha = (senha or "").strip()
        self.de = (de or "").strip()
        self.para = (para or "").strip()
        self._smtp_envio = smtp_envio

    def configurado(self) -> bool:
        return bool(self.smtp_host and self.de and self.para)

    def detalhe(self) -> str:
        if self.configurado():
            return "configurado para relatórios (nunca para alertas)"
        return ("opcional — relatórios por e-mail exigem EMAIL_SMTP_HOST, "
                "EMAIL_SMTP_FROM e EMAIL_SMTP_TO")

    def _entregar(self, alerta, *, agora=None) -> dict:
        return {"ok": False, "erro": self.MOTIVO}

    def enviar_relatorio(self, assunto: str, corpo: str) -> dict:
        if not self.configurado():
            return {"ok": False, "erro": "SMTP não configurado — " + self.detalhe()}
        if self._smtp_envio is not None:
            self._smtp_envio(assunto, corpo)
            return {"ok": True, "detalhe": "envio de teste"}
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["Subject"] = assunto
        msg["From"] = self.de
        msg["To"] = self.para
        msg.set_content(corpo)
        porta = int(self.smtp_port or "587")
        with smtplib.SMTP(self.smtp_host, porta, timeout=15) as smtp:
            smtp.starttls()
            if self.usuario:
                smtp.login(self.usuario, self.senha)
            smtp.send_message(msg)
        return {"ok": True, "detalhe": "relatório enviado"}


def construir_canais(cfg: dict, *, transportes: dict | None = None,
                     assinaturas_webpush: list | None = None) -> list[CanalBase]:
    """Monta os canais na ordem de prioridade (1 Telegram … 7 e-mail)."""
    cfg = cfg or {}
    transportes = transportes or {}
    painel_url = cfg.get("TRADINGAGENTS_PAINEL_URL") or PAINEL_PADRAO
    return [
        CanalTelegram(cfg.get("ALERTA_TELEGRAM_TOKEN", ""),
                      cfg.get("ALERTA_TELEGRAM_CHAT", ""),
                      painel_url=painel_url,
                      transporte=transportes.get("telegram")),
        CanalWindows(ativo=_ligado(cfg, "ALERTA_WINDOWS", True),
                     executar=transportes.get("windows")),
        CanalPainel(),
        CanalDiscord(cfg.get("ALERTA_DISCORD_WEBHOOK", ""),
                     transporte=transportes.get("discord")),
        CanalWhatsApp(cfg.get("ALERTA_WHATSAPP_TOKEN", ""),
                      cfg.get("ALERTA_WHATSAPP_PHONE", ""),
                      cfg.get("ALERTA_WHATSAPP_DESTINO", ""),
                      transporte=transportes.get("whatsapp")),
        CanalWebPush(habilitado=_ligado(cfg, "ALERTA_WEBPUSH", False),
                     vapid_publica=cfg.get("ALERTA_VAPID_PUBLICA", ""),
                     assinaturas=assinaturas_webpush,
                     transporte=transportes.get("webpush")),
        CanalEmail(cfg.get("EMAIL_SMTP_HOST", ""), cfg.get("EMAIL_SMTP_PORT", ""),
                   cfg.get("EMAIL_SMTP_USER", ""), cfg.get("EMAIL_SMTP_PASS", ""),
                   cfg.get("EMAIL_SMTP_FROM", ""), cfg.get("EMAIL_SMTP_TO", "")),
    ]


__all__ = ["CanalBase", "CanalPainel", "CanalTelegram", "CanalWindows",
           "CanalDiscord", "CanalWhatsApp", "CanalWebPush", "CanalEmail",
           "construir_canais", "PAINEL_PADRAO"]
