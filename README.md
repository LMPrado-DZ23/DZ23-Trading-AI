# TradingAgents DZ23 — análise de trading com IA, operável pelo navegador

Sistema completo: múltiplos agentes de IA analisam um ativo (ações ou
cripto), debatem, e entregam uma decisão — que você pode executar em
**9 corretoras** (Binance, Bybit, OKX, Coinbase, Kraken, Gate.io, KuCoin,
Mercado Bitcoin, Bitget), em **modo treino** (dinheiro fictício) ou **real**.

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
- Núcleo aprovado em 12 rodadas de auditoria de segurança (redação de
  segredos fail-closed, política de transporte, gate de capital
  incontornável) — 894 testes automatizados rodam a cada push (CI).
- Chaves ficam **no seu servidor** (`/data`, permissão 0600), sempre
  mascaradas na tela; **modo real só liga com frase de autorização
  explícita** digitada pelo dono.
- Trava anti-força-bruta no acesso (5 erros ⇒ 15 min).

## Créditos
Fork endurecido de [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
(Apache-2.0 — ver `LICENSE`). Pesquisa original: TradingAgents:
Multi-Agents LLM Financial Trading Framework.

> ⚠️ Uso por sua conta e risco. Nada aqui é recomendação de investimento.
