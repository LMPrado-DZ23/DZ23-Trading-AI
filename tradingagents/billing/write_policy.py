"""Política CENTRAL de escrita por rota — determinística, SEM IA.

Cada rota MUTÁVEL (POST/PUT/PATCH/DELETE) do serviço é classificada aqui, por
(método, TEMPLATE de rota) — nunca por prefixo genérico que pudesse liberar
uma ação perigosa dentro da mesma rota. O guard de entitlements consulta esta
tabela; `tests/test_route_policy.py` FALHA se qualquer rota mutável nova ficar
sem classificação (fail-closed por omissão).

Três classes:
- ALLOW_READONLY: seguro mesmo com conta somente-leitura — autenticação,
  leitura/exportação, pagamento/checkout, INÍCIO de trial (saída do read-only)
  e REDUÇÃO de risco/de-escalação (cancelar job, reconciliar, engatar
  kill-switch, parada de emergência).
- BLOCK_READONLY: cria trabalho/exposição/estado novo — novos jobs, execução
  PAPER/SHADOW, reuniões/votos/status de agentes, configuração, backup,
  importação, liberar LIVE, automações.
- ACTION_AWARE: a mesma rota tem ações de risco diferente; o handler valida a
  ação no servidor (ex.: kill-switch engage=libera, reset=bloqueia).
"""
from __future__ import annotations

from enum import Enum

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class WriteAccess(str, Enum):
    ALLOW_READONLY = "allow_readonly"
    BLOCK_READONLY = "block_readonly"
    ACTION_AWARE = "action_aware"


# (MÉTODO, TEMPLATE) -> WriteAccess. Templates são os do FastAPI (com {param}).
ROUTE_POLICY: dict[tuple[str, str], WriteAccess] = {
    # --- Autenticação / sessão: sempre liberadas ---------------------------
    ("POST", "/v1/auth/bootstrap"): WriteAccess.ALLOW_READONLY,
    ("POST", "/v1/auth/login"): WriteAccess.ALLOW_READONLY,
    ("POST", "/v1/auth/logout"): WriteAccess.ALLOW_READONLY,
    ("POST", "/v1/auth/signup"): WriteAccess.ALLOW_READONLY,
    ("POST", "/v1/auth/step-up"): WriteAccess.ALLOW_READONLY,
    # --- Pagamento / telemetria / webhook público / início de trial --------
    ("POST", "/v1/billing/checkout"): WriteAccess.ALLOW_READONLY,
    ("POST", "/v1/webhooks/woovi"): WriteAccess.ALLOW_READONLY,
    ("POST", "/v1/analytics/event"): WriteAccess.ALLOW_READONLY,
    ("POST", "/v1/trial/start"): WriteAccess.ALLOW_READONLY,
    # --- Redução de risco / de-escalação: liberadas em read-only -----------
    ("POST", "/v1/jobs/{jid}/cancel"): WriteAccess.ALLOW_READONLY,
    ("POST", "/v1/execution/reconciliation"): WriteAccess.ALLOW_READONLY,
    ("POST", "/v1/desk/reconciliar"): WriteAccess.ALLOW_READONLY,
    ("POST", "/v1/live/parada-emergencia"): WriteAccess.ALLOW_READONLY,
    # Rota legada (410, não executa nada): deixe o cliente ver o 410.
    ("POST", "/v1/broker/order"): WriteAccess.ALLOW_READONLY,
    # Exportar dados do próprio tenant é LEITURA/portabilidade (admin+step-up
    # gateiam à parte); permitido mesmo em conta somente-leitura.
    ("POST", "/v1/mudanca/exportar"): WriteAccess.ALLOW_READONLY,
    # --- Depende da ação (validada no servidor) ----------------------------
    ("POST", "/v1/execution/kill-switch"): WriteAccess.ACTION_AWARE,
    # --- Cria trabalho / exposição / estado novo: BLOQUEADAS ---------------
    ("POST", "/v1/config"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/jobs"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/admin/tenants"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/admin/users"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/agency/agents/{agent_id}/status"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/agency/meetings"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/agency/meetings/{meeting_id}/votes"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/execution/paper"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/execution/shadow"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/desk/cycle"): WriteAccess.BLOCK_READONLY,
    # §8: start (aumenta exposição) exige assinatura; stop (reduz risco) é sempre
    # acessível. O handler decide pela ação — não bloqueia a parada.
    ("POST", "/v1/desk/scheduler/{action}"): WriteAccess.ACTION_AWARE,
    ("POST", "/v1/backup"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/live/liberar"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/alertas/verificar"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/mudanca/importar"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/agency/missions"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/agency/tasks"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/agency/tasks/run"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/agency/tasks/{task_id}/approve"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/agency/worker/start"): WriteAccess.BLOCK_READONLY,
    ("POST", "/v1/agency/worker/stop"): WriteAccess.BLOCK_READONLY,
}

# Ações do kill-switch validadas no servidor: engatar reduz risco (liberado);
# resetar reabilita a execução (bloqueado em read-only, salvo política admin).
_KILL_SWITCH_ACTIONS: dict[str, WriteAccess] = {
    "engage": WriteAccess.ALLOW_READONLY,
    "reset": WriteAccess.BLOCK_READONLY,
}

# LEITURAS SENSÍVEIS (§7): GET não passa pelo guard de escrita, mas alguns
# expõem segredos/dados de conta e precisam de CAPABILITY. Matriz por (método,
# template) -> capability exigida. "admin+stepup" = admin E reautenticação.
SENSITIVE_READS: dict[tuple[str, str], str] = {
    # Export de segredos agora é POST (§4, ver ROUTE_POLICY); aqui ficam as
    # LEITURAS (GET) que precisam de capability.
    ("GET", "/v1/backups"): "platform-admin",           # backup GLOBAL da plataforma
    ("GET", "/v1/admin/users"): "admin",                # diagnóstico/admin (tenant DB)
}


def capability_for_read(method: str, template: str) -> str | None:
    """Capability exigida por uma leitura sensível, ou None se não é sensível."""
    return SENSITIVE_READS.get((method.upper(), template))


def policy_for(method: str, template: str) -> WriteAccess | None:
    """Classe de escrita da rota, ou None se NÃO classificada (o guard trata
    None como fail-closed = bloquear; o teste de matriz proíbe deixar None)."""
    return ROUTE_POLICY.get((method.upper(), template))


def kill_switch_action_access(action: str) -> WriteAccess:
    """engage → ALLOW; reset → BLOCK; ação inválida → BLOCK (fail-closed)."""
    return _KILL_SWITCH_ACTIONS.get(action.strip().lower(), WriteAccess.BLOCK_READONLY)


__all__ = ["MUTATING_METHODS", "WriteAccess", "ROUTE_POLICY", "SENSITIVE_READS",
           "policy_for", "kill_switch_action_access", "capability_for_read"]
