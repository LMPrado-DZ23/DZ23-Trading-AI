# RELEASE_GATES — DZ23 Trading

> Frase oficial até a auditoria externa: **"Release candidate para
> PAPER/SHADOW/TESTNET, aguardando auditoria independente."** Nunca "pronto
> para LIVE".

## Gate de deploy (todos verdes antes de redeploy)

| # | Gate | Como provar | Estado nesta sessão |
|---|---|---|---|
| 1 | suíte completa verde | CI do GitHub (pytest+compileall) | ⏳ via CI (local não roda: 3.14) |
| 2 | ruff verde | `ruff check .` | ✅ local |
| 3 | typecheck verde ou blocker | A2 — typecheck não existe | 🔴 A2 documentado |
| 4 | migrations analisadas | revisão | n/a nesta rodada (sem migration) |
| 5 | backup se mexeu no banco | — | n/a (sem mudança de schema) |
| 6 | build reproduzível | Dockerfile py3.12 + `.[desk]` | ✅ |
| 7 | SHA registrado | STATUS.md/Histórico | ✅ |
| 8 | rollback definido | Coolify → Rollback para SHA anterior | ✅ disponível |
| 9 | sem credencial em código/log | `.env.example` só nomes; scan | ✅ |
| 10 | LIVE explicitamente bloqueado | `/v1/status/operacao` LIVE=false | ✅ |

## Prova pós-deploy (após estabilização da troca de containers)

- `/healthz` → 200 (Postgres/Supabase conectado)
- `/v1/status/operacao` → 200, `modo_atual=PAPER`, `LIVE.habilitado=false`,
  `operacao_real.estado=BLOQUEADA`
- SHA implantado == SHA esperado (log do deploy mostra branch+commit)
- sem 5xx novo; console do navegador limpo; login funcional; fluxos PAPER ok;
  logs sem secrets

## Gate de cadastro (SIGNUP_ENABLED=true) — NÃO liberar sem tudo isto
cadastro transacional · verificação de e-mail · rate limiting ·
anti-enumeração · isolamento Tenant A×B · RBAC · sessões · recuperação de
senha · E2E completo · auditoria do SHA específico.

## Ações que exigem autorização do proprietário
habilitar LIVE · ordem/dinheiro real · excluir/resetar dados · migration
destrutiva · apagar app/volume/backup · trocar DNS · rotacionar credencial
crítica sem plano · merge definitivo sem rollback · liberar cadastro público.

## Estado atual do deploy
- Produção (`dz23trading.com.br`): rodando `40dbbf3` (validado nesta sessão:
  healthz 200, `/v1/status/operacao` 200 PAPER, LIVE=false).
- Commits `7884687`/`5563dcd`/`b199ee1` (B8/B7/B1-B9) na branch, **ainda não
  implantados** — aguardando gate 1 (suíte completa via CI) antes de redeploy.
