## Resumo

Descreva o problema e a solução.

## Tipo de mudança

- [ ] Bug fix
- [ ] Feature
- [ ] Documentação
- [ ] Segurança
- [ ] Infraestrutura/deployment
- [ ] Alteração de risco ou execução

## Validação

- [ ] `ruff check .`
- [ ] `python3 -m compileall -q tradingagents service scripts`
- [ ] `python3 -m pytest -q`
- [ ] `python3 scripts/smoke_agency_api.py`
- [ ] `python3 scripts/smoke_multitenant.py` (quando aplicável)
- [ ] `docker compose config` (quando aplicável)

## Segurança

- [ ] Não incluí secrets, credenciais ou dados privados.
- [ ] Mantive `LIVE_TRADING=false` e `REAL_MONEY_EXECUTION=false`.
- [ ] Não criei bypass do Risk Engine, ExecutionRouter, KillSwitch ou reconciliação.
- [ ] Alterações multi-tenant estão escopadas por tenant.
- [ ] Documentei migrações, limitações e dependências externas.

## Observações

Inclua riscos conhecidos e passos de rollout/rollback.
