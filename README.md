# TradingAgents DZ23 — análise de trading com IA, operável pelo navegador

Sistema multiagente: múltiplos agentes de IA analisam um ativo (ações ou
cripto), debatem e entregam uma decisão. A operação segura atual é Paper,
Shadow e preparação controlada para Demo; dinheiro real permanece bloqueado
por padrão.

**Feito para leigos:** tudo — configuração e operação — acontece numa página,
com guia de 3 passos, provedores de IA **gratuitos** (Groq, Gemini, Mistral,
OpenRouter, Ollama local) e modelos escolhidos automaticamente.

## Subir o seu (5 minutos)
1. Coolify/Docker → aponte para este repositório (build: `Dockerfile`)
2. Uma variável: `ACCESS_PASSWORD` (senha FORTE — 20+ caracteres) e um
   volume em `/data`
3. Abra o domínio → siga o guia "🚀 Comece aqui" na tela

Detalhes em [`DEPLOY.md`](DEPLOY.md).

## Segurança (herdada de auditoria adversarial independente)
- Execução governada por Risk Engine, ExecutionRouter, auditoria, reconciliação,
  KillSwitch e idempotência — 927 testes automatizados foram validados no
  último ciclo local.
- Chaves ficam **no seu servidor** (`/data`, permissão 0600), sempre
  mascaradas na tela; **modo real só liga com frase de autorização
  explícita** digitada pelo dono.
- Trava anti-força-bruta no acesso (5 erros ⇒ 15 min).

## Créditos
Fork endurecido de [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
(Apache-2.0 — ver `LICENSE`). Pesquisa original: TradingAgents:
Multi-Agents LLM Financial Trading Framework.

## Operação segura

Leia o [runbook operacional](docs/OPERATIONS-USER-READY.md) antes de iniciar.
O progresso verificado e as limitações atuais estão em [docs/progress.md](docs/progress.md).

Os defaults são `PAPER_TRADING=true`, `LIVE_TRADING=false`,
`REAL_MONEY_EXECUTION=false` e `LEGACY_BROKER_ORDER_ENABLED=false`.

> Uso por sua conta e risco. Nada aqui é recomendação de investimento, garantia de lucro ou promessa de crescimento de carteira.
