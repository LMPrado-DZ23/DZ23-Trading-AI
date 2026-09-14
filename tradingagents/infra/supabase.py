"""Supabase como banco gerenciado do sistema.

DECISÃO ASSUMIDA: usamos o PostgreSQL do Supabase via DATABASE_URL — o
mesmo caminho já testado do job store, tenancy e assinaturas. Nada de SDK
novo: menos dependência, menos superfície, e backup/restore continuam
valendo.

REGRA DURA: a chave `service_role` do Supabase ignora RLS e dá poder total
sobre o banco. Ela NÃO entra no frontend, NÃO vai para o navegador e NÃO é
necessária para o backend (que fala Postgres direto). Só a chave publicável
pode aparecer no cliente — e nem dela precisamos hoje.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

REF_RE = re.compile(r"^[a-z0-9]{20}$")


class SupabaseConfigError(ValueError):
    """Configuração inválida — melhor falar claro que falhar no meio."""


@dataclass(frozen=True)
class SupabaseConfig:
    project_ref: str
    db_password: str = ""
    region: str = "us-east-1"
    pooler: bool = True

    @property
    def rest_url(self) -> str:
        return f"https://{self.project_ref}.supabase.co/rest/v1/"

    @property
    def direct_host(self) -> str:
        return f"db.{self.project_ref}.supabase.co"

    @property
    def pooler_host(self) -> str:
        return f"aws-0-{self.region}.pooler.supabase.com"

    def database_url(self) -> str:
        """DSN pronto para DATABASE_URL.

        Pooler (6543) é o padrão: servidores de aplicação abrem e fecham
        conexões o tempo todo e o Postgres do Supabase tem limite baixo de
        conexões diretas.
        """
        if not self.db_password:
            raise SupabaseConfigError(
                "senha do banco ausente — pegue em Supabase → Settings → "
                "Database → Database password e informe em SUPABASE_DB_PASSWORD")
        from urllib.parse import quote
        senha = quote(self.db_password, safe="")
        if self.pooler:
            return (f"postgresql://postgres.{self.project_ref}:{senha}@"
                    f"{self.pooler_host}:6543/postgres")
        return f"postgresql://postgres:{senha}@{self.direct_host}:5432/postgres"


def config_from_env(env=None) -> SupabaseConfig | None:
    env = env if env is not None else os.environ
    ref = (env.get("SUPABASE_PROJECT_REF") or "").strip()
    if not ref:
        return None
    if not REF_RE.match(ref):
        raise SupabaseConfigError(
            "SUPABASE_PROJECT_REF deve ser o identificador do projeto "
            "(20 caracteres minúsculos), visto na URL do dashboard")
    return SupabaseConfig(
        project_ref=ref,
        db_password=(env.get("SUPABASE_DB_PASSWORD") or "").strip(),
        region=(env.get("SUPABASE_REGION") or "us-east-1").strip(),
        pooler=(env.get("SUPABASE_POOLER", "true").strip().lower() != "false"))


def resolve_database_url(env=None) -> tuple[str | None, str]:
    """Decide o DSN do sistema e explica a decisão (vai para o /healthz).

    DATABASE_URL explícito sempre ganha: quem escreveu a URL na mão sabe o
    que quer.
    """
    env = env if env is not None else os.environ
    explicito = (env.get("DATABASE_URL") or "").strip()
    if explicito:
        return explicito, "DATABASE_URL explícita"
    try:
        cfg = config_from_env(env)
    except SupabaseConfigError as exc:
        return None, f"Supabase mal configurado: {exc}"
    if cfg is None:
        return None, "banco local do volume (nenhum Postgres configurado)"
    try:
        return cfg.database_url(), f"Supabase {cfg.project_ref} (pooler)"
    except SupabaseConfigError as exc:
        return None, str(exc)


def check_rest(cfg: SupabaseConfig, service_key: str, *, transport=None) -> dict:
    """Confere se o projeto responde. A chave nunca volta na resposta."""
    if not service_key:
        return {"ok": False, "detalhe": "chave de serviço não configurada"}
    if transport is not None:
        return transport(cfg, service_key)
    import httpx
    try:
        r = httpx.get(cfg.rest_url,
                      headers={"apikey": service_key,
                               "Authorization": f"Bearer {service_key}"},
                      timeout=10)
        return {"ok": r.status_code < 400, "status_http": r.status_code}
    except Exception as exc:
        return {"ok": False, "detalhe": f"{type(exc).__name__}"}


__all__ = ["SupabaseConfig", "SupabaseConfigError", "config_from_env",
           "resolve_database_url", "check_rest"]
