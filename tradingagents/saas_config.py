"""Trava de configuração do SAAS_MODE (§7) — pura, determinística, tipada.

SAAS_MODE liga o produto pago: exige multi-tenant, chave de assinatura, banco
transacional e billing store real. Config ausente NUNCA pode cair no modo
gratuito legado — esta função devolve a lista do que falta e o serviço aborta
o boot (fail-closed) em vez de degradar em silêncio.
"""
from __future__ import annotations

TRUE_VALUES = frozenset({"1", "true", "yes", "sim", "on"})


def is_truthy(raw: str | None) -> bool:
    """Parser estrito de flag de ambiente (só valores afirmativos ligam)."""
    return (raw or "").strip().lower() in TRUE_VALUES


def missing_saas_requirements(*, multi_tenant: bool, tenant_store_ok: bool,
                              signing_key: str | None, database_url: str | None,
                              billing_store_ok: bool) -> list[str]:
    """Requisitos ausentes para rodar em SAAS_MODE. Lista vazia = pode subir.

    Cada item é uma frase acionável (o que configurar). Um SQLite local NÃO
    conta como banco de SaaS: exige-se DATABASE_URL (Postgres transacional)."""
    faltas: list[str] = []
    if not multi_tenant or not tenant_store_ok:
        faltas.append("MULTI_TENANT_ENABLED ligado e inicializado")
    if not signing_key:
        faltas.append("TENANT_TOKEN_SIGNING_KEY")
    if not (database_url or "").strip():
        faltas.append("DATABASE_URL (banco transacional; SQLite não serve p/ SaaS)")
    if not billing_store_ok:
        faltas.append("billing store inicializado (Postgres disponível no boot)")
    return faltas


__all__ = ["TRUE_VALUES", "is_truthy", "missing_saas_requirements"]
