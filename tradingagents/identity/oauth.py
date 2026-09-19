"""Login social (Google e Microsoft) — OAuth 2.0 com PKCE e state.

Decisões registradas:
- DECISÃO ASSUMIDA: OAuth só ENTRA no sistema; quem manda no acesso continua
  sendo o tenant store. O provedor prova a identidade do e-mail, nada mais.
- DECISÃO ASSUMIDA: `state` obrigatório e de uso único (anti-CSRF), com
  validade curta. Sem state válido, o callback é recusado.
- O client_secret nunca sai do servidor, nunca vai a log, nunca ao navegador.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode


class OAuthError(RuntimeError):
    """Falha de login social — nunca cria sessão por acidente."""


@dataclass(frozen=True)
class Provider:
    name: str
    auth_url: str
    token_url: str
    userinfo_url: str
    scope: str
    client_id_env: str
    client_secret_env: str
    redirect_env: str

    def configured(self, env=None) -> bool:
        env = env if env is not None else os.environ
        return bool(env.get(self.client_id_env) and env.get(self.client_secret_env)
                    and env.get(self.redirect_env))


PROVIDERS: dict[str, Provider] = {
    "google": Provider(
        "google",
        "https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token",
        "https://openidconnect.googleapis.com/v1/userinfo",
        "openid email profile",
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"),
    "microsoft": Provider(
        "microsoft",
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "https://graph.microsoft.com/oidc/userinfo",
        "openid email profile",
        "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_REDIRECT_URI"),
}


@dataclass
class PendingLogin:
    provider: str
    verifier: str
    created_at: float = field(default_factory=time.time)
    tenant_hint: str = ""


class StateStore:
    """States de uso único, com expiração. Em memória: sessão de login dura
    segundos e não precisa sobreviver a restart."""

    def __init__(self, ttl_seconds: int = 600):
        self.ttl = ttl_seconds
        self._states: dict[str, PendingLogin] = {}

    def new(self, provider: str, tenant_hint: str = "") -> tuple[str, str]:
        self._prune()
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        self._states[state] = PendingLogin(provider, verifier, tenant_hint=tenant_hint)
        return state, verifier

    def consume(self, state: str, provider: str) -> PendingLogin:
        self._prune()
        pend = self._states.pop(state, None)          # uso ÚNICO
        if pend is None:
            raise OAuthError("state inválido ou já utilizado")
        if pend.provider != provider:
            raise OAuthError("state pertence a outro provedor")
        return pend

    def _prune(self) -> None:
        agora = time.time()
        for k in [k for k, v in self._states.items() if agora - v.created_at > self.ttl]:
            self._states.pop(k, None)


def challenge_from(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def authorize_url(provider: Provider, state: str, verifier: str, env=None) -> str:
    env = env if env is not None else os.environ
    if not provider.configured(env):
        raise OAuthError(f"login com {provider.name} não está configurado no servidor")
    params = {
        "client_id": env[provider.client_id_env],
        "redirect_uri": env[provider.redirect_env],
        "response_type": "code",
        "scope": provider.scope,
        "state": state,
        "code_challenge": challenge_from(verifier),
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "select_account",
    }
    return f"{provider.auth_url}?{urlencode(params)}"


def exchange_code(provider: Provider, code: str, verifier: str, *, env=None,
                  transport=None) -> dict:
    """Troca o código pelo token e devolve o perfil. O segredo é enviado ao
    provedor e nunca aparece em retorno, log ou exceção."""
    env = env if env is not None else os.environ
    if not provider.configured(env):
        raise OAuthError(f"login com {provider.name} não está configurado")
    payload = {
        "client_id": env[provider.client_id_env],
        "client_secret": env[provider.client_secret_env],
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": env[provider.redirect_env],
    }
    if transport is not None:
        try:
            return transport(provider, payload)
        except OAuthError:
            raise
        except Exception as exc:
            raise OAuthError(f"falha no login social ({type(exc).__name__})") from exc
    import httpx
    try:
        tok = httpx.post(provider.token_url, data=payload, timeout=20)
        tok.raise_for_status()
        access = tok.json().get("access_token")
        if not access:
            raise OAuthError("provedor não devolveu token de acesso")
        perfil = httpx.get(provider.userinfo_url,
                           headers={"Authorization": f"Bearer {access}"}, timeout=20)
        perfil.raise_for_status()
        return perfil.json()
    except OAuthError:
        raise
    except Exception as exc:                 # mensagem sem segredo, sem corpo
        raise OAuthError(f"falha no login social ({type(exc).__name__})") from exc


# Provedores autorizados a CRIAR conta: só os que provam verificação de e-mail
# por regra do provider (Google envia email_verified). Microsoft NÃO entra — o
# userinfo atual não dá prova suficiente; login de conta EXISTENTE continua,
# criação de conta nova via Microsoft fica desabilitada até implementação
# comprovada (contrato/teste do provider).
ACCOUNT_CREATION_PROVIDERS = frozenset({"google"})

_VERDADEIRO = frozenset({"true", "1", "yes"})


def email_of(perfil: dict) -> str:
    """Aceita apenas e-mail com PROVA POSITIVA de verificação.

    Endurecido (§6):
      - usa SOMENTE o claim `email`; `preferred_username` (UPN) NÃO é e-mail
        verificado e não é aceito sem contrato específico de provedor;
      - `email_verified` precisa ser afirmativamente verdadeiro. AUSENTE/None =>
        REJEITA (fail-closed): sem essa prova, alguém entra com o e-mail de
        outra pessoa.
    """
    email = str(perfil.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise OAuthError("provedor não forneceu e-mail verificável")
    verificado = perfil.get("email_verified")
    ok = verificado is True or str(verificado).strip().lower() in _VERDADEIRO
    if not ok:
        raise OAuthError("e-mail não verificado no provedor")
    return email


__all__ = ["PROVIDERS", "Provider", "StateStore", "OAuthError", "authorize_url",
           "exchange_code", "email_of", "challenge_from",
           "ACCOUNT_CREATION_PROVIDERS"]
