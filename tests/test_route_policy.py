"""A matriz de entitlements é TESTE, não só documento (§5).

Enumera TODA rota mutável do FastAPI e exige que cada (método, template) esteja
classificada na política central `write_policy.ROUTE_POLICY`. Se alguém criar
uma rota POST/PUT/PATCH/DELETE nova e esquecer de classificá-la, este teste
FALHA — a rota não fica liberada por prefixo genérico nem por esquecimento.
"""
from __future__ import annotations

from tradingagents.billing.write_policy import (
    MUTATING_METHODS,
    ROUTE_POLICY,
    SENSITIVE_READS,
    WriteAccess,
    capability_for_read,
    kill_switch_action_access,
    policy_for,
)


def _rotas_mutaveis():
    from service import app as m
    reais = set()
    for route in m.app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", None)
        if path is None:
            continue
        for method in methods:
            if method in MUTATING_METHODS:
                reais.add((method, path))
    return reais


class TestMatrizDeRotas:
    def test_toda_rota_mutavel_esta_classificada(self):
        faltando = sorted(r for r in _rotas_mutaveis() if r not in ROUTE_POLICY)
        assert not faltando, (
            "rotas mutáveis SEM classificação em write_policy.ROUTE_POLICY "
            f"(fail-closed bloquearia, mas classifique explicitamente): {faltando}")

    def test_politica_nao_tem_rota_fantasma(self):
        reais = _rotas_mutaveis()
        fantasmas = sorted(k for k in ROUTE_POLICY if k not in reais)
        assert not fantasmas, (
            f"entradas de política sem rota real correspondente (drift): {fantasmas}")

    def test_classificacoes_criticas(self):
        # kill-switch é a única action-aware: engage libera, reset bloqueia.
        assert ROUTE_POLICY[("POST", "/v1/execution/kill-switch")] is WriteAccess.ACTION_AWARE
        # de-escalação / segurança: liberadas em read-only
        for p in ("/v1/jobs/{jid}/cancel", "/v1/execution/reconciliation",
                  "/v1/desk/reconciliar", "/v1/live/parada-emergencia"):
            assert ROUTE_POLICY[("POST", p)] is WriteAccess.ALLOW_READONLY, p
        # auth / pagamento / início-de-trial: liberadas
        for p in ("/v1/auth/login", "/v1/billing/checkout", "/v1/trial/start"):
            assert ROUTE_POLICY[("POST", p)] is WriteAccess.ALLOW_READONLY, p
        # nova exposição / trabalho / config / liberar LIVE: bloqueadas
        for p in ("/v1/config", "/v1/jobs", "/v1/execution/paper",
                  "/v1/execution/shadow", "/v1/live/liberar", "/v1/backup"):
            assert ROUTE_POLICY[("POST", p)] is WriteAccess.BLOCK_READONLY, p


class TestAcaoDoKillSwitch:
    def test_engage_libera_reset_bloqueia(self):
        assert kill_switch_action_access("engage") is WriteAccess.ALLOW_READONLY
        assert kill_switch_action_access("ENGAGE") is WriteAccess.ALLOW_READONLY
        assert kill_switch_action_access("reset") is WriteAccess.BLOCK_READONLY

    def test_acao_invalida_e_fail_closed(self):
        assert kill_switch_action_access("qualquer") is WriteAccess.BLOCK_READONLY
        assert kill_switch_action_access("") is WriteAccess.BLOCK_READONLY


class TestPolicyLookup:
    def test_rota_desconhecida_retorna_none(self):
        assert policy_for("POST", "/v1/rota/que/nao/existe") is None

    def test_metodo_normalizado(self):
        assert policy_for("post", "/v1/config") is WriteAccess.BLOCK_READONLY


class TestCapabilidadeDeLeiturasSensiveis:
    """§7: a matriz de capabilities não cobre só rotas mutáveis — GETs que
    expõem segredos/backups/diagnóstico exigem capability e existem no app."""

    def test_leituras_sensiveis_classificadas(self):
        assert capability_for_read("GET", "/v1/mudanca/exportar") == "admin+stepup"
        assert capability_for_read("GET", "/v1/backups") == "admin"
        assert capability_for_read("GET", "/v1/admin/users") == "admin"
        assert capability_for_read("GET", "/v1/agency/health") is None   # não sensível

    def test_toda_leitura_sensivel_existe_no_app(self):
        reais = set()
        for route in __import__("service.app", fromlist=["app"]).app.routes:
            path = getattr(route, "path", None)
            for method in (getattr(route, "methods", None) or set()):
                reais.add((method, path))
        faltando = sorted(k for k in SENSITIVE_READS if k not in reais)
        assert not faltando, f"leitura sensível sem rota real: {faltando}"
