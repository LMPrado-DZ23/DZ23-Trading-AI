# M3 — Verificação do implementador

Data: 2026-09-08

Branch: `feat/a5-end-user-platform`

Base M2: `6f3fc5384464fc9b7eadfd5f4faf358e24602f00`

## Escopo

- fila PostgreSQL com claim por lease, heartbeat, retry, cancelamento e fencing;
- API e worker separados;
- `AnalysisRunContext` imutável com `decision_at`, calendário, digests e schema 7;
- checkpoint e manifesto vinculados ao contexto;
- isolamento de configuração, credenciais LLM por tenant e capacidades OpenBao separadas;
- limites e contabilização de tokens/custo;
- validação point-in-time antes de chamadas de rede, com apenas o analista de
  mercado habilitado até as outras famílias terem `available_at` confiável;
- isolamento físico de cada attempt em subprocesso, deadline, encerramento da
  árvore, quota de disco e journal parcial durável;
- digest das fontes e do ambiente de dependências que vincula API, job e worker;
- migrations 0007–0017, privilégios mínimos, fencing e auditoria append-only.

## Reprodução local

- Ruff: verde.
- `compileall`: verde.
- Suíte integral com PostgreSQL e Redis reais: 0 falhas, 0 erros, 1 skip. O
  resumo pytest é 1184 passed, 1 skipped e 71 subtests passed.
- Integração PostgreSQL 17 + Redis 7.4: 9 passed, 0 skipped.

## Evidências

| Arquivo | Bytes | SHA-256 |
| --- | ---: | --- |
| `audit/m3-final-junit.xml` | 160001 | `baa78e902647dbbf33162137a788d3573f4abd585b0b1743bc983fd8b90944db` |
| `audit/m3-integration-junit.xml` | 1608 | `7c962cc7be893ab38eb8d585fc4f8eb8835bc6cfb171546d797b24544e09122d` |

## Limites honestos

- O único skip é DeepSeek live, que depende de uma credencial externa não
  comprometida. Bedrock foi removido do catálogo da plataforma até existir rota
  persistida de região; continua disponível apenas no cliente CLI opcional.
- Timeout menor que metade do lease e fencing reduzem duplicação após partição,
  mas uma chamada LLM externa não oferece exatamente-uma-vez. Cobrança e retries
  permanecem observáveis e limitados; execução de capital não pertence ao M3.
- Fundamentals, notícias, macro, social e Polymarket não são tratados como
  temporalmente corretos: estão excluídos de todos os jobs da plataforma. Isso
  reduz a análise a mercado/indicadores até o marco que implementar as fontes PIT.
- Migrations remotas 0006–0017 só serão aplicadas depois do schema congelado e da
  revisão independente.
- Esta é evidência do implementador, não aprovação. O gate só fecha após auditoria
  independente sobre o commit congelado.
