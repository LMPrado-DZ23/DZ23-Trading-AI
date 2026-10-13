# Contribuindo para o TradingAgents DZ23

Obrigado por contribuir. O projeto aceita correções, testes, documentação, melhorias de acessibilidade, integrações de dados e evoluções do runtime multiagente.

## Antes de começar

1. Leia o `README.md`, `DEPLOY.md` e os documentos de segurança.
2. Abra uma issue para mudanças grandes de arquitetura, contratos, risco, execução, autenticação ou persistência.
3. Nunca inclua secrets, credenciais de corretora, dados pessoais, dumps de banco ou logs de produção.

## Desenvolvimento

```bash
uv sync --locked --extra desk --extra dev
uv run uvicorn service.app:app --port 8080
```

No Windows (PowerShell), os mesmos comandos funcionam; não é preciso ativar
virtualenv manualmente.

Antes de abrir um pull request:

```bash
uv run ruff check .
uv run python -m compileall -q tradingagents service scripts
uv run pytest -q
uv run python scripts/smoke_agency_api.py
uv run python scripts/smoke_single_owner.py
```

Se Docker estiver disponível:

```bash
docker compose config
docker compose build
```

## Regras de engenharia

- Preserve `LIVE_TRADING=false` e `REAL_MONEY_EXECUTION=false`.
- Toda alteração de execução deve passar por Risk Engine, ExecutionRouter, idempotência, auditoria e reconciliação.
- Não trate texto de LLM como prova de ordem, fill, posição, saldo ou PnL.
- Adicione testes para comportamento novo e regressão para comportamento corrigido.
- Use Decimal para valores monetários e UTC para timestamps.
- O produto é single-owner: não reintroduza SaaS, multi-tenant, cadastro de
  terceiros ou cobrança — esses recursos foram removidos de propósito e não
  voltam por flag. Isolamento entre instalações é requisito de migração.
- Documente limitações externas e não declare integração real validada usando somente mocks.

## Pull requests

Descreva o problema, a solução, os riscos, os testes executados e qualquer migração necessária. Pull requests que alterem segurança, execução financeira ou autenticação precisam de revisão adicional e não podem remover testes para obter uma suíte verde.

## Licença

Ao contribuir, você concorda que sua contribuição será distribuída sob a Apache License 2.0 do projeto.
