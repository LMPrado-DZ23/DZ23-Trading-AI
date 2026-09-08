# M3 — Verificação do implementador

Data: 2026-09-08

Branch: `feat/a5-end-user-platform`

Base M2: `6f3fc5384464fc9b7eadfd5f4faf358e24602f00`

## Escopo

- fila PostgreSQL com claim por lease, heartbeat, retry, cancelamento e fencing;
- API e worker separados;
- `AnalysisRunContext` imutável com `decision_at`, calendário, digests e schema 3;
- checkpoint e manifesto vinculados ao contexto;
- isolamento de configuração, credenciais LLM por tenant e capacidades OpenBao separadas;
- limites e contabilização de tokens/custo;
- validação point-in-time antes de chamadas de rede;
- migrations 0007–0013, privilégios mínimos e auditoria append-only.

## Reprodução local

- Ruff: verde.
- `compileall`: verde.
- Suíte integral: 1201 casos JUnit; 0 falhas, 0 erros, 11 skips. O resumo pytest é
  1119 passed, 11 skipped e 71 subtests passed.
- Integração PostgreSQL 17 + Redis 7.4: 9 passed, 0 skipped.

## Evidências

| Arquivo | Bytes | SHA-256 |
| --- | ---: | --- |
| `audit/m3-final-junit.xml` | 154739 | `7aad41b55f31d416066e1beca020be028b4ec12e42a89703ec749a8230d58220` |
| `audit/m3-integration-junit.xml` | 1608 | `1f876cfdbed6f584207014c5efe9d5ab1d4156a601a4d27f351b1f1c5b49227a` |

## Limites honestos

- Os 11 skips da suíte integral incluem integrações que foram executadas em uma
  segunda invocação com PostgreSQL e Redis reais, além de Bedrock e DeepSeek live,
  que dependem de credenciais externas e não são promoção de venue.
- Timeout menor que metade do lease e fencing reduzem duplicação após partição,
  mas uma chamada LLM externa não oferece exatamente-uma-vez. Cobrança e retries
  permanecem observáveis e limitados; execução de capital não pertence ao M3.
- Migrations remotas 0006–0013 só serão aplicadas depois do schema congelado e da
  revisão independente.
- Esta é evidência do implementador, não aprovação. O gate só fecha após auditoria
  independente sobre o commit congelado.
